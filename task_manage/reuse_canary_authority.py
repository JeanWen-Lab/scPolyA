"""Runtime authority adapter for the frozen Reuse v2 candidate replay."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Callable, Literal, Mapping, Tuple

from .artifact_health import (
    ArtifactHealthTaskRecordV2,
    check_task_artifact_health_v2,
)
from .promotion_policy import (
    PROMOTION_POLICY_NAME_V1,
    PROMOTION_POLICY_VERSION_V1,
    REUSE_V2_AUTHORITY_ENV,
    REUSE_V2_CANDIDATE_POLICY_V1,
    REUSE_V2_POLICY_VERSION_ENV,
    REUSE_V2_ROLLBACK_ENV,
    promotion_policy_fingerprint,
)
from .promotion_replay import (
    PromotionReplayEntry,
    PromotionReplayReport,
    build_promotion_replay_report,
)
from .task_completion_storage_policy import (
    TASK_COMPLETION_STORAGE_POLICY_NAME,
    TASK_COMPLETION_STORAGE_POLICY_VERSION_V3,
)
from .reuse_authority_audit import ReuseAuthorityAuditEventV1, ReuseAuthorityAuditV1
from .task_instance_v3 import TaskInstanceV3


REUSE_V2_CANARY_AUTHORITY = "canary"
REUSE_V2_DEFAULT_AUTHORITY = "v2"
REUSE_V2_LEGACY_AUTHORITY = "legacy"
_APPROVED_REUSE_V2_AUTHORITIES = frozenset(
    {
        REUSE_V2_CANARY_AUTHORITY,
        REUSE_V2_DEFAULT_AUTHORITY,
    }
)

CanaryDecisionAction = Literal[
    "v2_reuse",
    "v2_recompute",
    "v2_fallback_legacy",
]


@dataclass(frozen=True)
class ReuseV2CanaryOutputHealth:
    checked: bool
    allow_reuse: bool
    expectation_count: int
    ready_count: int
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class ReuseV2CanaryDecision:
    applies: bool
    action: CanaryDecisionAction | None
    allow_reuse: bool
    source_decision_id: int | None
    policy_name: str
    policy_version: str
    policy_fingerprint: str
    evidence_snapshot_hash: str
    legacy_action: str
    candidate_action: str
    comparison: str
    health_status: str
    blockers: Tuple[str, ...]
    operator_warnings: Tuple[str, ...]
    reasons: Tuple[str, ...]
    rollback_reason: str = ""

    def build_audit(
        self,
        *,
        event_type: ReuseAuthorityAuditEventV1,
        invocation_id: str,
        task_instance: TaskInstanceV3,
        source_decision_id: int | None = None,
    ) -> ReuseAuthorityAuditV1:
        if not self.applies or self.action is None:
            raise ValueError("Cannot audit a non-applicable canary decision")
        return ReuseAuthorityAuditV1(
            event_type=event_type,
            invocation_id=invocation_id,
            policy_name=self.policy_name,
            policy_version=self.policy_version,
            policy_fingerprint=self.policy_fingerprint,
            evidence_snapshot_hash=self.evidence_snapshot_hash,
            source_decision_id=(
                source_decision_id
                if source_decision_id is not None
                else self.source_decision_id
            ),
            task_key=task_instance.key,
            instance_id=task_instance.instance_id,
            legacy_action=self.legacy_action,
            candidate_action=self.candidate_action,
            comparison=self.comparison,
            health_status=self.health_status,
            blockers=self.blockers,
            operator_warnings=self.operator_warnings,
            rollback_reason=self.rollback_reason,
        )


@dataclass
class ReuseV2CanaryAuthoritySession:
    state_or_db_path: str | Path
    invocation_id: str
    report_builder: Callable[[str | Path], PromotionReplayReport] = (
        build_promotion_replay_report
    )
    _report: PromotionReplayReport | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _entries_by_instance_id: Mapping[str, PromotionReplayEntry] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _rollback_reason: str = field(default="", init=False, repr=False)
    _snapshot_build_count: int = field(default=0, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    @property
    def rollback_reason(self) -> str:
        with self._lock:
            return self._rollback_reason

    @property
    def snapshot_build_count(self) -> int:
        with self._lock:
            return self._snapshot_build_count

    def evaluate(self, task_instance: TaskInstanceV3) -> ReuseV2CanaryDecision:
        with self._lock:
            authority_mode = _authority_mode()
            if authority_mode == REUSE_V2_LEGACY_AUTHORITY:
                return _not_applicable()
            if not REUSE_V2_CANDIDATE_POLICY_V1.selects(task_instance.key):
                return _not_applicable()
            if authority_mode not in _APPROVED_REUSE_V2_AUTHORITIES:
                reason = (
                    "authority_mode_not_approved:"
                    f"{authority_mode}"
                )
                self._activate_rollback_unlocked(reason)
                return self._fallback(
                    task_instance,
                    rollback_reason=reason,
                )

            operator_rollback = _operator_rollback_reason()
            if operator_rollback:
                self._activate_rollback_unlocked(operator_rollback)
            if self._rollback_reason:
                return self._fallback(
                    task_instance,
                    rollback_reason=self._rollback_reason,
                )

            configured_version = _configured_policy_version(
                authority_mode
            )
            if configured_version != PROMOTION_POLICY_VERSION_V1:
                reason = (
                    "policy_version_not_approved:"
                    f"{configured_version or 'unset'}"
                )
                self._activate_rollback_unlocked(reason)
                return self._fallback(task_instance, rollback_reason=reason)

            try:
                report = self._ensure_report_unlocked()
            except Exception as exc:
                reason = (
                    "promotion_replay_build_failed:"
                    f"{type(exc).__name__}:{exc}"
                )
                self._activate_rollback_unlocked(reason)
                return self._fallback(task_instance, rollback_reason=reason)

            if not report.replay_valid:
                reason = "promotion_replay_invalid:" + "|".join(report.errors)
                self._activate_rollback_unlocked(reason)
                return self._fallback(
                    task_instance,
                    report=report,
                    rollback_reason=reason,
                )
            expected_fingerprint = promotion_policy_fingerprint(
                REUSE_V2_CANDIDATE_POLICY_V1
            )
            if (
                report.policy.policy_name != PROMOTION_POLICY_NAME_V1
                or report.policy.policy_version != PROMOTION_POLICY_VERSION_V1
                or report.policy_fingerprint != expected_fingerprint
            ):
                reason = "promotion_replay_policy_mismatch"
                self._activate_rollback_unlocked(reason)
                return self._fallback(
                    task_instance,
                    report=report,
                    rollback_reason=reason,
                )

            entry = self._entries_by_instance_id.get(task_instance.instance_id)
            if entry is None:
                reason = "exact_instance_absent_from_replay"
                self._activate_rollback_unlocked(reason)
                return self._fallback(
                    task_instance,
                    report=report,
                    rollback_reason=reason,
                )
            if entry.task_key != task_instance.key or not entry.selected_by_policy:
                reason = "exact_instance_policy_relation_mismatch"
                self._activate_rollback_unlocked(reason)
                return self._fallback(
                    task_instance,
                    report=report,
                    entry=entry,
                    rollback_reason=reason,
                )
            if entry.legacy_action != "skip":
                reason = f"candidate_entry_not_legacy_skip:{entry.candidate_action}"
                self._activate_rollback_unlocked(reason)
                return self._fallback(
                    task_instance,
                    report=report,
                    entry=entry,
                    rollback_reason=reason,
                )

            if entry.candidate_action == "candidate_reuse":
                action: CanaryDecisionAction = "v2_reuse"
                allow_reuse = True
            elif entry.candidate_action == "candidate_recompute":
                action = "v2_recompute"
                allow_reuse = False
            else:
                reason = f"unsupported_candidate_action:{entry.candidate_action}"
                self._activate_rollback_unlocked(reason)
                return self._fallback(
                    task_instance,
                    report=report,
                    entry=entry,
                    rollback_reason=reason,
                )

            return ReuseV2CanaryDecision(
                applies=True,
                action=action,
                allow_reuse=allow_reuse,
                source_decision_id=entry.decision_id,
                policy_name=report.policy.policy_name,
                policy_version=report.policy.policy_version,
                policy_fingerprint=report.policy_fingerprint,
                evidence_snapshot_hash=report.evidence_snapshot_hash,
                legacy_action=entry.legacy_action,
                candidate_action=entry.candidate_action,
                comparison=entry.comparison,
                health_status=entry.health_status,
                blockers=entry.blockers,
                operator_warnings=entry.operator_warnings,
                reasons=(
                    _authority_reason(authority_mode),
                    f"promotion_policy={report.policy.policy_name}",
                    f"promotion_policy_version={report.policy.policy_version}",
                    f"evidence_snapshot={report.evidence_snapshot_hash}",
                    f"source_decision_id={entry.decision_id}",
                    f"candidate_action={entry.candidate_action}",
                    *(
                        ("legacy_completion_reconciled_from_done_and_ledger",)
                        if entry.legacy_completion_reconciled
                        else ()
                    ),
                ),
            )

    def completion_decision(
        self,
        decision: ReuseV2CanaryDecision,
    ) -> ReuseV2CanaryDecision:
        if decision.action != "v2_recompute":
            raise ValueError("Only v2_recompute has a completion decision")
        return ReuseV2CanaryDecision(
            applies=True,
            action="v2_recompute",
            allow_reuse=True,
            source_decision_id=decision.source_decision_id,
            policy_name=decision.policy_name,
            policy_version=decision.policy_version,
            policy_fingerprint=decision.policy_fingerprint,
            evidence_snapshot_hash=decision.evidence_snapshot_hash,
            legacy_action=decision.legacy_action,
            candidate_action=decision.candidate_action,
            comparison=decision.comparison,
            health_status=decision.health_status,
            blockers=decision.blockers,
            operator_warnings=decision.operator_warnings,
            reasons=(
                *decision.reasons,
                "candidate_recompute_completed",
                "structured_completion_written",
                (
                    "completion_storage_policy="
                    f"{TASK_COMPLETION_STORAGE_POLICY_NAME}:"
                    f"{TASK_COMPLETION_STORAGE_POLICY_VERSION_V3}"
                ),
            ),
        )

    def invalidate_after_recompute(self) -> None:
        with self._lock:
            self._report = None
            self._entries_by_instance_id = {}

    def activate_rollback(self, reason: str) -> str:
        with self._lock:
            self._activate_rollback_unlocked(reason)
            return self._rollback_reason

    def fallback_after_failure(
        self,
        task_instance: TaskInstanceV3,
        reason: str,
    ) -> ReuseV2CanaryDecision:
        with self._lock:
            self._activate_rollback_unlocked(reason)
            return self._fallback(
                task_instance,
                report=self._report,
                entry=self._entries_by_instance_id.get(task_instance.instance_id),
                rollback_reason=self._rollback_reason,
            )

    def _ensure_report_unlocked(self) -> PromotionReplayReport:
        if self._report is None:
            report = self.report_builder(self.state_or_db_path)
            entries = {
                entry.instance_id: entry for entry in report.entries
            }
            if len(entries) != len(report.entries):
                raise ValueError("promotion replay contains duplicate instances")
            self._report = report
            self._entries_by_instance_id = entries
            self._snapshot_build_count += 1
        return self._report

    def _activate_rollback_unlocked(self, reason: str) -> None:
        normalized = str(reason).strip() or "unspecified_canary_rollback"
        if not self._rollback_reason:
            self._rollback_reason = normalized

    def _fallback(
        self,
        task_instance: TaskInstanceV3,
        *,
        report: PromotionReplayReport | None = None,
        entry: PromotionReplayEntry | None = None,
        rollback_reason: str,
    ) -> ReuseV2CanaryDecision:
        policy = REUSE_V2_CANDIDATE_POLICY_V1
        return ReuseV2CanaryDecision(
            applies=True,
            action="v2_fallback_legacy",
            allow_reuse=True,
            source_decision_id=entry.decision_id if entry is not None else None,
            policy_name=(
                report.policy.policy_name if report is not None else policy.policy_name
            ),
            policy_version=(
                report.policy.policy_version
                if report is not None
                else policy.policy_version
            ),
            policy_fingerprint=(
                report.policy_fingerprint
                if report is not None
                else promotion_policy_fingerprint(policy)
            ),
            evidence_snapshot_hash=(
                report.evidence_snapshot_hash if report is not None else ""
            ),
            legacy_action=entry.legacy_action if entry is not None else "skip",
            candidate_action=(
                entry.candidate_action if entry is not None else "fallback_legacy"
            ),
            comparison=(
                entry.comparison if entry is not None else "legacy_skip_fallback"
            ),
            health_status=entry.health_status if entry is not None else "unknown",
            blockers=entry.blockers if entry is not None else (),
            operator_warnings=(
                entry.operator_warnings if entry is not None else ()
            ),
            reasons=(
                _authority_reason(),
                "fallback_to_legacy_authority",
                f"rollback_reason={rollback_reason}",
            ),
            rollback_reason=rollback_reason,
        )


def reuse_v2_canary_authority_requested() -> bool:
    return _authority_mode() == REUSE_V2_CANARY_AUTHORITY


def reuse_v2_default_authority_requested() -> bool:
    return _authority_mode() == REUSE_V2_DEFAULT_AUTHORITY


def reuse_v2_authority_adapter_requested() -> bool:
    """Claim precedence over hybrid for v2 or invalid nonlegacy modes."""

    return _authority_mode() != REUSE_V2_LEGACY_AUTHORITY


def evaluate_canary_required_output_health(
    *,
    state_or_db_path: str | Path,
    config: Mapping[str, object],
    task_instance: TaskInstanceV3,
) -> ReuseV2CanaryOutputHealth:
    """Recheck selected required outputs immediately before reuse audit."""

    record = ArtifactHealthTaskRecordV2(
        task_coordinate=task_instance.task_coordinate,
        done_at="",
    )
    try:
        entries = check_task_artifact_health_v2(
            state_or_db_path,
            record,
            config=config,
        )
    except Exception as exc:
        return ReuseV2CanaryOutputHealth(
            checked=False,
            allow_reuse=False,
            expectation_count=0,
            ready_count=0,
            reasons=(
                "required_output_health_check_failed:"
                f"{type(exc).__name__}:{exc}",
            ),
        )
    ready_count = sum(entry.status == "ready" for entry in entries)
    allow_reuse = bool(entries) and ready_count == len(entries)
    reasons = ["required_output_health_rechecked"]
    if not entries:
        reasons.append("required_output_expectation_unavailable")
    elif allow_reuse:
        reasons.append("required_outputs_still_ready")
    else:
        reasons.extend(
            f"required_output_status:{entry.expectation.label}={entry.status}"
            for entry in entries
            if entry.status != "ready"
        )
    return ReuseV2CanaryOutputHealth(
        checked=bool(entries),
        allow_reuse=allow_reuse,
        expectation_count=len(entries),
        ready_count=ready_count,
        reasons=tuple(reasons),
    )


def _operator_rollback_reason() -> str:
    value = os.environ.get(REUSE_V2_ROLLBACK_ENV, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return "operator_kill_switch"
    return ""


def _authority_mode() -> str:
    return (
        os.environ.get(
            REUSE_V2_AUTHORITY_ENV,
            REUSE_V2_DEFAULT_AUTHORITY,
        ).strip().lower()
        or REUSE_V2_DEFAULT_AUTHORITY
    )


def _configured_policy_version(authority_mode: str) -> str:
    configured = os.environ.get(
        REUSE_V2_POLICY_VERSION_ENV,
        "",
    ).strip()
    if not configured and authority_mode == REUSE_V2_DEFAULT_AUTHORITY:
        return PROMOTION_POLICY_VERSION_V1
    return configured


def _authority_reason(authority_mode: str | None = None) -> str:
    mode = authority_mode or _authority_mode()
    if mode == REUSE_V2_DEFAULT_AUTHORITY:
        return "reuse_v2_default_authority"
    if mode == REUSE_V2_CANARY_AUTHORITY:
        return "canary_authority_explicit"
    return f"reuse_v2_authority_mode={mode}"


def _not_applicable() -> ReuseV2CanaryDecision:
    policy = REUSE_V2_CANDIDATE_POLICY_V1
    return ReuseV2CanaryDecision(
        applies=False,
        action=None,
        allow_reuse=True,
        source_decision_id=None,
        policy_name=policy.policy_name,
        policy_version=policy.policy_version,
        policy_fingerprint=promotion_policy_fingerprint(policy),
        evidence_snapshot_hash="",
        legacy_action="skip",
        candidate_action="legacy_only",
        comparison="legacy_skip_outside_policy",
        health_status="unknown",
        blockers=(),
        operator_warnings=(),
        reasons=("canary_authority_not_applicable",),
    )
