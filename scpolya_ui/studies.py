from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, List

from task_manage import ParameterStudyPreview

from .command_builder import RunCommand
from .registry import WorkspaceRecord, ui_home, utc_now


STUDIES_VERSION = 1


@dataclass
class StudyVariantRecord:
    id: str
    label: str
    overrides: dict[str, Any]
    config: dict[str, Any]
    command: str
    argv: List[str]
    job_id: str = ""
    run_record_id: str = ""
    changed_raw_keys: List[str] = field(default_factory=list)
    changed_effective_keys: List[str] = field(default_factory=list)
    impacted_task_count: int = 0
    annotated_bed: str = ""
    annotated_bigbed: str = ""
    annotated_gtf: str = ""


@dataclass
class StudyRecord:
    id: str
    status: str
    workspace_id: str
    workspace_root: str
    name: str
    genes: List[str]
    grid: dict[str, Any]
    variants: List[StudyVariantRecord]
    created_at: str
    updated_at: str
    started_at: str = ""
    finished_at: str = ""
    note: str = ""
    base_run_record_id: str = ""
    base_run_label: str = ""


@dataclass
class StudyRegistry:
    version: int = STUDIES_VERSION
    studies: List[StudyRecord] = field(default_factory=list)


def studies_path(home: Path | None = None) -> Path:
    return (home or ui_home()) / "studies.json"


def load_studies(home: Path | None = None) -> StudyRegistry:
    path = studies_path(home)
    if not path.exists():
        return StudyRegistry()
    data = json.loads(path.read_text(encoding="utf-8"))
    studies = []
    for item in data.get("studies", []):
        variants = [StudyVariantRecord(**variant) for variant in item.get("variants", [])]
        payload = dict(item)
        payload["variants"] = variants
        studies.append(StudyRecord(**payload))
    return StudyRegistry(version=int(data.get("version", STUDIES_VERSION)), studies=studies)


def save_studies(registry: StudyRegistry, home: Path | None = None) -> None:
    path = studies_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": registry.version,
        "studies": [asdict(study) for study in registry.studies],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def create_study(
    registry: StudyRegistry,
    *,
    workspace: WorkspaceRecord,
    preview: ParameterStudyPreview,
    commands: dict[str, RunCommand],
    name: str,
    genes: Iterable[str] = (),
    grid: dict[str, Any] | None = None,
    base_run_record_id: str = "",
    base_run_label: str = "",
) -> StudyRecord:
    now = utc_now()
    study = StudyRecord(
        id=f"S-{uuid.uuid4().hex[:12]}",
        status="draft",
        workspace_id=workspace.id,
        workspace_root=workspace.root_path,
        name=name.strip() or f"Study {now}",
        genes=list(genes),
        grid=dict(grid or {}),
        base_run_record_id=base_run_record_id,
        base_run_label=base_run_label,
        variants=[
            _variant_record(variant, commands[variant.id])
            for variant in preview.variants
            if variant.id in commands
        ],
        created_at=now,
        updated_at=now,
    )
    registry.studies.insert(0, study)
    return study


def recent_studies(
    registry: StudyRegistry,
    *,
    workspace_id: str | None = None,
    limit: int = 20,
) -> list[StudyRecord]:
    studies: Iterable[StudyRecord] = registry.studies
    if workspace_id is not None:
        studies = (study for study in studies if study.workspace_id == workspace_id)
    return list(studies)[:limit]


def find_study(registry: StudyRegistry, study_id: str | None) -> StudyRecord | None:
    if not study_id:
        return None
    return next((study for study in registry.studies if study.id == study_id), None)


def parse_gene_list(text: str) -> list[str]:
    genes = []
    for token in text.replace(",", "\n").splitlines():
        gene = token.strip()
        if gene and gene not in genes:
            genes.append(gene)
    return genes


def terminal_study_status(statuses: Iterable[str]) -> str:
    status_set = set(statuses)
    if not status_set:
        return "draft"
    if status_set <= {"succeeded"}:
        return "succeeded"
    if "failed" in status_set or "exited" in status_set:
        return "failed"
    if "stopped" in status_set:
        return "stopped"
    if status_set <= {"planned"}:
        return "draft"
    return "running"


def _variant_record(variant: Any, command: RunCommand) -> StudyVariantRecord:
    artifacts = variant.peak_artifacts
    return StudyVariantRecord(
        id=variant.id,
        label=variant.label,
        overrides=dict(variant.overrides),
        config=dict(variant.config),
        command=command.shell_command,
        argv=list(command.argv),
        changed_raw_keys=list(variant.changed_raw_keys),
        changed_effective_keys=list(variant.changed_effective_keys),
        impacted_task_count=variant.impacted_task_count,
        annotated_bed=artifacts.annotated_bed if artifacts else "",
        annotated_bigbed=artifacts.annotated_bigbed if artifacts else "",
        annotated_gtf=artifacts.annotated_gtf if artifacts else "",
    )
