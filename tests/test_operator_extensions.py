"""Operator admission and subprocess isolation for the structured APA entry."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scpolya_agent import cli, operator_cli
from scpolya_agent.manifest import Manifest


@pytest.fixture
def configured(tmp_path):
    root = tmp_path / "native"
    root.mkdir()
    for rel in ("main.py", "arguments.py", "native_entry.py", "scpolya_agent/native_task_driver.py",
                "scpolya_ui/command_builder.py", "scpolya_ui/job_supervisor.py", "task_manage/run_config_preview.py"):
        target = root / rel
        target.parent.mkdir(exist_ok=True)
        target.write_text("# synthetic routing-only checkout\n")
    (root / "scpolya_agent/differential_apa.py").write_text(
        "import json,os,sys\n"
        "print(json.dumps({'isolated':sys.flags.isolated,'bytecode':sys.dont_write_bytecode,"
        "'argv':sys.argv[1:],'manifest':os.environ.get('SCPOLYA_MANIFEST'),"
        "'ambient_imported':any('hostile-ambient' in p for p in sys.path)}))\n"
        "raise SystemExit(7 if '--synthetic-failure' in sys.argv else 0)\n"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Routing test", "-c",
                    "user.email=routing@example.invalid", "commit", "-qm", "synthetic checkout"], check=True)
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    manifest = tmp_path / "operator.json"
    manifest.write_text(json.dumps({"schema": "scpolya.operator_manifest.v1", "native": {
        "source_root": str(root), "python": sys.executable, "expected_commit": commit}}))
    return root, manifest


def call(manifest, *args, extra_env=None):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(extra_env or {})}
    return subprocess.run([sys.executable, "-B", "-m", "scpolya_agent.operator_cli",
                           "--manifest", str(manifest), *args], env=env, capture_output=True, text=True)


def test_differential_route_isolated_and_preserves_arguments(configured, tmp_path):
    _, manifest = configured
    hostile = tmp_path / "hostile-ambient"
    hostile.mkdir()
    result = call(manifest, "differential-apa", "plan", "--request", "/tmp/request with spaces.json",
                  extra_env={"PYTHONPATH": str(hostile)})
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body == {"isolated": 1, "bytecode": True,
                    "argv": ["plan", "--request", "/tmp/request with spaces.json"],
                    "manifest": str(manifest), "ambient_imported": False}


def test_differential_child_failure_is_not_reported_as_success(configured):
    _, manifest = configured
    result = call(manifest, "differential-apa", "--synthetic-failure")
    assert result.returncode == 7


@pytest.mark.parametrize("change,code", [("dirty", "native_source_dirty"),
    ("commit", "native_source_commit"), ("missing", "differential_apa_source_missing")])
def test_differential_refuses_wrong_source(configured, change, code):
    root, manifest = configured
    if change == "dirty":
        (root / "untracked.py").write_text("raise RuntimeError('must not run')\n")
    elif change == "commit":
        value = json.loads(manifest.read_text())
        value["native"]["expected_commit"] = "0" * 40
        manifest.write_text(json.dumps(value))
    else:
        subprocess.run(["git", "-C", str(root), "rm", "scpolya_agent/differential_apa.py"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Routing test", "-c",
                        "user.email=routing@example.invalid", "commit", "-qm", "missing extension"], check=True)
        value = json.loads(manifest.read_text())
        value["native"]["expected_commit"] = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        manifest.write_text(json.dumps(value))
    result = call(manifest, "differential-apa", "plan")
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == code


@pytest.mark.parametrize("operation", ["action-binding", "action", "action-worker", "rerun", "scoped-export"])
def test_excluded_control_operations_refuse_cleanly(configured, operation):
    _, manifest = configured
    result = call(manifest, operation)
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "operation_not_in_release"


def test_new_entry_is_discoverable_without_native_runtime(capsys):
    assert operator_cli.run(["--help"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert "differential-apa" in result["commands"]
    assert result["differential_apa_commands"] == ["plan", "execute", "status", "result"]
    assert cli.main(["capabilities"]) == 0
    assert "differential-apa" in json.loads(capsys.readouterr().out)["operator_extensions"]


def test_doctor_extension_is_configuration_only(configured):
    _, manifest = configured
    result = operator_cli.doctor(Manifest(manifest))
    assert result["differential_apa"]["status"] == "configuration_present_runtime_not_certified"
    assert "R" in result["differential_apa"]["requirements"]
