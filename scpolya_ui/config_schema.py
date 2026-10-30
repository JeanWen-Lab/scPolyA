from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

from task_manage.runtime_controls import RUNTIME_CONTROL_SPECS
from quantification_config import (
    DEFAULT_INTRONIC_MISPRIMED_RATIO_THRESHOLD,
    DEFAULT_QUANTIFICATION_MODE,
    QUANTIFICATION_MODES,
)

from .errors import UserError


FieldType = Literal["str", "int", "float", "bool", "path", "enum"]


@dataclass(frozen=True)
class ConfigField:
    key: str
    label: str
    section: str
    kind: FieldType
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] = ()
    help: str = ""
    label_key: str = ""
    help_key: str = ""
    minimum: float | int | None = None
    maximum: float | int | None = None


RUNTIME_CONTROL_FIELDS: tuple[ConfigField, ...] = tuple(
    ConfigField(
        spec.key,
        spec.label,
        "Resources",
        "int",
        default=spec.default,
        help=spec.description,
    )
    for spec in RUNTIME_CONTROL_SPECS
)


CONFIG_FIELDS: tuple[ConfigField, ...] = (
    ConfigField("output_dir", "Output directory", "Basic Run", "path", required=True),
    ConfigField("run_id", "Run ID", "Basic Run", "str", required=True),
    ConfigField("species", "Species", "Basic Run", "enum", required=True, choices=("mouse", "human", "fly", "worm")),
    ConfigField(
        "inputs",
        "Inputs CSV",
        "Inputs",
        "path",
        required=True,
        help=(
            "Input rows can declare is_deduped and requires_split. "
            "For an undeduplicated whole BAM, requires_split=0 uses direct "
            "indexed per-contig dedup by default; requires_split=1 materializes "
            "contig BAMs first."
        ),
    ),
    ConfigField("gene_gtf", "Gene GTF", "Inputs", "path", required=True),
    ConfigField("chrom_sizes", "Chrom sizes", "Inputs", "path", required=True),
    ConfigField("bulk_window_gtf", "Bulk window GTF", "Inputs", "path"),
    ConfigField("bulk_gene_gtf", "Bulk gene GTF", "Inputs", "path"),
    ConfigField("cb_tag", "Cell barcode tag", "Tags / Dedup", "str", default="CB"),
    ConfigField("umi_tag", "UMI tag", "Tags / Dedup", "str", default="UB"),
    ConfigField(
        "dedup_method",
        "Dedup method",
        "Tags / Dedup",
        "enum",
        default="directional",
        choices=("directional", "unique", "cluster", "adjacency"),
    ),
    ConfigField(
        "force_split_bams",
        "Split before dedup",
        "Tags / Dedup",
        "bool",
        default=False,
        help=(
            "Off (default): whole BAMs requiring dedup use direct indexed "
            "per-contig umi_tools dedup. On: materialize per-contig BAMs before "
            "dedup for every whole-BAM input. A row with requires_split=1 also "
            "selects this route. Already-deduplicated whole BAMs auto-split "
            "unless force dedup is enabled."
        ),
    ),
    ConfigField(
        "force_dedup_bams",
        "Rededuplicate BAMs",
        "Tags / Dedup",
        "bool",
        default=False,
        help=(
            "Run umi_tools dedup even when an input row declares is_deduped=1. "
            "With split-before-dedup off, an already-deduplicated whole BAM then "
            "uses direct indexed per-contig dedup instead of automatic splitting."
        ),
    ),
    ConfigField("dedup_stats", "Write dedup stats", "Tags / Dedup", "bool", default=False),
    ConfigField("minpolyA", "Min polyA", "Filtering", "int", default=10),
    ConfigField("nonA_allowed", "Non-A allowed", "Filtering", "int", default=1),
    ConfigField("minMAPQ", "Min MAPQ", "Filtering", "int", default=0),
    ConfigField("remove_mt", "Remove mitochondrial reads", "Filtering", "bool", default=False),
    ConfigField(
        "coverage_scaling",
        "Coverage scaling",
        "Peak Calling",
        "enum",
        default="none",
        choices=("none", "expr", "polyA", "both"),
    ),
    ConfigField(
        "exp_peak_threshold_mode",
        "Expr threshold mode",
        "Peak Calling",
        "enum",
        default="fixed",
        choices=("fixed", "dynamic"),
    ),
    ConfigField(
        "polyA_peak_threshold_mode",
        "PolyA threshold mode",
        "Peak Calling",
        "enum",
        default="fixed",
        choices=("fixed", "dynamic"),
    ),
    ConfigField("merge_distance", "Expr merge distance", "Peak Calling", "int", required=True),
    ConfigField("merge_distance_polyA", "PolyA merge distance", "Peak Calling", "int"),
    ConfigField("unscaled_depth_threshold", "Expr unscaled threshold", "Peak Calling", "float"),
    ConfigField("scaled_depth_threshold", "Expr scaled threshold", "Peak Calling", "float"),
    ConfigField("unscaled_depth_threshold_polyA", "PolyA unscaled threshold", "Peak Calling", "float"),
    ConfigField("scaled_depth_threshold_polyA", "PolyA scaled threshold", "Peak Calling", "float"),
    ConfigField(
        "exp_dynamic_threshold_stat",
        "Expr stat",
        "Dynamic Thresholds",
        "enum",
        choices=("median", "mean", "median_nonzero", "max_norm_fixed"),
    ),
    ConfigField(
        "polyA_dynamic_threshold_stat",
        "PolyA stat",
        "Dynamic Thresholds",
        "enum",
        choices=("median", "mean", "median_nonzero", "max_norm_fixed"),
    ),
    ConfigField("exp_dynamic_threshold_alpha", "Expr dynamic alpha", "Dynamic Thresholds", "float", default=1.0),
    ConfigField("polyA_dynamic_threshold_alpha", "PolyA dyn alpha", "Dynamic Thresholds", "float", default=1.0),
    ConfigField("exp_dynamic_threshold_floor", "Expr dynamic floor", "Dynamic Thresholds", "float", default=0.0),
    ConfigField("polyA_dynamic_threshold_floor", "PolyA dyn floor", "Dynamic Thresholds", "float", default=0.0),
    ConfigField("exp_dynamic_threshold_normalized", "Expr norm", "Dynamic Thresholds", "float", default=0.3),
    ConfigField("polyA_dynamic_threshold_normalized", "PolyA norm", "Dynamic Thresholds", "float", default=0.3),
    *RUNTIME_CONTROL_FIELDS,
    ConfigField("merge_temp_dir", "Merge temp directory", "Resources", "path"),
    ConfigField("tools_seed", "Tools seed", "Resources", "int", default=2026),
    ConfigField("debug", "Debug", "Resources", "bool", default=False),
    ConfigField("verbose", "Verbose", "Resources", "bool", default=False),
    ConfigField("misprime_A_count", "Misprime A count", "Mispriming", "int", default=8),
    ConfigField("misprime_in", "Misprime window", "Mispriming", "int", default=10),
    ConfigField(
        "misprimed_ratio_threshold",
        "Misprimed ratio threshold",
        "Mispriming",
        "float",
        default=1,
        minimum=0,
        maximum=1,
    ),
    ConfigField(
        "quantification_mode",
        "Quantification mode",
        "Quantification",
        "enum",
        default=DEFAULT_QUANTIFICATION_MODE,
        choices=QUANTIFICATION_MODES,
        help=(
            "Legacy preserves existing summaries. Terminal-aware enables the "
            "optional terminal-aware quantification profile."
        ),
    ),
    ConfigField(
        "intronic_misprimed_ratio_threshold",
        "Intronic misprimed ratio threshold",
        "Quantification",
        "float",
        default=DEFAULT_INTRONIC_MISPRIMED_RATIO_THRESHOLD,
        help=(
            "Independent [0, 1] threshold used only by terminal-aware "
            "quantification."
        ),
        minimum=0,
        maximum=1,
    ),
    ConfigField("annotate_bam", "Annotate BAM", "Outputs", "bool", default=False),
    ConfigField("count_matrix", "Count matrix", "Outputs", "bool", default=False),
    ConfigField("rna_gene_matrix", "RNA gene matrix", "Outputs", "bool", default=False),
    ConfigField("summarize_matrix", "Summarize matrix", "Outputs", "bool", default=False),
    ConfigField("keep_annotate_bam_temp", "Keep annotation temp", "Outputs", "bool", default=False),
    ConfigField("keep_merge_temp", "Keep merge temp", "Outputs", "bool", default=False),
    ConfigField(
        "materialize_strand_bams",
        "Materialize strand BAMs",
        "Outputs",
        "bool",
        default=False,
        help=(
            "Retain compatibility-only plus/minus BAM and BAI files for each unit "
            "and cohort. This does not change scientific coverage, peaks, or "
            "matrices: coverage always reads unstranded cohort BAMs and applies "
            "bedtools strand filters. Off by default."
        ),
        label_key="config.field.materialize_strand_bams.label",
        help_key="config.field.materialize_strand_bams.help",
    ),
    ConfigField("psi_min_gene_umis", "PSI min gene UMIs", "Outputs", "int", default=1),
    ConfigField(
        "annotate_peak_source",
        "Annotate peak source",
        "Outputs",
        "enum",
        default="resolved",
        choices=("resolved", "merged"),
    ),
    ConfigField("deepcps", "Score sites with DeepCPS", "Site scoring", "bool", default=False),
    ConfigField("reference_fasta", "Alignment reference FASTA", "Site scoring", "path"),
    ConfigField("deepcps_model_dir", "DeepCPS model directory", "Site scoring", "path"),
    ConfigField("deepcps_model_manifest", "DeepCPS model manifest", "Site scoring", "path"),
    ConfigField("deepcps_reference_sha256", "Reference FASTA SHA256", "Site scoring", "str"),
    ConfigField("deepcps_threads", "DeepCPS threads", "Site scoring", "int", default=2, minimum=1, maximum=2),
    ConfigField("deepcps_batch_size", "DeepCPS batch size", "Site scoring", "int", default=128, minimum=1, maximum=512),
    ConfigField("repair_moved_run", "Repair moved run", "Repair", "bool", default=False),
    ConfigField("repair_moved_run_dry_run", "Repair moved run dry-run", "Repair", "bool", default=False),
)

FIELD_BY_KEY = {field.key: field for field in CONFIG_FIELDS}
SECTION_ORDER = tuple(dict.fromkeys(field.section for field in CONFIG_FIELDS))

PEAK_CALLING_FIELD_KEYS: tuple[str, ...] = (
    "coverage_scaling",
    "exp_peak_threshold_mode",
    "polyA_peak_threshold_mode",
    "merge_distance",
    "merge_distance_polyA",
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
)

PEAK_CALLING_ALWAYS_ACTIVE_KEYS: tuple[str, ...] = (
    "coverage_scaling",
    "exp_peak_threshold_mode",
    "polyA_peak_threshold_mode",
    "merge_distance",
    "merge_distance_polyA",
)

THRESHOLD_CONTROLLER_KEYS = frozenset(
    {
        "coverage_scaling",
        "exp_peak_threshold_mode",
        "polyA_peak_threshold_mode",
        "exp_dynamic_threshold_stat",
        "polyA_dynamic_threshold_stat",
    }
)

_CONDITIONAL_THRESHOLD_KEYS = frozenset(
    set(PEAK_CALLING_FIELD_KEYS) - set(PEAK_CALLING_ALWAYS_ACTIVE_KEYS)
)


def inactive_sweep_keys(config: Mapping[str, Any], keys: Iterable[str]) -> list[str]:
    """Threshold keys the base config's modes ignore, so sweeping them changes nothing."""
    active = set(active_peak_calling_keys(config))
    return [key for key in keys if key in _CONDITIONAL_THRESHOLD_KEYS and key not in active]


def fields_by_section() -> dict[str, list[ConfigField]]:
    grouped: dict[str, list[ConfigField]] = {section: [] for section in SECTION_ORDER}
    for field in CONFIG_FIELDS:
        grouped[field.section].append(field)
    return grouped


def active_peak_calling_keys(config: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the Peak Calling controls that affect the current run."""

    coverage_scaling = _configured_choice(config, "coverage_scaling", "none")
    exp_mode = _configured_choice(config, "exp_peak_threshold_mode", "fixed")
    poly_a_mode = _configured_choice(config, "polyA_peak_threshold_mode", "fixed")

    active = set(PEAK_CALLING_ALWAYS_ACTIVE_KEYS)
    active.update(
        _active_threshold_family_keys(
            mode=exp_mode,
            scaled=coverage_scaling in {"expr", "both"},
            fixed_unscaled_key="unscaled_depth_threshold",
            fixed_scaled_key="scaled_depth_threshold",
            stat_key="exp_dynamic_threshold_stat",
            stat_value=_configured_choice(config, "exp_dynamic_threshold_stat", ""),
            alpha_key="exp_dynamic_threshold_alpha",
            floor_key="exp_dynamic_threshold_floor",
            normalized_key="exp_dynamic_threshold_normalized",
        )
    )
    active.update(
        _active_threshold_family_keys(
            mode=poly_a_mode,
            scaled=coverage_scaling in {"polyA", "both"},
            fixed_unscaled_key="unscaled_depth_threshold_polyA",
            fixed_scaled_key="scaled_depth_threshold_polyA",
            stat_key="polyA_dynamic_threshold_stat",
            stat_value=_configured_choice(config, "polyA_dynamic_threshold_stat", ""),
            alpha_key="polyA_dynamic_threshold_alpha",
            floor_key="polyA_dynamic_threshold_floor",
            normalized_key="polyA_dynamic_threshold_normalized",
        )
    )
    return tuple(key for key in PEAK_CALLING_FIELD_KEYS if key in active)


def project_active_run_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build an executable config with inactive threshold fields removed."""

    projected = dict(config)
    active = set(active_peak_calling_keys(config))
    for key in _CONDITIONAL_THRESHOLD_KEYS - active:
        projected.pop(key, None)
    return projected


def _active_threshold_family_keys(
    *,
    mode: str,
    scaled: bool,
    fixed_unscaled_key: str,
    fixed_scaled_key: str,
    stat_key: str,
    stat_value: str,
    alpha_key: str,
    floor_key: str,
    normalized_key: str,
) -> tuple[str, ...]:
    if mode != "dynamic":
        return (fixed_scaled_key if scaled else fixed_unscaled_key,)
    if stat_value == "max_norm_fixed":
        return stat_key, normalized_key
    return stat_key, alpha_key, floor_key


def _configured_choice(config: Mapping[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    return default if value in (None, "") else str(value)


def coerce_field_value(field: ConfigField, value: Any) -> Any:
    if value in ("", None):
        return None
    if field.kind in ("str", "path", "enum"):
        return str(value)
    if field.kind == "bool":
        return bool(value)
    if field.kind == "int":
        # int(20.5) would silently become 20; a fractional entry is a user error instead.
        number = float(value)
        if not number.is_integer():
            raise UserError(
                "config.error.integer_field",
                f"{field.key} must be a whole number, got {value}",
                name=field.key,
                value=str(value),
            )
        return int(number)
    if field.kind == "float":
        return float(value)
    return value


def field_value(config: Mapping[str, Any], field: ConfigField) -> Any:
    if field.key in config:
        return config[field.key]
    return field.default


def overlay_form_values(config: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    for key, raw_value in values.items():
        field = FIELD_BY_KEY[key]
        value = coerce_field_value(field, raw_value)
        if value is None and not field.required:
            merged.pop(key, None)
        elif key not in config and not field.required and field.default is not None and value == field.default:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def unmapped_keys(config: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(str(key) for key in config if str(key) not in FIELD_BY_KEY))


def required_missing(config: Mapping[str, Any]) -> tuple[str, ...]:
    missing = []
    for field in CONFIG_FIELDS:
        if field.required and config.get(field.key) in (None, ""):
            missing.append(field.key)
    return tuple(missing)


def field_keys(fields: Iterable[ConfigField] = CONFIG_FIELDS) -> tuple[str, ...]:
    return tuple(field.key for field in fields)
