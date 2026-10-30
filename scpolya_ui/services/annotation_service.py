"""UI side of the agent notes overlay (``scpolya_agent.annotations``).

Only shown when agent mode is on. The person's notes, replies, answers and
resolve actions are appended to the same per-workspace store agents read.
"""

from __future__ import annotations

import getpass
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scpolya_agent import annotations as store
from scpolya_ui.registry import ui_home


@dataclass(frozen=True)
class NotesSummary:
    threads: tuple[dict[str, Any], ...]
    integrity: str
    attention: int
    unresolved: int


def user_name() -> str:
    try:
        return getpass.getuser() or "user"
    except Exception:
        return "user"


def load_notes(root: str, home: Path | None = None) -> NotesSummary:
    events, problem = store.load_events(root, home or ui_home())
    threads = tuple(store.threads(events))
    return NotesSummary(
        threads=threads,
        integrity=problem,
        attention=sum(1 for item in threads if item["awaiting_user"] or (item["unread_for_user"] and not item["resolved"])),
        unresolved=sum(1 for item in threads if not item["resolved"]),
    )


def mark_seen(root: str, home: Path | None = None) -> None:
    events, _ = store.load_events(root, home or ui_home())
    if not events or events[-1].get("kind") == "read" and events[-1].get("author", {}).get("type") == "user":
        return
    store.mark_read(root, reader_type="user", reader_name=user_name(), up_to=events[-1]["id"], home=home or ui_home())


def add_user_note(root: str, *, body: str, anchor_type: str = "workspace", anchor_ref: str = "",
                  anchor_label: str = "", home: Path | None = None) -> dict:
    return store.add_note(root, author_type="user", author_name=user_name(), anchor_type=anchor_type,
                          anchor_ref=anchor_ref or store.workspace_root(root), anchor_label=anchor_label,
                          body=body, home=home or ui_home())


def user_reply(root: str, thread_id: str, *, body: str = "", choice: str = "", home: Path | None = None) -> dict:
    return store.reply(root, thread_id, author_type="user", author_name=user_name(), body=body, choice=choice,
                       home=home or ui_home())


def set_resolved(root: str, thread_id: str, resolved: bool, home: Path | None = None) -> dict:
    return store.set_resolved(root, thread_id, resolved, author_name=user_name(), home=home or ui_home())


def evidence_state(record: dict) -> str:
    try:
        return store.evidence_state(record)
    except OSError:
        return "missing"


def anchor_target(anchor: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
    """(page, extra query) for anchors the UI can open, else None."""
    kind, ref = str(anchor.get("type") or ""), str(anchor.get("ref") or "")
    if kind == "run" and ref:
        return "runs", {"run_record": ref}
    if kind == "locus" and ref:
        return "igv_compare", {"igv_source": "single", "gene": ref}
    if kind == "plan" and ref:
        path = Path(ref)
        return "differential_apa", {"analysis": str(path.parent if path.name in {"plan.json", "attempt.json"} else path)}
    return None
