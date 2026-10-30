from __future__ import annotations

import pytest

from task_manage.artifact_health import (
    ArtifactHealthTaskRecordV2,
    _evaluate_expectation,
    _expected_artifacts_for_records,
)
from task_manage.run_layout import RunLayout
from task_manage.task_coordinate import build_task_coordinate_v1


def _config(tmp_path):
    return {
        "output_dir": str(tmp_path),
        "run_id": "run",
        "dedup_method": "umi_tools",
        "polyA_status": "polyA",
        "coverage_scaling": "none",
        "depth_threshold": "1",
        "depth_threshold_polyA": "2",
        "merge_distance": 25,
        "merge_distance_polyA": 25,
        "misprime_A_count": 8,
        "misprime_in": 10,
        "misprimed_ratio_threshold": 0.2,
        "annotate_peak_source": "resolved",
        "quantification_mode": "legacy",
        "intronic_misprimed_ratio_threshold": 0.5,
        "psi_min_gene_umis": 1,
        "minpolyA": 6,
        "nonA_allowed": 1,
        "minMAPQ": 10,
        "exp_peak_threshold_mode": "fixed",
        "polyA_peak_threshold_mode": "fixed",
        "scaled_depth_threshold": 1,
        "unscaled_depth_threshold": 1,
        "scaled_depth_threshold_polyA": 2,
        "unscaled_depth_threshold_polyA": 2,
        "exp_dynamic_threshold_stat": "median",
        "polyA_dynamic_threshold_stat": "median",
        "exp_dynamic_threshold_alpha": 1.0,
        "polyA_dynamic_threshold_alpha": 1.0,
        "exp_dynamic_threshold_floor": 0.0,
        "polyA_dynamic_threshold_floor": 0.0,
        "exp_dynamic_threshold_normalized": 0.3,
        "polyA_dynamic_threshold_normalized": 0.3,
    }


def _record(extra_key):
    coordinate = build_task_coordinate_v1(
        stage="summarize_matrix",
        task_base="summarize_matrix",
        identity={"sid": "sample", "dedup_method": "umi_tools"},
        params={"extra_key": extra_key},
    )
    return ArtifactHealthTaskRecordV2(
        task_coordinate=coordinate,
        done_at="2026-09-24T00:00:00Z",
    )


def test_mixed_done_coordinates_use_only_their_own_manifest_namespace(tmp_path):
    config = _config(tmp_path)
    layout = RunLayout(
        out=tmp_path,
        run_id="run",
        artifact_naming_mode="short",
    )
    legacy_item = dict(config)
    terminal_low = dict(config, quantification_mode="terminal-aware", intronic_misprimed_ratio_threshold=0.4)
    terminal_high = dict(config, quantification_mode="terminal-aware", intronic_misprimed_ratio_threshold=0.7)

    variants = (legacy_item, terminal_low, terminal_high)
    signatures = []
    for item in variants:
        layout.MatrixSummaryConfigSub(item, psi_min_gene_umis=1)
        signatures.append(layout.MatrixSignatureFromDict(item, psi_min_gene_umis=1))

    expectation_sets = []
    for signature in signatures:
        expectations = tuple(
            _expected_artifacts_for_records(
                (_record(signature),), config, layout.Root
            )
        )
        assert expectations
        assert all(not expectation.unresolved_detail for expectation in expectations)
        expectation_sets.append(expectations)

    path_sets = [
        {path for expectation in expectations for path in expectation.paths}
        for expectations in expectation_sets
    ]
    assert path_sets[0].isdisjoint(path_sets[1])
    assert path_sets[0].isdisjoint(path_sets[2])
    assert path_sets[1].isdisjoint(path_sets[2])
    from utils.terminal_quant_utils import TERMINAL_DISTAL_USAGE_SUFFIX

    assert not any(path.endswith(TERMINAL_DISTAL_USAGE_SUFFIX) for path in path_sets[0])
    assert any(path.endswith(TERMINAL_DISTAL_USAGE_SUFFIX) for path in path_sets[1])
    from utils.terminal_quant_utils import TERMINAL_FEATURE_OVERVIEW_SUFFIX

    assert not any(path.endswith(TERMINAL_FEATURE_OVERVIEW_SUFFIX) for path in path_sets[0])
    assert any(path.endswith(TERMINAL_FEATURE_OVERVIEW_SUFFIX) for path in path_sets[1])

    # Files from the 0.4 terminal summary cannot satisfy the 0.7 DONE coordinate.
    for expectation in expectation_sets[1]:
        path = expectation.paths[0]
        from pathlib import Path

        artifact = Path(path)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("ready\n")
    assert all(
        _evaluate_expectation(expectation).status == "ready"
        for expectation in expectation_sets[1]
    )
    assert all(
        _evaluate_expectation(expectation).status == "missing"
        for expectation in expectation_sets[2]
    )


def _count_matrix_expectations(tmp_path, config, epoch="5"):
    from task_manage.parameter_studies import final_peak_item_for_config

    layout = RunLayout(out=tmp_path, run_id="run", artifact_naming_mode="short")
    item = final_peak_item_for_config(config)
    layout.CountMatrixConfigSub(item)
    coordinate = build_task_coordinate_v1(
        stage="count_matrix",
        task_base="summarize_count_matrix",
        identity={"sid": "sample", "dedup_method": "umi_tools"},
        params={
            "extra_key": layout.AnnotatedPeakSignatureFromDict(item),
            **({} if epoch is None else {"pipeline_cache_epoch": epoch}),
        },
    )
    record = ArtifactHealthTaskRecordV2(task_coordinate=coordinate, done_at="2026-10-29T00:00:00Z")
    return tuple(_expected_artifacts_for_records((record,), config, layout.Root))


def test_summarized_counts_require_the_sample_assignment_report(tmp_path):
    # Sol review item 4: deleting only the diagnostics invalidates reuse.
    from pathlib import Path

    expectations = _count_matrix_expectations(tmp_path, _config(tmp_path))
    assert all(not e.unresolved_detail for e in expectations)
    by_label = {e.label: e for e in expectations}
    assert set(by_label) == {
        "sample peak count matrix",
        "sample per-PAS read-assignment table",
        "sample read-assignment summary",
    }
    assert by_label["sample per-PAS read-assignment table"].paths[0].endswith(".peak_assignment.tsv.gz")
    assert by_label["sample read-assignment summary"].paths[0].endswith(".peak_assignment_summary.json")
    for expectation in expectations:
        artifact = Path(expectation.paths[0])
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("ready\n")
    assert {_evaluate_expectation(e).status for e in expectations} == {"ready"}
    Path(by_label["sample read-assignment summary"].paths[0]).unlink()
    status = {e.label: _evaluate_expectation(e).status for e in expectations}
    assert status["sample read-assignment summary"] == "missing"
    assert status["sample peak count matrix"] == "ready"


def test_peak_sidecar_requires_its_unit_diagnostics(tmp_path):
    from pathlib import Path

    from task_manage.artifact_health import ArtifactExpectation, _unit_assignment_diagnostics_expectations

    sidecar = ArtifactExpectation(
        "annotate_bams", "annotate_slices", "s.chr1 peak-assignment sidecar",
        (str(tmp_path / "a" / "s.chr1.xr_to_peak.tsv.gz"), str(tmp_path / "b" / "s.chr1.xr_to_peak.tsv.gz")),
        mode="one",
    )
    expectations = tuple(_unit_assignment_diagnostics_expectations(sidecar))
    assert [Path(p).name for e in expectations for p in e.paths] == [
        "s.chr1.xr_to_peak.assignment.tsv.gz", "s.chr1.xr_to_peak.assignment.tsv.gz",
        "s.chr1.xr_to_peak.assignment_summary.json", "s.chr1.xr_to_peak.assignment_summary.json",
    ]
    assert all(e.mode == "one" and e.key == sidecar.key for e in expectations)
    assert {_evaluate_expectation(e).status for e in expectations} == {"missing"}
    unresolved = ArtifactExpectation("annotate_bams", "annotate_slices", "x", (), unresolved_detail="no row")
    assert tuple(_unit_assignment_diagnostics_expectations(unresolved)) == ()


@pytest.mark.parametrize("epoch", [None, "3", "4"])
def test_historical_done_records_do_not_require_assignment_diagnostics(tmp_path, epoch):
    # Sol r2 H1: diagnostics arrived with epoch 5; the record's own stored
    # epoch decides, so historical DONE records keep their original checks.
    from pathlib import Path

    historical = _count_matrix_expectations(tmp_path, _config(tmp_path), epoch=epoch)
    assert [e.label for e in historical] == ["sample peak count matrix"]
    Path(historical[0].paths[0]).parent.mkdir(parents=True, exist_ok=True)
    Path(historical[0].paths[0]).write_text("ready\n")
    assert _evaluate_expectation(historical[0]).status == "ready"
    current = _count_matrix_expectations(tmp_path, _config(tmp_path), epoch="5")
    assert {e.label: _evaluate_expectation(e).status for e in current} == {
        "sample peak count matrix": "ready",
        "sample per-PAS read-assignment table": "missing",
        "sample read-assignment summary": "missing",
    }


@pytest.mark.parametrize("epoch, required", [(None, False), ("3", False), ("4", False), ("5", True)])
def test_unit_diagnostics_gate_uses_the_stored_record_epoch(epoch, required):
    from task_manage.artifact_health import _record_has_assignment_diagnostics

    coordinate = build_task_coordinate_v1(
        stage="annotate_bams", task_base="annotate_slices",
        identity={"sid": "s", "ctg": "chr1"},
        params={"extra_key": "k", **({} if epoch is None else {"pipeline_cache_epoch": epoch})},
    )
    record = ArtifactHealthTaskRecordV2(task_coordinate=coordinate, done_at="2026-10-29T00:00:00Z")
    assert _record_has_assignment_diagnostics(record) is required
