from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from .task_coordinate import (
    TASK_COORDINATE_SCHEMA_V1,
    TaskCoordinateError,
    TaskCoordinateV1,
    build_task_coordinate_v1,
    build_task_coordinate_v1_from_legacy_done,
    decode_legacy_variant_str_v1,
    decode_task_coordinate_json_v1,
)
from .task_specs import task_specs_by_key
from .task_coordinate_store import (
    TASK_COORDINATE_AUTHORITY_V1,
    TASK_COORDINATE_DONE_TABLE_V1,
    TASK_COORDINATE_DONE_TABLE_V2,
    TASK_COORDINATE_LEGACY_COMPATIBILITY,
    TaskCoordinatePersistenceError,
    ensure_task_coordinate_ledger_columns_v1,
    init_task_coordinate_schema_v1,
    load_task_coordinate_state_v1,
    task_coordinate_done_v1,
    task_coordinate_done_v2,
    write_task_coordinate_done_v1,
)
from .task_parameter_ledger import (
    TASK_PARAMETER_LEDGER_TABLE_V1,
    TASK_PARAMETER_LEDGER_TABLE_V2,
    CurrentTaskParameterLedgerRecordV3,
    TaskParameterLedgerError,
    load_current_task_parameter_ledger_records_v3,
)
from .cache_epoch_status import require_safe_state_paths


TASK_COORDINATE_MIGRATION_REPORT_SCHEMA_V1 = (
    "scpolya.task_coordinate_migration_report.v1"
)
TASK_COORDINATE_MIGRATION_APPLY_SCHEMA_V1 = (
    "scpolya.task_coordinate_migration_apply.v1"
)
MigrationFindingLevel = Literal["blocker", "warning"]


class TaskCoordinateMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskCoordinateMigrationFindingV1:
    level: MigrationFindingLevel
    issue_type: str
    detail: str
    rowid: int | None = None
    stage: str = ""
    task_base: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "issue_type": self.issue_type,
            "detail": self.detail,
            "rowid": self.rowid,
            "stage": self.stage,
            "task_base": self.task_base,
        }


@dataclass(frozen=True)
class TaskCoordinateMigrationEntryV1:
    rowid: int
    task_coordinate: TaskCoordinateV1
    done_at: str
    contract_matches: bool

    @property
    def key(self) -> tuple[str, str]:
        return (
            self.task_coordinate.stage,
            self.task_coordinate.task_base,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rowid": self.rowid,
            "done_at": self.done_at,
            "contract_matches": self.contract_matches,
            **self.task_coordinate.as_dict(),
        }


@dataclass(frozen=True)
class TaskCoordinateMigrationReportV1:
    state_db_path: str
    state_available: bool
    source_done_available: bool
    authority_mode: str
    done_row_count: int
    coordinate_count: int
    ledger_available: bool
    ledger_row_count: int
    ledger_done_count: int
    contract_drift_count: int
    entries: tuple[TaskCoordinateMigrationEntryV1, ...]
    findings: tuple[TaskCoordinateMigrationFindingV1, ...]
    schema_version: str = TASK_COORDINATE_MIGRATION_REPORT_SCHEMA_V1

    @property
    def blocker_count(self) -> int:
        return sum(
            finding.level == "blocker"
            for finding in self.findings
        )

    @property
    def warning_count(self) -> int:
        return sum(
            finding.level == "warning"
            for finding in self.findings
        )

    @property
    def ready_to_migrate(self) -> bool:
        return (
            self.state_available
            and self.source_done_available
            and self.done_row_count == self.coordinate_count
            and self.blocker_count == 0
        )

    @property
    def task_counts(self) -> dict[str, int]:
        counts = Counter(
            f"{entry.key[0]}:{entry.key[1]}"
            for entry in self.entries
        )
        return dict(sorted(counts.items()))

    @property
    def finding_counts(self) -> dict[str, int]:
        counts = Counter(
            f"{finding.level}:{finding.issue_type}"
            for finding in self.findings
        )
        return dict(sorted(counts.items()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state_db_path": self.state_db_path,
            "state_available": self.state_available,
            "source_done_available": self.source_done_available,
            "authority_mode": self.authority_mode,
            "done_row_count": self.done_row_count,
            "coordinate_count": self.coordinate_count,
            "ledger_available": self.ledger_available,
            "ledger_row_count": self.ledger_row_count,
            "ledger_done_count": self.ledger_done_count,
            "contract_drift_count": self.contract_drift_count,
            "blocker_count": self.blocker_count,
            "warning_count": self.warning_count,
            "ready_to_migrate": self.ready_to_migrate,
            "task_counts": self.task_counts,
            "finding_counts": self.finding_counts,
            "entries": [entry.as_dict() for entry in self.entries],
            "findings": [
                finding.as_dict()
                for finding in self.findings
            ],
        }


@dataclass(frozen=True)
class TaskCoordinateMigrationApplyResultV1:
    state_db_path: str
    previous_authority_mode: str
    authority_mode: str
    applied: bool
    coordinate_count: int
    inserted_coordinate_count: int
    backfilled_ledger_count: int
    schema_version: str = TASK_COORDINATE_MIGRATION_APPLY_SCHEMA_V1

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state_db_path": self.state_db_path,
            "previous_authority_mode": self.previous_authority_mode,
            "authority_mode": self.authority_mode,
            "applied": self.applied,
            "coordinate_count": self.coordinate_count,
            "inserted_coordinate_count": self.inserted_coordinate_count,
            "backfilled_ledger_count": self.backfilled_ledger_count,
        }


def build_task_coordinate_migration_report_v1(
    state_or_db_path: str | Path,
) -> TaskCoordinateMigrationReportV1:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        return _empty_report(
            db_path,
            TaskCoordinateMigrationFindingV1(
                level="blocker",
                issue_type="state_missing",
                detail="state SQLite file does not exist",
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
            authority_mode = _read_authority_mode(con, tables)
            legacy_done_available = "task_done_flex" in tables
            structured_done_table = (
                TASK_COORDINATE_DONE_TABLE_V2
                if TASK_COORDINATE_DONE_TABLE_V2 in tables
                else TASK_COORDINATE_DONE_TABLE_V1
            )
            structured_done_available = structured_done_table in tables
            if (
                authority_mode != TASK_COORDINATE_AUTHORITY_V1
                and not legacy_done_available
            ):
                return _empty_report(
                    db_path,
                    TaskCoordinateMigrationFindingV1(
                        level="blocker",
                        issue_type="done_table_missing",
                        detail="task_done_flex table does not exist",
                    ),
                    state_available=True,
                )
            done_rows = (
                con.execute(
                    """
                    SELECT rowid, stage, task_base, variant_str,
                           k1, v1, k2, v2, k3, v3, k4, v4, k5, v5,
                           done_at
                    FROM task_done_flex
                    ORDER BY rowid
                    """
                ).fetchall()
                if authority_mode != TASK_COORDINATE_AUTHORITY_V1
                else ()
            )
            if TASK_PARAMETER_LEDGER_TABLE_V2 in tables:
                ledger_records_v2 = (
                    load_current_task_parameter_ledger_records_v3(con)
                )
                ledger_rows = tuple(
                    _legacy_shaped_ledger_row_from_v2(record)
                    for record in ledger_records_v2
                )
                ledger_available = True
            else:
                ledger_available = TASK_PARAMETER_LEDGER_TABLE_V1 in tables
                ledger_columns = (
                    {
                        str(row[1])
                        for row in con.execute(
                            f"PRAGMA table_info({TASK_PARAMETER_LEDGER_TABLE_V1})"
                        ).fetchall()
                    }
                    if ledger_available
                    else set()
                )
                ledger_coordinate_expression = (
                    "task_coordinate_id"
                    if "task_coordinate_id" in ledger_columns
                    else "''"
                )
                ledger_rows = (
                    con.execute(
                        f"""
                        SELECT stage, task_base, variant_str,
                               identity_json, task_params_json,
                               identity_keys_json, param_keys_json,
                               done_known, queued, force,
                               {ledger_coordinate_expression}
                        FROM {TASK_PARAMETER_LEDGER_TABLE_V1}
                        ORDER BY stage, task_base, variant_str, identity_hash
                        """
                    ).fetchall()
                    if ledger_available
                    else ()
                )
            if structured_done_available:
                alias_column = (
                    ", legacy_variant_str"
                    if structured_done_table == TASK_COORDINATE_DONE_TABLE_V1
                    else ""
                )
                structured_done_rows = con.execute(
                    f"""
                    SELECT task_coordinate_id, schema_version,
                           stage, task_base, identity_hash, params_hash,
                           coordinate_json{alias_column}, source, done_at
                    FROM {structured_done_table}
                    ORDER BY task_coordinate_id
                    """
                ).fetchall()
            else:
                structured_done_rows = ()
    except (OSError, sqlite3.Error, TaskParameterLedgerError) as exc:
        return _empty_report(
            db_path,
            TaskCoordinateMigrationFindingV1(
                level="blocker",
                issue_type="state_read_error",
                detail=f"{type(exc).__name__}: {exc}",
            ),
            state_available=True,
        )

    if authority_mode == TASK_COORDINATE_AUTHORITY_V1:
        return _build_coordinate_authority_report_v1(
            db_path=db_path,
            structured_done_available=structured_done_available,
            structured_done_table=structured_done_table,
            structured_done_rows=structured_done_rows,
            ledger_available=ledger_available,
            ledger_rows=ledger_rows,
        )

    findings: list[TaskCoordinateMigrationFindingV1] = []
    entries: list[TaskCoordinateMigrationEntryV1] = []
    coordinate_payloads: dict[str, str] = {}
    coordinate_rowids: dict[str, int] = {}
    specs = task_specs_by_key()
    contract_drift_count = 0

    if authority_mode not in {
        "uninitialized",
        TASK_COORDINATE_LEGACY_COMPATIBILITY,
        TASK_COORDINATE_AUTHORITY_V1,
    }:
        findings.append(
            TaskCoordinateMigrationFindingV1(
                level="blocker",
                issue_type="authority_state_invalid",
                detail=(
                    "task coordinate authority state is malformed or "
                    f"unsupported: {authority_mode!r}"
                ),
            )
        )

    for row in done_rows:
        (
            rowid,
            stage,
            task_base,
            variant_str,
            k1,
            v1,
            k2,
            v2,
            k3,
            v3,
            k4,
            v4,
            k5,
            v5,
            done_at,
        ) = row
        rowid = int(rowid)
        stage = str(stage or "")
        task_base = str(task_base or "")
        try:
            coordinate = build_task_coordinate_v1_from_legacy_done(
                stage=stage,
                task_base=task_base,
                variant_str=str(variant_str or ""),
                identity_keys=(k1, k2, k3, k4, k5),
                identity_values=(v1, v2, v3, v4, v5),
            )
        except (TaskCoordinateError, TypeError, ValueError) as exc:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="legacy_row_malformed",
                    detail=f"{type(exc).__name__}: {exc}",
                    rowid=rowid,
                    stage=stage,
                    task_base=task_base,
                )
            )
            continue

        previous_payload = coordinate_payloads.get(
            coordinate.task_coordinate_id
        )
        if previous_payload is not None:
            issue_type = (
                "coordinate_duplicate"
                if previous_payload == coordinate.canonical_json
                else "coordinate_collision"
            )
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type=issue_type,
                    detail=(
                        "task coordinate already belongs to legacy row "
                        f"{coordinate_rowids[coordinate.task_coordinate_id]}"
                    ),
                    rowid=rowid,
                    stage=stage,
                    task_base=task_base,
                )
            )
        else:
            coordinate_payloads[
                coordinate.task_coordinate_id
            ] = coordinate.canonical_json
            coordinate_rowids[coordinate.task_coordinate_id] = rowid

        spec = specs.get((stage, task_base))
        identity_keys = tuple(coordinate.identity_dict)
        param_keys = tuple(coordinate.params_dict)
        contract_matches = (
            spec is not None
            and set(identity_keys) == set(spec.identity_keys)
            and set(param_keys) == set(spec.param_keys)
        )
        if not contract_matches:
            contract_drift_count += 1
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="contract_drift",
                    detail=(
                        "legacy keys differ from current TaskSpec: "
                        f"identity={identity_keys!r}, params={param_keys!r}, "
                        "current_identity="
                        f"{None if spec is None else spec.identity_keys!r}, "
                        "current_params="
                        f"{None if spec is None else spec.param_keys!r}"
                    ),
                    rowid=rowid,
                    stage=stage,
                    task_base=task_base,
                )
            )
        entries.append(
            TaskCoordinateMigrationEntryV1(
                rowid=rowid,
                task_coordinate=coordinate,
                done_at=str(done_at or ""),
                contract_matches=contract_matches,
            )
        )

    if ledger_available:
        findings.extend(
            _ledger_findings(
                ledger_rows,
                entries,
                authority_mode=authority_mode,
            )
        )
    else:
        findings.append(
            TaskCoordinateMigrationFindingV1(
                level="warning",
                issue_type="ledger_unavailable",
                detail=(
                    "task parameter ledger storage is absent; migration can "
                    "validate DONE rows only"
                ),
            )
        )
    findings.extend(
        _structured_done_findings_v1(
            structured_done_rows,
            entries,
            authority_mode=authority_mode,
            structured_done_available=structured_done_available,
        )
    )

    return TaskCoordinateMigrationReportV1(
        state_db_path=str(db_path),
        state_available=True,
        source_done_available=True,
        authority_mode=authority_mode,
        done_row_count=len(done_rows),
        coordinate_count=len(entries),
        ledger_available=ledger_available,
        ledger_row_count=len(ledger_rows),
        ledger_done_count=sum(bool(row[7]) for row in ledger_rows),
        contract_drift_count=contract_drift_count,
        entries=tuple(entries),
        findings=tuple(findings),
    )


def _build_coordinate_authority_report_v1(
    *,
    db_path: Path,
    structured_done_available: bool,
    structured_done_table: str,
    structured_done_rows: tuple[Any, ...] | list[Any],
    ledger_available: bool,
    ledger_rows: tuple[Any, ...],
) -> TaskCoordinateMigrationReportV1:
    findings: list[TaskCoordinateMigrationFindingV1] = []
    entries: list[TaskCoordinateMigrationEntryV1] = []
    specs = task_specs_by_key()
    seen_ids: set[str] = set()
    contract_drift_count = 0

    if not structured_done_available:
        findings.append(
            TaskCoordinateMigrationFindingV1(
                level="blocker",
                issue_type="structured_done_table_missing",
                detail=(
                    "coordinate authority requires "
                    f"{structured_done_table}"
                ),
            )
        )

    for index, row in enumerate(structured_done_rows, start=1):
        coordinate_id = str(row[0] or "")
        stage = str(row[2] or "")
        task_base = str(row[3] or "")
        try:
            coordinate = decode_task_coordinate_json_v1(str(row[6] or ""))
        except (TaskCoordinateError, TypeError, ValueError) as exc:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="structured_done_malformed",
                    detail=f"{type(exc).__name__}: {exc}",
                    rowid=index,
                    stage=stage,
                    task_base=task_base,
                )
            )
            continue

        expected_payload = (
            coordinate.task_coordinate_id,
            coordinate.schema_version,
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
            coordinate.canonical_json,
        )
        if structured_done_table == TASK_COORDINATE_DONE_TABLE_V1:
            expected_payload = (
                *expected_payload,
                coordinate.legacy_variant_str,
            )
            source_index = 8
            done_at_index = 9
        else:
            source_index = 7
            done_at_index = 8
        if (
            tuple(str(value or "") for value in row[:source_index])
            != expected_payload
        ):
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="structured_done_payload_mismatch",
                    detail=(
                        "structured DONE columns differ from coordinate JSON: "
                        f"{coordinate_id}"
                    ),
                    rowid=index,
                    stage=stage,
                    task_base=task_base,
                )
            )
        if coordinate_id in seen_ids:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="coordinate_duplicate",
                    detail=(
                        "structured DONE contains a duplicate coordinate: "
                        f"{coordinate_id}"
                    ),
                    rowid=index,
                    stage=stage,
                    task_base=task_base,
                )
            )
        seen_ids.add(coordinate_id)
        if (
            str(row[source_index] or "") not in {"runtime", "migration"}
            or not str(row[done_at_index] or "")
        ):
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="structured_done_provenance_invalid",
                    detail=(
                        "structured DONE source or timestamp is invalid: "
                        f"{coordinate_id}"
                    ),
                    rowid=index,
                    stage=stage,
                    task_base=task_base,
                )
            )

        spec = specs.get((coordinate.stage, coordinate.task_base))
        identity_keys = tuple(coordinate.identity_dict)
        param_keys = tuple(coordinate.params_dict)
        contract_matches = (
            spec is not None
            and set(identity_keys) == set(spec.identity_keys)
            and set(param_keys) == set(spec.param_keys)
        )
        if not contract_matches:
            contract_drift_count += 1
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="contract_drift",
                    detail=(
                        "structured keys differ from current TaskSpec: "
                        f"identity={identity_keys!r}, params={param_keys!r}, "
                        "current_identity="
                        f"{None if spec is None else spec.identity_keys!r}, "
                        "current_params="
                        f"{None if spec is None else spec.param_keys!r}"
                    ),
                    rowid=index,
                    stage=coordinate.stage,
                    task_base=coordinate.task_base,
                )
            )
        entries.append(
            TaskCoordinateMigrationEntryV1(
                rowid=index,
                task_coordinate=coordinate,
                done_at=str(row[done_at_index] or ""),
                contract_matches=contract_matches,
            )
        )

    if ledger_available:
        findings.extend(
            _ledger_findings(
                ledger_rows,
                entries,
                authority_mode=TASK_COORDINATE_AUTHORITY_V1,
            )
        )
    else:
        findings.append(
            TaskCoordinateMigrationFindingV1(
                level="warning",
                issue_type="ledger_unavailable",
                detail=(
                    "task_param_ledger is absent; current validation can "
                    "inspect structured DONE rows only"
                ),
            )
        )

    return TaskCoordinateMigrationReportV1(
        state_db_path=str(db_path),
        state_available=True,
        source_done_available=structured_done_available,
        authority_mode=TASK_COORDINATE_AUTHORITY_V1,
        done_row_count=len(structured_done_rows),
        coordinate_count=len(entries),
        ledger_available=ledger_available,
        ledger_row_count=len(ledger_rows),
        ledger_done_count=sum(bool(row[7]) for row in ledger_rows),
        contract_drift_count=contract_drift_count,
        entries=tuple(entries),
        findings=tuple(findings),
    )


def format_task_coordinate_migration_report_v1(
    report: TaskCoordinateMigrationReportV1,
    *,
    limit: int = 20,
) -> str:
    lines = [
        "Task coordinate migration preview",
        f"state: {report.state_db_path}",
        f"authority mode: {report.authority_mode}",
        (
            "DONE/coordinates: "
            f"{report.done_row_count}/{report.coordinate_count}"
        ),
        (
            "ledger: "
            f"{'available' if report.ledger_available else 'unavailable'} "
            f"rows={report.ledger_row_count} done={report.ledger_done_count}"
        ),
        (
            "findings: "
            f"blockers={report.blocker_count} "
            f"warnings={report.warning_count} "
            f"contract_drift={report.contract_drift_count}"
        ),
        f"ready to migrate: {str(report.ready_to_migrate).lower()}",
    ]
    selected = report.findings[: max(0, int(limit))]
    if selected:
        lines.append("selected findings:")
        for finding in selected:
            location = (
                f" row={finding.rowid}"
                if finding.rowid is not None
                else ""
            )
            task = (
                f" {finding.stage}:{finding.task_base}"
                if finding.stage or finding.task_base
                else ""
            )
            lines.append(
                f"  [{finding.level}] {finding.issue_type}"
                f"{location}{task}: {finding.detail}"
            )
    return "\n".join(lines)


def apply_task_coordinate_migration_v1(
    state_or_db_path: str | Path,
) -> TaskCoordinateMigrationApplyResultV1:
    db_path = require_safe_state_paths(state_or_db_path)
    if not db_path.is_file():
        raise TaskCoordinateMigrationError(
            f"State SQLite file does not exist: {db_path}"
        )

    preflight = build_task_coordinate_migration_report_v1(db_path)
    _require_clean_migration_report_v1(preflight)

    con = sqlite3.connect(
        str(db_path),
        timeout=120,
        isolation_level=None,
    )
    con.execute("PRAGMA busy_timeout=120000")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        locked_report = build_task_coordinate_migration_report_v1(
            db_path
        )
        _require_clean_migration_report_v1(locked_report)

        if (
            locked_report.authority_mode
            == TASK_COORDINATE_AUTHORITY_V1
        ):
            state = load_task_coordinate_state_v1(con)
            _validate_structured_done_rows_v1(
                con,
                locked_report.entries,
            )
            _validate_ledger_coordinate_ids_v1(con)
            con.execute("COMMIT")
            return TaskCoordinateMigrationApplyResultV1(
                state_db_path=str(db_path),
                previous_authority_mode=state.authority_mode,
                authority_mode=state.authority_mode,
                applied=False,
                coordinate_count=len(locked_report.entries),
                inserted_coordinate_count=0,
                backfilled_ledger_count=0,
            )

        now = datetime.now(UTC).isoformat()
        state = init_task_coordinate_schema_v1(con, now=now)
        if (
            state.authority_mode
            != TASK_COORDINATE_LEGACY_COMPATIBILITY
        ):
            raise TaskCoordinateMigrationError(
                "Migration requires legacy-compatible authority, got "
                f"{state.authority_mode!r}"
            )
        ensure_task_coordinate_ledger_columns_v1(con)

        inserted_coordinate_count = 0
        for entry in locked_report.entries:
            coordinate = entry.task_coordinate
            legacy_row = con.execute(
                """
                SELECT done_at, meta_json
                FROM task_done_flex
                WHERE rowid=?
                """,
                (entry.rowid,),
            ).fetchone()
            if (
                legacy_row is None
                or str(legacy_row[0] or "") != entry.done_at
            ):
                raise TaskCoordinateMigrationError(
                    "Legacy DONE row changed while migration lock was held: "
                    f"rowid={entry.rowid}"
                )
            if not task_coordinate_done_v1(con, coordinate):
                write_task_coordinate_done_v1(
                    con,
                    coordinate,
                    done_at=entry.done_at,
                    meta_json=(
                        None
                        if legacy_row[1] is None
                        else str(legacy_row[1])
                    ),
                    source="migration",
                )
                inserted_coordinate_count += 1

        backfilled_ledger_count = (
            _backfill_ledger_coordinate_ids_v1(con)
        )
        _validate_structured_done_rows_v1(
            con,
            locked_report.entries,
        )
        _validate_ledger_coordinate_ids_v1(con)

        cursor = con.execute(
            """
            UPDATE task_coordinate_state_v1
            SET authority_mode=?, updated_at=?
            WHERE singleton_id=1 AND authority_mode=?
            """,
            (
                TASK_COORDINATE_AUTHORITY_V1,
                now,
                TASK_COORDINATE_LEGACY_COMPATIBILITY,
            ),
        )
        if cursor.rowcount != 1:
            raise TaskCoordinateMigrationError(
                "Task coordinate authority changed during migration"
            )
        migrated_state = load_task_coordinate_state_v1(con)
        if (
            migrated_state.authority_mode
            != TASK_COORDINATE_AUTHORITY_V1
        ):
            raise TaskCoordinateMigrationError(
                "Task coordinate authority switch did not persist"
            )
        con.execute("COMMIT")
        return TaskCoordinateMigrationApplyResultV1(
            state_db_path=str(db_path),
            previous_authority_mode=locked_report.authority_mode,
            authority_mode=migrated_state.authority_mode,
            applied=True,
            coordinate_count=len(locked_report.entries),
            inserted_coordinate_count=inserted_coordinate_count,
            backfilled_ledger_count=backfilled_ledger_count,
        )
    except Exception as exc:
        if con.in_transaction:
            con.execute("ROLLBACK")
        if isinstance(exc, TaskCoordinateMigrationError):
            raise
        raise TaskCoordinateMigrationError(
            "Task coordinate migration failed and was rolled back: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        con.close()


def format_task_coordinate_migration_apply_result_v1(
    result: TaskCoordinateMigrationApplyResultV1,
) -> str:
    return "\n".join(
        (
            "Task coordinate migration apply",
            f"state: {result.state_db_path}",
            (
                "authority: "
                f"{result.previous_authority_mode} -> "
                f"{result.authority_mode}"
            ),
            f"applied: {str(result.applied).lower()}",
            f"coordinates: {result.coordinate_count}",
            (
                "inserted structured DONE: "
                f"{result.inserted_coordinate_count}"
            ),
            (
                "backfilled ledger IDs: "
                f"{result.backfilled_ledger_count}"
            ),
        )
    )


def _require_clean_migration_report_v1(
    report: TaskCoordinateMigrationReportV1,
) -> None:
    if report.authority_mode not in {
        "uninitialized",
        TASK_COORDINATE_LEGACY_COMPATIBILITY,
        TASK_COORDINATE_AUTHORITY_V1,
    }:
        raise TaskCoordinateMigrationError(
            "Task coordinate authority state is invalid: "
            f"{report.authority_mode!r}"
        )
    if not report.ready_to_migrate:
        raise TaskCoordinateMigrationError(
            "Task coordinate migration preview is not clean: "
            f"{report.finding_counts}"
        )


def _validate_structured_done_rows_v1(
    con: sqlite3.Connection,
    entries: tuple[TaskCoordinateMigrationEntryV1, ...],
) -> None:
    expected = {
        entry.task_coordinate.task_coordinate_id:
        entry.task_coordinate
        for entry in entries
    }
    tables = {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    done_table = (
        TASK_COORDINATE_DONE_TABLE_V2
        if TASK_COORDINATE_DONE_TABLE_V2 in tables
        else TASK_COORDINATE_DONE_TABLE_V1
    )
    try:
        rows = con.execute(
            f"""
            SELECT task_coordinate_id, source, done_at
            FROM {done_table}
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise TaskCoordinateMigrationError(
            "Structured DONE table is missing or malformed"
        ) from exc
    actual_ids = {str(row[0]) for row in rows}
    if actual_ids != set(expected):
        raise TaskCoordinateMigrationError(
            "Structured DONE IDs differ from legacy reconstruction: "
            f"expected={len(expected)}, actual={len(actual_ids)}"
        )
    for coordinate in expected.values():
        try:
            if done_table == TASK_COORDINATE_DONE_TABLE_V2:
                task_coordinate_done_v2(con, coordinate)
            else:
                task_coordinate_done_v1(con, coordinate)
        except TaskCoordinatePersistenceError as exc:
            raise TaskCoordinateMigrationError(str(exc)) from exc
    if any(
        str(source) not in {"runtime", "migration"}
        or not str(done_at or "")
        for _, source, done_at in rows
    ):
        raise TaskCoordinateMigrationError(
            "Structured DONE provenance is invalid"
        )


def _backfill_ledger_coordinate_ids_v1(
    con: sqlite3.Connection,
) -> int:
    tables = {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "task_param_ledger" not in tables:
        return 0
    rows = con.execute(
        """
        SELECT rowid, stage, task_base, variant_str,
               identity_json, task_params_json,
               identity_keys_json, param_keys_json,
               task_coordinate_id
        FROM task_param_ledger
        ORDER BY rowid
        """
    ).fetchall()
    seen: set[str] = set()
    updated = 0
    for row in rows:
        coordinate = _coordinate_from_ledger_payload_v1(
            stage=row[1],
            task_base=row[2],
            variant_str=row[3],
            identity_json=row[4],
            task_params_json=row[5],
            identity_keys_json=row[6],
            param_keys_json=row[7],
        )
        coordinate_id = coordinate.task_coordinate_id
        if coordinate_id in seen:
            raise TaskCoordinateMigrationError(
                "Multiple ledger rows map to one task coordinate"
            )
        seen.add(coordinate_id)
        stored_id = str(row[8] or "")
        if stored_id and stored_id != coordinate_id:
            raise TaskCoordinateMigrationError(
                "Ledger row contains a mismatched task coordinate ID"
            )
        if not stored_id:
            con.execute(
                """
                UPDATE task_param_ledger
                SET task_coordinate_id=?
                WHERE rowid=?
                """,
                (coordinate_id, int(row[0])),
            )
            updated += 1
    return updated


def _validate_ledger_coordinate_ids_v1(
    con: sqlite3.Connection,
) -> None:
    tables = {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "task_param_ledger" not in tables:
        return
    columns = {
        str(row[1])
        for row in con.execute(
            "PRAGMA table_info(task_param_ledger)"
        ).fetchall()
    }
    if "task_coordinate_id" not in columns:
        raise TaskCoordinateMigrationError(
            "Coordinate-authority ledger lacks task_coordinate_id"
        )
    rows = con.execute(
        """
        SELECT stage, task_base, variant_str,
               identity_json, task_params_json,
               identity_keys_json, param_keys_json,
               task_coordinate_id
        FROM task_param_ledger
        """
    ).fetchall()
    for row in rows:
        coordinate = _coordinate_from_ledger_payload_v1(
            stage=row[0],
            task_base=row[1],
            variant_str=row[2],
            identity_json=row[3],
            task_params_json=row[4],
            identity_keys_json=row[5],
            param_keys_json=row[6],
        )
        if str(row[7] or "") != coordinate.task_coordinate_id:
            raise TaskCoordinateMigrationError(
                "Coordinate-authority ledger contains an empty or "
                "mismatched task coordinate ID"
            )


def _coordinate_from_ledger_payload_v1(
    *,
    stage: Any,
    task_base: Any,
    variant_str: Any,
    identity_json: Any,
    task_params_json: Any,
    identity_keys_json: Any,
    param_keys_json: Any,
) -> TaskCoordinateV1:
    identity_payload = json.loads(str(identity_json))
    params_payload = json.loads(str(task_params_json))
    identity_keys_payload = json.loads(str(identity_keys_json))
    param_keys_payload = json.loads(str(param_keys_json))
    if (
        not isinstance(identity_payload, list)
        or not isinstance(params_payload, Mapping)
        or not isinstance(identity_keys_payload, list)
        or not isinstance(param_keys_payload, list)
    ):
        raise TaskCoordinateError(
            "ledger identity/parameter payload types are invalid"
        )
    identity = []
    for item in identity_payload:
        if not isinstance(item, Mapping) or "key" not in item:
            raise TaskCoordinateError(
                "ledger identity item is malformed"
            )
        identity.append((str(item["key"]), item.get("value")))
    coordinate = build_task_coordinate_v1(
        stage=str(stage or ""),
        task_base=str(task_base or ""),
        identity=identity,
        params=params_payload,
    )
    decoded_params = decode_legacy_variant_str_v1(
        str(variant_str or "")
    )
    if tuple(decoded_params) != coordinate.params:
        raise TaskCoordinateError(
            "ledger variant alias differs from structured params"
        )
    if set(str(key) for key in identity_keys_payload) != set(
        coordinate.identity_dict
    ):
        raise TaskCoordinateError(
            "ledger identity key registry differs from payload"
        )
    if set(str(key) for key in param_keys_payload) != set(
        coordinate.params_dict
    ):
        raise TaskCoordinateError(
            "ledger parameter key registry differs from payload"
        )
    return coordinate


def _structured_done_findings_v1(
    rows: tuple[Any, ...] | list[Any],
    entries: list[TaskCoordinateMigrationEntryV1],
    *,
    authority_mode: str,
    structured_done_available: bool,
) -> list[TaskCoordinateMigrationFindingV1]:
    findings: list[TaskCoordinateMigrationFindingV1] = []
    expected = {
        entry.task_coordinate.task_coordinate_id:
        entry.task_coordinate
        for entry in entries
    }
    if (
        authority_mode == TASK_COORDINATE_AUTHORITY_V1
        and not structured_done_available
    ):
        return [
            TaskCoordinateMigrationFindingV1(
                level="blocker",
                issue_type="structured_done_table_missing",
                detail=(
                    "coordinate authority requires "
                    "task_done_coordinate_v1"
                ),
            )
        ]

    actual_ids: set[str] = set()
    for row in rows:
        coordinate_id = str(row[0] or "")
        actual_ids.add(coordinate_id)
        coordinate = expected.get(coordinate_id)
        if coordinate is None:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="structured_done_extra",
                    detail=(
                        "structured DONE has no matching legacy "
                        f"coordinate: {coordinate_id}"
                    ),
                )
            )
            continue
        expected_payload = (
            coordinate.schema_version,
            coordinate.stage,
            coordinate.task_base,
            coordinate.identity_hash,
            coordinate.params_hash,
            coordinate.canonical_json,
            coordinate.legacy_variant_str,
        )
        if tuple(str(value) for value in row[1:8]) != (
            expected_payload
        ):
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="structured_done_payload_mismatch",
                    detail=(
                        "structured DONE payload differs from legacy "
                        f"reconstruction: {coordinate_id}"
                    ),
                    stage=coordinate.stage,
                    task_base=coordinate.task_base,
                )
            )
        if (
            str(row[8] or "") not in {"runtime", "migration"}
            or not str(row[9] or "")
        ):
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="structured_done_provenance_invalid",
                    detail=(
                        "structured DONE source or timestamp is invalid: "
                        f"{coordinate_id}"
                    ),
                    stage=coordinate.stage,
                    task_base=coordinate.task_base,
                )
            )
    if (
        authority_mode == TASK_COORDINATE_AUTHORITY_V1
        and actual_ids != set(expected)
    ):
        findings.append(
            TaskCoordinateMigrationFindingV1(
                level="blocker",
                issue_type="structured_done_set_mismatch",
                detail=(
                    "coordinate authority structured/legacy DONE sets "
                    f"differ: structured={len(actual_ids)}, "
                    f"legacy={len(expected)}"
                ),
            )
        )
    return findings


def _legacy_shaped_ledger_row_from_v2(
    record: CurrentTaskParameterLedgerRecordV3,
) -> tuple[Any, ...]:
    coordinate = record.task_coordinate
    identity_payload = [
        {"key": field.key, "value": field.legacy_value}
        for field in coordinate.identity
    ]
    return (
        coordinate.stage,
        coordinate.task_base,
        coordinate.legacy_variant_str,
        json.dumps(identity_payload, sort_keys=True),
        json.dumps(coordinate.params_dict, sort_keys=True),
        json.dumps(list(coordinate.identity_dict), sort_keys=True),
        json.dumps(list(coordinate.params_dict), sort_keys=True),
        int(record.done_known),
        int(record.queued),
        int(record.force),
        coordinate.task_coordinate_id,
    )


def _ledger_findings(
    ledger_rows: tuple[Any, ...],
    done_entries: list[TaskCoordinateMigrationEntryV1],
    *,
    authority_mode: str,
) -> list[TaskCoordinateMigrationFindingV1]:
    findings: list[TaskCoordinateMigrationFindingV1] = []
    ledger_by_coordinate: dict[
        str,
        tuple[bool, bool, bool],
    ] = {}
    done_ids = {
        entry.task_coordinate.task_coordinate_id
        for entry in done_entries
    }
    for row in ledger_rows:
        (
            stage,
            task_base,
            variant_str,
            identity_json,
            task_params_json,
            identity_keys_json,
            param_keys_json,
            done_known,
            queued,
            force,
            stored_coordinate_id,
        ) = row
        stage = str(stage or "")
        task_base = str(task_base or "")
        try:
            coordinate = _coordinate_from_ledger_payload_v1(
                stage=stage,
                task_base=task_base,
                variant_str=variant_str,
                identity_json=identity_json,
                task_params_json=task_params_json,
                identity_keys_json=identity_keys_json,
                param_keys_json=param_keys_json,
            )
        except (
            json.JSONDecodeError,
            TaskCoordinateError,
            TypeError,
            ValueError,
        ) as exc:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="ledger_row_malformed",
                    detail=f"{type(exc).__name__}: {exc}",
                    stage=stage,
                    task_base=task_base,
                )
            )
            continue

        coordinate_id = coordinate.task_coordinate_id
        if (
            str(stored_coordinate_id or "")
            and str(stored_coordinate_id) != coordinate_id
        ):
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="ledger_coordinate_id_mismatch",
                    detail=(
                        "stored ledger task_coordinate_id differs from "
                        "its structured payload"
                    ),
                    stage=stage,
                    task_base=task_base,
                )
            )
        if (
            authority_mode == TASK_COORDINATE_AUTHORITY_V1
            and not str(stored_coordinate_id or "")
        ):
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="ledger_coordinate_id_missing",
                    detail=(
                        "coordinate authority ledger row has no "
                        "task_coordinate_id"
                    ),
                    stage=stage,
                    task_base=task_base,
                )
            )
        if coordinate_id in ledger_by_coordinate:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="ledger_coordinate_duplicate",
                    detail=(
                        "multiple ledger rows map to one task coordinate"
                    ),
                    stage=stage,
                    task_base=task_base,
                )
            )
            continue
        status = (bool(done_known), bool(queued), bool(force))
        ledger_by_coordinate[coordinate_id] = status
        if queued:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="ledger_in_flight",
                    detail=(
                        "queued ledger work prevents an authority switch"
                    ),
                    stage=stage,
                    task_base=task_base,
                )
            )

    for entry in done_entries:
        coordinate = entry.task_coordinate
        status = ledger_by_coordinate.get(
            coordinate.task_coordinate_id
        )
        if status is None:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="done_missing_ledger",
                    detail=(
                        "legacy DONE coordinate has no matching ledger row"
                    ),
                    rowid=entry.rowid,
                    stage=coordinate.stage,
                    task_base=coordinate.task_base,
                )
            )
        elif not status[0] or status[1]:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="done_ledger_status_mismatch",
                    detail=(
                        "legacy DONE coordinate is not completed and "
                        "unqueued in the ledger"
                    ),
                    rowid=entry.rowid,
                    stage=coordinate.stage,
                    task_base=coordinate.task_base,
                )
            )

    for coordinate_id, (done_known, _queued, _force) in (
        ledger_by_coordinate.items()
    ):
        if done_known and coordinate_id not in done_ids:
            findings.append(
                TaskCoordinateMigrationFindingV1(
                    level="blocker",
                    issue_type="ledger_done_missing_legacy",
                    detail=(
                        "completed ledger coordinate has no legacy DONE row"
                    ),
                )
            )
    return findings


def _read_authority_mode(
    con: sqlite3.Connection,
    tables: set[str],
) -> str:
    if "task_coordinate_state_v1" not in tables:
        return "uninitialized"
    try:
        return load_task_coordinate_state_v1(con).authority_mode
    except (sqlite3.Error, TaskCoordinatePersistenceError):
        return "malformed"


def _empty_report(
    db_path: Path,
    finding: TaskCoordinateMigrationFindingV1,
    *,
    state_available: bool = False,
) -> TaskCoordinateMigrationReportV1:
    return TaskCoordinateMigrationReportV1(
        state_db_path=str(db_path),
        state_available=state_available,
        source_done_available=False,
        authority_mode="unavailable",
        done_row_count=0,
        coordinate_count=0,
        ledger_available=False,
        ledger_row_count=0,
        ledger_done_count=0,
        contract_drift_count=0,
        entries=(),
        findings=(finding,),
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
