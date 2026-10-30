"""Read-only diagnostic relations between persisted coverage branch manifests."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Tuple

from .branch_manifest_report import (
    BranchManifestSample,
    ManifestAvailabilityStatus,
    branch_manifest_samples_from_decisions,
)
from .reuse_report import (
    CurrentReuseDecisionSummaryV2,
    load_current_reuse_decision_summaries_v2,
)


TaskKey = Tuple[str, str]
BranchDiagnosticRelationStatus = Literal[
    "matched",
    "unmatched",
    "ambiguous",
    "root",
    "no_op",
    "manifest_unavailable",
]

_REQUIRED_BRANCH_FIELDS = ("dedup_method", "polyA_status", "is_polyA", "strand")
_DIAGNOSTIC_ONLY = "diagnostic_only"


@dataclass(frozen=True)
class BranchDiagnosticRelation:
    """One consumer-oriented branch explanation, never a dynamic graph edge."""

    consumer_instance_id: str
    consumer_decision_id: int
    consumer_task_key: TaskKey
    consumer_manifest_status: ManifestAvailabilityStatus
    consumer_branch_status: str | None
    relation_status: BranchDiagnosticRelationStatus
    effective_input_artifact: str | None
    producer_instance_id: str | None
    producer_decision_id: int | None
    producer_task_key: TaskKey | None
    effective_output_artifact: str | None
    matched_field_keys: Tuple[str, ...]
    candidate_instance_ids: Tuple[str, ...]
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
            "consumer_manifest_status": self.consumer_manifest_status,
            "consumer_branch_status": self.consumer_branch_status,
            "relation_status": self.relation_status,
            "effective_input_artifact": self.effective_input_artifact,
            "producer_instance_id": self.producer_instance_id,
            "producer_decision_id": self.producer_decision_id,
            "producer_task_key": (
                None
                if self.producer_task_key is None
                else {
                    "stage": self.producer_task_key[0],
                    "task_base": self.producer_task_key[1],
                }
            ),
            "effective_output_artifact": self.effective_output_artifact,
            "matched_field_keys": list(self.matched_field_keys),
            "candidate_instance_ids": list(self.candidate_instance_ids),
            "detail": self.detail,
            "strict_effect": self.strict_effect,
        }


@dataclass(frozen=True)
class BranchDiagnosticRelationReport:
    """Manifest-only relation inventory, intentionally independent of graph state."""

    state_db_path: str
    source_decision_count: int
    latest_instance_count: int
    coverage_instance_count: int
    relations: Tuple[BranchDiagnosticRelation, ...]
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
        relations = self.relations if limit is None else self.relations[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "source_decision_count": self.source_decision_count,
            "latest_instance_count": self.latest_instance_count,
            "coverage_instance_count": self.coverage_instance_count,
            "strict_effect": self.strict_effect,
            "relation_count": self.relation_count,
            "matched_count": self.matched_count,
            "relation_status_counts": self.relation_status_counts,
            "relations": [relation.as_dict() for relation in relations],
        }


def build_branch_diagnostic_relation_report(
    state_or_db_path: str | Path,
) -> BranchDiagnosticRelationReport:
    """Read persisted v1 manifests and derive diagnostic relations only."""

    decisions = load_current_reuse_decision_summaries_v2(
        state_or_db_path,
        limit=None,
    )
    return build_branch_diagnostic_relation_report_from_decisions(
        decisions,
        state_db_path=str(state_or_db_path),
    )


def build_branch_diagnostic_relation_report_from_decisions(
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
    *,
    state_db_path: str = "",
) -> BranchDiagnosticRelationReport:
    """Explain latest scoped decisions without creating graph evidence."""

    decision_rows = tuple(decisions)
    samples = branch_manifest_samples_from_decisions(decision_rows, latest_only=True)
    active_producers = tuple(
        sample
        for sample in samples
        if _is_active_available(sample) and sample.effective_output_artifact is not None
    )
    relations = tuple(
        _relation_for_consumer(sample, active_producers)
        for sample in samples
    )
    return BranchDiagnosticRelationReport(
        state_db_path=state_db_path,
        source_decision_count=len(decision_rows),
        latest_instance_count=len({decision.instance_id for decision in decision_rows}),
        coverage_instance_count=len(samples),
        relations=relations,
    )


def format_branch_diagnostic_relation_report(
    report: BranchDiagnosticRelationReport,
    *,
    limit: int = 20,
) -> str:
    lines = [
        f"Branch diagnostic relations: {report.state_db_path or '-'}",
        f"source decisions: {report.source_decision_count}",
        f"latest instances: {report.latest_instance_count}",
        f"coverage instances: {report.coverage_instance_count}",
        f"relations: {report.relation_count}",
        f"strict effect: {report.strict_effect}",
        "statuses:",
    ]
    for status, count in report.relation_status_counts.items():
        lines.append(f"  {status}: {count}")
    relations = report.relations[: max(0, int(limit))]
    if relations:
        lines.append("consumers:")
    for relation in relations:
        consumer = f"{relation.consumer_task_key[0]}:{relation.consumer_task_key[1]}"
        producer = (
            "-"
            if relation.producer_task_key is None
            else f"{relation.producer_task_key[0]}:{relation.producer_task_key[1]}"
        )
        lines.append(
            f"  {relation.consumer_instance_id[:12]} {consumer} "
            f"status={relation.relation_status} "
            f"manifest={relation.consumer_manifest_status} "
            f"branch={relation.consumer_branch_status or '-'} producer={producer}"
        )
        lines.append(
            f"    input={relation.effective_input_artifact or '-'} "
            f"output={relation.effective_output_artifact or '-'}"
        )
        lines.append(f"    detail={relation.detail}")
    return "\n".join(lines)


def _relation_for_consumer(
    consumer: BranchManifestSample,
    active_producers: Tuple[BranchManifestSample, ...],
) -> BranchDiagnosticRelation:
    if consumer.manifest_status != "available":
        return _relation(
            consumer,
            relation_status="manifest_unavailable",
            detail=f"consumer manifest status is {consumer.manifest_status}",
        )
    if consumer.branch_status == "no_op":
        return _relation(
            consumer,
            relation_status="no_op",
            detail="consumer manifest selects a no-op branch with no produced relation",
        )
    if consumer.branch_status != "active":
        return _relation(
            consumer,
            relation_status="manifest_unavailable",
            detail=f"consumer branch status is {consumer.branch_status or 'missing'}",
        )
    if consumer.effective_input_artifact is None:
        return _relation(
            consumer,
            relation_status="root",
            detail="active consumer has no branch-manifest input artifact",
        )

    artifact_candidates = tuple(
        producer
        for producer in active_producers
        if producer.instance_id != consumer.instance_id
        and producer.effective_output_artifact == consumer.effective_input_artifact
    )
    compatible: list[tuple[BranchManifestSample, Tuple[str, ...]]] = []
    for producer in artifact_candidates:
        matched_fields = _matching_fields(producer, consumer)
        if matched_fields is not None:
            compatible.append((producer, matched_fields))
    candidate_ids = tuple(producer.instance_id for producer, _ in compatible)
    if not compatible:
        if artifact_candidates:
            detail = (
                f"{len(artifact_candidates)} active producer manifest(s) select "
                f"{consumer.effective_input_artifact}, but none match required/shared branch fields"
            )
        else:
            detail = (
                "no active producer manifest selects "
                f"{consumer.effective_input_artifact}"
            )
        return _relation(
            consumer,
            relation_status="unmatched",
            candidate_instance_ids=tuple(producer.instance_id for producer in artifact_candidates),
            detail=detail,
        )
    if len(compatible) > 1:
        return _relation(
            consumer,
            relation_status="ambiguous",
            candidate_instance_ids=candidate_ids,
            detail=(
                f"{len(compatible)} active producer manifests select the input artifact "
                "with matching branch fields"
            ),
        )
    producer, matched_fields = compatible[0]
    return _relation(
        consumer,
        relation_status="matched",
        producer=producer,
        matched_field_keys=matched_fields,
        candidate_instance_ids=candidate_ids,
        detail=(
            "valid decision-time manifests select the same symbolic artifact "
            "with matching shared branch fields; diagnostic only"
        ),
    )


def _relation(
    consumer: BranchManifestSample,
    *,
    relation_status: BranchDiagnosticRelationStatus,
    producer: BranchManifestSample | None = None,
    matched_field_keys: Tuple[str, ...] = (),
    candidate_instance_ids: Tuple[str, ...] = (),
    detail: str,
) -> BranchDiagnosticRelation:
    return BranchDiagnosticRelation(
        consumer_instance_id=consumer.instance_id,
        consumer_decision_id=consumer.decision_id,
        consumer_task_key=consumer.task_key,
        consumer_manifest_status=consumer.manifest_status,
        consumer_branch_status=consumer.branch_status,
        relation_status=relation_status,
        effective_input_artifact=consumer.effective_input_artifact,
        producer_instance_id=None if producer is None else producer.instance_id,
        producer_decision_id=None if producer is None else producer.decision_id,
        producer_task_key=None if producer is None else producer.task_key,
        effective_output_artifact=None if producer is None else producer.effective_output_artifact,
        matched_field_keys=matched_field_keys,
        candidate_instance_ids=candidate_instance_ids,
        detail=detail,
    )


def _is_active_available(sample: BranchManifestSample) -> bool:
    return sample.manifest_status == "available" and sample.branch_status == "active"


def _matching_fields(
    producer: BranchManifestSample,
    consumer: BranchManifestSample,
) -> Tuple[str, ...] | None:
    producer_fields = _field_map(producer.selected_fields)
    consumer_fields = _field_map(consumer.selected_fields)
    if producer_fields is None or consumer_fields is None:
        return None
    if any(
        key not in producer_fields
        or key not in consumer_fields
        or producer_fields[key] != consumer_fields[key]
        for key in _REQUIRED_BRANCH_FIELDS
    ):
        return None
    shared_keys = tuple(key for key in consumer_fields if key in producer_fields)
    if any(producer_fields[key] != consumer_fields[key] for key in shared_keys):
        return None
    return shared_keys


def _field_map(fields: Iterable[tuple[str, str]]) -> Mapping[str, str] | None:
    mapped: dict[str, str] = {}
    for key, value in fields:
        if key in mapped and mapped[key] != value:
            return None
        mapped[key] = value
    return mapped


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
