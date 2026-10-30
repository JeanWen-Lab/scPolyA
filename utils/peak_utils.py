import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Type, TypeVar, Literal, Optional, Any, Dict
import gffutils
import pandas as pd
import pysam

from logging_config import (
    LOG_MAIN,
    LOG_UTILS,
    LoggersWrapper,
    get_logger,
    run_tool_command,
    suffix_scope,
)
from utils.io_utils import bedtools_sort

TExc = TypeVar("TExc", bound=BaseException)
suffix = lambda x: f" (peak_utils:{x})"
logger_main = get_logger(LOG_MAIN)
logger_utils = get_logger(LOG_UTILS)
loggers = LoggersWrapper(main=logger_main, sub=logger_utils)

# A peak with no same-strand tail read has no mispriming evidence. Its ratio is
# written as this token rather than 0 so "not evaluable" is never read as clean.
NOT_EVALUABLE_RATIO_TOKEN = "NA"
RESOLVED_PEAK_COLUMNS = [
    'chrom', 'start', 'end', 'name', 'max_cov', 'strand',
    'total_polyA_reads', 'misprimed_ratio', 'total_reads'
]


def read_resolved_peaks_bed(path, names=None, dtype=None) -> pd.DataFrame:
    """Read a resolved-peak BED keeping contig text verbatim.

    Only ``misprimed_ratio`` interprets the not-evaluable token as missing; a
    contig literally named ``NA`` stays a string.
    """
    names = list(names or RESOLVED_PEAK_COLUMNS)
    frame = pd.read_csv(
        path,
        sep='\t',
        header=None,
        names=names,
        dtype=dtype or {'chrom': str, 'name': str, 'strand': str},
        keep_default_na=False,
        na_values={'misprimed_ratio': [NOT_EVALUABLE_RATIO_TOKEN]},
    )
    frame['misprimed_ratio'] = pd.to_numeric(frame['misprimed_ratio'], errors='raise')
    return frame


def write_resolved_peaks_bed(frame: pd.DataFrame, path) -> None:
    frame.to_csv(path, sep='\t', header=False, index=False, na_rep=NOT_EVALUABLE_RATIO_TOKEN)


def is_tail_read_on_peak_strand(read, strand: str) -> bool:
    """Tail orientation used by Poly(A) extraction: forward reads end in a
    3' A clip ('+'), reverse reads start with a 5' T clip ('-')."""
    if strand == '+':
        return not read.is_reverse
    if strand == '-':
        return bool(read.is_reverse)
    raise ValueError(f"Unsupported peak strand {strand!r}")


def combine_and_intersect_peaks(
        # --- 核心输入 ---
        exp_plus_peaks_bed: Path,
        exp_minus_peaks_bed: Path,
        polyA_plus_peaks_bed: Path,
        polyA_minus_peaks_bed: Path,

        # --- 核心输出路径 (由父函数提供) ---
        final_output_bed: Path,  # 最终输出路径

        # --- 外部资源 ---
        polyA_bam: Path,
        exp_bam: Path,
        standardized_chrom_sizes: Path,
        standard_chroms: set,

        # --- 参数 ---
        merge_distance: int = 0,
        misprime_A_count: int = 8,
        misprime_in: int = 10,
        misprimed_ratio_threshold: float = 1.0,
        include_unassessed_for_audit: bool = False,
):
    """
    Optimized version: Paths are injected, logic is deduplicated.
    """
    logger_utils.info(f"""Combine_and_intersect_peaks
- exp_plus_peaks_bed: {exp_plus_peaks_bed}
- exp_minus_peaks_bed: {exp_minus_peaks_bed}
- polyA_plus_peaks_bed: {polyA_plus_peaks_bed}
- polyA_minus_peaks_bed: {polyA_minus_peaks_bed}

- final_output_bed: {final_output_bed}

- polyA_bam: {polyA_bam}
- exp_bam: {exp_bam}
- standardized_chrom_sizes: {standardized_chrom_sizes}
- standard_chroms: {standard_chroms}

- merge_distance: {merge_distance}
- misprime_A_count: {misprime_A_count}
- misprime_in: {misprime_in}
- misprimed_ratio_threshold: {misprimed_ratio_threshold}
- include_unassessed_for_audit: {include_unassessed_for_audit}""")


    # 1. Input Validation
    input_beds = [exp_plus_peaks_bed, exp_minus_peaks_bed, polyA_plus_peaks_bed, polyA_minus_peaks_bed]
    # for _ in input_beds:
    #     print(_)
    for bed in input_beds:
        if bed and not Path(bed).is_file():
            loggers.raise_error(f"Input BED file '{bed}' does not exist.", FileNotFoundError)

    # 使用临时目录管理中间文件，函数结束后自动清理，保持工作目录整洁
    # Previously published outputs stay untouched until every new output is
    # complete; staging lives next to them so publication is a same-filesystem
    # os.replace per file (see publish_peak_outputs).
    final_output_bed = Path(final_output_bed)
    final_output_bed.parent.mkdir(parents=True, exist_ok=True)

    with _staging_directory(final_output_bed) as temp_dir:

        # =========================================================================
        # Step 1: Intersect & Split (处理正负链核心逻辑)
        # =========================================================================

        split_dfs = []

        # 定义内部函数处理单链逻辑
        def _process_strand(exp_bed, pa_bed, strand_symbol, strand_name):
            if not (exp_bed and pa_bed and exp_bed.exists() and pa_bed.exists()):
                return None

            # 1.1 Bedtools Intersect
            intersect_out = temp_dir / f"intersect_{strand_name}.bed"
            cmd = [
                "bedtools", "intersect",
                "-a", str(pa_bed),
                "-b", str(exp_bed),
                "-wa", "-wb"
            ]

            try:
                with open(intersect_out, 'w') as f:
                    run_tool_command(
                        cmd,
                        tool="bedtools",
                        operation=f"intersect-peaks.{strand_name}",
                        check=True,
                        stdout=f,
                    )
            except subprocess.CalledProcessError as e:
                loggers.raise_error(f"bedtools intersect failed for {strand_name}: {e}")

            if intersect_out.stat().st_size == 0:
                return None

            # 1.2 Read & Split Logic
            # 读取 Intersect 结果
            df = pd.read_csv(
                intersect_out, sep='\t', header=None,
                names=['chr_pa', 'start_pa', 'end_pa', 'name_pa', 'cov_pa', 'str_pa',
                       'chr_all', 'start_all', 'end_all', 'name_all', 'max_cov', 'str_all']
            )

            # 核心 Split 逻辑
            # Group by experimental peak to find all overlapping polyA sites
            grouped = df.groupby(['chr_all', 'start_all', 'end_all', 'str_all']).agg({
                'start_pa': list,
                'end_pa': list,
                'max_cov': 'first'
            }).reset_index()

            split_records = []

            for idx, row in grouped.iterrows():
                # Sort polyA sites by start position
                pa_sites = sorted(zip(row['start_pa'], row['end_pa']), key=lambda x: x[0])

                chrom = row['chr_all']
                all_start = row['start_all']
                all_end = row['end_all']
                max_cov = row['max_cov']

                if strand_symbol == '+':
                    # --- Plus Strand Logic ---
                    current_start = all_start
                    for i, (pa_start, pa_end) in enumerate(pa_sites):
                        # split ends at polyA end
                        split_end = pa_end
                        if current_start <= split_end:
                            split_records.append({
                                'chrom': chrom, 'start': current_start, 'end': split_end,
                                'name': f"split_{strand_name}_{idx}_{i}",
                                'score': max_cov, 'strand': strand_symbol
                            })
                        current_start = split_end + 1

                else:
                    # --- Minus Strand Logic ---
                    # Logic preserved from original: gap between polyA sites defines boundaries?
                    # Or PA_start to next_PA_start?
                    # Original logic interpretation:
                    # Single: PA_start -> All_End
                    # Multi: PA_start[i] -> PA_start[i+1]-1 (Gap filling?) -> Last: PA_start -> All_End

                    for i, (pa_start, pa_end) in enumerate(pa_sites):
                        split_start = pa_start
                        if i < len(pa_sites) - 1:
                            next_pa_start = pa_sites[i + 1][0]
                            split_end = next_pa_start - 1
                        else:
                            split_end = all_end

                        if split_start <= split_end:
                            split_records.append({
                                'chrom': chrom, 'start': split_start, 'end': split_end,
                                'name': f"split_{strand_name}_{idx}_{i}",
                                'score': max_cov, 'strand': strand_symbol
                            })

            return pd.DataFrame(split_records)

        # 执行正负链处理
        logger_utils.info("Processing Plus Strand Intersections...")
        df_plus = _process_strand(exp_plus_peaks_bed, polyA_plus_peaks_bed, '+', 'plus')
        if df_plus is not None and not df_plus.empty:
            split_dfs.append(df_plus)

        logger_utils.info("Processing Minus Strand Intersections...")
        df_minus = _process_strand(exp_minus_peaks_bed, polyA_minus_peaks_bed, '-', 'minus')
        if df_minus is not None and not df_minus.empty:
            split_dfs.append(df_minus)

        if not split_dfs:
            loggers.raise_error("No peaks generated after intersection.", RuntimeError)

        # 合并所有 Split Peaks
        combined_df: pd.DataFrame = pd.concat(split_dfs, ignore_index=True)
        combined_split_bed = temp_dir / "combined_split.bed"
        combined_df.to_csv(combined_split_bed, sep='\t', header=False, index=False)

        # =========================================================================
        # Step 2: Mispriming Assessment (需要临时文件交互)
        # =========================================================================

        # 准备接收 Mispriming 结果的临时文件
        resolved_plus = temp_dir / "resolved_plus.bed"
        resolved_minus = temp_dir / "resolved_minus.bed"

        # 假设 assess_peak_mispriming 是外部导入的函数
        # 注意：这里我们复用 combined_split_bed 作为输入，它内部会根据 strand 参数筛选
        if df_plus is not None:
            assess_peak_mispriming(
                combined_peaks_bed=str(combined_split_bed),
                resolved_peaks_bed=str(resolved_plus),
                merge_distance=merge_distance,
                strand='+',
                misprime_A_count=misprime_A_count,
                misprime_in=misprime_in,
                polyA_bam=str(polyA_bam),
                original_bam=str(exp_bam)
            )

        if df_minus is not None:
            assess_peak_mispriming(
                combined_peaks_bed=str(combined_split_bed),
                resolved_peaks_bed=str(resolved_minus),
                merge_distance=merge_distance,
                strand='-',
                misprime_A_count=misprime_A_count,
                misprime_in=misprime_in,
                polyA_bam=str(polyA_bam),
                original_bam=str(exp_bam)
            )

        # =========================================================================
        # Step 3: Combine Resolved, Sort, Filter
        # =========================================================================

        resolved_dfs = []
        # 列名定义：基于 assess_peak_mispriming 的输出格式
        cols = ['chrom', 'start', 'end', 'name', 'score', 'strand',
                'polyA_reads', 'misprimed_ratio', 'total_reads']

        if resolved_plus.exists() and resolved_plus.stat().st_size > 0:
            resolved_dfs.append(read_resolved_peaks_bed(resolved_plus, names=cols))

        if resolved_minus.exists() and resolved_minus.stat().st_size > 0:
            resolved_dfs.append(read_resolved_peaks_bed(resolved_minus, names=cols))

        if not resolved_dfs:
            loggers.raise_error("No resolved peaks found after mispriming assessment.", RuntimeError)

        final_df = pd.concat(resolved_dfs, ignore_index=True)

        # 写入未排序的临时文件
        temp_unsorted = temp_dir / "temp_unsorted.bed"
        write_resolved_peaks_bed(final_df, temp_unsorted)

        # 3.1 Sort (Bedtools)
        temp_sorted = temp_dir / "temp_sorted.bed"
        bedtools_sort(
            input_bed=str(temp_unsorted),
            sorted_bed=str(temp_sorted),
            chrom_sizes_sorted=str(standardized_chrom_sizes)
        )

        # 3.2 Chromosome Filter
        temp_chrom_filtered = temp_dir / "temp_chrom_filtered.bed"
        filter_bed_by_chromosomes(
            bed_path=temp_sorted,
            standard_chroms=standard_chroms,
            output_bed_path=temp_chrom_filtered
        )

        # 3.3 Mispriming Ratio Filter. All published files are first built in
        # the staging directory and committed only after every step succeeded.
        final_merged_output_bed = final_output_bed.with_name(f"{final_output_bed.stem}-merged.bed")
        staged_final = temp_dir / "final_filtered.bed"
        staged_merged = temp_dir / "final_filtered-merged.bed"
        staged_audit = temp_dir / "final_filtered-not-evaluable.audit.bed"

        filter_peaks_by_mispriming(
            peaks_bed=temp_chrom_filtered,
            output_bed=staged_final,
            threshold=misprimed_ratio_threshold,
            include_unassessed_for_audit=include_unassessed_for_audit,
            not_evaluable_audit_bed=staged_audit,
        )

        # Also emit a post-filter merged view while preserving the resolved sub-peak output.
        merge_filtered_peaks_posthoc(
            input_bed=staged_final,
            output_bed=staged_merged,
            merge_distance=merge_distance,
            chrom_sizes_sorted=standardized_chrom_sizes
        )

        # The final BED goes last: it is the file downstream stages read.
        publish_peak_outputs([
            (staged_audit, not_evaluable_audit_path(final_output_bed)),
            (staged_merged, final_merged_output_bed),
            (staged_final, final_output_bed),
        ])

        logger_utils.info(f"Final peaks generated at: {final_output_bed}")
        logger_utils.info(f"Final merged peaks generated at: {final_merged_output_bed}")
        return final_output_bed



def assess_peak_mispriming(combined_peaks_bed, resolved_peaks_bed, merge_distance, strand,
                           misprime_A_count, misprime_in, polyA_bam, original_bam):
    """
    Assesses mispriming in the final peaks by analyzing read sequences near the peak boundaries.

    Parameters:
    - combined_peaks_bed (str): Path to the combined peaks BED file.
    - resolved_peaks_bed (str): Path to save the resolved peaks BED file after mispriming assessment.
    - merge_distance (int): Maximum distance between peaks to consider merging them. (Unused in this function)
    - strand (str): Strand information ('+' or '-').
    - misprime_A_count (int): Minimum count of 'A's (for '+' strand) or 'T's (for '-' strand) to consider mispriming.
    - misprime_in (int): Window size for mispriming assessment.
    - polyA_bam (str): Path to the polyA filtered BAM file.
    - original_bam (str): Path to the original merged BAM file.

    Returns:
    - None
    """
    logger_utils.info(
        f"Assessing mispriming for strand {strand} with misprime_A_count={misprime_A_count} and misprime_in={misprime_in}...")

    try:
        peaks_df = pd.read_csv(
            combined_peaks_bed,
            sep='\t',
            header=None,
            names=['chrom', 'start', 'end', 'name', 'max_cov', 'strand'],
            dtype={'chrom': str, 'start': 'int64', 'end': 'int64'},
            keep_default_na=False,
            na_filter=False,
        )
    except Exception as e:
        loggers.raise_error(f"Failed to read combined peaks BED file '{combined_peaks_bed}': {e}")

    # Filter peaks by the specified strand
    peaks_df = peaks_df[peaks_df['strand'] == strand].copy()

    # Sort peaks by chrom and start
    peaks_df = peaks_df.sort_values(by=['chrom', 'start']).reset_index(drop=True)

    if peaks_df.empty:
        logger_utils.warning(f"No peaks found for strand {strand} in '{combined_peaks_bed}'.")
        return

    resolved_peaks = []
    peak_counter = 1

    with ExitStack() as bam_stack:
        # Open the polyA BAM file
        try:
            samfile_polyA = pysam.AlignmentFile(polyA_bam, 'rb')
        except Exception as exc:
            logger_utils.error(f"Failed to open polyA BAM file '{polyA_bam}': {exc}")
            raise
        bam_stack.callback(samfile_polyA.close)

        # Open the original BAM file
        try:
            samfile_original = pysam.AlignmentFile(original_bam, 'rb')
        except Exception as exc:
            logger_utils.error(f"Failed to open original BAM file '{original_bam}': {exc}")
            raise
        bam_stack.callback(samfile_original.close)

        for idx, peak in peaks_df.iterrows():
            chrom = peak['chrom']
            peak_start = int(peak['start'])
            peak_end = int(peak['end'])
            max_cov = peak['max_cov']

            # Debugging: Log the current peak being assessed
            logger_utils.debug(f"Assessing mispriming for peak: {chrom}:{peak_start}-{peak_end} on strand {strand}")

            # Initialize counters
            misprimed_reads = 0
            total_polyA_reads = 0  # Same-strand tail reads overlapping the entire peak
            original_total_reads_in_peak = 0  # Total reads in original BAM overlapping the entire peak

            # Fetch reads from original BAM overlapping the entire peak to get original_total_reads_in_peak
            try:
                for read in samfile_original.fetch(chrom, peak_start, peak_end):
                    if read.is_unmapped:
                        continue  # Skip unmapped reads
                    original_total_reads_in_peak += 1
            except ValueError as exc:
                logger_utils.error(
                    f"Error fetching reads for peak {chrom}:{peak_start}-{peak_end} in original BAM: {exc}.")
                raise
            except Exception as exc:
                logger_utils.error(
                    f"Unexpected error fetching reads for peak {chrom}:{peak_start}-{peak_end} in original BAM: {exc}.")
                raise

            # Fetch reads from polyA BAM overlapping the entire peak to assess mispriming
            try:
                for read in samfile_polyA.fetch(chrom, peak_start, peak_end):
                    if read.is_unmapped:
                        continue  # Skip unmapped reads
                    # if read.is_duplicate:
                    #    continue  # Optionally skip duplicate reads

                    # Ensure the read has a sequence
                    if read.query_sequence is None:
                        continue  # Skip reads without sequence

                    # Only tail reads oriented like this peak's strand carry
                    # evidence for it; the A/T rule below is strand-specific.
                    if not is_tail_read_on_peak_strand(read, strand):
                        continue

                    # Increment total_polyA_reads
                    total_polyA_reads += 1
                    seq = read.query_alignment_sequence

                    # Determine the sequence based on strand
                    if strand == '+':
                        # For '+' strand, consider the last 'misprime_in' bases
                        window_seq = seq[-misprime_in:] if len(seq) >= misprime_in else seq
                        count_A = window_seq.upper().count('A')
                    else:
                        # For '-' strand, consider the first 'misprime_in' bases
                        window_seq = seq[:misprime_in] if len(seq) >= misprime_in else seq
                        count_A = window_seq.upper().count('T')

                    if count_A >= misprime_A_count:
                        misprimed_reads += 1

            except Exception as exc:
                # A partial peak set must never be published as complete.
                logger_utils.error(
                    f"Error fetching reads for peak {chrom}:{peak_start}-{peak_end} in polyA BAM: {exc}.")
                raise

            # Calculate misprimed ratio
            if total_polyA_reads == 0:
                misprimed_ratio = float('nan')
                logger_utils.warning(
                    f"No same-strand tail reads for peak {chrom}:{peak_start}-{peak_end}; "
                    f"misprimed_ratio is not evaluable ({NOT_EVALUABLE_RATIO_TOKEN}).")
            else:
                misprimed_ratio = misprimed_reads / total_polyA_reads

            # Debugging statements for each peak
            logger_utils.debug(f"Total reads in polyA BAM peak: {total_polyA_reads}, Misprimed reads: {misprimed_reads}")
            logger_utils.debug(f"Misprimed ratio: {misprimed_ratio}")
            logger_utils.debug(f"Original total reads in original BAM peak: {original_total_reads_in_peak}")

            # Assign peak name with separate strand counter
            peak_name = f"final_peak_{peak_counter}_{strand}"
            peak_counter += 1  # Increment counter

            # Append the peak with misprimed_ratio and total_reads_in_peak
            resolved_peaks.append({
                'chrom': chrom,
                'start': peak_start,
                'end': peak_end,
                'name': peak_name,
                'max_cov': max_cov,
                'strand': strand,
                'total_polyA_reads': total_polyA_reads,  # Total reads from polyA BAM
                'misprimed_ratio': misprimed_ratio,
                'total_reads': original_total_reads_in_peak  # Total reads from original BAM
            })

    # Create DataFrame for resolved peaks
    resolved_peaks_df = pd.DataFrame(resolved_peaks)

    # Check if resolved_peaks_df is empty
    if resolved_peaks_df.empty:
        logger_utils.warning(f"No peaks passed mispriming assessment for strand {strand}.")
        return

    # Select and reorder columns
    columns_order = ['chrom', 'start', 'end', 'name', 'max_cov', 'strand',
                     'total_polyA_reads', 'misprimed_ratio', 'total_reads']
    resolved_peaks_df = resolved_peaks_df[columns_order]

    # Save to resolved peaks BED file
    try:
        write_resolved_peaks_bed(resolved_peaks_df, resolved_peaks_bed)
        logger_utils.info(
            f"Resolved peaks BED file for strand {strand} with misprimed_ratio, total_polyA_reads, and original_total_reads saved to '{resolved_peaks_bed}'.")
    except Exception as e:
        loggers.raise_error(f"Failed to write resolved peaks BED file '{resolved_peaks_bed}': {e}")


def _cleanup_best_effort(cleanup, path, what) -> bool:
    """Run a cleanup callable; log and swallow any failure.

    Cleanup must never replace an exception already propagating from the
    caller, and on the success path a leftover hidden directory is not a
    failed run. Returns whether the cleanup succeeded.
    """
    try:
        cleanup(path)
        return True
    except Exception as cleanup_exc:
        logger_utils.warning(f"Could not remove {what} '{path}' ({cleanup_exc!r}); left in place.")
        return False


@contextmanager
def _staging_directory(final_output_bed):
    """Hidden staging directory next to ``final_output_bed``.

    Unlike TemporaryDirectory, a failing removal on exit is logged and never
    masks an exception raised inside the block.
    """
    staging = Path(tempfile.mkdtemp(prefix=f".{final_output_bed.name}.staging.", dir=final_output_bed.parent))
    try:
        yield staging
    finally:
        _cleanup_best_effort(shutil.rmtree, staging, "staging directory")


def publish_peak_outputs(pairs) -> None:
    """Stage-level commit of (staged, destination) pairs with rollback.

    Each file is moved with os.replace (staged files must sit on the
    destination filesystem). Existing destinations are first moved into a
    backup directory next to them. If any step fails, newly published files
    are removed and the previous set is restored; if the restore itself fails,
    the backup directory is kept and its path logged so the previous outputs
    stay recoverable. This is not a global atomic swap of all files: a reader
    racing the commit can observe a mix, but a failed commit never leaves a
    new partial set in place of the previous one.

    Cleanup is best-effort and logged; it never replaces the publication
    exception. After a rollback the backup directory is only removed with
    rmdir, i.e. only when it is empty, so a backup is never deleted unless it
    has been restored. After a successful commit the superseded outputs in the
    backup directory are removed; if that fails the commit still succeeds and
    the directory is left in place (logged).
    """
    pairs = [(Path(staged), Path(dest)) for staged, dest in pairs]
    for staged, dest in pairs:
        if not staged.is_file():
            raise FileNotFoundError(f"Staged output missing before publication: {staged}")
    parent = pairs[0][1].parent
    backup_dir = Path(tempfile.mkdtemp(prefix=f".{pairs[-1][1].name}.publish-backup.", dir=parent))
    backups = []
    published = []
    try:
        for _, dest in pairs:
            if dest.exists():
                backup = backup_dir / dest.name
                os.replace(dest, backup)
                backups.append((backup, dest))
        for staged, dest in pairs:
            os.replace(staged, dest)
            published.append(dest)
    except BaseException as exc:
        logger_utils.error(f"Publishing peak outputs failed ({exc}); rolling back.")
        try:
            for dest in published:
                dest.unlink(missing_ok=True)
            for backup, dest in backups:
                os.replace(backup, dest)
        except BaseException as rollback_exc:
            logger_utils.error(
                f"Rollback failed ({rollback_exc}); previous outputs are preserved in '{backup_dir}'."
            )
            raise exc
        _cleanup_best_effort(os.rmdir, backup_dir, "empty publication backup directory")
        raise
    _cleanup_best_effort(shutil.rmtree, backup_dir, "publication backup directory")


def not_evaluable_audit_path(final_output_bed) -> Path:
    final_output_bed = Path(final_output_bed)
    return final_output_bed.with_name(f"{final_output_bed.stem}-not-evaluable.audit.bed")


def filter_peaks_by_mispriming(peaks_bed, output_bed, threshold=1,
                               include_unassessed_for_audit=False,
                               not_evaluable_audit_bed=None):
    """
    Filters out peaks with a mispriming ratio greater than the specified threshold.

    Parameters:
    - peaks_bed (str): Path to the input peaks BED file containing 'misprimed_ratio' column.
    - output_bed (str): Path to save the filtered peaks BED file.
    - threshold (float): Mispriming ratio threshold (default: 1).
    - include_unassessed_for_audit (bool): Carry peaks without same-strand tail
      support (ratio NA) into ``output_bed`` for a downstream audit that must
      exclude them itself (terminal-aware). Otherwise they are excluded.
    - not_evaluable_audit_bed (str, optional): Where to write the peaks without
      same-strand tail support (always written when given, possibly empty).

    Returns:
    - None
    """
    logger_utils.info(f"Filtering peaks with misprimed_ratio > {threshold}...")

    try:
        # Read the peaks BED file
        peaks_df = read_resolved_peaks_bed(
            peaks_bed,
            dtype={
                'chrom': str,
                'start': int,
                'end': int,
                'name': str,
                'max_cov': float,
                'strand': str,
                'total_polyA_reads': int,
                'total_reads': int
            }
        )
    except Exception as e:
        loggers.raise_error(f"Failed to read peaks BED file '{peaks_bed}': {e}")

    # Validate required columns
    required_columns = ['misprimed_ratio']
    for col in required_columns:
        if col not in peaks_df.columns:
            loggers.raise_error(f"Column '{col}' is missing in the peaks BED file.")

    # Validate threshold range
    if not (0.0 <= threshold <= 1.0):
        loggers.raise_error(f"Mispriming ratio threshold '{threshold}' is out of bounds. Must be between 0 and 1.")

    # Apply the filter. A not-evaluable ratio (no same-strand tail support)
    # never passes any threshold, including 1; it is carried forward only when
    # the caller explicitly asks for an audit-only pass-through.
    not_evaluable = peaks_df['misprimed_ratio'].isna()
    keep = peaks_df['misprimed_ratio'].le(threshold)
    if include_unassessed_for_audit:
        keep = keep | not_evaluable
    filtered_peaks_df = peaks_df[keep].copy()
    num_filtered = peaks_df.shape[0] - filtered_peaks_df.shape[0]
    logger_utils.info(
        f"Filtered out {num_filtered} peaks with misprimed_ratio > {threshold} or not evaluable; "
        f"{int(not_evaluable.sum())} peaks had no same-strand tail support "
        f"({'carried forward for audit only' if include_unassessed_for_audit else 'excluded'}).")
    if not_evaluable_audit_bed is not None:
        write_resolved_peaks_bed(peaks_df[not_evaluable], not_evaluable_audit_bed)
    logger_utils.info(f"{filtered_peaks_df.shape[0]} peaks remain after filtering.")

    # Check if any peaks remain
    if filtered_peaks_df.empty:
        logger_utils.warning(f"No peaks remain after filtering with threshold {threshold}.")

    # Save the filtered peaks BED file
    try:
        write_resolved_peaks_bed(filtered_peaks_df, output_bed)
        logger_utils.info(f"Filtered peaks BED file saved to '{output_bed}'.")
    except Exception as e:
        loggers.raise_error(f"Failed to write filtered peaks BED file '{output_bed}': {e}")


def merge_filtered_peaks_posthoc(input_bed, output_bed, merge_distance=0, chrom_sizes_sorted=None):
    """
    Merge post-filtered peaks by genomic interval while aggregating metadata columns.

    Output schema matches the input BED:
    chrom, start, end, name, max_cov, strand, total_polyA_reads, misprimed_ratio, total_reads
    """
    logger_utils.info(
        f"Generating post-filter merged peaks from '{input_bed}' with merge_distance={merge_distance}..."
    )

    cols = [
        'chrom', 'start', 'end', 'name', 'max_cov', 'strand',
        'total_polyA_reads', 'misprimed_ratio', 'total_reads'
    ]
    dtypes = {
        'chrom': str,
        'start': int,
        'end': int,
        'name': str,
        'max_cov': float,
        'strand': str,
        'total_polyA_reads': int,
        'misprimed_ratio': float,
        'total_reads': int
    }

    input_bed = Path(input_bed)
    output_bed = Path(output_bed)
    output_bed.parent.mkdir(parents=True, exist_ok=True)

    if not input_bed.exists() or input_bed.stat().st_size == 0:
        output_bed.write_text("")
        logger_utils.warning(
            f"Input BED '{input_bed}' is missing or empty; wrote empty merged file '{output_bed}'."
        )
        return

    try:
        peaks_df = read_resolved_peaks_bed(
            input_bed,
            names=cols,
            dtype={col: kind for col, kind in dtypes.items() if col != 'misprimed_ratio'},
        )
    except pd.errors.EmptyDataError:
        output_bed.write_text("")
        logger_utils.warning(f"Input BED '{input_bed}' is empty; wrote empty merged file '{output_bed}'.")
        return
    except Exception as e:
        loggers.raise_error(f"Failed to read filtered peaks BED file '{input_bed}': {e}")

    if peaks_df.empty:
        output_bed.write_text("")
        logger_utils.warning(f"No peaks available for post-filter merging in '{input_bed}'.")
        return

    # The merged view describes trusted peaks only: a peak without same-strand
    # tail support must not widen or join a supported interval.
    not_evaluable = peaks_df['misprimed_ratio'].isna()
    if not_evaluable.any():
        logger_utils.info(
            f"Post-filter merge ignores {int(not_evaluable.sum())} peaks without same-strand tail support."
        )
    peaks_df = peaks_df[~not_evaluable]
    if peaks_df.empty:
        output_bed.write_text("")
        logger_utils.warning(f"No evaluable peaks available for post-filter merging in '{input_bed}'.")
        return

    # Keep merge behavior deterministic before cluster scan.
    peaks_df = peaks_df.sort_values(by=['chrom', 'strand', 'start', 'end']).reset_index(drop=True)

    merged_records = []
    peak_counter = 1

    for (chrom, strand), grp in peaks_df.groupby(['chrom', 'strand'], sort=False):
        grp = grp.sort_values(by=['start', 'end']).reset_index(drop=True)
        current_rows = []
        current_end = None

        def flush_cluster(rows):
            nonlocal peak_counter
            if not rows:
                return
            cluster_df = pd.DataFrame(rows)
            total_polyA_reads = int(cluster_df['total_polyA_reads'].sum())
            weighted_num = float((cluster_df['misprimed_ratio'] * cluster_df['total_polyA_reads']).sum())
            misprimed_ratio = (weighted_num / total_polyA_reads) if total_polyA_reads > 0 else float('nan')
            strand_label = 'plus' if strand == '+' else 'minus' if strand == '-' else str(strand)

            merged_records.append({
                'chrom': chrom,
                'start': int(cluster_df['start'].min()),
                'end': int(cluster_df['end'].max()),
                'name': f"merged_peak_{peak_counter}_{strand_label}",
                'max_cov': float(cluster_df['max_cov'].max()),
                'strand': strand,
                'total_polyA_reads': total_polyA_reads,
                'misprimed_ratio': float(misprimed_ratio),
                'total_reads': int(cluster_df['total_reads'].sum()),
            })
            peak_counter += 1

        for row in grp.itertuples(index=False):
            row_dict = {
                'start': int(row.start),
                'end': int(row.end),
                'max_cov': float(row.max_cov),
                'total_polyA_reads': int(row.total_polyA_reads),
                'misprimed_ratio': float(row.misprimed_ratio),
                'total_reads': int(row.total_reads),
            }
            if not current_rows:
                current_rows = [row_dict]
                current_end = row_dict['end']
                continue

            # BED intervals are merged when overlap/adjacent gap is within merge_distance.
            if row_dict['start'] <= current_end + int(merge_distance):
                current_rows.append(row_dict)
                current_end = max(current_end, row_dict['end'])
            else:
                flush_cluster(current_rows)
                current_rows = [row_dict]
                current_end = row_dict['end']

        flush_cluster(current_rows)

    merged_df = pd.DataFrame(merged_records, columns=cols)

    temp_unsorted = output_bed.with_suffix(".tmp.unsorted.bed")
    write_resolved_peaks_bed(merged_df, temp_unsorted)

    if chrom_sizes_sorted is not None:
        bedtools_sort(
            input_bed=str(temp_unsorted),
            sorted_bed=str(output_bed),
            chrom_sizes_sorted=str(chrom_sizes_sorted)
        )
        temp_unsorted.unlink(missing_ok=True)
    else:
        temp_unsorted.replace(output_bed)

    logger_utils.info(
        f"Post-filter merged peaks BED file saved to '{output_bed}' ({len(merged_df)} peaks from {len(peaks_df)} input rows)."
    )


def filter_bed_by_chromosomes(bed_path, standard_chroms, output_bed_path):
    """
    Filters a BED file to include only entries on standard chromosomes.

    Parameters:
        bed_path (str): Path to the input BED file.
        standard_chroms (set): Set of standard chromosome names.
        output_bed_path (str): Path to save the filtered BED file.

    Returns:
        None
    """
    try:
        bed_df = pd.read_csv(bed_path, sep='\t', header=None, dtype=str, keep_default_na=False)
        if bed_df.shape[1] < 3:
            loggers.raise_error(f"BED file '{bed_path}' has insufficient columns.")

        filtered_bed_df = bed_df[bed_df[0].isin(standard_chroms)]
        excluded_peaks = bed_df.shape[0] - filtered_bed_df.shape[0]

        if excluded_peaks > 0:
            logger_utils.warning(f"Excluded {excluded_peaks} peaks mapped to non-standard chromosomes.")
        else:
            logger_utils.info("No non-standard chromosomes found in BED file.")

        filtered_bed_df.to_csv(output_bed_path, sep='\t', header=False, index=False)
        logger_utils.info(f"Filtered BED file saved to '{output_bed_path}'.")
    except FileNotFoundError:
        loggers.raise_error(f"BED file '{bed_path}' not found.")
    except Exception as e:
        loggers.raise_error(f"Error filtering BED file: {e}")
