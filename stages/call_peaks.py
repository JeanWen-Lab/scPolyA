from __future__ import annotations

from typing import TYPE_CHECKING, List, Dict

from logging_config import (
    stage_scope,
    get_logger,
    LOG_MAIN,
    LoggersWrapper,
    LOG_STAGE_call_peaks, suffix_scope, task_scope  # Needed to fetch UPM factors
)
from task_manage.run_layout import SORTED_CDS_BED, FILTERED_STD_CHROM_SIZES, SORTED_GENE_BED
from task_manage.define import CACHE_dedup_method, CACHE_upm_factor, \
    CACHE_upm_factor_polyA, CACHE_polyA_status,  \
    META_STANDARD_CHROMOSOMES
from task_manage.effective_config import build_final_peak_config, exp_coverage_scaled, polyA_coverage_scaled
from task_manage import ScItem
from task_manage.task_runtime import run_flex_batch
from utils.annotation_utils import negate_bedgraph
from utils.bedtools_utils import bedtools_generate_coverage, bedtools_subtract_coverage
from utils.io_utils import convert_bedgraph_to_bigwig
from utils.peak_utils import combine_and_intersect_peaks
from utils.read_eligibility import read_rule_from_args
from utils.pyranges_utils import call_and_merge_peaks_pyranges, compute_gene_dynamic_thresholds
from utils.terminal_quant_utils import quantification_artifact_suffix, quantification_mode
from utils.text_transform_utils import tt_scale_with_factor

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

STAGE = LOG_STAGE_call_peaks
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)


def call_peaks(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    with stage_scope(STAGE):
        mode = quantification_mode(args)
        read_rule = read_rule_from_args(args)
        exp_scaled = exp_coverage_scaled(args)
        polyA_scaled = polyA_coverage_scaled(args)


        def check_arg(val, name):
            if val is None:
                loggers.raise_error(f"Argument '{name}' is missing (None). Check your run script.", ValueError)
            return val

        if args.exp_peak_threshold_mode == "fixed":
            if exp_scaled:
                check_arg(args.scaled_depth_threshold, "--scaled-depth-threshold")
            else:
                check_arg(args.unscaled_depth_threshold, "--unscaled-depth-threshold")
        if args.polyA_peak_threshold_mode == "fixed":
            if polyA_scaled:
                check_arg(args.scaled_depth_threshold_polyA, "--scaled-depth-threshold-polyA")
            else:
                check_arg(args.unscaled_depth_threshold_polyA, "--unscaled-depth-threshold-polyA")

        sc_items: List[ScItem] = []

        upm_factor = None
        if exp_scaled:
            upm_factor = cache.get(CACHE_upm_factor)
        upm_factor_polyA = None
        if polyA_scaled:
            upm_factor_polyA = cache.get(CACHE_upm_factor_polyA)

        sc_items += [
            ScItem(strand=strand, scaling_factor=upm_factor,
                   is_cohort=True, is_polyA=False,
                   coverage_source="unstranded", strand_filter=strand,
                   add_dedup_method=True, args=args, )
            for strand in ["plus", "minus"]
        ]
        sc_items += [
            ScItem(strand=strand, scaling_factor=upm_factor_polyA,
                   is_cohort=True, is_polyA=True, add_polyA=True,
                   coverage_source="unstranded", strand_filter=strand,
                   add_dedup_method=True, args=args, )
            for strand in ["plus", "minus"]
        ]

        negate_items: List[ScItem] = []
        for _ in sc_items:
            if _.strand == "minus":
                negate_items.append(_)
                if not _.is_polyA:  # remove_CDS will be generated
                    negate_items.append(_.replace(remove_CDS=True))

        generate_bigwig_items: List[ScItem] = []
        for _ in sc_items:
            generate_bigwig_items.append(_)
            if _.strand == "minus":
                generate_bigwig_items.append(_.replace(is_negate=True))  # minus  neg
                if not _.is_polyA:
                    generate_bigwig_items.append(_.replace(is_negate=True, remove_CDS=True))  # minus remove_CDS neg
            if not _.is_polyA:
                generate_bigwig_items.append(_.replace(remove_CDS=True))  # remove_CDS neg

        final_item = build_final_peak_config(
            args,
            dedup_method=cache.get(CACHE_dedup_method),
            polyA_status=cache.get(CACHE_polyA_status),
            include_threshold_params=True,
        )
        final_output_root = layout.FinalOutputSub(final_item)

    def generate_raw_coverage_worker(item: ScItem):
        with suffix_scope(item.name):
            in_bam = (layout.CohortOutput(item.cohort_name) /
                      item.format_name(suffix="bam",
                                       strand=None,
                                       scaling_factor=None))  # overwrite scaling_factor for call_scaled_peaks
            out_bedgraph = (layout.CohortOutput(item.cohort_name) /
                            item.format_name(suffix="bedgraph", scaling_factor=None, is_coverage=True))
            out_bedgraph.unlink(missing_ok=True)

            local_log = layout.LogBedtools / f"bedtools_coverage.{item.name}.log"

            bedtools_generate_coverage(
                in_bam=in_bam,
                out_bedgraph=out_bedgraph,
                local_log=local_log,
                strand=item.strand_filter,
                read_rule=read_rule,
            )

    def upm_scale_worker(item: ScItem):
        scaling_factor = item.scaling_factor
        if scaling_factor is None:
            return

        with suffix_scope(item.name):
            in_bedgraph = (layout.CohortOutput(item.cohort_name) /
                           item.format_name(suffix="bedgraph", scaling_factor=None, is_coverage=True))

            out_bedgraph = (layout.CohortOutput(item.cohort_name) /
                            item.format_name(suffix="bedgraph", is_coverage=True))
            # remove overriding to recover the name with sf
            out_bedgraph.unlink(missing_ok=True)

            tt_scale_with_factor(in_bedgraph=in_bedgraph, out_bedgraph=out_bedgraph, scaling_factor=scaling_factor)

    def exclude_cds_region_worker(item: ScItem):
        with suffix_scope(item.name):
            if item.is_polyA:
                return
            sorted_cds_bed = layout.ReferenceFile(file_key=SORTED_CDS_BED, args=args)
            # print(sorted_cds_bed)
            in_bedgraph = (layout.CohortOutput(item.cohort_name) /
                           item.format_name(suffix="bedgraph", is_coverage=True))  # universal name

            out_bedgraph = (layout.CohortOutput(item.cohort_name) /
                            item.format_name(suffix="bedgraph", is_coverage=True, remove_CDS=True))

            logger_stage.info(f"Subtracting CDS bases from coverage file '{in_bedgraph}'...")
            bedtools_subtract_coverage(in_bedgraph=in_bedgraph, cds_bed=sorted_cds_bed,
                                      out_bedgraph=out_bedgraph)

    def negate_worker(item: ScItem):
        with suffix_scope(item.name):
            in_bedgraph = (layout.CohortOutput(item.cohort_name) /
                           item.format_name(suffix="bedgraph", is_coverage=True))
            neg_bedgraph = (layout.CohortOutput(item.cohort_name) /
                            item.format_name(suffix="bedgraph", is_coverage=True, is_negate=True))

            neg_bedgraph.unlink(missing_ok=True)
            negate_bedgraph(in_bedgraph=in_bedgraph, neg_bedgraph=neg_bedgraph)

    def generate_bigwig_worker(item: ScItem):
        # main
        with suffix_scope(item.name):
            standard_chrom_sizes = layout.ReferenceFile(file_key=FILTERED_STD_CHROM_SIZES, args=args)
            local_log = layout.LogBedGraphToBigWig / f"generate.bigwig.{item.name}.log"

            in_bedgraph = (layout.CohortOutput(item.cohort_name) /
                           item.format_name(suffix="bedgraph", is_coverage=True))
            out_bigwig = (layout.CohortOutput(item.cohort_name) /
                          item.format_name(suffix="bw", is_coverage=True))
            convert_bedgraph_to_bigwig(in_bedgraph=in_bedgraph, out_bigwig=out_bigwig,
                                       standard_chrom_sizes=standard_chrom_sizes, local_log=local_log)
            # negate

    def merge_call_peaks_worker(item: ScItem):
        remove_CDS = (not item.is_polyA)  # for expression peaks, we use remove_CDS version

        in_bedgraph = (layout.CohortOutput(item.cohort_name) /
                       item.format_name(suffix="bedgraph", is_coverage=True,
                                        remove_CDS=remove_CDS))  # unscaled/scaled universal

        # TODO: check if this is necessary
        # peaks_bed = (layout.CohortOutput(item.cohort_name) /
        #                 item.format_name(suffix="bedgraph", is_coverage=True, remove_CDS=remove_CDS, is_peak=True))

        merged_peaks_bed = (layout.CohortOutput(item.cohort_name) /
                            item.format_name(suffix="bed", is_coverage=True, remove_CDS=remove_CDS,
                                             is_merged_peak=True, add_threshold=True, add_merge_dist=True))
        merged_peaks_bed.unlink(missing_ok=True)

        threshold_mode = item.peak_threshold_mode
        dynamic_threshold_tsv = None
        if threshold_mode == "dynamic":
            gene_bed = layout.ReferenceFile(file_key=SORTED_GENE_BED, args=args)
            dynamic_threshold_tsv = (layout.CohortOutput(item.cohort_name) /
                                     item.format_name(suffix="dynamic-threshold.tsv",
                                                      is_coverage=True,
                                                      remove_CDS=remove_CDS,
                                                      add_threshold=True))
            dynamic_threshold_tsv.unlink(missing_ok=True)

            compute_gene_dynamic_thresholds(
                coverage_file=in_bedgraph,
                gene_bed_file=gene_bed,
                output_tsv=dynamic_threshold_tsv,
                strand=item.strand,
                stat=item.dynamic_threshold_stat,
                alpha=item.dynamic_threshold_alpha,
                floor=item.dynamic_threshold_floor,
                normalized=item.dynamic_threshold_normalized
            )

        call_and_merge_peaks_pyranges(coverage_file=in_bedgraph,
                                      merged_peaks_bed=merged_peaks_bed,
                                      depth_threshold=item.depth_threshold if threshold_mode == "fixed" else None,
                                      strand=item.strand,
                                      slack=item.merge_distance,
                                      threshold_mode=threshold_mode,
                                      gene_bed_file=layout.ReferenceFile(file_key=SORTED_GENE_BED, args=args),
                                      dynamic_threshold_tsv=dynamic_threshold_tsv)

    def combine_and_intersect_peaks_worker(item: Dict):
        _dedup_method = item["dedup_method"]
        _polyA_status = item["polyA_status"]
        misprimed_ratio_threshold: float = item["misprimed_ratio_threshold"]
        misprime_A_count: int = item["misprime_A_count"]
        misprime_in: int = item["misprime_in"]

        merge_distance = item["merge_distance"]
        # TODO: verify that merge_distance_polyA is not required

        final_bed = final_output_root / layout.FinalFile(
            suffix=quantification_artifact_suffix(
                "___peaks-mispriming-filtered.bed", mode
            ),
            d=item,
        )
        # Previous outputs are replaced only by a complete new set
        # (combine_and_intersect_peaks commits with rollback).

        # item_exp_plus, item_exp_minus, item_polyA_plus, item_polyA_minus = sc_items
        for _ in sc_items:
            if _.is_polyA:
                if _.strand == "plus":
                    item_polyA_plus = _

                else:
                    item_polyA_minus = _
            else:
                if _.strand == "plus":
                    item_exp_plus = _
                else:
                    item_exp_minus = _

        exp_plus_peaks_bed = (layout.CohortOutput(item_exp_plus.cohort_name) /
                              item_exp_plus.format_name(suffix="bed", is_coverage=True, remove_CDS=True,
                                                        is_merged_peak=True, add_threshold=True, add_merge_dist=True))
        exp_minus_peaks_bed = (layout.CohortOutput(item_exp_minus.cohort_name) /
                               item_exp_minus.format_name(suffix="bed", is_coverage=True, remove_CDS=True,
                                                          is_merged_peak=True, add_threshold=True, add_merge_dist=True))
        polyA_plus_peaks_bed = (layout.CohortOutput(item_polyA_plus.cohort_name) /
                                item_polyA_plus.format_name(suffix="bed", is_coverage=True, remove_CDS=False,
                                                            is_merged_peak=True, add_threshold=True,
                                                            add_merge_dist=True))
        polyA_minus_peaks_bed = (layout.CohortOutput(item_polyA_minus.cohort_name) /
                                 item_polyA_minus.format_name(suffix="bed", is_coverage=True, remove_CDS=False,
                                                              is_merged_peak=True, add_threshold=True,
                                                              add_merge_dist=True))
        exp_bam = (layout.CohortOutput(item_exp_plus.cohort_name) /
                   ScItem.cls_format_name(suffix="bam", is_cohort=True, add_dedup_method=True, args=args))
        polyA_bam = (layout.CohortOutput(item_polyA_plus.cohort_name) /
                   ScItem.cls_format_name(suffix="bam", is_cohort=True, is_polyA=True, add_polyA=True,
                                          add_dedup_method=True, args=args))
        standard_chrom_sizes_path = layout.ReferenceFile(file_key=FILTERED_STD_CHROM_SIZES, args=args)

        effective_misprimed_ratio_threshold = misprimed_ratio_threshold
        if mode == "terminal-aware":
            effective_misprimed_ratio_threshold = 1.0
            logger_stage.info(
                "Terminal-aware quantification defers mispriming exclusion until "
                "after peak feature annotation; retaining all assessed peaks here."
            )

        combine_and_intersect_peaks(exp_plus_peaks_bed=exp_plus_peaks_bed,
                                    exp_minus_peaks_bed=exp_minus_peaks_bed,
                                    polyA_plus_peaks_bed=polyA_plus_peaks_bed,
                                    polyA_minus_peaks_bed=polyA_minus_peaks_bed,
                                    final_output_bed=final_bed,
                                    exp_bam=exp_bam,
                                    polyA_bam=polyA_bam,
                                    standardized_chrom_sizes=standard_chrom_sizes_path,
                                    standard_chroms=cache.get(key=META_STANDARD_CHROMOSOMES),
                                    merge_distance=merge_distance,
                                    misprime_A_count=misprime_A_count,
                                    misprime_in=misprime_in,
                                    misprimed_ratio_threshold=effective_misprimed_ratio_threshold,
                                    include_unassessed_for_audit=(mode == "terminal-aware"),
                                    )

    with stage_scope(STAGE):
        task1 = "generate_raw_coverage"
        with task_scope(task1):
            run_flex_batch(tm=tm, stage=STAGE, task_base=task1, items=sc_items,
                           identity_keys=["strand", "is_polyA", "coverage_source", "strand_filter"],
                           param_keys=["dedup_method", "polyA_status"],
                           fn=generate_raw_coverage_worker,
                           max_workers=args.n_workers,
                           )

        task2 = "upm_scale_coverage"
        with task_scope(task2):
            run_flex_batch(tm=tm, stage=STAGE, task_base=task2, items=sc_items,
                           identity_keys=["strand", "is_polyA", "scaling_factor"],
                           param_keys=["dedup_method", "polyA_status"],
                           fn=upm_scale_worker,
                           max_workers=args.n_workers,
                           )

        task3 = "exclude_cds_region"
        with task_scope(task3):
            run_flex_batch(tm=tm, stage=STAGE, task_base=task3, items=sc_items,
                           identity_keys=["strand", "is_polyA", "scaling_factor"],
                           param_keys=["dedup_method", "polyA_status"],
                           fn=exclude_cds_region_worker,
                           max_workers=args.n_workers,
                           )

        task4 = "negate_bedgraph"
        with task_scope(task4):
            run_flex_batch(tm=tm, stage=STAGE, task_base=f"{task4}", items=negate_items,
                           identity_keys=["strand", "is_polyA", "scaling_factor", "remove_CDS"],
                           param_keys=["dedup_method", "polyA_status"],
                           fn=negate_worker,
                           max_workers=args.n_workers)

        task5 = "generate_bigwig"
        with task_scope(task5):
            run_flex_batch(tm=tm, stage=STAGE, task_base=f"{task5}", items=generate_bigwig_items,
                           identity_keys=["strand", "is_polyA", "scaling_factor", "remove_CDS", "is_negate"],
                           param_keys=["dedup_method", "polyA_status", "bigwig_conversion_schema"],
                           fn=generate_bigwig_worker,
                           max_workers=args.n_workers)

        task6 = "merge_call_peaks"
        with task_scope(task6):
            run_flex_batch(tm=tm, stage=STAGE, task_base=task6, items=sc_items,
                           identity_keys=["strand", "is_polyA", "scaling_factor", "remove_CDS"],
                           param_keys=["dedup_method", "polyA_status", "depth_threshold", "merge_distance",
                                       "peak_threshold_mode", "dynamic_threshold_stat",
                                       "dynamic_threshold_alpha", "dynamic_threshold_floor",
                                       "dynamic_threshold_normalized"],
                           fn=merge_call_peaks_worker,
                           max_workers=args.n_workers)

        task7 = "combine_and_intersect_peaks"
        with task_scope(task7):
            run_flex_batch(tm=tm, stage=STAGE, task_base=task7, items=[final_item],
                           identity_keys=["dedup_method", "polyA_status", "coverage_scaling"],
                           param_keys=["depth_threshold", "depth_threshold_polyA", "merge_distance",
                                       "merge_distance_polyA",
                                       "misprime_A_count", "misprime_in",
                                       "misprimed_ratio_threshold",
                                       "exp_peak_threshold_mode", "polyA_peak_threshold_mode",
                                       "exp_dynamic_threshold_stat", "polyA_dynamic_threshold_stat",
                                       "exp_dynamic_threshold_alpha", "polyA_dynamic_threshold_alpha",
                                       "exp_dynamic_threshold_floor", "polyA_dynamic_threshold_floor",
                                       "exp_dynamic_threshold_normalized", "polyA_dynamic_threshold_normalized"],
                           fn=combine_and_intersect_peaks_worker,
                           max_workers=args.n_workers)

# region deprecated
#
# (def call_peaks:)
#     def generate_bigwig_worker_1(item: ScItem):
#         with suffix_scope(item.name):
#             # main
#             standard_chrom_sizes = cache.get(CACHE_std_chrom_sizes_path)
#             local_log = layout.LogWigToBigWig / f"generate.bigwig.{item.name}.log"
#
#             if item.strand == "minus":
#                 neg_bedgraph = (layout.CohortOutput(item.cohort_name) /
#                                 item.format_name(suffix="bedgraph", is_coverage=True, is_negate=True))
#                 neg_bigwig = (layout.CohortOutput(item.cohort_name) /
#                               item.format_name(suffix="bw", is_coverage=True, is_negate=True))
#                 convert_bedgraph_to_bigwig(in_bedgraph=neg_bedgraph, out_bigwig=neg_bigwig,
#                                            standard_chrom_sizes=standard_chrom_sizes, local_log=local_log)
#
#     def generate_bigwig_worker_2(item: ScItem):
#         with suffix_scope(item.name):
#             # removeCDS
#             standard_chrom_sizes = cache.get(CACHE_std_chrom_sizes_path)
#             local_log = layout.LogWigToBigWig / f"generate.bigwig.{item.name}.log"
#             if not item.is_polyA:
#                 logger_main.error(item.name)
#                 remove_cds_bedgraph = (layout.CohortOutput(item.cohort_name) /
#                                        item.format_name(suffix="bedgraph", is_coverage=True, remove_CDS=True))
#                 remove_cds_bigwig = (layout.CohortOutput(item.cohort_name) /
#                                      item.format_name(suffix="bw", is_coverage=True, remove_CDS=True))
#                 convert_bedgraph_to_bigwig(in_bedgraph=remove_cds_bedgraph, out_bigwig=remove_cds_bigwig,
#                                            standard_chrom_sizes=standard_chrom_sizes, local_log=local_log)
#
#     def generate_bigwig_worker_3(item: ScItem):
#         with suffix_scope(item.name):
#             standard_chrom_sizes = cache.get(CACHE_std_chrom_sizes_path)
#             local_log = layout.LogWigToBigWig / f"generate.bigwig.{item.name}.log"
#             if not item.is_polyA and item.strand == "minus":
#                 neg_remove_cds_bedgraph = (layout.CohortOutput(item.cohort_name) /
#                                            item.format_name(suffix="bedgraph", is_coverage=True, is_negate=True,
#                                                             remove_CDS=True))
#                 neg_remove_cds_bigwig = (layout.CohortOutput(item.cohort_name) /
#                                          item.format_name(suffix="bw", is_coverage=True, is_negate=True,
#                                                           remove_CDS=True))
#                 convert_bedgraph_to_bigwig(in_bedgraph=neg_remove_cds_bedgraph, out_bigwig=neg_remove_cds_bigwig,
#                                            standard_chrom_sizes=standard_chrom_sizes, local_log=local_log)


#  def negate_worker_0(item: ScItem):
#     with suffix_scope(item.name):
#         if item.strand == "plus":
#             logger_stage.info(f"Skip negation.")
#             return
#
#         in_bedgraph = (layout.CohortOutput(item.cohort_name) /
#                        item.format_name(suffix="bedgraph", is_coverage=True))
#         neg_bedgraph = (layout.CohortOutput(item.cohort_name) /
#                         item.format_name(suffix="bedgraph", is_coverage=True, is_negate=True))
#         neg_bedgraph.unlink(missing_ok=True)
#         negate_bedgraph(in_bedgraph=in_bedgraph, neg_bedgraph=neg_bedgraph)
#
# def negate_worker_2(item: ScItem):
#     with suffix_scope(item.name):
#         if item.strand == "plus":
#             logger_stage.info(f"Skip negation.")
#             return
#         if not item.is_polyA:
#             remove_cds_bedgraph = (layout.CohortOutput(item.cohort_name) /
#                                    item.format_name(suffix="bedgraph", is_coverage=True, remove_CDS=True))
#             neg_remove_cds_bedgraph = (layout.CohortOutput(item.cohort_name) /
#                                        item.format_name(suffix="bedgraph", is_coverage=True, is_negate=True,
#                                                         remove_CDS=True))
#             neg_remove_cds_bedgraph.unlink(missing_ok=True)
#             negate_bedgraph(in_bedgraph=remove_cds_bedgraph, neg_bedgraph=neg_remove_cds_bedgraph)


# endregion
