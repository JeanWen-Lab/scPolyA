from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scpolya_ui.run_records import RunRecord, resolve_execution_config
from scpolya_ui.view_models.artifact_vm import (
    ArtifactState,
    run_record_final_primary_file_readiness,
)


@dataclass(frozen=True)
class RunRecordBadge:
    id: str
    label: str
    status: str
    kind: str


@dataclass(frozen=True)
class RunRecordSummary:
    id: str
    label: str
    status: str
    kind: str
    run_id: str
    config_hash: str
    output_dir: str
    root_path: str
    db_path: str
    db_exists: bool
    command: str = ""
    job_id: str = ""
    study_id: str = ""
    variant_id: str = ""
    ready_artifacts: int = 0
    total_artifacts: int = 0
    artifacts: tuple[ArtifactState, ...] = ()
    artifact_readiness_available: bool = False
    artifact_readiness_error: str = ""
    created_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    note: str = ""
    invocation_id: str = ""
    history_source: str = ""
    config_source: str = ""
    raw_status: str = ""
    invocation_error: str = ""


@dataclass(frozen=True)
class LogicalRunSummary:
    id: str
    label: str
    status: str
    run_id: str
    config_hash: str
    output_dir: str
    latest_record_id: str
    attempt_count: int
    latest: RunRecordSummary
    attempts: tuple[RunRecordSummary, ...]
    succeeded_count: int = 0
    failed_count: int = 0
    unknown_count: int = 0


def run_record_badge(record: RunRecord) -> RunRecordBadge:
    return RunRecordBadge(id=record.id, label=record.label, status=record.status, kind=record.kind)


def run_record_summary(record: RunRecord) -> RunRecordSummary:
    resolution = resolve_execution_config(record)
    readiness = run_record_final_primary_file_readiness(record)
    artifacts = readiness.artifacts
    ready = sum(1 for artifact in artifacts if artifact.status == "ready")
    return RunRecordSummary(
        id=record.id,
        label=record.label,
        status=record.status,
        kind=record.kind,
        run_id=record.run_id,
        config_hash=(
            record.invocation_config_hash
            or resolution.config_hash
            or record.execution_config_hash
            or record.config_hash
        ),
        output_dir=record.output_dir,
        root_path=record.root_path or record.workspace_root,
        db_path=record.db_path,
        db_exists=bool(record.db_path) and Path(record.db_path).expanduser().exists(),
        command=record.command,
        job_id=record.job_id,
        study_id=record.study_id,
        variant_id=record.variant_id,
        ready_artifacts=ready,
        total_artifacts=len(artifacts),
        artifacts=artifacts,
        artifact_readiness_available=readiness.available,
        artifact_readiness_error=readiness.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        finished_at=record.finished_at or (
            record.updated_at
            if record.status in {"succeeded", "failed", "stopped", "exited"}
            else ""
        ),
        note=record.note,
        invocation_id=record.invocation_id,
        history_source=record.history_source,
        config_source=record.config_source or resolution.source,
        raw_status=record.raw_status,
        invocation_error=record.invocation_error,
    )


def logical_run_summaries(
    records: Iterable[RunRecord],
) -> list[LogicalRunSummary]:
    groups: dict[tuple[str, ...], list[tuple[RunRecord, RunRecordSummary]]] = {}
    for record in records:
        summary = run_record_summary(record)
        key = _logical_run_key(record, summary)
        groups.setdefault(key, []).append((record, summary))

    logical_runs: list[LogicalRunSummary] = []
    for key, record_attempts in groups.items():
        execution_attempts = [
            (record, summary)
            for record, summary in record_attempts
            if record.kind != "base"
        ]
        visible_attempts = execution_attempts or record_attempts
        visible_attempts.sort(
            key=lambda item: (item[1].created_at, item[1].updated_at, item[1].id),
            reverse=True,
        )
        attempts = tuple(summary for _record, summary in visible_attempts)
        latest = attempts[0]
        logical_runs.append(
            LogicalRunSummary(
                id=_logical_run_id(key),
                label=latest.label,
                status=latest.status,
                run_id=latest.run_id,
                config_hash=latest.config_hash,
                output_dir=latest.output_dir,
                latest_record_id=latest.id,
                attempt_count=len(attempts),
                latest=latest,
                attempts=tuple(attempts),
                succeeded_count=sum(item.status == "succeeded" for item in attempts),
                failed_count=sum(item.status in {"failed", "cancelled", "stopped", "exited"} for item in attempts),
                unknown_count=sum(item.status == "unknown" for item in attempts),
            )
        )
    return logical_runs


def _logical_run_key(record: RunRecord, summary: RunRecordSummary) -> tuple[str, ...]:
    root_path = str(Path(summary.root_path).expanduser().resolve()) if summary.root_path else ""
    if root_path:
        return ("execution", record.workspace_id, root_path)
    output_dir = str(Path(summary.output_dir).expanduser().resolve()) if summary.output_dir else ""
    if not output_dir or not summary.run_id:
        return ("record", record.id)
    return (
        "execution",
        record.workspace_id,
        output_dir,
        summary.run_id,
    )


def _logical_run_id(key: tuple[str, ...]) -> str:
    payload = json.dumps(key, ensure_ascii=True, separators=(",", ":"))
    return f"LR-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"
