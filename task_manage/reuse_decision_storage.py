from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from .reuse_decisions import ReuseDecisionV2
from .task_coordinate import TaskCoordinateError, build_task_coordinate_v1
from .task_instance_v3 import (
    TASK_INSTANCE_SCHEMA_V3,
    TaskInstanceV3,
    TaskInstanceV3Field,
    derive_legacy_variant_str_from_task_instance_v3,
)


REUSE_DECISION_TABLE_V2 = "task_reuse_decisions_v2"
REUSE_DECISION_TABLE_V3 = "task_reuse_decisions_v3"
REUSE_AUTHORITY_AUDIT_TABLE_V1 = "task_reuse_authority_audit_v1"
REUSE_AUTHORITY_AUDIT_TABLE_V2 = "task_reuse_authority_audit_v2"
REUSE_DECISION_STORAGE_STATE_TABLE_V1 = (
    "task_reuse_decision_storage_state_v1"
)

REUSE_DECISION_STORAGE_POLICY_SCHEMA_V1 = (
    "scpolya.reuse_decision_storage_policy.v1"
)
REUSE_DECISION_STORAGE_POLICY_NAME = "reuse_decision_storage_policy"
REUSE_DECISION_STORAGE_POLICY_VERSION_V3 = "3"
REUSE_DECISION_ROW_SCHEMA_V3 = "scpolya.reuse_decision.storage.v3"
REUSE_DECISION_INSTANCE_SCHEMA_V3 = "scpolya.task_instance.storage.v3"
REUSE_AUTHORITY_AUDIT_STORAGE_SCHEMA_V2 = (
    "scpolya.reuse_authority_audit.storage.v2"
)
CURRENT_STORED_REUSE_DECISION_ROW_SCHEMA_V2 = (
    "scpolya.current_stored_reuse_decision_row.v2"
)
CurrentStoredDecisionCoordinateStatus = Literal[
    "available",
    "malformed",
    "compatibility_mismatch",
]

_FORBIDDEN_INSTANCE_KEYS = frozenset(
    {"legacy_variant_str", "variant_str"}
)
_ARCHIVE_DECISION_REQUIRED_COLUMNS = frozenset(
    {
        "decision_id",
        "stage",
        "task_base",
        "instance_id",
        "legacy_variant_str",
        "identity_hash",
        "params_hash",
        "symbolic_inputs_hash",
        "symbolic_outputs_hash",
        "contract_hash",
        "action",
        "legacy_done",
        "queued",
        "force",
        "reasons_json",
        "instance_json",
        "created_at",
    }
)
_ARCHIVE_DECISION_OPTIONAL_COLUMNS = frozenset(
    {"task_coordinate_id", "worker_callable"}
)
_ARCHIVE_AUDIT_COLUMNS = frozenset(
    {
        "authority_audit_id",
        "schema_version",
        "event_type",
        "invocation_id",
        "policy_name",
        "policy_version",
        "policy_fingerprint",
        "evidence_snapshot_hash",
        "source_decision_id",
        "decision_id",
        "stage",
        "task_base",
        "instance_id",
        "legacy_action",
        "candidate_action",
        "comparison",
        "health_status",
        "blockers_json",
        "operator_warnings_json",
        "rollback_reason",
        "created_at",
    }
)


class ReuseDecisionStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReuseDecisionStoragePolicyV1:
    schema_version: str
    policy_name: str
    policy_version: str
    runtime_decision_write_table: str
    runtime_authority_audit_write_table: str
    current_decision_read_tables: tuple[str, ...]
    current_authority_audit_read_tables: tuple[str, ...]
    archived_decision_read_api: str
    archived_authority_audit_read_api: str
    instance_payload_schema: str
    persist_legacy_variant_alias: bool
    global_decision_ids: bool
    global_authority_audit_ids: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "runtime_decision_write_table": (
                self.runtime_decision_write_table
            ),
            "runtime_authority_audit_write_table": (
                self.runtime_authority_audit_write_table
            ),
            "current_decision_read_tables": list(
                self.current_decision_read_tables
            ),
            "current_authority_audit_read_tables": list(
                self.current_authority_audit_read_tables
            ),
            "archived_decision_read_api": self.archived_decision_read_api,
            "archived_authority_audit_read_api": (
                self.archived_authority_audit_read_api
            ),
            "instance_payload_schema": self.instance_payload_schema,
            "persist_legacy_variant_alias": (
                self.persist_legacy_variant_alias
            ),
            "global_decision_ids": self.global_decision_ids,
            "global_authority_audit_ids": self.global_authority_audit_ids,
        }


@dataclass(frozen=True)
class ReuseDecisionStorageStateV1:
    policy: ReuseDecisionStoragePolicyV1
    policy_fingerprint: str
    archived_decision_table_present: bool
    archived_decision_row_count: int
    archived_decision_max_id: int
    archived_decision_fingerprint: str
    archived_authority_audit_table_present: bool
    archived_authority_audit_row_count: int
    archived_authority_audit_max_id: int
    archived_authority_audit_fingerprint: str
    initialized_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredReuseDecisionRowV1:
    storage_version: str
    decision_id: int
    schema_version: str
    stage: str
    task_base: str
    instance_id: str
    legacy_variant_str: str
    task_coordinate_id: str
    identity_hash: str
    params_hash: str
    symbolic_inputs_hash: str
    symbolic_outputs_hash: str
    contract_hash: str
    action: str
    legacy_done: bool
    queued: bool
    force: bool
    worker_callable: str
    reasons_json: str
    instance_json: str
    created_at: str


@dataclass(frozen=True)
class CurrentStoredReuseDecisionRowV2:
    storage_version: str
    decision_id: int
    storage_schema_version: str
    stage: str
    task_base: str
    instance_id: str
    task_coordinate_id: str
    coordinate_status: CurrentStoredDecisionCoordinateStatus
    identity_hash: str
    params_hash: str
    symbolic_inputs_hash: str
    symbolic_outputs_hash: str
    contract_hash: str
    action: str
    legacy_done: bool
    queued: bool
    force: bool
    worker_callable: str
    reasons: tuple[str, ...]
    instance_payload: Mapping[str, Any]
    created_at: str
    schema_version: str = CURRENT_STORED_REUSE_DECISION_ROW_SCHEMA_V2

    def __post_init__(self) -> None:
        if self.schema_version != CURRENT_STORED_REUSE_DECISION_ROW_SCHEMA_V2:
            raise ValueError(
                "Current stored reuse-decision row schema mismatch: "
                f"{self.schema_version!r}"
            )
        if self.storage_version not in {"v2", "v3"}:
            raise ValueError(
                "Current stored reuse-decision row has an unsupported source"
            )
        if self.coordinate_status not in {
            "available",
            "malformed",
            "compatibility_mismatch",
        }:
            raise ValueError(
                "Current stored reuse-decision row coordinate status is invalid"
            )
        if (
            self.coordinate_status == "malformed"
            and self.task_coordinate_id
        ) or (
            self.coordinate_status != "malformed"
            and not self.task_coordinate_id
        ):
            raise ValueError(
                "Current stored reuse-decision row coordinate ID/status mismatch"
            )
        if not isinstance(self.instance_payload, Mapping):
            raise TypeError(
                "Current stored reuse-decision rows require an instance mapping"
            )
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, str) for reason in self.reasons
        ):
            raise TypeError(
                "Current stored reuse-decision row reasons must be text tuples"
            )
        instance_payload = json.loads(_canonical_json(self.instance_payload))
        _reject_alias_keys(instance_payload)
        object.__setattr__(self, "instance_payload", instance_payload)

    def as_dict(self) -> dict[str, Any]:
        _reject_alias_keys(self.instance_payload)
        return {
            "schema_version": self.schema_version,
            "storage_version": self.storage_version,
            "decision_id": self.decision_id,
            "storage_schema_version": self.storage_schema_version,
            "stage": self.stage,
            "task_base": self.task_base,
            "instance_id": self.instance_id,
            "task_coordinate_id": self.task_coordinate_id,
            "coordinate_status": self.coordinate_status,
            "identity_hash": self.identity_hash,
            "params_hash": self.params_hash,
            "symbolic_inputs_hash": self.symbolic_inputs_hash,
            "symbolic_outputs_hash": self.symbolic_outputs_hash,
            "contract_hash": self.contract_hash,
            "action": self.action,
            "legacy_done": self.legacy_done,
            "queued": self.queued,
            "force": self.force,
            "worker_callable": self.worker_callable,
            "reasons": list(self.reasons),
            "instance_payload": json.loads(
                _canonical_json(self.instance_payload)
            ),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class StoredReuseAuthorityAuditRowV1:
    storage_version: str
    authority_audit_id: int
    storage_schema_version: str
    schema_version: str
    event_type: str
    invocation_id: str
    policy_name: str
    policy_version: str
    policy_fingerprint: str
    evidence_snapshot_hash: str
    source_decision_id: int | None
    decision_id: int
    stage: str
    task_base: str
    instance_id: str
    legacy_action: str
    candidate_action: str
    comparison: str
    health_status: str
    blockers_json: str
    operator_warnings_json: str
    rollback_reason: str
    created_at: str


@dataclass(frozen=True)
class _ArchiveSnapshot:
    present: bool
    row_count: int
    max_id: int
    fingerprint: str


REUSE_DECISION_STORAGE_POLICY_V3 = ReuseDecisionStoragePolicyV1(
    schema_version=REUSE_DECISION_STORAGE_POLICY_SCHEMA_V1,
    policy_name=REUSE_DECISION_STORAGE_POLICY_NAME,
    policy_version=REUSE_DECISION_STORAGE_POLICY_VERSION_V3,
    runtime_decision_write_table=REUSE_DECISION_TABLE_V3,
    runtime_authority_audit_write_table=REUSE_AUTHORITY_AUDIT_TABLE_V2,
    current_decision_read_tables=(
        REUSE_DECISION_TABLE_V2,
        REUSE_DECISION_TABLE_V3,
    ),
    current_authority_audit_read_tables=(
        REUSE_AUTHORITY_AUDIT_TABLE_V1,
        REUSE_AUTHORITY_AUDIT_TABLE_V2,
    ),
    archived_decision_read_api="load_archived_reuse_decision_rows_v2",
    archived_authority_audit_read_api=(
        "load_archived_reuse_authority_audit_rows_v1"
    ),
    instance_payload_schema=REUSE_DECISION_INSTANCE_SCHEMA_V3,
    persist_legacy_variant_alias=False,
    global_decision_ids=True,
    global_authority_audit_ids=True,
)


def reuse_decision_storage_policy_fingerprint(
    policy: ReuseDecisionStoragePolicyV1,
) -> str:
    return hashlib.sha256(_policy_json(policy).encode("ascii")).hexdigest()


def require_current_reuse_decision_storage_state_v1(
    state_or_db_path: str | Path,
) -> None:
    """Validate a fresh, upgradeable, or exact current decision store read-only."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            tables = _table_names(con)
            if REUSE_DECISION_STORAGE_STATE_TABLE_V1 in tables:
                load_reuse_decision_storage_state_v1(con)
                _load_current_decision_rows_v2(con)
                _load_current_authority_audit_rows(con)
                return
            if (
                REUSE_DECISION_TABLE_V3 in tables
                or REUSE_AUTHORITY_AUDIT_TABLE_V2 in tables
            ):
                raise ReuseDecisionStorageError(
                    "Current reuse-decision tables exist without their storage policy"
                )
            _validate_upgrade_archives(con)
    except ReuseDecisionStorageError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ReuseDecisionStorageError(
            "Could not validate reuse-decision storage before initialization: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def init_reuse_decision_storage_v3(
    con: sqlite3.Connection,
    *,
    now: str,
) -> ReuseDecisionStorageStateV1:
    if not str(now):
        raise ReuseDecisionStorageError(
            "Reuse-decision storage policy timestamp must be non-empty"
        )
    tables = _table_names(con)
    if REUSE_DECISION_STORAGE_STATE_TABLE_V1 in tables:
        return load_reuse_decision_storage_state_v1(con)
    if (
        REUSE_DECISION_TABLE_V3 in tables
        or REUSE_AUTHORITY_AUDIT_TABLE_V2 in tables
    ):
        raise ReuseDecisionStorageError(
            "Current reuse-decision tables exist without their storage policy"
        )

    _validate_upgrade_archives(con)
    archived_decisions = _archive_snapshot(
        con,
        table=REUSE_DECISION_TABLE_V2,
        id_column="decision_id",
    )
    archived_audits = _archive_snapshot(
        con,
        table=REUSE_AUTHORITY_AUDIT_TABLE_V1,
        id_column="authority_audit_id",
    )
    _init_current_decision_table_v3(con)
    _init_current_authority_audit_table_v2(con)
    _init_storage_state_table_v1(con)

    policy = REUSE_DECISION_STORAGE_POLICY_V3
    fingerprint = reuse_decision_storage_policy_fingerprint(policy)
    con.execute(
        f"""
        INSERT INTO {REUSE_DECISION_STORAGE_STATE_TABLE_V1}(
            singleton_id, schema_version, policy_name, policy_version,
            policy_fingerprint, policy_json,
            archived_decision_table_present,
            archived_decision_row_count, archived_decision_max_id,
            archived_decision_fingerprint,
            archived_authority_audit_table_present,
            archived_authority_audit_row_count,
            archived_authority_audit_max_id,
            archived_authority_audit_fingerprint,
            initialized_at, updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy.schema_version,
            policy.policy_name,
            policy.policy_version,
            fingerprint,
            _policy_json(policy),
            int(archived_decisions.present),
            archived_decisions.row_count,
            archived_decisions.max_id,
            archived_decisions.fingerprint,
            int(archived_audits.present),
            archived_audits.row_count,
            archived_audits.max_id,
            archived_audits.fingerprint,
            str(now),
            str(now),
        ),
    )
    return load_reuse_decision_storage_state_v1(con)


def load_reuse_decision_storage_state_v1(
    con: sqlite3.Connection,
) -> ReuseDecisionStorageStateV1:
    _validate_storage_state_schema_v1(con)
    try:
        row = con.execute(
            f"""
            SELECT schema_version, policy_name, policy_version,
                   policy_fingerprint, policy_json,
                   archived_decision_table_present,
                   archived_decision_row_count, archived_decision_max_id,
                   archived_decision_fingerprint,
                   archived_authority_audit_table_present,
                   archived_authority_audit_row_count,
                   archived_authority_audit_max_id,
                   archived_authority_audit_fingerprint,
                   initialized_at, updated_at
            FROM {REUSE_DECISION_STORAGE_STATE_TABLE_V1}
            WHERE singleton_id=1
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise ReuseDecisionStorageError(
            "Reuse-decision storage policy state is malformed"
        ) from exc
    if row is None:
        raise ReuseDecisionStorageError(
            "Reuse-decision storage policy has no singleton row"
        )

    policy = REUSE_DECISION_STORAGE_POLICY_V3
    fingerprint = reuse_decision_storage_policy_fingerprint(policy)
    expected_policy = (
        policy.schema_version,
        policy.policy_name,
        policy.policy_version,
        fingerprint,
        _policy_json(policy),
    )
    actual_policy = tuple(str(value or "") for value in row[:5])
    if actual_policy != expected_policy:
        raise ReuseDecisionStorageError(
            "Reuse-decision storage policy differs from approved v3"
        )

    archived_decisions = _archive_snapshot(
        con,
        table=REUSE_DECISION_TABLE_V2,
        id_column="decision_id",
    )
    archived_audits = _archive_snapshot(
        con,
        table=REUSE_AUTHORITY_AUDIT_TABLE_V1,
        id_column="authority_audit_id",
    )
    stored_archive = (
        _db_bool(row[5], label="archived decision table presence"),
        _nonnegative_int(row[6], label="archived decision row count"),
        _nonnegative_int(row[7], label="archived decision maximum ID"),
        str(row[8] or ""),
        _db_bool(row[9], label="archived audit table presence"),
        _nonnegative_int(row[10], label="archived audit row count"),
        _nonnegative_int(row[11], label="archived audit maximum ID"),
        str(row[12] or ""),
    )
    actual_archive = (
        archived_decisions.present,
        archived_decisions.row_count,
        archived_decisions.max_id,
        archived_decisions.fingerprint,
        archived_audits.present,
        archived_audits.row_count,
        archived_audits.max_id,
        archived_audits.fingerprint,
    )
    if stored_archive != actual_archive:
        raise ReuseDecisionStorageError(
            "Frozen reuse-decision or authority-audit archive changed"
        )
    initialized_at = str(row[13] or "")
    updated_at = str(row[14] or "")
    if not initialized_at or not updated_at:
        raise ReuseDecisionStorageError(
            "Reuse-decision storage policy timestamps are missing"
        )

    _validate_current_decision_schema_v3(con)
    _validate_current_authority_audit_schema_v2(con)
    return ReuseDecisionStorageStateV1(
        policy=policy,
        policy_fingerprint=fingerprint,
        archived_decision_table_present=archived_decisions.present,
        archived_decision_row_count=archived_decisions.row_count,
        archived_decision_max_id=archived_decisions.max_id,
        archived_decision_fingerprint=archived_decisions.fingerprint,
        archived_authority_audit_table_present=archived_audits.present,
        archived_authority_audit_row_count=archived_audits.row_count,
        archived_authority_audit_max_id=archived_audits.max_id,
        archived_authority_audit_fingerprint=archived_audits.fingerprint,
        initialized_at=initialized_at,
        updated_at=updated_at,
    )


def build_reuse_decision_instance_payload_v3(
    task_instance: TaskInstanceV3,
) -> dict[str, Any]:
    if not isinstance(task_instance, TaskInstanceV3):
        raise ReuseDecisionStorageError(
            "Reuse-decision v3 writes require TaskInstanceV3"
        )
    payload = dict(task_instance.as_dict())
    if payload.pop("schema_version", None) != TASK_INSTANCE_SCHEMA_V3:
        raise ReuseDecisionStorageError(
            "Reuse-decision v3 task-instance schema is invalid"
        )
    payload = {
        "schema_version": REUSE_DECISION_INSTANCE_SCHEMA_V3,
        **payload,
    }
    _reject_alias_keys(payload)
    return payload


def append_reuse_decision_v3(
    con: sqlite3.Connection,
    decision: ReuseDecisionV2,
    *,
    now: str,
    _validate_storage: bool = True,
) -> int:
    if not con.in_transaction:
        raise ReuseDecisionStorageError(
            "Reuse-decision v3 append requires an active write transaction"
        )
    if _validate_storage:
        _validate_current_write_policy(con)
    decision_id = _next_global_id(
        con,
        tables=(REUSE_DECISION_TABLE_V2, REUSE_DECISION_TABLE_V3),
        id_column="decision_id",
    )
    task_instance = decision.task_instance
    payload = build_reuse_decision_instance_payload_v3(task_instance)
    reasons_json = _canonical_json(list(decision.reasons))
    instance_json = _canonical_json(payload)
    con.execute(
        f"""
        INSERT INTO {REUSE_DECISION_TABLE_V3}(
            decision_id, schema_version, stage, task_base, instance_id,
            task_coordinate_id, identity_hash, params_hash,
            symbolic_inputs_hash, symbolic_outputs_hash, contract_hash,
            action, legacy_done, queued, force, worker_callable,
            reasons_json, instance_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            REUSE_DECISION_ROW_SCHEMA_V3,
            task_instance.stage,
            task_instance.task_base,
            task_instance.instance_id,
            task_instance.task_coordinate_id,
            task_instance.identity_hash,
            task_instance.params_hash,
            task_instance.symbolic_inputs_hash,
            task_instance.symbolic_outputs_hash,
            task_instance.contract_hash,
            decision.action,
            int(decision.legacy_done),
            int(decision.queued),
            int(decision.force),
            str(decision.worker_callable or ""),
            reasons_json,
            instance_json,
            str(now),
        ),
    )
    row = _select_current_decision_row(con, decision_id)
    if row is None:
        raise ReuseDecisionStorageError(
            "Reuse-decision v3 append returned no persisted row"
        )
    _decode_current_decision_row_v2(row)
    return decision_id


def append_reuse_authority_decision_v2(
    con: sqlite3.Connection,
    decision: ReuseDecisionV2,
    audit: Any,
    *,
    now: str,
) -> tuple[int, int]:
    if not con.in_transaction:
        raise ReuseDecisionStorageError(
            "Reuse authority append requires an active write transaction"
        )
    if decision.action not in {
        "v2_reuse",
        "v2_recompute",
        "v2_recompute_completed",
        "v2_fallback_legacy",
    }:
        raise ValueError(
            f"Authority audit cannot attach to action {decision.action!r}"
        )
    task_instance = decision.task_instance
    if tuple(audit.task_key) != task_instance.key:
        raise ValueError("Authority audit task key differs from decision")
    if str(audit.instance_id) != task_instance.instance_id:
        raise ValueError("Authority audit instance differs from decision")

    _validate_current_write_policy(con)
    _validate_authority_source_decision(con, audit)
    decision_id = append_reuse_decision_v3(
        con,
        decision,
        now=now,
        _validate_storage=False,
    )
    authority_audit_id = _next_global_id(
        con,
        tables=(
            REUSE_AUTHORITY_AUDIT_TABLE_V1,
            REUSE_AUTHORITY_AUDIT_TABLE_V2,
        ),
        id_column="authority_audit_id",
    )
    con.execute(
        f"""
        INSERT INTO {REUSE_AUTHORITY_AUDIT_TABLE_V2}(
            authority_audit_id, storage_schema_version,
            schema_version, event_type, invocation_id,
            policy_name, policy_version, policy_fingerprint,
            evidence_snapshot_hash, source_decision_id, decision_id,
            stage, task_base, instance_id, legacy_action,
            candidate_action, comparison, health_status,
            blockers_json, operator_warnings_json,
            rollback_reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            authority_audit_id,
            REUSE_AUTHORITY_AUDIT_STORAGE_SCHEMA_V2,
            str(audit.schema_version),
            str(audit.event_type),
            str(audit.invocation_id),
            str(audit.policy_name),
            str(audit.policy_version),
            str(audit.policy_fingerprint),
            str(audit.evidence_snapshot_hash or ""),
            audit.source_decision_id,
            decision_id,
            task_instance.stage,
            task_instance.task_base,
            task_instance.instance_id,
            str(audit.legacy_action),
            str(audit.candidate_action),
            str(audit.comparison),
            str(audit.health_status),
            _canonical_json(list(audit.blockers)),
            _canonical_json(list(audit.operator_warnings)),
            str(audit.rollback_reason or ""),
            str(now),
        ),
    )
    stored = _select_current_authority_audit_row(
        con,
        authority_audit_id,
    )
    if stored is None:
        raise ReuseDecisionStorageError(
            "Reuse authority audit v2 append returned no persisted row"
        )
    decoded = _decode_current_authority_audit_row(stored)
    if decoded.decision_id != decision_id:
        raise ReuseDecisionStorageError(
            "Reuse authority audit v2 differs from its appended decision"
        )
    return decision_id, authority_audit_id


def load_current_reuse_decision_rows_v2(
    state_or_db_path: str | Path,
    *,
    limit: int | None = None,
    latest_first: bool = True,
) -> tuple[CurrentStoredReuseDecisionRowV2, ...]:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return ()
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            load_reuse_decision_storage_state_v1(con)
            rows = _load_current_decision_rows_v2(con)
    except ReuseDecisionStorageError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ReuseDecisionStorageError(
            "Could not read current reuse-decision history: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return _ordered_rows(
        rows,
        id_attr="decision_id",
        latest_first=latest_first,
        limit=limit,
    )


def load_current_reuse_decision_rows_v1_compatibility(
    state_or_db_path: str | Path,
    *,
    limit: int | None = None,
    latest_first: bool = True,
) -> tuple[StoredReuseDecisionRowV1, ...]:
    """Load the released alias-bearing merged row view explicitly."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return ()
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            load_reuse_decision_storage_state_v1(con)
            rows = _load_current_decision_rows_v1_compatibility(con)
    except ReuseDecisionStorageError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ReuseDecisionStorageError(
            "Could not read compatibility reuse-decision history: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return _ordered_rows(
        rows,
        id_attr="decision_id",
        latest_first=latest_first,
        limit=limit,
    )


def load_archived_reuse_decision_rows_v2(
    state_or_db_path: str | Path,
    *,
    limit: int | None = None,
    latest_first: bool = True,
) -> tuple[StoredReuseDecisionRowV1, ...]:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return ()
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            tables = _table_names(con)
            if REUSE_DECISION_STORAGE_STATE_TABLE_V1 in tables:
                load_reuse_decision_storage_state_v1(con)
            elif REUSE_DECISION_TABLE_V3 in tables:
                raise ReuseDecisionStorageError(
                    "Current decision storage lacks its policy"
                )
            if REUSE_DECISION_TABLE_V2 not in tables:
                return ()
            _validate_archived_decision_schema_v2(con)
            rows = _load_archived_decision_rows(con)
    except ReuseDecisionStorageError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ReuseDecisionStorageError(
            "Could not read archived reuse-decision v2 history: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return _ordered_rows(
        rows,
        id_attr="decision_id",
        latest_first=latest_first,
        limit=limit,
    )


def load_current_reuse_authority_audit_rows_v1(
    state_or_db_path: str | Path,
) -> tuple[StoredReuseAuthorityAuditRowV1, ...]:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return ()
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            load_reuse_decision_storage_state_v1(con)
            return _load_current_authority_audit_rows(con)
    except ReuseDecisionStorageError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ReuseDecisionStorageError(
            "Could not read current reuse authority audit: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def load_archived_reuse_authority_audit_rows_v1(
    state_or_db_path: str | Path,
) -> tuple[StoredReuseAuthorityAuditRowV1, ...]:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return ()
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            tables = _table_names(con)
            if REUSE_DECISION_STORAGE_STATE_TABLE_V1 in tables:
                load_reuse_decision_storage_state_v1(con)
            elif REUSE_AUTHORITY_AUDIT_TABLE_V2 in tables:
                raise ReuseDecisionStorageError(
                    "Current authority-audit storage lacks its policy"
                )
            if REUSE_AUTHORITY_AUDIT_TABLE_V1 not in tables:
                return ()
            _validate_archived_authority_audit_schema_v1(con)
            return _load_archived_authority_audit_rows(con)
    except ReuseDecisionStorageError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ReuseDecisionStorageError(
            "Could not read archived reuse authority audit v1: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def current_reuse_decision_storage_available_v1(
    state_or_db_path: str | Path,
) -> bool:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return False
    with sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro",
        uri=True,
    ) as con:
        con.execute("PRAGMA query_only=ON")
        return (
            REUSE_DECISION_STORAGE_STATE_TABLE_V1 in _table_names(con)
        )


def archived_reuse_decision_storage_available_v2(
    state_or_db_path: str | Path,
) -> bool:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return False
    with sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro",
        uri=True,
    ) as con:
        con.execute("PRAGMA query_only=ON")
        return REUSE_DECISION_TABLE_V2 in _table_names(con)


def archived_reuse_authority_audit_storage_available_v1(
    state_or_db_path: str | Path,
) -> bool:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return False
    with sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro",
        uri=True,
    ) as con:
        con.execute("PRAGMA query_only=ON")
        return REUSE_AUTHORITY_AUDIT_TABLE_V1 in _table_names(con)


def _init_current_decision_table_v3(con: sqlite3.Connection) -> None:
    con.execute(
        f"""
        CREATE TABLE {REUSE_DECISION_TABLE_V3}
        (
            decision_id INTEGER PRIMARY KEY,
            schema_version TEXT NOT NULL,
            stage TEXT NOT NULL,
            task_base TEXT NOT NULL,
            instance_id TEXT NOT NULL,
            task_coordinate_id TEXT NOT NULL,
            identity_hash TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            symbolic_inputs_hash TEXT NOT NULL,
            symbolic_outputs_hash TEXT NOT NULL,
            contract_hash TEXT NOT NULL,
            action TEXT NOT NULL,
            legacy_done INTEGER NOT NULL CHECK(legacy_done IN (0, 1)),
            queued INTEGER NOT NULL CHECK(queued IN (0, 1)),
            force INTEGER NOT NULL CHECK(force IN (0, 1)),
            worker_callable TEXT NOT NULL DEFAULT '',
            reasons_json TEXT NOT NULL,
            instance_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        f"""
        CREATE INDEX idx_task_reuse_decisions_v3_lookup
        ON {REUSE_DECISION_TABLE_V3}(stage, task_base, instance_id)
        """
    )
    con.execute(
        f"""
        CREATE INDEX idx_task_reuse_decisions_v3_coordinate
        ON {REUSE_DECISION_TABLE_V3}(
            stage, task_base, task_coordinate_id
        )
        """
    )


def _init_current_authority_audit_table_v2(
    con: sqlite3.Connection,
) -> None:
    con.execute(
        f"""
        CREATE TABLE {REUSE_AUTHORITY_AUDIT_TABLE_V2}
        (
            authority_audit_id INTEGER PRIMARY KEY,
            storage_schema_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            event_type TEXT NOT NULL,
            invocation_id TEXT NOT NULL,
            policy_name TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_fingerprint TEXT NOT NULL,
            evidence_snapshot_hash TEXT NOT NULL DEFAULT '',
            source_decision_id INTEGER,
            decision_id INTEGER NOT NULL UNIQUE,
            stage TEXT NOT NULL,
            task_base TEXT NOT NULL,
            instance_id TEXT NOT NULL,
            legacy_action TEXT NOT NULL,
            candidate_action TEXT NOT NULL,
            comparison TEXT NOT NULL,
            health_status TEXT NOT NULL,
            blockers_json TEXT NOT NULL,
            operator_warnings_json TEXT NOT NULL,
            rollback_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(decision_id)
                REFERENCES {REUSE_DECISION_TABLE_V3}(decision_id)
        )
        """
    )
    con.execute(
        f"""
        CREATE INDEX idx_task_reuse_authority_audit_v2_lookup
        ON {REUSE_AUTHORITY_AUDIT_TABLE_V2}(
            invocation_id, stage, task_base, instance_id
        )
        """
    )


def _init_storage_state_table_v1(con: sqlite3.Connection) -> None:
    con.execute(
        f"""
        CREATE TABLE {REUSE_DECISION_STORAGE_STATE_TABLE_V1}
        (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            schema_version TEXT NOT NULL,
            policy_name TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_fingerprint TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            archived_decision_table_present INTEGER NOT NULL
                CHECK(archived_decision_table_present IN (0, 1)),
            archived_decision_row_count INTEGER NOT NULL
                CHECK(archived_decision_row_count >= 0),
            archived_decision_max_id INTEGER NOT NULL
                CHECK(archived_decision_max_id >= 0),
            archived_decision_fingerprint TEXT NOT NULL,
            archived_authority_audit_table_present INTEGER NOT NULL
                CHECK(archived_authority_audit_table_present IN (0, 1)),
            archived_authority_audit_row_count INTEGER NOT NULL
                CHECK(archived_authority_audit_row_count >= 0),
            archived_authority_audit_max_id INTEGER NOT NULL
                CHECK(archived_authority_audit_max_id >= 0),
            archived_authority_audit_fingerprint TEXT NOT NULL,
            initialized_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _load_current_decision_rows_v2(
    con: sqlite3.Connection,
) -> tuple[CurrentStoredReuseDecisionRowV2, ...]:
    _validate_current_decision_schema_v3(con)
    rows = [
        _current_decision_row_v2_from_archived_v1(row)
        for row in _load_archived_decision_rows(con)
    ]
    rows.extend(
        _decode_current_decision_row_v2(row)
        for row in con.execute(
            f"""
            SELECT decision_id, schema_version, stage, task_base,
                   instance_id, task_coordinate_id, identity_hash,
                   params_hash, symbolic_inputs_hash,
                   symbolic_outputs_hash, contract_hash, action,
                   legacy_done, queued, force, worker_callable,
                   reasons_json, instance_json, created_at
            FROM {REUSE_DECISION_TABLE_V3}
            ORDER BY decision_id
            """
        ).fetchall()
    )
    _validate_global_ids(
        rows,
        id_attr="decision_id",
        archived_version="v2",
        current_version="v3",
        label="reuse decision",
    )
    return tuple(sorted(rows, key=lambda row: row.decision_id))


def _load_current_decision_rows_v1_compatibility(
    con: sqlite3.Connection,
) -> tuple[StoredReuseDecisionRowV1, ...]:
    _validate_current_decision_schema_v3(con)
    rows = list(_load_archived_decision_rows(con))
    rows.extend(
        _decode_current_decision_row_v1_compatibility(row)
        for row in con.execute(
            f"""
            SELECT decision_id, schema_version, stage, task_base,
                   instance_id, task_coordinate_id, identity_hash,
                   params_hash, symbolic_inputs_hash,
                   symbolic_outputs_hash, contract_hash, action,
                   legacy_done, queued, force, worker_callable,
                   reasons_json, instance_json, created_at
            FROM {REUSE_DECISION_TABLE_V3}
            ORDER BY decision_id
            """
        ).fetchall()
    )
    _validate_global_ids(
        rows,
        id_attr="decision_id",
        archived_version="v2",
        current_version="v3",
        label="reuse decision",
    )
    return tuple(sorted(rows, key=lambda row: row.decision_id))


def _load_archived_decision_rows(
    con: sqlite3.Connection,
) -> tuple[StoredReuseDecisionRowV1, ...]:
    if REUSE_DECISION_TABLE_V2 not in _table_names(con):
        return ()
    columns = _validate_archived_decision_schema_v2(con)
    coordinate_sql = (
        "task_coordinate_id" if "task_coordinate_id" in columns else "''"
    )
    worker_sql = "worker_callable" if "worker_callable" in columns else "''"
    rows = con.execute(
        f"""
        SELECT decision_id, stage, task_base, instance_id,
               legacy_variant_str, {coordinate_sql}, identity_hash,
               params_hash, symbolic_inputs_hash,
               symbolic_outputs_hash, contract_hash, action,
               legacy_done, queued, force, {worker_sql},
               reasons_json, instance_json, created_at
        FROM {REUSE_DECISION_TABLE_V2}
        ORDER BY decision_id
        """
    ).fetchall()
    decoded = tuple(_decode_archived_decision_row(row) for row in rows)
    _validate_unique_positive_ids(
        decoded,
        id_attr="decision_id",
        label="archived reuse decision",
    )
    return decoded


def _decode_archived_decision_row(
    row: Iterable[Any],
) -> StoredReuseDecisionRowV1:
    values = tuple(row)
    return StoredReuseDecisionRowV1(
        storage_version="v2",
        decision_id=_positive_int(values[0], label="archived decision ID"),
        schema_version="scpolya.reuse_decision.storage.v2",
        stage=str(values[1] or ""),
        task_base=str(values[2] or ""),
        instance_id=str(values[3] or ""),
        legacy_variant_str=str(values[4] or ""),
        task_coordinate_id=str(values[5] or ""),
        identity_hash=str(values[6] or ""),
        params_hash=str(values[7] or ""),
        symbolic_inputs_hash=str(values[8] or ""),
        symbolic_outputs_hash=str(values[9] or ""),
        contract_hash=str(values[10] or ""),
        action=str(values[11] or ""),
        legacy_done=_db_bool(values[12], label="archived legacy_done"),
        queued=_db_bool(values[13], label="archived queued"),
        force=_db_bool(values[14], label="archived force"),
        worker_callable=str(values[15] or ""),
        reasons_json=str(values[16] or ""),
        instance_json=str(values[17] or ""),
        created_at=str(values[18] or ""),
    )


def _current_decision_row_v2_from_archived_v1(
    row: StoredReuseDecisionRowV1,
) -> CurrentStoredReuseDecisionRowV2:
    payload_raw = _load_json_compatibility(row.instance_json, default={})
    payload = payload_raw if isinstance(payload_raw, Mapping) else {}
    identity = _compatibility_coordinate_fields(payload.get("identity"))
    params = _compatibility_coordinate_fields(payload.get("params"))
    task_coordinate_id, coordinate_status = (
        _compatibility_coordinate_status(
            stage=row.stage,
            task_base=row.task_base,
            identity=identity,
            params=params,
            compatibility_alias=row.legacy_variant_str,
        )
    )
    reasons_raw = _load_json_compatibility(row.reasons_json, default=[])
    reasons = (
        tuple(str(reason) for reason in reasons_raw)
        if isinstance(reasons_raw, (list, tuple, Mapping))
        else ()
    )
    normalized_payload = _without_compatibility_alias_keys(payload)
    if not isinstance(normalized_payload, dict):
        raise ReuseDecisionStorageError(
            f"Archived reuse-decision row {row.decision_id} normalization failed"
        )
    return CurrentStoredReuseDecisionRowV2(
        storage_version=row.storage_version,
        decision_id=row.decision_id,
        storage_schema_version=row.schema_version,
        stage=row.stage,
        task_base=row.task_base,
        instance_id=row.instance_id,
        task_coordinate_id=task_coordinate_id,
        coordinate_status=coordinate_status,
        identity_hash=row.identity_hash,
        params_hash=row.params_hash,
        symbolic_inputs_hash=row.symbolic_inputs_hash,
        symbolic_outputs_hash=row.symbolic_outputs_hash,
        contract_hash=row.contract_hash,
        action=row.action,
        legacy_done=row.legacy_done,
        queued=row.queued,
        force=row.force,
        worker_callable=row.worker_callable,
        reasons=reasons,
        instance_payload=normalized_payload,
        created_at=row.created_at,
    )


def _decode_current_decision_row_v2(
    row: Iterable[Any],
) -> CurrentStoredReuseDecisionRowV2:
    values = tuple(row)
    decision_id = _positive_int(values[0], label="current decision ID")
    storage_schema_version = str(values[1] or "")
    if storage_schema_version != REUSE_DECISION_ROW_SCHEMA_V3:
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} has an unsupported schema"
        )
    reasons = _load_json(values[16], label="reuse-decision reasons")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) for reason in reasons
    ):
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} has malformed reasons"
        )
    payload = _load_json(values[17], label="reuse-decision instance")
    task_instance = _task_instance_from_payload_v3(
        payload,
        decision_id=decision_id,
    )
    result = CurrentStoredReuseDecisionRowV2(
        storage_version="v3",
        decision_id=decision_id,
        storage_schema_version=storage_schema_version,
        stage=str(values[2] or ""),
        task_base=str(values[3] or ""),
        instance_id=str(values[4] or ""),
        task_coordinate_id=str(values[5] or ""),
        coordinate_status="available",
        identity_hash=str(values[6] or ""),
        params_hash=str(values[7] or ""),
        symbolic_inputs_hash=str(values[8] or ""),
        symbolic_outputs_hash=str(values[9] or ""),
        contract_hash=str(values[10] or ""),
        action=str(values[11] or ""),
        legacy_done=_db_bool(values[12], label="current legacy_done"),
        queued=_db_bool(values[13], label="current queued"),
        force=_db_bool(values[14], label="current force"),
        worker_callable=str(values[15] or ""),
        reasons=tuple(reasons),
        instance_payload=payload,
        created_at=str(values[18] or ""),
    )
    expected_columns = (
        task_instance.stage,
        task_instance.task_base,
        task_instance.instance_id,
        task_instance.task_coordinate_id,
        task_instance.identity_hash,
        task_instance.params_hash,
        task_instance.symbolic_inputs_hash,
        task_instance.symbolic_outputs_hash,
        task_instance.contract_hash,
    )
    actual_columns = (
        result.stage,
        result.task_base,
        result.instance_id,
        result.task_coordinate_id,
        result.identity_hash,
        result.params_hash,
        result.symbolic_inputs_hash,
        result.symbolic_outputs_hash,
        result.contract_hash,
    )
    if actual_columns != expected_columns:
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} differs from its instance payload"
        )
    if not result.action or not result.created_at:
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} lacks action or timestamp"
        )
    return result


def _decode_current_decision_row_v1_compatibility(
    row: Iterable[Any],
) -> StoredReuseDecisionRowV1:
    values = tuple(row)
    current = _decode_current_decision_row_v2(values)
    task_instance = _task_instance_from_payload_v3(
        current.instance_payload,
        decision_id=current.decision_id,
    )
    return StoredReuseDecisionRowV1(
        storage_version=current.storage_version,
        decision_id=current.decision_id,
        schema_version=current.storage_schema_version,
        stage=current.stage,
        task_base=current.task_base,
        instance_id=current.instance_id,
        legacy_variant_str=(
            derive_legacy_variant_str_from_task_instance_v3(task_instance)
        ),
        task_coordinate_id=current.task_coordinate_id,
        identity_hash=current.identity_hash,
        params_hash=current.params_hash,
        symbolic_inputs_hash=current.symbolic_inputs_hash,
        symbolic_outputs_hash=current.symbolic_outputs_hash,
        contract_hash=current.contract_hash,
        action=current.action,
        legacy_done=current.legacy_done,
        queued=current.queued,
        force=current.force,
        worker_callable=current.worker_callable,
        reasons_json=str(values[16] or ""),
        instance_json=str(values[17] or ""),
        created_at=current.created_at,
    )


def _task_instance_from_payload_v3(
    payload: Any,
    *,
    decision_id: int,
) -> TaskInstanceV3:
    if not isinstance(payload, dict):
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} instance payload is not an object"
        )
    _reject_alias_keys(payload)
    expected_keys = {
        "schema_version",
        "stage",
        "task_base",
        "level",
        "identity",
        "params",
        "consumes",
        "produces",
        "reuse_policy_version",
        "implementation_version",
        "identity_hash",
        "params_hash",
        "symbolic_inputs_hash",
        "symbolic_outputs_hash",
        "contract_hash",
        "instance_id",
        "task_coordinate_id",
        "source_contracts",
    }
    if set(payload) != expected_keys:
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} instance fields differ from schema"
        )
    if payload.get("schema_version") != REUSE_DECISION_INSTANCE_SCHEMA_V3:
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} instance schema is unsupported"
        )
    identity = _instance_fields(
        payload.get("identity"),
        label="identity",
        decision_id=decision_id,
    )
    params = _instance_fields(
        payload.get("params"),
        label="params",
        decision_id=decision_id,
    )
    consumes = _string_tuple(
        payload.get("consumes"),
        label="consumes",
        decision_id=decision_id,
    )
    produces = _string_tuple(
        payload.get("produces"),
        label="produces",
        decision_id=decision_id,
    )
    source_contracts = payload.get("source_contracts")
    if not isinstance(source_contracts, dict):
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} source contracts are malformed"
        )
    level = payload.get("level")
    if level not in {None, "global", "sample", "pp_unit", "unit"}:
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} task level is malformed"
        )
    stage = str(payload.get("stage") or "")
    task_base = str(payload.get("task_base") or "")
    reuse_policy_version = str(payload.get("reuse_policy_version") or "")
    implementation_version = str(payload.get("implementation_version") or "")
    if not all(
        (stage, task_base, reuse_policy_version, implementation_version)
    ):
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} instance identity is incomplete"
        )
    task_instance = TaskInstanceV3(
        stage=stage,
        task_base=task_base,
        level=level,
        identity=identity,
        params=params,
        consumes=consumes,
        produces=produces,
        reuse_policy_version=reuse_policy_version,
        implementation_version=implementation_version,
        source_contracts=source_contracts,
    )
    expected_payload = build_reuse_decision_instance_payload_v3(task_instance)
    if payload != expected_payload:
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} instance hashes or fields disagree"
        )
    return task_instance


def _load_current_authority_audit_rows(
    con: sqlite3.Connection,
) -> tuple[StoredReuseAuthorityAuditRowV1, ...]:
    _validate_current_authority_audit_schema_v2(con)
    decisions = {
        row.decision_id: row for row in _load_current_decision_rows_v2(con)
    }
    rows = list(_load_archived_authority_audit_rows(con))
    current_rows = tuple(
        _decode_current_authority_audit_row(row)
        for row in con.execute(
            f"""
            SELECT authority_audit_id, storage_schema_version,
                   schema_version, event_type, invocation_id,
                   policy_name, policy_version, policy_fingerprint,
                   evidence_snapshot_hash, source_decision_id,
                   decision_id, stage, task_base, instance_id,
                   legacy_action, candidate_action, comparison,
                   health_status, blockers_json,
                   operator_warnings_json, rollback_reason, created_at
            FROM {REUSE_AUTHORITY_AUDIT_TABLE_V2}
            ORDER BY authority_audit_id
            """
        ).fetchall()
    )
    for row in current_rows:
        decision = decisions.get(row.decision_id)
        if decision is None or decision.storage_version != "v3":
            raise ReuseDecisionStorageError(
                f"Authority audit v2 row {row.authority_audit_id} lacks its v3 decision"
            )
        if (
            decision.stage,
            decision.task_base,
            decision.instance_id,
        ) != (row.stage, row.task_base, row.instance_id):
            raise ReuseDecisionStorageError(
                f"Authority audit v2 row {row.authority_audit_id} differs from its decision"
            )
        if row.source_decision_id is not None:
            source = decisions.get(row.source_decision_id)
            if source is None or (
                source.stage,
                source.task_base,
                source.instance_id,
            ) != (row.stage, row.task_base, row.instance_id):
                raise ReuseDecisionStorageError(
                    f"Authority audit v2 row {row.authority_audit_id} has an invalid source"
                )
    rows.extend(current_rows)
    _validate_global_ids(
        rows,
        id_attr="authority_audit_id",
        archived_version="v1",
        current_version="v2",
        label="reuse authority audit",
    )
    return tuple(sorted(rows, key=lambda row: row.authority_audit_id))


def _load_archived_authority_audit_rows(
    con: sqlite3.Connection,
) -> tuple[StoredReuseAuthorityAuditRowV1, ...]:
    if REUSE_AUTHORITY_AUDIT_TABLE_V1 not in _table_names(con):
        return ()
    _validate_archived_authority_audit_schema_v1(con)
    rows = con.execute(
        f"""
        SELECT authority_audit_id, schema_version, event_type,
               invocation_id, policy_name, policy_version,
               policy_fingerprint, evidence_snapshot_hash,
               source_decision_id, decision_id, stage, task_base,
               instance_id, legacy_action, candidate_action,
               comparison, health_status, blockers_json,
               operator_warnings_json, rollback_reason, created_at
        FROM {REUSE_AUTHORITY_AUDIT_TABLE_V1}
        ORDER BY authority_audit_id
        """
    ).fetchall()
    decoded = tuple(_decode_archived_authority_audit_row(row) for row in rows)
    _validate_unique_positive_ids(
        decoded,
        id_attr="authority_audit_id",
        label="archived reuse authority audit",
    )
    return decoded


def _decode_archived_authority_audit_row(
    row: Iterable[Any],
) -> StoredReuseAuthorityAuditRowV1:
    values = tuple(row)
    return StoredReuseAuthorityAuditRowV1(
        storage_version="v1",
        authority_audit_id=_positive_int(
            values[0], label="archived authority audit ID"
        ),
        storage_schema_version="scpolya.reuse_authority_audit.storage.v1",
        schema_version=str(values[1] or ""),
        event_type=str(values[2] or ""),
        invocation_id=str(values[3] or ""),
        policy_name=str(values[4] or ""),
        policy_version=str(values[5] or ""),
        policy_fingerprint=str(values[6] or ""),
        evidence_snapshot_hash=str(values[7] or ""),
        source_decision_id=(
            None
            if values[8] is None
            else _positive_int(values[8], label="archived source decision ID")
        ),
        decision_id=_positive_int(
            values[9], label="archived audited decision ID"
        ),
        stage=str(values[10] or ""),
        task_base=str(values[11] or ""),
        instance_id=str(values[12] or ""),
        legacy_action=str(values[13] or ""),
        candidate_action=str(values[14] or ""),
        comparison=str(values[15] or ""),
        health_status=str(values[16] or ""),
        blockers_json=str(values[17] or ""),
        operator_warnings_json=str(values[18] or ""),
        rollback_reason=str(values[19] or ""),
        created_at=str(values[20] or ""),
    )


def _decode_current_authority_audit_row(
    row: Iterable[Any],
) -> StoredReuseAuthorityAuditRowV1:
    values = tuple(row)
    authority_audit_id = _positive_int(
        values[0], label="current authority audit ID"
    )
    storage_schema_version = str(values[1] or "")
    if storage_schema_version != REUSE_AUTHORITY_AUDIT_STORAGE_SCHEMA_V2:
        raise ReuseDecisionStorageError(
            f"Authority audit v2 row {authority_audit_id} has an unsupported schema"
        )
    for index, label in (
        (18, "blockers"),
        (19, "operator warnings"),
    ):
        payload = _load_json(values[index], label=label)
        if not isinstance(payload, list) or any(
            not isinstance(item, str) for item in payload
        ):
            raise ReuseDecisionStorageError(
                f"Authority audit v2 row {authority_audit_id} has malformed {label}"
            )
    result = StoredReuseAuthorityAuditRowV1(
        storage_version="v2",
        authority_audit_id=authority_audit_id,
        storage_schema_version=storage_schema_version,
        schema_version=str(values[2] or ""),
        event_type=str(values[3] or ""),
        invocation_id=str(values[4] or ""),
        policy_name=str(values[5] or ""),
        policy_version=str(values[6] or ""),
        policy_fingerprint=str(values[7] or ""),
        evidence_snapshot_hash=str(values[8] or ""),
        source_decision_id=(
            None
            if values[9] is None
            else _positive_int(values[9], label="current source decision ID")
        ),
        decision_id=_positive_int(
            values[10], label="current audited decision ID"
        ),
        stage=str(values[11] or ""),
        task_base=str(values[12] or ""),
        instance_id=str(values[13] or ""),
        legacy_action=str(values[14] or ""),
        candidate_action=str(values[15] or ""),
        comparison=str(values[16] or ""),
        health_status=str(values[17] or ""),
        blockers_json=str(values[18] or ""),
        operator_warnings_json=str(values[19] or ""),
        rollback_reason=str(values[20] or ""),
        created_at=str(values[21] or ""),
    )
    required = (
        result.schema_version,
        result.event_type,
        result.invocation_id,
        result.policy_name,
        result.policy_version,
        result.policy_fingerprint,
        result.stage,
        result.task_base,
        result.instance_id,
        result.legacy_action,
        result.candidate_action,
        result.comparison,
        result.health_status,
        result.created_at,
    )
    if not all(required):
        raise ReuseDecisionStorageError(
            f"Authority audit v2 row {authority_audit_id} is incomplete"
        )
    return result


def _validate_authority_source_decision(
    con: sqlite3.Connection,
    audit: Any,
) -> None:
    source_decision_id = audit.source_decision_id
    if source_decision_id is None:
        return
    rows = _load_current_decision_rows_v2(con)
    source = next(
        (row for row in rows if row.decision_id == source_decision_id),
        None,
    )
    if source is None:
        raise RuntimeError(
            "Authority audit source decision no longer exists: "
            f"{source_decision_id}"
        )
    expected = (
        str(audit.task_key[0]),
        str(audit.task_key[1]),
        str(audit.instance_id),
    )
    if (source.stage, source.task_base, source.instance_id) != expected:
        raise RuntimeError(
            "Authority audit source decision does not match exact instance"
        )
    latest = max(
        (
            row.decision_id
            for row in rows
            if row.instance_id == source.instance_id
        ),
        default=0,
    )
    if latest != source_decision_id:
        raise RuntimeError(
            "Authority audit exact instance changed after replay snapshot"
        )


def _validate_upgrade_archives(con: sqlite3.Connection) -> None:
    tables = _table_names(con)
    has_decisions = REUSE_DECISION_TABLE_V2 in tables
    has_audits = REUSE_AUTHORITY_AUDIT_TABLE_V1 in tables
    if has_decisions:
        _validate_archived_decision_schema_v2(con)
        decisions = _load_archived_decision_rows(con)
    else:
        decisions = ()
    if has_audits:
        if not has_decisions:
            raise ReuseDecisionStorageError(
                "Archived authority audits lack their v2 decision table"
            )
        _validate_archived_authority_audit_schema_v1(con)
        audits = _load_archived_authority_audit_rows(con)
        decisions_by_id = {row.decision_id: row for row in decisions}
        for audit in audits:
            decision = decisions_by_id.get(audit.decision_id)
            if decision is None or (
                decision.stage,
                decision.task_base,
                decision.instance_id,
            ) != (audit.stage, audit.task_base, audit.instance_id):
                raise ReuseDecisionStorageError(
                    "Archived authority audit differs from its v2 decision: "
                    f"{audit.authority_audit_id}"
                )
            if audit.source_decision_id is not None:
                source = decisions_by_id.get(audit.source_decision_id)
                if source is None or (
                    source.stage,
                    source.task_base,
                    source.instance_id,
                ) != (audit.stage, audit.task_base, audit.instance_id):
                    raise ReuseDecisionStorageError(
                        "Archived authority audit has an invalid source: "
                        f"{audit.authority_audit_id}"
                    )


def _validate_archived_decision_schema_v2(
    con: sqlite3.Connection,
) -> frozenset[str]:
    columns = frozenset(
        str(row[1])
        for row in con.execute(
            f"PRAGMA table_info({REUSE_DECISION_TABLE_V2})"
        ).fetchall()
    )
    allowed = (
        _ARCHIVE_DECISION_REQUIRED_COLUMNS
        | _ARCHIVE_DECISION_OPTIONAL_COLUMNS
    )
    if (
        not _ARCHIVE_DECISION_REQUIRED_COLUMNS.issubset(columns)
        or not columns.issubset(allowed)
    ):
        raise ReuseDecisionStorageError(
            "Archived reuse-decision v2 columns are unsupported"
        )
    return columns


def _validate_archived_authority_audit_schema_v1(
    con: sqlite3.Connection,
) -> None:
    columns = frozenset(
        str(row[1])
        for row in con.execute(
            f"PRAGMA table_info({REUSE_AUTHORITY_AUDIT_TABLE_V1})"
        ).fetchall()
    )
    if columns != _ARCHIVE_AUDIT_COLUMNS:
        raise ReuseDecisionStorageError(
            "Archived reuse authority audit v1 columns are unsupported"
        )


def _validate_current_decision_schema_v3(
    con: sqlite3.Connection,
) -> None:
    expected = (
        ("decision_id", "INTEGER", 0, 1),
        ("schema_version", "TEXT", 1, 0),
        ("stage", "TEXT", 1, 0),
        ("task_base", "TEXT", 1, 0),
        ("instance_id", "TEXT", 1, 0),
        ("task_coordinate_id", "TEXT", 1, 0),
        ("identity_hash", "TEXT", 1, 0),
        ("params_hash", "TEXT", 1, 0),
        ("symbolic_inputs_hash", "TEXT", 1, 0),
        ("symbolic_outputs_hash", "TEXT", 1, 0),
        ("contract_hash", "TEXT", 1, 0),
        ("action", "TEXT", 1, 0),
        ("legacy_done", "INTEGER", 1, 0),
        ("queued", "INTEGER", 1, 0),
        ("force", "INTEGER", 1, 0),
        ("worker_callable", "TEXT", 1, 0),
        ("reasons_json", "TEXT", 1, 0),
        ("instance_json", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    )
    _require_column_schema(
        con,
        table=REUSE_DECISION_TABLE_V3,
        expected=expected,
        label="Reuse-decision v3",
    )
    _require_index(
        con,
        table=REUSE_DECISION_TABLE_V3,
        name="idx_task_reuse_decisions_v3_lookup",
        columns=("stage", "task_base", "instance_id"),
        unique=False,
    )
    _require_index(
        con,
        table=REUSE_DECISION_TABLE_V3,
        name="idx_task_reuse_decisions_v3_coordinate",
        columns=("stage", "task_base", "task_coordinate_id"),
        unique=False,
    )


def _validate_current_authority_audit_schema_v2(
    con: sqlite3.Connection,
) -> None:
    expected = (
        ("authority_audit_id", "INTEGER", 0, 1),
        ("storage_schema_version", "TEXT", 1, 0),
        ("schema_version", "TEXT", 1, 0),
        ("event_type", "TEXT", 1, 0),
        ("invocation_id", "TEXT", 1, 0),
        ("policy_name", "TEXT", 1, 0),
        ("policy_version", "TEXT", 1, 0),
        ("policy_fingerprint", "TEXT", 1, 0),
        ("evidence_snapshot_hash", "TEXT", 1, 0),
        ("source_decision_id", "INTEGER", 0, 0),
        ("decision_id", "INTEGER", 1, 0),
        ("stage", "TEXT", 1, 0),
        ("task_base", "TEXT", 1, 0),
        ("instance_id", "TEXT", 1, 0),
        ("legacy_action", "TEXT", 1, 0),
        ("candidate_action", "TEXT", 1, 0),
        ("comparison", "TEXT", 1, 0),
        ("health_status", "TEXT", 1, 0),
        ("blockers_json", "TEXT", 1, 0),
        ("operator_warnings_json", "TEXT", 1, 0),
        ("rollback_reason", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    )
    _require_column_schema(
        con,
        table=REUSE_AUTHORITY_AUDIT_TABLE_V2,
        expected=expected,
        label="Reuse authority audit v2",
    )
    _require_index(
        con,
        table=REUSE_AUTHORITY_AUDIT_TABLE_V2,
        name="idx_task_reuse_authority_audit_v2_lookup",
        columns=("invocation_id", "stage", "task_base", "instance_id"),
        unique=False,
    )
    foreign_keys = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in con.execute(
            f"PRAGMA foreign_key_list({REUSE_AUTHORITY_AUDIT_TABLE_V2})"
        ).fetchall()
    }
    if foreign_keys != {
        ("decision_id", REUSE_DECISION_TABLE_V3, "decision_id")
    }:
        raise ReuseDecisionStorageError(
            "Reuse authority audit v2 foreign keys differ from approved schema"
        )


def _validate_storage_state_schema_v1(con: sqlite3.Connection) -> None:
    expected = (
        ("singleton_id", "INTEGER", 0, 1),
        ("schema_version", "TEXT", 1, 0),
        ("policy_name", "TEXT", 1, 0),
        ("policy_version", "TEXT", 1, 0),
        ("policy_fingerprint", "TEXT", 1, 0),
        ("policy_json", "TEXT", 1, 0),
        ("archived_decision_table_present", "INTEGER", 1, 0),
        ("archived_decision_row_count", "INTEGER", 1, 0),
        ("archived_decision_max_id", "INTEGER", 1, 0),
        ("archived_decision_fingerprint", "TEXT", 1, 0),
        ("archived_authority_audit_table_present", "INTEGER", 1, 0),
        ("archived_authority_audit_row_count", "INTEGER", 1, 0),
        ("archived_authority_audit_max_id", "INTEGER", 1, 0),
        ("archived_authority_audit_fingerprint", "TEXT", 1, 0),
        ("initialized_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    )
    _require_column_schema(
        con,
        table=REUSE_DECISION_STORAGE_STATE_TABLE_V1,
        expected=expected,
        label="Reuse-decision storage state",
    )


def _validate_current_write_policy(con: sqlite3.Connection) -> None:
    """Validate the immutable policy and current schemas without rescanning archives."""

    _validate_storage_state_schema_v1(con)
    row = con.execute(
        f"""
        SELECT schema_version, policy_name, policy_version,
               policy_fingerprint, policy_json
        FROM {REUSE_DECISION_STORAGE_STATE_TABLE_V1}
        WHERE singleton_id=1
        """
    ).fetchone()
    policy = REUSE_DECISION_STORAGE_POLICY_V3
    expected = (
        policy.schema_version,
        policy.policy_name,
        policy.policy_version,
        reuse_decision_storage_policy_fingerprint(policy),
        _policy_json(policy),
    )
    if row is None or tuple(str(value or "") for value in row) != expected:
        raise ReuseDecisionStorageError(
            "Reuse-decision write policy differs from approved v3"
        )
    _validate_current_decision_schema_v3(con)
    _validate_current_authority_audit_schema_v2(con)


def _require_column_schema(
    con: sqlite3.Connection,
    *,
    table: str,
    expected: tuple[tuple[str, str, int, int], ...],
    label: str,
) -> None:
    try:
        actual = tuple(
            (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
            for row in con.execute(f"PRAGMA table_info({table})").fetchall()
        )
    except sqlite3.Error as exc:
        raise ReuseDecisionStorageError(f"{label} schema is malformed") from exc
    if actual != expected:
        raise ReuseDecisionStorageError(
            f"{label} columns differ from approved schema"
        )


def _require_index(
    con: sqlite3.Connection,
    *,
    table: str,
    name: str,
    columns: tuple[str, ...],
    unique: bool,
) -> None:
    indexes = {
        str(row[1]): bool(row[2])
        for row in con.execute(f"PRAGMA index_list({table})").fetchall()
    }
    index_columns = tuple(
        str(row[2])
        for row in con.execute(f'PRAGMA index_info("{name}")').fetchall()
    )
    if indexes.get(name) is not unique or index_columns != columns:
        raise ReuseDecisionStorageError(
            f"{table} index {name} differs from approved schema"
        )


def _archive_snapshot(
    con: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
) -> _ArchiveSnapshot:
    if table not in _table_names(con):
        payload = {"table": table, "present": False}
        return _ArchiveSnapshot(
            present=False,
            row_count=0,
            max_id=0,
            fingerprint=_sha256_json(payload),
        )
    columns = tuple(
        str(row[1])
        for row in con.execute(f"PRAGMA table_info({table})").fetchall()
    )
    if id_column not in columns:
        raise ReuseDecisionStorageError(
            f"Archived table {table} lacks {id_column}"
        )
    objects = tuple(
        {
            "type": str(row[0]),
            "name": str(row[1]),
            "sql": None if row[2] is None else str(row[2]),
        }
        for row in con.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE tbl_name=? AND type IN ('table', 'index', 'trigger')
            ORDER BY type, name
            """,
            (table,),
        ).fetchall()
    )
    quoted_table = '"' + table.replace('"', '""') + '"'
    quoted_id = '"' + id_column.replace('"', '""') + '"'
    rows = tuple(
        tuple(_fingerprint_value(value) for value in row)
        for row in con.execute(
            f"SELECT * FROM {quoted_table} ORDER BY {quoted_id}"
        ).fetchall()
    )
    sequence = None
    if "sqlite_sequence" in _table_names(con):
        sequence_row = con.execute(
            "SELECT seq FROM sqlite_sequence WHERE name=?",
            (table,),
        ).fetchone()
        sequence = None if sequence_row is None else int(sequence_row[0])
    ids = [
        _positive_int(row[columns.index(id_column)], label=f"{table} ID")
        for row in rows
    ]
    if len(ids) != len(set(ids)):
        raise ReuseDecisionStorageError(
            f"Archived table {table} contains duplicate IDs"
        )
    payload = {
        "table": table,
        "present": True,
        "columns": list(columns),
        "objects": list(objects),
        "sqlite_sequence": sequence,
        "rows": [list(row) for row in rows],
    }
    return _ArchiveSnapshot(
        present=True,
        row_count=len(rows),
        max_id=max(ids, default=0),
        fingerprint=_sha256_json(payload),
    )


def _next_global_id(
    con: sqlite3.Connection,
    *,
    tables: tuple[str, ...],
    id_column: str,
) -> int:
    table_names = _table_names(con)
    maxima = []
    for table in tables:
        if table not in table_names:
            continue
        row = con.execute(
            f"SELECT MAX({id_column}) FROM {table}"
        ).fetchone()
        maxima.append(0 if row is None or row[0] is None else int(row[0]))
    return max(maxima, default=0) + 1


def _select_current_decision_row(
    con: sqlite3.Connection,
    decision_id: int,
) -> tuple[Any, ...] | None:
    return con.execute(
        f"""
        SELECT decision_id, schema_version, stage, task_base,
               instance_id, task_coordinate_id, identity_hash,
               params_hash, symbolic_inputs_hash,
               symbolic_outputs_hash, contract_hash, action,
               legacy_done, queued, force, worker_callable,
               reasons_json, instance_json, created_at
        FROM {REUSE_DECISION_TABLE_V3}
        WHERE decision_id=?
        """,
        (decision_id,),
    ).fetchone()


def _select_current_authority_audit_row(
    con: sqlite3.Connection,
    authority_audit_id: int,
) -> tuple[Any, ...] | None:
    return con.execute(
        f"""
        SELECT authority_audit_id, storage_schema_version,
               schema_version, event_type, invocation_id,
               policy_name, policy_version, policy_fingerprint,
               evidence_snapshot_hash, source_decision_id,
               decision_id, stage, task_base, instance_id,
               legacy_action, candidate_action, comparison,
               health_status, blockers_json,
               operator_warnings_json, rollback_reason, created_at
        FROM {REUSE_AUTHORITY_AUDIT_TABLE_V2}
        WHERE authority_audit_id=?
        """,
        (authority_audit_id,),
    ).fetchone()


def _instance_fields(
    value: Any,
    *,
    label: str,
    decision_id: int,
) -> tuple[TaskInstanceV3Field, ...]:
    if not isinstance(value, list):
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} {label} is malformed"
        )
    fields: list[TaskInstanceV3Field] = []
    keys: list[str] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"key", "value", "legacy_value"}
            or not isinstance(item.get("key"), str)
            or not item["key"]
            or not isinstance(item.get("legacy_value"), str)
        ):
            raise ReuseDecisionStorageError(
                f"Reuse-decision v3 row {decision_id} {label} field is malformed"
            )
        keys.append(item["key"])
        fields.append(
            TaskInstanceV3Field(
                key=item["key"],
                value=item.get("value"),
                legacy_value=item["legacy_value"],
            )
        )
    if len(keys) != len(set(keys)):
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} {label} keys are duplicated"
        )
    return tuple(fields)


def _string_tuple(
    value: Any,
    *,
    label: str,
    decision_id: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} {label} is malformed"
        )
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ReuseDecisionStorageError(
            f"Reuse-decision v3 row {decision_id} {label} is duplicated"
        )
    return result


def _compatibility_coordinate_fields(
    value: Any,
) -> tuple[tuple[str, Any], ...]:
    if not isinstance(value, (list, tuple, Mapping)):
        return ()
    return tuple(
        (str(item.get("key", "")), item.get("value"))
        for item in value
        if isinstance(item, Mapping)
    )


def _compatibility_coordinate_status(
    *,
    stage: str,
    task_base: str,
    identity: Iterable[tuple[str, Any]],
    params: Iterable[tuple[str, Any]],
    compatibility_alias: str,
) -> tuple[str, CurrentStoredDecisionCoordinateStatus]:
    try:
        coordinate = build_task_coordinate_v1(
            stage=stage,
            task_base=task_base,
            identity=identity,
            params=params,
        )
    except (TaskCoordinateError, TypeError, ValueError):
        return "", "malformed"
    status: CurrentStoredDecisionCoordinateStatus = (
        "available"
        if coordinate.legacy_variant_str == compatibility_alias
        else "compatibility_mismatch"
    )
    return coordinate.task_coordinate_id, status


def _without_compatibility_alias_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_compatibility_alias_keys(child)
            for key, child in value.items()
            if str(key) not in _FORBIDDEN_INSTANCE_KEYS
        }
    if isinstance(value, list):
        return [
            _without_compatibility_alias_keys(child) for child in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _without_compatibility_alias_keys(child) for child in value
        )
    return value


def _reject_alias_keys(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            text_key = str(key)
            if text_key in _FORBIDDEN_INSTANCE_KEYS:
                raise ReuseDecisionStorageError(
                    f"Reuse-decision v3 instance contains forbidden key {path}.{text_key}"
                )
            _reject_alias_keys(child, path=f"{path}.{text_key}")
    elif isinstance(value, (tuple, list)):
        for index, child in enumerate(value):
            _reject_alias_keys(child, path=f"{path}[{index}]")


def _validate_global_ids(
    rows: Iterable[Any],
    *,
    id_attr: str,
    archived_version: str,
    current_version: str,
    label: str,
) -> None:
    values = tuple(rows)
    _validate_unique_positive_ids(values, id_attr=id_attr, label=label)
    archived_ids = [
        int(getattr(row, id_attr))
        for row in values
        if row.storage_version == archived_version
    ]
    current_ids = [
        int(getattr(row, id_attr))
        for row in values
        if row.storage_version == current_version
    ]
    if archived_ids and current_ids and min(current_ids) <= max(archived_ids):
        raise ReuseDecisionStorageError(
            f"Current {label} IDs do not continue after the frozen archive"
        )


def _validate_unique_positive_ids(
    rows: Iterable[Any],
    *,
    id_attr: str,
    label: str,
) -> None:
    ids = [int(getattr(row, id_attr)) for row in rows]
    if any(value <= 0 for value in ids) or len(ids) != len(set(ids)):
        raise ReuseDecisionStorageError(
            f"{label.title()} IDs are not positive and globally unique"
        )


def _ordered_rows(
    rows: Iterable[Any],
    *,
    id_attr: str,
    latest_first: bool,
    limit: int | None,
) -> tuple[Any, ...]:
    ordered = tuple(
        sorted(
            rows,
            key=lambda row: int(getattr(row, id_attr)),
            reverse=latest_first,
        )
    )
    if limit is None:
        return ordered
    return ordered[: max(0, int(limit))]


def _table_names(con: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _policy_json(policy: ReuseDecisionStoragePolicyV1) -> str:
    return _canonical_json(policy.as_dict())


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("ascii")).hexdigest()


def _load_json(value: Any, *, label: str) -> Any:
    try:
        return json.loads(
            str(value),
            object_pairs_hook=_strict_json_object,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReuseDecisionStorageError(f"Malformed {label} JSON") from exc


def _load_json_compatibility(value: Any, *, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _strict_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _fingerprint_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"type": type(value).__name__, "text": str(value)}


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise ReuseDecisionStorageError(f"{label} is not a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ReuseDecisionStorageError(
            f"{label} is not a positive integer"
        ) from exc
    if result <= 0:
        raise ReuseDecisionStorageError(f"{label} is not a positive integer")
    return result


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise ReuseDecisionStorageError(f"{label} is not nonnegative")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ReuseDecisionStorageError(f"{label} is not nonnegative") from exc
    if result < 0:
        raise ReuseDecisionStorageError(f"{label} is not nonnegative")
    return result


def _db_bool(value: Any, *, label: str) -> bool:
    if value not in (0, 1):
        raise ReuseDecisionStorageError(f"{label} must be 0 or 1")
    return bool(value)


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
