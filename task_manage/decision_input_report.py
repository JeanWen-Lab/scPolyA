"""Read-only availability and change warnings for decision input manifests."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Tuple

from .decision_input_manifest import (
    DECISION_INPUT_FINGERPRINT_POLICY_V1,
    DECISION_INPUT_MANIFEST_V1_SCHEMA,
    build_decision_input_manifest_v1,
    manifest_metadata_status_counts,
)
from .reuse_report import (
    CurrentReuseDecisionSummaryV2,
    load_current_reuse_decision_summaries_v2,
)
from .run_config_preview import DEFAULT_RUN_CONFIG_SNAPSHOT, load_run_config_snapshot
from .task_instance_v2 import (
    DEFAULT_IMPLEMENTATION_VERSION,
    DEFAULT_REUSE_POLICY_VERSION,
)
from .task_instance_v3 import TaskInstanceV3


TaskKey = Tuple[str, str]
DecisionInputManifestAvailability = Literal[
    "captured",
    "captured_with_warnings",
    "missing",
    "error",
    "malformed",
]
DecisionInputComparisonStatus = Literal[
    "unchanged",
    "changed",
    "manifest_missing",
    "manifest_error",
    "manifest_malformed",
    "policy_incompatible",
    "current_unavailable",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"
_WARNING_ONLY = "warning_only"
_CAPTURED_STATUSES = frozenset({"captured", "captured_with_warnings"})
_ALLOWED_FINGERPRINT_MODES = frozenset(
    {"metadata", "directory_metadata", "config", "missing", "empty", "unresolved"}
)


@dataclass(frozen=True)
class DecisionInputStatusEntry:
    instance_id: str
    decision_id: int
    task_key: TaskKey
    availability: DecisionInputManifestAvailability
    comparison_status: DecisionInputComparisonStatus
    captured_snapshot_hash: str | None
    current_snapshot_hash: str | None
    captured_metadata_status_counts: Mapping[str, int]
    current_metadata_status_counts: Mapping[str, int]
    detail: str
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY
    change_effect: Literal["warning_only"] = _WARNING_ONLY

    @property
    def warning(self) -> bool:
        return self.comparison_status == "changed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "decision_id": self.decision_id,
            "task_key": {"stage": self.task_key[0], "task_base": self.task_key[1]},
            "availability": self.availability,
            "comparison_status": self.comparison_status,
            "warning": self.warning,
            "captured_snapshot_hash": self.captured_snapshot_hash,
            "current_snapshot_hash": self.current_snapshot_hash,
            "captured_metadata_status_counts": dict(self.captured_metadata_status_counts),
            "current_metadata_status_counts": dict(self.current_metadata_status_counts),
            "detail": self.detail,
            "strict_effect": self.strict_effect,
            "change_effect": self.change_effect,
        }


@dataclass(frozen=True)
class DecisionInputStatusReport:
    state_db_path: str
    source_decision_count: int
    latest_instance_count: int
    external_instance_count: int
    config_available: bool
    config_error: str | None
    entries: Tuple[DecisionInputStatusEntry, ...]
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY
    change_effect: Literal["warning_only"] = _WARNING_ONLY
    # Candidate-scoped reports only: persisted decisions deliberately left out
    # (still in the database) with their reasons, so exclusions stay auditable.
    excluded_decision_count: int = 0
    excluded_external_instance_count: int = 0
    excluded_reason_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def availability_counts(self) -> dict[str, int]:
        return _counts(entry.availability for entry in self.entries)

    @property
    def comparison_status_counts(self) -> dict[str, int]:
        return _counts(entry.comparison_status for entry in self.entries)

    @property
    def warning_count(self) -> int:
        return sum(entry.warning for entry in self.entries)

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        entries = self.entries if limit is None else self.entries[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "source_decision_count": self.source_decision_count,
            "latest_instance_count": self.latest_instance_count,
            "external_instance_count": self.external_instance_count,
            "config_available": self.config_available,
            "config_error": self.config_error,
            "strict_effect": self.strict_effect,
            "change_effect": self.change_effect,
            "availability_counts": self.availability_counts,
            "comparison_status_counts": self.comparison_status_counts,
            "warning_count": self.warning_count,
            "excluded_decision_count": self.excluded_decision_count,
            "excluded_external_instance_count": self.excluded_external_instance_count,
            "excluded_reason_counts": dict(self.excluded_reason_counts),
            "entries": [entry.as_dict() for entry in entries],
        }


@dataclass(frozen=True)
class _ValidatedManifest:
    availability: DecisionInputManifestAvailability
    snapshot_hash: str | None
    policy_version: str | None
    metadata_status_counts: Mapping[str, int]
    detail: str


def build_decision_input_status_report(
    state_or_db_path: str | Path,
    *,
    snapshot_key: str = DEFAULT_RUN_CONFIG_SNAPSHOT,
) -> DecisionInputStatusReport:
    """Compare persisted decision-time metadata with current metadata only."""

    db_path = _resolve_state_db_path(state_or_db_path)
    if not db_path.is_file():
        return build_decision_input_status_report_from_decisions(
            (),
            current_config=None,
            state_db_path=str(state_or_db_path),
            config_error=f"FileNotFoundError: state database not found: {db_path}",
        )

    decisions = load_current_reuse_decision_summaries_v2(
        db_path,
        limit=None,
    )
    config: Mapping[str, Any] | None = None
    config_error: str | None = None
    try:
        config = load_run_config_snapshot(db_path, snapshot_key=snapshot_key)
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error) as exc:
        config_error = f"{type(exc).__name__}: {exc}"
    return build_decision_input_status_report_from_decisions(
        decisions,
        current_config=config,
        state_db_path=str(state_or_db_path),
        config_error=config_error,
    )


def build_candidate_decision_input_status_report(
    state_or_db_path: str | Path,
    *,
    source_decisions: Iterable[CurrentReuseDecisionSummaryV2],
    retained_decisions: Iterable[CurrentReuseDecisionSummaryV2],
    excluded_decision_reasons: Mapping[int, str],
    snapshot_key: str = DEFAULT_RUN_CONFIG_SNAPSHOT,
) -> DecisionInputStatusReport:
    """Input status for exactly the retained candidate decisions.

    Decisions excluded from a candidate graph are not compared, but are counted
    by reason. Every source decision must be either retained or carry an
    explicit exclusion reason; anything else fails closed.
    """

    source_rows = tuple(source_decisions)
    retained_rows = tuple(retained_decisions)
    retained_ids = {decision.decision_id for decision in retained_rows}
    source_ids = {decision.decision_id for decision in source_rows}
    if not retained_ids <= source_ids:
        raise ValueError("Candidate decision-input report retained unknown decisions")
    unexplained = sorted(source_ids - retained_ids - set(excluded_decision_reasons))
    if unexplained:
        raise ValueError(
            "Candidate decision-input report has excluded decisions without a "
            f"reason: {unexplained[:5]}"
        )
    db_path = _resolve_state_db_path(state_or_db_path)
    config: Mapping[str, Any] | None = None
    config_error: str | None = None
    if not db_path.is_file():
        # Same wording as build_decision_input_status_report.
        config_error = f"FileNotFoundError: state database not found: {db_path}"
    else:
        try:
            config = load_run_config_snapshot(db_path, snapshot_key=snapshot_key)
        except (KeyError, OSError, TypeError, ValueError, sqlite3.Error) as exc:
            config_error = f"{type(exc).__name__}: {exc}"
    report = build_decision_input_status_report_from_decisions(
        retained_rows,
        current_config=config,
        state_db_path=str(state_or_db_path),
        config_error=config_error,
    )
    excluded_rows = tuple(
        decision for decision in source_rows if decision.decision_id not in retained_ids
    )
    retained_instance_ids = {decision.instance_id for decision in retained_rows}
    excluded_external = {
        decision.instance_id
        for decision in excluded_rows
        if _has_external_inputs(decision.consumes)
        and decision.instance_id not in retained_instance_ids
    }
    return DecisionInputStatusReport(
        state_db_path=report.state_db_path,
        source_decision_count=len(source_rows),
        latest_instance_count=report.latest_instance_count,
        external_instance_count=report.external_instance_count,
        config_available=report.config_available,
        config_error=report.config_error,
        entries=report.entries,
        excluded_decision_count=len(excluded_rows),
        excluded_external_instance_count=len(excluded_external),
        excluded_reason_counts=_counts(
            excluded_decision_reasons[decision.decision_id] for decision in excluded_rows
        ),
    )


def build_decision_input_status_report_from_decisions(
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
    *,
    current_config: Mapping[str, Any] | None,
    state_db_path: str = "",
    config_error: str | None = None,
) -> DecisionInputStatusReport:
    """Fold append-only rows and emit warning-only metadata comparisons."""

    decision_rows = tuple(decisions)
    latest_decisions = _latest_by_instance_id(decision_rows)
    external_decisions = tuple(
        decision for decision in latest_decisions if _has_external_inputs(decision.consumes)
    )
    entries = tuple(
        _status_entry(
            decision,
            current_config=current_config,
            config_error=config_error,
        )
        for decision in external_decisions
    )
    return DecisionInputStatusReport(
        state_db_path=state_db_path,
        source_decision_count=len(decision_rows),
        latest_instance_count=len(latest_decisions),
        external_instance_count=len(external_decisions),
        config_available=current_config is not None,
        config_error=config_error,
        entries=entries,
    )


def format_decision_input_status_report(
    report: DecisionInputStatusReport,
    *,
    limit: int = 20,
) -> str:
    lines = [
        f"Decision input metadata: {report.state_db_path or '-'}",
        f"source decisions: {report.source_decision_count}",
        f"latest instances: {report.latest_instance_count}",
        f"external-input instances: {report.external_instance_count}",
        f"current config: {'available' if report.config_available else 'unavailable'}",
        f"warnings: {report.warning_count}",
        *(
            [
                f"excluded from candidate graph: {report.excluded_decision_count} decisions "
                f"({report.excluded_external_instance_count} external-input instances) "
                + ", ".join(f"{k}={v}" for k, v in report.excluded_reason_counts.items())
            ]
            if report.excluded_decision_count
            else []
        ),
        f"strict effect: {report.strict_effect}",
        f"change effect: {report.change_effect}",
        "comparison statuses:",
    ]
    for status, count in report.comparison_status_counts.items():
        lines.append(f"  {status}: {count}")
    if report.config_error:
        lines.append(f"config error: {report.config_error}")
    entries = report.entries[: max(0, int(limit))]
    if entries:
        lines.append("instances:")
    for entry in entries:
        prefix = "WARNING" if entry.warning else "INFO"
        task = f"{entry.task_key[0]}:{entry.task_key[1]}"
        lines.append(
            f"  {prefix} {entry.instance_id[:12]} {task} "
            f"availability={entry.availability} comparison={entry.comparison_status}"
        )
        lines.append(f"    detail={entry.detail}")
    return "\n".join(lines)


def _status_entry(
    decision: CurrentReuseDecisionSummaryV2,
    *,
    current_config: Mapping[str, Any] | None,
    config_error: str | None,
) -> DecisionInputStatusEntry:
    validated = _validate_manifest(decision)
    if validated.availability == "missing":
        return _entry(
            decision,
            validated=validated,
            comparison_status="manifest_missing",
            detail="historical decision has no captured input metadata; no read-time backfill",
        )
    if validated.availability == "malformed":
        return _entry(
            decision,
            validated=validated,
            comparison_status="manifest_malformed",
            detail=validated.detail,
        )
    if validated.availability == "error":
        return _entry(
            decision,
            validated=validated,
            comparison_status="manifest_error",
            detail=validated.detail,
        )
    if validated.policy_version != DECISION_INPUT_FINGERPRINT_POLICY_V1:
        return _entry(
            decision,
            validated=validated,
            comparison_status="policy_incompatible",
            detail=(
                f"captured policy {validated.policy_version!r} is not comparable with "
                f"{DECISION_INPUT_FINGERPRINT_POLICY_V1!r}"
            ),
        )
    if current_config is None:
        return _entry(
            decision,
            validated=validated,
            comparison_status="current_unavailable",
            detail=config_error or "current run config snapshot is unavailable",
        )

    current_manifest = build_decision_input_manifest_v1(
        _task_instance_for_current_capture(decision),
        current_config,
    )
    if not isinstance(current_manifest, Mapping) or current_manifest.get("status") == "error":
        return _entry(
            decision,
            validated=validated,
            comparison_status="current_unavailable",
            current_manifest=current_manifest,
            detail="current metadata snapshot could not be captured",
        )
    current_hash = current_manifest.get("snapshot_hash")
    if not isinstance(current_hash, str) or not current_hash:
        return _entry(
            decision,
            validated=validated,
            comparison_status="current_unavailable",
            current_manifest=current_manifest,
            detail="current metadata snapshot lacks a stable snapshot hash",
        )
    if current_hash == validated.snapshot_hash:
        return _entry(
            decision,
            validated=validated,
            comparison_status="unchanged",
            current_manifest=current_manifest,
            detail="captured and current metadata match; diagnostic only",
        )
    return _entry(
        decision,
        validated=validated,
        comparison_status="changed",
        current_manifest=current_manifest,
        detail=(
            "captured and current metadata differ; user review is required, "
            "but no DONE row or scheduling decision is changed"
        ),
    )


def _entry(
    decision: CurrentReuseDecisionSummaryV2,
    *,
    validated: _ValidatedManifest,
    comparison_status: DecisionInputComparisonStatus,
    detail: str,
    current_manifest: Mapping[str, Any] | None = None,
) -> DecisionInputStatusEntry:
    current_hash = None
    current_counts: Mapping[str, int] = {}
    if isinstance(current_manifest, Mapping):
        raw_hash = current_manifest.get("snapshot_hash")
        current_hash = raw_hash if isinstance(raw_hash, str) else None
        current_counts = manifest_metadata_status_counts(current_manifest)
    return DecisionInputStatusEntry(
        instance_id=decision.instance_id,
        decision_id=decision.decision_id,
        task_key=decision.key,
        availability=validated.availability,
        comparison_status=comparison_status,
        captured_snapshot_hash=validated.snapshot_hash,
        current_snapshot_hash=current_hash,
        captured_metadata_status_counts=validated.metadata_status_counts,
        current_metadata_status_counts=current_counts,
        detail=detail,
    )


def _validate_manifest(
    decision: CurrentReuseDecisionSummaryV2,
) -> _ValidatedManifest:
    manifest = decision.decision_input_manifest
    if manifest is None:
        return _ValidatedManifest("missing", None, None, {}, "manifest is missing")
    if not isinstance(manifest, Mapping):
        return _malformed("persisted decision input manifest is not a mapping")
    if manifest.get("schema_version") != DECISION_INPUT_MANIFEST_V1_SCHEMA:
        return _malformed("manifest schema_version is not decision-input-manifest-v1")
    if manifest.get("strict_effect") != _DIAGNOSTIC_ONLY:
        return _malformed("manifest strict_effect is not diagnostic_only")
    if manifest.get("change_effect") != _WARNING_ONLY:
        return _malformed("manifest change_effect is not warning_only")
    task_key = manifest.get("task_key")
    if (
        not isinstance(task_key, Mapping)
        or task_key.get("stage") != decision.stage
        or task_key.get("task_base") != decision.task_base
    ):
        return _malformed("manifest task_key does not match its decision")
    generated_from = manifest.get("generated_from")
    if (
        not isinstance(generated_from, Mapping)
        or generated_from.get("source") != "decision_task_instance_v2"
        or not isinstance(generated_from.get("implementation_version"), str)
        or not isinstance(generated_from.get("captured_at"), str)
    ):
        return _malformed("manifest is not a decision-time capture")
    policy = manifest.get("fingerprint_policy")
    if not isinstance(policy, Mapping):
        return _malformed("manifest fingerprint_policy is missing")
    policy_version = policy.get("version")
    if not isinstance(policy_version, str) or not policy_version:
        return _malformed("manifest fingerprint policy version is missing")
    if policy.get("content_hashing") != "disabled":
        return _malformed("manifest enables file-content hashing")
    status = manifest.get("status")
    if status == "error":
        return _ValidatedManifest(
            "error",
            None,
            policy_version,
            {},
            _manifest_reason_text(manifest) or "manifest capture failed",
        )
    if status not in _CAPTURED_STATUSES:
        return _malformed(f"unsupported manifest status: {status!r}")
    snapshot_hash = manifest.get("snapshot_hash")
    if not isinstance(snapshot_hash, str) or not snapshot_hash:
        return _malformed("captured manifest lacks snapshot_hash")
    validation_error = _validate_record_payloads(manifest)
    if validation_error is not None:
        return _malformed(validation_error)
    return _ValidatedManifest(
        status,
        snapshot_hash,
        policy_version,
        manifest_metadata_status_counts(manifest),
        _manifest_reason_text(manifest),
    )


def _validate_record_payloads(manifest: Mapping[str, Any]) -> str | None:
    declared = manifest.get("declared_inputs")
    if not isinstance(declared, Mapping):
        return "captured manifest lacks declared_inputs"
    records = declared.get("records")
    if not isinstance(records, (tuple, list)):
        return "captured manifest declared records are malformed"
    for record in records:
        if not isinstance(record, Mapping):
            return "captured manifest contains a malformed declared record"
        if record.get("source") != "decision_config":
            return "captured manifest declared record has a non-decision source"
        error = _fingerprint_error(record.get("fingerprint"))
        if error is not None:
            return error
    nested = manifest.get("nested_inputs")
    if not isinstance(nested, (tuple, list)):
        return "captured manifest nested_inputs are malformed"
    for record in nested:
        if not isinstance(record, Mapping):
            return "captured manifest contains a malformed nested record"
        if not isinstance(record.get("canonical_path"), str):
            return "captured manifest nested record lacks canonical_path"
        error = _fingerprint_error(record.get("fingerprint"))
        if error is not None:
            return error
    return None


def _fingerprint_error(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return "captured manifest record lacks a fingerprint"
    mode = value.get("mode")
    if mode not in _ALLOWED_FINGERPRINT_MODES:
        return f"captured manifest uses unsupported fingerprint mode: {mode!r}"
    if value.get("content_hash") is not None:
        return "captured manifest contains a file-content hash"
    return None


def _task_instance_for_current_capture(
    decision: CurrentReuseDecisionSummaryV2,
) -> TaskInstanceV3:
    return TaskInstanceV3(
        stage=decision.stage,
        task_base=decision.task_base,
        level=None,
        identity=(),
        params=(),
        consumes=decision.consumes,
        produces=decision.produces,
        reuse_policy_version=decision.reuse_policy_version or DEFAULT_REUSE_POLICY_VERSION,
        implementation_version=decision.implementation_version or DEFAULT_IMPLEMENTATION_VERSION,
    )


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


def _has_external_inputs(consumes: Iterable[str]) -> bool:
    return any(str(value).startswith("args.") for value in consumes)


def _resolve_state_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path).expanduser()
    if path.is_dir():
        if path.name == "state":
            return path / "state.sqlite"
        nested = path / "state" / "state.sqlite"
        if nested.exists():
            return nested
        legacy = path / "state.sqlite"
        if legacy.exists():
            return legacy
        if nested.parent.is_dir():
            return nested
        return legacy
    return path


def _manifest_reason_text(manifest: Mapping[str, Any]) -> str:
    reasons = manifest.get("reasons")
    if not isinstance(reasons, (tuple, list)):
        return ""
    return "; ".join(str(reason) for reason in reasons)


def _malformed(detail: str) -> _ValidatedManifest:
    return _ValidatedManifest("malformed", None, None, {}, detail)


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
