from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._logging_config import (
    CURRENT_IDENTITY,
    CURRENT_STAGE,
    CURRENT_TASK,
)
from .runtime_session import get_active_logging_session


TOOL_EXECUTION_SCHEMA_V1 = "scpolya.logging.tool_execution.v1"
_MAX_CAPTURE_CHARS = 1_000_000
_COMPATIBILITY_TAIL_BYTES = 4096
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.Lock] = {}
# Executions currently holding each compatibility log open in this process.
# Flat compatibility logs may be shared by concurrent workers; a byte delta of
# a shared log can then contain other executions' records.
_ACTIVE_COMPATIBILITY_GUARD = threading.Lock()
_ACTIVE_COMPATIBILITY: dict[Path, set["ToolExecution"]] = {}
_ATTEMPTS_GUARD = threading.Lock()
_ATTEMPTS: dict[tuple[str, str, str, str], int] = {}


@dataclass(frozen=True)
class _CompatibilitySnapshot:
    size: int
    tail_offset: int
    tail: bytes


class ToolExecution:
    def __init__(
        self,
        *,
        tool: str,
        operation: str,
        command: Sequence[Any] | str,
        compatibility_log: str | Path | None = None,
        cwd: str | Path | None = None,
        shell: bool = False,
        custom_environment: bool = False,
    ) -> None:
        self.tool = str(tool)
        self.operation = str(operation)
        self.command = _normalize_command(command)
        self.session = get_active_logging_session()
        self.execution_id = uuid.uuid4().hex
        self.stage = CURRENT_STAGE.get() or ""
        self.task = CURRENT_TASK.get() or ""
        self.identity = CURRENT_IDENTITY.get() or ""
        if compatibility_log is None and self.session is not None:
            try:
                compatibility_log = self.session.compatibility_tool_log(
                    self.tool,
                    self.operation,
                )
            except Exception:
                compatibility_log = None
        self.compatibility_log = None if compatibility_log is None else Path(compatibility_log)
        self.cwd = None if cwd is None else str(cwd)
        self.shell = bool(shell)
        self.custom_environment = bool(custom_environment)
        self.invocation_id = "" if self.session is None else self.session.invocation_id
        self.attempt = _next_attempt(
            self.invocation_id, self.tool, self.operation, self.identity
        )
        try:
            self.invocation_log = (
                None
                if self.session is None
                else self.session.allocate_tool_log(self.tool, self.operation)
            )
        except OSError:
            self.invocation_log = None
        self.started_at = ""
        self.finished_at = ""
        self._started_monotonic = 0.0
        self._returncode: int | None = None
        self._stdout: str | None = None
        self._stderr: str | None = None
        self._compatibility_key: Path | None = None
        self._shared_compatibility = False
        self._compatibility_snapshot: _CompatibilitySnapshot | None = None

    def __enter__(self) -> ToolExecution:
        # Registered only here, so an execution never entered cannot leave a
        # stale registry entry; __exit__ always unregisters.
        self._register_compatibility()
        self._compatibility_snapshot = _snapshot(self.compatibility_log)
        self.started_at = _utc_now()
        self._started_monotonic = time.monotonic()
        try:
            self._write_invocation(_render_start(self))
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if isinstance(exc, subprocess.CalledProcessError):
            self._returncode = int(exc.returncode)
            self._stdout = _as_text(getattr(exc, "stdout", None))
            self._stderr = _as_text(getattr(exc, "stderr", None))
        elif isinstance(exc, subprocess.TimeoutExpired):
            self._stdout = _as_text(getattr(exc, "stdout", None))
            self._stderr = _as_text(getattr(exc, "stderr", None))

        try:
            duration = max(0.0, time.monotonic() - self._started_monotonic)
            self.finished_at = _utc_now()
            status = _execution_status(exc, self._returncode)
            compatibility_output = _read_compatibility_delta(
                self.compatibility_log,
                self._compatibility_snapshot,
            )
            # Checked after the read: any concurrent writer registered (and so
            # set this flag) before it could write into the delta.
            if self._shared_compatibility:
                compatibility_output = (
                    "[omitted: this compatibility log was shared with concurrent "
                    "tool executions, so its delta is not attributable to this "
                    "execution; find this execution's record there by "
                    f"execution_id={self.execution_id}]\n"
                )
            self._write_invocation(
                _render_finish(
                    self,
                    status=status,
                    duration=duration,
                    exc=exc,
                    compatibility_output=compatibility_output,
                )
            )
            self._append_compatibility_summary(
                status=status,
                duration=duration,
                exc=exc,
            )
        except Exception:
            pass
        finally:
            self._unregister_compatibility()
        return False

    def _register_compatibility(self) -> None:
        if self.compatibility_log is None:
            return
        try:
            key = self.compatibility_log.expanduser().resolve()
        except Exception:
            return
        with _ACTIVE_COMPATIBILITY_GUARD:
            active = _ACTIVE_COMPATIBILITY.setdefault(key, set())
            if active:
                self._shared_compatibility = True
                for other in active:
                    other._shared_compatibility = True
            active.add(self)
        self._compatibility_key = key

    def _unregister_compatibility(self) -> None:
        key = self._compatibility_key
        if key is None:
            return
        with _ACTIVE_COMPATIBILITY_GUARD:
            active = _ACTIVE_COMPATIBILITY.get(key)
            if active is not None:
                active.discard(self)
                if not active:
                    del _ACTIVE_COMPATIBILITY[key]
        self._compatibility_key = None

    def record_result(self, result: subprocess.CompletedProcess[Any]) -> None:
        self._returncode = int(result.returncode)
        self._stdout = _as_text(result.stdout)
        self._stderr = _as_text(result.stderr)

    def record_output(self, output: Any, *, returncode: int = 0) -> None:
        self._returncode = int(returncode)
        self._stdout = _as_text(output)

    def _write_invocation(self, text: str) -> None:
        if self.invocation_log is None:
            return
        _best_effort_append(self.invocation_log, text)

    def _append_compatibility_summary(
        self,
        *,
        status: str,
        duration: float,
        exc: BaseException | None,
    ) -> None:
        if self.compatibility_log is None:
            return
        _best_effort_append(
            self.compatibility_log,
            _render_compatibility_summary(
                self,
                status=status,
                duration=duration,
                exc=exc,
            ),
        )


def run_tool_command(
    command: Sequence[Any] | str,
    *,
    tool: str,
    operation: str,
    compatibility_log: str | Path | None = None,
    **run_kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    with ToolExecution(
        tool=tool,
        operation=operation,
        command=command,
        compatibility_log=compatibility_log,
        cwd=run_kwargs.get("cwd"),
        shell=bool(run_kwargs.get("shell", False)),
        custom_environment=run_kwargs.get("env") is not None,
    ) as execution:
        result = subprocess.run(command, **run_kwargs)
        execution.record_result(result)
        return result


def check_tool_output(
    command: Sequence[Any] | str,
    *,
    tool: str,
    operation: str,
    compatibility_log: str | Path | None = None,
    **check_kwargs: Any,
) -> Any:
    with ToolExecution(
        tool=tool,
        operation=operation,
        command=command,
        compatibility_log=compatibility_log,
        cwd=check_kwargs.get("cwd"),
        shell=bool(check_kwargs.get("shell", False)),
        custom_environment=check_kwargs.get("env") is not None,
    ) as execution:
        output = subprocess.check_output(command, **check_kwargs)
        execution.record_output(output)
        return output


def _render_start(execution: ToolExecution) -> str:
    payload = {
        "schema": TOOL_EXECUTION_SCHEMA_V1,
        "event": "START",
        "invocation_id": execution.invocation_id,
        "tool": execution.tool,
        "operation": execution.operation,
        **_attribution(execution),
        "started_at": execution.started_at,
        "cwd": execution.cwd or os.getcwd(),
        "cwd_explicit": execution.cwd is not None,
        "shell": execution.shell,
        "environment": "custom" if execution.custom_environment else "inherit",
        "command": execution.command,
        "command_display": _display_command(execution.command),
        "compatibility_log": (
            "" if execution.compatibility_log is None else str(execution.compatibility_log)
        ),
    }
    return _metadata_block(payload)


def _render_finish(
    execution: ToolExecution,
    *,
    status: str,
    duration: float,
    exc: BaseException | None,
    compatibility_output: str | None,
) -> str:
    sections: list[str] = []
    if compatibility_output is not None:
        sections.append(_output_section("compatibility output", compatibility_output))
    if execution._stdout is not None:
        sections.append(_output_section("captured stdout", execution._stdout))
    if execution._stderr is not None:
        sections.append(_output_section("captured stderr", execution._stderr))
    payload = {
        "schema": TOOL_EXECUTION_SCHEMA_V1,
        "event": "FINISH",
        "invocation_id": execution.invocation_id,
        "tool": execution.tool,
        "operation": execution.operation,
        **_attribution(execution),
        "status": status,
        "exit_code": execution._returncode,
        "finished_at": execution.finished_at,
        "duration_seconds": round(duration, 6),
        "exception": _exception_text(exc),
    }
    sections.append(_metadata_block(payload))
    return "".join(sections)


def _render_compatibility_summary(
    execution: ToolExecution,
    *,
    status: str,
    duration: float,
    exc: BaseException | None,
) -> str:
    sections: list[str] = []
    if execution._stdout is not None:
        sections.append(_output_section("captured stdout", execution._stdout))
    if execution._stderr is not None:
        sections.append(_output_section("captured stderr", execution._stderr))
    payload = {
        "schema": TOOL_EXECUTION_SCHEMA_V1,
        "event": "SUMMARY",
        "invocation_id": execution.invocation_id,
        "tool": execution.tool,
        "operation": execution.operation,
        **_attribution(execution),
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
        "duration_seconds": round(duration, 6),
        "status": status,
        "exit_code": execution._returncode,
        "cwd": execution.cwd or os.getcwd(),
        "environment": "custom" if execution.custom_environment else "inherit",
        "command": execution.command,
        "command_display": _display_command(execution.command),
        "exception": _exception_text(exc),
    }
    sections.append(_metadata_block(payload))
    return "".join(sections)


def _attribution(execution: ToolExecution) -> dict[str, Any]:
    return {
        "execution_id": execution.execution_id,
        "attempt": execution.attempt,
        "stage": execution.stage,
        "task": execution.task,
        "identity": execution.identity,
    }


def _next_attempt(invocation_id: str, tool: str, operation: str, identity: str) -> int:
    key = (invocation_id, tool, operation, identity)
    with _ATTEMPTS_GUARD:
        attempt = _ATTEMPTS.get(key, 0) + 1
        _ATTEMPTS[key] = attempt
    return attempt


def _metadata_block(payload: Mapping[str, Any]) -> str:
    return "\n[scpolya-tool]\n" + "\n".join(
        f"{key}={json.dumps(value, ensure_ascii=True, sort_keys=True)}"
        for key, value in payload.items()
    ) + "\n[/scpolya-tool]\n"


def _output_section(label: str, value: str) -> str:
    bounded = _bounded_text(value)
    return f"\n--- {label} ---\n{bounded}" + ("" if bounded.endswith("\n") else "\n")


def _execution_status(exc: BaseException | None, returncode: int | None) -> str:
    if isinstance(exc, KeyboardInterrupt):
        return "cancelled"
    if exc is not None:
        return "failed"
    if returncode not in (None, 0):
        return "failed"
    return "succeeded"


def _normalize_command(command: Sequence[Any] | str) -> list[str] | str:
    if isinstance(command, str):
        return command
    return [str(value) for value in command]


def _display_command(command: list[str] | str) -> str:
    if isinstance(command, str):
        return command
    return shlex.join(command)


def _exception_text(exc: BaseException | None) -> str:
    if exc is None:
        return ""
    return f"{type(exc).__name__}: {exc}"


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _bounded_text(value: str) -> str:
    if len(value) <= _MAX_CAPTURE_CHARS:
        return value
    omitted = len(value) - _MAX_CAPTURE_CHARS
    return f"[... {omitted} leading characters omitted ...]\n{value[-_MAX_CAPTURE_CHARS:]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _snapshot(path: Path | None) -> _CompatibilitySnapshot | None:
    if path is None:
        return None
    try:
        size = path.stat().st_size
        tail_offset = max(0, size - _COMPATIBILITY_TAIL_BYTES)
        with path.open("rb") as handle:
            handle.seek(tail_offset)
            tail = handle.read()
        return _CompatibilitySnapshot(
            size=size,
            tail_offset=tail_offset,
            tail=tail,
        )
    except OSError:
        return _CompatibilitySnapshot(size=0, tail_offset=0, tail=b"")


def _read_compatibility_delta(
    path: Path | None,
    snapshot: _CompatibilitySnapshot | None,
) -> str | None:
    if path is None:
        return None
    try:
        final_size = path.stat().st_size
        start = 0
        if snapshot is not None and final_size >= snapshot.size:
            with path.open("rb") as handle:
                handle.seek(snapshot.tail_offset)
                current_tail = handle.read(len(snapshot.tail))
            if current_tail == snapshot.tail:
                start = snapshot.size
        read_start = max(start, final_size - _MAX_CAPTURE_CHARS)
        with path.open("rb") as handle:
            handle.seek(read_start)
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        if read_start > start:
            omitted = read_start - start
            text = f"[... {omitted} leading bytes omitted ...]\n{text}"
        return text
    except OSError:
        return None


def _best_effort_append(path: Path, text: str) -> None:
    try:
        resolved = path.expanduser().absolute()
        lock = _path_lock(resolved)
        with lock:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with resolved.open("a", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
    except Exception:
        return


def _path_lock(path: Path) -> threading.Lock:
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(path, threading.Lock())
