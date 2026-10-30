"""Exact output-health relations for dependency-qualified Reuse v2 nodes."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Tuple

from .artifact_health import (
    ArtifactHealthTaskRecordV2,
    ArtifactHealthEntry,
    check_task_artifact_health_v2,
    load_artifact_health_task_records_with_snapshot_v2,
)
from .instance_graph import (
    DynamicInstanceGraph,
    ObserveOnlyReusePlan,
)
from .pipeline_contracts import TaskKey
from .reuse_report import (
    CurrentReuseDecisionSummaryV2,
    ReuseDecisionSummary,
)
from .run_config_preview import load_run_config_snapshot
from .task_completion import (
    TaskCompletionAuthorityModeV1,
    TaskCompletionLoadError,
    TaskCompletionRecordSourceV1,
    TaskCompletionSourceV1,
    TaskSchedulerAuthorityV1,
    scheduler_authority_for_completion_mode_v1,
)
from .task_coordinate import (
    TaskCoordinateError,
    TaskCoordinateV1,
    build_task_coordinate_v1,
)


ReuseArtifactHealthStatus = Literal[
    "ready",
    "unhealthy",
    "expectation_unavailable",
    "done_unavailable",
    "done_ambiguous",
    "decision_mismatch",
    "health_unresolved",
]
HealthQualifiedPlanAction = Literal["would_reuse", "blocked"]

REQUIRED_OUTPUT_HEALTH_POLICY_NAME = "required_output_health_v1"
CURRENT_REUSE_ARTIFACT_HEALTH_RELATION_SCHEMA_V2 = (
    "scpolya.current_reuse_artifact_health_relation.v2"
)
CURRENT_REUSE_ARTIFACT_HEALTH_REPORT_SCHEMA_V2 = (
    "scpolya.current_reuse_artifact_health_report.v2"
)
CurrentReuseArtifactHealthCoordinateStatus = Literal[
    "available",
    "malformed",
    "compatibility_mismatch",
    "unavailable",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"


@dataclass(frozen=True)
class ReuseArtifactHealthRelation:
    instance_id: str
    decision_id: int
    task_key: TaskKey
    legacy_variant_str: str
    status: ReuseArtifactHealthStatus
    done_at: str | None
    expectation_count: int
    ready_count: int
    missing_count: int
    empty_count: int
    unresolved_count: int
    warning_count: int
    entries: Tuple[ArtifactHealthEntry, ...]
    detail: str
    task_coordinate_id: str = ""
    completion_source: TaskCompletionRecordSourceV1 | Literal[""] = ""

    @property
    def health_scope_registered(self) -> bool:
        return self.expectation_count > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "decision_id": self.decision_id,
            "task_key": {
                "stage": self.task_key[0],
                "task_base": self.task_key[1],
            },
            "task_coordinate_id": self.task_coordinate_id,
            "legacy_variant_str": self.legacy_variant_str,
            "completion_source": self.completion_source,
            "status": self.status,
            "done_at": self.done_at,
            "health_scope_registered": self.health_scope_registered,
            "expectation_count": self.expectation_count,
            "ready_count": self.ready_count,
            "missing_count": self.missing_count,
            "empty_count": self.empty_count,
            "unresolved_count": self.unresolved_count,
            "warning_count": self.warning_count,
            "entries": [asdict(entry) for entry in self.entries],
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CurrentReuseArtifactHealthRelationV2:
    """Alias-free health evidence for one current decision coordinate."""

    instance_id: str
    decision_id: int
    task_key: TaskKey
    task_coordinate_id: str
    coordinate_status: CurrentReuseArtifactHealthCoordinateStatus
    status: ReuseArtifactHealthStatus
    done_at: str | None
    expectation_count: int
    ready_count: int
    missing_count: int
    empty_count: int
    unresolved_count: int
    warning_count: int
    entries: Tuple[ArtifactHealthEntry, ...]
    detail: str
    completion_source: TaskCompletionRecordSourceV1 | Literal[""] = ""
    schema_version: str = CURRENT_REUSE_ARTIFACT_HEALTH_RELATION_SCHEMA_V2

    def __post_init__(self) -> None:
        if self.schema_version != CURRENT_REUSE_ARTIFACT_HEALTH_RELATION_SCHEMA_V2:
            raise ValueError(
                "Unsupported current artifact-health relation schema: "
                f"{self.schema_version!r}"
            )
        if self.coordinate_status not in {
            "available",
            "malformed",
            "compatibility_mismatch",
            "unavailable",
        }:
            raise ValueError(
                "Unsupported current artifact-health coordinate status: "
                f"{self.coordinate_status!r}"
            )
        coordinate_required = self.coordinate_status in {
            "available",
            "compatibility_mismatch",
        }
        if coordinate_required != bool(self.task_coordinate_id):
            raise ValueError(
                "Current artifact-health coordinate ID/status mismatch"
            )
        if (
            self.coordinate_status != "available"
            and self.status != "decision_mismatch"
        ):
            raise ValueError(
                "Unavailable current decision coordinates must fail closed"
            )

    @property
    def health_scope_registered(self) -> bool:
        return self.expectation_count > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "decision_id": self.decision_id,
            "task_key": {
                "stage": self.task_key[0],
                "task_base": self.task_key[1],
            },
            "task_coordinate_id": self.task_coordinate_id,
            "coordinate_status": self.coordinate_status,
            "completion_source": self.completion_source,
            "status": self.status,
            "done_at": self.done_at,
            "health_scope_registered": self.health_scope_registered,
            "expectation_count": self.expectation_count,
            "ready_count": self.ready_count,
            "missing_count": self.missing_count,
            "empty_count": self.empty_count,
            "unresolved_count": self.unresolved_count,
            "warning_count": self.warning_count,
            "entries": [asdict(entry) for entry in self.entries],
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ReuseArtifactHealthRelationReport:
    state_db_path: str
    source_decision_count: int
    source_node_count: int
    source_matches: bool
    config_available: bool
    config_error: str | None
    done_record_count: int
    relations: Tuple[ReuseArtifactHealthRelation, ...]
    policy_name: str = REQUIRED_OUTPUT_HEALTH_POLICY_NAME
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY
    completion_authority_mode: TaskCompletionAuthorityModeV1 = (
        "uninitialized"
    )
    completion_source: TaskCompletionSourceV1 = "legacy"
    legacy_fallback_count: int = 0

    @property
    def relation_count(self) -> int:
        return len(self.relations)

    @property
    def status_counts(self) -> dict[str, int]:
        return _counts(relation.status for relation in self.relations)

    @property
    def registered_scope_count(self) -> int:
        return sum(
            relation.health_scope_registered
            for relation in self.relations
        )

    @property
    def ready_count(self) -> int:
        return int(self.status_counts.get("ready", 0))

    @property
    def unhealthy_count(self) -> int:
        return int(self.status_counts.get("unhealthy", 0))

    @property
    def scheduler_authority(self) -> TaskSchedulerAuthorityV1:
        return scheduler_authority_for_completion_mode_v1(
            self.completion_authority_mode
        )

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        relations = self.relations
        if limit is not None:
            relations = relations[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "completion_authority_mode": (
                self.completion_authority_mode
            ),
            "completion_source": self.completion_source,
            "scheduler_authority": self.scheduler_authority,
            "legacy_fallback_count": self.legacy_fallback_count,
            "source_decision_count": self.source_decision_count,
            "source_node_count": self.source_node_count,
            "source_matches": self.source_matches,
            "config_available": self.config_available,
            "config_error": self.config_error,
            "done_record_count": self.done_record_count,
            "relation_count": self.relation_count,
            "status_counts": self.status_counts,
            "registered_scope_count": self.registered_scope_count,
            "ready_count": self.ready_count,
            "unhealthy_count": self.unhealthy_count,
            "relations": [
                relation.as_dict() for relation in relations
            ],
        }


@dataclass(frozen=True)
class CurrentReuseArtifactHealthRelationReportV2:
    state_db_path: str
    source_decision_count: int
    source_node_count: int
    source_matches: bool
    config_available: bool
    config_error: str | None
    done_record_count: int
    relations: Tuple[CurrentReuseArtifactHealthRelationV2, ...]
    policy_name: str = REQUIRED_OUTPUT_HEALTH_POLICY_NAME
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY
    completion_authority_mode: TaskCompletionAuthorityModeV1 = (
        "uninitialized"
    )
    completion_source: TaskCompletionSourceV1 = "legacy"
    legacy_fallback_count: int = 0
    schema_version: str = CURRENT_REUSE_ARTIFACT_HEALTH_REPORT_SCHEMA_V2

    def __post_init__(self) -> None:
        if self.schema_version != CURRENT_REUSE_ARTIFACT_HEALTH_REPORT_SCHEMA_V2:
            raise ValueError(
                "Unsupported current artifact-health report schema: "
                f"{self.schema_version!r}"
            )
        if any(
            not isinstance(relation, CurrentReuseArtifactHealthRelationV2)
            for relation in self.relations
        ):
            raise TypeError(
                "Current artifact-health reports require v2 relations"
            )

    @property
    def relation_count(self) -> int:
        return len(self.relations)

    @property
    def status_counts(self) -> dict[str, int]:
        return _counts(relation.status for relation in self.relations)

    @property
    def registered_scope_count(self) -> int:
        return sum(
            relation.health_scope_registered
            for relation in self.relations
        )

    @property
    def ready_count(self) -> int:
        return int(self.status_counts.get("ready", 0))

    @property
    def unhealthy_count(self) -> int:
        return int(self.status_counts.get("unhealthy", 0))

    @property
    def scheduler_authority(self) -> TaskSchedulerAuthorityV1:
        return scheduler_authority_for_completion_mode_v1(
            self.completion_authority_mode
        )

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        relations = self.relations
        if limit is not None:
            relations = relations[: max(0, int(limit))]
        return {
            "schema_version": self.schema_version,
            "state_db_path": self.state_db_path,
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "completion_authority_mode": (
                self.completion_authority_mode
            ),
            "completion_source": self.completion_source,
            "scheduler_authority": self.scheduler_authority,
            "legacy_fallback_count": self.legacy_fallback_count,
            "source_decision_count": self.source_decision_count,
            "source_node_count": self.source_node_count,
            "source_matches": self.source_matches,
            "config_available": self.config_available,
            "config_error": self.config_error,
            "done_record_count": self.done_record_count,
            "relation_count": self.relation_count,
            "status_counts": self.status_counts,
            "registered_scope_count": self.registered_scope_count,
            "ready_count": self.ready_count,
            "unhealthy_count": self.unhealthy_count,
            "relations": [
                relation.as_dict() for relation in relations
            ],
        }


@dataclass(frozen=True)
class HealthQualifiedReusePlanEntry:
    instance_id: str
    task_key: TaskKey
    dependency_action: str
    dependency_strict_reuse_eligible: bool
    health_status: ReuseArtifactHealthStatus
    action: HealthQualifiedPlanAction
    strict_reuse_eligible: bool
    blockers: Tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "task_key": {
                "stage": self.task_key[0],
                "task_base": self.task_key[1],
            },
            "dependency_action": self.dependency_action,
            "dependency_strict_reuse_eligible": (
                self.dependency_strict_reuse_eligible
            ),
            "health_status": self.health_status,
            "action": self.action,
            "strict_reuse_eligible": self.strict_reuse_eligible,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class HealthQualifiedReusePlan:
    policy_name: str
    entries: Tuple[HealthQualifiedReusePlanEntry, ...]
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    @property
    def action_counts(self) -> dict[str, int]:
        return _counts(entry.action for entry in self.entries)

    @property
    def health_status_counts(self) -> dict[str, int]:
        return _counts(entry.health_status for entry in self.entries)

    @property
    def strict_reuse_eligible_count(self) -> int:
        return sum(entry.strict_reuse_eligible for entry in self.entries)

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        entries = self.entries
        if limit is not None:
            entries = entries[: max(0, int(limit))]
        return {
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "entry_count": len(self.entries),
            "action_counts": self.action_counts,
            "health_status_counts": self.health_status_counts,
            "strict_reuse_eligible_count": (
                self.strict_reuse_eligible_count
            ),
            "entries": [entry.as_dict() for entry in entries],
        }


def build_current_reuse_artifact_health_relation_report_v2(
    state_or_db_path: str | Path,
    graph: DynamicInstanceGraph,
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
) -> CurrentReuseArtifactHealthRelationReportV2:
    """Build the alias-free current health evidence used by readiness."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.exists():
        config = None
        config_error = (
            f"FileNotFoundError: state database not found: {db_path}"
        )
    else:
        try:
            config = load_run_config_snapshot(db_path)
            config_error = None
        except (
            KeyError,
            OSError,
            TypeError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            config = None
            config_error = f"{type(exc).__name__}: {exc}"
    try:
        completion_snapshot, done_records = (
            load_artifact_health_task_records_with_snapshot_v2(db_path)
        )
        done_error = None
    except (
        OSError,
        TypeError,
        ValueError,
        sqlite3.Error,
        TaskCompletionLoadError,
    ) as exc:
        completion_snapshot = None
        done_records = ()
        done_error = f"{type(exc).__name__}: {exc}"

    return build_current_reuse_artifact_health_relation_report_from_components_v2(
        graph,
        decisions,
        done_records,
        state_db_path=db_path,
        config=config,
        config_error=config_error,
        done_error=done_error,
        completion_authority_mode=(
            "unavailable"
            if completion_snapshot is None
            else completion_snapshot.authority_mode
        ),
        completion_source=(
            "unavailable"
            if completion_snapshot is None
            else completion_snapshot.completion_source
        ),
        legacy_fallback_count=(
            0
            if completion_snapshot is None
            else completion_snapshot.legacy_fallback_count
        ),
    )


def build_current_reuse_artifact_health_relation_report_from_components_v2(
    graph: DynamicInstanceGraph,
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
    done_records: Iterable[ArtifactHealthTaskRecordV2],
    *,
    state_db_path: str | Path,
    config: Mapping[str, Any] | None,
    config_error: str | None = None,
    done_error: str | None = None,
    completion_authority_mode: TaskCompletionAuthorityModeV1 = (
        "uninitialized"
    ),
    completion_source: TaskCompletionSourceV1 = "legacy",
    legacy_fallback_count: int | None = None,
) -> CurrentReuseArtifactHealthRelationReportV2:
    """Build current health relations from immutable structured components."""

    decision_rows = tuple(decisions)
    if any(
        not isinstance(decision, CurrentReuseDecisionSummaryV2)
        for decision in decision_rows
    ):
        raise TypeError(
            "Current artifact health requires "
            "CurrentReuseDecisionSummaryV2"
        )
    done_rows = tuple(done_records)
    latest_by_instance_id: dict[str, CurrentReuseDecisionSummaryV2] = {}
    for decision in decision_rows:
        previous = latest_by_instance_id.get(decision.instance_id)
        if previous is None or decision.decision_id >= previous.decision_id:
            latest_by_instance_id[decision.instance_id] = decision
    source_matches = (
        len(decision_rows) == graph.source_decision_count
        and len(latest_by_instance_id) == len(graph.nodes)
        and all(
            node.instance_id in latest_by_instance_id
            and latest_by_instance_id[node.instance_id].decision_id
            == node.decision_id
            and latest_by_instance_id[node.instance_id].key == node.key
            for node in graph.nodes
        )
    )
    db_path = _resolve_db_path(state_db_path)
    done_by_coordinate: dict[
        str,
        list[ArtifactHealthTaskRecordV2],
    ] = {}
    if done_error is None:
        try:
            for record in done_rows:
                coordinate_id = _health_task_coordinate_id(record)
                done_by_coordinate.setdefault(
                    coordinate_id,
                    [],
                ).append(record)
        except (TaskCoordinateError, TypeError, ValueError) as exc:
            done_by_coordinate = {}
            done_error = (
                "completion record coordinate validation failed: "
                f"{type(exc).__name__}: {exc}"
            )
    relations = tuple(
        _current_relation_for_node_v2(
            node_instance_id=node.instance_id,
            node_decision_id=node.decision_id,
            node_task_key=node.key,
            decision=latest_by_instance_id.get(node.instance_id),
            done_records_by_coordinate=done_by_coordinate,
            state_db_path=db_path,
            config=config,
            source_matches=source_matches,
            config_error=config_error,
            done_error=done_error,
        )
        for node in graph.nodes
    )
    return CurrentReuseArtifactHealthRelationReportV2(
        state_db_path=str(db_path),
        source_decision_count=len(decision_rows),
        source_node_count=len(graph.nodes),
        source_matches=source_matches,
        config_available=config is not None,
        config_error=config_error,
        done_record_count=len(done_rows),
        relations=relations,
        completion_authority_mode=completion_authority_mode,
        completion_source=completion_source,
        legacy_fallback_count=(
            len(done_rows)
            if legacy_fallback_count is None
            else int(legacy_fallback_count)
        ),
    )


def build_reuse_artifact_health_relation_report(
    state_or_db_path: str | Path,
    graph: DynamicInstanceGraph,
    decisions: Iterable[ReuseDecisionSummary],
) -> ReuseArtifactHealthRelationReport:
    """Match candidate nodes to exact decisions, completions, and outputs."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.exists():
        config = None
        config_error = (
            f"FileNotFoundError: state database not found: {db_path}"
        )
    else:
        try:
            config = load_run_config_snapshot(db_path)
            config_error = None
        except (
            KeyError,
            OSError,
            TypeError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            config = None
            config_error = f"{type(exc).__name__}: {exc}"
    try:
        completion_snapshot, done_records = (
            load_artifact_health_task_records_with_snapshot_v2(db_path)
        )
        done_error = None
    except (
        OSError,
        TypeError,
        ValueError,
        sqlite3.Error,
        TaskCompletionLoadError,
    ) as exc:
        completion_snapshot = None
        done_records = ()
        done_error = f"{type(exc).__name__}: {exc}"

    return build_reuse_artifact_health_relation_report_from_components(
        graph,
        decisions,
        done_records,
        state_db_path=db_path,
        config=config,
        config_error=config_error,
        done_error=done_error,
        completion_authority_mode=(
            "unavailable"
            if completion_snapshot is None
            else completion_snapshot.authority_mode
        ),
        completion_source=(
            "unavailable"
            if completion_snapshot is None
            else completion_snapshot.completion_source
        ),
        legacy_fallback_count=(
            0
            if completion_snapshot is None
            else completion_snapshot.legacy_fallback_count
        ),
    )


def build_reuse_artifact_health_relation_report_from_components(
    graph: DynamicInstanceGraph,
    decisions: Iterable[ReuseDecisionSummary],
    done_records: Iterable[ArtifactHealthTaskRecordV2],
    *,
    state_db_path: str | Path,
    config: Mapping[str, Any] | None,
    config_error: str | None = None,
    done_error: str | None = None,
    completion_authority_mode: TaskCompletionAuthorityModeV1 = (
        "uninitialized"
    ),
    completion_source: TaskCompletionSourceV1 = "legacy",
    legacy_fallback_count: int | None = None,
) -> ReuseArtifactHealthRelationReport:
    """Build exact relations from already-loaded, immutable components."""

    decision_rows = tuple(decisions)
    done_rows = tuple(done_records)
    latest_by_instance_id: dict[str, ReuseDecisionSummary] = {}
    for decision in decision_rows:
        previous = latest_by_instance_id.get(decision.instance_id)
        if previous is None or decision.decision_id >= previous.decision_id:
            latest_by_instance_id[decision.instance_id] = decision
    source_matches = (
        len(decision_rows) == graph.source_decision_count
        and len(latest_by_instance_id) == len(graph.nodes)
        and all(
            node.instance_id in latest_by_instance_id
            and latest_by_instance_id[node.instance_id].decision_id
            == node.decision_id
            and latest_by_instance_id[node.instance_id].key == node.key
            for node in graph.nodes
        )
    )
    db_path = _resolve_db_path(state_db_path)
    done_by_coordinate: dict[
        str,
        list[ArtifactHealthTaskRecordV2],
    ] = {}
    if done_error is None:
        try:
            for record in done_rows:
                coordinate_id = _health_task_coordinate_id(record)
                done_by_coordinate.setdefault(
                    coordinate_id,
                    [],
                ).append(record)
        except (TaskCoordinateError, TypeError, ValueError) as exc:
            done_by_coordinate = {}
            done_error = (
                "completion record coordinate validation failed: "
                f"{type(exc).__name__}: {exc}"
            )
    relations = tuple(
        _relation_for_node(
            node_instance_id=node.instance_id,
            node_decision_id=node.decision_id,
            node_task_key=node.key,
            decision=latest_by_instance_id.get(node.instance_id),
            done_records_by_coordinate=done_by_coordinate,
            state_db_path=db_path,
            config=config,
            source_matches=source_matches,
            config_error=config_error,
            done_error=done_error,
        )
        for node in graph.nodes
    )
    return ReuseArtifactHealthRelationReport(
        state_db_path=str(db_path),
        source_decision_count=len(decision_rows),
        source_node_count=len(graph.nodes),
        source_matches=source_matches,
        config_available=config is not None,
        config_error=config_error,
        done_record_count=len(done_rows),
        relations=relations,
        completion_authority_mode=completion_authority_mode,
        completion_source=completion_source,
        legacy_fallback_count=(
            len(done_rows)
            if legacy_fallback_count is None
            else int(legacy_fallback_count)
        ),
    )


def build_health_qualified_reuse_plan(
    dependency_plan: ObserveOnlyReusePlan,
    relation_report: CurrentReuseArtifactHealthRelationReportV2,
    *,
    dependency_policy_name: str,
) -> HealthQualifiedReusePlan:
    if not isinstance(
        relation_report,
        CurrentReuseArtifactHealthRelationReportV2,
    ):
        raise TypeError(
            "Current health qualification requires "
            "CurrentReuseArtifactHealthRelationReportV2"
        )
    return _build_health_qualified_reuse_plan(
        dependency_plan,
        relation_report,
        dependency_policy_name=dependency_policy_name,
    )


def build_health_qualified_reuse_plan_from_v1_compatibility_relations(
    dependency_plan: ObserveOnlyReusePlan,
    relation_report: ReuseArtifactHealthRelationReport,
    *,
    dependency_policy_name: str,
) -> HealthQualifiedReusePlan:
    """Apply the unchanged health gate to one released v1 report."""

    if not isinstance(relation_report, ReuseArtifactHealthRelationReport):
        raise TypeError(
            "V1 health compatibility qualification requires "
            "ReuseArtifactHealthRelationReport"
        )
    return _build_health_qualified_reuse_plan(
        dependency_plan,
        relation_report,
        dependency_policy_name=dependency_policy_name,
    )


def _build_health_qualified_reuse_plan(
    dependency_plan: ObserveOnlyReusePlan,
    relation_report: (
        CurrentReuseArtifactHealthRelationReportV2
        | ReuseArtifactHealthRelationReport
    ),
    *,
    dependency_policy_name: str,
) -> HealthQualifiedReusePlan:
    """Require registered, ready outputs after dependency eligibility."""

    dependency_by_id = {
        entry.instance_id: entry for entry in dependency_plan.entries
    }
    health_by_id = {
        relation.instance_id: relation
        for relation in relation_report.relations
    }
    if len(dependency_by_id) != len(dependency_plan.entries):
        raise ValueError("Dependency plan contains duplicate instance IDs")
    if len(health_by_id) != len(relation_report.relations):
        raise ValueError("Artifact-health report contains duplicate instances")
    if set(dependency_by_id) != set(health_by_id):
        raise ValueError(
            "Artifact-health relations do not match dependency plan instances"
        )

    entries: list[HealthQualifiedReusePlanEntry] = []
    for dependency_entry in dependency_plan.entries:
        relation = health_by_id[dependency_entry.instance_id]
        if not dependency_entry.strict_reuse_eligible:
            action: HealthQualifiedPlanAction = "blocked"
            eligible = False
            blockers = tuple(dependency_entry.blockers)
        elif relation.status == "ready":
            action = "would_reuse"
            eligible = True
            blockers = ()
        else:
            action = "blocked"
            eligible = False
            blockers = (f"artifact_health:{relation.status}",)
        entries.append(
            HealthQualifiedReusePlanEntry(
                instance_id=dependency_entry.instance_id,
                task_key=relation.task_key,
                dependency_action=dependency_entry.action,
                dependency_strict_reuse_eligible=(
                    dependency_entry.strict_reuse_eligible
                ),
                health_status=relation.status,
                action=action,
                strict_reuse_eligible=eligible,
                blockers=blockers,
            )
        )
    return HealthQualifiedReusePlan(
        policy_name=(
            f"{dependency_policy_name}+{REQUIRED_OUTPUT_HEALTH_POLICY_NAME}"
        ),
        entries=tuple(entries),
    )


def _current_relation_for_node_v2(
    *,
    node_instance_id: str,
    node_decision_id: int,
    node_task_key: TaskKey,
    decision: CurrentReuseDecisionSummaryV2 | None,
    done_records_by_coordinate: Mapping[
        str,
        list[ArtifactHealthTaskRecordV2],
    ],
    state_db_path: Path,
    config: Mapping[str, Any] | None,
    source_matches: bool,
    config_error: str | None,
    done_error: str | None,
) -> CurrentReuseArtifactHealthRelationV2:
    if (
        not source_matches
        or decision is None
        or decision.decision_id != node_decision_id
        or decision.key != node_task_key
    ):
        return _empty_current_relation_v2(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            task_coordinate_id="",
            coordinate_status="unavailable",
            status="decision_mismatch",
            detail=(
                "candidate node does not match the latest retained decision"
            ),
        )
    if decision.coordinate_status != "available":
        return _empty_current_relation_v2(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            task_coordinate_id=decision.task_coordinate_id,
            coordinate_status=decision.coordinate_status,
            status="decision_mismatch",
            detail=(
                "decision task coordinate is invalid: "
                f"{decision.coordinate_status}"
            ),
        )
    coordinate_id = decision.task_coordinate_id
    if done_error:
        return _empty_current_relation_v2(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            task_coordinate_id=coordinate_id,
            coordinate_status="available",
            status="health_unresolved",
            detail=f"completion authority load failed: {done_error}",
        )
    matches = tuple(done_records_by_coordinate.get(coordinate_id, ()))
    if not matches:
        return _empty_current_relation_v2(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            task_coordinate_id=coordinate_id,
            coordinate_status="available",
            status="done_unavailable",
            detail="no exact completion coordinate matches the decision",
        )
    if len(matches) != 1:
        return _empty_current_relation_v2(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            task_coordinate_id=coordinate_id,
            coordinate_status="available",
            status="done_ambiguous",
            detail=(
                f"{len(matches)} completion rows match the exact coordinate"
            ),
        )
    done_record = matches[0]
    if config is None:
        return _empty_current_relation_v2(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            task_coordinate_id=coordinate_id,
            coordinate_status="available",
            completion_source=done_record.completion_source,
            status="health_unresolved",
            done_at=done_record.done_at,
            detail=(
                "run config required by artifact expectations is unavailable: "
                f"{config_error or 'unknown error'}"
            ),
        )

    try:
        entries = check_task_artifact_health_v2(
            state_db_path,
            done_record,
            config=config,
        )
    except (OSError, TypeError, ValueError) as exc:
        return _empty_current_relation_v2(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            task_coordinate_id=coordinate_id,
            coordinate_status="available",
            completion_source=done_record.completion_source,
            status="health_unresolved",
            done_at=done_record.done_at,
            detail=f"artifact expectation evaluation failed: {exc}",
        )
    if not entries:
        return _empty_current_relation_v2(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            task_coordinate_id=coordinate_id,
            coordinate_status="available",
            completion_source=done_record.completion_source,
            status="expectation_unavailable",
            done_at=done_record.done_at,
            detail=(
                "task has no registered required-output health expectation"
            ),
        )

    ready_count = sum(entry.status == "ready" for entry in entries)
    missing_count = sum(entry.status == "missing" for entry in entries)
    empty_count = sum(entry.status == "empty" for entry in entries)
    unresolved_count = sum(
        entry.status == "unresolved" for entry in entries
    )
    warning_count = sum(
        finding.level == "warning"
        for entry in entries
        for finding in entry.findings
    )
    healthy = (
        ready_count == len(entries)
        and missing_count == 0
        and empty_count == 0
        and unresolved_count == 0
        and warning_count == 0
    )
    return CurrentReuseArtifactHealthRelationV2(
        instance_id=node_instance_id,
        decision_id=node_decision_id,
        task_key=node_task_key,
        task_coordinate_id=coordinate_id,
        coordinate_status="available",
        completion_source=done_record.completion_source,
        status="ready" if healthy else "unhealthy",
        done_at=done_record.done_at,
        expectation_count=len(entries),
        ready_count=ready_count,
        missing_count=missing_count,
        empty_count=empty_count,
        unresolved_count=unresolved_count,
        warning_count=warning_count,
        entries=tuple(entries),
        detail=(
            "all registered required outputs are ready"
            if healthy
            else "one or more registered required outputs are unhealthy"
        ),
    )


def _empty_current_relation_v2(
    *,
    instance_id: str,
    decision_id: int,
    task_key: TaskKey,
    task_coordinate_id: str,
    coordinate_status: CurrentReuseArtifactHealthCoordinateStatus,
    status: ReuseArtifactHealthStatus,
    detail: str,
    done_at: str | None = None,
    completion_source: TaskCompletionRecordSourceV1 | Literal[""] = "",
) -> CurrentReuseArtifactHealthRelationV2:
    return CurrentReuseArtifactHealthRelationV2(
        instance_id=instance_id,
        decision_id=decision_id,
        task_key=task_key,
        task_coordinate_id=task_coordinate_id,
        coordinate_status=coordinate_status,
        completion_source=completion_source,
        status=status,
        done_at=done_at,
        expectation_count=0,
        ready_count=0,
        missing_count=0,
        empty_count=0,
        unresolved_count=0,
        warning_count=0,
        entries=(),
        detail=detail,
    )


def _relation_for_node(
    *,
    node_instance_id: str,
    node_decision_id: int,
    node_task_key: TaskKey,
    decision: ReuseDecisionSummary | None,
    done_records_by_coordinate: Mapping[
        str,
        list[ArtifactHealthTaskRecordV2],
    ],
    state_db_path: Path,
    config: Mapping[str, Any] | None,
    source_matches: bool,
    config_error: str | None,
    done_error: str | None,
) -> ReuseArtifactHealthRelation:
    if (
        not source_matches
        or decision is None
        or decision.decision_id != node_decision_id
        or decision.key != node_task_key
    ):
        return _empty_relation(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            legacy_variant_str=(
                "" if decision is None else decision.legacy_variant_str
            ),
            task_coordinate_id="",
            status="decision_mismatch",
            detail=(
                "candidate node does not match the latest retained decision"
            ),
        )
    try:
        coordinate = _decision_task_coordinate(decision)
    except (TaskCoordinateError, TypeError, ValueError) as exc:
        return _empty_relation(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            legacy_variant_str=decision.legacy_variant_str,
            task_coordinate_id="",
            status="decision_mismatch",
            detail=(
                "decision task coordinate is invalid: "
                f"{type(exc).__name__}: {exc}"
            ),
        )
    if done_error:
        return _empty_relation(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            legacy_variant_str=decision.legacy_variant_str,
            task_coordinate_id=coordinate.task_coordinate_id,
            status="health_unresolved",
            detail=f"completion authority load failed: {done_error}",
        )
    matches = tuple(
        done_records_by_coordinate.get(
            coordinate.task_coordinate_id,
            (),
        )
    )
    if not matches:
        return _empty_relation(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            legacy_variant_str=decision.legacy_variant_str,
            task_coordinate_id=coordinate.task_coordinate_id,
            status="done_unavailable",
            detail="no exact completion coordinate matches the decision",
        )
    if len(matches) != 1:
        return _empty_relation(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            legacy_variant_str=decision.legacy_variant_str,
            task_coordinate_id=coordinate.task_coordinate_id,
            status="done_ambiguous",
            detail=(
                f"{len(matches)} completion rows match the exact coordinate"
            ),
        )
    done_record = matches[0]
    if config is None:
        return _empty_relation(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            legacy_variant_str=decision.legacy_variant_str,
            task_coordinate_id=coordinate.task_coordinate_id,
            completion_source=done_record.completion_source,
            status="health_unresolved",
            done_at=done_record.done_at,
            detail=(
                "run config required by artifact expectations is unavailable: "
                f"{config_error or 'unknown error'}"
            ),
        )

    try:
        entries = check_task_artifact_health_v2(
            state_db_path,
            done_record,
            config=config,
        )
    except (OSError, TypeError, ValueError) as exc:
        return _empty_relation(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            legacy_variant_str=decision.legacy_variant_str,
            task_coordinate_id=coordinate.task_coordinate_id,
            completion_source=done_record.completion_source,
            status="health_unresolved",
            done_at=done_record.done_at,
            detail=f"artifact expectation evaluation failed: {exc}",
        )
    if not entries:
        return _empty_relation(
            instance_id=node_instance_id,
            decision_id=node_decision_id,
            task_key=node_task_key,
            legacy_variant_str=decision.legacy_variant_str,
            task_coordinate_id=coordinate.task_coordinate_id,
            completion_source=done_record.completion_source,
            status="expectation_unavailable",
            done_at=done_record.done_at,
            detail=(
                "task has no registered required-output health expectation"
            ),
        )

    ready_count = sum(entry.status == "ready" for entry in entries)
    missing_count = sum(entry.status == "missing" for entry in entries)
    empty_count = sum(entry.status == "empty" for entry in entries)
    unresolved_count = sum(
        entry.status == "unresolved" for entry in entries
    )
    warning_count = sum(
        finding.level == "warning"
        for entry in entries
        for finding in entry.findings
    )
    healthy = (
        ready_count == len(entries)
        and missing_count == 0
        and empty_count == 0
        and unresolved_count == 0
        and warning_count == 0
    )
    return ReuseArtifactHealthRelation(
        instance_id=node_instance_id,
        decision_id=node_decision_id,
        task_key=node_task_key,
        legacy_variant_str=decision.legacy_variant_str,
        task_coordinate_id=coordinate.task_coordinate_id,
        completion_source=done_record.completion_source,
        status="ready" if healthy else "unhealthy",
        done_at=done_record.done_at,
        expectation_count=len(entries),
        ready_count=ready_count,
        missing_count=missing_count,
        empty_count=empty_count,
        unresolved_count=unresolved_count,
        warning_count=warning_count,
        entries=tuple(entries),
        detail=(
            "all registered required outputs are ready"
            if healthy
            else "one or more registered required outputs are unhealthy"
        ),
    )


def _empty_relation(
    *,
    instance_id: str,
    decision_id: int,
    task_key: TaskKey,
    legacy_variant_str: str,
    task_coordinate_id: str,
    status: ReuseArtifactHealthStatus,
    detail: str,
    done_at: str | None = None,
    completion_source: TaskCompletionRecordSourceV1 | Literal[""] = "",
) -> ReuseArtifactHealthRelation:
    return ReuseArtifactHealthRelation(
        instance_id=instance_id,
        decision_id=decision_id,
        task_key=task_key,
        legacy_variant_str=legacy_variant_str,
        task_coordinate_id=task_coordinate_id,
        completion_source=completion_source,
        status=status,
        done_at=done_at,
        expectation_count=0,
        ready_count=0,
        missing_count=0,
        empty_count=0,
        unresolved_count=0,
        warning_count=0,
        entries=(),
        detail=detail,
    )


def _decision_task_coordinate(
    decision: ReuseDecisionSummary,
) -> TaskCoordinateV1:
    coordinate = build_task_coordinate_v1(
        stage=decision.stage,
        task_base=decision.task_base,
        identity=decision.identity,
        params=decision.params,
    )
    if coordinate.legacy_variant_str != decision.legacy_variant_str:
        raise TaskCoordinateError(
            "Decision legacy variant alias differs from structured params"
        )
    return coordinate


def _health_task_coordinate_id(
    record: ArtifactHealthTaskRecordV2,
) -> str:
    if not isinstance(record, ArtifactHealthTaskRecordV2):
        raise TypeError(
            "Reuse artifact health requires ArtifactHealthTaskRecordV2"
        )
    return record.task_coordinate_id


def _resolve_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path).expanduser()
    if path.is_dir():
        if path.name == "state":
            return path / "state.sqlite"
        candidate = path / "state" / "state.sqlite"
        if candidate.exists():
            return candidate
        return path / "state.sqlite"
    return path


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
