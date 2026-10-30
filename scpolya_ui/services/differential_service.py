"""Differential APA plan and attempt directories, and review-and-execute.

Plans and attempts are produced by ``scpolya_agent.differential_apa`` (the
``scpolya-operator differential-apa`` chain). The person reviews the exact
plan in the UI and starts it; the UI launches the adapter's own ``execute``
with the plan's bound Python, which repeats every check and runs exactly that
plan.json. No approval file is written or needed.
Reading uses the adapter's own verifying ``status`` and ``result`` readers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
from datetime import datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from scpolya_ui.errors import UserError
from scpolya_ui.registry import ui_home, utc_now

REGISTRY_FILE = "differential_analyses.json"
LAUNCH_FILE = "differential_launches.json"
LOG_DIR = "differential_logs"
APPROVAL_SCHEMA = "scpolya.differential_apa.approval.v1"  # legacy; no longer written
READY_STATUS = "READY_FOR_EXTERNAL_APPROVAL"  # legacy token kept for plan compatibility; means "ready"
# The adapter's resource contract: the caller supplies <=2 CPU, <=4 GiB, no swap.
ENVELOPE_PROPERTIES = ("CPUQuota=200%", "MemoryMax=4G", "MemorySwapMax=0")
PLAN_SCHEMA = "scpolya.differential_apa.plan.v1"
MAX_PLAN_BYTES = 2 * 1024 * 1024
TABLE_PAGE_ROWS = 100
TABLE_PAGE_BYTES = 262144


@dataclass
class AnalysisEntry:
    path: str
    workspace_id: str = ""
    added_at: str = ""


@dataclass(frozen=True)
class AnalysisView:
    path: str
    kind: str  # "plan" | "attempt" | "unknown"
    status: str
    error: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    attempt: dict[str, Any] = field(default_factory=dict)
    plan_sha256: str = ""
    issued_by: str = ""

    @property
    def eligibility(self) -> dict[str, Any]:
        source = self.attempt or self.plan
        value = source.get("eligibility")
        return value if isinstance(value, dict) else {}

    @property
    def request(self) -> dict[str, Any]:
        value = self.plan.get("request")
        return value if isinstance(value, dict) else {}

    @property
    def provenance_kind(self) -> str:
        provenance = self.attempt.get("provenance") or self.request.get("provenance") or {}
        return str(provenance.get("kind") or "") if isinstance(provenance, dict) else ""


def registry_path(home: Path | None = None) -> Path:
    return (home or ui_home()) / REGISTRY_FILE


def load_entries(home: Path | None = None) -> list[AnalysisEntry]:
    path = registry_path(home)
    if not path.is_file():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8")).get("analyses", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [AnalysisEntry(**{key: str(item.get(key, "")) for key in ("path", "workspace_id", "added_at")})
            for item in items if isinstance(item, dict) and item.get("path")]


def save_entries(entries: list[AnalysisEntry], home: Path | None = None) -> None:
    path = registry_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps({"version": 1, "analyses": [asdict(entry) for entry in entries]}, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def add_entry(path: str, *, workspace_id: str = "", home: Path | None = None) -> AnalysisEntry:
    resolved = analysis_directory(path)
    entries = load_entries(home)
    for entry in entries:
        if entry.path == str(resolved):
            return entry
    entry = AnalysisEntry(path=str(resolved), workspace_id=workspace_id, added_at=utc_now())
    save_entries([entry, *entries], home)
    return entry


def remove_entry(path: str, home: Path | None = None) -> None:
    save_entries([entry for entry in load_entries(home) if entry.path != path], home)


def analysis_directory(path: str) -> Path:
    """Accept a plan/attempt directory or its plan.json / attempt.json."""
    candidate = Path(path).expanduser()
    if candidate.is_file() and candidate.name in {"plan.json", "attempt.json"}:
        candidate = candidate.parent
    candidate = candidate.resolve()
    if not ((candidate / "plan.json").is_file() or (candidate / "attempt.json").is_file()):
        raise UserError("error.diff.no_analysis", f"No plan.json or attempt.json in {candidate}", path=str(candidate))
    return candidate


def describe(path: str, home: Path | None = None) -> AnalysisView:
    directory = Path(path)
    if (directory / "attempt.json").is_file():
        return _describe_attempt(directory)
    if (directory / "plan.json").is_file():
        return _describe_plan(directory)
    launch = load_launches(home).get(str(directory))
    if launch is not None:
        return _describe_launch(launch)
    return AnalysisView(path=path, kind="unknown", status="missing", error="plan.json / attempt.json not found")


# ---------- review and execute ----------
@dataclass(frozen=True)
class Launch:
    outdir: str
    plan: str
    approval: str  # legacy field; empty for launches without an approval file
    log: str
    pid: int
    issued_by: str  # legacy field; optional name, may be empty
    started_at: str
    envelope: str  # "systemd-scope" | "none"


_PROCESSES: dict[int, subprocess.Popen] = {}


def default_attempt_dir(plan_dir: str) -> str:
    directory = Path(plan_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return str(directory.parent / f"{directory.name}-attempt-{stamp}")


def validate_execute_request(view: AnalysisView, outdir: str) -> Path:
    """Checks the UI can make before launching; the adapter repeats its own."""
    if view.kind != "plan" or view.status != READY_STATUS:
        raise UserError("error.diff.not_ready", "Only a plan that passed its checks can be executed")
    plan_path = Path(view.path) / "plan.json"
    if hashlib.sha256(plan_path.read_bytes()).hexdigest() != view.plan_sha256:
        raise UserError("error.diff.plan_changed", "plan.json changed since it was opened; reopen it before running")
    target = Path(outdir.strip()).expanduser()
    if not target.is_absolute():
        raise UserError("error.diff.outdir_absolute", "Output directory must be an absolute path")
    target = target.resolve()
    if target.exists():
        raise UserError("error.diff.outdir_exists", "Output directory already exists; execution needs a new directory")
    plan_dir = Path(view.path).resolve()
    if target == plan_dir or plan_dir in target.parents:
        raise UserError("error.diff.outdir_inside_plan", "Output directory must be outside the plan directory")
    parent = next((item for item in target.parents if item.exists()), None)
    if parent is None or not os.access(parent, os.W_OK):
        raise UserError("error.diff.outdir_unwritable", f"Cannot create the output directory under {parent}", parent=str(parent))
    return target


def runtime_mismatch(plan: dict[str, Any]) -> str:
    """Compare this checkout and the plan's Python with what the plan bound."""
    from scpolya_agent import differential_apa
    from scpolya_diff.common import file_record

    python = plan.get("python") if isinstance(plan.get("python"), dict) else {}
    python_path = str(python.get("path") or "")
    if not python_path or not Path(python_path).is_file():
        return f"The plan's Python is missing: {python_path or '-'}"
    if file_record(python_path) != python:
        return f"The plan's Python changed: {python_path}"
    if differential_apa.sources() != plan.get("source"):
        return "The adapter source in this checkout differs from the plan's source binding"
    return ""


def execute_command(plan: dict[str, Any], plan_path: Path, outdir: Path, *, unit: str,
                    plan_sha256: str | None = None) -> tuple[list[str], str]:
    python = str(plan["python"]["path"])
    command = [python, "-m", "scpolya_agent.differential_apa", "execute",
               "--plan", str(plan_path), "--outdir", str(outdir)]
    if plan_sha256:
        command += ["--plan-sha256", plan_sha256]
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        return command, "none"
    envelope = [systemd_run, "--user", "--scope", "--quiet", f"--unit={unit}"]
    for item in ENVELOPE_PROPERTIES:
        envelope += ["-p", item]
    return [*envelope, "--", *command], "systemd-scope"


def review_and_execute(view: AnalysisView, outdir: str, *, workspace_id: str = "",
                       home: Path | None = None) -> Launch:
    problem = runtime_mismatch(view.plan)
    if problem:
        raise ValueError(problem)
    target = validate_execute_request(view, outdir)
    plan_path = Path(view.path).resolve() / "plan.json"
    logs = (home or ui_home()) / LOG_DIR
    logs.mkdir(parents=True, exist_ok=True)
    stem = f"{view.plan_sha256[:12]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    log_path = logs / (stem + ".log")
    unit = "scpolya-diff-" + re.sub(r"[^A-Za-z0-9_.-]", "-", stem)
    command, envelope = execute_command(view.plan, plan_path, target, unit=unit, plan_sha256=view.plan_sha256)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    with log_path.open("wb") as log:
        log.write((" ".join(command) + "\n").encode())
        log.flush()
        process = subprocess.Popen(command, cwd=_source_root(), env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    _PROCESSES[process.pid] = process
    threading.Thread(target=process.wait, daemon=True).start()
    launch = Launch(outdir=str(target), plan=str(plan_path), approval="", log=str(log_path),
                    pid=process.pid, issued_by="", started_at=utc_now(), envelope=envelope)
    launches = load_launches(home)
    launches[launch.outdir] = launch
    _save_launches(launches, home)
    entries = load_entries(home)
    if all(entry.path != launch.outdir for entry in entries):
        save_entries([AnalysisEntry(path=launch.outdir, workspace_id=workspace_id, added_at=utc_now()), *entries], home)
    return launch


def load_launches(home: Path | None = None) -> dict[str, Launch]:
    path = (home or ui_home()) / LAUNCH_FILE
    if not path.is_file():
        return {}
    try:
        items = json.loads(path.read_text(encoding="utf-8")).get("launches", [])
        return {str(item["outdir"]): Launch(**item) for item in items if isinstance(item, dict)}
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return {}


def launch_for(path: str, home: Path | None = None) -> Launch | None:
    return load_launches(home).get(path)


def process_alive(launch: Launch) -> bool:
    process = _PROCESSES.get(launch.pid)
    if process is not None:
        return process.poll() is None
    try:
        cmdline = Path(f"/proc/{launch.pid}/cmdline").read_bytes()
    except OSError:
        return False
    return launch.outdir.encode() in cmdline


def log_tail(launch: Launch, max_bytes: int = 4096) -> str:
    path = Path(launch.log)
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - max_bytes))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _save_launches(launches: dict[str, Launch], home: Path | None = None) -> None:
    path = (home or ui_home()) / LAUNCH_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps({"version": 1, "launches": [asdict(item) for item in launches.values()]}, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def _source_root() -> Path:
    import scpolya_agent

    return Path(scpolya_agent.__file__).resolve().parent.parent


def _describe_launch(launch: Launch) -> AnalysisView:
    if process_alive(launch):
        return AnalysisView(path=launch.outdir, kind="attempt", status="starting")
    error = "The execute process ended without creating attempt.json"
    for line in reversed(log_tail(launch).splitlines()):
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and doc.get("error"):
            error = str(doc["error"])
            break
    return AnalysisView(path=launch.outdir, kind="attempt", status="refused", error=error)


def read_table(path: str, name: str, *, offset: int = 0, limit: int = TABLE_PAGE_ROWS) -> dict[str, Any]:
    from scpolya_agent.differential_apa import result

    return result(path, name=name, offset=int(offset), limit=int(limit), max_bytes=TABLE_PAGE_BYTES)


def _describe_plan(directory: Path) -> AnalysisView:
    plan_path = directory / "plan.json"
    if plan_path.stat().st_size > MAX_PLAN_BYTES:
        return AnalysisView(path=str(directory), kind="plan", status="unreadable", error="plan.json is larger than 2 MiB")
    raw = plan_path.read_bytes()
    try:
        plan = json.loads(raw)
    except ValueError as exc:
        return AnalysisView(path=str(directory), kind="plan", status="unreadable", error=str(exc))
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        return AnalysisView(path=str(directory), kind="plan", status="unreadable", error="Not a differential APA plan")
    return AnalysisView(
        path=str(directory),
        kind="plan",
        status=str(plan.get("status") or "unknown"),
        plan=plan,
        plan_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _describe_attempt(directory: Path) -> AnalysisView:
    from scpolya_agent.differential_apa import status

    try:
        summary = status(str(directory))
    except Exception as exc:  # the adapter raises on any integrity failure
        return AnalysisView(path=str(directory), kind="attempt", status="unverified", error=str(exc))
    plan: dict[str, Any] = {}
    plan_record = summary.get("plan") if isinstance(summary.get("plan"), dict) else {}
    plan_path = Path(str(plan_record.get("path") or ""))
    if plan_path.is_file() and plan_path.stat().st_size <= MAX_PLAN_BYTES:
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except ValueError:
            plan = {}
    issued_by = ""
    attempt_path = directory / "attempt.json"
    if attempt_path.stat().st_size <= MAX_PLAN_BYTES:
        try:
            issued_by = str(json.loads(attempt_path.read_text(encoding="utf-8")).get("issued_by") or "")
        except (ValueError, AttributeError):
            issued_by = ""
    return AnalysisView(
        path=str(directory),
        kind="attempt",
        status=str(summary.get("status") or "unknown"),
        attempt=summary,
        plan=plan if isinstance(plan, dict) else {},
        issued_by=issued_by,
    )


def read_table_rows(path: str, name: str, *, max_rows: int = 2000) -> tuple[list[str], list[dict[str, Any]], bool]:
    """Read up to ``max_rows`` rows page by page; the flag says more rows exist."""
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < max_rows:
        page = read_table(path, name, offset=offset, limit=min(TABLE_PAGE_ROWS, max_rows - len(rows)))
        columns = columns or list(page.get("columns") or [])
        rows.extend(page.get("rows") or [])
        next_offset = page.get("next_offset")
        if not next_offset:
            return columns, rows, False
        offset = int(next_offset)
    return columns, rows, True


def readable_value(column: str, value: Any) -> str:
    """Display form: decoded unit ids and four significant digits; exact text stays in the tooltip."""
    from urllib.parse import unquote

    text = "" if value is None else str(value)
    if column == "unit_id":
        return unquote(text)
    if not text or text == "NA":
        return text
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer() and "." not in text and "e" not in text.lower():
        return text
    return f"{number:.4g}"


def sort_key(value: Any) -> tuple[int, float | str]:
    """Numbers ascending, then text, with NA and blanks last."""
    text = "" if value is None else str(value)
    if text in ("", "NA"):
        return (2, "")
    try:
        return (0, float(text))
    except ValueError:
        return (1, text.casefold())
