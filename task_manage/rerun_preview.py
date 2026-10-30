from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Literal, Mapping, Sequence, Tuple

from .pipeline_contracts import PipelineContract, TaskKey, build_pipeline_contract
from .task_variants import stringify_params


_MISSING = object()


ImpactKind = Literal["direct", "downstream", "direct+downstream"]


@dataclass(frozen=True)
class ParamChange:
    key: str
    old_value: Any = None
    new_value: Any = None
    old_present: bool = True
    new_present: bool = True

    @property
    def added(self) -> bool:
        return not self.old_present and self.new_present

    @property
    def removed(self) -> bool:
        return self.old_present and not self.new_present


@dataclass(frozen=True)
class RerunTaskImpact:
    stage: str
    task_base: str
    kind: ImpactKind
    changed_param_keys: Tuple[str, ...] = ()
    changed_identity_keys: Tuple[str, ...] = ()
    changed_input_keys: Tuple[str, ...] = ()
    upstream_task_keys: Tuple[TaskKey, ...] = ()
    consumed_artifacts: Tuple[str, ...] = ()

    @property
    def key(self) -> TaskKey:
        return self.stage, self.task_base

    @property
    def direct(self) -> bool:
        return self.kind in ("direct", "direct+downstream")

    @property
    def downstream(self) -> bool:
        return self.kind in ("downstream", "direct+downstream")


@dataclass(frozen=True)
class RerunPreview:
    changed_params: Tuple[ParamChange, ...]
    impacted_tasks: Tuple[RerunTaskImpact, ...]
    changed_key_order: Tuple[str, ...] = ()

    @property
    def changed_keys(self) -> Tuple[str, ...]:
        if self.changed_key_order:
            return self.changed_key_order
        return tuple(change.key for change in self.changed_params)

    @property
    def direct_tasks(self) -> Tuple[RerunTaskImpact, ...]:
        return tuple(task for task in self.impacted_tasks if task.direct)

    @property
    def downstream_tasks(self) -> Tuple[RerunTaskImpact, ...]:
        return tuple(task for task in self.impacted_tasks if task.downstream)

    @property
    def impacted_task_keys(self) -> Tuple[TaskKey, ...]:
        return tuple(task.key for task in self.impacted_tasks)

    @property
    def by_key(self) -> Dict[TaskKey, RerunTaskImpact]:
        return {task.key: task for task in self.impacted_tasks}


def diff_param_values(
    old_params: Mapping[str, Any],
    new_params: Mapping[str, Any],
    *,
    keys: Iterable[str] | None = None,
) -> Tuple[ParamChange, ...]:
    """Compare parameter maps using the same string semantics as variants."""

    key_order = list(keys) if keys is not None else sorted(set(old_params) | set(new_params))
    old_strings = stringify_params(old_params)
    new_strings = stringify_params(new_params)

    changes = []
    for key in key_order:
        old_raw = old_params.get(key, _MISSING)
        new_raw = new_params.get(key, _MISSING)
        old_present = old_raw is not _MISSING
        new_present = new_raw is not _MISSING
        if old_present != new_present or old_strings.get(key) != new_strings.get(key):
            changes.append(
                ParamChange(
                    key=key,
                    old_value=None if old_raw is _MISSING else old_raw,
                    new_value=None if new_raw is _MISSING else new_raw,
                    old_present=old_present,
                    new_present=new_present,
                )
            )
    return tuple(changes)


def preview_rerun_from_params(
    old_params: Mapping[str, Any],
    new_params: Mapping[str, Any],
    *,
    keys: Iterable[str] | None = None,
    contract: PipelineContract | None = None,
    include_identity_keys: bool = True,
    propagate_downstream: bool = True,
) -> RerunPreview:
    changes = diff_param_values(old_params, new_params, keys=keys)
    return preview_rerun_from_changed_keys(
        (change.key for change in changes),
        changed_params=changes,
        contract=contract,
        include_identity_keys=include_identity_keys,
        propagate_downstream=propagate_downstream,
    )


def preview_rerun_from_changed_keys(
    changed_keys: Iterable[str],
    *,
    changed_params: Sequence[ParamChange] = (),
    contract: PipelineContract | None = None,
    include_identity_keys: bool = True,
    propagate_downstream: bool = True,
) -> RerunPreview:
    contract = contract or build_pipeline_contract()
    changed_key_order = tuple(dict.fromkeys(changed_keys))
    changed_key_set = set(changed_key_order)
    task_order = {task.key: idx for idx, task in enumerate(contract.tasks)}

    impact_state: Dict[TaskKey, Dict[str, Any]] = {}

    def ensure_impact(key: TaskKey) -> Dict[str, Any]:
        task = contract.tasks_by_key[key]
        return impact_state.setdefault(
            key,
            {
                "stage": task.stage,
                "task_base": task.task_base,
                "direct": False,
                "downstream": False,
                "changed_param_keys": [],
                "changed_identity_keys": [],
                "changed_input_keys": [],
                "upstream_task_keys": [],
                "consumed_artifacts": [],
            },
        )

    for task in contract.tasks:
        changed_param_keys = [key for key in task.param_keys if key in changed_key_set]
        changed_param_keys.extend(_changed_preview_param_bindings(task, changed_key_set))
        changed_identity_keys = [
            key for key in task.identity_keys if include_identity_keys and key in changed_key_set
        ]
        changed_input_keys = [
            consume
            for consume in task.consumes
            if _consumed_arg_changed(consume, changed_key_set)
        ]
        if changed_param_keys or changed_identity_keys or changed_input_keys:
            state = ensure_impact(task.key)
            state["direct"] = True
            _append_unique_many(state["changed_param_keys"], changed_param_keys)
            _append_unique_many(state["changed_identity_keys"], changed_identity_keys)
            _append_unique_many(state["changed_input_keys"], changed_input_keys)

    if propagate_downstream:
        downstream_edges: Dict[TaskKey, list[tuple[TaskKey, str]]] = {}
        for edge in contract.artifact_edges:
            if edge.producer_key is None:
                continue
            downstream_edges.setdefault(edge.producer_key, []).append((edge.consumer_key, edge.artifact_key))

        queue = sorted(impact_state, key=lambda key: task_order.get(key, 10**9))
        seen = set(queue)
        index = 0
        while index < len(queue):
            upstream_key = queue[index]
            index += 1
            for consumer_key, artifact_key in downstream_edges.get(upstream_key, []):
                state = ensure_impact(consumer_key)
                state["downstream"] = True
                _append_unique(state["upstream_task_keys"], upstream_key)
                _append_unique(state["consumed_artifacts"], artifact_key)
                if consumer_key not in seen:
                    seen.add(consumer_key)
                    queue.append(consumer_key)

    impacts = []
    for key in sorted(impact_state, key=lambda task_key: task_order.get(task_key, 10**9)):
        state = impact_state[key]
        kind: ImpactKind
        if state["direct"] and state["downstream"]:
            kind = "direct+downstream"
        elif state["direct"]:
            kind = "direct"
        else:
            kind = "downstream"
        impacts.append(
            RerunTaskImpact(
                stage=state["stage"],
                task_base=state["task_base"],
                kind=kind,
                changed_param_keys=tuple(state["changed_param_keys"]),
                changed_identity_keys=tuple(state["changed_identity_keys"]),
                changed_input_keys=tuple(state["changed_input_keys"]),
                upstream_task_keys=tuple(state["upstream_task_keys"]),
                consumed_artifacts=tuple(state["consumed_artifacts"]),
            )
        )

    return RerunPreview(
        changed_params=tuple(changed_params),
        impacted_tasks=tuple(impacts),
        changed_key_order=changed_key_order,
    )


def _append_unique(target: list[Any], value: Any) -> None:
    if value not in target:
        target.append(value)


def _append_unique_many(target: list[Any], values: Iterable[Any]) -> None:
    for value in values:
        _append_unique(target, value)


def _changed_preview_param_bindings(task: Any, changed_key_set: set[str]) -> list[str]:
    changed: list[str] = []
    for preview_key, param_key in task.preview_param_bindings:
        if preview_key in changed_key_set:
            changed.append(f"{param_key}<-{preview_key}")
    return changed


def _consumed_arg_changed(consume: str, changed_key_set: set[str]) -> bool:
    if not consume.startswith("args."):
        return False
    arg_key = consume.removeprefix("args.")
    return consume in changed_key_set or arg_key in changed_key_set
