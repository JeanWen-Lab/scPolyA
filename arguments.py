import argparse
from pathlib import Path

from quantification_config import (
    DEFAULT_INTRONIC_MISPRIMED_RATIO_THRESHOLD,
    DEFAULT_QUANTIFICATION_MODE,
    QUANTIFICATION_MODES,
)


THRESHOLD_STATS = ["median", "mean", "median_nonzero", "max_norm_fixed"]
SCALING_CHOICES = ["none", "expr", "polyA", "both"]


def build_parser():
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--debug", action="store_true", default=False, help="Enable debug mode.")
    parent_parser.add_argument("--verbose", action="store_true", default=False, help="Enable verbose mode.")

    parser = argparse.ArgumentParser(
        description="scPolyA: single-cell poly(A) site analysis toolkit."
    )
    parser.add_argument("--version", action="version", version="scPolyA 2.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True, help="Sub-commands")

    _build_prepare_bulk_parser(subparsers, parent_parser)
    _build_run_parser(subparsers, parent_parser)

    return parser


def parse_arguments(argv=None):
    parser = build_parser()
    parsed = parser.parse_args(argv)

    if parsed.command == "prepare-bulk":
        _validate_prepare_bulk_args(parser, parsed)
    elif parsed.command == "run":
        _normalize_legacy_peak_flags(parser, parsed)
        _validate_run_args(parser, parsed)

    return parsed


def _build_prepare_bulk_parser(subparsers, parent_parser):
    parser_prep = subparsers.add_parser(
        "prepare-bulk",
        parents=[parent_parser],
        help="Generate trustworthy 3'UTR GTF from Bulk BigWig files.",
    )
    parser_prep.add_argument(
        "--n-workers",
        type=int,
        default=4,
        help=argparse.SUPPRESS,
    )
    parser_prep.add_argument(
        "--n-threads",
        type=int,
        default=4,
        help=argparse.SUPPRESS,
    )
    parser_prep.add_argument("--plus-bw", required=True, type=str, help="Bulk RNA-seq BigWig file (plus strand).")
    parser_prep.add_argument("--minus-bw", required=True, type=str, help="Bulk RNA-seq BigWig file (minus strand).")
    parser_prep.add_argument("--gene-gtf", required=True, type=str, help="Genome annotation GTF.")
    parser_prep.add_argument("--output-gene-gtf", type=str, default=None, help="Output bulk gene-locus GTF.")
    parser_prep.add_argument("--output-window-gtf", type=str, default=None, help="Output bulk 3'UTR window GTF.")
    parser_prep.add_argument(
        "--output-gtf",
        type=str,
        default=None,
        help="[Deprecated] Legacy single output path for gene-locus GTF; window GTF becomes '<output>.utr_windows.gtf'.",
    )


def _build_run_parser(subparsers, parent_parser):
    parser_run = subparsers.add_parser(
        "run",
        parents=[parent_parser],
        help="Run the main single-cell analysis pipeline.",
    )

    identity_group = parser_run.add_argument_group("Identity")
    identity_group.add_argument("--output-dir", type=str, required=True, help="Path to store all outputs.")
    identity_group.add_argument("--run-id", type=str, required=True, help="Experiment name used for output naming.")
    identity_group.add_argument(
        "--species", type=str, required=True, choices=["human", "mouse", "fly", "worm"], help="Input species."
    )

    input_group = parser_run.add_argument_group("Inputs And References")
    input_group.add_argument("--inputs", type=str, required=True, help="CSV file defining samples and BAM paths.")
    input_group.add_argument("--gene-gtf", type=str, required=True, help="Gene annotation GTF.")
    input_group.add_argument("--chrom-sizes", type=str, required=True, help="Chromosome sizes file.")
    input_group.add_argument(
        "--bulk-window-gtf",
        type=str,
        default=None,
        help="Bulk-derived 3'UTR GTF produced by `prepare-bulk`.",
    )
    input_group.add_argument(
        "--bulk-gene-gtf",
        type=str,
        default=None,
        help="Bulk-derived gene-locus GTF produced by `prepare-bulk`.",
    )

    _build_deepcps_arguments(parser_run)

    tag_group = parser_run.add_argument_group("Tags")
    tag_group.add_argument("--cb-tag", "--cell-barcode-tag", type=str, default="CB", help="Corrected cell barcode tag.")
    tag_group.add_argument("--umi-tag", type=str, default="UB", help="UMI tag.")

    preprocess_group = parser_run.add_argument_group("Preprocess")
    preprocess_group.add_argument(
        "--force-split-bams",
        action="store_true",
        default=False,
        help=(
            "Materialize per-contig BAMs before dedup for every whole-BAM input. "
            "By default, inputs requiring dedup are read directly by contig."
        ),
    )
    preprocess_group.add_argument(
        "--max-split-workers",
        type=int,
        default=4,
        help="Concurrent sample-contig BAM split tasks.",
    )
    preprocess_group.add_argument(
        "--max-filter-workers",
        type=int,
        default=4,
        help="Concurrent BAM filter and barcode-suffix rewrite tasks.",
    )
    preprocess_group.add_argument(
        "--force-dedup-bams", action="store_true", default=False, help="Deduplicate all BAM files."
    )
    preprocess_group.add_argument(
        "--dedup-method",
        type=str,
        default="directional",
        choices=["directional", "unique", "cluster", "adjacency"],
        help="UMI-tools deduplication method.",
    )
    preprocess_group.add_argument("--dedup-stats", action="store_true", help="Write deduplication stats.")
    preprocess_group.add_argument(
        "--max-dedup-workers",
        type=int,
        default=4,
        help="Concurrent per-unit umi_tools dedup tasks.",
    )

    polya_group = parser_run.add_argument_group("PolyA Extraction")
    polya_group.add_argument("--minpolyA", type=int, default=10, help="Minimum polyA tail length.")
    polya_group.add_argument("--nonA-allowed", type=int, default=1, help="Maximum non-A bases allowed in polyA tail.")
    polya_group.add_argument("--minMAPQ", type=int, default=0, help="Minimum mapping quality.")
    polya_group.add_argument(
        "--count-read-rule",
        choices=["legacy", "unified"],
        default="legacy",
        help=(
            "Read eligibility for discovery and counting. 'legacy' keeps the "
            "historical rules (poly(A) extract: mapped and MAPQ>=--minMAPQ; "
            "expression coverage: all reads; counting: featureCounts without "
            "-M, dropping NH>1 reads). 'unified' applies one rule everywhere: "
            "mapped, primary, MAPQ>=--minMAPQ, plus --multimapper-policy."
        ),
    )
    polya_group.add_argument(
        "--multimapper-policy",
        choices=["unique", "rescued"],
        default=None,
        help=(
            "Required with --count-read-rule unified. 'unique' rejects reads "
            "with NH>1 or mm:i:1 (Cell Ranger rescued multimappers); "
            "'rescued' keeps primary, MAPQ-passing rescued reads."
        ),
    )
    polya_group.add_argument(
        "--count-assignment",
        choices=["overlap", "three_prime_end"],
        default=None,
        help=(
            "How an eligible read is placed on a PAS window when counting. "
            "'three_prime_end' (default with --count-read-rule unified) uses the "
            "read's 3'-most aligned base (featureCounts --read2pos 3); 'overlap' "
            "uses featureCounts overlap, where a read touching two windows is "
            "ambiguous. --count-read-rule legacy always uses overlap."
        ),
    )
    polya_group.add_argument(
        "--max-extract-workers",
        type=int,
        default=16,
        help=(
            "Concurrent PolyA extraction child processes and optional "
            "strand-BAM materialization tasks."
        ),
    )
    polya_group.add_argument(
        "--materialize-strand-bams",
        action="store_true",
        default=False,
        help=(
            "Explicitly retain compatibility plus/minus BAMs for every unit and "
            "cohort. Peak coverage always uses the unstranded cohort BAMs with "
            "a strand filter, regardless of this output-only switch."
        ),
    )

    merge_group = parser_run.add_argument_group("Merge")
    merge_group.add_argument("--merge-temp-dir", type=str, default=None, help="Temporary directory for merging BAMs.")
    merge_group.add_argument(
        "--merge-thread",
        type=int,
        default=4,
        help="samtools -@ threads inside each concurrent chromosome merge task.",
    )
    merge_group.add_argument("--keep-merge-temp", action="store_true", help="Keep temporary merge files.")
    merge_group.add_argument(
        "--max-merge-workers",
        type=int,
        default=4,
        help="Concurrent chromosome merge and final BAM cat tasks.",
    )

    peak_group = parser_run.add_argument_group("Peak Calling")
    peak_group.add_argument(
        "--coverage-scaling",
        type=str,
        default="none",
        choices=SCALING_CHOICES,
        help="Coverage scaling mode for peak calling.",
    )
    peak_group.add_argument("--call-scaled-peaks", action="store_true", help=argparse.SUPPRESS)
    peak_group.add_argument("--call-scaled-polyA-peaks", action="store_true", help=argparse.SUPPRESS)
    peak_group.add_argument("--call-unscaled-peaks", action="store_true", help=argparse.SUPPRESS)
    peak_group.add_argument("--call-unscaled-polyA-peaks", action="store_true", help=argparse.SUPPRESS)

    expr_group = parser_run.add_argument_group("Expression Peak Thresholds")
    expr_group.add_argument(
        "--exp-peak-threshold-mode",
        type=str,
        default="fixed",
        choices=["fixed", "dynamic"],
        help="Threshold mode for expression peaks.",
    )
    expr_group.add_argument("--unscaled-depth-threshold", type=float, help="Fixed threshold for unscaled expression peaks.")
    expr_group.add_argument("--scaled-depth-threshold", type=float, help="Fixed threshold for scaled expression peaks.")
    expr_group.add_argument(
        "--exp-dynamic-threshold-stat",
        type=str,
        default=None,
        choices=THRESHOLD_STATS,
        help="Statistic used for dynamic expression thresholds.",
    )
    expr_group.add_argument("--exp-dynamic-threshold-alpha", type=float, default=1.0, help="Dynamic expression alpha.")
    expr_group.add_argument("--exp-dynamic-threshold-floor", type=float, default=0.0, help="Dynamic expression floor.")
    expr_group.add_argument(
        "--exp-dynamic-threshold-normalized",
        type=float,
        default=0.3,
        help="Normalized threshold for expression stat=max_norm_fixed.",
    )
    expr_group.add_argument("--merge-distance", type=int, help="Merge distance for expression peaks.")

    polya_peak_group = parser_run.add_argument_group("PolyA Peak Thresholds")
    polya_peak_group.add_argument(
        "--polyA-peak-threshold-mode",
        type=str,
        default="fixed",
        choices=["fixed", "dynamic"],
        help="Threshold mode for polyA peaks.",
    )
    polya_peak_group.add_argument(
        "--unscaled-depth-threshold-polyA", type=float, help="Fixed threshold for unscaled polyA peaks."
    )
    polya_peak_group.add_argument(
        "--scaled-depth-threshold-polyA", type=float, help="Fixed threshold for scaled polyA peaks."
    )
    polya_peak_group.add_argument(
        "--polyA-dynamic-threshold-stat",
        type=str,
        default=None,
        choices=THRESHOLD_STATS,
        help="Statistic used for dynamic polyA thresholds.",
    )
    polya_peak_group.add_argument("--polyA-dynamic-threshold-alpha", type=float, default=1.0, help="Dynamic polyA alpha.")
    polya_peak_group.add_argument("--polyA-dynamic-threshold-floor", type=float, default=0.0, help="Dynamic polyA floor.")
    polya_peak_group.add_argument(
        "--polyA-dynamic-threshold-normalized",
        type=float,
        default=0.3,
        help="Normalized threshold for polyA stat=max_norm_fixed.",
    )
    polya_peak_group.add_argument("--merge-distance-polyA", type=int, help="Merge distance for polyA peaks.")

    filtering_group = parser_run.add_argument_group("Filtering")
    filtering_group.add_argument(
        "--contigs",
        nargs="+",
        metavar="CONTIG",
        default=argparse.SUPPRESS,
        help=(
            "Restrict the run to these standard contigs (for example: chr1 chr10 chrX "
            "or 1 10 X)."
        ),
    )
    filtering_group.add_argument(
        "--remove-mt", "--remove-mitochondrial", action="store_true", default=False, help="Remove mitochondrial reads."
    )

    runtime_group = parser_run.add_argument_group("Runtime")
    runtime_group.add_argument(
        "--n-workers",
        type=int,
        default=4,
        help=(
            "Concurrent tasks for UMI counting, peak calling/annotation, "
            "count/RNA matrices, and summaries."
        ),
    )
    runtime_group.add_argument(
        "--n-threads",
        type=int,
        default=4,
        help=argparse.SUPPRESS,
    )
    runtime_group.add_argument(
        "--n-workers-annotate",
        type=int,
        default=0,
        help=(
            "Concurrent peak/RNA-gene feature sidecar tasks; 0 inherits "
            "--n-workers."
        ),
    )
    runtime_group.add_argument(
        "--n-threads-merge",
        type=int,
        default=0,
        help=argparse.SUPPRESS,
    )
    runtime_group.add_argument(
        "--n-threads-bam-annotation",
        type=int,
        default=4,
        help="featureCounts threads inside each concurrent feature sidecar task.",
    )
    runtime_group.add_argument("--tools-seed", type=int, default=2026, help="Random seed for external tools.")

    misprime_group = parser_run.add_argument_group("Mispriming")
    misprime_group.add_argument("--misprime-A-count", dest="misprime_A_count", type=int, default=8)
    misprime_group.add_argument("--misprime-in", dest="misprime_in", type=int, default=10)
    misprime_group.add_argument("--misprimed-ratio-threshold", type=float, default=1)

    quantification_group = parser_run.add_argument_group("Quantification")
    quantification_group.add_argument(
        "--quantification-mode",
        type=str,
        default=DEFAULT_QUANTIFICATION_MODE,
        choices=QUANTIFICATION_MODES,
        help=(
            "Quantification profile. 'legacy' preserves existing behavior; "
            "'terminal-aware' enables opt-in, currently unbenchmarked terminal-aware "
            "summary semantics."
        ),
    )
    quantification_group.add_argument(
        "--intronic-misprimed-ratio-threshold",
        type=float,
        default=DEFAULT_INTRONIC_MISPRIMED_RATIO_THRESHOLD,
        metavar="FLOAT",
        help=(
            "Intronic mispriming ratio threshold in [0, 1], used only when "
            "--quantification-mode=terminal-aware (default: 0.5)."
        ),
    )

    output_group = parser_run.add_argument_group("Downstream Outputs")
    output_group.add_argument(
        "--annotate-bam",
        action="store_true",
        help="Generate per-unit peak-assignment sidecars from resolved unit BAMs.",
    )
    output_group.add_argument(
        "--repair-moved-run",
        action="store_true",
        help="Repair moved-run path records in input tables and state.sqlite before rerun.",
    )
    output_group.add_argument(
        "--repair-moved-run-dry-run",
        action="store_true",
        help="Preview moved-run path repairs and exit before running pipeline.",
    )
    output_group.add_argument("--count-matrix", action="store_true", help="Generate per-sample peak-cell matrices.")
    output_group.add_argument(
        "--rna-gene-matrix",
        action="store_true",
        help="Generate per-sample RNA gene-cell tables from resolved unit BAMs using the reference gene GTF.",
    )
    output_group.add_argument(
        "--summarize-matrix",
        action="store_true",
        help="Generate peak metadata, gene-cell tables, cell-gene matrices, weighted 3'UTR length tables, and PSI tables.",
    )
    output_group.add_argument(
        "--psi-min-gene-umis", type=int, default=1, help="Minimum per-gene UMI count required to report PSI values."
    )
    output_group.add_argument(
        "--annotate-peak-source",
        type=str,
        default="resolved",
        choices=["resolved", "merged"],
        help="Peak source used by annotate_peaks and downstream BAM annotation.",
    )
    output_group.add_argument(
        "--keep-annotate-bam-temp",
        action="store_true",
        help="Deprecated placeholder; peak annotation now persists sidecars instead of annotated BAM temps.",
    )


def _validate_prepare_bulk_args(parser, parsed):
    if parsed.output_gtf:
        if parsed.output_gene_gtf and Path(parsed.output_gene_gtf) != Path(parsed.output_gtf):
            parser.error("--output-gtf conflicts with --output-gene-gtf.")
        parsed.output_gene_gtf = parsed.output_gene_gtf or parsed.output_gtf
        parsed.output_window_gtf = parsed.output_window_gtf or f"{parsed.output_gtf}.utr_windows.gtf"

    if not parsed.output_gene_gtf or not parsed.output_window_gtf:
        parser.error(
            "prepare-bulk requires output paths. Provide (--output-gene-gtf and --output-window-gtf) "
            "or legacy --output-gtf."
        )


def _normalize_legacy_peak_flags(parser, parsed):
    legacy_used = any(
        [
            parsed.call_scaled_peaks,
            parsed.call_scaled_polyA_peaks,
            parsed.call_unscaled_peaks,
            parsed.call_unscaled_polyA_peaks,
        ]
    )
    if not legacy_used:
        return

    if parsed.coverage_scaling != "none":
        parser.error("Do not mix --coverage-scaling with legacy --call-*-peaks flags.")

    if parsed.call_scaled_peaks and parsed.call_unscaled_peaks:
        parser.error("Legacy flags conflict for expression peaks: scaled and unscaled are both set.")
    if parsed.call_scaled_polyA_peaks and parsed.call_unscaled_polyA_peaks:
        parser.error("Legacy flags conflict for polyA peaks: scaled and unscaled are both set.")
    if not (parsed.call_scaled_peaks or parsed.call_unscaled_peaks):
        parser.error("Legacy flags require one expression peak mode.")
    if not (parsed.call_scaled_polyA_peaks or parsed.call_unscaled_polyA_peaks):
        parser.error("Legacy flags require one polyA peak mode.")

    exp_scaled = parsed.call_scaled_peaks
    polya_scaled = parsed.call_scaled_polyA_peaks
    if exp_scaled and polya_scaled:
        parsed.coverage_scaling = "both"
    elif exp_scaled:
        parsed.coverage_scaling = "expr"
    elif polya_scaled:
        parsed.coverage_scaling = "polyA"
    else:
        parsed.coverage_scaling = "none"


def _validate_run_args(parser, parsed):
    _validate_deepcps_args(parser, parsed)
    if parsed.repair_moved_run and parsed.repair_moved_run_dry_run:
        parser.error("--repair-moved-run and --repair-moved-run-dry-run are mutually exclusive.")
    count_read_rule = getattr(parsed, "count_read_rule", "legacy")
    multimapper_policy = getattr(parsed, "multimapper_policy", None)
    if count_read_rule == "unified" and multimapper_policy is None:
        parser.error("--count-read-rule unified requires --multimapper-policy unique or rescued.")
    if count_read_rule != "unified" and multimapper_policy is not None:
        parser.error("--multimapper-policy applies only to --count-read-rule unified.")
    count_assignment = getattr(parsed, "count_assignment", None)
    if count_read_rule != "unified":
        if count_assignment not in (None, "overlap"):
            parser.error("--count-assignment three_prime_end requires --count-read-rule unified.")
        if hasattr(parsed, "count_assignment"):
            parsed.count_assignment = "overlap"
    elif count_assignment is None:
        parsed.count_assignment = "three_prime_end"

    requested_contigs = getattr(parsed, "contigs", None)
    if requested_contigs is not None:
        normalized_contigs = [str(contig).strip() for contig in requested_contigs]
        if not all(normalized_contigs):
            parser.error("--contigs values must be non-empty.")
        if len(set(normalized_contigs)) != len(normalized_contigs):
            parser.error("--contigs values must be unique.")
        parsed.contigs = normalized_contigs

    _validate_non_negative(parser, parsed.merge_distance, "--merge-distance")
    if parsed.merge_distance is None:
        parser.error("--merge-distance is required.")

    if parsed.merge_distance_polyA is None:
        parsed.merge_distance_polyA = parsed.merge_distance
    else:
        _validate_non_negative(parser, parsed.merge_distance_polyA, "--merge-distance-polyA")

    if not (0.0 <= parsed.exp_dynamic_threshold_normalized <= 1.0):
        parser.error("--exp-dynamic-threshold-normalized must be within [0, 1].")
    if not (0.0 <= parsed.polyA_dynamic_threshold_normalized <= 1.0):
        parser.error("--polyA-dynamic-threshold-normalized must be within [0, 1].")
    _validate_ratio_threshold(
        parser,
        parsed.misprimed_ratio_threshold,
        "--misprimed-ratio-threshold",
    )
    _validate_ratio_threshold(
        parser,
        parsed.intronic_misprimed_ratio_threshold,
        "--intronic-misprimed-ratio-threshold",
    )

    _validate_threshold_family(
        parser=parser,
        label="expression",
        mode=parsed.exp_peak_threshold_mode,
        scaling=parsed.coverage_scaling in ("expr", "both"),
        fixed_unscaled=parsed.unscaled_depth_threshold,
        fixed_scaled=parsed.scaled_depth_threshold,
        dyn_stat=parsed.exp_dynamic_threshold_stat,
        dyn_alpha=parsed.exp_dynamic_threshold_alpha,
        dyn_floor=parsed.exp_dynamic_threshold_floor,
        dyn_normalized=parsed.exp_dynamic_threshold_normalized,
        fixed_unscaled_flag="--unscaled-depth-threshold",
        fixed_scaled_flag="--scaled-depth-threshold",
        dyn_stat_flag="--exp-dynamic-threshold-stat",
        dyn_alpha_flag="--exp-dynamic-threshold-alpha",
        dyn_floor_flag="--exp-dynamic-threshold-floor",
        dyn_normalized_flag="--exp-dynamic-threshold-normalized",
    )

    _validate_threshold_family(
        parser=parser,
        label="polyA",
        mode=parsed.polyA_peak_threshold_mode,
        scaling=parsed.coverage_scaling in ("polyA", "both"),
        fixed_unscaled=parsed.unscaled_depth_threshold_polyA,
        fixed_scaled=parsed.scaled_depth_threshold_polyA,
        dyn_stat=parsed.polyA_dynamic_threshold_stat,
        dyn_alpha=parsed.polyA_dynamic_threshold_alpha,
        dyn_floor=parsed.polyA_dynamic_threshold_floor,
        dyn_normalized=parsed.polyA_dynamic_threshold_normalized,
        fixed_unscaled_flag="--unscaled-depth-threshold-polyA",
        fixed_scaled_flag="--scaled-depth-threshold-polyA",
        dyn_stat_flag="--polyA-dynamic-threshold-stat",
        dyn_alpha_flag="--polyA-dynamic-threshold-alpha",
        dyn_floor_flag="--polyA-dynamic-threshold-floor",
        dyn_normalized_flag="--polyA-dynamic-threshold-normalized",
    )

    parsed.call_scaled_peaks = parsed.coverage_scaling in ("expr", "both")
    parsed.call_scaled_polyA_peaks = parsed.coverage_scaling in ("polyA", "both")
    parsed.call_unscaled_peaks = parsed.coverage_scaling in ("none", "polyA")
    parsed.call_unscaled_polyA_peaks = parsed.coverage_scaling in ("none", "expr")


def _validate_threshold_family(
    *,
    parser,
    label,
    mode,
    scaling,
    fixed_unscaled,
    fixed_scaled,
    dyn_stat,
    dyn_alpha,
    dyn_floor,
    dyn_normalized,
    fixed_unscaled_flag,
    fixed_scaled_flag,
    dyn_stat_flag,
    dyn_alpha_flag,
    dyn_floor_flag,
    dyn_normalized_flag,
):
    if mode == "fixed":
        required_flag = fixed_scaled_flag if scaling else fixed_unscaled_flag
        required_value = fixed_scaled if scaling else fixed_unscaled
        forbidden_flag = fixed_unscaled_flag if scaling else fixed_scaled_flag
        forbidden_value = fixed_unscaled if scaling else fixed_scaled

        if required_value is None:
            parser.error(f"{required_flag} is required when {label} threshold mode is 'fixed'.")
        if forbidden_value is not None:
            parser.error(
                f"{forbidden_flag} should not be provided for {label} fixed-threshold mode under coverage-scaling='{_scaling_label(scaling)}'."
            )
        if dyn_stat is not None:
            parser.error(f"{dyn_stat_flag} should not be provided when {label} threshold mode is 'fixed'.")
        _validate_non_negative(parser, required_value, required_flag)
        return

    if dyn_stat is None:
        parser.error(f"{dyn_stat_flag} is required when {label} threshold mode is 'dynamic'.")
    if fixed_unscaled is not None or fixed_scaled is not None:
        parser.error(
            f"{fixed_unscaled_flag} and {fixed_scaled_flag} should not be provided when {label} threshold mode is 'dynamic'."
        )
    if dyn_stat == "max_norm_fixed":
        if dyn_normalized is None:
            parser.error(f"{dyn_normalized_flag} is required when {dyn_stat_flag}=max_norm_fixed.")
    else:
        if dyn_alpha is None:
            parser.error(f"{dyn_alpha_flag} is required when {label} threshold mode is 'dynamic'.")
        if dyn_floor is None:
            parser.error(f"{dyn_floor_flag} is required when {label} threshold mode is 'dynamic'.")


def _validate_non_negative(parser, value, flag):
    if value is not None and value < 0:
        parser.error(f"{flag} must be >= 0.")


def _validate_ratio_threshold(parser, value, flag):
    if not (0.0 <= value <= 1.0):
        parser.error(f"{flag} must be finite and within [0, 1].")


def _scaling_label(is_scaled):
    return "scaled" if is_scaled else "unscaled"


def _build_deepcps_arguments(parser):
    group = parser.add_argument_group("Optional DeepCPS annotation")
    group.add_argument("--deepcps", action=argparse.BooleanOptionalAction, default=False,
                       help="Annotate sites with candidate DeepCPS scores; never filter or change count intervals.")
    group.add_argument("--reference-fasta", help="Indexed FASTA used for BAM alignment; required with --deepcps.")
    group.add_argument("--deepcps-model-dir", help="Directory containing all six seed weights and calibration resources.")
    group.add_argument("--deepcps-model-manifest", help="Content manifest (default: the bundled model resource pins).")
    group.add_argument("--deepcps-reference-sha256", help="Optional SHA256 pin for the original alignment FASTA.")
    group.add_argument("--deepcps-threads", type=int, default=2, help="CPU inference threads, 1..2 (default 2).")
    group.add_argument("--deepcps-batch-size", type=int, default=128, help="Inference batch size, 1..512 (default 128).")


def _validate_deepcps_args(parser, parsed):
    if not parsed.deepcps:
        return
    if not parsed.reference_fasta or not parsed.deepcps_model_dir:
        parser.error("--deepcps requires --reference-fasta and --deepcps-model-dir.")
    if not 1 <= parsed.deepcps_threads <= 2 or not 1 <= parsed.deepcps_batch_size <= 512:
        parser.error("DeepCPS requires --deepcps-threads 1..2 and --deepcps-batch-size 1..512.")
    pin = parsed.deepcps_reference_sha256
    if pin is not None and (len(pin) != 64 or any(c not in '0123456789abcdefABCDEF' for c in pin)):
        parser.error("--deepcps-reference-sha256 must contain 64 hexadecimal characters.")
