from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Tuple

from .branch_artifacts import (
    BRANCH_ARTIFACT_MANIFEST_V1_KEY,
    build_branch_artifact_manifest_v1,
)
from .define import StageLevel
from .pipeline_contracts import (
    CurrentTaskInstanceContractV2,
    PipelineContract,
    PipelineTaskContract,
    TaskInstanceContract,
    TaskKey,
    build_current_task_instance_contract_v2,
    build_pipeline_contract,
    snapshot_current_task_instance_contract_v2,
)
from .task_coordinate import (
    TaskCoordinateError,
    TaskCoordinateV1,
    build_task_coordinate_v1,
)
from .cache_epoch import PIPELINE_CACHE_EPOCH_KEY
from .task_instance_v2 import (
    DEFAULT_IMPLEMENTATION_VERSION,
    DEFAULT_REUSE_POLICY_VERSION,
    TaskInstanceV2,
    stable_task_json_hash,
)
from .task_specs import TaskSpec


TASK_INSTANCE_SCHEMA_V3 = "scpolya.task_instance.v3"


@dataclass(frozen=True)
class TaskInstanceV3Field:
    key: str
    value: Any
    legacy_value: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("Task-instance v3 fields require a non-empty key")
        if not isinstance(self.legacy_value, str):
            raise TypeError("Task-instance v3 legacy values must be text")

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": _normalize_json_value(self.value),
            "legacy_value": self.legacy_value,
        }

    def fingerprint_dict(self) -> dict[str, str]:
        return {"key": self.key, "legacy_value": self.legacy_value}


@dataclass(frozen=True)
class TaskInstanceV3:
    stage: str
    task_base: str
    level: StageLevel | None
    identity: Tuple[TaskInstanceV3Field, ...]
    params: Tuple[TaskInstanceV3Field, ...]
    consumes: Tuple[str, ...]
    produces: Tuple[str, ...]
    reuse_policy_version: str = DEFAULT_REUSE_POLICY_VERSION
    implementation_version: str = DEFAULT_IMPLEMENTATION_VERSION
    source_contracts: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = TASK_INSTANCE_SCHEMA_V3

    def __post_init__(self) -> None:
        if self.schema_version != TASK_INSTANCE_SCHEMA_V3:
            raise ValueError(
                f"Unsupported task-instance v3 schema: {self.schema_version!r}"
            )
        if not isinstance(self.stage, str) or not self.stage:
            raise ValueError("Task-instance v3 requires a non-empty stage")
        if not isinstance(self.task_base, str) or not self.task_base:
            raise ValueError("Task-instance v3 requires a non-empty task_base")
        if self.level not in {None, "global", "sample", "pp_unit", "unit"}:
            raise ValueError(f"Unsupported task-instance level: {self.level!r}")
        _validate_fields(self.identity, label="identity")
        _validate_fields(self.params, label="params")
        _validate_artifacts(self.consumes, label="consumes")
        _validate_artifacts(self.produces, label="produces")
        if (
            not isinstance(self.reuse_policy_version, str)
            or not self.reuse_policy_version
        ):
            raise ValueError("Task-instance v3 requires a reuse policy version")
        if (
            not isinstance(self.implementation_version, str)
            or not self.implementation_version
        ):
            raise ValueError("Task-instance v3 requires an implementation version")
        if not isinstance(self.source_contracts, Mapping):
            raise TypeError("Task-instance v3 source contracts must be a mapping")

    @property
    def key(self) -> TaskKey:
        return self.stage, self.task_base

    @property
    def identity_keys(self) -> Tuple[str, ...]:
        return tuple(field_value.key for field_value in self.identity)

    @property
    def param_keys(self) -> Tuple[str, ...]:
        return tuple(field_value.key for field_value in self.params)

    @property
    def identity_dict(self) -> dict[str, Any]:
        return {
            field_value.key: field_value.value
            for field_value in self.identity
        }

    @property
    def params_dict(self) -> dict[str, Any]:
        return {
            field_value.key: field_value.value
            for field_value in self.params
        }

    @property
    def identity_hash(self) -> str:
        return stable_task_json_hash(
            {
                "stage": self.stage,
                "task_base": self.task_base,
                "identity": [
                    field_value.fingerprint_dict()
                    for field_value in self.identity
                ],
            }
        )

    @property
    def params_hash(self) -> str:
        return stable_task_json_hash(
            {
                "stage": self.stage,
                "task_base": self.task_base,
                "params": [
                    field_value.fingerprint_dict()
                    for field_value in self.params
                ],
                "legacy_variant_str": (
                    derive_legacy_variant_str_from_task_instance_v3(self)
                ),
            }
        )

    @property
    def task_coordinate(self) -> TaskCoordinateV1:
        return build_task_coordinate_v1(
            stage=self.stage,
            task_base=self.task_base,
            identity=(
                (field_value.key, field_value.legacy_value)
                for field_value in self.identity
            ),
            params=(
                (field_value.key, field_value.legacy_value)
                for field_value in self.params
            ),
        )

    @property
    def task_coordinate_id(self) -> str:
        return self.task_coordinate.task_coordinate_id

    @property
    def symbolic_inputs_hash(self) -> str:
        return stable_task_json_hash(
            {
                "stage": self.stage,
                "task_base": self.task_base,
                "consumes": self.consumes,
            }
        )

    @property
    def symbolic_outputs_hash(self) -> str:
        return stable_task_json_hash(
            {
                "stage": self.stage,
                "task_base": self.task_base,
                "produces": self.produces,
            }
        )

    @property
    def contract_hash(self) -> str:
        return stable_task_json_hash(
            {
                "stage": self.stage,
                "task_base": self.task_base,
                "level": self.level,
                "identity_keys": self.identity_keys,
                "param_keys": self.param_keys,
                "consumes": self.consumes,
                "produces": self.produces,
                "reuse_policy_version": self.reuse_policy_version,
                "implementation_version": self.implementation_version,
            }
        )

    @property
    def instance_id(self) -> str:
        return stable_task_json_hash(
            {
                "stage": self.stage,
                "task_base": self.task_base,
                "identity_hash": self.identity_hash,
                "params_hash": self.params_hash,
                "symbolic_inputs_hash": self.symbolic_inputs_hash,
                "reuse_policy_version": self.reuse_policy_version,
                "implementation_version": self.implementation_version,
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "task_base": self.task_base,
            "level": self.level,
            "identity": [field_value.as_dict() for field_value in self.identity],
            "params": [field_value.as_dict() for field_value in self.params],
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "reuse_policy_version": self.reuse_policy_version,
            "implementation_version": self.implementation_version,
            "identity_hash": self.identity_hash,
            "params_hash": self.params_hash,
            "symbolic_inputs_hash": self.symbolic_inputs_hash,
            "symbolic_outputs_hash": self.symbolic_outputs_hash,
            "contract_hash": self.contract_hash,
            "instance_id": self.instance_id,
            "task_coordinate_id": self.task_coordinate_id,
            "source_contracts": _normalize_json_value(self.source_contracts),
        }


def derive_legacy_variant_str_from_task_instance_v3(
    task_instance: TaskInstanceV3,
) -> str:
    if not isinstance(task_instance, TaskInstanceV3):
        raise TypeError("Legacy alias derivation requires TaskInstanceV3")
    return task_instance.task_coordinate.legacy_variant_str


def task_instance_v2_compatibility_payload_from_v3(
    task_instance: TaskInstanceV3,
) -> dict[str, Any]:
    """Serialize the released TaskInstanceV2 diagnostic shape explicitly."""

    if not isinstance(task_instance, TaskInstanceV3):
        raise TypeError("Task-instance v2 serialization requires TaskInstanceV3")
    payload = task_instance.as_dict()
    payload.pop("schema_version")
    return {
        "stage": payload["stage"],
        "task_base": payload["task_base"],
        "level": payload["level"],
        "identity": payload["identity"],
        "params": payload["params"],
        "legacy_variant_str": (
            derive_legacy_variant_str_from_task_instance_v3(task_instance)
        ),
        "consumes": payload["consumes"],
        "produces": payload["produces"],
        "reuse_policy_version": payload["reuse_policy_version"],
        "implementation_version": payload["implementation_version"],
        "identity_hash": payload["identity_hash"],
        "params_hash": payload["params_hash"],
        "symbolic_inputs_hash": payload["symbolic_inputs_hash"],
        "symbolic_outputs_hash": payload["symbolic_outputs_hash"],
        "contract_hash": payload["contract_hash"],
        "instance_id": payload["instance_id"],
        "task_coordinate_id": payload["task_coordinate_id"],
        "source_contracts": payload["source_contracts"],
    }


def task_instance_v3_from_v2_compatibility(
    task_instance: TaskInstanceV2,
) -> TaskInstanceV3:
    """Validate and remove a V2 instance's caller-supplied alias."""

    if not isinstance(task_instance, TaskInstanceV2):
        raise TypeError("Task-instance compatibility input must be TaskInstanceV2")
    coordinate = task_instance.task_coordinate
    if coordinate.legacy_variant_str != task_instance.legacy_variant_str:
        raise TaskCoordinateError(
            "Task-instance V2 alias differs from its structured parameters"
        )
    converted = TaskInstanceV3(
        stage=task_instance.stage,
        task_base=task_instance.task_base,
        level=task_instance.level,
        identity=tuple(
            TaskInstanceV3Field(field.key, field.value, field.legacy_value)
            for field in task_instance.identity
        ),
        params=tuple(
            TaskInstanceV3Field(field.key, field.value, field.legacy_value)
            for field in task_instance.params
        ),
        consumes=task_instance.consumes,
        produces=task_instance.produces,
        reuse_policy_version=task_instance.reuse_policy_version,
        implementation_version=task_instance.implementation_version,
        source_contracts=task_instance.source_contracts,
    )
    if (
        task_instance_v2_compatibility_payload_from_v3(converted)
        != task_instance.as_dict()
    ):
        raise TaskCoordinateError(
            "Task-instance V2 fields or hashes differ after structured conversion"
        )
    return converted


def build_task_instance_v3(
    spec: TaskSpec | PipelineTaskContract,
    item: Any,
    *,
    reuse_policy_version: str = DEFAULT_REUSE_POLICY_VERSION,
    implementation_version: str = DEFAULT_IMPLEMENTATION_VERSION,
) -> TaskInstanceV3:
    current_contract = build_current_task_instance_contract_v2(spec, item)
    return build_task_instance_v3_from_current_contract(
        spec=spec,
        current_contract=current_contract,
        reuse_policy_version=reuse_policy_version,
        implementation_version=implementation_version,
    )


def build_task_instance_v3_by_key(
    stage: str,
    task_base: str,
    item: Any,
    *,
    contract: PipelineContract | None = None,
    reuse_policy_version: str = DEFAULT_REUSE_POLICY_VERSION,
    implementation_version: str = DEFAULT_IMPLEMENTATION_VERSION,
) -> TaskInstanceV3:
    contract = contract or build_pipeline_contract()
    spec = contract.tasks_by_key[(stage, task_base)]
    return build_task_instance_v3(
        spec,
        item,
        reuse_policy_version=reuse_policy_version,
        implementation_version=implementation_version,
    )


def build_task_instance_v3_from_current_contract(
    *,
    spec: TaskSpec | PipelineTaskContract,
    current_contract: CurrentTaskInstanceContractV2,
    reuse_policy_version: str = DEFAULT_REUSE_POLICY_VERSION,
    implementation_version: str = DEFAULT_IMPLEMENTATION_VERSION,
) -> TaskInstanceV3:
    if not isinstance(current_contract, CurrentTaskInstanceContractV2):
        raise TypeError(
            "Current task-instance construction requires "
            "CurrentTaskInstanceContractV2"
        )
    current_contract = snapshot_current_task_instance_contract_v2(
        current_contract
    )
    _validate_current_contract_spec(spec, current_contract)
    identity = tuple(
        TaskInstanceV3Field(
            key=field_value.key,
            value=field_value.value,
            legacy_value=_legacy_identity_value(field_value.value),
        )
        for field_value in current_contract.identity
    )
    params = tuple(
        TaskInstanceV3Field(
            key=field_value.key,
            value=field_value.value,
            legacy_value=_legacy_param_value(field_value.value),
        )
        for field_value in current_contract.task_params
    )
    source_contracts = dict(current_contract.source_contracts)
    branch_manifest = build_branch_artifact_manifest_v1(
        stage=current_contract.stage,
        task_base=current_contract.task_base,
        identity=tuple(
            (field_value.key, field_value.value)
            for field_value in identity
        ),
        params=tuple(
            (field_value.key, field_value.value)
            for field_value in params
        ),
        implementation_version=implementation_version,
    )
    if branch_manifest is not None:
        source_contracts[BRANCH_ARTIFACT_MANIFEST_V1_KEY] = branch_manifest

    return TaskInstanceV3(
        stage=current_contract.stage,
        task_base=current_contract.task_base,
        level=current_contract.level,
        identity=identity,
        params=params,
        consumes=tuple(spec.consumes),
        produces=tuple(spec.produces),
        reuse_policy_version=reuse_policy_version,
        implementation_version=implementation_version,
        source_contracts=source_contracts,
    )


def _validate_current_contract_spec(
    spec: TaskSpec | PipelineTaskContract,
    current_contract: CurrentTaskInstanceContractV2,
) -> None:
    if not isinstance(spec, (TaskSpec, PipelineTaskContract)):
        raise TypeError(
            "Current task-instance construction requires a task spec"
        )
    mismatches: list[str] = []
    if current_contract.key != spec.key:
        mismatches.append(
            f"task key {current_contract.key!r} != {spec.key!r}"
        )
    if current_contract.level != spec.level:
        mismatches.append(
            f"level {current_contract.level!r} != {spec.level!r}"
        )
    if current_contract.identity_keys != tuple(spec.identity_keys):
        mismatches.append(
            "identity keys "
            f"{current_contract.identity_keys!r} != {tuple(spec.identity_keys)!r}"
        )
    # The runtime appends the cache semantic epoch to every current contract;
    # it is not a TaskSpec parameter (see task_manage/cache_epoch.py).
    expected_param_keys = tuple(spec.param_keys) + (PIPELINE_CACHE_EPOCH_KEY,)
    if current_contract.param_keys != expected_param_keys:
        mismatches.append(
            "parameter keys "
            f"{current_contract.param_keys!r} != {expected_param_keys!r}"
        )
    if mismatches:
        raise ValueError(
            "Current task-instance contract differs from its task spec: "
            + "; ".join(mismatches)
        )


def build_task_instance_v3_from_contract(
    *,
    spec: TaskSpec | PipelineTaskContract,
    legacy_instance: TaskInstanceContract,
    reuse_policy_version: str = DEFAULT_REUSE_POLICY_VERSION,
    implementation_version: str = DEFAULT_IMPLEMENTATION_VERSION,
) -> TaskInstanceV3:
    identity = tuple(
        TaskInstanceV3Field(
            key=field_value.key,
            value=field_value.value,
            legacy_value=_legacy_identity_value(field_value.value),
        )
        for field_value in legacy_instance.identity
    )
    params = tuple(
        TaskInstanceV3Field(
            key=field_value.key,
            value=field_value.value,
            legacy_value=_legacy_param_value(field_value.value),
        )
        for field_value in legacy_instance.task_params
    )
    source_contracts = dict(legacy_instance.source_contracts)
    branch_manifest = build_branch_artifact_manifest_v1(
        stage=legacy_instance.stage,
        task_base=legacy_instance.task_base,
        identity=tuple(
            (field_value.key, field_value.value)
            for field_value in identity
        ),
        params=tuple(
            (field_value.key, field_value.value)
            for field_value in params
        ),
        implementation_version=implementation_version,
    )
    if branch_manifest is not None:
        source_contracts[BRANCH_ARTIFACT_MANIFEST_V1_KEY] = branch_manifest

    task_instance = TaskInstanceV3(
        stage=legacy_instance.stage,
        task_base=legacy_instance.task_base,
        level=legacy_instance.level,
        identity=identity,
        params=params,
        consumes=tuple(spec.consumes),
        produces=tuple(spec.produces),
        reuse_policy_version=reuse_policy_version,
        implementation_version=implementation_version,
        source_contracts=source_contracts,
    )
    if (
        derive_legacy_variant_str_from_task_instance_v3(task_instance)
        != legacy_instance.variant_str
    ):
        raise TaskCoordinateError(
            "Task-instance V3 parameters differ from the legacy contract"
        )
    return task_instance


def _validate_fields(
    fields: Tuple[TaskInstanceV3Field, ...],
    *,
    label: str,
) -> None:
    if not isinstance(fields, tuple) or any(
        not isinstance(field_value, TaskInstanceV3Field)
        for field_value in fields
    ):
        raise TypeError(f"Task-instance v3 {label} must be v3 field tuples")
    keys = tuple(field_value.key for field_value in fields)
    if len(keys) != len(set(keys)):
        raise ValueError(f"Task-instance v3 {label} keys must be unique")


def _validate_artifacts(values: Tuple[str, ...], *, label: str) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value
        for value in values
    ):
        raise TypeError(f"Task-instance v3 {label} must be non-empty strings")


def _legacy_identity_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _legacy_param_value(value: Any) -> str:
    return str(value)


def _normalize_json_value(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize_json_value(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_json_value(val)
            for key, val in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_normalize_json_value(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value
