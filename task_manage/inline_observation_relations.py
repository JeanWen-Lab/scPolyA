"""Read-only relations from inline completion observations to decisions."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Tuple

from .inline_execution_observations import (
    INLINE_EXECUTION_OBSERVATION_V1_SCHEMA,
    INLINE_INPUT_OBSERVATION_MANIFEST_V1_SCHEMA,
    INLINE_OBSERVED_PRODUCER_BY_ARTIFACT,
)
from .pipeline_contracts import TaskKey
from .reuse_report import (
    CurrentReuseDecisionSummaryV2,
    load_current_reuse_decision_summaries_v2,
)
from .task_instance_v2 import stable_task_json_hash


InlineObservationLoadStatus = Literal["available", "malformed"]
InlineObservationRelationStatus = Literal[
    "matched",
    "manifest_unavailable",
    "manifest_incomplete",
    "observation_unavailable",
    "observation_mismatch",
    "ambiguous",
]

_DIAGNOSTIC_ONLY = "diagnostic_only"
_TABLE_NAME = "inline_execution_observations_v1"


@dataclass(frozen=True)
class PersistedInlineExecutionObservationV1:
    observation_id: int
    schema_version: str
    status: str
    invocation_id: str
    observation_token: str
    producer_task_key: TaskKey
    producer_instance_id: str
    contract_hash: str
    reuse_policy_version: str
    implementation_version: str
    produced_artifacts: Tuple[str, ...]
    worker_callable: str
    instance_payload: Mapping[str, Any]
    completed_at: str
    load_status: InlineObservationLoadStatus
    load_detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "schema_version": self.schema_version,
            "status": self.status,
            "invocation_id": self.invocation_id,
            "observation_token": self.observation_token,
            "producer_task_key": {
                "stage": self.producer_task_key[0],
                "task_base": self.producer_task_key[1],
            },
            "producer_instance_id": self.producer_instance_id,
            "contract_hash": self.contract_hash,
            "reuse_policy_version": self.reuse_policy_version,
            "implementation_version": self.implementation_version,
            "produced_artifacts": list(self.produced_artifacts),
            "worker_callable": self.worker_callable,
            "instance_payload": dict(self.instance_payload),
            "completed_at": self.completed_at,
            "load_status": self.load_status,
            "load_detail": self.load_detail,
        }


@dataclass(frozen=True)
class InlineExecutionObservationLoad:
    state_db_path: str
    table_available: bool
    error: str | None
    observations: Tuple[PersistedInlineExecutionObservationV1, ...]

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def load_status_counts(self) -> dict[str, int]:
        return _counts(
            observation.load_status for observation in self.observations
        )

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        observations = self.observations
        if limit is not None:
            observations = observations[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "table_available": self.table_available,
            "error": self.error,
            "observation_count": self.observation_count,
            "load_status_counts": self.load_status_counts,
            "observations": [
                observation.as_dict() for observation in observations
            ],
        }


@dataclass(frozen=True)
class InlineObservationRelation:
    consumer_instance_id: str
    consumer_decision_id: int
    consumer_task_key: TaskKey
    artifact_key: str
    relation_status: InlineObservationRelationStatus
    manifest_status: str | None
    observation_id: int | None
    observation_token: str | None
    invocation_id: str | None
    producer_instance_id: str | None
    producer_task_key: TaskKey | None
    detail: str
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "consumer_instance_id": self.consumer_instance_id,
            "consumer_decision_id": self.consumer_decision_id,
            "consumer_task_key": {
                "stage": self.consumer_task_key[0],
                "task_base": self.consumer_task_key[1],
            },
            "artifact_key": self.artifact_key,
            "relation_status": self.relation_status,
            "manifest_status": self.manifest_status,
            "observation_id": self.observation_id,
            "observation_token": self.observation_token,
            "invocation_id": self.invocation_id,
            "producer_instance_id": self.producer_instance_id,
            "producer_task_key": (
                None
                if self.producer_task_key is None
                else {
                    "stage": self.producer_task_key[0],
                    "task_base": self.producer_task_key[1],
                }
            ),
            "detail": self.detail,
            "strict_effect": self.strict_effect,
        }


@dataclass(frozen=True)
class InlineObservationRelationReport:
    state_db_path: str
    source_decision_count: int
    latest_instance_count: int
    source_observation_count: int
    observation_table_available: bool
    observation_error: str | None
    observation_load_status_counts: Mapping[str, int]
    scoped_consumer_count: int
    relations: Tuple[InlineObservationRelation, ...]
    strict_effect: Literal["diagnostic_only"] = _DIAGNOSTIC_ONLY

    @property
    def relation_count(self) -> int:
        return len(self.relations)

    @property
    def relation_status_counts(self) -> dict[str, int]:
        return _counts(relation.relation_status for relation in self.relations)

    @property
    def matched_count(self) -> int:
        return int(self.relation_status_counts.get("matched", 0))

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        relations = self.relations
        if limit is not None:
            relations = relations[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "source_decision_count": self.source_decision_count,
            "latest_instance_count": self.latest_instance_count,
            "source_observation_count": self.source_observation_count,
            "observation_table_available": self.observation_table_available,
            "observation_error": self.observation_error,
            "observation_load_status_counts": dict(
                self.observation_load_status_counts
            ),
            "scoped_consumer_count": self.scoped_consumer_count,
            "strict_effect": self.strict_effect,
            "relation_count": self.relation_count,
            "matched_count": self.matched_count,
            "relation_status_counts": self.relation_status_counts,
            "relations": [relation.as_dict() for relation in relations],
        }


def load_inline_execution_observations_v1(
    state_or_db_path: str | Path,
) -> InlineExecutionObservationLoad:
    """Load completion evidence without creating or modifying SQLite state."""

    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.exists():
        return InlineExecutionObservationLoad(
            state_db_path=str(db_path),
            table_available=False,
            error=f"state database does not exist: {db_path}",
            observations=(),
        )
    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            if not _table_exists(con, _TABLE_NAME):
                return InlineExecutionObservationLoad(
                    state_db_path=str(db_path),
                    table_available=False,
                    error=f"table {_TABLE_NAME} does not exist",
                    observations=(),
                )
            rows = con.execute(
                """
                SELECT observation_id, schema_version, status, invocation_id,
                       observation_token, stage, task_base,
                       producer_instance_id, contract_hash,
                       reuse_policy_version, implementation_version,
                       produced_artifacts_json, worker_callable, instance_json,
                       completed_at
                FROM inline_execution_observations_v1
                ORDER BY observation_id
                """
            ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        return InlineExecutionObservationLoad(
            state_db_path=str(db_path),
            table_available=False,
            error=f"{type(exc).__name__}: {exc}",
            observations=(),
        )
    return InlineExecutionObservationLoad(
        state_db_path=str(db_path),
        table_available=True,
        error=None,
        observations=tuple(_observation_from_row(row) for row in rows),
    )


def build_inline_observation_relation_report(
    state_or_db_path: str | Path,
) -> InlineObservationRelationReport:
    decisions = load_current_reuse_decision_summaries_v2(
        state_or_db_path,
        limit=None,
    )
    observation_load = load_inline_execution_observations_v1(
        state_or_db_path
    )
    return build_inline_observation_relation_report_from_components(
        decisions,
        observation_load,
        state_db_path=str(state_or_db_path),
    )


def build_inline_observation_relation_report_from_components(
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
    observation_load: InlineExecutionObservationLoad,
    *,
    state_db_path: str = "",
) -> InlineObservationRelationReport:
    """Validate exact references on the latest consumer decision generation."""

    decision_rows = tuple(decisions)
    latest_by_instance_id: dict[str, CurrentReuseDecisionSummaryV2] = {}
    for decision in decision_rows:
        previous = latest_by_instance_id.get(decision.instance_id)
        if previous is None or decision.decision_id >= previous.decision_id:
            latest_by_instance_id[decision.instance_id] = decision
    scoped = tuple(
        (decision, artifact_key)
        for decision in latest_by_instance_id.values()
        for artifact_key in decision.consumes
        if artifact_key in INLINE_OBSERVED_PRODUCER_BY_ARTIFACT
    )
    observations_by_identity: dict[
        tuple[int, str],
        list[PersistedInlineExecutionObservationV1],
    ] = {}
    for observation in observation_load.observations:
        observations_by_identity.setdefault(
            (
                observation.observation_id,
                observation.observation_token,
            ),
            [],
        ).append(observation)
    relations = tuple(
        sorted(
            (
                _relation_for_consumer(
                    decision,
                    artifact_key,
                    observations_by_identity=observations_by_identity,
                )
                for decision, artifact_key in scoped
            ),
            key=lambda relation: (
                relation.consumer_decision_id,
                relation.consumer_instance_id,
                relation.artifact_key,
            ),
        )
    )
    return InlineObservationRelationReport(
        state_db_path=state_db_path or observation_load.state_db_path,
        source_decision_count=len(decision_rows),
        latest_instance_count=len(latest_by_instance_id),
        source_observation_count=observation_load.observation_count,
        observation_table_available=observation_load.table_available,
        observation_error=observation_load.error,
        observation_load_status_counts=observation_load.load_status_counts,
        scoped_consumer_count=len({decision.instance_id for decision, _ in scoped}),
        relations=relations,
    )


def format_inline_observation_relation_report(
    report: InlineObservationRelationReport,
    *,
    limit: int = 20,
) -> str:
    lines = [
        f"Inline observation relations: {report.state_db_path or '-'}",
        f"source decisions: {report.source_decision_count}",
        f"latest instances: {report.latest_instance_count}",
        f"source observations: {report.source_observation_count}",
        f"observation table: {int(report.observation_table_available)}",
        f"scoped consumers: {report.scoped_consumer_count}",
        f"relations: {report.relation_count}",
        f"strict effect: {report.strict_effect}",
        "statuses:",
    ]
    for status, count in report.relation_status_counts.items():
        lines.append(f"  {status}: {count}")
    if report.observation_error:
        lines.append(f"observation error: {report.observation_error}")
    relations = report.relations[: max(0, int(limit))]
    if relations:
        lines.append("consumers:")
    for relation in relations:
        consumer = (
            f"{relation.consumer_task_key[0]}:"
            f"{relation.consumer_task_key[1]}"
        )
        producer = (
            "-"
            if relation.producer_task_key is None
            else (
                f"{relation.producer_task_key[0]}:"
                f"{relation.producer_task_key[1]}"
            )
        )
        lines.append(
            f"  {relation.consumer_instance_id[:12]} {consumer} "
            f"artifact={relation.artifact_key} "
            f"status={relation.relation_status} producer={producer}"
        )
        lines.append(f"    detail={relation.detail}")
    return "\n".join(lines)


def _relation_for_consumer(
    consumer: CurrentReuseDecisionSummaryV2,
    artifact_key: str,
    *,
    observations_by_identity: Mapping[
        tuple[int, str],
        list[PersistedInlineExecutionObservationV1],
    ],
) -> InlineObservationRelation:
    manifest = consumer.inline_input_observation_manifest
    if not isinstance(manifest, Mapping):
        return _relation(
            consumer,
            artifact_key,
            relation_status="manifest_unavailable",
            detail="consumer decision has no structured inline observation manifest",
        )
    manifest_status = _optional_string(manifest.get("status"))
    if (
        manifest.get("schema_version")
        != INLINE_INPUT_OBSERVATION_MANIFEST_V1_SCHEMA
        or manifest.get("strict_effect") != _DIAGNOSTIC_ONLY
    ):
        return _relation(
            consumer,
            artifact_key,
            relation_status="manifest_unavailable",
            manifest_status=manifest_status,
            detail="consumer inline observation manifest schema/effect is invalid",
        )
    if manifest_status != "captured":
        return _relation(
            consumer,
            artifact_key,
            relation_status="manifest_incomplete",
            manifest_status=manifest_status,
            detail=(
                "consumer inline observation manifest did not capture every "
                "declared inline artifact"
            ),
        )

    generated_from = manifest.get("generated_from")
    declared_artifacts = _unique_string_tuple(
        manifest.get("declared_artifacts")
    )
    references = manifest.get("references")
    missing_artifacts = _unique_string_tuple(
        manifest.get("missing_artifacts")
    )
    ambiguous_artifacts = _unique_string_tuple(
        manifest.get("ambiguous_artifacts")
    )
    consumer_key = manifest.get("consumer_task_key")
    if (
        not isinstance(generated_from, Mapping)
        or generated_from.get("source")
        != "current_process_inline_observation_registry"
        or not _nonblank_string(generated_from.get("invocation_id"))
        or not _nonblank_string(generated_from.get("captured_at"))
        or declared_artifacts is None
        or artifact_key not in declared_artifacts
        or references is None
        or not isinstance(references, (tuple, list))
        or missing_artifacts != ()
        or ambiguous_artifacts != ()
        or manifest.get("consumer_instance_id") != consumer.instance_id
        or not isinstance(consumer_key, Mapping)
        or (
            str(consumer_key.get("stage") or ""),
            str(consumer_key.get("task_base") or ""),
        )
        != consumer.key
    ):
        return _relation(
            consumer,
            artifact_key,
            relation_status="manifest_incomplete",
            manifest_status=manifest_status,
            detail="consumer inline observation manifest fields are incomplete or mismatched",
        )
    if not _valid_manifest_hash(manifest):
        return _relation(
            consumer,
            artifact_key,
            relation_status="observation_mismatch",
            manifest_status=manifest_status,
            detail="consumer inline observation manifest hash does not match its payload",
        )

    artifact_references = tuple(
        reference
        for reference in references
        if isinstance(reference, Mapping)
        and reference.get("artifact_key") == artifact_key
    )
    if not artifact_references:
        return _relation(
            consumer,
            artifact_key,
            relation_status="manifest_incomplete",
            manifest_status=manifest_status,
            detail="consumer manifest has no reference for the declared artifact",
        )
    if len(artifact_references) != 1:
        return _relation(
            consumer,
            artifact_key,
            relation_status="ambiguous",
            manifest_status=manifest_status,
            detail="consumer manifest has multiple references for one artifact",
        )
    reference = artifact_references[0]
    reference_id = _positive_int(reference.get("observation_id"))
    reference_token = _nonblank_string(
        reference.get("observation_token")
    )
    if reference_id is None or reference_token is None:
        return _relation(
            consumer,
            artifact_key,
            relation_status="observation_mismatch",
            manifest_status=manifest_status,
            detail="consumer reference lacks an observation ID or token",
        )
    candidates = tuple(
        observations_by_identity.get(
            (reference_id, reference_token),
            (),
        )
    )
    if not candidates:
        return _relation(
            consumer,
            artifact_key,
            relation_status="observation_unavailable",
            manifest_status=manifest_status,
            observation_id=reference_id,
            observation_token=reference_token,
            detail="referenced inline completion row is unavailable",
        )
    if len(candidates) != 1:
        return _relation(
            consumer,
            artifact_key,
            relation_status="ambiguous",
            manifest_status=manifest_status,
            observation_id=reference_id,
            observation_token=reference_token,
            detail="multiple persisted rows match one observation reference",
        )
    observation = candidates[0]
    mismatch = _observation_mismatch_reason(
        consumer=consumer,
        artifact_key=artifact_key,
        manifest=manifest,
        reference=reference,
        observation=observation,
    )
    if mismatch is not None:
        return _relation(
            consumer,
            artifact_key,
            relation_status="observation_mismatch",
            manifest_status=manifest_status,
            observation=observation,
            detail=mismatch,
        )
    return _relation(
        consumer,
        artifact_key,
        relation_status="matched",
        manifest_status=manifest_status,
        observation=observation,
        detail=(
            "consumer decision references the exact current-process persisted "
            "inline completion observation; diagnostic only"
        ),
    )


def _observation_mismatch_reason(
    *,
    consumer: CurrentReuseDecisionSummaryV2,
    artifact_key: str,
    manifest: Mapping[str, Any],
    reference: Mapping[str, Any],
    observation: PersistedInlineExecutionObservationV1,
) -> str | None:
    expected_producer_key = INLINE_OBSERVED_PRODUCER_BY_ARTIFACT[
        artifact_key
    ]
    reference_task_key = _task_key(reference.get("producer_task_key"))
    generated_from = manifest["generated_from"]
    invocation_id = str(generated_from["invocation_id"])
    if (
        observation.load_status != "available"
        or observation.schema_version
        != INLINE_EXECUTION_OBSERVATION_V1_SCHEMA
        or observation.status != "completed"
        or observation.producer_task_key != expected_producer_key
        or reference_task_key != expected_producer_key
        or artifact_key not in observation.produced_artifacts
        or reference.get("artifact_key") != artifact_key
        or reference.get("invocation_id") != invocation_id
        or observation.invocation_id != invocation_id
        or reference.get("producer_instance_id")
        != observation.producer_instance_id
        or reference.get("contract_hash") != observation.contract_hash
        or reference.get("reuse_policy_version")
        != observation.reuse_policy_version
        or reference.get("implementation_version")
        != observation.implementation_version
        or reference.get("completed_at") != observation.completed_at
    ):
        return (
            "persisted observation and consumer reference disagree on "
            "schema, task, artifact, invocation, identity, or version"
        )
    payload = observation.instance_payload
    payload_task_key = (
        str(payload.get("stage") or ""),
        str(payload.get("task_base") or ""),
    )
    payload_produces = _unique_string_tuple(payload.get("produces"))
    if (
        payload_task_key != observation.producer_task_key
        or payload.get("instance_id") != observation.producer_instance_id
        or payload.get("contract_hash") != observation.contract_hash
        or payload.get("reuse_policy_version")
        != observation.reuse_policy_version
        or payload.get("implementation_version")
        != observation.implementation_version
        or payload_produces is None
        or artifact_key not in payload_produces
    ):
        return "persisted observation columns disagree with its task instance payload"
    if not _ordered_timestamps(
        observation.completed_at,
        str(generated_from["captured_at"]),
        consumer.created_at,
    ):
        return (
            "inline completion, manifest capture, and consumer decision "
            "timestamps are missing, malformed, or out of order"
        )
    return None


def _valid_manifest_hash(manifest: Mapping[str, Any]) -> bool:
    declared_artifacts = _unique_string_tuple(
        manifest.get("declared_artifacts")
    )
    missing_artifacts = _unique_string_tuple(
        manifest.get("missing_artifacts")
    )
    ambiguous_artifacts = _unique_string_tuple(
        manifest.get("ambiguous_artifacts")
    )
    references = manifest.get("references")
    generated_from = manifest.get("generated_from")
    if (
        declared_artifacts is None
        or missing_artifacts is None
        or ambiguous_artifacts is None
        or not isinstance(references, (tuple, list))
        or not isinstance(generated_from, Mapping)
    ):
        return False
    expected = stable_task_json_hash(
        {
            "schema_version": (
                INLINE_INPUT_OBSERVATION_MANIFEST_V1_SCHEMA
            ),
            "invocation_id": generated_from.get("invocation_id"),
            "consumer_instance_id": manifest.get("consumer_instance_id"),
            "declared_artifacts": declared_artifacts,
            "references": list(references),
            "missing_artifacts": sorted(missing_artifacts),
            "ambiguous_artifacts": sorted(ambiguous_artifacts),
        }
    )
    return manifest.get("manifest_hash") == expected


def _relation(
    consumer: CurrentReuseDecisionSummaryV2,
    artifact_key: str,
    *,
    relation_status: InlineObservationRelationStatus,
    manifest_status: str | None = None,
    observation: PersistedInlineExecutionObservationV1 | None = None,
    observation_id: int | None = None,
    observation_token: str | None = None,
    detail: str,
) -> InlineObservationRelation:
    return InlineObservationRelation(
        consumer_instance_id=consumer.instance_id,
        consumer_decision_id=consumer.decision_id,
        consumer_task_key=consumer.key,
        artifact_key=artifact_key,
        relation_status=relation_status,
        manifest_status=manifest_status,
        observation_id=(
            observation_id
            if observation is None
            else observation.observation_id
        ),
        observation_token=(
            observation_token
            if observation is None
            else observation.observation_token
        ),
        invocation_id=(
            None if observation is None else observation.invocation_id
        ),
        producer_instance_id=(
            None
            if observation is None
            else observation.producer_instance_id
        ),
        producer_task_key=(
            None if observation is None else observation.producer_task_key
        ),
        detail=detail,
    )


def _observation_from_row(
    row: tuple[Any, ...],
) -> PersistedInlineExecutionObservationV1:
    (
        observation_id,
        schema_version,
        status,
        invocation_id,
        observation_token,
        stage,
        task_base,
        producer_instance_id,
        contract_hash,
        reuse_policy_version,
        implementation_version,
        produced_artifacts_json,
        worker_callable,
        instance_json,
        completed_at,
    ) = row
    artifacts_raw = _loads_json(produced_artifacts_json)
    artifacts = _unique_string_tuple(artifacts_raw)
    instance_raw = _loads_json(instance_json)
    instance_payload = (
        dict(instance_raw) if isinstance(instance_raw, Mapping) else {}
    )
    errors: list[str] = []
    if schema_version != INLINE_EXECUTION_OBSERVATION_V1_SCHEMA:
        errors.append("unsupported schema")
    if status != "completed":
        errors.append("status is not completed")
    if not _nonblank_string(invocation_id):
        errors.append("blank invocation ID")
    if not _nonblank_string(observation_token):
        errors.append("blank observation token")
    if artifacts is None or not artifacts:
        errors.append("malformed produced artifacts")
        artifacts = ()
    if not instance_payload:
        errors.append("malformed instance payload")
    return PersistedInlineExecutionObservationV1(
        observation_id=int(observation_id),
        schema_version=str(schema_version or ""),
        status=str(status or ""),
        invocation_id=str(invocation_id or ""),
        observation_token=str(observation_token or ""),
        producer_task_key=(str(stage or ""), str(task_base or "")),
        producer_instance_id=str(producer_instance_id or ""),
        contract_hash=str(contract_hash or ""),
        reuse_policy_version=str(reuse_policy_version or ""),
        implementation_version=str(implementation_version or ""),
        produced_artifacts=artifacts,
        worker_callable=str(worker_callable or ""),
        instance_payload=instance_payload,
        completed_at=str(completed_at or ""),
        load_status="malformed" if errors else "available",
        load_detail="; ".join(errors) if errors else "validated row shape",
    )


def _ordered_timestamps(*values: str) -> bool:
    try:
        parsed = tuple(datetime.fromisoformat(value) for value in values)
    except (TypeError, ValueError):
        return False
    if len(parsed) != 3:
        return False
    completed_at, captured_at, decision_at = parsed
    return (
        completed_at <= captured_at
        and captured_at.replace(microsecond=0) <= decision_at
    )


def _task_key(value: Any) -> TaskKey | None:
    if not isinstance(value, Mapping):
        return None
    stage = _nonblank_string(value.get("stage"))
    task_base = _nonblank_string(value.get("task_base"))
    if stage is None or task_base is None:
        return None
    return stage, task_base


def _unique_string_tuple(value: Any) -> Tuple[str, ...] | None:
    if not isinstance(value, (tuple, list)):
        return None
    values = tuple(str(item) for item in value)
    if any(not item for item in values) or len(set(values)) != len(values):
        return None
    return values


def _nonblank_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _loads_json(value: Any) -> Any:
    try:
        return json.loads(str(value))
    except Exception:
        return None


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
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        is not None
    )


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
