from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CHILD_PROCESS: subprocess.Popen[bytes] | None = None
STOP_REQUESTED = False
MANAGED_JOB_ID_ENV = "SCPOLYA_UI_JOB_ID"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def write_status(status_path: Path, payload: dict[str, Any]) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = status_path.with_suffix(status_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(status_path)


def append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as handle:
        handle.write(message.encode("utf-8"))


def install_signal_handlers(log_path: Path, status_path: Path, base_status: dict[str, Any]) -> None:
    def request_stop(signum: int, _frame: object) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True
        append_log(log_path, f"\n[scpolya-ui-supervisor] stop signal={signum} at {utc_now()}\n")
        status = dict(base_status)
        status.update(
            {
                "status": "stopping",
                "updated_at": utc_now(),
                "stop_signal": signum,
            }
        )
        write_status(status_path, status)
        if CHILD_PROCESS is not None and CHILD_PROCESS.poll() is None:
            try:
                os.killpg(CHILD_PROCESS.pid, signal.SIGTERM)
            except ProcessLookupError:
                return

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def child_environment(argv: list[str], *, job_id: str = "") -> dict[str, str]:
    env = os.environ.copy()
    if job_id:
        env[MANAGED_JOB_ID_ENV] = job_id
    if not argv:
        return env
    executable = Path(argv[0]).expanduser()
    if not executable.is_absolute():
        return env
    bin_dir = str(executable.parent)
    current_path = env.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []
    if bin_dir not in path_parts:
        env["PATH"] = bin_dir + (os.pathsep + current_path if current_path else "")
    return env


def run_spec(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    job_id = str(spec["job_id"])
    argv = [str(item) for item in spec["argv"]]
    cwd = Path(spec["cwd"]).expanduser().resolve()
    log_path = Path(spec["log_path"]).expanduser()
    status_path = Path(spec["status_path"]).expanduser()
    shell_command = str(spec.get("command") or "")
    run_root = str(spec.get("run_root") or "")

    base_status: dict[str, Any] = {
        "job_id": job_id,
        "status": "starting",
        "created_at": str(spec.get("created_at") or utc_now()),
        "updated_at": utc_now(),
        "started_at": "",
        "finished_at": "",
        "supervisor_pid": os.getpid(),
        "child_pid": None,
        "exit_code": None,
        "cwd": str(cwd),
        "run_root": run_root,
        "log_path": str(log_path),
        "spec_path": str(spec_path),
        "launch_mode": str(spec.get("launch_mode") or "rerun"),
        "continue_from_run_record_id": str(
            spec.get("continue_from_run_record_id") or ""
        ),
    }
    write_status(status_path, base_status)
    install_signal_handlers(log_path, status_path, base_status)

    append_log(
        log_path,
        (
            f"[scpolya-ui-supervisor] started_at={utc_now()}\n"
            f"[scpolya-ui-supervisor] job_id={job_id}\n"
            f"[scpolya-ui-supervisor] cwd={cwd}\n"
            f"[scpolya-ui-supervisor] command={shell_command}\n\n"
        ),
    )

    global CHILD_PROCESS
    with log_path.open("ab", buffering=0) as log_file:
        CHILD_PROCESS = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=child_environment(argv, job_id=job_id),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        running_status = dict(base_status)
        running_status.update(
            {
                "status": "running",
                "started_at": utc_now(),
                "updated_at": utc_now(),
                "child_pid": CHILD_PROCESS.pid,
            }
        )
        write_status(status_path, running_status)
        return_code = CHILD_PROCESS.wait()

    final_status = dict(running_status)
    final_status.update(
        {
            "status": final_status_for_return_code(return_code),
            "finished_at": utc_now(),
            "updated_at": utc_now(),
            "exit_code": return_code,
        }
    )
    write_status(status_path, final_status)
    if run_root:
        try:
            from logging_config.runtime_session import (
                finalize_managed_invocation_metadata,
            )

            finalize_managed_invocation_metadata(
                run_root,
                job_id=job_id,
                pid=CHILD_PROCESS.pid,
                job_status=final_status["status"],
                exit_code=return_code,
                finished_at=final_status["finished_at"],
            )
        except Exception as exc:
            append_log(
                log_path,
                (
                    "\n[scpolya-ui-supervisor] invocation finalization warning: "
                    f"{type(exc).__name__}: {exc}\n"
                ),
            )
    append_log(
        log_path,
        (
            f"\n[scpolya-ui-supervisor] finished status={final_status['status']} "
            f"exit_code={return_code} at {final_status['finished_at']}\n"
        ),
    )
    return return_code


def final_status_for_return_code(return_code: int) -> str:
    if STOP_REQUESTED or return_code == -int(signal.SIGTERM):
        return "stopped"
    if return_code == 0:
        return "succeeded"
    return "failed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one scpolyA UI job spec and write durable status.")
    parser.add_argument("--spec", required=True, type=Path)
    args = parser.parse_args(argv)
    return run_spec(args.spec.expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
