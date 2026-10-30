from pathlib import Path
from types import SimpleNamespace

import pytest

from task_manage.artifact_health import (
    ArtifactExpectation, ArtifactHealthTaskRecordV2, _evaluate_expectation, _peak_expectations,
)
from task_manage.output_projection import project_run_outputs_v1, validate_output_projection_contract
from task_manage.run_layout import RunLayout
from task_manage.task_coordinate import build_task_coordinate_v1


@pytest.mark.parametrize("epoch,required", [(None, False), ("2", False), ("3", True), ("4", True), ("5", True)])
def test_audit_requirement_comes_from_done_coordinate_not_current_defaults(tmp_path, epoch, required):
    coordinate = build_task_coordinate_v1(
        stage="call_peaks", task_base="combine_and_intersect_peaks", identity={},
        params={} if epoch is None else {"pipeline_cache_epoch": epoch},
    )
    record = ArtifactHealthTaskRecordV2(task_coordinate=coordinate, done_at="2026-10-14T00:00:00Z")
    final = tmp_path / "final.bed"
    artifacts = SimpleNamespace(
        mispriming_filtered_bed=str(final), mispriming_filtered_bed_candidates=(),
        mispriming_filtered_merged_bed=str(tmp_path / "final-merged.bed"),
        mispriming_filtered_merged_bed_candidates=(),
    )
    expectations = list(_peak_expectations(record, artifacts,
        layout=RunLayout(tmp_path, "run"), final_item=None, quantification_mode="terminal-aware"))
    audits = [item for item in expectations if "not-evaluable" in item.label]
    assert len(audits) == int(required)
    if required:
        audit = audits[0]
        assert _evaluate_expectation(audit).status == "missing"
        path = Path(audit.paths[0])
        assert path.name == "final-not-evaluable.audit.bed"
        path.touch()
        assert _evaluate_expectation(audit).status == "ready"
        path.write_text("chr1\t10\t20\tp\t1\t+\t0\tNA\t1\n")
        assert _evaluate_expectation(audit).status == "ready"


def test_allow_empty_does_not_weaken_other_artifacts_or_duplicate_candidate_check(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.touch()
    strict = ArtifactExpectation("stage", "task", "ordinary", (str(first),))
    assert _evaluate_expectation(strict).status == "empty"
    second.touch()
    ambiguous = ArtifactExpectation("stage", "task", "audit", (str(first), str(second)),
                                    mode="one", allow_empty=True)
    assert _evaluate_expectation(ambiguous).status == "unresolved"


@pytest.mark.parametrize("mode", ["legacy", "terminal-aware"])
def test_projection_registers_audit_beside_resolved_peaks(tmp_path, mode):
    validate_output_projection_contract()
    config = {
        "output_dir": str(tmp_path), "run_id": "run", "quantification_mode": mode,
        "dedup_method": "directional", "minpolyA": 10, "nonA_allowed": 1,
        "minMAPQ": 0, "coverage_scaling": "none",
        "unscaled_depth_threshold": 1, "unscaled_depth_threshold_polyA": 1,
        "merge_distance": 0, "merge_distance_polyA": 0,
        "misprime_A_count": 8, "misprime_in": 10,
        "misprimed_ratio_threshold": 0.2, "intronic_misprimed_ratio_threshold": 0.5,
    }
    projection = project_run_outputs_v1(config)
    scopes = projection.by_scope_id
    final = Path(scopes["final.filtered_peaks"].path)
    audit = scopes["final.not_evaluable_peaks_audit"]
    assert audit.path == str(final.with_name(f"{final.stem}-not-evaluable.audit.bed"))
    assert audit.producer_task_keys == (("call_peaks", "combine_and_intersect_peaks"),)
