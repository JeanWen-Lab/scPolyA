"""Candidate-only graph evidence from persisted coverage branch relations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Tuple

from .branch_manifest_relations import (
    BranchDiagnosticRelation,
    BranchDiagnosticRelationReport,
)
from .instance_graph import (
    DynamicInstanceEdge,
    DynamicInstanceEdgeKind,
    DynamicInstanceGraph,
)
from .pipeline_contracts import TaskKey


COVERAGE_BRANCH_CANDIDATE_POLICY_NAME = "coverage_branch_manifest_relations_v1"

CoverageBranchCandidateApplicationStatus = Literal[
    "matched_applied",
    "no_op_applied",
    "root_unchanged",
    "unsafe_relation_unchanged",
    "source_mismatch_unchanged",
    "stale_relation_unchanged",
    "edge_unavailable_unchanged",
    "already_concrete_unchanged",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"
_DECLARED_COVERAGE_INPUT = "cohort_output/*.bedgraph"
_REQUIRED_MATCHED_FIELDS = frozenset(
    {"dedup_method", "polyA_status", "is_polyA", "strand"}
)
_REPLACEABLE_EDGE_KINDS: frozenset[DynamicInstanceEdgeKind] = frozenset(
    {
        "heuristic_matched",
        "singleton",
        "ambiguous",
        "unmatched",
        "producer_unobserved",
        "static_ambiguous",
    }
)

_RAW_COVERAGE = "cohort_coverage:raw_bedgraph"
_SCALED_COVERAGE = "cohort_coverage:scaled_bedgraph"
_REMOVE_CDS_COVERAGE = "cohort_coverage:remove_cds_bedgraph"
_NEGATED_COVERAGE = "cohort_coverage:negated_bedgraph"

_SEMANTIC_PRODUCER_BY_ARTIFACT: Mapping[str, TaskKey] = {
    _RAW_COVERAGE: ("call_peaks", "generate_raw_coverage"),
    _SCALED_COVERAGE: ("call_peaks", "upm_scale_coverage"),
    _REMOVE_CDS_COVERAGE: ("call_peaks", "exclude_cds_region"),
    _NEGATED_COVERAGE: ("call_peaks", "negate_bedgraph"),
}

_ALLOWED_SEMANTIC_INPUTS_BY_CONSUMER: Mapping[TaskKey, frozenset[str]] = {
    ("call_peaks", "upm_scale_coverage"): frozenset({_RAW_COVERAGE}),
    ("call_peaks", "exclude_cds_region"): frozenset(
        {_RAW_COVERAGE, _SCALED_COVERAGE}
    ),
    ("call_peaks", "negate_bedgraph"): frozenset(
        {_RAW_COVERAGE, _SCALED_COVERAGE, _REMOVE_CDS_COVERAGE}
    ),
    ("call_peaks", "generate_bigwig"): frozenset(
        {
            _RAW_COVERAGE,
            _SCALED_COVERAGE,
            _REMOVE_CDS_COVERAGE,
            _NEGATED_COVERAGE,
        }
    ),
    ("call_peaks", "merge_call_peaks"): frozenset(
        {_RAW_COVERAGE, _SCALED_COVERAGE, _REMOVE_CDS_COVERAGE}
    ),
}

_ROOT_TASK_KEYS = frozenset({("call_peaks", "generate_raw_coverage")})


@dataclass(frozen=True)
class CoverageBranchCandidateApplication:
    """How one diagnostic relation affected the isolated candidate graph."""

    consumer_instance_id: str
    consumer_decision_id: int
    consumer_task_key: TaskKey
    relation_status: str
    application_status: CoverageBranchCandidateApplicationStatus
    declared_input_artifact: str | None
    effective_input_artifact: str | None
    producer_instance_id: str | None
    edge_kind_before: DynamicInstanceEdgeKind | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "consumer_instance_id": self.consumer_instance_id,
            "consumer_decision_id": self.consumer_decision_id,
            "consumer_task_key": {
                "stage": self.consumer_task_key[0],
                "task_base": self.consumer_task_key[1],
            },
            "relation_status": self.relation_status,
            "application_status": self.application_status,
            "declared_input_artifact": self.declared_input_artifact,
            "effective_input_artifact": self.effective_input_artifact,
            "producer_instance_id": self.producer_instance_id,
            "edge_kind_before": self.edge_kind_before,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CoverageBranchCandidateGraphResult:
    """An immutable candidate graph plus an audit of every relation application."""

    source_graph: DynamicInstanceGraph
    graph: DynamicInstanceGraph
    source_relation_count: int
    relation_status_counts: Mapping[str, int]
    applications: Tuple[CoverageBranchCandidateApplication, ...]
    policy_name: str = COVERAGE_BRANCH_CANDIDATE_POLICY_NAME
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
            in {"matched_applied", "no_op_applied"}
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
            "source_relation_count": self.source_relation_count,
            "relation_status_counts": dict(self.relation_status_counts),
            "application_count": len(self.applications),
            "application_status_counts": self.application_status_counts,
            "applied_relation_count": self.applied_relation_count,
            "unchanged_relation_count": self.unchanged_relation_count,
            "source_graph": source_report.as_dict(),
            "candidate_graph": candidate_report.as_dict(),
            "edge_count_delta": candidate_report.edge_count - source_report.edge_count,
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


def build_coverage_branch_candidate_graph(
    graph: DynamicInstanceGraph,
    relation_report: BranchDiagnosticRelationReport,
) -> CoverageBranchCandidateGraphResult:
    """Apply exact persisted branch relations to an isolated graph view only."""

    if relation_report.strict_effect != _DIAGNOSTIC_ONLY:
        raise ValueError("Coverage branch relations must remain diagnostic_only")
    relation_ids = tuple(
        relation.consumer_instance_id for relation in relation_report.relations
    )
    if len(set(relation_ids)) != len(relation_ids):
        raise ValueError("Coverage branch relation report has duplicate consumers")

    source_matches = (
        relation_report.source_decision_count == graph.source_decision_count
        and relation_report.latest_instance_count == len(graph.nodes)
    )
    nodes_by_id = graph.nodes_by_instance_id
    candidate_edges = list(graph.edges)
    applications: list[CoverageBranchCandidateApplication] = []

    for relation in relation_report.relations:
        if not source_matches:
            applications.append(
                _application(
                    relation,
                    application_status="source_mismatch_unchanged",
                    detail=(
                        "relation report and graph do not describe the same "
                        "decision/latest-instance counts"
                    ),
                )
            )
            continue

        consumer = nodes_by_id.get(relation.consumer_instance_id)
        if (
            consumer is None
            or consumer.decision_id != relation.consumer_decision_id
            or consumer.key != relation.consumer_task_key
        ):
            applications.append(
                _application(
                    relation,
                    application_status="stale_relation_unchanged",
                    detail="consumer relation does not match the latest graph node",
                )
            )
            continue

        if relation.relation_status == "matched":
            applications.append(
                _apply_matched_relation(
                    relation,
                    nodes_by_id=nodes_by_id,
                    candidate_edges=candidate_edges,
                )
            )
            continue
        if relation.relation_status == "no_op":
            applications.append(
                _apply_no_op_relation(
                    relation,
                    candidate_edges=candidate_edges,
                )
            )
            continue
        if relation.relation_status == "root":
            if (
                relation.consumer_task_key in _ROOT_TASK_KEYS
                and relation.effective_input_artifact is None
                and relation.producer_instance_id is None
            ):
                applications.append(
                    _application(
                        relation,
                        application_status="root_unchanged",
                        detail=(
                            "branch root creates no synthetic producer; "
                            "non-coverage inputs remain unchanged"
                        ),
                    )
                )
            else:
                applications.append(
                    _application(
                        relation,
                        application_status="unsafe_relation_unchanged",
                        detail="root relation does not match the explicit root policy",
                    )
                )
            continue

        applications.append(
            _application(
                relation,
                application_status="unsafe_relation_unchanged",
                detail=(
                    f"relation status {relation.relation_status} is not candidate evidence"
                ),
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
    return CoverageBranchCandidateGraphResult(
        source_graph=graph,
        graph=candidate_graph,
        source_relation_count=relation_report.relation_count,
        relation_status_counts=relation_report.relation_status_counts,
        applications=tuple(applications),
    )


def _apply_matched_relation(
    relation: BranchDiagnosticRelation,
    *,
    nodes_by_id: Mapping[str, Any],
    candidate_edges: list[DynamicInstanceEdge],
) -> CoverageBranchCandidateApplication:
    semantic_input = relation.effective_input_artifact
    producer_id = relation.producer_instance_id
    producer_key = relation.producer_task_key
    producer = None if producer_id is None else nodes_by_id.get(producer_id)
    expected_producer_key = _SEMANTIC_PRODUCER_BY_ARTIFACT.get(
        semantic_input or ""
    )
    allowed_inputs = _ALLOWED_SEMANTIC_INPUTS_BY_CONSUMER.get(
        relation.consumer_task_key
    )
    if (
        relation.consumer_manifest_status != "available"
        or relation.consumer_branch_status != "active"
        or semantic_input is None
        or producer_id is None
        or producer_key is None
        or producer is None
        or producer.decision_id != relation.producer_decision_id
        or producer.key != producer_key
        or producer_key != expected_producer_key
        or allowed_inputs is None
        or semantic_input not in allowed_inputs
        or relation.effective_output_artifact != semantic_input
        or relation.candidate_instance_ids != (producer_id,)
        or not _REQUIRED_MATCHED_FIELDS.issubset(relation.matched_field_keys)
    ):
        return _application(
            relation,
            application_status="unsafe_relation_unchanged",
            declared_input_artifact=_DECLARED_COVERAGE_INPUT,
            detail="matched relation fails the explicit semantic/task/generation policy",
        )

    edge_indexes = _coverage_edge_indexes(
        candidate_edges,
        consumer_instance_id=relation.consumer_instance_id,
    )
    if len(edge_indexes) != 1:
        return _application(
            relation,
            application_status="edge_unavailable_unchanged",
            declared_input_artifact=_DECLARED_COVERAGE_INPUT,
            detail=(
                "candidate requires exactly one declared coverage input edge; "
                f"found {len(edge_indexes)}"
            ),
        )

    edge_index = edge_indexes[0]
    previous = candidate_edges[edge_index]
    if previous.is_concrete:
        if previous.producer_instance_id == producer_id:
            return _application(
                relation,
                application_status="already_concrete_unchanged",
                declared_input_artifact=_DECLARED_COVERAGE_INPUT,
                edge_kind_before=previous.kind,
                detail="baseline edge already names the same exact producer",
            )
        return _application(
            relation,
            application_status="edge_unavailable_unchanged",
            declared_input_artifact=_DECLARED_COVERAGE_INPUT,
            edge_kind_before=previous.kind,
            detail="baseline edge is already concrete with a different producer",
        )
    if previous.kind not in _REPLACEABLE_EDGE_KINDS:
        return _application(
            relation,
            application_status="edge_unavailable_unchanged",
            declared_input_artifact=_DECLARED_COVERAGE_INPUT,
            edge_kind_before=previous.kind,
            detail=f"baseline edge kind {previous.kind} is not replaceable",
        )

    candidate_edges[edge_index] = DynamicInstanceEdge(
        artifact_key=previous.artifact_key,
        consumer_instance_id=previous.consumer_instance_id,
        consumer_key=previous.consumer_key,
        kind="resolved",
        producer_instance_id=producer_id,
        producer_task_keys=(producer_key,),
        candidate_instance_ids=(producer_id,),
        matched_field_keys=relation.matched_field_keys,
        detail=(
            f"candidate_policy={COVERAGE_BRANCH_CANDIDATE_POLICY_NAME}; "
            f"semantic_input={semantic_input}; "
            f"consumer_decision_id={relation.consumer_decision_id}; "
            f"producer_decision_id={relation.producer_decision_id}; "
            "source_strict_effect=diagnostic_only; artifact_health=separate"
        ),
    )
    return _application(
        relation,
        application_status="matched_applied",
        declared_input_artifact=_DECLARED_COVERAGE_INPUT,
        edge_kind_before=previous.kind,
        detail=(
            f"replaced one {previous.kind} edge with exact producer {producer_id}"
        ),
    )


def _apply_no_op_relation(
    relation: BranchDiagnosticRelation,
    *,
    candidate_edges: list[DynamicInstanceEdge],
) -> CoverageBranchCandidateApplication:
    if (
        relation.consumer_task_key not in _ALLOWED_SEMANTIC_INPUTS_BY_CONSUMER
        or relation.consumer_manifest_status != "available"
        or relation.consumer_branch_status != "no_op"
        or relation.effective_input_artifact is not None
        or relation.effective_output_artifact is not None
        or relation.producer_instance_id is not None
        or relation.producer_decision_id is not None
        or relation.producer_task_key is not None
        or relation.candidate_instance_ids
    ):
        return _application(
            relation,
            application_status="unsafe_relation_unchanged",
            declared_input_artifact=_DECLARED_COVERAGE_INPUT,
            detail="no-op relation fails the explicit consumer/input policy",
        )

    edge_indexes = _coverage_edge_indexes(
        candidate_edges,
        consumer_instance_id=relation.consumer_instance_id,
    )
    if len(edge_indexes) != 1:
        return _application(
            relation,
            application_status="edge_unavailable_unchanged",
            declared_input_artifact=_DECLARED_COVERAGE_INPUT,
            detail=(
                "candidate no-op requires exactly one declared coverage input edge; "
                f"found {len(edge_indexes)}"
            ),
        )

    edge_index = edge_indexes[0]
    previous = candidate_edges.pop(edge_index)
    return _application(
        relation,
        application_status="no_op_applied",
        declared_input_artifact=_DECLARED_COVERAGE_INPUT,
        edge_kind_before=previous.kind,
        detail=(
            "removed only the declared coverage input edge; all unrelated "
            "inputs remain in the candidate graph"
        ),
    )


def _coverage_edge_indexes(
    edges: list[DynamicInstanceEdge],
    *,
    consumer_instance_id: str,
) -> Tuple[int, ...]:
    return tuple(
        index
        for index, edge in enumerate(edges)
        if edge.consumer_instance_id == consumer_instance_id
        and edge.artifact_key == _DECLARED_COVERAGE_INPUT
    )


def _application(
    relation: BranchDiagnosticRelation,
    *,
    application_status: CoverageBranchCandidateApplicationStatus,
    declared_input_artifact: str | None = None,
    edge_kind_before: DynamicInstanceEdgeKind | None = None,
    detail: str,
) -> CoverageBranchCandidateApplication:
    return CoverageBranchCandidateApplication(
        consumer_instance_id=relation.consumer_instance_id,
        consumer_decision_id=relation.consumer_decision_id,
        consumer_task_key=relation.consumer_task_key,
        relation_status=relation.relation_status,
        application_status=application_status,
        declared_input_artifact=declared_input_artifact,
        effective_input_artifact=relation.effective_input_artifact,
        producer_instance_id=relation.producer_instance_id,
        edge_kind_before=edge_kind_before,
        detail=detail,
    )
