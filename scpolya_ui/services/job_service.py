from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from scpolya_ui.command_builder import RunCommand, build_run_command, materialize_execution_config
from scpolya_ui.jobs import JobRegistry, JobRecord, create_planned_job, recent_jobs, save_jobs
from scpolya_ui.job_runner import launch_job
from scpolya_ui.registry import WorkspaceRecord, utc_now
from scpolya_ui.run_data import summarize_run_state
from scpolya_ui.run_records import (
    RunRecord,
    RunRecordRegistry,
    config_hash,
    create_run_record,
    ensure_base_run_record,
    find_run_record,
    recent_run_records,
    resolve_execution_config,
    save_run_records,
    update_planned_run_record,
)


ACTIVE_JOB_STATUSES = frozenset({"starting", "running", "stopping"})
INTERRUPTED_RUN_STATUSES = frozenset({"failed", "stopped", "exited", "cancelled"})
RUN_LAUNCH_MODES = frozenset({"rerun", "continue"})
CONTINUE_STATE_READY_PAIRS = frozenset(
    {
        ("ready", "ready"),
        ("uninitialized", "uninitialized"),
    }
)


@dataclass(frozen=True)
class JobSummary:
    id: str
    status: str
    workspace_id: str
    command: str
    exit_code: int | None = None
    log_path: str = ""


@dataclass(frozen=True)
class RunLaunchSummary:
    job_id: str
    run_record_id: str
    status: str
    command: str
    launch_mode: str = "rerun"


def latest_job_status(registry: JobRegistry) -> str:
    return registry.jobs[0].status if registry.jobs else "none"


def job_summary(job: JobRecord) -> JobSummary:
    return JobSummary(
        id=job.id,
        status=job.status,
        workspace_id=job.workspace_id,
        command=job.command,
        exit_code=job.exit_code,
        log_path=job.log_path,
    )


def job_summaries(
    registry: JobRegistry,
    *,
    workspace_id: str | None = None,
    limit: int = 20,
) -> list[JobSummary]:
    return [job_summary(job) for job in recent_jobs(registry, workspace_id=workspace_id, limit=limit)]


def create_run_setup_job(
    jobs: JobRegistry,
    run_records: RunRecordRegistry,
    *,
    workspace: WorkspaceRecord,
    command: RunCommand,
    base_config: dict | None = None,
    launch: bool = False,
    cwd: str | Path | None = None,
    home: str | Path | None = None,
    existing_job_id: str = "",
    existing_run_record_id: str = "",
    note: str = "",
    launch_mode: str = "rerun",
    continue_from_run_record_id: str = "",
    base_run_record_id: str = "",
) -> RunLaunchSummary:
    home_path = Path(home).expanduser() if home is not None else None
    normalized_launch_mode = _normalize_launch_mode(launch_mode)
    continue_source_id = (
        str(continue_from_run_record_id or "")
        if normalized_launch_mode == "continue"
        else ""
    )
    continue_record = None
    if normalized_launch_mode == "continue":
        continue_record = validate_continue_launch(
            run_records,
            continue_from_run_record_id=continue_source_id,
            command=command,
        )
        if continue_record.workspace_id != workspace.id:
            raise ValueError("Continue source does not belong to the selected workspace")
    base_record = ensure_base_run_record(run_records, workspace=workspace, config=base_config or {})
    source_record = continue_record
    if source_record is None and base_run_record_id:
        source_record = find_run_record(run_records, base_run_record_id)
        if source_record is None or source_record.workspace_id != workspace.id:
            raise ValueError("Selected base run does not belong to this workspace; select it again")
    bound_config = dict(base_config or {})
    if source_record is not None:
        source_config = resolve_execution_config(source_record)
        # Continue already validates the exact materialized command against its
        # interrupted source; callers may supply a sparse pre-parser config.
        equivalent = source_config.available and source_config.config == bound_config
        if source_config.available and not equivalent and base_config is not None:
            try:
                equivalent = materialize_execution_config(build_run_command(bound_config).argv) == source_config.config
            except ValueError:
                equivalent = False
        if not source_config.available or (continue_record is None and base_config is not None and not equivalent):
            raise ValueError("Selected base run config changed; reload the source before planning")
        bound_config = dict(source_config.config)
    source_binding = {
        "kind": "selected_run" if source_record is not None else "workspace_state_at_plan_time",
        "run_record_id": source_record.id if source_record is not None else base_record.id,
        "invocation_id": source_record.invocation_id if source_record is not None else "",
        "root_path": source_record.root_path if source_record is not None else workspace.root_path,
        "config": bound_config, "config_hash": config_hash(bound_config),
    }
    pair = _existing_run_setup_pair(
        jobs,
        run_records,
        workspace=workspace,
        command=command,
        job_id=existing_job_id,
        run_record_id=existing_run_record_id,
        launch=launch,
    )
    if pair is None:
        pair = _matching_planned_pair(
            jobs,
            run_records,
            workspace=workspace,
            command=command,
            launch_mode=normalized_launch_mode,
            continue_from_run_record_id=continue_source_id,
        )

    if pair is not None:
        job, record = pair
        if job.status == "planned" and record.status == "planned":
            _update_planned_job(
                job,
                command,
                note=note,
                launch_mode=normalized_launch_mode,
                continue_from_run_record_id=continue_source_id,
            )
            update_planned_run_record(
                record,
                command=command,
                label=record.label,
                note=note,
            )
            record.parent_run_id = source_binding["run_record_id"]
            record.source_run_binding = source_binding
            save_jobs(jobs, home_path)
            save_run_records(run_records, home_path)
            if launch:
                launch_job(job, cwd=cwd, home=home_path)
                record.status = job.status
                record.updated_at = job.updated_at
    else:
        sequence = _next_manual_run_number(run_records, workspace.id)
        generated_note = note.strip()
        label = _manual_run_label(sequence)
        job = create_planned_job(
            jobs,
            workspace=workspace,
            command=command,
            note=generated_note,
            launch_mode=normalized_launch_mode,
            continue_from_run_record_id=continue_source_id,
        )
        record = create_run_record(
            run_records,
            workspace=workspace,
            kind="manual_rerun",
            command=command,
            label=label,
            status=job.status,
            job_id=job.id,
            parent_run_id=source_binding["run_record_id"],
            note=generated_note,
        )
        record.source_run_binding = source_binding
        save_jobs(jobs, home_path)
        save_run_records(run_records, home_path)
        if launch:
            launch_job(job, cwd=cwd, home=home_path)
            record.status = job.status
            record.updated_at = job.updated_at
    save_jobs(jobs, home_path)
    save_run_records(run_records, home_path)
    return RunLaunchSummary(
        job_id=job.id,
        run_record_id=record.id,
        status=job.status,
        command=job.command,
        launch_mode=job.launch_mode,
    )


def validate_continue_launch(
    run_records: RunRecordRegistry,
    *,
    continue_from_run_record_id: str,
    command: RunCommand,
) -> RunRecord:
    source = find_run_record(run_records, continue_from_run_record_id)
    if source is None:
        raise ValueError("Continue requires an existing interrupted run record")
    if source.status not in INTERRUPTED_RUN_STATUSES:
        raise ValueError(
            f"Run record {source.id} has status {source.status!r}; Continue requires "
            "a failed, stopped, exited, or cancelled attempt"
        )
    if not source.db_path or not Path(source.db_path).expanduser().is_file():
        raise ValueError(f"Continue source state database is missing: {source.db_path or '-'}")
    state_summary = summarize_run_state(source.db_path)
    state_pair = (state_summary.completion_state, state_summary.ledger_state)
    if state_pair not in CONTINUE_STATE_READY_PAIRS:
        raise ValueError(
            "Continue requires readable current task state; "
            f"completion={state_summary.completion_state}, "
            f"parameter_ledger={state_summary.ledger_state}"
        )

    source_config = resolve_execution_config(source)
    if not source_config.available:
        raise ValueError(
            f"Continue source execution config is unavailable: {source_config.error or source.id}"
        )
    proposed_config = materialize_execution_config(command.argv)
    proposed_hash = config_hash(proposed_config)
    if proposed_hash != source_config.config_hash:
        raise ValueError(
            "Continue requires the exact execution config of the interrupted run; "
            "use Rerun when changing parameters"
        )
    return source


def _existing_run_setup_pair(
    jobs: JobRegistry,
    run_records: RunRecordRegistry,
    *,
    workspace: WorkspaceRecord,
    command: RunCommand,
    job_id: str,
    run_record_id: str,
    launch: bool,
) -> tuple[JobRecord, RunRecord] | None:
    if not job_id or not run_record_id:
        return _active_pair_for_target(jobs, run_records, workspace=workspace, command=command) if launch else None
    job = next((item for item in jobs.jobs if item.id == job_id), None)
    record = find_run_record(run_records, run_record_id)
    if not _is_linked_manual_pair(job, record, workspace.id):
        return _active_pair_for_target(jobs, run_records, workspace=workspace, command=command) if launch else None
    assert job is not None and record is not None
    if job.status == "planned" and record.status == "planned":
        return job, record
    if launch and job.status in ACTIVE_JOB_STATUSES and _same_run_target(job.config, command.config):
        return job, record
    return _active_pair_for_target(jobs, run_records, workspace=workspace, command=command) if launch else None


def _matching_planned_pair(
    jobs: JobRegistry,
    run_records: RunRecordRegistry,
    *,
    workspace: WorkspaceRecord,
    command: RunCommand,
    launch_mode: str,
    continue_from_run_record_id: str,
) -> tuple[JobRecord, RunRecord] | None:
    target_hash = config_hash(command.config)
    visible_records = recent_run_records(
        run_records,
        workspace_id=workspace.id,
        limit=max(20, len(run_records.records)),
    )
    for record in visible_records:
        if record.kind != "manual_rerun" or record.status != "planned" or record.config_hash != target_hash:
            continue
        job = next((item for item in jobs.jobs if item.id == record.job_id), None)
        if (
            _is_linked_manual_pair(job, record, workspace.id)
            and job is not None
            and job.status == "planned"
            and job.launch_mode == launch_mode
            and job.continue_from_run_record_id == continue_from_run_record_id
        ):
            return job, record
    return None


def _active_pair_for_target(
    jobs: JobRegistry,
    run_records: RunRecordRegistry,
    *,
    workspace: WorkspaceRecord,
    command: RunCommand,
) -> tuple[JobRecord, RunRecord] | None:
    for job in jobs.jobs:
        if (
            job.workspace_id != workspace.id
            or job.status not in ACTIVE_JOB_STATUSES
            or not _same_run_target(job.config, command.config)
        ):
            continue
        record = next((item for item in run_records.records if item.job_id == job.id), None)
        if _is_linked_manual_pair(job, record, workspace.id):
            assert record is not None
            return job, record
    return None


def _is_linked_manual_pair(
    job: JobRecord | None,
    record: RunRecord | None,
    workspace_id: str,
) -> bool:
    return bool(
        job is not None
        and record is not None
        and job.workspace_id == workspace_id
        and record.workspace_id == workspace_id
        and record.kind == "manual_rerun"
        and record.job_id == job.id
    )


def _same_run_target(left: dict, right: dict) -> bool:
    return (
        str(left.get("output_dir") or ""),
        str(left.get("run_id") or ""),
    ) == (
        str(right.get("output_dir") or ""),
        str(right.get("run_id") or ""),
    )


def _update_planned_job(
    job: JobRecord,
    command: RunCommand,
    *,
    note: str = "",
    launch_mode: str = "rerun",
    continue_from_run_record_id: str = "",
) -> None:
    job.command = command.shell_command
    job.argv = list(command.argv)
    job.config = dict(command.config)
    job.note = note.strip()
    job.launch_mode = _normalize_launch_mode(launch_mode)
    job.continue_from_run_record_id = str(continue_from_run_record_id or "")
    job.updated_at = utc_now()


def _normalize_launch_mode(value: str) -> str:
    normalized = str(value or "rerun").strip().lower()
    if normalized not in RUN_LAUNCH_MODES:
        raise ValueError(f"Unsupported run launch mode: {value!r}")
    return normalized


def _next_manual_run_number(run_records: RunRecordRegistry, workspace_id: str) -> int:
    """Return a stable human sequence for newly-created manual runs."""
    highest = 0
    used_labels: set[str] = set()
    pattern = re.compile(r"^Run\s+(\d+)\b", re.IGNORECASE)
    for record in run_records.records:
        if record.workspace_id != workspace_id or record.kind != "manual_rerun":
            continue
        label = " ".join(str(record.label or "").split())
        used_labels.add(label.casefold())
        match = pattern.match(label)
        if match:
            highest = max(highest, int(match.group(1)))
    candidate = highest + 1
    while _manual_run_label(candidate).casefold() in used_labels:
        candidate += 1
    return candidate


def _manual_run_label(sequence: int) -> str:
    return f"Run {sequence:02d}"
