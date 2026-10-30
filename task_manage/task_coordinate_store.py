from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from .task_coordinate import (
    TASK_COORDINATE_HASH_ALGORITHM,
    TASK_COORDINATE_SCHEMA_V1,
    TaskCoordinateError,
    TaskCoordinateV1,
    decode_task_coordinate_json_v1,
)


TASK_COORDINATE_STATE_TABLE_V1 = "task_coordinate_state_v1"
TASK_COORDINATE_DONE_TABLE_V1 = "task_done_coordinate_v1"
TASK_COORDINATE_DONE_TABLE_V2 = "task_done_coordinate_v2"
TASK_COORDINATE_AUTHORITY_V1 = "coordinate_v1"
TASK_COORDINATE_LEGACY_COMPATIBILITY = "legacy_compatibility"
TaskCoordinateAuthorityModeV1 = Literal[
    "coordinate_v1",
    "legacy_compatibility",
]


class TaskCoordinatePersistenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskCoordinateStateV1:
    authority_mode: TaskCoordinateAuthorityModeV1
    initialized_at: str
    updated_at: str
    schema_version: str = TASK_COORDINATE_SCHEMA_V1
    hash_algorithm: str = TASK_COORDINATE_HASH_ALGORITHM


def init_task_coordinate_schema_v1(
    con: sqlite3.Connection,
    *,
    now: str,
) -> TaskCoordinateStateV1:
    """Initialize the archived alias-bearing coordinate storage schema."""

    _init_task_coordinate_state_table_v1(con)
    _init_task_coordinate_done_table_v1(con)
    _init_task_coordinate_state_row_v1(con, now=now)
    return load_task_coordinate_state_v1(con)


def init_task_coordinate_schema_v2(
    con: sqlite3.Connection,
    *,
    now: str,
) -> TaskCoordinateStateV1:
    """Initialize current alias-free coordinate completion storage."""

    _init_task_coordinate_state_table_v1(con)
    _init_task_coordinate_done_table_v2(con)
    _init_task_coordinate_state_row_v1(con, now=now)
    return load_task_coordinate_state_v1(con)


def _init_task_coordinate_state_table_v1(
    con: sqlite3.Connection,
) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TASK_COORDINATE_STATE_TABLE_V1}
        (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            schema_version TEXT NOT NULL,
            hash_algorithm TEXT NOT NULL,
            authority_mode TEXT NOT NULL CHECK(
                authority_mode IN (
                    '{TASK_COORDINATE_AUTHORITY_V1}',
                    '{TASK_COORDINATE_LEGACY_COMPATIBILITY}'
                )
            ),
            initialized_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _init_task_coordinate_done_table_v1(
    con: sqlite3.Connection,
) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TASK_COORDINATE_DONE_TABLE_V1}
        (
            task_coordinate_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            stage TEXT NOT NULL,
            task_base TEXT NOT NULL,
            identity_hash TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            coordinate_json TEXT NOT NULL,
            legacy_variant_str TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            done_at TEXT NOT NULL,
            meta_json TEXT,
            UNIQUE(stage, task_base, identity_hash, params_hash)
        )
        """
    )
    con.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_task_done_coordinate_v1_lookup
            ON {TASK_COORDINATE_DONE_TABLE_V1}(
                stage, task_base, task_coordinate_id
            )
        """
    )
    con.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_task_done_coordinate_v1_legacy
            ON {TASK_COORDINATE_DONE_TABLE_V1}(
                stage, task_base, legacy_variant_str
            )
        """
    )


def _init_task_coordinate_done_table_v2(
    con: sqlite3.Connection,
) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TASK_COORDINATE_DONE_TABLE_V2}
        (
            task_coordinate_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            stage TEXT NOT NULL,
            task_base TEXT NOT NULL,
            identity_hash TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            coordinate_json TEXT NOT NULL,
            source TEXT NOT NULL,
            done_at TEXT NOT NULL,
            meta_json TEXT,
            UNIQUE(stage, task_base, identity_hash, params_hash)
        )
        """
    )
    con.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_task_done_coordinate_v2_lookup
            ON {TASK_COORDINATE_DONE_TABLE_V2}(
                stage, task_base, task_coordinate_id
            )
        """
    )


def _init_task_coordinate_state_row_v1(
    con: sqlite3.Connection,
    *,
    now: str,
) -> None:
    existing_state = con.execute(
        f"""
        SELECT 1
        FROM {TASK_COORDINATE_STATE_TABLE_V1}
        WHERE singleton_id=1
        """
    ).fetchone()
    if existing_state is None:
        has_legacy_done = con.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name='task_done_flex'
            """
        ).fetchone() is not None
        done_count = (
            int(
                con.execute(
                    "SELECT COUNT(*) FROM task_done_flex"
                ).fetchone()[0]
            )
            if has_legacy_done
            else 0
        )
        authority_mode = (
            TASK_COORDINATE_AUTHORITY_V1
            if done_count == 0
            else TASK_COORDINATE_LEGACY_COMPATIBILITY
        )
        con.execute(
            f"""
            INSERT OR IGNORE INTO {TASK_COORDINATE_STATE_TABLE_V1}(
                singleton_id, schema_version, hash_algorithm,
                authority_mode, initialized_at, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?)
            """,
            (
                TASK_COORDINATE_SCHEMA_V1,
                TASK_COORDINATE_HASH_ALGORITHM,
                authority_mode,
                now,
                now,
            ),
        )


def ensure_task_coordinate_ledger_columns_v1(
    con: sqlite3.Connection,
) -> None:
    tables = {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "task_param_ledger" in tables:
        columns = {
            str(row[1])
            for row in con.execute(
                "PRAGMA table_info(task_param_ledger)"
            ).fetchall()
        }
        if "task_coordinate_id" not in columns:
            con.execute(
                """
                ALTER TABLE task_param_ledger
                ADD COLUMN task_coordinate_id TEXT NOT NULL DEFAULT ''
                """
            )
        con.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_task_param_ledger_coordinate_v1
            ON task_param_ledger(stage, task_base, task_coordinate_id)
            WHERE task_coordinate_id <> ''
            """
        )
    if "task_reuse_decisions_v2" in tables:
        columns = {
            str(row[1])
            for row in con.execute(
                "PRAGMA table_info(task_reuse_decisions_v2)"
            ).fetchall()
        }
        if "task_coordinate_id" not in columns:
            con.execute(
                """
                ALTER TABLE task_reuse_decisions_v2
                ADD COLUMN task_coordinate_id TEXT NOT NULL DEFAULT ''
                """
            )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_task_reuse_decisions_v2_coordinate
            ON task_reuse_decisions_v2(
                stage, task_base, task_coordinate_id
            )
            """
        )


def load_task_coordinate_state_v1(
    con: sqlite3.Connection,
) -> TaskCoordinateStateV1:
    try:
        row = con.execute(
            f"""
            SELECT schema_version, hash_algorithm, authority_mode,
                   initialized_at, updated_at
            FROM {TASK_COORDINATE_STATE_TABLE_V1}
            WHERE singleton_id=1
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise TaskCoordinatePersistenceError(
            "Task coordinate state table is malformed"
        ) from exc
    if row is None:
        raise TaskCoordinatePersistenceError(
            "Task coordinate state singleton is missing"
        )
    (
        schema_version,
        hash_algorithm,
        authority_mode,
        initialized_at,
        updated_at,
    ) = (str(value or "") for value in row)
    if schema_version != TASK_COORDINATE_SCHEMA_V1:
        raise TaskCoordinatePersistenceError(
            "Unsupported task coordinate state schema: "
            f"{schema_version!r}"
        )
    if hash_algorithm != TASK_COORDINATE_HASH_ALGORITHM:
        raise TaskCoordinatePersistenceError(
            "Unsupported task coordinate hash algorithm: "
            f"{hash_algorithm!r}"
        )
    if authority_mode not in {
        TASK_COORDINATE_AUTHORITY_V1,
        TASK_COORDINATE_LEGACY_COMPATIBILITY,
    }:
        raise TaskCoordinatePersistenceError(
            f"Invalid task coordinate authority mode: {authority_mode!r}"
        )
    if not initialized_at or not updated_at:
        raise TaskCoordinatePersistenceError(
            "Task coordinate state timestamps are missing"
        )
    return TaskCoordinateStateV1(
        authority_mode=authority_mode,
        initialized_at=initialized_at,
        updated_at=updated_at,
    )


def task_coordinate_done_v1(
    con: sqlite3.Connection,
    coordinate: TaskCoordinateV1,
) -> bool:
    row = con.execute(
        f"""
        SELECT schema_version, stage, task_base, identity_hash,
               params_hash, coordinate_json, legacy_variant_str
        FROM {TASK_COORDINATE_DONE_TABLE_V1}
        WHERE task_coordinate_id=?
        LIMIT 1
        """,
        (coordinate.task_coordinate_id,),
    ).fetchone()
    if row is None:
        return False
    expected = (
        coordinate.schema_version,
        coordinate.stage,
        coordinate.task_base,
        coordinate.identity_hash,
        coordinate.params_hash,
        coordinate.canonical_json,
        coordinate.legacy_variant_str,
    )
    if tuple(str(value) for value in row) != expected:
        raise TaskCoordinatePersistenceError(
            "Stored task coordinate payload does not match its ID: "
            f"{coordinate.task_coordinate_id}"
        )
    return True


def write_task_coordinate_done_v1(
    con: sqlite3.Connection,
    coordinate: TaskCoordinateV1,
    *,
    done_at: str,
    meta_json: str | None,
    source: str,
) -> None:
    if source not in {"runtime", "migration"}:
        raise TaskCoordinatePersistenceError(
            f"Unsupported task coordinate DONE source: {source!r}"
        )
    existing = con.execute(
        f"""
        SELECT schema_version, stage, task_base, identity_hash,
               params_hash, coordinate_json, legacy_variant_str
        FROM {TASK_COORDINATE_DONE_TABLE_V1}
        WHERE task_coordinate_id=?
        """,
        (coordinate.task_coordinate_id,),
    ).fetchone()
    expected = (
        coordinate.schema_version,
        coordinate.stage,
        coordinate.task_base,
        coordinate.identity_hash,
        coordinate.params_hash,
        coordinate.canonical_json,
        coordinate.legacy_variant_str,
    )
    if existing is not None and tuple(str(value) for value in existing) != (
        expected
    ):
        raise TaskCoordinatePersistenceError(
            "Task coordinate ID collision or stored payload mismatch: "
            f"{coordinate.task_coordinate_id}"
        )

    component_match = con.execute(
        f"""
        SELECT task_coordinate_id
        FROM {TASK_COORDINATE_DONE_TABLE_V1}
        WHERE stage=?
          AND task_base=?
          AND identity_hash=?
          AND params_hash=?
        """,
        (
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
        ),
    ).fetchone()
    if (
        component_match is not None
        and str(component_match[0]) != coordinate.task_coordinate_id
    ):
        raise TaskCoordinatePersistenceError(
            "Task coordinate components map to a different stored ID"
        )

    con.execute(
        f"""
        INSERT INTO {TASK_COORDINATE_DONE_TABLE_V1}(
            task_coordinate_id, schema_version, stage, task_base,
            identity_hash, params_hash, coordinate_json,
            legacy_variant_str, source, done_at, meta_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_coordinate_id) DO UPDATE SET
            legacy_variant_str=excluded.legacy_variant_str,
            source=excluded.source,
            done_at=excluded.done_at,
            meta_json=excluded.meta_json
        """,
        (
            coordinate.task_coordinate_id,
            coordinate.schema_version,
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
            coordinate.canonical_json,
            coordinate.legacy_variant_str,
            source,
            str(done_at),
            meta_json,
        ),
    )


def task_coordinate_done_v2(
    con: sqlite3.Connection,
    coordinate: TaskCoordinateV1,
) -> bool:
    row = con.execute(
        f"""
        SELECT schema_version, stage, task_base, identity_hash,
               params_hash, coordinate_json
        FROM {TASK_COORDINATE_DONE_TABLE_V2}
        WHERE task_coordinate_id=?
        LIMIT 1
        """,
        (coordinate.task_coordinate_id,),
    ).fetchone()
    if row is None:
        return False
    expected = (
        coordinate.schema_version,
        coordinate.stage,
        coordinate.task_base,
        coordinate.identity_hash,
        coordinate.params_hash,
        coordinate.canonical_json,
    )
    if tuple(str(value) for value in row) != expected:
        raise TaskCoordinatePersistenceError(
            "Stored task coordinate v2 payload does not match its ID: "
            f"{coordinate.task_coordinate_id}"
        )
    return True


def write_task_coordinate_done_v2(
    con: sqlite3.Connection,
    coordinate: TaskCoordinateV1,
    *,
    done_at: str,
    meta_json: str | None,
    source: str,
) -> None:
    if source not in {"runtime", "migration"}:
        raise TaskCoordinatePersistenceError(
            f"Unsupported task coordinate DONE source: {source!r}"
        )
    if not str(done_at):
        raise TaskCoordinatePersistenceError(
            "Task coordinate DONE timestamp must be non-empty"
        )
    existing = con.execute(
        f"""
        SELECT schema_version, stage, task_base, identity_hash,
               params_hash, coordinate_json
        FROM {TASK_COORDINATE_DONE_TABLE_V2}
        WHERE task_coordinate_id=?
        """,
        (coordinate.task_coordinate_id,),
    ).fetchone()
    expected = (
        coordinate.schema_version,
        coordinate.stage,
        coordinate.task_base,
        coordinate.identity_hash,
        coordinate.params_hash,
        coordinate.canonical_json,
    )
    if existing is not None and tuple(str(value) for value in existing) != expected:
        raise TaskCoordinatePersistenceError(
            "Task coordinate ID collision or stored v2 payload mismatch: "
            f"{coordinate.task_coordinate_id}"
        )

    component_match = con.execute(
        f"""
        SELECT task_coordinate_id
        FROM {TASK_COORDINATE_DONE_TABLE_V2}
        WHERE stage=?
          AND task_base=?
          AND identity_hash=?
          AND params_hash=?
        """,
        (
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
        ),
    ).fetchone()
    if (
        component_match is not None
        and str(component_match[0]) != coordinate.task_coordinate_id
    ):
        raise TaskCoordinatePersistenceError(
            "Task coordinate components map to a different stored v2 ID"
        )

    con.execute(
        f"""
        INSERT INTO {TASK_COORDINATE_DONE_TABLE_V2}(
            task_coordinate_id, schema_version, stage, task_base,
            identity_hash, params_hash, coordinate_json,
            source, done_at, meta_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_coordinate_id) DO UPDATE SET
            source=excluded.source,
            done_at=excluded.done_at,
            meta_json=excluded.meta_json
        """,
        (
            coordinate.task_coordinate_id,
            coordinate.schema_version,
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
            coordinate.canonical_json,
            source,
            str(done_at),
            meta_json,
        ),
    )


def migrate_task_coordinate_done_v1_to_v2(
    con: sqlite3.Connection,
) -> int:
    """Copy one exact archived v1 row set into current alias-free storage."""

    tables = {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if TASK_COORDINATE_DONE_TABLE_V1 not in tables:
        raise TaskCoordinatePersistenceError(
            "Completion storage v2 upgrade requires task_done_coordinate_v1"
        )
    if TASK_COORDINATE_DONE_TABLE_V2 not in tables:
        raise TaskCoordinatePersistenceError(
            "Completion storage v2 upgrade target is missing"
        )

    source_rows = con.execute(
        f"""
        SELECT task_coordinate_id, schema_version, stage, task_base,
               identity_hash, params_hash, coordinate_json,
               legacy_variant_str, source, done_at, meta_json
        FROM {TASK_COORDINATE_DONE_TABLE_V1}
        ORDER BY task_coordinate_id
        """
    ).fetchall()
    source_ids: set[str] = set()
    inserted = 0
    for row in source_rows:
        coordinate_id = str(row[0] or "")
        try:
            coordinate = decode_task_coordinate_json_v1(str(row[6] or ""))
        except (TaskCoordinateError, TypeError, ValueError) as exc:
            raise TaskCoordinatePersistenceError(
                "Archived task coordinate v1 payload is malformed: "
                f"{coordinate_id or '<empty>'}"
            ) from exc
        expected_v1 = (
            coordinate.task_coordinate_id,
            coordinate.schema_version,
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
            coordinate.canonical_json,
            coordinate.legacy_variant_str,
        )
        if tuple(str(value or "") for value in row[:8]) != expected_v1:
            raise TaskCoordinatePersistenceError(
                "Archived task coordinate v1 columns differ from payload: "
                f"{coordinate_id or '<empty>'}"
            )
        source = str(row[8] or "")
        done_at = str(row[9] or "")
        if source not in {"runtime", "migration"} or not done_at:
            raise TaskCoordinatePersistenceError(
                "Archived task coordinate v1 provenance is invalid: "
                f"{coordinate_id}"
            )
        if coordinate_id in source_ids:
            raise TaskCoordinatePersistenceError(
                f"Archived task coordinate v1 contains duplicate ID: {coordinate_id}"
            )
        source_ids.add(coordinate_id)

        existing = con.execute(
            f"""
            SELECT task_coordinate_id, schema_version, stage, task_base,
                   identity_hash, params_hash, coordinate_json,
                   source, done_at, meta_json
            FROM {TASK_COORDINATE_DONE_TABLE_V2}
            WHERE task_coordinate_id=?
            """,
            (coordinate_id,),
        ).fetchone()
        expected_v2 = (
            coordinate.task_coordinate_id,
            coordinate.schema_version,
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
            coordinate.canonical_json,
            source,
            done_at,
            None if row[10] is None else str(row[10]),
        )
        if existing is not None:
            actual_v2 = (
                *(str(value or "") for value in existing[:9]),
                None if existing[9] is None else str(existing[9]),
            )
            if actual_v2 != expected_v2:
                raise TaskCoordinatePersistenceError(
                    "Existing task coordinate v2 row differs from archived v1: "
                    f"{coordinate_id}"
                )
            continue
        con.execute(
            f"""
            INSERT INTO {TASK_COORDINATE_DONE_TABLE_V2}(
                task_coordinate_id, schema_version, stage, task_base,
                identity_hash, params_hash, coordinate_json,
                source, done_at, meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            expected_v2,
        )
        inserted += 1

    target_ids = {
        str(row[0])
        for row in con.execute(
            f"SELECT task_coordinate_id FROM {TASK_COORDINATE_DONE_TABLE_V2}"
        ).fetchall()
    }
    if target_ids != source_ids:
        raise TaskCoordinatePersistenceError(
            "Completion storage v2 row set differs from archived v1: "
            f"v1={len(source_ids)}, v2={len(target_ids)}"
        )
    return inserted
