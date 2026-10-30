from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from logging_config.runtime_session import finalize_managed_invocation_metadata

from .command_builder import RunCommand
from .jobs import JobRecord, JobRegistry, create_planned_job
from .registry import WorkspaceRecord, ui_home, utc_now


RUNNING_PROCESSES: dict[str, subprocess.Popen[bytes]] = {}
TERMINATED_EXIT_CODE = -int(signal.SIGTERM)
REPO_ROOT = Path(__file__).resolve().parents[1]


def job_logs_dir(home: Path | None = None) -> Path:
    return (home or ui_home()) / "job_logs"


def job_specs_dir(home: Path | None = None) -> Path:
    return (home or ui_home()) / "job_specs"


def job_status_dir(home: Path | None = None) -> Path:
    return (home or ui_home()) / "job_status"


def start_job(
    registry: JobRegistry,
    *,
    workspace: WorkspaceRecord,
    command: RunCommand,
    cwd: str | Path | None = None,
    note: str = "",
    home: Path | None = None,
) -> JobRecord:
    job = create_planned_job(registry, workspace=workspace, command=command, note=note)
    launch_job(job, cwd=cwd, home=home)
    return job


def launch_job(
    job: JobRecord,
    *,
    cwd: str | Path | None = None,
    home: Path | None = None,
) -> JobRecord:
    run_cwd = Path(cwd).expanduser().resolve() if cwd is not None else Path(job.workspace_root).expanduser()
    log_dir = job_logs_dir(home)
    spec_dir = job_specs_dir(home)
    status_dir = job_status_dir(home)
    log_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)
    status_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job.id}.log"
    spec_path = spec_dir / f"{job.id}.json"
    status_path = status_dir / f"{job.id}.json"

    now = utc_now()
    job.status = "starting"
    job.log_path = str(log_path)
    job.status_path = str(status_path)
    job.spec_path = str(spec_path)
    job.cwd = str(run_cwd)
    job.run_root = _run_root_from_config(job.config, cwd=run_cwd)
    job.started_at = now
    job.finished_at = ""
    job.updated_at = now
    job.exit_code = None
    job.pid = None
    job.supervisor_pid = None

    spec = {
        "job_id": job.id,
        "argv": list(job.argv),
        "command": job.command,
        "config": job.config,
        "cwd": str(run_cwd),
        "run_root": job.run_root,
        "log_path": str(log_path),
        "status_path": str(status_path),
        "created_at": job.created_at,
        "launch_mode": job.launch_mode,
        "continue_from_run_record_id": job.continue_from_run_record_id,
    }
    spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")

    with log_path.open("ab", buffering=0) as log_file:
        header = (
            f"[scpolya-ui] supervisor_requested_at={now}\n"
            f"[scpolya-ui] spec={spec_path}\n"
            f"[scpolya-ui] status={status_path}\n\n"
        )
        log_file.write(header.encode("utf-8"))
        process = subprocess.Popen(
            [sys.executable, "-m", "scpolya_ui.job_supervisor", "--spec", str(spec_path)],
            cwd=str(REPO_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    job.supervisor_pid = process.pid
    RUNNING_PROCESSES[job.id] = process
    return job


def refresh_registry_jobs(registry: JobRegistry, *, workspace_id: str | None = None) -> bool:
    changed = False
    for job in registry.jobs:
        if workspace_id is not None and job.workspace_id != workspace_id:
            continue
        before = (job.status, job.exit_code, job.updated_at, job.finished_at)
        refresh_job_status(job)
        after = (job.status, job.exit_code, job.updated_at, job.finished_at)
        changed = changed or before != after
    return changed


def refresh_job_status(job: JobRecord) -> JobRecord:
    status_data = read_job_status(job)
    if status_data:
        _apply_status_data(job, status_data)
        if job.status not in {"starting", "running", "stopping"}:
            process = RUNNING_PROCESSES.pop(job.id, None)
            if process is not None:
                process.poll()
            _finalize_job_invocation(job)
            return job

    if job.status not in {"starting", "running", "stopping"}:
        return job

    process = RUNNING_PROCESSES.get(job.id)
    if process is not None:
        return_code = process.poll()
        if return_code is None:
            return job
        RUNNING_PROCESSES.pop(job.id, None)
        if not status_data:
            _finish_job(job, return_code, stopped=job.status == "stopping")
        return job

    if job.supervisor_pid and _pid_is_alive(job.supervisor_pid):
        return job
    if job.pid and _pid_is_alive(job.pid):
        return job

    stop_was_requested = job.status == "stopping"
    job.status = "stopped" if stop_was_requested else "exited"
    if stop_was_requested and job.exit_code is None:
        job.exit_code = TERMINATED_EXIT_CODE
    job.finished_at = job.finished_at or utc_now()
    job.updated_at = utc_now()
    _append_log_footer(
        job,
        (
            "stop completed; process tree is no longer alive"
            if stop_was_requested
            else "process exited; exit code unavailable after UI restart"
        ),
    )
    _finalize_job_invocation(job)
    return job


def stop_job(job: JobRecord) -> JobRecord:
    refresh_job_status(job)
    if job.status not in {"starting", "running", "stopping"}:
        return job

    job.status = "stopping"
    job.updated_at = utc_now()
    status_data = read_job_status(job)
    child_pid = _int_or_none(status_data.get("child_pid")) if status_data else job.pid
    supervisor_pid = _int_or_none(status_data.get("supervisor_pid")) if status_data else job.supervisor_pid

    if child_pid:
        _terminate_pid(child_pid)
    elif supervisor_pid:
        _terminate_pid(supervisor_pid)

    process = RUNNING_PROCESSES.get(job.id)
    if process is not None and process.poll() is not None:
        _finish_job(job, process.returncode)
        return job

    if process is not None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is not None:
            RUNNING_PROCESSES.pop(job.id, None)
            _finish_job(job, process.returncode, stopped=True)
            return job

    job.status = "stopping"
    job.updated_at = utc_now()
    _append_log_footer(job, "stop requested from UI; awaiting supervisor exit")
    return job


def read_job_log(job: JobRecord, *, max_bytes: int = 65536) -> str:
    if not job.log_path:
        return ""
    path = Path(job.log_path).expanduser()
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        return handle.read().decode("utf-8", errors="replace")


def read_job_status(job: JobRecord) -> dict[str, Any]:
    if not job.status_path:
        return {}
    path = Path(job.status_path).expanduser()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _apply_status_data(job: JobRecord, data: dict[str, Any]) -> None:
    job.status = str(data.get("status") or job.status)
    job.updated_at = str(data.get("updated_at") or job.updated_at)
    job.started_at = str(data.get("started_at") or job.started_at)
    job.finished_at = str(data.get("finished_at") or job.finished_at)
    job.cwd = str(data.get("cwd") or job.cwd)
    job.run_root = str(data.get("run_root") or job.run_root)
    job.log_path = str(data.get("log_path") or job.log_path)
    job.spec_path = str(data.get("spec_path") or job.spec_path)
    job.pid = _int_or_none(data.get("child_pid")) or job.pid
    job.supervisor_pid = _int_or_none(data.get("supervisor_pid")) or job.supervisor_pid
    if data.get("exit_code") is not None:
        job.exit_code = _int_or_none(data.get("exit_code"))


def _finish_job(job: JobRecord, return_code: int | None, *, stopped: bool = False) -> None:
    code = int(return_code) if return_code is not None else None
    job.exit_code = code
    if stopped:
        job.status = "stopped"
    elif code == 0:
        job.status = "succeeded"
    else:
        job.status = "failed"
    job.finished_at = utc_now()
    job.updated_at = job.finished_at
    _append_log_footer(job, f"finished status={job.status} exit_code={job.exit_code}")
    _finalize_job_invocation(job)


def _finalize_job_invocation(job: JobRecord) -> bool:
    if job.status not in {"succeeded", "failed", "stopped", "exited", "cancelled"}:
        return False
    run_root = job.run_root or _run_root_from_config(job.config, cwd=job.cwd or REPO_ROOT)
    if not run_root:
        return False
    try:
        return finalize_managed_invocation_metadata(
            run_root,
            job_id=job.id,
            pid=job.pid,
            job_status=job.status,
            exit_code=job.exit_code,
            finished_at=job.finished_at,
        )
    except (OSError, ValueError, TypeError):
        return False


def _run_root_from_config(config: dict[str, Any], *, cwd: str | Path) -> str:
    output_dir = str(config.get("output_dir") or "").strip()
    run_id = str(config.get("run_id") or "").strip()
    if not output_dir or not run_id:
        return ""
    output_path = Path(output_dir).expanduser()
    if not output_path.is_absolute():
        output_path = Path(cwd).expanduser() / output_path
    clean_run_id = re.sub(r"[^a-zA-Z0-9._-]", "_", run_id)
    return str((output_path / clean_run_id).resolve())


def _append_log_footer(job: JobRecord, message: str) -> None:
    if not job.log_path:
        return
    path = Path(job.log_path).expanduser()
    try:
        with path.open("ab", buffering=0) as handle:
            handle.write(f"\n[scpolya-ui] {message} at {utc_now()}\n".encode("utf-8"))
    except OSError:
        return


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        raise
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
