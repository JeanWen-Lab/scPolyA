"""Decision-time metadata snapshots for declared external inputs."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Tuple

from .artifact_records import ArtifactPathFingerprint, fingerprint_path
from .config_keys import config_key_spec
from .input_provenance import input_artifact_records_for_task_instance
from .task_instance_v2 import stable_task_json_hash
from .task_instance_v3 import TaskInstanceV3


DECISION_INPUT_MANIFEST_V1_KEY = "decision_input_manifest_v1"
DECISION_INPUT_MANIFEST_V1_SCHEMA = "decision-input-manifest-v1"
DECISION_INPUT_FINGERPRINT_POLICY_V1 = "metadata-v1"

DecisionInputCaptureStatus = Literal["captured", "captured_with_warnings", "error"]

_DIAGNOSTIC_ONLY = "diagnostic_only"
_WARNING_ONLY = "warning_only"


def attach_decision_input_manifest_v1(
    task_instance: TaskInstanceV3,
    config: Mapping[str, Any] | None,
) -> TaskInstanceV3:
    """Return the instance with an additive decision-time input snapshot."""

    manifest = build_decision_input_manifest_v1(task_instance, config)
    if manifest is None:
        return task_instance
    source_contracts = dict(task_instance.source_contracts)
    source_contracts[DECISION_INPUT_MANIFEST_V1_KEY] = manifest
    return replace(task_instance, source_contracts=source_contracts)


def build_decision_input_manifest_v1(
    task_instance: TaskInstanceV3,
    config: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Capture metadata only for declared ``args.*`` inputs.

    File bodies are never hashed. The input CSV is parsed only to discover the
    source BAM paths selected by the register-inputs stage.
    """

    external_keys = _external_artifact_keys(task_instance.consumes)
    if not external_keys:
        return None
    captured_at = datetime.now(UTC).isoformat()
    try:
        normalized_config = _normalized_config(config, external_keys)
        declared_records = input_artifact_records_for_task_instance(
            task_instance,
            normalized_config,
            fingerprint_mode="metadata",
            record_source="decision_config",
        )
        nested_records, nested_reasons = _nested_input_records(
            external_keys=external_keys,
            config=normalized_config,
        )
        reasons = [
            *(
                f"declared input {record.artifact_key} status={record.path_status}"
                for record in declared_records.records
                if record.path_status != "ready"
            ),
            *nested_reasons,
        ]
        declared_payload = declared_records.as_dict()
        nested_payload = list(nested_records)
        snapshot_hash = stable_task_json_hash(
            {
                "fingerprint_policy_version": DECISION_INPUT_FINGERPRINT_POLICY_V1,
                "declared_record_hashes": sorted(
                    record.record_hash for record in declared_records.records
                ),
                "nested_records": nested_payload,
            }
        )
        status: DecisionInputCaptureStatus = (
            "captured_with_warnings" if reasons else "captured"
        )
        return _manifest_payload(
            task_instance=task_instance,
            captured_at=captured_at,
            status=status,
            declared_inputs=declared_payload,
            nested_inputs=nested_payload,
            snapshot_hash=snapshot_hash,
            reasons=tuple(reasons),
        )
    except Exception as exc:  # Diagnostics must not interrupt legacy scheduling.
        return _manifest_payload(
            task_instance=task_instance,
            captured_at=captured_at,
            status="error",
            declared_inputs=None,
            nested_inputs=[],
            snapshot_hash=None,
            reasons=(f"input manifest capture failed: {type(exc).__name__}: {exc}",),
        )


def _manifest_payload(
    *,
    task_instance: TaskInstanceV3,
    captured_at: str,
    status: DecisionInputCaptureStatus,
    declared_inputs: Mapping[str, Any] | None,
    nested_inputs: list[dict[str, Any]],
    snapshot_hash: str | None,
    reasons: Tuple[str, ...],
) -> dict[str, Any]:
    return {
        "schema_version": DECISION_INPUT_MANIFEST_V1_SCHEMA,
        "status": status,
        "strict_effect": _DIAGNOSTIC_ONLY,
        "change_effect": _WARNING_ONLY,
        "generated_from": {
            "source": "decision_task_instance_v2",
            "implementation_version": task_instance.implementation_version,
            "captured_at": captured_at,
        },
        "task_key": {
            "stage": task_instance.stage,
            "task_base": task_instance.task_base,
        },
        "fingerprint_policy": {
            "version": DECISION_INPUT_FINGERPRINT_POLICY_V1,
            "path_normalization": "expanduser_resolve",
            "file_fields": [
                "path",
                "exists",
                "is_file",
                "is_dir",
                "size_bytes",
                "mtime_ns",
            ],
            "content_hashing": "disabled",
            "directory_membership": "exact_sorted_sample_bams",
        },
        "snapshot_hash": snapshot_hash,
        "declared_inputs": declared_inputs,
        "nested_inputs": nested_inputs,
        "reasons": list(reasons),
    }


def _external_artifact_keys(consumes: Iterable[str]) -> Tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            artifact_key
            for artifact_key in (str(value) for value in consumes)
            if artifact_key.startswith("args.") and len(artifact_key) > len("args.")
        )
    )


def _normalized_config(
    config: Mapping[str, Any] | None,
    artifact_keys: Iterable[str],
) -> Mapping[str, Any] | None:
    if config is None:
        return None
    normalized = dict(config)
    for artifact_key in artifact_keys:
        config_key = artifact_key.removeprefix("args.")
        spec = config_key_spec(config_key)
        value = normalized.get(config_key)
        if (
            spec is not None
            and spec.has_category("path")
            and isinstance(value, (str, Path))
            and str(value).strip()
        ):
            normalized[config_key] = str(_canonical_path(value))
    return normalized


def _nested_input_records(
    *,
    external_keys: Tuple[str, ...],
    config: Mapping[str, Any] | None,
) -> tuple[Tuple[dict[str, Any], ...], Tuple[str, ...]]:
    if "args.inputs" not in external_keys:
        return (), ()
    if config is None or "inputs" not in config:
        return (), ("args.inputs source discovery unavailable",)
    raw_inputs = config.get("inputs")
    if not isinstance(raw_inputs, (str, Path)) or not str(raw_inputs).strip():
        return (), ("args.inputs is not a nonblank path",)
    inputs_path = _canonical_path(raw_inputs)
    if not inputs_path.is_file():
        return (), (f"args.inputs CSV is not readable: {inputs_path}",)

    records: list[dict[str, Any]] = []
    reasons: list[str] = []
    with inputs_path.open("r", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"sample_id", "source_path"}
        if not required.issubset(set(reader.fieldnames or ())):
            return (), (
                f"args.inputs CSV lacks required columns: {sorted(required)}",
            )
        for row_number, row in enumerate(reader, start=2):
            sample_id = str(row.get("sample_id") or "").strip()
            declared_path = str(row.get("source_path") or "").strip()
            if not sample_id or not declared_path:
                reasons.append(
                    f"args.inputs row {row_number} lacks sample_id or source_path"
                )
                continue
            source_path = _canonical_path(declared_path)
            if source_path.is_file():
                records.append(
                    _nested_record(
                        row_number=row_number,
                        sample_id=sample_id,
                        record_kind="source_bam",
                        declared_path=declared_path,
                        path=source_path,
                    )
                )
                continue
            if source_path.is_dir():
                records.append(
                    _nested_record(
                        row_number=row_number,
                        sample_id=sample_id,
                        record_kind="source_directory",
                        declared_path=declared_path,
                        path=source_path,
                    )
                )
                members = tuple(
                    sorted(
                        (_canonical_path(path) for path in source_path.glob(f"{sample_id}.*.bam")),
                        key=str,
                    )
                )
                if not members:
                    reasons.append(
                        f"args.inputs row {row_number} directory has no {sample_id}.*.bam members"
                    )
                records.extend(
                    _nested_record(
                        row_number=row_number,
                        sample_id=sample_id,
                        record_kind="source_bam",
                        declared_path=str(member),
                        path=member,
                        parent_path=source_path,
                    )
                    for member in members
                )
                continue
            record = _nested_record(
                row_number=row_number,
                sample_id=sample_id,
                record_kind="source_path",
                declared_path=declared_path,
                path=source_path,
            )
            records.append(record)
            reasons.append(
                f"args.inputs row {row_number} source path status="
                f"{record['fingerprint']['status']}"
            )
    ordered = tuple(
        sorted(
            records,
            key=lambda record: (
                int(record["csv_row"]),
                str(record["record_kind"]),
                str(record["canonical_path"]),
            ),
        )
    )
    return ordered, tuple(reasons)


def _nested_record(
    *,
    row_number: int,
    sample_id: str,
    record_kind: str,
    declared_path: str,
    path: Path,
    parent_path: Path | None = None,
) -> dict[str, Any]:
    fingerprint = fingerprint_path(path, mode="metadata")
    return {
        "source": "args.inputs",
        "csv_row": row_number,
        "sample_id": sample_id,
        "record_kind": record_kind,
        "declared_path": declared_path,
        "canonical_path": fingerprint.path,
        "parent_path": None if parent_path is None else str(parent_path),
        "fingerprint": fingerprint.as_dict(),
        "metadata_hash": _metadata_hash(fingerprint),
    }


def _metadata_hash(fingerprint: ArtifactPathFingerprint) -> str:
    return stable_task_json_hash(
        {
            "path": fingerprint.path,
            "exists": fingerprint.exists,
            "is_file": fingerprint.is_file,
            "is_dir": fingerprint.is_dir,
            "size_bytes": fingerprint.size_bytes,
            "mtime_ns": fingerprint.mtime_ns,
            "mode": fingerprint.mode,
        }
    )


def _canonical_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def manifest_metadata_status_counts(manifest: Mapping[str, Any]) -> dict[str, int]:
    """Return path-status counts for diagnostics and focused tests."""

    values: list[str] = []
    declared = manifest.get("declared_inputs")
    if isinstance(declared, Mapping):
        raw_records = declared.get("records")
        if isinstance(raw_records, (tuple, list)):
            values.extend(
                str(record.get("path_status"))
                for record in raw_records
                if isinstance(record, Mapping)
            )
    nested = manifest.get("nested_inputs")
    if isinstance(nested, (tuple, list)):
        for record in nested:
            if not isinstance(record, Mapping):
                continue
            fingerprint = record.get("fingerprint")
            if isinstance(fingerprint, Mapping):
                values.append(str(fingerprint.get("status")))
    return dict(sorted(Counter(values).items()))
