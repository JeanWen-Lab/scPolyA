from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, List

from .command_builder import RunCommand
from .registry import WorkspaceRecord, ui_home, utc_now


JOBS_VERSION = 1


@dataclass
class JobRecord:
    id: str
    status: str
    workspace_id: str
    workspace_root: str
    command: str
    argv: List[str]
    config: dict[str, Any]
    created_at: str
    updated_at: str
    note: str = ""
    log_path: str = ""
    pid: int | None = None
    supervisor_pid: int | None = None
    exit_code: int | None = None
    cwd: str = ""
    run_root: str = ""
    started_at: str = ""
    finished_at: str = ""
    status_path: str = ""
    spec_path: str = ""
    launch_mode: str = "rerun"
    continue_from_run_record_id: str = ""


@dataclass
class JobRegistry:
    version: int = JOBS_VERSION
    jobs: List[JobRecord] = field(default_factory=list)


def jobs_path(home: Path | None = None) -> Path:
    return (home or ui_home()) / "jobs.json"


def load_jobs(home: Path | None = None) -> JobRegistry:
    path = jobs_path(home)
    if not path.exists():
        return JobRegistry()
    data = json.loads(path.read_text(encoding="utf-8"))
    jobs = [JobRecord(**item) for item in data.get("jobs", [])]
    return JobRegistry(version=int(data.get("version", JOBS_VERSION)), jobs=jobs)


def save_jobs(registry: JobRegistry, home: Path | None = None) -> None:
    path = jobs_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": registry.version,
        "jobs": [asdict(job) for job in registry.jobs],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def create_planned_job(
    registry: JobRegistry,
    *,
    workspace: WorkspaceRecord,
    command: RunCommand,
    note: str = "",
    launch_mode: str = "rerun",
    continue_from_run_record_id: str = "",
) -> JobRecord:
    now = utc_now()
    job = JobRecord(
        id=f"J-{uuid.uuid4().hex[:12]}",
        status="planned",
        workspace_id=workspace.id,
        workspace_root=workspace.root_path,
        command=command.shell_command,
        argv=list(command.argv),
        config=command.config,
        note=note,
        launch_mode=str(launch_mode or "rerun"),
        continue_from_run_record_id=str(continue_from_run_record_id or ""),
        created_at=now,
        updated_at=now,
    )
    registry.jobs.insert(0, job)
    return job


def recent_jobs(
    registry: JobRegistry,
    *,
    workspace_id: str | None = None,
    limit: int = 20,
) -> list[JobRecord]:
    jobs: Iterable[JobRecord] = registry.jobs
    if workspace_id is not None:
        jobs = (job for job in jobs if job.workspace_id == workspace_id)
    return list(jobs)[:limit]
