from __future__ import annotations

import json
import re
from argparse import Namespace
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from .finalized_unit_inputs import (
    FinalizedUnitInputRow,
    find_finalized_unit_input_row,
    finalized_unit_input_table_path,
    load_finalized_unit_input_table,
)
from .parameter_studies import (
    final_peak_item_for_config,
    preview_peak_artifacts_for_config,
)
from .run_config_preview import load_run_config_snapshot
from .run_layout import RunLayout
from .sc_item import ScItem
from .task_completion import (
    TaskCompletionAuthorityModeV1,
    TaskCompletionRecordV1,
    TaskCompletionRecordSourceV1,
    TaskCompletionSnapshotV2,
    TaskCompletionSourceV1,
    load_task_completion_snapshot_v2,
)
from .task_coordinate import (
    TaskCoordinateError,
    TaskCoordinateV1,
    build_task_coordinate_v1,
)
from .effective_config import annotated_peak_signature
from .artifact_naming import (
    ArtifactNameManifestError,
    build_scoped_artifact_name_v1,
    load_artifact_name_manifest_v1,
)
from quantification_config import (
    resolved_quantification_mode,
    terminal_annotation_schema_from_coordinate_mode,
)
from utils.terminal_quant_utils import (
    TERMINAL_DISTAL_USAGE_SUFFIX,
    TERMINAL_FEATURE_OVERVIEW_SUFFIX,
    TERMINAL_IPUI_GENE_TOTAL_SUFFIX,
    TERMINAL_IPUI_PER_SITE_SUFFIX,
    quantification_artifact_suffix,
    quantification_summary_suffix,
)
from utils.assignment_diagnostics import unit_diagnostics_paths
from utils.polya_extract import polya_publish_marker_path


ExpectationMode = Literal["all", "any", "one"]
HealthStatus = Literal["ready", "missing", "empty", "unresolved"]
FindingLevel = Literal["warning", "info"]
ARTIFACT_HEALTH_TASK_RECORD_SCHEMA_V2 = (
    "scpolya.artifact_health.task_record.v2"
)


@dataclass(frozen=True)
class ArtifactHealthTaskRecordV2:
    task_coordinate: TaskCoordinateV1
    done_at: str
    completion_source: TaskCompletionRecordSourceV1 = "structured"
    schema_version: str = ARTIFACT_HEALTH_TASK_RECORD_SCHEMA_V2

    def __post_init__(self) -> None:
        if not isinstance(self.task_coordinate, TaskCoordinateV1):
            raise TypeError(
                "Artifact-health task records require TaskCoordinateV1"
            )
        if self.schema_version != ARTIFACT_HEALTH_TASK_RECORD_SCHEMA_V2:
            raise ValueError(
                "Artifact-health task record schema mismatch: "
                f"{self.schema_version!r}"
            )

    @property
    def stage(self) -> str:
        return self.task_coordinate.stage

    @property
    def task_base(self) -> str:
        return self.task_coordinate.task_base

    @property
    def task_coordinate_id(self) -> str:
        return self.task_coordinate.task_coordinate_id

    @property
    def identity(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (field.key, field.legacy_value)
            for field in self.task_coordinate.identity
        )

    @property
    def params(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (field.key, field.legacy_value)
            for field in self.task_coordinate.params
        )

    @property
    def key(self) -> tuple[str, str]:
        return self.stage, self.task_base

    def identity_value(self, key: str, default: str = "") -> str:
        for item_key, item_value in self.identity:
            if item_key == key:
                return item_value
        return default


@dataclass(frozen=True)
class ArtifactExpectation:
    stage: str
    task_base: str
    label: str
    paths: tuple[str, ...]
    mode: ExpectationMode = "all"
    note: str = ""
    unresolved_detail: str = ""
    allow_empty: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return self.stage, self.task_base


@dataclass(frozen=True)
class ArtifactHealthFinding:
    level: FindingLevel
    issue_type: str
    stage: str
    task_base: str
    label: str
    path: str
    detail: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.stage, self.task_base


@dataclass(frozen=True)
class ArtifactHealthEntry:
    expectation: ArtifactExpectation
    status: HealthStatus
    ready_paths: tuple[str, ...] = ()
    missing_paths: tuple[str, ...] = ()
    empty_paths: tuple[str, ...] = ()
    findings: tuple[ArtifactHealthFinding, ...] = ()


@dataclass(frozen=True)
class ArtifactHealthReport:
    state_db_path: str
    run_root: str
    done_task_count: int
    checked_expectation_count: int
    ready_count: int
    missing_count: int
    empty_count: int
    unresolved_count: int
    entries: tuple[ArtifactHealthEntry, ...]
    findings: tuple[ArtifactHealthFinding, ...]
    completion_authority_mode: TaskCompletionAuthorityModeV1 = (
        "uninitialized"
    )
    completion_source: TaskCompletionSourceV1 = "legacy"
    legacy_fallback_count: int = 0

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "warning")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warning_count"] = self.warning_count
        return payload


def check_artifact_health(
    state_or_db_path: str | Path,
    *,
    config: Mapping[str, Any] | None = None,
    root: str | Path | None = None,
) -> ArtifactHealthReport:
    """Return read-only artifact health diagnostics for high-value DONE tasks.

    This function does not change completion or ledger tables. It is a warning
    layer for stale reuse risks, not a scheduler or repair tool.
    """

    db_path = _resolve_db_path(state_or_db_path)
    run_config = dict(config) if config is not None else load_run_config_snapshot(db_path)
    run_root = _resolve_run_root(db_path, run_config, root=root)
    completion_snapshot, records = (
        load_artifact_health_task_records_with_snapshot_v2(db_path)
    )
    expectations = tuple(_expected_artifacts_for_records(records, run_config, run_root))
    entries = tuple(_evaluate_expectation(expectation) for expectation in expectations)
    findings = tuple(finding for entry in entries for finding in entry.findings)
    return ArtifactHealthReport(
        state_db_path=str(db_path),
        run_root=str(run_root),
        done_task_count=len(records),
        checked_expectation_count=len(entries),
        ready_count=sum(1 for entry in entries if entry.status == "ready"),
        missing_count=sum(1 for entry in entries if entry.status == "missing"),
        empty_count=sum(1 for entry in entries if entry.status == "empty"),
        unresolved_count=sum(1 for entry in entries if entry.status == "unresolved"),
        entries=entries,
        findings=findings,
        completion_authority_mode=completion_snapshot.authority_mode,
        completion_source=completion_snapshot.completion_source,
        legacy_fallback_count=(
            completion_snapshot.legacy_fallback_count
        ),
    )


def check_task_artifact_health_v2(
    state_or_db_path: str | Path,
    record: ArtifactHealthTaskRecordV2,
    *,
    config: Mapping[str, Any] | None = None,
    root: str | Path | None = None,
) -> tuple[ArtifactHealthEntry, ...]:
    """Return artifact-health entries for a single DONE candidate.

    This helper is read-only. It shares the same expectation/evaluation logic as
    `check_artifact_health()` but lets runtime code inspect one completion
    candidate without scanning every completion row.
    """

    db_path = _resolve_db_path(state_or_db_path)
    run_config = dict(config) if config is not None else load_run_config_snapshot(db_path)
    run_root = _resolve_run_root(db_path, run_config, root=root)
    expectations = tuple(_expected_artifacts_for_records((record,), run_config, run_root))
    return tuple(_evaluate_expectation(expectation) for expectation in expectations)


def load_artifact_health_task_records_v2(
    state_or_db_path: str | Path,
) -> tuple[ArtifactHealthTaskRecordV2, ...]:
    """Load active completion truth without creating or modifying SQLite."""

    return load_artifact_health_task_records_with_snapshot_v2(
        state_or_db_path
    )[1]


def load_artifact_health_task_records_with_snapshot_v2(
    state_or_db_path: str | Path,
) -> tuple[
    TaskCompletionSnapshotV2,
    tuple[ArtifactHealthTaskRecordV2, ...],
]:
    """Load completion authority metadata and artifact-health records once."""

    snapshot = load_task_completion_snapshot_v2(
        _resolve_db_path(state_or_db_path)
    )
    records = tuple(
        ArtifactHealthTaskRecordV2(
            task_coordinate=record.task_coordinate,
            done_at=record.done_at,
            completion_source=record.completion_source,
        )
        for record in snapshot.records
    )
    return snapshot, records


def artifact_health_task_record_v2_from_completion_record(
    record: TaskCompletionRecordV1,
) -> ArtifactHealthTaskRecordV2:
    """Validate and remove a completion record's derived legacy alias."""

    coordinate = build_task_coordinate_v1(
        stage=record.stage,
        task_base=record.task_base,
        identity=record.identity,
        params=record.params,
    )
    if coordinate.task_coordinate_id != record.task_coordinate_id:
        raise TaskCoordinateError(
            "Completion task coordinate ID differs from structured payload"
        )
    if coordinate.legacy_variant_str != record.legacy_variant_str:
        raise TaskCoordinateError(
            "Completion legacy alias differs from structured params"
        )
    return ArtifactHealthTaskRecordV2(
        task_coordinate=coordinate,
        done_at=record.done_at,
        completion_source=record.completion_source,
    )


_DIRECT_PEAK_COORDINATE_KEYS = frozenset(
    {
        ("call_peaks", "combine_and_intersect_peaks"),
        ("annotate_peaks", "assign_peaks_to_genes"),
        ("annotate_peaks", "merge_assigned_peaks"),
        ("annotate_peaks", "resolve_peak_issues"),
        ("annotate_peaks", "convert_peaks_bed_to_bigbed"),
        ("annotate_peaks", "convert_peaks_bed_to_gtf"),
    }
)
_ANNOTATION_EXTRA_KEY_COORDINATE_KEYS = frozenset(
    {
        ("annotate_bams", "annotate_slices"),
        ("count_matrix", "count_unit_matrix"),
        ("count_matrix", "summarize_count_matrix"),
    }
)
_SUMMARY_EXTRA_KEY_COORDINATE_KEYS = frozenset(
    {("summarize_matrix", "summarize_matrix")}
)


def _apply_exact_peak_payload(
    config: dict[str, Any], payload: Mapping[str, Any]
) -> None:
    config.update(payload)
    # Effective-config projection consumes CLI-shaped threshold keys. Populate
    # both scaling forms from the already-resolved coordinate payload so current
    # run defaults cannot replace a historical threshold.
    if "depth_threshold" in payload:
        config["scaled_depth_threshold"] = payload["depth_threshold"]
        config["unscaled_depth_threshold"] = payload["depth_threshold"]
        config["exp_peak_threshold_mode"] = "fixed"
    if "depth_threshold_polyA" in payload:
        config["scaled_depth_threshold_polyA"] = payload["depth_threshold_polyA"]
        config["unscaled_depth_threshold_polyA"] = payload["depth_threshold_polyA"]
        config["polyA_peak_threshold_mode"] = "fixed"


def _coordinate_artifact_config(
    record: ArtifactHealthTaskRecordV2,
    *,
    config: Mapping[str, Any],
    layout: RunLayout,
) -> tuple[dict[str, Any], str]:
    """Resolve artifact identity from the DONE coordinate, never current defaults."""
    out = dict(config)
    fields = _record_fields(record)

    if record.key in _DIRECT_PEAK_COORDINATE_KEYS:
        # Quantification mode was absent from all pre-terminal coordinates.
        raw_coordinate_mode = fields.get("quantification_mode", "")
        coordinate_mode = resolved_quantification_mode(
            {"quantification_mode": raw_coordinate_mode}
        )
        if coordinate_mode not in {"legacy", "terminal-aware"}:
            coordinate_mode = "legacy"
        is_annotation_coordinate = record.key[0] == "annotate_peaks"
        coordinate_annotation_schema = terminal_annotation_schema_from_coordinate_mode(
            raw_coordinate_mode
        )
        if (
            is_annotation_coordinate
            and coordinate_mode == "terminal-aware"
            and coordinate_annotation_schema is None
        ):
            return (
                out,
                "historical terminal annotation coordinate lacks an explicit "
                "schema revision; retired v1 health is unresolved",
            )
        if coordinate_annotation_schema is not None:
            out["terminal_annotation_schema"] = coordinate_annotation_schema
        artifact_field_keys = {
            "dedup_method", "polyA_status", "coverage_scaling",
            "depth_threshold", "depth_threshold_polyA", "merge_distance",
            "merge_distance_polyA", "misprime_A_count", "misprime_in",
            "misprimed_ratio_threshold", "annotate_peak_source",
        }
        comparable_fields = {
            key: value
            for key, value in fields.items()
            if key in artifact_field_keys
        }
        current_item = final_peak_item_for_config(config)
        if current_item is not None and all(
            str(current_item.get(key)) == value
            for key, value in comparable_fields.items()
        ) and resolved_quantification_mode(current_item) == coordinate_mode:
            out["quantification_mode"] = coordinate_mode
            if coordinate_annotation_schema is not None:
                out["terminal_annotation_schema"] = coordinate_annotation_schema
            return out, ""

        try:
            manifest = load_artifact_name_manifest_v1(layout.ArtifactNameManifest)
        except ArtifactNameManifestError as exc:
            return out, f"artifact-name manifest is invalid: {exc}"
        if manifest is not None:
            manifest_candidates: list[dict[str, Any]] = []
            for name_record in manifest.records:
                if (
                    record.key != ("call_peaks", "combine_and_intersect_peaks")
                    and name_record.scope == "annotation"
                ):
                    peak_payload = name_record.payload.get("peak")
                    source = name_record.payload.get("annotate_peak_source")
                    manifest_annotation_schema = name_record.payload.get(
                        "terminal_annotation_schema"
                    )
                elif (
                    record.key == ("call_peaks", "combine_and_intersect_peaks")
                    and name_record.scope == "peak"
                ):
                    peak_payload = name_record.payload
                    source = fields.get("annotate_peak_source")
                    manifest_annotation_schema = None
                else:
                    continue
                if not isinstance(peak_payload, Mapping):
                    continue
                candidate = dict(peak_payload)
                candidate["annotate_peak_source"] = source
                if manifest_annotation_schema not in (None, ""):
                    candidate["terminal_annotation_schema"] = manifest_annotation_schema
                candidate_mode = str(
                    candidate.get("quantification_mode", "legacy")
                )
                if candidate_mode != coordinate_mode:
                    continue
                if is_annotation_coordinate and (
                    candidate.get("terminal_annotation_schema")
                    != terminal_annotation_schema_from_coordinate_mode(
                        raw_coordinate_mode
                    )
                ):
                    continue
                if all(
                    str(candidate.get(key)) == value
                    for key, value in comparable_fields.items()
                ):
                    if candidate not in manifest_candidates:
                        manifest_candidates.append(candidate)
            if len(manifest_candidates) == 1:
                _apply_exact_peak_payload(out, manifest_candidates[0])
                out["quantification_mode"] = coordinate_mode
                if coordinate_annotation_schema is not None:
                    out["terminal_annotation_schema"] = coordinate_annotation_schema
                return out, ""
            if len(manifest_candidates) > 1:
                return (
                    out,
                    "DONE coordinate matches multiple typed artifact-name "
                    "manifest payloads",
                )

        _apply_exact_peak_payload(out, fields)
        out["quantification_mode"] = coordinate_mode
        if coordinate_annotation_schema is not None:
            out["terminal_annotation_schema"] = coordinate_annotation_schema
        return out, ""

    if record.key not in (
        _ANNOTATION_EXTRA_KEY_COORDINATE_KEYS
        | _SUMMARY_EXTRA_KEY_COORDINATE_KEYS
    ):
        return out, ""

    extra_key = fields.get("extra_key", "").strip()
    if not extra_key:
        return out, "DONE coordinate lacks artifact extra_key"

    try:
        manifest = load_artifact_name_manifest_v1(layout.ArtifactNameManifest)
    except ArtifactNameManifestError as exc:
        return out, f"artifact-name manifest is invalid: {exc}"

    matched_payload: Mapping[str, Any] | None = None
    matched_scope = ""
    if manifest is not None:
        for name_record in manifest.records:
            if extra_key in {name_record.code, name_record.legacy_name}:
                matched_payload = name_record.payload
                matched_scope = name_record.scope
                break

        if matched_payload is None and record.key in _SUMMARY_EXTRA_KEY_COORDINATE_KEYS:
            for name_record in manifest.records:
                if name_record.scope != "matrix":
                    continue
                annotation_payload = name_record.payload.get("annotation")
                if not isinstance(annotation_payload, Mapping):
                    continue
                annotation_code = build_scoped_artifact_name_v1(
                    "annotation",
                    annotation_payload,
                    legacy_name="manifest-lookup",
                ).code
                psi_min = name_record.payload.get("psi_min_gene_umis")
                quantification = name_record.payload.get("quantification")
                quantification_config = (
                    quantification if isinstance(quantification, Mapping) else {}
                )
                short_signature = (
                    f"{annotation_code}."
                    f"{RunLayout.LegacyMatrixSubdirName(psi_min, quantification_config)}"
                )
                if extra_key == short_signature:
                    matched_payload = name_record.payload
                    matched_scope = "matrix"
                    break

    if matched_payload is not None:
        if matched_scope == "annotation":
            annotation_payload = matched_payload
        elif matched_scope == "matrix":
            annotation_payload = matched_payload.get("annotation")
            out["psi_min_gene_umis"] = matched_payload.get("psi_min_gene_umis")
            quantification = matched_payload.get("quantification")
            if isinstance(quantification, Mapping):
                out.update(quantification)
        else:
            annotation_payload = None
        if isinstance(annotation_payload, Mapping):
            peak_payload = annotation_payload.get("peak")
            if isinstance(peak_payload, Mapping):
                _apply_exact_peak_payload(out, peak_payload)
                out["annotate_peak_source"] = annotation_payload.get(
                    "annotate_peak_source", "resolved"
                )
                schema = annotation_payload.get("terminal_annotation_schema")
                if schema not in (None, ""):
                    out["terminal_annotation_schema"] = schema
                else:
                    out.pop("terminal_annotation_schema", None)
                payload_mode = peak_payload.get(
                    "quantification_mode", "legacy"
                )
                out["quantification_mode"] = payload_mode
                if (
                    resolved_quantification_mode(
                        {"quantification_mode": payload_mode}
                    )
                    == "terminal-aware"
                    and schema in (None, "")
                ):
                    return (
                        out,
                        "historical terminal annotation manifest lacks an "
                        "explicit schema revision; retired v1 health is unresolved",
                    )
                return out, ""

    # Historical runs may predate a manifest. Reuse current configuration only
    # when the coordinate's complete opaque signature is an exact match.
    current_item = final_peak_item_for_config(config)
    if current_item is not None:
        try:
            if record.key in _SUMMARY_EXTRA_KEY_COORDINATE_KEYS:
                annotation_extra, separator, summary_tail = extra_key.rpartition(
                    ".psi-min-gene-umis-"
                )
                summary_match = re.fullmatch(
                    r"(?P<psi>[^.]+)(?:\.quant-terminal-aware"
                    r"\.intronic-misprimed-rt(?P<threshold>[-+0-9.eE]+))?",
                    summary_tail,
                ) if separator and annotation_extra else None
                current_annotation = layout.AnnotatedPeakSignatureFromDict(
                    current_item
                )
                if summary_match and annotation_extra == current_annotation:
                    out["psi_min_gene_umis"] = summary_match.group("psi")
                    threshold = summary_match.group("threshold")
                    if threshold is None:
                        out["quantification_mode"] = "legacy"
                    else:
                        out["quantification_mode"] = "terminal-aware"
                        out["intronic_misprimed_ratio_threshold"] = float(
                            threshold
                        )
                    return out, ""
                current_signature = layout.MatrixSignatureFromDict(
                    current_item,
                    psi_min_gene_umis=config.get("psi_min_gene_umis", 1),
                )
            else:
                current_signature = layout.AnnotatedPeakSignatureFromDict(
                    current_item
                )
        except ArtifactNameManifestError:
            current_signature = ""
        if extra_key == current_signature:
            return out, ""

    return (
        out,
        "DONE artifact extra_key cannot be resolved to an authoritative "
        "artifact-name manifest record or the exact current coordinate",
    )


def _expected_artifacts_for_records(
    records: Iterable[ArtifactHealthTaskRecordV2],
    config: Mapping[str, Any],
    run_root: Path,
) -> Iterable[ArtifactExpectation]:
    layout = _layout_from_config_or_root(config, run_root)

    for record in records:
        coordinate_config, coordinate_error = _coordinate_artifact_config(
            record,
            config=config,
            layout=layout,
        )
        if coordinate_error:
            yield ArtifactExpectation(
                record.stage,
                record.task_base,
                "coordinate-specific artifact namespace",
                (),
                unresolved_detail=coordinate_error,
            )
            continue

        peak_artifacts = preview_peak_artifacts_for_config(
            _config_with_root(coordinate_config, run_root)
        )
        args = _namespace_from_config(coordinate_config)
        final_item = final_peak_item_for_config(coordinate_config)
        quantification_mode = resolved_quantification_mode(coordinate_config)

        if peak_artifacts is not None:
            yield from _peak_expectations(
                record,
                peak_artifacts,
                layout=layout,
                final_item=final_item,
                quantification_mode=quantification_mode,
            )

        if record.key == ("extract_subsets", "split_strand"):
            yield _strand_unit_bam_expectation(
                record=record,
                layout=layout,
                args=args,
            )

        if record.key == ("extract_subsets", "extract_polyA"):
            yield _polya_unit_bam_expectation(
                record=record,
                layout=layout,
                args=args,
            )

        if record.key in {
            ("merge_units", "cat_chunks"),
            ("merge_units", "cat_strand_chunks"),
        }:
            yield _cohort_bam_expectation(
                record=record,
                layout=layout,
                args=args,
            )

        if record.key == ("call_peaks", "generate_raw_coverage"):
            yield _raw_coverage_expectation(
                record=record,
                layout=layout,
                args=args,
            )

        if record.key == ("annotate_bams", "annotate_slices"):
            sidecar = _peak_sidecar_expectation(
                record=record,
                peak_artifacts=peak_artifacts,
                config=coordinate_config,
                layout=layout,
                args=args,
            )
            yield sidecar
            if _record_has_assignment_diagnostics(record):
                yield from _unit_assignment_diagnostics_expectations(sidecar)

        if (
            record.key == ("count_matrix", "count_unit_matrix")
            and final_item is not None
        ):
            yield _peak_unit_count_expectation(
                record=record,
                config=coordinate_config,
                layout=layout,
                args=args,
                final_item=final_item,
                quantification_mode=quantification_mode,
            )

        if (
            record.key == ("count_matrix", "summarize_count_matrix")
            and final_item is not None
        ):
            sid = record.identity_value("sid") or record.identity_value("sample_id")
            dedup_method = record.identity_value("dedup_method") or str(coordinate_config.get("dedup_method") or "")
            if sid and dedup_method:
                item = ScItem(sid=sid, dedup_status=dedup_method, add_dedup_method=True, args=args)
                file_name = item.format_name(
                    suffix=quantification_artifact_suffix(
                        "counts.tsv.gz", quantification_mode
                    ),
                    is_annotated_bam=True,
                )
                paths = tuple(
                    str(root / file_name)
                    for root in layout.CountMatrixConfigSubCandidates(
                        final_item
                    )
                )
                yield ArtifactExpectation(
                    record.stage,
                    record.task_base,
                    f"{sid} peak count matrix",
                    paths,
                    mode="one",
                )
                for base, label in (
                    ("peak_assignment.tsv.gz", "per-PAS read-assignment table"),
                    ("peak_assignment_summary.json", "read-assignment summary"),
                ) if _record_has_assignment_diagnostics(record) else ():
                    report_name = item.format_name(
                        suffix=quantification_artifact_suffix(base, quantification_mode),
                        is_annotated_bam=True,
                    )
                    yield ArtifactExpectation(
                        record.stage,
                        record.task_base,
                        f"{sid} {label}",
                        tuple(
                            str(root / report_name)
                            for root in layout.CountMatrixConfigSubCandidates(final_item)
                        ),
                        mode="one",
                    )

        if (
            record.key == ("summarize_matrix", "summarize_matrix")
            and final_item is not None
        ):
            sid = record.identity_value("sid") or record.identity_value("sample_id")
            dedup_method = record.identity_value("dedup_method") or str(coordinate_config.get("dedup_method") or "")
            psi_min = coordinate_config.get("psi_min_gene_umis", 1)
            if sid and dedup_method:
                item = ScItem(sid=sid, dedup_status=dedup_method, add_dedup_method=True, args=args)
                summary_roots = layout.MatrixSummaryConfigSubCandidates(
                    final_item,
                    psi_min_gene_umis=psi_min,
                )
                peak_metadata_name = (
                    f"{annotated_peak_signature(layout, final_item)}."
                    f"{quantification_summary_suffix('peak_to_gene.tsv.gz', quantification_mode)}"
                )
                yield ArtifactExpectation(
                    record.stage,
                    record.task_base,
                    "summary peak-to-gene metadata",
                    tuple(str(root / peak_metadata_name) for root in summary_roots),
                    mode="one",
                )
                yield ArtifactExpectation(
                    record.stage,
                    record.task_base,
                    f"{sid} summary gene-cell long table",
                    tuple(
                        str(
                            root
                            / item.format_name(
                                suffix=quantification_summary_suffix(
                                    "gene_cell.tsv.gz", quantification_mode
                                ),
                                is_annotated_bam=True,
                            )
                        )
                        for root in summary_roots
                    ),
                    mode="one",
                )
                yield ArtifactExpectation(
                    record.stage,
                    record.task_base,
                    f"{sid} summary PSI table",
                    tuple(
                        str(
                            root
                            / item.format_name(
                                suffix=quantification_summary_suffix(
                                    "psi.tsv.gz", quantification_mode
                                ),
                                is_annotated_bam=True,
                            )
                        )
                        for root in summary_roots
                    ),
                    mode="one",
                )
                yield ArtifactExpectation(
                    record.stage,
                    record.task_base,
                    f"{sid} summary weighted UTR table",
                    tuple(
                        str(
                            root / item.format_name(
                                suffix=quantification_summary_suffix(
                                    "weighted_three_prime_utr_length.tsv.gz",
                                    quantification_mode,
                                ),
                                is_annotated_bam=True,
                            )
                        )
                        for root in summary_roots
                    ),
                    mode="one",
                )
                dense_or_skipped = tuple(
                    str(root / item.format_name(suffix=suffix, is_annotated_bam=True))
                    for root in summary_roots
                    for suffix in (
                        quantification_summary_suffix(
                            "cell_gene.matrix.tsv.gz", quantification_mode
                        ),
                        quantification_summary_suffix(
                            "cell_gene.matrix.skipped.json", quantification_mode
                        ),
                    )
                )
                yield ArtifactExpectation(
                    record.stage,
                    record.task_base,
                    f"{sid} summary dense matrix or skip manifest",
                    dense_or_skipped,
                    mode="one",
                )
                if quantification_mode == "terminal-aware":
                    for label, suffix in (
                        ("summary distal-site usage table", TERMINAL_DISTAL_USAGE_SUFFIX),
                        ("summary intronic iPUI per-site table", TERMINAL_IPUI_PER_SITE_SUFFIX),
                        ("summary intronic iPUI gene-total table", TERMINAL_IPUI_GENE_TOTAL_SUFFIX),
                        ("summary feature overview table", TERMINAL_FEATURE_OVERVIEW_SUFFIX),
                    ):
                        yield ArtifactExpectation(
                            record.stage,
                            record.task_base,
                            f"{sid} {label}",
                            tuple(
                                str(
                                    root
                                    / item.format_name(
                                        suffix=suffix,
                                        is_annotated_bam=True,
                                    )
                                )
                                for root in summary_roots
                            ),
                            mode="one",
                        )

        if record.key == ("rna_gene_matrix", "summarize_gene_matrix"):
            sid = record.identity_value("sid") or record.identity_value("sample_id")
            dedup_method = record.identity_value("dedup_method") or str(coordinate_config.get("dedup_method") or "")
            if sid and dedup_method:
                item = ScItem(sid=sid, dedup_status=dedup_method, add_dedup_method=True, args=args)
                rna_config = {
                    "dedup_method": dedup_method,
                    "cb_tag": coordinate_config.get("cb_tag"),
                    "umi_tag": coordinate_config.get("umi_tag"),
                }
                output_roots = layout.RnaGeneMatrixSubCandidates(
                    rna_config
                )
                file_name = item.format_name(
                    suffix="rna_gene_cell.tsv.gz",
                    is_annotated_bam=False,
                )
                yield ArtifactExpectation(
                    record.stage,
                    record.task_base,
                    f"{sid} RNA gene-cell long table",
                    tuple(
                        str(root / file_name)
                        for root in output_roots
                    ),
                    mode="one",
                )


def _peak_expectations(
    record: ArtifactHealthTaskRecordV2,
    peak_artifacts: Any,
    *,
    layout: RunLayout,
    final_item: Mapping[str, Any] | None,
    quantification_mode: str,
) -> Iterable[ArtifactExpectation]:
    if record.key == ("call_peaks", "combine_and_intersect_peaks"):
        yield ArtifactExpectation(
            record.stage,
            record.task_base,
            "mispriming-filtered peak BED",
            peak_artifacts.mispriming_filtered_bed_candidates
            or (peak_artifacts.mispriming_filtered_bed,),
            mode="one",
        )
        yield ArtifactExpectation(
            record.stage,
            record.task_base,
            "merged mispriming-filtered peak BED",
            peak_artifacts.mispriming_filtered_merged_bed_candidates
            or (peak_artifacts.mispriming_filtered_merged_bed,),
            mode="one",
        )
        # Audit publication was introduced with science/cache generation 3.
        # Historical records must not acquire requirements from current config.
        # Extend this explicit generation set when introducing a successor.
        if dict(record.params).get("pipeline_cache_epoch") in {"3", "4", "5"}:
            candidates = peak_artifacts.mispriming_filtered_bed_candidates or (
                peak_artifacts.mispriming_filtered_bed,
            )
            yield ArtifactExpectation(
                record.stage, record.task_base,
                "not-evaluable same-strand tail evidence audit BED",
                tuple(str(Path(path).with_name(f"{Path(path).stem}-not-evaluable.audit.bed"))
                      for path in candidates),
                mode="one", allow_empty=True,
                note="Empty is valid; missing audit is incomplete current-generation publication.",
            )
    elif (
        record.key == ("annotate_peaks", "assign_peaks_to_genes")
        and final_item is not None
    ):
        yield ArtifactExpectation(
            record.stage,
            record.task_base,
            "assigned peak BED",
            tuple(
                str(path)
                for path in layout.AnnotatedFinalOutputFileCandidates(
                    suffix=quantification_artifact_suffix(
                        "___peaks-assigned.bed", quantification_mode
                    ),
                    d=final_item,
                )
            ),
            mode="one",
        )
    elif (
        record.key == ("annotate_peaks", "merge_assigned_peaks")
        and final_item is not None
    ):
        yield ArtifactExpectation(
            record.stage,
            record.task_base,
            "assigned peaks with stop-codon annotations",
            tuple(
                str(path)
                for path in layout.AnnotatedFinalOutputFileCandidates(
                    suffix=quantification_artifact_suffix(
                        "___peaks-assigned-with-stop-codon.df.pkl",
                        quantification_mode,
                    ),
                    d=final_item,
                )
            ),
            mode="one",
        )
    elif record.key == ("annotate_peaks", "resolve_peak_issues"):
        yield ArtifactExpectation(
            record.stage,
            record.task_base,
            "annotated peak BED",
            peak_artifacts.annotated_bed_candidates
            or (peak_artifacts.annotated_bed,),
            mode="one",
        )
    elif record.key == ("annotate_peaks", "convert_peaks_bed_to_bigbed"):
        yield ArtifactExpectation(
            record.stage,
            record.task_base,
            "annotated peak BigBed",
            peak_artifacts.annotated_bigbed_candidates
            or (peak_artifacts.annotated_bigbed,),
            mode="one",
        )
    elif record.key == ("annotate_peaks", "convert_peaks_bed_to_gtf"):
        yield ArtifactExpectation(
            record.stage,
            record.task_base,
            "annotated peak GTF",
            peak_artifacts.annotated_gtf_candidates
            or (peak_artifacts.annotated_gtf,),
            mode="one",
        )


def _strand_unit_bam_expectation(
    *,
    record: ArtifactHealthTaskRecordV2,
    layout: RunLayout,
    args: Namespace,
) -> ArtifactExpectation:
    fields = _record_fields(record)
    uid = fields.get("uid", "")
    dedup_method = fields.get("dedup_method", "")
    poly_a_status = fields.get("polyA_status", "")
    materialize = fields.get("materialize_strand_bams", "")
    label = f"{uid} explicit plus/minus BAMs" if uid else "explicit plus/minus unit BAMs"
    if not uid or not dedup_method:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="DONE coordinate lacks uid or dedup_method",
        )
    if str(materialize).lower() != "true":
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail=(
                "DONE coordinate lacks explicit materialize_strand_bams=true signature"
            ),
        )
    is_poly_a = poly_a_status not in (None, "")
    item = ScItem(
        uid=uid,
        dedup_status=dedup_method,
        is_polyA=is_poly_a,
        add_polyA=is_poly_a,
        add_dedup_method=True,
        args=args,
    )
    if is_poly_a and item.polyA_status != poly_a_status:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="DONE PolyA identity differs from the current run configuration",
        )
    bam_paths = tuple(
        layout.UnitOutput(uid) / item.format_name(suffix="bam", strand=strand)
        for strand in ("plus", "minus")
    )
    paths = tuple(
        str(path)
        for bam_path in bam_paths
        for path in (bam_path, bam_path.with_suffix(".bam.bai"))
    )
    return ArtifactExpectation(
        record.stage,
        record.task_base,
        label,
        paths,
        mode="all",
    )


def _polya_unit_bam_expectation(
    *,
    record: ArtifactHealthTaskRecordV2,
    layout: RunLayout,
    args: Namespace,
) -> ArtifactExpectation:
    fields = _record_fields(record)
    uid = fields.get("uid", "")
    dedup_method = fields.get("dedup_method", "")
    poly_a_status = fields.get("polyA_status", "")
    label = f"{uid} Poly(A) BAM" if uid else "unit Poly(A) BAM"
    if not uid or not dedup_method or not poly_a_status:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail=(
                "DONE coordinate lacks uid, dedup_method, or polyA_status"
            ),
        )

    item = ScItem(
        uid=uid,
        dedup_status=dedup_method,
        is_polyA=True,
        add_polyA=True,
        add_dedup_method=True,
        args=args,
    )
    if item.polyA_status != poly_a_status:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail=(
                "DONE PolyA identity differs from the current run configuration"
            ),
        )
    bam_path = layout.UnitOutput(uid) / item.format_name(suffix="bam")
    publish_marker = polya_publish_marker_path(bam_path)
    return ArtifactExpectation(
        record.stage,
        record.task_base,
        label,
        (str(bam_path), str(bam_path.with_suffix(".bam.bai"))),
        mode="all",
        unresolved_detail=(
            "an interrupted Poly(A) BAM/index publication must be recovered "
            f"before reuse: {publish_marker}"
            if publish_marker.exists()
            else ""
        ),
    )


def _cohort_bam_expectation(
    *,
    record: ArtifactHealthTaskRecordV2,
    layout: RunLayout,
    args: Namespace,
) -> ArtifactExpectation:
    fields = _record_fields(record)
    dedup_method = fields.get("dedup_method", "")
    poly_a_status = fields.get("polyA_status", "")
    strand = fields.get("strand", "") or None
    is_strand_task = record.key == ("merge_units", "cat_strand_chunks")
    label = "explicit stranded cohort BAM" if is_strand_task else "unstranded cohort BAM"
    if not dedup_method:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="DONE coordinate lacks dedup_method",
        )
    if is_strand_task and strand not in {"plus", "minus"}:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="stranded cohort DONE coordinate lacks plus/minus strand",
        )
    if not is_strand_task and strand is not None:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="default cohort DONE coordinate unexpectedly contains a strand",
        )
    is_poly_a = poly_a_status not in (None, "")
    item = ScItem(
        strand=strand,
        is_cohort=True,
        is_polyA=is_poly_a,
        add_polyA=is_poly_a,
        add_dedup_method=True,
        args=args,
    )
    if item.dedup_method != dedup_method or (is_poly_a and item.polyA_status != poly_a_status):
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="DONE cohort identity differs from the current run configuration",
        )
    bam_path = layout.CohortOutput(item.cohort_name) / item.format_name(suffix="bam")
    return ArtifactExpectation(
        record.stage,
        record.task_base,
        label,
        (str(bam_path), str(bam_path.with_suffix(".bam.bai"))),
        mode="all",
    )


def _raw_coverage_expectation(
    *,
    record: ArtifactHealthTaskRecordV2,
    layout: RunLayout,
    args: Namespace,
) -> ArtifactExpectation:
    fields = _record_fields(record)
    strand = fields.get("strand", "")
    strand_filter = fields.get("strand_filter", "")
    coverage_source = fields.get("coverage_source", "")
    dedup_method = fields.get("dedup_method", "")
    poly_a_status = fields.get("polyA_status", "")
    is_poly_a = str(fields.get("is_polyA", "")).lower() == "true"
    label = f"{strand} raw coverage BedGraph" if strand else "raw coverage BedGraph"
    if (
        strand not in {"plus", "minus"}
        or strand_filter != strand
        or coverage_source != "unstranded"
        or not dedup_method
    ):
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail=(
                "DONE coordinate lacks source=unstranded and matching plus/minus strand-filter signature"
            ),
        )
    item = ScItem(
        strand=strand,
        is_cohort=True,
        is_polyA=is_poly_a,
        add_polyA=is_poly_a,
        add_dedup_method=True,
        args=args,
    )
    if item.dedup_method != dedup_method or (
        is_poly_a and item.polyA_status != poly_a_status
    ):
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="DONE coverage identity differs from the current run configuration",
        )
    path = layout.CohortOutput(item.cohort_name) / item.format_name(
        suffix="bedgraph",
        scaling_factor=None,
        is_coverage=True,
    )
    return ArtifactExpectation(
        record.stage,
        record.task_base,
        label,
        (str(path),),
        mode="all",
    )


def _record_fields(record: ArtifactHealthTaskRecordV2) -> dict[str, str]:
    fields = dict(record.identity)
    for key, value in record.params:
        fields.setdefault(key, value)
    return fields


def _peak_sidecar_expectation(
    *,
    record: ArtifactHealthTaskRecordV2,
    peak_artifacts: Any,
    config: Mapping[str, Any],
    layout: RunLayout,
    args: Namespace,
) -> ArtifactExpectation:
    sid = record.identity_value("sid") or record.identity_value("sample_id")
    ctg = record.identity_value("ctg") or record.identity_value("contig")
    label = f"{sid}.{ctg} peak-assignment sidecar" if sid and ctg else "peak-assignment sidecar"
    if not sid or not ctg:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="DONE identity is missing sid or ctg",
        )
    if peak_artifacts is None:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="could not derive the peak configuration from the run config",
        )

    dedup_method = str(config.get("dedup_method") or "")
    if not dedup_method:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="run config is missing dedup_method",
        )

    unit_row, unresolved_detail = _find_finalized_unit_input_row(
        layout,
        dedup_method=dedup_method,
        sid=sid,
        ctg=ctg,
    )
    if unit_row is None:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail=unresolved_detail,
        )

    unit_id = unit_row.unit_id
    dedup_status = unit_row.dedup_status
    if not unit_id or not dedup_status:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="matched finalized unit input row is missing unit_id or dedup_status",
        )

    item = ScItem(
        uid=unit_id,
        sid=unit_id.replace(f".{ctg}", ""),
        ctg=ctg,
        dedup_status=dedup_status,
        add_dedup_method=True,
        args=args,
    )
    final_item = final_peak_item_for_config(config)
    if final_item is None:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail=(
                "could not derive the peak configuration from the run config"
            ),
        )
    file_name = item.format_name(
        suffix=quantification_artifact_suffix(
            "xr_to_peak.tsv.gz",
            resolved_quantification_mode(config),
        )
    )
    paths = tuple(
        str(root / file_name)
        for root in layout.PeakSidecarsConfigSubCandidates(final_item)
    )
    return ArtifactExpectation(
        record.stage,
        record.task_base,
        label,
        paths,
        mode="one",
    )


# Assignment diagnostics were introduced with science/cache generation 5.
# Gate on the epoch stored in the DONE record itself, never on the current
# code epoch or run config, so historical records (epoch <= 4 or none) do not
# acquire the new requirement. Extend this set when introducing a successor.
_ASSIGNMENT_DIAGNOSTICS_EPOCHS = frozenset({"5"})


def _record_has_assignment_diagnostics(record: ArtifactHealthTaskRecordV2) -> bool:
    return dict(record.params).get("pipeline_cache_epoch") in _ASSIGNMENT_DIAGNOSTICS_EPOCHS


def _unit_assignment_diagnostics_expectations(
    sidecar: ArtifactExpectation,
) -> Iterable[ArtifactExpectation]:
    """The per-unit assignment table and summary written beside the sidecar."""

    if not sidecar.paths:
        return
    for index, label in ((0, "per-PAS assignment diagnostics"), (1, "assignment summary")):
        yield ArtifactExpectation(
            sidecar.stage,
            sidecar.task_base,
            f"{sidecar.label} {label}",
            tuple(str(unit_diagnostics_paths(Path(path))[index]) for path in sidecar.paths),
            mode="one",
        )


def _peak_unit_count_expectation(
    *,
    record: ArtifactHealthTaskRecordV2,
    config: Mapping[str, Any],
    layout: RunLayout,
    args: Namespace,
    final_item: Mapping[str, Any],
    quantification_mode: str,
) -> ArtifactExpectation:
    sid = record.identity_value("sid") or record.identity_value("sample_id")
    ctg = record.identity_value("ctg") or record.identity_value("contig")
    label = f"{sid}.{ctg} peak count table" if sid and ctg else "unit peak count table"
    if not sid or not ctg:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="DONE identity is missing sid or ctg",
        )
    dedup_method = str(config.get("dedup_method") or "")
    if not dedup_method:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail="run config is missing dedup_method",
        )
    unit_row, unresolved_detail = _find_finalized_unit_input_row(
        layout,
        dedup_method=dedup_method,
        sid=sid,
        ctg=ctg,
    )
    if unit_row is None:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail=unresolved_detail,
        )
    if not unit_row.unit_id or not unit_row.dedup_status:
        return ArtifactExpectation(
            record.stage,
            record.task_base,
            label,
            (),
            unresolved_detail=(
                "matched finalized unit input row is missing unit_id or dedup_status"
            ),
        )
    item = ScItem(
        uid=unit_row.unit_id,
        sid=sid,
        ctg=ctg,
        dedup_status=unit_row.dedup_status,
        add_dedup_method=True,
        args=args,
    )
    file_name = item.format_name(
        suffix=quantification_artifact_suffix(
            "peak-counts.tsv.gz", quantification_mode
        )
    )
    return ArtifactExpectation(
        record.stage,
        record.task_base,
        label,
        tuple(
            str(root / "unit_counts" / file_name)
            for root in layout.CountMatrixTempConfigSubCandidates(final_item)
        ),
        mode="one",
    )


def _find_finalized_unit_input_row(
    layout: RunLayout,
    *,
    dedup_method: str,
    sid: str,
    ctg: str,
) -> tuple[FinalizedUnitInputRow | None, str]:
    """Read one finalized unit row without creating layout directories."""

    loaded = load_finalized_unit_input_table(finalized_unit_input_table_path(layout, dedup_method=dedup_method))
    if loaded.table is None:
        return None, loaded.detail
    return find_finalized_unit_input_row(loaded.table, sid=sid, ctg=ctg)


def _evaluate_expectation(expectation: ArtifactExpectation) -> ArtifactHealthEntry:
    if expectation.unresolved_detail:
        path = expectation.paths[0] if expectation.paths else ""
        finding = _finding(
            "warning",
            "unresolved",
            expectation,
            path,
            detail=expectation.unresolved_detail,
        )
        return ArtifactHealthEntry(
            expectation=expectation,
            status="unresolved",
            findings=(finding,),
        )

    paths = tuple(Path(path).expanduser() for path in expectation.paths)
    ready = tuple(str(path) for path in paths if path.exists() and path.stat().st_size > 0)
    empty = tuple(str(path) for path in paths if path.exists() and path.stat().st_size == 0)
    missing = tuple(str(path) for path in paths if not path.exists())
    if expectation.allow_empty:
        ready = ready + empty
        empty = ()
    findings: list[ArtifactHealthFinding] = []

    if expectation.mode == "one":
        existing_count = len(ready) + len(empty)
        if existing_count > 1:
            status = "unresolved"
            for path in (*ready, *empty):
                findings.append(
                    _finding(
                        "warning",
                        "ambiguous",
                        expectation,
                        path,
                        detail=(
                            "multiple mutually exclusive artifact "
                            "candidates exist"
                        ),
                    )
                )
        elif ready:
            status = "ready"
        elif empty:
            status = "empty"
        else:
            status = "missing"
    elif expectation.mode == "any":
        if ready:
            status = "ready"
        elif empty:
            status = "empty"
        else:
            status = "missing"
    else:
        if missing:
            status = "missing"
        elif empty:
            status = "empty"
        else:
            status = "ready"

    if status == "missing":
        for path in missing:
            findings.append(_finding("warning", "missing", expectation, path))
    elif status == "empty":
        for path in empty:
            findings.append(_finding("warning", "empty", expectation, path))

    for path in paths:
        if path.name.endswith(".skipped.json") and path.exists() and path.stat().st_size > 0:
            findings.extend(_validate_skip_manifest(path, expectation))

    return ArtifactHealthEntry(
        expectation=expectation,
        status=status,
        ready_paths=ready,
        missing_paths=missing,
        empty_paths=empty,
        findings=tuple(findings),
    )


def _validate_skip_manifest(path: Path, expectation: ArtifactExpectation) -> tuple[ArtifactHealthFinding, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return (
            _finding(
                "warning",
                "invalid_skip_manifest",
                expectation,
                str(path),
                detail=f"could not parse JSON: {exc}",
            ),
        )
    long_path = payload.get("long_gene_cell_path")
    if not long_path:
        return (
            _finding(
                "warning",
                "invalid_skip_manifest",
                expectation,
                str(path),
                detail="missing long_gene_cell_path",
            ),
        )
    target = Path(str(long_path)).expanduser()
    if not target.exists():
        return (
            _finding(
                "warning",
                "skip_manifest_target_missing",
                expectation,
                str(path),
                detail=f"long_gene_cell_path does not exist: {target}",
            ),
        )
    if target.stat().st_size == 0:
        return (
            _finding(
                "warning",
                "skip_manifest_target_empty",
                expectation,
                str(path),
                detail=f"long_gene_cell_path is empty: {target}",
            ),
        )
    return ()


def _finding(
    level: FindingLevel,
    issue_type: str,
    expectation: ArtifactExpectation,
    path: str,
    *,
    detail: str = "",
) -> ArtifactHealthFinding:
    return ArtifactHealthFinding(
        level=level,
        issue_type=issue_type,
        stage=expectation.stage,
        task_base=expectation.task_base,
        label=expectation.label,
        path=str(path),
        detail=detail,
    )


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


def _resolve_run_root(
    db_path: Path,
    config: Mapping[str, Any],
    *,
    root: str | Path | None,
) -> Path:
    if root is not None:
        return Path(root).expanduser()
    output_dir = config.get("output_dir")
    run_id = config.get("run_id")
    if output_dir not in (None, "") and run_id not in (None, ""):
        return RunLayout(out=Path(str(output_dir)).expanduser(), run_id=str(run_id)).Root
    if db_path.parent.name == "state":
        return db_path.parent.parent
    return db_path.parent


def _layout_from_config_or_root(config: Mapping[str, Any], run_root: Path) -> RunLayout:
    output_dir = config.get("output_dir")
    run_id = config.get("run_id")
    if output_dir not in (None, "") and run_id not in (None, ""):
        return RunLayout(out=Path(str(output_dir)).expanduser(), run_id=str(run_id))
    return RunLayout(out=run_root.parent, run_id=run_root.name)


def _config_with_root(config: Mapping[str, Any], run_root: Path) -> dict[str, Any]:
    out = dict(config)
    out.setdefault("output_dir", str(run_root.parent))
    out.setdefault("run_id", run_root.name)
    return out


def _namespace_from_config(config: Mapping[str, Any]) -> Namespace:
    return Namespace(**dict(config))
