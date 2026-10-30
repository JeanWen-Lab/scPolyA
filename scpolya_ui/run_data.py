from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .registry import PathMapping, apply_path_mappings_to_value

from task_manage import (
    RunConfigPreview,
    load_run_config_snapshot,
    preview_rerun_from_state,
)
from task_manage.task_completion import (
    LegacyTaskCompletionAccessError,
    TaskCompletionLoadError,
    TaskCompletionRecordV2,
    load_task_completion_snapshot_v2,
)
from task_manage.task_parameter_ledger import (
    CurrentTaskParameterLedgerRecordV3,
    TaskParameterLedgerError,
    load_current_task_parameter_ledger_records_v3,
)


@dataclass
class StageStatus:
    stage: str
    done_rows: int = 0
    ledger_rows: int = 0
    done_known_rows: int = 0
    queued_rows: int = 0


@dataclass
class TaskActivity:
    stage: str
    task: str
    state: str
    identity: str
    updated_at: str


@dataclass
class RunStateSummary:
    db_path: str
    exists: bool
    size_bytes: int = 0
    done_rows: int = 0
    ledger_rows: int = 0
    spec_rows: int = 0
    artifact_ref_rows: int = 0
    stages: List[StageStatus] = field(default_factory=list)
    recent_tasks: List[TaskActivity] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    config_error: str = ""
    completion_state: str = "unavailable"
    completion_error: str = ""
    ledger_state: str = "unavailable"
    ledger_error: str = ""
    completion_authority_mode: str = "unavailable"
    completion_source: str = "unavailable"
    legacy_fallback_count: int = 0


@dataclass
class LogFileSummary:
    path: str
    label: str
    size_bytes: int
    modified_at: str
    containment_root: str = ""


INVOCATION_LOG_SCHEMA_V1 = "scpolya.logging.invocation.v1"
INVOCATION_LOG_SCHEMA_V2 = "scpolya.logging.invocation.v2"
INVOCATION_LOG_SCHEMAS = frozenset({INVOCATION_LOG_SCHEMA_V1, INVOCATION_LOG_SCHEMA_V2})
INVOCATION_LOG_STATUSES = frozenset({"running", "succeeded", "failed", "cancelled"})
MAX_INVOCATION_METADATA_BYTES = 2 * 1024 * 1024
PIPELINE_STAGE_LOG_ORDER = (
    "bulk_references",
    "register_inputs",
    "init_references",
    "preprocess_units",
    "extract_subsets",
    "calculate_umi",
    "merge_units",
    "call_peaks",
    "annotate_peaks",
    "annotate_bams",
    "count_matrix",
    "summarize_matrix",
    "rna_gene_matrix",
)
_PIPELINE_STAGE_LOG_RANK = {
    name: index for index, name in enumerate(PIPELINE_STAGE_LOG_ORDER)
}


@dataclass(frozen=True)
class InvocationToolLogGroup:
    name: str
    logs: tuple[LogFileSummary, ...]


@dataclass(frozen=True)
class InvocationLogSummary:
    key: str
    metadata_path: str
    directory_name: str
    invocation_id: str
    schema_version: str
    job_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    pid: int
    hostname: str
    run_root: str
    output_dir: str
    run_id: str
    argv: tuple[str, ...]
    config_hash: str
    config_schema_version: int | None
    config_source: str
    resolved_config: dict[str, Any]
    error: str
    run_log: LogFileSummary | None
    stage_logs: tuple[LogFileSummary, ...]
    tool_groups: tuple[InvocationToolLogGroup, ...]

    @property
    def log_count(self) -> int:
        return int(self.run_log is not None) + len(self.stage_logs) + sum(
            len(group.logs) for group in self.tool_groups
        )

    @property
    def logs(self) -> tuple[LogFileSummary, ...]:
        rows: list[LogFileSummary] = []
        if self.run_log is not None:
            rows.append(self.run_log)
        rows.extend(self.stage_logs)
        for group in self.tool_groups:
            rows.extend(group.logs)
        return tuple(rows)


@dataclass(frozen=True)
class RunLogCatalog:
    run_root: str
    logs_root: str
    invocations: tuple[InvocationLogSummary, ...]
    compatibility_logs: tuple[LogFileSummary, ...]
    ignored_metadata_count: int = 0


def summarize_run_state(state_or_db_path: str | Path) -> RunStateSummary:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.exists():
        return RunStateSummary(db_path=str(db_path), exists=False, error="state.sqlite not found")

    summary = RunStateSummary(
        db_path=str(db_path),
        exists=True,
        size_bytes=db_path.stat().st_size,
    )
    try:
        summary.config = load_run_config_snapshot(db_path)
    except Exception as exc:
        summary.config_error = str(exc)
        summary.error = _join_errors(summary.error, summary.config_error)

    completion_records: tuple[TaskCompletionRecordV2, ...] = ()
    try:
        completion_snapshot = load_task_completion_snapshot_v2(db_path)
        completion_records = completion_snapshot.records
        summary.done_rows = len(completion_records)
        summary.completion_authority_mode = (
            completion_snapshot.authority_mode
        )
        summary.completion_source = completion_snapshot.completion_source
        summary.legacy_fallback_count = (
            completion_snapshot.legacy_fallback_count
        )
        summary.completion_state = _completion_snapshot_state(completion_snapshot)
    except Exception as exc:
        summary.completion_error = str(exc)
        summary.completion_state = _completion_error_state(exc)
        summary.error = _join_errors(summary.error, summary.completion_error)

    ledger_records: tuple[CurrentTaskParameterLedgerRecordV3, ...] = ()
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            has_ledger_state = _table_exists(
                con,
                "task_parameter_ledger_storage_state_v1",
            )
            has_ledger_v2 = _table_exists(con, "task_param_ledger_v2")
            if not has_ledger_state and not has_ledger_v2:
                summary.ledger_state = "uninitialized"
            else:
                try:
                    ledger_records = load_current_task_parameter_ledger_records_v3(con)
                    summary.ledger_rows = len(ledger_records)
                    summary.ledger_state = "ready"
                except Exception as exc:
                    summary.ledger_error = str(exc)
                    summary.ledger_state = _ledger_error_state(exc)
                    summary.error = _join_errors(summary.error, summary.ledger_error)
            summary.spec_rows = _count_table(con, "task_spec_registry")
            summary.artifact_ref_rows = _count_table(con, "task_artifact_refs")
            summary.stages = _stage_status(
                completion_records=completion_records,
                ledger_records=ledger_records,
            )
            summary.recent_tasks = _recent_tasks(ledger_records)
    except Exception as exc:
        if not summary.ledger_error:
            summary.ledger_error = str(exc)
            summary.ledger_state = _ledger_error_state(exc)
        summary.error = _join_errors(summary.error, str(exc))
    return summary


def _completion_snapshot_state(snapshot: Any) -> str:
    authority = str(getattr(snapshot, "authority_mode", "") or "")
    source = str(getattr(snapshot, "completion_source", "") or "")
    if authority == "uninitialized" or source == "unavailable":
        return "uninitialized"
    if authority == "unavailable":
        return "unavailable"
    return "ready"


def _completion_error_state(exc: Exception) -> str:
    if isinstance(exc, LegacyTaskCompletionAccessError):
        return "legacy_only"
    if isinstance(exc, TaskCompletionLoadError):
        return "malformed"
    return "unavailable"


def _ledger_error_state(exc: Exception) -> str:
    if isinstance(exc, TaskParameterLedgerError):
        return "malformed"
    return "unavailable"


def build_preview_from_json(
    state_or_db_path: str | Path,
    proposed_json: str,
    *,
    apply_mappings: bool = False,
    mapped_config: Dict[str, Any] | None = None,
) -> RunConfigPreview:
    proposed = json.loads(proposed_json or "{}")
    if not isinstance(proposed, dict):
        raise TypeError("Proposed config JSON must be an object")
    if apply_mappings and mapped_config is not None:
        proposed = mapped_config
    return preview_rerun_from_state(state_or_db_path, proposed)


def preview_to_impact_rows(preview: RunConfigPreview) -> List[Dict[str, Any]]:
    rows = []
    for impact in preview.rerun_preview.impacted_tasks:
        rows.append(
            {
                "key": f"{impact.stage}:{impact.task_base}",
                "stage": impact.stage,
                "task": impact.task_base,
                "kind": impact.kind,
                "params": _join(impact.changed_param_keys),
                "identity": _join(impact.changed_identity_keys),
                "inputs": _join(impact.changed_input_keys),
                "upstream": _join(f"{stage}:{task}" for stage, task in impact.upstream_task_keys),
                "artifacts": _join(impact.consumed_artifacts),
            }
        )
    return rows


def preview_to_output_impact_rows(preview: RunConfigPreview) -> List[Dict[str, Any]]:
    rows = []
    physical_output_impact = preview.physical_output_impact
    if physical_output_impact is None:
        return rows
    for impact in physical_output_impact.impacts:
        scope = impact.new_scope or impact.old_scope
        if scope is None:
            continue
        short_codes = [name.short_code for name in scope.artifact_names]
        legacy_names = [name.legacy_name for name in scope.artifact_names]
        payloads = {
            name.scope: dict(name.payload)
            for name in scope.artifact_names
        }
        rows.append(
            {
                "scope_id": impact.scope_id,
                "label": impact.label,
                "action": impact.action,
                "path": impact.path,
                "old_path": "" if impact.old_scope is None else impact.old_scope.path,
                "new_path": "" if impact.new_scope is None else impact.new_scope.path,
                "path_kind": scope.path_kind,
                "retention": scope.retention,
                "conditional": impact.conditional_on_producer_execution,
                "producers": _join(
                    f"{stage}:{task}"
                    for stage, task in impact.producer_task_keys
                ),
                "task_impacts": _join(
                    f"{stage}:{task}"
                    for stage, task in impact.task_impact_keys
                ),
                "short_codes": _join(short_codes),
                "legacy_names": _join(legacy_names),
                "config_payload": json.dumps(
                    payloads,
                    sort_keys=True,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ) if payloads else "",
                "note": impact.note,
            }
        )
    return rows


def output_projection_rows(preview: RunConfigPreview) -> List[Dict[str, Any]]:
    rows = []
    physical_output_impact = preview.physical_output_impact
    if physical_output_impact is None:
        return rows
    for scope in physical_output_impact.new_projection.scopes:
        rows.append(
            {
                "scope_id": scope.scope_id,
                "label": scope.label,
                "path": scope.path,
                "path_kind": scope.path_kind,
                "retention": scope.retention,
                "conditional": scope.conditional_on_producer_execution,
                "producers": _join(
                    f"{stage}:{task}"
                    for stage, task in scope.producer_task_keys
                ),
                "short_codes": _join(
                    name.short_code for name in scope.artifact_names
                ),
                "legacy_names": _join(
                    name.legacy_name for name in scope.artifact_names
                ),
                "note": scope.note,
            }
        )
    return rows


def changed_key_rows(preview: RunConfigPreview) -> List[Dict[str, str]]:
    rows = [{"row_key": f"raw:{key}", "type": "raw", "key": key} for key in preview.changed_raw_keys]
    rows.extend(
        {"row_key": f"effective:{key}", "type": "effective", "key": key}
        for key in preview.changed_effective_keys
    )
    return rows


def summary_cards(summary: RunStateSummary) -> Dict[str, str]:
    return {
        "done": str(summary.done_rows),
        "ledger": str(summary.ledger_rows),
        "specs": str(summary.spec_rows),
        "db": _format_bytes(summary.size_bytes),
    }


def recent_log_files(run_root: str | Path, *, limit: int = 30) -> List[LogFileSummary]:
    root = Path(run_root).expanduser()
    logs_dir = root / "logs"
    resolved = _resolved_directory_within(logs_dir, root)
    if resolved is None:
        return []

    rows = list(_discover_log_files(resolved, containment_root=resolved, label_root=resolved))
    rows.sort(key=lambda item: (item.modified_at, item.label), reverse=True)
    return rows[:limit]


def discover_run_log_catalog(
    run_root: str | Path, *, path_mappings: tuple[PathMapping, ...] = (),
) -> RunLogCatalog:
    root = Path(run_root).expanduser()
    logs_dir = root / "logs"
    resolved_logs = _resolved_directory_within(logs_dir, root)
    if resolved_logs is None:
        return RunLogCatalog(
            run_root=str(root),
            logs_root=str(logs_dir),
            invocations=(),
            compatibility_logs=(),
        )

    invocations_dir = resolved_logs / "invocations"
    invocations: list[InvocationLogSummary] = []
    ignored_metadata_count = 0
    if invocations_dir.is_dir():
        for metadata_path in sorted(invocations_dir.glob("*/invocation.json")):
            invocation = _load_invocation_log_summary(metadata_path, invocations_dir, path_mappings)
            if invocation is None:
                ignored_metadata_count += 1
                continue
            invocations.append(invocation)
    invocations.sort(
        key=lambda item: (item.started_at, item.directory_name),
        reverse=True,
    )

    compatibility_logs = list(
        _discover_log_files(
            resolved_logs,
            containment_root=resolved_logs,
            label_root=resolved_logs,
            excluded_prefix="invocations" if invocations else "",
        )
    )
    compatibility_logs.sort(key=lambda item: (item.modified_at, item.label), reverse=True)
    return RunLogCatalog(
        run_root=str(root.resolve()),
        logs_root=str(resolved_logs),
        invocations=tuple(invocations),
        compatibility_logs=tuple(compatibility_logs),
        ignored_metadata_count=ignored_metadata_count,
    )


def read_log_tail(
    log_path: str | Path,
    *,
    max_bytes: int = 65536,
    max_lines: int = 240,
    allowed_root: str | Path | None = None,
) -> str:
    path = Path(log_path).expanduser()
    if allowed_root is not None:
        resolved = _resolved_file_within(path, Path(allowed_root).expanduser())
        if resolved is None:
            return "Selected log path is outside its discovered log scope."
        path = resolved
    if not path.exists():
        return "Log file not found."
    if not path.is_file():
        return "Selected log path is not a file."
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError as exc:
        return f"Failed to read log: {exc}"
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def _load_invocation_log_summary(
    metadata_path: Path,
    invocations_dir: Path,
    path_mappings: tuple[PathMapping, ...] = (),
) -> InvocationLogSummary | None:
    resolved_metadata = _resolved_file_within(metadata_path, invocations_dir)
    if resolved_metadata is None or metadata_path.is_symlink():
        return None
    invocation_dir = resolved_metadata.parent
    try:
        if invocation_dir.parent != invocations_dir.resolve(strict=True):
            return None
        if resolved_metadata.stat().st_size > MAX_INVOCATION_METADATA_BYTES:
            return None
        payload = json.loads(resolved_metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    try:
        schema_version = _required_string(payload, "schema_version")
        if schema_version not in INVOCATION_LOG_SCHEMAS:
            return None
        invocation_id = _required_string(payload, "invocation_id")
        invocation_uuid = uuid.UUID(invocation_id)
        if not invocation_dir.name.endswith(f"-{invocation_uuid.hex[:8]}"):
            return None
        status = _required_string(payload, "status")
        if status not in INVOCATION_LOG_STATUSES:
            return None
        started_at = _parse_utc_datetime(_required_string(payload, "started_at"))
        finished_raw = payload.get("finished_at")
        duration_raw = payload.get("duration_seconds")
        if status == "running":
            if finished_raw != "" or duration_raw is not None:
                return None
            finished_at = None
            duration_seconds = None
        else:
            if not isinstance(finished_raw, str) or not finished_raw:
                return None
            finished_at = _parse_utc_datetime(finished_raw)
            duration_seconds = _nonnegative_number(duration_raw)
        pid = payload.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None
        argv_raw = payload.get("argv")
        if not isinstance(argv_raw, list) or not all(isinstance(value, str) for value in argv_raw):
            return None
        config_hash = _required_string(payload, "config_hash")
        error = payload.get("error")
        if not isinstance(error, str):
            return None
        stored_run_root = _required_string(payload, "run_root")
        discovered_run_root = invocations_dir.parent.parent.resolve(strict=True)
        job_id = payload.get("job_id", "")
        hostname = payload.get("hostname", "")
        if not isinstance(job_id, str) or not isinstance(hostname, str):
            return None
        output_dir = payload.get("output_dir", "")
        run_id = payload.get("run_id", "")
        config_source = payload.get("config_source", "argv")
        if not all(isinstance(value, str) for value in (output_dir, run_id, config_source)):
            return None
        config_schema_version = payload.get("config_schema_version")
        resolved_config_raw = payload.get("resolved_config", {})
        if schema_version == INVOCATION_LOG_SCHEMA_V2:
            if (
                isinstance(config_schema_version, bool)
                or not isinstance(config_schema_version, int)
                or config_schema_version <= 0
                or not isinstance(resolved_config_raw, dict)
            ):
                return None
            view_root = apply_path_mappings_to_value(stored_run_root, path_mappings)
            if Path(view_root).expanduser().resolve() != discovered_run_root:
                return None
            if _invocation_config_hash(resolved_config_raw) != config_hash:
                return None
            if str(resolved_config_raw.get("output_dir") or "") != output_dir:
                return None
            if str(resolved_config_raw.get("run_id") or "") != run_id:
                return None
        elif config_schema_version is not None and (
            isinstance(config_schema_version, bool) or not isinstance(config_schema_version, int)
        ):
            return None
        if not isinstance(resolved_config_raw, dict):
            return None
        paths = payload.get("paths")
        if not isinstance(paths, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in paths.items()
        ):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None

    run_log = _log_file_summary(
        invocation_dir / "run.log",
        containment_root=invocation_dir,
        label="run.log",
    )
    stage_logs = tuple(
        sorted(
            _discover_log_files(
                invocation_dir / "stages",
                containment_root=invocation_dir,
                label_root=invocation_dir / "stages",
            ),
            key=_stage_log_sort_key,
        )
    )
    tool_logs = _discover_log_files(
        invocation_dir / "tools",
        containment_root=invocation_dir,
        label_root=invocation_dir / "tools",
    )
    grouped_tools: dict[str, list[LogFileSummary]] = {}
    for log in tool_logs:
        parts = Path(log.label).parts
        group_name = parts[0] if len(parts) > 1 else "Other"
        label = str(Path(*parts[1:])) if len(parts) > 1 else log.label
        grouped_tools.setdefault(group_name, []).append(
            LogFileSummary(
                path=log.path,
                label=label,
                size_bytes=log.size_bytes,
                modified_at=log.modified_at,
                containment_root=log.containment_root,
            )
        )
    tool_groups = tuple(
        InvocationToolLogGroup(
            name=name,
            logs=tuple(sorted(logs, key=lambda item: item.label.casefold())),
        )
        for name, logs in sorted(grouped_tools.items(), key=lambda item: item[0].casefold())
    )
    return InvocationLogSummary(
        key=str(resolved_metadata),
        metadata_path=str(resolved_metadata),
        directory_name=invocation_dir.name,
        invocation_id=invocation_id,
        schema_version=schema_version,
        job_id=job_id.strip(),
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        pid=pid,
        hostname=hostname.strip(),
        run_root=stored_run_root if schema_version == INVOCATION_LOG_SCHEMA_V2 else str(discovered_run_root),
        output_dir=output_dir,
        run_id=run_id,
        argv=tuple(argv_raw),
        config_hash=config_hash,
        config_schema_version=config_schema_version,
        config_source=config_source,
        resolved_config=dict(resolved_config_raw),
        error=error,
        run_log=run_log,
        stage_logs=stage_logs,
        tool_groups=tool_groups,
    )


def _invocation_config_hash(config: Mapping[str, Any]) -> str:
    payload = json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stage_log_sort_key(log: LogFileSummary) -> tuple[int, str]:
    parts = Path(log.label).parts
    first_part = parts[0] if parts else log.label
    stage_name = Path(first_part).stem.casefold()
    return (
        _PIPELINE_STAGE_LOG_RANK.get(stage_name, len(PIPELINE_STAGE_LOG_ORDER)),
        log.label.casefold(),
    )


def _discover_log_files(
    search_root: Path,
    *,
    containment_root: Path,
    label_root: Path,
    excluded_prefix: str = "",
) -> tuple[LogFileSummary, ...]:
    resolved_search_root = _resolved_directory_within(search_root, containment_root)
    if resolved_search_root is None:
        return ()
    try:
        resolved_label_root = label_root.resolve(strict=True)
    except OSError:
        return ()
    rows: list[LogFileSummary] = []
    seen_paths: set[str] = set()
    for path in resolved_search_root.rglob("*.log"):
        try:
            relative = path.relative_to(resolved_label_root)
        except ValueError:
            continue
        if excluded_prefix and relative.parts and relative.parts[0] == excluded_prefix:
            continue
        summary = _log_file_summary(
            path,
            containment_root=containment_root,
            label=str(relative),
        )
        if summary is None or summary.path in seen_paths:
            continue
        seen_paths.add(summary.path)
        rows.append(summary)
    return tuple(rows)


def _log_file_summary(
    path: Path,
    *,
    containment_root: Path,
    label: str,
) -> LogFileSummary | None:
    resolved = _resolved_file_within(path, containment_root)
    if resolved is None:
        return None
    try:
        stat = resolved.stat()
        resolved_root = containment_root.resolve(strict=True)
    except OSError:
        return None
    return LogFileSummary(
        path=str(resolved),
        label=label,
        size_bytes=stat.st_size,
        modified_at=_format_mtime(stat.st_mtime),
        containment_root=str(resolved_root),
    )


def _resolved_directory_within(path: Path, root: Path) -> Path | None:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_dir() or not resolved.is_relative_to(resolved_root):
        return None
    return resolved


def _resolved_file_within(path: Path, root: Path) -> Path | None:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not resolved.is_relative_to(resolved_root):
        return None
    return resolved


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return parsed


def _nonnegative_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("duration must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("duration must be finite and nonnegative")
    return number


def _stage_status(
    *,
    completion_records: tuple[TaskCompletionRecordV2, ...] = (),
    ledger_records: tuple[CurrentTaskParameterLedgerRecordV3, ...] = (),
) -> List[StageStatus]:
    stages: Dict[str, StageStatus] = {}
    for record in completion_records:
        status = stages.setdefault(
            record.stage,
            StageStatus(stage=record.stage),
        )
        status.done_rows += 1
    for record in ledger_records:
        status = stages.setdefault(
            record.stage,
            StageStatus(stage=record.stage),
        )
        status.ledger_rows += 1
        status.done_known_rows += int(record.done_known)
        status.queued_rows += int(record.queued)
    return [stages[key] for key in sorted(stages)]


def _recent_tasks(
    ledger_records: tuple[CurrentTaskParameterLedgerRecordV3, ...],
    *,
    limit: int = 20,
) -> List[TaskActivity]:
    rows = []
    ordered = sorted(
        ledger_records,
        key=lambda record: (
            record.updated_at,
            record.task_coordinate_id,
        ),
        reverse=True,
    )[: max(0, int(limit))]
    for record in ordered:
        if record.done_known:
            state = "done"
        elif record.queued:
            state = "queued/running"
        else:
            state = "seen"
        rows.append(
            TaskActivity(
                stage=record.stage,
                task=record.task_base,
                state=state,
                identity=_coordinate_identity_label(record),
                updated_at=record.updated_at,
            )
        )
    return rows


def _count_table(con: sqlite3.Connection, table: str) -> int:
    if not _table_exists(con, table):
        return 0
    return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _join_errors(current: str, new: str) -> str:
    if not current:
        return new
    if not new:
        return current
    return f"{current}; {new}"


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _resolve_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path).expanduser()
    if path.name == "state.sqlite":
        return path
    if path.is_dir() and (path / "state.sqlite").exists():
        return path / "state.sqlite"
    return path / "state" / "state.sqlite"


def _join(values) -> str:
    return ", ".join(str(value) for value in values)


def _format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{value} B"


def _format_mtime(value: float) -> str:
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _coordinate_identity_label(
    record: CurrentTaskParameterLedgerRecordV3,
) -> str:
    fields = record.task_coordinate.identity
    parts = [
        f"{field.key}={field.legacy_value}"
        for field in fields[:4]
    ]
    if len(fields) > 4:
        parts.append("...")
    return ", ".join(parts)
