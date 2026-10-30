from __future__ import annotations

import csv
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from logging_config import get_logger, LOG_MAIN, LOG_UTILS, LoggersWrapper
from task_manage.config_keys import path_config_keys
from task_manage.task_runtime import stable_hash, select_immutable_config

logger_main = get_logger(LOG_MAIN)
logger_utils = get_logger(LOG_UTILS)
loggers = LoggersWrapper(main=logger_main, sub=logger_utils)

PATH_CONFIG_KEYS = path_config_keys(command="run")
REFERENCE_CONFIG_KEYS = tuple(
    key for key in ("gene_gtf", "bulk_window_gtf", "bulk_gene_gtf") if key in PATH_CONFIG_KEYS
)


@dataclass(frozen=True)
class InputTableRepairPreview:
    path: Path
    backup_path: Path
    changed_rows: int
    changed_fields: tuple[str, ...]


@dataclass(frozen=True)
class ConfigRepairPreview:
    snapshot_key: str
    key: str
    old_value: Any
    new_value: Any
    reason: str


@dataclass(frozen=True)
class MovedRunRepairPlan:
    run_root: Path
    old_root: Path | None
    state_db: Path
    timestamp: str
    input_table_repairs: tuple["_InputTableRepairPlan", ...] = ()
    state_meta_repair: "_StateMetaRepairPlan | None" = None

    @property
    def input_table_changes(self) -> tuple[InputTableRepairPreview, ...]:
        return tuple(repair.preview for repair in self.input_table_repairs)

    @property
    def config_changes(self) -> tuple[ConfigRepairPreview, ...]:
        if self.state_meta_repair is None:
            return ()
        return self.state_meta_repair.changes

    @property
    def has_changes(self) -> bool:
        return bool(self.input_table_repairs or self.state_meta_repair)


@dataclass(frozen=True)
class _InputTableRepairPlan:
    path: Path
    backup_path: Path
    fieldnames: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    changed_rows: int
    changed_fields: tuple[str, ...]

    @property
    def preview(self) -> InputTableRepairPreview:
        return InputTableRepairPreview(
            path=self.path,
            backup_path=self.backup_path,
            changed_rows=self.changed_rows,
            changed_fields=self.changed_fields,
        )


@dataclass(frozen=True)
class _StateMetaRepairPlan:
    state_db: Path
    timestamp: str
    preserved_meta: dict[str, str]
    config_json: dict[str, Any]
    immutable_config_json: dict[str, Any]
    immutable_config_hash: str
    reference_overrides: dict[str, str]
    changes: tuple[ConfigRepairPreview, ...]


def repair_moved_run(layout, args, *, dry_run: bool = False) -> MovedRunRepairPlan:
    plan = preview_moved_run_repair(layout=layout, args=args)
    _log_repair_plan(plan, dry_run=dry_run)
    if dry_run:
        loggers.info("Dry-run only; no moved-run repair changes were applied.")
        return plan

    if not plan.has_changes:
        return plan

    if plan.state_meta_repair is not None:
        _apply_reference_overrides_to_args(args, plan.state_meta_repair.reference_overrides)
    _apply_input_table_repairs(plan.input_table_repairs)
    if plan.state_meta_repair is not None:
        _apply_state_meta_repair(plan.state_meta_repair)
    return plan


def preview_moved_run_repair(layout, args, *, timestamp: str | None = None) -> MovedRunRepairPlan:
    run_root = layout.Root.resolve()
    timestamp = timestamp or datetime.now().strftime("%Y%m%d%H%M%S")

    old_root = _detect_old_root(layout)
    input_table_repairs: tuple[_InputTableRepairPlan, ...] = ()
    if old_root is None or old_root == run_root:
        old_root = None
    else:
        input_table_repairs = _plan_input_table_repairs(layout, old_root, run_root, timestamp)

    state_db = layout.State / "state.sqlite"
    state_meta_repair = None
    if state_db.exists():
        state_meta_repair = _plan_state_meta_repair(
            state_db=state_db,
            layout=layout,
            args=args,
            old_root=old_root,
            new_root=run_root,
            timestamp=timestamp,
        )

    return MovedRunRepairPlan(
        run_root=run_root,
        old_root=old_root,
        state_db=state_db,
        timestamp=timestamp,
        input_table_repairs=input_table_repairs,
        state_meta_repair=state_meta_repair,
    )


def _detect_old_root(layout) -> Path | None:
    candidates = [
        (layout.InputTables / "preprocess_inputs.csv", "pp_path", "/inputs/"),
    ]

    for unit_csv in sorted(layout.InputTables.glob("unit_inputs*.csv")):
        candidates.append((unit_csv, "resolved_path", "/unit_outputs/"))

    for csv_path, field_name, marker in candidates:
        if not csv_path.exists():
            continue
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                value = (row.get(field_name) or "").strip()
                if marker not in value:
                    continue
                prefix = value.split(marker, 1)[0]
                if prefix:
                    return Path(prefix)
    return None


def _plan_input_table_repairs(
        layout,
        old_root: Path,
        new_root: Path,
        timestamp: str,
) -> tuple[_InputTableRepairPlan, ...]:
    specs = [
        ("preprocess_inputs.csv", ["pp_path"]),
    ]
    specs.extend((p.name, ["resolved_path"]) for p in sorted(layout.InputTables.glob("unit_inputs*.csv")))

    repairs: list[_InputTableRepairPlan] = []
    for file_name, path_fields in specs:
        path = layout.InputTables / file_name
        if not path.exists():
            continue

        with open(path, "r", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = list(reader.fieldnames or [])

        changed = False
        changed_rows = 0
        changed_fields: set[str] = set()
        for row in rows:
            row_changed = False
            for field in path_fields:
                value = row.get(field)
                if not value:
                    continue
                new_value = _replace_run_root(value=value, old_root=old_root, new_root=new_root)
                if new_value != value:
                    row[field] = new_value
                    changed = True
                    changed_fields.add(field)
                    row_changed = True
            if row_changed:
                changed_rows += 1

        if not changed:
            continue

        backup_path = path.with_name(f"{path.name}.before-path-repair.{timestamp}")
        repairs.append(
            _InputTableRepairPlan(
                path=path,
                backup_path=backup_path,
                fieldnames=tuple(fieldnames),
                rows=tuple(rows),
                changed_rows=changed_rows,
                changed_fields=tuple(sorted(changed_fields)),
            )
        )
    return tuple(repairs)


def _apply_input_table_repairs(repairs: tuple[_InputTableRepairPlan, ...]) -> None:
    for repair in repairs:
        shutil.move(repair.path, repair.backup_path)
        with open(repair.path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=repair.fieldnames)
            writer.writeheader()
            writer.writerows(repair.rows)
        loggers.info(f"Repaired {repair.path}; preserved previous copy at {repair.backup_path}")


def _plan_state_meta_repair(
        state_db: Path,
        layout,
        args,
        old_root: Path | None,
        new_root: Path,
        timestamp: str,
) -> _StateMetaRepairPlan | None:
    backup_dir = layout.Backup
    path = sqlite3.connect(str(state_db))
    try:
        if not _table_exists(path, "meta"):
            return None
        current = dict(path.execute(
            "select key, value from meta where key in ('config_json', 'immutable_config_json', 'immutable_config_hash')"
        ).fetchall())
        if not current:
            return None

        config = json.loads(current.get("config_json", "{}"))
        immutable = json.loads(current.get("immutable_config_json", "{}"))
        reference_overrides = _reference_arg_overrides(args=args, backup_dir=backup_dir, old_config=config)

        config, config_changes = _rewrite_config_paths(
            config=config,
            args=args,
            old_root=old_root,
            new_root=new_root,
            snapshot_key="config_json",
            reference_overrides=reference_overrides,
            add_missing_path_args=True,
            apply_cli_path_args=old_root is not None,
        )
        immutable, immutable_changes = _rewrite_config_paths(
            config=immutable,
            args=args,
            old_root=old_root,
            new_root=new_root,
            snapshot_key="immutable_config_json",
            reference_overrides=reference_overrides,
            add_missing_path_args=False,
            apply_cli_path_args=old_root is not None,
        )

        immutable_hash = stable_hash(select_immutable_config(config))
        changes = (*config_changes, *immutable_changes)
        if current.get("immutable_config_hash") != immutable_hash:
            changes = (
                *changes,
                ConfigRepairPreview(
                    snapshot_key="meta",
                    key="immutable_config_hash",
                    old_value=current.get("immutable_config_hash"),
                    new_value=immutable_hash,
                    reason="recomputed-immutable-hash",
                ),
            )
        if not changes and current.get("immutable_config_hash") == immutable_hash:
            return None

        return _StateMetaRepairPlan(
            state_db=state_db,
            timestamp=timestamp,
            preserved_meta=dict(current),
            config_json=config,
            immutable_config_json=immutable,
            immutable_config_hash=immutable_hash,
            reference_overrides=reference_overrides,
            changes=changes,
        )
    finally:
        path.close()


def _apply_state_meta_repair(repair: _StateMetaRepairPlan) -> None:
    path = sqlite3.connect(str(repair.state_db))
    try:
        for meta_key, raw_value in repair.preserved_meta.items():
            preserved_key = f"{meta_key}__before_path_repair__{repair.timestamp}"
            path.execute("insert or replace into meta(key, value) values(?, ?)", (preserved_key, raw_value))

        path.execute(
            "insert or replace into meta(key, value) values(?, ?)",
            ("config_json", json.dumps(repair.config_json, sort_keys=True)),
        )
        path.execute(
            "insert or replace into meta(key, value) values(?, ?)",
            ("immutable_config_json", json.dumps(repair.immutable_config_json, sort_keys=True))
        )
        path.execute(
            "insert or replace into meta(key, value) values(?, ?)",
            ("immutable_config_hash", repair.immutable_config_hash)
        )
        path.commit()
        loggers.info(f"Repaired state meta in {repair.state_db}")
    finally:
        path.close()


def _apply_reference_overrides_to_args(args, reference_overrides: dict[str, str]) -> None:
    for key, value in reference_overrides.items():
        setattr(args, key, value)


def _reference_arg_overrides(args, backup_dir: Path, old_config: dict[str, Any]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for key in REFERENCE_CONFIG_KEYS:
        backup_path = _choose_backup_path(
            key=key,
            backup_dir=backup_dir,
            current_value=getattr(args, key, None),
            old_value=old_config.get(key),
        )
        if backup_path is not None:
            overrides[key] = str(backup_path)
    return overrides


def _rewrite_config_paths(
        config: dict[str, Any],
        args,
        old_root: Path | None,
        new_root: Path,
        snapshot_key: str,
        reference_overrides: dict[str, str],
        add_missing_path_args: bool,
        apply_cli_path_args: bool,
) -> tuple[dict[str, Any], tuple[ConfigRepairPreview, ...]]:
    out = dict(config)
    changes: list[ConfigRepairPreview] = []

    def set_value(key: str, new_value: Any, reason: str) -> None:
        if key not in out and not add_missing_path_args:
            return
        old_value = out.get(key)
        if old_value == new_value:
            return
        out[key] = new_value
        changes.append(
            ConfigRepairPreview(
                snapshot_key=snapshot_key,
                key=key,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
            )
        )

    for key, value in list(out.items()):
        if key in PATH_CONFIG_KEYS and isinstance(value, str) and old_root is not None:
            set_value(
                key=key,
                new_value=_replace_run_root(value=value, old_root=old_root, new_root=new_root),
                reason="run-root-prefix",
            )

    arg_values = vars(args)
    if apply_cli_path_args:
        for key in sorted(PATH_CONFIG_KEYS):
            value = arg_values.get(key)
            if value is not None:
                set_value(key=key, new_value=value, reason="cli-path-arg")

    for key, value in reference_overrides.items():
        set_value(key=key, new_value=value, reason="backup-reference")

    return out, tuple(changes)


def _log_repair_plan(plan: MovedRunRepairPlan, *, dry_run: bool) -> None:
    mode = "Dry-run moved-run repair preview" if dry_run else "Moved-run repair plan"
    if plan.old_root is None:
        loggers.info(f"{mode}: no moved run root detected for {plan.run_root}.")
    else:
        loggers.info(f"{mode}: old={plan.old_root} new={plan.run_root}")

    if not plan.state_db.exists():
        loggers.info(f"{mode}: no state.sqlite found at {plan.state_db}; DB repair skipped.")

    for change in plan.input_table_changes:
        loggers.info(
            f"{mode}: would repair {change.path} rows={change.changed_rows} "
            f"fields={','.join(change.changed_fields)} backup={change.backup_path}"
        )

    for change in plan.config_changes:
        loggers.info(
            f"{mode}: {change.snapshot_key}.{change.key} via {change.reason}: "
            f"{change.old_value!r} -> {change.new_value!r}"
        )

    if not plan.has_changes:
        loggers.info(f"{mode}: no changes needed.")


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _choose_backup_path(key: str, backup_dir: Path, current_value: Any, old_value: Any) -> Path | None:
    if not backup_dir.exists():
        return None

    candidates = []
    for value in [current_value, old_value]:
        if not value:
            continue
        candidate = backup_dir / Path(str(value)).name
        if candidate.exists():
            return candidate
        candidates.append(Path(str(value)).name)

    gtfs = sorted(backup_dir.glob("*.gtf"))
    if key == "gene_gtf":
        gene_gtfs = [p for p in gtfs if "bulk-reference" not in p.name]
        if len(gene_gtfs) == 1:
            return gene_gtfs[0]
    if key == "bulk_window_gtf":
        bulk_gtfs = [p for p in gtfs if "bulk-reference" in p.name]
        if len(bulk_gtfs) == 1:
            return bulk_gtfs[0]
    return None


def _replace_run_root(value: str, old_root: Path, new_root: Path) -> str:
    value_str = str(value)
    old_str = str(old_root)
    if value_str.startswith(old_str):
        return str(new_root) + value_str[len(old_str):]
    return value_str
