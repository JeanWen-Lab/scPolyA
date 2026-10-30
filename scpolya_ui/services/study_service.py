from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from task_manage import (
    ParameterStudyPreview,
    expand_parameter_grid,
    preview_parameter_study_from_state,
    preview_peak_artifacts_for_config,
    preview_rerun_from_state,
)

from scpolya_ui.command_builder import RunCommand, materialize_execution_config
from scpolya_ui.jobs import JobRegistry, create_planned_job, save_jobs
from scpolya_ui.job_runner import launch_job, refresh_registry_jobs, stop_job
from scpolya_ui.registry import WorkspaceRecord, utc_now
from scpolya_ui.run_records import (
    RunRecordRegistry,
    create_run_record,
    ensure_base_run_record,
    find_run_record,
    save_run_records,
    sync_run_records_from_jobs,
)
from scpolya_ui.services.config_service import build_command_for_config, materialize_config_for_execution
from scpolya_ui.studies import (
    StudyRecord,
    StudyRegistry,
    create_study,
    find_study,
    recent_studies,
    save_studies,
    terminal_study_status,
)
from scpolya_ui.view_models.study_vm import StudySummary, StudyVariantSummary, study_summary, study_variant_summary


@dataclass(frozen=True)
class StudyBuildBundle:
    preview: ParameterStudyPreview
    commands: dict[str, RunCommand]
    grid: dict[str, list[Any]]


@dataclass(frozen=True)
class StudyQueueSummary:
    study_id: str
    status: str
    planned_jobs: int
    launched_job_id: str = ""


def study_count(registry: StudyRegistry, *, workspace_id: str | None = None) -> int:
    return len(recent_studies(registry, workspace_id=workspace_id, limit=10_000))


def study_summaries(
    registry: StudyRegistry,
    *,
    workspace_id: str | None = None,
    limit: int = 20,
) -> list[StudySummary]:
    return [study_summary(study) for study in recent_studies(registry, workspace_id=workspace_id, limit=limit)]


def study_variant_summaries(registry: StudyRegistry, study_id: str | None) -> list[StudyVariantSummary]:
    study = find_study(registry, study_id)
    if study is None:
        return []
    return [study_variant_summary(variant) for variant in study.variants]


def get_study(registry: StudyRegistry, study_id: str | None) -> StudyRecord | None:
    return find_study(registry, study_id)


def build_study_preview_bundle(
    state_db_path: str | Path,
    grid: Mapping[str, list[Any]],
    *,
    base_config: Mapping[str, Any],
    python_executable: str | None = None,
    main_path: str | Path = "main.py",
) -> StudyBuildBundle:
    variants = expand_parameter_grid(grid)
    execution_base_config = materialize_config_for_execution(
        base_config,
        python_executable=python_executable or sys.executable,
        main_path=main_path,
    )
    raw_preview = preview_parameter_study_from_state(
        state_db_path,
        variants,
        base_config=execution_base_config,
    )
    commands: dict[str, RunCommand] = {}
    materialized_variants = []
    for variant in raw_preview.variants:
        command = build_command_for_config(
            variant.config,
            python_executable=python_executable or sys.executable,
            main_path=main_path,
        )
        execution_config = materialize_execution_config(command.argv)
        commands[variant.id] = command
        materialized_variants.append(
            replace(
                variant,
                config=execution_config,
                rerun_preview=preview_rerun_from_state(state_db_path, execution_config),
                peak_artifacts=preview_peak_artifacts_for_config(execution_config),
            )
        )
    preview = ParameterStudyPreview(
        base_config=execution_base_config,
        variants=tuple(materialized_variants),
    )
    return StudyBuildBundle(preview=preview, commands=commands, grid={str(key): list(value) for key, value in grid.items()})


def study_annotation_schema_notices(bundle: StudyBuildBundle) -> tuple[str, ...]:
    """Distinct pre-launch annotation-schema notices of the study's variants."""

    notices: list[str] = []
    for variant in bundle.preview.variants:
        notice = getattr(variant.rerun_preview, "annotation_schema_notice", "")
        if notice and notice not in notices:
            notices.append(notice)
    return tuple(notices)


def save_study_draft(
    studies: StudyRegistry,
    *,
    workspace: WorkspaceRecord,
    bundle: StudyBuildBundle,
    name: str,
    base_run_record_id: str = "",
    base_run_label: str = "",
    home: str | Path | None = None,
) -> StudyRecord:
    home_path = Path(home).expanduser() if home is not None else None
    study = create_study(
        studies,
        workspace=workspace,
        preview=bundle.preview,
        commands=bundle.commands,
        name=name,
        genes=[],
        grid=bundle.grid,
        base_run_record_id=base_run_record_id,
        base_run_label=base_run_label,
    )
    save_studies(studies, home_path)
    return study


def ensure_study_variant_jobs(
    jobs: JobRegistry,
    run_records: RunRecordRegistry,
    studies: StudyRegistry,
    *,
    workspace: WorkspaceRecord,
    study: StudyRecord,
    base_config: Mapping[str, Any] | None = None,
    base_run_record_id: str = "",
    home: str | Path | None = None,
) -> StudyQueueSummary:
    home_path = Path(home).expanduser() if home is not None else None
    base_record = find_run_record(run_records, base_run_record_id or study.base_run_record_id)
    if base_record is None:
        base_record = ensure_base_run_record(run_records, workspace=workspace, config=base_config or {})
    planned = 0
    for variant in study.variants:
        command = RunCommand(
            argv=tuple(variant.argv),
            shell_command=variant.command,
            config=variant.config,
        )
        job = next((item for item in jobs.jobs if item.id == variant.job_id), None) if variant.job_id else None
        if job is None:
            job = create_planned_job(jobs, workspace=workspace, command=command, note=f"{study.id} {variant.id}")
            variant.job_id = job.id
            planned += 1
        if not variant.run_record_id:
            record = create_run_record(
                run_records,
                workspace=workspace,
                kind="study_variant",
                command=command,
                label=f"{study.name} {variant.id}",
                status=job.status,
                job_id=job.id,
                study_id=study.id,
                variant_id=variant.id,
                parent_run_id=base_record.id,
                note=variant.label,
            )
            variant.run_record_id = record.id
    study.updated_at = utc_now()
    save_jobs(jobs, home_path)
    save_run_records(run_records, home_path)
    save_studies(studies, home_path)
    return StudyQueueSummary(study_id=study.id, status=study.status, planned_jobs=planned)


def advance_study_queue(
    jobs: JobRegistry,
    run_records: RunRecordRegistry,
    studies: StudyRegistry,
    *,
    workspace: WorkspaceRecord,
    study: StudyRecord,
    base_config: Mapping[str, Any] | None = None,
    base_run_record_id: str = "",
    cwd: str | Path | None = None,
    home: str | Path | None = None,
) -> StudyQueueSummary:
    home_path = Path(home).expanduser() if home is not None else None
    ensure_study_variant_jobs(
        jobs,
        run_records,
        studies,
        workspace=workspace,
        study=study,
        base_config=base_config,
        base_run_record_id=base_run_record_id,
        home=home_path,
    )
    refresh_registry_jobs(jobs, workspace_id=workspace.id)
    job_by_id = {job.id: job for job in jobs.jobs}
    now_running = {"starting", "running", "stopping"}
    terminal = {"succeeded", "failed", "stopped", "exited"}
    variant_jobs = [job_by_id.get(variant.job_id) for variant in study.variants if variant.job_id]
    launched_job_id = ""

    if any(job is not None and job.status in now_running for job in variant_jobs):
        study.status = "running"
    else:
        next_job = next((job_by_id.get(variant.job_id) for variant in study.variants if job_by_id.get(variant.job_id) and job_by_id[variant.job_id].status == "planned"), None)
        if next_job is not None:
            launch_job(next_job, cwd=cwd, home=home_path)
            launched_job_id = next_job.id
            study.status = "running"
            study.started_at = study.started_at or utc_now()
            study.finished_at = ""
        elif variant_jobs and all(job is not None and job.status in terminal for job in variant_jobs):
            study.status = terminal_study_status(job.status for job in variant_jobs if job is not None)
            study.finished_at = max((job.updated_at for job in variant_jobs if job is not None), default=utc_now())

    study.updated_at = utc_now()
    sync_run_records_from_jobs(run_records, jobs)
    save_jobs(jobs, home_path)
    save_run_records(run_records, home_path)
    save_studies(studies, home_path)
    planned = sum(1 for job in variant_jobs if job is not None and job.status == "planned")
    return StudyQueueSummary(
        study_id=study.id,
        status=study.status,
        planned_jobs=planned,
        launched_job_id=launched_job_id,
    )


def stop_study_queue(
    jobs: JobRegistry,
    run_records: RunRecordRegistry,
    studies: StudyRegistry,
    *,
    study: StudyRecord,
    home: str | Path | None = None,
) -> StudyQueueSummary:
    home_path = Path(home).expanduser() if home is not None else None
    job_by_id = {job.id: job for job in jobs.jobs}
    for variant in study.variants:
        job = job_by_id.get(variant.job_id)
        if job is not None and job.status in {"starting", "running", "stopping"}:
            stop_job(job)
    study.status = "stopped"
    study.finished_at = utc_now()
    study.updated_at = study.finished_at
    sync_run_records_from_jobs(run_records, jobs)
    save_jobs(jobs, home_path)
    save_run_records(run_records, home_path)
    save_studies(studies, home_path)
    return StudyQueueSummary(study_id=study.id, status=study.status, planned_jobs=0)
