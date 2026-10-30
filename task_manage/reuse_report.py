from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Tuple

from .reuse_decision_storage import (
    CurrentStoredReuseDecisionRowV2,
    ReuseDecisionStorageError,
    StoredReuseDecisionRowV1,
    archived_reuse_decision_storage_available_v2,
    current_reuse_decision_storage_available_v1,
    load_archived_reuse_decision_rows_v2,
    load_current_reuse_decision_rows_v1_compatibility,
    load_current_reuse_decision_rows_v2,
)
from .task_coordinate import (
    TaskCoordinateError,
    build_task_coordinate_v1,
)


CURRENT_REUSE_DECISION_SUMMARY_SCHEMA_V2 = (
    "scpolya.current_reuse_decision_summary.v2"
)
CurrentDecisionCoordinateStatus = Literal[
    "available",
    "malformed",
    "compatibility_mismatch",
]


@dataclass(frozen=True)
class ReuseDecisionSummary:
    decision_id: int
    stage: str
    task_base: str
    action: str
    legacy_done: bool
    queued: bool
    force: bool
    legacy_variant_str: str
    identity_hash: str
    params_hash: str
    instance_id: str
    reasons: Tuple[str, ...]
    identity: Tuple[Tuple[str, Any], ...]
    params: Tuple[Tuple[str, Any], ...]
    execution_mode: str
    worker_arg: str | None
    worker_callable: str | None
    consumes: Tuple[str, ...]
    produces: Tuple[str, ...]
    worker_payload: Mapping[str, Any]
    created_at: str
    reuse_policy_version: str | None = None
    implementation_version: str | None = None
    declaration_source: str | None = None
    branch_artifact_manifest: Any | None = None
    decision_input_manifest: Any | None = None
    inline_input_observation_manifest: Any | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.stage, self.task_base

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CurrentReuseDecisionSummaryV2:
    """Alias-free decision view for one validated current storage policy."""

    decision_id: int
    stage: str
    task_base: str
    action: str
    legacy_done: bool
    queued: bool
    force: bool
    identity_hash: str
    params_hash: str
    instance_id: str
    reasons: Tuple[str, ...]
    identity: Tuple[Tuple[str, Any], ...]
    params: Tuple[Tuple[str, Any], ...]
    execution_mode: str
    worker_arg: str | None
    worker_callable: str | None
    consumes: Tuple[str, ...]
    produces: Tuple[str, ...]
    worker_payload: Mapping[str, Any]
    created_at: str
    task_coordinate_id: str
    coordinate_status: CurrentDecisionCoordinateStatus
    reuse_policy_version: str | None = None
    implementation_version: str | None = None
    declaration_source: str | None = None
    branch_artifact_manifest: Any | None = None
    decision_input_manifest: Any | None = None
    inline_input_observation_manifest: Any | None = None
    schema_version: str = CURRENT_REUSE_DECISION_SUMMARY_SCHEMA_V2

    def __post_init__(self) -> None:
        if self.schema_version != CURRENT_REUSE_DECISION_SUMMARY_SCHEMA_V2:
            raise ValueError(
                "Unsupported current reuse-decision summary schema: "
                f"{self.schema_version!r}"
            )
        if self.coordinate_status not in {
            "available",
            "malformed",
            "compatibility_mismatch",
        }:
            raise ValueError(
                "Unsupported current decision coordinate status: "
                f"{self.coordinate_status!r}"
            )
        if (
            self.coordinate_status != "malformed"
            and not self.task_coordinate_id
        ):
            raise ValueError(
                "A reconstructed current decision coordinate requires an ID"
            )

    @property
    def key(self) -> tuple[str, str]:
        return self.stage, self.task_base

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReuseV2Report:
    db_path: str
    has_decision_table: bool
    decision_count: int
    action_counts: dict[str, int]
    task_counts: dict[str, int]
    execution_mode_counts: dict[str, int]
    worker_arg_counts: dict[str, int]
    worker_callable_counts: dict[str, int]
    reason_counts: dict[str, int]
    latest_decisions: Tuple[ReuseDecisionSummary, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "db_path": self.db_path,
            "has_decision_table": self.has_decision_table,
            "decision_count": self.decision_count,
            "action_counts": self.action_counts,
            "task_counts": self.task_counts,
            "execution_mode_counts": self.execution_mode_counts,
            "worker_arg_counts": self.worker_arg_counts,
            "worker_callable_counts": self.worker_callable_counts,
            "reason_counts": self.reason_counts,
            "latest_decisions": [decision.as_dict() for decision in self.latest_decisions],
        }


def build_reuse_v2_report(
    state_or_db_path: str | Path,
    *,
    limit: int = 20,
    archived_v2: bool = False,
) -> ReuseV2Report:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.exists():
        return _empty_report(db_path)
    has_decision_table = (
        archived_reuse_decision_storage_available_v2(db_path)
        if archived_v2
        else current_reuse_decision_storage_available_v1(db_path)
    )
    if not has_decision_table:
        if (
            not archived_v2
            and archived_reuse_decision_storage_available_v2(db_path)
        ):
            raise ReuseDecisionStorageError(
                "Legacy-only reuse-decision storage requires the explicit "
                "archived_v2 reader"
            )
        return _empty_report(db_path)

    decisions = load_reuse_v2_decisions(
        db_path,
        limit=None,
        latest_first=False,
        archived_v2=archived_v2,
    )
    action_counts = dict(
        sorted(Counter(decision.action for decision in decisions).items())
    )
    task_counter = Counter(decision.key for decision in decisions)
    task_count_rows = tuple(
        (stage, task_base, count)
        for (stage, task_base), count in sorted(task_counter.items())
    )
    task_counts = {
        f"{stage}:{task_base}": int(count)
        for stage, task_base, count in task_count_rows
    }
    registry = _load_task_registry_read_only(db_path)
    execution_mode_counts, worker_arg_counts = _declaration_counts(
        task_count_rows,
        registry,
    )
    worker_callable_counts = dict(
        sorted(
            Counter(
                decision.worker_callable
                for decision in decisions
                if decision.worker_callable
            ).items()
        )
    )
    latest = tuple(reversed(decisions))[: max(0, int(limit))]
    reason_counter: Counter[str] = Counter()
    for decision in latest:
        reason_counter.update(decision.reasons)
    return ReuseV2Report(
        db_path=str(db_path),
        has_decision_table=True,
        decision_count=len(decisions),
        action_counts=action_counts,
        task_counts=task_counts,
        execution_mode_counts=execution_mode_counts,
        worker_arg_counts=worker_arg_counts,
        worker_callable_counts=worker_callable_counts,
        reason_counts=dict(sorted(reason_counter.items())),
        latest_decisions=latest,
    )


def load_reuse_v2_decisions(
    state_or_db_path: str | Path,
    *,
    limit: int | None = None,
    latest_first: bool = True,
    archived_v2: bool = False,
) -> Tuple[ReuseDecisionSummary, ...]:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.exists():
        return ()

    rows = (
        load_archived_reuse_decision_rows_v2(
            db_path,
            limit=limit,
            latest_first=latest_first,
        )
        if archived_v2
        else load_current_reuse_decision_rows_v1_compatibility(
            db_path,
            limit=limit,
            latest_first=latest_first,
        )
    )
    registry = _load_task_registry_read_only(db_path)
    return tuple(_decision_from_row(row, registry) for row in rows)


def load_current_reuse_decision_summaries_v2(
    state_or_db_path: str | Path,
    *,
    limit: int | None = None,
    latest_first: bool = True,
) -> Tuple[CurrentReuseDecisionSummaryV2, ...]:
    """Load normalized merged history under the exact current policy."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.exists():
        return ()
    rows = load_current_reuse_decision_rows_v2(
        db_path,
        limit=limit,
        latest_first=latest_first,
    )
    registry = _load_task_registry_read_only(db_path)
    return tuple(
        _current_decision_summary_v2_from_row(row, registry)
        for row in rows
    )


def load_archived_reuse_v2_decisions(
    state_or_db_path: str | Path,
    *,
    limit: int | None = None,
    latest_first: bool = True,
) -> Tuple[ReuseDecisionSummary, ...]:
    return load_reuse_v2_decisions(
        state_or_db_path,
        limit=limit,
        latest_first=latest_first,
        archived_v2=True,
    )


def current_reuse_decision_summary_v2_from_v1_compatibility(
    decision: ReuseDecisionSummary,
) -> CurrentReuseDecisionSummaryV2:
    """Normalize one released summary before current graph consumption."""

    if not isinstance(decision, ReuseDecisionSummary):
        raise TypeError(
            "Current decision-summary compatibility input must be "
            "ReuseDecisionSummary"
        )
    task_coordinate_id, coordinate_status = _coordinate_status(
        stage=decision.stage,
        task_base=decision.task_base,
        identity=decision.identity,
        params=decision.params,
        compatibility_alias=decision.legacy_variant_str,
    )
    return CurrentReuseDecisionSummaryV2(
        decision_id=decision.decision_id,
        stage=decision.stage,
        task_base=decision.task_base,
        action=decision.action,
        legacy_done=decision.legacy_done,
        queued=decision.queued,
        force=decision.force,
        identity_hash=decision.identity_hash,
        params_hash=decision.params_hash,
        instance_id=decision.instance_id,
        reasons=decision.reasons,
        identity=decision.identity,
        params=decision.params,
        execution_mode=decision.execution_mode,
        worker_arg=decision.worker_arg,
        worker_callable=decision.worker_callable,
        consumes=decision.consumes,
        produces=decision.produces,
        worker_payload=decision.worker_payload,
        created_at=decision.created_at,
        task_coordinate_id=task_coordinate_id,
        coordinate_status=coordinate_status,
        reuse_policy_version=decision.reuse_policy_version,
        implementation_version=decision.implementation_version,
        declaration_source=decision.declaration_source,
        branch_artifact_manifest=decision.branch_artifact_manifest,
        decision_input_manifest=decision.decision_input_manifest,
        inline_input_observation_manifest=(
            decision.inline_input_observation_manifest
        ),
    )


def format_reuse_v2_report(report: ReuseV2Report) -> str:
    lines = [
        f"Reuse v2 report: {report.db_path}",
        f"decision table: {'yes' if report.has_decision_table else 'no'}",
        f"decision count: {report.decision_count}",
    ]
    if report.action_counts:
        lines.append("actions:")
        for action, count in sorted(report.action_counts.items()):
            lines.append(f"  {action}: {count}")
    if report.task_counts:
        lines.append("tasks:")
        for task_key, count in sorted(report.task_counts.items()):
            lines.append(f"  {task_key}: {count}")
    if report.execution_mode_counts:
        lines.append("execution modes:")
        for execution_mode, count in sorted(report.execution_mode_counts.items()):
            lines.append(f"  {execution_mode}: {count}")
    if report.worker_arg_counts:
        lines.append("worker args:")
        for worker_arg, count in sorted(report.worker_arg_counts.items()):
            lines.append(f"  {worker_arg}: {count}")
    if report.worker_callable_counts:
        lines.append("worker callables:")
        for worker_callable, count in sorted(report.worker_callable_counts.items()):
            lines.append(f"  {worker_callable}: {count}")
    if report.reason_counts:
        lines.append("latest reasons:")
        for reason, count in sorted(report.reason_counts.items()):
            lines.append(f"  {reason}: {count}")
    if report.latest_decisions:
        lines.append("latest decisions:")
        for decision in report.latest_decisions:
            identity = ", ".join(f"{key}={value}" for key, value in decision.identity) or "-"
            params = ", ".join(f"{key}={value}" for key, value in decision.params) or "-"
            lines.append(
                f"  #{decision.decision_id} {decision.stage}:{decision.task_base} "
                f"{decision.action} mode={decision.execution_mode or '-'} "
                f"worker_arg={decision.worker_arg or '-'} "
                f"worker_callable={decision.worker_callable or '-'} "
                f"policy={decision.reuse_policy_version or '-'} "
                f"implementation={decision.implementation_version or '-'} "
                f"declarations={decision.declaration_source or '-'} "
                f"queued={int(decision.queued)} legacy_done={int(decision.legacy_done)} "
                f"identity=[{identity}] params=[{params}]"
            )
            if decision.consumes or decision.produces:
                consumes = ", ".join(decision.consumes) or "-"
                produces = ", ".join(decision.produces) or "-"
                lines.append(f"    consumes=[{consumes}] produces=[{produces}]")
            if decision.worker_payload:
                lines.append(f"    worker_payload={json.dumps(decision.worker_payload, sort_keys=True)}")
            if decision.branch_artifact_manifest is not None:
                manifest_status = (
                    decision.branch_artifact_manifest.get("status", "unknown")
                    if isinstance(decision.branch_artifact_manifest, Mapping)
                    else "malformed"
                )
                lines.append(f"    branch_artifact_manifest={manifest_status}")
            if decision.decision_input_manifest is not None:
                manifest_status = (
                    decision.decision_input_manifest.get("status", "unknown")
                    if isinstance(decision.decision_input_manifest, Mapping)
                    else "malformed"
                )
                lines.append(f"    decision_input_manifest={manifest_status}")
    return "\n".join(lines)


def _decision_from_row(
    row: StoredReuseDecisionRowV1,
    registry: Mapping[tuple[str, str], Mapping[str, Any]],
) -> ReuseDecisionSummary:
    return ReuseDecisionSummary(
        legacy_variant_str=str(row.legacy_variant_str or ""),
        **_decision_common_fields_from_row(
            row,
            registry,
            reasons=tuple(
                str(item)
                for item in _loads_json(row.reasons_json, default=[])
            ),
            instance_payload_raw=_loads_json(
                row.instance_json,
                default={},
            ),
        ),
    )


def _current_decision_summary_v2_from_row(
    row: CurrentStoredReuseDecisionRowV2,
    registry: Mapping[tuple[str, str], Mapping[str, Any]],
) -> CurrentReuseDecisionSummaryV2:
    fields = _decision_common_fields_from_row(
        row,
        registry,
        reasons=row.reasons,
        instance_payload_raw=row.instance_payload,
    )
    return CurrentReuseDecisionSummaryV2(
        task_coordinate_id=row.task_coordinate_id,
        coordinate_status=row.coordinate_status,
        **fields,
    )


def _decision_common_fields_from_row(
    row: StoredReuseDecisionRowV1 | CurrentStoredReuseDecisionRowV2,
    registry: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    reasons: Tuple[str, ...],
    instance_payload_raw: Any,
) -> dict[str, Any]:
    key = (str(row.stage), str(row.task_base))
    declaration = registry.get(key, {})
    instance_payload = (
        instance_payload_raw
        if isinstance(instance_payload_raw, Mapping)
        else {}
    )
    identity = tuple(
        (str(item.get("key", "")), item.get("value"))
        for item in instance_payload.get("identity", [])
        if isinstance(item, dict)
    )
    params = tuple(
        (str(item.get("key", "")), item.get("value"))
        for item in instance_payload.get("params", [])
        if isinstance(item, dict)
    )
    payload_consumes = _optional_string_tuple(instance_payload.get("consumes"))
    payload_produces = _optional_string_tuple(instance_payload.get("produces"))
    consumes = (
        payload_consumes
        if payload_consumes is not None
        else _string_tuple(declaration.get("consumes", ()))
    )
    produces = (
        payload_produces
        if payload_produces is not None
        else _string_tuple(declaration.get("produces", ()))
    )
    worker_payload = _worker_payload_from_instance(instance_payload)
    branch_artifact_manifest = _branch_artifact_manifest_from_instance(
        instance_payload
    )
    decision_input_manifest = _decision_input_manifest_from_instance(
        instance_payload
    )
    inline_input_observation_manifest = (
        _inline_input_observation_manifest_from_instance(instance_payload)
    )
    return {
        "decision_id": int(row.decision_id),
        "stage": str(row.stage),
        "task_base": str(row.task_base),
        "action": str(row.action),
        "legacy_done": bool(row.legacy_done),
        "queued": bool(row.queued),
        "force": bool(row.force),
        "identity_hash": str(row.identity_hash),
        "params_hash": str(row.params_hash),
        "instance_id": str(row.instance_id),
        "reasons": reasons,
        "identity": identity,
        "params": params,
        "execution_mode": str(declaration.get("execution_mode") or ""),
        "worker_arg": _optional_string(declaration.get("worker_arg")),
        "worker_callable": _optional_string(row.worker_callable),
        "consumes": consumes,
        "produces": produces,
        "worker_payload": worker_payload,
        "created_at": str(row.created_at),
        "reuse_policy_version": _optional_string(
            instance_payload.get("reuse_policy_version")
        ),
        "implementation_version": _optional_string(
            instance_payload.get("implementation_version")
        ),
        "declaration_source": _declaration_source(
            payload_consumes=payload_consumes,
            payload_produces=payload_produces,
            declaration=declaration,
        ),
        "branch_artifact_manifest": branch_artifact_manifest,
        "decision_input_manifest": decision_input_manifest,
        "inline_input_observation_manifest": (
            inline_input_observation_manifest
        ),
    }


def _coordinate_status(
    *,
    stage: str,
    task_base: str,
    identity: Iterable[tuple[str, Any]],
    params: Iterable[tuple[str, Any]],
    compatibility_alias: str,
) -> tuple[str, CurrentDecisionCoordinateStatus]:
    try:
        coordinate = build_task_coordinate_v1(
            stage=stage,
            task_base=task_base,
            identity=identity,
            params=params,
        )
    except (TaskCoordinateError, TypeError, ValueError):
        return "", "malformed"
    status: CurrentDecisionCoordinateStatus = (
        "available"
        if coordinate.legacy_variant_str == compatibility_alias
        else "compatibility_mismatch"
    )
    return coordinate.task_coordinate_id, status


def _empty_report(db_path: Path) -> ReuseV2Report:
    return ReuseV2Report(
        db_path=str(db_path),
        has_decision_table=False,
        decision_count=0,
        action_counts={},
        task_counts={},
        execution_mode_counts={},
        worker_arg_counts={},
        worker_callable_counts={},
        reason_counts={},
        latest_decisions=(),
    )


def _load_task_registry(con: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    registry = _current_task_registry()
    if not _table_exists(con, "task_spec_registry"):
        return registry

    for row in con.execute(
        """
        SELECT stage, task_base, execution_mode, worker_arg, consumes_json, produces_json
        FROM task_spec_registry
        """
    ).fetchall():
        stage, task_base, execution_mode, worker_arg, consumes_json, produces_json = row
        registry[(str(stage), str(task_base))] = {
            "execution_mode": str(execution_mode or ""),
            "worker_arg": _optional_string(worker_arg),
            "consumes": _string_tuple(_loads_json(consumes_json, default=[])),
            "produces": _string_tuple(_loads_json(produces_json, default=[])),
            "declaration_source": "stored_task_spec_registry",
        }
    return registry


def _load_task_registry_read_only(
    db_path: Path,
) -> dict[tuple[str, str], dict[str, Any]]:
    with sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro",
        uri=True,
    ) as con:
        con.execute("PRAGMA query_only=ON")
        return _load_task_registry(con)


def _current_task_registry() -> dict[tuple[str, str], dict[str, Any]]:
    try:
        from .task_specs import task_specs_by_key
    except Exception:
        return {}

    registry = {}
    for key, spec in task_specs_by_key().items():
        registry[key] = {
            "execution_mode": spec.execution_mode,
            "worker_arg": spec.worker_arg,
            "consumes": tuple(spec.consumes),
            "produces": tuple(spec.produces),
            "declaration_source": "current_task_specs",
        }
    return registry


def _declaration_counts(
    task_count_rows: Iterable[tuple[Any, Any, Any]],
    registry: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    execution_modes: Counter[str] = Counter()
    worker_args: Counter[str] = Counter()
    for stage, task_base, count in task_count_rows:
        declaration = registry.get((str(stage), str(task_base)), {})
        n = int(count)
        execution_mode = str(declaration.get("execution_mode") or "unknown")
        worker_arg = str(declaration.get("worker_arg") or "none")
        execution_modes[execution_mode] += n
        worker_args[worker_arg] += n
    return dict(sorted(execution_modes.items())), dict(sorted(worker_args.items()))


def _worker_payload_from_instance(instance_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    source_contracts = instance_payload.get("source_contracts")
    if not isinstance(source_contracts, Mapping):
        return {}
    worker_payload = source_contracts.get("worker_payload")
    if not isinstance(worker_payload, Mapping):
        return {}
    return dict(worker_payload)


def _branch_artifact_manifest_from_instance(instance_payload: Mapping[str, Any]) -> Any | None:
    source_contracts = instance_payload.get("source_contracts")
    if not isinstance(source_contracts, Mapping):
        return None
    if "branch_artifact_manifest_v1" not in source_contracts:
        return None
    manifest = source_contracts["branch_artifact_manifest_v1"]
    return dict(manifest) if isinstance(manifest, Mapping) else manifest


def _decision_input_manifest_from_instance(instance_payload: Mapping[str, Any]) -> Any | None:
    source_contracts = instance_payload.get("source_contracts")
    if not isinstance(source_contracts, Mapping):
        return None
    if "decision_input_manifest_v1" not in source_contracts:
        return None
    manifest = source_contracts["decision_input_manifest_v1"]
    return dict(manifest) if isinstance(manifest, Mapping) else manifest


def _inline_input_observation_manifest_from_instance(
    instance_payload: Mapping[str, Any],
) -> Any | None:
    source_contracts = instance_payload.get("source_contracts")
    if not isinstance(source_contracts, Mapping):
        return None
    key = "inline_input_observation_manifest_v1"
    if key not in source_contracts:
        return None
    manifest = source_contracts[key]
    return dict(manifest) if isinstance(manifest, Mapping) else manifest


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _string_tuple(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _optional_string_tuple(value: Any) -> Tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    return tuple(str(item) for item in value)


def _declaration_source(
    *,
    payload_consumes: Tuple[str, ...] | None,
    payload_produces: Tuple[str, ...] | None,
    declaration: Mapping[str, Any],
) -> str:
    fallback_source = str(declaration.get("declaration_source") or "missing_declaration")
    consumes_source = "instance_payload" if payload_consumes is not None else fallback_source
    produces_source = "instance_payload" if payload_produces is not None else fallback_source
    if consumes_source == produces_source:
        return consumes_source
    return f"{consumes_source}+{produces_source}"


def _loads_json(value: Any, *, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _resolve_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path).expanduser()
    if path.is_dir():
        if path.name == "state":
            return path / "state.sqlite"
        candidate = path / "state" / "state.sqlite"
        if candidate.exists():
            return candidate
        return path / "state.sqlite"
    return path


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None
