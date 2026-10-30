"""Additive completion evidence for selected in-process task producers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Iterable, Literal, Mapping, Sequence, Tuple
from uuid import uuid4

from .pipeline_contracts import TaskKey
from .task_instance_v2 import stable_task_json_hash
from .task_instance_v3 import (
    TaskInstanceV3,
    task_instance_v2_compatibility_payload_from_v3,
)


INLINE_EXECUTION_OBSERVATION_V1_SCHEMA = "inline-execution-observation-v1"
INLINE_INPUT_OBSERVATION_MANIFEST_V1_KEY = (
    "inline_input_observation_manifest_v1"
)
INLINE_INPUT_OBSERVATION_MANIFEST_V1_SCHEMA = (
    "inline-input-observation-manifest-v1"
)

InlineInputObservationManifestStatus = Literal["captured", "incomplete"]

_DIAGNOSTIC_ONLY = "diagnostic_only"
_COMPLETED = "completed"

INLINE_OBSERVED_ARTIFACTS_BY_TASK: Mapping[TaskKey, Tuple[str, ...]] = {
    ("preprocess_units", "finalize_units"): (
        "input_tables/unit_inputs.<dedup_method>.csv",
        "cache:unit_inputs_csv",
    ),
    ("calculate_umi", "calculate_umi"): (
        "cache:upm_factor",
        "cache:upm_factor_polyA",
        "task_meta:upm_factor",
    ),
    ("annotate_peaks", "extract_genomic_features"): (
        "cache:features_dict",
    ),
    ("annotate_peaks", "classify_peaks"): (
        "cache:annotated_merged_peaks_df",
    ),
    ("deepcps", "score_and_join"): (
        "final_output/deepcps/scored.tsv",
        "final_output/deepcps/sites.deepcps.tsv",
        "final_output/deepcps/provenance.json",
    ),
}

INLINE_OBSERVED_PRODUCER_BY_ARTIFACT: Mapping[str, TaskKey] = {
    artifact_key: task_key
    for task_key, artifact_keys in INLINE_OBSERVED_ARTIFACTS_BY_TASK.items()
    for artifact_key in artifact_keys
}


@dataclass(frozen=True)
class InlineExecutionObservationV1:
    """One successfully completed inline producer invocation."""

    invocation_id: str
    observation_token: str
    producer_instance_id: str
    producer_task_key: TaskKey
    produced_artifacts: Tuple[str, ...]
    contract_hash: str
    reuse_policy_version: str
    implementation_version: str
    worker_callable: str
    instance_payload: Mapping[str, Any]
    completed_at: str
    observation_id: int | None = None
    schema_version: str = INLINE_EXECUTION_OBSERVATION_V1_SCHEMA
    status: Literal["completed"] = _COMPLETED
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    def with_observation_id(
        self,
        observation_id: int,
    ) -> "InlineExecutionObservationV1":
        if int(observation_id) <= 0:
            raise ValueError("Inline observation ID must be positive")
        return replace(self, observation_id=int(observation_id))

    def reference_for(
        self,
        artifact_key: str,
    ) -> "InlineExecutionObservationReferenceV1":
        if self.observation_id is None:
            raise ValueError(
                "Inline observation must be persisted before it is referenced"
            )
        if artifact_key not in self.produced_artifacts:
            raise ValueError(
                f"Inline observation does not produce {artifact_key!r}"
            )
        return InlineExecutionObservationReferenceV1(
            artifact_key=artifact_key,
            observation_id=self.observation_id,
            observation_token=self.observation_token,
            invocation_id=self.invocation_id,
            producer_instance_id=self.producer_instance_id,
            producer_task_key=self.producer_task_key,
            contract_hash=self.contract_hash,
            reuse_policy_version=self.reuse_policy_version,
            implementation_version=self.implementation_version,
            completed_at=self.completed_at,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "strict_effect": self.strict_effect,
            "observation_id": self.observation_id,
            "invocation_id": self.invocation_id,
            "observation_token": self.observation_token,
            "producer_instance_id": self.producer_instance_id,
            "producer_task_key": {
                "stage": self.producer_task_key[0],
                "task_base": self.producer_task_key[1],
            },
            "produced_artifacts": list(self.produced_artifacts),
            "contract_hash": self.contract_hash,
            "reuse_policy_version": self.reuse_policy_version,
            "implementation_version": self.implementation_version,
            "worker_callable": self.worker_callable,
            "instance_payload": dict(self.instance_payload),
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class InlineExecutionObservationReferenceV1:
    """Exact persisted observation selected by one consumer decision."""

    artifact_key: str
    observation_id: int
    observation_token: str
    invocation_id: str
    producer_instance_id: str
    producer_task_key: TaskKey
    contract_hash: str
    reuse_policy_version: str
    implementation_version: str
    completed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_key": self.artifact_key,
            "observation_id": self.observation_id,
            "observation_token": self.observation_token,
            "invocation_id": self.invocation_id,
            "producer_instance_id": self.producer_instance_id,
            "producer_task_key": {
                "stage": self.producer_task_key[0],
                "task_base": self.producer_task_key[1],
            },
            "contract_hash": self.contract_hash,
            "reuse_policy_version": self.reuse_policy_version,
            "implementation_version": self.implementation_version,
            "completed_at": self.completed_at,
        }


def new_inline_execution_invocation_id() -> str:
    """Return a process-local invocation identifier."""

    return uuid4().hex


def build_inline_execution_observation_v1(
    task_instance: TaskInstanceV3,
    *,
    invocation_id: str,
    worker_callable: str = "",
    observation_token: str | None = None,
    completed_at: str | None = None,
) -> InlineExecutionObservationV1:
    """Build completion evidence after the inline callable returns."""

    normalized_invocation = str(invocation_id).strip()
    if not normalized_invocation:
        raise ValueError("Inline observation invocation ID must be nonblank")
    expected_artifacts = INLINE_OBSERVED_ARTIFACTS_BY_TASK.get(
        task_instance.key
    )
    if expected_artifacts is None:
        raise ValueError(
            f"Task {task_instance.key!r} is not an observed inline producer"
        )
    if tuple(task_instance.produces) != expected_artifacts:
        raise ValueError(
            f"Inline producer declaration changed for {task_instance.key!r}"
        )
    token = str(observation_token or uuid4().hex).strip()
    if not token:
        raise ValueError("Inline observation token must be nonblank")
    return InlineExecutionObservationV1(
        invocation_id=normalized_invocation,
        observation_token=token,
        producer_instance_id=task_instance.instance_id,
        producer_task_key=task_instance.key,
        produced_artifacts=tuple(task_instance.produces),
        contract_hash=task_instance.contract_hash,
        reuse_policy_version=task_instance.reuse_policy_version,
        implementation_version=task_instance.implementation_version,
        worker_callable=str(worker_callable or ""),
        instance_payload=(
            task_instance_v2_compatibility_payload_from_v3(task_instance)
        ),
        completed_at=completed_at or datetime.now(UTC).isoformat(),
    )


def attach_inline_input_observation_manifest_v1(
    task_instance: TaskInstanceV3,
    *,
    invocation_id: str,
    observations_by_artifact: Mapping[
        str,
        Sequence[InlineExecutionObservationV1],
    ],
    captured_at: str | None = None,
) -> TaskInstanceV3:
    """Attach only references registered by the current runtime invocation."""

    artifact_keys = tuple(
        artifact_key
        for artifact_key in task_instance.consumes
        if artifact_key in INLINE_OBSERVED_PRODUCER_BY_ARTIFACT
    )
    if not artifact_keys:
        return task_instance

    references: list[InlineExecutionObservationReferenceV1] = []
    missing_artifacts: list[str] = []
    ambiguous_artifacts: list[str] = []
    for artifact_key in artifact_keys:
        expected_task_key = INLINE_OBSERVED_PRODUCER_BY_ARTIFACT[artifact_key]
        candidates = tuple(
            observation
            for observation in observations_by_artifact.get(artifact_key, ())
            if observation.invocation_id == invocation_id
            and observation.producer_task_key == expected_task_key
            and artifact_key in observation.produced_artifacts
        )
        if not candidates:
            missing_artifacts.append(artifact_key)
            continue
        if len(candidates) != 1:
            ambiguous_artifacts.append(artifact_key)
            continue
        references.append(candidates[0].reference_for(artifact_key))

    status: InlineInputObservationManifestStatus = (
        "captured"
        if not missing_artifacts
        and not ambiguous_artifacts
        and len(references) == len(artifact_keys)
        else "incomplete"
    )
    captured = captured_at or datetime.now(UTC).isoformat()
    reference_payloads = [
        reference.as_dict()
        for reference in sorted(
            references,
            key=lambda value: (
                value.artifact_key,
                value.observation_id,
            ),
        )
    ]
    manifest = {
        "schema_version": INLINE_INPUT_OBSERVATION_MANIFEST_V1_SCHEMA,
        "status": status,
        "strict_effect": _DIAGNOSTIC_ONLY,
        "generated_from": {
            "source": "current_process_inline_observation_registry",
            "invocation_id": invocation_id,
            "captured_at": captured,
        },
        "consumer_task_key": {
            "stage": task_instance.stage,
            "task_base": task_instance.task_base,
        },
        "consumer_instance_id": task_instance.instance_id,
        "declared_artifacts": list(artifact_keys),
        "references": reference_payloads,
        "missing_artifacts": sorted(missing_artifacts),
        "ambiguous_artifacts": sorted(ambiguous_artifacts),
        "manifest_hash": stable_task_json_hash(
            {
                "schema_version": (
                    INLINE_INPUT_OBSERVATION_MANIFEST_V1_SCHEMA
                ),
                "invocation_id": invocation_id,
                "consumer_instance_id": task_instance.instance_id,
                "declared_artifacts": artifact_keys,
                "references": reference_payloads,
                "missing_artifacts": sorted(missing_artifacts),
                "ambiguous_artifacts": sorted(ambiguous_artifacts),
            }
        ),
    }
    source_contracts = dict(task_instance.source_contracts)
    source_contracts[INLINE_INPUT_OBSERVATION_MANIFEST_V1_KEY] = manifest
    return replace(task_instance, source_contracts=source_contracts)


def observations_by_artifact(
    observations: Iterable[InlineExecutionObservationV1],
) -> dict[str, Tuple[InlineExecutionObservationV1, ...]]:
    grouped: dict[str, list[InlineExecutionObservationV1]] = {}
    for observation in observations:
        for artifact_key in observation.produced_artifacts:
            grouped.setdefault(artifact_key, []).append(observation)
    return {
        artifact_key: tuple(values)
        for artifact_key, values in grouped.items()
    }
