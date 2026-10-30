from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scpolya_ui.errors import UserError
from scpolya_ui.jobs import JobRegistry
from scpolya_ui.registry import (
    WorkspaceRecord,
    WorkspaceRegistry,
    add_path_mapping,
    find_workspace,
    path_mapping_problem,
    save_registry,
    touch_workspace,
    upsert_workspace,
)
from scpolya_ui.run_records import RunRecordRegistry
from scpolya_ui.studies import StudyRegistry
from scpolya_ui.view_models.run_vm import logical_run_summaries
from scpolya_ui.view_models.workspace_vm import (
    PathMappingSummary,
    WorkspaceOption,
    WorkspaceSummary,
    path_mapping_summaries,
    workspace_option,
    workspace_summary,
)


def workspace_count(registry: WorkspaceRegistry) -> int:
    return len(registry.workspaces)


def workspace_options(registry: WorkspaceRegistry) -> list[WorkspaceOption]:
    return [workspace_option(record) for record in registry.workspaces]


def get_workspace(registry: WorkspaceRegistry, workspace_id: str | None) -> WorkspaceRecord | None:
    if not workspace_id:
        return None
    return find_workspace(registry, workspace_id)


def default_workspace(registry: WorkspaceRegistry) -> WorkspaceRecord | None:
    return registry.workspaces[0] if registry.workspaces else None


def register_workspace(
    registry: WorkspaceRegistry,
    *,
    path: str | Path,
    alias: str | None = None,
    notes: str = "",
    home: str | Path | None = None,
) -> WorkspaceRecord:
    candidate = Path(str(path).strip()).expanduser()
    if not str(path).strip() or not candidate.is_absolute():
        raise UserError("error.workspace.path_absolute", "Workspace path must be an absolute path", path=str(path))
    if not candidate.exists():
        raise UserError("error.workspace.path_missing", f"Workspace path does not exist: {candidate}", path=str(candidate))
    workspace = upsert_workspace(registry, path=candidate, alias=alias, notes=notes)
    save_registry(registry, Path(home).expanduser() if home is not None else None)
    return workspace


def select_workspace(
    registry: WorkspaceRegistry,
    workspace_id: str,
    *,
    home: str | Path | None = None,
) -> WorkspaceRecord | None:
    workspace = touch_workspace(registry, workspace_id)
    if workspace is not None:
        save_registry(registry, Path(home).expanduser() if home is not None else None)
    return workspace


def add_workspace_mapping(
    registry: WorkspaceRegistry,
    workspace_id: str,
    *,
    old_path: str | Path,
    new_path: str | Path,
    note: str = "",
    home: str | Path | None = None,
) -> WorkspaceRecord | None:
    workspace = find_workspace(registry, workspace_id)
    if workspace is None:
        return None
    old = Path(old_path).expanduser()
    new = Path(new_path).expanduser()
    # A wrong mapping silently rewrites every displayed path, so refuse the obvious mistakes.
    # The rule is shared with loading, so a saved mapping can always be applied.
    problem = path_mapping_problem(old, new)
    if problem == "relative":
        raise UserError("workspace.mapping_error.relative", "Both mapping paths must be absolute")
    if problem == "traversal":
        raise UserError("workspace.mapping_error.traversal", "Mapping paths cannot contain '..'")
    if problem == "same":
        raise UserError("workspace.mapping_error.same", "Old and new paths are the same")
    if any(Path(item.old_path).expanduser() == old for item in workspace.path_mappings):
        raise UserError("workspace.mapping_error.duplicate", f"A mapping for {old} already exists", path=str(old))
    if not new.exists():
        raise UserError("workspace.mapping_error.missing", f"New path does not exist: {new}", path=str(new))
    add_path_mapping(workspace, old_path=old, new_path=new, note=note)
    save_registry(registry, Path(home).expanduser() if home is not None else None)
    return workspace


def remove_workspace_mapping(
    registry: WorkspaceRegistry,
    workspace_id: str,
    *,
    old_path: str,
    new_path: str | None = None,
    ignored: bool | None = None,
    home: str | Path | None = None,
) -> WorkspaceRecord | None:
    """Remove a path mapping.

    ignored=True or False removes one row: the first ignored (or applied) mapping matching old_path and, if
    given, new_path. The other list is never touched, so removing an ignored copy that is identical to an
    applied mapping keeps the applied one. Without ignored, every mapping for old_path (narrowed by new_path)
    is removed, except that an identical ignored copy is removed alone rather than together with its applied twin.
    """
    workspace = find_workspace(registry, workspace_id)
    if workspace is None:
        return None

    def matches(item) -> bool:
        return item.old_path == old_path and (new_path is None or item.new_path == new_path)

    def without_first(items):
        index = next((i for i, item in enumerate(items) if matches(item)), None)
        return items if index is None else items[:index] + items[index + 1:]

    if ignored is None and new_path is not None:
        if any(matches(item) for item in workspace.path_mappings) and any(
            matches(item) for item in workspace.ignored_path_mappings
        ):
            ignored = True
    if ignored is True:
        workspace.ignored_path_mappings = without_first(workspace.ignored_path_mappings)
    elif ignored is False:
        workspace.path_mappings = without_first(workspace.path_mappings)
    else:
        workspace.path_mappings = [item for item in workspace.path_mappings if not matches(item)]
        workspace.ignored_path_mappings = [item for item in workspace.ignored_path_mappings if not matches(item)]
    save_registry(registry, Path(home).expanduser() if home is not None else None)
    return workspace


def workspace_summaries(
    registry: WorkspaceRegistry,
    *,
    run_records: RunRecordRegistry | None = None,
    studies: StudyRegistry | None = None,
    jobs: JobRegistry | None = None,
) -> list[WorkspaceSummary]:
    run_counts = Counter()
    if run_records is not None:
        for workspace_id in {record.workspace_id for record in run_records.records}:
            records = [
                record
                for record in run_records.records
                if record.workspace_id == workspace_id
            ]
            run_counts[workspace_id] = len(logical_run_summaries(records))
    study_counts = Counter(study.workspace_id for study in (studies.studies if studies else ()))
    job_counts = Counter(job.workspace_id for job in (jobs.jobs if jobs else ()))
    return [
        workspace_summary(
            record,
            run_count=run_counts[record.id],
            study_count=study_counts[record.id],
            job_count=job_counts[record.id],
        )
        for record in registry.workspaces
    ]


def workspace_mapping_summaries(record: WorkspaceRecord | None) -> list[PathMappingSummary]:
    if record is None:
        return []
    return path_mapping_summaries(record)


OUTPUT_MARKERS = ("final_output", "count_matrix", "merged_cohort", "unit_outputs")


@dataclass(frozen=True)
class UntrackedOutputs:
    """Pipeline output found in a workspace that has no invocation history."""

    log_files: int
    output_dirs: tuple[str, ...]
    has_state_db: bool


def untracked_outputs(record: WorkspaceRecord) -> UntrackedOutputs | None:
    """Describe existing output when ``logs/invocations`` is absent; ``None`` if there is nothing."""

    root = Path(record.root_path).expanduser()
    if (root / "logs" / "invocations").is_dir():
        return None
    logs = root / "logs"
    try:
        log_files = sum(1 for path in logs.rglob("*") if path.is_file()) if logs.is_dir() else 0
    except OSError:
        log_files = 0
    output_dirs = tuple(name for name in OUTPUT_MARKERS if (root / name).is_dir())
    has_state_db = Path(record.db_path).expanduser().is_file()
    if not (log_files or output_dirs or has_state_db):
        return None
    return UntrackedOutputs(log_files=log_files, output_dirs=output_dirs, has_state_db=has_state_db)


@dataclass(frozen=True)
class MovedRoot:
    """Run history recorded under another folder; ``old_prefix -> new_prefix`` would relocate it."""

    recorded_root: str
    invocation_count: int
    old_prefix: str
    new_prefix: str


def moved_run_roots(record: WorkspaceRecord, *, limit: int = 200) -> list[MovedRoot]:
    """Invocation records whose stored run root does not match this folder, even after mappings."""
    import json

    from scpolya_ui.registry import apply_path_mappings_to_value

    root = Path(record.root_path).expanduser()
    invocations = root / "logs" / "invocations"
    if not invocations.is_dir():
        return []
    try:
        actual = root.resolve(strict=True)
    except OSError:
        return []
    counts: Counter[str] = Counter()
    for index, metadata in enumerate(sorted(invocations.glob("*/invocation.json"))):
        if index >= limit:
            break
        try:
            stored = json.loads(metadata.read_text(encoding="utf-8")).get("run_root")
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(stored, str) or not Path(stored).is_absolute():
            continue
        try:
            mapped = apply_path_mappings_to_value(stored, record.path_mappings)
        except ValueError:
            mapped = stored
        if Path(mapped).expanduser().resolve() != actual:
            counts[stored] += 1
    moved = []
    for stored, count in counts.items():
        old_parts, new_parts = list(Path(stored).parts), list(actual.parts)
        # Map at the deepest folders that differ, so files beside the run root follow too.
        while len(old_parts) > 1 and len(new_parts) > 1 and old_parts[-1] == new_parts[-1]:
            old_parts.pop()
            new_parts.pop()
        if len(old_parts) <= 1 or len(new_parts) <= 1:
            # The whole recorded path reappears inside the new one (a mirrored copy), so the only
            # common prefix is '/'. A '/' mapping would relocate every absolute path; map the run root only.
            old_parts, new_parts = list(Path(stored).parts), list(actual.parts)
        moved.append(MovedRoot(stored, count, str(Path(*old_parts)), str(Path(*new_parts))))
    return moved


@dataclass(frozen=True)
class RunDestination:
    """Where a launch from Run Setup writes, judged the way the engine will use it.

    ``target`` is output_dir/run_id exactly as configured: the engine writes there and never applies
    workspace path mappings. ``mapped_view`` is set when that target is a recorded location which this
    workspace maps to another folder (a moved or copied run): the preview's reuse assumption then does not
    hold for the target, so launching is refused until the output folder is chosen explicitly.
    """

    target: str
    exists: bool
    mapped_view: str = ""
    suggested_output_dir: str = ""
    moved_history: bool = False

    @property
    def blocked(self) -> bool:
        return bool(self.mapped_view)


def run_destination(config: Mapping[str, Any], workspace: WorkspaceRecord | None) -> RunDestination | None:
    from scpolya_ui.registry import apply_path_mappings_to_value

    output_dir = str(config.get("output_dir") or "").strip()
    run_id = str(config.get("run_id") or "").strip()
    if not output_dir:
        return None
    target = str(Path(output_dir).expanduser() / run_id) if run_id else str(Path(output_dir).expanduser())
    exists = Path(target).exists()
    if workspace is None:
        return RunDestination(target=target, exists=exists)
    try:
        mapped = apply_path_mappings_to_value(target, workspace.path_mappings)
    except ValueError:
        mapped = target
    if mapped != target:
        suggested = str(Path(mapped).parent) if run_id and Path(mapped).name == run_id else ""
        return RunDestination(target=target, exists=exists, mapped_view=mapped, suggested_output_dir=suggested)
    moved = False
    if exists:
        # The engine reads the run state as recorded, without UI mappings: history recorded under another
        # root means the engine's moved-run checks apply, even when the UI already maps that root here.
        recorded = _recorded_run_roots(Path(target))
        moved = any(Path(root) != Path(target) for root in recorded)
    return RunDestination(target=target, exists=exists, moved_history=moved)


def _recorded_run_roots(root: Path, *, limit: int = 200) -> set[str]:
    import json

    roots: set[str] = set()
    for index, metadata in enumerate(sorted((root / "logs" / "invocations").glob("*/invocation.json"))):
        if index >= limit:
            break
        try:
            stored = json.loads(metadata.read_text(encoding="utf-8")).get("run_root")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(stored, str) and Path(stored).is_absolute():
            roots.add(stored)
    return roots


def comparison_base_config(
    base_config: Mapping[str, Any], proposed: Mapping[str, Any], workspace: WorkspaceRecord | None
) -> dict[str, Any]:
    """Base configuration to compare a proposal against.

    When the user has explicitly chosen the local copy of a moved run as the output folder (the base run's
    recorded output_dir as this workspace maps it), the base run lives there, so the comparison uses that
    folder and the preview shows only the real setting changes. Nothing else is remapped, and the stored base
    configuration itself is left as recorded.
    """
    from scpolya_ui.registry import apply_path_mappings_to_value

    base = dict(base_config)
    recorded = str(base.get("output_dir") or "")
    if workspace is None or not recorded or not workspace.path_mappings:
        return base
    try:
        mapped = apply_path_mappings_to_value(recorded, workspace.path_mappings)
    except ValueError:
        return base
    if mapped != recorded and str(proposed.get("output_dir") or "") == mapped:
        base["output_dir"] = mapped
    return base


# Inputs the engine requires for every run (arguments.py marks them required) and opens before any task
# is scheduled; optional paths are not judged here so a launch is never refused for a file it may not use.
REQUIRED_INPUT_KEYS = ("inputs", "gene_gtf", "chrom_sizes")


@dataclass(frozen=True)
class LaunchInputProblem:
    """A reason the engine would stop this launch before running any task.

    ``kind`` is one of ``missing_input`` (a required input path does not exist on this computer),
    ``missing_source`` (a ``source_path`` row of the inputs table does not resolve, as ``register_inputs``
    requires), ``immutable_changed`` (a structural setting differs from the one fixed in the target run's state,
    which the engine refuses for the same state database) and ``moved_without_repair`` (the target's history was
    recorded under another folder and "Repair moved run" is off, so its input tables still name that folder).
    """

    kind: str
    key: str
    value: str = ""
    stored: str = ""


def launch_input_problems(
    config: Mapping[str, Any], destination: RunDestination | None
) -> tuple[LaunchInputProblem, ...]:
    import csv

    problems: list[LaunchInputProblem] = []
    for key in REQUIRED_INPUT_KEYS:
        value = str(config.get(key) or "").strip()
        if value and not Path(value).expanduser().exists():
            problems.append(LaunchInputProblem("missing_input", key, value))
    inputs = str(config.get("inputs") or "").strip()
    if inputs and Path(inputs).expanduser().is_file():
        try:
            with open(Path(inputs).expanduser(), newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, UnicodeDecodeError, csv.Error):
            rows = []
        for row in rows:
            source = str((row or {}).get("source_path") or "").strip()
            if not source or not Path(source).is_absolute():
                continue
            try:
                Path(source).expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                problems.append(LaunchInputProblem("missing_source", str(row.get("sample_id") or ""), source))
    if destination is None or not destination.exists or destination.blocked:
        return tuple(problems)
    state_db = Path(destination.target) / "state" / "state.sqlite"
    if state_db.is_file():
        from task_manage import load_run_config_snapshot
        from task_manage.task_runtime import select_immutable_config

        try:
            stored = select_immutable_config(load_run_config_snapshot(state_db))
        except (KeyError, OSError, ValueError, sqlite3.Error):
            stored = None
        if stored is not None:
            proposed = select_immutable_config(config)
            for key in sorted(set(stored) | set(proposed)):
                if stored.get(key) != proposed.get(key):
                    problems.append(LaunchInputProblem(
                        "immutable_changed", key, _problem_text(proposed.get(key)), _problem_text(stored.get(key))
                    ))
    if destination.moved_history and not (config.get("repair_moved_run") or config.get("repair_moved_run_dry_run")):
        problems.append(LaunchInputProblem("moved_without_repair", "repair_moved_run"))
    return tuple(problems)


def _problem_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)
