from __future__ import annotations

import hashlib
import json
import shlex
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Mapping

from task_manage import load_run_config_snapshot, preview_peak_artifacts_for_config

from .command_builder import RunCommand, materialize_execution_config
from .jobs import JobRegistry
from .registry import WorkspaceRecord, ui_home, utc_now
from .run_data import InvocationLogSummary, discover_run_log_catalog

RUN_RECORDS_VERSION = 3


@dataclass
class RunRecord:
    id: str
    kind: str
    status: str
    workspace_id: str
    workspace_root: str
    label: str
    config: dict[str, Any]
    config_hash: str
    created_at: str
    updated_at: str
    command: str = ""
    argv: List[str] = field(default_factory=list)
    job_id: str = ""
    study_id: str = ""
    variant_id: str = ""
    parent_run_id: str = ""
    output_dir: str = ""
    run_id: str = ""
    root_path: str = ""
    db_path: str = ""
    expected_artifacts: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    execution_config: dict[str, Any] = field(default_factory=dict)
    execution_config_hash: str = ""
    finished_at: str = ""
    invocation_id: str = ""
    invocation_metadata_path: str = ""
    invocation_schema_version: str = ""
    invocation_config_hash: str = ""
    history_source: str = ""
    config_source: str = ""
    raw_status: str = ""
    invocation_error: str = ""
    source_run_binding: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionConfigResolution:
    config: dict[str, Any] = field(default_factory=dict)
    config_hash: str = ""
    source: str = ""
    error: str = ""

    @property
    def available(self) -> bool:
        return bool(self.config) and not self.error


@dataclass
class RunRecordRegistry:
    version: int = RUN_RECORDS_VERSION
    records: List[RunRecord] = field(default_factory=list)


def run_records_path(home: Path | None = None) -> Path:
    return (home or ui_home()) / "run_records.json"


def load_run_records(home: Path | None = None) -> RunRecordRegistry:
    path = run_records_path(home)
    if not path.exists():
        return RunRecordRegistry()
    data = json.loads(path.read_text(encoding="utf-8"))
    records = [RunRecord(**item) for item in data.get("records", [])]
    return RunRecordRegistry(
        version=max(int(data.get("version", RUN_RECORDS_VERSION)), RUN_RECORDS_VERSION),
        records=records,
    )


def save_run_records(registry: RunRecordRegistry, home: Path | None = None) -> None:
    path = run_records_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": registry.version,
        "records": [asdict(record) for record in registry.records],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def ensure_base_run_record(
    registry: RunRecordRegistry,
    *,
    workspace: WorkspaceRecord,
    config: Mapping[str, Any] | None = None,
) -> RunRecord:
    existing = next(
        (
            record
            for record in registry.records
            if record.kind == "base" and record.workspace_id == workspace.id
        ),
        None,
    )
    if existing is not None:
        return existing
    payload = dict(config or {})
    execution_config = dict(payload)
    now = utc_now()
    record = RunRecord(
        id=f"R-{uuid.uuid4().hex[:12]}",
        kind="base",
        status="registered",
        workspace_id=workspace.id,
        workspace_root=workspace.root_path,
        label=workspace.alias,
        config=payload,
        config_hash=config_hash(payload),
        command="",
        argv=[],
        output_dir=str(payload.get("output_dir") or ""),
        run_id=str(payload.get("run_id") or ""),
        root_path=workspace.root_path,
        db_path=workspace.db_path,
        expected_artifacts=_expected_artifacts(execution_config),
        execution_config=execution_config,
        execution_config_hash=config_hash(execution_config),
        created_at=now,
        updated_at=now,
    )
    registry.records.insert(0, record)
    return record


def create_run_record(
    registry: RunRecordRegistry,
    *,
    workspace: WorkspaceRecord,
    kind: str,
    command: RunCommand,
    label: str,
    status: str = "planned",
    job_id: str = "",
    study_id: str = "",
    variant_id: str = "",
    parent_run_id: str = "",
    note: str = "",
) -> RunRecord:
    now = utc_now()
    config = dict(command.config)
    execution_config = materialize_execution_config(command.argv)
    record = RunRecord(
        id=f"R-{uuid.uuid4().hex[:12]}",
        kind=kind,
        status=status,
        workspace_id=workspace.id,
        workspace_root=workspace.root_path,
        label=label,
        config=config,
        config_hash=config_hash(config),
        command=command.shell_command,
        argv=list(command.argv),
        job_id=job_id,
        study_id=study_id,
        variant_id=variant_id,
        parent_run_id=parent_run_id,
        output_dir=str(config.get("output_dir") or ""),
        run_id=str(config.get("run_id") or ""),
        root_path=_run_root_from_config(config),
        db_path=_state_db_from_config(config),
        expected_artifacts=_expected_artifacts(execution_config),
        note=note,
        execution_config=execution_config,
        execution_config_hash=config_hash(execution_config),
        created_at=now,
        updated_at=now,
    )
    registry.records.insert(0, record)
    return record


def update_planned_run_record(
    record: RunRecord,
    *,
    command: RunCommand,
    label: str,
    note: str | None = None,
) -> RunRecord:
    if record.status != "planned":
        raise ValueError(f"Run record {record.id} is not a planned draft")
    config = dict(command.config)
    execution_config = materialize_execution_config(command.argv)
    record.label = label
    if note is not None:
        record.note = note
    record.config = config
    record.config_hash = config_hash(config)
    record.command = command.shell_command
    record.argv = list(command.argv)
    record.output_dir = str(config.get("output_dir") or "")
    record.run_id = str(config.get("run_id") or "")
    record.root_path = _run_root_from_config(config)
    record.db_path = _state_db_from_config(config)
    record.expected_artifacts = _expected_artifacts(execution_config)
    record.execution_config = execution_config
    record.execution_config_hash = config_hash(execution_config)
    record.updated_at = utc_now()
    return record


def find_run_record(registry: RunRecordRegistry, record_id: str | None) -> RunRecord | None:
    if not record_id:
        return None
    return next((record for record in registry.records if record.id == record_id), None)


def recent_run_records(
    registry: RunRecordRegistry,
    *,
    workspace_id: str | None = None,
    limit: int = 20,
    include_superseded_drafts: bool = False,
) -> list[RunRecord]:
    records: Iterable[RunRecord] = registry.records
    if workspace_id is not None:
        records = (record for record in records if record.workspace_id == workspace_id)
    rows = list(records)
    if not include_superseded_drafts:
        rows = _without_superseded_planned_drafts(rows)
    return rows[:limit]


def _without_superseded_planned_drafts(records: Iterable[RunRecord]) -> list[RunRecord]:
    newer_execution_keys: set[tuple[str, str]] = set()
    visible_draft_keys: set[tuple[str, str]] = set()
    visible: list[RunRecord] = []
    for record in records:
        if record.kind != "manual_rerun":
            visible.append(record)
            continue
        key = (record.workspace_id, record.config_hash)
        if record.status == "planned":
            if key in newer_execution_keys or key in visible_draft_keys:
                continue
            visible_draft_keys.add(key)
        else:
            newer_execution_keys.add(key)
        visible.append(record)
    return visible


def sync_run_records_from_jobs(
    registry: RunRecordRegistry,
    job_registry: JobRegistry,
) -> bool:
    jobs = {job.id: job for job in job_registry.jobs}
    changed = False
    for record in registry.records:
        if not record.job_id:
            continue
        job = jobs.get(record.job_id)
        if job is None:
            continue
        previous_status = record.status
        if previous_status != job.status:
            record.status = job.status
            record.updated_at = job.updated_at
            record.finished_at = job.finished_at if job.status in {
                "succeeded",
                "failed",
                "stopped",
                "exited",
            } else ""
            changed = True
            if job.status == "succeeded":
                changed = _snapshot_completed_execution_config(record) or changed
    return changed


def sync_run_records_from_invocations(
    registry: RunRecordRegistry,
    job_registry: JobRegistry,
    workspaces: Iterable[WorkspaceRecord],
) -> bool:
    """Discover immutable invocation history without touching pipeline state."""

    jobs = {job.id: job for job in job_registry.jobs}
    by_invocation = {
        record.invocation_id: record
        for record in registry.records
        if record.invocation_id
    }
    by_job = {
        record.job_id: record
        for record in registry.records
        if record.job_id
    }
    changed = False
    for workspace in workspaces:
        catalog = discover_run_log_catalog(workspace.root_path, path_mappings=tuple(workspace.path_mappings))
        for invocation in reversed(catalog.invocations):
            record = by_invocation.get(invocation.invocation_id)
            if record is None and invocation.job_id:
                record = by_job.get(invocation.job_id)
            if record is None:
                record = _run_record_from_invocation(workspace, invocation, jobs.get(invocation.job_id))
                registry.records.insert(0, record)
                by_invocation[invocation.invocation_id] = record
                if invocation.job_id:
                    by_job[invocation.job_id] = record
                changed = True
                continue
            changed = _update_run_record_from_invocation(
                record,
                workspace,
                invocation,
                jobs.get(invocation.job_id),
            ) or changed
            by_invocation[invocation.invocation_id] = record
    if changed:
        registry.records.sort(
            key=lambda record: (record.created_at, record.updated_at, record.id),
            reverse=True,
        )
    return changed


def _run_record_from_invocation(
    workspace: WorkspaceRecord,
    invocation: InvocationLogSummary,
    job: Any | None,
) -> RunRecord:
    execution_config, config_source = _verified_invocation_config(invocation)
    output_dir, run_id = _invocation_target(invocation, execution_config)
    started_at = invocation.started_at.isoformat()
    finished_at = invocation.finished_at.isoformat() if invocation.finished_at else ""
    status = _invocation_display_status(invocation, job)
    record = RunRecord(
        id=f"R-I-{invocation.invocation_id.replace('-', '')[:16]}",
        kind="cli_invocation",
        status=status,
        workspace_id=workspace.id,
        workspace_root=workspace.root_path,
        label=workspace.alias,
        config=dict(execution_config),
        config_hash=config_hash(execution_config) if execution_config else invocation.config_hash,
        created_at=started_at,
        updated_at=finished_at or started_at,
        command=shlex.join(invocation.argv),
        argv=list(invocation.argv),
        job_id=invocation.job_id,
        output_dir=output_dir,
        run_id=run_id,
        root_path=invocation.run_root,
        db_path=workspace.db_path,
        note="",
        execution_config=dict(execution_config),
        execution_config_hash=config_hash(execution_config) if execution_config else "",
        finished_at=finished_at,
        invocation_id=invocation.invocation_id,
        invocation_metadata_path=invocation.metadata_path,
        invocation_schema_version=invocation.schema_version,
        invocation_config_hash=invocation.config_hash,
        history_source="invocation",
        config_source=config_source,
        raw_status=invocation.status,
        invocation_error=invocation.error,
    )
    return record


def _update_run_record_from_invocation(
    record: RunRecord,
    workspace: WorkspaceRecord,
    invocation: InvocationLogSummary,
    job: Any | None,
) -> bool:
    execution_config, config_source = _verified_invocation_config(invocation)
    output_dir, run_id = _invocation_target(invocation, execution_config)
    values: dict[str, Any] = {
        "status": _invocation_display_status(invocation, job),
        "created_at": invocation.started_at.isoformat(),
        "workspace_root": workspace.root_path,
        "command": shlex.join(invocation.argv),
        "argv": list(invocation.argv),
        "job_id": invocation.job_id or record.job_id,
        "output_dir": output_dir or record.output_dir,
        "run_id": run_id or record.run_id,
        "root_path": invocation.run_root,
        "db_path": workspace.db_path,
        "updated_at": (
            invocation.finished_at.isoformat()
            if invocation.finished_at
            else invocation.started_at.isoformat()
        ),
        "finished_at": invocation.finished_at.isoformat() if invocation.finished_at else "",
        "invocation_id": invocation.invocation_id,
        "invocation_metadata_path": invocation.metadata_path,
        "invocation_schema_version": invocation.schema_version,
        "invocation_config_hash": invocation.config_hash,
        "config_source": config_source,
        "raw_status": invocation.status,
        "invocation_error": invocation.error,
        "history_source": "invocation",
    }
    if record.kind == "cli_invocation":
        values["note"] = ""
    if execution_config:
        values.update(
            {
                "config": dict(execution_config),
                "config_hash": config_hash(execution_config),
                "execution_config": dict(execution_config),
                "execution_config_hash": config_hash(execution_config),
            }
        )
    changed = False
    for key, value in values.items():
        if getattr(record, key) != value:
            setattr(record, key, value)
            changed = True
    return changed


def _verified_invocation_config(
    invocation: InvocationLogSummary,
) -> tuple[dict[str, Any], str]:
    if invocation.resolved_config:
        return _normalize_value(invocation.resolved_config), "invocation_snapshot"
    try:
        argv = tuple(invocation.argv)
        if len(argv) >= 2 and argv[1] == "run":
            argv = ("python", *argv)
        recovered = materialize_execution_config(argv)
    except Exception:
        return {}, "unavailable"
    if _full_config_hash(recovered) != invocation.config_hash:
        return {}, "unavailable"
    return recovered, "argv_verified"


def _invocation_target(
    invocation: InvocationLogSummary,
    execution_config: Mapping[str, Any],
) -> tuple[str, str]:
    output_dir = str(invocation.output_dir or execution_config.get("output_dir") or "")
    run_id = str(invocation.run_id or execution_config.get("run_id") or "")
    if not output_dir:
        output_dir = _argv_option(invocation.argv, "--output-dir")
    if not run_id:
        run_id = _argv_option(invocation.argv, "--run-id")
    if not output_dir and run_id and Path(invocation.run_root).name == run_id:
        output_dir = str(Path(invocation.run_root).parent)
    return output_dir, run_id


def _argv_option(argv: Iterable[str], option: str) -> str:
    values = tuple(argv)
    try:
        index = values.index(option)
    except ValueError:
        return ""
    return values[index + 1] if index + 1 < len(values) else ""


def _invocation_display_status(invocation: InvocationLogSummary, job: Any | None) -> str:
    if invocation.status != "running":
        return invocation.status
    if job is not None and getattr(job, "status", "") in {"starting", "running", "stopping"}:
        return str(job.status)
    return "unknown"


def _full_config_hash(config: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _normalize_value(config),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_execution_config(record: RunRecord) -> ExecutionConfigResolution:
    if record.execution_config:
        config = dict(record.execution_config)
        calculated_hash = config_hash(config)
        if record.execution_config_hash and record.execution_config_hash != calculated_hash:
            return ExecutionConfigResolution(
                error=(
                    f"Stored execution config hash mismatch for {record.id}: "
                    f"expected {record.execution_config_hash}, calculated {calculated_hash}"
                )
            )
        return ExecutionConfigResolution(
            config=config,
            config_hash=calculated_hash,
            source=record.config_source or "snapshot",
        )

    if record.history_source == "invocation" and record.config_source == "unavailable":
        return ExecutionConfigResolution(
            error=(
                f"Historical execution config is unavailable for {record.id}: "
                "the invocation snapshot is absent and its argv does not match the recorded hash"
            )
        )

    if record.argv:
        try:
            config = materialize_execution_config(record.argv)
        except Exception as exc:
            return ExecutionConfigResolution(
                error=f"Execution config recovery failed: {type(exc).__name__}: {exc}"
            )
        return ExecutionConfigResolution(
            config=config,
            config_hash=config_hash(config),
            source="argv",
        )

    if record.kind == "base" and record.config:
        config = dict(record.config)
        return ExecutionConfigResolution(
            config=config,
            config_hash=config_hash(config),
            source="workspace_snapshot",
        )

    return ExecutionConfigResolution(
        error=f"Execution config is unavailable for {record.id}: no snapshot or run argv"
    )


def config_hash(config: Mapping[str, Any]) -> str:
    payload = json.dumps(_normalize_value(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _snapshot_completed_execution_config(record: RunRecord) -> bool:
    if not record.db_path:
        return False
    try:
        config = load_run_config_snapshot(record.db_path)
    except Exception:
        return False
    normalized = _normalize_value(config)
    if not isinstance(normalized, dict):
        return False
    execution_hash = config_hash(normalized)
    expected_artifacts = _expected_artifacts(normalized)
    if (
        record.execution_config == normalized
        and record.execution_config_hash == execution_hash
        and record.expected_artifacts == expected_artifacts
    ):
        return False
    record.execution_config = normalized
    record.execution_config_hash = execution_hash
    record.expected_artifacts = expected_artifacts
    return True


def _expected_artifacts(config: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = preview_peak_artifacts_for_config(config)
    return artifacts.as_dict() if artifacts is not None else {}


def _run_root_from_config(config: Mapping[str, Any]) -> str:
    output_dir = config.get("output_dir")
    run_id = config.get("run_id")
    if not output_dir or not run_id:
        return ""
    return str(Path(str(output_dir)).expanduser() / str(run_id))


def _state_db_from_config(config: Mapping[str, Any]) -> str:
    root = _run_root_from_config(config)
    return str(Path(root) / "state" / "state.sqlite") if root else ""


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_normalize_value(val) for val in value]
    if isinstance(value, list):
        return [_normalize_value(val) for val in value]
    if isinstance(value, Path):
        return str(value)
    return value
