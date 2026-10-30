from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .cache_epoch import (
    PIPELINE_CACHE_EPOCH,
    PIPELINE_CACHE_EPOCH_KEY,
    require_current_cache_epoch,
)
from .define import TASK_PARAM_SCOPES, StageLevel
from .task_specs import TASK_SPECS, ExecutionMode, TaskSpec, validate_task_specs
from .task_variants import canonical_variant_str
from quantification_config import terminal_annotation_coordinate_mode


TaskKey = Tuple[str, str]
CURRENT_TASK_INSTANCE_CONTRACT_SCHEMA_V2 = (
    "scpolya.current_task_instance_contract.v2"
)


@dataclass(frozen=True)
class TaskFieldValue:
    key: str
    value: Any


@dataclass(frozen=True)
class PipelineTaskContract:
    stage: str
    task_base: str
    level: StageLevel
    execution_mode: ExecutionMode
    identity_keys: Tuple[str, ...] = ()
    param_keys: Tuple[str, ...] = ()
    consumes: Tuple[str, ...] = ()
    produces: Tuple[str, ...] = ()
    preview_param_bindings: Tuple[Tuple[str, str], ...] = ()
    worker_arg: str | None = None
    ui_group: str | None = None
    notes: str = ""
    stage_index: int = 0
    task_index: int = 0

    @property
    def key(self) -> TaskKey:
        return self.stage, self.task_base

    @property
    def variant_keys(self) -> Tuple[str, ...]:
        return self.param_keys


@dataclass(frozen=True)
class PipelineArtifactEdge:
    artifact_key: str
    consumer_stage: str
    consumer_task_base: str
    producer_stage: str | None = None
    producer_task_base: str | None = None

    @property
    def consumer_key(self) -> TaskKey:
        return self.consumer_stage, self.consumer_task_base

    @property
    def producer_key(self) -> TaskKey | None:
        if self.producer_stage is None or self.producer_task_base is None:
            return None
        return self.producer_stage, self.producer_task_base

    @property
    def is_external(self) -> bool:
        return self.producer_key is None


@dataclass(frozen=True)
class TaskInstanceContract:
    stage: str
    task_base: str
    level: StageLevel | None
    identity: Tuple[TaskFieldValue, ...]
    task_params: Tuple[TaskFieldValue, ...]
    variant_str: str
    source_contracts: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> TaskKey:
        return self.stage, self.task_base

    @property
    def identity_keys(self) -> Tuple[str, ...]:
        return tuple(field_value.key for field_value in self.identity)

    @property
    def identity_values(self) -> Tuple[Any, ...]:
        return tuple(field_value.value for field_value in self.identity)

    @property
    def identity_dict(self) -> Dict[str, Any]:
        return {field_value.key: field_value.value for field_value in self.identity}

    @property
    def param_keys(self) -> Tuple[str, ...]:
        return tuple(field_value.key for field_value in self.task_params)

    @property
    def task_params_dict(self) -> Dict[str, Any]:
        return {field_value.key: field_value.value for field_value in self.task_params}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "task_base": self.task_base,
            "level": self.level,
            "identity": [
                {"key": field_value.key, "value": _normalize_value(field_value.value)}
                for field_value in self.identity
            ],
            "task_params": {
                field_value.key: _normalize_value(field_value.value)
                for field_value in self.task_params
            },
            "variant_str": self.variant_str,
            "source_contracts": _normalize_value(self.source_contracts),
        }


@dataclass(frozen=True)
class CurrentTaskInstanceContractV2:
    stage: str
    task_base: str
    level: StageLevel | None
    identity: Tuple[TaskFieldValue, ...]
    task_params: Tuple[TaskFieldValue, ...]
    source_contracts: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CURRENT_TASK_INSTANCE_CONTRACT_SCHEMA_V2

    def __post_init__(self) -> None:
        _validate_current_task_instance_contract_v2(self)

    @property
    def key(self) -> TaskKey:
        return self.stage, self.task_base

    @property
    def identity_keys(self) -> Tuple[str, ...]:
        return tuple(field_value.key for field_value in self.identity)

    @property
    def identity_values(self) -> Tuple[Any, ...]:
        return tuple(field_value.value for field_value in self.identity)

    @property
    def identity_dict(self) -> Dict[str, Any]:
        return {field_value.key: field_value.value for field_value in self.identity}

    @property
    def param_keys(self) -> Tuple[str, ...]:
        return tuple(field_value.key for field_value in self.task_params)

    @property
    def task_params_dict(self) -> Dict[str, Any]:
        return {field_value.key: field_value.value for field_value in self.task_params}

    def as_dict(self) -> Dict[str, Any]:
        _validate_current_task_instance_contract_v2(self)
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "task_base": self.task_base,
            "level": self.level,
            "identity": [
                {"key": field_value.key, "value": _normalize_value(field_value.value)}
                for field_value in self.identity
            ],
            "task_params": {
                field_value.key: _normalize_value(field_value.value)
                for field_value in self.task_params
            },
            "source_contracts": _normalize_value(self.source_contracts),
        }


def _validate_current_task_instance_contract_v2(
    contract: CurrentTaskInstanceContractV2,
) -> None:
    if contract.schema_version != CURRENT_TASK_INSTANCE_CONTRACT_SCHEMA_V2:
        raise ValueError(
            "Unsupported current task-instance contract schema: "
            f"{contract.schema_version!r}"
        )
    if not isinstance(contract.stage, str) or not contract.stage:
        raise ValueError("Current task-instance contract requires a non-empty stage")
    if not isinstance(contract.task_base, str) or not contract.task_base:
        raise ValueError(
            "Current task-instance contract requires a non-empty task_base"
        )
    if contract.level not in {None, "global", "sample", "pp_unit", "unit"}:
        raise ValueError(
            f"Unsupported current task-instance contract level: {contract.level!r}"
        )
    _validate_current_task_fields(contract.identity, label="identity")
    _validate_current_task_fields(contract.task_params, label="task_params")
    # Type invariant, not a builder convention: a current contract always
    # carries exactly one current cache epoch (see cache_epoch.py).
    if PIPELINE_CACHE_EPOCH_KEY in contract.identity_keys:
        raise ValueError(
            f"{PIPELINE_CACHE_EPOCH_KEY!r} is a task parameter, never an identity field"
        )
    require_current_cache_epoch(
        contract.task_params,
        label=f"Current task-instance contract {contract.stage}:{contract.task_base}",
    )
    scoped_param_keys = TASK_PARAM_SCOPES.get(contract.task_base)
    if scoped_param_keys is not None:
        unexpected_param_keys = sorted(
            set(contract.param_keys)
            - set(scoped_param_keys)
            - {PIPELINE_CACHE_EPOCH_KEY}
        )
        if unexpected_param_keys:
            raise ValueError(
                "Current task-instance contract parameters exceed the "
                f"task scope: {unexpected_param_keys}"
            )
    if not isinstance(contract.source_contracts, Mapping):
        raise TypeError(
            "Current task-instance contract source contracts must be a mapping"
        )
    _reject_compatibility_alias_keys(contract.source_contracts)


def snapshot_current_task_instance_contract_v2(
    contract: CurrentTaskInstanceContractV2,
) -> CurrentTaskInstanceContractV2:
    if not isinstance(contract, CurrentTaskInstanceContractV2):
        raise TypeError(
            "Current task-instance snapshots require "
            "CurrentTaskInstanceContractV2"
        )
    _validate_current_task_instance_contract_v2(contract)
    return CurrentTaskInstanceContractV2(
        stage=contract.stage,
        task_base=contract.task_base,
        level=contract.level,
        identity=tuple(
            TaskFieldValue(field_value.key, deepcopy(field_value.value))
            for field_value in contract.identity
        ),
        task_params=tuple(
            TaskFieldValue(field_value.key, deepcopy(field_value.value))
            for field_value in contract.task_params
        ),
        source_contracts=deepcopy(dict(contract.source_contracts)),
        schema_version=contract.schema_version,
    )


@dataclass(frozen=True)
class PipelineContract:
    tasks: Tuple[PipelineTaskContract, ...]
    artifact_edges: Tuple[PipelineArtifactEdge, ...]

    @property
    def tasks_by_key(self) -> Dict[TaskKey, PipelineTaskContract]:
        return {task.key: task for task in self.tasks}

    @property
    def tasks_by_stage(self) -> Dict[str, Tuple[PipelineTaskContract, ...]]:
        grouped: Dict[str, list[PipelineTaskContract]] = {}
        for task in self.tasks:
            grouped.setdefault(task.stage, []).append(task)
        return {stage: tuple(tasks) for stage, tasks in grouped.items()}


def build_pipeline_contract(specs: Sequence[TaskSpec] = TASK_SPECS) -> PipelineContract:
    tasks = pipeline_task_contracts(specs)
    return PipelineContract(
        tasks=tasks,
        artifact_edges=pipeline_artifact_edges(tasks),
    )


def pipeline_task_contracts(specs: Sequence[TaskSpec] = TASK_SPECS) -> Tuple[PipelineTaskContract, ...]:
    stage_order: Dict[str, int] = {}
    task_counts: Dict[str, int] = {}
    contracts = []
    for spec in specs:
        stage_order.setdefault(spec.stage, len(stage_order))
        task_index = task_counts.get(spec.stage, 0)
        task_counts[spec.stage] = task_index + 1
        contracts.append(
            PipelineTaskContract(
                stage=spec.stage,
                task_base=spec.task_base,
                level=spec.level,
                execution_mode=spec.execution_mode,
                identity_keys=spec.identity_keys,
                param_keys=spec.param_keys,
                consumes=spec.consumes,
                produces=spec.produces,
                preview_param_bindings=spec.preview_param_bindings,
                worker_arg=spec.worker_arg,
                ui_group=spec.ui_group,
                notes=spec.notes,
                stage_index=stage_order[spec.stage],
                task_index=task_index,
            )
        )
    return tuple(contracts)


def pipeline_contracts_by_key(
    specs: Sequence[TaskSpec] = TASK_SPECS,
) -> Dict[TaskKey, PipelineTaskContract]:
    return {contract.key: contract for contract in pipeline_task_contracts(specs)}


def pipeline_contracts_by_stage(
    specs: Sequence[TaskSpec] = TASK_SPECS,
) -> Dict[str, Tuple[PipelineTaskContract, ...]]:
    return build_pipeline_contract(specs).tasks_by_stage


def pipeline_artifact_edges(
    tasks: Sequence[PipelineTaskContract] | None = None,
) -> Tuple[PipelineArtifactEdge, ...]:
    tasks = tuple(tasks) if tasks is not None else pipeline_task_contracts()
    producers: Dict[str, list[PipelineTaskContract]] = {}
    for task in tasks:
        for artifact_key in task.produces:
            producers.setdefault(artifact_key, []).append(task)

    edges = []
    for task in tasks:
        for artifact_key in task.consumes:
            artifact_producers = producers.get(artifact_key)
            if not artifact_producers:
                edges.append(
                    PipelineArtifactEdge(
                        artifact_key=artifact_key,
                        consumer_stage=task.stage,
                        consumer_task_base=task.task_base,
                    )
                )
                continue
            for producer in artifact_producers:
                edges.append(
                    PipelineArtifactEdge(
                        artifact_key=artifact_key,
                        producer_stage=producer.stage,
                        producer_task_base=producer.task_base,
                        consumer_stage=task.stage,
                        consumer_task_base=task.task_base,
                    )
                )
    return tuple(edges)


def build_task_instance_contract(
    spec: TaskSpec | PipelineTaskContract,
    item: Any,
) -> TaskInstanceContract:
    return build_task_instance_contract_from_keys(
        stage=spec.stage,
        task_base=spec.task_base,
        item=item,
        identity_keys=spec.identity_keys,
        param_keys=spec.param_keys,
        level=spec.level,
    )


def build_task_instance_contract_from_keys(
    *,
    stage: str,
    task_base: str,
    item: Any,
    identity_keys: Iterable[str],
    param_keys: Iterable[str],
    level: StageLevel | None = None,
) -> TaskInstanceContract:
    identity = tuple(
        TaskFieldValue(key=key, value=_read_item_field(item, key))
        for key in identity_keys
    )
    task_params = tuple(
        TaskFieldValue(key=key, value=_read_item_field(item, key))
        for key in param_keys
    )
    params = {field_value.key: field_value.value for field_value in task_params}
    return TaskInstanceContract(
        stage=stage,
        task_base=task_base,
        level=level,
        identity=identity,
        task_params=task_params,
        variant_str=canonical_variant_str(task_base, task_params=params),
        source_contracts=_snapshot_source_contracts(item),
    )


def build_current_task_instance_contract_v2(
    spec: TaskSpec | PipelineTaskContract,
    item: Any,
) -> CurrentTaskInstanceContractV2:
    return build_current_task_instance_contract_from_keys_v2(
        stage=spec.stage,
        task_base=spec.task_base,
        item=item,
        identity_keys=spec.identity_keys,
        param_keys=spec.param_keys,
        level=spec.level,
    )


def build_current_task_instance_contract_from_keys_v2(
    *,
    stage: str,
    task_base: str,
    item: Any,
    identity_keys: Iterable[str],
    param_keys: Iterable[str],
    level: StageLevel | None = None,
) -> CurrentTaskInstanceContractV2:
    param_keys = tuple(param_keys)
    if PIPELINE_CACHE_EPOCH_KEY in param_keys:
        raise ValueError(
            f"{PIPELINE_CACHE_EPOCH_KEY!r} is injected by the runtime and must "
            "not be declared as a task parameter"
        )
    return CurrentTaskInstanceContractV2(
        stage=stage,
        task_base=task_base,
        level=level,
        identity=tuple(
            TaskFieldValue(key=key, value=_read_item_field(item, key))
            for key in identity_keys
        ),
        # Current coordinates carry the cache semantic epoch (see cache_epoch.py).
        # Historical decode paths never pass through this builder.
        task_params=tuple(
            TaskFieldValue(
                key=key,
                value=(
                    terminal_annotation_coordinate_mode(
                        _read_item_field(item, key)
                    )
                    if stage == "annotate_peaks" and key == "quantification_mode"
                    else _read_item_field(item, key)
                ),
            )
            for key in param_keys
        ) + (TaskFieldValue(PIPELINE_CACHE_EPOCH_KEY, PIPELINE_CACHE_EPOCH),),
        source_contracts=_snapshot_source_contracts(item),
    )


def validate_pipeline_contracts(specs: Sequence[TaskSpec] = TASK_SPECS) -> None:
    validate_task_specs()
    contract = build_pipeline_contract(specs)
    keys = [task.key for task in contract.tasks]
    if len(keys) != len(set(keys)):
        raise AssertionError("PipelineTaskContract registry contains duplicate task keys")

    task_keys = set(keys)
    for task in contract.tasks:
        for artifact_key in (*task.consumes, *task.produces):
            if not isinstance(artifact_key, str) or not artifact_key:
                raise AssertionError(f"Invalid artifact key for {task.key}: {artifact_key!r}")

    for edge in contract.artifact_edges:
        if edge.consumer_key not in task_keys:
            raise AssertionError(f"Unknown consumer task in edge: {edge}")
        if edge.producer_key is not None and edge.producer_key not in task_keys:
            raise AssertionError(f"Unknown producer task in edge: {edge}")


def _read_item_field(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _snapshot_source_contracts(item: Any) -> Dict[str, Any]:
    snapshots = {}
    for name in ("task_identity", "artifact_flags", "effective_params", "worker_payload"):
        try:
            value = getattr(item, name)
        except Exception:
            continue
        if value is not None:
            snapshots[name] = _normalize_value(value)
    return snapshots


def _validate_current_task_fields(
    values: Tuple[TaskFieldValue, ...],
    *,
    label: str,
) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(field_value, TaskFieldValue)
        for field_value in values
    ):
        raise TypeError(
            f"Current task-instance contract {label} must be field-value tuples"
        )
    keys = tuple(field_value.key for field_value in values)
    if any(not isinstance(key, str) or not key for key in keys):
        raise ValueError(
            f"Current task-instance contract {label} keys must be non-empty strings"
        )
    if len(keys) != len(set(keys)):
        raise ValueError(
            f"Current task-instance contract {label} keys must be unique"
        )
    if any(key in {"variant_str", "legacy_variant_str"} for key in keys):
        raise ValueError(
            f"Current task-instance contract {label} cannot use compatibility "
            "alias keys"
        )
    for field_value in values:
        _reject_compatibility_alias_keys(field_value.value)


def _reject_compatibility_alias_keys(value: Any) -> None:
    if is_dataclass(value):
        _reject_compatibility_alias_keys(asdict(value))
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if str(key) in {"variant_str", "legacy_variant_str"}:
                raise ValueError(
                    "Current task-instance contracts cannot carry compatibility aliases"
                )
            _reject_compatibility_alias_keys(nested_value)
        return
    if isinstance(value, (tuple, list)):
        for nested_value in value:
            _reject_compatibility_alias_keys(nested_value)


def _normalize_value(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return tuple(_normalize_value(val) for val in value)
    if isinstance(value, list):
        return [_normalize_value(val) for val in value]
    if isinstance(value, Path):
        return str(value)
    return value
