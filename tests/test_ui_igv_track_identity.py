from pathlib import Path

from scpolya_ui.config_schema import CONFIG_FIELDS
from scpolya_ui.igv_manifest import _projected_cohort_tracks


def _config(**overrides):
    config = {field.key: field.default for field in CONFIG_FIELDS}
    config.update(
        dedup_method="directional",
        minpolyA=5,
        nonA_allowed=1,
        minMAPQ=10,
        coverage_scaling="expr",
        exp_peak_threshold_mode="dynamic",
        exp_dynamic_threshold_stat="max_norm_fixed",
        exp_dynamic_threshold_normalized=0.05,
        polyA_peak_threshold_mode="fixed",
        unscaled_depth_threshold_polyA=3.0,
        merge_distance=10,
        merge_distance_polyA=10,
    )
    config.update(overrides)
    return config


def _write(root, directory, name):
    path = root / directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("chr1\t10\t20\n")
    return path


def test_peak_tracks_follow_selected_threshold_and_polya_identity(tmp_path):
    # Literal producer filenames, including the lexical-first wrong setting
    # observed in the real PBMC depth3/depth1 run directory.
    correct = _write(tmp_path, "cohort.directional.polyA-5-1-10",
                     "cohort.directional.polyA-5-1-10.plus.coverage.peaks-merged-3.0-10.bed")
    _write(tmp_path, "cohort.directional.polyA-5-1-10",
           "cohort.directional.polyA-5-1-10.plus.coverage.peaks-merged-1.0-10.bed")
    _write(tmp_path, "cohort.directional.polyA-10-1-10",
           "cohort.directional.polyA-10-1-10.plus.coverage.peaks-merged-3.0-10.bed")
    expression = _write(tmp_path, "cohort.directional",
                        "cohort.directional.minus.coverage.scaled.removeCDS.peaks-merged-dyn-max_norm_fixed-n0.05-10.bed")
    tracks = _projected_cohort_tracks(str(tmp_path / "**" / "*peaks-merged*.bed"),
                                     config=_config(), kind="peaks", url_for_file=str)
    assert {Path(track.path) for track in tracks} == {correct, expression}
    changed = _projected_cohort_tracks(str(tmp_path / "**" / "*peaks-merged*.bed"),
                                      config=_config(unscaled_depth_threshold_polyA=1.0),
                                      kind="peaks", url_for_file=str)
    assert {Path(track.path).name for track in changed if track.role == "raw_peak_polya"} == {
        "cohort.directional.polyA-5-1-10.plus.coverage.peaks-merged-1.0-10.bed"
    }


def test_missing_requested_peak_variant_does_not_substitute_another(tmp_path):
    _write(tmp_path, "cohort.directional.polyA-5-1-10",
           "cohort.directional.polyA-5-1-10.plus.coverage.peaks-merged-1.0-10.bed")
    tracks = _projected_cohort_tracks(str(tmp_path / "**" / "*.bed"),
                                     config=_config(), kind="peaks", url_for_file=str)
    assert tracks == ()


def test_coverage_tracks_follow_selected_polya_and_scaling(tmp_path):
    correct = _write(tmp_path, "cohort.directional.polyA-5-1-10",
                     "cohort.directional.polyA-5-1-10.minus.coverage.scaled.bw")
    _write(tmp_path, "cohort.directional.polyA-5-1-10",
           "cohort.directional.polyA-5-1-10.minus.coverage.bw")
    _write(tmp_path, "cohort.directional.polyA-10-1-10",
           "cohort.directional.polyA-10-1-10.minus.coverage.scaled.bw")
    tracks = _projected_cohort_tracks(str(tmp_path / "**" / "*.bw"),
                                     config=_config(coverage_scaling="both", scaled_depth_threshold_polyA=2.5),
                                     kind="coverage", url_for_file=str)
    assert [Path(track.path) for track in tracks] == [correct]
