from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .task_coordinate import (
    TaskCoordinateError,
    TaskCoordinateV1,
    build_task_coordinate_v1,
    decode_task_coordinate_json_v1,
)


TASK_PARAMETER_LEDGER_TABLE_V1 = "task_param_ledger"
TASK_PARAMETER_LEDGER_TABLE_V2 = "task_param_ledger_v2"
TASK_PARAMETER_LEDGER_STORAGE_STATE_TABLE_V1 = (
    "task_parameter_ledger_storage_state_v1"
)
TASK_PARAMETER_LEDGER_STORAGE_POLICY_SCHEMA_V1 = (
    "scpolya.task_parameter_ledger_storage_policy.v1"
)
TASK_PARAMETER_LEDGER_STORAGE_POLICY_NAME = (
    "task_parameter_ledger_storage_policy"
)
TASK_PARAMETER_LEDGER_STORAGE_POLICY_VERSION_V1 = "1"
TASK_PARAMETER_LEDGER_STORAGE_POLICY_VERSION_V2 = "2"
CURRENT_TASK_PARAMETER_LEDGER_RECORD_SCHEMA_V3 = (
    "scpolya.current_task_parameter_ledger.record.v3"
)
CURRENT_TASK_PARAMETER_LEDGER_SNAPSHOT_SCHEMA_V2 = (
    "scpolya.current_task_parameter_ledger.snapshot.v2"
)


class TaskParameterLedgerError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskParameterLedgerStoragePolicyV1:
    schema_version: str
    policy_name: str
    policy_version: str
    runtime_read_table: str
    runtime_write_tables: tuple[str, ...]
    historical_read_api: str
    retain_legacy_variant_alias: bool
    supersedes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "runtime_read_table": self.runtime_read_table,
            "runtime_write_tables": list(self.runtime_write_tables),
            "historical_read_api": self.historical_read_api,
            "retain_legacy_variant_alias": (
                self.retain_legacy_variant_alias
            ),
            "supersedes": self.supersedes,
        }


@dataclass(frozen=True)
class TaskParameterLedgerStorageStateV1:
    policy: TaskParameterLedgerStoragePolicyV1
    policy_fingerprint: str
    initialized_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskParameterLedgerRecordV2:
    task_coordinate: TaskCoordinateV1
    done_known: bool
    queued: bool
    force: bool
    updated_at: str

    @property
    def task_coordinate_id(self) -> str:
        return self.task_coordinate.task_coordinate_id

    @property
    def stage(self) -> str:
        return self.task_coordinate.stage

    @property
    def task_base(self) -> str:
        return self.task_coordinate.task_base

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_coordinate": self.task_coordinate.as_dict(),
            "done_known": self.done_known,
            "queued": self.queued,
            "force": self.force,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class CurrentTaskParameterLedgerRecordV3:
    task_coordinate: TaskCoordinateV1
    done_known: bool
    queued: bool
    force: bool
    updated_at: str
    schema_version: str = CURRENT_TASK_PARAMETER_LEDGER_RECORD_SCHEMA_V3

    def __post_init__(self) -> None:
        if self.schema_version != CURRENT_TASK_PARAMETER_LEDGER_RECORD_SCHEMA_V3:
            raise ValueError(
                "Current task parameter ledger record schema mismatch: "
                f"{self.schema_version!r}"
            )
        if not isinstance(self.task_coordinate, TaskCoordinateV1):
            raise TypeError(
                "Current task parameter ledger records require TaskCoordinateV1"
            )
        _validate_status(
            self.done_known,
            self.queued,
            self.force,
            updated_at=self.updated_at,
        )

    @property
    def task_coordinate_id(self) -> str:
        return self.task_coordinate.task_coordinate_id

    @property
    def stage(self) -> str:
        return self.task_coordinate.stage

    @property
    def task_base(self) -> str:
        return self.task_coordinate.task_base

    def as_dict(self) -> dict[str, Any]:
        coordinate = self.task_coordinate
        return {
            "schema_version": self.schema_version,
            "task_coordinate": {
                **coordinate.payload,
                "task_coordinate_id": coordinate.task_coordinate_id,
                "identity_hash": coordinate.identity_hash,
                "params_hash": coordinate.params_hash,
            },
            "done_known": self.done_known,
            "queued": self.queued,
            "force": self.force,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TaskParameterLedgerSnapshotV1:
    state_db_path: str
    storage_table: str
    records: tuple[TaskParameterLedgerRecordV2, ...]
    available: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "state_db_path": self.state_db_path,
            "storage_table": self.storage_table,
            "available": self.available,
            "record_count": len(self.records),
            "records": [record.as_dict() for record in self.records],
        }


@dataclass(frozen=True)
class CurrentTaskParameterLedgerSnapshotV2:
    state_db_path: str
    storage_table: str
    records: tuple[CurrentTaskParameterLedgerRecordV3, ...]
    available: bool = True
    schema_version: str = CURRENT_TASK_PARAMETER_LEDGER_SNAPSHOT_SCHEMA_V2

    def __post_init__(self) -> None:
        if self.schema_version != CURRENT_TASK_PARAMETER_LEDGER_SNAPSHOT_SCHEMA_V2:
            raise ValueError(
                "Current task parameter ledger snapshot schema mismatch: "
                f"{self.schema_version!r}"
            )
        if self.storage_table != TASK_PARAMETER_LEDGER_TABLE_V2:
            raise ValueError(
                "Current task parameter ledger snapshots require v2 storage"
            )
        if any(
            not isinstance(record, CurrentTaskParameterLedgerRecordV3)
            for record in self.records
        ):
            raise TypeError(
                "Current task parameter ledger snapshots require v3 records"
            )
        if not self.available and self.records:
            raise ValueError(
                "Unavailable current task parameter ledger cannot contain records"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state_db_path": self.state_db_path,
            "storage_table": self.storage_table,
            "available": self.available,
            "record_count": len(self.records),
            "records": [record.as_dict() for record in self.records],
        }


TASK_PARAMETER_LEDGER_STORAGE_POLICY_V1 = (
    TaskParameterLedgerStoragePolicyV1(
        schema_version=TASK_PARAMETER_LEDGER_STORAGE_POLICY_SCHEMA_V1,
        policy_name=TASK_PARAMETER_LEDGER_STORAGE_POLICY_NAME,
        policy_version=TASK_PARAMETER_LEDGER_STORAGE_POLICY_VERSION_V1,
        runtime_read_table=TASK_PARAMETER_LEDGER_TABLE_V1,
        runtime_write_tables=(TASK_PARAMETER_LEDGER_TABLE_V1,),
        historical_read_api="implicit_compatibility",
        retain_legacy_variant_alias=True,
    )
)

TASK_PARAMETER_LEDGER_STORAGE_POLICY_V2 = (
    TaskParameterLedgerStoragePolicyV1(
        schema_version=TASK_PARAMETER_LEDGER_STORAGE_POLICY_SCHEMA_V1,
        policy_name=TASK_PARAMETER_LEDGER_STORAGE_POLICY_NAME,
        policy_version=TASK_PARAMETER_LEDGER_STORAGE_POLICY_VERSION_V2,
        runtime_read_table=TASK_PARAMETER_LEDGER_TABLE_V2,
        runtime_write_tables=(TASK_PARAMETER_LEDGER_TABLE_V2,),
        historical_read_api=(
            "load_archived_task_parameter_ledger_snapshot_v1"
        ),
        retain_legacy_variant_alias=False,
        supersedes=(
            f"{TASK_PARAMETER_LEDGER_STORAGE_POLICY_NAME}:"
            f"{TASK_PARAMETER_LEDGER_STORAGE_POLICY_VERSION_V1}"
        ),
    )
)


def task_parameter_ledger_storage_policy_fingerprint(
    policy: TaskParameterLedgerStoragePolicyV1,
) -> str:
    return hashlib.sha256(_policy_json(policy).encode("ascii")).hexdigest()


def require_current_task_parameter_ledger_state_v1(
    state_or_db_path: str | Path,
) -> None:
    """Validate a fresh, upgradeable, or exact current ledger read-only."""

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
            has_v1 = TASK_PARAMETER_LEDGER_TABLE_V1 in tables
            has_v2 = TASK_PARAMETER_LEDGER_TABLE_V2 in tables
            has_state = (
                TASK_PARAMETER_LEDGER_STORAGE_STATE_TABLE_V1 in tables
            )
            if has_state:
                load_task_parameter_ledger_storage_state_v1(con)
                if not has_v2:
                    raise TaskParameterLedgerError(
                        "Task parameter ledger policy v2 lacks its current table"
                    )
                load_current_task_parameter_ledger_records_v3(con)
                return
            if has_v2:
                raise TaskParameterLedgerError(
                    "Task parameter ledger v2 exists without its storage policy"
                )
            if has_v1:
                _load_archived_task_parameter_ledger_records_v1(con)
    except TaskParameterLedgerError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskParameterLedgerError(
            "Could not validate task parameter ledger before initialization: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def init_task_parameter_ledger_schema_v2(
    con: sqlite3.Connection,
) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TASK_PARAMETER_LEDGER_TABLE_V2}
        (
            task_coordinate_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            stage TEXT NOT NULL,
            task_base TEXT NOT NULL,
            identity_hash TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            coordinate_json TEXT NOT NULL,
            done_known INTEGER NOT NULL CHECK(done_known IN (0, 1)),
            queued INTEGER NOT NULL CHECK(queued IN (0, 1)),
            force INTEGER NOT NULL CHECK(force IN (0, 1)),
            updated_at TEXT NOT NULL,
            UNIQUE(stage, task_base, identity_hash, params_hash)
        )
        """
    )
    con.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_task_param_ledger_v2_lookup
            ON {TASK_PARAMETER_LEDGER_TABLE_V2}(
                stage, task_base, task_coordinate_id
            )
        """
    )


def init_task_parameter_ledger_storage_policy_v2(
    con: sqlite3.Connection,
    *,
    now: str,
) -> TaskParameterLedgerStorageStateV1:
    if not str(now):
        raise TaskParameterLedgerError(
            "Task parameter ledger policy timestamp must be non-empty"
        )
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS
            {TASK_PARAMETER_LEDGER_STORAGE_STATE_TABLE_V1}
        (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            schema_version TEXT NOT NULL,
            policy_name TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_fingerprint TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            initialized_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    existing = con.execute(
        f"""
        SELECT 1
        FROM {TASK_PARAMETER_LEDGER_STORAGE_STATE_TABLE_V1}
        WHERE singleton_id=1
        """
    ).fetchone()
    if existing is None:
        policy = TASK_PARAMETER_LEDGER_STORAGE_POLICY_V2
        fingerprint = task_parameter_ledger_storage_policy_fingerprint(policy)
        con.execute(
            f"""
            INSERT INTO {TASK_PARAMETER_LEDGER_STORAGE_STATE_TABLE_V1}(
                singleton_id, schema_version, policy_name, policy_version,
                policy_fingerprint, policy_json, initialized_at, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy.schema_version,
                policy.policy_name,
                policy.policy_version,
                fingerprint,
                _policy_json(policy),
                now,
                now,
            ),
        )
    return load_task_parameter_ledger_storage_state_v1(con)


def load_task_parameter_ledger_storage_state_v1(
    con: sqlite3.Connection,
) -> TaskParameterLedgerStorageStateV1:
    try:
        row = con.execute(
            f"""
            SELECT schema_version, policy_name, policy_version,
                   policy_fingerprint, policy_json,
                   initialized_at, updated_at
            FROM {TASK_PARAMETER_LEDGER_STORAGE_STATE_TABLE_V1}
            WHERE singleton_id=1
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise TaskParameterLedgerError(
            "Task parameter ledger storage policy state is malformed"
        ) from exc
    if row is None:
        raise TaskParameterLedgerError(
            "Task parameter ledger storage policy has no singleton row"
        )
    policy = TASK_PARAMETER_LEDGER_STORAGE_POLICY_V2
    fingerprint = task_parameter_ledger_storage_policy_fingerprint(policy)
    expected = (
        policy.schema_version,
        policy.policy_name,
        policy.policy_version,
        fingerprint,
        _policy_json(policy),
    )
    actual = tuple(str(value or "") for value in row[:5])
    if actual != expected:
        raise TaskParameterLedgerError(
            "Task parameter ledger storage policy differs from approved v2"
        )
    initialized_at = str(row[5] or "")
    updated_at = str(row[6] or "")
    if not initialized_at or not updated_at:
        raise TaskParameterLedgerError(
            "Task parameter ledger storage policy timestamps are missing"
        )
    return TaskParameterLedgerStorageStateV1(
        policy=policy,
        policy_fingerprint=fingerprint,
        initialized_at=initialized_at,
        updated_at=updated_at,
    )


def migrate_task_parameter_ledger_v1_to_v2(
    con: sqlite3.Connection,
) -> int:
    """Copy one validated alias-bearing ledger into current v2 storage."""

    tables = _table_names(con)
    if TASK_PARAMETER_LEDGER_TABLE_V1 not in tables:
        raise TaskParameterLedgerError(
            "Task parameter ledger v2 upgrade requires the archived v1 table"
        )
    if TASK_PARAMETER_LEDGER_TABLE_V2 not in tables:
        raise TaskParameterLedgerError(
            "Task parameter ledger v2 upgrade target is missing"
        )
    records = _load_archived_task_parameter_ledger_records_v1(con)
    inserted = 0
    source_ids = {record.task_coordinate_id for record in records}
    for record in records:
        existing = _select_task_parameter_ledger_v2_row(
            con,
            record.task_coordinate_id,
        )
        expected = _record_storage_tuple(record)
        if existing is not None:
            if _normalize_v2_storage_row(existing) != expected:
                raise TaskParameterLedgerError(
                    "Existing task parameter ledger v2 row differs from v1: "
                    f"{record.task_coordinate_id}"
                )
            continue
        con.execute(
            f"""
            INSERT INTO {TASK_PARAMETER_LEDGER_TABLE_V2}(
                task_coordinate_id, schema_version, stage, task_base,
                identity_hash, params_hash, coordinate_json,
                done_known, queued, force, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            expected,
        )
        inserted += 1
    target_ids = {
        str(row[0])
        for row in con.execute(
            f"SELECT task_coordinate_id FROM {TASK_PARAMETER_LEDGER_TABLE_V2}"
        ).fetchall()
    }
    if target_ids != source_ids:
        raise TaskParameterLedgerError(
            "Task parameter ledger v2 row set differs from archived v1: "
            f"v1={len(source_ids)}, v2={len(target_ids)}"
        )
    return inserted


def write_task_parameter_ledger_v2(
    con: sqlite3.Connection,
    task_coordinate: TaskCoordinateV1,
    *,
    done_known: bool,
    queued: bool,
    force: bool,
    updated_at: str,
) -> None:
    if not isinstance(task_coordinate, TaskCoordinateV1):
        raise TaskParameterLedgerError(
            "Task parameter ledger writes require a structured coordinate"
        )
    _validate_status(done_known, queued, force, updated_at=updated_at)
    _validate_current_schema_v2(con)
    existing = _select_task_parameter_ledger_v2_row(
        con,
        task_coordinate.task_coordinate_id,
    )
    expected_coordinate = _coordinate_storage_tuple(task_coordinate)
    if existing is not None:
        actual_coordinate = tuple(str(value or "") for value in existing[:7])
        if actual_coordinate != expected_coordinate:
            raise TaskParameterLedgerError(
                "Task parameter ledger coordinate ID collision or payload "
                f"mismatch: {task_coordinate.task_coordinate_id}"
            )
    component_match = con.execute(
        f"""
        SELECT task_coordinate_id
        FROM {TASK_PARAMETER_LEDGER_TABLE_V2}
        WHERE stage=? AND task_base=?
          AND identity_hash=? AND params_hash=?
        """,
        (
            task_coordinate.stage,
            task_coordinate.task_base,
            task_coordinate.identity_hash,
            task_coordinate.params_hash,
        ),
    ).fetchone()
    if (
        component_match is not None
        and str(component_match[0]) != task_coordinate.task_coordinate_id
    ):
        raise TaskParameterLedgerError(
            "Task parameter ledger components map to a different coordinate"
        )
    con.execute(
        f"""
        INSERT INTO {TASK_PARAMETER_LEDGER_TABLE_V2}(
            task_coordinate_id, schema_version, stage, task_base,
            identity_hash, params_hash, coordinate_json,
            done_known, queued, force, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_coordinate_id) DO UPDATE SET
            done_known=excluded.done_known,
            queued=excluded.queued,
            force=excluded.force,
            updated_at=excluded.updated_at
        """,
        (
            *expected_coordinate,
            int(done_known),
            int(queued),
            int(force),
            str(updated_at),
        ),
    )


def load_current_task_parameter_ledger_records_v3(
    con: sqlite3.Connection,
) -> tuple[CurrentTaskParameterLedgerRecordV3, ...]:
    load_task_parameter_ledger_storage_state_v1(con)
    _validate_current_schema_v2(con)
    rows = con.execute(
        f"""
        SELECT task_coordinate_id, schema_version, stage, task_base,
               identity_hash, params_hash, coordinate_json,
               done_known, queued, force, updated_at
        FROM {TASK_PARAMETER_LEDGER_TABLE_V2}
        ORDER BY task_coordinate_id
        """
    ).fetchall()
    records: list[CurrentTaskParameterLedgerRecordV3] = []
    seen: set[str] = set()
    for row in rows:
        coordinate_id = str(row[0] or "")
        coordinate = _decode_coordinate_storage_row(row[:7], label="current")
        if coordinate_id in seen:
            raise TaskParameterLedgerError(
                "Task parameter ledger v2 contains a duplicate coordinate: "
                f"{coordinate_id}"
            )
        seen.add(coordinate_id)
        done_known, queued, force = _status_values(row[7], row[8], row[9])
        updated_at = str(row[10] or "")
        _validate_status(
            done_known,
            queued,
            force,
            updated_at=updated_at,
        )
        records.append(
            CurrentTaskParameterLedgerRecordV3(
                task_coordinate=coordinate,
                done_known=done_known,
                queued=queued,
                force=force,
                updated_at=updated_at,
            )
        )
    return tuple(records)


def load_current_task_parameter_ledger_snapshot_v2(
    state_or_db_path: str | Path,
) -> CurrentTaskParameterLedgerSnapshotV2:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return CurrentTaskParameterLedgerSnapshotV2(
            state_db_path=str(db_path),
            storage_table=TASK_PARAMETER_LEDGER_TABLE_V2,
            records=(),
            available=False,
        )
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            records = load_current_task_parameter_ledger_records_v3(con)
    except TaskParameterLedgerError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskParameterLedgerError(
            "Could not read current task parameter ledger: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return CurrentTaskParameterLedgerSnapshotV2(
        state_db_path=str(db_path),
        storage_table=TASK_PARAMETER_LEDGER_TABLE_V2,
        records=records,
    )


def load_archived_task_parameter_ledger_snapshot_v1(
    state_or_db_path: str | Path,
) -> TaskParameterLedgerSnapshotV1:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return TaskParameterLedgerSnapshotV1(
            state_db_path=str(db_path),
            storage_table=TASK_PARAMETER_LEDGER_TABLE_V1,
            records=(),
            available=False,
        )
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            records = _load_archived_task_parameter_ledger_records_v1(con)
    except TaskParameterLedgerError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskParameterLedgerError(
            "Could not read archived task parameter ledger: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return TaskParameterLedgerSnapshotV1(
        state_db_path=str(db_path),
        storage_table=TASK_PARAMETER_LEDGER_TABLE_V1,
        records=records,
    )


def _load_archived_task_parameter_ledger_records_v1(
    con: sqlite3.Connection,
) -> tuple[TaskParameterLedgerRecordV2, ...]:
    tables = _table_names(con)
    if TASK_PARAMETER_LEDGER_TABLE_V1 not in tables:
        raise TaskParameterLedgerError(
            "Archived task parameter ledger requires task_param_ledger"
        )
    _validate_archived_schema_v1(con)
    rows = con.execute(
        f"""
        SELECT stage, task_base, variant_str, task_coordinate_id,
               identity_hash, identity_json, task_params_json,
               identity_keys_json, param_keys_json,
               done_known, queued, force, updated_at
        FROM {TASK_PARAMETER_LEDGER_TABLE_V1}
        ORDER BY stage, task_base, task_coordinate_id, identity_hash
        """
    ).fetchall()
    records: list[TaskParameterLedgerRecordV2] = []
    seen: set[str] = set()
    for row in rows:
        stage = str(row[0] or "")
        task_base = str(row[1] or "")
        try:
            identity_payload = json.loads(
                str(row[5] or ""),
                object_pairs_hook=_strict_json_object,
            )
            params_payload = json.loads(
                str(row[6] or ""),
                object_pairs_hook=_strict_json_object,
            )
            identity_keys = json.loads(
                str(row[7] or ""),
                object_pairs_hook=_strict_json_object,
            )
            param_keys = json.loads(
                str(row[8] or ""),
                object_pairs_hook=_strict_json_object,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TaskParameterLedgerError(
                "Archived task parameter ledger JSON is malformed: "
                f"{stage}:{task_base}"
            ) from exc
        if (
            not isinstance(identity_payload, list)
            or any(
                not isinstance(item, dict)
                or set(item) != {"key", "value"}
                or not isinstance(item["key"], str)
                for item in identity_payload
            )
            or not isinstance(params_payload, dict)
            or not all(isinstance(key, str) for key in params_payload)
            or not isinstance(identity_keys, list)
            or not all(isinstance(key, str) and key for key in identity_keys)
            or not isinstance(param_keys, list)
            or not all(isinstance(key, str) and key for key in param_keys)
        ):
            raise TaskParameterLedgerError(
                "Archived task parameter ledger payload shape is invalid: "
                f"{stage}:{task_base}"
            )
        try:
            coordinate = build_task_coordinate_v1(
                stage=stage,
                task_base=task_base,
                identity=(
                    (item["key"], item.get("value"))
                    for item in identity_payload
                ),
                params=params_payload,
            )
        except (TaskCoordinateError, TypeError, ValueError) as exc:
            raise TaskParameterLedgerError(
                "Archived task parameter ledger coordinate is invalid: "
                f"{stage}:{task_base}"
            ) from exc
        expected_old_identity_hash = hashlib.sha256(
            json.dumps(
                {"identity": identity_payload},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        if (
            str(row[2] or "") != coordinate.legacy_variant_str
            or str(row[3] or "") != coordinate.task_coordinate_id
            or str(row[4] or "") != expected_old_identity_hash
            or len(identity_keys) != len(set(identity_keys))
            or set(identity_keys) != set(coordinate.identity_dict)
            or len(param_keys) != len(set(param_keys))
            or set(param_keys) != set(coordinate.params_dict)
        ):
            raise TaskParameterLedgerError(
                "Archived task parameter ledger fields differ from its "
                f"coordinate: {stage}:{task_base}"
            )
        if coordinate.task_coordinate_id in seen:
            raise TaskParameterLedgerError(
                "Archived task parameter ledger contains duplicate coordinate: "
                f"{coordinate.task_coordinate_id}"
            )
        seen.add(coordinate.task_coordinate_id)
        done_known, queued, force = _status_values(row[9], row[10], row[11])
        updated_at = str(row[12] or "")
        _validate_status(
            done_known,
            queued,
            force,
            updated_at=updated_at,
        )
        records.append(
            TaskParameterLedgerRecordV2(
                task_coordinate=coordinate,
                done_known=done_known,
                queued=queued,
                force=force,
                updated_at=updated_at,
            )
        )
    return tuple(records)


def _validate_current_schema_v2(con: sqlite3.Connection) -> None:
    try:
        columns = tuple(
            (
                str(row[1]),
                str(row[2]).upper(),
                int(row[3]),
                int(row[5]),
            )
            for row in con.execute(
                f"PRAGMA table_info({TASK_PARAMETER_LEDGER_TABLE_V2})"
            ).fetchall()
        )
    except sqlite3.Error as exc:
        raise TaskParameterLedgerError(
            "Task parameter ledger v2 schema is malformed"
        ) from exc
    expected = (
        ("task_coordinate_id", "TEXT", 0, 1),
        ("schema_version", "TEXT", 1, 0),
        ("stage", "TEXT", 1, 0),
        ("task_base", "TEXT", 1, 0),
        ("identity_hash", "TEXT", 1, 0),
        ("params_hash", "TEXT", 1, 0),
        ("coordinate_json", "TEXT", 1, 0),
        ("done_known", "INTEGER", 1, 0),
        ("queued", "INTEGER", 1, 0),
        ("force", "INTEGER", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    )
    if columns != expected:
        raise TaskParameterLedgerError(
            "Task parameter ledger v2 columns differ from approved schema"
        )
    indexes = {
        str(row[1]): (bool(row[2]), str(row[3]))
        for row in con.execute(
            f"PRAGMA index_list({TASK_PARAMETER_LEDGER_TABLE_V2})"
        ).fetchall()
    }
    index_columns = {
        name: tuple(
            str(row[2])
            for row in con.execute(f'PRAGMA index_info("{name}")').fetchall()
        )
        for name in indexes
    }
    has_primary = any(
        unique and origin == "pk" and index_columns[name] == (
            "task_coordinate_id",
        )
        for name, (unique, origin) in indexes.items()
    )
    has_component_unique = any(
        unique and index_columns[name] == (
            "stage",
            "task_base",
            "identity_hash",
            "params_hash",
        )
        for name, (unique, _origin) in indexes.items()
    )
    if (
        indexes.get("idx_task_param_ledger_v2_lookup") != (False, "c")
        or index_columns.get("idx_task_param_ledger_v2_lookup")
        != ("stage", "task_base", "task_coordinate_id")
        or not has_primary
        or not has_component_unique
    ):
        raise TaskParameterLedgerError(
            "Task parameter ledger v2 indexes differ from approved schema"
        )


def _validate_archived_schema_v1(con: sqlite3.Connection) -> None:
    columns = tuple(
        str(row[1])
        for row in con.execute(
            f"PRAGMA table_info({TASK_PARAMETER_LEDGER_TABLE_V1})"
        ).fetchall()
    )
    expected_current_order = (
        "stage",
        "task_base",
        "variant_str",
        "task_coordinate_id",
        "identity_hash",
        "identity_json",
        "task_params_json",
        "identity_keys_json",
        "param_keys_json",
        "done_known",
        "queued",
        "force",
        "updated_at",
    )
    expected_historical_order = (
        "stage",
        "task_base",
        "variant_str",
        "identity_hash",
        "identity_json",
        "task_params_json",
        "identity_keys_json",
        "param_keys_json",
        "done_known",
        "queued",
        "force",
        "updated_at",
        "task_coordinate_id",
    )
    if columns not in {expected_current_order, expected_historical_order}:
        raise TaskParameterLedgerError(
            "Archived task parameter ledger columns are unsupported"
        )


def _decode_coordinate_storage_row(
    row: Any,
    *,
    label: str,
) -> TaskCoordinateV1:
    coordinate_id = str(row[0] or "")
    try:
        coordinate = decode_task_coordinate_json_v1(str(row[6] or ""))
    except (TaskCoordinateError, TypeError, ValueError) as exc:
        raise TaskParameterLedgerError(
            f"{label.title()} task parameter ledger coordinate is malformed: "
            f"{coordinate_id or '<empty>'}"
        ) from exc
    expected = _coordinate_storage_tuple(coordinate)
    actual = tuple(str(value or "") for value in row[:7])
    if actual != expected:
        raise TaskParameterLedgerError(
            f"{label.title()} task parameter ledger columns differ from "
            f"coordinate JSON: {coordinate_id or '<empty>'}"
        )
    return coordinate


def _select_task_parameter_ledger_v2_row(
    con: sqlite3.Connection,
    coordinate_id: str,
) -> tuple[Any, ...] | None:
    return con.execute(
        f"""
        SELECT task_coordinate_id, schema_version, stage, task_base,
               identity_hash, params_hash, coordinate_json,
               done_known, queued, force, updated_at
        FROM {TASK_PARAMETER_LEDGER_TABLE_V2}
        WHERE task_coordinate_id=?
        """,
        (coordinate_id,),
    ).fetchone()


def _coordinate_storage_tuple(
    coordinate: TaskCoordinateV1,
) -> tuple[str, ...]:
    return (
        coordinate.task_coordinate_id,
        coordinate.schema_version,
        coordinate.stage,
        coordinate.task_base,
        coordinate.identity_hash,
        coordinate.params_hash,
        coordinate.canonical_json,
    )


def _record_storage_tuple(
    record: TaskParameterLedgerRecordV2,
) -> tuple[Any, ...]:
    return (
        *_coordinate_storage_tuple(record.task_coordinate),
        int(record.done_known),
        int(record.queued),
        int(record.force),
        record.updated_at,
    )


def _normalize_v2_storage_row(row: Any) -> tuple[Any, ...]:
    return (
        *(str(value or "") for value in row[:7]),
        int(row[7]),
        int(row[8]),
        int(row[9]),
        str(row[10] or ""),
    )


def _status_values(
    done_known: Any,
    queued: Any,
    force: Any,
) -> tuple[bool, bool, bool]:
    raw = (done_known, queued, force)
    if any(value not in (0, 1) for value in raw):
        raise TaskParameterLedgerError(
            "Task parameter ledger status flags must be 0 or 1"
        )
    return bool(done_known), bool(queued), bool(force)


def _validate_status(
    done_known: bool,
    queued: bool,
    force: bool,
    *,
    updated_at: str,
) -> None:
    if not all(isinstance(value, bool) for value in (done_known, queued, force)):
        raise TaskParameterLedgerError(
            "Task parameter ledger status values must be boolean"
        )
    if not str(updated_at):
        raise TaskParameterLedgerError(
            "Task parameter ledger updated_at must be non-empty"
        )


def _policy_json(policy: TaskParameterLedgerStoragePolicyV1) -> str:
    return json.dumps(
        policy.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _table_names(con: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


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
