from __future__ import annotations

from pathlib import Path

from scpolya_ui.job_runner import refresh_registry_jobs
from scpolya_ui.jobs import JobRegistry, save_jobs
from scpolya_ui.registry import WorkspaceRegistry
from scpolya_ui.run_data import RunStateSummary, summarize_run_state
from scpolya_ui.run_records import (
    RunRecord,
    RunRecordRegistry,
    find_run_record,
    recent_run_records,
    save_run_records,
    sync_run_records_from_invocations,
    sync_run_records_from_jobs,
)
from scpolya_ui.view_models.run_vm import (
    LogicalRunSummary,
    RunRecordSummary,
    run_record_summary,
)
from scpolya_ui.view_models.run_vm import (
    logical_run_summaries as build_logical_run_summaries,
)


def load_run_summary(state_or_db_path: str | Path) -> RunStateSummary:
    return summarize_run_state(state_or_db_path)


def run_record_summaries(
    registry: RunRecordRegistry,
    *,
    workspace_id: str | None = None,
    limit: int = 20,
) -> list[RunRecordSummary]:
    return [
        run_record_summary(record)
        for record in recent_run_records(registry, workspace_id=workspace_id, limit=limit)
    ]


def logical_run_summaries(
    registry: RunRecordRegistry,
    *,
    workspace_id: str | None = None,
    limit: int = 50,
) -> list[LogicalRunSummary]:
    records = recent_run_records(
        registry,
        workspace_id=workspace_id,
        limit=max(1, len(registry.records)),
    )
    return build_logical_run_summaries(records)[:limit]


def get_run_record(registry: RunRecordRegistry, record_id: str | None) -> RunRecord | None:
    return find_run_record(registry, record_id)


def default_run_record(
    registry: RunRecordRegistry,
    *,
    workspace_id: str | None = None,
) -> RunRecord | None:
    records = recent_run_records(registry, workspace_id=workspace_id, limit=1)
    return records[0] if records else None


def selected_run_record_summary(registry: RunRecordRegistry, record_id: str | None) -> RunRecordSummary | None:
    record = find_run_record(registry, record_id)
    return run_record_summary(record) if record else None


def refresh_run_record_statuses(
    run_records: RunRecordRegistry,
    jobs: JobRegistry,
    *,
    home: str | Path | None = None,
    workspaces: WorkspaceRegistry | None = None,
) -> bool:
    home_path = Path(home).expanduser() if home is not None else None
    jobs_changed = refresh_registry_jobs(jobs)
    records_changed = sync_run_records_from_jobs(run_records, jobs)
    if workspaces is not None:
        records_changed = sync_run_records_from_invocations(
            run_records,
            jobs,
            workspaces.workspaces,
        ) or records_changed
    if jobs_changed:
        save_jobs(jobs, home_path)
    if records_changed:
        save_run_records(run_records, home_path)
    return jobs_changed or records_changed
