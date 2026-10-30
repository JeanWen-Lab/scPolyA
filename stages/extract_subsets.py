from __future__ import annotations

import csv
from pathlib import Path
from typing import List, TYPE_CHECKING

import pysam

from logging_config import stage_scope, task_scope, get_logger, LOG_MAIN, LoggersWrapper, LOG_STAGE_extract_subsets
from task_manage import ScItem  # 使用 ScItem
from task_manage.define import CACHE_unit_inputs, CACHE_polyA_status
from task_manage.pipeline_contracts import build_current_task_instance_contract_from_keys_v2
from task_manage.task_coordinate import build_task_coordinate_v1_from_contract
from task_manage.task_runtime import run_flex_batch, stage_level  # 使用 run_flex_batch
from utils.polya_extract import validate_polya_bam
from utils.read_eligibility import read_rule_from_args
from utils.polya_extract_runtime import (
    cleanup_stale_polya_temps,
    recover_interrupted_polya_publish,
    run_isolated_polya_extraction,
)

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

STAGE = LOG_STAGE_extract_subsets
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)


def extract_subsets(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    # region Preperation -----------------------------------------
    with stage_scope(STAGE):

        polyA_status_str = ScItem.fmt_polyA(args=args)
        cache.set(CACHE_polyA_status, polyA_status_str)

        csv_path = cache.get(CACHE_unit_inputs)
        if not csv_path.exists():
            loggers.raise_error(f"Missing unit inputs csv: {csv_path}.", FileNotFoundError)

        # ----------------------------------------------------
        # ScItem list construction

        items_extract_polyA: List[ScItem] = []
        items_split_strand: List[ScItem] = []

        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                uid = r['unit_id']
                dedup_status = r['dedup_status']
                raw_path = Path(r['resolved_path'])  # 这是 CSV 提供的绝对路径

                # [Item 1] Extract PolyA
                # 目标：生成 unit.polyA.bam
                # 输入：extra_info['src'] (原始 BAM)
                item_ext = ScItem(
                    uid=uid,
                    dedup_status=dedup_status,
                    is_polyA=True,
                    add_polyA=True,
                    add_dedup_method=True,
                    extra_info={"src": raw_path},
                    args=args
                )
                items_extract_polyA.append(item_ext)

                if args.materialize_strand_bams:
                    # Compatibility outputs only. Peak coverage reads the
                    # unstranded cohort BAMs directly and never consumes these.
                    items_split_strand.append(ScItem(
                        uid=uid,
                        dedup_status=dedup_status,
                        is_polyA=False,
                        add_dedup_method=True,
                        materialize_strand_bams=True,
                        extra_info={"src": raw_path},
                        args=args
                    ))
                    items_split_strand.append(ScItem(
                        uid=uid,
                        dedup_status=dedup_status,
                        is_polyA=True,
                        add_polyA=True,
                        add_dedup_method=True,
                        materialize_strand_bams=True,
                        # Source is the extract_polyA output.
                        args=args
                    ))

    def _split_strand(input_bam, plus_bam, minus_bam):
        try:
            with pysam.AlignmentFile(str(input_bam), "rb") as bam_in:
                with pysam.AlignmentFile(str(plus_bam), "wb", template=bam_in) as bam_plus, \
                        pysam.AlignmentFile(str(minus_bam), "wb", template=bam_in) as bam_minus:
                    for read in bam_in.fetch(until_eof=True):
                        if read.is_reverse:
                            bam_minus.write(read)
                        else:
                            bam_plus.write(read)
            pysam.index(str(plus_bam))
            pysam.index(str(minus_bam))
        except Exception as e:
            loggers.raise_error(f"Failed split strand: {e}")

    # Worker 1: Extract PolyA Reads
    def _polyA_output(item: ScItem):
        return layout.UnitOutput(item.uid) / item.format_name(suffix="bam")

    def flex_extract_polyA_worker(item: ScItem):
        in_bam = item.extra_info['src']

        out_bam = _polyA_output(item)

        if not in_bam.exists():
            raise FileNotFoundError(f"Input BAM not found: {in_bam}")

        stats = run_isolated_polya_extraction(
            input_bam=in_bam,
            output_bam=out_bam,
            nonA_allowed=args.nonA_allowed,
            minpolyA=args.minpolyA,
            minMAPQ=args.minMAPQ,
            read_rule=read_rule_from_args(args),
        )
        logger_stage.info(
            f"Created PolyA BAM: {out_bam.name} "
            f"(Retained: {stats.retained_reads}/{stats.total_reads})"
        )

    # Worker 2: Split by strand
    def flex_split_strand_worker(item: ScItem):
        # 输入：
        if item.extra_info is not None:
            # Case A: Split Raw (comes from CSV)
            in_bam = item.extra_info['src']
        else:
            # Case B: Split PolyA (comes from last step)
            in_bam = layout.UnitOutput(item.uid) / item.format_name(suffix="bam")

        if not in_bam.exists():
            # 这通常意味着上一步失败了，或者逻辑没对齐
            raise FileNotFoundError(f"Input BAM for splitting not found: {in_bam}")

        # 输出：派生出 Plus 和 Minus 文件名
        # 使用 ScItem.format_name 生成变体
        plus_bam = layout.UnitOutput(item.uid) / item.format_name(suffix="bam", strand="plus")
        minus_bam = layout.UnitOutput(item.uid) / item.format_name(suffix="bam", strand="minus")

        # 清理
        plus_bam.unlink(missing_ok=True)
        minus_bam.unlink(missing_ok=True)
        plus_bam.with_suffix(".bam.bai").unlink(missing_ok=True)
        minus_bam.with_suffix(".bam.bai").unlink(missing_ok=True)

        logger_stage.info(f"[{item.uid}] Splitting {in_bam.name} -> Plus/Minus")
        _split_strand(in_bam, plus_bam, minus_bam)

    # EXEC.
    with stage_scope(STAGE):

        # This parent-only preflight must run before compatibility DONE/reuse
        # decisions. extract_polyA is not guaranteed to pass through a reuse
        # health gate, so deferring recovery to a queued worker could otherwise
        # reuse an interrupted mixed BAM/index pair without executing the worker.
        regular_extract_items: List[ScItem] = []
        forced_extract_items: List[ScItem] = []
        for item in items_extract_polyA:
            out_bam = _polyA_output(item)
            if recover_interrupted_polya_publish(out_bam):
                logger_stage.warning(
                    f"[{item.uid}] Recovered interrupted PolyA BAM publication: "
                    f"{out_bam.name}"
                )
            removed_temps = cleanup_stale_polya_temps(out_bam)
            if removed_temps:
                logger_stage.warning(
                    f"[{item.uid}] Removed {len(removed_temps)} stale PolyA "
                    "temporary file(s) from a terminated parent."
                )
            contract = build_current_task_instance_contract_from_keys_v2(
                stage=STAGE,
                task_base="extract_polyA",
                item=item,
                identity_keys=["uid", "dedup_method", "polyA_status"],
                param_keys=["minpolyA", "minMAPQ"],
                level=stage_level(STAGE),
            )
            coordinate = build_task_coordinate_v1_from_contract(contract)
            if tm.check_task_done(coordinate):
                try:
                    validate_polya_bam(out_bam)
                except Exception as exc:
                    logger_stage.warning(
                        f"[{item.uid}] Completed PolyA output is unhealthy and "
                        f"will be recomputed at the same coordinate: {exc}"
                    )
                    forced_extract_items.append(item)
                    continue
            regular_extract_items.append(item)

        task1 = "extract_polyA"
        with task_scope(task1):
            for batch_items, force, desc in (
                (regular_extract_items, False, "Extract PolyA"),
                (forced_extract_items, True, "Repair PolyA Outputs"),
            ):
                if not batch_items:
                    continue
                run_flex_batch(
                    tm=tm,
                    stage=STAGE,
                    task_base=task1,
                    items=batch_items,
                    # Identity: 哪个 Unit，用什么去重方法，什么 PolyA 参数
                    identity_keys=["uid", "dedup_method", "polyA_status"],
                    param_keys=["minpolyA", "minMAPQ"],  # 这里的 param_keys 用于计算 variant_str
                    fn=flex_extract_polyA_worker,
                    force=force,
                    desc=desc,
                    max_workers=args.max_extract_workers,
                )
        if items_split_strand:
            task2 = "split_strand"
            with task_scope(task2):
                run_flex_batch(
                    tm=tm,
                    stage=STAGE,
                    task_base=task2,
                    items=items_split_strand,
                    identity_keys=["uid", "dedup_method", "polyA_status"],
                    # This explicit signature prevents historical implicit
                    # strand-split DONE rows from being guessed as reusable.
                    param_keys=["materialize_strand_bams"],
                    fn=flex_split_strand_worker,
                    desc="Materialize Strand BAMs",
                    max_workers=args.max_extract_workers,
                )

# region in-func helpers -----------------------------------------


# endregion
