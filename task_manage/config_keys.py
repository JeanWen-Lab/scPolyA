from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ConfigCommand = Literal["run", "prepare-bulk"]
ConfigKeyCategory = Literal[
    "path",
    "run_identity",
    "structural",
    "scientific",
    "runtime",
    "output_control",
    "repair_control",
    "compatibility",
]


@dataclass(frozen=True)
class ConfigKeySpec:
    key: str
    categories: tuple[ConfigKeyCategory, ...]
    commands: tuple[ConfigCommand, ...] = ("run",)
    cli_group: str = ""
    include_in_raw_preview: bool = False
    persist_in_run_config: bool = True
    legacy: bool = False
    notes: str = ""

    def has_category(self, category: ConfigKeyCategory) -> bool:
        return category in self.categories

    def applies_to(self, command: ConfigCommand | None) -> bool:
        return command is None or command in self.commands


def _spec(
    key: str,
    categories: tuple[ConfigKeyCategory, ...],
    *,
    commands: tuple[ConfigCommand, ...] = ("run",),
    cli_group: str = "",
    include_in_raw_preview: bool = False,
    persist_in_run_config: bool = True,
    legacy: bool = False,
    notes: str = "",
) -> ConfigKeySpec:
    return ConfigKeySpec(
        key=key,
        categories=categories,
        commands=commands,
        cli_group=cli_group,
        include_in_raw_preview=include_in_raw_preview,
        persist_in_run_config=persist_in_run_config,
        legacy=legacy,
        notes=notes,
    )


CONFIG_KEY_SPECS: tuple[ConfigKeySpec, ...] = (
    _spec("debug", ("runtime",), commands=("run", "prepare-bulk"), cli_group="common"),
    _spec("verbose", ("runtime",), commands=("run", "prepare-bulk"), cli_group="common"),
    _spec(
        "n_workers",
        ("runtime",),
        commands=("run", "prepare-bulk"),
        cli_group="runtime",
        notes=(
            "Active for run; accepted but hidden for prepare-bulk command "
            "compatibility."
        ),
    ),
    _spec(
        "n_threads",
        ("runtime", "compatibility"),
        commands=("run", "prepare-bulk"),
        cli_group="common",
        legacy=True,
        notes="Accepted for historical CLI compatibility; no current consumer.",
    ),
    _spec("plus_bw", ("path", "scientific"), commands=("prepare-bulk",), cli_group="prepare_bulk"),
    _spec("minus_bw", ("path", "scientific"), commands=("prepare-bulk",), cli_group="prepare_bulk"),
    _spec("output_gene_gtf", ("path", "output_control"), commands=("prepare-bulk",), cli_group="prepare_bulk"),
    _spec("output_window_gtf", ("path", "output_control"), commands=("prepare-bulk",), cli_group="prepare_bulk"),
    _spec(
        "output_gtf",
        ("path", "output_control", "compatibility"),
        commands=("prepare-bulk",),
        cli_group="prepare_bulk",
        legacy=True,
    ),
    _spec("output_dir", ("path", "run_identity"), cli_group="identity"),
    _spec("run_id", ("run_identity",), cli_group="identity"),
    _spec("inputs", ("path", "structural"), cli_group="inputs", include_in_raw_preview=True),
    _spec(
        "gene_gtf",
        ("path", "structural"),
        commands=("run", "prepare-bulk"),
        cli_group="inputs",
        include_in_raw_preview=True,
    ),
    _spec("chrom_sizes", ("path", "structural"), cli_group="inputs", include_in_raw_preview=True),
    _spec("bulk_window_gtf", ("path", "structural"), cli_group="inputs", include_in_raw_preview=True),
    _spec("bulk_gene_gtf", ("path", "structural"), cli_group="inputs", include_in_raw_preview=True),
    _spec("species", ("structural",), cli_group="identity", include_in_raw_preview=True),
    _spec(
        "contigs",
        ("structural",),
        cli_group="filtering",
        include_in_raw_preview=True,
        notes="Optional explicit standard-contig scope for bounded runs.",
    ),
    _spec("remove_mt", ("structural", "scientific"), cli_group="filtering", include_in_raw_preview=True),
    _spec("cb_tag", ("structural",), cli_group="tags", include_in_raw_preview=True),
    _spec("umi_tag", ("structural",), cli_group="tags", include_in_raw_preview=True),
    _spec("force_split_bams", ("output_control",), cli_group="preprocess"),
    _spec("max_split_workers", ("runtime",), cli_group="preprocess"),
    _spec("max_filter_workers", ("runtime",), cli_group="preprocess"),
    _spec("force_dedup_bams", ("output_control",), cli_group="preprocess"),
    _spec("dedup_method", ("scientific",), cli_group="preprocess", include_in_raw_preview=True),
    _spec("dedup_stats", ("output_control",), cli_group="preprocess"),
    _spec("max_dedup_workers", ("runtime",), cli_group="preprocess"),
    _spec("minpolyA", ("scientific",), cli_group="polyA", include_in_raw_preview=True),
    _spec("nonA_allowed", ("scientific",), cli_group="polyA", include_in_raw_preview=True),
    _spec("minMAPQ", ("scientific",), cli_group="polyA", include_in_raw_preview=True),
    _spec(
        "count_read_rule",
        ("scientific",),
        cli_group="polyA",
        notes="legacy keeps historical read rules; unified shares one rule for discovery and counting.",
    ),
    _spec(
        "multimapper_policy",
        ("scientific",),
        cli_group="polyA",
        notes="unique or rescued; only with count_read_rule=unified.",
    ),
    _spec(
        "count_assignment",
        ("scientific",),
        cli_group="polyA",
        notes="overlap or three_prime_end; legacy always overlap, unified defaults to three_prime_end.",
    ),
    _spec("max_extract_workers", ("runtime",), cli_group="polyA"),
    _spec(
        "materialize_strand_bams",
        ("output_control",),
        cli_group="polyA",
        include_in_raw_preview=True,
        notes=(
            "Explicit compatibility output switch. Coverage always reads the "
            "unstranded cohort BAMs and is scientifically independent of this key."
        ),
    ),
    _spec("merge_temp_dir", ("path", "runtime"), cli_group="merge"),
    _spec("merge_thread", ("runtime",), cli_group="merge"),
    _spec("keep_merge_temp", ("runtime",), cli_group="merge"),
    _spec("max_merge_workers", ("runtime",), cli_group="merge"),
    _spec("coverage_scaling", ("scientific",), cli_group="peak_calling", include_in_raw_preview=True),
    _spec(
        "call_scaled_peaks",
        ("scientific", "compatibility"),
        cli_group="peak_calling",
        legacy=True,
        notes="Legacy CLI flag normalized into coverage_scaling.",
    ),
    _spec(
        "call_scaled_polyA_peaks",
        ("scientific", "compatibility"),
        cli_group="peak_calling",
        legacy=True,
        notes="Legacy CLI flag normalized into coverage_scaling.",
    ),
    _spec(
        "call_unscaled_peaks",
        ("scientific", "compatibility"),
        cli_group="peak_calling",
        legacy=True,
        notes="Legacy CLI flag normalized into coverage_scaling.",
    ),
    _spec(
        "call_unscaled_polyA_peaks",
        ("scientific", "compatibility"),
        cli_group="peak_calling",
        legacy=True,
        notes="Legacy CLI flag normalized into coverage_scaling.",
    ),
    _spec("exp_peak_threshold_mode", ("scientific",), cli_group="expression_thresholds", include_in_raw_preview=True),
    _spec("polyA_peak_threshold_mode", ("scientific",), cli_group="polyA_thresholds", include_in_raw_preview=True),
    _spec("unscaled_depth_threshold", ("scientific",), cli_group="expression_thresholds", include_in_raw_preview=True),
    _spec("scaled_depth_threshold", ("scientific",), cli_group="expression_thresholds", include_in_raw_preview=True),
    _spec("unscaled_depth_threshold_polyA", ("scientific",), cli_group="polyA_thresholds", include_in_raw_preview=True),
    _spec("scaled_depth_threshold_polyA", ("scientific",), cli_group="polyA_thresholds", include_in_raw_preview=True),
    _spec("exp_dynamic_threshold_stat", ("scientific",), cli_group="expression_thresholds", include_in_raw_preview=True),
    _spec("polyA_dynamic_threshold_stat", ("scientific",), cli_group="polyA_thresholds", include_in_raw_preview=True),
    _spec("exp_dynamic_threshold_alpha", ("scientific",), cli_group="expression_thresholds", include_in_raw_preview=True),
    _spec("polyA_dynamic_threshold_alpha", ("scientific",), cli_group="polyA_thresholds", include_in_raw_preview=True),
    _spec("exp_dynamic_threshold_floor", ("scientific",), cli_group="expression_thresholds", include_in_raw_preview=True),
    _spec("polyA_dynamic_threshold_floor", ("scientific",), cli_group="polyA_thresholds", include_in_raw_preview=True),
    _spec(
        "exp_dynamic_threshold_normalized",
        ("scientific",),
        cli_group="expression_thresholds",
        include_in_raw_preview=True,
    ),
    _spec(
        "polyA_dynamic_threshold_normalized",
        ("scientific",),
        cli_group="polyA_thresholds",
        include_in_raw_preview=True,
    ),
    _spec("merge_distance", ("scientific",), cli_group="peak_calling", include_in_raw_preview=True),
    _spec("merge_distance_polyA", ("scientific",), cli_group="peak_calling", include_in_raw_preview=True),
    _spec("n_workers_annotate", ("runtime",), cli_group="runtime"),
    _spec(
        "n_threads_merge",
        ("runtime", "compatibility"),
        cli_group="runtime",
        legacy=True,
        notes="Accepted for historical CLI compatibility; no current consumer.",
    ),
    _spec("n_threads_bam_annotation", ("runtime",), cli_group="runtime"),
    _spec("tools_seed", ("runtime",), cli_group="runtime"),
    _spec("misprime_A_count", ("scientific",), cli_group="mispriming", include_in_raw_preview=True),
    _spec("misprime_in", ("scientific",), cli_group="mispriming", include_in_raw_preview=True),
    _spec("misprimed_ratio_threshold", ("scientific",), cli_group="mispriming", include_in_raw_preview=True),
    _spec("quantification_mode", ("scientific",), cli_group="quantification"),
    _spec(
        "intronic_misprimed_ratio_threshold",
        ("scientific",),
        cli_group="quantification",
        notes="Behaviorally active only when quantification_mode is terminal-aware.",
    ),
    _spec("deepcps", ("scientific",), cli_group="deepcps", include_in_raw_preview=True),
    _spec("reference_fasta", ("path", "scientific"), cli_group="deepcps", include_in_raw_preview=True),
    _spec("deepcps_model_dir", ("path", "scientific"), cli_group="deepcps", include_in_raw_preview=True),
    _spec("deepcps_model_manifest", ("path", "scientific"), cli_group="deepcps", include_in_raw_preview=True),
    _spec("deepcps_reference_sha256", ("scientific",), cli_group="deepcps", include_in_raw_preview=True),
    _spec("deepcps_threads", ("runtime",), cli_group="deepcps", include_in_raw_preview=True),
    _spec("deepcps_batch_size", ("runtime",), cli_group="deepcps", include_in_raw_preview=True),
    _spec("annotate_bam", ("output_control",), cli_group="downstream", include_in_raw_preview=True),
    _spec("repair_moved_run", ("repair_control",), cli_group="downstream", persist_in_run_config=False),
    _spec("repair_moved_run_dry_run", ("repair_control",), cli_group="downstream", persist_in_run_config=False),
    _spec("count_matrix", ("output_control",), cli_group="downstream", include_in_raw_preview=True),
    _spec("rna_gene_matrix", ("output_control",), cli_group="downstream", include_in_raw_preview=True),
    _spec("summarize_matrix", ("output_control",), cli_group="downstream", include_in_raw_preview=True),
    _spec("psi_min_gene_umis", ("scientific",), cli_group="downstream", include_in_raw_preview=True),
    _spec("annotate_peak_source", ("scientific",), cli_group="downstream", include_in_raw_preview=True),
    _spec(
        "keep_annotate_bam_temp",
        ("runtime", "compatibility"),
        cli_group="downstream",
        legacy=True,
        notes="Deprecated placeholder; sidecars are now persisted instead of annotated BAM temps.",
    ),
    _spec("gtf", ("path", "structural", "compatibility"), legacy=True, notes="Legacy alias for gene_gtf."),
    _spec("cell_tag", ("structural", "compatibility"), legacy=True, notes="Legacy alias for cb_tag."),
    _spec("fasta", ("path", "structural", "compatibility"), legacy=True, notes="Legacy reference key."),
    _spec("ref_dir", ("path", "structural", "compatibility"), legacy=True, notes="Legacy reference directory key."),
)


RAW_PREVIEW_CONFIG_KEYS: tuple[str, ...] = (
    "deepcps", "reference_fasta", "deepcps_model_dir", "deepcps_model_manifest",
    "deepcps_reference_sha256", "deepcps_threads", "deepcps_batch_size",
    "inputs",
    "gene_gtf",
    "chrom_sizes",
    "bulk_window_gtf",
    "bulk_gene_gtf",
    "species",
    "contigs",
    "remove_mt",
    "dedup_method",
    "cb_tag",
    "umi_tag",
    "minpolyA",
    "nonA_allowed",
    "minMAPQ",
    "materialize_strand_bams",
    "coverage_scaling",
    "exp_peak_threshold_mode",
    "polyA_peak_threshold_mode",
    "unscaled_depth_threshold",
    "scaled_depth_threshold",
    "unscaled_depth_threshold_polyA",
    "scaled_depth_threshold_polyA",
    "exp_dynamic_threshold_stat",
    "polyA_dynamic_threshold_stat",
    "exp_dynamic_threshold_alpha",
    "polyA_dynamic_threshold_alpha",
    "exp_dynamic_threshold_floor",
    "polyA_dynamic_threshold_floor",
    "exp_dynamic_threshold_normalized",
    "polyA_dynamic_threshold_normalized",
    "merge_distance",
    "merge_distance_polyA",
    "misprime_A_count",
    "misprime_in",
    "misprimed_ratio_threshold",
    "annotate_peak_source",
    "psi_min_gene_umis",
    "annotate_bam",
    "count_matrix",
    "rna_gene_matrix",
    "summarize_matrix",
)


DERIVED_PREVIEW_KEYS: tuple[str, ...] = (
    "deepcps_fingerprint",
    "polyA_status",
    "scaling_factor",
    "depth_threshold",
    "depth_threshold_polyA",
    "peak_threshold_mode",
    "dynamic_threshold_stat",
    "dynamic_threshold_alpha",
    "dynamic_threshold_floor",
    "dynamic_threshold_normalized",
    "peak_extra_key",
    "rna_gene_extra_key",
    "summary_extra_key",
)


def config_key_specs_by_key() -> dict[str, ConfigKeySpec]:
    specs: dict[str, ConfigKeySpec] = {}
    for spec in CONFIG_KEY_SPECS:
        if spec.key in specs:
            raise AssertionError(f"Duplicate ConfigKeySpec key: {spec.key}")
        specs[spec.key] = spec
    return specs


def config_key_spec(key: str) -> ConfigKeySpec | None:
    return config_key_specs_by_key().get(key)


def config_keys_with_category(
    category: ConfigKeyCategory,
    *,
    command: ConfigCommand | None = None,
    include_legacy: bool = True,
) -> frozenset[str]:
    return frozenset(
        spec.key
        for spec in CONFIG_KEY_SPECS
        if spec.has_category(category)
        and spec.applies_to(command)
        and (include_legacy or not spec.legacy)
    )


def path_config_keys(
    *,
    command: ConfigCommand | None = "run",
    include_legacy: bool = False,
) -> frozenset[str]:
    return config_keys_with_category("path", command=command, include_legacy=include_legacy)


def structural_config_keys(
    *,
    command: ConfigCommand | None = "run",
    include_legacy: bool = True,
) -> frozenset[str]:
    return config_keys_with_category("structural", command=command, include_legacy=include_legacy)


def scientific_config_keys(
    *,
    command: ConfigCommand | None = "run",
    include_legacy: bool = False,
) -> frozenset[str]:
    return config_keys_with_category("scientific", command=command, include_legacy=include_legacy)


def runtime_config_keys(
    *,
    command: ConfigCommand | None = "run",
    include_legacy: bool = False,
) -> frozenset[str]:
    return config_keys_with_category("runtime", command=command, include_legacy=include_legacy)


def raw_preview_config_keys() -> tuple[str, ...]:
    specs = config_key_specs_by_key()
    missing = [key for key in RAW_PREVIEW_CONFIG_KEYS if key not in specs]
    misflagged = [
        key
        for key in RAW_PREVIEW_CONFIG_KEYS
        if key in specs and not specs[key].include_in_raw_preview
    ]
    extra = [
        spec.key
        for spec in CONFIG_KEY_SPECS
        if spec.include_in_raw_preview and spec.key not in RAW_PREVIEW_CONFIG_KEYS
    ]
    if missing or misflagged or extra:
        raise AssertionError(
            "Raw preview config key registry mismatch: "
            f"missing={missing}; misflagged={misflagged}; extra={extra}"
        )
    return RAW_PREVIEW_CONFIG_KEYS


def persisted_run_config_keys(
    *,
    command: ConfigCommand | None = "run",
    include_legacy: bool = True,
) -> frozenset[str]:
    return frozenset(
        spec.key
        for spec in CONFIG_KEY_SPECS
        if spec.persist_in_run_config
        and spec.applies_to(command)
        and (include_legacy or not spec.legacy)
    )


def known_config_or_preview_keys() -> frozenset[str]:
    return frozenset({*(spec.key for spec in CONFIG_KEY_SPECS), *DERIVED_PREVIEW_KEYS})
