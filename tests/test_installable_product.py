"""Candidate boundary regressions: synthetic sources and existing bundled bytes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scpolya_agent import cli
from scpolya_agent.manifest import Manifest, ManifestError, canonical, digest, parse
from scpolya_agent.source_query import query, resolve

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    raw = canonical(value) + b"\n"
    path.write_bytes(raw)
    return dict(path=path.name, sha256=hashlib.sha256(raw).hexdigest())


@pytest.fixture
def inputs(tmp_path):
    source = {"data": {"unit": {"count": 7, "zero": 0, "nullable": None, "flag": True},
                       "unit_extra": {"count": 9}, "rows": [1, 2], "a/b": {"~name": 3}},
              "metadata": {"mentioned_path": "/private/other-source.json", "sha256": "a" * 64}}
    source_pin = write(tmp_path / "source.json", source)
    grant = dict(schema="scpolya.source_grant.v1", task_id="synthetic-task", issued_by="synthetic-operator",
                 not_before=time.time() - 60, expires=time.time() + 3600, max_response_bytes=8192,
                 sources={"alpha": {"sha256": source_pin["sha256"], "pointer_prefixes": ["/data/unit", "/data/rows", "/data/a~1b"]}})
    grant_pin = write(tmp_path / "grant.json", grant)
    manifest = dict(schema="scpolya.operator_manifest.v1", sources={"alpha": {**source_pin, "format": "json", "max_bytes": 4096}},
                    grants={"task": grant_pin})
    write(tmp_path / "operator.json", manifest)
    return tmp_path, source, grant, manifest


def test_relative_manifest_works_after_directory_move(inputs, tmp_path):
    directory, source, _, _ = inputs
    relocated = directory / "moved"
    relocated.mkdir()
    for name in ("operator.json", "source.json", "grant.json"):
        (directory / name).rename(relocated / name)
    result = query(Manifest(relocated / "operator.json"), "task", "alpha", "/data/unit/count")
    assert result["value"] == 7
    assert result["source_ref"]["path"] == str(relocated / "source.json")
    assert result["source_ref"]["json_pointer"] == "/data/unit/count"
    assert result["source_ref"]["sha256"] != digest(source)
    assert result["envelope_sha256"] == digest({k: v for k, v in result.items() if k != "envelope_sha256"})


@pytest.mark.parametrize("pointer,value", [("/data/unit/zero", 0), ("/data/unit/nullable", None),
    ("/data/unit/flag", True), ("/data/a~1b/~0name", 3), ("/data/rows/0", 1)])
def test_types_escapes_and_source_citations(inputs, pointer, value):
    directory, _, _, _ = inputs
    result = query(Manifest(directory / "operator.json"), "task", "alpha", pointer)
    assert canonical(result["value"]) == canonical(value)
    assert result["source_ref"]["value_sha256"] == digest(value)
    assert result["delivered_sources"] == [result["source_ref"]]
    assert digest(True) != digest(1)


@pytest.mark.parametrize("pointer,code", [("/data/unit_extra/count", "selector_not_granted"),
    ("/data", "selector_not_granted"), ("", "selector_not_granted"),
    ("/result/data/unit", "selector_not_granted"), ("/data/rows/01", "selector_absent"),
    ("/data/rows/-1", "selector_absent"), ("/data/rows/-", "selector_absent"),
    ("/data/a~2b", "selector_invalid"), ("/data/unit/unknown", "selector_absent")])
def test_invalid_scope_or_selector_refused(inputs, pointer, code):
    directory, _, _, _ = inputs
    with pytest.raises(ManifestError) as exc:
        query(Manifest(directory / "operator.json"), "task", "alpha", pointer)
    assert exc.value.code == code


def test_metadata_never_self_grants_source(inputs):
    directory, _, _, _ = inputs
    with pytest.raises(ManifestError, match="metadata"):
        query(Manifest(directory / "operator.json"), "task", "metadata", "/")
    with pytest.raises(ManifestError) as exc:
        query(Manifest(directory / "operator.json"), "other-task", "alpha", "/data/unit")
    assert exc.value.code == "grant_unregistered"


def test_pin_change_returns_no_partial_value(inputs, capsys):
    directory, _, _, _ = inputs
    (directory / "source.json").write_text('{"data":{"unit":{"count":999}}}')
    assert cli.main(["--manifest", str(directory / "operator.json"), "source-query", "--grant", "task", "--source", "alpha", "--pointer", "/data/unit/count"]) == 2
    response = json.loads(capsys.readouterr().out)
    assert response["error"]["code"] == "source_pin_mismatch"
    assert "value" not in response and "delivered_sources" not in response


def test_grant_hash_and_expiration_enforced(inputs):
    directory, _, grant, manifest = inputs
    grant["expires"] = 1
    manifest["grants"]["task"] = write(directory / "grant.json", grant)
    write(directory / "operator.json", manifest)
    with pytest.raises(ManifestError) as exc:
        query(Manifest(directory / "operator.json"), "task", "alpha", "/data/unit")
    assert exc.value.code == "grant_expired_or_not_active"
    (directory / "grant.json").write_text("{}")
    with pytest.raises(ManifestError) as exc:
        query(Manifest(directory / "operator.json"), "task", "alpha", "/data/unit")
    assert exc.value.code == "source_pin_mismatch"


def test_response_cap_and_source_pin_in_grant(inputs):
    directory, _, grant, manifest = inputs
    grant["max_response_bytes"] = 10
    manifest["grants"]["task"] = write(directory / "grant.json", grant)
    write(directory / "operator.json", manifest)
    with pytest.raises(ManifestError) as exc:
        query(Manifest(directory / "operator.json"), "task", "alpha", "/data/unit")
    assert exc.value.code == "response_bound"
    grant["sources"]["alpha"]["sha256"] = "0" * 64
    manifest["grants"]["task"] = write(directory / "grant.json", grant)
    write(directory / "operator.json", manifest)
    with pytest.raises(ManifestError) as exc:
        query(Manifest(directory / "operator.json"), "task", "alpha", "/data/unit")
    assert exc.value.code == "grant_source_pin"


def test_duplicate_nonfinite_and_symlink_sources_refused(inputs):
    directory, _, _, manifest = inputs
    for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}'):
        with pytest.raises(ManifestError):
            parse(raw)
    (directory / "link.json").symlink_to(directory / "source.json")
    manifest["sources"]["alpha"]["path"] = "link.json"
    write(directory / "operator.json", manifest)
    with pytest.raises(ManifestError) as exc:
        Manifest(directory / "operator.json")
    assert exc.value.code == "symlink_path_refused"


def test_missing_native_checkout_refused_before_child_launch(tmp_path, monkeypatch, capsys):
    manifest = {"schema": "scpolya.operator_manifest.v1", "native": {"source_root": "absent", "python": sys.executable,
                "expected_commit": "5" * 40}}
    write(tmp_path / "operator.json", manifest)
    from scpolya_agent import operator_cli as operator
    monkeypatch.setattr(operator.subprocess, "run", lambda *a, **k: pytest.fail("child launched before source admission"))
    assert operator.main(["--manifest", str(tmp_path / "operator.json"), "native", "driver", "--", "run"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "native_source_missing"


def test_generic_tool_uses_fixed_script_and_isolated_configured_interpreter(inputs, monkeypatch):
    directory, _, _, _ = inputs
    captured = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv: captured.append(argv) or type("Result", (), {"returncode": 0})())
    assert cli.tool(["coverage", "--", "plan", "--request", "/tmp/synthetic.json"], Manifest(directory / "operator.json")) == 0
    assert captured[0][1:3] == ["-I", "-B"]
    assert captured[0][3].endswith("scpolya_agent/coverage_diagnostics.py")
    assert captured[0][4:] == ["plan", "--request", "/tmp/synthetic.json"]


def test_citation_requires_exact_delivered_field_and_type(inputs):
    from scpolya_agent.citations import check
    directory, _, _, manifest = inputs
    response = query(Manifest(directory / "operator.json"), "task", "alpha", "/data/unit")
    delivery = write(directory / "delivery.json", response)
    manifest["control"] = {"deliveries": {"delivery-001": delivery}}
    write(directory / "operator.json", manifest)
    configured = Manifest(directory / "operator.json")
    assert check(configured, "delivery-001", "alpha", "/data/unit/count", digest(7))["status"] == "verified"
    for pointer, value_sha, code in [("/data/unit_extra/count", digest(9), "citation_not_delivered"),
        ("/result/data/unit/count", digest(7), "citation_not_delivered"),
        ("/data/unit/flag", digest(1), "citation_value_hash")]:
        with pytest.raises(ManifestError) as exc:
            check(configured, "delivery-001", "alpha", pointer, value_sha)
        assert exc.value.code == code
    with pytest.raises(ManifestError) as exc:
        check(configured, "mentioned-response.json", "alpha", "/data/unit/count", digest(7))
    assert exc.value.code == "delivery_unregistered"


def test_citation_rejects_changed_delivery_and_source(inputs):
    from scpolya_agent.citations import check
    directory, _, _, manifest = inputs
    response = query(Manifest(directory / "operator.json"), "task", "alpha", "/data/unit/flag")
    response["value"] = 1
    manifest["control"] = {"deliveries": {"d1": write(directory / "delivery.json", response)}}
    write(directory / "operator.json", manifest)
    with pytest.raises(ManifestError) as exc:
        check(Manifest(directory / "operator.json"), "d1", "alpha", "/data/unit/flag", digest(1))
    assert exc.value.code == "delivery_envelope_hash"


