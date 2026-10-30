from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .define import TASK_PARAM_SCOPES
from .task_coordinate import (
    TaskCoordinateError,
    decode_legacy_variant_str_v1,
)
from .task_variants import encode_params, stringify_params


TASK_METADATA_COORDINATE_SCHEMA_V1 = (
    "scpolya.task_metadata_coordinate.v1"
)
TASK_METADATA_ROW_SCHEMA_V2 = "scpolya.task_metadata.storage.v2"
TASK_METADATA_TABLE_V1 = "task_meta"
TASK_METADATA_TABLE_V2 = "task_meta_v2"
TASK_METADATA_STORAGE_STATE_TABLE_V1 = (
    "task_metadata_storage_state_v1"
)
TASK_METADATA_STORAGE_POLICY_SCHEMA_V1 = (
    "scpolya.task_metadata_storage_policy.v1"
)
TASK_METADATA_STORAGE_POLICY_NAME = "task_metadata_storage_policy"
TASK_METADATA_STORAGE_POLICY_VERSION_V2 = "2"


class TaskMetadataError(RuntimeError):
    pass


class TaskMetadataCoordinateError(ValueError):
    pass


@dataclass(frozen=True)
class TaskMetadataParameterV1:
    key: str
    legacy_value: str

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "legacy_value": self.legacy_value}


@dataclass(frozen=True)
class TaskMetadataScopeV1:
    sample_id: str | None
    contig: str | None

    def __post_init__(self) -> None:
        if (
            (self.sample_id is not None and not isinstance(self.sample_id, str))
            or (self.contig is not None and not isinstance(self.contig, str))
            or self.sample_id == ""
            or self.contig == ""
        ):
            raise TaskMetadataCoordinateError(
                "Task metadata scope identifiers must be non-empty"
            )
        if self.contig is not None and self.sample_id is None:
            raise TaskMetadataCoordinateError(
                "Task metadata contig scope requires a sample_id"
            )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "sample_id": self.sample_id,
            "contig": self.contig,
        }


@dataclass(frozen=True)
class TaskMetadataCoordinateV1:
    stage: str
    task_base: str
    scope: TaskMetadataScopeV1
    params: tuple[TaskMetadataParameterV1, ...]
    schema_version: str = TASK_METADATA_COORDINATE_SCHEMA_V1

    def __post_init__(self) -> None:
        if self.schema_version != TASK_METADATA_COORDINATE_SCHEMA_V1:
            raise TaskMetadataCoordinateError(
                "Unsupported task metadata coordinate schema: "
                f"{self.schema_version!r}"
            )
        if (
            not isinstance(self.stage, str)
            or not isinstance(self.task_base, str)
            or not self.stage
            or not self.task_base
        ):
            raise TaskMetadataCoordinateError(
                "Task metadata coordinate requires stage and task_base"
            )
        if not isinstance(self.scope, TaskMetadataScopeV1):
            raise TaskMetadataCoordinateError(
                "Task metadata coordinate scope is malformed"
            )
        if not isinstance(self.params, tuple) or any(
            not isinstance(field, TaskMetadataParameterV1)
            for field in self.params
        ):
            raise TaskMetadataCoordinateError(
                "Task metadata coordinate parameters are malformed"
            )
        keys = tuple(field.key for field in self.params)
        if (
            any(not isinstance(key, str) or not key for key in keys)
            or len(keys) != len(set(keys))
            or keys != tuple(sorted(keys))
            or any(not isinstance(field.legacy_value, str) for field in self.params)
        ):
            raise TaskMetadataCoordinateError(
                "Task metadata coordinate parameters are not canonical"
            )

    @property
    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "task_base": self.task_base,
            "scope": self.scope.as_dict(),
            "params": [field.as_dict() for field in self.params],
        }

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.payload)

    @property
    def metadata_coordinate_id(self) -> str:
        return hashlib.sha256(
            self.canonical_json.encode("ascii")
        ).hexdigest()

    @property
    def params_dict(self) -> dict[str, str]:
        return {
            field.key: field.legacy_value
            for field in self.params
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.payload,
            "metadata_coordinate_id": self.metadata_coordinate_id,
        }


@dataclass(frozen=True)
class TaskMetadataStoragePolicyV1:
    schema_version: str
    policy_name: str
    policy_version: str
    runtime_read_table: str
    runtime_write_tables: tuple[str, ...]
    archived_read_api: str
    coordinate_schema: str
    persist_legacy_variant_alias: bool
    nullable_sql_identity: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "runtime_read_table": self.runtime_read_table,
            "runtime_write_tables": list(self.runtime_write_tables),
            "archived_read_api": self.archived_read_api,
            "coordinate_schema": self.coordinate_schema,
            "persist_legacy_variant_alias": (
                self.persist_legacy_variant_alias
            ),
            "nullable_sql_identity": self.nullable_sql_identity,
        }


@dataclass(frozen=True)
class TaskMetadataStorageStateV1:
    policy: TaskMetadataStoragePolicyV1
    policy_fingerprint: str
    archived_table_present: bool
    archived_row_count: int
    archived_fingerprint: str
    migrated_logical_row_count: int
    collapsed_duplicate_row_count: int
    initialized_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskMetadataRecordV2:
    coordinate: TaskMetadataCoordinateV1
    key: str
    value: Any
    value_json: str
    updated_at: str

    @property
    def metadata_coordinate_id(self) -> str:
        return self.coordinate.metadata_coordinate_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "coordinate": self.coordinate.as_dict(),
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ArchivedTaskMetadataRecordV1:
    rowid: int
    coordinate: TaskMetadataCoordinateV1
    legacy_variant_str: str
    key: str
    value: Any
    value_json: str
    updated_at: str

    @property
    def metadata_coordinate_id(self) -> str:
        return self.coordinate.metadata_coordinate_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "rowid": self.rowid,
            "coordinate": self.coordinate.as_dict(),
            "legacy_variant_str": self.legacy_variant_str,
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TaskMetadataSnapshotV1:
    state_db_path: str
    storage_table: str
    records: tuple[TaskMetadataRecordV2, ...]
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
class ArchivedTaskMetadataSnapshotV1:
    state_db_path: str
    storage_table: str
    records: tuple[ArchivedTaskMetadataRecordV1, ...]
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
class _ArchiveSnapshot:
    present: bool
    row_count: int
    fingerprint: str


TASK_METADATA_STORAGE_POLICY_V2 = TaskMetadataStoragePolicyV1(
    schema_version=TASK_METADATA_STORAGE_POLICY_SCHEMA_V1,
    policy_name=TASK_METADATA_STORAGE_POLICY_NAME,
    policy_version=TASK_METADATA_STORAGE_POLICY_VERSION_V2,
    runtime_read_table=TASK_METADATA_TABLE_V2,
    runtime_write_tables=(TASK_METADATA_TABLE_V2,),
    archived_read_api="load_archived_task_metadata_snapshot_v1",
    coordinate_schema=TASK_METADATA_COORDINATE_SCHEMA_V1,
    persist_legacy_variant_alias=False,
    nullable_sql_identity=False,
)


def build_task_metadata_coordinate_v1(
    *,
    stage: str,
    task_base: str,
    sample_id: str | None = None,
    contig: str | None = None,
    context_params: Mapping[str, Any] | None = None,
    task_params: Mapping[str, Any] | None = None,
) -> TaskMetadataCoordinateV1:
    merged: dict[str, str] = {}
    merged.update(stringify_params(context_params))
    merged.update(stringify_params(task_params))
    scope = TASK_PARAM_SCOPES.get(str(task_base))
    if scope is not None:
        merged = {key: value for key, value in merged.items() if key in scope}
    return _build_task_metadata_coordinate_from_params_v1(
        stage=stage,
        task_base=task_base,
        sample_id=sample_id,
        contig=contig,
        params=merged,
    )


def decode_task_metadata_coordinate_json_v1(
    coordinate_json: str,
) -> TaskMetadataCoordinateV1:
    payload = _load_json(
        coordinate_json,
        label="task metadata coordinate",
        error_type=TaskMetadataCoordinateError,
    )
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "stage",
        "task_base",
        "scope",
        "params",
    }:
        raise TaskMetadataCoordinateError(
            "Task metadata coordinate JSON fields differ from schema v1"
        )
    scope = payload["scope"]
    params = payload["params"]
    if (
        payload["schema_version"] != TASK_METADATA_COORDINATE_SCHEMA_V1
        or not isinstance(payload["stage"], str)
        or not isinstance(payload["task_base"], str)
        or not isinstance(scope, dict)
        or set(scope) != {"sample_id", "contig"}
        or (
            scope["sample_id"] is not None
            and not isinstance(scope["sample_id"], str)
        )
        or (
            scope["contig"] is not None
            and not isinstance(scope["contig"], str)
        )
        or not isinstance(params, list)
    ):
        raise TaskMetadataCoordinateError(
            "Task metadata coordinate JSON is malformed"
        )
    fields: list[TaskMetadataParameterV1] = []
    for item in params:
        if (
            not isinstance(item, dict)
            or set(item) != {"key", "legacy_value"}
            or not isinstance(item["key"], str)
            or not isinstance(item["legacy_value"], str)
        ):
            raise TaskMetadataCoordinateError(
                "Task metadata coordinate parameter is malformed"
            )
        fields.append(
            TaskMetadataParameterV1(
                key=item["key"],
                legacy_value=item["legacy_value"],
            )
        )
    coordinate = TaskMetadataCoordinateV1(
        stage=payload["stage"],
        task_base=payload["task_base"],
        scope=TaskMetadataScopeV1(
            sample_id=scope["sample_id"],
            contig=scope["contig"],
        ),
        params=tuple(fields),
    )
    if coordinate.canonical_json != coordinate_json:
        raise TaskMetadataCoordinateError(
            "Task metadata coordinate JSON is not canonical"
        )
    return coordinate


def derive_legacy_variant_str_from_task_metadata_coordinate_v1(
    coordinate: TaskMetadataCoordinateV1,
) -> str:
    if not isinstance(coordinate, TaskMetadataCoordinateV1):
        raise TaskMetadataCoordinateError(
            "Legacy task metadata alias derivation requires a coordinate"
        )
    return encode_params(coordinate.params_dict)


def task_metadata_storage_policy_fingerprint(
    policy: TaskMetadataStoragePolicyV1,
) -> str:
    return hashlib.sha256(_policy_json(policy).encode("ascii")).hexdigest()


def require_current_task_metadata_storage_state_v1(
    state_or_db_path: str | Path,
) -> None:
    """Validate fresh, upgradeable, or exact current metadata read-only."""

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
            has_v1 = TASK_METADATA_TABLE_V1 in tables
            has_v2 = TASK_METADATA_TABLE_V2 in tables
            has_state = TASK_METADATA_STORAGE_STATE_TABLE_V1 in tables
            if has_state:
                load_task_metadata_storage_state_v1(con)
                load_task_metadata_records_v2(con)
                return
            if has_v2:
                raise TaskMetadataError(
                    "Task metadata v2 exists without its storage policy"
                )
            if has_v1:
                archived = _load_archived_task_metadata_records_v1(con)
                _select_migration_records(archived)
                _archive_snapshot(con)
    except TaskMetadataError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskMetadataError(
            "Could not validate task metadata before initialization: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def init_task_metadata_storage_v2(
    con: sqlite3.Connection,
    *,
    now: str,
) -> TaskMetadataStorageStateV1:
    if not con.in_transaction:
        raise TaskMetadataError(
            "Task metadata initialization requires an active transaction"
        )
    if not str(now):
        raise TaskMetadataError(
            "Task metadata policy timestamp must be non-empty"
        )
    tables = _table_names(con)
    if TASK_METADATA_STORAGE_STATE_TABLE_V1 in tables:
        return load_task_metadata_storage_state_v1(con)
    if TASK_METADATA_TABLE_V2 in tables:
        raise TaskMetadataError(
            "Task metadata v2 exists without its storage policy"
        )

    archived = (
        _load_archived_task_metadata_records_v1(con)
        if TASK_METADATA_TABLE_V1 in tables
        else ()
    )
    selected, collapsed_count = _select_migration_records(archived)
    archive = _archive_snapshot(con)
    _init_current_table_v2(con)
    _init_storage_state_table_v1(con)
    for record in selected:
        _insert_task_metadata_record_v2(con, record)

    policy = TASK_METADATA_STORAGE_POLICY_V2
    fingerprint = task_metadata_storage_policy_fingerprint(policy)
    con.execute(
        f"""
        INSERT INTO {TASK_METADATA_STORAGE_STATE_TABLE_V1}(
            singleton_id, schema_version, policy_name, policy_version,
            policy_fingerprint, policy_json, archived_table_present,
            archived_row_count, archived_fingerprint,
            migrated_logical_row_count, collapsed_duplicate_row_count,
            initialized_at, updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy.schema_version,
            policy.policy_name,
            policy.policy_version,
            fingerprint,
            _policy_json(policy),
            int(archive.present),
            archive.row_count,
            archive.fingerprint,
            len(selected),
            collapsed_count,
            str(now),
            str(now),
        ),
    )
    state = load_task_metadata_storage_state_v1(con)
    records = load_task_metadata_records_v2(con)
    if len(records) != len(selected):
        raise TaskMetadataError(
            "Task metadata migration row count differs from validated v1"
        )
    return state


def load_task_metadata_storage_state_v1(
    con: sqlite3.Connection,
) -> TaskMetadataStorageStateV1:
    _validate_storage_state_schema_v1(con)
    try:
        row = con.execute(
            f"""
            SELECT schema_version, policy_name, policy_version,
                   policy_fingerprint, policy_json,
                   archived_table_present, archived_row_count,
                   archived_fingerprint, migrated_logical_row_count,
                   collapsed_duplicate_row_count, initialized_at, updated_at
            FROM {TASK_METADATA_STORAGE_STATE_TABLE_V1}
            WHERE singleton_id=1
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise TaskMetadataError(
            "Task metadata storage policy state is malformed"
        ) from exc
    if row is None:
        raise TaskMetadataError(
            "Task metadata storage policy has no singleton row"
        )
    policy = TASK_METADATA_STORAGE_POLICY_V2
    fingerprint = task_metadata_storage_policy_fingerprint(policy)
    expected_policy = (
        policy.schema_version,
        policy.policy_name,
        policy.policy_version,
        fingerprint,
        _policy_json(policy),
    )
    if tuple(str(value or "") for value in row[:5]) != expected_policy:
        raise TaskMetadataError(
            "Task metadata storage policy differs from approved v2"
        )
    archive = _archive_snapshot(con)
    stored_archive = (
        _db_bool(row[5], label="archived task metadata presence"),
        _nonnegative_int(row[6], label="archived task metadata row count"),
        str(row[7] or ""),
    )
    if stored_archive != (
        archive.present,
        archive.row_count,
        archive.fingerprint,
    ):
        raise TaskMetadataError("Frozen task metadata v1 archive changed")
    migrated_count = _nonnegative_int(
        row[8], label="migrated task metadata row count"
    )
    collapsed_count = _nonnegative_int(
        row[9], label="collapsed task metadata duplicate count"
    )
    if archive.present and migrated_count + collapsed_count != archive.row_count:
        raise TaskMetadataError(
            "Task metadata migration counts differ from frozen archive"
        )
    if not archive.present and (migrated_count or collapsed_count):
        raise TaskMetadataError(
            "Fresh task metadata policy contains migration counts"
        )
    initialized_at = str(row[10] or "")
    updated_at = str(row[11] or "")
    if not initialized_at or not updated_at:
        raise TaskMetadataError(
            "Task metadata storage policy timestamps are missing"
        )
    _validate_current_schema_v2(con)
    return TaskMetadataStorageStateV1(
        policy=policy,
        policy_fingerprint=fingerprint,
        archived_table_present=archive.present,
        archived_row_count=archive.row_count,
        archived_fingerprint=archive.fingerprint,
        migrated_logical_row_count=migrated_count,
        collapsed_duplicate_row_count=collapsed_count,
        initialized_at=initialized_at,
        updated_at=updated_at,
    )


def read_task_metadata_value_v2(
    con: sqlite3.Connection,
    coordinate: TaskMetadataCoordinateV1,
    *,
    key: str,
) -> Any:
    _validate_write_inputs(coordinate, key=key, updated_at=None)
    load_task_metadata_storage_state_v1(con)
    row = con.execute(
        f"""
        SELECT metadata_coordinate_id, schema_version, stage, task_base,
               coordinate_json, key, value_json, updated_at
        FROM {TASK_METADATA_TABLE_V2}
        WHERE metadata_coordinate_id=? AND key=?
        """,
        (coordinate.metadata_coordinate_id, str(key)),
    ).fetchone()
    if row is None:
        return None
    record = _decode_current_row(row)
    if record.coordinate != coordinate:
        raise TaskMetadataError(
            "Task metadata coordinate ID collision or payload mismatch"
        )
    return record.value


def write_task_metadata_value_v2(
    con: sqlite3.Connection,
    coordinate: TaskMetadataCoordinateV1,
    *,
    key: str,
    value: Any,
    updated_at: str,
) -> None:
    _validate_write_inputs(coordinate, key=key, updated_at=updated_at)
    load_task_metadata_storage_state_v1(con)
    coordinate_rows = con.execute(
        f"""
        SELECT DISTINCT schema_version, stage, task_base, coordinate_json
        FROM {TASK_METADATA_TABLE_V2}
        WHERE metadata_coordinate_id=?
        """,
        (coordinate.metadata_coordinate_id,),
    ).fetchall()
    expected_coordinate = (
        TASK_METADATA_ROW_SCHEMA_V2,
        coordinate.stage,
        coordinate.task_base,
        coordinate.canonical_json,
    )
    if any(
        tuple(str(value or "") for value in row) != expected_coordinate
        for row in coordinate_rows
    ):
        raise TaskMetadataError(
            "Task metadata coordinate ID collision or payload mismatch"
        )
    value_json = encode_task_metadata_value_json(value)
    con.execute(
        f"""
        INSERT INTO {TASK_METADATA_TABLE_V2}(
            metadata_coordinate_id, schema_version, stage, task_base,
            coordinate_json, key, value_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(metadata_coordinate_id, key) DO UPDATE SET
            value_json=excluded.value_json,
            updated_at=excluded.updated_at
        """,
        (
            coordinate.metadata_coordinate_id,
            TASK_METADATA_ROW_SCHEMA_V2,
            coordinate.stage,
            coordinate.task_base,
            coordinate.canonical_json,
            str(key),
            value_json,
            str(updated_at),
        ),
    )


def encode_task_metadata_value_json(value: Any) -> str:
    try:
        return _canonical_json(_jsonify(value))
    except (TypeError, ValueError) as exc:
        raise TaskMetadataError("Task metadata value is not valid JSON") from exc


def load_task_metadata_records_v2(
    con: sqlite3.Connection,
) -> tuple[TaskMetadataRecordV2, ...]:
    load_task_metadata_storage_state_v1(con)
    rows = con.execute(
        f"""
        SELECT metadata_coordinate_id, schema_version, stage, task_base,
               coordinate_json, key, value_json, updated_at
        FROM {TASK_METADATA_TABLE_V2}
        ORDER BY metadata_coordinate_id, key
        """
    ).fetchall()
    records = tuple(_decode_current_row(row) for row in rows)
    identities = {
        (record.metadata_coordinate_id, record.key)
        for record in records
    }
    if len(identities) != len(records):
        raise TaskMetadataError(
            "Task metadata v2 contains duplicate coordinate keys"
        )
    return records


def load_task_metadata_snapshot_v1(
    state_or_db_path: str | Path,
) -> TaskMetadataSnapshotV1:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return TaskMetadataSnapshotV1(
            state_db_path=str(db_path),
            storage_table=TASK_METADATA_TABLE_V2,
            records=(),
            available=False,
        )
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            records = load_task_metadata_records_v2(con)
    except TaskMetadataError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskMetadataError(
            "Could not read current task metadata: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return TaskMetadataSnapshotV1(
        state_db_path=str(db_path),
        storage_table=TASK_METADATA_TABLE_V2,
        records=records,
    )


def load_archived_task_metadata_snapshot_v1(
    state_or_db_path: str | Path,
) -> ArchivedTaskMetadataSnapshotV1:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return ArchivedTaskMetadataSnapshotV1(
            state_db_path=str(db_path),
            storage_table=TASK_METADATA_TABLE_V1,
            records=(),
            available=False,
        )
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            records = _load_archived_task_metadata_records_v1(con)
    except TaskMetadataError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskMetadataError(
            "Could not read archived task metadata: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return ArchivedTaskMetadataSnapshotV1(
        state_db_path=str(db_path),
        storage_table=TASK_METADATA_TABLE_V1,
        records=records,
    )


def _build_task_metadata_coordinate_from_params_v1(
    *,
    stage: Any,
    task_base: Any,
    sample_id: Any,
    contig: Any,
    params: Mapping[str, Any],
) -> TaskMetadataCoordinateV1:
    fields = tuple(
        TaskMetadataParameterV1(key=str(key), legacy_value=str(value))
        for key, value in sorted(params.items(), key=lambda item: str(item[0]))
    )
    if len({field.key for field in fields}) != len(fields):
        raise TaskMetadataCoordinateError(
            "Task metadata coordinate contains duplicate parameter keys"
        )
    return TaskMetadataCoordinateV1(
        stage=str(stage),
        task_base=str(task_base),
        scope=TaskMetadataScopeV1(
            sample_id=None if sample_id is None else str(sample_id),
            contig=None if contig is None else str(contig),
        ),
        params=fields,
    )


def _build_task_metadata_coordinate_from_legacy_v1(
    *,
    stage: str,
    task_base: str,
    sample_id: str | None,
    contig: str | None,
    variant_str: str,
) -> TaskMetadataCoordinateV1:
    try:
        fields = decode_legacy_variant_str_v1(variant_str)
        return _build_task_metadata_coordinate_from_params_v1(
            stage=stage,
            task_base=task_base,
            sample_id=sample_id,
            contig=contig,
            params={field.key: field.legacy_value for field in fields},
        )
    except (TaskCoordinateError, TaskMetadataCoordinateError) as exc:
        raise TaskMetadataError(
            "Archived task metadata alias or scope is invalid: "
            f"{stage}:{task_base}"
        ) from exc


def _load_archived_task_metadata_records_v1(
    con: sqlite3.Connection,
) -> tuple[ArchivedTaskMetadataRecordV1, ...]:
    if TASK_METADATA_TABLE_V1 not in _table_names(con):
        raise TaskMetadataError(
            "Archived task metadata requires task_meta"
        )
    _validate_archived_schema_v1(con)
    rows = con.execute(
        f"""
        SELECT rowid, sample_id, contig, stage, task_base, variant_str,
               key, value_json, updated_at
        FROM {TASK_METADATA_TABLE_V1}
        ORDER BY rowid
        """
    ).fetchall()
    records: list[ArchivedTaskMetadataRecordV1] = []
    for row in rows:
        rowid = _positive_int(row[0], label="archived task metadata rowid")
        sample_id = None if row[1] is None else str(row[1])
        contig = None if row[2] is None else str(row[2])
        stage = str(row[3] or "")
        task_base = str(row[4] or "")
        variant_str = str(row[5] or "")
        key = str(row[6] or "")
        value_json = str(row[7] or "")
        updated_at = str(row[8] or "")
        if not key or not updated_at:
            raise TaskMetadataError(
                f"Archived task metadata row {rowid} lacks key or timestamp"
            )
        coordinate = _build_task_metadata_coordinate_from_legacy_v1(
            stage=stage,
            task_base=task_base,
            sample_id=sample_id,
            contig=contig,
            variant_str=variant_str,
        )
        if (
            derive_legacy_variant_str_from_task_metadata_coordinate_v1(
                coordinate
            )
            != variant_str
        ):
            raise TaskMetadataError(
                f"Archived task metadata row {rowid} alias does not round-trip"
            )
        value = _load_json(
            value_json,
            label=f"archived task metadata row {rowid} value",
        )
        records.append(
            ArchivedTaskMetadataRecordV1(
                rowid=rowid,
                coordinate=coordinate,
                legacy_variant_str=variant_str,
                key=key,
                value=value,
                value_json=value_json,
                updated_at=updated_at,
            )
        )
    return tuple(records)


def _select_migration_records(
    records: tuple[ArchivedTaskMetadataRecordV1, ...],
) -> tuple[tuple[ArchivedTaskMetadataRecordV1, ...], int]:
    grouped: dict[
        tuple[str, str], list[ArchivedTaskMetadataRecordV1]
    ] = {}
    coordinates: dict[str, TaskMetadataCoordinateV1] = {}
    for record in records:
        coordinate_id = record.metadata_coordinate_id
        known = coordinates.setdefault(coordinate_id, record.coordinate)
        if known != record.coordinate:
            raise TaskMetadataError(
                "Archived task metadata coordinate hash collision"
            )
        grouped.setdefault((coordinate_id, record.key), []).append(record)
    selected: list[ArchivedTaskMetadataRecordV1] = []
    for identity, candidates in sorted(grouped.items()):
        semantic_values = {_canonical_json(item.value) for item in candidates}
        if len(semantic_values) != 1:
            raise TaskMetadataError(
                "Archived task metadata contains conflicting duplicate values: "
                f"{identity[0]}:{identity[1]}"
            )
        selected.append(
            max(candidates, key=lambda item: (item.updated_at, item.rowid))
        )
    selected.sort(key=lambda item: (item.metadata_coordinate_id, item.key))
    return tuple(selected), len(records) - len(selected)


def _insert_task_metadata_record_v2(
    con: sqlite3.Connection,
    record: ArchivedTaskMetadataRecordV1,
) -> None:
    coordinate = record.coordinate
    con.execute(
        f"""
        INSERT INTO {TASK_METADATA_TABLE_V2}(
            metadata_coordinate_id, schema_version, stage, task_base,
            coordinate_json, key, value_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            coordinate.metadata_coordinate_id,
            TASK_METADATA_ROW_SCHEMA_V2,
            coordinate.stage,
            coordinate.task_base,
            coordinate.canonical_json,
            record.key,
            record.value_json,
            record.updated_at,
        ),
    )


def _decode_current_row(row: Any) -> TaskMetadataRecordV2:
    coordinate_id = str(row[0] or "")
    schema_version = str(row[1] or "")
    stage = str(row[2] or "")
    task_base = str(row[3] or "")
    coordinate_json = str(row[4] or "")
    key = str(row[5] or "")
    value_json = str(row[6] or "")
    updated_at = str(row[7] or "")
    try:
        coordinate = decode_task_metadata_coordinate_json_v1(coordinate_json)
    except TaskMetadataCoordinateError as exc:
        raise TaskMetadataError(
            f"Current task metadata coordinate is malformed: {coordinate_id}"
        ) from exc
    if (
        coordinate_id != coordinate.metadata_coordinate_id
        or schema_version != TASK_METADATA_ROW_SCHEMA_V2
        or stage != coordinate.stage
        or task_base != coordinate.task_base
        or not key
        or not updated_at
    ):
        raise TaskMetadataError(
            "Current task metadata columns differ from coordinate JSON: "
            f"{coordinate_id or '<empty>'}"
        )
    value = _load_json(value_json, label="current task metadata value")
    return TaskMetadataRecordV2(
        coordinate=coordinate,
        key=key,
        value=value,
        value_json=value_json,
        updated_at=updated_at,
    )


def _init_current_table_v2(con: sqlite3.Connection) -> None:
    con.execute(
        f"""
        CREATE TABLE {TASK_METADATA_TABLE_V2}
        (
            metadata_coordinate_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            stage TEXT NOT NULL,
            task_base TEXT NOT NULL,
            coordinate_json TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(metadata_coordinate_id, key)
        )
        """
    )
    con.execute(
        f"""
        CREATE INDEX idx_task_meta_v2_lookup
        ON {TASK_METADATA_TABLE_V2}(
            stage, task_base, key, metadata_coordinate_id
        )
        """
    )


def _init_storage_state_table_v1(con: sqlite3.Connection) -> None:
    con.execute(
        f"""
        CREATE TABLE {TASK_METADATA_STORAGE_STATE_TABLE_V1}
        (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            schema_version TEXT NOT NULL,
            policy_name TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            policy_fingerprint TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            archived_table_present INTEGER NOT NULL,
            archived_row_count INTEGER NOT NULL,
            archived_fingerprint TEXT NOT NULL,
            migrated_logical_row_count INTEGER NOT NULL,
            collapsed_duplicate_row_count INTEGER NOT NULL,
            initialized_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _validate_current_schema_v2(con: sqlite3.Connection) -> None:
    expected = (
        ("metadata_coordinate_id", "TEXT", 1, 1),
        ("schema_version", "TEXT", 1, 0),
        ("stage", "TEXT", 1, 0),
        ("task_base", "TEXT", 1, 0),
        ("coordinate_json", "TEXT", 1, 0),
        ("key", "TEXT", 1, 2),
        ("value_json", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    )
    _require_column_schema(
        con,
        table=TASK_METADATA_TABLE_V2,
        expected=expected,
        label="Task metadata v2",
    )
    _require_index(
        con,
        table=TASK_METADATA_TABLE_V2,
        name="idx_task_meta_v2_lookup",
        columns=("stage", "task_base", "key", "metadata_coordinate_id"),
        unique=False,
    )
    primary = tuple(
        name
        for _order, name in sorted(
            (int(row[5]), str(row[1]))
            for row in con.execute(
                f"PRAGMA table_info({TASK_METADATA_TABLE_V2})"
            ).fetchall()
            if int(row[5])
        )
    )
    if primary != ("metadata_coordinate_id", "key"):
        raise TaskMetadataError(
            "Task metadata v2 primary key differs from approved schema"
        )


def _validate_storage_state_schema_v1(con: sqlite3.Connection) -> None:
    expected = (
        ("singleton_id", "INTEGER", 0, 1),
        ("schema_version", "TEXT", 1, 0),
        ("policy_name", "TEXT", 1, 0),
        ("policy_version", "TEXT", 1, 0),
        ("policy_fingerprint", "TEXT", 1, 0),
        ("policy_json", "TEXT", 1, 0),
        ("archived_table_present", "INTEGER", 1, 0),
        ("archived_row_count", "INTEGER", 1, 0),
        ("archived_fingerprint", "TEXT", 1, 0),
        ("migrated_logical_row_count", "INTEGER", 1, 0),
        ("collapsed_duplicate_row_count", "INTEGER", 1, 0),
        ("initialized_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    )
    _require_column_schema(
        con,
        table=TASK_METADATA_STORAGE_STATE_TABLE_V1,
        expected=expected,
        label="Task metadata storage state",
    )


def _validate_archived_schema_v1(con: sqlite3.Connection) -> None:
    expected = (
        ("sample_id", "TEXT", 0, 1),
        ("contig", "TEXT", 0, 2),
        ("stage", "TEXT", 1, 3),
        ("task_base", "TEXT", 1, 4),
        ("variant_str", "TEXT", 1, 5),
        ("key", "TEXT", 1, 6),
        ("value_json", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0),
    )
    _require_column_schema(
        con,
        table=TASK_METADATA_TABLE_V1,
        expected=expected,
        label="Archived task metadata v1",
    )
    _require_index(
        con,
        table=TASK_METADATA_TABLE_V1,
        name="idx_task_meta_lookup",
        columns=("stage", "task_base", "variant_str", "key"),
        unique=False,
    )


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
        raise TaskMetadataError(f"{label} schema is malformed") from exc
    if actual != expected:
        raise TaskMetadataError(
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
    actual_columns = tuple(
        str(row[2])
        for row in con.execute(f'PRAGMA index_info("{name}")').fetchall()
    )
    if indexes.get(name) is not unique or actual_columns != columns:
        raise TaskMetadataError(
            f"{table} index {name} differs from approved schema"
        )


def _archive_snapshot(con: sqlite3.Connection) -> _ArchiveSnapshot:
    table = TASK_METADATA_TABLE_V1
    if table not in _table_names(con):
        return _ArchiveSnapshot(
            present=False,
            row_count=0,
            fingerprint=_sha256_json({"table": table, "present": False}),
        )
    _validate_archived_schema_v1(con)
    column_info = tuple(
        tuple(_fingerprint_value(value) for value in row)
        for row in con.execute(f"PRAGMA table_info({table})").fetchall()
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
    rows = tuple(
        tuple(_fingerprint_value(value) for value in row)
        for row in con.execute(
            f"SELECT rowid, * FROM {table} ORDER BY rowid"
        ).fetchall()
    )
    sequence = None
    if "sqlite_sequence" in _table_names(con):
        sequence_row = con.execute(
            "SELECT seq FROM sqlite_sequence WHERE name=?",
            (table,),
        ).fetchone()
        sequence = None if sequence_row is None else int(sequence_row[0])
    payload = {
        "table": table,
        "present": True,
        "column_info": [list(row) for row in column_info],
        "objects": list(objects),
        "sqlite_sequence": sequence,
        "rows": [list(row) for row in rows],
    }
    return _ArchiveSnapshot(
        present=True,
        row_count=len(rows),
        fingerprint=_sha256_json(payload),
    )


def _validate_write_inputs(
    coordinate: TaskMetadataCoordinateV1,
    *,
    key: str,
    updated_at: str | None,
) -> None:
    if not isinstance(coordinate, TaskMetadataCoordinateV1):
        raise TaskMetadataError(
            "Task metadata access requires a structured coordinate"
        )
    if not isinstance(key, str) or not key:
        raise TaskMetadataError("Task metadata key must be non-empty text")
    if updated_at is not None and not str(updated_at):
        raise TaskMetadataError(
            "Task metadata updated_at must be non-empty"
        )


def _policy_json(policy: TaskMetadataStoragePolicyV1) -> str:
    return _canonical_json(policy.as_dict())


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("ascii")).hexdigest()


def _load_json(
    value: Any,
    *,
    label: str,
    error_type: type[Exception] = TaskMetadataError,
) -> Any:
    try:
        return json.loads(
            str(value),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise error_type(f"Malformed {label} JSON") from exc


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _jsonify(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(_jsonify(item) for item in value)
    if isinstance(value, (tuple, list)):
        return [_jsonify(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    return str(value)


def _fingerprint_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"type": type(value).__name__, "text": str(value)}


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise TaskMetadataError(f"{label} is not a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskMetadataError(f"{label} is not a positive integer") from exc
    if result <= 0:
        raise TaskMetadataError(f"{label} is not a positive integer")
    return result


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise TaskMetadataError(f"{label} is not nonnegative")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskMetadataError(f"{label} is not nonnegative") from exc
    if result < 0:
        raise TaskMetadataError(f"{label} is not nonnegative")
    return result


def _db_bool(value: Any, *, label: str) -> bool:
    if value not in (0, 1):
        raise TaskMetadataError(f"{label} must be 0 or 1")
    return bool(value)


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
        nested = path / "state" / "state.sqlite"
        if nested.exists():
            return nested
        return path / "state.sqlite"
    return path
