"""task_manage.task_runtime

Runtime state & DONE-only task tracking.

This module is intentionally *not* a dependency planner.

- Keep a small :class:`Plan` (a universe snapshot: which samples/units exist).
- Track DONE-only state in SQLite: existence == DONE, absence == pending.
- Structured task coordinates are the completion authority for new and
  explicitly migrated runs. Legacy aliases remain isolated to versioned
  compatibility surfaces.

Stage/task registry and stage levels come from :mod:`task_manage.define`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import contextvars
import collections.abc
import functools
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Set, Tuple, Callable

from tqdm import tqdm

from logging_config import (
    C_GREEN,
    C_PINK,
    C_RED,
    C_RESET,
    LOG_MAIN,
    LOG_TASK,
    LoggersWrapper,
    get_logger,
    identity_scope,
    suffix_scope,
)

logger_main = get_logger(LOG_MAIN)
logger_task = get_logger(LOG_TASK)

loggers = LoggersWrapper(logger_main, logger_task)

from .cache_epoch_status import (
    PIPELINE_CACHE_EPOCH_COMPLETION_KEY,
    PIPELINE_CACHE_EPOCH_ROOT_KEY,
    build_cache_epoch_completion_marker,
    build_cache_epoch_root_marker,
    require_compatible_run_root,
    require_safe_state_paths,
)
from .cache_epoch import require_current_cache_epoch
from .define import (
    STAGE_TASKS,
    STAGE_LEVEL,
    IMMUTABLE_CONFIG_KEYS,
    Params,
    GLOBAL_META_STAGE,
    GLOBAL_META_TASK,
    StageLevel
)

from dataclasses import dataclass, field
from typing import Any, Optional
from .pipeline_contracts import build_current_task_instance_contract_from_keys_v2
from .reuse_decisions import (
    ReuseDecisionV2,
    decide_reuse_v2_canary_authority,
    decide_reuse_v2_hybrid_gate,
    decide_reuse_v2_observe_only,
    worker_callable_name,
)
from .reuse_authority_audit import ReuseAuthorityAuditV1
from .reuse_decision_storage import (
    append_reuse_authority_decision_v2,
    append_reuse_decision_v3,
    init_reuse_decision_storage_v3,
    require_current_reuse_decision_storage_state_v1,
)
from .reuse_canary_authority import (
    ReuseV2CanaryAuthoritySession,
    ReuseV2CanaryDecision,
    evaluate_canary_required_output_health,
    reuse_v2_authority_adapter_requested,
)
from .reuse_hybrid_gate import evaluate_hybrid_reuse_gate
from .task_instance_v3 import (
    TaskInstanceV3,
    build_task_instance_v3,
    build_task_instance_v3_from_current_contract,
)
from .task_coordinate import (
    TaskCoordinateV1,
    build_task_coordinate_v1,
    build_task_coordinate_v1_from_contract,
)
from .task_coordinate_store import (
    TASK_COORDINATE_AUTHORITY_V1,
    TASK_COORDINATE_DONE_TABLE_V1,
    TaskCoordinatePersistenceError,
    init_task_coordinate_schema_v2,
    load_task_coordinate_state_v1,
    migrate_task_coordinate_done_v1_to_v2,
    task_coordinate_done_v2,
    write_task_coordinate_done_v2,
)
from .task_completion import require_current_task_completion_state_v1
from .task_completion_storage_policy import (
    TASK_COMPLETION_STORAGE_POLICY_V3,
    TASK_COMPLETION_STORAGE_STATE_TABLE_V1,
    init_task_completion_storage_policy_v3,
    load_task_completion_storage_state_for_upgrade_v1,
    load_task_completion_storage_state_v1,
)
from .task_parameter_ledger import (
    TASK_PARAMETER_LEDGER_STORAGE_STATE_TABLE_V1,
    TASK_PARAMETER_LEDGER_TABLE_V1,
    init_task_parameter_ledger_schema_v2,
    init_task_parameter_ledger_storage_policy_v2,
    load_task_parameter_ledger_storage_state_v1,
    migrate_task_parameter_ledger_v1_to_v2,
    require_current_task_parameter_ledger_state_v1,
    write_task_parameter_ledger_v2,
)
from .task_metadata import (
    TaskMetadataCoordinateV1,
    build_task_metadata_coordinate_v1,
    init_task_metadata_storage_v2,
    read_task_metadata_value_v2,
    require_current_task_metadata_storage_state_v1,
    write_task_metadata_value_v2,
)
from .decision_input_manifest import attach_decision_input_manifest_v1
from .inline_execution_observations import (
    InlineExecutionObservationV1,
    attach_inline_input_observation_manifest_v1,
    build_inline_execution_observation_v1,
    new_inline_execution_invocation_id,
)
from .task_specs import TASK_SPECS, TaskSpec, task_specs_by_key, validate_task_specs


# Runtime writes contend with each other and with slow shared disks. A short
# busy timeout produced "database is locked" failures under disk contention
# (fresh-run r3), so wait up to two minutes per attempt and then retry the
# whole transaction a bounded number of times with backoff.
SQLITE_BUSY_TIMEOUT_MS = 120_000
SQLITE_LOCKED_RETRY_DELAYS_S: Tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
_sqlite_retry_sleep = time.sleep


def is_sqlite_locked_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def _materialise_iterator(value: Any) -> Any:
    return list(value) if isinstance(value, collections.abc.Iterator) else value


def retry_on_sqlite_locked(fn: Callable) -> Callable:
    """Retry a self-contained SQLite write transaction on lock errors.

    The wrapped callable must open its own connection and roll back on
    failure, so a retried attempt never sees a half-applied transaction.
    One-shot iterators passed as arguments are materialised once, before the
    first attempt: a failed attempt may already have consumed items, and a
    retry over the same iterator would silently write fewer rows. Writers
    that take nested iterables materialise them before calling a retried
    helper.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        args = tuple(_materialise_iterator(value) for value in args)
        kwargs = {key: _materialise_iterator(value) for key, value in kwargs.items()}
        delays = SQLITE_LOCKED_RETRY_DELAYS_S
        for attempt in range(len(delays) + 1):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if not is_sqlite_locked_error(exc) or attempt >= len(delays):
                    raise
                logger_main.warning(
                    f"SQLite write {fn.__qualname__} hit {exc!s}; retry "
                    f"{attempt + 1}/{len(delays)} in {delays[attempt]:g}s."
                )
                _sqlite_retry_sleep(delays[attempt])

    return wrapper


@dataclass
class TaskManager:
    state_dir: Path
    config: Dict[str, Any]
    db_name: str = "state.sqlite"
    enforce_immutable: bool = True
    cache: Resources = None
    inline_execution_invocation_id: str = field(
        default_factory=new_inline_execution_invocation_id,
        init=False,
        repr=False,
    )
    _inline_execution_observations: Dict[
        str,
        List[InlineExecutionObservationV1],
    ] = field(default_factory=dict, init=False, repr=False)
    reuse_v2_canary_session: ReuseV2CanaryAuthoritySession = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.state_dir = Path(self.state_dir)
        self.reuse_v2_canary_session = ReuseV2CanaryAuthoritySession(
            state_or_db_path=self.db_path,
            invocation_id=self.inline_execution_invocation_id,
        )

    @property
    def db_path(self) -> Path:
        return self.state_dir / self.db_name

    @property
    def store(self) -> SQLiteStore:
        return SQLiteStore(self.db_path)

    def attach_inline_input_observations(
        self,
        task_instance,
    ):
        """Attach current-process inline references without affecting runtime."""

        try:
            return attach_inline_input_observation_manifest_v1(
                task_instance,
                invocation_id=self.inline_execution_invocation_id,
                observations_by_artifact=self._inline_execution_observations,
            )
        except Exception as exc:
            loggers.warning(
                "Could not attach inline execution observation diagnostics "
                f"for {task_instance.key}: {type(exc).__name__}: {exc}"
            )
            return task_instance

    def record_inline_execution(
        self,
        *,
        stage: str,
        task_base: str,
        item: Any,
        fn: Callable[..., Any],
    ) -> InlineExecutionObservationV1 | None:
        """Persist a best-effort completion event after an inline call succeeds."""

        try:
            spec = task_specs_by_key()[(stage, task_base)]
            if spec.execution_mode != "inline":
                raise ValueError(
                    f"Task {(stage, task_base)!r} is not declared inline"
                )
            task_instance = self.attach_inline_input_observations(
                attach_decision_input_manifest_v1(
                    build_task_instance_v3(spec, item),
                    self.config,
                )
            )
            observation = build_inline_execution_observation_v1(
                task_instance,
                invocation_id=self.inline_execution_invocation_id,
                worker_callable=worker_callable_name(fn),
            )
            observation = observation.with_observation_id(
                self.store.insert_inline_execution_observation_v1(
                    observation
                )
            )
            for artifact_key in observation.produced_artifacts:
                self._inline_execution_observations.setdefault(
                    artifact_key,
                    [],
                ).append(observation)
            return observation
        except Exception as exc:
            loggers.warning(
                "Could not persist inline execution observation "
                f"for {stage}:{task_base}: {type(exc).__name__}: {exc}"
            )
            return None

    def run_inline_execution(
        self,
        *,
        stage: str,
        task_base: str,
        item: Any,
        fn: Callable[..., Any],
        call_args: Tuple[Any, ...] = (),
        call_kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        """Run inline work, then record completion only after success."""

        result = fn(*call_args, **dict(call_kwargs or {}))
        self.record_inline_execution(
            stage=stage,
            task_base=task_base,
            item=item,
            fn=fn,
        )
        return result

    def load_or_init(self, plan: Plan, *, sample_inputs: Optional[Dict[str, Any]] = None) -> None:
        """Initialize schema, enforce immutable config, upsert universe."""
        st = self.store
        # Read-only checks before any write: the completion-storage structure
        # first, then the cache generation (unknown/future epoch or state that
        # cannot be verified must not be resumed; see cache_epoch.py).
        require_safe_state_paths(self.db_path)
        require_current_task_completion_state_v1(self.db_path)
        require_compatible_run_root(self.db_path)
        st.init_schema()
        st.init_flex_schema()
        st.init_ledger_schema()
        validate_task_specs()

        immutable_cfg = select_immutable_config(self.config)
        immutable_hash = stable_hash(immutable_cfg)

        old_hash = st.meta_get("immutable_config_hash")
        if self.enforce_immutable and old_hash and old_hash != immutable_hash:
            raise RuntimeError(
                "Immutable (structural) config changed for the same state.sqlite.\n"
                f"Old immutable_config_hash={old_hash}\n"
                f"New immutable_config_hash={immutable_hash}\n"
                "Use a new state_dir (new db) or relax IMMUTABLE_CONFIG_KEYS."
            )

        root_marker = build_cache_epoch_root_marker(
            st.meta_get(PIPELINE_CACHE_EPOCH_ROOT_KEY)
        )
        if st.meta_get(PIPELINE_CACHE_EPOCH_ROOT_KEY) != root_marker:
            st.meta_set(PIPELINE_CACHE_EPOCH_ROOT_KEY, root_marker)
        st.meta_set("created_at", st.meta_get("created_at") or utc_now())
        st.meta_set("updated_at", utc_now())
        st.meta_set("immutable_config_hash", immutable_hash)
        st.meta_set("immutable_config_json", json.dumps(immutable_cfg, sort_keys=True))
        st.meta_set("config_json", json.dumps(self.config, sort_keys=True))
        st.upsert_run_config_snapshot("full_config", self.config)
        st.upsert_run_config_snapshot("immutable_config", immutable_cfg)
        st.upsert_task_specs(TASK_SPECS)

        st.upsert_samples(plan.samples, input_json_by_sample=sample_inputs)
        st.upsert_contigs(plan.contigs_by_sample)

    def record_cache_epoch_plan_completion(self) -> str:
        """Durably mark that this invocation finished its whole plan under the current epoch.

        Called only by ``main.py run`` after every stage returned successfully.
        Re-verifies the root first (never overwrites an unknown/unreadable state).
        """
        require_compatible_run_root(self.db_path)
        st = self.store
        value = build_cache_epoch_completion_marker(
            st.meta_get(PIPELINE_CACHE_EPOCH_COMPLETION_KEY),
            invocation_id=self.inline_execution_invocation_id,
        )
        st.meta_set(PIPELINE_CACHE_EPOCH_COMPLETION_KEY, value)
        return value

    def task_meta_get(
            self,
            key: str,
            *,
            stage: str = GLOBAL_META_STAGE,
            task_base: str = GLOBAL_META_TASK,
            sample_id: Optional[str] = None,
            contig: Optional[str] = None,
            context_params: Optional[Params] = None,
            task_params: Optional[Params] = None,
            default: Any = None,
            without_context: Optional[bool] = False
    ) -> Any:
        """
        Read operational meta for a task identity.

        The metadata identity is a structured coordinate built from scope and
        the effective task parameters.
        Returns `default` if missing.
        """
        logger_task.info(f"task_meta querying [{key}] with context=[{context_params}], "
                         f"params=[{task_params}], without_context=[{without_context}]")
        assert_stage_and_task(stage, task_base)
        if without_context:
            context_params = {}
        coordinate = build_task_metadata_coordinate_v1(
            stage=stage,
            task_base=task_base,
            sample_id=sample_id,
            contig=contig,
            context_params=context_params,
            task_params=task_params,
        )

        lvl = stage_level(stage)
        _validate_key_for_level(lvl, sample_id=sample_id, contig=contig, allow_wildcard=False)

        out = self.store.task_meta_get(
            coordinate,
            key=key,
        )
        logger_task.debug(
            "task_meta queried [%s], coordinate_id=[%s]",
            out,
            coordinate.metadata_coordinate_id,
        )
        return default if out is None else out

    def task_meta_set(
            self,
            key: str,
            value: Any,
            *,
            stage: str = GLOBAL_META_STAGE,
            task_base: str = GLOBAL_META_TASK,
            sample_id: Optional[str] = None,
            contig: Optional[str] = None,
            context_params: Optional[Params] = None,
            task_params: Optional[Params] = None,
            without_context: Optional[bool] = False,
    ) -> None:
        """
        Write operational meta for a task identity.

        The metadata identity is a structured coordinate built from scope and
        the effective task parameters.
        """
        logger_task.debug(f"task_meta setting [{key}] with context=[{context_params}], "
                          f"params=[{task_params}], without_context=[{without_context}]")
        assert_stage_and_task(stage, task_base)
        if without_context:
            context_params = {}
        coordinate = build_task_metadata_coordinate_v1(
            stage=stage,
            task_base=task_base,
            sample_id=sample_id,
            contig=contig,
            context_params=self.cache.get("context") if context_params is None else context_params,
            task_params=task_params,
        )

        lvl = stage_level(stage)
        _validate_key_for_level(lvl, sample_id=sample_id, contig=contig, allow_wildcard=False)

        logger_task.debug(
            "task_meta set key [%s], coordinate_id=[%s]",
            key,
            coordinate.metadata_coordinate_id,
        )
        self.store.task_meta_set(
            coordinate,
            key=key,
            value=value,
        )

    def check_task_done(
        self,
        task_coordinate: TaskCoordinateV1,
    ) -> bool:
        """Check current completion by one exact structured coordinate."""

        with self.store.connect() as con:
            state = load_task_coordinate_state_v1(con)
            if state.authority_mode != TASK_COORDINATE_AUTHORITY_V1:
                raise TaskCoordinatePersistenceError(
                    "Current completion lookup requires coordinate_v1 "
                    "authority"
                )
            load_task_completion_storage_state_v1(con)
            return task_coordinate_done_v2(con, task_coordinate)

    @retry_on_sqlite_locked
    def mark_task_done(
        self,
        task_coordinate: TaskCoordinateV1,
        *,
        meta: Any = None,
    ) -> None:
        """Persist completion in current alias-free structured storage."""

        require_current_cache_epoch(
            task_coordinate.params,
            label=f"Current completion {task_coordinate.stage}:{task_coordinate.task_base}",
        )
        now = utc_now()
        meta_json = json.dumps(_jsonify(meta)) if meta else None

        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            try:
                state = load_task_coordinate_state_v1(con)
                if state.authority_mode != TASK_COORDINATE_AUTHORITY_V1:
                    raise TaskCoordinatePersistenceError(
                        "Current completion writes require coordinate_v1 "
                        "authority"
                    )
                load_task_completion_storage_state_v1(con)
                write_task_coordinate_done_v2(
                    con,
                    task_coordinate,
                    done_at=now,
                    meta_json=meta_json,
                    source="runtime",
                )
                con.execute("COMMIT;")
            except Exception:
                if con.in_transaction:
                    con.execute("ROLLBACK;")
                raise


class SQLiteStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        require_safe_state_paths(self.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(
            str(self.db_path),
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        con.execute(f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_MS)};")
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA foreign_keys=ON;")
        return con

    @retry_on_sqlite_locked
    def init_flex_schema(self) -> None:
        """Initialize or transactionally upgrade current completion storage."""
        require_safe_state_paths(self.db_path)
        require_current_task_completion_state_v1(self.db_path)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            try:
                now = utc_now()
                tables_before = {
                    str(row[0])
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                storage_state = (
                    load_task_completion_storage_state_for_upgrade_v1(con)
                    if TASK_COMPLETION_STORAGE_STATE_TABLE_V1 in tables_before
                    else None
                )
                state = init_task_coordinate_schema_v2(con, now=now)
                if state.authority_mode != TASK_COORDINATE_AUTHORITY_V1:
                    raise TaskCoordinatePersistenceError(
                        "Current completion initialization requires "
                        "coordinate_v1 authority"
                    )
                if (
                    storage_state is None
                    or storage_state.policy != TASK_COMPLETION_STORAGE_POLICY_V3
                ):
                    if TASK_COORDINATE_DONE_TABLE_V1 in tables_before:
                        migrate_task_coordinate_done_v1_to_v2(con)
                    elif TASK_COMPLETION_STORAGE_STATE_TABLE_V1 in tables_before:
                        raise TaskCoordinatePersistenceError(
                            "Completion storage policy v2 lacks its v1 DONE table"
                        )
                init_task_completion_storage_policy_v3(con, now=now)
                con.execute("COMMIT;")
            except Exception:
                if con.in_transaction:
                    con.execute("ROLLBACK;")
                raise

    @retry_on_sqlite_locked
    def init_ledger_schema(self) -> None:
        """
        Additive UI/search ledger tables.

        These tables are informational. Resume authority is selected by
        `task_coordinate_state_v1`; completion writes are structured-only.
        """
        require_safe_state_paths(self.db_path)
        require_current_task_completion_state_v1(self.db_path)
        require_current_task_parameter_ledger_state_v1(self.db_path)
        require_current_reuse_decision_storage_state_v1(self.db_path)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            try:
                tables_before = {
                    str(row[0])
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                has_policy = (
                    TASK_PARAMETER_LEDGER_STORAGE_STATE_TABLE_V1
                    in tables_before
                )
                if has_policy:
                    load_task_parameter_ledger_storage_state_v1(con)
                init_task_parameter_ledger_schema_v2(con)
                if (
                    not has_policy
                    and TASK_PARAMETER_LEDGER_TABLE_V1 in tables_before
                ):
                    migrate_task_parameter_ledger_v1_to_v2(con)
                init_task_parameter_ledger_storage_policy_v2(
                    con,
                    now=utc_now(),
                )
                init_reuse_decision_storage_v3(
                    con,
                    now=utc_now(),
                )
                con.execute("COMMIT;")
            except Exception:
                if con.in_transaction:
                    con.execute("ROLLBACK;")
                raise

        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_config_ledger
                (
                    snapshot_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    value_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_spec_registry
                (
                    stage TEXT NOT NULL,
                    task_base TEXT NOT NULL,
                    level TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    identity_keys_json TEXT NOT NULL,
                    param_keys_json TEXT NOT NULL,
                    consumes_json TEXT NOT NULL,
                    produces_json TEXT NOT NULL,
                    worker_arg TEXT,
                    ui_group TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(stage, task_base)
                );

                CREATE TABLE IF NOT EXISTS task_artifact_refs
                (
                    stage TEXT NOT NULL,
                    task_base TEXT NOT NULL,
                    role TEXT NOT NULL,
                    artifact_index INTEGER NOT NULL,
                    artifact_key TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(stage, task_base, role, artifact_index)
                );

                CREATE INDEX IF NOT EXISTS idx_task_artifact_refs_lookup
                    ON task_artifact_refs(role, artifact_key);

                CREATE TABLE IF NOT EXISTS inline_execution_observations_v1
                (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    observation_token TEXT NOT NULL UNIQUE,
                    stage TEXT NOT NULL,
                    task_base TEXT NOT NULL,
                    producer_instance_id TEXT NOT NULL,
                    contract_hash TEXT NOT NULL,
                    reuse_policy_version TEXT NOT NULL,
                    implementation_version TEXT NOT NULL,
                    produced_artifacts_json TEXT NOT NULL,
                    worker_callable TEXT NOT NULL DEFAULT '',
                    instance_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_inline_execution_observations_v1_lookup
                    ON inline_execution_observations_v1(
                        stage, task_base, producer_instance_id, observation_id
                    );
                """
            )

    @retry_on_sqlite_locked
    def upsert_run_config_snapshot(self, snapshot_key: str, value: Any) -> None:
        now = utc_now()
        normalized = _jsonify(value)
        blob = json.dumps(normalized, sort_keys=True)
        value_hash = stable_json_hash(normalized)
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO run_config_ledger(snapshot_key, value_json, value_hash, updated_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(snapshot_key) DO UPDATE SET
                    value_json=excluded.value_json,
                    value_hash=excluded.value_hash,
                    updated_at=excluded.updated_at
                """,
                (snapshot_key, blob, value_hash, now),
            )

    @retry_on_sqlite_locked
    def upsert_task_specs(self, specs: Iterable[TaskSpec]) -> None:
        now = utc_now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            con.execute("DELETE FROM task_artifact_refs;")
            con.execute("DELETE FROM task_spec_registry;")
            for spec in specs:
                con.execute(
                    """
                    INSERT INTO task_spec_registry(
                        stage, task_base, level, execution_mode,
                        identity_keys_json, param_keys_json, consumes_json, produces_json,
                        worker_arg, ui_group, notes, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        spec.stage,
                        spec.task_base,
                        spec.level,
                        spec.execution_mode,
                        json.dumps(_jsonify(spec.identity_keys), sort_keys=True),
                        json.dumps(_jsonify(spec.param_keys), sort_keys=True),
                        json.dumps(_jsonify(spec.consumes), sort_keys=True),
                        json.dumps(_jsonify(spec.produces), sort_keys=True),
                        spec.worker_arg,
                        spec.ui_group,
                        spec.notes,
                        now,
                    ),
                )
                for role, artifacts in (("consume", spec.consumes), ("produce", spec.produces)):
                    for idx, artifact_key in enumerate(artifacts):
                        con.execute(
                            """
                            INSERT INTO task_artifact_refs(
                                stage, task_base, role, artifact_index, artifact_key, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (spec.stage, spec.task_base, role, idx, artifact_key, now),
                        )
            con.execute("COMMIT;")

    @retry_on_sqlite_locked
    def upsert_task_parameter_ledger(
        self,
        task_coordinate: TaskCoordinateV1,
        *,
        done_known: bool,
        queued: bool,
        force: bool,
    ) -> None:
        """Persist current informational state by exact task coordinate."""

        now = utc_now()
        with self.connect() as con:
            load_task_parameter_ledger_storage_state_v1(con)
            write_task_parameter_ledger_v2(
                con,
                task_coordinate,
                done_known=done_known,
                queued=queued,
                force=force,
                updated_at=now,
            )

    @retry_on_sqlite_locked
    def insert_task_reuse_decision_v2(self, decision: ReuseDecisionV2) -> int:
        """Append one Reuse-v2 domain observation to current v3 storage."""

        now = utc_now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            try:
                decision_id = append_reuse_decision_v3(
                    con,
                    decision,
                    now=now,
                )
                con.execute("COMMIT;")
            except Exception:
                if con.in_transaction:
                    con.execute("ROLLBACK;")
                raise
        return decision_id

    @retry_on_sqlite_locked
    def insert_task_reuse_authority_decision_v1(
        self,
        decision: ReuseDecisionV2,
        audit: ReuseAuthorityAuditV1,
    ) -> Tuple[int, int]:
        """Atomically append a Reuse-v2 decision and audit to current storage."""

        now = utc_now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            try:
                decision_id, authority_audit_id = (
                    append_reuse_authority_decision_v2(
                        con,
                        decision,
                        audit,
                        now=now,
                    )
                )
                con.execute("COMMIT;")
            except Exception:
                try:
                    if con.in_transaction:
                        con.execute("ROLLBACK;")
                except sqlite3.Error:
                    pass
                raise
        return decision_id, authority_audit_id

    @retry_on_sqlite_locked
    def insert_inline_execution_observation_v1(
        self,
        observation: InlineExecutionObservationV1,
    ) -> int:
        """Append one successful inline completion observation."""

        with self.connect() as con:
            cursor = con.execute(
                """
                INSERT INTO inline_execution_observations_v1(
                    schema_version, status, invocation_id, observation_token,
                    stage, task_base, producer_instance_id, contract_hash,
                    reuse_policy_version, implementation_version,
                    produced_artifacts_json, worker_callable, instance_json,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.schema_version,
                    observation.status,
                    observation.invocation_id,
                    observation.observation_token,
                    observation.producer_task_key[0],
                    observation.producer_task_key[1],
                    observation.producer_instance_id,
                    observation.contract_hash,
                    observation.reuse_policy_version,
                    observation.implementation_version,
                    json.dumps(
                        _jsonify(observation.produced_artifacts),
                        sort_keys=True,
                    ),
                    observation.worker_callable,
                    json.dumps(
                        _jsonify(observation.instance_payload),
                        sort_keys=True,
                    ),
                    observation.completed_at,
                ),
            )
            observation_id = cursor.lastrowid
        if observation_id is None:
            raise RuntimeError("Inline execution observation insert returned no ID")
        return int(observation_id)

    @retry_on_sqlite_locked
    def init_schema(self) -> None:
        require_safe_state_paths(self.db_path)
        require_current_task_metadata_storage_state_v1(self.db_path)
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta
                (
                    key
                    TEXT
                    PRIMARY
                    KEY,
                    value
                    TEXT
                    NOT
                    NULL
                );

                CREATE TABLE IF NOT EXISTS samples
                (
                    sample_id
                    TEXT
                    PRIMARY
                    KEY,
                    input_json
                    TEXT,
                    created_at
                    TEXT
                    NOT
                    NULL,
                    updated_at
                    TEXT
                    NOT
                    NULL
                );

                CREATE TABLE IF NOT EXISTS contigs
                (
                    sample_id
                    TEXT
                    NOT
                    NULL,
                    contig
                    TEXT
                    NOT
                    NULL,
                    input_path
                    TEXT,
                    created_at
                    TEXT
                    NOT
                    NULL,
                    updated_at
                    TEXT
                    NOT
                    NULL,
                    PRIMARY
                    KEY
                (
                    sample_id,
                    contig
                ),
                    FOREIGN KEY
                (
                    sample_id
                ) REFERENCES samples
                (
                    sample_id
                ) ON DELETE CASCADE
                    );

                CREATE INDEX IF NOT EXISTS idx_contigs_sample
                    ON contigs(sample_id);
                """
            )
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            try:
                init_task_metadata_storage_v2(con, now=utc_now())
                con.execute("COMMIT;")
            except Exception:
                if con.in_transaction:
                    con.execute("ROLLBACK;")
                raise

    # ---- meta helpers ----
    def meta_get(self, key: str) -> Optional[str]:
        with self.connect() as con:
            row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    @retry_on_sqlite_locked
    def meta_set(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO meta(key, value)
                VALUES (?, ?) ON CONFLICT(key) DO
                UPDATE SET value =excluded.value
                """,
                (key, value),
            )

    def task_meta_get(
            self,
            coordinate: TaskMetadataCoordinateV1,
            *,
            key: str,
    ):
        with self.connect() as con:
            return read_task_metadata_value_v2(
                con,
                coordinate,
                key=key,
            )

    @retry_on_sqlite_locked
    def task_meta_set(
            self,
            coordinate: TaskMetadataCoordinateV1,
            *,
            key: str,
            value,
    ) -> None:
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            try:
                write_task_metadata_value_v2(
                    con,
                    coordinate,
                    key=key,
                    value=value,
                    updated_at=utc_now(),
                )
                con.execute("COMMIT;")
            except Exception:
                if con.in_transaction:
                    con.execute("ROLLBACK;")
                raise

    # ---- universe ----
    def upsert_samples(self, sample_ids: Iterable[str], input_json_by_sample: Optional[Dict[str, Any]] = None) -> None:
        # Materialise before the retried transaction (see retry_on_sqlite_locked).
        self._upsert_samples(list(sample_ids), dict(input_json_by_sample or {}))

    @retry_on_sqlite_locked
    def _upsert_samples(self, sample_ids: List[str], input_json_by_sample: Dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            for sid in sample_ids:
                ij = input_json_by_sample.get(sid)
                ij_str = json.dumps(ij, sort_keys=True) if ij is not None else None
                con.execute(
                    """
                    INSERT INTO samples(sample_id, input_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?) ON CONFLICT(sample_id) DO
                    UPDATE SET
                        input_json=excluded.input_json,
                        updated_at=excluded.updated_at
                    """,
                    (sid, ij_str, now, now),
                )
            con.execute("COMMIT;")

    def upsert_contigs(self, contigs_by_sample: Mapping[str, Iterable[str]]) -> None:
        # Materialise the nested iterables before the retried transaction.
        self._upsert_contigs([(sid, list(contigs)) for sid, contigs in contigs_by_sample.items()])

    @retry_on_sqlite_locked
    def _upsert_contigs(self, contigs_by_sample: List[Tuple[str, List[str]]]) -> None:
        now = utc_now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE;")
            for sid, contigs in contigs_by_sample:
                for ctg in contigs:
                    con.execute(
                        """
                        INSERT INTO contigs(sample_id, contig, input_path, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?) ON CONFLICT(sample_id, contig) DO
                        UPDATE SET
                            updated_at=excluded.updated_at
                        """,
                        (sid, ctg, None, now, now),
                    )
            con.execute("COMMIT;")

@dataclass
class Resources:
    tm: Any
    layout: Any
    args: Any
    _cache: Dict[str, Any] = field(default_factory=dict)
    suffix = " (task_runtime:Resources)"

    def has(self, key) -> bool:
        return key in self._cache

    def get(self, key):
        if key in self._cache:
            return self._cache[key]
        else:
            with suffix_scope(self.suffix):
                loggers.raise_error(f"Uncached key \"{key}\"", KeyError)
            raise  # unreachable; for IDE inspection only.

    def set(self, key, value):
        with suffix_scope(self.suffix):
            if key in self._cache:
                self._cache[key] = value
                loggers.warning(f"Updated existing cache with key \"{key}\".")
            else:
                self._cache[key] = value
                loggers.info(f"Cache updated with key \"{key}\", value type: {type(value).__name__}.")

    def context(self):
        return self._cache["context"]

    def contextualize(self, key, value):
        with suffix_scope(self.suffix):
            if "context" not in self._cache:
                self._cache["context"] = {}
                loggers.info(f"Empty context initialized in cache.")
            new_ctx = dict(self._cache["context"])
            new_ctx.update({key: value})
            self._cache["context"] = new_ctx
            loggers.info(f"Context updated with key {key}={value}.")
            loggers.info(f"Context: {self._cache['context']}.")


@dataclass(frozen=True)
class Plan:
    """Universe snapshot: which sample_ids exist and which unit-contigs per sample exist.

    NOTE: "contigs_by_sample" is used for BOTH pp_unit and unit universes.
    The semantic difference is driven by the stage level (pp_unit vs unit).
    """
    samples: List[str]
    contigs_by_sample: Dict[str, List[str]]


def _auto_workers(max_workers: int | None) -> int:
    if max_workers and max_workers > 0:
        return max_workers
    cpu = os.cpu_count() or 4
    return min(32, max(1, cpu))


def _record_canary_dispatch(
    *,
    tm: TaskManager,
    task_instance: TaskInstanceV3,
    canary_result: ReuseV2CanaryDecision,
    force: bool,
    worker_callable: str,
) -> tuple[bool, dict[str, Any] | None]:
    if canary_result.action is None:
        raise ValueError("Applicable canary decision has no action")
    if canary_result.action == "v2_reuse":
        output_health = evaluate_canary_required_output_health(
            state_or_db_path=tm.db_path,
            config=tm.config,
            task_instance=task_instance,
        )
        if not output_health.allow_reuse:
            failure_reason = (
                "post_reuse_required_output_unhealthy:"
                + "|".join(output_health.reasons)
            )
            canary_result = (
                tm.reuse_v2_canary_session.fallback_after_failure(
                    task_instance,
                    failure_reason,
                )
            )
    decision = decide_reuse_v2_canary_authority(
        task_instance,
        action=canary_result.action,
        queued=not canary_result.allow_reuse,
        force=force,
        authority_reasons=canary_result.reasons,
        worker_callable=worker_callable,
    )
    audit = canary_result.build_audit(
        event_type="dispatch",
        invocation_id=tm.reuse_v2_canary_session.invocation_id,
        task_instance=task_instance,
    )
    try:
        dispatch_decision_id, _ = (
            tm.store.insert_task_reuse_authority_decision_v1(
                decision,
                audit,
            )
        )
    except Exception as exc:
        failure_reason = (
            "candidate_audit_write_failure:"
            f"{type(exc).__name__}:{exc}"
        )
        fallback = tm.reuse_v2_canary_session.fallback_after_failure(
            task_instance,
            failure_reason,
        )
        loggers.warning(
            "Reuse v2 authority audit failed before dispatch; "
            f"preserving legacy DONE for {task_instance.key}: {failure_reason}"
        )
        _record_canary_fallback_best_effort(
            tm=tm,
            task_instance=task_instance,
            canary_result=fallback,
            force=force,
            worker_callable=worker_callable,
        )
        return True, None

    if canary_result.action != "v2_recompute":
        return True, None
    return False, {
        "task_instance": task_instance,
        "canary_result": canary_result,
        "dispatch_decision_id": dispatch_decision_id,
    }


def _record_canary_completion(
    *,
    tm: TaskManager,
    task_instance: TaskInstanceV3,
    canary_result: ReuseV2CanaryDecision,
    dispatch_decision_id: int,
    force: bool,
    worker_callable: str,
) -> None:
    completion = tm.reuse_v2_canary_session.completion_decision(
        canary_result
    )
    decision = decide_reuse_v2_canary_authority(
        task_instance,
        action="v2_recompute_completed",
        queued=False,
        force=force,
        authority_reasons=completion.reasons,
        worker_callable=worker_callable,
    )
    audit = completion.build_audit(
        event_type="completion",
        invocation_id=tm.reuse_v2_canary_session.invocation_id,
        task_instance=task_instance,
        source_decision_id=dispatch_decision_id,
    )
    try:
        tm.store.insert_task_reuse_authority_decision_v1(
            decision,
            audit,
        )
    except Exception as exc:
        failure_reason = (
            "candidate_completion_audit_write_failure:"
            f"{type(exc).__name__}:{exc}"
        )
        fallback = tm.reuse_v2_canary_session.fallback_after_failure(
            task_instance,
            failure_reason,
        )
        loggers.warning(
            "Reuse v2 authority completion audit failed; "
            f"subsequent work uses legacy authority: {failure_reason}"
        )
        _record_canary_fallback_best_effort(
            tm=tm,
            task_instance=task_instance,
            canary_result=fallback,
            force=force,
            worker_callable=worker_callable,
        )
    finally:
        tm.reuse_v2_canary_session.invalidate_after_recompute()


def _record_canary_fallback_best_effort(
    *,
    tm: TaskManager,
    task_instance: TaskInstanceV3,
    canary_result: ReuseV2CanaryDecision,
    force: bool,
    worker_callable: str,
) -> None:
    try:
        tm.store.insert_task_reuse_decision_v2(
            decide_reuse_v2_canary_authority(
                task_instance,
                action="v2_fallback_legacy",
                queued=False,
                force=force,
                authority_reasons=canary_result.reasons,
                worker_callable=worker_callable,
            )
        )
    except Exception as fallback_exc:
        loggers.warning(
            "Could not append Reuse v2 fallback decision after audit "
            f"failure: {type(fallback_exc).__name__}: {fallback_exc}"
        )


def run_flex_batch(
        tm: TaskManager,
        *,
        stage: str,
        task_base: str,
        items: Iterable[Any],  # List[ScItem], or dict. Used for fn, no relation to task records.
        identity_keys: List[str],  # au maximum 5 keys ['sample_id', 'contig']
        param_keys: List[str],  # structured task parameter keys
        fn: Callable[[Any], None],  # fn(sc_obj)
        force: bool = False,
        desc: Optional[str] = None,
        max_workers: Optional[int] = None,
):
    """
    tm: TaskManager,
    stage: str, one of keys.
    task_base: str, one of keys.
    items: Iterable[Any], for example List[ScItem] or dict. Used for fn, no relation to task records.
        identity_keys
    identity_keys: List[str], at most 5 keys. Must be property or key of instances in `items`
    param_keys: List[str], structured task parameter keys
    fn: Callable[[Any], None], for example fn(item: ScItem) or fn(item: Dict). The actual worker function.
    #TODO: force
    force: bool, not used now.
    desc: description for progress bar.
    """
    assert_stage_and_task(stage, task_base)
    tm.store.init_flex_schema()  # 懒加载建表
    tm.store.init_ledger_schema()

    desc = desc or f"{stage}:{task_base}"
    items = list(items)
    scheduled_callable_name = worker_callable_name(fn)
    try:


        work_queue = []
        task_spec = task_specs_by_key().get((stage, task_base))
        contract_identity_keys = tuple(identity_keys)
        contract_param_keys = tuple(param_keys)
        if task_spec is not None:
            # The registry is the fingerprint authority. This also lets
            # additive plumbing fields participate in task identity without
            # requiring algorithm-stage source changes merely to repeat the
            # registry declaration at every run_flex_batch call site.
            undeclared_identity = set(contract_identity_keys) - set(task_spec.identity_keys)
            undeclared_params = set(contract_param_keys) - set(task_spec.param_keys)
            if undeclared_identity or undeclared_params:
                raise ValueError(
                    f"Runtime task keys exceed TaskSpec for {(stage, task_base)!r}: "
                    f"identity={sorted(undeclared_identity)}, params={sorted(undeclared_params)}"
                )
            contract_identity_keys = task_spec.identity_keys
            contract_param_keys = task_spec.param_keys

        for obj in items:
            current_contract = build_current_task_instance_contract_from_keys_v2(
                stage=stage,
                task_base=task_base,
                item=obj,
                identity_keys=contract_identity_keys,
                param_keys=contract_param_keys,
                level=stage_level(stage),
            )
            id_vals = list(current_contract.identity_values)
            task_coordinate = build_task_coordinate_v1_from_contract(
                current_contract
            )

            # 3. check sql database.
            done_known = False
            reuse_decision_recorded = False
            canary_queue_context = None
            if not force:
                done_known = tm.check_task_done(task_coordinate)
                if done_known:
                    should_reuse_legacy_done = True
                    if task_spec is not None:
                        task_instance_v3 = tm.attach_inline_input_observations(
                            attach_decision_input_manifest_v1(
                                build_task_instance_v3_from_current_contract(
                                    spec=task_spec,
                                    current_contract=current_contract,
                                ),
                                tm.config,
                            )
                        )
                        if reuse_v2_authority_adapter_requested():
                            canary_result = (
                                tm.reuse_v2_canary_session.evaluate(
                                    task_instance_v3
                                )
                            )
                            if canary_result.applies:
                                (
                                    should_reuse_legacy_done,
                                    canary_queue_context,
                                ) = _record_canary_dispatch(
                                    tm=tm,
                                    task_instance=task_instance_v3,
                                    canary_result=canary_result,
                                    force=force,
                                    worker_callable=scheduled_callable_name,
                                )
                                reuse_decision_recorded = True
                            else:
                                tm.store.insert_task_reuse_decision_v2(
                                    decide_reuse_v2_observe_only(
                                        task_instance_v3,
                                        legacy_done=True,
                                        force=force,
                                        queued=False,
                                        worker_callable=scheduled_callable_name,
                                    )
                                )
                                reuse_decision_recorded = True
                        else:
                            hybrid_gate = evaluate_hybrid_reuse_gate(
                                state_or_db_path=str(tm.db_path),
                                config=tm.config,
                                task_coordinate=task_coordinate,
                            )
                            if hybrid_gate.checked:
                                should_reuse_legacy_done = hybrid_gate.allow_reuse
                                decision = decide_reuse_v2_hybrid_gate(
                                    task_instance_v3,
                                    action="hybrid_reuse" if hybrid_gate.allow_reuse else "hybrid_recompute",
                                    queued=not hybrid_gate.allow_reuse,
                                    force=force,
                                    gate_reasons=hybrid_gate.reasons,
                                    worker_callable=scheduled_callable_name,
                                )
                            else:
                                decision = decide_reuse_v2_observe_only(
                                    task_instance_v3,
                                    legacy_done=True,
                                    force=force,
                                    queued=False,
                                    worker_callable=scheduled_callable_name,
                                )
                            tm.store.insert_task_reuse_decision_v2(
                                decision
                            )
                            reuse_decision_recorded = True
                    if not should_reuse_legacy_done:
                        done_known = True
                    else:
                        tm.store.upsert_task_parameter_ledger(
                            task_coordinate,
                            done_known=True,
                            queued=False,
                            force=force,
                        )
                        continue
                if done_known and task_spec is None:
                    tm.store.upsert_task_parameter_ledger(
                        task_coordinate,
                        done_known=True,
                        queued=False,
                        force=force,
                    )
                    continue

            if task_spec is not None and not reuse_decision_recorded:
                task_instance_v3 = tm.attach_inline_input_observations(
                    attach_decision_input_manifest_v1(
                        build_task_instance_v3_from_current_contract(
                            spec=task_spec,
                            current_contract=current_contract,
                        ),
                        tm.config,
                    )
                )
                tm.store.insert_task_reuse_decision_v2(
                    decide_reuse_v2_observe_only(
                        task_instance_v3,
                        legacy_done=done_known,
                        force=force,
                        queued=True,
                        worker_callable=scheduled_callable_name,
                    )
                )
            tm.store.upsert_task_parameter_ledger(
                task_coordinate,
                done_known=done_known,
                queued=True,
                force=force,
            )
            work_queue.append({
                "obj": obj,
                "id_vals": id_vals,
                "task_coordinate": task_coordinate,
                "canary_queue_context": canary_queue_context,
            })
        loggers.info(f"Flex Batch [{desc}] "
                     f"{C_PINK}{len(work_queue)}/{len(items)}{C_RESET} tasks queued.",
                     origin=stage, task=task_base, root_color=C_PINK)
        # loggers.info(f"Total: {len(list(items) if isinstance(items, list) else '?')}, Pending: {len(work_queue)}",
        #              origin=stage, task=task_base)
    except Exception as e:
        loggers.raise_error(f"Error in preparing task queue: {e}", origin=stage, task=task_base, root_color=C_RED, task_color=C_RED)

    if not work_queue:
        loggers.info(f"SKIPPED Flex Batch (All Done).", origin=stage, task=task_base, root_color=C_GREEN)
        return

    # 4. execution
    workers = _auto_workers(max_workers)
    loggers.info(f"Flex Batch [{desc}] using {workers} worker(s).", origin=stage, task=task_base)
    errors = []

    def _worker(job):
        with identity_scope(_logging_task_identity(job["task_coordinate"])):
            fn(job["obj"])

            try:
                tm.mark_task_done(job["task_coordinate"])
                tm.store.upsert_task_parameter_ledger(
                    job["task_coordinate"],
                    done_known=True,
                    queued=False,
                    force=force,
                )
            except Exception as exc:
                if job["canary_queue_context"] is not None:
                    tm.reuse_v2_canary_session.activate_rollback(
                        "structured_completion_write_failure:"
                        f"{type(exc).__name__}:{exc}"
                    )
                raise

            if job["canary_queue_context"] is not None:
                _record_canary_completion(
                    tm=tm,
                    task_instance=job["canary_queue_context"]["task_instance"],
                    canary_result=job["canary_queue_context"]["canary_result"],
                    dispatch_decision_id=job["canary_queue_context"][
                        "dispatch_decision_id"
                    ],
                    force=force,
                    worker_callable=scheduled_callable_name,
                )

    with ThreadPoolExecutor(max_workers=workers) as ex:
        # progress bar
        futures = {}
        for job in work_queue:
            # 显示格式: "val1|val2" (例如 "SampleA|chr1")
            name = "|".join(str(v) for v in job["id_vals"] if v)
            if not name: name = "task"
            # Propagate current ContextVars (stage/task/suffix, etc.) into worker threads.
            ctx = contextvars.copy_context()
            futures[ex.submit(ctx.run, _worker, job)] = name

        for fut in tqdm(as_completed(futures), total=len(futures), desc=desc):
            name = futures[fut]
            try:
                fut.result()
            except Exception as e:
                errors.append((name, e))
                loggers.raise_error(f"Failed: {name} -> {e}", origin=stage, task=task_base, root_color=C_RED, task_color=C_RED)

    if errors:
        loggers.raise_error(f"{len(errors)} tasks failed.", RuntimeError, origin=stage, task=task_base, root_color=C_RED, task_color=C_RED)

    loggers.info(f"COMPLETED Flex Batch.", origin=stage, task=task_base, root_color=C_GREEN)


# region ======= helpers =========================================================
def utc_now() -> str:
    """RFC3339-ish UTC timestamp string."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _logging_task_identity(task_coordinate: Any) -> str:
    coordinate_id = str(task_coordinate.task_coordinate_id)[:12]
    fields = ",".join(
        f"{field.key}={field.legacy_value}"
        for field in task_coordinate.identity
    )
    if len(fields) > 180:
        fields = fields[:177] + "..."
    return f"tc={coordinate_id}" + (f";{fields}" if fields else "")


def stable_hash(obj: Mapping[str, Any]) -> str:
    return stable_json_hash(obj)


def stable_json_hash(obj: Any) -> str:
    blob = json.dumps(_jsonify(obj), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def select_immutable_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Pick only keys that define the structural identity of a run."""
    out: Dict[str, Any] = {}
    for k in sorted(IMMUTABLE_CONFIG_KEYS):
        if k in config:
            # Older run snapshots predate the optional contig-scope field.
            # Treat an omitted scope and an explicit empty scope as the same
            # full-genome default, while preserving non-empty scopes as identity.
            if k == "contigs" and not config[k]:
                continue
            out[k] = config[k]
    return out


def _jsonify(x):
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, Path):
        return str(x)
    if isinstance(x, set):
        return sorted(_jsonify(i) for i in x)
    if isinstance(x, tuple):
        return [_jsonify(i) for i in x]
    if isinstance(x, list):
        return [_jsonify(i) for i in x]
    if isinstance(x, dict):
        return {str(k): _jsonify(v) for k, v in x.items()}
    return str(x)


# region ======= internal API ====================================================

def stage_level(stage: str) -> StageLevel:
    if stage not in STAGE_LEVEL:
        raise KeyError(f"Unknown stage '{stage}'. Add it to STAGE_LEVEL in define.py.")
    return STAGE_LEVEL[stage]


def assert_stage_and_task(stage: str, task_base: str) -> None:
    if stage not in STAGE_TASKS:
        raise KeyError(f"Unknown stage '{stage}'. Add it to STAGE_TASKS in define.py.")
    allowed = STAGE_TASKS[stage]
    if task_base not in allowed:
        raise KeyError(
            f"Unknown task_base '{task_base}' for stage '{stage}'. "
            f"Allowed={sorted(allowed)}"
        )


def _validate_key_for_level(
        lvl: str,
        *,
        sample_id: Optional[str],
        contig: Optional[str],
        allow_wildcard: bool,
) -> None:
    """Validate (sample_id, contig) shape for a stage level.

    Rules:
      - global: sample_id=None and contig=None
      - sample: contig must be None; sample_id required unless allow_wildcard=True
      - pp_unit/unit: either both (sample_id, contig) specified, OR both None if allow_wildcard=True
    """
    if lvl == "global":
        if sample_id is not None or contig is not None:
            raise ValueError("Global-level task must not specify sample_id/contig.")
        return

    if lvl == "sample":
        if contig is not None:
            raise ValueError("Sample-level task must not specify contig.")
        if not allow_wildcard and (sample_id is None or sample_id == ""):
            raise ValueError("Sample-level task requires sample_id.")
        return

    # pp_unit / unit
    if allow_wildcard and sample_id is None and contig is None:
        return
    if sample_id is None or sample_id == "" or contig is None or contig == "":
        raise ValueError("Unit-level task requires both sample_id and contig.")

# endregion
