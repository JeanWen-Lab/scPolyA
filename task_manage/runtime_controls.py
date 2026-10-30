from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .task_specs import TASK_SPECS


RuntimeControlKind = Literal[
    "task_workers",
    "tool_threads",
    "legacy_noop",
]
RuntimeControlGroupKey = Literal[
    "general_analysis",
    "preprocessing",
    "polya_extraction",
    "cohort_merge",
    "feature_assignment",
    "legacy",
]
RuntimeControlZeroSemantics = Literal[
    "automatic_cpu_cap",
    "inherit_n_workers",
    "samtools_no_extra_threads",
    "coerce_to_one",
    "no_effect",
]
TaskKey = tuple[str, str]


@dataclass(frozen=True)
class RuntimeControlGroupSpec:
    key: RuntimeControlGroupKey
    label: str
    description: str


@dataclass(frozen=True)
class RuntimeControlSpec:
    key: str
    cli_flag: str
    label: str
    group: RuntimeControlGroupKey
    kind: RuntimeControlKind
    default: int
    zero_semantics: RuntimeControlZeroSemantics
    description: str
    task_keys: tuple[TaskKey, ...] = ()
    fallback_task_keys: tuple[TaskKey, ...] = ()
    tools: tuple[str, ...] = ()
    paired_control: str = ""
    legacy: bool = False

    @property
    def visible_by_default(self) -> bool:
        return not self.legacy and self.kind != "legacy_noop"


RUNTIME_CONTROL_GROUPS: tuple[RuntimeControlGroupSpec, ...] = (
    RuntimeControlGroupSpec(
        key="general_analysis",
        label="General analysis",
        description="Shared task parallelism for UMI, peak, and matrix stages.",
    ),
    RuntimeControlGroupSpec(
        key="preprocessing",
        label="Preprocessing",
        description="Per-unit BAM split, filter, barcode, and dedup task pools.",
    ),
    RuntimeControlGroupSpec(
        key="polya_extraction",
        label="PolyA extraction",
        description="PolyA extraction and optional strand-BAM materialization task pools.",
    ),
    RuntimeControlGroupSpec(
        key="cohort_merge",
        label="Cohort merge",
        description="Concurrent merge jobs and samtools threads inside each job.",
    ),
    RuntimeControlGroupSpec(
        key="feature_assignment",
        label="Feature assignment",
        description="Concurrent sidecar jobs and featureCounts threads per job.",
    ),
    RuntimeControlGroupSpec(
        key="legacy",
        label="Legacy inactive controls",
        description="Accepted for historical CLI compatibility but not consumed.",
    ),
)


def _task_keys_for_worker_arg(*worker_args: str) -> tuple[TaskKey, ...]:
    accepted = frozenset(worker_args)
    return tuple(
        spec.key
        for spec in TASK_SPECS
        if spec.worker_arg in accepted
    )


_ANNOTATION_TASK_KEYS = _task_keys_for_worker_arg(
    "n_workers_annotate_or_n_workers"
)


RUNTIME_CONTROL_SPECS: tuple[RuntimeControlSpec, ...] = (
    RuntimeControlSpec(
        key="n_workers",
        cli_flag="--n-workers",
        label="General analysis task workers",
        group="general_analysis",
        kind="task_workers",
        default=4,
        zero_semantics="automatic_cpu_cap",
        description=(
            "Concurrent tasks for UMI counting, peak calling and annotation, "
            "count/RNA matrices, and matrix summaries."
        ),
        task_keys=_task_keys_for_worker_arg("n_workers"),
        fallback_task_keys=_ANNOTATION_TASK_KEYS,
    ),
    RuntimeControlSpec(
        key="max_split_workers",
        cli_flag="--max-split-workers",
        label="BAM split task workers",
        group="preprocessing",
        kind="task_workers",
        default=4,
        zero_semantics="automatic_cpu_cap",
        description="Concurrent sample-contig BAM split tasks.",
        task_keys=_task_keys_for_worker_arg("max_split_workers"),
    ),
    RuntimeControlSpec(
        key="max_filter_workers",
        cli_flag="--max-filter-workers",
        label="Filter and barcode task workers",
        group="preprocessing",
        kind="task_workers",
        default=4,
        zero_semantics="automatic_cpu_cap",
        description=(
            "Concurrent BAM filter tasks and barcode-suffix rewrite tasks."
        ),
        task_keys=_task_keys_for_worker_arg("max_filter_workers"),
    ),
    RuntimeControlSpec(
        key="max_dedup_workers",
        cli_flag="--max-dedup-workers",
        label="UMI dedup task workers",
        group="preprocessing",
        kind="task_workers",
        default=4,
        zero_semantics="automatic_cpu_cap",
        description="Concurrent per-unit umi_tools dedup tasks.",
        task_keys=_task_keys_for_worker_arg("max_dedup_workers"),
    ),
    RuntimeControlSpec(
        key="max_extract_workers",
        cli_flag="--max-extract-workers",
        label="PolyA extraction task workers",
        group="polya_extraction",
        kind="task_workers",
        default=16,
        zero_semantics="automatic_cpu_cap",
        description=(
            "Concurrent PolyA extraction child processes and optional "
            "strand-BAM materialization tasks."
        ),
        task_keys=_task_keys_for_worker_arg("max_extract_workers"),
    ),
    RuntimeControlSpec(
        key="max_merge_workers",
        cli_flag="--max-merge-workers",
        label="Cohort merge task workers",
        group="cohort_merge",
        kind="task_workers",
        default=4,
        zero_semantics="automatic_cpu_cap",
        description="Concurrent chromosome merge and final BAM cat tasks.",
        task_keys=_task_keys_for_worker_arg("max_merge_workers"),
        paired_control="merge_thread",
    ),
    RuntimeControlSpec(
        key="merge_thread",
        cli_flag="--merge-thread",
        label="samtools threads per merge task",
        group="cohort_merge",
        kind="tool_threads",
        default=4,
        zero_semantics="samtools_no_extra_threads",
        description=(
            "Value passed to samtools merge -@ for each concurrent merge task."
        ),
        task_keys=(
            ("merge_units", "merge_chunks"),
            ("merge_units", "merge_strand_chunks"),
        ),
        tools=("samtools",),
        paired_control="max_merge_workers",
    ),
    RuntimeControlSpec(
        key="n_workers_annotate",
        cli_flag="--n-workers-annotate",
        label="Feature assignment task workers",
        group="feature_assignment",
        kind="task_workers",
        default=0,
        zero_semantics="inherit_n_workers",
        description=(
            "Concurrent peak and RNA-gene feature sidecar tasks; zero inherits "
            "general analysis task workers."
        ),
        task_keys=_ANNOTATION_TASK_KEYS,
        paired_control="n_threads_bam_annotation",
    ),
    RuntimeControlSpec(
        key="n_threads_bam_annotation",
        cli_flag="--n-threads-bam-annotation",
        label="featureCounts threads per assignment task",
        group="feature_assignment",
        kind="tool_threads",
        default=4,
        zero_semantics="coerce_to_one",
        description=(
            "featureCounts -T value inside each concurrent peak or RNA-gene "
            "feature assignment task."
        ),
        task_keys=_ANNOTATION_TASK_KEYS,
        tools=("featureCounts",),
        paired_control="n_workers_annotate",
    ),
    RuntimeControlSpec(
        key="n_threads",
        cli_flag="--n-threads",
        label="Legacy global threads",
        group="legacy",
        kind="legacy_noop",
        default=4,
        zero_semantics="no_effect",
        description="No current pipeline consumer; retained for old CLI compatibility.",
        legacy=True,
    ),
    RuntimeControlSpec(
        key="n_threads_merge",
        cli_flag="--n-threads-merge",
        label="Legacy merge tool threads",
        group="legacy",
        kind="legacy_noop",
        default=0,
        zero_semantics="no_effect",
        description="No current pipeline consumer; retained for old CLI compatibility.",
        legacy=True,
    ),
)


def runtime_control_specs_by_key() -> dict[str, RuntimeControlSpec]:
    return {spec.key: spec for spec in RUNTIME_CONTROL_SPECS}


def visible_runtime_control_specs() -> tuple[RuntimeControlSpec, ...]:
    return tuple(
        spec for spec in RUNTIME_CONTROL_SPECS if spec.visible_by_default
    )


def runtime_control_specs_by_group(
    *,
    include_legacy: bool = False,
) -> tuple[tuple[RuntimeControlGroupSpec, tuple[RuntimeControlSpec, ...]], ...]:
    visible = tuple(
        spec
        for spec in RUNTIME_CONTROL_SPECS
        if include_legacy or spec.visible_by_default
    )
    return tuple(
        (
            group,
            tuple(spec for spec in visible if spec.group == group.key),
        )
        for group in RUNTIME_CONTROL_GROUPS
        if include_legacy or group.key != "legacy"
    )


def validate_runtime_control_specs() -> None:
    expected_keys = {
        "n_workers",
        "n_threads",
        "max_split_workers",
        "max_filter_workers",
        "max_dedup_workers",
        "max_extract_workers",
        "merge_thread",
        "max_merge_workers",
        "n_workers_annotate",
        "n_threads_merge",
        "n_threads_bam_annotation",
    }
    by_key = runtime_control_specs_by_key()
    if len(by_key) != len(RUNTIME_CONTROL_SPECS):
        raise AssertionError("Duplicate runtime control key")
    if set(by_key) != expected_keys:
        raise AssertionError(
            "Runtime control key coverage mismatch: "
            f"missing={sorted(expected_keys - set(by_key))}, "
            f"extra={sorted(set(by_key) - expected_keys)}"
        )

    task_specs = {spec.key: spec for spec in TASK_SPECS}
    for spec in RUNTIME_CONTROL_SPECS:
        unknown_tasks = sorted(
            key
            for key in (*spec.task_keys, *spec.fallback_task_keys)
            if key not in task_specs
        )
        if unknown_tasks:
            raise AssertionError(
                f"Runtime control {spec.key} has unknown tasks: {unknown_tasks}"
            )
        if spec.paired_control:
            paired = by_key.get(spec.paired_control)
            if paired is None or paired.paired_control != spec.key:
                raise AssertionError(
                    f"Runtime control pair is not symmetric: {spec.key}"
                )
        if spec.kind == "legacy_noop" and not spec.legacy:
            raise AssertionError(
                f"Legacy no-op control is not marked legacy: {spec.key}"
            )

    worker_args = {
        spec.worker_arg
        for spec in TASK_SPECS
        if spec.worker_arg not in {None, "fixed_1"}
    }
    represented_worker_args = {
        spec.key
        for spec in RUNTIME_CONTROL_SPECS
        if spec.kind == "task_workers" and spec.key != "n_workers_annotate"
    }
    represented_worker_args.add("n_workers_annotate_or_n_workers")
    if worker_args != represented_worker_args:
        raise AssertionError(
            "TaskSpec worker binding coverage mismatch: "
            f"missing={sorted(worker_args - represented_worker_args)}, "
            f"extra={sorted(represented_worker_args - worker_args)}"
        )
