"""Candidate graph containing only exact active contract generations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Tuple

from .contract_generation_lifecycle import (
    CONTRACT_GENERATION_LIFECYCLE_POLICY_NAME,
    ContractGenerationLifecycleReport,
    ContractGenerationV1,
    build_contract_generation_lifecycle_report,
)
from .instance_graph import (
    DynamicInstanceGraph,
    build_dynamic_instance_graph,
)
from .cache_epoch import (
    NEW_RUN_ROOT_ADVICE,
    PIPELINE_CACHE_EPOCH,
    PIPELINE_CACHE_EPOCH_KEY,
    CacheEpochCompatibilityError,
    classify_cache_epoch,
    describe_cache_epoch,
)
from .pipeline_contracts import TaskKey
from .reuse_report import (
    CurrentReuseDecisionSummaryV2,
    load_current_reuse_decision_summaries_v2,
)


ContractGenerationCandidateApplicationStatus = Literal[
    "active_retained",
    "superseded_excluded",
    "unresolved_retained",
    "source_mismatch_unchanged",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"


@dataclass(frozen=True)
class ContractGenerationCandidateApplication:
    logical_slot_id: str
    generation_id: str
    task_key: TaskKey
    instance_id: str
    generation_status: str
    application_status: ContractGenerationCandidateApplicationStatus
    decision_ids: Tuple[int, ...]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "logical_slot_id": self.logical_slot_id,
            "generation_id": self.generation_id,
            "task_key": {
                "stage": self.task_key[0],
                "task_base": self.task_key[1],
            },
            "instance_id": self.instance_id,
            "generation_status": self.generation_status,
            "application_status": self.application_status,
            "decision_ids": list(self.decision_ids),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ContractGenerationCandidateGraphResult:
    source_graph: DynamicInstanceGraph
    graph: DynamicInstanceGraph
    lifecycle_report: ContractGenerationLifecycleReport
    retained_decisions: Tuple[CurrentReuseDecisionSummaryV2, ...]
    source_matches: bool
    applications: Tuple[ContractGenerationCandidateApplication, ...]
    cache_epoch_superseded_decision_ids: Tuple[int, ...] = ()
    source_decisions: Tuple[CurrentReuseDecisionSummaryV2, ...] = ()
    policy_name: str = CONTRACT_GENERATION_LIFECYCLE_POLICY_NAME
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    def __post_init__(self) -> None:
        if any(
            not isinstance(decision, CurrentReuseDecisionSummaryV2)
            for decision in self.retained_decisions
        ):
            raise TypeError(
                "Current contract-generation candidates require "
                "CurrentReuseDecisionSummaryV2"
            )

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
    def excluded_decision_reasons(self) -> dict[int, str]:
        """Auditable reason for every decision left out of ``retained_decisions``."""

        retained = {decision.decision_id for decision in self.retained_decisions}
        epoch_ids = set(self.cache_epoch_superseded_decision_ids)
        reasons: dict[int, str] = {}
        for decision_id in self.lifecycle_report.superseded_decision_ids:
            if decision_id not in retained:
                reasons[decision_id] = "contract_generation_superseded"
        for decision_id in self.cache_epoch_superseded_decision_ids:
            if decision_id not in retained and decision_id not in reasons:
                reasons[decision_id] = "cache_epoch_predecessor"
        return reasons

    @property
    def excluded_generation_count(self) -> int:
        return int(
            self.application_status_counts.get("superseded_excluded", 0)
        )

    @property
    def excluded_decision_count(self) -> int:
        return sum(
            len(application.decision_ids)
            for application in self.applications
            if application.application_status == "superseded_excluded"
        )

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        source_report = self.source_graph.report()
        candidate_report = self.graph.report()
        applications = self.applications
        if limit is not None:
            applications = applications[: max(0, int(limit))]
        return {
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "source_matches": self.source_matches,
            "table_available": self.lifecycle_report.table_available,
            "error": self.lifecycle_report.error,
            "source_decision_count": (
                self.lifecycle_report.source_decision_count
            ),
            "retained_decision_count": len(self.retained_decisions),
            "decision_load_status_counts": dict(
                self.lifecycle_report.decision_load_status_counts
            ),
            "logical_slot_count": self.lifecycle_report.logical_slot_count,
            "unresolved_logical_slot_count": (
                self.lifecycle_report.unresolved_logical_slot_count
            ),
            "generation_count": self.lifecycle_report.generation_count,
            "generation_status_counts": (
                self.lifecycle_report.generation_status_counts
            ),
            "application_count": len(self.applications),
            "application_status_counts": self.application_status_counts,
            "excluded_generation_count": self.excluded_generation_count,
            "excluded_decision_count": self.excluded_decision_count,
            "excluded_decision_reason_counts": dict(
                sorted(Counter(self.excluded_decision_reasons.values()).items())
            ),
            "cache_epoch_superseded_decision_count": len(
                self.cache_epoch_superseded_decision_ids
            ),
            "source_graph": source_report.as_dict(),
            "candidate_graph": candidate_report.as_dict(),
            "node_count_delta": (
                candidate_report.node_count - source_report.node_count
            ),
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


def build_active_contract_generation_candidate_graph(
    state_or_db_path: str | Path,
    graph: DynamicInstanceGraph,
    *,
    lifecycle_report: ContractGenerationLifecycleReport | None = None,
) -> ContractGenerationCandidateGraphResult:
    """Exclude only exact superseded generations in an isolated graph."""

    lifecycle = lifecycle_report or (
        build_contract_generation_lifecycle_report(state_or_db_path)
    )
    if lifecycle.strict_effect != _DIAGNOSTIC_ONLY:
        raise ValueError("Contract generation lifecycle must be diagnostic_only")
    if lifecycle.policy_name != CONTRACT_GENERATION_LIFECYCLE_POLICY_NAME:
        raise ValueError("Unsupported contract generation lifecycle policy")

    decisions = load_current_reuse_decision_summaries_v2(
        state_or_db_path,
        limit=None,
        latest_first=False,
    )
    fresh_source_graph = build_dynamic_instance_graph(
        state_or_db_path,
        decisions=decisions,
    )
    source_matches = (
        lifecycle.source_decision_count == len(decisions)
        and lifecycle.source_decision_count == graph.source_decision_count
        and _same_db_path(
            lifecycle.state_db_path,
            fresh_source_graph.state_db_path,
        )
        and graph == fresh_source_graph
    )

    epoch_superseded_ids = (
        cache_epoch_superseded_decision_ids(decisions)
        if source_matches
        else ()
    )
    excluded_decision_ids = set(lifecycle.superseded_decision_ids) | set(
        epoch_superseded_ids
    )
    retained_decisions = (
        tuple(
            decision
            for decision in decisions
            if decision.decision_id not in excluded_decision_ids
        )
        if source_matches
        else tuple(decisions)
    )
    candidate_graph = (
        build_dynamic_instance_graph(
            state_or_db_path,
            decisions=retained_decisions,
        )
        if source_matches and excluded_decision_ids
        else graph
    )
    applications = tuple(
        _application(generation, source_matches=source_matches)
        for generation in lifecycle.generations
    )
    return ContractGenerationCandidateGraphResult(
        source_graph=graph,
        graph=candidate_graph,
        lifecycle_report=lifecycle,
        retained_decisions=retained_decisions,
        source_matches=source_matches,
        applications=applications,
        cache_epoch_superseded_decision_ids=epoch_superseded_ids,
        source_decisions=tuple(decisions),
    )


def cache_epoch_superseded_decision_ids(
    decisions: Tuple[CurrentReuseDecisionSummaryV2, ...],
) -> Tuple[int, ...]:
    """Known-predecessor-epoch decisions set aside in an upgraded run root.

    See cache_epoch.py. Once the persisted history holds at least one
    current-epoch decision, the root has been upgraded: every decision written
    under an explicitly known predecessor generation (``KNOWN_PREDECESSOR_CACHE_EPOCHS``)
    can never authorize current reuse (its coordinate cannot match a current
    one), so it is excluded from the isolated candidate graph instead of
    competing with current producers for the same artifact paths. Excluding a
    producer never grants reuse: a consumer left without a current producer
    gets a blocking ``producer_unobserved`` edge.

    A history without any current-epoch decision is left unchanged. A decision
    with an unknown epoch (for example a future generation) raises
    ``CacheEpochCompatibilityError`` instead of being treated as older.
    Stored rows are never modified.
    """

    unknown = [
        decision
        for decision in decisions
        if classify_cache_epoch(decision.params) == "unknown"
    ]
    if unknown:
        first = unknown[0]
        raise CacheEpochCompatibilityError(
            f"{len(unknown)} persisted decision(s) carry an unknown "
            f"{PIPELINE_CACHE_EPOCH_KEY} (e.g. decision {first.decision_id} "
            f"{first.stage}:{first.task_base} epoch="
            f"{describe_cache_epoch(first.params)}; this code writes "
            f"{PIPELINE_CACHE_EPOCH!r}). {NEW_RUN_ROOT_ADVICE}"
        )
    if not any(
        classify_cache_epoch(decision.params) == "current"
        for decision in decisions
    ):
        return ()
    return tuple(
        sorted(
            decision.decision_id
            for decision in decisions
            if classify_cache_epoch(decision.params) == "predecessor"
        )
    )


def _application(
    generation: ContractGenerationV1,
    *,
    source_matches: bool,
) -> ContractGenerationCandidateApplication:
    if not source_matches:
        application_status: ContractGenerationCandidateApplicationStatus = (
            "source_mismatch_unchanged"
        )
        detail = (
            "source graph and lifecycle report do not describe the same "
            "persisted decision history"
        )
    elif generation.status == "superseded":
        application_status = "superseded_excluded"
        detail = (
            "excluded exact superseded decision generation from the "
            "isolated candidate graph"
        )
    elif generation.status == "active":
        application_status = "active_retained"
        detail = "retained active generation"
    else:
        application_status = "unresolved_retained"
        detail = "retained unresolved generation without inference"
    return ContractGenerationCandidateApplication(
        logical_slot_id=generation.logical_slot_id,
        generation_id=generation.generation_id,
        task_key=generation.task_key,
        instance_id=generation.instance_id,
        generation_status=generation.status,
        application_status=application_status,
        decision_ids=generation.decision_ids,
        detail=detail,
    )


def _same_db_path(left: str, right: str) -> bool:
    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
