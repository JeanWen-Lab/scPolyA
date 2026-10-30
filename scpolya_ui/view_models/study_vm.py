from __future__ import annotations

from dataclasses import dataclass

from scpolya_ui.studies import StudyRecord, StudyVariantRecord
from scpolya_ui.view_models.artifact_vm import ArtifactState, artifact_state


@dataclass(frozen=True)
class StudySummary:
    id: str
    name: str
    status: str
    variant_count: int
    ready_variant_count: int = 0


@dataclass(frozen=True)
class StudyVariantSummary:
    id: str
    label: str
    job_id: str
    run_record_id: str
    impacted_task_count: int
    ready_artifacts: int
    artifacts: tuple[ArtifactState, ...]


def study_summary(study: StudyRecord) -> StudySummary:
    variants = tuple(study_variant_summary(variant) for variant in study.variants)
    ready = sum(1 for variant in variants if variant.ready_artifacts > 0)
    return StudySummary(
        id=study.id,
        name=study.name,
        status=study.status,
        variant_count=len(study.variants),
        ready_variant_count=ready,
    )


def study_variant_summary(variant: StudyVariantRecord) -> StudyVariantSummary:
    artifacts = (
        artifact_state(variant.annotated_bigbed, label="annotated_bigbed", role="annotated_bigbed"),
        artifact_state(variant.annotated_bed, label="annotated_bed", role="annotated_bed"),
        artifact_state(variant.annotated_gtf, label="annotated_gtf", role="annotated_gtf"),
    )
    ready = sum(1 for artifact in artifacts if artifact.status == "ready")
    return StudyVariantSummary(
        id=variant.id,
        label=variant.label,
        job_id=variant.job_id,
        run_record_id=variant.run_record_id,
        impacted_task_count=variant.impacted_task_count,
        ready_artifacts=ready,
        artifacts=artifacts,
    )
