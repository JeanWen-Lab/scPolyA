from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scpolya_ui.jobs import JobRegistry, load_jobs, save_jobs
from scpolya_ui.registry import WorkspaceRegistry, load_registry, save_registry
from scpolya_ui.run_records import (
    RunRecordRegistry,
    load_run_records,
    save_run_records,
    sync_run_records_from_invocations,
)
from scpolya_ui.studies import StudyRegistry, load_studies, save_studies


@dataclass
class ProductRegistries:
    workspaces: WorkspaceRegistry
    jobs: JobRegistry
    run_records: RunRecordRegistry
    studies: StudyRegistry


def load_product_registries(home: str | Path | None = None) -> ProductRegistries:
    home_path = Path(home).expanduser() if home is not None else None
    registries = ProductRegistries(
        workspaces=load_registry(home_path),
        jobs=load_jobs(home_path),
        run_records=load_run_records(home_path),
        studies=load_studies(home_path),
    )
    if sync_run_records_from_invocations(
        registries.run_records,
        registries.jobs,
        registries.workspaces.workspaces,
    ):
        save_run_records(registries.run_records, home_path)
    return registries


def save_product_registries(registries: ProductRegistries, home: str | Path | None = None) -> None:
    home_path = Path(home).expanduser() if home is not None else None
    save_registry(registries.workspaces, home_path)
    save_jobs(registries.jobs, home_path)
    save_run_records(registries.run_records, home_path)
    save_studies(registries.studies, home_path)
