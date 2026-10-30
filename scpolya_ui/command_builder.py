from __future__ import annotations

import shlex
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


RUN_SUBCOMMAND = "run"

REQUIRED_RUN_KEYS: tuple[str, ...] = (
    "output_dir",
    "run_id",
    "species",
    "inputs",
    "gene_gtf",
    "chrom_sizes",
    "merge_distance",
)

VALUE_FLAGS: tuple[tuple[str, str], ...] = (
    ("output_dir", "--output-dir"),
    ("run_id", "--run-id"),
    ("species", "--species"),
    ("inputs", "--inputs"),
    ("gene_gtf", "--gene-gtf"),
    ("chrom_sizes", "--chrom-sizes"),
    ("bulk_window_gtf", "--bulk-window-gtf"),
    ("bulk_gene_gtf", "--bulk-gene-gtf"),
    ("cb_tag", "--cb-tag"),
    ("umi_tag", "--umi-tag"),
    ("max_split_workers", "--max-split-workers"),
    ("max_filter_workers", "--max-filter-workers"),
    ("dedup_method", "--dedup-method"),
    ("max_dedup_workers", "--max-dedup-workers"),
    ("minpolyA", "--minpolyA"),
    ("nonA_allowed", "--nonA-allowed"),
    ("minMAPQ", "--minMAPQ"),
    ("max_extract_workers", "--max-extract-workers"),
    ("merge_temp_dir", "--merge-temp-dir"),
    ("merge_thread", "--merge-thread"),
    ("max_merge_workers", "--max-merge-workers"),
    ("coverage_scaling", "--coverage-scaling"),
    ("exp_peak_threshold_mode", "--exp-peak-threshold-mode"),
    ("unscaled_depth_threshold", "--unscaled-depth-threshold"),
    ("scaled_depth_threshold", "--scaled-depth-threshold"),
    ("exp_dynamic_threshold_stat", "--exp-dynamic-threshold-stat"),
    ("exp_dynamic_threshold_alpha", "--exp-dynamic-threshold-alpha"),
    ("exp_dynamic_threshold_floor", "--exp-dynamic-threshold-floor"),
    ("exp_dynamic_threshold_normalized", "--exp-dynamic-threshold-normalized"),
    ("merge_distance", "--merge-distance"),
    ("polyA_peak_threshold_mode", "--polyA-peak-threshold-mode"),
    ("unscaled_depth_threshold_polyA", "--unscaled-depth-threshold-polyA"),
    ("scaled_depth_threshold_polyA", "--scaled-depth-threshold-polyA"),
    ("polyA_dynamic_threshold_stat", "--polyA-dynamic-threshold-stat"),
    ("polyA_dynamic_threshold_alpha", "--polyA-dynamic-threshold-alpha"),
    ("polyA_dynamic_threshold_floor", "--polyA-dynamic-threshold-floor"),
    ("polyA_dynamic_threshold_normalized", "--polyA-dynamic-threshold-normalized"),
    ("merge_distance_polyA", "--merge-distance-polyA"),
    ("n_workers_annotate", "--n-workers-annotate"),
    ("n_threads_merge", "--n-threads-merge"),
    ("n_threads_bam_annotation", "--n-threads-bam-annotation"),
    ("tools_seed", "--tools-seed"),
    ("misprime_A_count", "--misprime-A-count"),
    ("misprime_in", "--misprime-in"),
    ("misprimed_ratio_threshold", "--misprimed-ratio-threshold"),
    ("quantification_mode", "--quantification-mode"),
    (
        "intronic_misprimed_ratio_threshold",
        "--intronic-misprimed-ratio-threshold",
    ),
    ("psi_min_gene_umis", "--psi-min-gene-umis"),
    ("annotate_peak_source", "--annotate-peak-source"),
    ("n_workers", "--n-workers"),
    ("n_threads", "--n-threads"),
    ("reference_fasta", "--reference-fasta"),
    ("deepcps_model_dir", "--deepcps-model-dir"),
    ("deepcps_model_manifest", "--deepcps-model-manifest"),
    ("deepcps_reference_sha256", "--deepcps-reference-sha256"),
    ("deepcps_threads", "--deepcps-threads"),
    ("deepcps_batch_size", "--deepcps-batch-size"),
)

SEQUENCE_FLAGS: tuple[tuple[str, str], ...] = (
    ("contigs", "--contigs"),
)

BOOL_FLAGS: tuple[tuple[str, str], ...] = (
    ("debug", "--debug"),
    ("verbose", "--verbose"),
    ("force_split_bams", "--force-split-bams"),
    ("force_dedup_bams", "--force-dedup-bams"),
    ("dedup_stats", "--dedup-stats"),
    ("remove_mt", "--remove-mt"),
    ("materialize_strand_bams", "--materialize-strand-bams"),
    ("annotate_bam", "--annotate-bam"),
    ("repair_moved_run", "--repair-moved-run"),
    ("repair_moved_run_dry_run", "--repair-moved-run-dry-run"),
    ("count_matrix", "--count-matrix"),
    ("rna_gene_matrix", "--rna-gene-matrix"),
    ("summarize_matrix", "--summarize-matrix"),
    ("keep_annotate_bam_temp", "--keep-annotate-bam-temp"),
    ("keep_merge_temp", "--keep-merge-temp"),
)

DERIVED_OR_LEGACY_KEYS = frozenset(
    {
        "command",
        "call_scaled_peaks",
        "call_scaled_polyA_peaks",
        "call_unscaled_peaks",
        "call_unscaled_polyA_peaks",
    }
)

ADVANCED_RUN_KEYS = frozenset(
    key for key, _ in SEQUENCE_FLAGS
)

KNOWN_KEYS = frozenset(
    {
        *REQUIRED_RUN_KEYS,
        *(key for key, _ in VALUE_FLAGS),
        *(key for key, _ in SEQUENCE_FLAGS),
        *(key for key, _ in BOOL_FLAGS),
        *DERIVED_OR_LEGACY_KEYS,
        "deepcps",
    }
)


@dataclass(frozen=True)
class RunCommand:
    argv: tuple[str, ...]
    shell_command: str
    config: dict[str, Any]
    ignored_keys: tuple[str, ...] = ()


def build_run_command(
    config: Mapping[str, Any],
    *,
    python_executable: str = "python",
    main_path: str | Path = "main.py",
) -> RunCommand:
    normalized = _normalize_config(config)
    _validate_deepcps_config(normalized)
    missing = [key for key in REQUIRED_RUN_KEYS if _is_empty(normalized.get(key))]
    if missing:
        raise ValueError("Missing required run config keys: " + ", ".join(missing))
    if normalized.get("repair_moved_run") and normalized.get("repair_moved_run_dry_run"):
        raise ValueError("repair_moved_run and repair_moved_run_dry_run are mutually exclusive")

    argv: list[str] = [str(python_executable), str(main_path), RUN_SUBCOMMAND]
    for key, flag in VALUE_FLAGS:
        value = normalized.get(key)
        if _is_empty(value) or value is False:
            continue
        argv.extend([flag, str(value)])
    for key, flag in SEQUENCE_FLAGS:
        values = _sequence_values(normalized.get(key), key=key)
        if values:
            argv.append(flag)
            argv.extend(values)
    for key, flag in BOOL_FLAGS:
        if bool(normalized.get(key)):
            argv.append(flag)
    if "deepcps" in normalized:
        argv.append("--deepcps" if normalized["deepcps"] else "--no-deepcps")

    ignored = tuple(sorted(key for key in normalized if key not in KNOWN_KEYS or key in DERIVED_OR_LEGACY_KEYS))
    return RunCommand(
        argv=tuple(argv),
        shell_command=shlex.join(argv),
        config=normalized,
        ignored_keys=ignored,
    )


def materialize_execution_config(argv: Sequence[str]) -> dict[str, Any]:
    """Parse a generated command with the runtime parser to apply exact defaults."""

    values = tuple(str(value) for value in argv)
    if len(values) < 3 or values[2] != RUN_SUBCOMMAND:
        raise ValueError("Run argv must start with <python> <main.py> run")

    from arguments import parse_arguments

    try:
        parsed = parse_arguments(list(values[2:]))
    except SystemExit as exc:
        raise ValueError(f"Run argv could not be parsed (exit code {exc.code})") from exc
    config = _normalize_config(vars(parsed))
    if config.get("command") != RUN_SUBCOMMAND:
        raise ValueError(f"Expected a run command, received {config.get('command')!r}")
    return config


def _validate_deepcps_config(config: Mapping[str, Any]) -> None:
    """Reject lossy truthiness/coercion; file validation belongs to native plan."""
    if "deepcps" in config and type(config["deepcps"]) is not bool:
        raise ValueError("deepcps must be a boolean")
    for key, upper in (("deepcps_threads", 2), ("deepcps_batch_size", 512)):
        if key in config and (type(config[key]) is not int or not 1 <= config[key] <= upper):
            raise ValueError(f"{key} must be an integer in 1..{upper}")
    for key in ("reference_fasta", "deepcps_model_dir", "deepcps_model_manifest"):
        if key in config and config[key] is not None and (
            not isinstance(config[key], str) or not config[key].strip()
        ):
            raise ValueError(f"{key} must be a nonempty path string")
    pin = config.get("deepcps_reference_sha256")
    if pin is not None and (not isinstance(pin, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", pin)):
        raise ValueError("deepcps_reference_sha256 must contain 64 hexadecimal characters")
    if config.get("deepcps") and any(not config.get(k) for k in ("reference_fasta", "deepcps_model_dir")):
        raise ValueError("deepcps requires reference_fasta and deepcps_model_dir")


def _normalize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _normalize_value(value) for key, value in dict(config).items()}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return value


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _sequence_values(value: Any, *, key: str) -> tuple[str, ...]:
    if _is_empty(value):
        return ()
    if isinstance(value, str):
        raw_values = (value,)
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (bytes, bytearray),
    ):
        raw_values = tuple(value)
    else:
        raise ValueError(f"{key} must be a string or sequence of values")
    values = tuple(str(item).strip() for item in raw_values)
    if not values or not all(values):
        raise ValueError(f"{key} values must be non-empty")
    if len(set(values)) != len(values):
        raise ValueError(f"{key} values must be unique")
    return values
