from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ._logging_config import (
    CURRENT_IDENTITY,
    CURRENT_INVOCATION_ID,
    CURRENT_STAGE,
    CURRENT_TASK,
    MultiLineConsoleFormatter,
    MultiLineFormatter,
    ScPolyALogger,
    TqdmLoggingHandler,
    get_logger,
)
from .define import LOG_MAIN

INVOCATION_METADATA_SCHEMA_V1 = "scpolya.logging.invocation.v1"
INVOCATION_METADATA_SCHEMA_V2 = "scpolya.logging.invocation.v2"
CURRENT_INVOCATION_METADATA_SCHEMA = INVOCATION_METADATA_SCHEMA_V2
MANAGED_JOB_ID_ENV = "SCPOLYA_UI_JOB_ID"
_TARGET_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_ACTIVE_SESSION: RunLoggingSession | None = None
_ACTIVE_SESSION_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def finalize_managed_invocation_metadata(
    run_root: str | Path,
    *,
    job_id: str = "",
    pid: int | None = None,
    job_status: str,
    exit_code: int | None = None,
    finished_at: str = "",
) -> bool:
    """Close a managed invocation when its pipeline cannot close itself."""

    final_status = {
        "succeeded": "succeeded",
        "failed": "failed",
        "exited": "failed",
        "stopped": "cancelled",
        "cancelled": "cancelled",
    }.get(str(job_status).strip().lower())
    if final_status is None:
        return False

    invocation_root = Path(run_root).expanduser() / "logs" / "invocations"
    if not invocation_root.is_dir():
        return False
    normalized_job_id = str(job_id or "").strip()
    normalized_pid = int(pid) if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None
    if not normalized_job_id and normalized_pid is None:
        return False

    for metadata_path in sorted(
        invocation_root.glob("*/invocation.json"),
        key=_metadata_mtime,
        reverse=True,
    ):
        if metadata_path.is_symlink():
            continue
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("schema_version") not in {
            INVOCATION_METADATA_SCHEMA_V1,
            INVOCATION_METADATA_SCHEMA_V2,
        }:
            continue
        if str(payload.get("status") or "").strip().lower() != "running":
            continue

        payload_job_id = str(payload.get("job_id") or "").strip()
        payload_pid = payload.get("pid")
        if normalized_job_id and payload_job_id:
            metadata_matches = normalized_job_id == payload_job_id
        else:
            metadata_matches = bool(
                normalized_pid is not None
                and isinstance(payload_pid, int)
                and not isinstance(payload_pid, bool)
                and normalized_pid == payload_pid
            )
        if not metadata_matches:
            continue

        closed_at = _normalized_utc_timestamp(finished_at)
        duration_seconds = _elapsed_seconds(payload.get("started_at"), closed_at)
        payload.update(
            {
                "status": final_status,
                "finished_at": closed_at,
                "duration_seconds": duration_seconds,
            }
        )
        if normalized_job_id and not payload_job_id:
            payload["job_id"] = normalized_job_id
        if final_status != "succeeded" and not str(payload.get("error") or "").strip():
            exit_detail = "unknown" if exit_code is None else str(exit_code)
            payload["error"] = (
                f"Managed job {normalized_job_id or '-'} ended with status "
                f"{job_status} (exit_code={exit_detail}); invocation metadata "
                "was finalized by the job supervisor."
            )

        tmp_path = metadata_path.with_name(
            f".{metadata_path.name}.{os.getpid()}.managed.tmp"
        )
        try:
            tmp_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp_path.replace(metadata_path)
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True
    return False


def _metadata_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _normalized_utc_timestamp(value: str) -> str:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(
                raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).isoformat(timespec="milliseconds")
        except ValueError:
            pass
    return utc_now()


def _elapsed_seconds(started_at: object, finished_at: str) -> float:
    try:
        started = datetime.fromisoformat(str(started_at))
        finished = datetime.fromisoformat(finished_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        return round(max(0.0, (finished - started).total_seconds()), 6)
    except (TypeError, ValueError):
        return 0.0


class _ContextDefaultsFilter(logging.Filter):
    def __init__(self, invocation_id: str) -> None:
        super().__init__()
        self.invocation_id = invocation_id

    def filter(self, record: logging.LogRecord) -> bool:
        invocation_id = str(
            getattr(record, "scpolya_invocation_id", "")
            or CURRENT_INVOCATION_ID.get()
            or self.invocation_id
        )
        identity = str(
            getattr(record, "scpolya_identity", "")
            or CURRENT_IDENTITY.get()
            or ""
        )
        record.scpolya_invocation_id = invocation_id
        record.scpolya_invocation_short = invocation_id[:8]
        record.scpolya_stage = str(
            getattr(record, "scpolya_stage", "")
            or CURRENT_STAGE.get()
            or ""
        )
        record.scpolya_task = str(
            getattr(record, "scpolya_task", "")
            or CURRENT_TASK.get()
            or ""
        )
        record.scpolya_identity = identity
        record.scpolya_identity_suffix = (
            "" if not identity else f" identity={_one_line(identity)}"
        )
        record.scpolya_channel = str(
            getattr(record, "scpolya_channel", "") or ""
        )
        return True


class _InvocationFanoutHandler(logging.Handler):
    def __init__(
        self,
        *,
        log_dir: Path,
        invocation_dir: Path,
        invocation_id: str,
    ) -> None:
        super().__init__(level=logging.DEBUG)
        self.log_dir = log_dir
        self.invocation_dir = invocation_dir
        self.stage_log_dir = invocation_dir / "stages"
        self.run_log_path = invocation_dir / "run.log"
        self.main_log_path = log_dir / "main.log"
        self._streams: dict[Path, Any] = {}
        self.addFilter(_ContextDefaultsFilter(invocation_id))
        self.setFormatter(
            MultiLineFormatter(
                "%(asctime)s %(levelname)s invocation=%(scpolya_invocation_short)s"
                " source=%(name)s%(scpolya_identity_suffix)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self._stream(self.main_log_path)
        self._stream(self.run_log_path)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            rendered = self.format(record) + "\n"
            paths = {self.run_log_path}
            if record.name == LOG_MAIN:
                paths.add(self.main_log_path)
            for target in self._record_targets(record):
                paths.add(self.log_dir / f"{target}.log")
                paths.add(self.stage_log_dir / f"{target}.log")
            for path in sorted(paths, key=str):
                stream = self._stream(path)
                stream.write(rendered)
                stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        for stream in tuple(self._streams.values()):
            try:
                stream.flush()
                stream.close()
            except Exception:
                pass
        self._streams.clear()
        super().close()

    def _stream(self, path: Path):
        stream = self._streams.get(path)
        if stream is not None:
            return stream
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a", encoding="utf-8")
        self._streams[path] = stream
        return stream

    @staticmethod
    def _record_targets(record: logging.LogRecord) -> tuple[str, ...]:
        values = {
            str(getattr(record, "scpolya_channel", "") or ""),
            str(getattr(record, "scpolya_stage", "") or ""),
        }
        if record.name != LOG_MAIN:
            values.add(record.name)
        return tuple(
            sorted(
                {
                    _safe_target(value)
                    for value in values
                    if value and value != LOG_MAIN
                }
            )
        )


class RunLoggingSession:
    def __init__(
        self,
        *,
        log_dir: Path,
        invocation_id: str,
        verbose: bool,
        argv: Sequence[str],
        config: Mapping[str, Any],
        run_root: Path | None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.invocation_id = str(invocation_id).strip()
        if not self.invocation_id:
            raise ValueError("Logging invocation ID must be non-empty")
        self.verbose = bool(verbose)
        self.argv = tuple(str(value) for value in argv)
        self.run_root = (
            None
            if run_root is None
            else Path(run_root).expanduser().resolve()
        )
        self.started_at = utc_now()
        self._started_monotonic = time.monotonic()
        self._finished = False
        self._closed = False
        self._context_token = CURRENT_INVOCATION_ID.set(self.invocation_id)
        self._configured_loggers: set[str] = set()
        self._console_handlers: list[logging.Handler] = []
        self._tool_log_lock = threading.Lock()
        self._tool_log_counts: dict[tuple[str, str], int] = {}

        directory_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        self.invocation_dir = (
            self.log_dir
            / "invocations"
            / f"{directory_stamp}-{self.invocation_id[:8]}"
        )
        self.stage_log_dir = self.invocation_dir / "stages"
        self.tool_log_dir = self.invocation_dir / "tools"
        self.metadata_path = self.invocation_dir / "invocation.json"
        self.run_log_path = self.invocation_dir / "run.log"
        self.stage_log_dir.mkdir(parents=True, exist_ok=True)
        self.tool_log_dir.mkdir(parents=True, exist_ok=True)

        self._fanout_handler = _InvocationFanoutHandler(
            log_dir=self.log_dir,
            invocation_dir=self.invocation_dir,
            invocation_id=self.invocation_id,
        )
        self._context_filter = _ContextDefaultsFilter(self.invocation_id)
        self.main_logger = self._configure_logger(
            get_logger(LOG_MAIN),
            console_output=True,
        )
        self._metadata: dict[str, Any] = {
            "schema_version": CURRENT_INVOCATION_METADATA_SCHEMA,
            "invocation_id": self.invocation_id,
            "job_id": str(os.environ.get(MANAGED_JOB_ID_ENV) or ""),
            "status": "running",
            "started_at": self.started_at,
            "finished_at": "",
            "duration_seconds": None,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "run_root": "" if self.run_root is None else str(self.run_root),
            "output_dir": str(config.get("output_dir") or ""),
            "run_id": str(config.get("run_id") or ""),
            "argv": list(self.argv),
            "config_hash": _config_hash(config),
            "config_schema_version": 1,
            "config_source": "parsed_cli",
            "resolved_config": _jsonable(config),
            "error": "",
            "paths": {
                "compatibility_main_log": str(self.log_dir / "main.log"),
                "run_log": str(self.run_log_path),
                "stage_log_dir": str(self.stage_log_dir),
                "tool_log_dir": str(self.tool_log_dir),
            },
        }
        self._write_metadata()
        self.main_logger.info(
            "INVOCATION START "
            f"id={self.invocation_id} "
            f"run_root={self._metadata['run_root'] or '-'}"
        )

    def configure_module_logger(
        self,
        name: str,
        *,
        console_output: bool = False,
    ) -> ScPolyALogger:
        return self._configure_logger(
            get_logger(name),
            console_output=console_output,
        )

    def allocate_tool_log(self, tool: str, operation: str) -> Path:
        tool_component = _bounded_path_component(tool, fallback="tool")
        operation_component = _bounded_path_component(
            operation,
            fallback="command",
        )
        key = (tool_component, operation_component)
        with self._tool_log_lock:
            count = self._tool_log_counts.get(key, 0) + 1
            self._tool_log_counts[key] = count
        suffix = "" if count == 1 else f".{count:02d}"
        path = (
            self.tool_log_dir
            / tool_component
            / f"{operation_component}{suffix}.log"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def compatibility_tool_log(self, tool: str, operation: str) -> Path:
        return (
            self.log_dir
            / _bounded_path_component(tool, fallback="tool")
            / f"{_bounded_path_component(operation, fallback='command')}.log"
        )

    def finish(self, status: str, *, error: str = "") -> None:
        if self._finished:
            return
        normalized = str(status).strip().lower()
        if normalized not in {"succeeded", "failed", "cancelled"}:
            raise ValueError(f"Unsupported logging invocation status: {status!r}")
        duration = max(0.0, time.monotonic() - self._started_monotonic)
        message = (
            f"INVOCATION FINISH status={normalized} "
            f"id={self.invocation_id} duration_seconds={duration:.3f}"
        )
        if normalized == "succeeded":
            self.main_logger.info(message)
        else:
            self.main_logger.error(message)
        self._metadata.update(
            {
                "status": normalized,
                "finished_at": utc_now(),
                "duration_seconds": round(duration, 6),
                "error": str(error)[:4000],
            }
        )
        self._write_metadata()
        self._finished = True

    def close(self) -> None:
        if self._closed:
            return
        if not self._finished:
            self.finish(
                "failed",
                error="Logging session closed without an explicit invocation outcome",
            )
        for logger_name in tuple(self._configured_loggers):
            logger = get_logger(logger_name)
            logger.handlers = [
                handler
                for handler in logger.handlers
                if handler is not self._fanout_handler
                and handler not in self._console_handlers
            ]
        for handler in self._console_handlers:
            try:
                handler.close()
            except Exception:
                pass
        self._console_handlers.clear()
        self._fanout_handler.close()
        try:
            CURRENT_INVOCATION_ID.reset(self._context_token)
        except (LookupError, RuntimeError, ValueError):
            pass
        self._closed = True
        _clear_active_session(self)

    def _configure_logger(
        self,
        logger: ScPolyALogger,
        *,
        console_output: bool,
    ) -> ScPolyALogger:
        _close_handlers(logger.handlers, preserve={self._fanout_handler})
        logger.handlers.clear()
        logger.setLevel(logging.DEBUG if self.verbose else logging.INFO)
        logger.propagate = False
        logger.addHandler(self._fanout_handler)
        if console_output:
            console_handler = TqdmLoggingHandler()
            console_handler.setLevel(logging.DEBUG if self.verbose else logging.INFO)
            console_handler.addFilter(self._context_filter)
            console_handler.setFormatter(
                MultiLineConsoleFormatter(
                    "\033[36m%(asctime)s\033[0m \033[33m%(levelname)s\033[0m "
                    "invocation=%(scpolya_invocation_short)s"
                    "%(scpolya_identity_suffix)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            logger.addHandler(console_handler)
            self._console_handlers.append(console_handler)
        self._configured_loggers.add(logger.name)
        return logger

    def _write_metadata(self) -> None:
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.metadata_path.with_name(
            f".{self.metadata_path.name}.{os.getpid()}.tmp"
        )
        tmp_path.write_text(
            json.dumps(self._metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.metadata_path)


def setup_run_logging(
    *,
    log_dir: str | Path,
    invocation_id: str,
    verbose: bool,
    argv: Sequence[str] = (),
    config: Mapping[str, Any] | None = None,
    run_root: str | Path | None = None,
) -> RunLoggingSession:
    global _ACTIVE_SESSION
    with _ACTIVE_SESSION_LOCK:
        if _ACTIVE_SESSION is not None:
            _ACTIVE_SESSION.close()
        session = RunLoggingSession(
            log_dir=Path(log_dir),
            invocation_id=invocation_id,
            verbose=verbose,
            argv=tuple(argv),
            config=dict(config or {}),
            run_root=None if run_root is None else Path(run_root),
        )
        _ACTIVE_SESSION = session
        return session


def get_active_logging_session() -> RunLoggingSession | None:
    return _ACTIVE_SESSION


def _clear_active_session(session: RunLoggingSession) -> None:
    global _ACTIVE_SESSION
    with _ACTIVE_SESSION_LOCK:
        if _ACTIVE_SESSION is session:
            _ACTIVE_SESSION = None


def _safe_target(value: str) -> str:
    normalized = _TARGET_COMPONENT_RE.sub("_", str(value)).strip("._")
    return normalized or "unknown"


def _bounded_path_component(
    value: str,
    *,
    fallback: str,
    max_length: int = 120,
) -> str:
    raw = str(value)
    normalized = _TARGET_COMPONENT_RE.sub("_", raw).strip("._") or fallback
    if len(normalized) <= max_length:
        return normalized
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    prefix_length = max(1, max_length - len(digest) - 1)
    return f"{normalized[:prefix_length]}.{digest}"


def _one_line(value: str) -> str:
    return " ".join(str(value).split())[:240]


def _config_hash(config: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _jsonable(config),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_jsonable(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ),
        )
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _close_handlers(
    handlers: Iterable[logging.Handler],
    *,
    preserve: set[logging.Handler] | None = None,
) -> None:
    preserved = preserve or set()
    for handler in tuple(handlers):
        if handler in preserved:
            continue
        try:
            handler.close()
        except Exception:
            pass
