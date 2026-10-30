from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, TypeVar

from .task_coordinate import (
    TASK_COORDINATE_SCHEMA_V1,
    TaskCoordinateError,
    TaskCoordinateV1,
    build_task_coordinate_v1_from_legacy_done,
    decode_task_coordinate_json_v1,
)
from .task_coordinate_store import (
    TASK_COORDINATE_AUTHORITY_V1,
    TASK_COORDINATE_DONE_TABLE_V1,
    TASK_COORDINATE_DONE_TABLE_V2,
    TASK_COORDINATE_LEGACY_COMPATIBILITY,
    TASK_COORDINATE_STATE_TABLE_V1,
    TaskCoordinatePersistenceError,
    load_task_coordinate_state_v1,
)
from .task_completion_storage_policy import (
    TASK_COMPLETION_STORAGE_POLICY_V2,
    TASK_COMPLETION_STORAGE_POLICY_V3,
    TASK_COMPLETION_STORAGE_STATE_TABLE_V1,
    TaskCompletionStoragePolicyError,
    load_task_completion_storage_state_for_upgrade_v1,
    load_task_completion_storage_state_v1,
)


TASK_COORDINATE_SCHEDULER_AUTHORITY_V1 = "task_done_coordinate_v1"
TASK_COORDINATE_SCHEDULER_AUTHORITY_V2 = "task_done_coordinate_v2"
LEGACY_SCHEDULER_AUTHORITY_V1 = "legacy_task_done_flex"
TASK_COMPLETION_RECORD_SCHEMA_V2 = "scpolya.task_completion.record.v2"
TASK_COMPLETION_SNAPSHOT_SCHEMA_V2 = "scpolya.task_completion.snapshot.v2"

TaskCompletionAuthorityModeV1 = Literal[
    "coordinate_v1",
    "legacy_compatibility",
    "uninitialized",
    "unavailable",
]
TaskCompletionSourceV1 = Literal[
    "structured",
    "legacy",
    "mixed",
    "unavailable",
]
TaskCompletionRecordSourceV1 = Literal["structured", "legacy"]
TaskSchedulerAuthorityV1 = Literal[
    "task_done_coordinate_v1",
    "task_done_coordinate_v2",
    "legacy_task_done_flex",
]


class TaskCompletionLoadError(RuntimeError):
    pass


class LegacyTaskCompletionAccessError(TaskCompletionLoadError):
    """Raised when legacy completion is requested through a current reader."""


@dataclass(frozen=True)
class TaskCompletionRecordV1:
    stage: str
    task_base: str
    task_coordinate_id: str
    identity: tuple[tuple[str, str], ...]
    params: tuple[tuple[str, str], ...]
    legacy_variant_str: str
    done_at: str
    meta_json: str | None
    completion_source: TaskCompletionRecordSourceV1
    structured_source: str | None = None
    schema_version: str = TASK_COORDINATE_SCHEMA_V1

    @property
    def key(self) -> tuple[str, str]:
        return self.stage, self.task_base

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskCompletionSnapshotV1:
    state_db_path: str
    authority_mode: TaskCompletionAuthorityModeV1
    completion_source: TaskCompletionSourceV1
    records: tuple[TaskCompletionRecordV1, ...]
    legacy_fallback_count: int
    scheduler_authority_override: TaskSchedulerAuthorityV1 | None = None

    @property
    def scheduler_authority(self) -> TaskSchedulerAuthorityV1:
        if self.scheduler_authority_override is not None:
            return self.scheduler_authority_override
        return scheduler_authority_for_completion_mode_v1(
            self.authority_mode
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "state_db_path": self.state_db_path,
            "authority_mode": self.authority_mode,
            "completion_source": self.completion_source,
            "scheduler_authority": self.scheduler_authority,
            "record_count": len(self.records),
            "legacy_fallback_count": self.legacy_fallback_count,
            "records": [record.as_dict() for record in self.records],
        }


@dataclass(frozen=True)
class TaskCompletionRecordV2:
    task_coordinate: TaskCoordinateV1
    done_at: str
    meta_json: str | None
    structured_source: Literal["runtime", "migration"]
    schema_version: str = TASK_COMPLETION_RECORD_SCHEMA_V2

    def __post_init__(self) -> None:
        if not isinstance(self.task_coordinate, TaskCoordinateV1):
            raise TypeError(
                "Current completion records require TaskCoordinateV1"
            )
        if self.schema_version != TASK_COMPLETION_RECORD_SCHEMA_V2:
            raise ValueError(
                "Current completion record schema mismatch: "
                f"{self.schema_version!r}"
            )
        if not isinstance(self.done_at, str) or not self.done_at:
            raise ValueError(
                "Current completion records require a completion timestamp"
            )
        if self.meta_json is not None and not isinstance(self.meta_json, str):
            raise TypeError("Current completion metadata must be text or null")
        if self.structured_source not in {"runtime", "migration"}:
            raise ValueError(
                "Current completion provenance must be runtime or migration"
            )

    @property
    def stage(self) -> str:
        return self.task_coordinate.stage

    @property
    def task_base(self) -> str:
        return self.task_coordinate.task_base

    @property
    def task_coordinate_id(self) -> str:
        return self.task_coordinate.task_coordinate_id

    @property
    def identity(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (field.key, field.legacy_value)
            for field in self.task_coordinate.identity
        )

    @property
    def params(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (field.key, field.legacy_value)
            for field in self.task_coordinate.params
        )

    @property
    def key(self) -> tuple[str, str]:
        return self.stage, self.task_base

    @property
    def completion_source(self) -> Literal["structured"]:
        return "structured"

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
            "done_at": self.done_at,
            "meta_json": self.meta_json,
            "completion_source": self.completion_source,
            "structured_source": self.structured_source,
        }


@dataclass(frozen=True)
class TaskCompletionSnapshotV2:
    state_db_path: str
    authority_mode: TaskCompletionAuthorityModeV1
    completion_source: TaskCompletionSourceV1
    records: tuple[TaskCompletionRecordV2, ...]
    legacy_fallback_count: int
    schema_version: str = TASK_COMPLETION_SNAPSHOT_SCHEMA_V2

    def __post_init__(self) -> None:
        if self.schema_version != TASK_COMPLETION_SNAPSHOT_SCHEMA_V2:
            raise ValueError(
                "Current completion snapshot schema mismatch: "
                f"{self.schema_version!r}"
            )
        if any(
            not isinstance(record, TaskCompletionRecordV2)
            for record in self.records
        ):
            raise TypeError(
                "Current completion snapshots require v2 records"
            )
        if self.completion_source not in {"structured", "unavailable"}:
            raise ValueError(
                "Current completion snapshots cannot expose legacy records"
            )
        if self.authority_mode not in {
            "coordinate_v1",
            "uninitialized",
            "unavailable",
        }:
            raise ValueError(
                "Current completion snapshots require current authority"
            )
        if (
            self.authority_mode == "coordinate_v1"
            and self.completion_source != "structured"
        ):
            raise ValueError(
                "Coordinate authority requires structured completion source"
            )
        if (
            self.authority_mode != "coordinate_v1"
            and self.completion_source != "unavailable"
        ):
            raise ValueError(
                "Uninitialized completion cannot expose structured source"
            )
        if self.records and self.completion_source != "structured":
            raise ValueError(
                "Current completion records require structured source"
            )
        if self.legacy_fallback_count != 0:
            raise ValueError(
                "Current completion snapshots cannot contain legacy fallback"
            )

    @property
    def scheduler_authority(self) -> TaskSchedulerAuthorityV1:
        return scheduler_authority_for_completion_mode_v1(
            self.authority_mode
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state_db_path": self.state_db_path,
            "authority_mode": self.authority_mode,
            "completion_source": self.completion_source,
            "scheduler_authority": self.scheduler_authority,
            "record_count": len(self.records),
            "legacy_fallback_count": self.legacy_fallback_count,
            "records": [record.as_dict() for record in self.records],
        }


def scheduler_authority_for_completion_mode_v1(
    authority_mode: TaskCompletionAuthorityModeV1,
) -> TaskSchedulerAuthorityV1:
    if authority_mode == TASK_COORDINATE_AUTHORITY_V1:
        return TASK_COORDINATE_SCHEDULER_AUTHORITY_V2
    return LEGACY_SCHEDULER_AUTHORITY_V1


def load_task_completion_snapshot_v2(
    state_or_db_path: str | Path,
) -> TaskCompletionSnapshotV2:
    """Load current structured completion without legacy fallback."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return TaskCompletionSnapshotV2(
            state_db_path=str(db_path),
            authority_mode="unavailable",
            completion_source="unavailable",
            records=(),
            legacy_fallback_count=0,
        )
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            return load_task_completion_snapshot_from_connection_v2(
                con,
                state_db_path=db_path,
            )
    except TaskCompletionLoadError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskCompletionLoadError(
            f"Could not read completion state: {type(exc).__name__}: {exc}"
        ) from exc


def load_archived_task_completion_snapshot_v1(
    state_or_db_path: str | Path,
) -> TaskCompletionSnapshotV1:
    """Explicitly inspect archived alias-bearing coordinate completion."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return TaskCompletionSnapshotV1(
            state_db_path=str(db_path),
            authority_mode="unavailable",
            completion_source="unavailable",
            records=(),
            legacy_fallback_count=0,
            scheduler_authority_override=(
                TASK_COORDINATE_SCHEDULER_AUTHORITY_V1
            ),
        )
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            tables = {
                str(row[0])
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if (
                TASK_COORDINATE_STATE_TABLE_V1 not in tables
                or TASK_COORDINATE_DONE_TABLE_V1 not in tables
            ):
                raise TaskCompletionLoadError(
                    "Archived coordinate completion requires v1 state and "
                    "DONE tables"
                )
            try:
                state = load_task_coordinate_state_v1(con)
            except TaskCoordinatePersistenceError as exc:
                raise TaskCompletionLoadError(str(exc)) from exc
            if state.authority_mode != TASK_COORDINATE_AUTHORITY_V1:
                raise TaskCompletionLoadError(
                    "Archived coordinate reader requires coordinate authority"
                )
            if TASK_COMPLETION_STORAGE_STATE_TABLE_V1 in tables:
                try:
                    load_task_completion_storage_state_for_upgrade_v1(con)
                except TaskCompletionStoragePolicyError as exc:
                    raise TaskCompletionLoadError(str(exc)) from exc
            records = _load_structured_completion_records_v1(con)
            return TaskCompletionSnapshotV1(
                state_db_path=str(db_path),
                authority_mode=state.authority_mode,
                completion_source="structured",
                records=_ordered_records(records.values()),
                legacy_fallback_count=0,
                scheduler_authority_override=(
                    TASK_COORDINATE_SCHEDULER_AUTHORITY_V1
                ),
            )
    except TaskCompletionLoadError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskCompletionLoadError(
            "Could not read archived coordinate completion state: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def require_current_task_completion_state_v1(
    state_or_db_path: str | Path,
) -> None:
    """Fail before runtime initialization can mutate legacy completion state."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            tables = {
                str(row[0])
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            has_state = TASK_COORDINATE_STATE_TABLE_V1 in tables
            has_structured_v1 = TASK_COORDINATE_DONE_TABLE_V1 in tables
            has_structured_v2 = TASK_COORDINATE_DONE_TABLE_V2 in tables
            has_legacy = "task_done_flex" in tables
            if not has_state:
                if has_structured_v1 or has_structured_v2:
                    raise TaskCompletionLoadError(
                        "Structured completion table exists without coordinate "
                        "authority state"
                    )
                if has_legacy:
                    raise LegacyTaskCompletionAccessError(
                        "Current runtime refuses legacy-only completion state; "
                        "use the explicit read-only legacy adapter"
                    )
                return
            try:
                state = load_task_coordinate_state_v1(con)
            except TaskCoordinatePersistenceError as exc:
                raise TaskCompletionLoadError(str(exc)) from exc
            if state.authority_mode != TASK_COORDINATE_AUTHORITY_V1:
                raise LegacyTaskCompletionAccessError(
                    "Current runtime refuses legacy-compatible completion "
                    "state; use the explicit read-only legacy adapter"
                )
            if TASK_COMPLETION_STORAGE_STATE_TABLE_V1 not in tables:
                if not has_structured_v1 or has_structured_v2:
                    raise TaskCompletionLoadError(
                        "Coordinate authority without a storage policy must "
                        "provide only the archived v1 upgrade source"
                    )
                return
            try:
                storage_state = (
                    load_task_completion_storage_state_for_upgrade_v1(con)
                )
            except TaskCompletionStoragePolicyError as exc:
                raise TaskCompletionLoadError(str(exc)) from exc
            if storage_state.policy == TASK_COMPLETION_STORAGE_POLICY_V3:
                if not has_structured_v2:
                    raise TaskCompletionLoadError(
                        "Storage policy v3 lacks task_done_coordinate_v2"
                    )
                _validate_current_structured_table_schema_v2(con)
            elif (
                storage_state.policy == TASK_COMPLETION_STORAGE_POLICY_V2
                and not has_structured_v1
            ):
                raise TaskCompletionLoadError(
                    "Storage policy v2 lacks task_done_coordinate_v1"
                )
    except TaskCompletionLoadError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskCompletionLoadError(
            "Could not validate current completion state before runtime "
            f"initialization: {type(exc).__name__}: {exc}"
        ) from exc


def load_legacy_task_completion_snapshot_v1(
    state_or_db_path: str | Path,
) -> TaskCompletionSnapshotV1:
    """Explicitly inspect historical completion through a read-only adapter."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return TaskCompletionSnapshotV1(
            state_db_path=str(db_path),
            authority_mode="unavailable",
            completion_source="unavailable",
            records=(),
            legacy_fallback_count=0,
        )
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            return load_legacy_task_completion_snapshot_from_connection_v1(
                con,
                state_db_path=db_path,
            )
    except TaskCompletionLoadError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskCompletionLoadError(
            "Could not read explicit legacy completion state: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def load_task_completion_snapshot_from_connection_v2(
    con: sqlite3.Connection,
    *,
    state_db_path: str | Path,
) -> TaskCompletionSnapshotV2:
    """Load current completion from an existing read-only connection."""

    db_path = _resolve_db_path(state_db_path)
    try:
        tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        has_state = TASK_COORDINATE_STATE_TABLE_V1 in tables
        has_structured = TASK_COORDINATE_DONE_TABLE_V2 in tables
        has_archived_structured = TASK_COORDINATE_DONE_TABLE_V1 in tables
        has_legacy = "task_done_flex" in tables

        if not has_state:
            if has_structured or has_archived_structured:
                raise TaskCompletionLoadError(
                    "Structured completion table exists without coordinate "
                    "authority state"
                )
            if not has_legacy:
                return TaskCompletionSnapshotV2(
                    state_db_path=str(db_path),
                    authority_mode="uninitialized",
                    completion_source="unavailable",
                    records=(),
                    legacy_fallback_count=0,
                )
            raise LegacyTaskCompletionAccessError(
                "Legacy-only completion state requires explicit read access "
                "through load_legacy_task_completion_snapshot_v1()"
            )

        try:
            state = load_task_coordinate_state_v1(con)
        except TaskCoordinatePersistenceError as exc:
            raise TaskCompletionLoadError(str(exc)) from exc
        if state.authority_mode != TASK_COORDINATE_AUTHORITY_V1:
            raise LegacyTaskCompletionAccessError(
                "Legacy-compatible completion state requires explicit read "
                "access through load_legacy_task_completion_snapshot_v1()"
            )
        if not has_structured:
            raise TaskCompletionLoadError(
                "Coordinate authority state lacks its current structured "
                "completion table"
            )
        _validate_current_storage_policy_v1(con, tables)

        structured = _load_structured_completion_records_v2(con)
        return TaskCompletionSnapshotV2(
            state_db_path=str(db_path),
            authority_mode=state.authority_mode,
            completion_source="structured",
            records=_ordered_records(structured.values()),
            legacy_fallback_count=0,
        )
    except TaskCompletionLoadError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskCompletionLoadError(
            f"Could not load completion snapshot: {type(exc).__name__}: {exc}"
        ) from exc


def load_legacy_task_completion_snapshot_from_connection_v1(
    con: sqlite3.Connection,
    *,
    state_db_path: str | Path,
) -> TaskCompletionSnapshotV1:
    """Explicitly load legacy completion from an existing read-only connection."""

    db_path = _resolve_db_path(state_db_path)
    try:
        tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        has_state = TASK_COORDINATE_STATE_TABLE_V1 in tables
        has_structured = TASK_COORDINATE_DONE_TABLE_V1 in tables
        has_legacy = "task_done_flex" in tables

        if not has_legacy:
            raise TaskCompletionLoadError(
                "Explicit legacy completion read requires task_done_flex"
            )
        if not has_state:
            if has_structured:
                raise TaskCompletionLoadError(
                    "Structured completion table exists without coordinate "
                    "authority state"
                )
            legacy = _load_legacy_completion_records_v1(con)
            return TaskCompletionSnapshotV1(
                state_db_path=str(db_path),
                authority_mode="uninitialized",
                completion_source="legacy",
                records=_ordered_records(legacy.values()),
                legacy_fallback_count=len(legacy),
            )

        try:
            state = load_task_coordinate_state_v1(con)
        except TaskCoordinatePersistenceError as exc:
            raise TaskCompletionLoadError(str(exc)) from exc
        if state.authority_mode == TASK_COORDINATE_AUTHORITY_V1:
            raise TaskCompletionLoadError(
                "Explicit legacy reader refuses current coordinate-authority "
                "completion state"
            )
        if state.authority_mode != TASK_COORDINATE_LEGACY_COMPATIBILITY:
            raise TaskCompletionLoadError(
                "Unsupported task completion authority mode: "
                f"{state.authority_mode!r}"
            )
        if not has_structured:
            raise TaskCompletionLoadError(
                "Legacy-compatible authority state lacks its structured "
                "completion table"
            )

        legacy = _load_legacy_completion_records_v1(con)
        structured = _load_structured_completion_records_v1(con)
        _validate_structured_legacy_pairs_v1(
            structured,
            legacy,
            require_equal=False,
        )
        fallback_ids = set(legacy) - set(structured)
        records = {
            **{
                coordinate_id: legacy[coordinate_id]
                for coordinate_id in fallback_ids
            },
            **structured,
        }
        if structured and fallback_ids:
            source: TaskCompletionSourceV1 = "mixed"
        elif structured:
            source = "structured"
        else:
            source = "legacy"
        return TaskCompletionSnapshotV1(
            state_db_path=str(db_path),
            authority_mode=state.authority_mode,
            completion_source=source,
            records=_ordered_records(records.values()),
            legacy_fallback_count=len(fallback_ids),
        )
    except TaskCompletionLoadError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise TaskCompletionLoadError(
            "Could not load explicit legacy completion snapshot: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _load_legacy_completion_records_v1(
    con: sqlite3.Connection,
) -> dict[str, TaskCompletionRecordV1]:
    rows = con.execute(
        """
        SELECT rowid, stage, task_base, variant_str,
               k1, v1, k2, v2, k3, v3, k4, v4, k5, v5,
               done_at, meta_json
        FROM task_done_flex
        ORDER BY rowid
        """
    ).fetchall()
    records: dict[str, TaskCompletionRecordV1] = {}
    for row in rows:
        try:
            coordinate = build_task_coordinate_v1_from_legacy_done(
                stage=str(row[1] or ""),
                task_base=str(row[2] or ""),
                variant_str=str(row[3] or ""),
                identity_keys=(row[4], row[6], row[8], row[10], row[12]),
                identity_values=(
                    row[5],
                    row[7],
                    row[9],
                    row[11],
                    row[13],
                ),
            )
        except (TaskCoordinateError, TypeError, ValueError) as exc:
            raise TaskCompletionLoadError(
                "Malformed legacy completion row "
                f"{int(row[0])}: {type(exc).__name__}: {exc}"
            ) from exc
        coordinate_id = coordinate.task_coordinate_id
        if coordinate_id in records:
            raise TaskCompletionLoadError(
                "Multiple legacy completion rows map to task coordinate "
                f"{coordinate_id}"
            )
        records[coordinate_id] = _record_v1_from_coordinate(
            coordinate,
            done_at=str(row[14] or ""),
            meta_json=None if row[15] is None else str(row[15]),
            completion_source="legacy",
        )
    return records


def _validate_current_storage_policy_v1(
    con: sqlite3.Connection,
    tables: set[str],
) -> None:
    if TASK_COMPLETION_STORAGE_STATE_TABLE_V1 not in tables:
        raise TaskCompletionLoadError(
            "Current completion requires storage policy v3"
        )
    try:
        load_task_completion_storage_state_v1(con)
    except TaskCompletionStoragePolicyError as exc:
        raise TaskCompletionLoadError(str(exc)) from exc
    _validate_current_structured_table_schema_v2(con)


def _validate_current_structured_table_schema_v2(
    con: sqlite3.Connection,
) -> None:
    try:
        columns = tuple(
            str(row[1])
            for row in con.execute(
                f"PRAGMA table_info({TASK_COORDINATE_DONE_TABLE_V2})"
            ).fetchall()
        )
    except sqlite3.Error as exc:
        raise TaskCompletionLoadError(
            "Current structured completion table is malformed"
        ) from exc
    expected = (
        "task_coordinate_id",
        "schema_version",
        "stage",
        "task_base",
        "identity_hash",
        "params_hash",
        "coordinate_json",
        "source",
        "done_at",
        "meta_json",
    )
    if columns != expected:
        raise TaskCompletionLoadError(
            "Current structured completion columns differ from storage v2: "
            f"{columns!r}"
        )


def _load_structured_completion_records_v1(
    con: sqlite3.Connection,
) -> dict[str, TaskCompletionRecordV1]:
    rows = con.execute(
        f"""
        SELECT task_coordinate_id, schema_version, stage, task_base,
               identity_hash, params_hash, coordinate_json,
               legacy_variant_str, source, done_at, meta_json
        FROM {TASK_COORDINATE_DONE_TABLE_V1}
        ORDER BY task_coordinate_id
        """
    ).fetchall()
    records: dict[str, TaskCompletionRecordV1] = {}
    for row in rows:
        coordinate_id = str(row[0] or "")
        try:
            coordinate = decode_task_coordinate_json_v1(row[6])
        except (TaskCoordinateError, TypeError, ValueError) as exc:
            raise TaskCompletionLoadError(
                "Malformed structured completion coordinate "
                f"{coordinate_id or '<empty>'}: {type(exc).__name__}: {exc}"
            ) from exc
        expected = (
            coordinate.task_coordinate_id,
            coordinate.schema_version,
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
            coordinate.legacy_variant_str,
        )
        actual = (
            coordinate_id,
            str(row[1] or ""),
            str(row[2] or ""),
            str(row[3] or ""),
            str(row[4] or ""),
            str(row[5] or ""),
            str(row[7] or ""),
        )
        if actual != expected:
            raise TaskCompletionLoadError(
                "Structured completion columns differ from coordinate "
                f"payload: {coordinate_id or '<empty>'}"
            )
        source = str(row[8] or "")
        done_at = str(row[9] or "")
        if source not in {"runtime", "migration"} or not done_at:
            raise TaskCompletionLoadError(
                "Structured completion provenance is invalid: "
                f"{coordinate_id}"
            )
        if coordinate_id in records:
            raise TaskCompletionLoadError(
                "Structured completion contains duplicate task coordinate "
                f"{coordinate_id}"
            )
        records[coordinate_id] = _record_v1_from_coordinate(
            coordinate,
            done_at=done_at,
            meta_json=None if row[10] is None else str(row[10]),
            completion_source="structured",
            structured_source=source,
        )
    return records


def _load_structured_completion_records_v2(
    con: sqlite3.Connection,
) -> dict[str, TaskCompletionRecordV2]:
    rows = con.execute(
        f"""
        SELECT task_coordinate_id, schema_version, stage, task_base,
               identity_hash, params_hash, coordinate_json,
               source, done_at, meta_json
        FROM {TASK_COORDINATE_DONE_TABLE_V2}
        ORDER BY task_coordinate_id
        """
    ).fetchall()
    records: dict[str, TaskCompletionRecordV2] = {}
    for row in rows:
        coordinate_id = str(row[0] or "")
        try:
            coordinate = decode_task_coordinate_json_v1(row[6])
        except (TaskCoordinateError, TypeError, ValueError) as exc:
            raise TaskCompletionLoadError(
                "Malformed structured completion coordinate "
                f"{coordinate_id or '<empty>'}: {type(exc).__name__}: {exc}"
            ) from exc
        expected = (
            coordinate.task_coordinate_id,
            coordinate.schema_version,
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
        )
        actual = tuple(str(value or "") for value in row[:6])
        if actual != expected:
            raise TaskCompletionLoadError(
                "Structured completion v2 columns differ from coordinate "
                f"payload: {coordinate_id or '<empty>'}"
            )
        if str(row[6] or "") != coordinate.canonical_json:
            raise TaskCompletionLoadError(
                "Structured completion v2 JSON is not canonical: "
                f"{coordinate_id or '<empty>'}"
            )
        source = str(row[7] or "")
        done_at = str(row[8] or "")
        if source not in {"runtime", "migration"} or not done_at:
            raise TaskCompletionLoadError(
                "Structured completion v2 provenance is invalid: "
                f"{coordinate_id}"
            )
        if coordinate_id in records:
            raise TaskCompletionLoadError(
                "Structured completion v2 contains duplicate task coordinate "
                f"{coordinate_id}"
            )
        records[coordinate_id] = _record_v2_from_coordinate(
            coordinate,
            done_at=done_at,
            meta_json=None if row[9] is None else str(row[9]),
            structured_source=source,
        )
    return records


def _validate_structured_legacy_pairs_v1(
    structured: dict[str, TaskCompletionRecordV1],
    legacy: dict[str, TaskCompletionRecordV1],
    *,
    require_equal: bool,
) -> None:
    structured_ids = set(structured)
    legacy_ids = set(legacy)
    if not structured_ids.issubset(legacy_ids):
        raise TaskCompletionLoadError(
            "Structured completion contains coordinates absent from the "
            "compatibility table"
        )
    if require_equal and structured_ids != legacy_ids:
        raise TaskCompletionLoadError(
            "Coordinate-authority structured and compatibility completion "
            f"sets differ: structured={len(structured_ids)}, "
            f"legacy={len(legacy_ids)}"
        )
    for coordinate_id in structured_ids:
        structured_record = structured[coordinate_id]
        legacy_record = legacy[coordinate_id]
        if (
            structured_record.done_at != legacy_record.done_at
            or structured_record.meta_json != legacy_record.meta_json
        ):
            raise TaskCompletionLoadError(
                "Structured and compatibility completion metadata differ: "
                f"{coordinate_id}"
            )


def _record_v1_from_coordinate(
    coordinate: TaskCoordinateV1,
    *,
    done_at: str,
    meta_json: str | None,
    completion_source: TaskCompletionRecordSourceV1,
    structured_source: str | None = None,
) -> TaskCompletionRecordV1:
    return TaskCompletionRecordV1(
        stage=coordinate.stage,
        task_base=coordinate.task_base,
        task_coordinate_id=coordinate.task_coordinate_id,
        identity=tuple(
            (field.key, field.legacy_value)
            for field in coordinate.identity
        ),
        params=tuple(
            (field.key, field.legacy_value)
            for field in coordinate.params
        ),
        legacy_variant_str=coordinate.legacy_variant_str,
        done_at=done_at,
        meta_json=meta_json,
        completion_source=completion_source,
        structured_source=structured_source,
    )


def _record_v2_from_coordinate(
    coordinate: TaskCoordinateV1,
    *,
    done_at: str,
    meta_json: str | None,
    structured_source: str,
) -> TaskCompletionRecordV2:
    return TaskCompletionRecordV2(
        task_coordinate=coordinate,
        done_at=done_at,
        meta_json=meta_json,
        structured_source=structured_source,
    )


_TaskCompletionRecord = TypeVar(
    "_TaskCompletionRecord",
    TaskCompletionRecordV1,
    TaskCompletionRecordV2,
)


def _ordered_records(
    records: Iterable[_TaskCompletionRecord],
) -> tuple[_TaskCompletionRecord, ...]:
    return tuple(
        sorted(
            records,
            key=lambda record: (
                record.done_at,
                record.stage,
                record.task_base,
                record.task_coordinate_id,
            ),
        )
    )


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
