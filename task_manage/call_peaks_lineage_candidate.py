"""Candidate-only exact lineage for the remaining call-peaks heuristics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, Sequence, Tuple

from .instance_graph import (
    DynamicInstanceEdge,
    DynamicInstanceEdgeKind,
    DynamicInstanceGraph,
    DynamicInstanceNode,
)
from .pipeline_contracts import TaskKey


CALL_PEAKS_LINEAGE_CANDIDATE_POLICY_NAME = (
    "call_peaks_lineage_relations_v1"
)

CallPeaksLineageApplicationStatus = Literal[
    "raw_source_applied",
    "final_fan_in_applied",
    "unsafe_evidence_unchanged",
    "edge_unavailable_unchanged",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"
_LEGACY_COHORT_BAM_ARTIFACT = "cohort_output/*.bam"
_UNSTRANDED_COHORT_BAM_ARTIFACT = "cohort_output/unstranded.bam"
_MERGED_PEAK_ARTIFACT = "cohort_output/*peaks-merged*.bed"
_RAW_CONSUMER_KEY: TaskKey = ("call_peaks", "generate_raw_coverage")
_RAW_PRODUCER_KEY: TaskKey = ("merge_units", "cat_chunks")
_FINAL_CONSUMER_KEY: TaskKey = (
    "call_peaks",
    "combine_and_intersect_peaks",
)
_FINAL_PRODUCER_KEY: TaskKey = ("call_peaks", "merge_call_peaks")
_STRANDS = ("plus", "minus")
_COVERAGE_SCALING_MODES = frozenset({"none", "expr", "polyA", "both"})
_REPLACEABLE_EDGE_KINDS = frozenset(
    {"heuristic_matched", "singleton", "ambiguous", "unmatched"}
)


@dataclass(frozen=True)
class CallPeaksLineageCandidateApplication:
    consumer_instance_id: str
    consumer_task_key: TaskKey
    artifact_key: str
    application_status: CallPeaksLineageApplicationStatus
    edge_kind_before: DynamicInstanceEdgeKind | None
    producer_instance_ids: Tuple[str, ...]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "consumer_instance_id": self.consumer_instance_id,
            "consumer_task_key": {
                "stage": self.consumer_task_key[0],
                "task_base": self.consumer_task_key[1],
            },
            "artifact_key": self.artifact_key,
            "application_status": self.application_status,
            "edge_kind_before": self.edge_kind_before,
            "producer_instance_ids": list(self.producer_instance_ids),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CallPeaksLineageCandidateGraphResult:
    source_graph: DynamicInstanceGraph
    graph: DynamicInstanceGraph
    applications: Tuple[CallPeaksLineageCandidateApplication, ...]
    policy_name: str = CALL_PEAKS_LINEAGE_CANDIDATE_POLICY_NAME
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    @property
    def application_status_counts(self) -> dict[str, int]:
        return dict(
            sorted(
                Counter(
                    application.application_status
                    for application in self.applications
                ).items()
            )
        )

    @property
    def applied_relation_count(self) -> int:
        return sum(
            application.application_status
            in {"raw_source_applied", "final_fan_in_applied"}
            for application in self.applications
        )

    @property
    def unchanged_relation_count(self) -> int:
        return len(self.applications) - self.applied_relation_count

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        source_report = self.source_graph.report()
        candidate_report = self.graph.report()
        applications = self.applications
        if limit is not None:
            applications = applications[: max(0, int(limit))]
        return {
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "application_count": len(self.applications),
            "application_status_counts": self.application_status_counts,
            "applied_relation_count": self.applied_relation_count,
            "unchanged_relation_count": self.unchanged_relation_count,
            "source_graph": source_report.as_dict(),
            "candidate_graph": candidate_report.as_dict(),
            "edge_count_delta": (
                candidate_report.edge_count - source_report.edge_count
            ),
            "concrete_edge_count_delta": (
                candidate_report.concrete_edge_count
                - source_report.concrete_edge_count
            ),
            "blocking_edge_count_delta": (
                candidate_report.blocking_edge_count
                - source_report.blocking_edge_count
            ),
            "applications": [
                application.as_dict() for application in applications
            ],
        }


def build_call_peaks_lineage_candidate_graph(
    graph: DynamicInstanceGraph,
) -> CallPeaksLineageCandidateGraphResult:
    """Replace only exact raw-BAM and final four-branch heuristic edges."""

    candidate_edges = list(graph.edges)
    nodes_by_task = graph.nodes_by_task_key
    applications: list[CallPeaksLineageCandidateApplication] = []

    raw_consumers = nodes_by_task.get(_RAW_CONSUMER_KEY, ())
    raw_producers = nodes_by_task.get(_RAW_PRODUCER_KEY, ())
    for consumer in raw_consumers:
        applications.append(
            _apply_raw_source(
                consumer,
                raw_producers,
                candidate_edges=candidate_edges,
            )
        )

    final_consumers = nodes_by_task.get(_FINAL_CONSUMER_KEY, ())
    final_producers = nodes_by_task.get(_FINAL_PRODUCER_KEY, ())
    for consumer in final_consumers:
        applications.append(
            _apply_final_fan_in(
                consumer,
                final_producers,
                candidate_edges=candidate_edges,
            )
        )

    candidate_graph = DynamicInstanceGraph(
        state_db_path=graph.state_db_path,
        topology_source=graph.topology_source,
        edge_resolver_count=graph.edge_resolver_count,
        source_decision_count=graph.source_decision_count,
        nodes=graph.nodes,
        edges=tuple(candidate_edges),
    )
    return CallPeaksLineageCandidateGraphResult(
        source_graph=graph,
        graph=candidate_graph,
        applications=tuple(applications),
    )


def _apply_raw_source(
    consumer: DynamicInstanceNode,
    producer_nodes: Sequence[DynamicInstanceNode],
    *,
    candidate_edges: list[DynamicInstanceEdge],
) -> CallPeaksLineageCandidateApplication:
    artifact_key = (
        _UNSTRANDED_COHORT_BAM_ARTIFACT
        if _UNSTRANDED_COHORT_BAM_ARTIFACT in consumer.consumes
        else _LEGACY_COHORT_BAM_ARTIFACT
    )
    edge_index, edge = _replaceable_edge(
        candidate_edges,
        consumer=consumer,
        artifact_key=artifact_key,
        producer_key=_RAW_PRODUCER_KEY,
    )
    if edge_index is None or edge is None:
        return _application(
            consumer,
            artifact_key=artifact_key,
            application_status="edge_unavailable_unchanged",
            edge=edge,
            detail=(
                "expected exactly one heuristic cohort-BAM edge from "
                "merge_units:cat_chunks"
            ),
        )

    expected, expected_error = _raw_consumer_signature(
        consumer,
        artifact_key=artifact_key,
    )
    if expected_error:
        return _application(
            consumer,
            artifact_key=artifact_key,
            application_status="unsafe_evidence_unchanged",
            edge=edge,
            detail=expected_error,
        )

    matches: list[DynamicInstanceNode] = []
    incomplete: list[str] = []
    for producer in producer_nodes:
        relation = _raw_producer_relation(producer, expected)
        if relation == "match":
            matches.append(producer)
        elif relation == "incomplete":
            incomplete.append(producer.instance_id)
    if incomplete or len(matches) != 1:
        detail = (
            f"raw cohort-BAM relation requires one producer; "
            f"matched={len(matches)} incomplete={len(incomplete)}"
        )
        if incomplete:
            detail += f"; incomplete={','.join(sorted(incomplete))}"
        return _application(
            consumer,
            artifact_key=artifact_key,
            application_status="unsafe_evidence_unchanged",
            edge=edge,
            producer_instance_ids=tuple(
                node.instance_id for node in matches
            ),
            detail=detail,
        )

    producer = matches[0]
    if (
        artifact_key == _LEGACY_COHORT_BAM_ARTIFACT
        and set(edge.candidate_instance_ids) != {producer.instance_id}
    ):
        return _application(
            consumer,
            artifact_key=artifact_key,
            application_status="unsafe_evidence_unchanged",
            edge=edge,
            producer_instance_ids=(producer.instance_id,),
            detail=(
                "heuristic edge candidates disagree with the exact "
                "cohort-BAM relation"
            ),
        )

    candidate_edges[edge_index] = DynamicInstanceEdge(
        artifact_key=artifact_key,
        consumer_instance_id=consumer.instance_id,
        consumer_key=consumer.key,
        kind="resolved",
        producer_instance_id=producer.instance_id,
        producer_task_keys=(_RAW_PRODUCER_KEY,),
        candidate_instance_ids=(producer.instance_id,),
        matched_field_keys=(
            "dedup_method",
            "is_polyA",
            "polyA_status",
            *(
                ("coverage_source", "strand_filter")
                if artifact_key == _UNSTRANDED_COHORT_BAM_ARTIFACT
                else ("strand",)
            ),
        ),
        detail=(
            "exact raw-coverage cohort BAM selected by dedup method, "
            "is_polyA/polyA_status, and explicit coverage-source semantics; "
            "artifact_health=separate"
        ),
    )
    return _application(
        consumer,
        artifact_key=artifact_key,
        application_status="raw_source_applied",
        edge=edge,
        producer_instance_ids=(producer.instance_id,),
        detail="replaced one exact raw-coverage source edge",
    )


def _apply_final_fan_in(
    consumer: DynamicInstanceNode,
    producer_nodes: Sequence[DynamicInstanceNode],
    *,
    candidate_edges: list[DynamicInstanceEdge],
) -> CallPeaksLineageCandidateApplication:
    edge_index, edge = _replaceable_edge(
        candidate_edges,
        consumer=consumer,
        artifact_key=_MERGED_PEAK_ARTIFACT,
        producer_key=_FINAL_PRODUCER_KEY,
    )
    if edge_index is None or edge is None:
        return _application(
            consumer,
            artifact_key=_MERGED_PEAK_ARTIFACT,
            application_status="edge_unavailable_unchanged",
            edge=edge,
            detail=(
                "expected exactly one heuristic merged-peak edge from "
                "call_peaks:merge_call_peaks"
            ),
        )

    config, config_error = _final_consumer_config(consumer)
    if config_error:
        return _application(
            consumer,
            artifact_key=_MERGED_PEAK_ARTIFACT,
            application_status="unsafe_evidence_unchanged",
            edge=edge,
            detail=config_error,
        )

    matched_by_branch: dict[
        tuple[bool, str],
        list[DynamicInstanceNode],
    ] = {}
    incomplete_ids: set[str] = set()
    for is_poly_a in (False, True):
        for strand in _STRANDS:
            branch = (is_poly_a, strand)
            matches: list[DynamicInstanceNode] = []
            for producer in producer_nodes:
                relation = _final_producer_relation(
                    producer,
                    config=config,
                    is_poly_a=is_poly_a,
                    strand=strand,
                )
                if relation == "match":
                    matches.append(producer)
                elif relation == "incomplete":
                    incomplete_ids.add(producer.instance_id)
            matched_by_branch[branch] = matches

    invalid_branches = {
        branch: matches
        for branch, matches in matched_by_branch.items()
        if len(matches) != 1
    }
    if incomplete_ids or invalid_branches:
        branch_counts = ",".join(
            f"{'polyA' if branch[0] else 'exp'}:{branch[1]}={len(matches)}"
            for branch, matches in sorted(invalid_branches.items())
        )
        detail = (
            "final peak fan-in requires one producer for each of four "
            f"branches; invalid={branch_counts or '-'}; "
            f"incomplete={len(incomplete_ids)}"
        )
        if incomplete_ids:
            detail += f"; ids={','.join(sorted(incomplete_ids))}"
        return _application(
            consumer,
            artifact_key=_MERGED_PEAK_ARTIFACT,
            application_status="unsafe_evidence_unchanged",
            edge=edge,
            producer_instance_ids=tuple(
                sorted(
                    {
                        producer.instance_id
                        for matches in matched_by_branch.values()
                        for producer in matches
                    }
                )
            ),
            detail=detail,
        )

    ordered_producers = tuple(
        matched_by_branch[(is_poly_a, strand)][0]
        for is_poly_a in (False, True)
        for strand in _STRANDS
    )
    producer_ids = tuple(
        producer.instance_id for producer in ordered_producers
    )
    if len(set(producer_ids)) != 4:
        return _application(
            consumer,
            artifact_key=_MERGED_PEAK_ARTIFACT,
            application_status="unsafe_evidence_unchanged",
            edge=edge,
            producer_instance_ids=producer_ids,
            detail="one producer was selected for more than one final branch",
        )

    replacement_edges = tuple(
        DynamicInstanceEdge(
            artifact_key=_MERGED_PEAK_ARTIFACT,
            consumer_instance_id=consumer.instance_id,
            consumer_key=consumer.key,
            kind="fan_in_complete",
            producer_instance_id=producer.instance_id,
            producer_task_keys=(_FINAL_PRODUCER_KEY,),
            candidate_instance_ids=producer_ids,
            matched_field_keys=(
                "dedup_method",
                "polyA_status",
                "coverage_scaling",
                "is_polyA",
                "strand",
                "depth_threshold",
                "merge_distance",
                "peak_threshold_mode",
                "dynamic_threshold_stat",
                "dynamic_threshold_alpha",
                "dynamic_threshold_floor",
                "dynamic_threshold_normalized",
            ),
            detail=(
                "exact four-branch final peak fan-in "
                "(Exp/PolyA x plus/minus); artifact_health=separate"
            ),
        )
        for producer in ordered_producers
    )
    candidate_edges[edge_index : edge_index + 1] = replacement_edges
    return _application(
        consumer,
        artifact_key=_MERGED_PEAK_ARTIFACT,
        application_status="final_fan_in_applied",
        edge=edge,
        producer_instance_ids=producer_ids,
        detail="replaced one heuristic with four exact fan-in edges",
    )


def _replaceable_edge(
    edges: Sequence[DynamicInstanceEdge],
    *,
    consumer: DynamicInstanceNode,
    artifact_key: str,
    producer_key: TaskKey,
) -> tuple[int | None, DynamicInstanceEdge | None]:
    matches = tuple(
        (index, edge)
        for index, edge in enumerate(edges)
        if edge.consumer_instance_id == consumer.instance_id
        and edge.artifact_key == artifact_key
    )
    if len(matches) != 1:
        return None, matches[0][1] if matches else None
    index, edge = matches[0]
    if (
        edge.kind not in _REPLACEABLE_EDGE_KINDS
        or edge.producer_task_keys != (producer_key,)
        or edge.producer_instance_id is not None
    ):
        return None, edge
    return index, edge


def _raw_consumer_signature(
    consumer: DynamicInstanceNode,
    *,
    artifact_key: str,
) -> tuple[dict[str, Any], str | None]:
    fields = consumer.fields_dict
    required = ("dedup_method", "strand", "is_polyA", "polyA_status")
    missing = tuple(key for key in required if key not in fields)
    if missing:
        return {}, (
            "raw coverage consumer lacks required persisted fields: "
            + ",".join(missing)
        )
    dedup_method = fields["dedup_method"]
    strand = fields["strand"]
    is_poly_a = fields["is_polyA"]
    poly_a_status = fields["polyA_status"]
    if not isinstance(dedup_method, str) or not dedup_method:
        return {}, "raw coverage consumer has invalid dedup_method"
    if strand not in _STRANDS:
        return {}, "raw coverage consumer has invalid strand"
    if not isinstance(is_poly_a, bool):
        return {}, "raw coverage consumer is_polyA is not boolean"
    if is_poly_a and (
        not isinstance(poly_a_status, str) or not poly_a_status
    ):
        return {}, "PolyA raw coverage consumer lacks polyA_status"
    if not is_poly_a and poly_a_status not in (None, ""):
        return {}, "expression raw coverage consumer has a PolyA status"
    unstranded_source = artifact_key == _UNSTRANDED_COHORT_BAM_ARTIFACT
    if unstranded_source:
        if fields.get("coverage_source") != "unstranded":
            return {}, "raw coverage consumer lacks coverage_source=unstranded"
        if fields.get("strand_filter") != strand:
            return {}, "raw coverage consumer strand_filter does not match strand"
    return {
        "dedup_method": dedup_method,
        "strand": strand,
        "producer_strand": None if unstranded_source else strand,
        "is_polyA": is_poly_a,
        "polyA_status": poly_a_status,
    }, None


def _raw_producer_relation(
    producer: DynamicInstanceNode,
    expected: dict[str, Any],
) -> Literal["match", "unrelated", "incomplete"]:
    fields = producer.fields_dict
    for key, expected_value in (
        ("dedup_method", expected["dedup_method"]),
        ("strand", expected["producer_strand"]),
    ):
        if key in fields and fields[key] != expected_value:
            return "unrelated"
    if any(
        key not in fields
        for key in ("dedup_method", "strand", "polyA_status")
    ):
        return "incomplete"
    poly_a_status = fields["polyA_status"]
    producer_is_poly_a = poly_a_status not in (None, "")
    if producer_is_poly_a != expected["is_polyA"]:
        return "unrelated"
    if producer_is_poly_a and poly_a_status != expected["polyA_status"]:
        return "unrelated"
    return "match"


def _final_consumer_config(
    consumer: DynamicInstanceNode,
) -> tuple[dict[str, Any], str | None]:
    fields = consumer.fields_dict
    required = (
        "dedup_method",
        "polyA_status",
        "coverage_scaling",
        "depth_threshold",
        "depth_threshold_polyA",
        "merge_distance",
        "merge_distance_polyA",
        "exp_peak_threshold_mode",
        "polyA_peak_threshold_mode",
        "exp_dynamic_threshold_stat",
        "polyA_dynamic_threshold_stat",
        "exp_dynamic_threshold_alpha",
        "polyA_dynamic_threshold_alpha",
        "exp_dynamic_threshold_floor",
        "polyA_dynamic_threshold_floor",
        "exp_dynamic_threshold_normalized",
        "polyA_dynamic_threshold_normalized",
    )
    missing = tuple(key for key in required if key not in fields)
    if missing:
        return {}, (
            "final peak consumer lacks required persisted fields: "
            + ",".join(missing)
        )
    if (
        not isinstance(fields["dedup_method"], str)
        or not fields["dedup_method"]
    ):
        return {}, "final peak consumer has invalid dedup_method"
    if (
        not isinstance(fields["polyA_status"], str)
        or not fields["polyA_status"]
    ):
        return {}, "final peak consumer has invalid polyA_status"
    if fields["coverage_scaling"] not in _COVERAGE_SCALING_MODES:
        return {}, "final peak consumer has unsupported coverage_scaling"
    return dict(fields), None


def _final_producer_relation(
    producer: DynamicInstanceNode,
    *,
    config: dict[str, Any],
    is_poly_a: bool,
    strand: str,
) -> Literal["match", "unrelated", "incomplete"]:
    fields = producer.fields_dict
    branch_keys = (
        "dedup_method",
        "polyA_status",
        "strand",
        "is_polyA",
        "scaling_factor",
        "remove_CDS",
        "depth_threshold",
        "merge_distance",
        "peak_threshold_mode",
        "dynamic_threshold_stat",
        "dynamic_threshold_alpha",
        "dynamic_threshold_floor",
        "dynamic_threshold_normalized",
    )

    explicit_relations = (
        ("dedup_method", config["dedup_method"]),
        ("strand", strand),
        ("is_polyA", is_poly_a),
    )
    for key, expected in explicit_relations:
        if key in fields and fields[key] != expected:
            return "unrelated"
    if "polyA_status" in fields:
        expected_poly_a_status = (
            config["polyA_status"] if is_poly_a else None
        )
        if fields["polyA_status"] not in (
            expected_poly_a_status,
            "" if expected_poly_a_status is None else expected_poly_a_status,
        ):
            return "unrelated"
    if any(key not in fields for key in branch_keys):
        return "incomplete"
    if not isinstance(fields["is_polyA"], bool):
        return "incomplete"
    if fields["remove_CDS"] is not False:
        return "unrelated"

    scaling_mode = config["coverage_scaling"]
    scaling_required = (
        scaling_mode in {"polyA", "both"}
        if is_poly_a
        else scaling_mode in {"expr", "both"}
    )
    scaling_factor = fields["scaling_factor"]
    if scaling_required:
        if (
            isinstance(scaling_factor, bool)
            or not isinstance(scaling_factor, (int, float))
            or scaling_factor <= 0
        ):
            return "unrelated"
    elif scaling_factor is not None:
        return "unrelated"

    prefix = "polyA" if is_poly_a else "exp"
    expected_values = {
        "depth_threshold": config[
            "depth_threshold_polyA" if is_poly_a else "depth_threshold"
        ],
        "merge_distance": config[
            "merge_distance_polyA" if is_poly_a else "merge_distance"
        ],
        "peak_threshold_mode": config[
            f"{prefix}_peak_threshold_mode"
        ],
        "dynamic_threshold_stat": config[
            f"{prefix}_dynamic_threshold_stat"
        ],
        "dynamic_threshold_alpha": config[
            f"{prefix}_dynamic_threshold_alpha"
        ],
        "dynamic_threshold_floor": config[
            f"{prefix}_dynamic_threshold_floor"
        ],
        "dynamic_threshold_normalized": config[
            f"{prefix}_dynamic_threshold_normalized"
        ],
    }
    if any(
        _legacy_value(fields[key]) != _legacy_value(value)
        for key, value in expected_values.items()
    ):
        return "unrelated"
    return "match"


def _application(
    consumer: DynamicInstanceNode,
    *,
    artifact_key: str,
    application_status: CallPeaksLineageApplicationStatus,
    edge: DynamicInstanceEdge | None,
    detail: str,
    producer_instance_ids: Tuple[str, ...] = (),
) -> CallPeaksLineageCandidateApplication:
    return CallPeaksLineageCandidateApplication(
        consumer_instance_id=consumer.instance_id,
        consumer_task_key=consumer.key,
        artifact_key=artifact_key,
        application_status=application_status,
        edge_kind_before=None if edge is None else edge.kind,
        producer_instance_ids=producer_instance_ids,
        detail=detail,
    )


def _legacy_value(value: Any) -> str:
    return "" if value is None else str(value)
