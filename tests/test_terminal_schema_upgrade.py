from __future__ import annotations

from pathlib import Path

import pytest

from quantification_config import (
    TERMINAL_ANNOTATION_COORDINATE_MODE,
    TERMINAL_ANNOTATION_SCHEMA_IDENTITY,
)
from task_manage import Plan, TaskManager, run_flex_batch
from task_manage.artifact_health import (
    ArtifactHealthTaskRecordV2,
    _evaluate_expectation,
    _expected_artifacts_for_records,
)
from task_manage.cache_epoch import PIPELINE_CACHE_EPOCH, PIPELINE_CACHE_EPOCH_KEY
from task_manage.task_coordinate import build_task_coordinate_v1
from task_manage.task_coordinate_store import write_task_coordinate_done_v2
from task_manage.task_instance_v3 import build_task_instance_v3_by_key
from task_manage.task_specs import task_specs_by_key
from task_manage.run_layout import RunLayout
from quantification_config import summary_quantification_signature


# v3 -> v4: gene-assigned PASs outside the CDS canonical model are no longer
# labelled Intergenic, so v3 annotation products must not be reused as v4.
PREVIOUS_TERMINAL_ANNOTATION_SCHEMA_IDENTITIES = (
    "terminal-annotation-v2",
    "terminal-annotation-v3",
)


def _peak_item(mode: str) -> dict[str, object]:
    item: dict[str, object] = {
        "dedup_method": "directional",
        "polyA_status": "polyA-5-0-10",
        "coverage_scaling": "none",
        "depth_threshold": "1",
        "depth_threshold_polyA": "2",
        "merge_distance": 0,
        "merge_distance_polyA": 0,
        "misprime_A_count": 8,
        "misprime_in": 10,
        "misprimed_ratio_threshold": 1.0,
        "annotate_peak_source": "resolved",
        "quantification_mode": mode,
        "intronic_misprimed_ratio_threshold": 0.5,
    }
    if mode == "terminal-aware":
        item["terminal_annotation_schema"] = TERMINAL_ANNOTATION_SCHEMA_IDENTITY
    return item


def _pre_r3_coordinate(stage: str, task_base: str, item: dict[str, object]):
    spec = task_specs_by_key()[(stage, task_base)]
    return build_task_coordinate_v1(
        stage=stage,
        task_base=task_base,
        identity={key: item.get(key) for key in spec.identity_keys},
        params={key: item.get(key) for key in spec.param_keys},
    )


def _annotation_coordinate_with_mode(
    stage: str,
    task_base: str,
    item: dict[str, object],
    coordinate_mode: str,
):
    spec = task_specs_by_key()[(stage, task_base)]
    params = {key: item.get(key) for key in spec.param_keys}
    params["quantification_mode"] = coordinate_mode
    # Same epoch as current work, so only the schema can differ.
    params[PIPELINE_CACHE_EPOCH_KEY] = PIPELINE_CACHE_EPOCH
    return build_task_coordinate_v1(
        stage=stage,
        task_base=task_base,
        identity={key: item.get(key) for key in spec.identity_keys},
        params=params,
    )


def _write_historical_completion(tm, coordinate):
    with tm.store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        write_task_coordinate_done_v2(
            con,
            coordinate,
            done_at="2026-10-14T00:00:00+00:00",
            meta_json=None,
            source="migration",
        )
        con.execute("COMMIT")


@pytest.mark.parametrize("previous_schema", PREVIOUS_TERMINAL_ANNOTATION_SCHEMA_IDENTITIES)
def test_current_terminal_schema_invalidates_previous_done_and_artifacts(
    tmp_path, previous_schema
):
    PREVIOUS_TERMINAL_ANNOTATION_COORDINATE_MODE = f"terminal-aware@{previous_schema}"
    assert previous_schema != TERMINAL_ANNOTATION_SCHEMA_IDENTITY
    current = _peak_item("terminal-aware")
    old = dict(current)
    old["terminal_annotation_schema"] = previous_schema
    layout = RunLayout(out=tmp_path, run_id="upgrade", artifact_naming_mode="short")

    # Peak calling (and everything upstream of it) stays reusable.
    assert layout.PeakNameRecord(old).code == layout.PeakNameRecord(current).code
    for task_base in ("combine_and_intersect_peaks", "merge_call_peaks"):
        assert (
            build_task_instance_v3_by_key("call_peaks", task_base, old).task_coordinate_id
            == build_task_instance_v3_by_key("call_peaks", task_base, current).task_coordinate_id
        )
    old_annotation_record = layout.AnnotationNameRecord(old)
    assert old_annotation_record.code != layout.AnnotationNameRecord(current).code
    old_annotation = old_annotation_record.code
    current_annotation = layout.AnnotatedPeakSignatureFromDict(current)
    old_summary = (
        f"{old_annotation}.psi-min-gene-umis-1"
        f"{summary_quantification_signature(old)}"
    )
    current_summary = layout.MatrixSignatureFromDict(current, psi_min_gene_umis=1)
    assert old_annotation != current_annotation
    assert old_summary != current_summary

    annotate_tasks = (
        "assign_peaks_to_genes",
        "merge_assigned_peaks",
        "resolve_peak_issues",
        "convert_peaks_bed_to_bigbed",
        "convert_peaks_bed_to_gtf",
    )
    for task_base in annotate_tasks:
        old_coordinate = _annotation_coordinate_with_mode(
            "annotate_peaks",
            task_base,
            old,
            PREVIOUS_TERMINAL_ANNOTATION_COORDINATE_MODE,
        )
        current_instance = build_task_instance_v3_by_key(
            "annotate_peaks", task_base, current
        )
        # Control: the helper reproduces the current coordinate exactly.
        assert _annotation_coordinate_with_mode(
            "annotate_peaks", task_base, current, TERMINAL_ANNOTATION_COORDINATE_MODE
        ).task_coordinate_id == current_instance.task_coordinate_id
        assert old_coordinate.task_coordinate_id != current_instance.task_coordinate_id
        assert (
            current_instance.task_coordinate.params_dict["quantification_mode"]
            == TERMINAL_ANNOTATION_COORDINATE_MODE
        )

    downstream = (
        ("annotate_bams", "annotate_slices", {"sid": "S", "ctg": "chr1"}),
        (
            "count_matrix",
            "count_unit_matrix",
            {"sid": "S", "ctg": "chr1", "dedup_method": "directional"},
        ),
        (
            "count_matrix",
            "summarize_count_matrix",
            {"sid": "S", "dedup_method": "directional"},
        ),
    )
    for stage, task_base, identity in downstream:
        old_coordinate = build_task_coordinate_v1(
            stage=stage,
            task_base=task_base,
            identity=identity,
            params={"extra_key": old_annotation},
        )
        current_coordinate = build_task_coordinate_v1(
            stage=stage,
            task_base=task_base,
            identity=identity,
            params={"extra_key": current_annotation},
        )
        assert old_coordinate.task_coordinate_id != current_coordinate.task_coordinate_id
    old_summary_coordinate = build_task_coordinate_v1(
        stage="summarize_matrix",
        task_base="summarize_matrix",
        identity={"sid": "S", "dedup_method": "directional"},
        params={"extra_key": old_summary},
    )
    current_summary_coordinate = build_task_coordinate_v1(
        stage="summarize_matrix",
        task_base="summarize_matrix",
        identity={"sid": "S", "dedup_method": "directional"},
        params={"extra_key": current_summary},
    )
    assert old_summary_coordinate.task_coordinate_id != current_summary_coordinate.task_coordinate_id

    tm = TaskManager(state_dir=layout.State, config={"run_id": "upgrade"})
    tm.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    layout.AnnotatedFinalOutputSub(current)
    peak_record = layout.PeakNameRecord(old)
    old_assigned = layout.AnnotatedFinalOutputFileCandidates(
        suffix="___peaks-assigned.terminal-aware.v2.bed",
        d=old,
    )[0]
    old_assigned.parent.mkdir(parents=True, exist_ok=True)
    old_assigned.write_text("\t".join(["."] * 20) + "\n")
    current_assigned = layout.AnnotatedFinalOutputFileCandidates(
        suffix="___peaks-assigned.terminal-aware.v2.bed",
        d=current,
    )[0]
    assert old_assigned != current_assigned
    assert not current_assigned.exists()

    old_done = _annotation_coordinate_with_mode(
        "annotate_peaks",
        "assign_peaks_to_genes",
        old,
        PREVIOUS_TERMINAL_ANNOTATION_COORDINATE_MODE,
    )
    health_entries = tuple(
        _evaluate_expectation(expectation)
        for expectation in _expected_artifacts_for_records(
            (ArtifactHealthTaskRecordV2(old_done, "2026-09-24T00:00:00Z"),),
            {
                **current,
                "output_dir": str(tmp_path),
                "run_id": "upgrade",
            },
            layout.Root,
        )
    )
    assert health_entries
    assert all(entry.status == "ready" for entry in health_entries)
    assert all(str(old_assigned) in entry.expectation.paths for entry in health_entries)
    assert all(str(old_assigned) in entry.ready_paths for entry in health_entries)

    _write_historical_completion(tm, old_done)
    ran: list[str] = []
    spec = task_specs_by_key()[("annotate_peaks", "assign_peaks_to_genes")]
    run_flex_batch(
        tm=tm,
        stage=spec.stage,
        task_base=spec.task_base,
        items=[current],
        identity_keys=list(spec.identity_keys),
        param_keys=list(spec.param_keys),
        fn=lambda _item: ran.append("reran"),
        max_workers=1,
    )
    assert ran == ["reran"]


def test_legacy_coordinate_and_artifact_names_are_pre_r3_exact():
    legacy = _peak_item("legacy")
    spec = task_specs_by_key()[("annotate_peaks", "assign_peaks_to_genes")]
    old_coordinate = _pre_r3_coordinate(
        "annotate_peaks", "assign_peaks_to_genes", legacy
    )
    current_coordinate = build_task_instance_v3_by_key(
        "annotate_peaks", "assign_peaks_to_genes", legacy
    ).task_coordinate
    # Legacy-mode coordinates keep every pre-R3 field exactly; the only addition
    # is the cache semantic epoch, which deliberately invalidates pre-epoch DONE.
    expected = build_task_coordinate_v1(
        stage="annotate_peaks",
        task_base="assign_peaks_to_genes",
        identity=old_coordinate.identity_dict,
        params={**old_coordinate.params_dict, PIPELINE_CACHE_EPOCH_KEY: PIPELINE_CACHE_EPOCH},
    )
    assert current_coordinate.canonical_json == expected.canonical_json
    assert current_coordinate.task_coordinate_id == expected.task_coordinate_id
    assert current_coordinate.task_coordinate_id != old_coordinate.task_coordinate_id
    assert current_coordinate.identity_dict == old_coordinate.identity_dict
    assert "terminal_annotation_schema" not in legacy
    assert set(spec.param_keys) == set(old_coordinate.params_dict)

    layout = RunLayout(out=Path("/tmp"), run_id="legacy", artifact_naming_mode="legacy")
    expected_peak = (
        "final.directional.polyA-5-0-10.cov-scl-none.dp1.dpA2."
        "md0.mdA0.misp-A8-in10-rt1.0"
    )
    assert layout.FinalNameFromDict(legacy) == expected_peak
    assert layout.AnnotationSubdirNameFromDict(legacy) == "annotate-src-resolved"
    assert layout.FinalFile("___peaks-annotated.bed", legacy) == (
        f"{expected_peak}.___peaks-annotated.bed"
    )
