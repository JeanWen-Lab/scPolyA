from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal


TASK_COMPLETION_STORAGE_POLICY_SCHEMA_V1 = (
    "scpolya.task_completion_storage_policy.v1"
)
TASK_COMPLETION_STORAGE_POLICY_NAME = "task_completion_storage_policy"
TASK_COMPLETION_STORAGE_POLICY_VERSION_V1 = "1"
TASK_COMPLETION_STORAGE_POLICY_VERSION_V2 = "2"
TASK_COMPLETION_STORAGE_POLICY_VERSION_V3 = "3"
TASK_COMPLETION_STORAGE_STATE_TABLE_V1 = (
    "task_completion_storage_state_v1"
)

TaskCompletionLegacyTableMode = Literal[
    "dual_write",
    "explicit_read_only",
]


class TaskCompletionStoragePolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskCompletionStoragePolicyV1:
    schema_version: str
    policy_name: str
    policy_version: str
    runtime_read_table: str
    runtime_write_tables: tuple[str, ...]
    legacy_table_mode: TaskCompletionLegacyTableMode
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
            "legacy_table_mode": self.legacy_table_mode,
            "historical_read_api": self.historical_read_api,
            "retain_legacy_variant_alias": self.retain_legacy_variant_alias,
            "supersedes": self.supersedes,
        }


@dataclass(frozen=True)
class TaskCompletionStorageStateV1:
    policy: TaskCompletionStoragePolicyV1
    policy_fingerprint: str
    initialized_at: str
    updated_at: str


TASK_COMPLETION_STORAGE_POLICY_V1 = TaskCompletionStoragePolicyV1(
    schema_version=TASK_COMPLETION_STORAGE_POLICY_SCHEMA_V1,
    policy_name=TASK_COMPLETION_STORAGE_POLICY_NAME,
    policy_version=TASK_COMPLETION_STORAGE_POLICY_VERSION_V1,
    runtime_read_table="task_done_coordinate_v1",
    runtime_write_tables=(
        "task_done_coordinate_v1",
        "task_done_flex",
    ),
    legacy_table_mode="dual_write",
    historical_read_api="implicit_compatibility",
    retain_legacy_variant_alias=True,
)

TASK_COMPLETION_STORAGE_POLICY_V2 = TaskCompletionStoragePolicyV1(
    schema_version=TASK_COMPLETION_STORAGE_POLICY_SCHEMA_V1,
    policy_name=TASK_COMPLETION_STORAGE_POLICY_NAME,
    policy_version=TASK_COMPLETION_STORAGE_POLICY_VERSION_V2,
    runtime_read_table="task_done_coordinate_v1",
    runtime_write_tables=("task_done_coordinate_v1",),
    legacy_table_mode="explicit_read_only",
    historical_read_api="load_legacy_task_completion_snapshot_v1",
    retain_legacy_variant_alias=True,
    supersedes=(
        f"{TASK_COMPLETION_STORAGE_POLICY_NAME}:"
        f"{TASK_COMPLETION_STORAGE_POLICY_VERSION_V1}"
    ),
)

TASK_COMPLETION_STORAGE_POLICY_V3 = TaskCompletionStoragePolicyV1(
    schema_version=TASK_COMPLETION_STORAGE_POLICY_SCHEMA_V1,
    policy_name=TASK_COMPLETION_STORAGE_POLICY_NAME,
    policy_version=TASK_COMPLETION_STORAGE_POLICY_VERSION_V3,
    runtime_read_table="task_done_coordinate_v2",
    runtime_write_tables=("task_done_coordinate_v2",),
    legacy_table_mode="explicit_read_only",
    historical_read_api="load_archived_task_completion_snapshot_v1",
    retain_legacy_variant_alias=False,
    supersedes=(
        f"{TASK_COMPLETION_STORAGE_POLICY_NAME}:"
        f"{TASK_COMPLETION_STORAGE_POLICY_VERSION_V2}"
    ),
)


def task_completion_storage_policy_fingerprint(
    policy: TaskCompletionStoragePolicyV1,
) -> str:
    payload = _policy_json(policy)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def init_task_completion_storage_policy_v2(
    con: sqlite3.Connection,
    *,
    now: str,
) -> TaskCompletionStorageStateV1:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TASK_COMPLETION_STORAGE_STATE_TABLE_V1}
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
        FROM {TASK_COMPLETION_STORAGE_STATE_TABLE_V1}
        WHERE singleton_id=1
        """
    ).fetchone()
    if existing is None:
        policy = TASK_COMPLETION_STORAGE_POLICY_V2
        fingerprint = task_completion_storage_policy_fingerprint(policy)
        con.execute(
            f"""
            INSERT INTO {TASK_COMPLETION_STORAGE_STATE_TABLE_V1}(
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
    return _load_task_completion_storage_state_for_policy_v1(
        con,
        TASK_COMPLETION_STORAGE_POLICY_V2,
    )


def init_task_completion_storage_policy_v3(
    con: sqlite3.Connection,
    *,
    now: str,
) -> TaskCompletionStorageStateV1:
    """Install current alias-free storage policy from an exact v2 source."""

    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TASK_COMPLETION_STORAGE_STATE_TABLE_V1}
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
        FROM {TASK_COMPLETION_STORAGE_STATE_TABLE_V1}
        WHERE singleton_id=1
        """
    ).fetchone()
    policy = TASK_COMPLETION_STORAGE_POLICY_V3
    fingerprint = task_completion_storage_policy_fingerprint(policy)
    if existing is None:
        con.execute(
            f"""
            INSERT INTO {TASK_COMPLETION_STORAGE_STATE_TABLE_V1}(
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
    else:
        existing_state = load_task_completion_storage_state_for_upgrade_v1(
            con
        )
        if existing_state.policy == TASK_COMPLETION_STORAGE_POLICY_V2:
            cursor = con.execute(
                f"""
                UPDATE {TASK_COMPLETION_STORAGE_STATE_TABLE_V1}
                SET schema_version=?, policy_name=?, policy_version=?,
                    policy_fingerprint=?, policy_json=?, updated_at=?
                WHERE singleton_id=1
                """,
                (
                    policy.schema_version,
                    policy.policy_name,
                    policy.policy_version,
                    fingerprint,
                    _policy_json(policy),
                    now,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskCompletionStoragePolicyError(
                    "Task completion storage policy v3 switch did not persist"
                )
    return load_task_completion_storage_state_v1(con)


def load_task_completion_storage_state_v1(
    con: sqlite3.Connection,
) -> TaskCompletionStorageStateV1:
    """Load and validate the current policy-v3 singleton."""

    return _load_task_completion_storage_state_for_policy_v1(
        con,
        TASK_COMPLETION_STORAGE_POLICY_V3,
    )


def load_task_completion_storage_state_for_upgrade_v1(
    con: sqlite3.Connection,
) -> TaskCompletionStorageStateV1:
    """Accept only exact policy v2 or v3 state during a runtime upgrade."""

    errors: list[TaskCompletionStoragePolicyError] = []
    for policy in (
        TASK_COMPLETION_STORAGE_POLICY_V3,
        TASK_COMPLETION_STORAGE_POLICY_V2,
    ):
        try:
            return _load_task_completion_storage_state_for_policy_v1(
                con,
                policy,
            )
        except TaskCompletionStoragePolicyError as exc:
            errors.append(exc)
    raise TaskCompletionStoragePolicyError(
        "Task completion storage policy state is neither approved v2 nor v3"
    ) from errors[-1]


def _load_task_completion_storage_state_for_policy_v1(
    con: sqlite3.Connection,
    policy: TaskCompletionStoragePolicyV1,
) -> TaskCompletionStorageStateV1:
    try:
        row = con.execute(
            f"""
            SELECT schema_version, policy_name, policy_version,
                   policy_fingerprint, policy_json,
                   initialized_at, updated_at
            FROM {TASK_COMPLETION_STORAGE_STATE_TABLE_V1}
            WHERE singleton_id=1
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise TaskCompletionStoragePolicyError(
            "Task completion storage policy state is missing or malformed"
        ) from exc
    if row is None:
        raise TaskCompletionStoragePolicyError(
            "Task completion storage policy state has no singleton row"
        )

    expected_fingerprint = task_completion_storage_policy_fingerprint(policy)
    expected = (
        policy.schema_version,
        policy.policy_name,
        policy.policy_version,
        expected_fingerprint,
        _policy_json(policy),
    )
    actual = tuple(str(value or "") for value in row[:5])
    if actual != expected:
        raise TaskCompletionStoragePolicyError(
            "Task completion storage policy state differs from the approved "
            f"policy v{policy.policy_version}"
        )
    initialized_at = str(row[5] or "")
    updated_at = str(row[6] or "")
    if not initialized_at or not updated_at:
        raise TaskCompletionStoragePolicyError(
            "Task completion storage policy timestamps are missing"
        )
    return TaskCompletionStorageStateV1(
        policy=policy,
        policy_fingerprint=expected_fingerprint,
        initialized_at=initialized_at,
        updated_at=updated_at,
    )


def _policy_json(policy: TaskCompletionStoragePolicyV1) -> str:
    return json.dumps(
        policy.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
