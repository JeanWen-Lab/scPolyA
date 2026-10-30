from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from scpolya_ui.registry import ui_home
from scpolya_ui.run_records import RunRecordRegistry, recent_run_records
from scpolya_ui.view_models.artifact_vm import ArtifactState, run_record_final_primary_file_readiness


ARTIFACT_INDEX_VERSION = 2


@dataclass(frozen=True)
class ArtifactIndexEntry:
    run_record_id: str
    workspace_id: str
    label: str
    role: str
    path: str
    status: str
    size_bytes: int


@dataclass(frozen=True)
class ArtifactIndex:
    version: int
    workspace_id: str
    total_count: int
    ready_count: int
    missing_count: int
    entries: tuple[ArtifactIndexEntry, ...]
    unavailable_count: int = 0
    warnings: tuple[str, ...] = ()


def artifact_index_path(home: str | Path | None = None) -> Path:
    root = Path(home).expanduser() if home is not None else ui_home()
    return root / "artifact_index.json"


def build_artifact_index(
    run_records: RunRecordRegistry,
    *,
    workspace_id: str | None = None,
    limit: int = 200,
) -> ArtifactIndex:
    entries: list[ArtifactIndexEntry] = []
    warnings: list[str] = []
    for record in recent_run_records(run_records, workspace_id=workspace_id, limit=limit):
        readiness = run_record_final_primary_file_readiness(record)
        if not readiness.available:
            warnings.append(f"{record.id}: {readiness.error}")
            continue
        for artifact in readiness.artifacts:
            entries.append(_entry(record.id, record.workspace_id, record.label, artifact))
    ready = sum(1 for item in entries if item.status == "ready")
    missing = sum(1 for item in entries if item.status == "missing")
    return ArtifactIndex(
        version=ARTIFACT_INDEX_VERSION,
        workspace_id=workspace_id or "",
        total_count=len(entries),
        ready_count=ready,
        missing_count=missing,
        entries=tuple(entries),
        unavailable_count=len(warnings),
        warnings=tuple(warnings),
    )


def refresh_artifact_index(
    run_records: RunRecordRegistry,
    *,
    workspace_id: str | None = None,
    home: str | Path | None = None,
) -> ArtifactIndex:
    index = build_artifact_index(run_records, workspace_id=workspace_id)
    save_artifact_index(index, home=home)
    return index


def load_artifact_index(home: str | Path | None = None) -> ArtifactIndex:
    path = artifact_index_path(home)
    if not path.exists():
        return ArtifactIndex(version=ARTIFACT_INDEX_VERSION, workspace_id="", total_count=0, ready_count=0, missing_count=0, entries=())
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = tuple(ArtifactIndexEntry(**item) for item in data.get("entries", []))
    return ArtifactIndex(
        version=int(data.get("version", ARTIFACT_INDEX_VERSION)),
        workspace_id=str(data.get("workspace_id") or ""),
        total_count=int(data.get("total_count", len(entries))),
        ready_count=int(data.get("ready_count", sum(1 for item in entries if item.status == "ready"))),
        missing_count=int(data.get("missing_count", sum(1 for item in entries if item.status == "missing"))),
        entries=entries,
        unavailable_count=int(data.get("unavailable_count", 0)),
        warnings=tuple(str(item) for item in data.get("warnings", ())),
    )


def save_artifact_index(index: ArtifactIndex, *, home: str | Path | None = None) -> None:
    path = artifact_index_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(index)
    payload["entries"] = [asdict(entry) for entry in index.entries]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _entry(record_id: str, workspace_id: str, record_label: str, artifact: ArtifactState) -> ArtifactIndexEntry:
    return ArtifactIndexEntry(
        run_record_id=record_id,
        workspace_id=workspace_id,
        label=f"{record_label}: {artifact.label}",
        role=artifact.role,
        path=artifact.path,
        status=artifact.status,
        size_bytes=artifact.size_bytes,
    )
