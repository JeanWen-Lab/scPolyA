"""Readers resolve the terminal annotation schema from the run's own records."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from quantification_config import TERMINAL_ANNOTATION_SCHEMA_IDENTITY
from scpolya_ui.config_schema import CONFIG_FIELDS
from scpolya_ui.run_records import RunRecord, config_hash
from scpolya_ui.services import run_evidence_service as service
from task_manage.artifact_naming import register_artifact_name_v1
from task_manage.output_projection import project_run_outputs_v1
from task_manage.parameter_studies import final_peak_item_for_config
from task_manage.run_layout import RunLayout

V3 = "terminal-annotation-v3"
V4 = "terminal-annotation-v4"
TABLE = "S.terminal_aware_distal_site_usage.v1.tsv.gz"


def _config(tmp_path):
    config = {f.key: f.default for f in CONFIG_FIELDS}
    config.update(output_dir=str(tmp_path), run_id="native", species="human", dedup_method="directional",
                  gene_gtf=str(tmp_path / "genes.gtf"), chrom_sizes=str(tmp_path / "chrom.sizes"),
                  quantification_mode="terminal-aware", summarize_matrix=True, psi_min_gene_umis=1,
                  minpolyA=5, nonA_allowed=1, minMAPQ=10, coverage_scaling="expr",
                  polyA_peak_threshold_mode="fixed", unscaled_depth_threshold_polyA=3.0,
                  exp_peak_threshold_mode="dynamic", exp_dynamic_threshold_stat="max_norm_fixed",
                  exp_dynamic_threshold_normalized=0.05, merge_distance_polyA=10, merge_distance=10)
    return config


def _write_run_outputs(tmp_path, config, schema, value):
    """Record and write one schema's annotation + summary outputs, as a run would."""
    layout = RunLayout(out=tmp_path, run_id="native", artifact_naming_mode="short")
    item = final_peak_item_for_config(config)
    item["terminal_annotation_schema"] = schema
    directory = layout.MatrixSummaryConfigSub(item, psi_min_gene_umis=1)  # registers records
    directory.mkdir(parents=True, exist_ok=True)
    table = directory / TABLE
    with gzip.open(table, "wt") as handle:
        handle.write(f"gene_id\tstrand\tcell_barcode\tdistal_site_usage\nG\t+\tA\t{value}\n")
    return table


def _record(tmp_path, config):
    root = tmp_path / "native"
    inv_id = "12345678123442348234123456789012"
    invocation = root / "logs/invocations/20261026T000000.000000Z-12345678/invocation.json"
    invocation.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    payload = {"schema_version": "scpolya.logging.invocation.v2", "invocation_id": inv_id,
               "status": "succeeded", "started_at": "2026-10-26T00:00:00+00:00",
               "finished_at": "2026-10-26T00:00:01+00:00", "duration_seconds": 1, "pid": 123,
               "argv": ["fixture", "run"], "error": "", "paths": {}, "run_root": str(root),
               "output_dir": str(tmp_path), "run_id": "native", "config_schema_version": 1,
               "resolved_config": config, "config_hash": digest}
    invocation.write_text(json.dumps(payload))
    return RunRecord(id="R-test", kind="cli_invocation", status="succeeded", workspace_id="W",
                     workspace_root=str(root), label="test", config=config, config_hash=config_hash(config),
                     created_at="", updated_at="", run_id="native", root_path=str(root), invocation_id=inv_id,
                     execution_config=config, execution_config_hash=config_hash(config))


def _annotation_schema(projection):
    scope = projection.by_scope_id["matrix_summary"]
    return next(n.payload["terminal_annotation_schema"] for n in scope.artifact_names if n.scope == "annotation")


def test_current_schema_is_v4():
    assert TERMINAL_ANNOTATION_SCHEMA_IDENTITY == V4


@pytest.mark.parametrize("schema", [V3, V4])
def test_recorded_v3_and_v4_runs_are_read_from_their_own_records(tmp_path, schema):
    config = _config(tmp_path)
    table = _write_run_outputs(tmp_path, config, schema, "0.25")
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert not projection.warnings
    assert _annotation_schema(projection) == schema
    assert Path(projection.by_scope_id["matrix_summary"].path) == table.parent
    # The default still describes what current code would write.
    assert _annotation_schema(project_run_outputs_v1(dict(config))) == V4
    tables, binding = service.discover(_record(tmp_path, config))
    assert [(t.key, t.path) for t in tables] == [("distal", str(table))]


def test_missing_record_projects_current_without_claiming_outputs(tmp_path):
    config = _config(tmp_path)
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert not projection.warnings
    assert _annotation_schema(projection) == V4
    assert not Path(projection.by_scope_id["matrix_summary"].path).exists()


def test_upgrade_writes_beside_v3_outputs_and_reader_refuses_ambiguity(tmp_path):
    config = _config(tmp_path)
    old_table = _write_run_outputs(tmp_path, config, V3, "0.25")
    old_bytes = old_table.read_bytes()
    # A v4 annotation of the same run root records a second, distinct namespace.
    layout = RunLayout(out=tmp_path, run_id="native", artifact_naming_mode="short")
    item = final_peak_item_for_config(config)
    assert item["terminal_annotation_schema"] == V4
    new_dir = layout.AnnotatedFinalOutputSub(item)
    register_artifact_name_v1(layout.ArtifactNameManifest, mode="short", record=layout.AnnotationNameRecord(item))
    assert V3 not in str(new_dir) and new_dir != old_table.parent
    assert old_table.read_bytes() == old_bytes
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert any("several schemas" in w and V3 in w and V4 in w for w in projection.warnings)
    with pytest.raises(Exception, match="several schemas"):
        service.discover(_record(tmp_path, config))
    assert old_table.read_bytes() == old_bytes


@pytest.mark.parametrize("schema", ["terminal-annotation-v2", "terminal-annotation-v9"])
def test_unreadable_or_unknown_recorded_schema_is_refused_not_guessed(tmp_path, schema):
    config = _config(tmp_path)
    _write_run_outputs(tmp_path, config, schema, "0.25")
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert any(schema in w and "does not open" in w for w in projection.warnings)
    with pytest.raises(Exception, match="does not open"):
        service.discover(_record(tmp_path, config))


def test_legacy_named_v3_directory_is_recorded_evidence(tmp_path):
    config = _config(tmp_path)
    layout = RunLayout(out=tmp_path, run_id="native", artifact_naming_mode="legacy")
    item = final_peak_item_for_config(config)
    item["terminal_annotation_schema"] = V3
    layout.AnnotatedFinalOutputSub(item)
    # Older legacy-named runs predate the manifest: the directory is the record.
    layout.ArtifactNameManifest.unlink(missing_ok=True)
    config["artifact_naming_mode"] = "legacy"
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert not projection.warnings
    assert _annotation_schema(projection) == V3


def test_annotation_outputs_without_a_record_are_refused(tmp_path):
    config = _config(tmp_path)
    layout = RunLayout(out=tmp_path, run_id="native", artifact_naming_mode="short")
    item = final_peak_item_for_config(config)
    item["terminal_annotation_schema"] = V3
    written = layout.AnnotatedFinalOutputSub(item)
    layout.ArtifactNameManifest.unlink()
    config["artifact_naming_mode"] = "short"
    assert written.is_dir()
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert any("lacks artifact_name_manifest" in w for w in projection.warnings)
    with pytest.raises(Exception, match="lacks artifact_name_manifest"):
        service.discover(_record(tmp_path, config))


def _annotation_scoped(projection):
    return [s.scope_id for s in projection.scopes if any(n.scope == "annotation" for n in s.artifact_names)]


@pytest.mark.parametrize("schemas", [(V3, V4), ("terminal-annotation-v2",), ("terminal-annotation-v9",)])
def test_refused_schema_omits_annotation_scopes_for_every_reader(tmp_path, schemas):
    from scpolya_ui import igv_manifest
    from scpolya_ui.services import site_score_service

    config = _config(tmp_path)
    for schema in schemas:
        _write_run_outputs(tmp_path, config, schema, "0.25")
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert projection.warnings and not _annotation_scoped(projection)
    assert "final.filtered_peaks" in projection.by_scope_id
    if schemas != ("terminal-annotation-v2",):
        # v2 alone is located read-only by the site-score page; see
        # tests/test_ui_site_score_recorded_schema.py.
        assert site_score_service._annotated_bed_path(config) is None
        assert site_score_service.deepcps_result_root(config) is None
    discovered = igv_manifest._projected_stage_tracks(config, str)
    assert discovered.annotation_schema_refused
    assert any("Annotation-scoped outputs omitted" in m for m in discovered.missing_tracks)
    assert not any(t.role == "annotated_peak" for t in discovered.tracks)


def test_v2_run_keeps_peak_level_readiness(tmp_path):
    from scpolya_ui.view_models.artifact_vm import final_primary_file_readiness

    config = _config(tmp_path)
    _write_run_outputs(tmp_path, config, "terminal-annotation-v2", "0.25")
    readiness = final_primary_file_readiness(dict(config))
    assert readiness.available and not readiness.error
    roles = {a.role for a in readiness.artifacts}
    assert "final.filtered_peaks" in roles and "annotation_schema_not_opened" in roles
    assert not any(r.startswith("final.annotated") for r in roles)


def test_records_for_other_peak_payloads_or_sources_are_not_evidence(tmp_path):
    config = _config(tmp_path)
    other_threshold = dict(config, misprimed_ratio_threshold=0.5)
    _write_run_outputs(tmp_path, other_threshold, V3, "0.25")
    other_source = dict(config, annotate_peak_source="merged" if config.get("annotate_peak_source") != "merged" else "resolved")
    _write_run_outputs(tmp_path, other_source, "terminal-annotation-v9", "0.25")
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert not projection.warnings
    assert _annotation_schema(projection) == V4


def test_symlinked_legacy_directory_is_not_evidence(tmp_path):
    config = _config(tmp_path)
    layout = RunLayout(out=tmp_path, run_id="native", artifact_naming_mode="legacy")
    item = final_peak_item_for_config(config)
    outside = tmp_path / "outside" / f"annotate-src-{item['annotate_peak_source']}.{V3}"
    outside.mkdir(parents=True)
    legacy_root = layout.FinalOutput / layout.PeakNameRecord(item).legacy_name
    legacy_root.parent.mkdir(parents=True, exist_ok=True)
    legacy_root.symlink_to(outside.parent, target_is_directory=True)
    layout.ArtifactNameManifest.unlink(missing_ok=True)
    config["artifact_naming_mode"] = "legacy"
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert not projection.warnings
    assert _annotation_schema(projection) == V4


def _manifest_bytes(tmp_path):
    return RunLayout(out=tmp_path, run_id="native").ArtifactNameManifest.read_bytes()


@pytest.mark.parametrize("schemas, expected", [
    ((), ""), ((V4,), ""), ((V3,), "older"), ((V3, V4), "refused"), (("terminal-annotation-v9",), "refused"),
])
def test_pre_run_notice_names_older_schemas_and_new_root_policy(tmp_path, schemas, expected):
    from task_manage.output_projection import terminal_annotation_root_notice

    config = _config(tmp_path)
    for schema in schemas:
        _write_run_outputs(tmp_path, config, schema, "0.25")
    layout = RunLayout(out=tmp_path, run_id="native")
    before = _manifest_bytes(tmp_path) if schemas else None
    notice = terminal_annotation_root_notice(layout, dict(config))
    if not expected:
        assert notice == ""
    else:
        assert all(schema in notice for schema in schemas)
        assert "new run root" in notice and "--output-dir or --run-id" in notice
        assert notice.count("never rewritten, copied, migrated or deleted") == 1
        assert ("mixed-schema" in notice) is (expected == "older")
        assert ("already refuse" in notice) is (expected == "refused")
        # Legacy mode does not use terminal annotation outputs.
        assert terminal_annotation_root_notice(layout, dict(config, quantification_mode="legacy")) == ""
    if schemas:
        assert _manifest_bytes(tmp_path) == before


def test_pre_run_notice_ignores_records_the_readers_ignore(tmp_path):
    from task_manage.output_projection import terminal_annotation_root_notice

    config = _config(tmp_path)
    layout = RunLayout(out=tmp_path, run_id="native")
    # A legacy-quantification run and a v3 run of another peak configuration
    # in the same root: readers of this config see neither.
    legacy = dict(config, quantification_mode="legacy")
    RunLayout(out=tmp_path, run_id="native", artifact_naming_mode="short").AnnotatedFinalOutputSub(
        final_peak_item_for_config(legacy))
    _write_run_outputs(tmp_path, dict(config, misprimed_ratio_threshold=0.5), V3, "0.25")
    assert not project_run_outputs_v1(dict(config), annotation_schema="recorded").warnings
    assert terminal_annotation_root_notice(layout, dict(config)) == ""


def test_pre_run_notice_reports_an_unreadable_manifest(tmp_path):
    from task_manage.output_projection import terminal_annotation_root_notice

    config = _config(tmp_path)
    _write_run_outputs(tmp_path, config, V3, "0.25")
    layout = RunLayout(out=tmp_path, run_id="native")
    layout.ArtifactNameManifest.write_text("{not json")
    notice = terminal_annotation_root_notice(layout, dict(config))
    assert "could not be read" in notice and "new run root" in notice
    assert layout.ArtifactNameManifest.read_text() == "{not json"


def test_mixed_refusal_names_schemas_and_new_root_without_fallback(tmp_path):
    config = _config(tmp_path)
    for schema in (V3, V4):
        _write_run_outputs(tmp_path, config, schema, "0.25")
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    (warning,) = [w for w in projection.warnings if "several schemas" in w]
    assert f"({V3}, {V4})" in warning and "no schema was assumed" in warning
    assert "new run root" in warning and "--output-dir or --run-id" in warning
    assert "matrix_summary" not in projection.by_scope_id
    # The default projection still describes what current code would write.
    assert _annotation_schema(project_run_outputs_v1(dict(config))) == V4


def test_main_warns_before_any_work_and_leaves_the_manifest(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import main as pipeline_main

    config = _config(tmp_path)
    _write_run_outputs(tmp_path, config, V3, "0.25")
    before = _manifest_bytes(tmp_path)
    events = []
    for guard in ("require_safe_state_paths", "require_current_task_completion_state_v1",
                  "require_compatible_run_root"):
        monkeypatch.setattr(pipeline_main, guard, lambda _state: None)

    class Logger:
        def info(self, message): pass
        def warning(self, message): events.append(("warning", message))

    real_layout = pipeline_main.RunLayout

    class Layout(real_layout):
        def ensure_base_dirs(self):
            events.append(("ensure_base_dirs", None))
            return super().ensure_base_dirs()

    monkeypatch.setattr(pipeline_main, "RunLayout", Layout)
    real_notice = pipeline_main.terminal_annotation_root_notice

    def notice(layout, config):
        events.append(("notice", None))
        return real_notice(layout, config)

    monkeypatch.setattr(pipeline_main, "terminal_annotation_root_notice", notice)

    class Manager:
        inline_execution_invocation_id = "inv"
        def __init__(self, **_kw): events.append(("manager", None))
        def load_or_init(self, _plan): pass

    monkeypatch.setattr(pipeline_main, "TaskManager", Manager)
    monkeypatch.setattr(pipeline_main, "setup_run_logging",
                        lambda **_kw: SimpleNamespace(main_logger=Logger()))
    monkeypatch.setattr(pipeline_main, "setup_module_logger", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_main, "Resources", lambda **_kw: SimpleNamespace(set=lambda *a: None))
    args = SimpleNamespace(**dict(config, verbose=False, repair_moved_run=False,
                                  repair_moved_run_dry_run=False))
    pipeline_main.run_mode_initialization(args)
    warnings = [message for kind, message in events if kind == "warning"]
    assert len(warnings) == 1 and V3 in warnings[0] and "new run root" in warnings[0]
    order = [kind for kind, _ in events]
    assert order.index("notice") < order.index("ensure_base_dirs") < order.index("manager")
    assert _manifest_bytes(tmp_path) == before


@pytest.mark.parametrize("schema, fallback_used", [(None, True), ("terminal-annotation-v2", False), ("mixed", False)])
def test_igv_expected_artifact_fallback_is_skipped_while_schema_is_refused(tmp_path, schema, fallback_used):
    from scpolya_ui.igv_manifest import build_single_run_igv_manifest

    config = _config(tmp_path)
    for written in ((V3, V4) if schema == "mixed" else () if schema is None else (schema,)):
        _write_run_outputs(tmp_path, config, written, "0.25")
    stale = tmp_path / "elsewhere" / "S.peaks-annotated.bed"
    stale.parent.mkdir()
    stale.write_text("chr1\t10\t20\tp\t0\t+\n")
    manifest = build_single_run_igv_manifest(
        record_id="R", label="run", gene="chr1:1-1000", base_config=dict(config),
        workspace_root=str(tmp_path / "native"), expected_artifacts={"annotated_bed": str(stale)},
        url_for_file=str,
    )
    used = any(t.role == "annotated_peak" and Path(t.path) == stale for t in manifest.tracks)
    assert used is fallback_used
    if not fallback_used:
        assert any("Annotation-scoped outputs omitted" in m for m in manifest.missing_tracks)
        assert any("no annotated BED" in m for m in manifest.missing_tracks)


def test_run_config_preview_carries_the_pre_run_notice(tmp_path):
    from task_manage import Plan, TaskManager
    from task_manage.run_config_preview import preview_rerun_from_state

    config = _config(tmp_path)
    layout = RunLayout(out=tmp_path, run_id="native")
    TaskManager(state_dir=layout.State, config=dict(config)).load_or_init(Plan(samples=[], contigs_by_sample={}))
    assert preview_rerun_from_state(layout.State, dict(config)).annotation_schema_notice == ""
    _write_run_outputs(tmp_path, config, V3, "0.25")
    before = _manifest_bytes(tmp_path)
    payload = preview_rerun_from_state(layout.State, dict(config)).as_dict()
    assert V3 in payload["annotation_schema_notice"] and "new run root" in payload["annotation_schema_notice"]
    assert _manifest_bytes(tmp_path) == before


def _legacy_peak_root(tmp_path, config):
    layout = RunLayout(out=tmp_path, run_id="native", artifact_naming_mode="legacy")
    item = final_peak_item_for_config(config)
    root = layout.FinalOutput / layout.PeakNameRecord(item).legacy_name
    root.mkdir(parents=True, exist_ok=True)
    layout.ArtifactNameManifest.unlink(missing_ok=True)
    return layout, item, root


def test_pre_schema_legacy_annotation_directory_is_refused_as_unrecorded(tmp_path):
    from task_manage.output_projection import terminal_annotation_root_notice

    config = _config(tmp_path)
    layout, item, root = _legacy_peak_root(tmp_path, config)
    (root / f"annotate-src-{item['annotate_peak_source']}").mkdir()
    config["artifact_naming_mode"] = "legacy"
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert any("unrecorded" in w and "does not open" in w for w in projection.warnings)
    assert not _annotation_scoped(projection)
    assert "unrecorded" in terminal_annotation_root_notice(layout, dict(config))


def test_symlinked_legacy_schema_child_and_oversized_directory(tmp_path, monkeypatch):
    config = _config(tmp_path)
    layout, item, root = _legacy_peak_root(tmp_path, config)
    config["artifact_naming_mode"] = "legacy"
    target = tmp_path / "outside-child"
    target.mkdir()
    (root / f"annotate-src-{item['annotate_peak_source']}.{V3}").symlink_to(target, target_is_directory=True)
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert not projection.warnings and _annotation_schema(projection) == V4
    from task_manage import output_projection
    monkeypatch.setattr(output_projection, "_LEGACY_ANNOTATION_DIR_LIMIT", 1)
    (root / "other").mkdir()
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert any("too many entries" in w for w in projection.warnings)


def test_unreadable_legacy_directory_is_a_warning_not_a_crash(tmp_path, monkeypatch):
    config = _config(tmp_path)
    layout, item, root = _legacy_peak_root(tmp_path, config)
    config["artifact_naming_mode"] = "legacy"
    real_iterdir = Path.iterdir

    def iterdir(self):
        if self == root:
            raise PermissionError("denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert any("could not be read" in w and "PermissionError" in w for w in projection.warnings)
    assert not _annotation_scoped(projection)


def test_igv_stage_audit_keeps_peak_tracks_but_not_annotation_tracks_when_refused(tmp_path):
    from scpolya_ui.igv_manifest import build_single_run_igv_manifest

    config = _config(tmp_path)
    for schema in (V3, V4):
        _write_run_outputs(tmp_path, config, schema, "0.25")
    root = tmp_path / "native"
    called = root / "cohort_output/cohort.directional/cohort.directional.plus.coverage.removeCDS.peaks-merged-10.bed"
    annotated = root / "final_output/x/S.___peaks-annotated.bed"
    for path in (called, annotated):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("chr1\t10\t20\tp\t0\t+\n")
    manifest = build_single_run_igv_manifest(
        record_id="R", label="run", gene="chr1:1-1000", base_config=dict(config),
        workspace_root=str(root), expected_artifacts={}, url_for_file=str,
    )
    paths = {Path(t.path): t.role for t in manifest.tracks}
    assert paths.get(called) == "raw_peak_scrna"
    assert annotated not in paths


def test_preview_notice_follows_the_proposed_run_root(tmp_path):
    from task_manage.run_config_preview import _layout_for_proposal

    config = _config(tmp_path)
    state = tmp_path / "native" / "state"
    assert _layout_for_proposal(state, dict(config, run_id="fresh")).Root == tmp_path / "fresh"
    assert _layout_for_proposal(state, {}).Root == tmp_path / "native"
    assert _layout_for_proposal(tmp_path / "elsewhere" / "db.sqlite", {}) is None


def test_igv_stage_audit_annotation_tracks_remain_for_unrefused_roots(tmp_path):
    from scpolya_ui.igv_manifest import build_single_run_igv_manifest

    root = tmp_path / "native"
    annotated = root / "final_output/x/S.___peaks-annotated.bed"
    annotated.parent.mkdir(parents=True)
    annotated.write_text("chr1\t10\t20\tp\t0\t+\n")
    manifest = build_single_run_igv_manifest(
        record_id="R", label="run", gene="chr1:1-1000", base_config={},
        workspace_root=str(root), expected_artifacts={}, url_for_file=str,
    )
    assert any(Path(t.path) == annotated and t.role == "annotated_peak" for t in manifest.tracks)


def test_legacy_mode_root_with_bare_annotation_directory_gets_no_notice(tmp_path):
    from task_manage.output_projection import terminal_annotation_root_notice

    config = dict(_config(tmp_path), quantification_mode="legacy", artifact_naming_mode="legacy")
    layout, item, root = _legacy_peak_root(tmp_path, config)
    (root / f"annotate-src-{item['annotate_peak_source']}").mkdir()
    assert not any(w.startswith("Annotation-scoped") for w in
                   project_run_outputs_v1(dict(config), annotation_schema="recorded").warnings)
    assert terminal_annotation_root_notice(layout, dict(config)) == ""
