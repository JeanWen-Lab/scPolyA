from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Sequence, Tuple

from .artifact_naming import (
    ArtifactNameError,
    ScopedArtifactNameV1,
    load_artifact_name_manifest_v1,
)
from .define import (
    INPUTS_CSV_NAME,
    PREPROCESS_INPUTS_CSV_NAME,
    UNIT_INPUTS_CSV_NAME,
)
from .rerun_preview import RerunPreview
from .run_layout import (
    ARG_BULK_GENE_GTF,
    ARG_BULK_WINDOW_GTF,
    ARG_CHROM_SIZES,
    ARG_GENE_GTF,
    BULK_GENE_BED,
    BULK_WINDOW_BED,
    CANONICAL_TRANSCRIPTS,
    CDS_BED,
    CDS_GTF,
    FILTERED_ADJUSTED_BULK_GENE_GTF,
    FILTERED_ADJUSTED_BULK_WINDOW_GTF,
    FILTERED_ADJUSTED_GTF,
    FILTERED_STD_CHROM_SIZES,
    GENE_BED,
    GFFDB,
    SORTED_BULK_GENE_BED,
    SORTED_BULK_WINDOW_BED,
    SORTED_CDS_BED,
    SORTED_GENE_BED,
    SORTED_STOP_CODON_BED,
    SORTED_STOP_CODON_BIGBED,
    STOP_CODON_POSITIONS,
    STOP_CODON_POSITIONS_TSV,
    UNFILTERED_ADJUSTED_BULK_GENE_GTF,
    UNFILTERED_ADJUSTED_BULK_WINDOW_GTF,
    UNFILTERED_ADJUSTED_GTF,
    UNFILTERED_STD_CHROM_SIZES,
    VALID_REF_KEYS,
    RunLayout,
)
from .task_specs import TASK_SPECS
from quantification_config import (
    READABLE_TERMINAL_ANNOTATION_SCHEMAS,
    TERMINAL_ANNOTATION_SCHEMA_IDENTITY,
    resolved_quantification_mode,
)
from utils.terminal_quant_utils import quantification_artifact_suffix


OUTPUT_PROJECTION_SCHEMA_V1 = "scpolya.output_projection.v1"
PHYSICAL_OUTPUT_IMPACT_SCHEMA_V1 = "scpolya.physical_output_impact.v1"

TaskKey = Tuple[str, str]
OutputPathKind = Literal["file", "directory", "glob"]
OutputRetention = Literal["primary", "retained_intermediate"]
OutputImpactAction = Literal[
    "replace_in_scope",
    "write_new_scope",
    "newly_enabled",
    "no_longer_generated",
]


@dataclass(frozen=True)
class OutputArtifactNameV1:
    scope: str
    short_code: str
    legacy_name: str
    payload: Mapping[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "short_code": self.short_code,
            "legacy_name": self.legacy_name,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class OutputScopeV1:
    scope_id: str
    label: str
    path: str
    path_kind: OutputPathKind
    retention: OutputRetention
    producer_task_keys: Tuple[TaskKey, ...]
    config_keys: Tuple[str, ...] = ()
    artifact_names: Tuple[OutputArtifactNameV1, ...] = ()
    conditional_on_producer_execution: bool = False
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "label": self.label,
            "path": self.path,
            "path_kind": self.path_kind,
            "retention": self.retention,
            "producer_task_keys": [
                {"stage": stage, "task_base": task_base}
                for stage, task_base in self.producer_task_keys
            ],
            "config_keys": self.config_keys,
            "artifact_names": [name.as_dict() for name in self.artifact_names],
            "conditional_on_producer_execution": self.conditional_on_producer_execution,
            "note": self.note,
        }


@dataclass(frozen=True)
class RunOutputProjectionV1:
    run_root: str | None
    naming_mode: Literal["short", "legacy"] | None
    scopes: Tuple[OutputScopeV1, ...]
    warnings: Tuple[str, ...] = ()
    excluded_categories: Tuple[str, ...] = (
        "logs",
        "state_and_control_plane",
        "success_deleted_scratch",
    )
    schema_version: str = OUTPUT_PROJECTION_SCHEMA_V1

    @property
    def by_scope_id(self) -> Dict[str, OutputScopeV1]:
        return {scope.scope_id: scope for scope in self.scopes}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_root": self.run_root,
            "naming_mode": self.naming_mode,
            "scope_count": len(self.scopes),
            "scopes": [scope.as_dict() for scope in self.scopes],
            "warnings": self.warnings,
            "excluded_categories": self.excluded_categories,
        }


@dataclass(frozen=True)
class OutputScopeImpactV1:
    scope_id: str
    label: str
    action: OutputImpactAction
    old_scope: OutputScopeV1 | None
    new_scope: OutputScopeV1 | None
    producer_task_keys: Tuple[TaskKey, ...]
    task_impact_keys: Tuple[TaskKey, ...]
    conditional_on_producer_execution: bool = False
    note: str = ""

    @property
    def path(self) -> str:
        scope = self.new_scope or self.old_scope
        return "" if scope is None else scope.path

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "label": self.label,
            "action": self.action,
            "path": self.path,
            "old_scope": None if self.old_scope is None else self.old_scope.as_dict(),
            "new_scope": None if self.new_scope is None else self.new_scope.as_dict(),
            "producer_task_keys": [
                {"stage": stage, "task_base": task_base}
                for stage, task_base in self.producer_task_keys
            ],
            "task_impact_keys": [
                {"stage": stage, "task_base": task_base}
                for stage, task_base in self.task_impact_keys
            ],
            "conditional_on_producer_execution": self.conditional_on_producer_execution,
            "note": self.note,
        }


@dataclass(frozen=True)
class PhysicalOutputImpactPreviewV1:
    old_projection: RunOutputProjectionV1
    new_projection: RunOutputProjectionV1
    impacts: Tuple[OutputScopeImpactV1, ...]
    schema_version: str = PHYSICAL_OUTPUT_IMPACT_SCHEMA_V1

    @property
    def by_scope_id(self) -> Dict[str, OutputScopeImpactV1]:
        return {impact.scope_id: impact for impact in self.impacts}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "impact_count": len(self.impacts),
            "old_projection": self.old_projection.as_dict(),
            "new_projection": self.new_projection.as_dict(),
            "impacts": [impact.as_dict() for impact in self.impacts],
        }


STATE_ONLY_OUTPUT_TASKS_V1 = frozenset(
    {
        ("__global__", "__meta__"),
        ("calculate_umi", "calculate_umi"),
        ("annotate_peaks", "extract_genomic_features"),
        ("annotate_peaks", "classify_peaks"),
    }
)


TASK_OUTPUT_SCOPE_IDS_V1: Mapping[TaskKey, Tuple[str, ...]] = {
    ("register_inputs", "copy_inputs_csv"): ("input_tables.inputs",),
    ("register_inputs", "parse_and_link"): ("inputs.links",),
    ("register_inputs", "write_preprocess_csv"): ("input_tables.preprocess",),
    ("init_references", "backup_gtf"): (
        "backup.gene_gtf",
        "backup.bulk_window_gtf",
        "backup.bulk_gene_gtf",
    ),
    ("init_references", "adjust_chrom_sizes"): (
        f"reference.{UNFILTERED_STD_CHROM_SIZES}",
    ),
    ("init_references", "filter_chrom_sizes"): (
        f"reference.{FILTERED_STD_CHROM_SIZES}",
    ),
    ("init_references", "check_adjust_gtf"): (
        f"reference.{UNFILTERED_ADJUSTED_GTF}",
        f"reference.{UNFILTERED_ADJUSTED_BULK_WINDOW_GTF}",
        f"reference.{UNFILTERED_ADJUSTED_BULK_GENE_GTF}",
    ),
    ("init_references", "prefilter_validate_gtf"): (
        f"reference.{FILTERED_ADJUSTED_GTF}",
        f"reference.{FILTERED_ADJUSTED_BULK_WINDOW_GTF}",
        f"reference.{FILTERED_ADJUSTED_BULK_GENE_GTF}",
    ),
    ("init_references", "gtf_to_bed"): (
        f"reference.{GENE_BED}",
        f"reference.{BULK_WINDOW_BED}",
        f"reference.{BULK_GENE_BED}",
    ),
    ("init_references", "sort_gene_bed"): (
        f"reference.{SORTED_GENE_BED}",
        f"reference.{SORTED_BULK_WINDOW_BED}",
        f"reference.{SORTED_BULK_GENE_BED}",
    ),
    ("init_references", "build_gffdb"): (f"reference.{GFFDB}",),
    ("init_references", "cache_canonical_transcripts"): (
        f"reference.{CANONICAL_TRANSCRIPTS}",
    ),
    ("init_references", "cache_stop_codon"): (
        f"reference.{STOP_CODON_POSITIONS}",
        f"reference.{STOP_CODON_POSITIONS_TSV}",
    ),
    ("init_references", "generate_stop_codon_bigbed"): (
        f"reference.{SORTED_STOP_CODON_BED}",
        f"reference.{SORTED_STOP_CODON_BIGBED}",
    ),
    ("init_references", "prepare_cds"): (
        f"reference.{CDS_GTF}",
        f"reference.{CDS_BED}",
        f"reference.{SORTED_CDS_BED}",
    ),
    ("preprocess_units", "split_to_units"): (
        "unit_outputs.bams.undeduped",
        "unit_outputs.bams.external",
    ),
    ("preprocess_units", "filter_units"): (
        "unit_outputs.bams.undeduped",
        "unit_outputs.bams.external",
    ),
    ("preprocess_units", "dedup_units"): (
        "unit_outputs.bams.deduplicated",
        "unit_outputs.dedup_stats",
    ),
    ("preprocess_units", "rewrite_barcode_suffix"): (
        "unit_outputs.bams.barcode_suffix",
    ),
    ("preprocess_units", "finalize_units"): ("input_tables.unit_inputs",),
    ("extract_subsets", "extract_polyA"): ("unit_outputs.bams.polyA",),
    ("extract_subsets", "split_strand"): (
        "unit_outputs.strand_bams.plus",
        "unit_outputs.strand_bams.minus",
    ),
    ("calculate_umi", "count_umi_exp"): ("unit_outputs.umi_counts",),
    ("calculate_umi", "count_umi_polyA"): ("unit_outputs.umi_counts",),
    ("merge_units", "merge_chunks"): (
        "merged_cohort.chunks.expression",
        "merged_cohort.chunks.polyA",
    ),
    ("merge_units", "cat_chunks"): (
        "cohort_output.bams.expression",
        "cohort_output.bams.polyA",
    ),
    ("merge_units", "merge_strand_chunks"): (
        "merged_cohort.strand_chunks.plus",
        "merged_cohort.strand_chunks.minus",
    ),
    ("merge_units", "cat_strand_chunks"): (
        "cohort_output.strand_bams.plus",
        "cohort_output.strand_bams.minus",
    ),
    ("call_peaks", "generate_raw_coverage"): ("cohort_output.raw_coverage",),
    ("call_peaks", "upm_scale_coverage"): ("cohort_output.scaled_coverage",),
    ("call_peaks", "exclude_cds_region"): ("cohort_output.cds_excluded_coverage",),
    ("call_peaks", "negate_bedgraph"): ("cohort_output.negated_coverage",),
    ("call_peaks", "generate_bigwig"): ("cohort_output.bigwigs",),
    ("call_peaks", "merge_call_peaks"): (
        "cohort_output.merged_peaks",
        "cohort_output.dynamic_thresholds",
    ),
    ("call_peaks", "combine_and_intersect_peaks"): (
        "final.filtered_peaks",
        "final.filtered_peaks_merged",
        "final.not_evaluable_peaks_audit",
    ),
    ("annotate_peaks", "assign_peaks_to_genes"): ("final.assigned_peaks",),
    ("annotate_peaks", "merge_assigned_peaks"): ("final.assigned_peaks_pickle",),
    ("annotate_peaks", "resolve_peak_issues"): ("final.annotated_peaks_bed",),
    ("annotate_peaks", "convert_peaks_bed_to_bigbed"): ("final.annotated_peaks_bigbed",),
    ("annotate_peaks", "convert_peaks_bed_to_gtf"): ("final.annotated_peaks_gtf",),
    ("deepcps", "score_and_join"): ("final.deepcps",),
    ("annotate_bams", "annotate_slices"): ("peak_sidecars",),
    ("count_matrix", "count_unit_matrix"): ("count_matrix_temp",),
    ("count_matrix", "summarize_count_matrix"): ("count_matrix",),
    ("rna_gene_matrix", "annotate_gene_units"): ("rna_gene_matrix_temp.sidecars",),
    ("rna_gene_matrix", "count_gene_units"): ("rna_gene_matrix_temp.unit_counts",),
    ("rna_gene_matrix", "summarize_gene_matrix"): ("rna_gene_matrix",),
    ("summarize_matrix", "summarize_matrix"): ("matrix_summary",),
}


_SCOPE_ORDER = (
    "input_tables.inputs",
    "inputs.links",
    "input_tables.preprocess",
    "backup.gene_gtf",
    "backup.bulk_window_gtf",
    "backup.bulk_gene_gtf",
    *(f"reference.{key}" for key in VALID_REF_KEYS),
    "unit_outputs.bams.undeduped",
    "unit_outputs.bams.external",
    "unit_outputs.bams.deduplicated",
    "unit_outputs.bams.barcode_suffix",
    "unit_outputs.bams.polyA",
    "unit_outputs.strand_bams.plus",
    "unit_outputs.strand_bams.minus",
    "unit_outputs.umi_counts",
    "unit_outputs.dedup_stats",
    "input_tables.unit_inputs",
    "merged_cohort.chunks.expression",
    "merged_cohort.chunks.polyA",
    "merged_cohort.strand_chunks.plus",
    "merged_cohort.strand_chunks.minus",
    "cohort_output.bams.expression",
    "cohort_output.bams.polyA",
    "cohort_output.strand_bams.plus",
    "cohort_output.strand_bams.minus",
    "cohort_output.raw_coverage",
    "cohort_output.scaled_coverage",
    "cohort_output.cds_excluded_coverage",
    "cohort_output.negated_coverage",
    "cohort_output.bigwigs",
    "cohort_output.merged_peaks",
    "cohort_output.dynamic_thresholds",
    "final.filtered_peaks",
    "final.filtered_peaks_merged",
    "final.not_evaluable_peaks_audit",
    "final.assigned_peaks",
    "final.assigned_peaks_pickle",
    "final.annotated_peaks_bed",
    "final.annotated_peaks_bigbed",
    "final.annotated_peaks_gtf",
    "final.deepcps",
    "peak_sidecars",
    "count_matrix_temp",
    "count_matrix",
    "matrix_summary",
    "rna_gene_matrix_temp.sidecars",
    "rna_gene_matrix_temp.unit_counts",
    "rna_gene_matrix",
)
_KNOWN_SCOPE_IDS = frozenset(_SCOPE_ORDER)
_SCOPE_INDEX = {scope_id: index for index, scope_id in enumerate(_SCOPE_ORDER)}


def validate_output_projection_contract() -> None:
    registry_keys = {spec.key for spec in TASK_SPECS}
    mapped_keys = set(TASK_OUTPUT_SCOPE_IDS_V1)
    state_only_keys = set(STATE_ONLY_OUTPUT_TASKS_V1)
    missing = sorted(registry_keys - mapped_keys - state_only_keys)
    extra = sorted((mapped_keys | state_only_keys) - registry_keys)
    overlap = sorted(mapped_keys & state_only_keys)
    unknown_scopes = sorted(
        {
            scope_id
            for scope_ids in TASK_OUTPUT_SCOPE_IDS_V1.values()
            for scope_id in scope_ids
            if scope_id not in _KNOWN_SCOPE_IDS
        }
    )
    unowned_scopes = sorted(
        _KNOWN_SCOPE_IDS
        - {
            scope_id
            for scope_ids in TASK_OUTPUT_SCOPE_IDS_V1.values()
            for scope_id in scope_ids
        }
    )
    if missing or extra or overlap or unknown_scopes or unowned_scopes:
        raise AssertionError(
            "Output projection contract mismatch. "
            f"missing={missing}; extra={extra}; overlap={overlap}; "
            f"unknown_scopes={unknown_scopes}; unowned_scopes={unowned_scopes}"
        )


def project_run_outputs_v1(
    config: Mapping[str, Any] | Namespace,
    *,
    effective_params: Mapping[str, Any] | None = None,
    annotation_schema: Literal["current", "recorded"] = "current",
) -> RunOutputProjectionV1:
    """Project a config's physical outputs.

    ``annotation_schema="current"`` describes what this code would write.
    Readers of existing outputs pass ``"recorded"``: the terminal annotation
    schema then comes from the run's own artifact records, and a missing
    record falls back to current only because nothing was written yet.
    """
    if annotation_schema not in ("current", "recorded"):
        raise ValueError(f"Unsupported annotation_schema: {annotation_schema!r}")
    raw = _to_mapping(config)
    params = dict(effective_params or _effective_params(raw))
    warnings: list[str] = []
    output_dir = raw.get("output_dir")
    run_id = raw.get("run_id")
    if output_dir in (None, "") or run_id in (None, ""):
        missing = [
            key
            for key, value in (("output_dir", output_dir), ("run_id", run_id))
            if value in (None, "")
        ]
        warnings.append(
            "Physical output projection requires " + ", ".join(missing)
        )
        return RunOutputProjectionV1(
            run_root=None,
            naming_mode=None,
            scopes=(),
            warnings=tuple(warnings),
        )

    layout = RunLayout(
        out=Path(str(output_dir)).expanduser(),
        run_id=str(run_id),
        artifact_naming_mode=_optional_string(raw.get("artifact_naming_mode")),
    )
    try:
        naming_mode = layout.ArtifactNamingMode
    except ArtifactNameError as exc:
        warnings.append(
            "Physical output namespace could not be projected: "
            f"{type(exc).__name__}: {exc}"
        )
        return RunOutputProjectionV1(
            run_root=str(layout.Root),
            naming_mode=None,
            scopes=(),
            warnings=tuple(warnings),
        )
    scopes: list[OutputScopeV1] = []

    def add_scope(
        scope_id: str,
        *,
        label: str,
        path: Path | str,
        path_kind: OutputPathKind,
        retention: OutputRetention,
        config_keys: Sequence[str] = (),
        artifact_names: Sequence[OutputArtifactNameV1] = (),
        conditional_on_producer_execution: bool = False,
        note: str = "",
    ) -> None:
        scopes.append(
            OutputScopeV1(
                scope_id=scope_id,
                label=label,
                path=str(path),
                path_kind=path_kind,
                retention=retention,
                producer_task_keys=_producer_tasks(scope_id),
                config_keys=tuple(config_keys),
                artifact_names=tuple(artifact_names),
                conditional_on_producer_execution=conditional_on_producer_execution,
                note=note,
            )
        )

    add_scope(
        "input_tables.inputs",
        label="Registered input table",
        path=layout.InputTables / INPUTS_CSV_NAME,
        path_kind="file",
        retention="retained_intermediate",
        config_keys=("inputs",),
    )
    add_scope(
        "inputs.links",
        label="Registered BAM and index links",
        path=layout.Inputs,
        path_kind="directory",
        retention="retained_intermediate",
        config_keys=("inputs",),
        note="Variable cardinality follows the configured inputs table.",
    )
    add_scope(
        "input_tables.preprocess",
        label="Preprocess input table",
        path=layout.InputTables / PREPROCESS_INPUTS_CSV_NAME,
        path_kind="file",
        retention="retained_intermediate",
        config_keys=("inputs",),
    )

    for key, label in (
        (ARG_GENE_GTF, "Gene GTF backup"),
        (ARG_BULK_WINDOW_GTF, "Bulk-window GTF backup"),
        (ARG_BULK_GENE_GTF, "Bulk-gene GTF backup"),
    ):
        source = raw.get(key)
        if source in (None, ""):
            if key == ARG_GENE_GTF:
                warnings.append("gene_gtf is required to project reference outputs")
            continue
        add_scope(
            f"backup.{key}",
            label=label,
            path=layout.Backup / Path(str(source)).expanduser().name,
            path_kind="file",
            retention="retained_intermediate",
            config_keys=(key,),
        )

    args = Namespace(**raw)
    for reference_key, (_, source_key) in VALID_REF_KEYS.items():
        source = raw.get(source_key)
        if source in (None, ""):
            if source_key in (ARG_CHROM_SIZES, ARG_GENE_GTF):
                warning = f"{source_key} is required to project {reference_key}"
                if warning not in warnings:
                    warnings.append(warning)
            continue
        add_scope(
            f"reference.{reference_key}",
            label=_reference_label(reference_key),
            path=layout.ReferenceFile(reference_key, args),
            path_kind="file",
            retention="retained_intermediate",
            config_keys=_reference_config_keys(reference_key, source_key),
        )

    for status, label in (
        ("undeduped", "Materialized undeduplicated per-unit BAMs and indexes"),
        ("external", "Materialized externally deduplicated per-unit BAMs and indexes"),
    ):
        add_scope(
            f"unit_outputs.bams.{status}",
            label=label,
            path=layout.UnitOutputsDir / "**" / f"*.{status}.bam*",
            path_kind="glob",
            retention="retained_intermediate",
            config_keys=("inputs", "contigs"),
            note=(
                f"Matches only *.{status}.bam and *.{status}.bam.bai files; "
                "plus/minus compatibility BAM names cannot match this scope."
            ),
        )
    dedup_method = raw.get("dedup_method")
    if dedup_method in (None, ""):
        warnings.append("dedup_method is required to project deduplicated unit BAMs")
    else:
        add_scope(
            "unit_outputs.bams.deduplicated",
            label="Pipeline-deduplicated per-unit BAMs and indexes",
            path=layout.UnitOutputsDir / "**" / f"*.{dedup_method}.bam*",
            path_kind="glob",
            retention="retained_intermediate",
            config_keys=("inputs", "contigs", "dedup_method", "cb_tag", "umi_tag"),
            note=(
                "The exact dedup-method suffix prevents plus/minus compatibility BAMs "
                "from matching this scope."
            ),
        )
    add_scope(
        "unit_outputs.bams.barcode_suffix",
        label="Barcode-suffix rewritten per-unit BAMs and indexes",
        path=layout.UnitOutputsDir / "**" / "*.cb-sfx-*.bam*",
        path_kind="glob",
        retention="retained_intermediate",
        config_keys=("inputs", "contigs", "dedup_method", "cb_tag"),
        note="Matches only retained barcode-suffix rewrite BAMs and indexes.",
    )
    poly_a_status = params.get("polyA_status")
    if poly_a_status in (None, ""):
        warnings.append("PolyA parameters are required to project unstranded unit PolyA BAMs")
    else:
        add_scope(
            "unit_outputs.bams.polyA",
            label="Unstranded per-unit PolyA BAMs and indexes",
            path=layout.UnitOutputsDir / "**" / f"*.{poly_a_status}.bam*",
            path_kind="glob",
            retention="retained_intermediate",
            config_keys=("dedup_method", "minpolyA", "nonA_allowed", "minMAPQ"),
            note=(
                "The exact PolyA-status suffix prevents plus/minus compatibility BAMs "
                "from matching this scope."
            ),
        )
    if _truthy(raw.get("materialize_strand_bams")):
        for strand in ("plus", "minus"):
            add_scope(
                f"unit_outputs.strand_bams.{strand}",
                label=f"Explicit per-unit {strand} compatibility BAMs and indexes",
                path=layout.UnitOutputsDir / "**" / f"*.{strand}.bam*",
                path_kind="glob",
                retention="retained_intermediate",
                config_keys=(
                    "materialize_strand_bams",
                    "dedup_method",
                    "minpolyA",
                    "nonA_allowed",
                    "minMAPQ",
                ),
                note=(
                    f"Matches only *.{strand}.bam and *.{strand}.bam.bai files "
                    "under the per-unit directories."
                ),
            )
    if raw.get("coverage_scaling", "none") != "none":
        add_scope(
            "unit_outputs.umi_counts",
            label="Per-unit expression and PolyA UMI counts",
            path=layout.UnitOutputsDir / "**" / "*.umi_count.txt",
            path_kind="glob",
            retention="retained_intermediate",
            config_keys=("coverage_scaling", "dedup_method", "minpolyA", "nonA_allowed", "minMAPQ"),
        )
    if _truthy(raw.get("dedup_stats")):
        add_scope(
            "unit_outputs.dedup_stats",
            label="UMI deduplication statistics",
            path=layout.UnitOutputsDir / "**" / "*.bam.stats_*.tsv",
            path_kind="glob",
            retention="retained_intermediate",
            config_keys=("dedup_stats", "dedup_method"),
            conditional_on_producer_execution=True,
            note=(
                "Changing dedup_stats does not by itself invalidate completed dedup tasks; "
                "files are written when dedup_units executes."
            ),
        )
    dedup_method = raw.get("dedup_method")
    if dedup_method not in (None, ""):
        add_scope(
            "input_tables.unit_inputs",
            label="Finalized unit input table",
            path=layout.InputTables / UNIT_INPUTS_CSV_NAME(dedup_method),
            path_kind="file",
            retention="retained_intermediate",
            config_keys=("inputs", "contigs", "dedup_method"),
        )
    else:
        warnings.append("dedup_method is required to project finalized unit inputs")

    if _truthy(raw.get("keep_merge_temp")):
        merge_root = raw.get("merge_temp_dir")
        temp_root = (
            Path(str(merge_root)).expanduser()
            if merge_root not in (None, "")
            else layout.MergedCohort
        )
        dedup_method = raw.get("dedup_method")
        poly_a_status = params.get("polyA_status")
        if dedup_method in (None, "") or poly_a_status in (None, ""):
            warnings.append(
                "dedup_method and PolyA parameters are required to project retained merge chunks"
            )
        else:
            for branch, suffix in (
                ("expression", str(dedup_method)),
                ("polyA", f"{dedup_method}.{poly_a_status}"),
            ):
                add_scope(
                    f"merged_cohort.chunks.{branch}",
                    label=f"Retained unstranded {branch} cohort merge chunks",
                    path=temp_root / f"chunk.cohort.*.{suffix}.bam",
                    path_kind="glob",
                    retention="retained_intermediate",
                    config_keys=(
                        "keep_merge_temp",
                        "merge_temp_dir",
                        "contigs",
                        "dedup_method",
                        "minpolyA",
                        "nonA_allowed",
                        "minMAPQ",
                    ),
                    conditional_on_producer_execution=True,
                    note=(
                        "Changing keep_merge_temp does not by itself invalidate completed "
                        "merge tasks; chunks are retained when merge_chunks and cat_chunks execute."
                    ),
                )
        if _truthy(raw.get("materialize_strand_bams")):
            for strand in ("plus", "minus"):
                add_scope(
                    f"merged_cohort.strand_chunks.{strand}",
                    label=f"Retained {strand} compatibility cohort merge chunks",
                    path=temp_root / f"chunk.cohort.*.{strand}.bam",
                    path_kind="glob",
                    retention="retained_intermediate",
                    config_keys=(
                        "materialize_strand_bams",
                        "keep_merge_temp",
                        "merge_temp_dir",
                        "contigs",
                        "dedup_method",
                    ),
                    conditional_on_producer_execution=True,
                    note=(
                        f"Matches only *.{strand}.bam compatibility chunks retained when both "
                        "materialize_strand_bams and keep_merge_temp are enabled."
                    ),
                )

    cohort_keys = ("dedup_method", "coverage_scaling", "minpolyA", "nonA_allowed", "minMAPQ")
    dedup_method = raw.get("dedup_method")
    poly_a_status = params.get("polyA_status")
    if dedup_method in (None, "") or poly_a_status in (None, ""):
        warnings.append(
            "dedup_method and PolyA parameters are required to project unstranded cohort BAMs"
        )
    else:
        for branch, cohort_name in (
            ("expression", f"cohort.{dedup_method}"),
            ("polyA", f"cohort.{dedup_method}.{poly_a_status}"),
        ):
            add_scope(
                f"cohort_output.bams.{branch}",
                label=f"Merged unstranded {branch} cohort BAM and index",
                path=layout.CohortOutputDir / cohort_name / f"{cohort_name}.bam*",
                path_kind="glob",
                retention="retained_intermediate",
                config_keys=("dedup_method", "minpolyA", "nonA_allowed", "minMAPQ"),
                note=(
                    "The terminal wildcard matches only the BAM itself and its .bai index; "
                    "plus/minus compatibility BAM names cannot match this scope."
                ),
            )
    if _truthy(raw.get("materialize_strand_bams")):
        for strand in ("plus", "minus"):
            add_scope(
                f"cohort_output.strand_bams.{strand}",
                label=f"Explicit cohort {strand} compatibility BAMs and indexes",
                path=layout.CohortOutputDir / "**" / f"*.{strand}.bam*",
                path_kind="glob",
                retention="retained_intermediate",
                config_keys=(
                    "materialize_strand_bams",
                    "dedup_method",
                    "minpolyA",
                    "nonA_allowed",
                    "minMAPQ",
                ),
                note=(
                    f"Matches only *.{strand}.bam and *.{strand}.bam.bai compatibility files; "
                    "coverage does not consume them."
                ),
            )
    add_scope(
        "cohort_output.raw_coverage",
        label="Raw cohort coverage tracks",
        path=layout.CohortOutputDir / "**" / "*.coverage.bedgraph",
        path_kind="glob",
        retention="retained_intermediate",
        config_keys=cohort_keys,
    )
    if raw.get("coverage_scaling", "none") != "none":
        add_scope(
            "cohort_output.scaled_coverage",
            label="Scaled cohort coverage tracks",
            path=layout.CohortOutputDir / "**" / "*.coverage.scaled.bedgraph",
            path_kind="glob",
            retention="retained_intermediate",
            config_keys=cohort_keys,
        )
    add_scope(
        "cohort_output.cds_excluded_coverage",
        label="CDS-excluded expression coverage tracks",
        path=layout.CohortOutputDir / "**" / "*.coverage*.removeCDS.bedgraph",
        path_kind="glob",
        retention="retained_intermediate",
        config_keys=cohort_keys,
    )
    add_scope(
        "cohort_output.negated_coverage",
        label="Negated minus-strand coverage tracks",
        path=layout.CohortOutputDir / "**" / "*.coverage*.negate.bedgraph",
        path_kind="glob",
        retention="retained_intermediate",
        config_keys=cohort_keys,
    )
    add_scope(
        "cohort_output.bigwigs",
        label="Cohort BigWig tracks",
        path=layout.CohortOutputDir / "**" / "*.coverage*.bw",
        path_kind="glob",
        retention="primary",
        config_keys=cohort_keys,
    )
    add_scope(
        "cohort_output.merged_peaks",
        label="Strand-specific merged peaks",
        path=layout.CohortOutputDir / "**" / "*peaks-merged*.bed",
        path_kind="glob",
        retention="retained_intermediate",
        config_keys=cohort_keys + ("merge_distance", "merge_distance_polyA"),
    )
    if (
        raw.get("exp_peak_threshold_mode", "fixed") == "dynamic"
        or raw.get("polyA_peak_threshold_mode", "fixed") == "dynamic"
    ):
        add_scope(
            "cohort_output.dynamic_thresholds",
            label="Dynamic peak thresholds",
            path=layout.CohortOutputDir / "**" / "*.dynamic-threshold.tsv",
            path_kind="glob",
            retention="retained_intermediate",
            config_keys=(
                "exp_peak_threshold_mode",
                "polyA_peak_threshold_mode",
                "exp_dynamic_threshold_stat",
                "polyA_dynamic_threshold_stat",
                "exp_dynamic_threshold_alpha",
                "polyA_dynamic_threshold_alpha",
                "exp_dynamic_threshold_floor",
                "polyA_dynamic_threshold_floor",
                "exp_dynamic_threshold_normalized",
                "polyA_dynamic_threshold_normalized",
            ),
        )

    final_item = _final_peak_item(raw, params)
    if (
        final_item is not None
        and annotation_schema == "recorded"
        and resolved_quantification_mode(final_item) == "terminal-aware"
    ):
        recorded_schema, schema_warning = recorded_terminal_annotation_schema(
            layout, final_item
        )
        if schema_warning:
            warnings.append(ANNOTATION_SCHEMA_NOT_OPENED + schema_warning)
        elif recorded_schema is not None:
            final_item["terminal_annotation_schema"] = recorded_schema
    if final_item is None:
        warnings.append("Peak naming configuration is incomplete; final and peak-derived scopes were omitted")
    else:
        peak_record = layout.PeakNameRecord(final_item)
        annotation_record = layout.AnnotationNameRecord(final_item)
        peak_names = (_artifact_name(peak_record),)
        annotation_names = peak_names + (_artifact_name(annotation_record),)
        quantification_mode = resolved_quantification_mode(raw)
        filtered_candidates = layout.FinalOutputFileCandidates(
            suffix=quantification_artifact_suffix(
                "___peaks-mispriming-filtered.bed",
                quantification_mode,
            ),
            d=final_item,
        )
        filtered_path = filtered_candidates[0]
        filtered_label = (
            "Mispriming-audited peaks"
            if quantification_mode == "terminal-aware"
            else "Mispriming-filtered peaks"
        )
        add_scope(
            "final.filtered_peaks",
            label=filtered_label,
            path=filtered_path,
            path_kind="file",
            retention="primary",
            config_keys=_PEAK_CONFIG_KEYS,
            artifact_names=peak_names,
        )
        add_scope(
            "final.filtered_peaks_merged",
            label=f"Merged {filtered_label.lower()}",
            path=filtered_path.with_name(f"{filtered_path.stem}-merged.bed"),
            path_kind="file",
            retention="primary",
            config_keys=_PEAK_CONFIG_KEYS,
            artifact_names=peak_names,
        )
        add_scope(
            "final.not_evaluable_peaks_audit",
            label="Peaks without evaluable same-strand tail evidence (audit only)",
            path=filtered_path.with_name(f"{filtered_path.stem}-not-evaluable.audit.bed"),
            path_kind="file",
            retention="primary",
            config_keys=_PEAK_CONFIG_KEYS,
            artifact_names=peak_names,
            note="Always emitted by current code; an empty file means no not-evaluable peaks.",
        )
        annotated_outputs = (
            (
                "final.assigned_peaks",
                "Assigned peaks",
                "___peaks-assigned.bed",
            ),
            (
                "final.assigned_peaks_pickle",
                "Assigned peaks with stop-codon annotations",
                "___peaks-assigned-with-stop-codon.df.pkl",
            ),
            (
                "final.annotated_peaks_bed",
                "Resolved annotated peaks BED",
                "___peaks-annotated.bed",
            ),
            (
                "final.annotated_peaks_bigbed",
                "Resolved annotated peaks BigBed",
                "___peaks-annotated.bb",
            ),
            (
                "final.annotated_peaks_gtf",
                "Resolved annotated peaks GTF",
                "___peaks-annotated.gtf",
            ),
        )
        for scope_id, label, suffix in annotated_outputs:
            add_scope(
                scope_id,
                label=label,
                path=layout.AnnotatedFinalOutputFileCandidates(
                    suffix=quantification_artifact_suffix(
                        suffix,
                        quantification_mode,
                    ),
                    d=final_item,
                )[0],
                path_kind="file",
                retention="primary",
                config_keys=_ANNOTATION_CONFIG_KEYS,
                artifact_names=annotation_names,
            )

        if _truthy(raw.get("deepcps")):
            add_scope(
                "final.deepcps",
                label="DeepCPS candidate scores, joined sites and provenance",
                path=layout.AnnotatedFinalOutputFileCandidates(
                    suffix=quantification_artifact_suffix("___peaks-annotated.bed", quantification_mode),
                    d=final_item,
                )[0].parent / "deepcps" / "*" / "*",
                path_kind="glob",
                retention="primary",
                config_keys=_ANNOTATION_CONFIG_KEYS + (
                    "deepcps", "reference_fasta", "deepcps_model_dir", "deepcps_model_manifest",
                    "deepcps_reference_sha256", "deepcps_threads", "deepcps_batch_size",
                ),
                note="Content fingerprint directories bind actual inputs/model bytes; scores never replace counting BEDs.",
            )

        annotate_outputs = any(
            _truthy(raw.get(key))
            for key in ("annotate_bam", "count_matrix", "summarize_matrix")
        )
        count_outputs = any(
            _truthy(raw.get(key))
            for key in ("count_matrix", "summarize_matrix")
        )
        if annotate_outputs:
            add_scope(
                "peak_sidecars",
                label="Per-unit peak assignment sidecars",
                path=layout.PeakSidecarsConfigSubCandidates(final_item)[0],
                path_kind="directory",
                retention="retained_intermediate",
                config_keys=_ANNOTATION_CONFIG_KEYS + ("annotate_bam", "count_matrix", "summarize_matrix"),
                artifact_names=annotation_names,
                note="Variable cardinality follows samples and contigs.",
            )
        if count_outputs:
            add_scope(
                "count_matrix_temp",
                label="Per-unit peak count tables",
                path=layout.CountMatrixTempConfigSubCandidates(final_item)[0],
                path_kind="directory",
                retention="retained_intermediate",
                config_keys=_ANNOTATION_CONFIG_KEYS + ("count_matrix", "summarize_matrix"),
                artifact_names=annotation_names,
                note="Variable cardinality follows samples and contigs.",
            )
            add_scope(
                "count_matrix",
                label="Per-sample peak count matrices",
                path=layout.CountMatrixConfigSubCandidates(final_item)[0],
                path_kind="directory",
                retention="primary",
                config_keys=_ANNOTATION_CONFIG_KEYS + ("count_matrix", "summarize_matrix"),
                artifact_names=annotation_names,
                note="Variable cardinality follows samples.",
            )
        if _truthy(raw.get("summarize_matrix")):
            psi_min_gene_umis = raw.get("psi_min_gene_umis")
            if psi_min_gene_umis in (None, ""):
                warnings.append("psi_min_gene_umis is required to project matrix_summary")
            else:
                matrix_record = layout.MatrixNameRecord(
                    final_item,
                    psi_min_gene_umis=psi_min_gene_umis,
                )
                add_scope(
                    "matrix_summary",
                    label="Peak matrix summaries",
                    path=layout.MatrixSummaryConfigSubCandidates(
                        final_item,
                        psi_min_gene_umis=psi_min_gene_umis,
                    )[0],
                    path_kind="directory",
                    retention="primary",
                    config_keys=_SUMMARY_CONFIG_KEYS + ("summarize_matrix",),
                    artifact_names=annotation_names + (_artifact_name(matrix_record),),
                    note=(
                        "Contains per-sample PSI, gene-cell, dense matrix, and weighted 3' UTR outputs; "
                        "terminal-aware mode also contains versioned per-site and gene-total iPUI tables."
                    ),
                )

    if _truthy(raw.get("rna_gene_matrix")):
        rna_item = _rna_gene_item(raw)
        if rna_item is None:
            warnings.append("dedup_method, cb_tag, and umi_tag are required to project RNA gene matrices")
        else:
            rna_record = layout.RnaGeneNameRecord(rna_item)
            rna_names = (_artifact_name(rna_record),)
            temp_root = layout.RnaGeneMatrixTempSubCandidates(rna_item)[0]
            add_scope(
                "rna_gene_matrix_temp.sidecars",
                label="Per-unit gene assignment sidecars",
                path=temp_root / "sidecars",
                path_kind="directory",
                retention="retained_intermediate",
                config_keys=("rna_gene_matrix", "dedup_method", "cb_tag", "umi_tag"),
                artifact_names=rna_names,
                note="Variable cardinality follows samples and contigs.",
            )
            add_scope(
                "rna_gene_matrix_temp.unit_counts",
                label="Per-unit RNA gene count tables",
                path=temp_root / "unit_counts",
                path_kind="directory",
                retention="retained_intermediate",
                config_keys=("rna_gene_matrix", "dedup_method", "cb_tag", "umi_tag"),
                artifact_names=rna_names,
                note="Variable cardinality follows samples and contigs.",
            )
            add_scope(
                "rna_gene_matrix",
                label="Per-sample RNA gene matrices",
                path=layout.RnaGeneMatrixSubCandidates(rna_item)[0],
                path_kind="directory",
                retention="primary",
                config_keys=("rna_gene_matrix", "dedup_method", "cb_tag", "umi_tag"),
                artifact_names=rna_names,
                note="Dense matrix creation may be represented by a retained skip manifest when size limits apply.",
            )

    scope_ids = [scope.scope_id for scope in scopes]
    duplicates = sorted({scope_id for scope_id in scope_ids if scope_ids.count(scope_id) > 1})
    unknown = sorted(set(scope_ids) - _KNOWN_SCOPE_IDS)
    if duplicates or unknown:
        raise AssertionError(
            f"Invalid projected output scopes. duplicates={duplicates}; unknown={unknown}"
        )
    if any(w.startswith(ANNOTATION_SCHEMA_NOT_OPENED) for w in warnings):
        # Never hand readers current-schema paths for a refused recorded
        # schema: keep only scopes that do not depend on the annotation.
        scopes = [
            scope for scope in scopes
            if not any(name.scope == "annotation" for name in scope.artifact_names)
        ]
    scopes.sort(key=lambda scope: _SCOPE_INDEX[scope.scope_id])
    return RunOutputProjectionV1(
        run_root=str(layout.Root),
        naming_mode=naming_mode,
        scopes=tuple(scopes),
        warnings=tuple(warnings),
    )


def preview_physical_output_impact_v1(
    old_config: Mapping[str, Any] | Namespace,
    new_config: Mapping[str, Any] | Namespace,
    *,
    rerun_preview: RerunPreview,
    old_effective_params: Mapping[str, Any] | None = None,
    new_effective_params: Mapping[str, Any] | None = None,
) -> PhysicalOutputImpactPreviewV1:
    old_projection = project_run_outputs_v1(
        old_config,
        effective_params=old_effective_params,
        annotation_schema="recorded",
    )
    new_projection = project_run_outputs_v1(
        new_config,
        effective_params=new_effective_params,
    )
    old_by_id = old_projection.by_scope_id
    new_by_id = new_projection.by_scope_id
    task_impacts = set(rerun_preview.impacted_task_keys)
    scope_ids = sorted(
        set(old_by_id) | set(new_by_id),
        key=lambda scope_id: _SCOPE_INDEX[scope_id],
    )
    impacts: list[OutputScopeImpactV1] = []
    for scope_id in scope_ids:
        old_scope = old_by_id.get(scope_id)
        new_scope = new_by_id.get(scope_id)
        producer_tasks = _ordered_task_union(
            () if old_scope is None else old_scope.producer_task_keys,
            () if new_scope is None else new_scope.producer_task_keys,
        )
        impacted_tasks = tuple(key for key in producer_tasks if key in task_impacts)
        action: OutputImpactAction | None = None
        if old_scope is None:
            action = "newly_enabled"
        elif new_scope is None:
            action = "no_longer_generated"
        elif _scope_namespace_changed(old_scope, new_scope):
            action = "write_new_scope"
        elif impacted_tasks:
            action = "replace_in_scope"
        if action is None:
            continue

        active_scope = new_scope or old_scope
        assert active_scope is not None
        conditional = bool(
            (old_scope and old_scope.conditional_on_producer_execution)
            or (new_scope and new_scope.conditional_on_producer_execution)
        )
        note = active_scope.note
        if action in ("write_new_scope", "no_longer_generated"):
            retention_note = "Files in the previous scope are not deleted automatically."
            note = f"{note} {retention_note}".strip()
        impacts.append(
            OutputScopeImpactV1(
                scope_id=scope_id,
                label=active_scope.label,
                action=action,
                old_scope=old_scope,
                new_scope=new_scope,
                producer_task_keys=producer_tasks,
                task_impact_keys=impacted_tasks,
                conditional_on_producer_execution=conditional,
                note=note,
            )
        )

    return PhysicalOutputImpactPreviewV1(
        old_projection=old_projection,
        new_projection=new_projection,
        impacts=tuple(impacts),
    )


_PEAK_CONFIG_KEYS = (
    "dedup_method",
    "minpolyA",
    "nonA_allowed",
    "minMAPQ",
    "coverage_scaling",
    "exp_peak_threshold_mode",
    "polyA_peak_threshold_mode",
    "merge_distance",
    "merge_distance_polyA",
    "misprime_A_count",
    "misprime_in",
    "misprimed_ratio_threshold",
    "quantification_mode",
)
_ANNOTATION_CONFIG_KEYS = _PEAK_CONFIG_KEYS + ("annotate_peak_source",)
_SUMMARY_CONFIG_KEYS = _ANNOTATION_CONFIG_KEYS + (
    "psi_min_gene_umis",
    "intronic_misprimed_ratio_threshold",
)


def _effective_params(config: Mapping[str, Any]) -> Mapping[str, Any]:
    from .run_config_preview import build_effective_config_projection

    return build_effective_config_projection(config).preview_params


ANNOTATION_SCHEMA_NOT_OPENED = "Annotation-scoped outputs omitted: "
_LEGACY_ANNOTATION_DIR_LIMIT = 256
ANNOTATION_SCHEMA_NEW_ROOT_ADVICE = (
    f"To run {TERMINAL_ANNOTATION_SCHEMA_IDENTITY} annotation, use a new run "
    "root (a new --output-dir or --run-id) and keep the old root for its "
    "recorded schema; existing files are never rewritten, copied, migrated or "
    "deleted"
)


def terminal_annotation_root_notice(
    layout: RunLayout,
    config: Mapping[str, Any] | Namespace,
) -> str:
    """Pre-run notice when this run root records another annotation schema for this config.

    Read-only, and uses the same evidence as the readers
    (``recorded_terminal_annotation_schema``): it speaks only when the readers
    would refuse the root, or when the recorded schema is readable but not
    current, so continuing here would leave a mixed-schema root.
    """
    from .parameter_studies import final_peak_item_for_config

    values = config if isinstance(config, Mapping) else vars(config)
    if resolved_quantification_mode(values) != "terminal-aware":
        return ""
    try:
        final_item = final_peak_item_for_config(dict(values))
    except (KeyError, TypeError, ValueError, ArtifactNameError):
        return ""  # incomplete or invalid config: validated elsewhere
    if final_item is None:
        return ""
    recorded, warning = recorded_terminal_annotation_schema(layout, final_item)
    if warning:
        advice = "" if ANNOTATION_SCHEMA_NEW_ROOT_ADVICE in warning else (
            f" {ANNOTATION_SCHEMA_NEW_ROOT_ADVICE}."
        )
        return (
            f"Readers already refuse this run root's annotation outputs ({warning})."
            f"{advice}"
        )
    if recorded in (None, TERMINAL_ANNOTATION_SCHEMA_IDENTITY):
        return ""
    return (
        f"This run root already records terminal annotation outputs for "
        f"{recorded}. Continuing it with "
        f"{TERMINAL_ANNOTATION_SCHEMA_IDENTITY} writes new outputs beside them, "
        "and readers (GUI tables, IGV tracks, site scores, CS1 coverage) then "
        "refuse the root as mixed-schema. "
        f"{ANNOTATION_SCHEMA_NEW_ROOT_ADVICE}."
    )


def recorded_terminal_annotation_schema(
    layout: RunLayout,
    final_item: Mapping[str, Any],
) -> tuple[str | None, str]:
    """Return the run's recorded terminal annotation schema for this config.

    Evidence is the artifact-name manifest's annotation records for this exact
    peak payload and annotation source, plus legacy-named annotation
    directories. Returns ``(schema, "")`` for exactly one readable schema,
    ``(None, "")`` when nothing was recorded, and ``(None, warning)`` for
    unreadable, unknown or conflicting evidence; never a guess.
    """
    schemas, warning = _recorded_annotation_schema_evidence(layout, final_item)
    if warning:
        return None, warning
    if not schemas:
        # Short-named outputs without a manifest are refused by RunLayout.
        return None, ""
    if len(schemas) > 1:
        return None, (
            "Run holds terminal annotation outputs for several schemas "
            f"({', '.join(sorted(s or 'unrecorded' for s in schemas))}); "
            "the current tables are ambiguous, so none were selected and no "
            f"schema was assumed. {ANNOTATION_SCHEMA_NEW_ROOT_ADVICE}"
        )
    (schema,) = schemas
    if schema not in READABLE_TERMINAL_ANNOTATION_SCHEMAS:
        return None, (
            f"Run annotation outputs use schema {schema or 'unrecorded'}, which "
            "this reader does not open as current tables (readable: "
            f"{', '.join(sorted(READABLE_TERMINAL_ANNOTATION_SCHEMAS))})"
        )
    return schema, ""


def _recorded_annotation_schema_evidence(
    layout: RunLayout,
    final_item: Mapping[str, Any],
) -> tuple[set[str], str]:
    """Schemas recorded for this peak payload and source, or a read warning."""
    source = str(final_item.get("annotate_peak_source", "resolved")).strip()
    try:
        peak_record = layout.PeakNameRecord(final_item)
        manifest = load_artifact_name_manifest_v1(layout.ArtifactNameManifest)
    except ArtifactNameError as exc:
        return set(), (
            "Recorded annotation schema could not be read: "
            f"{type(exc).__name__}: {exc}"
        )
    schemas: set[str] = set()
    if manifest is not None:
        for record in manifest.records:
            payload = record.payload
            if (
                record.scope == "annotation"
                and payload.get("peak") == peak_record.payload
                and payload.get("annotate_peak_source") == source
            ):
                schemas.add(str(payload.get("terminal_annotation_schema") or ""))
    legacy_root = layout.FinalOutput / peak_record.legacy_name
    prefix = f"annotate-src-{source}."
    try:
        if legacy_root.is_dir() and not legacy_root.is_symlink():
            for index, child in enumerate(legacy_root.iterdir()):
                if index >= _LEGACY_ANNOTATION_DIR_LIMIT:
                    return set(), (
                        "Legacy annotation directory has too many entries to "
                        "establish a recorded schema"
                    )
                # A bare annotate-src-<source> directory predates schema
                # suffixes: it is an unrecorded terminal annotation.
                if (
                    (child.name.startswith(prefix) or child.name == prefix[:-1])
                    and child.is_dir()
                    and not child.is_symlink()
                ):
                    schemas.add(child.name[len(prefix):])
    except OSError as exc:
        return set(), (
            "Legacy annotation directory could not be read: "
            f"{type(exc).__name__}: {exc}"
        )
    return schemas, ""


# Recorded schemas whose annotated BED the read-only site-score page may locate
# although readers do not open their tables as current. Only the BED's path is
# derived; the page then reads DeepCPS files beside it and verifies them against
# their provenance and the BED digest. No table of this schema is opened.
SITE_SCORE_LOCATABLE_ANNOTATION_SCHEMAS: frozenset[str] = frozenset(
    {"terminal-annotation-v2"}
)


@dataclass(frozen=True)
class RecordedAnnotatedBedV1:
    path: Path | None
    schema: str | None
    refusal: str = ""


def recorded_annotated_peaks_bed_v1(
    config: Mapping[str, Any] | Namespace,
) -> RecordedAnnotatedBedV1:
    """Locate a terminal-aware run's annotated BED under its recorded schema.

    For the site-score reader only. Readable schemas resolve as in
    ``project_run_outputs_v1(..., annotation_schema="recorded")``; schemas in
    ``SITE_SCORE_LOCATABLE_ANNOTATION_SCHEMAS`` also resolve. Unknown, mixed or
    unreadable evidence returns ``path=None`` with the reader's refusal text.
    """
    raw = _to_mapping(config)
    if raw.get("output_dir") in (None, "") or raw.get("run_id") in (None, ""):
        return RecordedAnnotatedBedV1(None, None)
    layout = RunLayout(
        out=Path(str(raw["output_dir"])).expanduser(),
        run_id=str(raw["run_id"]),
        artifact_naming_mode=_optional_string(raw.get("artifact_naming_mode")),
    )
    try:
        final_item = _final_peak_item(raw, _effective_params(raw))
    except (ArtifactNameError, KeyError, TypeError, ValueError):
        return RecordedAnnotatedBedV1(None, None)
    if final_item is None or resolved_quantification_mode(final_item) != "terminal-aware":
        return RecordedAnnotatedBedV1(None, None)
    schemas, warning = _recorded_annotation_schema_evidence(layout, final_item)
    if (
        not warning
        and len(schemas) == 1
        and next(iter(schemas)) in SITE_SCORE_LOCATABLE_ANNOTATION_SCHEMAS
    ):
        schema = next(iter(schemas))
    else:
        schema, warning = recorded_terminal_annotation_schema(layout, final_item)
        if warning:
            return RecordedAnnotatedBedV1(None, None, warning)
    if schema is not None:
        final_item["terminal_annotation_schema"] = schema
    try:
        path = layout.AnnotatedFinalOutputFileCandidates(
            suffix=quantification_artifact_suffix(
                "___peaks-annotated.bed",
                resolved_quantification_mode(raw),
            ),
            d=final_item,
        )[0]
    except ArtifactNameError as exc:
        return RecordedAnnotatedBedV1(None, schema, f"{type(exc).__name__}: {exc}")
    return RecordedAnnotatedBedV1(Path(path), schema)


def _final_peak_item(
    config: Mapping[str, Any],
    params: Mapping[str, Any],
) -> Dict[str, Any] | None:
    required = (
        "dedup_method",
        "polyA_status",
        "coverage_scaling",
        "depth_threshold",
        "depth_threshold_polyA",
        "merge_distance",
        "merge_distance_polyA",
    )
    if any(params.get(key) in (None, "") for key in required):
        return None
    depth_threshold = _paired_value(params["depth_threshold"], "exp")
    merge_distance = _paired_value(params["merge_distance"], "exp")
    if depth_threshold is None or merge_distance is None:
        return None
    item = {
        "dedup_method": params["dedup_method"],
        "polyA_status": params["polyA_status"],
        "coverage_scaling": params["coverage_scaling"],
        "depth_threshold": depth_threshold,
        "depth_threshold_polyA": params["depth_threshold_polyA"],
        "merge_distance": merge_distance,
        "merge_distance_polyA": params["merge_distance_polyA"],
        "misprime_A_count": config.get("misprime_A_count"),
        "misprime_in": config.get("misprime_in"),
        "misprimed_ratio_threshold": config.get("misprimed_ratio_threshold"),
        "annotate_peak_source": config.get("annotate_peak_source") or "resolved",
        "quantification_mode": config.get("quantification_mode") or "legacy",
        "intronic_misprimed_ratio_threshold": config.get(
            "intronic_misprimed_ratio_threshold",
            0.5,
        ),
    }
    required_item = (
        "misprime_A_count",
        "misprime_in",
        "misprimed_ratio_threshold",
    )
    if any(item[key] in (None, "") for key in required_item):
        return None
    return item


def _rna_gene_item(config: Mapping[str, Any]) -> Dict[str, Any] | None:
    keys = ("dedup_method", "cb_tag", "umi_tag")
    if any(config.get(key) in (None, "") for key in keys):
        return None
    return {key: config[key] for key in keys}


def _paired_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == key:
                return item[1]
    return None


def _artifact_name(record: ScopedArtifactNameV1) -> OutputArtifactNameV1:
    return OutputArtifactNameV1(
        scope=record.scope,
        short_code=record.code,
        legacy_name=record.legacy_name,
        payload=dict(record.payload),
    )


def _producer_tasks(scope_id: str) -> Tuple[TaskKey, ...]:
    return tuple(
        spec.key
        for spec in TASK_SPECS
        if scope_id in TASK_OUTPUT_SCOPE_IDS_V1.get(spec.key, ())
    )


def _ordered_task_union(*groups: Sequence[TaskKey]) -> Tuple[TaskKey, ...]:
    values = {key for group in groups for key in group}
    return tuple(spec.key for spec in TASK_SPECS if spec.key in values)


def _scope_namespace_changed(old: OutputScopeV1, new: OutputScopeV1) -> bool:
    return (
        old.path != new.path
        or old.path_kind != new.path_kind
        or old.retention != new.retention
        or old.artifact_names != new.artifact_names
    )


def _reference_config_keys(reference_key: str, source_key: str) -> Tuple[str, ...]:
    if reference_key in (UNFILTERED_STD_CHROM_SIZES, FILTERED_STD_CHROM_SIZES):
        return (source_key, "species", "remove_mt", "contigs")
    return (source_key, "species", "remove_mt", "contigs")


def _reference_label(reference_key: str) -> str:
    return "Reference: " + reference_key.replace("_", " ")


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _to_mapping(config: Mapping[str, Any] | Namespace) -> Dict[str, Any]:
    if isinstance(config, Namespace):
        return dict(vars(config))
    return dict(config)
