from __future__ import annotations

from scpolya_agent import annotations as store
from scpolya_ui.services import annotation_service as notes


def test_attention_counts_and_user_actions(tmp_path):
    home, root = tmp_path / "home", tmp_path / "ws"
    root.mkdir()
    question = store.add_note(root, author_type="agent", author_name="Sol", anchor_type="run", anchor_ref="R-1",
                              note_type="question", body="?", options=["yes"], home=home)
    store.add_note(root, author_type="agent", author_name="Sol", anchor_type="locus", anchor_ref="chr1:1-9",
                   body="fyi", home=home)
    summary = notes.load_notes(str(root), home)
    assert (summary.attention, summary.unresolved) == (2, 2)
    notes.mark_seen(str(root), home)
    assert notes.load_notes(str(root), home).attention == 1  # the unanswered question
    notes.user_reply(str(root), question["id"], choice="yes", home=home)
    notes.set_resolved(str(root), question["id"], True, home=home)
    summary = notes.load_notes(str(root), home)
    assert (summary.attention, summary.unresolved) == (0, 1)
    note = notes.add_user_note(str(root), body="please rerun", home=home)
    assert note["author"]["type"] == "user" and note["anchor"]["type"] == "workspace"


def test_anchor_targets():
    assert notes.anchor_target({"type": "run", "ref": "R-1"}) == ("runs", {"run_record": "R-1"})
    assert notes.anchor_target({"type": "plan", "ref": "/x/plan/plan.json"}) == ("differential_apa", {"analysis": "/x/plan"})
    assert notes.anchor_target({"type": "locus", "ref": "TNFRSF1B"})[0] == "igv_compare"
    assert notes.anchor_target({"type": "task", "ref": "t"}) is None
