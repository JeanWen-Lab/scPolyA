"""Read-only Stage 12F promotion-readiness diagnostics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Tuple

from .branch_manifest_relations import (
    build_branch_diagnostic_relation_report_from_decisions,
)
from .coverage_branch_candidate import (
    COVERAGE_BRANCH_CANDIDATE_POLICY_NAME,
    CoverageBranchCandidateGraphResult,
    build_coverage_branch_candidate_graph,
)
from .contract_generation_candidate import (
    ContractGenerationCandidateGraphResult,
    build_active_contract_generation_candidate_graph,
)
from .contract_generation_lifecycle import (
    CONTRACT_GENERATION_LIFECYCLE_POLICY_NAME,
    build_contract_generation_lifecycle_report,
)
from .call_peaks_lineage_candidate import (
    CALL_PEAKS_LINEAGE_CANDIDATE_POLICY_NAME,
    CallPeaksLineageCandidateGraphResult,
    build_call_peaks_lineage_candidate_graph,
)
from .decision_input_report import (
    DecisionInputStatusReport,
    build_candidate_decision_input_status_report,
    build_decision_input_status_report,
)
from .instance_graph import (
    OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
    DynamicInstanceGraph,
    ObserveOnlyReusePlan,
    build_dynamic_instance_graph,
    build_observe_only_reuse_plan,
)
from .inline_observation_candidate import (
    INLINE_OBSERVATION_CANDIDATE_POLICY_NAME,
    InlineObservationCandidateGraphResult,
    build_inline_observation_candidate_graph,
)
from .inline_observation_relations import (
    build_inline_observation_relation_report_from_components,
    load_inline_execution_observations_v1,
)
from .pipeline_contracts import TaskKey
from .reuse_artifact_health import (
    REQUIRED_OUTPUT_HEALTH_POLICY_NAME,
    CurrentReuseArtifactHealthRelationReportV2,
    HealthQualifiedReusePlan,
    build_current_reuse_artifact_health_relation_report_v2,
    build_health_qualified_reuse_plan,
)
from .reuse_report import CurrentReuseDecisionSummaryV2
from .task_completion import (
    LEGACY_SCHEDULER_AUTHORITY_V1,
    TaskSchedulerAuthorityV1,
)


PromotionReadinessStatus = Literal[
    "no_observations",
    "blocked",
    "partial",
    "evidence_complete",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"
_WARNING_ONLY = "warning_only"
_LEGACY_SCHEDULER = LEGACY_SCHEDULER_AUTHORITY_V1


@dataclass(frozen=True)
class PromotionEvidenceSnapshot:
    """One immutable production composition shared by readiness and replay."""

    state_db_path: str
    baseline_graph: DynamicInstanceGraph
    baseline_plan: ObserveOnlyReusePlan
    operator_managed_plan: ObserveOnlyReusePlan
    contract_generation_candidate: ContractGenerationCandidateGraphResult
    contract_generation_plan: ObserveOnlyReusePlan
    operator_managed_contract_generation_plan: ObserveOnlyReusePlan
    coverage_branch_candidate: CoverageBranchCandidateGraphResult
    coverage_branch_plan: ObserveOnlyReusePlan
    operator_managed_coverage_branch_plan: ObserveOnlyReusePlan
    inline_observation_candidate: InlineObservationCandidateGraphResult
    inline_observation_plan: ObserveOnlyReusePlan
    operator_managed_inline_observation_plan: ObserveOnlyReusePlan
    call_peaks_lineage_candidate: CallPeaksLineageCandidateGraphResult
    call_peaks_lineage_plan: ObserveOnlyReusePlan
    dependency_plan: ObserveOnlyReusePlan
    artifact_health_relations: CurrentReuseArtifactHealthRelationReportV2
    artifact_health_plan: HealthQualifiedReusePlan
    input_report: DecisionInputStatusReport
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    @property
    def final_graph(self) -> DynamicInstanceGraph:
        return self.call_peaks_lineage_candidate.graph

    @property
    def retained_decisions(self) -> Tuple[CurrentReuseDecisionSummaryV2, ...]:
        return self.contract_generation_candidate.retained_decisions

    @property
    def evidence_policy_names(self) -> Tuple[str, ...]:
        return (
            OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY.name,
            self.contract_generation_candidate.policy_name,
            self.coverage_branch_candidate.policy_name,
            self.inline_observation_candidate.policy_name,
            self.call_peaks_lineage_candidate.policy_name,
            REQUIRED_OUTPUT_HEALTH_POLICY_NAME,
        )


@dataclass(frozen=True)
class PromotionTaskPolicyCandidate:
    action_counts: Mapping[str, int]
    strict_reuse_eligible_count: int
    eligible_delta: int
    blocker_class_counts: Mapping[str, int]
    blocker_signature_counts: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_counts": dict(self.action_counts),
            "strict_reuse_eligible_count": self.strict_reuse_eligible_count,
            "eligible_delta": self.eligible_delta,
            "blocker_class_counts": dict(self.blocker_class_counts),
            "blocker_signature_counts": dict(self.blocker_signature_counts),
        }


@dataclass(frozen=True)
class PromotionTaskReadiness:
    task_key: TaskKey
    instance_count: int
    action_counts: Mapping[str, int]
    strict_reuse_eligible_count: int
    blocker_class_counts: Mapping[str, int]
    blocker_signature_counts: Mapping[str, int]
    operator_warning_count: int
    operator_managed_external_candidate: PromotionTaskPolicyCandidate

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_key": {
                "stage": self.task_key[0],
                "task_base": self.task_key[1],
            },
            "instance_count": self.instance_count,
            "action_counts": dict(self.action_counts),
            "strict_reuse_eligible_count": self.strict_reuse_eligible_count,
            "blocker_class_counts": dict(self.blocker_class_counts),
            "blocker_signature_counts": dict(self.blocker_signature_counts),
            "operator_warning_count": self.operator_warning_count,
            "operator_managed_external_candidate": (
                self.operator_managed_external_candidate.as_dict()
            ),
        }


@dataclass(frozen=True)
class PromotionPolicyCandidate:
    policy_name: str
    readiness_status: PromotionReadinessStatus
    plan_entry_count: int
    action_counts: Mapping[str, int]
    strict_reuse_eligible_count: int
    eligible_delta: int
    blocker_class_counts: Mapping[str, int]
    blocker_signature_counts: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "readiness_status": self.readiness_status,
            "plan_entry_count": self.plan_entry_count,
            "action_counts": dict(self.action_counts),
            "strict_reuse_eligible_count": self.strict_reuse_eligible_count,
            "eligible_delta": self.eligible_delta,
            "blocker_class_counts": dict(self.blocker_class_counts),
            "blocker_signature_counts": dict(self.blocker_signature_counts),
        }


@dataclass(frozen=True)
class ContractGenerationPromotionCandidate:
    policy_name: str
    table_available: bool
    error: str | None
    source_decision_count: int
    retained_decision_count: int
    decision_load_status_counts: Mapping[str, int]
    logical_slot_count: int
    unresolved_logical_slot_count: int
    generation_count: int
    generation_status_counts: Mapping[str, int]
    application_status_counts: Mapping[str, int]
    excluded_generation_count: int
    excluded_decision_count: int
    source_node_count: int
    candidate_node_count: int
    node_count_delta: int
    source_edge_count: int
    candidate_edge_count: int
    edge_count_delta: int
    source_concrete_edge_count: int
    candidate_concrete_edge_count: int
    concrete_edge_count_delta: int
    source_blocking_edge_count: int
    candidate_blocking_edge_count: int
    blocking_edge_count_delta: int
    strict_plan: PromotionPolicyCandidate
    operator_managed_external_plan: PromotionPolicyCandidate
    incremental_eligible_delta_over_operator_managed_external: int
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "table_available": self.table_available,
            "error": self.error,
            "source_decision_count": self.source_decision_count,
            "retained_decision_count": self.retained_decision_count,
            "decision_load_status_counts": dict(
                self.decision_load_status_counts
            ),
            "logical_slot_count": self.logical_slot_count,
            "unresolved_logical_slot_count": (
                self.unresolved_logical_slot_count
            ),
            "generation_count": self.generation_count,
            "generation_status_counts": dict(
                self.generation_status_counts
            ),
            "application_status_counts": dict(
                self.application_status_counts
            ),
            "excluded_generation_count": self.excluded_generation_count,
            "excluded_decision_count": self.excluded_decision_count,
            "source_node_count": self.source_node_count,
            "candidate_node_count": self.candidate_node_count,
            "node_count_delta": self.node_count_delta,
            "source_edge_count": self.source_edge_count,
            "candidate_edge_count": self.candidate_edge_count,
            "edge_count_delta": self.edge_count_delta,
            "source_concrete_edge_count": self.source_concrete_edge_count,
            "candidate_concrete_edge_count": (
                self.candidate_concrete_edge_count
            ),
            "concrete_edge_count_delta": self.concrete_edge_count_delta,
            "source_blocking_edge_count": self.source_blocking_edge_count,
            "candidate_blocking_edge_count": (
                self.candidate_blocking_edge_count
            ),
            "blocking_edge_count_delta": self.blocking_edge_count_delta,
            "strict_plan": self.strict_plan.as_dict(),
            "operator_managed_external_plan": (
                self.operator_managed_external_plan.as_dict()
            ),
            "incremental_eligible_delta_over_operator_managed_external": (
                self.incremental_eligible_delta_over_operator_managed_external
            ),
        }


@dataclass(frozen=True)
class CoverageBranchPromotionCandidate:
    policy_name: str
    source_relation_count: int
    relation_status_counts: Mapping[str, int]
    application_status_counts: Mapping[str, int]
    applied_relation_count: int
    unchanged_relation_count: int
    source_edge_count: int
    candidate_edge_count: int
    edge_count_delta: int
    source_concrete_edge_count: int
    candidate_concrete_edge_count: int
    concrete_edge_count_delta: int
    source_blocking_edge_count: int
    candidate_blocking_edge_count: int
    blocking_edge_count_delta: int
    strict_plan: PromotionPolicyCandidate
    operator_managed_external_plan: PromotionPolicyCandidate
    incremental_eligible_delta_over_operator_managed_external: int
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "source_relation_count": self.source_relation_count,
            "relation_status_counts": dict(self.relation_status_counts),
            "application_status_counts": dict(self.application_status_counts),
            "applied_relation_count": self.applied_relation_count,
            "unchanged_relation_count": self.unchanged_relation_count,
            "source_edge_count": self.source_edge_count,
            "candidate_edge_count": self.candidate_edge_count,
            "edge_count_delta": self.edge_count_delta,
            "source_concrete_edge_count": self.source_concrete_edge_count,
            "candidate_concrete_edge_count": self.candidate_concrete_edge_count,
            "concrete_edge_count_delta": self.concrete_edge_count_delta,
            "source_blocking_edge_count": self.source_blocking_edge_count,
            "candidate_blocking_edge_count": self.candidate_blocking_edge_count,
            "blocking_edge_count_delta": self.blocking_edge_count_delta,
            "strict_plan": self.strict_plan.as_dict(),
            "operator_managed_external_plan": (
                self.operator_managed_external_plan.as_dict()
            ),
            "incremental_eligible_delta_over_operator_managed_external": (
                self.incremental_eligible_delta_over_operator_managed_external
            ),
        }


@dataclass(frozen=True)
class InlineObservationPromotionCandidate:
    policy_name: str
    source_observation_count: int
    observation_table_available: bool
    observation_error: str | None
    observation_load_status_counts: Mapping[str, int]
    source_relation_count: int
    relation_status_counts: Mapping[str, int]
    application_status_counts: Mapping[str, int]
    applied_relation_count: int
    unchanged_relation_count: int
    source_edge_count: int
    candidate_edge_count: int
    edge_count_delta: int
    source_concrete_edge_count: int
    candidate_concrete_edge_count: int
    concrete_edge_count_delta: int
    source_blocking_edge_count: int
    candidate_blocking_edge_count: int
    blocking_edge_count_delta: int
    strict_plan: PromotionPolicyCandidate
    operator_managed_external_plan: PromotionPolicyCandidate
    incremental_eligible_delta_over_prior_candidate: int
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "source_observation_count": self.source_observation_count,
            "observation_table_available": self.observation_table_available,
            "observation_error": self.observation_error,
            "observation_load_status_counts": dict(
                self.observation_load_status_counts
            ),
            "source_relation_count": self.source_relation_count,
            "relation_status_counts": dict(self.relation_status_counts),
            "application_status_counts": dict(
                self.application_status_counts
            ),
            "applied_relation_count": self.applied_relation_count,
            "unchanged_relation_count": self.unchanged_relation_count,
            "source_edge_count": self.source_edge_count,
            "candidate_edge_count": self.candidate_edge_count,
            "edge_count_delta": self.edge_count_delta,
            "source_concrete_edge_count": self.source_concrete_edge_count,
            "candidate_concrete_edge_count": (
                self.candidate_concrete_edge_count
            ),
            "concrete_edge_count_delta": self.concrete_edge_count_delta,
            "source_blocking_edge_count": self.source_blocking_edge_count,
            "candidate_blocking_edge_count": (
                self.candidate_blocking_edge_count
            ),
            "blocking_edge_count_delta": self.blocking_edge_count_delta,
            "strict_plan": self.strict_plan.as_dict(),
            "operator_managed_external_plan": (
                self.operator_managed_external_plan.as_dict()
            ),
            "incremental_eligible_delta_over_prior_candidate": (
                self.incremental_eligible_delta_over_prior_candidate
            ),
        }


@dataclass(frozen=True)
class CallPeaksLineagePromotionCandidate:
    policy_name: str
    application_status_counts: Mapping[str, int]
    applied_relation_count: int
    unchanged_relation_count: int
    source_edge_count: int
    candidate_edge_count: int
    edge_count_delta: int
    source_concrete_edge_count: int
    candidate_concrete_edge_count: int
    concrete_edge_count_delta: int
    source_blocking_edge_count: int
    candidate_blocking_edge_count: int
    blocking_edge_count_delta: int
    strict_plan: PromotionPolicyCandidate
    operator_managed_external_plan: PromotionPolicyCandidate
    incremental_eligible_delta_over_prior_candidate: int
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "application_status_counts": dict(
                self.application_status_counts
            ),
            "applied_relation_count": self.applied_relation_count,
            "unchanged_relation_count": self.unchanged_relation_count,
            "source_edge_count": self.source_edge_count,
            "candidate_edge_count": self.candidate_edge_count,
            "edge_count_delta": self.edge_count_delta,
            "source_concrete_edge_count": self.source_concrete_edge_count,
            "candidate_concrete_edge_count": (
                self.candidate_concrete_edge_count
            ),
            "concrete_edge_count_delta": self.concrete_edge_count_delta,
            "source_blocking_edge_count": self.source_blocking_edge_count,
            "candidate_blocking_edge_count": (
                self.candidate_blocking_edge_count
            ),
            "blocking_edge_count_delta": self.blocking_edge_count_delta,
            "strict_plan": self.strict_plan.as_dict(),
            "operator_managed_external_plan": (
                self.operator_managed_external_plan.as_dict()
            ),
            "incremental_eligible_delta_over_prior_candidate": (
                self.incremental_eligible_delta_over_prior_candidate
            ),
        }


@dataclass(frozen=True)
class ArtifactHealthPromotionCandidate:
    policy_name: str
    source_matches: bool
    config_available: bool
    config_error: str | None
    done_record_count: int
    relation_count: int
    relation_status_counts: Mapping[str, int]
    registered_scope_count: int
    ready_relation_count: int
    unhealthy_relation_count: int
    dependency_plan_entry_count: int
    dependency_strict_reuse_eligible_count: int
    action_counts: Mapping[str, int]
    health_status_counts: Mapping[str, int]
    strict_reuse_eligible_count: int
    eligible_delta_over_dependency_candidate: int
    blocker_class_counts: Mapping[str, int]
    blocker_signature_counts: Mapping[str, int]
    readiness_status: PromotionReadinessStatus
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "source_matches": self.source_matches,
            "config_available": self.config_available,
            "config_error": self.config_error,
            "done_record_count": self.done_record_count,
            "relation_count": self.relation_count,
            "relation_status_counts": dict(
                self.relation_status_counts
            ),
            "registered_scope_count": self.registered_scope_count,
            "ready_relation_count": self.ready_relation_count,
            "unhealthy_relation_count": self.unhealthy_relation_count,
            "dependency_plan_entry_count": (
                self.dependency_plan_entry_count
            ),
            "dependency_strict_reuse_eligible_count": (
                self.dependency_strict_reuse_eligible_count
            ),
            "action_counts": dict(self.action_counts),
            "health_status_counts": dict(self.health_status_counts),
            "strict_reuse_eligible_count": (
                self.strict_reuse_eligible_count
            ),
            "eligible_delta_over_dependency_candidate": (
                self.eligible_delta_over_dependency_candidate
            ),
            "blocker_class_counts": dict(self.blocker_class_counts),
            "blocker_signature_counts": dict(
                self.blocker_signature_counts
            ),
            "readiness_status": self.readiness_status,
        }


@dataclass(frozen=True)
class PromotionReadinessReport:
    state_db_path: str
    readiness_status: PromotionReadinessStatus
    source_decision_count: int
    node_count: int
    edge_count: int
    concrete_edge_count: int
    blocking_edge_count: int
    edge_kind_counts: Mapping[str, int]
    plan_entry_count: int
    action_counts: Mapping[str, int]
    strict_reuse_eligible_count: int
    blocker_class_counts: Mapping[str, int]
    blocker_signature_counts: Mapping[str, int]
    input_config_available: bool
    input_config_error: str | None
    input_manifest_availability_counts: Mapping[str, int]
    input_comparison_status_counts: Mapping[str, int]
    operator_warning_count: int
    operator_warning_task_counts: Mapping[str, int]
    operator_managed_external_candidate: PromotionPolicyCandidate
    contract_generation_candidate: (
        ContractGenerationPromotionCandidate | None
    )
    coverage_branch_candidate: CoverageBranchPromotionCandidate | None
    inline_observation_candidate: InlineObservationPromotionCandidate | None
    call_peaks_lineage_candidate: (
        CallPeaksLineagePromotionCandidate | None
    )
    artifact_health_candidate: ArtifactHealthPromotionCandidate | None
    tasks: Tuple[PromotionTaskReadiness, ...]
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY
    input_change_effect: Literal["warning_only"] = _WARNING_ONLY
    scheduler_authority: TaskSchedulerAuthorityV1 = _LEGACY_SCHEDULER
    runtime_promotion_allowed: Literal[False] = False

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        tasks = self.tasks if limit is None else self.tasks[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "readiness_status": self.readiness_status,
            "strict_effect": self.strict_effect,
            "input_change_effect": self.input_change_effect,
            "scheduler_authority": self.scheduler_authority,
            "runtime_promotion_allowed": self.runtime_promotion_allowed,
            "source_decision_count": self.source_decision_count,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "concrete_edge_count": self.concrete_edge_count,
            "blocking_edge_count": self.blocking_edge_count,
            "edge_kind_counts": dict(self.edge_kind_counts),
            "plan_entry_count": self.plan_entry_count,
            "action_counts": dict(self.action_counts),
            "strict_reuse_eligible_count": self.strict_reuse_eligible_count,
            "blocker_class_counts": dict(self.blocker_class_counts),
            "blocker_signature_counts": dict(self.blocker_signature_counts),
            "input_config_available": self.input_config_available,
            "input_config_error": self.input_config_error,
            "input_manifest_availability_counts": dict(
                self.input_manifest_availability_counts
            ),
            "input_comparison_status_counts": dict(
                self.input_comparison_status_counts
            ),
            "operator_warning_count": self.operator_warning_count,
            "operator_warning_task_counts": dict(self.operator_warning_task_counts),
            "operator_managed_external_candidate": (
                self.operator_managed_external_candidate.as_dict()
            ),
            "contract_generation_candidate": (
                None
                if self.contract_generation_candidate is None
                else self.contract_generation_candidate.as_dict()
            ),
            "coverage_branch_candidate": (
                None
                if self.coverage_branch_candidate is None
                else self.coverage_branch_candidate.as_dict()
            ),
            "inline_observation_candidate": (
                None
                if self.inline_observation_candidate is None
                else self.inline_observation_candidate.as_dict()
            ),
            "call_peaks_lineage_candidate": (
                None
                if self.call_peaks_lineage_candidate is None
                else self.call_peaks_lineage_candidate.as_dict()
            ),
            "artifact_health_candidate": (
                None
                if self.artifact_health_candidate is None
                else self.artifact_health_candidate.as_dict()
            ),
            "task_count": len(self.tasks),
            "tasks": [task.as_dict() for task in tasks],
        }


def build_promotion_readiness_report(
    state_or_db_path: str | Path,
) -> PromotionReadinessReport:
    """Compose existing read-only diagnostics without promoting scheduler state."""

    snapshot = build_promotion_evidence_snapshot(state_or_db_path)
    return build_promotion_readiness_report_from_components(
        snapshot.baseline_graph,
        snapshot.baseline_plan,
        snapshot.input_report,
        operator_managed_external_plan=snapshot.operator_managed_plan,
        contract_generation_candidate=(
            snapshot.contract_generation_candidate
        ),
        contract_generation_plan=snapshot.contract_generation_plan,
        operator_managed_external_contract_generation_plan=(
            snapshot.operator_managed_contract_generation_plan
        ),
        coverage_branch_candidate=snapshot.coverage_branch_candidate,
        coverage_branch_plan=snapshot.coverage_branch_plan,
        operator_managed_external_coverage_branch_plan=(
            snapshot.operator_managed_coverage_branch_plan
        ),
        inline_observation_candidate=snapshot.inline_observation_candidate,
        inline_observation_plan=snapshot.inline_observation_plan,
        operator_managed_external_inline_observation_plan=(
            snapshot.operator_managed_inline_observation_plan
        ),
        call_peaks_lineage_candidate=(
            snapshot.call_peaks_lineage_candidate
        ),
        call_peaks_lineage_plan=snapshot.call_peaks_lineage_plan,
        operator_managed_external_call_peaks_lineage_plan=(
            snapshot.dependency_plan
        ),
        artifact_health_relations=snapshot.artifact_health_relations,
        artifact_health_plan=snapshot.artifact_health_plan,
        state_db_path=str(state_or_db_path),
    )


def build_promotion_evidence_snapshot(
    state_or_db_path: str | Path,
) -> PromotionEvidenceSnapshot:
    """Build the exact read-only evidence chain once for downstream reports."""

    graph = build_dynamic_instance_graph(state_or_db_path)
    plan = build_observe_only_reuse_plan(graph)
    operator_managed_plan = build_observe_only_reuse_plan(
        graph,
        policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
    )
    generation_lifecycle = build_contract_generation_lifecycle_report(
        state_or_db_path
    )
    generation_candidate = (
        build_active_contract_generation_candidate_graph(
            state_or_db_path,
            graph,
            lifecycle_report=generation_lifecycle,
        )
    )
    generation_candidate_plan = build_observe_only_reuse_plan(
        generation_candidate.graph,
    )
    combined_generation_candidate_plan = build_observe_only_reuse_plan(
        generation_candidate.graph,
        policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
    )
    branch_relations = (
        build_branch_diagnostic_relation_report_from_decisions(
            generation_candidate.retained_decisions,
            state_db_path=generation_candidate.graph.state_db_path,
        )
    )
    branch_candidate = build_coverage_branch_candidate_graph(
        generation_candidate.graph,
        branch_relations,
    )
    branch_candidate_plan = build_observe_only_reuse_plan(
        branch_candidate.graph,
    )
    combined_candidate_plan = build_observe_only_reuse_plan(
        branch_candidate.graph,
        policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
    )
    inline_relations = build_inline_observation_relation_report_from_components(
        generation_candidate.retained_decisions,
        load_inline_execution_observations_v1(state_or_db_path),
        state_db_path=generation_candidate.graph.state_db_path,
    )
    inline_candidate = build_inline_observation_candidate_graph(
        branch_candidate.graph,
        inline_relations,
    )
    inline_candidate_plan = build_observe_only_reuse_plan(
        inline_candidate.graph,
    )
    final_combined_candidate_plan = build_observe_only_reuse_plan(
        inline_candidate.graph,
        policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
    )
    call_peaks_candidate = build_call_peaks_lineage_candidate_graph(
        inline_candidate.graph
    )
    call_peaks_candidate_plan = build_observe_only_reuse_plan(
        call_peaks_candidate.graph,
    )
    final_lineage_combined_plan = build_observe_only_reuse_plan(
        call_peaks_candidate.graph,
        policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
    )
    full_lineage_policy_name = "+".join(
        (
            OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY.name,
            generation_candidate.policy_name,
            branch_candidate.policy_name,
            inline_candidate.policy_name,
            call_peaks_candidate.policy_name,
        )
    )
    artifact_health_relations = (
        build_current_reuse_artifact_health_relation_report_v2(
            state_or_db_path,
            call_peaks_candidate.graph,
            generation_candidate.retained_decisions,
        )
    )
    artifact_health_plan = build_health_qualified_reuse_plan(
        final_lineage_combined_plan,
        artifact_health_relations,
        dependency_policy_name=full_lineage_policy_name,
    )
    # Same decision set as every candidate stage above: excluded history is
    # counted by reason, never silently dropped or compared against the graph.
    input_report = build_candidate_decision_input_status_report(
        state_or_db_path,
        source_decisions=generation_candidate.source_decisions,
        retained_decisions=generation_candidate.retained_decisions,
        excluded_decision_reasons=generation_candidate.excluded_decision_reasons,
    )
    return PromotionEvidenceSnapshot(
        state_db_path=graph.state_db_path,
        baseline_graph=graph,
        baseline_plan=plan,
        operator_managed_plan=operator_managed_plan,
        contract_generation_candidate=generation_candidate,
        contract_generation_plan=generation_candidate_plan,
        operator_managed_contract_generation_plan=(
            combined_generation_candidate_plan
        ),
        coverage_branch_candidate=branch_candidate,
        coverage_branch_plan=branch_candidate_plan,
        operator_managed_coverage_branch_plan=combined_candidate_plan,
        inline_observation_candidate=inline_candidate,
        inline_observation_plan=inline_candidate_plan,
        operator_managed_inline_observation_plan=(
            final_combined_candidate_plan
        ),
        call_peaks_lineage_candidate=call_peaks_candidate,
        call_peaks_lineage_plan=call_peaks_candidate_plan,
        dependency_plan=final_lineage_combined_plan,
        artifact_health_relations=artifact_health_relations,
        artifact_health_plan=artifact_health_plan,
        input_report=input_report,
    )


def build_promotion_readiness_report_from_components(
    graph: DynamicInstanceGraph,
    plan: ObserveOnlyReusePlan,
    input_report: DecisionInputStatusReport,
    *,
    operator_managed_external_plan: ObserveOnlyReusePlan | None = None,
    contract_generation_candidate: (
        ContractGenerationCandidateGraphResult | None
    ) = None,
    contract_generation_plan: ObserveOnlyReusePlan | None = None,
    operator_managed_external_contract_generation_plan: (
        ObserveOnlyReusePlan | None
    ) = None,
    coverage_branch_candidate: CoverageBranchCandidateGraphResult | None = None,
    coverage_branch_plan: ObserveOnlyReusePlan | None = None,
    operator_managed_external_coverage_branch_plan: ObserveOnlyReusePlan | None = None,
    inline_observation_candidate: InlineObservationCandidateGraphResult | None = None,
    inline_observation_plan: ObserveOnlyReusePlan | None = None,
    operator_managed_external_inline_observation_plan: ObserveOnlyReusePlan | None = None,
    call_peaks_lineage_candidate: (
        CallPeaksLineageCandidateGraphResult | None
    ) = None,
    call_peaks_lineage_plan: ObserveOnlyReusePlan | None = None,
    operator_managed_external_call_peaks_lineage_plan: (
        ObserveOnlyReusePlan | None
    ) = None,
    artifact_health_relations: (
        CurrentReuseArtifactHealthRelationReportV2 | None
    ) = None,
    artifact_health_plan: HealthQualifiedReusePlan | None = None,
    state_db_path: str | None = None,
) -> PromotionReadinessReport:
    """Aggregate strict blockers and operator warnings as separate evidence."""

    if (
        artifact_health_relations is not None
        and not isinstance(
            artifact_health_relations,
            CurrentReuseArtifactHealthRelationReportV2,
        )
    ):
        raise TypeError(
            "Current promotion readiness requires "
            "CurrentReuseArtifactHealthRelationReportV2"
        )
    nodes_by_id = graph.nodes_by_instance_id
    plan_by_id = {entry.instance_id: entry for entry in plan.entries}
    if len(nodes_by_id) != len(graph.nodes):
        raise ValueError("Dynamic instance graph contains duplicate instance IDs")
    if len(plan_by_id) != len(plan.entries):
        raise ValueError("Observe-only plan contains duplicate instance IDs")
    if set(nodes_by_id) != set(plan_by_id):
        raise ValueError("Observe-only plan instances do not match the dynamic graph")
    candidate_plan = operator_managed_external_plan or build_observe_only_reuse_plan(
        graph,
        policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
    )
    candidate_by_id = {entry.instance_id: entry for entry in candidate_plan.entries}
    if len(candidate_by_id) != len(candidate_plan.entries):
        raise ValueError("Operator-managed plan contains duplicate instance IDs")
    if set(nodes_by_id) != set(candidate_by_id):
        raise ValueError("Operator-managed plan instances do not match the dynamic graph")
    generation_candidate_summary: (
        ContractGenerationPromotionCandidate | None
    ) = None
    generation_graph = graph
    combined_generation_plan = candidate_plan
    generation_policy_prefix = ""
    if contract_generation_candidate is not None:
        if (
            contract_generation_candidate.policy_name
            != CONTRACT_GENERATION_LIFECYCLE_POLICY_NAME
        ):
            raise ValueError(
                "Contract generation candidate uses an unsupported policy name"
            )
        if contract_generation_candidate.source_graph != graph:
            raise ValueError(
                "Contract generation candidate source does not match the "
                "dynamic graph"
            )
        generation_graph = contract_generation_candidate.graph
        generation_plan = (
            contract_generation_plan
            or build_observe_only_reuse_plan(generation_graph)
        )
        combined_generation_plan = (
            operator_managed_external_contract_generation_plan
            or build_observe_only_reuse_plan(
                generation_graph,
                policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
            )
        )
        _require_plan_instances(
            generation_graph,
            generation_plan,
            label="Contract generation",
        )
        _require_plan_instances(
            generation_graph,
            combined_generation_plan,
            label="Operator-managed external plus contract generation",
        )
        source_generation_graph_report = graph.report()
        candidate_generation_graph_report = generation_graph.report()
        lifecycle = contract_generation_candidate.lifecycle_report
        generation_candidate_summary = ContractGenerationPromotionCandidate(
            policy_name=contract_generation_candidate.policy_name,
            table_available=lifecycle.table_available,
            error=lifecycle.error,
            source_decision_count=lifecycle.source_decision_count,
            retained_decision_count=len(
                contract_generation_candidate.retained_decisions
            ),
            decision_load_status_counts=dict(
                lifecycle.decision_load_status_counts
            ),
            logical_slot_count=lifecycle.logical_slot_count,
            unresolved_logical_slot_count=(
                lifecycle.unresolved_logical_slot_count
            ),
            generation_count=lifecycle.generation_count,
            generation_status_counts=lifecycle.generation_status_counts,
            application_status_counts=(
                contract_generation_candidate.application_status_counts
            ),
            excluded_generation_count=(
                contract_generation_candidate.excluded_generation_count
            ),
            excluded_decision_count=(
                contract_generation_candidate.excluded_decision_count
            ),
            source_node_count=source_generation_graph_report.node_count,
            candidate_node_count=(
                candidate_generation_graph_report.node_count
            ),
            node_count_delta=(
                candidate_generation_graph_report.node_count
                - source_generation_graph_report.node_count
            ),
            source_edge_count=source_generation_graph_report.edge_count,
            candidate_edge_count=(
                candidate_generation_graph_report.edge_count
            ),
            edge_count_delta=(
                candidate_generation_graph_report.edge_count
                - source_generation_graph_report.edge_count
            ),
            source_concrete_edge_count=(
                source_generation_graph_report.concrete_edge_count
            ),
            candidate_concrete_edge_count=(
                candidate_generation_graph_report.concrete_edge_count
            ),
            concrete_edge_count_delta=(
                candidate_generation_graph_report.concrete_edge_count
                - source_generation_graph_report.concrete_edge_count
            ),
            source_blocking_edge_count=(
                source_generation_graph_report.blocking_edge_count
            ),
            candidate_blocking_edge_count=(
                candidate_generation_graph_report.blocking_edge_count
            ),
            blocking_edge_count_delta=(
                candidate_generation_graph_report.blocking_edge_count
                - source_generation_graph_report.blocking_edge_count
            ),
            strict_plan=_promotion_policy_candidate(
                generation_plan,
                baseline_plan=plan,
                policy_name=contract_generation_candidate.policy_name,
            ),
            operator_managed_external_plan=_promotion_policy_candidate(
                combined_generation_plan,
                baseline_plan=plan,
                policy_name=(
                    f"{OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY.name}+"
                    f"{contract_generation_candidate.policy_name}"
                ),
            ),
            incremental_eligible_delta_over_operator_managed_external=(
                combined_generation_plan.strict_reuse_eligible_count
                - candidate_plan.strict_reuse_eligible_count
            ),
        )
        generation_policy_prefix = (
            f"{contract_generation_candidate.policy_name}+"
        )
    elif (
        contract_generation_plan is not None
        or operator_managed_external_contract_generation_plan is not None
    ):
        raise ValueError(
            "Contract generation plans require a contract generation "
            "candidate graph"
        )
    branch_candidate_summary: CoverageBranchPromotionCandidate | None = None
    branch_graph = generation_graph
    combined_branch_plan = combined_generation_plan
    if coverage_branch_candidate is not None:
        if (
            coverage_branch_candidate.policy_name
            != COVERAGE_BRANCH_CANDIDATE_POLICY_NAME
        ):
            raise ValueError(
                "Coverage branch candidate uses an unsupported policy name"
            )
        if coverage_branch_candidate.source_graph != generation_graph:
            raise ValueError(
                "Coverage branch candidate source does not match the "
                "preceding candidate graph"
            )
        branch_graph = coverage_branch_candidate.graph
        branch_plan = coverage_branch_plan or build_observe_only_reuse_plan(
            branch_graph
        )
        combined_branch_plan = (
            operator_managed_external_coverage_branch_plan
            or build_observe_only_reuse_plan(
                branch_graph,
                policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
            )
        )
        _require_plan_instances(
            branch_graph,
            branch_plan,
            label="Coverage branch",
        )
        _require_plan_instances(
            branch_graph,
            combined_branch_plan,
            label="Operator-managed external plus coverage branch",
        )
        source_graph_report = generation_graph.report()
        candidate_graph_report = branch_graph.report()
        branch_candidate_summary = CoverageBranchPromotionCandidate(
            policy_name=coverage_branch_candidate.policy_name,
            source_relation_count=coverage_branch_candidate.source_relation_count,
            relation_status_counts=dict(
                coverage_branch_candidate.relation_status_counts
            ),
            application_status_counts=(
                coverage_branch_candidate.application_status_counts
            ),
            applied_relation_count=(
                coverage_branch_candidate.applied_relation_count
            ),
            unchanged_relation_count=(
                coverage_branch_candidate.unchanged_relation_count
            ),
            source_edge_count=source_graph_report.edge_count,
            candidate_edge_count=candidate_graph_report.edge_count,
            edge_count_delta=(
                candidate_graph_report.edge_count - source_graph_report.edge_count
            ),
            source_concrete_edge_count=source_graph_report.concrete_edge_count,
            candidate_concrete_edge_count=(
                candidate_graph_report.concrete_edge_count
            ),
            concrete_edge_count_delta=(
                candidate_graph_report.concrete_edge_count
                - source_graph_report.concrete_edge_count
            ),
            source_blocking_edge_count=source_graph_report.blocking_edge_count,
            candidate_blocking_edge_count=(
                candidate_graph_report.blocking_edge_count
            ),
            blocking_edge_count_delta=(
                candidate_graph_report.blocking_edge_count
                - source_graph_report.blocking_edge_count
            ),
            strict_plan=_promotion_policy_candidate(
                branch_plan,
                baseline_plan=plan,
                policy_name=(
                    f"{generation_policy_prefix}"
                    f"{coverage_branch_candidate.policy_name}"
                ),
            ),
            operator_managed_external_plan=_promotion_policy_candidate(
                combined_branch_plan,
                baseline_plan=plan,
                policy_name=(
                    f"{OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY.name}+"
                    f"{generation_policy_prefix}"
                    f"{coverage_branch_candidate.policy_name}"
                ),
            ),
            incremental_eligible_delta_over_operator_managed_external=(
                combined_branch_plan.strict_reuse_eligible_count
                - combined_generation_plan.strict_reuse_eligible_count
            ),
        )
    elif (
        coverage_branch_plan is not None
        or operator_managed_external_coverage_branch_plan is not None
    ):
        raise ValueError(
            "Coverage branch plans require a coverage branch candidate graph"
        )
    inline_candidate_summary: InlineObservationPromotionCandidate | None = None
    lineage_source_graph = branch_graph
    combined_inline_plan = combined_branch_plan
    if inline_observation_candidate is not None:
        if (
            inline_observation_candidate.policy_name
            != INLINE_OBSERVATION_CANDIDATE_POLICY_NAME
        ):
            raise ValueError(
                "Inline observation candidate uses an unsupported policy name"
            )
        if inline_observation_candidate.source_graph != branch_graph:
            raise ValueError(
                "Inline observation candidate source does not match the "
                "preceding candidate graph"
            )
        inline_graph = inline_observation_candidate.graph
        inline_plan = (
            inline_observation_plan
            or build_observe_only_reuse_plan(inline_graph)
        )
        final_combined_plan = (
            operator_managed_external_inline_observation_plan
            or build_observe_only_reuse_plan(
                inline_graph,
                policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
            )
        )
        lineage_source_graph = inline_graph
        combined_inline_plan = final_combined_plan
        _require_plan_instances(
            inline_graph,
            inline_plan,
            label="Inline observation",
        )
        _require_plan_instances(
            inline_graph,
            final_combined_plan,
            label=(
                "Operator-managed external plus coverage branch plus "
                "inline observation"
            ),
        )
        source_inline_graph_report = branch_graph.report()
        candidate_inline_graph_report = inline_graph.report()
        inline_policy_name = (
            f"{generation_policy_prefix}"
            f"{inline_observation_candidate.policy_name}"
        )
        if coverage_branch_candidate is not None:
            inline_policy_name = (
                f"{generation_policy_prefix}"
                f"{coverage_branch_candidate.policy_name}+"
                f"{inline_observation_candidate.policy_name}"
            )
        inline_candidate_summary = InlineObservationPromotionCandidate(
            policy_name=inline_observation_candidate.policy_name,
            source_observation_count=(
                inline_observation_candidate.source_observation_count
            ),
            observation_table_available=(
                inline_observation_candidate.observation_table_available
            ),
            observation_error=(
                inline_observation_candidate.observation_error
            ),
            observation_load_status_counts=dict(
                inline_observation_candidate.observation_load_status_counts
            ),
            source_relation_count=(
                inline_observation_candidate.source_relation_count
            ),
            relation_status_counts=dict(
                inline_observation_candidate.relation_status_counts
            ),
            application_status_counts=(
                inline_observation_candidate.application_status_counts
            ),
            applied_relation_count=(
                inline_observation_candidate.applied_relation_count
            ),
            unchanged_relation_count=(
                inline_observation_candidate.unchanged_relation_count
            ),
            source_edge_count=source_inline_graph_report.edge_count,
            candidate_edge_count=candidate_inline_graph_report.edge_count,
            edge_count_delta=(
                candidate_inline_graph_report.edge_count
                - source_inline_graph_report.edge_count
            ),
            source_concrete_edge_count=(
                source_inline_graph_report.concrete_edge_count
            ),
            candidate_concrete_edge_count=(
                candidate_inline_graph_report.concrete_edge_count
            ),
            concrete_edge_count_delta=(
                candidate_inline_graph_report.concrete_edge_count
                - source_inline_graph_report.concrete_edge_count
            ),
            source_blocking_edge_count=(
                source_inline_graph_report.blocking_edge_count
            ),
            candidate_blocking_edge_count=(
                candidate_inline_graph_report.blocking_edge_count
            ),
            blocking_edge_count_delta=(
                candidate_inline_graph_report.blocking_edge_count
                - source_inline_graph_report.blocking_edge_count
            ),
            strict_plan=_promotion_policy_candidate(
                inline_plan,
                baseline_plan=plan,
                policy_name=inline_policy_name,
            ),
            operator_managed_external_plan=_promotion_policy_candidate(
                final_combined_plan,
                baseline_plan=plan,
                policy_name=(
                    f"{OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY.name}+"
                    f"{inline_policy_name}"
                ),
            ),
            incremental_eligible_delta_over_prior_candidate=(
                final_combined_plan.strict_reuse_eligible_count
                - combined_branch_plan.strict_reuse_eligible_count
            ),
        )
    elif (
        inline_observation_plan is not None
        or operator_managed_external_inline_observation_plan is not None
    ):
        raise ValueError(
            "Inline observation plans require an inline candidate graph"
        )
    call_peaks_candidate_summary: (
        CallPeaksLineagePromotionCandidate | None
    ) = None
    health_source_graph = lineage_source_graph
    health_dependency_plan = combined_inline_plan
    if call_peaks_lineage_candidate is not None:
        if (
            call_peaks_lineage_candidate.policy_name
            != CALL_PEAKS_LINEAGE_CANDIDATE_POLICY_NAME
        ):
            raise ValueError(
                "Call-peaks lineage candidate uses an unsupported policy name"
            )
        if call_peaks_lineage_candidate.source_graph != lineage_source_graph:
            raise ValueError(
                "Call-peaks lineage candidate source does not match the "
                "preceding candidate graph"
            )
        call_peaks_graph = call_peaks_lineage_candidate.graph
        call_peaks_plan = (
            call_peaks_lineage_plan
            or build_observe_only_reuse_plan(call_peaks_graph)
        )
        final_lineage_plan = (
            operator_managed_external_call_peaks_lineage_plan
            or build_observe_only_reuse_plan(
                call_peaks_graph,
                policy=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY,
            )
        )
        health_source_graph = call_peaks_graph
        health_dependency_plan = final_lineage_plan
        _require_plan_instances(
            call_peaks_graph,
            call_peaks_plan,
            label="Call-peaks lineage",
        )
        _require_plan_instances(
            call_peaks_graph,
            final_lineage_plan,
            label=(
                "Operator-managed external plus contract generation plus "
                "coverage plus inline plus call-peaks lineage"
            ),
        )
        source_call_peaks_report = lineage_source_graph.report()
        candidate_call_peaks_report = call_peaks_graph.report()
        policy_parts = []
        if contract_generation_candidate is not None:
            policy_parts.append(contract_generation_candidate.policy_name)
        if coverage_branch_candidate is not None:
            policy_parts.append(coverage_branch_candidate.policy_name)
        if inline_observation_candidate is not None:
            policy_parts.append(inline_observation_candidate.policy_name)
        policy_parts.append(call_peaks_lineage_candidate.policy_name)
        composed_call_peaks_policy = "+".join(policy_parts)
        call_peaks_candidate_summary = (
            CallPeaksLineagePromotionCandidate(
                policy_name=call_peaks_lineage_candidate.policy_name,
                application_status_counts=(
                    call_peaks_lineage_candidate.application_status_counts
                ),
                applied_relation_count=(
                    call_peaks_lineage_candidate.applied_relation_count
                ),
                unchanged_relation_count=(
                    call_peaks_lineage_candidate.unchanged_relation_count
                ),
                source_edge_count=source_call_peaks_report.edge_count,
                candidate_edge_count=(
                    candidate_call_peaks_report.edge_count
                ),
                edge_count_delta=(
                    candidate_call_peaks_report.edge_count
                    - source_call_peaks_report.edge_count
                ),
                source_concrete_edge_count=(
                    source_call_peaks_report.concrete_edge_count
                ),
                candidate_concrete_edge_count=(
                    candidate_call_peaks_report.concrete_edge_count
                ),
                concrete_edge_count_delta=(
                    candidate_call_peaks_report.concrete_edge_count
                    - source_call_peaks_report.concrete_edge_count
                ),
                source_blocking_edge_count=(
                    source_call_peaks_report.blocking_edge_count
                ),
                candidate_blocking_edge_count=(
                    candidate_call_peaks_report.blocking_edge_count
                ),
                blocking_edge_count_delta=(
                    candidate_call_peaks_report.blocking_edge_count
                    - source_call_peaks_report.blocking_edge_count
                ),
                strict_plan=_promotion_policy_candidate(
                    call_peaks_plan,
                    baseline_plan=plan,
                    policy_name=composed_call_peaks_policy,
                ),
                operator_managed_external_plan=(
                    _promotion_policy_candidate(
                        final_lineage_plan,
                        baseline_plan=plan,
                        policy_name=(
                            f"{OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY.name}+"
                            f"{composed_call_peaks_policy}"
                        ),
                    )
                ),
                incremental_eligible_delta_over_prior_candidate=(
                    final_lineage_plan.strict_reuse_eligible_count
                    - combined_inline_plan.strict_reuse_eligible_count
                ),
            )
        )
    elif (
        call_peaks_lineage_plan is not None
        or operator_managed_external_call_peaks_lineage_plan is not None
    ):
        raise ValueError(
            "Call-peaks lineage plans require a call-peaks candidate graph"
        )
    artifact_health_candidate_summary: (
        ArtifactHealthPromotionCandidate | None
    ) = None
    if artifact_health_relations is not None and artifact_health_plan is not None:
        health_node_ids = set(health_source_graph.nodes_by_instance_id)
        relation_ids = [
            relation.instance_id
            for relation in artifact_health_relations.relations
        ]
        plan_ids = [
            entry.instance_id for entry in artifact_health_plan.entries
        ]
        if (
            len(set(relation_ids)) != len(relation_ids)
            or set(relation_ids) != health_node_ids
        ):
            raise ValueError(
                "Artifact-health relations do not match the final "
                "candidate graph"
            )
        if (
            len(set(plan_ids)) != len(plan_ids)
            or set(plan_ids) != health_node_ids
        ):
            raise ValueError(
                "Artifact-health plan does not match the final candidate graph"
            )
        if len(health_dependency_plan.entries) != len(plan_ids):
            raise ValueError(
                "Artifact-health dependency plan entry count changed"
            )
        health_blocker_classes: Counter[str] = Counter()
        health_blocker_signatures: Counter[str] = Counter()
        for entry in artifact_health_plan.entries:
            for blocker in entry.blockers:
                health_blocker_classes[_blocker_class(blocker)] += 1
                health_blocker_signatures[
                    _blocker_signature(blocker)
                ] += 1
        artifact_health_candidate_summary = ArtifactHealthPromotionCandidate(
            policy_name=artifact_health_plan.policy_name,
            source_matches=artifact_health_relations.source_matches,
            config_available=artifact_health_relations.config_available,
            config_error=artifact_health_relations.config_error,
            done_record_count=artifact_health_relations.done_record_count,
            relation_count=artifact_health_relations.relation_count,
            relation_status_counts=artifact_health_relations.status_counts,
            registered_scope_count=(
                artifact_health_relations.registered_scope_count
            ),
            ready_relation_count=artifact_health_relations.ready_count,
            unhealthy_relation_count=(
                artifact_health_relations.unhealthy_count
            ),
            dependency_plan_entry_count=len(
                health_dependency_plan.entries
            ),
            dependency_strict_reuse_eligible_count=(
                health_dependency_plan.strict_reuse_eligible_count
            ),
            action_counts=artifact_health_plan.action_counts,
            health_status_counts=(
                artifact_health_plan.health_status_counts
            ),
            strict_reuse_eligible_count=(
                artifact_health_plan.strict_reuse_eligible_count
            ),
            eligible_delta_over_dependency_candidate=(
                artifact_health_plan.strict_reuse_eligible_count
                - health_dependency_plan.strict_reuse_eligible_count
            ),
            blocker_class_counts=_sorted_counts(
                health_blocker_classes
            ),
            blocker_signature_counts=_sorted_counts(
                health_blocker_signatures
            ),
            readiness_status=_health_readiness_status(
                artifact_health_plan
            ),
        )
    elif (
        artifact_health_relations is not None
        or artifact_health_plan is not None
    ):
        raise ValueError(
            "Artifact-health relations and plan must be supplied together"
        )
    unknown_input_ids = {
        entry.instance_id
        for entry in input_report.entries
        if entry.instance_id not in nodes_by_id
    }
    if unknown_input_ids:
        raise ValueError(
            "Decision-input status instances are absent from the dynamic graph: "
            + ", ".join(sorted(unknown_input_ids))
        )

    task_rows: dict[TaskKey, dict[str, Any]] = {}

    def task_row(task_key: TaskKey) -> dict[str, Any]:
        return task_rows.setdefault(
            task_key,
            {
                "instance_count": 0,
                "actions": Counter(),
                "eligible": 0,
                "blocker_classes": Counter(),
                "blocker_signatures": Counter(),
                "candidate_actions": Counter(),
                "candidate_eligible": 0,
                "candidate_blocker_classes": Counter(),
                "candidate_blocker_signatures": Counter(),
                "warnings": 0,
            },
        )

    blocker_classes: Counter[str] = Counter()
    blocker_signatures: Counter[str] = Counter()
    for entry in plan.entries:
        node = nodes_by_id[entry.instance_id]
        row = task_row(node.key)
        row["instance_count"] += 1
        row["actions"][entry.action] += 1
        row["eligible"] += int(entry.strict_reuse_eligible)
        for blocker in entry.blockers:
            blocker_class = _blocker_class(blocker)
            blocker_signature = _blocker_signature(blocker)
            blocker_classes[blocker_class] += 1
            blocker_signatures[blocker_signature] += 1
            row["blocker_classes"][blocker_class] += 1
            row["blocker_signatures"][blocker_signature] += 1

    candidate_blocker_classes: Counter[str] = Counter()
    candidate_blocker_signatures: Counter[str] = Counter()
    for entry in candidate_plan.entries:
        node = nodes_by_id[entry.instance_id]
        row = task_row(node.key)
        row["candidate_actions"][entry.action] += 1
        row["candidate_eligible"] += int(entry.strict_reuse_eligible)
        for blocker in entry.blockers:
            blocker_class = _blocker_class(blocker)
            blocker_signature = _blocker_signature(blocker)
            candidate_blocker_classes[blocker_class] += 1
            candidate_blocker_signatures[blocker_signature] += 1
            row["candidate_blocker_classes"][blocker_class] += 1
            row["candidate_blocker_signatures"][blocker_signature] += 1

    warning_task_counts: Counter[str] = Counter()
    for entry in input_report.entries:
        if not entry.warning:
            continue
        task_row(entry.task_key)["warnings"] += 1
        warning_task_counts[_task_label(entry.task_key)] += 1

    tasks = tuple(
        sorted(
            (
                PromotionTaskReadiness(
                    task_key=task_key,
                    instance_count=int(row["instance_count"]),
                    action_counts=_sorted_counts(row["actions"]),
                    strict_reuse_eligible_count=int(row["eligible"]),
                    blocker_class_counts=_sorted_counts(row["blocker_classes"]),
                    blocker_signature_counts=_sorted_counts(
                        row["blocker_signatures"]
                    ),
                    operator_warning_count=int(row["warnings"]),
                    operator_managed_external_candidate=PromotionTaskPolicyCandidate(
                        action_counts=_sorted_counts(row["candidate_actions"]),
                        strict_reuse_eligible_count=int(row["candidate_eligible"]),
                        eligible_delta=(
                            int(row["candidate_eligible"]) - int(row["eligible"])
                        ),
                        blocker_class_counts=_sorted_counts(
                            row["candidate_blocker_classes"]
                        ),
                        blocker_signature_counts=_sorted_counts(
                            row["candidate_blocker_signatures"]
                        ),
                    ),
                )
                for task_key, row in task_rows.items()
            ),
            key=_task_readiness_sort_key,
        )
    )

    graph_report = graph.report()
    return PromotionReadinessReport(
        state_db_path=state_db_path if state_db_path is not None else graph.state_db_path,
        readiness_status=_readiness_status(plan),
        source_decision_count=graph_report.source_decision_count,
        node_count=graph_report.node_count,
        edge_count=graph_report.edge_count,
        concrete_edge_count=graph_report.concrete_edge_count,
        blocking_edge_count=graph_report.blocking_edge_count,
        edge_kind_counts=dict(graph_report.edge_kind_counts),
        plan_entry_count=len(plan.entries),
        action_counts=dict(plan.action_counts),
        strict_reuse_eligible_count=plan.strict_reuse_eligible_count,
        blocker_class_counts=_sorted_counts(blocker_classes),
        blocker_signature_counts=_sorted_counts(blocker_signatures),
        input_config_available=input_report.config_available,
        input_config_error=input_report.config_error,
        input_manifest_availability_counts=dict(input_report.availability_counts),
        input_comparison_status_counts=dict(input_report.comparison_status_counts),
        operator_warning_count=input_report.warning_count,
        operator_warning_task_counts=_sorted_counts(warning_task_counts),
        operator_managed_external_candidate=PromotionPolicyCandidate(
            policy_name=OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY.name,
            readiness_status=_readiness_status(candidate_plan),
            plan_entry_count=len(candidate_plan.entries),
            action_counts=dict(candidate_plan.action_counts),
            strict_reuse_eligible_count=candidate_plan.strict_reuse_eligible_count,
            eligible_delta=(
                candidate_plan.strict_reuse_eligible_count
                - plan.strict_reuse_eligible_count
            ),
            blocker_class_counts=_sorted_counts(candidate_blocker_classes),
            blocker_signature_counts=_sorted_counts(candidate_blocker_signatures),
        ),
        contract_generation_candidate=generation_candidate_summary,
        coverage_branch_candidate=branch_candidate_summary,
        inline_observation_candidate=inline_candidate_summary,
        call_peaks_lineage_candidate=call_peaks_candidate_summary,
        artifact_health_candidate=artifact_health_candidate_summary,
        tasks=tasks,
        scheduler_authority=(
            _LEGACY_SCHEDULER
            if artifact_health_relations is None
            else artifact_health_relations.scheduler_authority
        ),
    )


def format_promotion_readiness_report(
    report: PromotionReadinessReport,
    *,
    limit: int = 20,
) -> str:
    lines = [
        f"Reuse v2 promotion readiness: {report.state_db_path or '-'}",
        f"readiness: {report.readiness_status}",
        f"runtime promotion allowed: {int(report.runtime_promotion_allowed)}",
        f"scheduler authority: {report.scheduler_authority}",
        f"source decisions: {report.source_decision_count}",
        f"instances: {report.node_count}",
        f"edges: {report.edge_count} ({report.concrete_edge_count} concrete, "
        f"{report.blocking_edge_count} blocking)",
        f"strict reuse eligible: {report.strict_reuse_eligible_count}",
        f"operator input warnings: {report.operator_warning_count}",
    ]
    if report.action_counts:
        lines.append("observe-only actions:")
        for action, count in report.action_counts.items():
            lines.append(f"  {action}: {count}")
    if report.blocker_class_counts:
        lines.append("blocker classes:")
        for blocker_class, count in report.blocker_class_counts.items():
            lines.append(f"  {blocker_class}: {count}")
    if report.blocker_signature_counts:
        lines.append("blocker signatures:")
        for signature, count in report.blocker_signature_counts.items():
            lines.append(f"  {signature}: {count}")
    if report.input_config_error:
        lines.append(f"input config error: {report.input_config_error}")
    candidate = report.operator_managed_external_candidate
    lines.extend(
        [
            f"candidate policy: {candidate.policy_name}",
            f"candidate readiness: {candidate.readiness_status}",
            f"candidate strict reuse eligible: "
            f"{candidate.strict_reuse_eligible_count} "
            f"(delta={candidate.eligible_delta:+d})",
        ]
    )
    if candidate.action_counts:
        lines.append("candidate actions:")
        for action, count in candidate.action_counts.items():
            lines.append(f"  {action}: {count}")
    if candidate.blocker_class_counts:
        lines.append("candidate blocker classes:")
        for blocker_class, count in candidate.blocker_class_counts.items():
            lines.append(f"  {blocker_class}: {count}")
    generation_candidate = report.contract_generation_candidate
    if generation_candidate is not None:
        generation_plan = generation_candidate.strict_plan
        generation_combined_plan = (
            generation_candidate.operator_managed_external_plan
        )
        lines.extend(
            [
                f"contract generation candidate policy: "
                f"{generation_candidate.policy_name}",
                f"contract generations excluded: "
                f"{generation_candidate.excluded_generation_count} "
                f"({generation_candidate.excluded_decision_count} decisions)",
                f"contract generation graph nodes: "
                f"{generation_candidate.candidate_node_count} "
                f"(delta={generation_candidate.node_count_delta:+d})",
                f"contract generation graph edges: "
                f"{generation_candidate.candidate_edge_count} "
                f"(delta={generation_candidate.edge_count_delta:+d}, "
                f"concrete_delta="
                f"{generation_candidate.concrete_edge_count_delta:+d}, "
                f"blocking_delta="
                f"{generation_candidate.blocking_edge_count_delta:+d})",
                f"contract generation strict reuse eligible: "
                f"{generation_plan.strict_reuse_eligible_count} "
                f"(delta={generation_plan.eligible_delta:+d})",
                f"external plus contract generation strict reuse eligible: "
                f"{generation_combined_plan.strict_reuse_eligible_count} "
                f"(baseline_delta={generation_combined_plan.eligible_delta:+d}, "
                f"generation_increment="
                f"{generation_candidate.incremental_eligible_delta_over_operator_managed_external:+d})",
            ]
        )
    branch_candidate = report.coverage_branch_candidate
    if branch_candidate is not None:
        branch_plan = branch_candidate.strict_plan
        combined_plan = branch_candidate.operator_managed_external_plan
        lines.extend(
            [
                f"coverage branch candidate policy: {branch_candidate.policy_name}",
                f"coverage branch relations applied: "
                f"{branch_candidate.applied_relation_count}/"
                f"{branch_candidate.source_relation_count}",
                f"coverage branch graph edges: "
                f"{branch_candidate.candidate_edge_count} "
                f"(delta={branch_candidate.edge_count_delta:+d}, "
                f"concrete_delta={branch_candidate.concrete_edge_count_delta:+d}, "
                f"blocking_delta={branch_candidate.blocking_edge_count_delta:+d})",
                f"coverage branch strict reuse eligible: "
                f"{branch_plan.strict_reuse_eligible_count} "
                f"(delta={branch_plan.eligible_delta:+d})",
                f"combined candidate policy: {combined_plan.policy_name}",
                f"combined candidate strict reuse eligible: "
                f"{combined_plan.strict_reuse_eligible_count} "
                f"(baseline_delta={combined_plan.eligible_delta:+d}, "
                f"branch_increment="
                f"{branch_candidate.incremental_eligible_delta_over_operator_managed_external:+d})",
            ]
        )
    inline_candidate = report.inline_observation_candidate
    if inline_candidate is not None:
        inline_plan = inline_candidate.strict_plan
        final_plan = inline_candidate.operator_managed_external_plan
        lines.extend(
            [
                f"inline observation candidate policy: "
                f"{inline_candidate.policy_name}",
                f"inline observations: "
                f"{inline_candidate.source_observation_count}",
                f"inline observation relations applied: "
                f"{inline_candidate.applied_relation_count}/"
                f"{inline_candidate.source_relation_count}",
                f"inline candidate graph edges: "
                f"{inline_candidate.candidate_edge_count} "
                f"(delta={inline_candidate.edge_count_delta:+d}, "
                f"concrete_delta="
                f"{inline_candidate.concrete_edge_count_delta:+d}, "
                f"blocking_delta="
                f"{inline_candidate.blocking_edge_count_delta:+d})",
                f"coverage plus inline strict reuse eligible: "
                f"{inline_plan.strict_reuse_eligible_count} "
                f"(baseline_delta={inline_plan.eligible_delta:+d})",
                f"final combined candidate policy: {final_plan.policy_name}",
                f"final combined strict reuse eligible: "
                f"{final_plan.strict_reuse_eligible_count} "
                f"(baseline_delta={final_plan.eligible_delta:+d}, "
                f"inline_increment="
                f"{inline_candidate.incremental_eligible_delta_over_prior_candidate:+d})",
            ]
        )
    call_peaks_candidate = report.call_peaks_lineage_candidate
    if call_peaks_candidate is not None:
        call_peaks_plan = call_peaks_candidate.strict_plan
        final_lineage_plan = (
            call_peaks_candidate.operator_managed_external_plan
        )
        lines.extend(
            [
                f"call-peaks lineage candidate policy: "
                f"{call_peaks_candidate.policy_name}",
                f"call-peaks lineage relations applied: "
                f"{call_peaks_candidate.applied_relation_count}/"
                f"{call_peaks_candidate.applied_relation_count + call_peaks_candidate.unchanged_relation_count}",
                f"call-peaks lineage graph edges: "
                f"{call_peaks_candidate.candidate_edge_count} "
                f"(delta={call_peaks_candidate.edge_count_delta:+d}, "
                f"concrete_delta="
                f"{call_peaks_candidate.concrete_edge_count_delta:+d}, "
                f"blocking_delta="
                f"{call_peaks_candidate.blocking_edge_count_delta:+d})",
                f"full lineage strict reuse eligible: "
                f"{call_peaks_plan.strict_reuse_eligible_count} "
                f"(baseline_delta={call_peaks_plan.eligible_delta:+d})",
                f"full lineage plus external strict reuse eligible: "
                f"{final_lineage_plan.strict_reuse_eligible_count} "
                f"(baseline_delta={final_lineage_plan.eligible_delta:+d}, "
                f"call_peaks_increment="
                f"{call_peaks_candidate.incremental_eligible_delta_over_prior_candidate:+d})",
            ]
        )
    artifact_health_candidate = report.artifact_health_candidate
    if artifact_health_candidate is not None:
        lines.extend(
            [
                f"artifact-health candidate policy: "
                f"{artifact_health_candidate.policy_name}",
                f"artifact-health registered scope: "
                f"{artifact_health_candidate.registered_scope_count}/"
                f"{artifact_health_candidate.relation_count}",
                f"artifact-health ready relations: "
                f"{artifact_health_candidate.ready_relation_count} "
                f"(unhealthy="
                f"{artifact_health_candidate.unhealthy_relation_count})",
                f"health-qualified strict reuse eligible: "
                f"{artifact_health_candidate.strict_reuse_eligible_count} "
                f"(dependency_eligible="
                f"{artifact_health_candidate.dependency_strict_reuse_eligible_count}, "
                f"delta="
                f"{artifact_health_candidate.eligible_delta_over_dependency_candidate:+d})",
            ]
        )
    tasks = report.tasks[: max(0, int(limit))]
    if tasks:
        lines.append("tasks:")
    for task in tasks:
        actions = ",".join(
            f"{action}={count}" for action, count in task.action_counts.items()
        ) or "-"
        blockers = ",".join(
            f"{blocker}={count}"
            for blocker, count in task.blocker_class_counts.items()
        ) or "-"
        task_candidate = task.operator_managed_external_candidate
        lines.append(
            f"  {_task_label(task.task_key)} instances={task.instance_count} "
            f"eligible={task.strict_reuse_eligible_count} actions=[{actions}] "
            f"blockers=[{blockers}] warnings={task.operator_warning_count} "
            f"candidate_eligible={task_candidate.strict_reuse_eligible_count} "
            f"candidate_delta={task_candidate.eligible_delta:+d}"
        )
    return "\n".join(lines)


def _require_plan_instances(
    graph: DynamicInstanceGraph,
    plan: ObserveOnlyReusePlan,
    *,
    label: str,
) -> None:
    node_ids = set(graph.nodes_by_instance_id)
    plan_ids = [entry.instance_id for entry in plan.entries]
    if len(set(plan_ids)) != len(plan_ids):
        raise ValueError(f"{label} plan contains duplicate instance IDs")
    if node_ids != set(plan_ids):
        raise ValueError(f"{label} plan instances do not match its graph")


def _promotion_policy_candidate(
    candidate_plan: ObserveOnlyReusePlan,
    *,
    baseline_plan: ObserveOnlyReusePlan,
    policy_name: str,
) -> PromotionPolicyCandidate:
    blocker_classes: Counter[str] = Counter()
    blocker_signatures: Counter[str] = Counter()
    for entry in candidate_plan.entries:
        for blocker in entry.blockers:
            blocker_classes[_blocker_class(blocker)] += 1
            blocker_signatures[_blocker_signature(blocker)] += 1
    return PromotionPolicyCandidate(
        policy_name=policy_name,
        readiness_status=_readiness_status(candidate_plan),
        plan_entry_count=len(candidate_plan.entries),
        action_counts=dict(candidate_plan.action_counts),
        strict_reuse_eligible_count=candidate_plan.strict_reuse_eligible_count,
        eligible_delta=(
            candidate_plan.strict_reuse_eligible_count
            - baseline_plan.strict_reuse_eligible_count
        ),
        blocker_class_counts=_sorted_counts(blocker_classes),
        blocker_signature_counts=_sorted_counts(blocker_signatures),
    )


def _readiness_status(plan: ObserveOnlyReusePlan) -> PromotionReadinessStatus:
    if not plan.entries:
        return "no_observations"
    eligible = plan.strict_reuse_eligible_count
    if eligible == len(plan.entries):
        return "evidence_complete"
    if eligible:
        return "partial"
    return "blocked"


def _health_readiness_status(
    plan: HealthQualifiedReusePlan,
) -> PromotionReadinessStatus:
    if not plan.entries:
        return "no_observations"
    eligible = plan.strict_reuse_eligible_count
    if eligible == len(plan.entries):
        return "evidence_complete"
    if eligible:
        return "partial"
    return "blocked"


def _blocker_class(blocker: str) -> str:
    return str(blocker).partition(":")[0] or "unknown"


def _blocker_signature(blocker: str) -> str:
    value = str(blocker)
    if value.startswith("upstream_not_strict_reuse:") and ":" in value:
        return value.rsplit(":", 1)[0]
    return value


def _task_label(task_key: TaskKey) -> str:
    return f"{task_key[0]}:{task_key[1]}"


def _sorted_counts(values: Mapping[str, int]) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in sorted(values.items())
        if int(value)
    }


def _task_readiness_sort_key(
    task: PromotionTaskReadiness,
) -> tuple[int, int, int, str, str]:
    return (
        -int(task.action_counts.get("blocked", 0)),
        -int(task.action_counts.get("would_run", 0)),
        -task.operator_warning_count,
        task.task_key[0],
        task.task_key[1],
    )
