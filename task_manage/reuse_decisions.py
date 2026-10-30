from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Iterable, Literal, Tuple

from .task_instance_v3 import (
    TaskInstanceV3,
    task_instance_v2_compatibility_payload_from_v3,
)


ReuseDecisionAction = Literal[
    "legacy_reuse",
    "would_run",
    "hybrid_reuse",
    "hybrid_recompute",
    "v2_reuse",
    "v2_recompute",
    "v2_recompute_completed",
    "v2_fallback_legacy",
    "blocked",
]


@dataclass(frozen=True)
class ReuseDecisionV2:
    task_instance: TaskInstanceV3
    action: ReuseDecisionAction
    legacy_done: bool
    force: bool
    queued: bool
    reasons: Tuple[str, ...]
    worker_callable: str = ""

    @property
    def stage(self) -> str:
        return self.task_instance.stage

    @property
    def task_base(self) -> str:
        return self.task_instance.task_base

    @property
    def instance_id(self) -> str:
        return self.task_instance.instance_id

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "task_base": self.task_base,
            "instance_id": self.instance_id,
            "action": self.action,
            "legacy_done": self.legacy_done,
            "force": self.force,
            "queued": self.queued,
            "reasons": list(self.reasons),
            "worker_callable": self.worker_callable,
            "task_instance": (
                task_instance_v2_compatibility_payload_from_v3(
                    self.task_instance
                )
            ),
        }


def worker_callable_name(fn: Callable[..., Any]) -> str:
    """Describe the candidate callable passed to ``run_flex_batch``, not execution history."""
    try:
        if isinstance(fn, partial):
            target = worker_callable_name(fn.func)
            return f"functools.partial({target})" if target else "functools.partial"

        module = getattr(fn, "__module__", "")
        qualname = getattr(fn, "__qualname__", "")
        if isinstance(module, str) and module and isinstance(qualname, str) and qualname:
            return f"{module}:{qualname}"

        callable_type = type(fn)
        module = getattr(callable_type, "__module__", "")
        qualname = getattr(callable_type, "__qualname__", callable_type.__name__)
        if module:
            return f"{module}:{qualname}.__call__"
        return f"{qualname}.__call__"
    except Exception:
        return ""


def decide_reuse_v2_observe_only(
    task_instance: TaskInstanceV3,
    *,
    legacy_done: bool,
    force: bool,
    queued: bool,
    worker_callable: str = "",
) -> ReuseDecisionV2:
    reasons: list[str] = ["observe_only"]

    if force:
        action: ReuseDecisionAction = "would_run"
        reasons.append("force_requested")
    elif legacy_done:
        action = "legacy_reuse"
        reasons.append("legacy_task_done_flex_match")
    else:
        action = "would_run"
        reasons.append("no_legacy_task_done_flex_match")

    if queued:
        reasons.append("legacy_scheduler_would_queue")
    else:
        reasons.append("legacy_scheduler_would_skip")

    return ReuseDecisionV2(
        task_instance=task_instance,
        action=action,
        legacy_done=legacy_done,
        force=force,
        queued=queued,
        reasons=tuple(reasons),
        worker_callable=worker_callable,
    )


def decide_reuse_v2_hybrid_gate(
    task_instance: TaskInstanceV3,
    *,
    action: Literal["hybrid_reuse", "hybrid_recompute"],
    queued: bool,
    force: bool,
    gate_reasons: Iterable[str],
    worker_callable: str = "",
) -> ReuseDecisionV2:
    reasons = ["hybrid_gate_enabled", *gate_reasons]
    if queued:
        reasons.append("legacy_done_overridden_by_hybrid_gate")
    else:
        reasons.append("legacy_done_preserved_by_hybrid_gate")
    return ReuseDecisionV2(
        task_instance=task_instance,
        action=action,
        legacy_done=True,
        force=force,
        queued=queued,
        reasons=tuple(reasons),
        worker_callable=worker_callable,
    )


def decide_reuse_v2_canary_authority(
    task_instance: TaskInstanceV3,
    *,
    action: Literal[
        "v2_reuse",
        "v2_recompute",
        "v2_recompute_completed",
        "v2_fallback_legacy",
    ],
    queued: bool,
    force: bool,
    authority_reasons: Iterable[str],
    worker_callable: str = "",
) -> ReuseDecisionV2:
    reasons = ["reuse_v2_authority_v1", *authority_reasons]
    if queued:
        reasons.append("legacy_done_overridden_by_v2_authority")
    else:
        reasons.append("legacy_done_preserved_by_v2_authority")
    return ReuseDecisionV2(
        task_instance=task_instance,
        action=action,
        legacy_done=True,
        force=force,
        queued=queued,
        reasons=tuple(dict.fromkeys(str(reason) for reason in reasons if str(reason))),
        worker_callable=worker_callable,
    )
