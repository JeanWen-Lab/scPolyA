from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Tuple

from .artifact_records import (
    DEFAULT_CONTENT_FINGERPRINT_MAX_BYTES,
    ArtifactRecord,
    ArtifactRecordSet,
    FingerprintRequestMode,
    fingerprint_config_value,
    fingerprint_path,
    unresolved_artifact_fingerprint,
)
from .config_keys import config_key_spec
from .run_config_preview import DEFAULT_RUN_CONFIG_SNAPSHOT, load_run_config_snapshot


_OPTIONAL_EXTERNAL_PATH_CONFIG_KEYS = frozenset({"bulk_window_gtf", "bulk_gene_gtf"})


@dataclass(frozen=True)
class TaskInputProvenance:
    """Read-only external-input records associated with one observed instance."""

    instance_id: str
    stage: str
    task_base: str
    records: ArtifactRecordSet
    decision_id: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "instance_id": self.instance_id,
            "stage": self.stage,
            "task_base": self.task_base,
            "decision_id": self.decision_id,
            "artifact_records": self.records.as_dict(),
        }


@dataclass(frozen=True)
class InputProvenanceReport:
    """External `args.*` provenance inventory with no scheduler side effects."""

    state_db_path: str
    snapshot_key: str
    config_available: bool
    config_error: str | None
    source_node_count: int
    task_inputs: Tuple[TaskInputProvenance, ...]

    @property
    def input_instance_count(self) -> int:
        return len(self.task_inputs)

    @property
    def record_count(self) -> int:
        return sum(len(task_input.records.records) for task_input in self.task_inputs)

    @property
    def ready_count(self) -> int:
        return sum(task_input.records.ready_count for task_input in self.task_inputs)

    @property
    def missing_count(self) -> int:
        return sum(task_input.records.missing_count for task_input in self.task_inputs)

    @property
    def empty_count(self) -> int:
        return sum(task_input.records.empty_count for task_input in self.task_inputs)

    @property
    def unresolved_count(self) -> int:
        return sum(task_input.records.unresolved_count for task_input in self.task_inputs)

    def as_dict(self, *, limit: int | None = None) -> dict[str, object]:
        task_inputs = tuple(sorted(self.task_inputs, key=lambda entry: entry.instance_id))
        if limit is not None:
            task_inputs = task_inputs[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "snapshot_key": self.snapshot_key,
            "config_available": self.config_available,
            "config_error": self.config_error,
            "source_node_count": self.source_node_count,
            "input_instance_count": self.input_instance_count,
            "record_count": self.record_count,
            "ready_count": self.ready_count,
            "missing_count": self.missing_count,
            "empty_count": self.empty_count,
            "unresolved_count": self.unresolved_count,
            "task_inputs": [task_input.as_dict() for task_input in task_inputs],
        }


def input_artifact_records_for_task_instance(
    task_instance: Any,
    config: Mapping[str, Any] | None,
    *,
    fingerprint_mode: FingerprintRequestMode = "auto",
    content_max_bytes: int = DEFAULT_CONTENT_FINGERPRINT_MAX_BYTES,
    record_source: str = "run_config_snapshot",
) -> ArtifactRecordSet:
    """Fingerprint only declared `args.*` inputs for one observed instance."""

    return input_artifact_records_from_declared_inputs(
        stage=str(getattr(task_instance, "stage")),
        task_base=str(getattr(task_instance, "task_base")),
        consumes=tuple(str(value) for value in getattr(task_instance, "consumes")),
        config=config,
        fingerprint_mode=fingerprint_mode,
        content_max_bytes=content_max_bytes,
        record_source=record_source,
    )


def input_artifact_records_from_declared_inputs(
    *,
    stage: str,
    task_base: str,
    consumes: Iterable[str],
    config: Mapping[str, Any] | None,
    fingerprint_mode: FingerprintRequestMode = "auto",
    content_max_bytes: int = DEFAULT_CONTENT_FINGERPRINT_MAX_BYTES,
    record_source: str = "run_config_snapshot",
) -> ArtifactRecordSet:
    """Return deterministic input records for declared external config inputs."""

    artifact_keys = tuple(
        dict.fromkeys(
            artifact_key
            for artifact_key in consumes
            if artifact_key.startswith("args.") and len(artifact_key) > len("args.")
        )
    )
    records = tuple(
        _input_record_for_artifact(
            artifact_key=artifact_key,
            stage=stage,
            task_base=task_base,
            config=config,
            fingerprint_mode=fingerprint_mode,
            content_max_bytes=content_max_bytes,
            record_source=record_source,
        )
        for artifact_key in artifact_keys
    )
    return ArtifactRecordSet(records=records)


def build_input_provenance_report(
    nodes: Iterable[Any],
    config: Mapping[str, Any] | None,
    *,
    state_db_path: str = "",
    snapshot_key: str = DEFAULT_RUN_CONFIG_SNAPSHOT,
    config_error: str | None = None,
    fingerprint_mode: FingerprintRequestMode = "auto",
    content_max_bytes: int = DEFAULT_CONTENT_FINGERPRINT_MAX_BYTES,
) -> InputProvenanceReport:
    """Group declared external-input records by latest observed instance."""

    source_nodes = tuple(nodes)
    task_inputs: list[TaskInputProvenance] = []
    for node in sorted(source_nodes, key=lambda value: str(getattr(value, "instance_id"))):
        records = input_artifact_records_for_task_instance(
            node,
            config,
            fingerprint_mode=fingerprint_mode,
            content_max_bytes=content_max_bytes,
        )
        if not records.records:
            continue
        task_inputs.append(
            TaskInputProvenance(
                instance_id=str(getattr(node, "instance_id")),
                stage=str(getattr(node, "stage")),
                task_base=str(getattr(node, "task_base")),
                records=records,
                decision_id=_optional_int(getattr(node, "decision_id", None)),
            )
        )
    return InputProvenanceReport(
        state_db_path=state_db_path,
        snapshot_key=snapshot_key,
        config_available=config is not None,
        config_error=config_error,
        source_node_count=len(source_nodes),
        task_inputs=tuple(task_inputs),
    )


def build_input_provenance_report_from_state(
    state_or_db_path: str | Path,
    *,
    snapshot_key: str = DEFAULT_RUN_CONFIG_SNAPSHOT,
    fingerprint_mode: FingerprintRequestMode = "auto",
    content_max_bytes: int = DEFAULT_CONTENT_FINGERPRINT_MAX_BYTES,
) -> InputProvenanceReport:
    """Read a run's latest decisions and config snapshot without mutating state."""

    from .instance_graph import build_dynamic_instance_graph

    graph = build_dynamic_instance_graph(state_or_db_path)
    config: Mapping[str, Any] | None = None
    config_error: str | None = None
    db_path = Path(graph.state_db_path)
    if not db_path.exists():
        config_error = f"state database does not exist: {db_path}"
    else:
        try:
            config = load_run_config_snapshot(db_path, snapshot_key=snapshot_key)
        except (KeyError, OSError, TypeError, ValueError, sqlite3.Error) as exc:
            config_error = f"{type(exc).__name__}: {exc}"
    return build_input_provenance_report(
        graph.nodes,
        config,
        state_db_path=graph.state_db_path,
        snapshot_key=snapshot_key,
        config_error=config_error,
        fingerprint_mode=fingerprint_mode,
        content_max_bytes=content_max_bytes,
    )


def format_input_provenance_report(report: InputProvenanceReport, *, limit: int = 20) -> str:
    lines = [
        f"External input provenance: {report.state_db_path or '-'}",
        f"config snapshot: {'available' if report.config_available else 'unavailable'} ({report.snapshot_key})",
        f"observed nodes: {report.source_node_count}",
        f"input instances: {report.input_instance_count}",
        (
            "records: "
            f"{report.record_count} ready={report.ready_count} missing={report.missing_count} "
            f"empty={report.empty_count} unresolved={report.unresolved_count}"
        ),
    ]
    if report.config_error:
        lines.append(f"config error: {report.config_error}")
    task_inputs = tuple(sorted(report.task_inputs, key=lambda entry: entry.instance_id))[: max(0, int(limit))]
    if task_inputs:
        lines.append("instances:")
        for task_input in task_inputs:
            lines.append(
                f"  {task_input.instance_id[:12]} {task_input.stage}:{task_input.task_base} "
                f"records={len(task_input.records.records)}"
            )
            for record in task_input.records.records:
                fingerprint_mode = record.fingerprint.mode if record.fingerprint else "-"
                lines.append(
                    f"    {record.artifact_key} status={record.path_status} mode={fingerprint_mode} "
                    f"path={record.path}"
                )
    return "\n".join(lines)


def _input_record_for_artifact(
    *,
    artifact_key: str,
    stage: str,
    task_base: str,
    config: Mapping[str, Any] | None,
    fingerprint_mode: FingerprintRequestMode,
    content_max_bytes: int,
    record_source: str,
) -> ArtifactRecord:
    config_key = artifact_key.removeprefix("args.")
    config_uri = f"config://{artifact_key}"
    common = {
        "role": "input",
        "artifact_key": artifact_key,
        "stage": stage,
        "task_base": task_base,
        "label": config_key,
        "required": True,
        "source": record_source,
    }
    if config is None:
        fingerprint = unresolved_artifact_fingerprint(config_uri, "run config snapshot is unavailable")
        return ArtifactRecord(
            path=config_uri,
            expectation_mode="external_config",
            expectation_status="snapshot_unavailable",
            path_status=fingerprint.status,
            fingerprint=fingerprint,
            **common,
        )
    if config_key not in config:
        fingerprint = unresolved_artifact_fingerprint(config_uri, "declared config key is absent from snapshot")
        return ArtifactRecord(
            path=config_uri,
            expectation_mode="external_config",
            expectation_status="config_key_absent",
            path_status=fingerprint.status,
            fingerprint=fingerprint,
            **common,
        )

    spec = config_key_spec(config_key)
    if spec is None:
        fingerprint = unresolved_artifact_fingerprint(config_uri, "config key type is not registered")
        return ArtifactRecord(
            path=config_uri,
            expectation_mode="external_config",
            expectation_status="unknown_config_key",
            path_status=fingerprint.status,
            fingerprint=fingerprint,
            **common,
        )

    value = config[config_key]
    if not spec.has_category("path"):
        fingerprint = fingerprint_config_value(
            artifact_key,
            value,
            detail="persisted scalar config value",
        )
        return ArtifactRecord(
            path=fingerprint.path,
            expectation_mode="external_config_scalar",
            expectation_status="config_value",
            path_status=fingerprint.status,
            fingerprint=fingerprint,
            **common,
        )

    if value is None:
        if config_key not in _OPTIONAL_EXTERNAL_PATH_CONFIG_KEYS:
            fingerprint = unresolved_artifact_fingerprint(config_uri, "required path config is explicitly absent")
            return ArtifactRecord(
                path=config_uri,
                expectation_mode="external_config_path",
                expectation_status="required_path_absent",
                path_status=fingerprint.status,
                fingerprint=fingerprint,
                **common,
            )
        fingerprint = fingerprint_config_value(
            artifact_key,
            value,
            detail="persisted path config explicitly absent",
        )
        return ArtifactRecord(
            path=fingerprint.path,
            expectation_mode="external_config_path",
            expectation_status="config_path_not_applicable",
            path_status=fingerprint.status,
            fingerprint=fingerprint,
            **{**common, "required": False},
        )
    if not isinstance(value, (str, Path)):
        fingerprint = unresolved_artifact_fingerprint(config_uri, "path config value is not a string or Path")
        return ArtifactRecord(
            path=config_uri,
            expectation_mode="external_config_path",
            expectation_status="invalid_path_config",
            path_status=fingerprint.status,
            fingerprint=fingerprint,
            **common,
        )

    raw_path = str(value)
    if not raw_path.strip():
        if config_key in _OPTIONAL_EXTERNAL_PATH_CONFIG_KEYS:
            fingerprint = fingerprint_config_value(
                artifact_key,
                value,
                detail="persisted optional path config explicitly blank",
            )
            return ArtifactRecord(
                path=fingerprint.path,
                expectation_mode="external_config_path",
                expectation_status="config_path_not_applicable",
                path_status=fingerprint.status,
                fingerprint=fingerprint,
                **{**common, "required": False},
            )
        fingerprint = unresolved_artifact_fingerprint(config_uri, "path config value is blank")
        return ArtifactRecord(
            path=config_uri,
            expectation_mode="external_config_path",
            expectation_status="blank_path_config",
            path_status=fingerprint.status,
            fingerprint=fingerprint,
            **common,
        )
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        fingerprint = unresolved_artifact_fingerprint(
            str(path),
            "relative path lacks a persisted working-directory base",
        )
        return ArtifactRecord(
            path=str(path),
            expectation_mode="external_config_path",
            expectation_status="relative_path_unresolved",
            path_status=fingerprint.status,
            fingerprint=fingerprint,
            **common,
        )

    try:
        fingerprint = fingerprint_path(
            path,
            mode=fingerprint_mode,
            content_max_bytes=content_max_bytes,
        )
    except OSError as exc:
        fingerprint = unresolved_artifact_fingerprint(str(path), str(exc))
    return ArtifactRecord(
        path=fingerprint.path,
        expectation_mode="external_config_path",
        expectation_status="path_fingerprinted" if fingerprint.status != "unresolved" else "path_unreadable",
        path_status=fingerprint.status,
        fingerprint=fingerprint,
        **common,
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
