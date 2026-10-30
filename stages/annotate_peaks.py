from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict

import pandas as pd

from logging_config import (
    stage_scope,
    get_logger,
    LOG_MAIN,
    LoggersWrapper,
    LOG_STAGE_annotate_peaks, task_scope  # Needed to fetch UPM factors
)
from task_manage.run_layout import SORTED_GENE_BED, FILTERED_STD_CHROM_SIZES, FILTERED_ADJUSTED_GTF, GFFDB, SORTED_BULK_WINDOW_BED, \
    SORTED_BULK_GENE_BED
from task_manage import run_flex_batch
from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_CANONICAL_TRANSCRIPTS, \
    CACHE_STOP_CODON_POSITIONS, CACHE_MERGED_PEAKS_DF, CACHE_FEATURES_DICT, CACHE_ANNOTATED_MERGED_PEAKS_DF
from task_manage.effective_config import build_annotated_peak_config
from utils.annotation_utils import assign_peaks_to_genes, load_peak_assignments, merge_stop_codon_info, \
    get_canonical_transcripts, get_stop_codon_positions, get_genomic_features, annotate_peaks_with_features, \
    get_gene_models, resolve_peak_issues, convert_peaks_bed_to_gtf, log_gtf_features
from utils.io_utils import create_or_load_gffdb, convert_bed_to_bigbed
from utils.terminal_quant_utils import quantification_artifact_suffix, quantification_mode

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

STAGE = LOG_STAGE_annotate_peaks
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)


def annotate_peaks(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    with stage_scope(STAGE):
        mode = quantification_mode(args)
        final_item = build_annotated_peak_config(
            args,
            dedup_method=cache.get(CACHE_dedup_method),
            polyA_status=cache.get(CACHE_polyA_status),
        )
        annotation_output_root = layout.AnnotatedFinalOutputSub(final_item)

    def _annotate_source_tag(item: Dict) -> str:
        src = item.get("annotate_peak_source", "resolved")
        if src not in {"resolved", "merged"}:
            loggers.raise_error(f"Unsupported annotate_peak_source='{src}'.", ValueError)
        return src

    def _peak_input_bed(item: Dict) -> Path:
        src = _annotate_source_tag(item)
        base_candidates = layout.FinalOutputFileCandidates(
            suffix=quantification_artifact_suffix(
                "___peaks-mispriming-filtered.bed", mode
            ),
            d=item,
        )
        peak_bed = layout.ResolveExistingArtifact(
            tuple(
                base_bed
                if src == "resolved"
                else base_bed.with_name(f"{base_bed.stem}-merged.bed")
                for base_bed in base_candidates
            ),
            label=f"{src} peak input BED",
        )
        if not peak_bed.exists():
            loggers.raise_error(
                f"Required peaks BED file not found for annotate_peak_source='{src}': {peak_bed}",
                FileNotFoundError
            )
        return peak_bed

    def _annotate_subdir(item: Dict) -> Path:
        _annotate_source_tag(item)
        return annotation_output_root

    def _annotate_file(item: Dict, suffix: str) -> Path:
        return _annotate_subdir(item) / layout.FinalFile(
            suffix=quantification_artifact_suffix(suffix, mode), d=item
        )

    def assign_peaks_worker(item: Dict):
        # input
        final_bed = _peak_input_bed(item)
        sorted_gene_bed = layout.ReferenceFile(file_key=SORTED_GENE_BED, args=args)
        chrom_sizes = layout.ReferenceFile(file_key=FILTERED_STD_CHROM_SIZES, args=args)

        bulk_window_bed = layout.ReferenceFile(file_key=SORTED_BULK_WINDOW_BED, args=args) if args.bulk_window_gtf else None
        bulk_gene_bed = layout.ReferenceFile(file_key=SORTED_BULK_GENE_BED, args=args) if args.bulk_gene_gtf else None
        # output
        assigned_peaks_bed = _annotate_file(item, "___peaks-assigned.bed")

        logger_stage.info(
            f"Using annotate_peak_source='{_annotate_source_tag(item)}' with input peaks '{final_bed}'."
        )


        assign_peaks_to_genes(peaks_bed=final_bed,
                              gene_bed=sorted_gene_bed,
                              chrom_sizes=chrom_sizes,
                              bulk_window_bed=bulk_window_bed,
                              bulk_gene_bed=bulk_gene_bed,
                              include_gene_id=(mode == "terminal-aware"),
                              identity_gtf=(
                                  layout.ReferenceFile(file_key=FILTERED_ADJUSTED_GTF, args=args)
                                  if mode == "terminal-aware"
                                  else None
                              ),
                              # out
                              assigned_peaks_bed=str(assigned_peaks_bed))

    def merge_assigned_peaks_worker(item: Dict):
        # input
        assigned_peaks_bed = _annotate_file(item, "___peaks-assigned.bed")
        canonical_transcripts = cache.get(CACHE_CANONICAL_TRANSCRIPTS)
        stop_codon_positions = cache.get(CACHE_STOP_CODON_POSITIONS)
        if mode == "terminal-aware" and (
            any(not isinstance(value, dict) for value in canonical_transcripts.values())
            or any(
                info.get("gene_id") in (None, "")
                for info in stop_codon_positions.values()
            )
        ):
            gffdb = create_or_load_gffdb(layout.ReferenceFile(file_key=GFFDB, args=args))
            canonical_transcripts = get_canonical_transcripts(gffdb)
            stop_codon_positions = get_stop_codon_positions(
                gffdb, canonical_transcripts
            )
        peaks_df = load_peak_assignments(assigned_peaks_bed)
        # output
        merged_peaks_df_pkl = _annotate_file(item, "___peaks-assigned-with-stop-codon.df.pkl")

        # exec.
        merged_peaks_df = merge_stop_codon_info(peaks_df=peaks_df,
                                                canonical_transcripts=canonical_transcripts,
                                                stop_codon_positions=stop_codon_positions)
        merged_peaks_df.to_pickle(merged_peaks_df_pkl)
        cache.set(CACHE_MERGED_PEAKS_DF, merged_peaks_df)

    # force: cache.set
    def merge_assigned_peaks_worker_2(item: Dict):
        if cache.has(CACHE_MERGED_PEAKS_DF):
            return
        merged_peaks_df_pkl = _annotate_file(item, "___peaks-assigned-with-stop-codon.df.pkl")
        merged_peaks_df = pd.read_pickle(merged_peaks_df_pkl)
        cache.set(CACHE_MERGED_PEAKS_DF, merged_peaks_df)

    # force: cache.set
    def extract_genomic_features_worker(item: Dict):
        # input
        gffdb = create_or_load_gffdb(layout.ReferenceFile(file_key=GFFDB, args=args))
        canonical_transcripts = cache.get(CACHE_CANONICAL_TRANSCRIPTS)
        if mode == "terminal-aware" and any(
            not isinstance(value, dict)
            for value in canonical_transcripts.values()
        ):
            canonical_transcripts = get_canonical_transcripts(gffdb)
        # exec.
        features_dict = get_genomic_features(gffdb=gffdb, canonical_transcripts=canonical_transcripts)
        cache.set(CACHE_FEATURES_DICT, features_dict)

    # force: cache.set
    def classify_peaks_worker(item: Dict):
        # input
        merged_peaks_df = cache.get(CACHE_MERGED_PEAKS_DF)
        features_dict = cache.get(CACHE_FEATURES_DICT)
        # Legacy output keeps its historical Intergenic fallback unchanged.
        gene_models = None
        if mode == "terminal-aware":
            gffdb = create_or_load_gffdb(layout.ReferenceFile(file_key=GFFDB, args=args))
            gene_models = get_gene_models(gffdb)
        # exec.
        annotated_merged_peaks_df = annotate_peaks_with_features(merged_peaks_df=merged_peaks_df,
                                                                 features_dict=features_dict,
                                                                 gene_models=gene_models)
        cache.set(CACHE_ANNOTATED_MERGED_PEAKS_DF, annotated_merged_peaks_df)

    def resolve_peak_issues_worker(item: Dict):
        # input
        annotated_merged_peaks_df = cache.get(CACHE_ANNOTATED_MERGED_PEAKS_DF)
        chrom_sizes = layout.ReferenceFile(file_key=FILTERED_STD_CHROM_SIZES, args=args)
        # output
        annotated_merged_peaks_bed = _annotate_file(item, "___peaks-annotated.bed")
        # exec.
        resolve_peak_issues(annotated_peaks_df=annotated_merged_peaks_df,
                            output_bed=annotated_merged_peaks_bed,
                            chrom_sizes_sorted=chrom_sizes,
                            include_gene_id=(mode == "terminal-aware"))

    # TODO: this part is commented in the original code, I'll leave it here for now
    # original logic
    # ----- Save Annotated Peaks with Sanity Checks -----
    # annotated_peaks_df = processed_peaks_df  # Assuming 'processed_peaks_df' is already annotated
    # annotated_peaks_bed = os.path.join(output_dir, f"{prefix_name}_annotated_peaks.bed")
    # write_annotated_peaks(annotated_peaks_df, annotated_peaks_bed)
    def sanity_check_and_save_annotated_peaks():
        pass

    def convert_peaks_bed_to_bigbed_worker(item: Dict):
        # input
        annotated_merged_peaks_bed = _annotate_file(item, "___peaks-annotated.bed")
        chrom_sizes = layout.ReferenceFile(file_key=FILTERED_STD_CHROM_SIZES, args=args)
        # output
        annotated_merged_peaks_bigbed = _annotate_file(item, "___peaks-annotated.bb")
        # exec
        convert_bed_to_bigbed(input_bed=annotated_merged_peaks_bed,
                              output_bigbed=annotated_merged_peaks_bigbed,
                              chrom_sizes=chrom_sizes)

    def convert_peaks_bed_to_gtf_worker(item: Dict):
        # input
        annotated_merged_peaks_bed = _annotate_file(item, "___peaks-annotated.bed")
        # output
        annotated_merged_peaks_gtf = _annotate_file(item, "___peaks-annotated.gtf")
        # exec.
        convert_peaks_bed_to_gtf(input_peaks_bed=annotated_merged_peaks_bed,
                                 output_peaks_gtf=annotated_merged_peaks_gtf,
                                 non_intronic_misprimed_ratio_threshold=(
                                     args.misprimed_ratio_threshold
                                     if mode == "terminal-aware"
                                     else None
                                 ))

    def log_gtf_features_worker(item: Dict):
        annotated_merged_peaks_gtf = _annotate_file(item, "___peaks-annotated.gtf")
        log_gtf_features(annotated_merged_peaks_gtf)

    # EXEC.
    with stage_scope(STAGE):
        identity_keys = ["dedup_method", "polyA_status", "coverage_scaling"]
        param_keys = ["depth_threshold", "depth_threshold_polyA", "merge_distance",
                      "merge_distance_polyA",
                      "misprime_A_count", "misprime_in", "misprimed_ratio_threshold",
                      "annotate_peak_source"]

        for (task, fn) in [
            ("assign_peaks_to_genes", assign_peaks_worker),
            ("merge_assigned_peaks", merge_assigned_peaks_worker),
        ]:
            with task_scope(task):
                run_flex_batch(tm=tm, stage=STAGE, task_base=task, items=[final_item],
                               identity_keys=identity_keys, param_keys=param_keys, fn=fn,
                               max_workers=args.n_workers)

        for (task, fn) in [
            ("merge_assigned_peaks", merge_assigned_peaks_worker_2),
            ("extract_genomic_features", extract_genomic_features_worker),
            ("classify_peaks", classify_peaks_worker),
        ]:
            with task_scope(task):
                if task in {
                    "extract_genomic_features",
                    "classify_peaks",
                }:
                    tm.run_inline_execution(
                        stage=STAGE,
                        task_base=task,
                        item=final_item,
                        fn=fn,
                        call_args=(final_item,),
                    )
                else:
                    fn(final_item)

        for (task, fn) in [
            ("resolve_peak_issues", resolve_peak_issues_worker),
            ("convert_peaks_bed_to_bigbed", convert_peaks_bed_to_bigbed_worker),
            ("convert_peaks_bed_to_gtf", convert_peaks_bed_to_gtf_worker),
        ]:
            with task_scope(task):
                run_flex_batch(tm=tm, stage=STAGE, task_base=task, items=[final_item],
                               identity_keys=identity_keys, param_keys=param_keys, fn=fn,
                               max_workers=args.n_workers)

        task = "convert_peaks_bed_to_gtf"
        with task_scope(task):
            log_gtf_features_worker(final_item)
