# task_manage/define.py
from __future__ import annotations

from typing import Dict, Set, Literal, Mapping, Any, Tuple

# region Structure
#
Params = Mapping[str, Any]
GLOBAL_META_STAGE = "__global__"
GLOBAL_META_TASK = "__meta__"

# endregion


# region: Input tables (files under {Root}/input_tables/) =====================
INPUTS_CSV_NAME = "inputs.csv"
PREPROCESS_INPUTS_CSV_NAME = "preprocess_inputs.csv"
UNIT_INPUTS_CSV_NAME = lambda x: f"unit_inputs.{x}.csv"
# endregion


# region: Conventions
FLAG_UNDEDUPED = "undeduped"
FLAG_EXTERNAL = "external"
SOURCE_TYPE_FILE = "file"
SOURCE_TYPE_DIR = "dir"
# endregion


# region Meta/Cache keys
META_BAM_FILES_HAS_CHR_PREFIX = "bam_has_chr_prefix"
META_STANDARD_CHROMOSOMES = "standard_chroms"
CACHE_CANONICAL_TRANSCRIPTS = "canonical_transcripts"
CACHE_STOP_CODON_POSITIONS = "stop_codon_positions"
CACHE_unit_inputs = "unit_inputs_csv"
CACHE_polyA_status = "polyA_status"
CACHE_dedup_method = "dedup_method"
CACHE_upm_factor = "upm_factor"
CACHE_upm_factor_polyA = "upm_factor_polyA"
CACHE_MERGED_PEAKS_DF = "merged_peaks_df"
CACHE_FEATURES_DICT = "features_dict"
CACHE_ANNOTATED_MERGED_PEAKS_DF = "annotated_merged_peaks_df"
# endregion


def COHORT_STRAND_NAME(dedup_method, polyA_status=None, strand=None):
    if polyA_status is not None:
        parts = polyA_status.split("-")
        if len(parts) != 4 or parts[0] != "polyA" or not all(p.isdigit() for p in parts[1:]):
            raise ValueError(f"Invalid polyA_status format: '{polyA_status}'. Expected 'polyA-INT-INT-INT'.")
    if strand is not None and strand not in ['plus', 'minus']:
        raise ValueError(f"Invalid strand: {strand}, expected 'plus' or 'minus'.")

    cat_list = ["cohort"] + [_ for _ in [dedup_method, polyA_status, strand] if _ is not None]

    return ".".join(cat_list)


# =============================================================================
# Stages & tasks
# =============================================================================
StageLevel = Literal["global", "sample", "pp_unit", "unit"]

# ----------------------------------------
# Stage -> allowed task_base names
# ----------------------------------------
STAGE_TASKS: Dict[str, Set[str]] = {
    "__global__": {"__meta__"},
    "init_references": {
        "backup_gtf",
        "adjust_chrom_sizes",
        "filter_chrom_sizes",
        "check_adjust_gtf",
        "prefilter_validate_gtf",
        "gtf_to_bed",
        "sort_gene_bed",
        "build_gffdb",
        "cache_canonical_transcripts",
        "cache_stop_codon",
        "generate_stop_codon_bigbed",
        "prepare_cds"
    },

    # global: create input_tables + inputs symlinks (do once)
    "register_inputs": {
        "copy_inputs_csv",
        "parse_and_link",
        "write_preprocess_csv",
    },

    "preprocess_units": {
        "split_to_units",
        "filter_units",
        "dedup_units",
        "rewrite_barcode_suffix",
        "finalize_units",
    },
    "extract_subsets": {
        "extract_polyA",  # processed.bam -> processed.polyA.bam
        "split_strand",  # processed BAMs -> plus/minus BAMs
    },

    "calculate_umi": {
        "calculate_umi",
        "count_umi_exp",
        "count_umi_polyA",
    },

    "merge_units": {
        "merge_chunks",
        "cat_chunks",
        "merge_strand_chunks",
        "cat_strand_chunks",
    },
    "call_peaks": {
        "generate_raw_coverage",
        "upm_scale_coverage",
        "exclude_cds_region",
        "negate_bedgraph",
        "generate_bigwig",
        "merge_call_peaks",
        "combine_and_intersect_peaks",
    },
    "annotate_peaks": {
        "assign_peaks_to_genes",
        "merge_assigned_peaks",
        "extract_genomic_features",
        "classify_peaks",
        "resolve_peak_issues",
        "convert_peaks_bed_to_bigbed",
        "convert_peaks_bed_to_gtf",
    },
    "deepcps": {"score_and_join"},
    "annotate_bams": {
        "annotate_slices",
    },
    "count_matrix": {
        "count_unit_matrix",
        "summarize_count_matrix",
    },
    "rna_gene_matrix": {
        "annotate_gene_units",
        "count_gene_units",
        "summarize_gene_matrix"
    },
    "summarize_matrix": {
        "summarize_matrix"
    },

}

STAGE_LEVEL = {
    "__global__": "global",
    "init_references": "global",
    "register_inputs": "global",
    "preprocess_units": "unit",
    "extract_subsets": "unit",
    "merge_units": "unit",
    "calculate_umi": "global",
    "call_peaks": "unit",
    "annotate_peaks": "global",
    "deepcps": "global",
    "annotate_bams": "unit",
    "count_matrix": "sample",
    "rna_gene_matrix": "sample",
    "summarize_matrix": "sample",
}
Pair = Tuple[str, str]  # (sample_id, contig)
# ----------------------------------------
# Parameter scopes: which keys affect variant_str
# ----------------------------------------
# If present: variant_str keeps only these keys.
# (So “param expanding” won’t accidentally change identity.)
TASK_PARAM_SCOPES: Dict[str, Set[str]] = {
    # "call_peak": {"th"},
    # "annotate_peaks": {"th"},
}

# ----------------------------------------
# Resume guard: immutable config keys
# ----------------------------------------
# Keys that define the "structure" of a run; changing them should usually require a new run dir/db.
IMMUTABLE_CONFIG_KEYS: Set[str] = {
    # core reference identity
    "species",
    "contigs",
    "remove_mt",  # or remove_chrM (keep one consistent name in args->config mapping)
    "gtf",
    "fasta",
    "chrom_sizes",
    "ref_dir",
    "bulk_window_gtf",
    "bulk_gene_gtf",
    # tags affect dedup semantics (structural)
    "cell_tag",
    "umi_tag",
}

# region deprecated

# META_GTF_HAS_CHR_PREFIX = "gtf_has_chr_prefix"
# META_GENE_BED_PATH = "gene_bed_path"
# META_filtered_chroms_path = "filtered_chrom_sizes_path"


# META_sorted_bed_path = "sorted_bed_path"
# META_sorted_cds_bed_path = "sorted_CDS_bed_path"
# META_unit_inputs = "unit_inputs_path"
# META_adjusted_gtf_path = "adjusted_gtf_path"
# META_filtered_gtf_path = "filtered_gtf_path"
# META_gffdb_path = "gffdb_path"
# CACHE_cohort_status = "cohort_status"
# CACHE_std_chrom_sizes_path = "standard_chroms_sizes_path"
# CACHE_GFFDB = "gffdb"
# CACHE_cohort_polyA_status = "cohort_polyA_status"
# CACHE_polyA_ctx = "polyA_context"


# REF_standard_chroms_sizes = "chrom.sizes.standardized"
# REF_filtered_chroms = "chrom.sizes.standardized.filtered"
# REF_adjusted_gtf: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.adjusted.gtf"
# REF_filtered_gtf: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.filtered.gtf"
# REF_gene_bed: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.gene.bed"
# REF_sorted_bed: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.sorted.bed"
# REF_gffdb: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.db"
# REF_canonical_transcripts: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.canonical_transcripts.json"
# REF_stop_codon: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.stop_codon_positions.json"
# REF_stop_codon_tsv: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.stop_codon_positions.tsv"
# REF_sorted_stop_codon_bed: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.stop_codon_positions.sorted.bed"
# REF_sorted_stop_codon_bigbed: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.stop_codon_positions.sorted.bb"
# REF_cds_gft: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.CDS.gtf"
# REF_cds_bed: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.CDS.bed"
# REF_sorted_cds_bed: Callable[[Union[str, Path]], str] = lambda x: f"{Path(x).stem}.CDS.sorted.bed"

# endregion
