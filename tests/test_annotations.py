from __future__ import annotations

import hashlib
import json

import pytest

from scpolya_agent import annotations as A


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv(A.UI_HOME_ENV, str(tmp_path / "home"))
    root = tmp_path / "results"
    root.mkdir()
    return root


def test_store_lives_in_ui_home_not_workspace(ws, tmp_path):
    A.add_note(ws, author_type="agent", author_name="Sol", anchor_type="run", anchor_ref="R-1", body="hello")
    assert A.store_path(ws).is_relative_to(tmp_path / "home")
    assert list(ws.iterdir()) == []


def test_chain_threads_and_tamper_detection(ws):
    note = A.add_note(ws, author_type="agent", author_name="Sol", anchor_type="plan", anchor_ref="/p/plan.json",
                      note_type="question", body="Diagnostics only?", options=["yes", "pause"])
    threads = A.threads(A.load_events(ws)[0])
    assert threads[0]["awaiting_user"] and not threads[0]["resolved"]
    A.reply(ws, note["id"], author_type="user", author_name="me", choice="yes")
    A.set_resolved(ws, note["id"], True, author_name="me")
    events, problem = A.load_events(ws)
    assert problem == "" and [e["kind"] for e in events] == ["note", "reply", "resolve"]
    thread = A.threads(events)[0]
    assert thread["resolved"] and not thread["awaiting_user"] and not thread["read_by_agent"]
    A.mark_read(ws, reader_type="agent", reader_name="Sol", up_to=events[-1]["id"])
    assert A.threads(A.load_events(ws)[0])[0]["read_by_agent"]
    path = A.store_path(ws)
    lines = path.read_bytes().splitlines()
    first = json.loads(lines[0]); first["body"] = "edited"
    lines[0] = json.dumps(first, sort_keys=True).encode()
    path.write_bytes(b"\n".join(lines) + b"\n")
    assert "line 2" in A.load_events(ws)[1]


def test_validation(ws):
    with pytest.raises(ValueError):
        A.add_note(ws, author_type="agent", author_name="Sol", anchor_type="run", anchor_ref="R", body="x", options=["a"])
    with pytest.raises(ValueError):
        A.add_note(ws, author_type="agent", author_name="Sol", anchor_type="run", anchor_ref="R",
                   note_type="approval_request", body="x")
    note = A.add_note(ws, author_type="agent", author_name="Sol", anchor_type="run", anchor_ref="R",
                      note_type="question", body="q", options=["a"])
    with pytest.raises(ValueError):
        A.reply(ws, note["id"], author_type="agent", author_name="Sol", choice="a")
    with pytest.raises(ValueError):
        A.reply(ws, note["id"], author_type="user", author_name="me", choice="b")


def test_evidence_binding(ws, tmp_path):
    item = tmp_path / "e.tsv"
    item.write_text("a\n")
    note = A.add_note(ws, author_type="agent", author_name="Sol", anchor_type="artifact", anchor_ref=str(item),
                      body="see", evidence=[str(item)])
    record = note["evidence"][0]
    assert record["sha256"] == hashlib.sha256(b"a\n").hexdigest()
    assert A.evidence_state(record) == "match"
    item.write_text("b\n")
    assert A.evidence_state(record) == "changed"
    item.unlink()
    assert A.evidence_state(record) == "missing"


def test_cli_add_list_reply_wait(ws, capsys):
    assert A.main(["add", "--workspace", str(ws), "--anchor-type", "run", "--anchor", "R-1",
                   "--type", "question", "--body", "ok?", "--option", "yes", "--author", "Sol"]) == 0
    thread_id = json.loads(capsys.readouterr().out)["event"]["id"]
    assert A.main(["wait", "--workspace", str(ws), "--thread", thread_id, "--timeout", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "timeout"
    A.reply(A.workspace_root(ws), thread_id, author_type="user", author_name="me", choice="yes")
    assert A.main(["list", "--workspace", str(ws), "--author", "Sol"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["integrity"] == "intact" and listing["threads"][0]["events"][1]["choice"] == "yes"
    assert A.load_events(ws)[0][-1]["kind"] == "read"
    assert A.main(["reply", "--workspace", str(ws), "--thread", "A-missing", "--body", "x"]) == 2


def test_operator_routes_annotate_without_manifest(ws, capsys):
    from scpolya_agent.operator_cli import main

    assert main(["annotate", "list", "--workspace", str(ws), "--no-mark-read"]) == 0
    assert json.loads(capsys.readouterr().out)["threads"] == []
