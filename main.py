# sc-polya-remastered/main.py
import shutil
import sys
from argparse import Namespace
from pathlib import Path
from typing import List

from arguments import parse_arguments
from logging_config import (RunLoggingSession, run_tool_command, setup_module_logger, setup_run_logging,
                            LOG_TASK, LOG_STAGE_init_references, LOG_STAGE_register_inputs, LOG_STAGE_preprocess_units,
                            ScPolyALogger, LOG_STAGE_extract_subsets, LOG_STAGE_call_peaks, LOG_STAGE_merge_units,
                            LOG_STAGE_annotate_bams, LOG_STAGE_annotate_peaks, LOG_STAGE_count_matrix,
                            LOG_STAGE_summarize_matrix, LOG_STAGE_rna_gene_matrix,
                            LOG_STAGE_bulk_references, LOG_STAGE_calculate_umi, LOG_UTILS, LOG_NC)
from stages import (register_inputs, init_references, preprocess_inputs, extract_subsets, merge_units,
                    calculate_umi_count, call_peaks, annotate_peaks, annotate_bams, count_matrix, rna_gene_matrix,
                    summarize_matrix)
from task_manage import TaskManager, Plan, Resources
from task_manage.run_layout import RunLayout
from task_manage.output_projection import terminal_annotation_root_notice
from task_manage.cache_epoch import CacheEpochCompatibilityError, NEW_RUN_ROOT_ADVICE
from task_manage.cache_epoch_status import require_compatible_run_root, require_safe_state_paths
from task_manage.task_completion import (
    TaskCompletionLoadError,
    require_current_task_completion_state_v1,
)
from quantification_config import quantification_log_message
from utils.run_repair_utils import repair_moved_run

# region Global variables
Paths: RunLayout | None = None  # path defines;
Verbose: bool | None = None  # debug
manager: TaskManager | None = None
logger: ScPolyALogger | None = None
args: Namespace | None = None
Cache: Resources | None = None
logging_session: RunLoggingSession | None = None

REQUIRED_TOOLS = ['featureCounts', 'samtools', 'umi_tools', 'bedtools', 'bedToBigBed', 'bedGraphToBigWig']
# endregion


def run_mode_initialization(parsed_args):
    global args, Verbose, Paths, manager, logger, Cache, logging_session
    args = parsed_args
    Verbose = args.verbose
    out = Path(args.output_dir)
    Paths = RunLayout(out=out, run_id=args.run_id)
    # Cache generation guard (task_manage/cache_epoch.py): stop before any
    # directory, log, repair, state, scheduling or scientific-output write when
    # the run root is earlier-generation, incompatible or unreadable.
    try:
        require_safe_state_paths(Paths.State)
    except CacheEpochCompatibilityError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        require_current_task_completion_state_v1(Paths.State)
    except TaskCompletionLoadError as exc:
        raise SystemExit(f"{exc}. {NEW_RUN_ROOT_ADVICE}") from exc
    try:
        require_compatible_run_root(Paths.State)
    except CacheEpochCompatibilityError as exc:
        raise SystemExit(str(exc)) from exc
    # Read-only: warn before any work when this root records another
    # terminal annotation schema (same-root continuation becomes mixed-schema).
    annotation_root_notice = terminal_annotation_root_notice(Paths, args)

    Paths.ensure_base_dirs()
    manager = TaskManager(state_dir=Paths.State, config=vars(args))
    empty_plan = Plan(samples=[], contigs_by_sample={})
    manager.load_or_init(empty_plan)
    logging_session = setup_run_logging(
        log_dir=Paths.Logs,
        invocation_id=manager.inline_execution_invocation_id,
        verbose=Verbose,
        argv=tuple(sys.argv),
        config=vars(args),
        run_root=Paths.Root,
    )
    logger = logging_session.main_logger
    logger.info(quantification_log_message(vars(args)))
    if annotation_root_notice:
        logger.warning(annotation_root_notice)

    if args.repair_moved_run or args.repair_moved_run_dry_run:
        repair_moved_run(layout=Paths, args=args, dry_run=args.repair_moved_run_dry_run)
        if args.repair_moved_run_dry_run:
            raise SystemExit(0)

    for _ in [LOG_TASK, LOG_UTILS, LOG_NC,
              LOG_STAGE_bulk_references,
              LOG_STAGE_register_inputs, LOG_STAGE_init_references,
              LOG_STAGE_preprocess_units, LOG_STAGE_extract_subsets, LOG_STAGE_merge_units,
              LOG_STAGE_calculate_umi,
              LOG_STAGE_call_peaks, LOG_STAGE_annotate_peaks, LOG_STAGE_annotate_bams, LOG_STAGE_count_matrix,
              LOG_STAGE_summarize_matrix, LOG_STAGE_rna_gene_matrix]:
        setup_module_logger(_, log_dir=Paths.Logs, verbose=Verbose, console_output=False)

    Cache = Resources(tm=manager, layout=Paths, args=args)
    Cache.set("context", {})
    manager.cache = Cache

def verify_tools():
    for tool in REQUIRED_TOOLS:
        tool_path = shutil.which(str(tool))
        if tool_path is None:
            logger.raise_error(f"{tool} is not installed or not found in PATH.", SystemExit)
        logger.debug(f"Found {tool} at {tool_path}")


def get_bam_contigs(bam_path: Path) -> List[str]:
    """Extract contig names from BAM header using samtools."""
    cmd = ['samtools', 'view', '-H', str(bam_path)]
    result = run_tool_command(
        cmd,
        tool="samtools",
        operation=f"header.{bam_path.name}",
        capture_output=True,
        text=True,
        check=True,
    )
    contigs = []
    for line in result.stdout.splitlines():
        if line.startswith('@SQ'):
            parts = line.split('\t')
            for p in parts:
                if p.startswith('SN:'):
                    contigs.append(p[3:])
    return contigs


def _main():
    parsed_args = parse_arguments()
    if parsed_args.command == "prepare-bulk":
        from modules.bulk_references import build_bulk_reference
        build_bulk_reference(parsed_args)


    elif parsed_args.command == "run":
        run_mode_initialization(parsed_args)
        verify_tools()

        register_inputs(tm=manager, layout=Paths, args=args, cache=Cache)
        # - copy_inputs_csv
        # - parse_and_link
        # - write_preprocess_csv

        init_references(tm=manager, layout=Paths, args=args, cache=Cache)
        # - backup_gtf
        # - adjust_chrom_sizes
        # - filter_chrom_sizes
        # - check_adjust_gtf
        # - prefilter_validate_gtf
        # - gtf_to_bed
        # - sort_gene_bed
        # - build_gffdb
        # - cache_canonical_transcripts
        # - cache_stop_codon
        # - generate_stop_codon_bigbed
        # - prepare_cds

        preprocess_inputs(tm=manager, layout=Paths, args=args, cache=Cache)
        # - split_to_units
        # - filter_units
        # - dedup_units
        # - finalize_units

        extract_subsets(tm=manager, layout=Paths, args=args, cache=Cache)
        # - extract_polyA
        # - split_strand_all
        # - split_strand_polyA

        calculate_umi_count(tm=manager, layout=Paths, args=args, cache=Cache)
        # - count_unit_umi
        # - calculate_upm_factor

        merge_units(tm=manager, layout=Paths, args=args, cache=Cache)
        # - merge_strand
        # - cat_cohort

        call_peaks(tm=manager, layout=Paths, args=args, cache=Cache)
        # - generate_raw_coverage
        # - upm_scale_coverage
        # - exclude_cds_region
        # - negate_bedgraph
        # - generate_bigwig
        # - merge_call_peaks
        # - combine_and_intersect_peaks

        # TODO

        # annotate_peaks  assign：不拿原始gtf
        # 按照 peak 和 3'UTR 最近距离算——来自于 bulk + stop + gtf

        annotate_peaks(tm=manager, layout=Paths, args=args, cache=Cache)

        if getattr(args, "deepcps", False):
            from stages.deepcps import deepcps
            deepcps(tm=manager, layout=Paths, args=args, cache=Cache)
        # - assign_peaks
        # - merge_assigned_peaks
        # - extract_genomic_features
        # - classify_peaks
        # - resolve_peak_issues
        # - convert_peaks_bed_to_bigbed
        # - convert_peaks_bed_to_gtf
        # - log_gtf_features



        if args.annotate_bam or args.count_matrix or args.summarize_matrix:
            annotate_bams(tm=manager, layout=Paths, args=args, cache=Cache)
            # - annotate_unit
            # - cat_unit

        if args.count_matrix or args.summarize_matrix:
            count_matrix(tm=manager, layout=Paths, args=args, cache=Cache)
            # - count_matrix

        if args.summarize_matrix:
            summarize_matrix(tm=manager, layout=Paths, args=args, cache=Cache)
            # - summarize_matrix

        if args.rna_gene_matrix:
            rna_gene_matrix(tm=manager, layout=Paths, args=args, cache=Cache)
            # - annotate_gene_units
            # - count_gene_units
            # - summarize_gene_matrix

        logger.info("All tasks finished.")
        # Durable evidence that the whole plan completed under the current cache
        # generation (task_manage/cache_epoch_status.py); written only here.
        manager.record_cache_epoch_plan_completion()


def main():
    global logging_session
    try:
        _main()
    except KeyboardInterrupt as exc:
        if logging_session is not None:
            logging_session.finish(
                "cancelled",
                error=f"{type(exc).__name__}: {exc}",
            )
        raise
    except SystemExit as exc:
        if logging_session is not None:
            succeeded = exc.code is None or exc.code == 0
            logging_session.finish(
                "succeeded" if succeeded else "failed",
                error="" if succeeded else f"SystemExit: {exc.code}",
            )
        raise
    except BaseException as exc:
        if logging_session is not None:
            logging_session.finish(
                "failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        raise
    else:
        if logging_session is not None:
            logging_session.finish("succeeded")
    finally:
        if logging_session is not None:
            logging_session.close()
            logging_session = None


if __name__ == "__main__":
    main()
