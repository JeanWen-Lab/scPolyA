from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .define import UNIT_INPUTS_CSV_NAME
from .run_config_preview import load_run_config_snapshot
from .run_layout import RunLayout


_REQUIRED_COLUMNS = frozenset({"unit_id", "sample_id", "contig_name", "dedup_status"})


@dataclass(frozen=True)
class FinalizedUnitInputRow:
    """One persisted downstream unit row from a finalized input table."""

    unit_id: str
    sample_id: str
    contig_name: str
    dedup_status: str
    resolved_path: str | None = None


@dataclass(frozen=True)
class FinalizedUnitInputTable:
    """Strictly parsed finalized unit metadata with no runtime side effects."""

    path: Path
    rows: tuple[FinalizedUnitInputRow, ...]


@dataclass(frozen=True)
class FinalizedUnitInputTableLoad:
    """Read-only finalized-unit evidence or the reason it is unavailable."""

    path: Path
    table: FinalizedUnitInputTable | None = None
    detail: str = ""

    @property
    def is_ready(self) -> bool:
        return self.table is not None


def load_finalized_unit_input_table(table_path: str | Path) -> FinalizedUnitInputTableLoad:
    """Load one finalized unit CSV without creating layout directories."""

    path = Path(table_path).expanduser()
    if not path.exists():
        return FinalizedUnitInputTableLoad(
            path=path,
            detail=f"finalized unit input table is missing: {path}",
        )
    if not path.is_file():
        return FinalizedUnitInputTableLoad(
            path=path,
            detail=f"finalized unit input table is not a regular file: {path}",
        )

    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, strict=True)
            fieldnames = reader.fieldnames
            if not fieldnames:
                return FinalizedUnitInputTableLoad(
                    path=path,
                    detail=f"finalized unit input table has no header: {path}",
                )
            missing_columns = sorted(_REQUIRED_COLUMNS.difference(fieldnames))
            if missing_columns:
                return FinalizedUnitInputTableLoad(
                    path=path,
                    detail=f"finalized unit input table is missing required columns {missing_columns}: {path}",
                )
            if len(fieldnames) != len(set(fieldnames)):
                return FinalizedUnitInputTableLoad(
                    path=path,
                    detail=f"finalized unit input table has duplicate columns: {path}",
                )

            rows: list[FinalizedUnitInputRow] = []
            for raw_row in reader:
                if None in raw_row or any(value is None for value in raw_row.values()):
                    return FinalizedUnitInputTableLoad(
                        path=path,
                        detail=f"finalized unit input table has malformed rows: {path}",
                    )
                rows.append(
                    FinalizedUnitInputRow(
                        unit_id=raw_row["unit_id"],
                        sample_id=raw_row["sample_id"],
                        contig_name=raw_row["contig_name"],
                        dedup_status=raw_row["dedup_status"],
                        resolved_path=raw_row.get("resolved_path"),
                    )
                )
    except (OSError, UnicodeError, csv.Error) as exc:
        return FinalizedUnitInputTableLoad(
            path=path,
            detail=f"could not parse finalized unit input table {path}: {exc}",
        )

    return FinalizedUnitInputTableLoad(
        path=path,
        table=FinalizedUnitInputTable(path=path, rows=tuple(rows)),
    )


def finalized_unit_input_table_path(layout: RunLayout, *, dedup_method: str) -> Path:
    """Return the canonical persisted unit-table location without creating it."""

    return layout.InputTables / UNIT_INPUTS_CSV_NAME(dedup_method)


def load_finalized_unit_input_table_for_state(
    state_or_db_path: str | Path,
) -> FinalizedUnitInputTableLoad:
    """Load a run's persisted unit table through its saved config and layout."""

    state_path = _resolve_state_db_path(state_or_db_path)
    if not state_path.exists():
        return FinalizedUnitInputTableLoad(
            path=state_path,
            detail=f"state database is missing for finalized unit evidence: {state_path}",
        )
    try:
        config = load_run_config_snapshot(state_path)
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        return FinalizedUnitInputTableLoad(
            path=state_path,
            detail=f"could not load persisted run config for finalized unit evidence: {exc}",
        )

    output_dir = config.get("output_dir")
    run_id = config.get("run_id")
    dedup_method = config.get("dedup_method")
    if output_dir in (None, "") or run_id in (None, ""):
        return FinalizedUnitInputTableLoad(
            path=state_path,
            detail="persisted run config lacks output_dir or run_id for finalized unit evidence",
        )
    if not isinstance(dedup_method, str) or not dedup_method.strip():
        return FinalizedUnitInputTableLoad(
            path=state_path,
            detail="persisted run config lacks a nonblank dedup_method for finalized unit evidence",
        )

    layout = RunLayout(out=Path(str(output_dir)).expanduser(), run_id=str(run_id))
    return load_finalized_unit_input_table(finalized_unit_input_table_path(layout, dedup_method=dedup_method))


def find_finalized_unit_input_row(
    table: FinalizedUnitInputTable,
    *,
    sid: str,
    ctg: str,
) -> tuple[FinalizedUnitInputRow | None, str]:
    """Find one unit row using the existing sample/contig then unit-ID semantics."""

    sample_contig_matches = [
        row for row in table.rows if row.sample_id == sid and row.contig_name == ctg
    ]
    if len(sample_contig_matches) == 1:
        return sample_contig_matches[0], ""
    if len(sample_contig_matches) > 1:
        return None, (
            f"finalized unit input table has {len(sample_contig_matches)} rows "
            f"for sample_id={sid!r}, contig_name={ctg!r}: {table.path}"
        )

    expected_unit_id = f"{sid}.{ctg}"
    unit_id_matches = [row for row in table.rows if row.unit_id == expected_unit_id]
    if len(unit_id_matches) == 1:
        return unit_id_matches[0], ""
    if len(unit_id_matches) > 1:
        return None, (
            f"finalized unit input table has {len(unit_id_matches)} rows "
            f"for unit_id={expected_unit_id!r}: {table.path}"
        )
    return None, (
        f"finalized unit input table has no row for sample_id={sid!r}, "
        f"contig_name={ctg!r} or unit_id={expected_unit_id!r}: {table.path}"
    )


def _resolve_state_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path).expanduser()
    if not path.is_dir():
        return path
    if path.name == "state":
        return path / "state.sqlite"
    candidate = path / "state" / "state.sqlite"
    if candidate.exists():
        return candidate
    return path / "state.sqlite"
