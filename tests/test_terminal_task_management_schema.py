from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from quantification_config import (
    TERMINAL_ANNOTATION_COORDINATE_MODE,
    TERMINAL_ANNOTATION_SCHEMA_IDENTITY,
    summary_quantification_signature,
)
from task_manage import Plan, TaskManager
from task_manage.artifact_health import (
    ArtifactHealthTaskRecordV2,
    _coordinate_artifact_config,
    check_artifact_health,
    check_task_artifact_health_v2,
    load_artifact_health_task_records_v2,
)
from task_manage.artifact_naming import (
    annotation_name_payload,
    build_scoped_artifact_name_v1,
)
from task_manage.dependency_graph import build_static_dependency_graph
from task_manage.instance_graph import (
    build_dynamic_instance_graph_from_decisions,
    build_observe_only_reuse_plan,
)
from task_manage.pipeline_contracts import (
    PipelineContract,
    pipeline_contracts_by_key,
)
from task_manage.reuse_report import CurrentReuseDecisionSummaryV2
from task_manage.run_layout import RunLayout
from task_manage.task_coordinate import build_task_coordinate_v1
from task_manage.task_instance_v3 import TaskInstanceV3, build_task_instance_v3_by_key


def _terminal_item() -> dict[str, object]:
    return {
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
        "quantification_mode": "terminal-aware",
        "terminal_annotation_schema": TERMINAL_ANNOTATION_SCHEMA_IDENTITY,
        "intronic_misprimed_ratio_threshold": 0.5,
        "psi_min_gene_umis": 1,
    }


def _decision(
    instance: TaskInstanceV3,
    decision_id: int,
    *,
    consumes: tuple[str, ...] | None = None,
    produces: tuple[str, ...] | None = None,
) -> CurrentReuseDecisionSummaryV2:
    return CurrentReuseDecisionSummaryV2(
        decision_id=decision_id,
        stage=instance.stage,
        task_base=instance.task_base,
        action="legacy_reuse",
        legacy_done=True,
        queued=False,
        force=False,
        identity_hash=instance.identity_hash,
        params_hash=instance.params_hash,
        instance_id=instance.instance_id,
        reasons=(),
        identity=tuple((field.key, field.value) for field in instance.identity),
        params=tuple((field.key, field.value) for field in instance.params),
        execution_mode="flex",
        worker_arg=None,
        worker_callable=None,
        consumes=instance.consumes if consumes is None else consumes,
        produces=instance.produces if produces is None else produces,
        worker_payload={},
        created_at=f"2026-09-24T00:00:{decision_id:02d}Z",
        task_coordinate_id=instance.task_coordinate_id,
        coordinate_status="available",
        reuse_policy_version=instance.reuse_policy_version,
        implementation_version=instance.implementation_version,
        declaration_source="instance_payload",
    )


def _historical_annotation_instance(instance: TaskInstanceV3) -> TaskInstanceV3:
    assert instance.stage == "annotate_peaks"
    return replace(
        instance,
        params=tuple(
            replace(field, value="terminal-aware", legacy_value="terminal-aware")
            if field.key == "quantification_mode"
            else field
            for field in instance.params
        ),
    )


def _pair_graph(
    producer: TaskInstanceV3,
    consumer: TaskInstanceV3,
    artifact_key: str,
):
    contracts = pipeline_contracts_by_key()
    producer_contract = replace(
        contracts[producer.key],
        consumes=(),
        produces=(artifact_key,),
    )
    consumer_contract = replace(
        contracts[consumer.key],
        consumes=(artifact_key,),
        produces=(),
    )
    static_graph = build_static_dependency_graph(
        PipelineContract(
            tasks=(producer_contract, consumer_contract),
            artifact_edges=(),
        )
    )
    return build_dynamic_instance_graph_from_decisions(
        (
            _decision(producer, 1, consumes=(), produces=(artifact_key,)),
            _decision(consumer, 2, consumes=(artifact_key,), produces=()),
        ),
        state_db_path="terminal-schema-production-fields.sqlite",
        static_graph=static_graph,
    )


def _assert_resolved_pair(
    producer: TaskInstanceV3,
    consumer: TaskInstanceV3,
    artifact_key: str,
) -> None:
    graph = _pair_graph(producer, consumer, artifact_key)
    edges = graph.incoming_edges(consumer.instance_id)
    assert len(edges) == 1
    assert edges[0].kind == "resolved"
    assert edges[0].producer_instance_id == producer.instance_id
    plan = build_observe_only_reuse_plan(graph)
    by_instance = {entry.instance_id: entry for entry in plan.entries}
    assert by_instance[consumer.instance_id].action == "would_reuse"
    assert "strict_reuse_evidence_incomplete" not in (
        by_instance[consumer.instance_id].reasons
    )


def _assert_unmatched_pair(
    producer: TaskInstanceV3,
    consumer: TaskInstanceV3,
    artifact_key: str,
) -> None:
    graph = _pair_graph(producer, consumer, artifact_key)
    edges = graph.incoming_edges(consumer.instance_id)
    assert len(edges) == 1
    assert edges[0].kind == "unmatched"
    plan = build_observe_only_reuse_plan(graph)
    entry = next(
        item for item in plan.entries if item.instance_id == consumer.instance_id
    )
    assert entry.action == "blocked"
    assert "strict_reuse_evidence_incomplete" in entry.reasons


def test_current_annotation_task_instances_resolve_all_schema_sensitive_edges(tmp_path):
    item = _terminal_item()
    layout = RunLayout(
        out=tmp_path,
        run_id="graph",
        artifact_naming_mode="short",
    )
    annotation_signature = layout.AnnotatedPeakSignatureFromDict(item)
    summary_signature = layout.MatrixSignatureFromDict(
        item,
        psi_min_gene_umis=1,
    )

    call = build_task_instance_v3_by_key(
        "call_peaks", "combine_and_intersect_peaks", item
    )
    assign = build_task_instance_v3_by_key(
        "annotate_peaks", "assign_peaks_to_genes", item
    )
    resolve = build_task_instance_v3_by_key(
        "annotate_peaks", "resolve_peak_issues", item
    )
    summary = build_task_instance_v3_by_key(
        "summarize_matrix",
        "summarize_matrix",
        {**item, "sid": "S", "extra_key": summary_signature},
    )
    gtf = build_task_instance_v3_by_key(
        "annotate_peaks", "convert_peaks_bed_to_gtf", item
    )
    annotate = build_task_instance_v3_by_key(
        "annotate_bams",
        "annotate_slices",
        {"sid": "S", "ctg": "chr1", "extra_key": annotation_signature},
    )

    assert call.params_dict["quantification_mode"] == "terminal-aware"
    for instance in (assign, resolve, gtf):
        assert (
            instance.params_dict["quantification_mode"]
            == TERMINAL_ANNOTATION_COORDINATE_MODE
        )

    _assert_resolved_pair(
        call,
        assign,
        "final_output/*peaks-mispriming-*.bed",
    )
    _assert_resolved_pair(
        resolve,
        summary,
        "final_output/*peaks-annotated*.bed",
    )
    _assert_resolved_pair(
        gtf,
        annotate,
        "final_output/*peaks-annotated*.gtf",
    )


def test_annotation_graph_rejects_v1_v2_cross_links(tmp_path):
    item = _terminal_item()
    layout = RunLayout(
        out=tmp_path,
        run_id="graph-negative",
        artifact_naming_mode="short",
    )
    current_annotation = layout.AnnotatedPeakSignatureFromDict(item)
    peak_record = layout.PeakNameRecord(item)
    old_annotation = build_scoped_artifact_name_v1(
        "annotation",
        annotation_name_payload(
            peak_record.payload,
            annotate_peak_source="resolved",
        ),
        legacy_name=f"{peak_record.legacy_name}.annotate-src-resolved",
    ).code
    current_summary = layout.MatrixSignatureFromDict(item, psi_min_gene_umis=1)
    old_summary = (
        f"{old_annotation}.psi-min-gene-umis-1"
        f"{summary_quantification_signature(item)}"
    )

    for producer_task, consumer_task, artifact_key, current_extra, old_extra in (
        (
            "resolve_peak_issues",
            ("summarize_matrix", "summarize_matrix"),
            "final_output/*peaks-annotated*.bed",
            current_summary,
            old_summary,
        ),
        (
            "convert_peaks_bed_to_gtf",
            ("annotate_bams", "annotate_slices"),
            "final_output/*peaks-annotated*.gtf",
            current_annotation,
            old_annotation,
        ),
    ):
        current_producer = build_task_instance_v3_by_key(
            "annotate_peaks", producer_task, item
        )
        old_producer = _historical_annotation_instance(current_producer)
        consumer_identity = (
            {"sid": "S", "dedup_method": "directional"}
            if consumer_task[0] == "summarize_matrix"
            else {"sid": "S", "ctg": "chr1"}
        )
        current_consumer = build_task_instance_v3_by_key(
            *consumer_task,
            {**consumer_identity, "extra_key": current_extra},
        )
        old_consumer = build_task_instance_v3_by_key(
            *consumer_task,
            {**consumer_identity, "extra_key": old_extra},
        )

        _assert_resolved_pair(old_producer, old_consumer, artifact_key)
        _assert_unmatched_pair(old_producer, current_consumer, artifact_key)
        _assert_unmatched_pair(current_producer, old_consumer, artifact_key)


def test_legacy_graph_signatures_and_health_paths_remain_exact(tmp_path):
    item = {
        **_terminal_item(),
        "quantification_mode": "legacy",
    }
    item.pop("terminal_annotation_schema")
    layout = RunLayout(
        out=tmp_path,
        run_id="legacy-invariant",
        artifact_naming_mode="short",
    )
    annotation_signature = layout.AnnotatedPeakSignatureFromDict(item)
    summary_signature = layout.MatrixSignatureFromDict(item, psi_min_gene_umis=1)
    assert layout.LegacyAnnotationSubdirName(item) == "annotate-src-resolved"

    call = build_task_instance_v3_by_key(
        "call_peaks", "combine_and_intersect_peaks", item
    )
    assign = build_task_instance_v3_by_key(
        "annotate_peaks", "assign_peaks_to_genes", item
    )
    resolve = build_task_instance_v3_by_key(
        "annotate_peaks", "resolve_peak_issues", item
    )
    summary = build_task_instance_v3_by_key(
        "summarize_matrix",
        "summarize_matrix",
        {**item, "sid": "S", "extra_key": summary_signature},
    )
    gtf = build_task_instance_v3_by_key(
        "annotate_peaks", "convert_peaks_bed_to_gtf", item
    )
    annotate = build_task_instance_v3_by_key(
        "annotate_bams",
        "annotate_slices",
        {"sid": "S", "ctg": "chr1", "extra_key": annotation_signature},
    )
    assert call.params_dict["quantification_mode"] == "legacy"
    assert assign.params_dict["quantification_mode"] == "legacy"
    _assert_resolved_pair(call, assign, "final_output/*peaks-mispriming-*.bed")
    _assert_resolved_pair(resolve, summary, "final_output/*peaks-annotated*.bed")
    _assert_resolved_pair(gtf, annotate, "final_output/*peaks-annotated*.gtf")

    config = {
        **item,
        "output_dir": str(tmp_path),
        "run_id": "legacy-invariant",
    }
    layout.AnnotatedFinalOutputSub(item)
    legacy_path = layout.AnnotatedFinalOutputFileCandidates(
        suffix="___peaks-assigned.bed",
        d=item,
    )[0]
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("legacy\n")
    entries = check_task_artifact_health_v2(
        layout.State,
        ArtifactHealthTaskRecordV2(
            assign.task_coordinate,
            "2026-09-24T00:00:00Z",
        ),
        config=config,
    )
    assert len(entries) == 1
    assert entries[0].status == "ready"
    assert str(legacy_path) in entries[0].expectation.paths
    assert all("terminal-annotation" not in path for path in entries[0].expectation.paths)
    assert all("terminal-aware" not in path for path in entries[0].expectation.paths)


def test_mixed_v1_v2_annotation_health_is_schema_exact_and_read_only(tmp_path):
    item = _terminal_item()
    config = {
        **item,
        "output_dir": str(tmp_path),
        "run_id": "mixed-health",
    }
    layout = RunLayout(
        out=tmp_path,
        run_id="mixed-health",
        artifact_naming_mode="short",
    )
    tm = TaskManager(state_dir=layout.State, config=config)
    tm.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    layout.AnnotatedFinalOutputSub(item)

    peak_record = layout.PeakNameRecord(item)
    old_annotation = build_scoped_artifact_name_v1(
        "annotation",
        annotation_name_payload(
            peak_record.payload,
            annotate_peak_source="resolved",
        ),
        legacy_name=f"{peak_record.legacy_name}.annotate-src-resolved",
    )
    old_path = (
        layout.FinalOutput
        / peak_record.code
        / old_annotation.code
        / f"{peak_record.code}.___peaks-assigned.terminal-aware.v1.bed"
    )
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("historical-v1\n")

    current_path = layout.AnnotatedFinalOutputFileCandidates(
        suffix="___peaks-assigned.terminal-aware.v2.bed",
        d=item,
    )[0]
    current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.write_text("current-v2\n")

    assign = build_task_instance_v3_by_key(
        "annotate_peaks", "assign_peaks_to_genes", item
    )
    current_coordinate = assign.task_coordinate
    old_coordinate = build_task_coordinate_v1(
        stage="annotate_peaks",
        task_base="assign_peaks_to_genes",
        identity=assign.task_coordinate.identity_dict,
        params={
            **assign.task_coordinate.params_dict,
            "quantification_mode": "terminal-aware",
        },
    )
    old_record = ArtifactHealthTaskRecordV2(
        old_coordinate,
        "2026-09-24T00:00:00Z",
    )
    current_record = ArtifactHealthTaskRecordV2(
        current_coordinate,
        "2026-09-24T00:00:01Z",
    )

    no_manifest_layout = RunLayout(
        out=tmp_path,
        run_id="coordinate-fallback",
        artifact_naming_mode="short",
    )
    fallback_config, fallback_error = _coordinate_artifact_config(
        current_record,
        config={**config, "misprime_in": 99},
        layout=no_manifest_layout,
    )
    assert not fallback_error
    assert fallback_config["misprime_in"] == "10"
    assert (
        fallback_config["terminal_annotation_schema"]
        == TERMINAL_ANNOTATION_SCHEMA_IDENTITY
    )

    old_entries = check_task_artifact_health_v2(
        layout.State,
        old_record,
        config=config,
    )
    current_entries = check_task_artifact_health_v2(
        layout.State,
        current_record,
        config=config,
    )
    assert len(old_entries) == len(current_entries) == 1
    assert old_entries[0].status == "unresolved"
    assert not old_entries[0].expectation.paths
    assert "historical terminal annotation coordinate" in (
        old_entries[0].expectation.unresolved_detail
    )
    assert current_entries[0].status == "ready"
    assert all("terminal-aware.v2" in path for path in current_entries[0].expectation.paths)
    assert all("terminal-aware.v1" not in path for path in current_entries[0].expectation.paths)

    current_path.unlink()
    assert old_path.is_file()
    current_missing = check_task_artifact_health_v2(
        layout.State,
        current_record,
        config=config,
    )
    assert current_missing[0].status == "missing"
    current_path.write_text("current-v2\n")

    tm.mark_task_done(old_coordinate)
    tm.mark_task_done(current_coordinate)
    before_records = load_artifact_health_task_records_v2(tm.db_path)
    before_db = (
        tm.db_path.stat().st_size,
        tm.db_path.stat().st_mtime_ns,
        tm.db_path.stat().st_ino,
    )
    report = check_artifact_health(tm.db_path, config=config)
    after_db = (
        tm.db_path.stat().st_size,
        tm.db_path.stat().st_mtime_ns,
        tm.db_path.stat().st_ino,
    )
    after_records = load_artifact_health_task_records_v2(tm.db_path)

    assert report.done_task_count == 2
    assert report.ready_count == 1
    assert report.unresolved_count == 1
    assert before_records == after_records
    assert before_db == after_db
    assert old_path.read_text() == "historical-v1\n"
    assert current_path.read_text() == "current-v2\n"
