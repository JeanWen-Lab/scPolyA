"""Read-only availability diagnostics for persisted branch-artifact manifests."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Tuple

from .branch_artifacts import (
    BRANCH_ARTIFACT_MANIFEST_V1_SCHEMA,
    COVERAGE_BRANCH_TASK_KEYS,
)
from .reuse_report import (
    CurrentReuseDecisionSummaryV2,
    load_current_reuse_decision_summaries_v2,
)


TaskKey = Tuple[str, str]
ManifestAvailabilityStatus = Literal[
    "available",
    "missing",
    "unavailable",
    "unsupported",
    "error",
    "malformed",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"
_MANIFEST_STATUSES = frozenset({"available", "unavailable", "unsupported", "error"})
_BRANCH_STATUSES = frozenset({"active", "no_op", "unresolved", "unsupported"})


@dataclass(frozen=True)
class BranchManifestSample:
    """One latest coverage decision's persisted manifest availability."""

    instance_id: str
    decision_id: int
    task_key: TaskKey
    manifest_status: ManifestAvailabilityStatus
    branch_status: str | None
    branch_role: str | None
    effective_input_artifact: str | None
    effective_output_artifact: str | None
    selected_fields: Tuple[Tuple[str, str], ...]
    reasons: Tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "decision_id": self.decision_id,
            "task_key": {"stage": self.task_key[0], "task_base": self.task_key[1]},
            "manifest_status": self.manifest_status,
            "branch_status": self.branch_status,
            "branch_role": self.branch_role,
            "effective_input_artifact": self.effective_input_artifact,
            "effective_output_artifact": self.effective_output_artifact,
            "selected_fields": [
                {"key": key, "value": value}
                for key, value in self.selected_fields
            ],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class BranchManifestTaskAvailability:
    """Availability grouped by one coverage task type."""

    task_key: TaskKey
    instance_count: int
    manifest_status_counts: Mapping[str, int]
    branch_status_counts: Mapping[str, int]
    branch_role_counts: Mapping[str, int]
    samples: Tuple[BranchManifestSample, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_key": {"stage": self.task_key[0], "task_base": self.task_key[1]},
            "instance_count": self.instance_count,
            "manifest_status_counts": dict(self.manifest_status_counts),
            "branch_status_counts": dict(self.branch_status_counts),
            "branch_role_counts": dict(self.branch_role_counts),
            "samples": [sample.as_dict() for sample in self.samples],
        }


@dataclass(frozen=True)
class BranchManifestAvailabilityReport:
    """A manifest-only report that never creates graph or scheduler evidence."""

    state_db_path: str
    source_decision_count: int
    latest_instance_count: int
    coverage_instance_count: int
    manifest_status_counts: Mapping[str, int]
    branch_status_counts: Mapping[str, int]
    branch_role_counts: Mapping[str, int]
    tasks: Tuple[BranchManifestTaskAvailability, ...]
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    @property
    def manifest_available_count(self) -> int:
        return int(self.manifest_status_counts.get("available", 0))

    @property
    def manifest_missing_count(self) -> int:
        return int(self.manifest_status_counts.get("missing", 0))

    @property
    def manifest_unresolved_count(self) -> int:
        return int(self.manifest_status_counts.get("unavailable", 0))

    @property
    def manifest_unsupported_count(self) -> int:
        return int(self.manifest_status_counts.get("unsupported", 0))

    @property
    def manifest_error_count(self) -> int:
        return int(self.manifest_status_counts.get("error", 0))

    @property
    def manifest_malformed_count(self) -> int:
        return int(self.manifest_status_counts.get("malformed", 0))

    def as_dict(self) -> dict[str, Any]:
        return {
            "state_db_path": self.state_db_path,
            "source_decision_count": self.source_decision_count,
            "latest_instance_count": self.latest_instance_count,
            "coverage_instance_count": self.coverage_instance_count,
            "strict_effect": self.strict_effect,
            "manifest_status_counts": dict(self.manifest_status_counts),
            "manifest_available_count": self.manifest_available_count,
            "manifest_missing_count": self.manifest_missing_count,
            "manifest_unresolved_count": self.manifest_unresolved_count,
            "manifest_unsupported_count": self.manifest_unsupported_count,
            "manifest_error_count": self.manifest_error_count,
            "manifest_malformed_count": self.manifest_malformed_count,
            "branch_status_counts": dict(self.branch_status_counts),
            "branch_role_counts": dict(self.branch_role_counts),
            "tasks": [task.as_dict() for task in self.tasks],
        }


def build_branch_manifest_availability_report(
    state_or_db_path: str | Path,
    *,
    sample_limit: int = 20,
) -> BranchManifestAvailabilityReport:
    """Inspect persisted manifests without rebuilding them from current code."""

    decisions = load_current_reuse_decision_summaries_v2(
        state_or_db_path,
        limit=None,
    )
    return build_branch_manifest_availability_report_from_decisions(
        decisions,
        state_db_path=str(state_or_db_path),
        sample_limit=sample_limit,
    )


def build_branch_manifest_availability_report_from_decisions(
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
    *,
    state_db_path: str = "",
    sample_limit: int = 20,
) -> BranchManifestAvailabilityReport:
    """Fold append-only decisions and describe only their stored manifests."""

    decision_rows = tuple(decisions)
    latest_decisions = _latest_by_instance_id(decision_rows)
    samples = _coverage_manifest_samples(latest_decisions)
    grouped: dict[TaskKey, list[BranchManifestSample]] = {
        task_key: [] for task_key in COVERAGE_BRANCH_TASK_KEYS
    }
    for sample in samples:
        grouped[sample.task_key].append(sample)

    limit = max(0, int(sample_limit))
    task_reports = tuple(
        _task_availability(task_key, tuple(grouped[task_key]), sample_limit=limit)
        for task_key in COVERAGE_BRANCH_TASK_KEYS
    )
    return BranchManifestAvailabilityReport(
        state_db_path=state_db_path,
        source_decision_count=len(decision_rows),
        latest_instance_count=len(latest_decisions),
        coverage_instance_count=len(samples),
        manifest_status_counts=_counts(sample.manifest_status for sample in samples),
        branch_status_counts=_counts(
            sample.branch_status for sample in samples if sample.branch_status is not None
        ),
        branch_role_counts=_counts(
            sample.branch_role for sample in samples if sample.branch_role is not None
        ),
        tasks=task_reports,
    )


def branch_manifest_samples_from_decisions(
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
    *,
    latest_only: bool = True,
) -> Tuple[BranchManifestSample, ...]:
    """Return scoped manifest records without consulting current branch logic."""

    decision_rows = tuple(decisions)
    selected_decisions = _latest_by_instance_id(decision_rows) if latest_only else decision_rows
    return _coverage_manifest_samples(selected_decisions)


def format_branch_manifest_availability_report(report: BranchManifestAvailabilityReport) -> str:
    lines = [
        f"Branch manifest availability: {report.state_db_path or '-'}",
        f"source decisions: {report.source_decision_count}",
        f"latest instances: {report.latest_instance_count}",
        f"coverage instances: {report.coverage_instance_count}",
        f"strict effect: {report.strict_effect}",
        "manifest statuses:",
    ]
    for status, count in report.manifest_status_counts.items():
        lines.append(f"  {status}: {count}")
    lines.append("tasks:")
    for task in report.tasks:
        key = f"{task.task_key[0]}:{task.task_key[1]}"
        statuses = ", ".join(
            f"{status}={count}" for status, count in task.manifest_status_counts.items()
        ) or "-"
        lines.append(f"  {key} instances={task.instance_count} manifests=[{statuses}]")
        for sample in task.samples:
            branch = sample.branch_role or "-"
            branch_status = sample.branch_status or "-"
            lines.append(
                f"    {sample.instance_id[:12]} manifest={sample.manifest_status} "
                f"branch={branch}/{branch_status}"
            )
    return "\n".join(lines)


def _latest_by_instance_id(
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
) -> Tuple[CurrentReuseDecisionSummaryV2, ...]:
    latest: dict[str, CurrentReuseDecisionSummaryV2] = {}
    for decision in decisions:
        previous = latest.get(decision.instance_id)
        if previous is None or decision.decision_id >= previous.decision_id:
            latest[decision.instance_id] = decision
    return tuple(
        sorted(
            latest.values(),
            key=lambda decision: (decision.stage, decision.task_base, decision.instance_id),
        )
    )


def _task_availability(
    task_key: TaskKey,
    samples: Tuple[BranchManifestSample, ...],
    *,
    sample_limit: int,
) -> BranchManifestTaskAvailability:
    return BranchManifestTaskAvailability(
        task_key=task_key,
        instance_count=len(samples),
        manifest_status_counts=_counts(sample.manifest_status for sample in samples),
        branch_status_counts=_counts(
            sample.branch_status for sample in samples if sample.branch_status is not None
        ),
        branch_role_counts=_counts(
            sample.branch_role for sample in samples if sample.branch_role is not None
        ),
        samples=samples[:sample_limit],
    )


def _coverage_manifest_samples(
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
) -> Tuple[BranchManifestSample, ...]:
    coverage_keys = frozenset(COVERAGE_BRANCH_TASK_KEYS)
    return tuple(
        sorted(
            (
                _manifest_sample(decision)
                for decision in decisions
                if decision.key in coverage_keys
            ),
            key=lambda sample: (sample.task_key, sample.instance_id, sample.decision_id),
        )
    )


def _manifest_sample(
    decision: CurrentReuseDecisionSummaryV2,
) -> BranchManifestSample:
    manifest = decision.branch_artifact_manifest
    if manifest is None:
        return _sample(
            decision,
            manifest_status="missing",
            reasons=("decision has no persisted branch_artifact_manifest_v1",),
        )
    if not isinstance(manifest, Mapping):
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=("persisted branch_artifact_manifest_v1 is not a mapping",),
        )
    schema_version = manifest.get("schema_version")
    if schema_version != BRANCH_ARTIFACT_MANIFEST_V1_SCHEMA:
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=(f"unsupported manifest schema_version: {schema_version!r}",),
        )
    if manifest.get("strict_effect") != _DIAGNOSTIC_ONLY:
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=("manifest strict_effect is not diagnostic_only",),
        )
    generated_from = manifest.get("generated_from")
    if (
        not isinstance(generated_from, Mapping)
        or generated_from.get("source") != "decision_task_instance_v2"
        or not isinstance(generated_from.get("implementation_version"), str)
    ):
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=("manifest generated_from is not a decision-time snapshot",),
        )
    if not _manifest_task_matches(manifest, decision.key):
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=("manifest task_key does not match its decision",),
        )
    status = manifest.get("status")
    if status not in _MANIFEST_STATUSES:
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=(f"unsupported manifest status: {status!r}",),
        )
    if status == "error":
        if manifest.get("descriptor") is not None:
            return _sample(
                decision,
                manifest_status="malformed",
                reasons=("error manifest must not contain a descriptor",),
            )
        return _sample(
            decision,
            manifest_status="error",
            reasons=_manifest_reasons(manifest),
        )

    descriptor = manifest.get("descriptor")
    if not isinstance(descriptor, Mapping):
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=("non-error manifest lacks a descriptor mapping",),
        )
    if descriptor.get("strict_effect") != _DIAGNOSTIC_ONLY:
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=("descriptor strict_effect is not diagnostic_only",),
        )
    branch_status = descriptor.get("branch_status")
    if branch_status not in _BRANCH_STATUSES:
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=(f"unsupported descriptor branch_status: {branch_status!r}",),
        )
    expected_status = {
        "active": "available",
        "no_op": "available",
        "unresolved": "unavailable",
        "unsupported": "unsupported",
    }[branch_status]
    if status != expected_status:
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=(
                f"manifest status {status!r} conflicts with branch_status {branch_status!r}",
            ),
        )
    branch_role = descriptor.get("branch_role")
    if not isinstance(branch_role, str) or not branch_role:
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=("descriptor lacks a nonblank branch_role",),
        )
    selected_fields = _selected_fields(descriptor)
    if selected_fields is None:
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=("descriptor selected_fields are malformed",),
        )
    effective_input_artifact, input_error = _artifact_value(descriptor, "effective_input_artifact")
    effective_output_artifact, output_error = _artifact_value(descriptor, "effective_output_artifact")
    if input_error is not None or output_error is not None:
        return _sample(
            decision,
            manifest_status="malformed",
            reasons=tuple(error for error in (input_error, output_error) if error is not None),
        )
    return _sample(
        decision,
        manifest_status=status,
        branch_status=branch_status,
        branch_role=branch_role,
        effective_input_artifact=effective_input_artifact,
        effective_output_artifact=effective_output_artifact,
        selected_fields=selected_fields,
        reasons=_manifest_reasons(manifest),
    )


def _sample(
    decision: CurrentReuseDecisionSummaryV2,
    *,
    manifest_status: ManifestAvailabilityStatus,
    branch_status: str | None = None,
    branch_role: str | None = None,
    effective_input_artifact: str | None = None,
    effective_output_artifact: str | None = None,
    selected_fields: Tuple[Tuple[str, str], ...] = (),
    reasons: Tuple[str, ...] = (),
) -> BranchManifestSample:
    return BranchManifestSample(
        instance_id=decision.instance_id,
        decision_id=decision.decision_id,
        task_key=decision.key,
        manifest_status=manifest_status,
        branch_status=branch_status,
        branch_role=branch_role,
        effective_input_artifact=effective_input_artifact,
        effective_output_artifact=effective_output_artifact,
        selected_fields=selected_fields,
        reasons=reasons,
    )


def _manifest_task_matches(manifest: Mapping[str, Any], task_key: TaskKey) -> bool:
    raw_task_key = manifest.get("task_key")
    return (
        isinstance(raw_task_key, Mapping)
        and raw_task_key.get("stage") == task_key[0]
        and raw_task_key.get("task_base") == task_key[1]
    )


def _manifest_reasons(manifest: Mapping[str, Any]) -> Tuple[str, ...]:
    raw_reasons = manifest.get("reasons")
    if not isinstance(raw_reasons, (tuple, list)):
        return ()
    return tuple(str(reason) for reason in raw_reasons)


def _selected_fields(descriptor: Mapping[str, Any]) -> Tuple[Tuple[str, str], ...] | None:
    raw_fields = descriptor.get("selected_fields")
    if not isinstance(raw_fields, (tuple, list)):
        return None
    fields: list[tuple[str, str]] = []
    for item in raw_fields:
        if not isinstance(item, Mapping):
            return None
        key = item.get("key")
        value = item.get("value")
        if not isinstance(key, str) or not key or not isinstance(value, str):
            return None
        fields.append((key, value))
    return tuple(fields)


def _artifact_value(descriptor: Mapping[str, Any], key: str) -> tuple[str | None, str | None]:
    value = descriptor.get(key)
    if value is None or isinstance(value, str):
        return value, None
    return None, f"descriptor {key} is not a string or null"


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
