"""Read-only replay of the first Reuse v2 promotion candidate policy."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Tuple

from .decision_input_report import DecisionInputStatusReport
from .instance_graph import DynamicInstanceGraph
from .pipeline_contracts import TaskKey
from .promotion_policy import (
    REUSE_V2_CANDIDATE_POLICY_V1,
    PromotionPolicyV1,
    promotion_policy_errors,
    promotion_policy_fingerprint,
)
from .reuse_artifact_health import (
    CurrentReuseArtifactHealthRelationReportV2,
    HealthQualifiedReusePlan,
    ReuseArtifactHealthStatus,
)
from .reuse_readiness import (
    PromotionEvidenceSnapshot,
    build_promotion_evidence_snapshot,
)
from .task_completion import (
    LEGACY_SCHEDULER_AUTHORITY_V1,
    TaskSchedulerAuthorityV1,
)


PromotionReplayAction = Literal[
    "candidate_reuse",
    "candidate_recompute",
    "legacy_only",
    "legacy_run",
]
PromotionReplayComparison = Literal[
    "legacy_skip_candidate_reuse",
    "legacy_skip_candidate_recompute",
    "legacy_skip_outside_policy",
    "legacy_run_preserved",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"
_LEGACY_SCHEDULER = LEGACY_SCHEDULER_AUTHORITY_V1


@dataclass(frozen=True)
class PromotionReplayEntry:
    instance_id: str
    decision_id: int
    task_key: TaskKey
    observed_action: str
    legacy_action: Literal["skip", "run"]
    selected_by_policy: bool
    candidate_action: PromotionReplayAction
    comparison: PromotionReplayComparison
    future_audit_action: str | None
    health_status: ReuseArtifactHealthStatus
    dependency_strict_reuse_eligible: bool
    strict_reuse_eligible: bool
    blockers: Tuple[str, ...]
    operator_warnings: Tuple[str, ...]
    legacy_completion_reconciled: bool = False

    @property
    def changes_legacy_dispatch(self) -> bool:
        return self.comparison == "legacy_skip_candidate_recompute"

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "decision_id": self.decision_id,
            "task_key": {
                "stage": self.task_key[0],
                "task_base": self.task_key[1],
            },
            "observed_action": self.observed_action,
            "legacy_action": self.legacy_action,
            "selected_by_policy": self.selected_by_policy,
            "candidate_action": self.candidate_action,
            "comparison": self.comparison,
            "future_audit_action": self.future_audit_action,
            "health_status": self.health_status,
            "dependency_strict_reuse_eligible": (
                self.dependency_strict_reuse_eligible
            ),
            "strict_reuse_eligible": self.strict_reuse_eligible,
            "blockers": list(self.blockers),
            "operator_warnings": list(self.operator_warnings),
            "legacy_completion_reconciled": (
                self.legacy_completion_reconciled
            ),
            "changes_legacy_dispatch": self.changes_legacy_dispatch,
        }


@dataclass(frozen=True)
class PromotionReplayReport:
    state_db_path: str
    policy: PromotionPolicyV1
    policy_fingerprint: str
    evidence_snapshot_hash: str
    evidence_policy_names: Tuple[str, ...]
    source_matches: bool
    config_available: bool
    replay_valid: bool
    errors: Tuple[str, ...]
    entries: Tuple[PromotionReplayEntry, ...]
    scheduler_authority: TaskSchedulerAuthorityV1 = _LEGACY_SCHEDULER
    runtime_activation_allowed: Literal[False] = False
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    @property
    def action_counts(self) -> dict[str, int]:
        return _counts(entry.candidate_action for entry in self.entries)

    @property
    def comparison_counts(self) -> dict[str, int]:
        return _counts(entry.comparison for entry in self.entries)

    @property
    def task_action_counts(self) -> dict[str, dict[str, int]]:
        counts: dict[str, Counter[str]] = {}
        for entry in self.entries:
            label = f"{entry.task_key[0]}:{entry.task_key[1]}"
            counts.setdefault(label, Counter())[entry.candidate_action] += 1
        return {
            task: dict(sorted(task_counts.items()))
            for task, task_counts in sorted(counts.items())
        }

    @property
    def selected_instance_count(self) -> int:
        return sum(entry.selected_by_policy for entry in self.entries)

    @property
    def candidate_reuse_count(self) -> int:
        return int(self.action_counts.get("candidate_reuse", 0))

    @property
    def candidate_recompute_count(self) -> int:
        return int(self.action_counts.get("candidate_recompute", 0))

    @property
    def legacy_dispatch_change_count(self) -> int:
        return sum(entry.changes_legacy_dispatch for entry in self.entries)

    @property
    def operator_warning_count(self) -> int:
        return sum(bool(entry.operator_warnings) for entry in self.entries)

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        entries = self.entries
        if limit is not None:
            entries = entries[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "strict_effect": self.strict_effect,
            "runtime_activation_allowed": self.runtime_activation_allowed,
            "scheduler_authority": self.scheduler_authority,
            "policy": self.policy.as_dict(),
            "policy_fingerprint": self.policy_fingerprint,
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "evidence_policy_names": list(self.evidence_policy_names),
            "source_matches": self.source_matches,
            "config_available": self.config_available,
            "replay_valid": self.replay_valid,
            "errors": list(self.errors),
            "entry_count": len(self.entries),
            "selected_instance_count": self.selected_instance_count,
            "candidate_reuse_count": self.candidate_reuse_count,
            "candidate_recompute_count": self.candidate_recompute_count,
            "legacy_dispatch_change_count": (
                self.legacy_dispatch_change_count
            ),
            "operator_warning_count": self.operator_warning_count,
            "action_counts": self.action_counts,
            "comparison_counts": self.comparison_counts,
            "task_action_counts": self.task_action_counts,
            "entries": [entry.as_dict() for entry in entries],
        }


def build_promotion_replay_report(
    state_or_db_path: str | Path,
    *,
    policy: PromotionPolicyV1 = REUSE_V2_CANDIDATE_POLICY_V1,
) -> PromotionReplayReport:
    """Replay the candidate policy over one immutable production snapshot."""

    snapshot = build_promotion_evidence_snapshot(state_or_db_path)
    source_errors: list[str] = []
    if not Path(snapshot.state_db_path).is_file():
        source_errors.append(
            f"state database not found: {snapshot.state_db_path}"
        )
    if not snapshot.contract_generation_candidate.source_matches:
        source_errors.append("contract generation source mismatch")
    lifecycle = snapshot.contract_generation_candidate.lifecycle_report
    if lifecycle.error:
        source_errors.append(
            f"contract generation lifecycle error: {lifecycle.error}"
        )
    if lifecycle.unresolved_logical_slot_count:
        source_errors.append(
            "contract generation lifecycle has unresolved logical slots"
        )
    if snapshot.coverage_branch_candidate.application_status_counts.get(
        "source_mismatch_unchanged",
        0,
    ):
        source_errors.append("coverage branch source mismatch")
    inline_candidate = snapshot.inline_observation_candidate
    if inline_candidate.application_status_counts.get(
        "source_mismatch_unchanged",
        0,
    ):
        source_errors.append("inline observation source mismatch")
    if inline_candidate.observation_error:
        source_errors.append(
            "inline observation load error: "
            f"{inline_candidate.observation_error}"
        )
    if (
        snapshot.final_graph.nodes
        and not inline_candidate.observation_table_available
    ):
        source_errors.append("inline observation table unavailable")
    return build_promotion_replay_report_from_snapshot(
        snapshot,
        policy=policy,
        source_errors=source_errors,
    )


def build_promotion_replay_report_from_snapshot(
    snapshot: PromotionEvidenceSnapshot,
    *,
    policy: PromotionPolicyV1 = REUSE_V2_CANDIDATE_POLICY_V1,
    source_errors: Iterable[str] = (),
) -> PromotionReplayReport:
    return build_promotion_replay_report_from_components(
        snapshot.final_graph,
        snapshot.artifact_health_plan,
        snapshot.artifact_health_relations,
        snapshot.input_report,
        evidence_policy_names=snapshot.evidence_policy_names,
        state_db_path=snapshot.state_db_path,
        policy=policy,
        source_errors=source_errors,
    )


def build_promotion_replay_report_from_components(
    graph: DynamicInstanceGraph,
    health_plan: HealthQualifiedReusePlan,
    health_relations: CurrentReuseArtifactHealthRelationReportV2,
    input_report: DecisionInputStatusReport,
    *,
    evidence_policy_names: Tuple[str, ...],
    state_db_path: str = "",
    policy: PromotionPolicyV1 = REUSE_V2_CANDIDATE_POLICY_V1,
    source_errors: Iterable[str] = (),
) -> PromotionReplayReport:
    """Classify exact candidate instances without writing scheduler state."""

    if not isinstance(
        health_relations,
        CurrentReuseArtifactHealthRelationReportV2,
    ):
        raise TypeError(
            "Current promotion replay requires "
            "CurrentReuseArtifactHealthRelationReportV2"
        )
    source_error_rows = tuple(
        str(error) for error in source_errors if str(error)
    )
    nodes_by_id = graph.nodes_by_instance_id
    if len(nodes_by_id) != len(graph.nodes):
        raise ValueError("Promotion replay graph contains duplicate instances")
    health_by_id = {
        entry.instance_id: entry for entry in health_plan.entries
    }
    relation_by_id = {
        relation.instance_id: relation
        for relation in health_relations.relations
    }
    if len(health_by_id) != len(health_plan.entries):
        raise ValueError("Promotion replay health plan contains duplicates")
    if len(relation_by_id) != len(health_relations.relations):
        raise ValueError("Promotion replay health relations contain duplicates")
    if set(nodes_by_id) != set(health_by_id):
        raise ValueError(
            "Promotion replay health plan does not match graph instances"
        )
    if set(nodes_by_id) != set(relation_by_id):
        raise ValueError(
            "Promotion replay health relations do not match graph instances"
        )

    warning_by_id: dict[str, list[str]] = {}
    for input_entry in input_report.entries:
        if input_entry.instance_id not in nodes_by_id:
            raise ValueError(
                "Promotion replay input warning is absent from the graph: "
                f"{input_entry.instance_id}"
            )
        if input_entry.warning:
            warning_by_id.setdefault(input_entry.instance_id, []).append(
                input_entry.detail
            )

    errors = list(
        promotion_policy_errors(
            policy,
            evidence_policy_names=evidence_policy_names,
        )
    )
    errors.extend(source_error_rows)
    expected_health_policy_name = "+".join(evidence_policy_names)
    if health_plan.policy_name != expected_health_policy_name:
        errors.append(
            "health plan policy chain differs from evidence snapshot"
        )
    if not health_relations.source_matches:
        errors.append("artifact-health source mismatch")
    if not health_relations.config_available and graph.nodes:
        errors.append(
            "artifact-health config unavailable: "
            f"{health_relations.config_error or 'unknown error'}"
        )
    errors = list(dict.fromkeys(errors))
    replay_valid = not errors
    global_blockers = tuple(
        f"promotion_replay:{error}" for error in errors
    )

    entries: list[PromotionReplayEntry] = []
    ordered_nodes = sorted(
        graph.nodes,
        key=lambda node: (
            node.stage,
            node.task_base,
            node.instance_id,
        ),
    )
    for node in ordered_nodes:
        health = health_by_id[node.instance_id]
        relation = relation_by_id[node.instance_id]
        selected = policy.selects(node.key)
        active_run_observation = (
            node.queued
            or node.observed_action in {
                "would_run",
                "hybrid_recompute",
                "v2_recompute",
            }
        ) and not node.legacy_completion_reconciled
        must_run = (
            node.force
            or not node.legacy_done
            or active_run_observation
        )
        if must_run:
            legacy_action: Literal["skip", "run"] = "run"
            candidate_action: PromotionReplayAction = "legacy_run"
            comparison: PromotionReplayComparison = "legacy_run_preserved"
            future_audit_action = None
            blockers = tuple(health.blockers)
        elif not selected:
            legacy_action = "skip"
            candidate_action = "legacy_only"
            comparison = "legacy_skip_outside_policy"
            future_audit_action = None
            blockers = tuple(health.blockers)
        elif replay_valid and health.strict_reuse_eligible:
            legacy_action = "skip"
            candidate_action = "candidate_reuse"
            comparison = "legacy_skip_candidate_reuse"
            future_audit_action = "v2_reuse"
            blockers = ()
        else:
            legacy_action = "skip"
            candidate_action = "candidate_recompute"
            comparison = "legacy_skip_candidate_recompute"
            future_audit_action = "v2_recompute"
            blockers = tuple(
                dict.fromkeys((*global_blockers, *health.blockers))
            )
            if not blockers:
                blockers = (
                    f"artifact_health:{relation.status}",
                )
        entries.append(
            PromotionReplayEntry(
                instance_id=node.instance_id,
                decision_id=node.decision_id,
                task_key=node.key,
                observed_action=node.observed_action,
                legacy_action=legacy_action,
                selected_by_policy=selected,
                candidate_action=candidate_action,
                comparison=comparison,
                future_audit_action=future_audit_action,
                health_status=relation.status,
                dependency_strict_reuse_eligible=(
                    health.dependency_strict_reuse_eligible
                ),
                strict_reuse_eligible=health.strict_reuse_eligible,
                blockers=blockers,
                operator_warnings=tuple(
                    warning_by_id.get(node.instance_id, ())
                ),
                legacy_completion_reconciled=(
                    node.legacy_completion_reconciled
                ),
            )
        )

    policy_fingerprint = promotion_policy_fingerprint(policy)
    scheduler_authority = health_relations.scheduler_authority
    snapshot_hash = _snapshot_hash(
        policy_fingerprint=policy_fingerprint,
        evidence_policy_names=evidence_policy_names,
        errors=tuple(errors),
        entries=tuple(entries),
        scheduler_authority=scheduler_authority,
    )
    return PromotionReplayReport(
        state_db_path=state_db_path or graph.state_db_path,
        policy=policy,
        policy_fingerprint=policy_fingerprint,
        evidence_snapshot_hash=snapshot_hash,
        evidence_policy_names=evidence_policy_names,
        source_matches=(
            health_relations.source_matches
            and not source_error_rows
        ),
        config_available=health_relations.config_available,
        replay_valid=replay_valid,
        errors=tuple(errors),
        entries=tuple(entries),
        scheduler_authority=scheduler_authority,
    )


def format_promotion_replay_report(
    report: PromotionReplayReport,
    *,
    limit: int = 20,
) -> str:
    lines = [
        f"Reuse v2 promotion replay: {report.state_db_path or '-'}",
        f"policy: {report.policy.policy_name}@"
        f"{report.policy.policy_version}",
        f"replay valid: {int(report.replay_valid)}",
        f"runtime activation allowed: "
        f"{int(report.runtime_activation_allowed)}",
        f"scheduler authority: {report.scheduler_authority}",
        f"evidence snapshot: {report.evidence_snapshot_hash}",
        f"instances: {len(report.entries)} "
        f"(selected={report.selected_instance_count})",
        f"candidate reuse: {report.candidate_reuse_count}",
        f"candidate recompute: {report.candidate_recompute_count}",
        f"legacy dispatch changes: "
        f"{report.legacy_dispatch_change_count}",
        f"operator warnings: {report.operator_warning_count}",
    ]
    if report.errors:
        lines.append("errors:")
        lines.extend(f"  {error}" for error in report.errors)
    if report.action_counts:
        lines.append("actions:")
        lines.extend(
            f"  {action}: {count}"
            for action, count in report.action_counts.items()
        )
    if report.task_action_counts:
        lines.append("task actions:")
        for task, counts in report.task_action_counts.items():
            summary = ", ".join(
                f"{action}={count}"
                for action, count in counts.items()
            )
            lines.append(f"  {task}: {summary}")
    selected_entries = tuple(
        entry for entry in report.entries if entry.selected_by_policy
    )
    for entry in selected_entries[: max(0, int(limit))]:
        blockers = ",".join(entry.blockers) or "-"
        lines.append(
            f"  #{entry.decision_id} "
            f"{entry.task_key[0]}:{entry.task_key[1]} "
            f"{entry.candidate_action} health={entry.health_status} "
            f"blockers=[{blockers}]"
        )
    return "\n".join(lines)


def _snapshot_hash(
    *,
    policy_fingerprint: str,
    evidence_policy_names: Tuple[str, ...],
    errors: Tuple[str, ...],
    entries: Tuple[PromotionReplayEntry, ...],
    scheduler_authority: TaskSchedulerAuthorityV1,
) -> str:
    payload: Mapping[str, Any] = {
        "policy_fingerprint": policy_fingerprint,
        "evidence_policy_names": list(evidence_policy_names),
        "errors": list(errors),
        "scheduler_authority": scheduler_authority,
        "entries": [entry.as_dict() for entry in entries],
    }
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
