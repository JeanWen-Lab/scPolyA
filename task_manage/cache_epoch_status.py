"""Run-root cache-epoch status: preview notice, startup guard, completion marker.

Bounded backend check (see cache_epoch.py). The inspector reads only the
completion tables, the reuse-decision table and one ``meta`` row of an existing
``state.sqlite`` in SQLite read-only mode, plus run-root file/symlink presence.
It never follows symlinked directories and never creates, migrates or modifies
a state database. (A read-only connection to a WAL-mode database may still
create SQLite's own ``-shm``/``-wal`` sidecar files; database content is
unchanged.)

Upgrade completion is *not* inferred from the observed current-epoch rows: a
run stopped between stages has current rows only for the tasks it reached.
Instead ``main.py run`` records a durable completion marker
(``meta[PIPELINE_CACHE_EPOCH_COMPLETION_KEY]``) only after the whole plan of an
invocation finished successfully under the current epoch
(``record_cache_epoch_plan_completion``). The marker keeps the first and last
completing invocation and a completion count, so its lifecycle is auditable.

Statuses:
- ``no_state``: no state database and no known scientific output file.
- ``fresh``: state exists but holds no completion/decision rows or marker, and
  no known scientific output file exists.
- ``untracked_outputs``: state is absent or fresh but the run root contains a
  file or symlink outside SQLite's own state files. Its cache generation cannot
  be established, so the root is blocked and must remain untouched.
- ``current``: no predecessor evidence and current-generation evidence or a
  current-epoch completion marker exists. (Ordinary failures of later
  current-generation runs are reported in
  ``done_counts["current_incomplete"]``; they are not an upgrade state.)
- ``full_rebuild``: predecessor evidence and no current-generation evidence.
  This is a read-only diagnosis; the run root is blocked and must not be
  upgraded in place.
- ``upgrade_in_progress``: predecessor evidence plus current-generation
  evidence or a current marker. This mixed-generation root is blocked and must
  not be resumed, regardless of the marker.
- ``incompatible``: an unknown epoch (rows or marker), e.g. written by newer
  code. Blocks the run; use a new run root.
- ``unreadable``: the state could not be verified (bad SQLite file, malformed
  completion/decision JSON, malformed marker). Blocks the run; nothing is
  repaired, deleted or ignored automatically.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Tuple

from .cache_epoch import (
    NEW_RUN_ROOT_ADVICE,
    PIPELINE_CACHE_EPOCH,
    PIPELINE_CACHE_EPOCH_KEY,
    CacheEpochCompatibilityError,
    classify_cache_epoch,
    describe_cache_epoch,
)

CacheEpochRunRootStatus = Literal[
    "no_state",
    "fresh",
    "untracked_outputs",
    "current",
    "full_rebuild",
    "upgrade_in_progress",
    "incompatible",
    "unreadable",
]

PIPELINE_CACHE_EPOCH_COMPLETION_KEY = "pipeline_cache_epoch_completion_v1"
PIPELINE_CACHE_EPOCH_ROOT_KEY = "pipeline_cache_epoch_root_v1"
_MARKER_SCHEMA = "scpolya.pipeline_cache_epoch_completion.v1"
_ROOT_MARKER_SCHEMA = "scpolya.pipeline_cache_epoch_root.v1"
_DONE_TABLES = ("task_done_coordinate_v1", "task_done_coordinate_v2")
_DECISION_TABLE = "task_reuse_decisions_v3"
_EXAMPLE_LIMIT = 5

FULL_REBUILD_SCOPE = (
    "every task of every stage in this run root (references, dedup/split, "
    "barcode-suffix rewrite, Poly(A), UMI scaling, merge/cat, coverage/peaks, "
    "annotation/sidecars, unit/sample counts, RNA, summaries); rebuild only in "
    "a new run root, leaving historical in-place outputs untouched"
)

_STATE_DB_FILENAMES = frozenset(
    {"state.sqlite", "state.sqlite-wal", "state.sqlite-shm", "state.sqlite-journal"}
)
_STATE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

UNREADABLE_ADVICE = (
    "The cache generation of this run root cannot be verified, so a run on it "
    "stops before repair, scheduling or any output write. Restore "
    "state/state.sqlite from a backup or a known-good copy, or start a new run "
    "root (a new --output-dir or --run-id). The state is not repaired, deleted "
    "or ignored automatically."
)


class CacheEpochStateUnreadableError(CacheEpochCompatibilityError):
    """The run root's state could not be read well enough to verify its epoch."""


@dataclass(frozen=True)
class RunRootCacheEpochStatus:
    state_db_path: str
    status: CacheEpochRunRootStatus
    current_epoch: str = PIPELINE_CACHE_EPOCH
    done_counts: Mapping[str, int] = field(default_factory=dict)
    decision_counts: Mapping[str, int] = field(default_factory=dict)
    incomplete_task_keys: Tuple[str, ...] = ()
    unknown_examples: Tuple[str, ...] = ()
    untracked_path_examples: Tuple[str, ...] = ()
    completion_marker: Mapping[str, Any] | None = None
    root_marker: Mapping[str, Any] | None = None
    error: str | None = None

    @property
    def rebuild_required(self) -> bool:
        return self.status in {"full_rebuild", "upgrade_in_progress"}

    @property
    def rebuild_task_count(self) -> int:
        """Earlier-generation completions that will not be reused (upper bound of the rebuild)."""

        if self.rebuild_required:
            return int(self.done_counts.get("predecessor", 0))
        return 0

    @property
    def blocks_run(self) -> bool:
        return self.status in {
            "full_rebuild",
            "upgrade_in_progress",
            "untracked_outputs",
            "incompatible",
            "unreadable",
        }

    @property
    def notice(self) -> str:
        """User-facing text; empty when nothing needs to be said."""

        if self.status == "full_rebuild":
            return (
                "Earlier cache generation: this run root was produced by an earlier "
                "scPolyA cache generation "
                f"({self.done_counts.get('predecessor', 0)} completed task instance(s), "
                f"{self.decision_counts.get('predecessor', 0)} reuse decision(s)). "
                "This version will not recompute or "
                "replace products in that historical root. "
                + NEW_RUN_ROOT_ADVICE
            )
        if self.status == "upgrade_in_progress":
            return (
                "Mixed cache generations: this run root contains earlier-generation "
                "history and current-generation evidence "
                f"({self.done_counts.get('current', 0)} task instance(s) completed "
                "under the current generation; "
                f"{self.done_counts.get('predecessor', 0)} earlier-generation "
                "completion(s) are never reused). A current completion marker does "
                "not make mixed history resumable. This version will not continue or "
                "repair that root. "
                + NEW_RUN_ROOT_ADVICE
            )
        if self.status == "untracked_outputs":
            return (
                "Untracked run-root content: this run root has no usable cache-epoch "
                "evidence but contains file(s) or symlink(s) "
                f"({', '.join(self.untracked_path_examples) or 'present'}). "
                "Their generation cannot be verified. "
                + NEW_RUN_ROOT_ADVICE
            )
        if self.status == "incompatible":
            return (
                f"Incompatible run root: {self.done_counts.get('unknown', 0)} "
                f"completion(s) and {self.decision_counts.get('unknown', 0)} "
                "decision(s) carry an unknown cache generation "
                f"({', '.join(self.unknown_examples) or 'unknown'}). "
                + NEW_RUN_ROOT_ADVICE
            )
        if self.status == "unreadable":
            return f"Unreadable run state ({self.error}). " + UNREADABLE_ADVICE
        return ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "state_db_path": self.state_db_path,
            "status": self.status,
            "current_epoch": self.current_epoch,
            "epoch_key": PIPELINE_CACHE_EPOCH_KEY,
            "rebuild_required": self.rebuild_required,
            "rebuild_task_count": self.rebuild_task_count,
            "blocks_run": self.blocks_run,
            "rebuild_scope": FULL_REBUILD_SCOPE if self.rebuild_required else "",
            "done_counts": dict(self.done_counts),
            "decision_counts": dict(self.decision_counts),
            "incomplete_task_keys": list(self.incomplete_task_keys),
            "unknown_examples": list(self.unknown_examples),
            "untracked_path_examples": list(self.untracked_path_examples),
            "completion_marker": None if self.completion_marker is None else dict(self.completion_marker),
            "root_marker": None if self.root_marker is None else dict(self.root_marker),
            "error": self.error,
            "notice": self.notice,
        }


class _Unreadable(ValueError):
    pass


def require_safe_state_paths(state_or_db_path: str | Path) -> Path:
    """Reject state symlinks and orphan sidecars before any SQLite connection."""

    try:
        return _validate_safe_state_paths(state_or_db_path)
    except _Unreadable as exc:
        raise CacheEpochStateUnreadableError(
            f"Unsafe run state path ({exc}). " + UNREADABLE_ADVICE
        ) from exc


def _validate_safe_state_paths(state_or_db_path: str | Path) -> Path:
    db_path = _resolve_db_path(state_or_db_path)
    state_dir = db_path.parent
    run_root = state_dir.parent
    try:
        if run_root.is_symlink():
            raise _Unreadable(f"run root is a symlink: {run_root}")
        if state_dir.is_symlink():
            raise _Unreadable(f"state directory is a symlink: {state_dir}")
        if os.path.lexists(state_dir) and not state_dir.is_dir():
            raise _Unreadable(f"state path is not a directory: {state_dir}")
        if db_path.is_symlink():
            raise _Unreadable(f"state database is a symlink: {db_path}")
        if os.path.lexists(db_path) and not db_path.is_file():
            raise _Unreadable(f"state database is not a regular file: {db_path}")
        sidecars = tuple(Path(f"{db_path}{suffix}") for suffix in _STATE_SIDECAR_SUFFIXES)
        for sidecar in sidecars:
            if sidecar.is_symlink():
                raise _Unreadable(f"SQLite sidecar is a symlink: {sidecar}")
            if os.path.lexists(sidecar) and not sidecar.is_file():
                raise _Unreadable(f"SQLite sidecar is not a regular file: {sidecar}")
        if not os.path.lexists(db_path):
            orphaned = tuple(sidecar for sidecar in sidecars if os.path.lexists(sidecar))
            if orphaned:
                raise _Unreadable(
                    "SQLite sidecar exists without state.sqlite: "
                    + ", ".join(str(path) for path in orphaned)
                )
    except OSError as exc:
        raise _Unreadable(
            f"state-path inspection failed at {exc.filename!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return db_path


def _json_object(raw: Any, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise _Unreadable(f"{label}: malformed JSON ({exc})") from exc
    if not isinstance(value, dict):
        raise _Unreadable(f"{label}: JSON is not an object")
    return value


def _params(payload: Mapping[str, Any], *, label: str) -> list[Any]:
    params = payload.get("params", [])
    if params is None:
        params = []
    if not isinstance(params, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("key"), str) for item in params
    ):
        raise _Unreadable(f"{label}: malformed params")
    return params


def inspect_run_root_cache_epoch(state_or_db_path: str | Path) -> RunRootCacheEpochStatus:
    db_path = _resolve_db_path(state_or_db_path)
    try:
        db_path = _validate_safe_state_paths(state_or_db_path)
        untracked_paths = _untracked_run_root_examples(db_path)
    except _Unreadable as exc:
        return RunRootCacheEpochStatus(
            state_db_path=str(db_path),
            status="unreadable",
            error=str(exc),
        )
    if not db_path.is_file():
        return RunRootCacheEpochStatus(
            state_db_path=str(db_path),
            status="untracked_outputs" if untracked_paths else "no_state",
            untracked_path_examples=untracked_paths,
        )
    done: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    current_done_ids: set[str] = set()
    current_decision_ids: dict[str, str] = {}
    unknown: list[str] = []
    marker: dict[str, Any] | None = None
    root_marker: dict[str, Any] | None = None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tables = {
                row[0]
                for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in _DONE_TABLES:
                if table not in tables:
                    continue
                for coordinate_id, stage, task_base, coordinate_json in con.execute(
                    f"SELECT task_coordinate_id, stage, task_base, coordinate_json FROM {table}"
                ):
                    label = f"{table} {stage}:{task_base} coordinate_json"
                    params = _params(_json_object(coordinate_json, label=label), label=label)
                    kind = classify_cache_epoch(params)
                    done[kind] += 1
                    if kind == "current":
                        current_done_ids.add(str(coordinate_id))
                    elif kind == "unknown" and len(unknown) < _EXAMPLE_LIMIT:
                        unknown.append(
                            f"done {stage}:{task_base} epoch={describe_cache_epoch(params)}"
                        )
            if _DECISION_TABLE in tables:
                for decision_id, coordinate_id, stage, task_base, instance_json in con.execute(
                    "SELECT decision_id, task_coordinate_id, stage, task_base, instance_json "
                    f"FROM {_DECISION_TABLE}"
                ):
                    label = f"decision {decision_id} {stage}:{task_base} instance_json"
                    params = _params(_json_object(instance_json, label=label), label=label)
                    kind = classify_cache_epoch(params)
                    decisions[kind] += 1
                    if kind == "current" and coordinate_id:
                        current_decision_ids[str(coordinate_id)] = f"{stage}:{task_base}"
                    elif kind == "unknown" and len(unknown) < _EXAMPLE_LIMIT:
                        unknown.append(
                            f"decision {decision_id} {stage}:{task_base} "
                            f"epoch={describe_cache_epoch(params)}"
                        )
            if "meta" in tables:
                row = con.execute(
                    "SELECT value FROM meta WHERE key=?", (PIPELINE_CACHE_EPOCH_ROOT_KEY,)
                ).fetchone()
                if row is not None:
                    root_marker = decode_cache_epoch_root_marker(row[0])
                row = con.execute(
                    "SELECT value FROM meta WHERE key=?", (PIPELINE_CACHE_EPOCH_COMPLETION_KEY,)
                ).fetchone()
                if row is not None:
                    marker = decode_cache_epoch_completion_marker(row[0])
        finally:
            con.close()
    except (_Unreadable, sqlite3.Error, OSError) as exc:
        return RunRootCacheEpochStatus(
            state_db_path=str(db_path),
            status="unreadable",
            error=f"{type(exc).__name__}: {exc}" if not isinstance(exc, _Unreadable) else str(exc),
        )
    marker_kind = (
        None if marker is None
        else classify_cache_epoch([(PIPELINE_CACHE_EPOCH_KEY, marker["epoch"])])
    )
    root_marker_kind = (
        None if root_marker is None
        else classify_cache_epoch([(PIPELINE_CACHE_EPOCH_KEY, root_marker["epoch"])])
    )
    if marker_kind == "unknown" and len(unknown) < _EXAMPLE_LIMIT:
        unknown.append(f"completion marker epoch={marker['epoch']!r}")
    if root_marker_kind == "unknown" and len(unknown) < _EXAMPLE_LIMIT:
        unknown.append(f"root marker epoch={root_marker['epoch']!r}")
    # Dispatched current-generation instances without a current completion.
    incomplete = {
        coordinate_id: key
        for coordinate_id, key in current_decision_ids.items()
        if coordinate_id not in current_done_ids
    }
    total = done + decisions
    current_evidence = bool(
        done["current"] or decisions["current"] or root_marker_kind == "current"
    )
    upgrade_completed = marker_kind == "current"
    predecessor_evidence = bool(
        total["predecessor"]
        or marker_kind == "predecessor"
        or root_marker_kind == "predecessor"
    )
    if total["unknown"] or marker_kind == "unknown" or root_marker_kind == "unknown":
        status: CacheEpochRunRootStatus = "incompatible"
    elif predecessor_evidence:
        status = "upgrade_in_progress" if current_evidence or upgrade_completed else "full_rebuild"
    elif current_evidence or upgrade_completed:
        status = "current"
    elif untracked_paths:
        status = "untracked_outputs"
    else:
        status = "fresh"
    return RunRootCacheEpochStatus(
        state_db_path=str(db_path),
        status=status,
        done_counts={**_full_counts(done), "current_incomplete": len(incomplete)},
        decision_counts=_full_counts(decisions),
        incomplete_task_keys=tuple(sorted(set(incomplete.values()))),
        unknown_examples=tuple(unknown),
        untracked_path_examples=untracked_paths,
        completion_marker=marker,
        root_marker=root_marker,
    )


_MARKER_STRING_FIELDS = (
    "first_completed_at",
    "first_invocation_id",
    "last_completed_at",
    "last_invocation_id",
)
_MARKER_FIELDS = frozenset(
    ("schema", "epoch", "completed_invocation_count", *_MARKER_STRING_FIELDS)
)


def decode_cache_epoch_root_marker(raw: Any) -> dict[str, Any]:
    label = f"meta {PIPELINE_CACHE_EPOCH_ROOT_KEY}"
    marker = _json_object(raw, label=label)
    expected_fields = {"schema", "epoch"}
    if set(marker) != expected_fields:
        raise _Unreadable(
            f"{label}: fields are {sorted(marker)}, expected {sorted(expected_fields)}"
        )
    if marker.get("schema") != _ROOT_MARKER_SCHEMA:
        raise _Unreadable(
            f"{label}: schema is {marker.get('schema')!r}, expected {_ROOT_MARKER_SCHEMA!r}"
        )
    epoch = marker.get("epoch")
    if not isinstance(epoch, str) or not epoch:
        raise _Unreadable(f"{label}: epoch must be a non-empty string, got {epoch!r}")
    return marker


def build_cache_epoch_root_marker(previous: str | None) -> str:
    if previous is not None:
        try:
            marker = decode_cache_epoch_root_marker(previous)
        except _Unreadable as exc:
            raise CacheEpochStateUnreadableError(
                f"Unreadable run state ({exc}). " + UNREADABLE_ADVICE
            ) from exc
        if marker["epoch"] != PIPELINE_CACHE_EPOCH:
            raise CacheEpochCompatibilityError(
                f"Existing root marker has epoch {marker['epoch']!r}; "
                f"refusing to overwrite it with {PIPELINE_CACHE_EPOCH!r}. " + NEW_RUN_ROOT_ADVICE
            )
        return previous
    return json.dumps(
        {"schema": _ROOT_MARKER_SCHEMA, "epoch": PIPELINE_CACHE_EPOCH},
        sort_keys=True,
    )


def decode_cache_epoch_completion_marker(raw: Any) -> dict[str, Any]:
    """Strict decoder shared by inspection and marker updates.

    Requires the exact schema and a non-empty string epoch. A marker of a
    foreign epoch is returned as-is (it is classified ``incompatible`` and never
    overwritten; its shape is not this code's to interpret). A current-epoch
    marker must have exactly the documented fields: non-empty string first/last
    timestamps and invocation IDs, and ``completed_invocation_count`` an ``int``
    >= 1 (not bool, float or numeric string). Anything else raises
    ``_Unreadable`` -- nothing is defaulted, coerced or repaired.
    """

    label = f"meta {PIPELINE_CACHE_EPOCH_COMPLETION_KEY}"
    marker = _json_object(raw, label=label)
    if marker.get("schema") != _MARKER_SCHEMA:
        raise _Unreadable(f"{label}: schema is {marker.get('schema')!r}, expected {_MARKER_SCHEMA!r}")
    epoch = marker.get("epoch")
    if not isinstance(epoch, str) or not epoch:
        raise _Unreadable(f"{label}: epoch must be a non-empty string, got {epoch!r}")
    if epoch != PIPELINE_CACHE_EPOCH:
        return marker
    missing = sorted(_MARKER_FIELDS - set(marker))
    extra = sorted(set(marker) - _MARKER_FIELDS)
    if missing or extra:
        raise _Unreadable(f"{label}: missing fields {missing}, unexpected fields {extra}")
    for key in _MARKER_STRING_FIELDS:
        value = marker[key]
        if not isinstance(value, str) or not value.strip():
            raise _Unreadable(f"{label}: {key} must be a non-empty string, got {value!r}")
    count = marker["completed_invocation_count"]
    if type(count) is not int or count < 1:
        raise _Unreadable(
            f"{label}: completed_invocation_count must be an integer >= 1, got {count!r}"
        )
    return marker


def require_compatible_run_root(state_or_db_path: str | Path) -> RunRootCacheEpochStatus:
    """Raise before writes unless the root is new, fresh or wholly current."""

    status = inspect_run_root_cache_epoch(state_or_db_path)
    if status.status == "unreadable":
        raise CacheEpochStateUnreadableError(status.notice)
    if status.blocks_run:
        raise CacheEpochCompatibilityError(status.notice)
    return status


def build_cache_epoch_completion_marker(
    previous: str | None,
    *,
    invocation_id: str,
    now: str | None = None,
) -> str:
    """JSON for the durable completion marker (first/last completion + count)."""

    stamp = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not isinstance(invocation_id, str) or not invocation_id.strip():
        raise ValueError("completion marker requires a non-empty invocation id")
    if previous is not None:
        try:
            prior = decode_cache_epoch_completion_marker(previous)
        except _Unreadable as exc:
            raise CacheEpochStateUnreadableError(
                f"Unreadable run state ({exc}). " + UNREADABLE_ADVICE
            ) from exc
        if prior["epoch"] != PIPELINE_CACHE_EPOCH:
            raise CacheEpochCompatibilityError(
                f"Existing completion marker has epoch {prior['epoch']!r}; "
                f"refusing to overwrite it with {PIPELINE_CACHE_EPOCH!r}. " + NEW_RUN_ROOT_ADVICE
            )
        marker = {
            **prior,
            "last_completed_at": stamp,
            "last_invocation_id": invocation_id,
            "completed_invocation_count": prior["completed_invocation_count"] + 1,
        }
    else:
        marker = {
            "schema": _MARKER_SCHEMA,
            "epoch": PIPELINE_CACHE_EPOCH,
            "first_completed_at": stamp,
            "first_invocation_id": invocation_id,
            "last_completed_at": stamp,
            "last_invocation_id": invocation_id,
            "completed_invocation_count": 1,
        }
    decode_cache_epoch_completion_marker(json.dumps(marker))  # never write a shape we would reject
    return json.dumps(marker, sort_keys=True)


def _full_counts(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter.get(key, 0)) for key in ("current", "predecessor", "unknown")}


def _resolve_db_path(state_or_db_path: str | Path) -> Path:
    """Accept a state.sqlite file, a ``state/`` directory or a run root."""

    path = Path(state_or_db_path)
    if path.name == "state.sqlite":
        return path
    if path.name == "state":
        return path / "state.sqlite"
    if path.is_dir():
        if (path / "state.sqlite").is_file():
            return path / "state.sqlite"
        return path / "state" / "state.sqlite"
    return path


def _untracked_run_root_examples(db_path: Path) -> tuple[str, ...]:
    if db_path.name != "state.sqlite" or db_path.parent.name != "state":
        return ()
    run_root = db_path.parent.parent
    if run_root.is_symlink():
        return (".",)
    if not run_root.is_dir():
        return ()
    examples: list[str] = []
    def raise_walk_error(error: OSError) -> None:
        raise _Unreadable(
            f"run-root scan failed at {error.filename!r}: "
            f"{type(error).__name__}: {error}"
        ) from error

    for directory, directory_names, file_names in os.walk(
        run_root,
        followlinks=False,
        onerror=raise_walk_error,
    ):
        current = Path(directory)
        directory_names.sort()
        file_names.sort()
        for name in tuple(directory_names):
            candidate = current / name
            if not candidate.is_symlink():
                continue
            examples.append(candidate.relative_to(run_root).as_posix())
            directory_names.remove(name)
            if len(examples) >= _EXAMPLE_LIMIT:
                return tuple(examples)
        for name in file_names:
            candidate = current / name
            if current == db_path.parent and name in _STATE_DB_FILENAMES:
                continue
            examples.append(candidate.relative_to(run_root).as_posix())
            if len(examples) >= _EXAMPLE_LIMIT:
                return tuple(examples)
    return tuple(examples)
