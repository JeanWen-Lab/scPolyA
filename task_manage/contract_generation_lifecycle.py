"""Read-only active/superseded lifecycle derived from Reuse v2 decisions."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Tuple

from .pipeline_contracts import TaskKey
from .reuse_decision_storage import (
    ReuseDecisionStorageError,
    StoredReuseDecisionRowV1,
    archived_reuse_decision_storage_available_v2,
    current_reuse_decision_storage_available_v1,
    load_archived_reuse_decision_rows_v2,
    load_current_reuse_decision_rows_v1_compatibility,
)
from .task_instance_v2 import stable_task_json_hash


ContractDecisionLoadStatus = Literal["available", "malformed"]
ContractGenerationStatus = Literal["active", "superseded", "unresolved"]

CONTRACT_GENERATION_LIFECYCLE_POLICY_NAME = "active_contract_generations_v1"

_DIAGNOSTIC_ONLY = "diagnostic_only"
@dataclass(frozen=True)
class PersistedContractDecisionV1:
    decision_id: int
    task_key: TaskKey
    instance_id: str
    legacy_variant_str: str
    identity_hash: str
    params_hash: str
    symbolic_inputs_hash: str
    symbolic_outputs_hash: str
    contract_hash: str
    reuse_policy_version: str
    implementation_version: str
    identity_payload: Tuple[Mapping[str, Any], ...]
    params_payload: Tuple[Mapping[str, Any], ...]
    consumes: Tuple[str, ...]
    produces: Tuple[str, ...]
    created_at: str
    logical_slot_id: str | None
    coarse_slot_id: str | None
    generation_id: str | None
    load_status: ContractDecisionLoadStatus
    load_detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "task_key": {
                "stage": self.task_key[0],
                "task_base": self.task_key[1],
            },
            "instance_id": self.instance_id,
            "legacy_variant_str": self.legacy_variant_str,
            "identity_hash": self.identity_hash,
            "params_hash": self.params_hash,
            "symbolic_inputs_hash": self.symbolic_inputs_hash,
            "symbolic_outputs_hash": self.symbolic_outputs_hash,
            "contract_hash": self.contract_hash,
            "reuse_policy_version": self.reuse_policy_version,
            "implementation_version": self.implementation_version,
            "identity": [dict(item) for item in self.identity_payload],
            "params": [dict(item) for item in self.params_payload],
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "created_at": self.created_at,
            "logical_slot_id": self.logical_slot_id,
            "coarse_slot_id": self.coarse_slot_id,
            "generation_id": self.generation_id,
            "load_status": self.load_status,
            "load_detail": self.load_detail,
        }


@dataclass(frozen=True)
class ContractDecisionHistoryLoad:
    state_db_path: str
    table_available: bool
    error: str | None
    decisions: Tuple[PersistedContractDecisionV1, ...]

    @property
    def decision_count(self) -> int:
        return len(self.decisions)

    @property
    def load_status_counts(self) -> dict[str, int]:
        return _counts(row.load_status for row in self.decisions)

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        decisions = self.decisions
        if limit is not None:
            decisions = decisions[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "table_available": self.table_available,
            "error": self.error,
            "decision_count": self.decision_count,
            "load_status_counts": self.load_status_counts,
            "decisions": [row.as_dict() for row in decisions],
        }


@dataclass(frozen=True)
class ContractGenerationV1:
    logical_slot_id: str
    coarse_slot_id: str
    generation_id: str
    task_key: TaskKey
    legacy_variant_str: str
    identity_hash: str
    params_hash: str
    identity_payload: Tuple[Mapping[str, Any], ...]
    params_payload: Tuple[Mapping[str, Any], ...]
    instance_id: str
    contract_hash: str
    symbolic_inputs_hash: str
    symbolic_outputs_hash: str
    reuse_policy_version: str
    implementation_version: str
    consumes: Tuple[str, ...]
    produces: Tuple[str, ...]
    decision_ids: Tuple[int, ...]
    first_decision_id: int
    latest_decision_id: int
    status: ContractGenerationStatus
    superseded_by_generation_id: str | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "logical_slot_id": self.logical_slot_id,
            "coarse_slot_id": self.coarse_slot_id,
            "generation_id": self.generation_id,
            "task_key": {
                "stage": self.task_key[0],
                "task_base": self.task_key[1],
            },
            "legacy_variant_str": self.legacy_variant_str,
            "identity_hash": self.identity_hash,
            "params_hash": self.params_hash,
            "identity": [dict(item) for item in self.identity_payload],
            "params": [dict(item) for item in self.params_payload],
            "instance_id": self.instance_id,
            "contract_hash": self.contract_hash,
            "symbolic_inputs_hash": self.symbolic_inputs_hash,
            "symbolic_outputs_hash": self.symbolic_outputs_hash,
            "reuse_policy_version": self.reuse_policy_version,
            "implementation_version": self.implementation_version,
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "decision_ids": list(self.decision_ids),
            "first_decision_id": self.first_decision_id,
            "latest_decision_id": self.latest_decision_id,
            "status": self.status,
            "superseded_by_generation_id": self.superseded_by_generation_id,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ContractGenerationLifecycleReport:
    state_db_path: str
    table_available: bool
    error: str | None
    source_decision_count: int
    decision_load_status_counts: Mapping[str, int]
    logical_slot_count: int
    unresolved_logical_slot_count: int
    unresolved_decision_ids: Tuple[int, ...]
    generations: Tuple[ContractGenerationV1, ...]
    policy_name: str = CONTRACT_GENERATION_LIFECYCLE_POLICY_NAME
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    @property
    def generation_count(self) -> int:
        return len(self.generations)

    @property
    def generation_status_counts(self) -> dict[str, int]:
        return _counts(generation.status for generation in self.generations)

    @property
    def active_generation_count(self) -> int:
        return int(self.generation_status_counts.get("active", 0))

    @property
    def superseded_generation_count(self) -> int:
        return int(self.generation_status_counts.get("superseded", 0))

    @property
    def superseded_decision_ids(self) -> Tuple[int, ...]:
        return tuple(
            decision_id
            for generation in self.generations
            if generation.status == "superseded"
            for decision_id in generation.decision_ids
        )

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        generations = self.generations
        if limit is not None:
            generations = generations[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "table_available": self.table_available,
            "error": self.error,
            "policy_name": self.policy_name,
            "strict_effect": self.strict_effect,
            "source_decision_count": self.source_decision_count,
            "decision_load_status_counts": dict(
                self.decision_load_status_counts
            ),
            "logical_slot_count": self.logical_slot_count,
            "unresolved_logical_slot_count": (
                self.unresolved_logical_slot_count
            ),
            "unresolved_decision_ids": list(self.unresolved_decision_ids),
            "generation_count": self.generation_count,
            "generation_status_counts": self.generation_status_counts,
            "active_generation_count": self.active_generation_count,
            "superseded_generation_count": (
                self.superseded_generation_count
            ),
            "superseded_decision_ids": list(
                self.superseded_decision_ids
            ),
            "generations": [
                generation.as_dict() for generation in generations
            ],
        }


def load_contract_decision_history(
    state_or_db_path: str | Path,
    *,
    archived_v2: bool = False,
) -> ContractDecisionHistoryLoad:
    """Load persisted decision generations without creating SQLite state."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.exists():
        return ContractDecisionHistoryLoad(
            state_db_path=str(db_path),
            table_available=False,
            error=f"state database does not exist: {db_path}",
            decisions=(),
        )
    table_available = (
        archived_reuse_decision_storage_available_v2(db_path)
        if archived_v2
        else current_reuse_decision_storage_available_v1(db_path)
    )
    if not table_available:
        detail = (
            "archived reuse-decision v2 table does not exist"
            if archived_v2
            else "current reuse-decision storage policy does not exist"
        )
        return ContractDecisionHistoryLoad(
            state_db_path=str(db_path),
            table_available=False,
            error=detail,
            decisions=(),
        )
    try:
        rows = (
            load_archived_reuse_decision_rows_v2(
                db_path,
                latest_first=False,
            )
            if archived_v2
            else load_current_reuse_decision_rows_v1_compatibility(
                db_path,
                latest_first=False,
            )
        )
    except (OSError, ReuseDecisionStorageError) as exc:
        return ContractDecisionHistoryLoad(
            state_db_path=str(db_path),
            table_available=table_available,
            error=f"{type(exc).__name__}: {exc}",
            decisions=(),
        )
    return ContractDecisionHistoryLoad(
        state_db_path=str(db_path),
        table_available=True,
        error=None,
        decisions=tuple(_decision_from_row(row) for row in rows),
    )


def build_contract_generation_lifecycle_report(
    state_or_db_path: str | Path,
    *,
    archived_v2: bool = False,
) -> ContractGenerationLifecycleReport:
    history = load_contract_decision_history(
        state_or_db_path,
        archived_v2=archived_v2,
    )
    return build_contract_generation_lifecycle_report_from_history(history)


def build_contract_generation_lifecycle_report_from_history(
    history: ContractDecisionHistoryLoad,
) -> ContractGenerationLifecycleReport:
    """Fold exact persisted rows by logical slot and contract generation."""

    rows = history.decisions
    valid_rows = tuple(row for row in rows if row.load_status == "available")
    malformed_by_coarse: dict[str, list[PersistedContractDecisionV1]] = (
        defaultdict(list)
    )
    for row in rows:
        if row.load_status == "malformed" and row.coarse_slot_id is not None:
            malformed_by_coarse[row.coarse_slot_id].append(row)

    valid_by_coarse: dict[str, list[PersistedContractDecisionV1]] = defaultdict(
        list
    )
    for row in valid_rows:
        if row.coarse_slot_id is None or row.logical_slot_id is None:
            continue
        valid_by_coarse[row.coarse_slot_id].append(row)

    unresolved_coarse_slots = {
        coarse_slot_id
        for coarse_slot_id, coarse_rows in valid_by_coarse.items()
        if (
            coarse_slot_id in malformed_by_coarse
            or len(
                {
                    row.logical_slot_id
                    for row in coarse_rows
                    if row.logical_slot_id is not None
                }
            )
            != 1
        )
    }

    valid_by_logical: dict[str, list[PersistedContractDecisionV1]] = defaultdict(
        list
    )
    for row in valid_rows:
        if row.logical_slot_id is not None:
            valid_by_logical[row.logical_slot_id].append(row)

    generations: list[ContractGenerationV1] = []
    for logical_slot_id, slot_rows in valid_by_logical.items():
        coarse_slot_id = slot_rows[0].coarse_slot_id
        if coarse_slot_id is None:
            continue
        rows_by_generation: dict[
            str,
            list[PersistedContractDecisionV1],
        ] = defaultdict(list)
        for row in slot_rows:
            if row.generation_id is not None:
                rows_by_generation[row.generation_id].append(row)
        active_generation_id = max(
            rows_by_generation,
            key=lambda generation_id: max(
                row.decision_id
                for row in rows_by_generation[generation_id]
            ),
        )
        slot_unresolved = coarse_slot_id in unresolved_coarse_slots
        for generation_id, generation_rows in rows_by_generation.items():
            ordered_rows = tuple(
                sorted(generation_rows, key=lambda row: row.decision_id)
            )
            representative = ordered_rows[-1]
            if slot_unresolved:
                status: ContractGenerationStatus = "unresolved"
                superseded_by = None
                detail = (
                    "logical slot contains malformed or conflicting persisted "
                    "identity/parameter evidence"
                )
            elif generation_id == active_generation_id:
                status = "active"
                superseded_by = None
                detail = (
                    "generation owns the greatest persisted decision_id in "
                    "its logical slot"
                )
            else:
                status = "superseded"
                superseded_by = active_generation_id
                detail = (
                    "a distinct generation has a greater persisted "
                    "decision_id in this logical slot"
                )
            generations.append(
                ContractGenerationV1(
                    logical_slot_id=logical_slot_id,
                    coarse_slot_id=coarse_slot_id,
                    generation_id=generation_id,
                    task_key=representative.task_key,
                    legacy_variant_str=(
                        representative.legacy_variant_str
                    ),
                    identity_hash=representative.identity_hash,
                    params_hash=representative.params_hash,
                    identity_payload=representative.identity_payload,
                    params_payload=representative.params_payload,
                    instance_id=representative.instance_id,
                    contract_hash=representative.contract_hash,
                    symbolic_inputs_hash=(
                        representative.symbolic_inputs_hash
                    ),
                    symbolic_outputs_hash=(
                        representative.symbolic_outputs_hash
                    ),
                    reuse_policy_version=(
                        representative.reuse_policy_version
                    ),
                    implementation_version=(
                        representative.implementation_version
                    ),
                    consumes=representative.consumes,
                    produces=representative.produces,
                    decision_ids=tuple(
                        row.decision_id for row in ordered_rows
                    ),
                    first_decision_id=ordered_rows[0].decision_id,
                    latest_decision_id=ordered_rows[-1].decision_id,
                    status=status,
                    superseded_by_generation_id=superseded_by,
                    detail=detail,
                )
            )

    unresolved_decision_ids = tuple(
        sorted(
            {
                row.decision_id
                for row in rows
                if row.load_status == "malformed"
            }
            | {
                row.decision_id
                for row in valid_rows
                if row.coarse_slot_id in unresolved_coarse_slots
            }
        )
    )
    ordered_generations = tuple(
        sorted(
            generations,
            key=lambda generation: (
                generation.task_key,
                generation.logical_slot_id,
                generation.latest_decision_id,
                generation.generation_id,
            ),
        )
    )
    return ContractGenerationLifecycleReport(
        state_db_path=history.state_db_path,
        table_available=history.table_available,
        error=history.error,
        source_decision_count=history.decision_count,
        decision_load_status_counts=history.load_status_counts,
        logical_slot_count=len(valid_by_logical),
        unresolved_logical_slot_count=len(
            {
                generation.logical_slot_id
                for generation in ordered_generations
                if generation.status == "unresolved"
            }
        ),
        unresolved_decision_ids=unresolved_decision_ids,
        generations=ordered_generations,
    )


def format_contract_generation_lifecycle_report(
    report: ContractGenerationLifecycleReport,
    *,
    limit: int = 20,
) -> str:
    lines = [
        f"Reuse v2 contract generations: {report.state_db_path}",
        f"decision table: {'yes' if report.table_available else 'no'}",
        f"strict effect: {report.strict_effect}",
        f"source decisions: {report.source_decision_count}",
        f"logical slots: {report.logical_slot_count}",
        f"generations: {report.generation_count}",
        f"superseded generations: {report.superseded_generation_count}",
        f"unresolved logical slots: {report.unresolved_logical_slot_count}",
    ]
    if report.error:
        lines.append(f"error: {report.error}")
    if report.decision_load_status_counts:
        lines.append("decision load statuses:")
        for status, count in report.decision_load_status_counts.items():
            lines.append(f"  {status}: {count}")
    if report.generation_status_counts:
        lines.append("generation statuses:")
        for status, count in report.generation_status_counts.items():
            lines.append(f"  {status}: {count}")
    generations = report.generations[: max(0, int(limit))]
    if generations:
        lines.append("generations:")
    for generation in generations:
        lines.append(
            f"  {generation.task_key[0]}:{generation.task_key[1]} "
            f"{generation.status} decisions="
            f"{generation.first_decision_id}-"
            f"{generation.latest_decision_id} "
            f"instance={generation.instance_id[:12]} "
            f"contract={generation.contract_hash[:12]}"
        )
    return "\n".join(lines)


def _decision_from_row(
    row: StoredReuseDecisionRowV1,
) -> PersistedContractDecisionV1:
    decision_id_raw = row.decision_id
    stage_raw = row.stage
    task_base_raw = row.task_base
    instance_id_raw = row.instance_id
    legacy_variant_str_raw = row.legacy_variant_str
    identity_hash_raw = row.identity_hash
    params_hash_raw = row.params_hash
    symbolic_inputs_hash_raw = row.symbolic_inputs_hash
    symbolic_outputs_hash_raw = row.symbolic_outputs_hash
    contract_hash_raw = row.contract_hash
    instance_json = row.instance_json
    created_at_raw = row.created_at
    decision_id = _positive_int(decision_id_raw) or 0
    stage = str(stage_raw or "")
    task_base = str(task_base_raw or "")
    instance_id = str(instance_id_raw or "")
    legacy_variant_str = str(legacy_variant_str_raw or "")
    identity_hash = str(identity_hash_raw or "")
    params_hash = str(params_hash_raw or "")
    symbolic_inputs_hash = str(symbolic_inputs_hash_raw or "")
    symbolic_outputs_hash = str(symbolic_outputs_hash_raw or "")
    contract_hash = str(contract_hash_raw or "")
    created_at = str(created_at_raw or "")

    payload_raw = _loads_json(instance_json)
    payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    identity_payload = _field_payload(payload.get("identity"))
    params_payload = _field_payload(payload.get("params"))
    consumes = _string_tuple(payload.get("consumes"))
    produces = _string_tuple(payload.get("produces"))
    reuse_policy_version = str(payload.get("reuse_policy_version") or "")
    implementation_version = str(
        payload.get("implementation_version") or ""
    )

    errors: list[str] = []
    if not decision_id:
        errors.append("invalid decision ID")
    for label, value in (
        ("stage", stage),
        ("task base", task_base),
        ("instance ID", instance_id),
        ("identity hash", identity_hash),
        ("params hash", params_hash),
        ("symbolic inputs hash", symbolic_inputs_hash),
        ("symbolic outputs hash", symbolic_outputs_hash),
        ("contract hash", contract_hash),
    ):
        if not value:
            errors.append(f"blank {label}")
    if not payload:
        errors.append("malformed instance payload")
    if identity_payload is None:
        errors.append("malformed identity payload")
        identity_payload = ()
    if params_payload is None:
        errors.append("malformed params payload")
        params_payload = ()
    if consumes is None:
        errors.append("malformed consumes payload")
        consumes = ()
    if produces is None:
        errors.append("malformed produces payload")
        produces = ()
    if not reuse_policy_version:
        errors.append("blank reuse policy version")
    if not implementation_version:
        errors.append("blank implementation version")

    exact_fields = [
        ("stage", stage),
        ("task_base", task_base),
        ("instance_id", instance_id),
        ("identity_hash", identity_hash),
        ("params_hash", params_hash),
        ("symbolic_inputs_hash", symbolic_inputs_hash),
        ("symbolic_outputs_hash", symbolic_outputs_hash),
        ("contract_hash", contract_hash),
    ]
    if row.storage_version == "v2":
        exact_fields.append(("legacy_variant_str", legacy_variant_str))
    for key, expected in exact_fields:
        if payload.get(key) != expected:
            errors.append(f"column/payload mismatch for {key}")

    coarse_slot_id = None
    if stage and task_base and identity_hash and params_hash:
        coarse_slot_id = stable_task_json_hash(
            {
                "stage": stage,
                "task_base": task_base,
                "legacy_variant_str": legacy_variant_str,
                "identity_hash": identity_hash,
                "params_hash": params_hash,
            }
        )

    logical_slot_id = None
    generation_id = None
    if not errors and coarse_slot_id is not None:
        logical_slot_id = stable_task_json_hash(
            {
                "coarse_slot_id": coarse_slot_id,
                "identity": identity_payload,
                "params": params_payload,
            }
        )
        generation_id = stable_task_json_hash(
            {
                "logical_slot_id": logical_slot_id,
                "instance_id": instance_id,
                "contract_hash": contract_hash,
                "symbolic_inputs_hash": symbolic_inputs_hash,
                "symbolic_outputs_hash": symbolic_outputs_hash,
                "reuse_policy_version": reuse_policy_version,
                "implementation_version": implementation_version,
                "consumes": consumes,
                "produces": produces,
            }
        )

    return PersistedContractDecisionV1(
        decision_id=decision_id,
        task_key=(stage, task_base),
        instance_id=instance_id,
        legacy_variant_str=legacy_variant_str,
        identity_hash=identity_hash,
        params_hash=params_hash,
        symbolic_inputs_hash=symbolic_inputs_hash,
        symbolic_outputs_hash=symbolic_outputs_hash,
        contract_hash=contract_hash,
        reuse_policy_version=reuse_policy_version,
        implementation_version=implementation_version,
        identity_payload=identity_payload,
        params_payload=params_payload,
        consumes=consumes,
        produces=produces,
        created_at=created_at,
        logical_slot_id=logical_slot_id,
        coarse_slot_id=coarse_slot_id,
        generation_id=generation_id,
        load_status="malformed" if errors else "available",
        load_detail="; ".join(errors) if errors else "validated persisted row",
    )


def _field_payload(
    value: Any,
) -> Tuple[Mapping[str, Any], ...] | None:
    if not isinstance(value, (tuple, list)):
        return None
    normalized: list[Mapping[str, Any]] = []
    keys: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        key = item.get("key")
        if not isinstance(key, str) or not key:
            return None
        if "value" not in item or "legacy_value" not in item:
            return None
        if not isinstance(item.get("legacy_value"), str):
            return None
        keys.append(key)
        normalized.append(
            {
                "key": key,
                "value": item.get("value"),
                "legacy_value": item.get("legacy_value"),
            }
        )
    if len(set(keys)) != len(keys):
        return None
    return tuple(normalized)


def _string_tuple(value: Any) -> Tuple[str, ...] | None:
    if not isinstance(value, (tuple, list)):
        return None
    values = tuple(str(item) for item in value)
    if any(not item for item in values) or len(set(values)) != len(values):
        return None
    return values


def _loads_json(value: Any) -> Any:
    try:
        return json.loads(str(value))
    except Exception:
        return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


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


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
