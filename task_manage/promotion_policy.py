"""Versioned, non-authoritative Reuse v2 promotion policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Tuple

from .call_peaks_lineage_candidate import (
    CALL_PEAKS_LINEAGE_CANDIDATE_POLICY_NAME,
)
from .contract_generation_lifecycle import (
    CONTRACT_GENERATION_LIFECYCLE_POLICY_NAME,
)
from .coverage_branch_candidate import (
    COVERAGE_BRANCH_CANDIDATE_POLICY_NAME,
)
from .inline_observation_candidate import (
    INLINE_OBSERVATION_CANDIDATE_POLICY_NAME,
)
from .instance_graph import OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY
from .pipeline_contracts import TaskKey
from .reuse_artifact_health import REQUIRED_OUTPUT_HEALTH_POLICY_NAME


PROMOTION_POLICY_SCHEMA_V1 = "scpolya.reuse_v2.promotion_policy.v1"
PROMOTION_POLICY_NAME_V1 = "reuse_v2_candidate_policy_v1"
PROMOTION_POLICY_VERSION_V1 = "1"

REUSE_V2_AUTHORITY_ENV = "SCPOLYA_REUSE_V2_AUTHORITY"
REUSE_V2_POLICY_VERSION_ENV = "SCPOLYA_REUSE_V2_POLICY_VERSION"
REUSE_V2_ROLLBACK_ENV = "SCPOLYA_REUSE_V2_ROLLBACK"

# Compatibility aliases retained for the frozen Stage 12F.8 policy payload.
PLANNED_REUSE_V2_AUTHORITY_ENV = REUSE_V2_AUTHORITY_ENV
PLANNED_REUSE_V2_POLICY_ENV = REUSE_V2_POLICY_VERSION_ENV

_DIAGNOSTIC_ONLY = "diagnostic_only"
_LEGACY_AUTHORITY = "legacy_task_done_flex"

PROMOTION_TASK_KEYS_V1: Tuple[TaskKey, ...] = tuple(
    sorted(
        (
            ("annotate_bams", "annotate_slices"),
            ("annotate_peaks", "convert_peaks_bed_to_bigbed"),
            ("annotate_peaks", "convert_peaks_bed_to_gtf"),
            ("annotate_peaks", "resolve_peak_issues"),
            ("call_peaks", "combine_and_intersect_peaks"),
            ("count_matrix", "summarize_count_matrix"),
            ("rna_gene_matrix", "summarize_gene_matrix"),
            ("summarize_matrix", "summarize_matrix"),
        )
    )
)

PROMOTION_EVIDENCE_POLICIES_V1: Tuple[str, ...] = (
    OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY.name,
    CONTRACT_GENERATION_LIFECYCLE_POLICY_NAME,
    COVERAGE_BRANCH_CANDIDATE_POLICY_NAME,
    INLINE_OBSERVATION_CANDIDATE_POLICY_NAME,
    CALL_PEAKS_LINEAGE_CANDIDATE_POLICY_NAME,
    REQUIRED_OUTPUT_HEALTH_POLICY_NAME,
)

PROMOTION_PRECEDENCE_V1: Tuple[str, ...] = (
    "force_or_legacy_queued_runs",
    "unselected_tasks_keep_legacy_authority",
    "selected_complete_healthy_instances_reuse",
    "selected_incomplete_or_unhealthy_instances_recompute",
)

PROMOTION_COMPATIBILITY_ACTIONS_V1: Tuple[str, ...] = (
    "preserve_task_done_flex_on_reuse",
    "write_task_done_flex_after_recompute",
    "append_authority_decision_audit",
    "never_delete_done_or_decision_history",
    "preserve_legacy_variant_str",
)

PROMOTION_AUDIT_FIELDS_V1: Tuple[str, ...] = (
    "policy_name",
    "policy_version",
    "evidence_snapshot_hash",
    "instance_id",
    "decision_id",
    "task_key",
    "legacy_action",
    "candidate_action",
    "comparison",
    "health_status",
    "blockers",
    "operator_warnings",
)

PROMOTION_ROLLBACK_TRIGGERS_V1: Tuple[str, ...] = (
    "operator_kill_switch",
    "policy_or_evidence_error_before_dispatch",
    "candidate_audit_write_failure",
    "legacy_compatibility_write_failure",
    "post_reuse_required_output_unhealthy",
)


@dataclass(frozen=True)
class PromotionPolicyV1:
    schema_version: str
    policy_name: str
    policy_version: str
    approved_task_keys: Tuple[TaskKey, ...]
    evidence_policy_names: Tuple[str, ...]
    precedence: Tuple[str, ...]
    compatibility_actions: Tuple[str, ...]
    audit_fields: Tuple[str, ...]
    rollback_triggers: Tuple[str, ...]
    planned_authority_env: str
    planned_policy_env: str
    default_authority: Literal["legacy_task_done_flex"]
    runtime_activation_allowed: Literal[False] = False
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    def selects(self, task_key: TaskKey) -> bool:
        return task_key in self.approved_task_keys

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "strict_effect": self.strict_effect,
            "runtime_activation_allowed": self.runtime_activation_allowed,
            "default_authority": self.default_authority,
            "approved_task_keys": [
                {"stage": stage, "task_base": task_base}
                for stage, task_base in self.approved_task_keys
            ],
            "evidence_policy_names": list(self.evidence_policy_names),
            "precedence": list(self.precedence),
            "compatibility_actions": list(self.compatibility_actions),
            "audit_fields": list(self.audit_fields),
            "rollback_triggers": list(self.rollback_triggers),
            "planned_authority_env": self.planned_authority_env,
            "planned_policy_env": self.planned_policy_env,
        }


REUSE_V2_CANDIDATE_POLICY_V1 = PromotionPolicyV1(
    schema_version=PROMOTION_POLICY_SCHEMA_V1,
    policy_name=PROMOTION_POLICY_NAME_V1,
    policy_version=PROMOTION_POLICY_VERSION_V1,
    approved_task_keys=PROMOTION_TASK_KEYS_V1,
    evidence_policy_names=PROMOTION_EVIDENCE_POLICIES_V1,
    precedence=PROMOTION_PRECEDENCE_V1,
    compatibility_actions=PROMOTION_COMPATIBILITY_ACTIONS_V1,
    audit_fields=PROMOTION_AUDIT_FIELDS_V1,
    rollback_triggers=PROMOTION_ROLLBACK_TRIGGERS_V1,
    planned_authority_env=PLANNED_REUSE_V2_AUTHORITY_ENV,
    planned_policy_env=PLANNED_REUSE_V2_POLICY_ENV,
    default_authority=_LEGACY_AUTHORITY,
)


def promotion_policy_errors(
    policy: PromotionPolicyV1,
    *,
    evidence_policy_names: Tuple[str, ...] | None = None,
) -> Tuple[str, ...]:
    """Return canonical policy errors without enabling runtime authority."""

    errors: list[str] = []
    if policy.schema_version != PROMOTION_POLICY_SCHEMA_V1:
        errors.append(
            f"schema_version={policy.schema_version!r}"
        )
    if policy.policy_name != PROMOTION_POLICY_NAME_V1:
        errors.append(f"policy_name={policy.policy_name!r}")
    if policy.policy_version != PROMOTION_POLICY_VERSION_V1:
        errors.append(f"policy_version={policy.policy_version!r}")
    if policy.strict_effect != _DIAGNOSTIC_ONLY:
        errors.append(f"strict_effect={policy.strict_effect!r}")
    if policy.runtime_activation_allowed:
        errors.append("runtime_activation_allowed must remain false")
    if policy.default_authority != _LEGACY_AUTHORITY:
        errors.append(
            f"default_authority={policy.default_authority!r}"
        )
    if (
        not policy.approved_task_keys
        or len(set(policy.approved_task_keys))
        != len(policy.approved_task_keys)
        or tuple(sorted(policy.approved_task_keys))
        != policy.approved_task_keys
    ):
        errors.append("approved_task_keys are empty, duplicate, or unsorted")
    if policy.evidence_policy_names != PROMOTION_EVIDENCE_POLICIES_V1:
        errors.append("policy evidence chain differs from v1")
    if evidence_policy_names is not None and (
        policy.evidence_policy_names != evidence_policy_names
    ):
        errors.append("snapshot evidence chain differs from policy")
    if policy.precedence != PROMOTION_PRECEDENCE_V1:
        errors.append("precedence differs from v1")
    if policy.compatibility_actions != PROMOTION_COMPATIBILITY_ACTIONS_V1:
        errors.append("compatibility actions differ from v1")
    if policy.audit_fields != PROMOTION_AUDIT_FIELDS_V1:
        errors.append("audit fields differ from v1")
    if policy.rollback_triggers != PROMOTION_ROLLBACK_TRIGGERS_V1:
        errors.append("rollback triggers differ from v1")
    if policy.planned_authority_env != PLANNED_REUSE_V2_AUTHORITY_ENV:
        errors.append("planned authority environment key differs from v1")
    if policy.planned_policy_env != PLANNED_REUSE_V2_POLICY_ENV:
        errors.append("planned policy environment key differs from v1")
    return tuple(errors)


def promotion_policy_fingerprint(policy: PromotionPolicyV1) -> str:
    payload = json.dumps(
        policy.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
