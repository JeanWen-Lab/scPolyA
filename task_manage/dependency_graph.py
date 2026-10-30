from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Sequence, Tuple

from .pipeline_contracts import PipelineContract, PipelineTaskContract, TaskKey, build_pipeline_contract


@dataclass(frozen=True)
class StaticDependencyEdge:
    artifact_key: str
    producer_key: TaskKey | None
    consumer_key: TaskKey
    ambiguous: bool = False

    @property
    def is_external(self) -> bool:
        return self.producer_key is None

    @property
    def is_self_edge(self) -> bool:
        return self.producer_key == self.consumer_key

    @property
    def is_deterministic(self) -> bool:
        return not self.is_external and not self.ambiguous and not self.is_self_edge


@dataclass(frozen=True)
class AmbiguousArtifact:
    artifact_key: str
    producer_keys: Tuple[TaskKey, ...]
    consumer_keys: Tuple[TaskKey, ...]
    reason: str


@dataclass(frozen=True)
class StaticDependencyGraphReport:
    task_count: int
    edge_count: int
    deterministic_edge_count: int
    external_input_keys: Tuple[str, ...]
    unresolved_external_input_keys: Tuple[str, ...]
    ambiguous_artifacts: Tuple[AmbiguousArtifact, ...]
    cycle_count: int


@dataclass(frozen=True)
class StaticDependencyGraph:
    tasks: Tuple[PipelineTaskContract, ...]
    edges: Tuple[StaticDependencyEdge, ...]
    artifact_producers: Mapping[str, Tuple[TaskKey, ...]]
    artifact_consumers: Mapping[str, Tuple[TaskKey, ...]]

    @property
    def task_keys(self) -> Tuple[TaskKey, ...]:
        return tuple(task.key for task in self.tasks)

    @property
    def tasks_by_key(self) -> Dict[TaskKey, PipelineTaskContract]:
        return {task.key: task for task in self.tasks}

    @property
    def deterministic_edges(self) -> Tuple[StaticDependencyEdge, ...]:
        return tuple(edge for edge in self.edges if edge.is_deterministic)

    @property
    def external_input_keys(self) -> Tuple[str, ...]:
        return tuple(
            dict.fromkeys(edge.artifact_key for edge in self.edges if edge.is_external)
        )

    @property
    def unresolved_external_input_keys(self) -> Tuple[str, ...]:
        return tuple(
            key for key in self.external_input_keys if not is_known_external_artifact(key)
        )

    @property
    def ambiguous_artifacts(self) -> Tuple[AmbiguousArtifact, ...]:
        artifacts: list[AmbiguousArtifact] = []
        for artifact_key, producer_keys in self.artifact_producers.items():
            if len(producer_keys) <= 1:
                continue
            artifacts.append(
                AmbiguousArtifact(
                    artifact_key=artifact_key,
                    producer_keys=producer_keys,
                    consumer_keys=self.artifact_consumers.get(artifact_key, ()),
                    reason="multiple_producers",
                )
            )
        return tuple(artifacts)

    def roots(self, *, include_ambiguous: bool = False) -> Tuple[TaskKey, ...]:
        incoming: set[TaskKey] = set()
        for edge in self.iter_task_edges(include_ambiguous=include_ambiguous):
            incoming.add(edge.consumer_key)
        return tuple(task_key for task_key in self.task_keys if task_key not in incoming)

    def leaves(self, *, include_ambiguous: bool = False) -> Tuple[TaskKey, ...]:
        outgoing: set[TaskKey] = set()
        for edge in self.iter_task_edges(include_ambiguous=include_ambiguous):
            if edge.producer_key is not None:
                outgoing.add(edge.producer_key)
        return tuple(task_key for task_key in self.task_keys if task_key not in outgoing)

    def upstream_of(self, task_key: TaskKey, *, include_ambiguous: bool = False) -> Tuple[TaskKey, ...]:
        self._require_task(task_key)
        upstream = [
            edge.producer_key
            for edge in self.iter_task_edges(include_ambiguous=include_ambiguous)
            if edge.consumer_key == task_key and edge.producer_key is not None
        ]
        return _ordered_unique(upstream, self.task_keys)

    def downstream_of(self, task_key: TaskKey, *, include_ambiguous: bool = False) -> Tuple[TaskKey, ...]:
        self._require_task(task_key)
        downstream = [
            edge.consumer_key
            for edge in self.iter_task_edges(include_ambiguous=include_ambiguous)
            if edge.producer_key == task_key
        ]
        return _ordered_unique(downstream, self.task_keys)

    def upstream_closure(self, task_key: TaskKey, *, include_ambiguous: bool = False) -> Tuple[TaskKey, ...]:
        self._require_task(task_key)
        reverse = self._reverse_adjacency(include_ambiguous=include_ambiguous)
        return self._closure(task_key, reverse)

    def downstream_closure(self, task_key: TaskKey, *, include_ambiguous: bool = False) -> Tuple[TaskKey, ...]:
        self._require_task(task_key)
        adjacency = self._adjacency(include_ambiguous=include_ambiguous)
        return self._closure(task_key, adjacency)

    def topological_order(self, *, include_ambiguous: bool = False) -> Tuple[TaskKey, ...]:
        adjacency = self._adjacency(include_ambiguous=include_ambiguous)
        indegree = {task_key: 0 for task_key in self.task_keys}
        for source_key, target_keys in adjacency.items():
            if source_key not in indegree:
                continue
            for target_key in target_keys:
                indegree[target_key] += 1

        task_order = {task_key: index for index, task_key in enumerate(self.task_keys)}
        queue = deque(sorted((key for key, value in indegree.items() if value == 0), key=task_order.get))
        ordered: list[TaskKey] = []
        while queue:
            source_key = queue.popleft()
            ordered.append(source_key)
            for target_key in sorted(adjacency.get(source_key, ()), key=task_order.get):
                indegree[target_key] -= 1
                if indegree[target_key] == 0:
                    queue.append(target_key)

        if len(ordered) != len(self.task_keys):
            raise ValueError("Static dependency graph contains a cycle")
        return tuple(ordered)

    def find_cycles(self, *, include_ambiguous: bool = False) -> Tuple[Tuple[TaskKey, ...], ...]:
        adjacency = self._adjacency(include_ambiguous=include_ambiguous)
        task_order = {task_key: index for index, task_key in enumerate(self.task_keys)}
        visiting: set[TaskKey] = set()
        visited: set[TaskKey] = set()
        stack: list[TaskKey] = []
        cycles: list[Tuple[TaskKey, ...]] = []
        seen: set[Tuple[TaskKey, ...]] = set()

        def visit(task_key: TaskKey) -> None:
            if task_key in visiting:
                cycle = _canonical_cycle(tuple(stack[stack.index(task_key):] + [task_key]), task_order)
                if cycle not in seen:
                    seen.add(cycle)
                    cycles.append(cycle)
                return
            if task_key in visited:
                return
            visiting.add(task_key)
            stack.append(task_key)
            for child_key in sorted(adjacency.get(task_key, ()), key=task_order.get):
                visit(child_key)
            stack.pop()
            visiting.remove(task_key)
            visited.add(task_key)

        for task_key in self.task_keys:
            visit(task_key)
        return tuple(cycles)

    def iter_task_edges(
        self,
        *,
        include_ambiguous: bool = False,
        include_self_edges: bool = False,
    ) -> Tuple[StaticDependencyEdge, ...]:
        selected = []
        for edge in self.edges:
            if edge.is_external:
                continue
            if edge.is_self_edge and not include_self_edges:
                continue
            if edge.ambiguous and not include_ambiguous:
                continue
            selected.append(edge)
        return tuple(selected)

    def report(self) -> StaticDependencyGraphReport:
        cycles = self.find_cycles()
        return StaticDependencyGraphReport(
            task_count=len(self.tasks),
            edge_count=len(self.edges),
            deterministic_edge_count=len(self.deterministic_edges),
            external_input_keys=self.external_input_keys,
            unresolved_external_input_keys=self.unresolved_external_input_keys,
            ambiguous_artifacts=self.ambiguous_artifacts,
            cycle_count=len(cycles),
        )

    def _adjacency(self, *, include_ambiguous: bool) -> Dict[TaskKey, Tuple[TaskKey, ...]]:
        outgoing: dict[TaskKey, list[TaskKey]] = defaultdict(list)
        for edge in self.iter_task_edges(include_ambiguous=include_ambiguous):
            if edge.producer_key is None:
                continue
            outgoing[edge.producer_key].append(edge.consumer_key)
        return {key: _ordered_unique(values, self.task_keys) for key, values in outgoing.items()}

    def _reverse_adjacency(self, *, include_ambiguous: bool) -> Dict[TaskKey, Tuple[TaskKey, ...]]:
        incoming: dict[TaskKey, list[TaskKey]] = defaultdict(list)
        for edge in self.iter_task_edges(include_ambiguous=include_ambiguous):
            if edge.producer_key is None:
                continue
            incoming[edge.consumer_key].append(edge.producer_key)
        return {key: _ordered_unique(values, self.task_keys) for key, values in incoming.items()}

    def _closure(self, task_key: TaskKey, adjacency: Mapping[TaskKey, Sequence[TaskKey]]) -> Tuple[TaskKey, ...]:
        seen: set[TaskKey] = set()
        queue = deque(adjacency.get(task_key, ()))
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(adjacency.get(current, ()))
        return tuple(task for task in self.task_keys if task in seen)

    def _require_task(self, task_key: TaskKey) -> None:
        if task_key not in set(self.task_keys):
            raise KeyError(f"Unknown task key: {task_key!r}")


def build_static_dependency_graph(contract: PipelineContract | None = None) -> StaticDependencyGraph:
    contract = contract or build_pipeline_contract()
    producer_map: dict[str, list[TaskKey]] = defaultdict(list)
    consumer_map: dict[str, list[TaskKey]] = defaultdict(list)

    for task in contract.tasks:
        for artifact_key in task.produces:
            producer_map[artifact_key].append(task.key)
        for artifact_key in task.consumes:
            consumer_map[artifact_key].append(task.key)

    edges: list[StaticDependencyEdge] = []
    for task in contract.tasks:
        for artifact_key in task.consumes:
            producer_keys = tuple(producer_map.get(artifact_key, ()))
            ambiguous = len(producer_keys) > 1
            if not producer_keys:
                edges.append(
                    StaticDependencyEdge(
                        artifact_key=artifact_key,
                        producer_key=None,
                        consumer_key=task.key,
                    )
                )
                continue
            for producer_key in producer_keys:
                edges.append(
                    StaticDependencyEdge(
                        artifact_key=artifact_key,
                        producer_key=producer_key,
                        consumer_key=task.key,
                        ambiguous=ambiguous,
                    )
                )

    return StaticDependencyGraph(
        tasks=contract.tasks,
        edges=tuple(edges),
        artifact_producers={key: tuple(values) for key, values in producer_map.items()},
        artifact_consumers={key: tuple(values) for key, values in consumer_map.items()},
    )


def is_known_external_artifact(artifact_key: str) -> bool:
    return artifact_key.startswith("args.")


def _ordered_unique(values: Iterable[TaskKey | None], task_order: Sequence[TaskKey]) -> Tuple[TaskKey, ...]:
    order = {task_key: index for index, task_key in enumerate(task_order)}
    unique = {value for value in values if value is not None}
    return tuple(sorted(unique, key=order.get))


def _canonical_cycle(cycle: Tuple[TaskKey, ...], task_order: Mapping[TaskKey, int]) -> Tuple[TaskKey, ...]:
    if len(cycle) <= 2:
        return cycle
    body = cycle[:-1]
    start_index = min(range(len(body)), key=lambda index: task_order.get(body[index], 10**9))
    rotated = body[start_index:] + body[:start_index]
    return tuple(rotated + (rotated[0],))
