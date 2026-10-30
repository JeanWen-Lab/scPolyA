from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Tuple

from .reuse_report import ReuseDecisionSummary, load_reuse_v2_decisions


@dataclass(frozen=True)
class ReuseDecisionPresence:
    match_key: str
    decision_id: int
    stage: str
    task_base: str
    action: str
    queued: bool
    legacy_done: bool
    execution_mode: str
    worker_arg: str | None
    worker_callable: str | None
    identity: Tuple[Tuple[str, Any], ...]
    params: Tuple[Tuple[str, Any], ...]
    reuse_policy_version: str | None = None
    implementation_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReuseDecisionChange:
    match_key: str
    stage: str
    task_base: str
    identity: Tuple[Tuple[str, Any], ...]
    params: Tuple[Tuple[str, Any], ...]
    changed_fields: Tuple[str, ...]
    left: ReuseDecisionPresence
    right: ReuseDecisionPresence

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReuseV2Comparison:
    left_db_path: str
    right_db_path: str
    compare_key: str
    left_decision_count: int
    right_decision_count: int
    left_instance_count: int
    right_instance_count: int
    shared_instance_count: int
    left_only_count: int
    right_only_count: int
    changed_count: int
    changed_decisions: Tuple[ReuseDecisionChange, ...]
    left_only: Tuple[ReuseDecisionPresence, ...]
    right_only: Tuple[ReuseDecisionPresence, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "left_db_path": self.left_db_path,
            "right_db_path": self.right_db_path,
            "compare_key": self.compare_key,
            "left_decision_count": self.left_decision_count,
            "right_decision_count": self.right_decision_count,
            "left_instance_count": self.left_instance_count,
            "right_instance_count": self.right_instance_count,
            "shared_instance_count": self.shared_instance_count,
            "left_only_count": self.left_only_count,
            "right_only_count": self.right_only_count,
            "changed_count": self.changed_count,
            "changed_decisions": [change.as_dict() for change in self.changed_decisions],
            "left_only": [presence.as_dict() for presence in self.left_only],
            "right_only": [presence.as_dict() for presence in self.right_only],
        }


def build_reuse_v2_comparison(
    left_state_or_db_path: str | Path,
    right_state_or_db_path: str | Path,
    *,
    limit: int = 20,
) -> ReuseV2Comparison:
    left_decisions = load_reuse_v2_decisions(left_state_or_db_path, latest_first=False)
    right_decisions = load_reuse_v2_decisions(right_state_or_db_path, latest_first=False)
    left_latest = _latest_decision_by_match_key(left_decisions)
    right_latest = _latest_decision_by_match_key(right_decisions)

    left_keys = set(left_latest)
    right_keys = set(right_latest)
    shared_keys = sorted(left_keys & right_keys)
    left_only_keys = sorted(left_keys - right_keys)
    right_only_keys = sorted(right_keys - left_keys)

    changes = []
    for match_key in shared_keys:
        left = left_latest[match_key]
        right = right_latest[match_key]
        changed_fields = _changed_fields(left, right)
        if changed_fields:
            changes.append(
                ReuseDecisionChange(
                    match_key=match_key,
                    stage=left.stage,
                    task_base=left.task_base,
                    identity=left.identity,
                    params=left.params,
                    changed_fields=changed_fields,
                    left=_presence_from_decision(match_key, left),
                    right=_presence_from_decision(match_key, right),
                )
            )

    return ReuseV2Comparison(
        left_db_path=str(_display_db_path(left_state_or_db_path)),
        right_db_path=str(_display_db_path(right_state_or_db_path)),
        compare_key="stage + task_base + identity + params",
        left_decision_count=len(left_decisions),
        right_decision_count=len(right_decisions),
        left_instance_count=len(left_latest),
        right_instance_count=len(right_latest),
        shared_instance_count=len(shared_keys),
        left_only_count=len(left_only_keys),
        right_only_count=len(right_only_keys),
        changed_count=len(changes),
        changed_decisions=tuple(changes[: max(0, int(limit))]),
        left_only=tuple(_presence_from_decision(key, left_latest[key]) for key in left_only_keys[: max(0, int(limit))]),
        right_only=tuple(_presence_from_decision(key, right_latest[key]) for key in right_only_keys[: max(0, int(limit))]),
    )


def format_reuse_v2_comparison(comparison: ReuseV2Comparison) -> str:
    lines = [
        "Reuse v2 comparison",
        f"left: {comparison.left_db_path}",
        f"right: {comparison.right_db_path}",
        f"compare key: {comparison.compare_key}",
        f"decision rows: left={comparison.left_decision_count} right={comparison.right_decision_count}",
        (
            "latest instances: "
            f"left={comparison.left_instance_count} right={comparison.right_instance_count} "
            f"shared={comparison.shared_instance_count} "
            f"left_only={comparison.left_only_count} right_only={comparison.right_only_count}"
        ),
        f"changed shared instances: {comparison.changed_count}",
    ]
    if comparison.changed_decisions:
        lines.append("changed decisions:")
        for change in comparison.changed_decisions:
            lines.extend(_format_change(change))
    if comparison.left_only:
        lines.append("left-only latest instances:")
        for presence in comparison.left_only:
            lines.append(f"  {_format_presence(presence)}")
    if comparison.right_only:
        lines.append("right-only latest instances:")
        for presence in comparison.right_only:
            lines.append(f"  {_format_presence(presence)}")
    return "\n".join(lines)


def _latest_decision_by_match_key(
    decisions: Tuple[ReuseDecisionSummary, ...],
) -> dict[str, ReuseDecisionSummary]:
    latest: dict[str, ReuseDecisionSummary] = {}
    for decision in decisions:
        latest[_match_key(decision)] = decision
    return latest


def _match_key(decision: ReuseDecisionSummary) -> str:
    payload = {
        "stage": decision.stage,
        "task_base": decision.task_base,
        "identity": decision.identity,
        "params": decision.params,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _presence_from_decision(match_key: str, decision: ReuseDecisionSummary) -> ReuseDecisionPresence:
    return ReuseDecisionPresence(
        match_key=match_key,
        decision_id=decision.decision_id,
        stage=decision.stage,
        task_base=decision.task_base,
        action=decision.action,
        queued=decision.queued,
        legacy_done=decision.legacy_done,
        execution_mode=decision.execution_mode,
        worker_arg=decision.worker_arg,
        worker_callable=decision.worker_callable,
        reuse_policy_version=decision.reuse_policy_version,
        implementation_version=decision.implementation_version,
        identity=decision.identity,
        params=decision.params,
    )


def _changed_fields(left: ReuseDecisionSummary, right: ReuseDecisionSummary) -> Tuple[str, ...]:
    fields: list[str] = []
    for name in (
        "action",
        "queued",
        "legacy_done",
        "force",
        "legacy_variant_str",
        "execution_mode",
        "worker_arg",
        "worker_callable",
        "reuse_policy_version",
        "implementation_version",
        "consumes",
        "produces",
        "worker_payload",
        "branch_artifact_manifest",
        "reasons",
    ):
        if getattr(left, name) != getattr(right, name):
            fields.append(name)
    return tuple(fields)


def _format_change(change: ReuseDecisionChange) -> list[str]:
    identity = _format_pairs(change.identity)
    params = _format_pairs(change.params)
    return [
        (
            f"  {change.stage}:{change.task_base} {change.match_key[:12]} "
            f"changed=[{', '.join(change.changed_fields)}]"
        ),
        f"    identity=[{identity}] params=[{params}]",
        f"    left: {_format_presence(change.left)}",
        f"    right: {_format_presence(change.right)}",
    ]


def _format_presence(presence: ReuseDecisionPresence) -> str:
    return (
        f"#{presence.decision_id} {presence.stage}:{presence.task_base} "
        f"{presence.action} mode={presence.execution_mode or '-'} "
        f"worker_arg={presence.worker_arg or '-'} "
        f"worker_callable={presence.worker_callable or '-'} "
        f"policy={presence.reuse_policy_version or '-'} "
        f"implementation={presence.implementation_version or '-'} "
        f"queued={int(presence.queued)} legacy_done={int(presence.legacy_done)} "
        f"key={presence.match_key[:12]}"
    )


def _format_pairs(pairs: Tuple[Tuple[str, Any], ...]) -> str:
    return ", ".join(f"{key}={value}" for key, value in pairs) or "-"


def _stable_json(value: Any) -> str:
    return json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":"))


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _display_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path).expanduser()
    if path.is_dir():
        if path.name == "state":
            return path / "state.sqlite"
        candidate = path / "state" / "state.sqlite"
        if candidate.exists():
            return candidate
        return path / "state.sqlite"
    return path
