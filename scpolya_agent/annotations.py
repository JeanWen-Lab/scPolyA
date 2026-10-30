"""Asynchronous notes between agents and the person using the scpolyA UI.

Notes are an overlay. They never change a run, a task status, an output file
or ``state.sqlite``. Each workspace has one append-only JSONL file under the
UI home (``$SCPOLYA_UI_HOME`` or ``~/.scpolya-ui``), not inside the results
directory. Every line carries the SHA-256 of the line before it, so edits or
deletions are detectable; later events (reply, resolve, read) never rewrite
earlier ones.

Delivery is asynchronous: nothing notifies an agent. An agent sees replies
the next time it runs ``list`` or while it blocks in ``wait``. Agent text is
an unverified narrative; evidence entries bind a path to the SHA-256 it had
when the note was written.

Agents use ``add``, ``list``, ``reply`` and ``wait``. Resolving a thread and
answering a question with one of its options are the person's actions in the
UI. There is no approval command here: an ``approval_request`` note only
points the person at a plan.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "scpolya.annotation.event.v1"
UI_HOME_ENV = "SCPOLYA_UI_HOME"
ANCHOR_TYPES = ("workspace", "run", "task", "artifact", "locus", "plan", "figure")
NOTE_TYPES = ("note", "flag", "question", "approval_request")
EVENT_KINDS = ("note", "reply", "resolve", "reopen", "read")
AUTHOR_TYPES = ("agent", "user")
MAX_BODY_CHARS = 8000
MAX_REF_CHARS = 1000
MAX_OPTIONS = 6
MAX_EVIDENCE = 8
MAX_EVIDENCE_BYTES = 1024**3
MAX_STORE_BYTES = 16 * 1024 * 1024
MAX_WAIT_SECONDS = 1800
GENESIS = "0" * 64


def ui_home() -> Path:
    return Path(os.environ.get(UI_HOME_ENV) or Path.home() / ".scpolya-ui").expanduser()


def workspace_root(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def store_path(root: str | Path, home: Path | None = None) -> Path:
    key = hashlib.sha256(workspace_root(root).encode()).hexdigest()[:16]
    return (home or ui_home()) / "annotations" / f"{key}.jsonl"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object, label: str, limit: int) -> str:
    _need(isinstance(value, str) and value.strip() != "", f"{label} is required")
    _need(len(value) <= limit, f"{label} exceeds {limit} characters")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_record(path: str | Path) -> dict:
    item = Path(path).expanduser().resolve()
    _need(item.is_file(), f"Evidence file not found: {item}")
    size = item.stat().st_size
    _need(size <= MAX_EVIDENCE_BYTES, f"Evidence file exceeds 1 GiB: {item}")
    return {"path": str(item), "sha256": _sha256_file(item), "bytes": size}


def evidence_state(record: dict) -> str:
    """'match' | 'changed' | 'missing' | 'unchecked' for a stored evidence entry."""
    item = Path(str(record.get("path") or ""))
    if not item.is_file():
        return "missing"
    size = item.stat().st_size
    if size != record.get("bytes"):
        return "changed"
    if size > 256 * 1024 * 1024:
        return "unchecked"
    return "match" if _sha256_file(item) == record.get("sha256") else "changed"


# ---------- store ----------
def _read_lines(path: Path) -> list[bytes]:
    if not path.is_file():
        return []
    _need(path.stat().st_size <= MAX_STORE_BYTES, "Annotation store exceeds 16 MiB")
    return [line for line in path.read_bytes().splitlines() if line.strip()]


def load_events(root: str | Path, home: Path | None = None) -> tuple[list[dict], str]:
    """Events in order and an integrity note ('' when the hash chain is intact)."""
    events, previous, problem = [], GENESIS, ""
    for number, line in enumerate(_read_lines(store_path(root, home)), start=1):
        try:
            event = json.loads(line)
        except ValueError:
            problem = problem or f"line {number} is not JSON"
            previous = hashlib.sha256(line).hexdigest()
            continue
        if not problem and event.get("prev") != previous:
            problem = f"hash chain breaks at line {number}"
        events.append(event)
        previous = hashlib.sha256(line).hexdigest()
    return events, problem


def _append(root: str | Path, fields: dict, home: Path | None = None) -> dict:
    path = store_path(root, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.seek(0)
            data = handle.read()
            _need(len(data) <= MAX_STORE_BYTES, "Annotation store exceeds 16 MiB")
            lines = [line for line in data.splitlines() if line.strip()]
            event = {
                "schema": SCHEMA,
                "id": "A-" + uuid.uuid4().hex[:16],
                "workspace": workspace_root(root),
                "created_at": now(),
                **fields,
                "prev": hashlib.sha256(lines[-1]).hexdigest() if lines else GENESIS,
            }
            if data and not data.endswith(b"\n"):
                handle.write(b"\n")
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True).encode() + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return event


def _author(author_type: str, name: str) -> dict:
    _need(author_type in AUTHOR_TYPES, "author type must be agent or user")
    return {"type": author_type, "name": _text(name, "author name", 120)}


def add_note(root, *, author_type: str, author_name: str, anchor_type: str, anchor_ref: str,
             note_type: str = "note", body: str, options=(), evidence=(), anchor_label: str = "",
             home: Path | None = None) -> dict:
    _need(anchor_type in ANCHOR_TYPES, "anchor type must be one of " + ", ".join(ANCHOR_TYPES))
    _need(note_type in NOTE_TYPES, "note type must be one of " + ", ".join(NOTE_TYPES))
    options = [_text(item, "option", 80) for item in options]
    _need(len(options) <= MAX_OPTIONS, f"at most {MAX_OPTIONS} options")
    _need(not options or note_type == "question", "options are only for questions")
    evidence = list(evidence)
    _need(len(evidence) <= MAX_EVIDENCE, f"at most {MAX_EVIDENCE} evidence files")
    _need(note_type != "approval_request" or anchor_type == "plan", "approval requests must anchor a plan")
    body = _text(body, "body", MAX_BODY_CHARS)
    return _append(root, {
        "kind": "note",
        "note_type": note_type,
        "author": _author(author_type, author_name),
        "anchor": {"type": anchor_type, "ref": _text(anchor_ref, "anchor", MAX_REF_CHARS),
                   **({"label": anchor_label[:200]} if anchor_label else {})},
        "body": body,
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "options": options,
        "evidence": [evidence_record(item) for item in evidence],
    }, home)


def _thread_root(events: list[dict], thread_id: str) -> dict:
    root = next((event for event in events if event.get("kind") == "note" and event.get("id") == thread_id), None)
    _need(root is not None, f"No thread {thread_id}")
    return root


def reply(root, thread_id: str, *, author_type: str, author_name: str, body: str = "", choice: str = "",
          home: Path | None = None) -> dict:
    events, _ = load_events(root, home)
    note = _thread_root(events, thread_id)
    if choice:
        _need(author_type == "user", "only the person answers with an option")
        _need(choice in note.get("options", []), "choice is not one of the question's options")
    _need(bool(choice) or bool(body.strip()), "reply needs a body or a choice")
    if body:
        _text(body, "body", MAX_BODY_CHARS)
    return _append(root, {
        "kind": "reply",
        "thread": thread_id,
        "author": _author(author_type, author_name),
        "body": body,
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        **({"choice": choice} if choice else {}),
    }, home)


def set_resolved(root, thread_id: str, resolved: bool, *, author_name: str, home: Path | None = None) -> dict:
    events, _ = load_events(root, home)
    _thread_root(events, thread_id)
    return _append(root, {"kind": "resolve" if resolved else "reopen", "thread": thread_id,
                          "author": _author("user", author_name)}, home)


def mark_read(root, *, reader_type: str, reader_name: str, up_to: str, home: Path | None = None) -> dict:
    return _append(root, {"kind": "read", "author": _author(reader_type, reader_name), "up_to": up_to}, home)


def threads(events: list[dict]) -> list[dict]:
    """Group events into threads, newest activity first."""
    by_id: dict[str, dict] = {}
    order = {event.get("id"): index for index, event in enumerate(events)}
    agent_read = user_read = -1
    for index, event in enumerate(events):
        kind = event.get("kind")
        if kind == "note":
            by_id[event["id"]] = {"note": event, "replies": [], "resolved": False, "last_index": index}
        elif kind in ("reply", "resolve", "reopen") and event.get("thread") in by_id:
            thread = by_id[event["thread"]]
            if kind == "reply":
                thread["replies"].append(event)
            else:
                thread["resolved"] = kind == "resolve"
            thread["last_index"] = index
        elif kind == "read":
            position = order.get(event.get("up_to"), -1)
            if (event.get("author") or {}).get("type") == "agent":
                agent_read = max(agent_read, position)
            else:
                user_read = max(user_read, position)
    result = []
    for thread in by_id.values():
        items = [thread["note"], *thread["replies"]]
        user_items = [order[item["id"]] for item in items if item["author"]["type"] == "user"]
        agent_items = [order[item["id"]] for item in items if item["author"]["type"] == "agent"]
        thread["read_by_agent"] = not user_items or max(user_items) <= agent_read
        thread["unread_for_user"] = bool(agent_items) and max(agent_items) > user_read
        thread["awaiting_user"] = (
            not thread["resolved"]
            and thread["note"].get("note_type") in ("question", "approval_request")
            and not any(item["author"]["type"] == "user" for item in thread["replies"])
        )
        result.append(thread)
    result.sort(key=lambda thread: thread["last_index"], reverse=True)
    return result


# ---------- agent CLI ----------
def _emit(doc: dict, code: int = 0) -> int:
    print(json.dumps(doc, ensure_ascii=False, sort_keys=True))
    return code


def _agent_name(value: str | None) -> str:
    return value or os.environ.get("SCPOLYA_AGENT_NAME") or "agent"


def _thread_view(thread: dict) -> dict:
    return {
        "id": thread["note"]["id"],
        "resolved": thread["resolved"],
        "awaiting_user": thread["awaiting_user"],
        "events": [thread["note"], *thread["replies"]],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scpolya-operator annotate", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("add", help="write a note, flag, question or approval request")
    p.add_argument("--workspace", required=True)
    p.add_argument("--anchor-type", required=True, choices=ANCHOR_TYPES)
    p.add_argument("--anchor", required=True, help="run record id, task id, artifact path, chrom:start-end, plan path ...")
    p.add_argument("--anchor-label", default="")
    p.add_argument("--type", dest="note_type", default="note", choices=NOTE_TYPES)
    p.add_argument("--body", required=True)
    p.add_argument("--option", action="append", default=[], help="answer option for a question (repeatable)")
    p.add_argument("--evidence", action="append", default=[], help="file bound by SHA-256 (repeatable)")
    p.add_argument("--author")
    p = sub.add_parser("list", help="read threads; records a read receipt unless --no-mark-read")
    p.add_argument("--workspace", required=True)
    p.add_argument("--anchor-type", choices=ANCHOR_TYPES)
    p.add_argument("--anchor")
    p.add_argument("--open", action="store_true", help="only unresolved threads")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--no-mark-read", action="store_true")
    p.add_argument("--author")
    p = sub.add_parser("reply", help="reply in a thread")
    p.add_argument("--workspace", required=True)
    p.add_argument("--thread", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--author")
    p = sub.add_parser("wait", help="block until the person replies in a thread, or time out")
    p.add_argument("--workspace", required=True)
    p.add_argument("--thread", required=True)
    p.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        root = workspace_root(args.workspace)
        _need(Path(root).is_dir(), f"Workspace directory not found: {root}")
        if args.command == "add":
            event = add_note(root, author_type="agent", author_name=_agent_name(args.author),
                             anchor_type=args.anchor_type, anchor_ref=args.anchor, anchor_label=args.anchor_label,
                             note_type=args.note_type, body=args.body, options=args.option, evidence=args.evidence)
            return _emit({"schema": "scpolya.annotation.result.v1", "status": "written", "event": event})
        if args.command == "reply":
            event = reply(root, args.thread, author_type="agent", author_name=_agent_name(args.author), body=args.body)
            return _emit({"schema": "scpolya.annotation.result.v1", "status": "written", "event": event})
        events, problem = load_events(root)
        if args.command == "list":
            selected = [
                thread for thread in threads(events)
                if (not args.open or not thread["resolved"])
                and (not args.anchor_type or thread["note"]["anchor"]["type"] == args.anchor_type)
                and (not args.anchor or thread["note"]["anchor"]["ref"] == args.anchor)
            ][: max(1, min(args.limit, 200))]
            if events and not args.no_mark_read:
                mark_read(root, reader_type="agent", reader_name=_agent_name(args.author), up_to=events[-1]["id"])
            return _emit({"schema": "scpolya.annotation.list.v1", "status": "ok", "workspace": root,
                          "integrity": problem or "intact", "threads": [_thread_view(item) for item in selected],
                          "note": "Agent text is unverified narrative; the UI marks it reference only."})
        # wait
        _thread_root(events, args.thread)
        seen = {event.get("id") for event in events}
        deadline = time.monotonic() + max(1, min(args.timeout, MAX_WAIT_SECONDS))
        while True:
            events, _ = load_events(root)
            fresh = [event for event in events if event.get("id") not in seen and event.get("thread") == args.thread
                     and event.get("kind") in ("reply", "resolve") and (event.get("author") or {}).get("type") == "user"]
            if fresh:
                return _emit({"schema": "scpolya.annotation.wait.v1", "status": "answered", "events": fresh})
            if time.monotonic() >= deadline:
                return _emit({"schema": "scpolya.annotation.wait.v1", "status": "timeout", "events": []})
            time.sleep(2)
    except (ValueError, OSError) as exc:
        return _emit({"schema": "scpolya.annotation.error.v1", "status": "refused", "error": str(exc)[:2000]}, 2)


if __name__ == "__main__":
    raise SystemExit(main())
