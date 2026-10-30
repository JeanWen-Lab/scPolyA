from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scpolya_ui.run_records import RunRecord, resolve_execution_config
from task_manage import project_run_outputs_v1
from task_manage.output_projection import ANNOTATION_SCHEMA_NOT_OPENED


@dataclass(frozen=True)
class ArtifactState:
    path: str
    status: str
    label: str = ""
    exists: bool = False
    size_bytes: int = 0
    role: str = ""


@dataclass(frozen=True)
class ArtifactReadiness:
    artifacts: tuple[ArtifactState, ...] = ()
    available: bool = False
    error: str = ""


def artifact_state(path: str | Path | None, *, label: str = "", role: str = "") -> ArtifactState:
    if not path:
        return ArtifactState(path="", status="missing", label=label, role=role)
    item = Path(path).expanduser()
    exists = item.is_file()
    size = item.stat().st_size if exists else 0
    # The producer always writes this audit; zero bytes means no such peaks.
    allow_empty = role == "final.not_evaluable_peaks_audit"
    status = "ready" if exists and (size > 0 or allow_empty) else "missing"
    return ArtifactState(path=str(item), status=status, label=label or item.name, exists=exists, size_bytes=size, role=role)


def artifact_states_from_mapping(artifacts: Mapping[str, object]) -> tuple[ArtifactState, ...]:
    return tuple(
        artifact_state(str(path), label=key, role=key)
        for key, path in sorted(artifacts.items())
        if isinstance(path, str) and path
    )


def final_primary_file_readiness(config: Mapping[str, Any]) -> ArtifactReadiness:
    try:
        projection = project_run_outputs_v1(config, annotation_schema="recorded")
    except Exception as exc:
        return ArtifactReadiness(error=f"Output projection failed: {type(exc).__name__}: {exc}")

    # A refused recorded annotation schema omits only annotation-scoped
    # outputs; peak-level artifacts of the run stay listed.
    schema_notes = tuple(w for w in projection.warnings if w.startswith(ANNOTATION_SCHEMA_NOT_OPENED))
    fatal = tuple(w for w in projection.warnings if w not in schema_notes)
    if fatal:
        return ArtifactReadiness(error="; ".join(fatal))

    scopes = tuple(
        scope
        for scope in projection.scopes
        if scope.scope_id.startswith("final.")
        and scope.path_kind == "file"
        and scope.retention == "primary"
    )
    if not scopes:
        return ArtifactReadiness(error="No primary final file outputs were projected")

    artifacts = list(
            artifact_state(scope.path, label=scope.label, role=scope.scope_id)
            for scope in scopes
    )
    artifacts.extend(
        ArtifactState(path="", status="missing", label=note, role="annotation_schema_not_opened")
        for note in schema_notes
    )
    # Summary/count namespaces are projected directories, so the final-file
    # list alone omitted all native APA tables. Enumerate only these scopes.
    for scope in projection.scopes:
        if scope.scope_id not in {"matrix_summary", "count_matrix", "rna_gene_matrix"}:
            continue
        directory = Path(scope.path)
        root = Path(projection.run_root) if projection.run_root else None
        if not directory.is_dir() or root is None or not directory.resolve().is_relative_to(root.resolve()):
            continue
        for path in sorted(directory.glob("*.tsv.gz"))[:256]:
            if path.resolve().is_relative_to(root.resolve()):
                artifacts.append(artifact_state(path, label=path.name, role=f"native_table.{scope.scope_id}"))
    return ArtifactReadiness(
        artifacts=tuple(artifacts),
        available=True,
    )


def run_record_final_primary_file_readiness(record: RunRecord) -> ArtifactReadiness:
    if record.history_source == "invocation":
        return ArtifactReadiness(
            error="Historical artifact snapshot is unavailable for this CLI invocation"
        )
    resolution = resolve_execution_config(record)
    if not resolution.available:
        return ArtifactReadiness(error=resolution.error)
    return final_primary_file_readiness(resolution.config)
