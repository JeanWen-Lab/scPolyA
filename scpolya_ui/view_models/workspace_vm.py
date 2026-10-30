from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scpolya_ui.registry import PathMapping, WorkspaceRecord, path_mapping_problem


@dataclass(frozen=True)
class WorkspaceOption:
    id: str
    label: str
    root_path: str


@dataclass(frozen=True)
class WorkspaceSummary:
    id: str
    label: str
    root_path: str
    db_path: str
    db_exists: bool
    mapping_count: int = 0
    run_count: int = 0
    study_count: int = 0
    job_count: int = 0
    updated_at: str = ""
    last_opened_at: str = ""


@dataclass(frozen=True)
class PathMappingSummary:
    old_path: str
    new_path: str
    note: str
    created_at: str
    # Why a saved mapping is not applied ('relative', 'traversal', 'same', 'duplicate'); empty if it is.
    problem: str = ""


def workspace_option(record: WorkspaceRecord) -> WorkspaceOption:
    return WorkspaceOption(id=record.id, label=record.alias or record.root_path, root_path=record.root_path)


def workspace_summary(
    record: WorkspaceRecord,
    *,
    run_count: int = 0,
    study_count: int = 0,
    job_count: int = 0,
) -> WorkspaceSummary:
    return WorkspaceSummary(
        id=record.id,
        label=record.alias or record.root_path,
        root_path=record.root_path,
        db_path=record.db_path,
        db_exists=Path(record.db_path).expanduser().exists(),
        mapping_count=len(record.path_mappings) + len(record.ignored_path_mappings),
        run_count=run_count,
        study_count=study_count,
        job_count=job_count,
        updated_at=record.updated_at,
        last_opened_at=record.last_opened_at,
    )


def path_mapping_summary(mapping: PathMapping, *, problem: str = "") -> PathMappingSummary:
    return PathMappingSummary(
        old_path=mapping.old_path,
        new_path=mapping.new_path,
        note=mapping.note,
        created_at=mapping.created_at,
        problem=problem,
    )


def path_mapping_summaries(record: WorkspaceRecord) -> list[PathMappingSummary]:
    usable = [path_mapping_summary(mapping) for mapping in record.path_mappings]
    ignored = [
        path_mapping_summary(
            mapping, problem=path_mapping_problem(mapping.old_path, mapping.new_path) or "duplicate"
        )
        for mapping in record.ignored_path_mappings
    ]
    return usable + ignored
