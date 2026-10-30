from __future__ import annotations

import hashlib
import json

import pytest

from scpolya_ui.services.differential_service import (
    ENVELOPE_PROPERTIES,
    PLAN_SCHEMA,
    Launch,
    _save_launches,
    add_entry,
    analysis_directory,
    describe,
    execute_command,
    load_entries,
    remove_entry,
    runtime_mismatch,
    validate_execute_request,
)


def _plan(tmp_path, status="PREPARED_ELIGIBILITY_ONLY"):
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    doc = {
        "schema": PLAN_SCHEMA,
        "status": status,
        "eligibility": {"mode": "eligibility_only", "independent_donors": 1},
        "request": {"contrast": None, "cell_type": "CD8 T", "provenance": {"kind": "real"}},
    }
    (plan_dir / "plan.json").write_text(json.dumps(doc), encoding="utf-8")
    return plan_dir


def test_registry_round_trip(tmp_path):
    plan_dir = _plan(tmp_path)
    home = tmp_path / "home"
    entry = add_entry(str(plan_dir / "plan.json"), workspace_id="W-1", home=home)
    assert entry.path == str(plan_dir.resolve())
    assert add_entry(str(plan_dir), home=home).path == entry.path
    assert [item.path for item in load_entries(home)] == [entry.path]
    remove_entry(entry.path, home=home)
    assert load_entries(home) == []


def test_describe_plan_reports_hash_and_fields(tmp_path):
    plan_dir = _plan(tmp_path, status="READY_FOR_EXTERNAL_APPROVAL")
    view = describe(str(plan_dir))
    assert view.kind == "plan" and view.status == "READY_FOR_EXTERNAL_APPROVAL"
    assert view.plan_sha256 == hashlib.sha256((plan_dir / "plan.json").read_bytes()).hexdigest()
    assert view.provenance_kind == "real" and view.eligibility["independent_donors"] == 1


def test_tampered_attempt_is_unverified(tmp_path):
    out = tmp_path / "attempt"
    out.mkdir()
    (out / "attempt.json").write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    view = describe(str(out))
    assert view.kind == "attempt" and view.status == "unverified" and view.error


def test_unrecognised_directory_is_refused(tmp_path):
    with pytest.raises(ValueError):
        analysis_directory(str(tmp_path))


def test_execute_request_needs_no_approval_and_checks_new_outdir(tmp_path):
    plan_dir = _plan(tmp_path, status="READY_FOR_EXTERNAL_APPROVAL")
    view = describe(str(plan_dir))
    assert view.plan_sha256 == hashlib.sha256((plan_dir / "plan.json").read_bytes()).hexdigest()
    outdir = tmp_path / "attempt-1"
    target = validate_execute_request(view, str(outdir))
    assert target == outdir.resolve() and not outdir.exists()
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize("case", ["not_ready", "relative", "exists", "inside_plan", "changed"])
def test_execute_request_refuses(tmp_path, case):
    plan_dir = _plan(tmp_path, status="PREPARED_ELIGIBILITY_ONLY" if case == "not_ready" else "READY_FOR_EXTERNAL_APPROVAL")
    view = describe(str(plan_dir))
    outdir = tmp_path / "attempt"
    if case == "relative":
        outdir = "attempt"
    elif case == "exists":
        outdir.mkdir()
    elif case == "inside_plan":
        outdir = plan_dir / "attempt"
    elif case == "changed":
        (plan_dir / "plan.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        validate_execute_request(view, str(outdir))


def test_execute_command_uses_plan_python_and_resource_envelope(tmp_path, monkeypatch):
    plan = {"python": {"path": "/opt/py/bin/python3.11"}}
    args = (plan, tmp_path / "plan.json", tmp_path / "out")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemd-run")
    command, envelope = execute_command(*args, unit="scpolya-diff-x", plan_sha256="ab" * 32)
    assert envelope == "systemd-scope"
    assert command[:5] == ["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--unit=scpolya-diff-x"]
    for item in ENVELOPE_PROPERTIES:
        assert item in command
    tail = command[command.index("--") + 1 :]
    assert tail[:4] == ["/opt/py/bin/python3.11", "-m", "scpolya_agent.differential_apa", "execute"]
    assert "--approval" not in tail and tail[tail.index("--plan-sha256") + 1] == "ab" * 32
    monkeypatch.setattr("shutil.which", lambda name: None)
    command, envelope = execute_command(*args, unit="u")
    assert envelope == "none" and command[0] == "/opt/py/bin/python3.11"


def test_runtime_mismatch_reports_missing_python(tmp_path):
    assert "missing" in runtime_mismatch({"python": {"path": str(tmp_path / "nope")}})


def test_refused_launch_reads_adapter_error(tmp_path):
    home = tmp_path / "home"
    log = tmp_path / "x.log"
    log.write_text('cmd\n{"schema": "e", "status": "REFUSED", "error": "Stale source/Python binding"}\n', encoding="utf-8")
    outdir = str(tmp_path / "attempt")
    _save_launches({outdir: Launch(outdir=outdir, plan="p", approval="a", log=str(log), pid=2**22 + 7,
                                   issued_by="R", started_at="t", envelope="none")}, home)
    view = describe(outdir, home=home)
    assert (view.kind, view.status, view.error) == ("attempt", "refused", "Stale source/Python binding")
