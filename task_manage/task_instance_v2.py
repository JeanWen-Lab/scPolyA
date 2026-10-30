from __future__ import annotations

import hashlib
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
    PipelineContract,
    PipelineTaskContract,
    TaskInstanceContract,
    TaskKey,
    build_pipeline_contract,
    build_task_instance_contract,
)
from .task_specs import TaskSpec
from .task_coordinate import (
    TaskCoordinateV1,
    build_task_coordinate_v1,
)


DEFAULT_REUSE_POLICY_VERSION = "reuse-v2-observe-1"
DEFAULT_IMPLEMENTATION_VERSION = "task-spec-v1"


@dataclass(frozen=True)
class TaskInstanceV2Field:
    key: str
    value: Any
    legacy_value: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": _normalize_json_value(self.value),
            "legacy_value": self.legacy_value,
        }

    def fingerprint_dict(self) -> dict[str, str]:
        return {"key": self.key, "legacy_value": self.legacy_value}


@dataclass(frozen=True)
class TaskInstanceV2:
    stage: str
    task_base: str
    level: StageLevel | None
    identity: Tuple[TaskInstanceV2Field, ...]
    params: Tuple[TaskInstanceV2Field, ...]
    legacy_variant_str: str
    consumes: Tuple[str, ...]
    produces: Tuple[str, ...]
    reuse_policy_version: str = DEFAULT_REUSE_POLICY_VERSION
    implementation_version: str = DEFAULT_IMPLEMENTATION_VERSION
    source_contracts: Mapping[str, Any] = field(default_factory=dict)

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
        return {field_value.key: field_value.value for field_value in self.identity}

    @property
    def params_dict(self) -> dict[str, Any]:
        return {field_value.key: field_value.value for field_value in self.params}

    @property
    def legacy_identity_dict(self) -> dict[str, str]:
        return {field_value.key: field_value.legacy_value for field_value in self.identity}

    @property
    def legacy_params_dict(self) -> dict[str, str]:
        return {field_value.key: field_value.legacy_value for field_value in self.params}

    @property
    def identity_hash(self) -> str:
        return stable_task_json_hash(
            {
                "stage": self.stage,
                "task_base": self.task_base,
                "identity": [field_value.fingerprint_dict() for field_value in self.identity],
            }
        )

    @property
    def params_hash(self) -> str:
        return stable_task_json_hash(
            {
                "stage": self.stage,
                "task_base": self.task_base,
                "params": [field_value.fingerprint_dict() for field_value in self.params],
                "legacy_variant_str": self.legacy_variant_str,
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
            "stage": self.stage,
            "task_base": self.task_base,
            "level": self.level,
            "identity": [field_value.as_dict() for field_value in self.identity],
            "params": [field_value.as_dict() for field_value in self.params],
            "legacy_variant_str": self.legacy_variant_str,
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


def build_task_instance_v2(
    spec: TaskSpec | PipelineTaskContract,
    item: Any,
    *,
    reuse_policy_version: str = DEFAULT_REUSE_POLICY_VERSION,
    implementation_version: str = DEFAULT_IMPLEMENTATION_VERSION,
) -> TaskInstanceV2:
    legacy_instance = build_task_instance_contract(spec, item)
    return build_task_instance_v2_from_contract(
        spec=spec,
        legacy_instance=legacy_instance,
        reuse_policy_version=reuse_policy_version,
        implementation_version=implementation_version,
    )


def build_task_instance_v2_by_key(
    stage: str,
    task_base: str,
    item: Any,
    *,
    contract: PipelineContract | None = None,
    reuse_policy_version: str = DEFAULT_REUSE_POLICY_VERSION,
    implementation_version: str = DEFAULT_IMPLEMENTATION_VERSION,
) -> TaskInstanceV2:
    contract = contract or build_pipeline_contract()
    spec = contract.tasks_by_key[(stage, task_base)]
    return build_task_instance_v2(
        spec,
        item,
        reuse_policy_version=reuse_policy_version,
        implementation_version=implementation_version,
    )


def build_task_instance_v2_from_contract(
    *,
    spec: TaskSpec | PipelineTaskContract,
    legacy_instance: TaskInstanceContract,
    reuse_policy_version: str = DEFAULT_REUSE_POLICY_VERSION,
    implementation_version: str = DEFAULT_IMPLEMENTATION_VERSION,
) -> TaskInstanceV2:
    identity = tuple(
        TaskInstanceV2Field(
            key=field_value.key,
            value=field_value.value,
            legacy_value=_legacy_identity_value(field_value.value),
        )
        for field_value in legacy_instance.identity
    )
    params = tuple(
        TaskInstanceV2Field(
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
        identity=tuple((field_value.key, field_value.value) for field_value in identity),
        params=tuple((field_value.key, field_value.value) for field_value in params),
        implementation_version=implementation_version,
    )
    if branch_manifest is not None:
        source_contracts[BRANCH_ARTIFACT_MANIFEST_V1_KEY] = branch_manifest

    return TaskInstanceV2(
        stage=legacy_instance.stage,
        task_base=legacy_instance.task_base,
        level=legacy_instance.level,
        identity=identity,
        params=params,
        legacy_variant_str=legacy_instance.variant_str,
        consumes=tuple(spec.consumes),
        produces=tuple(spec.produces),
        reuse_policy_version=reuse_policy_version,
        implementation_version=implementation_version,
        source_contracts=source_contracts,
    )


def stable_task_json_dumps(value: Any) -> str:
    return json.dumps(_normalize_json_value(value), sort_keys=True, separators=(",", ":"))


def stable_task_json_hash(value: Any) -> str:
    return hashlib.sha256(stable_task_json_dumps(value).encode("utf-8")).hexdigest()


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
        return {str(key): _normalize_json_value(val) for key, val in value.items()}
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
