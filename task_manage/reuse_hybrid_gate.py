from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple

from .artifact_health import (
    ArtifactHealthTaskRecordV2,
    check_task_artifact_health_v2,
)
from .task_coordinate import TaskCoordinateV1


REUSE_V2_HYBRID_GATE_ENV = "SCPOLYA_REUSE_V2_HYBRID_GATE"
REUSE_V2_HYBRID_TASKS_ENV = "SCPOLYA_REUSE_V2_HYBRID_TASKS"

DEFAULT_HYBRID_GATE_TASKS: frozenset[tuple[str, str]] = frozenset(
    {
        ("call_peaks", "combine_and_intersect_peaks"),
        ("annotate_peaks", "resolve_peak_issues"),
        ("annotate_peaks", "convert_peaks_bed_to_bigbed"),
        ("annotate_peaks", "convert_peaks_bed_to_gtf"),
        ("annotate_bams", "annotate_slices"),
        ("count_matrix", "summarize_count_matrix"),
        ("summarize_matrix", "summarize_matrix"),
        ("rna_gene_matrix", "summarize_gene_matrix"),
    }
)


@dataclass(frozen=True)
class HybridReuseGateResult:
    enabled: bool
    applies_to_task: bool
    checked: bool
    allow_reuse: bool
    expectation_count: int = 0
    ready_count: int = 0
    missing_count: int = 0
    empty_count: int = 0
    unresolved_count: int = 0
    reasons: Tuple[str, ...] = ()

    @property
    def should_queue(self) -> bool:
        return self.enabled and self.applies_to_task and self.checked and not self.allow_reuse


def hybrid_reuse_gate_enabled() -> bool:
    return _env_truthy(os.environ.get(REUSE_V2_HYBRID_GATE_ENV))


def hybrid_gate_task_keys() -> frozenset[tuple[str, str]]:
    raw = os.environ.get(REUSE_V2_HYBRID_TASKS_ENV, "").strip()
    if not raw:
        return DEFAULT_HYBRID_GATE_TASKS
    if raw.lower() == "all":
        return DEFAULT_HYBRID_GATE_TASKS
    return frozenset(_parse_task_key(item) for item in raw.split(",") if item.strip())


def hybrid_reuse_gate_applies(stage: str, task_base: str) -> bool:
    return (stage, task_base) in hybrid_gate_task_keys()


def evaluate_hybrid_reuse_gate(
    *,
    state_or_db_path: str,
    config: dict,
    task_coordinate: TaskCoordinateV1,
) -> HybridReuseGateResult:
    stage = task_coordinate.stage
    task_base = task_coordinate.task_base
    if not hybrid_reuse_gate_enabled():
        return HybridReuseGateResult(
            enabled=False,
            applies_to_task=False,
            checked=False,
            allow_reuse=True,
            reasons=("hybrid_gate_disabled",),
        )

    if not hybrid_reuse_gate_applies(stage, task_base):
        return HybridReuseGateResult(
            enabled=True,
            applies_to_task=False,
            checked=False,
            allow_reuse=True,
            reasons=("hybrid_gate_not_configured_for_task",),
        )

    record = ArtifactHealthTaskRecordV2(
        task_coordinate=task_coordinate,
        done_at="",
    )
    entries = check_task_artifact_health_v2(
        state_or_db_path,
        record,
        config=config,
    )
    if not entries:
        return HybridReuseGateResult(
            enabled=True,
            applies_to_task=True,
            checked=False,
            allow_reuse=True,
            reasons=("hybrid_gate_no_artifact_expectations",),
        )

    ready_count = sum(1 for entry in entries if entry.status == "ready")
    missing_count = sum(1 for entry in entries if entry.status == "missing")
    empty_count = sum(1 for entry in entries if entry.status == "empty")
    unresolved_count = sum(1 for entry in entries if entry.status == "unresolved")
    allow_reuse = missing_count == 0 and empty_count == 0 and unresolved_count == 0
    reasons = ["hybrid_gate_checked_outputs"]
    if allow_reuse:
        reasons.append("hybrid_gate_outputs_ready")
    else:
        reasons.append("hybrid_gate_outputs_not_ready")
        if missing_count:
            reasons.append(f"missing_outputs={missing_count}")
        if empty_count:
            reasons.append(f"empty_outputs={empty_count}")
        if unresolved_count:
            reasons.append(f"unresolved_outputs={unresolved_count}")

    return HybridReuseGateResult(
        enabled=True,
        applies_to_task=True,
        checked=True,
        allow_reuse=allow_reuse,
        expectation_count=len(entries),
        ready_count=ready_count,
        missing_count=missing_count,
        empty_count=empty_count,
        unresolved_count=unresolved_count,
        reasons=tuple(reasons),
    )


def _env_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_task_key(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if ":" in text:
        stage, task_base = text.split(":", 1)
    elif "/" in text:
        stage, task_base = text.split("/", 1)
    else:
        raise ValueError(
            f"Invalid {REUSE_V2_HYBRID_TASKS_ENV} task key {raw!r}; "
            "expected stage:task_base"
        )
    return stage.strip(), task_base.strip()
