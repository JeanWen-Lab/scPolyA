from __future__ import annotations

import hashlib
import csv
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
from contextlib import closing, contextmanager
from dataclasses import asdict
from itertools import islice
from pathlib import Path
from typing import Any, Iterator

from .bounds import (
    MAX_COMPLETION_BYTES, MAX_COMPLETION_ROWS, MAX_CONFIG_BYTES,
    MAX_IDENTITY_ROWS, MAX_INVOCATION_BYTES, MAX_INVOCATIONS,
    MAX_METADATA_BYTES, MAX_NAMESPACE_ENTRIES, MAX_RESPONSE_BYTES,
    MAX_STATE_ROWS, MAX_STATE_TEXT_BYTES, MAX_TABLE_BYTES,
    bounded_file as _bounded_file, checked_stat, encode_inspection,
    finalize, lexical_path, put_section, read_bounded,
)

INSPECTION_SCHEMA = "scpolya.agent.workspace_inspection.v1"
MAX_STATE_BYTES = 64 * 1024 * 1024


def _issue(result: dict, section: str, code: str, detail: str) -> None:
    if len(result["issues"]) < 24:
        result["issues"].append({"section": section, "code": code, "detail": detail[:160]})
    else:
        result["issues_omitted"] = result.get("issues_omitted", 0) + 1


def _epoch_evidence(db: Path, copied_epoch: dict | None = None) -> dict:
    """A copied DB can block a run; it cannot clear the original filesystem.

    The core reader's run-root scan does not apply to our private DB location.
    Its nonblocking result therefore describes copied state only, including
    when that result is `fresh` or `current`.
    """
    copied = None
    if copied_epoch is not None:
        copied = {key: copied_epoch[key] for key in (
            "status", "current_epoch", "blocks_run", "rebuild_required", "done_counts", "decision_counts"
        )}
        copied.update(error=(copied_epoch.get("error") or "")[:160] or None,
                      notice=(copied_epoch.get("notice") or "")[:160], report_fields="bounded_summary")
        copied.update(assessment_scope="copied_state_only", source_state_db_path=str(db),
                      read_location="private_copy", untracked_path_examples=None)
    blocked = copied is not None and copied["blocks_run"] is True
    return {
        "status": "blocked" if blocked else "unknown",
        "blocks_run": True if blocked else None,
        "assessment_scope": "original_run_root",
        "assessment_complete": False,
        "state_db_path": str(db),
        "current_epoch": copied["current_epoch"] if copied is not None else None,
        "reason": (
            "Copied state supplies a blocking condition; original-root filesystem compatibility was not fully assessed."
            if blocked else
            "Original-root filesystem compatibility was not assessed; a nonblocking or absent copied-state result cannot clear the run root."
        ),
        "copied_state_epoch": copied,
    }


def _error_text(exc: Exception, private: Path | None = None) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return (text.replace(str(private), "<private-state-snapshot>") if private else text)[:160]


def _checkout(repo: Path) -> dict:
    """Read Git provenance with optional index writes disabled; never run a shell."""
    result = {"path": str(repo), "commit": None, "dirty": None, "source": "git"}
    if len(os.fsencode(repo)) > 4096:
        return {"path": None, "commit": None, "dirty": None, "source": "unavailable", "reason": "checkout path byte limit exceeded"}
    try:
        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "--no-optional-locks", "-C", str(repo), *args],
                check=True, capture_output=True, text=True, timeout=5,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            ).stdout.strip()

        result["commit"] = git("rev-parse", "--verify", "HEAD")
        result["dirty"] = bool(git("status", "--porcelain", "--untracked-files=normal"))
    except (OSError, subprocess.SubprocessError) as exc:
        result["source"] = "unavailable"
        result["reason"] = _error_text(exc)
    return result


def _fingerprint(path: Path) -> tuple | None:
    try:
        stamp = path.lstat()
        return stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns
    except FileNotFoundError:
        return None


@contextmanager
def _state_copy(db: Path) -> Iterator[Path]:
    """Copy bounded, quiescent SQLite evidence, including WAL, into private storage.

    Opening the original even with mode=ro can create -shm/-wal files. The
    existing core readers open only this disposable copy. No SQLite connection
    is made to the original. A changing source is rejected, without retry.
    """
    from task_manage.cache_epoch_status import require_safe_state_paths

    for path in (db, *(Path(f"{db}{suffix}") for suffix in ("-wal", "-journal", "-shm"))):
        checked_stat(path, db.parent.parent)
    require_safe_state_paths(db)
    sources = [db, Path(f"{db}-wal"), Path(f"{db}-journal")]
    before = [_fingerprint(path) for path in sources]
    if before[0] is None:
        raise FileNotFoundError(f"state database is missing: {db}")
    if before[2] is not None and before[2][2]:
        raise ValueError("rollback journal present; quiescent state required")
    if sum(item[2] for item in before if item is not None) > MAX_STATE_BYTES:
        raise ValueError(f"state evidence exceeds {MAX_STATE_BYTES} byte limit")
    # Do not put the copy under a run's TMPDIR, even when configured by caller.
    with tempfile.TemporaryDirectory(prefix="scpolya-inspect-", dir="/tmp") as directory:
        private = Path(directory) / "state.sqlite"
        for source, stamp in zip(sources[:2], before[:2]):
            if stamp is not None:
                with source.open("rb") as handle:
                    data = handle.read(stamp[2] + 1)
                if len(data) != stamp[2]:
                    raise ValueError("state size changed while copying")
                target = private if source == db else Path(f"{private}-wal")
                target.write_bytes(data)
        if before != [_fingerprint(path) for path in sources]:
            raise ValueError("state changed while copying; retry after the writer stops")
        yield private


def _config_source(db: Path) -> str:
    with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "run_config_ledger" in tables and con.execute(
            "SELECT 1 FROM run_config_ledger WHERE snapshot_key='full_config'"
        ).fetchone():
            return "run_config_ledger:full_config"
    return "meta:config_json"


def _observe_path(path: Path, root: Path, *, pattern: bool = False) -> dict:
    row: dict[str, Any] = {"path": str(path), "exists": None, "health": "unresolved"}
    if pattern:
        row["reason"] = "pattern not expanded; no recursive artifact search"
        return row
    try:
        stamp = checked_stat(path, root)
        if stamp is None:
            row.update(exists=False, health="missing")
        elif stat.S_ISREG(stamp.st_mode):
            size = stamp.st_size
            row.update(exists=True, kind="file", size_bytes=size,
                       health="nonempty" if size else "empty")
        elif stat.S_ISDIR(stamp.st_mode):
            row.update(exists=True, kind="directory", health="directory_exists")
        else:
            row["reason"] = "unsupported filesystem object"
    except (OSError, ValueError) as exc:
        row["reason"] = _error_text(exc)
    return row


def _invocations(root: Path, result: dict) -> None:
    """Read bounded metadata only; admit complete snapshots into the response."""
    from scpolya_ui.run_data import INVOCATION_LOG_SCHEMA_V1, INVOCATION_LOG_SCHEMA_V2

    directory = root / "logs" / "invocations"
    catalog = {"status": "available", "scope": str(directory), "entries_total": 0,
               "records_returned": 0, "entries_omitted": 0, "snapshots_available": 0,
               "bytes_read": 0, "aggregate_byte_limit": MAX_INVOCATION_BYTES}
    result["invocation_catalog"] = catalog
    try:
        stamp = checked_stat(directory, root)
        if stamp is None:
            catalog["status"] = "missing"
            return
        if not stat.S_ISDIR(stamp.st_mode):
            raise ValueError("invocation metadata directory is not a directory")
        with os.scandir(directory) as entries:
            children = list(islice(entries, MAX_INVOCATIONS + 1))
        if len(children) > MAX_INVOCATIONS:
            catalog.update(status="omitted", entries_total=None, entries_total_lower_bound=len(children),
                           entries_omitted=None, reason="invocation entry limit exceeded; catalog not read")
            _issue(result, "invocations", "entry_budget_exceeded", catalog["reason"])
            return
        catalog["entries_total"] = len(children)
        for child in sorted(children, key=lambda item: item.name):
            if not child.is_dir(follow_symlinks=False):
                if child.is_symlink():
                    _issue(result, "invocations", "unsafe_metadata_path", "symlink invocation directory omitted before descent")
                continue
            path = Path(child.path) / "invocation.json"
            row = {"path": str(path), "schema_version": None,
                   "config_status": "unavailable", "effective_config": None,
                   "historical_results": {"status": "unavailable", "snapshot": None,
                                          "reason": "invocation-bound result snapshots are not persisted"},
                   "task_completion_evidence": False}
            try:
                remaining = MAX_INVOCATION_BYTES - catalog["bytes_read"]
                stamp = _bounded_file(path, root, MAX_METADATA_BYTES)
                if stamp.st_size > remaining:
                    row.update(config_status="omitted", reason="aggregate invocation byte budget exhausted; snapshot not read")
                else:
                    data = read_bounded(path, root, min(remaining, MAX_METADATA_BYTES))
                    catalog["bytes_read"] += len(data)
                    payload = json.loads(data)
                    schema = payload["schema_version"]
                    row["schema_version"] = schema
                    if schema == INVOCATION_LOG_SCHEMA_V1:
                        row["reason"] = "historical CLI record has no verified configuration snapshot"
                    elif schema == INVOCATION_LOG_SCHEMA_V2:
                        config = payload["resolved_config"]
                        if not isinstance(config, dict) or type(payload.get("config_schema_version")) is not int or payload["config_schema_version"] != 1:
                            raise ValueError("unsupported configuration snapshot schema")
                        digest = hashlib.sha256(encode_inspection(config).encode("ascii")).hexdigest()
                        if digest != payload["config_hash"]:
                            raise ValueError("invocation configuration hash mismatch")
                        if lexical_path(payload["run_root"]) != root:
                            raise ValueError("invocation belongs to another run root")
                        if config.get("output_dir") != payload.get("output_dir") or config.get("run_id") != payload.get("run_id"):
                            raise ValueError("invocation config/output identity mismatch")
                        row.update(config_status="available", effective_config=config,
                                   config_source=str(path) + "#resolved_config")
                    else:
                        raise ValueError(f"unsupported invocation schema: {schema!r}")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                row["reason"] = _error_text(exc)
                if "limit" in row["reason"]:
                    row["config_status"] = "omitted"
            if not put_section(result, "invocations", [*result["invocations"], row]):
                row.update(config_status="omitted", effective_config=None,
                           reason="overall response byte budget; entire config snapshot omitted")
                row.pop("config_source", None)
                if not put_section(result, "invocations", [*result["invocations"], row]):
                    catalog["status"] = "partial"
                    continue
            if row["config_status"] != "available":
                _issue(result, "invocations", "snapshot_unavailable", row["reason"])
                catalog["status"] = "partial"
            else:
                catalog["snapshots_available"] += 1
        catalog["records_returned"] = len(result["invocations"])
        catalog["entries_omitted"] = len(children) - catalog["records_returned"]
        if catalog["entries_omitted"]:
            catalog["status"] = "partial"
    except (OSError, ValueError) as exc:
        catalog.update(status="unavailable", entries_total=None, entries_omitted=None, reason=_error_text(exc))
        _issue(result, "invocations", "metadata_unavailable", catalog["reason"])


def _identities(root: Path, config: dict, result: dict) -> None:
    from task_manage.define import UNIT_INPUTS_CSV_NAME
    from task_manage.finalized_unit_inputs import load_finalized_unit_input_table

    method = config.get("dedup_method")
    if not isinstance(method, str) or not method or "/" in method or "\\" in method:
        raise ValueError("persisted config lacks a safe dedup_method; authoritative table cannot be selected")
    path = root / "input_tables" / UNIT_INPUTS_CSV_NAME(method)
    result["identities"]["source"] = str(path)
    data = read_bounded(path, root, MAX_TABLE_BYTES)
    rows = list(islice(csv.DictReader(data.decode("utf-8").splitlines(), strict=True), MAX_IDENTITY_ROWS + 1))
    if len(rows) > MAX_IDENTITY_ROWS:
        result["identities"].update(status="omitted", returned_rows=0, total_rows=None,
                                    total_rows_lower_bound=len(rows), reason="identity row limit exceeded; whole table omitted")
        _issue(result, "identities", "row_budget_exceeded", result["identities"]["reason"])
        return
    _bounded_file(path, root, MAX_TABLE_BYTES)
    loaded = load_finalized_unit_input_table(path)
    if not loaded.is_ready:
        raise ValueError(loaded.detail)
    if any(not row.unit_id.strip() or not row.sample_id.strip() for row in loaded.table.rows):
        raise ValueError("finalized unit table contains blank unit/sample identity")
    section = {"status": "available", "source": str(path), "rows": [asdict(row) for row in loaded.table.rows],
               "returned_rows": len(loaded.table.rows), "total_rows": len(loaded.table.rows)}
    if not put_section(result, "identities", section):
        result["identities"].update(status="omitted", rows=None, returned_rows=0,
                                    total_rows=len(rows), reason="whole identity table omitted by response byte budget")
        _issue(result, "identities", "response_budget_exceeded", result["identities"]["reason"])


def _namespace_preflight(root: Path, layout: Any) -> int:
    """Bound the exact shallow namespaces the native naming reader may list."""
    manifest = layout.ArtifactNameManifest
    if checked_stat(manifest, root) is not None:
        _bounded_file(manifest, root, MAX_METADATA_BYTES)
    count = 0
    for path in (
        layout.FinalOutput, layout.AnnotatedBamsTemp, layout.AnnotatedBamsOutputs,
        layout.PeakSidecarsOutputs, layout.CountMatrixOutputs, layout.CountMatrixTempOutputs,
        layout.MatrixSummaryOutputs, layout.RnaGeneMatrixOutputs, layout.RnaGeneMatrixTempOutputs,
    ):
        stamp = checked_stat(path, root)
        if stamp is None:
            continue
        if not stat.S_ISDIR(stamp.st_mode):
            raise ValueError("native namespace root is not a directory")
        with os.scandir(path) as entries:
            for _ in entries:
                count += 1
                if count > MAX_NAMESPACE_ENTRIES:
                    raise ValueError("native namespace entry limit exceeded before projection")
    return count


def _artifacts(root: Path, db: Path, config: dict, result: dict) -> None:
    from task_manage.output_projection import project_run_outputs_v1
    from task_manage.run_layout import RunLayout

    output_dir, run_id = config.get("output_dir"), config.get("run_id")
    if not isinstance(output_dir, str) or not output_dir or not isinstance(run_id, str) or not run_id:
        raise ValueError("persisted config lacks output_dir/run_id; artifact projection unavailable")
    # Validate raw components before normalization or any native filesystem read.
    # Use the same pure constructor/Root rule as project_run_outputs_v1 without
    # rewriting persisted config to fit the explicit root.
    lexical_path(output_dir)  # Reject raw '..' before abspath can hide it.
    if run_id in {".", ".."} or RunLayout.clean_name(run_id) != run_id:
        raise ValueError("persisted run_id would change under native clean_name; artifact projection unavailable")
    layout = RunLayout(out=Path(output_dir).expanduser(), run_id=run_id)
    configured_root = lexical_path(layout.Root)
    if configured_root != root:
        raise ValueError("persisted configuration points at a different root; no historical path rebasing")
    if len(encode_inspection(config)) > 8192 or any(isinstance(value, str) and len(value) > 1024 for value in config.values()):
        raise ValueError("native projection config text budget exceeded; no path construction attempted")
    checked_stat(layout.Root, Path(root.anchor))
    count = _namespace_preflight(root, layout)
    projection = project_run_outputs_v1(config, annotation_schema="recorded")
    section = dict(result["artifacts"], status="available", projection=projection.as_dict(),
                   namespace_entries_checked=count, observations=[
        {"scope_id": scope.scope_id, **_observe_path(Path(scope.path), root, pattern=scope.path_kind == "glob")}
        for scope in projection.scopes
    ])
    # The combined native health API follows targets before returning paths.
    # Withhold all branches until a safe expectation-only interface exists.
    snapshot = result["task_completion"]["snapshot"] if result["task_completion"]["status"] == "available" else None
    records = snapshot["records"] if snapshot is not None else None
    reason = "combined native health reader accesses targets before path validation; health withheld"
    section["completed_task_health"] = {
        "status": "withheld", "reason": reason, "entries": None,
        "done_task_count": snapshot["record_count"] if snapshot is not None else None,
        "unassessed_tasks": [{"task_coordinate_id": row["task_coordinate"]["task_coordinate_id"],
                              "stage": row["task_coordinate"]["stage"],
                              "task_base": row["task_coordinate"]["task_base"], "reason": reason}
                             for row in records] if records is not None else None,
    }
    if not put_section(result, "artifacts", section):
        result["artifacts"].update(status="omitted", reason="artifact projection omitted by response byte budget")
        _issue(result, "artifacts", "response_budget_exceeded", result["artifacts"]["reason"])
        return
    for warning in projection.warnings:
        _issue(result, "artifacts", "projection_warning", warning)
    _issue(result, "artifacts", "health_withheld", reason)


def _table_budget(con: sqlite3.Connection, table: str) -> dict:
    """Measure every stored cell without loading row text into Python."""
    names = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    sizes = ['coalesce(length(cast("' + column.replace('"', '""') + '" AS BLOB)),0)' for column in sorted(names)]
    length = "+".join(sizes) or "0"
    count, size = con.execute(f"SELECT count(*), coalesce(sum({length}),0) FROM {table}").fetchone()
    return {"present": True, "input_rows": count, "stored_cell_bytes": size}


def _state_bounds(db: Path) -> dict:
    """SQL counts/lengths over the private copy, before JSON/record materialization.

    Counts label candidate rows only; they are not completion authority. Native
    readers still validate every admitted completion and epoch record.
    """
    from task_manage.task_coordinate_store import TASK_COORDINATE_DONE_TABLE_V2, TASK_COORDINATE_STATE_TABLE_V1
    from task_manage.task_completion_storage_policy import TASK_COMPLETION_STORAGE_STATE_TABLE_V1

    epoch_tables = ("task_done_coordinate_v1", TASK_COORDINATE_DONE_TABLE_V2, "task_reuse_decisions_v3", "meta")
    completion_tables = (TASK_COORDINATE_STATE_TABLE_V1, TASK_COMPLETION_STORAGE_STATE_TABLE_V1, TASK_COORDINATE_DONE_TABLE_V2)

    with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        measured = {
            table: _table_budget(con, table) if table in tables else
            {"present": False, "input_rows": 0, "stored_cell_bytes": 0}
            for table in dict.fromkeys((*epoch_tables, *completion_tables))
        }
        completion = {table: measured[table] for table in completion_tables}
        total_rows = sum(item["input_rows"] for item in completion.values())
        total_bytes = sum(item["stored_cell_bytes"] for item in completion.values())
        done = measured[TASK_COORDINATE_DONE_TABLE_V2]
        return {
            "epoch_allowed": all(measured[table]["input_rows"] <= MAX_STATE_ROWS and
                                 measured[table]["stored_cell_bytes"] <= MAX_STATE_TEXT_BYTES for table in epoch_tables),
            "completion_allowed": total_rows <= MAX_COMPLETION_ROWS and total_bytes <= MAX_COMPLETION_BYTES,
            "candidate_done_rows": done["input_rows"] if done["present"] else None,
            "completion_input_rows_total": total_rows,
            "completion_stored_cell_bytes_total": total_bytes,
            "completion_tables": completion,
        }


def _config_size(db: Path, source: str) -> int | None:
    with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)) as con:
        if source.startswith("run_config_ledger"):
            row = con.execute("SELECT length(cast(value_json AS BLOB)) FROM run_config_ledger WHERE snapshot_key='full_config'").fetchone()
        else:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row = con.execute("SELECT length(cast(value AS BLOB)) FROM meta WHERE key='config_json'").fetchone() if "meta" in tables else None
        return row[0] if row else None


def inspect_workspace(run_root: str | Path, *, repo_root: str | Path | None = None) -> dict[str, Any]:
    """Inspect an explicit run root without executing tasks or writing to that root.

    repo_root describes the inspected checkout only. Historical product versions
    remain unknown unless the product gains an authenticated persisted version
    contract. JSON-compatible output contains no synthesized missing counters.
    """
    if not os.fspath(run_root) or len(os.fsencode(run_root)) > 4096:
        return {"schema_version": INSPECTION_SCHEMA, "status": "error", "issues": [
            {"section": "run_root", "code": "invalid_run_root", "detail": "explicit root required; path limit is 4096 bytes"}]}
    module_root = Path(__file__).resolve().parents[1]
    try:
        root = lexical_path(run_root)
        repo = lexical_path(repo_root) if repo_root is not None else module_root
    except (OSError, ValueError) as exc:
        return {"schema_version": INSPECTION_SCHEMA, "status": "error", "issues": [
            {"section": "input_path", "code": "invalid_path_representation", "detail": _error_text(exc)}]}
    db = root / "state" / "state.sqlite"
    result: dict[str, Any] = {
        "schema_version": INSPECTION_SCHEMA, "status": "partial", "run_root": str(root),
        "capabilities": {"inspect_context": True, "execute": False, "recommend_parameters": False,
                         "analyze": False, "export_report": False},
        "versions": {"inspector_checkout": _checkout(module_root),
                     "requested_checkout": _checkout(repo) if repo != module_root else None,
                     "run_product": {
            "status": "unavailable", "version": None, "commit": None, "source": None,
            "reason": "existing run schemas do not persist verified product version/commit; checkout is not historical evidence"}},
        "state": {"path": str(db), "status": "unavailable", "read_method": "private bounded copy including WAL; original never opened by SQLite"},
        "configuration": {"status": "unavailable", "source": None, "effective_config": None,
                          "snapshots_returned": 0, "snapshots_total": None, "candidate_snapshots_total": None,
                          "semantics": "persisted full_config only; no current defaults or CLI reconstruction"},
        "cache_epoch": _epoch_evidence(db),
        "task_completion": {"status": "unavailable", "source": str(db), "snapshot": None},
        "identities": {"status": "unavailable", "source": None, "rows": None},
        "artifacts": {"status": "unavailable", "projection": None, "observations": None,
                      "native_locations": None,
                      "projection_basis": "current pure output contract applied to persisted config; path projection is not a historical result snapshot",
                      "completed_task_health": None, "readiness_semantics": "non-following path metadata only; completed-task health withheld; existence never proves task completion"},
        "scientific_quality": {"status": "not_evaluated", "metrics": None},
        "invocations": [], "issues": [],
        "serialization": {"encoding": "ASCII JSON (ensure_ascii=True)", "separators": [",", ":"],
                          "max_bytes_including_cli_newline": MAX_RESPONSE_BYTES},
        "limits": {"invocation_record_bytes": MAX_METADATA_BYTES,
                   "invocation_aggregate_bytes": MAX_INVOCATION_BYTES, "invocation_entries": MAX_INVOCATIONS,
                   "completion_rows": MAX_COMPLETION_ROWS, "completion_text_bytes": MAX_COMPLETION_BYTES,
                   "completion_budget_scope": "aggregate authority + storage-policy + current DONE tables",
                   "identity_rows": MAX_IDENTITY_ROWS, "identity_table_bytes": MAX_TABLE_BYTES,
                   "config_bytes": MAX_CONFIG_BYTES, "namespace_entries": MAX_NAMESPACE_ENTRIES,
                   "epoch_rows_per_table": MAX_STATE_ROWS, "epoch_text_bytes_per_table": MAX_STATE_TEXT_BYTES,
                   "native_projection_config_ascii_bytes": 8192, "native_projection_scalar_chars": 1024},
    }
    _issue(result, "versions", "historical_version_unavailable", result["versions"]["run_product"]["reason"])
    try:
        stamp = checked_stat(root, Path(root.anchor))
        if stamp is None or not stat.S_ISDIR(stamp.st_mode):
            raise ValueError("run root must be an existing non-symlink directory")
        result["run_root"] = str(root)
        db = root / "state" / "state.sqlite"
    except (OSError, ValueError) as exc:
        result["status"] = "error"
        _issue(result, "run_root", "invalid_run_root", _error_text(exc))
        return finalize(result)
    # Fixed directory observations remain useful when state/config is missing.
    # No directory contents are listed, and no producer completion is inferred.
    result["artifacts"]["native_locations"] = [
        _observe_path(root / name, root) for name in (
            "input_tables", "unit_outputs", "cohort_output", "final_output",
            "peak_sidecars", "count_matrix", "matrix_summary", "rna_gene_matrix",
        )
    ]
    try:
        with _state_copy(db) as private:
            from task_manage.cache_epoch_status import inspect_run_root_cache_epoch
            from task_manage.run_config_preview import load_run_config_snapshot
            from task_manage.task_completion import load_task_completion_snapshot_v2

            preflight_error = None
            try:
                bounds = _state_bounds(private)
            except Exception as exc:
                bounds = {"epoch_allowed": False, "completion_allowed": False, "candidate_done_rows": None,
                          "completion_input_rows_total": None, "completion_stored_cell_bytes_total": None,
                          "completion_tables": None}
                preflight_error = _error_text(exc, private)
            epoch_allowed, completion_allowed = bounds["epoch_allowed"], bounds["completion_allowed"]
            completion_rows = bounds["candidate_done_rows"]
            result["state"].update(candidate_completion_rows=completion_rows,
                completion_budget={"allowed": completion_allowed, "scope": "aggregate native completion input tables",
                                   "input_rows_total": bounds["completion_input_rows_total"],
                                   "stored_cell_bytes_total": bounds["completion_stored_cell_bytes_total"],
                                   "tables": bounds["completion_tables"]},
                budget_preflight={"status": "failed" if preflight_error is not None else "available",
                                  "epoch_allowed": epoch_allowed, "completion_allowed": completion_allowed,
                                  "reason": preflight_error})
            epoch = inspect_run_root_cache_epoch(private).as_dict() if epoch_allowed else None
            result["cache_epoch"] = _epoch_evidence(db, epoch)
            _issue(result, "cache_epoch", "original_root_not_fully_assessed",
                   result["cache_epoch"]["reason"])
            result["state"]["status"] = "unreadable" if preflight_error is not None or (epoch is not None and epoch["status"] == "unreadable") else "available"
            if preflight_error is not None:
                _issue(result, "cache_epoch", "epoch_preflight_failed", preflight_error)
            elif epoch is None:
                _issue(result, "cache_epoch", "epoch_budget_exceeded", "copied-state epoch report omitted before native materialization by row/text limits")
            elif epoch["status"] != "current":
                _issue(result, "cache_epoch", "not_current", epoch["notice"] or epoch["status"])
            try:
                if preflight_error is not None:
                    result["task_completion"].update(status="unavailable", records_returned=0,
                        records_total=None, candidate_rows_total=None,
                        reason="state budget preflight failed; native completion loader withheld")
                    _issue(result, "task_completion", "completion_preflight_failed", preflight_error)
                elif not completion_allowed:
                    result["task_completion"].update(status="omitted", records_returned=0,
                        candidate_rows_total=completion_rows, records_total=None,
                        reason="aggregate authority/storage-policy/DONE input budget exceeded; native completion snapshot withheld")
                    _issue(result, "task_completion", "completion_budget_exceeded", result["task_completion"]["reason"])
                else:
                    snapshot = load_task_completion_snapshot_v2(private).as_dict()
                    snapshot["state_db_path"] = str(db)
                    section = dict(result["task_completion"],
                        status="available" if snapshot["completion_source"] == "structured" else "unavailable",
                        snapshot=snapshot, records_total=snapshot["record_count"], records_returned=snapshot["record_count"])
                    if not put_section(result, "task_completion", section):
                        result["task_completion"].update(status="omitted", records_returned=0,
                            records_total=snapshot["record_count"], reason="whole completion snapshot omitted by response byte budget")
                        _issue(result, "task_completion", "response_budget_exceeded", result["task_completion"]["reason"])
                    if snapshot["completion_source"] != "structured":
                        _issue(result, "task_completion", "no_authority", "structured completion authority is unavailable")
            except Exception as exc:
                _issue(result, "task_completion", "completion_unavailable", _error_text(exc, private))
            try:
                source = _config_source(private)
                size = _config_size(private, source)
                result["configuration"]["source"] = f"{db}#{source}"
                result["configuration"]["candidate_snapshots_total"] = 1 if size is not None else None
                if size is not None and size > MAX_CONFIG_BYTES:
                    result["configuration"].update(status="omitted", total_input_bytes=size,
                        reason="config input byte limit exceeded; whole snapshot omitted before JSON loading")
                    _issue(result, "configuration", "config_budget_exceeded", result["configuration"]["reason"])
                    config = None
                else:
                    config = load_run_config_snapshot(private)
                    encode_inspection(config)
                    section = dict(result["configuration"], status="available", effective_config=config,
                                   snapshots_total=1, snapshots_returned=1)
                    if not put_section(result, "configuration", section):
                        result["configuration"].update(status="omitted", snapshots_total=1,
                            reason="whole config snapshot omitted by response byte budget")
                        _issue(result, "configuration", "response_budget_exceeded", result["configuration"]["reason"])
                        config = None
            except Exception as exc:
                _issue(result, "configuration", "snapshot_unavailable", _error_text(exc, private))
                config = None
            if config is not None:
                for section, reader in (("identities", _identities), ("artifacts", _artifacts)):
                    try:
                        if section == "identities":
                            reader(root, config, result)
                        elif epoch is not None and epoch["status"] == "current":
                            reader(root, private, config, result)
                        else:
                            raise ValueError("artifact contract projection withheld for noncurrent/unknown cache epoch")
                    except Exception as exc:
                        detail = _error_text(exc, private)
                        if "limit" in detail or "budget" in detail:
                            result[section].update(status="omitted", reason=detail)
                            if section == "identities":
                                result[section].update(returned_rows=0, total_rows=None)
                        _issue(result, section, "evidence_unavailable", detail)
    except Exception as exc:
        result["state"]["status"] = "missing" if isinstance(exc, FileNotFoundError) else "unreadable"
        _issue(result, "state", "state_unavailable", _error_text(exc))
    try:
        _invocations(root, result)
    except Exception as exc:
        _issue(result, "invocations", "reader_unavailable", _error_text(exc))
    return finalize(result)
