from __future__ import annotations

import csv
import gc
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from contextlib import closing

import pytest

from arguments import parse_arguments
from scpolya_agent import INSPECTION_SCHEMA, encode_inspection, inspect_workspace
from scpolya_agent.bounds import (
    MAX_COMPLETION_BYTES, MAX_COMPLETION_ROWS, MAX_IDENTITY_ROWS, MAX_INVOCATION_BYTES,
    MAX_METADATA_BYTES, MAX_NAMESPACE_ENTRIES, MAX_RESPONSE_BYTES, MAX_STATE_TEXT_BYTES,
)
from task_manage import Plan, TaskManager
from task_manage.cache_epoch import PIPELINE_CACHE_EPOCH, PIPELINE_CACHE_EPOCH_KEY
from task_manage.define import UNIT_INPUTS_CSV_NAME
from task_manage.output_projection import project_run_outputs_v1
from task_manage.pipeline_contracts import build_current_task_instance_contract_from_keys_v2
from task_manage.task_coordinate import build_task_coordinate_v1, build_task_coordinate_v1_from_contract
from task_manage.task_coordinate_store import write_task_coordinate_done_v2
from task_manage.task_specs import task_specs_by_key

REPO = Path(__file__).resolve().parents[1]


def _tree(root):
    """Content + write metadata for tiny fixtures only; excludes access time."""
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mode, path.lstat().st_mtime_ns,
            path.lstat().st_ctime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        )
        for path in [root, *sorted(root.rglob("*"))]
    }


@pytest.fixture
def run(tmp_path):
    args = parse_arguments([
        "run", "--output-dir", str(tmp_path), "--run-id", "run",
        "--species", "mouse", "--inputs", "inputs.csv", "--gene-gtf", "genes.gtf",
        "--chrom-sizes", "chrom.sizes", "--coverage-scaling", "none",
        "--merge-distance", "0", "--unscaled-depth-threshold", "1",
        "--unscaled-depth-threshold-polyA", "1",
    ])
    config = {**vars(args), "dedup_method": "directional", "polyA_status": "polyA-10-1-0",
              "artifact_naming_mode": "legacy"}
    root = tmp_path / "run"
    tm = TaskManager(state_dir=root / "state", config=config)
    tm.load_or_init(Plan(samples=["001"], contigs_by_sample={"001": ["chr1"]}))
    # Fixture setup's core DB contexts do not close connections. Collect them
    # before observations, so delayed fixture checkpoints cannot mimic writes by
    # the inspector during later imports/allocations.
    gc.collect()
    table = root / "input_tables" / UNIT_INPUTS_CSV_NAME("directional")
    table.parent.mkdir()
    with table.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["unit_id", "sample_id", "contig_name", "dedup_status", "resolved_path"])
        writer.writeheader()
        for sample in ("001", "NA", "1e3", "S.chr1"):
            writer.writerow(dict(unit_id=f"{sample}.chr1", sample_id=sample,
                                 contig_name="chr1", dedup_status="external", resolved_path="unused.bam"))
    return root, tm, config


def _done(tm, epoch=PIPELINE_CACHE_EPOCH):
    stage, task = "extract_subsets", "extract_polyA"
    item = {"uid": "001.chr1", "dedup_method": "directional", "polyA_status": "polyA-10-1-0",
            "minpolyA": 10, "nonA_allowed": 1, "minMAPQ": 0}
    spec = task_specs_by_key()[(stage, task)]
    contract = build_current_task_instance_contract_from_keys_v2(
        stage=stage, task_base=task, item=item, identity_keys=spec.identity_keys,
        param_keys=spec.param_keys, level=spec.level,
    )
    coordinate = build_task_coordinate_v1_from_contract(contract)
    if epoch != PIPELINE_CACHE_EPOCH:
        params = coordinate.params_dict
        params[PIPELINE_CACHE_EPOCH_KEY] = epoch
        coordinate = build_task_coordinate_v1(stage=stage, task_base=task,
                                               identity=coordinate.identity_dict, params=params)
    with closing(tm.store.connect()) as con, con:
        write_task_coordinate_done_v2(con, coordinate, done_at="2026-10-18T00:00:00Z",
                                     meta_json=None, source="runtime")
    return coordinate


def test_valid_context_text_identity_and_completion_are_read_only(run):
    root, tm, config = run
    coordinate = _done(tm)
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["schema_version"] == INSPECTION_SCHEMA
    assert result["configuration"]["effective_config"] == config
    assert result["configuration"]["source"].endswith("#run_config_ledger:full_config")
    epoch = result["cache_epoch"]
    assert epoch["status"] == "unknown" and epoch["blocks_run"] is None
    assert epoch["assessment_complete"] is False
    assert epoch["copied_state_epoch"]["status"] == "current"
    assert epoch["copied_state_epoch"]["blocks_run"] is False
    snapshot = result["task_completion"]["snapshot"]
    assert snapshot["completion_source"] == "structured"
    assert snapshot["record_count"] == 1
    assert snapshot["records"][0]["task_coordinate"]["task_coordinate_id"] == coordinate.task_coordinate_id
    assert [row["sample_id"] for row in result["identities"]["rows"]] == ["001", "NA", "1e3", "S.chr1"]
    assert result["artifacts"]["status"] == "available", result["issues"]
    assert result["artifacts"]["completed_task_health"]["unassessed_tasks"][0]["reason"]
    assert result["scientific_quality"] == {"status": "not_evaluated", "metrics": None}
    assert result["versions"]["run_product"]["commit"] is None
    assert result["versions"]["inspector_checkout"]["commit"] is not None
    assert result["capabilities"] == dict(inspect_context=True, execute=False, recommend_parameters=False,
                                         analyze=False, export_report=False)
    assert "scpolya-inspect-" not in json.dumps(result)


def test_existing_artifact_does_not_manufacture_completion_or_quality(run):
    root, _, config = run
    scope = next(scope for scope in project_run_outputs_v1(config).scopes
                 if scope.path_kind == "file" and Path(scope.path).is_relative_to(root))
    artifact = Path(scope.path)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("nonempty is not task completion\n")
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    observed = next(row for row in result["artifacts"]["observations"] if row["scope_id"] == scope.scope_id)
    assert observed["exists"] is True and observed["health"] == "nonempty"
    assert result["task_completion"]["snapshot"]["record_count"] == 0
    assert result["artifacts"]["completed_task_health"]["done_task_count"] == 0
    assert result["scientific_quality"]["metrics"] is None


@pytest.mark.parametrize("state_kind", ["missing", "corrupt", "empty_sqlite"])
def test_unavailable_state_has_reasons_and_no_synthetic_zero(tmp_path, state_kind):
    root = tmp_path / "run"
    (root / "state").mkdir(parents=True)
    db = root / "state" / "state.sqlite"
    if state_kind == "corrupt":
        db.write_text("this is not sqlite")
    elif state_kind == "empty_sqlite":
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE unrelated (value TEXT)")
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["status"] == "partial"
    assert result["configuration"]["effective_config"] is None
    assert result["identities"]["rows"] is None
    assert result["scientific_quality"]["metrics"] is None
    assert result["task_completion"]["status"] == "unavailable"
    assert result["issues"]
    epoch = result["cache_epoch"]
    if state_kind == "corrupt":
        assert epoch["status"] == "unknown" and epoch["blocks_run"] is None
        assert epoch["copied_state_epoch"] is None
        assert result["state"]["budget_preflight"]["status"] == "failed"
    else:
        assert epoch["status"] == "unknown" and epoch["blocks_run"] is None
        if state_kind == "missing":
            assert epoch["copied_state_epoch"] is None
        else:
            assert epoch["copied_state_epoch"]["status"] == "fresh"
    assert epoch["assessment_complete"] is False
    if state_kind != "empty_sqlite":
        assert result["task_completion"]["snapshot"] is None


@pytest.mark.parametrize("epoch, expected", [("3", "upgrade_in_progress"), ("future", "incompatible")])
def test_older_or_unknown_epoch_is_not_reinterpreted_as_current(run, epoch, expected):
    root, tm, _ = run
    _done(tm, epoch)
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["cache_epoch"]["status"] == "blocked"
    assert result["cache_epoch"]["blocks_run"] is True
    assert result["cache_epoch"]["assessment_complete"] is False
    copied = result["cache_epoch"]["copied_state_epoch"]
    assert copied["status"] == expected
    assert copied["blocks_run"] is True
    assert copied["assessment_scope"] == "copied_state_only"
    assert copied["untracked_path_examples"] is None
    assert result["versions"]["run_product"]["version"] is None
    assert result["artifacts"]["projection"] is None


@pytest.mark.parametrize("schema, corrupt_hash", [("v1", False), ("v2", False), ("v2", True), ("v9", False)])
def test_invocation_snapshot_provenance_never_falls_back_to_current_state(run, schema, corrupt_hash):
    root, _, config = run
    path = root / "logs" / "invocations" / "record" / "invocation.json"
    path.parent.mkdir(parents=True)
    historical = dict(config, minMAPQ=99)
    digest = hashlib.sha256(json.dumps(historical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    path.write_text(json.dumps(dict(
        schema_version=f"scpolya.logging.invocation.{schema}", run_root=str(root),
        resolved_config=historical, config_schema_version=1,
        config_hash="bad" if corrupt_hash else digest,
        output_dir=historical["output_dir"], run_id=historical["run_id"],
        status="completed",
    )))
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    record = result["invocations"][0]
    if schema == "v2" and not corrupt_hash:
        assert record["effective_config"]["minMAPQ"] == 99
        assert result["configuration"]["effective_config"]["minMAPQ"] != 99
    else:
        assert record["effective_config"] is None
        assert record["config_status"] == "unavailable"
        assert record["reason"]
    assert record["task_completion_evidence"] is False
    assert record["historical_results"]["snapshot"] is None


def test_wal_is_read_from_private_copy_without_touching_original(run):
    root, tm, _ = run
    with sqlite3.connect(tm.db_path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE wal_only_fixture (value TEXT)")
        writer.commit()
        _done(tm)
        before = _tree(root)
        result = inspect_workspace(root)
        assert _tree(root) == before
        assert result["task_completion"]["snapshot"]["record_count"] == 1


def test_corrupt_config_and_unit_table_are_reported(run):
    root, tm, _ = run
    with closing(tm.store.connect()) as con, con:
        con.execute("UPDATE run_config_ledger SET value_json='not-json' WHERE snapshot_key='full_config'")
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["configuration"]["status"] == "unavailable"
    assert any(issue["section"] == "configuration" for issue in result["issues"])


def test_malformed_unit_table_is_not_parsed_as_identity(run):
    root, _, _ = run
    path = root / "input_tables" / UNIT_INPUTS_CSV_NAME("directional")
    path.write_text("unit_id,contig_name\n001.chr1,chr1\n")
    result = inspect_workspace(root)
    assert result["identities"]["status"] == "unavailable"
    assert result["identities"]["rows"] is None
    assert any(issue["section"] == "identities" for issue in result["issues"])


@pytest.mark.parametrize("args", [[], ["inspect"], ["execute"], ["inspect", "--run-root", "/does/not/exist"]])
def test_real_cli_errors_are_structured_json(args):
    call = subprocess.run([sys.executable, "-B", "-m", "scpolya_agent", *args],
                          cwd=REPO, capture_output=True, text=True)
    assert call.returncode == 2
    result = json.loads(call.stdout)
    assert result["schema_version"] == INSPECTION_SCHEMA
    assert result["status"] == "error" and result["issues"]
    assert "Traceback" not in call.stderr


def test_real_cli_is_json_only_repeatable_and_read_only(run):
    root, _, _ = run
    before = _tree(root)
    command = [sys.executable, "-B", "-m", "scpolya_agent", "inspect", "--run-root", str(root), "--repo-root", str(REPO)]
    calls = [subprocess.run(command, cwd=REPO, capture_output=True, text=True) for _ in range(2)]
    assert all(call.returncode == 0 for call in calls)
    assert calls[0].stdout == calls[1].stdout
    assert json.loads(calls[0].stdout)["configuration"]["status"] == "available"
    assert _tree(root) == before


def test_no_nicegui_import_for_inspection(run):
    root, _, _ = run
    inspect_workspace(root)
    assert not any(name == "nicegui" or name.startswith("nicegui.") for name in sys.modules)


def test_state_symlink_and_size_limit_do_not_open_original(run, tmp_path, monkeypatch):
    root, tm, _ = run
    import scpolya_agent.inspection as inspection
    monkeypatch.setattr(inspection, "MAX_STATE_BYTES", 1)
    before = _tree(root)
    result = inspect_workspace(root)
    assert result["state"]["status"] == "unreadable"
    assert _tree(root) == before
    monkeypatch.setattr(inspection, "MAX_STATE_BYTES", 64 * 1024 * 1024)
    other = tmp_path / "other"
    other.mkdir()
    (other / "state").symlink_to(root / "state", target_is_directory=True)
    result = inspect_workspace(other)
    assert result["state"]["status"] == "unreadable"
    assert _tree(root) == before


def test_unit_health_never_calls_directory_creating_core_branch(run, monkeypatch):
    root, tm, _ = run
    _done(tm)
    (root / "unit_outputs").mkdir()
    from task_manage.run_layout import RunLayout

    def forbidden(*args, **kwargs):
        pytest.fail("inspection invoked a directory-creating core getter")

    monkeypatch.setattr(RunLayout, "UnitOutput", forbidden)
    monkeypatch.setattr(RunLayout, "CohortOutput", forbidden)
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert not (root / "unit_outputs" / "001.chr1").exists()
    assert result["task_completion"]["snapshot"]["record_count"] == 1
    assert len(result["artifacts"]["completed_task_health"]["unassessed_tasks"]) == 1


def test_combined_native_health_is_withheld_even_for_existing_files(run, monkeypatch):
    root, tm, config = run
    from task_manage.parameter_studies import final_peak_item_for_config
    from task_manage.run_layout import RunLayout
    # The fixture, not inspection, publishes the product's namespace manifest.
    RunLayout(out=root.parent, run_id=root.name, artifact_naming_mode="legacy").AnnotatedFinalOutputSub(
        final_peak_item_for_config(config)
    )
    coordinate = build_task_coordinate_v1(
        stage="annotate_peaks", task_base="convert_peaks_bed_to_gtf", identity={},
        params={PIPELINE_CACHE_EPOCH_KEY: PIPELINE_CACHE_EPOCH},
    )
    with closing(tm.store.connect()) as con, con:
        write_task_coordinate_done_v2(con, coordinate, done_at="2026-10-18T00:00:00Z",
                                     meta_json=None, source="runtime")
    import task_manage.artifact_health as health_module
    monkeypatch.setattr(health_module, "check_task_artifact_health_v2", lambda *args, **kwargs: pytest.fail("combined health must never run"))
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["artifacts"]["status"] == "available", result["issues"]
    health = result["artifacts"]["completed_task_health"]
    assert health["status"] == "withheld" and health["entries"] is None
    assert len(health["unassessed_tasks"]) == 1
    scope = next(scope for scope in project_run_outputs_v1(config).scopes if scope.path_kind == "file")
    path = Path(scope.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture content: file health, no scientific validation\n")
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["artifacts"]["completed_task_health"] is not None, result["issues"]
    assert result["artifacts"]["completed_task_health"]["status"] == "withheld"
    assert result["scientific_quality"]["status"] == "not_evaluated"


def test_changed_state_copy_is_rejected(run, monkeypatch):
    root, _, _ = run
    import scpolya_agent.inspection as inspection
    fingerprint = inspection._fingerprint
    calls = 0

    def changing(path):
        nonlocal calls
        calls += 1
        value = fingerprint(path)
        if calls > 3 and value is not None:
            return (*value[:-1], value[-1] + 1)
        return value

    monkeypatch.setattr(inspection, "_fingerprint", changing)
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["state"]["status"] == "unreadable"
    assert any("changed while copying" in issue["detail"] for issue in result["issues"])
    assert result["task_completion"]["snapshot"] is None


def test_config_root_mismatch_does_not_read_or_rebase_historical_artifacts(run, tmp_path):
    root, tm, config = run
    with closing(tm.store.connect()) as con, con:
        con.execute("UPDATE run_config_ledger SET value_json=? WHERE snapshot_key='full_config'",
                    (json.dumps(dict(config, output_dir=str(tmp_path / "elsewhere"))),))
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["artifacts"]["projection"] is None
    assert any("different root" in issue["detail"] for issue in result["issues"])


def test_arbitrary_repo_argument_cannot_replace_actual_inspector_version(run, tmp_path):
    root, _, _ = run
    result = inspect_workspace(root, repo_root=tmp_path)
    assert result["versions"]["inspector_checkout"]["path"] == str(REPO)
    assert result["versions"]["requested_checkout"]["commit"] is None
    assert result["versions"]["run_product"]["commit"] is None


def test_blank_identity_and_corrupt_manifest_are_explicitly_unavailable(run):
    root, _, _ = run
    table = root / "input_tables" / UNIT_INPUTS_CSV_NAME("directional")
    table.write_text("unit_id,sample_id,contig_name,dedup_status\nu1,   ,chr1,external\n")
    manifest = root / "state" / "artifact_name_manifest.v1.json"
    manifest.write_text("not-json")
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["identities"]["rows"] is None
    assert any("blank unit/sample" in issue["detail"] for issue in result["issues"])
    assert result["artifacts"]["projection"]["scopes"] == []
    assert result["artifacts"]["projection"]["warnings"]
    assert any(issue["section"] == "artifacts" for issue in result["issues"])


@pytest.mark.parametrize("occupied", [False, True], ids=["empty-root-control", "fresh-db-with-untracked-output"])
def test_copied_fresh_db_never_clears_original_root(tmp_path, occupied):
    """DIR-INSPECT-001: a DB-only copy loses native filesystem evidence."""
    from task_manage.cache_epoch_status import inspect_run_root_cache_epoch

    root = tmp_path / "run"
    db = root / "state" / "state.sqlite"
    db.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db)) as con, con:
        con.execute("CREATE TABLE unrelated (value TEXT)")
    if occupied:
        (root / "final_output").mkdir()
        (root / "final_output" / "orphan.tsv").write_text("synthetic untracked output\n")
    # Native inspection is used only as the synthetic fixture oracle. The
    # adapter never opens the original DB or duplicates these filesystem rules.
    native = inspect_run_root_cache_epoch(db)
    assert native.status == ("untracked_outputs" if occupied else "fresh")
    assert native.blocks_run is occupied
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    epoch = result["cache_epoch"]
    assert epoch["assessment_scope"] == "original_run_root"
    assert epoch["assessment_complete"] is False
    assert epoch["status"] == "unknown"
    assert epoch["blocks_run"] is None
    assert "not assessed" in epoch["reason"]
    copied = epoch["copied_state_epoch"]
    assert copied["assessment_scope"] == "copied_state_only"
    assert copied["source_state_db_path"] == str(db)
    assert "state_db_path" not in copied
    assert copied["status"] == "fresh" and copied["blocks_run"] is False
    assert copied["untracked_path_examples"] is None
    assert any(issue["code"] == "original_root_not_fully_assessed" for issue in result["issues"])


def _trace_boundary_access(patch, symlink, external):
    """Record real target-following calls, allowing lstat of the link itself."""
    calls = []

    def watched(path, operation, follow=True):
        try:
            # Preserve raw '..' components so a tracing helper cannot hide the
            # same link/.. traversal that DIR-INSPECT-002 is intended to catch.
            value = Path(os.fspath(path))
            if not value.is_absolute():
                value = Path(os.getcwd()) / value
        except TypeError:  # File descriptors are not path traversals.
            return
        if value.is_relative_to(external) or (
            value.is_relative_to(symlink) and (follow or value != symlink)
        ):
            calls.append((operation, str(value)))

    original_stat, original_open = Path.stat, Path.open
    original_iterdir, original_resolve = Path.iterdir, Path.resolve
    original_scandir = os.scandir

    def tracing_stat(path, *args, **kwargs):
        watched(path, "stat", kwargs.get("follow_symlinks", True))
        return original_stat(path, *args, **kwargs)

    def tracing_open(path, *args, **kwargs):
        watched(path, "open")
        return original_open(path, *args, **kwargs)

    def tracing_iterdir(path):
        watched(path, "iterdir")
        return original_iterdir(path)

    def tracing_resolve(path, *args, **kwargs):
        watched(path, "resolve")
        return original_resolve(path, *args, **kwargs)

    def tracing_scandir(path):
        watched(path, "scandir")
        return original_scandir(path)

    patch.setattr(Path, "stat", tracing_stat)
    patch.setattr(Path, "open", tracing_open)
    patch.setattr(Path, "iterdir", tracing_iterdir)
    patch.setattr(Path, "resolve", tracing_resolve)
    patch.setattr(os, "scandir", tracing_scandir)
    return calls


@pytest.mark.parametrize("location", ["final_output", "logs", "logs/invocations", "input_tables", "state"])
def test_directory_and_metadata_ancestor_symlinks_have_zero_external_access(run, tmp_path, monkeypatch, location):
    root, _, _ = run
    external = tmp_path / "outside"
    external.mkdir()
    (external / "invocations").mkdir()
    (external / "final.external-marker").mkdir()
    symlink = root / location
    if symlink.exists():
        symlink.rename(root / (symlink.name + "-saved"))
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(external, target_is_directory=True)
    with monkeypatch.context() as patch:
        calls = _trace_boundary_access(patch, symlink, external)
        result = inspect_workspace(root)
    assert calls == []
    assert any("symlink" in issue["detail"] for issue in result["issues"])
    if location == "final_output":
        assert result["artifacts"]["projection"] is None


def test_root_ancestor_symlink_is_rejected_before_external_access(run, tmp_path, monkeypatch):
    root, _, _ = run
    symlink = tmp_path / "linked-parent"
    symlink.symlink_to(root.parent, target_is_directory=True)
    with monkeypatch.context() as patch:
        calls = _trace_boundary_access(patch, symlink, root)
        result = inspect_workspace(symlink / root.name)
    assert calls == []
    assert result["status"] == "error"


@pytest.mark.parametrize("kind", ["manifest", "invocation", "invocation-parent", "artifact"])
def test_file_symlinks_are_not_followed_for_metadata_or_health(run, tmp_path, monkeypatch, kind):
    root, tm, config = run
    external = tmp_path / "outside-file"
    external.write_text("synthetic outside content")
    if kind == "manifest":
        symlink = root / "state" / "artifact_name_manifest.v1.json"
    elif kind in {"invocation", "invocation-parent"}:
        symlink = root / "logs" / "invocations" / "record"
        if kind == "invocation":
            symlink = symlink / "invocation.json"
    else:
        import task_manage.artifact_health as health
        coordinate = build_task_coordinate_v1(
            stage="rna_gene_matrix", task_base="summarize_gene_matrix",
            identity={"sid": "001", "dedup_method": "directional"},
            params={PIPELINE_CACHE_EPOCH_KEY: PIPELINE_CACHE_EPOCH},
        )
        with closing(tm.store.connect()) as con, con:
            write_task_coordinate_done_v2(con, coordinate, done_at="2026-10-18T00:00:00Z", meta_json=None, source="runtime")
        # Fixture-only native expectation construction reproduces B2 exactly.
        record = health.ArtifactHealthTaskRecordV2(task_coordinate=coordinate, done_at="2026-10-18T00:00:00Z")
        expectations = tuple(health._expected_artifacts_for_records((record,), config, root))
        symlink = next(Path(path) for entry in expectations for path in entry.paths
                       if any(part.startswith("rna-gene-matrix.") for part in Path(path).parts))
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(external)
    with monkeypatch.context() as patch:
        calls = _trace_boundary_access(patch, symlink, external)
        result = inspect_workspace(root)
    assert calls == []
    if kind == "artifact":
        assert result["artifacts"]["completed_task_health"]["status"] == "withheld"


def test_namespace_entry_limit_precedes_native_projection(run, monkeypatch):
    root, _, _ = run
    namespace = root / "final_output"
    namespace.mkdir()
    for index in range(MAX_NAMESPACE_ENTRIES + 1):
        (namespace / str(index)).mkdir()
    import task_manage.output_projection as projection
    monkeypatch.setattr(projection, "project_run_outputs_v1", lambda *args, **kwargs: pytest.fail("unbounded namespace reached native projection"))
    result = inspect_workspace(root)
    assert result["artifacts"]["status"] == "omitted"
    assert result["artifacts"]["projection"] is None
    assert any("namespace entry limit" in issue["detail"] for issue in result["issues"])


def _write_invocation(root, name, padding):
    path = root / "logs" / "invocations" / name / "invocation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {"output_dir": str(root.parent), "run_id": root.name, "padding": padding}
    payload = dict(schema_version="scpolya.logging.invocation.v2", run_root=str(root),
                   resolved_config=config, config_schema_version=1,
                   config_hash=hashlib.sha256(encode_inspection(config).encode("ascii")).hexdigest(),
                   output_dir=config["output_dir"], run_id=config["run_id"])
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(data)
    return path, len(data)


def test_aggregate_invocation_input_budget_is_enforced_before_reading(run):
    root, _, _ = run
    size = None
    for index in range(100):
        _, size = _write_invocation(root, f"{index:03d}", "x" * 20_000)
    result = inspect_workspace(root)
    catalog = result["invocation_catalog"]
    expected = MAX_INVOCATION_BYTES // size
    assert catalog["entries_total"] == 100 and catalog["records_returned"] == 100
    assert catalog["bytes_read"] == expected * size <= MAX_INVOCATION_BYTES
    assert catalog["snapshots_available"] == expected
    assert sum(row["config_status"] == "omitted" for row in result["invocations"]) == 100 - expected
    assert len(encode_inspection(result).encode("ascii")) + 1 <= MAX_RESPONSE_BYTES


def test_original_100_large_invocation_counterexample_is_not_materialized(run, monkeypatch):
    root, _, _ = run
    first, size = _write_invocation(root, "000", "x" * 900_000)
    for index in range(1, 100):
        path = first.parents[1] / f"{index:03d}" / "invocation.json"
        path.parent.mkdir()
        os.link(first, path)  # One tiny synthetic allocation, 100 valid records.
    assert size > MAX_METADATA_BYTES
    original_open = Path.open

    def no_oversize_open(path, *args, **kwargs):
        if path.name == "invocation.json":
            pytest.fail("oversize invocation was opened")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", no_oversize_open)
        result = inspect_workspace(root)
    assert result["invocation_catalog"]["bytes_read"] == 0
    assert result["invocation_catalog"]["snapshots_available"] == 0
    assert all(row["effective_config"] is None for row in result["invocations"])
    assert len(encode_inspection(result).encode("ascii")) + 1 <= MAX_RESPONSE_BYTES


def test_response_budget_omits_whole_snapshots_with_same_api_cli_encoding(run):
    root, tm, config = run
    persisted = dict(config, padding="é" * 12_000)
    with closing(tm.store.connect()) as con, con:
        con.execute("UPDATE run_config_ledger SET value_json=? WHERE snapshot_key='full_config'",
                    (json.dumps(persisted, ensure_ascii=False),))
    for index in range(10):
        _write_invocation(root, f"{index:03d}", "é" * 6000)
    result = inspect_workspace(root)
    assert result["configuration"]["effective_config"] == persisted
    assert any(row["config_status"] == "omitted" and "response byte budget" in row["reason"] for row in result["invocations"])
    for row in result["invocations"]:
        if row["config_status"] == "available":
            assert row["effective_config"]["padding"] == "é" * 6000
        else:
            assert row["effective_config"] is None
    encoded = encode_inspection(result) + "\n"
    assert encoded.isascii() and len(encoded) <= MAX_RESPONSE_BYTES
    call = subprocess.run([sys.executable, "-B", "-m", "scpolya_agent", "inspect", "--run-root", str(root)],
                          cwd=REPO, capture_output=True, text=True, check=True)
    assert call.stdout == encoded


def test_completion_and_identity_rows_are_bounded_before_native_loading(run, monkeypatch):
    root, tm, _ = run
    with closing(tm.store.connect()) as con, con:
        for index in range(MAX_COMPLETION_ROWS + 1):
            coordinate = build_task_coordinate_v1(stage="rna_gene_matrix", task_base="summarize_gene_matrix",
                identity={"sid": str(index), "dedup_method": "directional"}, params={PIPELINE_CACHE_EPOCH_KEY: PIPELINE_CACHE_EPOCH})
            write_task_coordinate_done_v2(con, coordinate, done_at="2026-10-18T00:00:00Z", meta_json=None, source="runtime")
    table = root / "input_tables" / UNIT_INPUTS_CSV_NAME("directional")
    table.write_text("unit_id,sample_id,contig_name,dedup_status\n" + "".join(f"u{i},s{i},chr1,external\n" for i in range(MAX_IDENTITY_ROWS + 1)))
    import task_manage.task_completion as completion
    import task_manage.finalized_unit_inputs as identities
    monkeypatch.setattr(completion, "load_task_completion_snapshot_v2", lambda *args: pytest.fail("over-limit completion snapshot loaded"))
    monkeypatch.setattr(identities, "load_finalized_unit_input_table", lambda *args: pytest.fail("over-limit identity table loaded"))
    result = inspect_workspace(root)
    assert result["task_completion"]["status"] == "omitted"
    assert result["task_completion"]["snapshot"] is None
    assert result["task_completion"]["candidate_rows_total"] == MAX_COMPLETION_ROWS + 1
    assert result["task_completion"]["records_returned"] == 0
    assert result["task_completion"]["records_total"] is None
    assert result["identities"]["status"] == "omitted"
    assert result["identities"]["returned_rows"] == 0 and result["identities"]["total_rows"] is None
    assert result["identities"]["total_rows_lower_bound"] == MAX_IDENTITY_ROWS + 1


def test_large_config_is_omitted_before_json_loading(run, monkeypatch):
    root, tm, config = run
    with closing(tm.store.connect()) as con, con:
        con.execute("UPDATE run_config_ledger SET value_json=? WHERE snapshot_key='full_config'",
                    (json.dumps(dict(config, padding="x" * 40_000)),))
    import task_manage.run_config_preview as preview
    monkeypatch.setattr(preview, "load_run_config_snapshot", lambda *args: pytest.fail("oversize config loaded"))
    result = inspect_workspace(root)
    assert result["configuration"]["status"] == "omitted"
    assert result["configuration"]["effective_config"] is None
    assert result["configuration"]["total_input_bytes"] > result["limits"]["config_bytes"]
    assert result["configuration"]["snapshots_returned"] == 0
    assert result["configuration"]["snapshots_total"] is None
    assert result["configuration"]["candidate_snapshots_total"] == 1


def test_completion_cell_budget_precedes_native_loading(run, monkeypatch):
    root, tm, _ = run
    coordinate = _done(tm)
    with closing(tm.store.connect()) as con, con:
        con.execute("UPDATE task_done_coordinate_v2 SET meta_json=? WHERE task_coordinate_id=?",
                    ("x" * (MAX_COMPLETION_BYTES + 1), coordinate.task_coordinate_id))
    import task_manage.task_completion as completion
    monkeypatch.setattr(completion, "load_task_completion_snapshot_v2", lambda *args: pytest.fail("over-limit cells loaded"))
    result = inspect_workspace(root)
    assert result["task_completion"]["status"] == "omitted"
    assert result["task_completion"]["candidate_rows_total"] == 1
    assert result["task_completion"]["records_returned"] == 0


def test_identity_file_and_invocation_entry_limits(run, monkeypatch):
    root, _, _ = run
    table = root / "input_tables" / UNIT_INPUTS_CSV_NAME("directional")
    table.write_text("unit_id,sample_id,contig_name,dedup_status\n" + "x" * 70_000)
    invocation_root = root / "logs" / "invocations"
    invocation_root.mkdir(parents=True)
    for index in range(101):
        (invocation_root / str(index)).mkdir()
    import task_manage.finalized_unit_inputs as identities
    monkeypatch.setattr(identities, "load_finalized_unit_input_table", lambda *args: pytest.fail("over-limit table loaded"))
    result = inspect_workspace(root)
    assert result["identities"]["status"] == "omitted"
    assert result["identities"]["rows"] is None
    assert result["identities"]["returned_rows"] == 0
    assert result["invocations"] == []
    assert result["invocation_catalog"]["status"] == "omitted"
    assert result["invocation_catalog"]["entries_total"] is None
    assert result["invocation_catalog"]["entries_total_lower_bound"] == 101
    assert result["invocation_catalog"]["bytes_read"] == 0


def test_uninitialized_completion_authority_does_not_become_zero_done_health(run, tmp_path):
    _, _, config = run
    from task_manage.cache_epoch_status import PIPELINE_CACHE_EPOCH_ROOT_KEY, build_cache_epoch_root_marker
    root = tmp_path / "uninitialized"
    db = root / "state" / "state.sqlite"
    db.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db)) as con, con:
        con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT INTO meta VALUES (?, ?)", [
            (PIPELINE_CACHE_EPOCH_ROOT_KEY, build_cache_epoch_root_marker(None)),
            ("config_json", json.dumps(dict(config, run_id=root.name))),
        ])
    result = inspect_workspace(root)
    assert result["cache_epoch"]["copied_state_epoch"]["status"] == "current"
    assert result["task_completion"]["status"] == "unavailable"
    assert result["task_completion"]["snapshot"]["completion_source"] == "unavailable"
    health = result["artifacts"]["completed_task_health"]
    assert health["status"] == "withheld"
    assert health["done_task_count"] is None and health["unassessed_tasks"] is None


@pytest.mark.parametrize("case", ["dotdot_symlink", "cleaned_run_id"])
def test_native_projection_binding_is_validated_before_target_access(run, tmp_path, monkeypatch, case):
    original_root, tm, config = run
    if case == "dotdot_symlink":
        root = original_root
        external = tmp_path / "external"
        (external / "child").mkdir(parents=True)
        link = tmp_path / "link"
        link.symlink_to(external / "child", target_is_directory=True)
        declared = dict(config, output_dir=str(link) + "/..")
        outside_root = external / "run"
    else:
        parent = tmp_path / "outer"
        parent.mkdir()
        root = parent / "inner"
        original_root.rename(root)
        # The fixture DB moves, so update only its fixture access path.
        tm_db = root / "state" / "state.sqlite"
        declared = dict(config, output_dir=str(tmp_path), run_id="outer/inner")
        outside_root = tmp_path / "outer_inner"
        external = outside_root
        link = outside_root
    (outside_root / "final_output" / "final.marker").mkdir(parents=True)
    db = tm.db_path if case == "dotdot_symlink" else tm_db
    with closing(sqlite3.connect(db)) as con, con:
        con.execute("UPDATE run_config_ledger SET value_json=? WHERE snapshot_key='full_config'",
                    (json.dumps(declared),))
    import task_manage.output_projection as projection
    before = _tree(root)
    with monkeypatch.context() as patch:
        calls = _trace_boundary_access(patch, link, external)
        patch.setattr(projection, "project_run_outputs_v1", lambda *args: pytest.fail("unbound config reached native projection"))
        result = inspect_workspace(root)
    assert calls == []
    assert _tree(root) == before
    assert result["configuration"]["effective_config"] == declared
    assert result["artifacts"]["status"] == "unavailable"
    assert result["artifacts"]["projection"] is None
    assert any(("parent traversal" if case == "dotdot_symlink" else "clean_name") in issue["detail"] for issue in result["issues"])


@pytest.mark.parametrize("linked", [False, True], ids=["plain-parent-traversal", "symlink-parent-traversal"])
def test_explicit_root_parent_traversal_is_rejected_without_normalizing(run, tmp_path, monkeypatch, linked):
    root, _, _ = run
    component = tmp_path / "component"
    external = tmp_path / "external"
    if linked:
        (external / "child").mkdir(parents=True)
        component.symlink_to(external / "child", target_is_directory=True)
    else:
        component.mkdir()
    raw = str(component) + "/../run"
    with monkeypatch.context() as patch:
        calls = _trace_boundary_access(patch, component, external)
        result = inspect_workspace(raw)
    assert calls == []
    assert result["status"] == "error"
    assert result["issues"][0]["code"] == "invalid_path_representation"
    call = subprocess.run([sys.executable, "-B", "-m", "scpolya_agent", "inspect", "--run-root", raw],
                          cwd=REPO, capture_output=True, text=True)
    assert call.returncode == 2
    assert json.loads(call.stdout)["status"] == "error"


@pytest.mark.parametrize("failure", [sqlite3.OperationalError, sqlite3.DatabaseError])
def test_state_budget_preflight_failure_withholds_native_materialization(run, monkeypatch, failure):
    root, _, config = run
    import scpolya_agent.inspection as inspection
    import task_manage.cache_epoch_status as epoch
    import task_manage.task_completion as completion
    import task_manage.output_projection as projection

    def failed_bounds(*args):
        raise failure("forced budget preflight failure")

    def forbidden(*args, **kwargs):
        pytest.fail("native materialization followed a failed budget preflight")

    monkeypatch.setattr(inspection, "_state_bounds", failed_bounds)
    monkeypatch.setattr(epoch, "inspect_run_root_cache_epoch", forbidden)
    monkeypatch.setattr(completion, "load_task_completion_snapshot_v2", forbidden)
    monkeypatch.setattr(projection, "project_run_outputs_v1", forbidden)
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["state"]["budget_preflight"]["status"] == "failed"
    assert result["cache_epoch"]["status"] == "unknown" and result["cache_epoch"]["blocks_run"] is None
    assert result["cache_epoch"]["copied_state_epoch"] is None
    assert result["task_completion"]["status"] == "unavailable"
    assert result["task_completion"]["snapshot"] is None
    assert result["task_completion"]["records_returned"] == 0
    assert result["task_completion"]["records_total"] is None
    assert result["configuration"]["effective_config"] == config  # Its own byte check succeeded.
    assert result["identities"]["status"] == "available"
    assert result["artifacts"]["projection"] is None
    assert any(issue["code"] == "completion_preflight_failed" for issue in result["issues"])


@pytest.mark.parametrize("table", ["task_coordinate_state_v1", "task_completion_storage_state_v1"])
def test_completion_singleton_cells_are_budgeted_before_native_loading(run, monkeypatch, table):
    root, tm, config = run
    from scpolya_agent.bounds import MAX_COMPLETION_BYTES
    with closing(tm.store.connect()) as con, con:
        con.execute(f"UPDATE {table} SET updated_at=? WHERE singleton_id=1", ("x" * (MAX_COMPLETION_BYTES + 1),))
    import task_manage.task_completion as completion
    monkeypatch.setattr(completion, "load_task_completion_snapshot_v2", lambda *args: pytest.fail("oversize singleton reached native completion"))
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    budget = result["state"]["completion_budget"]
    assert budget["allowed"] is False
    assert result["state"]["budget_preflight"]["completion_allowed"] is False
    assert result["state"]["budget_preflight"]["epoch_allowed"] is True
    assert budget["tables"][table]["stored_cell_bytes"] > MAX_COMPLETION_BYTES
    assert budget["stored_cell_bytes_total"] > MAX_COMPLETION_BYTES
    assert result["task_completion"]["status"] == "omitted"
    assert result["task_completion"]["snapshot"] is None
    assert result["task_completion"]["candidate_rows_total"] == 0
    assert result["task_completion"]["records_total"] is None
    assert result["task_completion"]["records_returned"] == 0
    assert result["configuration"]["effective_config"] == config


def test_completion_byte_budget_is_aggregate_across_native_tables(run, monkeypatch):
    root, tm, _ = run
    from scpolya_agent.bounds import MAX_COMPLETION_BYTES
    tables = ("task_coordinate_state_v1", "task_completion_storage_state_v1", "task_done_coordinate_v2")
    with closing(tm.store.connect()) as con, con:
        for table in tables[:2]:
            con.execute(f"UPDATE {table} SET updated_at=? WHERE singleton_id=1", ("x" * (MAX_COMPLETION_BYTES // 2),))
    import task_manage.task_completion as completion
    monkeypatch.setattr(completion, "load_task_completion_snapshot_v2", lambda *args: pytest.fail("aggregate byte limit bypassed"))
    result = inspect_workspace(root)
    budget = result["state"]["completion_budget"]
    assert set(budget["tables"]) == set(tables)
    assert all(item["stored_cell_bytes"] < MAX_COMPLETION_BYTES for item in budget["tables"].values())
    assert budget["stored_cell_bytes_total"] == sum(item["stored_cell_bytes"] for item in budget["tables"].values()) > MAX_COMPLETION_BYTES
    assert budget["allowed"] is False
    assert result["task_completion"]["records_total"] is None
    assert result["task_completion"]["snapshot"] is None


def test_completion_row_budget_includes_authority_and_policy_singletons(run, monkeypatch):
    root, tm, _ = run
    _done(tm)
    import scpolya_agent.inspection as inspection
    import task_manage.task_completion as completion
    # Isolate row aggregation with a two-row test cap: each table has only one
    # row, but authority + policy + DONE total three. Production cap remains 100.
    monkeypatch.setattr(inspection, "MAX_COMPLETION_ROWS", 2)
    monkeypatch.setattr(completion, "load_task_completion_snapshot_v2", lambda *args: pytest.fail("aggregate row limit bypassed"))
    result = inspect_workspace(root)
    budget = result["state"]["completion_budget"]
    assert all(item["input_rows"] <= 2 for item in budget["tables"].values())
    assert budget["input_rows_total"] == 3
    assert budget["allowed"] is False
    assert result["task_completion"]["candidate_rows_total"] == 1
    assert result["task_completion"]["records_total"] is None


@pytest.mark.parametrize("table", ["task_coordinate_state_v1", "task_completion_storage_state_v1"])
def test_unmeasurable_completion_table_fails_closed(run, monkeypatch, table):
    root, _, config = run
    import scpolya_agent.inspection as inspection
    import task_manage.task_completion as completion
    original = inspection._table_budget

    def failed_measurement(con, name):
        if name == table:
            raise sqlite3.OperationalError("forced singleton budget measurement failure")
        return original(con, name)

    monkeypatch.setattr(inspection, "_table_budget", failed_measurement)
    monkeypatch.setattr(completion, "load_task_completion_snapshot_v2", lambda *args: pytest.fail("unmeasured table reached native completion"))
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["state"]["budget_preflight"]["status"] == "failed"
    budget = result["state"]["completion_budget"]
    assert budget["allowed"] is False
    assert budget["input_rows_total"] is None and budget["stored_cell_bytes_total"] is None
    assert budget["tables"] is None
    assert result["task_completion"]["snapshot"] is None
    assert result["task_completion"]["records_total"] is None
    assert result["configuration"]["effective_config"] == config


@pytest.mark.parametrize("kind", ["missing_authority", "missing_policy", "missing_done", "legacy_only"])
def test_completion_budget_does_not_invent_authority_for_missing_or_legacy_tables(run, kind):
    root, tm, config = run
    authority, policy, done = "task_coordinate_state_v1", "task_completion_storage_state_v1", "task_done_coordinate_v2"
    with closing(tm.store.connect()) as con, con:
        if kind == "legacy_only":
            for table in (authority, policy, done, "task_done_coordinate_v1", "task_done_flex"):
                con.execute(f"DROP TABLE IF EXISTS {table}")
            con.execute("CREATE TABLE task_done_flex(stage TEXT)")
            con.execute("INSERT INTO task_done_flex VALUES ('legacy fixture')")
        else:
            table = {"missing_authority": authority, "missing_policy": policy, "missing_done": done}[kind]
            con.execute(f"DROP TABLE {table}")
    result = inspect_workspace(root)
    budget = result["state"]["completion_budget"]
    assert budget["allowed"] is True  # Input admission is not authority.
    assert result["task_completion"]["status"] == "unavailable"
    assert result["task_completion"]["snapshot"] is None
    assert result["task_completion"].get("records_total") is None
    assert result["configuration"]["effective_config"] == config
    if kind in {"missing_done", "legacy_only"}:
        assert result["state"]["candidate_completion_rows"] is None
    if kind == "legacy_only":
        assert all(not item["present"] for item in budget["tables"].values())


@pytest.mark.parametrize("offset", [0, 1], ids=["exact-total-default", "total-default-plus-one"])
def test_new_completion_default_exact_aggregate_boundary(run, monkeypatch, offset):
    root, tm, _ = run
    tables = ("task_coordinate_state_v1", "task_completion_storage_state_v1", "task_done_coordinate_v2")
    with closing(tm.store.connect()) as con, con:
        con.execute("UPDATE task_coordinate_state_v1 SET updated_at='' WHERE singleton_id=1")
        # Independent fixture oracle over the small rows, before adding padding.
        base_bytes = sum(len(value if isinstance(value, bytes) else str(value).encode("utf-8"))
                         for table in tables for row in con.execute(f"SELECT * FROM {table}")
                         for value in row if value is not None)
        con.execute("UPDATE task_coordinate_state_v1 SET updated_at=? WHERE singleton_id=1",
                    ("x" * (MAX_COMPLETION_BYTES + offset - base_bytes),))
    import task_manage.task_completion as completion
    original = completion.load_task_completion_snapshot_v2
    calls = []

    def traced(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(completion, "load_task_completion_snapshot_v2", traced)
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    budget = result["state"]["completion_budget"]
    assert result["limits"]["completion_text_bytes"] == 64 * 1024
    assert budget["stored_cell_bytes_total"] == MAX_COMPLETION_BYTES + offset
    assert budget["allowed"] is (offset == 0)
    assert len(calls) == (1 if offset == 0 else 0)
    if offset:
        assert result["task_completion"]["snapshot"] is None
        assert result["task_completion"]["records_total"] is None
    else:
        assert result["task_completion"]["status"] == "available"


@pytest.mark.parametrize("offset", [0, 1], ids=["exact-table-default", "table-default-plus-one"])
def test_new_epoch_default_exact_table_boundary(run, monkeypatch, offset):
    root, tm, _ = run
    with closing(tm.store.connect()) as con, con:
        con.execute("INSERT INTO meta(key,value) VALUES ('budget-fixture-padding','')")
        base_bytes = sum(len(value if isinstance(value, bytes) else str(value).encode("utf-8"))
                         for row in con.execute("SELECT * FROM meta") for value in row if value is not None)
        con.execute("UPDATE meta SET value=? WHERE key='budget-fixture-padding'",
                    ("x" * (MAX_STATE_TEXT_BYTES + offset - base_bytes),))
    import task_manage.cache_epoch_status as epoch
    original = epoch.inspect_run_root_cache_epoch
    calls = []

    def traced(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(epoch, "inspect_run_root_cache_epoch", traced)
    before = _tree(root)
    result = inspect_workspace(root)
    assert _tree(root) == before
    assert result["limits"]["epoch_text_bytes_per_table"] == 256 * 1024
    assert result["state"]["budget_preflight"]["epoch_allowed"] is (offset == 0)
    assert len(calls) == (1 if offset == 0 else 0)
    assert result["cache_epoch"]["status"] == "unknown" and result["cache_epoch"]["blocks_run"] is None
    if offset:
        assert result["cache_epoch"]["copied_state_epoch"] is None
    else:
        assert result["cache_epoch"]["copied_state_epoch"]["status"] == "current"
