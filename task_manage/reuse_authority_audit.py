"""Append-only audit records for Reuse v2 runtime authority."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Tuple

from .pipeline_contracts import TaskKey
from .reuse_decision_storage import (
    ReuseDecisionStorageError,
    StoredReuseAuthorityAuditRowV1,
    archived_reuse_authority_audit_storage_available_v1,
    current_reuse_decision_storage_available_v1,
    load_archived_reuse_authority_audit_rows_v1,
    load_archived_reuse_decision_rows_v2,
    load_current_reuse_authority_audit_rows_v1,
    load_current_reuse_decision_rows_v2,
)


REUSE_AUTHORITY_AUDIT_SCHEMA_V1 = "scpolya.reuse_v2.authority_audit.v1"
REUSE_AUTHORITY_AUDIT_TABLE_V1 = "task_reuse_authority_audit_v1"

ReuseAuthorityAuditEventV1 = Literal["dispatch", "completion"]


@dataclass(frozen=True)
class ReuseAuthorityAuditV1:
    event_type: ReuseAuthorityAuditEventV1
    invocation_id: str
    policy_name: str
    policy_version: str
    policy_fingerprint: str
    evidence_snapshot_hash: str
    source_decision_id: int | None
    task_key: TaskKey
    instance_id: str
    legacy_action: str
    candidate_action: str
    comparison: str
    health_status: str
    blockers: Tuple[str, ...] = ()
    operator_warnings: Tuple[str, ...] = ()
    rollback_reason: str = ""
    schema_version: str = REUSE_AUTHORITY_AUDIT_SCHEMA_V1

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "invocation_id": self.invocation_id,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.policy_fingerprint,
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "source_decision_id": self.source_decision_id,
            "task_key": {
                "stage": self.task_key[0],
                "task_base": self.task_key[1],
            },
            "instance_id": self.instance_id,
            "legacy_action": self.legacy_action,
            "candidate_action": self.candidate_action,
            "comparison": self.comparison,
            "health_status": self.health_status,
            "blockers": list(self.blockers),
            "operator_warnings": list(self.operator_warnings),
            "rollback_reason": self.rollback_reason,
        }


@dataclass(frozen=True)
class StoredReuseAuthorityAuditV1:
    authority_audit_id: int
    decision_id: int
    decision_action: str
    created_at: str
    audit: ReuseAuthorityAuditV1

    def as_dict(self) -> dict[str, Any]:
        payload = self.audit.as_dict()
        payload.update(
            {
                "authority_audit_id": self.authority_audit_id,
                "decision_id": self.decision_id,
                "decision_action": self.decision_action,
                "created_at": self.created_at,
            }
        )
        return payload


@dataclass(frozen=True)
class ReuseAuthorityAuditReportV1:
    state_db_path: str
    table_available: bool
    error: str | None
    entries: Tuple[StoredReuseAuthorityAuditV1, ...]

    @property
    def event_counts(self) -> dict[str, int]:
        return _counts(entry.audit.event_type for entry in self.entries)

    @property
    def decision_action_counts(self) -> dict[str, int]:
        return _counts(entry.decision_action for entry in self.entries)

    @property
    def candidate_action_counts(self) -> dict[str, int]:
        return _counts(entry.audit.candidate_action for entry in self.entries)

    @property
    def rollback_count(self) -> int:
        return sum(bool(entry.audit.rollback_reason) for entry in self.entries)

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        entries = self.entries
        if limit is not None:
            entries = entries[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "table_available": self.table_available,
            "error": self.error,
            "entry_count": len(self.entries),
            "event_counts": self.event_counts,
            "decision_action_counts": self.decision_action_counts,
            "candidate_action_counts": self.candidate_action_counts,
            "rollback_count": self.rollback_count,
            "entries": [entry.as_dict() for entry in entries],
        }


def build_reuse_authority_audit_report_v1(
    state_or_db_path: str | Path,
    *,
    archived_v1: bool = False,
) -> ReuseAuthorityAuditReportV1:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return ReuseAuthorityAuditReportV1(
            state_db_path=str(db_path),
            table_available=False,
            error=f"state database not found: {db_path}",
            entries=(),
        )
    table_available = (
        archived_reuse_authority_audit_storage_available_v1(db_path)
        if archived_v1
        else current_reuse_decision_storage_available_v1(db_path)
    )
    if not table_available:
        return ReuseAuthorityAuditReportV1(
            state_db_path=str(db_path),
            table_available=False,
            error=None,
            entries=(),
        )
    try:
        rows = (
            load_archived_reuse_authority_audit_rows_v1(db_path)
            if archived_v1
            else load_current_reuse_authority_audit_rows_v1(db_path)
        )
        decision_rows = (
            load_archived_reuse_decision_rows_v2(
                db_path,
                latest_first=False,
            )
            if archived_v1
            else load_current_reuse_decision_rows_v2(
                db_path,
                latest_first=False,
            )
        )
        actions = {row.decision_id: row.action for row in decision_rows}
        entries = tuple(
            _stored_audit_from_row(row, decision_action=actions[row.decision_id])
            for row in rows
        )
        return ReuseAuthorityAuditReportV1(
            state_db_path=str(db_path),
            table_available=True,
            error=None,
            entries=entries,
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        ReuseDecisionStorageError,
        json.JSONDecodeError,
    ) as exc:
        return ReuseAuthorityAuditReportV1(
            state_db_path=str(db_path),
            table_available=True,
            error=f"{type(exc).__name__}: {exc}",
            entries=(),
        )


def build_archived_reuse_authority_audit_report_v1(
    state_or_db_path: str | Path,
) -> ReuseAuthorityAuditReportV1:
    return build_reuse_authority_audit_report_v1(
        state_or_db_path,
        archived_v1=True,
    )


def format_reuse_authority_audit_report_v1(
    report: ReuseAuthorityAuditReportV1,
    *,
    limit: int = 20,
) -> str:
    lines = [
        f"Reuse v2 authority audit: {report.state_db_path}",
        f"audit table: {'yes' if report.table_available else 'no'}",
        f"entries: {len(report.entries)}",
        f"rollbacks: {report.rollback_count}",
    ]
    if report.error:
        lines.append(f"error: {report.error}")
    if report.decision_action_counts:
        lines.append(
            "decision actions: "
            + ", ".join(
                f"{action}={count}"
                for action, count in report.decision_action_counts.items()
            )
        )
    for entry in report.entries[: max(0, int(limit))]:
        audit = entry.audit
        lines.append(
            f"  #{entry.authority_audit_id}/decision#{entry.decision_id} "
            f"{audit.event_type} {audit.task_key[0]}:{audit.task_key[1]} "
            f"{entry.decision_action} candidate={audit.candidate_action} "
            f"snapshot={audit.evidence_snapshot_hash or '-'}"
        )
        if audit.rollback_reason:
            lines.append(f"    rollback={audit.rollback_reason}")
    return "\n".join(lines)


def _stored_audit_from_row(
    row: StoredReuseAuthorityAuditRowV1,
    *,
    decision_action: str,
) -> StoredReuseAuthorityAuditV1:
    return StoredReuseAuthorityAuditV1(
        authority_audit_id=row.authority_audit_id,
        decision_id=row.decision_id,
        decision_action=str(decision_action),
        created_at=row.created_at,
        audit=ReuseAuthorityAuditV1(
            schema_version=row.schema_version,
            event_type=row.event_type,
            invocation_id=row.invocation_id,
            policy_name=row.policy_name,
            policy_version=row.policy_version,
            policy_fingerprint=row.policy_fingerprint,
            evidence_snapshot_hash=row.evidence_snapshot_hash,
            source_decision_id=row.source_decision_id,
            task_key=(row.stage, row.task_base),
            instance_id=row.instance_id,
            legacy_action=row.legacy_action,
            candidate_action=row.candidate_action,
            comparison=row.comparison,
            health_status=row.health_status,
            blockers=tuple(
                str(value) for value in json.loads(row.blockers_json)
            ),
            operator_warnings=tuple(
                str(value)
                for value in json.loads(row.operator_warnings_json)
            ),
            rollback_reason=row.rollback_reason,
        ),
    )


def _resolve_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path).expanduser()
    if path.is_dir():
        if path.name == "state":
            return path / "state.sqlite"
        nested = path / "state" / "state.sqlite"
        if nested.exists():
            return nested
        return path / "state.sqlite"
    return path


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
