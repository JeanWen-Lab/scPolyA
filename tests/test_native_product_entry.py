"""Bounded tests for the native product entry bridge and four skills.

These tests never run a native job or read a raw BAM/FASTA/H5.  Complete-source bridge tests are skipped when the current test
interpreter cannot import the native driver's declared dependencies.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "native_entry.py"
VALIDATOR = Path(os.environ.get("SCPOLYA_SKILL_VALIDATOR", "/nonexistent/quick_validate.py"))
ANALYSIS_SKILL = Path(os.environ.get("SCPOLYA_ANALYSIS_SKILL", "/nonexistent/SKILL.md"))
ANALYSIS_SHA256 = "eb9dcbf1bbb9516ea26861146ba78740a3e9aced79d3728ce80ad544a3c2cb1c"


def run_entry(*args: str, env_remove: str | None = None, timeout: int = 60):
    env = os.environ.copy()
    if env_remove:
        env.pop(env_remove, None)
    return subprocess.run([sys.executable, "-I", "-B", str(ENTRY), *args],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def json_run(*args: str, **kwargs):
    r = run_entry(*args, **kwargs)
    assert r.stderr == "", r.stderr
    assert r.stdout, r.stdout
    return r, json.loads(r.stdout)


def native_dependencies_available() -> bool:
    try:
        import tqdm  # noqa: F401
        return True
    except Exception:
        return False


def test_copied_package_only_source_is_not_silently_bound(tmp_path):
    (tmp_path / "scpolya_agent").mkdir()
    (tmp_path / "scpolya_agent" / "native_task_driver.py").write_text("# copied package only\n")
    r, doc = json_run("--source-root", str(tmp_path), "bridge")
    assert r.returncode == 2
    assert doc["status"] == "error"
    assert doc["error"]["code"] == "incomplete_native_source_root"
    assert "arguments.py" in doc["error"]["detail"]
    assert doc["error"]["next_supported_operation"].startswith("set --source-root")


def test_skills_are_exactly_the_four_original_scopes():
    expected = {"scpolya-prepare-run", "scpolya-compare-settings",
                "scpolya-resume-diagnose", "scpolya-explain-export"}
    dirs = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())
    assert {p.name for p in dirs} == expected
    for p in dirs:
        if VALIDATOR.is_file():
            assert subprocess.run([sys.executable, "-I", "-B", str(VALIDATOR), str(p)],
                                  check=False).returncode == 0
    _, doc = json_run("--source-root", str(ROOT), "skills")
    assert doc["status"] == "observed"
    assert {s["id"] for s in doc["skills"]} == expected
    assert all(s["status"] == "available" and s["sha256"] for s in doc["skills"])


def test_shared_analysis_skill_is_explicit_and_pinned(tmp_path):
    if not ANALYSIS_SKILL.is_file():
        pytest.skip("accepted analysis skill is not present in this checkout environment")
    copy = tmp_path / "SKILL.md"
    shutil.copyfile(ANALYSIS_SKILL, copy)
    _, doc = json_run("--source-root", str(ROOT), "bridge", "--analysis-skill", str(copy))
    assert doc["shared_analysis_skill"]["status"] == "available"
    assert doc["shared_analysis_skill"]["sha256"] == ANALYSIS_SHA256
    assert doc["shared_analysis_skill"]["modified"] is False
    copy.write_text("modified copy; this test mutates only its temporary file\n")
    _, bad = json_run("--source-root", str(ROOT), "bridge", "--analysis-skill", str(copy))
    assert bad["shared_analysis_skill"]["status"] == "unavailable"
    assert bad["shared_analysis_skill"]["reason"].startswith("sha256 mismatch")


def test_product_operations_are_catalog_only_without_private_default():
    _, doc = json_run("--source-root", str(ROOT), "bridge", env_remove="SCPOLYA_PRODUCT_SOURCE_ROOT")
    catalog = doc["product_operations"]
    assert catalog["configured"] is False
    assert catalog["binding"] == "not_included_in_release"
    assert catalog["operations"] == []
    assert "source_root" not in catalog


@pytest.mark.skipif(not native_dependencies_available(), reason="native driver dependencies unavailable")
def test_complete_source_bridge_and_settings_use_actual_parser():
    _, doc = json_run("--source-root", str(ROOT), "bridge", timeout=120)
    assert doc["status"] == "available"
    bridge = doc["native_bridge"]
    assert bridge["complete"] is True
    assert bridge["driver"]["verbs"] == ["plan", "run", "recover", "status", "stop",
                                          "harvest", "snapshot", "identity", "deepcps-result"]
    assert set(bridge["driver"]["request_keys"]) == {
        "schema", "job_name", "job_dir", "python", "preset", "run", "resources", "env", "preflight"}
    identity = bridge["driver"]["runtime_identity"]
    expected_head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    assert identity["source"]["head"] == expected_head
    assert identity["source"]["tracked_bytes_sha256"]
    assert bridge["driver"]["plan_contract"] == "driver builds plan; entry never invents one"
    assert doc["authorization"]["approval_file_required"] is False
    assert doc["authorization"]["os_security_claim"] is False

    _, settings = json_run("--source-root", str(ROOT), "settings")
    assert settings["parser_defaults"]["minpolyA"] == 10
    assert settings["parser_defaults"]["minMAPQ"] == 0
    assert settings["native_preset"]["minpolyA"] == 5
    assert settings["native_preset"]["minMAPQ"] == 10
    assert settings["native_preset"]["unscaled_depth_threshold_polyA"] == 3
    assert "total terminal A/T" in settings["meaning"]["minpolyA"]
    assert "per-base" in settings["meaning"]["unscaled_depth_threshold_polyA"]
    assert "are not necessarily package defaults" in settings["warnings"][0]


def test_run_recover_refuses_without_existing_plan(tmp_path):
    missing = tmp_path / "missing.json"
    for verb in ("run", "recover"):
        for extra in ((), ("--approval", str(missing))):
            r, doc = json_run("--source-root", str(ROOT), "driver", "--", verb,
                              "--plan", str(missing), *extra)
            assert r.returncode == 2
            assert doc["error"]["code"] == "existing_plan_required"
            assert "does not mint" in doc["error"]["detail"]
        r, doc = json_run("--source-root", str(ROOT), "driver", "--", verb)
        assert r.returncode == 2 and doc["error"]["code"] == "existing_plan_required"
    assert not missing.exists()


def test_driver_router_allows_only_existing_native_verbs():
    for verb in ("launch", "execute", "_unit-entry", ""):
        args = ["--source-root", str(ROOT), "driver"]
        if verb:
            args += ["--", verb]
        r, doc = json_run(*args)
        assert r.returncode == 2
        assert doc["error"]["code"] == "driver_verb_required"
