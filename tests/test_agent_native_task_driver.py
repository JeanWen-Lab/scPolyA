from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pysam
import pytest

from scpolya_agent import native_task_driver as drv

REAL_CGROUP_POPULATED = drv._cgroup_populated

REPO = Path(__file__).resolve().parents[1]
CHR_LEN = 50_000

FAKE_MAIN = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
out = Path(args[args.index("--output-dir") + 1]) / args[args.index("--run-id") + 1]
behaviour = (Path(__file__).parent / "behaviour").read_text().strip()
sys.path.insert(0, (Path(__file__).parent / "real_repo").read_text().strip())
from task_manage import Plan, TaskManager
tm = TaskManager(state_dir=out / "state", config={"run_id": out.name})
tm.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
inv = out / "logs" / "invocations" / tm.inline_execution_invocation_id
inv.mkdir(parents=True, exist_ok=True)
(inv / "invocation.json").write_text(json.dumps({
    "schema_version": "scpolya.invocation_metadata.v2", "status": "running",
    "invocation_id": tm.inline_execution_invocation_id, "job_id": os.environ.get("SCPOLYA_UI_JOB_ID", ""),
    "started_at": "2026-10-20T00:00:00+00:00", "pid": os.getpid()}))
if behaviour == "fail":
    sys.exit(3)
if behaviour == "sleep":
    time.sleep(60)
if behaviour == "succeed":
    tm.record_cache_epoch_plan_completion()
'''


def _bam(path: Path, *, umi: bool = True, chrom_len: int = CHR_LEN) -> Path:
    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "chr1", "LN": chrom_len}]}
    unsorted = path.with_suffix(".unsorted.bam")
    with pysam.AlignmentFile(str(unsorted), "wb", header=header) as out:
        for i in range(20):
            read = pysam.AlignedSegment()
            read.query_name = f"r{i}"
            read.query_sequence = "ACGT" * 10
            read.flag = 0
            read.reference_id = 0
            read.reference_start = 100 + i
            read.mapping_quality = 60
            read.cigarstring = "40M"
            read.query_qualities = pysam.qualitystring_to_array("I" * 40)
            tags = [("CB", "AAAC-1")] + ([("UB", f"U{i}")] if umi else [])
            read.set_tags(tags)
            out.write(read)
    pysam.sort("-o", str(path), str(unsorted))
    pysam.index(str(path))
    return path


def _metadata_schema() -> str:
    from logging_config import runtime_session
    return runtime_session.INVOCATION_METADATA_SCHEMA_V2


@pytest.fixture
def case(tmp_path):
    fake = tmp_path / "fake_repo"
    fake.mkdir()
    (fake / "main.py").write_text(FAKE_MAIN.replace("scpolya.invocation_metadata.v2", _metadata_schema()))
    (fake / "real_repo").write_text(str(REPO))
    (fake / "behaviour").write_text("succeed")
    bam = _bam(tmp_path / "in.bam")
    csv_path = tmp_path / "inputs.csv"
    csv_path.write_text(f"sample_id,source_path,is_deduped,requires_split\nS,{bam},0,1\n")
    sizes = tmp_path / "chrom.sizes"
    sizes.write_text(f"chr1\t{CHR_LEN}\n")
    gtf = tmp_path / "genes.gtf"
    gtf.write_text('chr1\tt\tgene\t1\t1000\t.\t+\t.\tgene_id "G";\n')
    request = {
        "schema": drv.REQUEST_SCHEMA, "job_name": "unit-case", "job_dir": str(tmp_path / "job"),
        "python": sys.executable, "preset": "manuscript-v4",
        "run": {"output_dir": str(tmp_path / "out"), "run_id": "r1", "species": "human",
                "inputs": str(csv_path), "gene_gtf": str(gtf), "chrom_sizes": str(sizes), "contigs": ["chr1"]},
        "resources": {"cpu_quota_percent": 200, "memory_max_bytes": 8 << 30, "runtime_max_sec": 60, "workers": 1},
        "preflight": {"max_records_per_contig": 5},
    }
    return {"tmp": tmp_path, "fake": fake, "request": request, "bam": bam, "csv": csv_path, "sizes": sizes}


def _write_plan(case) -> Path:
    plan = drv.build_plan(case["request"], repo_root=case["fake"])
    job = Path(plan["job_dir"])
    job.mkdir(parents=True)
    path = job / "plan.json"
    path.write_text(json.dumps(plan, sort_keys=True))
    return path


def _approve(plan_path: Path, actions=("run", "recover"), sha=None) -> Path:
    """Legacy approval file. The driver no longer requires or reads it; it is
    passed in some tests only to prove old callers keep working."""
    plan = json.loads(plan_path.read_text())
    path = plan_path.parent / "approval.json"
    path.write_text(json.dumps({"schema": drv.APPROVAL_SCHEMA, "plan_sha256": sha or plan["plan_sha256"],
                                "allowed_actions": list(actions), "issued_by": "test-operator"}))
    return path


def inline_launcher(plan, attempt_dir):
    """Run the real unit entry (product job_supervisor.run_spec) synchronously, without systemd."""
    proc = subprocess.run([sys.executable, "-B", str(Path(drv.__file__)), "_unit-entry", "--attempt-dir", str(attempt_dir)],
                          cwd=plan["product"]["repo_root"], env={**plan["env"], "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"},
                          capture_output=True, text=True, timeout=120)
    return {"launcher": "inline-test", "unit": "inline-test.service", "control_group": INLINE_CGROUP,
            "returncode": 0, "child_returncode": proc.returncode, "stderr": proc.stderr[-2000:]}


INLINE_CGROUP = "/scpolya-inline-test-never-exists.scope"


def gone(unit, env):
    return {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead", "MainPID": "0", "ControlGroup": ""}


def active(unit, env):
    return {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "MainPID": "123",
            "ControlGroup": INLINE_CGROUP}


def bus_down(unit, env):
    raise drv.UnitQueryError("unit_query_failed", "rc=1 Failed to connect to bus")


@pytest.fixture(autouse=True)
def only_inline_cgroup_is_known(monkeypatch):
    """The inline unit entry runs in pytest's own (populated) cgroup; tests declare cgroup state explicitly."""
    state = {"populated": None}
    monkeypatch.setattr(drv, "_cgroup_populated",
                        lambda path: state["populated"] if str(path).endswith(INLINE_CGROUP) else None)
    return state


def test_preset_resolves_through_real_parser_and_reports_differences(case):
    case["request"]["run"]["minpolyA"] = 10
    plan = drv.build_plan(case["request"], repo_root=case["fake"])
    cfg = plan["materialized_config"]
    assert cfg["minpolyA"] == 10 and cfg["nonA_allowed"] == 1 and cfg["minMAPQ"] == 10
    assert cfg["polyA_peak_threshold_mode"] == "fixed" and cfg["unscaled_depth_threshold_polyA"] == 3
    assert cfg["dedup_method"] == "directional" and cfg["contigs"] == ["chr1"]
    assert all(cfg[k] == 1 for k in drv.WORKER_KEYS)
    prov = plan["config_provenance"]
    assert prov["request_overrides_preset"] == {"minpolyA": {"preset": 5, "request": 10}}
    assert prov["preset_differs_from_parser_default"]["minMAPQ"] == {"parser_default": 0, "preset": 10}
    assert plan["status"] == "ready_for_external_approval" and plan["mode"] == "fresh"
    assert plan["plan_sha256"] == drv.plan_hash(plan)
    assert plan["samples"][0]["declared_is_deduped"] == "0"
    assert plan["argv"][1] == str(case["fake"] / "main.py") and plan["argv"][2] == "run"


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r["run"].update(not_a_flag=1), "unknown_run_keys"),
    (lambda r: r["run"].update(n_workers=4), "invalid_request"),
    (lambda r: r["run"].pop("contigs"), "invalid_request"),
    (lambda r: r.update(extra=1), "invalid_request"),
    (lambda r: r.update(preset="defaults"), "unknown_preset"),
])
def test_request_validation_rejects(case, mutate, code):
    mutate(case["request"])
    with pytest.raises(drv.DriverError) as err:
        drv.build_plan(case["request"], repo_root=case["fake"])
    assert err.value.code == code


def test_missing_umi_tag_blocks_plan_and_run(case):
    _bam(case["bam"], umi=False)
    plan_path = _write_plan(case)
    plan = json.loads(plan_path.read_text())
    assert plan["status"] == "blocked"
    assert [i["code"] for i in plan["issues"]] == ["umi_tag_absent"]
    with pytest.raises(drv.DriverError) as err:
        drv.run_plan(plan_path, _approve(plan_path), launcher=inline_launcher)
    assert err.value.code == "plan_blocked"


def test_chrom_size_mismatch_blocks(case):
    case["sizes"].write_text("chr1\t49999\n")
    plan = drv.build_plan(case["request"], repo_root=case["fake"])
    assert "chrom_size_mismatch" in {i["code"] for i in plan["issues"]}


def test_existing_run_root_blocks_fresh_plan(case):
    (case["tmp"] / "out" / "r1").mkdir(parents=True)
    plan = drv.build_plan(case["request"], repo_root=case["fake"])
    assert plan["status"] == "blocked" and "run_root_exists" in {i["code"] for i in plan["issues"]}


def test_tampered_plan_is_refused_with_or_without_legacy_approval(case):
    plan_path = _write_plan(case)
    plan = json.loads(plan_path.read_text())
    plan["argv"].append("--debug")
    plan_path.write_text(json.dumps(plan))
    for approval in (None, _approve(plan_path)):
        with pytest.raises(drv.DriverError) as err:
            drv.run_plan(plan_path, approval, launcher=inline_launcher)
        assert err.value.code == "plan_hash_mismatch"
    assert not drv.list_attempts(plan)


def test_run_without_approval_runs_exact_plan_and_records_hash(case):
    plan_path = _write_plan(case)
    plan = json.loads(plan_path.read_text())
    receipt = drv.run_plan(plan_path, launcher=inline_launcher)
    assert receipt["launch_mode"] == "fresh"
    attempts = drv.list_attempts(plan)
    assert len(attempts) == 1
    spec = json.loads((attempts[0] / "spec.json").read_text())
    assert spec["plan_sha256"] == plan["plan_sha256"] and spec["argv"] == plan["argv"]
    assert drv.status_plan(plan_path, query=gone)["state"] == "succeeded_verified"


def test_legacy_mismatched_approval_is_ignored(case):
    plan_path = _write_plan(case)
    stale = _approve(plan_path, sha="0" * 64, actions=("recover",))
    drv.run_plan(plan_path, stale, launcher=inline_launcher)
    assert drv.status_plan(plan_path, query=gone)["state"] == "succeeded_verified"


def test_cli_run_and_recover_accept_missing_approval(case, capsys):
    _bam(case["bam"], umi=False)
    plan_path = _write_plan(case)
    for verb in ("run", "recover"):
        code = drv.main([verb, "--plan", str(plan_path)])
        out = json.loads(capsys.readouterr().out)
        # Argument parsing succeeds without --approval; the plan's own checks decide.
        assert code == 2 and out["code"] in {"plan_blocked", "nothing_to_recover"}, out


def test_input_drift_after_planning_is_refused(case):
    plan_path = _write_plan(case)
    case["csv"].write_text(case["csv"].read_text() + "\n")
    with pytest.raises(drv.DriverError) as err:
        drv.run_plan(plan_path, _approve(plan_path), launcher=inline_launcher)
    assert err.value.code == "input_drift"


def test_success_is_verified_by_native_completion_marker(case):
    plan_path = _write_plan(case)
    approval = None  # no approval file is needed
    drv.run_plan(plan_path, launcher=inline_launcher)
    status = drv.status_plan(plan_path, query=gone)
    assert status["state"] == "succeeded_verified", status
    assert status["invocation"]["status"] == "succeeded"
    assert status["run_root_state"]["completion_marker"]["last_invocation_id"] == status["invocation"]["invocation_id"]
    resources = status["attempts"][0]["resources"]
    assert resources is not None and "memory.peak" in resources["after"]["files"]
    with pytest.raises(drv.DriverError) as err:
        drv.run_plan(plan_path, approval, launcher=inline_launcher)
    assert err.value.code == "already_launched"
    with pytest.raises(drv.DriverError) as err:
        drv.recover_plan(plan_path, approval, launcher=inline_launcher, query=gone)
    assert err.value.code == "already_complete"


def test_exit_zero_without_completion_marker_is_not_reported_complete(case):
    (case["fake"] / "behaviour").write_text("exit0-no-marker")
    plan_path = _write_plan(case)
    drv.run_plan(plan_path, _approve(plan_path), launcher=inline_launcher)
    assert drv.status_plan(plan_path, query=gone)["state"] == "succeeded_unverified"


def test_failed_attempt_is_preserved_and_recovered_in_same_root(case, monkeypatch):
    (case["fake"] / "behaviour").write_text("fail")
    plan_path = _write_plan(case)
    approval = _approve(plan_path)
    drv.run_plan(plan_path, approval, launcher=inline_launcher)
    status = drv.status_plan(plan_path, query=gone)
    assert status["state"] == "failed" and status["attempts"][0]["exit_code"] == 3

    class Preview:
        changed_effective_keys = ("minpolyA",)
        full_cache_rebuild = False

    monkeypatch.setattr(drv, "preview_rerun_from_state", lambda *a, **k: Preview())
    with pytest.raises(drv.DriverError) as err:
        drv.recover_plan(plan_path, approval, launcher=inline_launcher, query=gone)
    assert err.value.code == "config_drift"
    assert len(drv.list_attempts(json.loads(plan_path.read_text()))) == 1

    Preview.changed_effective_keys = ()
    (case["fake"] / "behaviour").write_text("succeed")
    receipt = drv.recover_plan(plan_path, approval, launcher=inline_launcher, query=gone)
    assert receipt["attempt"] == "a002" and receipt["launch_mode"] == "resume"
    final = drv.status_plan(plan_path, query=gone)
    assert final["state"] == "succeeded_verified"
    assert [a["supervisor_status"] for a in final["attempts"]] == ["failed", "succeeded"]
    spec = json.loads((Path(case["request"]["job_dir"]) / "attempts" / "a002" / "spec.json").read_text())
    assert spec["continue_from_run_record_id"] == "unit-case-a001" and spec["run_root"] == final["run_root"]
    assert (Path(case["request"]["job_dir"]) / "attempts" / "a001" / "native.log").is_file()


def _fake_running_attempt(case, *, control_group=INLINE_CGROUP, status="running"):
    plan_path = _write_plan(case)
    attempt = Path(case["request"]["job_dir"]) / "attempts" / "a001"
    attempt.mkdir(parents=True)
    launch = {"job_id": "unit-case-a001", "unit": "gone-unit.service", "launch_mode": "fresh", "returncode": 0}
    if control_group:
        launch["control_group"] = control_group
    (attempt / "launch.json").write_text(json.dumps(launch))
    (attempt / "status.json").write_text(json.dumps({"status": status}))
    return plan_path


def test_running_lost_and_unknown_are_distinguished(case, only_inline_cgroup_is_known):
    plan_path = _fake_running_attempt(case)
    assert drv.status_plan(plan_path, query=active)["state"] == "running"
    assert drv.status_plan(plan_path, query=gone)["state"] == "lost"
    down = drv.status_plan(plan_path, query=bus_down)
    assert down["state"] == "unknown" and down["quiescence"]["reason"] == "unit_query_failed"
    only_inline_cgroup_is_known["populated"] = True
    assert drv.status_plan(plan_path, query=gone)["state"] == "unknown"


def test_lost_requires_known_cgroup(case):
    plan_path = _fake_running_attempt(case, control_group=None)
    status = drv.status_plan(plan_path, query=gone)
    assert status["state"] == "unknown" and status["quiescence"]["reason"] == "cgroup_unknown"


def test_bus_failure_blocks_recovery_and_stop(case):
    plan_path = _fake_running_attempt(case, status="stopped")
    with pytest.raises(drv.DriverError) as err:
        drv.recover_plan(plan_path, _approve(plan_path), launcher=inline_launcher, query=bus_down)
    assert err.value.code == "not_quiescent"
    assert json.loads((Path(case["request"]["job_dir"]) / "attempts/a001/quiescence-before-recover.json").read_text())["reason"] == "unit_query_failed"
    assert len(drv.list_attempts(json.loads(plan_path.read_text()))) == 1
    running = _fake_running_attempt({**case, "request": {**case["request"], "job_dir": str(case["tmp"] / "jobR"), "job_name": "unit-case-r"}})
    with pytest.raises(drv.UnitQueryError):
        drv.stop_plan(running, query=bus_down)


def test_stopped_but_cgroup_still_populated_blocks_recovery(case, only_inline_cgroup_is_known):
    plan_path = _fake_running_attempt(case, status="stopped")
    only_inline_cgroup_is_known["populated"] = True
    with pytest.raises(drv.DriverError) as err:
        drv.recover_plan(plan_path, _approve(plan_path), launcher=inline_launcher, query=gone)
    assert err.value.code == "not_quiescent"
    with pytest.raises(drv.DriverError) as err:
        drv.recover_plan(plan_path, _approve(plan_path), launcher=inline_launcher, query=active)
    assert err.value.code == "not_quiescent"  # supervisor wrote 'stopped' but the unit main process is still running
    only_inline_cgroup_is_known["populated"] = False
    receipt = drv.recover_plan(plan_path, _approve(plan_path), launcher=inline_launcher, query=gone)
    assert receipt["attempt"] == "a002" and receipt["recovery_preview"]["quiescence"]["confirmed"]


def test_missing_caller_bus_env_is_not_inherited_and_query_fails_closed(case, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    plan = drv.build_plan(case["request"], repo_root=case["fake"])
    uid = __import__("os").getuid()
    assert plan["systemd_bus_env"]["XDG_RUNTIME_DIR"] == f"/run/user/{uid}"
    assert plan["systemd_bus_env"]["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{uid}/bus"
    with pytest.raises(drv.UnitQueryError):
        drv.unit_query("scpolya-no-such-unit.service", {"PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": "/nonexistent-run"})


def test_runtime_drift_refuses_launch(case, monkeypatch):
    plan_path = _write_plan(case)
    real = drv.runtime_identity
    monkeypatch.setattr(drv, "runtime_identity", lambda py, repo: {**real(py, repo), "identity_sha256": "0" * 64})
    with pytest.raises(drv.DriverError) as err:
        drv.run_plan(plan_path, _approve(plan_path), launcher=inline_launcher)
    assert err.value.code == "runtime_drift"
    assert not drv.list_attempts(json.loads(plan_path.read_text()))


def test_plan_freezes_runtime_identity(case):
    plan = drv.build_plan(case["request"], repo_root=case["fake"])
    ident = plan["runtime_identity"]
    assert ident["python"]["realpath"] == str(Path(sys.executable).resolve()) and len(ident["python"]["sha256"]) == 64
    assert "pysam" in ident["selected_packages"]
    assert set(ident["tools"]) == set(drv.RUNTIME_TOOLS)


def test_recover_requires_prior_attempt(case):
    plan_path = _write_plan(case)
    with pytest.raises(drv.DriverError) as err:
        drv.recover_plan(plan_path, _approve(plan_path), launcher=inline_launcher, query=gone)
    assert err.value.code == "nothing_to_recover"


@pytest.mark.skipif(not __import__("os").environ.get("SCPOLYA_TEST_SYSTEMD"), reason="opt-in live systemd --user check")
def test_systemd_live_retained_unit_quiescence_harvest_and_stop(case, monkeypatch):
    import time
    monkeypatch.setattr(drv, "_cgroup_populated", REAL_CGROUP_POPULATED)
    plan_path = _write_plan(case)
    receipt = drv.run_plan(plan_path, _approve(plan_path))
    assert receipt["returncode"] == 0 and receipt["control_group"]
    for _ in range(120):
        st = drv.status_plan(plan_path)
        if st["state"] not in {"running", "unknown"} and st["quiescence"]["confirmed"]:
            break
        time.sleep(0.5)
    assert st["state"] == "succeeded_verified", st
    res = st["attempts"][0]["resources"]["after"]["files"]
    assert res["cpu.max"] == "200000 100000" and res["memory.max"] == str(8 << 30) and res["memory.swap.max"] == "0"
    assert res["memory.peak"] and "oom_kill" in res["memory.events"]
    harvest = drv.harvest_plan(plan_path)
    props = harvest["quiescence"]["unit_properties"]
    assert props["Result"] == "success" and props["ExecMainStatus"] == "0"
    assert harvest["after_cleanup"]["LoadState"] == "not-found"

    (case["fake"] / "behaviour").write_text("sleep")
    case["request"].update(job_name="unit-case-stop", job_dir=str(case["tmp"] / "job2"))
    case["request"]["run"]["run_id"] = "r2"
    plan2 = _write_plan(case)
    drv.run_plan(plan2, _approve(plan2))
    for _ in range(60):
        if drv.status_plan(plan2)["state"] == "running" and (Path(case["request"]["job_dir"]) / "attempts/a001/status.json").is_file():
            break
        time.sleep(0.5)
    time.sleep(2)
    assert drv.status_plan(plan2)["state"] == "running"
    drv.snapshot_state(plan2, label="at-stop")
    assert drv.stop_plan(plan2)["stop_requested"]
    for _ in range(60):
        st2 = drv.status_plan(plan2)
        if st2["state"] == "stopped" and st2["quiescence"]["confirmed"]:
            break
        time.sleep(0.5)
    assert st2["state"] == "stopped" and st2["quiescence"]["confirmed"], st2
    assert drv.harvest_plan(plan2)["after_cleanup"]["LoadState"] == "not-found"
