from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Literal, Tuple

from .define import STAGE_LEVEL, STAGE_TASKS, StageLevel


ExecutionMode = Literal["flex", "inline", "loader", "flex+loader"]


@dataclass(frozen=True)
class TaskSpec:
    stage: str
    task_base: str
    level: StageLevel
    execution_mode: ExecutionMode
    identity_keys: Tuple[str, ...] = ()
    param_keys: Tuple[str, ...] = ()
    consumes: Tuple[str, ...] = ()
    produces: Tuple[str, ...] = ()
    preview_param_bindings: Tuple[Tuple[str, str], ...] = ()
    worker_arg: str | None = None
    ui_group: str | None = None
    notes: str = ""

    @property
    def key(self) -> Tuple[str, str]:
        return self.stage, self.task_base


def _spec(
    stage: str,
    task_base: str,
    *,
    execution_mode: ExecutionMode = "flex",
    identity_keys: Iterable[str] = (),
    param_keys: Iterable[str] = (),
    consumes: Iterable[str] = (),
    produces: Iterable[str] = (),
    preview_param_bindings: Iterable[Tuple[str, str]] = (),
    worker_arg: str | None = None,
    ui_group: str | None = None,
    notes: str = "",
) -> TaskSpec:
    return TaskSpec(
        stage=stage,
        task_base=task_base,
        level=STAGE_LEVEL[stage],
        execution_mode=execution_mode,
        identity_keys=tuple(identity_keys),
        param_keys=tuple(param_keys),
        consumes=tuple(consumes),
        produces=tuple(produces),
        preview_param_bindings=tuple(preview_param_bindings),
        worker_arg=worker_arg,
        ui_group=ui_group or stage,
        notes=notes,
    )


_FINAL_IDENTITY = ("dedup_method", "polyA_status", "coverage_scaling")
_ANNOTATE_PEAK_PARAMS = (
    "depth_threshold",
    "depth_threshold_polyA",
    "merge_distance",
    "merge_distance_polyA",
    "misprime_A_count",
    "misprime_in",
    "misprimed_ratio_threshold",
    "annotate_peak_source",
    "quantification_mode",
)
_CALL_FINAL_PARAMS = (
    "depth_threshold",
    "depth_threshold_polyA",
    "merge_distance",
    "merge_distance_polyA",
    "misprime_A_count",
    "misprime_in",
    "misprimed_ratio_threshold",
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
    "quantification_mode",
)
_PEAK_EXTRA_KEY_BINDING = (("peak_extra_key", "extra_key"),)
_RNA_GENE_EXTRA_KEY_BINDING = (("rna_gene_extra_key", "extra_key"),)
_SUMMARY_EXTRA_KEY_BINDING = (("summary_extra_key", "extra_key"),)


TASK_SPECS: Tuple[TaskSpec, ...] = (
    _spec("__global__", "__meta__", execution_mode="inline", ui_group="state"),
    _spec("register_inputs", "copy_inputs_csv", identity_keys=("uid",), consumes=("args.inputs",), produces=("input_tables/inputs.csv",), worker_arg="fixed_1"),
    _spec(
        "register_inputs",
        "parse_and_link",
        identity_keys=("uid",),
        consumes=("args.inputs",),
        produces=("inputs/*.bam", "task_meta:bam_has_chr_prefix"),
        worker_arg="fixed_1",
    ),
    _spec(
        "register_inputs",
        "write_preprocess_csv",
        identity_keys=("uid",),
        consumes=("args.inputs",),
        produces=("input_tables/preprocess_inputs.csv",),
        worker_arg="fixed_1",
    ),
    _spec("init_references", "backup_gtf", identity_keys=("uid",), consumes=("args.gene_gtf",), produces=("backup/*.gtf",), worker_arg="fixed_1"),
    _spec("init_references", "adjust_chrom_sizes", identity_keys=("uid",), consumes=("args.chrom_sizes", "args.species", "args.remove_mt", "args.contigs", "task_meta:bam_has_chr_prefix"), produces=("reference:unfiltered_standard_chrom_sizes", "task_meta:standard_chroms"), worker_arg="fixed_1"),
    _spec("init_references", "filter_chrom_sizes", identity_keys=("uid",), consumes=("task_meta:standard_chroms",), produces=("reference:filtered_standard_chrom_sizes",), worker_arg="fixed_1"),
    _spec("init_references", "check_adjust_gtf", identity_keys=("uid",), consumes=("inputs/*.bam", "task_meta:bam_has_chr_prefix", "task_meta:standard_chroms", "args.gene_gtf", "args.bulk_window_gtf", "args.bulk_gene_gtf"), produces=("reference:adjusted_gtf",), worker_arg="fixed_1"),
    _spec("init_references", "prefilter_validate_gtf", identity_keys=("uid",), consumes=("reference:adjusted_gtf",), produces=("reference:filtered_adjusted_gtf",), worker_arg="fixed_1"),
    _spec("init_references", "gtf_to_bed", identity_keys=("uid",), consumes=("reference:filtered_adjusted_gtf",), produces=("reference:gene_bed",), worker_arg="fixed_1"),
    _spec("init_references", "sort_gene_bed", identity_keys=("uid",), consumes=("reference:gene_bed", "reference:filtered_standard_chrom_sizes"), produces=("reference:sorted_gene_bed",), worker_arg="fixed_1"),
    _spec("init_references", "build_gffdb", identity_keys=("uid",), consumes=("reference:filtered_adjusted_gtf",), produces=("reference:gffdb",), worker_arg="fixed_1"),
    _spec("init_references", "cache_canonical_transcripts", identity_keys=("uid",), consumes=("reference:gffdb",), produces=("reference:canonical_transcripts", "cache:canonical_transcripts"), worker_arg="fixed_1"),
    _spec("init_references", "cache_stop_codon", identity_keys=("uid",), consumes=("reference:gffdb", "cache:canonical_transcripts"), produces=("reference:stop_codon_positions", "reference:stop_codon_positions_tsv", "cache:stop_codon_positions"), worker_arg="fixed_1"),
    _spec("init_references", "generate_stop_codon_bigbed", identity_keys=("uid",), consumes=("reference:stop_codon_positions_tsv", "reference:filtered_standard_chrom_sizes"), produces=("reference:stop_codon_bigbed",), worker_arg="fixed_1"),
    _spec("init_references", "prepare_cds", identity_keys=("uid",), consumes=("reference:filtered_adjusted_gtf", "reference:filtered_standard_chrom_sizes"), produces=("reference:sorted_cds_bed",), worker_arg="fixed_1"),
    _spec("preprocess_units", "split_to_units", identity_keys=("sid", "ctg"), consumes=("input_tables/preprocess_inputs.csv", "args.force_split_bams", "args.force_dedup_bams"), produces=("unit_outputs/*.bam",), worker_arg="max_split_workers"),
    _spec("preprocess_units", "filter_units", identity_keys=("sid", "ctg"), consumes=("input_tables/preprocess_inputs.csv", "task_meta:standard_chroms"), produces=("unit_outputs/*.bam",), worker_arg="max_filter_workers"),
    _spec("preprocess_units", "dedup_units", identity_keys=("sid", "ctg", "dedup_method"), param_keys=("extra_key",), consumes=("unit_outputs/*.bam", "args.force_split_bams", "args.force_dedup_bams", "args.cb_tag", "args.umi_tag", "args.tools_seed", "args.dedup_stats"), produces=("unit_outputs/*.bam", "cache:dedup_method"), worker_arg="max_dedup_workers"),
    _spec("preprocess_units", "rewrite_barcode_suffix", identity_keys=("sid", "ctg", "dedup_status"), param_keys=("extra_key",), consumes=("unit_outputs/*.bam",), produces=("unit_outputs/*cb-sfx*.bam",), worker_arg="max_filter_workers"),
    _spec("preprocess_units", "finalize_units", execution_mode="inline", consumes=("input_tables/preprocess_inputs.csv", "unit_outputs/*.bam"), produces=("input_tables/unit_inputs.<dedup_method>.csv", "cache:unit_inputs_csv")),
    _spec("extract_subsets", "extract_polyA", identity_keys=("uid", "dedup_method", "polyA_status"), param_keys=("minpolyA", "minMAPQ"), consumes=("cache:unit_inputs_csv",), produces=("unit_outputs/*.polyA*.bam",), worker_arg="max_extract_workers"),
    _spec(
        "extract_subsets",
        "split_strand",
        identity_keys=("uid", "dedup_method", "polyA_status"),
        param_keys=("materialize_strand_bams",),
        consumes=("unit_outputs/*.bam", "args.materialize_strand_bams"),
        produces=("unit_outputs/*strand.bam",),
        worker_arg="max_extract_workers",
        notes="Explicit compatibility output; absent from default execution.",
    ),
    _spec("calculate_umi", "count_umi_exp", identity_keys=("uid", "dedup_method"), consumes=("cache:unit_inputs_csv", "args.coverage_scaling"), produces=("unit_outputs/*.umi_count.txt",), worker_arg="n_workers"),
    _spec("calculate_umi", "count_umi_polyA", identity_keys=("uid", "dedup_method", "polyA_status"), consumes=("unit_outputs/*.polyA*.bam", "args.coverage_scaling"), produces=("unit_outputs/*.umi_count.txt",), worker_arg="n_workers"),
    _spec("calculate_umi", "calculate_umi", execution_mode="inline", consumes=("unit_outputs/*.umi_count.txt", "args.coverage_scaling"), produces=("cache:upm_factor", "cache:upm_factor_polyA", "task_meta:upm_factor")),
    _spec("merge_units", "merge_chunks", identity_keys=("dedup_method", "polyA_status", "strand", "ctg"), consumes=("unit_outputs/*.bam",), produces=("merged_cohort/unstranded_chunk*.bam",), worker_arg="max_merge_workers"),
    _spec("merge_units", "cat_chunks", identity_keys=("dedup_method", "polyA_status", "strand"), consumes=("merged_cohort/unstranded_chunk*.bam",), produces=("cohort_output/unstranded.bam",), worker_arg="max_merge_workers"),
    _spec(
        "merge_units",
        "merge_strand_chunks",
        identity_keys=("dedup_method", "polyA_status", "strand", "ctg"),
        consumes=("unit_outputs/*strand.bam",),
        produces=("merged_cohort/strand_chunk*.bam",),
        worker_arg="max_merge_workers",
        notes="Explicit compatibility output; absent from default execution.",
    ),
    _spec(
        "merge_units",
        "cat_strand_chunks",
        identity_keys=("dedup_method", "polyA_status", "strand"),
        consumes=("merged_cohort/strand_chunk*.bam",),
        produces=("cohort_output/strand.bam",),
        worker_arg="max_merge_workers",
        notes="Explicit compatibility output; absent from default execution.",
    ),
    _spec("call_peaks", "generate_raw_coverage", identity_keys=("strand", "is_polyA", "coverage_source", "strand_filter"), param_keys=("dedup_method", "polyA_status", "read_rule"), consumes=("cohort_output/unstranded.bam",), produces=("cohort_output/*.bedgraph",), worker_arg="n_workers"),
    _spec("call_peaks", "upm_scale_coverage", identity_keys=("strand", "is_polyA", "scaling_factor"), param_keys=("dedup_method", "polyA_status", "read_rule"), consumes=("cohort_output/*.bedgraph", "cache:upm_factor"), produces=("cohort_output/*.scaled.bedgraph",), worker_arg="n_workers"),
    _spec("call_peaks", "exclude_cds_region", identity_keys=("strand", "is_polyA", "scaling_factor"), param_keys=("dedup_method", "polyA_status", "read_rule"), consumes=("cohort_output/*.bedgraph", "reference:sorted_cds_bed"), produces=("cohort_output/*removeCDS*.bedgraph",), worker_arg="n_workers"),
    _spec("call_peaks", "negate_bedgraph", identity_keys=("strand", "is_polyA", "scaling_factor", "remove_CDS"), param_keys=("dedup_method", "polyA_status", "read_rule"), consumes=("cohort_output/*.bedgraph",), produces=("cohort_output/*negate*.bedgraph",), worker_arg="n_workers"),
    _spec("call_peaks", "generate_bigwig", identity_keys=("strand", "is_polyA", "scaling_factor", "remove_CDS", "is_negate"), param_keys=("dedup_method", "polyA_status", "read_rule", "bigwig_conversion_schema"), consumes=("cohort_output/*.bedgraph", "reference:filtered_standard_chrom_sizes"), produces=("cohort_output/*.bw",), worker_arg="n_workers"),
    _spec("call_peaks", "merge_call_peaks", identity_keys=("strand", "is_polyA", "scaling_factor", "remove_CDS"), param_keys=("dedup_method", "polyA_status", "read_rule", "depth_threshold", "merge_distance", "peak_threshold_mode", "dynamic_threshold_stat", "dynamic_threshold_alpha", "dynamic_threshold_floor", "dynamic_threshold_normalized"), consumes=("cohort_output/*.bedgraph", "reference:sorted_gene_bed"), produces=("cohort_output/*peaks-merged*.bed",), worker_arg="n_workers"),
    _spec("call_peaks", "combine_and_intersect_peaks", identity_keys=_FINAL_IDENTITY, param_keys=_CALL_FINAL_PARAMS, consumes=("cohort_output/*peaks-merged*.bed",), produces=("final_output/*peaks-mispriming-*.bed",), worker_arg="n_workers", notes="Mode-aware family: legacy filtered BED or terminal-aware audited BED."),
    _spec("annotate_peaks", "assign_peaks_to_genes", identity_keys=_FINAL_IDENTITY, param_keys=_ANNOTATE_PEAK_PARAMS, consumes=("final_output/*peaks-mispriming-*.bed", "reference:sorted_gene_bed"), produces=("final_output/*peaks-assigned*.bed",), worker_arg="n_workers"),
    _spec("annotate_peaks", "merge_assigned_peaks", execution_mode="flex+loader", identity_keys=_FINAL_IDENTITY, param_keys=_ANNOTATE_PEAK_PARAMS, consumes=("final_output/*peaks-assigned*.bed", "cache:canonical_transcripts", "cache:stop_codon_positions"), produces=("cache:merged_peaks_df", "final_output/*peaks-assigned-with-stop-codon*.df.pkl"), worker_arg="n_workers"),
    _spec("annotate_peaks", "extract_genomic_features", execution_mode="inline", consumes=("reference:gffdb", "cache:canonical_transcripts"), produces=("cache:features_dict",)),
    _spec("annotate_peaks", "classify_peaks", execution_mode="inline", consumes=("cache:merged_peaks_df", "cache:features_dict", "reference:gffdb"), produces=("cache:annotated_merged_peaks_df",)),
    _spec("annotate_peaks", "resolve_peak_issues", identity_keys=_FINAL_IDENTITY, param_keys=_ANNOTATE_PEAK_PARAMS, consumes=("cache:annotated_merged_peaks_df",), produces=("final_output/*peaks-annotated*.bed",), worker_arg="n_workers"),
    _spec("annotate_peaks", "convert_peaks_bed_to_bigbed", identity_keys=_FINAL_IDENTITY, param_keys=_ANNOTATE_PEAK_PARAMS, consumes=("final_output/*peaks-annotated*.bed",), produces=("final_output/*peaks-annotated*.bb",), worker_arg="n_workers"),
    _spec("annotate_peaks", "convert_peaks_bed_to_gtf", identity_keys=_FINAL_IDENTITY, param_keys=_ANNOTATE_PEAK_PARAMS, consumes=("final_output/*peaks-annotated*.bed",), produces=("final_output/*peaks-annotated*.gtf",), worker_arg="n_workers"),
    _spec("deepcps", "score_and_join", execution_mode="inline", identity_keys=("deepcps_fingerprint",), consumes=("final_output/*peaks-annotated*.bed", "args.reference_fasta", "cohort_output/*.bw", "args.deepcps_model_dir"), produces=("final_output/deepcps/scored.tsv", "final_output/deepcps/sites.deepcps.tsv", "final_output/deepcps/provenance.json"), notes="Optional content-bound candidate annotation; inline receipt distinguishes executed/reused inference."),
    _spec("annotate_bams", "annotate_slices", identity_keys=("sid", "ctg"), param_keys=("extra_key",), consumes=("cache:unit_inputs_csv", "final_output/*peaks-annotated*.gtf", "args.cb_tag"), produces=("peak_sidecars/*.xr_to_peak*.tsv.gz", "peak_sidecars/*.xr_to_peak*.assignment*"), preview_param_bindings=_PEAK_EXTRA_KEY_BINDING, worker_arg="n_workers_annotate_or_n_workers"),
    _spec("count_matrix", "count_unit_matrix", identity_keys=("sid", "ctg", "dedup_method"), param_keys=("extra_key",), consumes=("cache:unit_inputs_csv", "peak_sidecars/*.xr_to_peak*.tsv.gz", "args.cb_tag", "args.umi_tag"), produces=("count_matrix_temp/unit_counts/*.tsv.gz",), preview_param_bindings=_PEAK_EXTRA_KEY_BINDING, worker_arg="n_workers"),
    _spec("count_matrix", "summarize_count_matrix", identity_keys=("sid", "dedup_method"), param_keys=("extra_key",), consumes=("count_matrix_temp/unit_counts/*.tsv.gz",), produces=("count_matrix/*.counts*.tsv.gz", "count_matrix/*.peak_assignment*"), preview_param_bindings=_PEAK_EXTRA_KEY_BINDING, worker_arg="n_workers"),
    _spec("rna_gene_matrix", "annotate_gene_units", identity_keys=("sid", "ctg", "dedup_method"), param_keys=("extra_key",), consumes=("cache:unit_inputs_csv", "reference:filtered_adjusted_gtf", "args.cb_tag"), produces=("rna_gene_matrix_temp/sidecars/*.xr_to_gene.tsv.gz",), preview_param_bindings=_RNA_GENE_EXTRA_KEY_BINDING, worker_arg="n_workers_annotate_or_n_workers"),
    _spec("rna_gene_matrix", "count_gene_units", identity_keys=("sid", "ctg", "dedup_method"), param_keys=("extra_key",), consumes=("rna_gene_matrix_temp/sidecars/*.xr_to_gene.tsv.gz", "args.cb_tag", "args.umi_tag"), produces=("rna_gene_matrix_temp/unit_counts/*.tsv.gz",), preview_param_bindings=_RNA_GENE_EXTRA_KEY_BINDING, worker_arg="n_workers"),
    _spec("rna_gene_matrix", "summarize_gene_matrix", identity_keys=("sid", "dedup_method"), param_keys=("extra_key",), consumes=("rna_gene_matrix_temp/unit_counts/*.tsv.gz",), produces=("rna_gene_matrix/*.tsv.gz",), preview_param_bindings=_RNA_GENE_EXTRA_KEY_BINDING, worker_arg="n_workers"),
    _spec(
        "summarize_matrix",
        "summarize_matrix",
        identity_keys=("sid", "dedup_method"),
        param_keys=("extra_key",),
        consumes=("count_matrix/*.counts*.tsv.gz", "final_output/*peaks-annotated*.bed"),
        produces=("matrix_summary/*.tsv.gz", "matrix_summary/*.skipped.json"),
        preview_param_bindings=_SUMMARY_EXTRA_KEY_BINDING,
        worker_arg="n_workers",
        notes=(
            "summary_extra_key conditionally includes quantification_mode and "
            "intronic_misprimed_ratio_threshold for terminal-aware runs. "
            "Terminal-aware summaries also require distal_site_usage.v1 and "
            "the display-only feature_overview.v1; a missing table forces "
            "summary recomputation."
        ),
    ),
)


def task_specs_by_key() -> Dict[Tuple[str, str], TaskSpec]:
    return {spec.key: spec for spec in TASK_SPECS}


def task_specs_by_stage() -> Dict[str, Tuple[TaskSpec, ...]]:
    grouped: Dict[str, list[TaskSpec]] = {}
    for spec in TASK_SPECS:
        grouped.setdefault(spec.stage, []).append(spec)
    return {stage: tuple(specs) for stage, specs in grouped.items()}


def validate_task_specs() -> None:
    specs = task_specs_by_key()
    registry_keys = {
        (stage, task_base)
        for stage, task_bases in STAGE_TASKS.items()
        for task_base in task_bases
    }
    spec_keys = set(specs)
    missing = sorted(registry_keys - spec_keys)
    extra = sorted(spec_keys - registry_keys)
    if missing or extra:
        raise AssertionError(f"TaskSpec registry mismatch. missing={missing}; extra={extra}")

    for spec in TASK_SPECS:
        if spec.stage not in STAGE_LEVEL:
            raise AssertionError(f"Unknown stage in TaskSpec: {spec.stage}")
        if spec.level != STAGE_LEVEL[spec.stage]:
            raise AssertionError(
                f"Stage level mismatch for {spec.stage}:{spec.task_base}; "
                f"spec={spec.level}, registry={STAGE_LEVEL[spec.stage]}"
            )
        invalid_bindings = [
            (preview_key, param_key)
            for preview_key, param_key in spec.preview_param_bindings
            if not preview_key or param_key not in spec.param_keys
        ]
        if invalid_bindings:
            raise AssertionError(
                f"Invalid preview_param_bindings for {spec.stage}:{spec.task_base}: "
                f"{invalid_bindings}"
            )
