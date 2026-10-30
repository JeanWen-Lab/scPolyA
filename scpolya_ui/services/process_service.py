from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scpolya_ui.jobs import JobRecord


@dataclass(frozen=True)
class ManagedProcess:
    pid: int
    ppid: int
    pgid: int
    state: str
    role: str
    command: str


@dataclass(frozen=True)
class ManagedProcessInventory:
    processes: tuple[ManagedProcess, ...] = ()
    error: str = ""


def managed_process_inventory(
    job: JobRecord,
    *,
    proc_root: str | Path = "/proc",
) -> ManagedProcessInventory:
    root = Path(proc_root)
    if not root.is_dir():
        return ManagedProcessInventory(error=f"Process inventory is unavailable: {root}")

    supervisor_pid = _positive_pid(job.supervisor_pid)
    pipeline_pid = _positive_pid(job.pid)
    roots = {pid for pid in (supervisor_pid, pipeline_pid) if pid is not None}
    if not roots:
        return ManagedProcessInventory()

    process_table: dict[int, ManagedProcess] = {}
    try:
        process_dirs = tuple(root.iterdir())
    except OSError as exc:
        return ManagedProcessInventory(error=f"Failed to read process inventory: {exc}")
    for process_dir in process_dirs:
        if not process_dir.name.isdigit() or not process_dir.is_dir():
            continue
        process = _read_process(process_dir)
        if process is not None:
            process_table[process.pid] = process

    included = {pid for pid in roots if pid in process_table}
    if pipeline_pid is not None:
        included.update(
            process.pid
            for process in process_table.values()
            if process.pgid == pipeline_pid
        )

    children_by_parent: dict[int, list[int]] = {}
    for process in process_table.values():
        children_by_parent.setdefault(process.ppid, []).append(process.pid)
    queue = list(included)
    while queue:
        parent_pid = queue.pop()
        for child_pid in children_by_parent.get(parent_pid, ()):
            if child_pid in included:
                continue
            included.add(child_pid)
            queue.append(child_pid)

    rows = []
    for pid in included:
        process = process_table[pid]
        role = (
            "supervisor"
            if pid == supervisor_pid
            else "pipeline"
            if pid == pipeline_pid
            else "managed"
        )
        rows.append(
            ManagedProcess(
                pid=process.pid,
                ppid=process.ppid,
                pgid=process.pgid,
                state=process.state,
                role=role,
                command=process.command,
            )
        )
    role_order = {"supervisor": 0, "pipeline": 1, "managed": 2}
    rows.sort(key=lambda process: (role_order[process.role], process.pid))
    return ManagedProcessInventory(processes=tuple(rows))


def _read_process(process_dir: Path) -> ManagedProcess | None:
    try:
        stat_line = (process_dir / "stat").read_text(encoding="utf-8")
        closing_paren = stat_line.rfind(")")
        if closing_paren < 0:
            return None
        pid = int(stat_line[: stat_line.find(" ")])
        fields = stat_line[closing_paren + 1 :].strip().split()
        if len(fields) < 3:
            return None
        state = fields[0]
        ppid = int(fields[1])
        pgid = int(fields[2])
        raw_command = (process_dir / "cmdline").read_bytes()
        command_parts = [
            part.decode("utf-8", errors="replace")
            for part in raw_command.split(b"\0")
            if part
        ]
        if command_parts:
            command = " ".join(command_parts)
        else:
            command = stat_line[stat_line.find("(") + 1 : closing_paren]
    except (OSError, UnicodeError, ValueError):
        return None
    return ManagedProcess(
        pid=pid,
        ppid=ppid,
        pgid=pgid,
        state=state,
        role="managed",
        command=command,
    )


def _positive_pid(value: int | None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
