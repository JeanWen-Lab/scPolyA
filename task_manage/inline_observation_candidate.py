"""Candidate-only graph evidence from exact inline completion relations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Tuple

from .inline_execution_observations import (
    INLINE_OBSERVED_PRODUCER_BY_ARTIFACT,
)
from .inline_observation_relations import (
    InlineObservationRelation,
    InlineObservationRelationReport,
)
from .instance_graph import (
    DynamicInstanceEdge,
    DynamicInstanceEdgeKind,
    DynamicInstanceGraph,
)
from .pipeline_contracts import TaskKey


INLINE_OBSERVATION_CANDIDATE_POLICY_NAME = (
    "inline_execution_observation_relations_v1"
)

InlineObservationCandidateApplicationStatus = Literal[
    "matched_applied",
    "unsafe_relation_unchanged",
    "source_mismatch_unchanged",
    "stale_relation_unchanged",
    "edge_unavailable_unchanged",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"


@dataclass(frozen=True)
class InlineObservationCandidateApplication:
    consumer_instance_id: str
    consumer_decision_id: int
    consumer_task_key: TaskKey
    artifact_key: str
    relation_status: str
    application_status: InlineObservationCandidateApplicationStatus
    observation_id: int | None
    observation_token: str | None
    producer_instance_id: str | None
    producer_task_key: TaskKey | None
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
            "artifact_key": self.artifact_key,
            "relation_status": self.relation_status,
            "application_status": self.application_status,
            "observation_id": self.observation_id,
            "observation_token": self.observation_token,
            "producer_instance_id": self.producer_instance_id,
            "producer_task_key": (
                None
                if self.producer_task_key is None
                else {
                    "stage": self.producer_task_key[0],
                    "task_base": self.producer_task_key[1],
                }
            ),
            "edge_kind_before": self.edge_kind_before,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class InlineObservationCandidateGraphResult:
    source_graph: DynamicInstanceGraph
    graph: DynamicInstanceGraph
    source_relation_count: int
    source_observation_count: int
    observation_table_available: bool
    observation_error: str | None
    observation_load_status_counts: Mapping[str, int]
    relation_status_counts: Mapping[str, int]
    applications: Tuple[InlineObservationCandidateApplication, ...]
    policy_name: str = INLINE_OBSERVATION_CANDIDATE_POLICY_NAME
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
            application.application_status == "matched_applied"
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
            "source_observation_count": self.source_observation_count,
            "observation_table_available": self.observation_table_available,
            "observation_error": self.observation_error,
            "observation_load_status_counts": dict(
                self.observation_load_status_counts
            ),
            "relation_status_counts": dict(self.relation_status_counts),
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


def build_inline_observation_candidate_graph(
    graph: DynamicInstanceGraph,
    relation_report: InlineObservationRelationReport,
) -> InlineObservationCandidateGraphResult:
    """Apply exact completion references to an isolated graph view."""

    if relation_report.strict_effect != _DIAGNOSTIC_ONLY:
        raise ValueError(
            "Inline observation relations must remain diagnostic_only"
        )
    relation_keys = tuple(
        (relation.consumer_instance_id, relation.artifact_key)
        for relation in relation_report.relations
    )
    if len(set(relation_keys)) != len(relation_keys):
        raise ValueError(
            "Inline observation relation report has duplicate consumer artifacts"
        )

    source_matches = (
        relation_report.source_decision_count == graph.source_decision_count
        and relation_report.latest_instance_count == len(graph.nodes)
    )
    nodes_by_id = graph.nodes_by_instance_id
    candidate_edges = list(graph.edges)
    applications: list[InlineObservationCandidateApplication] = []

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
                    detail=(
                        "consumer relation does not match the latest graph node"
                    ),
                )
            )
            continue
        if relation.relation_status != "matched":
            applications.append(
                _application(
                    relation,
                    application_status="unsafe_relation_unchanged",
                    detail=(
                        f"relation status {relation.relation_status} "
                        "is not candidate evidence"
                    ),
                )
            )
            continue
        applications.append(
            _apply_matched_relation(
                relation,
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
    return InlineObservationCandidateGraphResult(
        source_graph=graph,
        graph=candidate_graph,
        source_relation_count=relation_report.relation_count,
        source_observation_count=relation_report.source_observation_count,
        observation_table_available=(
            relation_report.observation_table_available
        ),
        observation_error=relation_report.observation_error,
        observation_load_status_counts=(
            relation_report.observation_load_status_counts
        ),
        relation_status_counts=relation_report.relation_status_counts,
        applications=tuple(applications),
    )


def _apply_matched_relation(
    relation: InlineObservationRelation,
    *,
    candidate_edges: list[DynamicInstanceEdge],
) -> InlineObservationCandidateApplication:
    expected_producer_key = INLINE_OBSERVED_PRODUCER_BY_ARTIFACT.get(
        relation.artifact_key
    )
    if (
        relation.observation_id is None
        or not relation.observation_token
        or not relation.invocation_id
        or not relation.producer_instance_id
        or relation.producer_task_key is None
        or relation.producer_task_key != expected_producer_key
    ):
        return _application(
            relation,
            application_status="unsafe_relation_unchanged",
            detail=(
                "matched relation lacks the exact observation/task/artifact "
                "identity required by the candidate policy"
            ),
        )

    edge_indexes = tuple(
        index
        for index, edge in enumerate(candidate_edges)
        if edge.consumer_instance_id == relation.consumer_instance_id
        and edge.artifact_key == relation.artifact_key
    )
    if len(edge_indexes) != 1:
        return _application(
            relation,
            application_status="edge_unavailable_unchanged",
            detail=(
                "candidate requires exactly one declared inline input edge; "
                f"found {len(edge_indexes)}"
            ),
        )
    edge_index = edge_indexes[0]
    previous = candidate_edges[edge_index]
    if previous.kind != "producer_unobserved":
        return _application(
            relation,
            application_status="edge_unavailable_unchanged",
            edge_kind_before=previous.kind,
            detail=(
                "candidate replaces only a producer_unobserved inline "
                f"artifact edge, not {previous.kind}"
            ),
        )

    candidate_edges[edge_index] = DynamicInstanceEdge(
        artifact_key=previous.artifact_key,
        consumer_instance_id=previous.consumer_instance_id,
        consumer_key=previous.consumer_key,
        kind="inline_observed",
        producer_instance_id=relation.producer_instance_id,
        producer_task_keys=(relation.producer_task_key,),
        candidate_instance_ids=(relation.producer_instance_id,),
        matched_field_keys=(
            "observation_id",
            "observation_token",
            "invocation_id",
        ),
        detail=(
            f"candidate_policy={INLINE_OBSERVATION_CANDIDATE_POLICY_NAME}; "
            f"observation_id={relation.observation_id}; "
            f"observation_token={relation.observation_token}; "
            f"invocation_id={relation.invocation_id}; "
            "completion_boundary=true; "
            "source_strict_effect=diagnostic_only; artifact_health=separate"
        ),
    )
    return _application(
        relation,
        application_status="matched_applied",
        edge_kind_before=previous.kind,
        detail=(
            "replaced one producer_unobserved edge with exact inline "
            f"completion observation {relation.observation_id}"
        ),
    )


def _application(
    relation: InlineObservationRelation,
    *,
    application_status: InlineObservationCandidateApplicationStatus,
    edge_kind_before: DynamicInstanceEdgeKind | None = None,
    detail: str,
) -> InlineObservationCandidateApplication:
    return InlineObservationCandidateApplication(
        consumer_instance_id=relation.consumer_instance_id,
        consumer_decision_id=relation.consumer_decision_id,
        consumer_task_key=relation.consumer_task_key,
        artifact_key=relation.artifact_key,
        relation_status=relation.relation_status,
        application_status=application_status,
        observation_id=relation.observation_id,
        observation_token=relation.observation_token,
        producer_instance_id=relation.producer_instance_id,
        producer_task_key=relation.producer_task_key,
        edge_kind_before=edge_kind_before,
        detail=detail,
    )
