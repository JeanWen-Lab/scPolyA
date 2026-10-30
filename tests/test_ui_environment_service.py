from __future__ import annotations

import ast
from pathlib import Path

from scpolya_ui.registry import PathMapping
from scpolya_ui.job_supervisor import child_environment
from scpolya_ui.services.environment_service import (
    PIPELINE_TOOLS,
    job_search_path,
    pipeline_tool_checks,
    reference_input_checks,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _main_required_tools() -> tuple[str, ...]:
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "REQUIRED_TOOLS" for target in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("REQUIRED_TOOLS not found in main.py")


def test_pipeline_tools_match_main_verify_tools():
    assert PIPELINE_TOOLS == _main_required_tools()


def test_tool_checks_report_missing_tool():
    checks = pipeline_tool_checks(("definitely-not-a-real-tool-scpolya",))
    assert len(checks) == 1
    assert checks[0].found is False


def test_reference_input_checks_apply_mappings(tmp_path):
    new_root = tmp_path / "new"
    new_root.mkdir()
    (new_root / "genes.gtf").write_text("x", encoding="utf-8")
    config = {
        "gene_gtf": "/old/location/genes.gtf",
        "chrom_sizes": str(tmp_path / "missing.sizes"),
        "species": "mouse",
    }
    mapping = PathMapping(old_path="/old/location", new_path=str(new_root), note="")
    checks = {item.key: item for item in reference_input_checks(config, [mapping])}
    assert checks["gene_gtf"].exists and checks["gene_gtf"].mapped
    assert checks["gene_gtf"].resolved == str(new_root / "genes.gtf")
    assert not checks["chrom_sizes"].exists and not checks["chrom_sizes"].mapped
    assert "species" not in checks


def test_job_search_path_matches_supervisor_environment(monkeypatch, tmp_path):
    python = tmp_path / "env" / "bin" / "python"
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    expected = child_environment([str(python), "main.py"])["PATH"]
    assert job_search_path(str(python)) == expected


def test_tool_checks_find_tool_in_interpreter_dir(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "samtools"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)
    path = job_search_path(str(bin_dir / "python"), base_path="/nonexistent")
    (check,) = pipeline_tool_checks(("samtools",), search_path=path)
    assert check.found and check.path == str(tool)
