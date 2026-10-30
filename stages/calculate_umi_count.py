from __future__ import annotations

import csv
import subprocess
from pathlib import Path
from typing import List, TYPE_CHECKING

from logging_config import (
    LOG_MAIN,
    LOG_STAGE_calculate_umi,
    LoggersWrapper,
    get_logger,
    run_tool_command,
    stage_scope,
    task_scope,
)
from task_manage.define import CACHE_unit_inputs, CACHE_polyA_status, CACHE_upm_factor, CACHE_upm_factor_polyA, \
    CACHE_dedup_method
from task_manage import ScItem  # 使用 ScItem
from task_manage.task_runtime import run_flex_batch  # 使用 run_flex_batch

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

STAGE = LOG_STAGE_calculate_umi
# 注意：Flex Batch 下不需要区分 STAGE_UNIT/STAGE_GLOBAL，统一用 STAGE 即可，
# 或者保留原有的区分以便在 DB 里更好看。这里我们统一用 STAGE。
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)


def calculate_umi_count(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    with stage_scope(STAGE):

        # 1. 准备配置
        run_exp = args.coverage_scaling in ("expr", "both")
        run_polyA = args.coverage_scaling in ("polyA", "both")

        if not run_exp and not run_polyA:
            loggers.info("Skipped upm factor calculation (no scaled peaks requested).")
            return

        polyA_status_str = cache.get(CACHE_polyA_status)
        dedup_method = cache.get(CACHE_dedup_method)

        csv_path = cache.get(CACHE_unit_inputs)
        if not csv_path.exists():
            loggers.raise_error(f"Missing unit inputs csv: {csv_path}.", FileNotFoundError)

        # 2. 构建 Items
        items_exp: List[ScItem] = []
        items_polyA: List[ScItem] = []

        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                uid = r['unit_id']
                dedup_status = r['dedup_status']
                # raw_path 是 CSV 里的路径，用于 exp 统计
                raw_path = Path(r['resolved_path'])

                if run_exp:
                    # Item for Expression Count (Raw / Deduped)
                    item_cnt = ScItem(
                        uid=uid,
                        dedup_status=dedup_status,
                        is_polyA=False,
                        add_dedup_method=True,
                        extra_info={"src": raw_path},  # Exp 直接统计原始输入
                        args=args
                    )
                    items_exp.append(item_cnt)

                if run_polyA:
                    # Item for PolyA Count
                    # 输入：之前 extract_subsets 生成的 polyA bam
                    # 我们不需要在这里显式指定 src，worker 可以自己推导
                    item_cnt = ScItem(
                        uid=uid,
                        dedup_status=dedup_status,
                        is_polyA=True,
                        add_polyA=True,
                        add_dedup_method=True,
                        args=args
                    )
                    items_polyA.append(item_cnt)

        # 3. 定义 Worker
        def flex_count_worker(item: ScItem):
            # 确定输入文件
            if item.is_polyA:
                # PolyA: 推导 extract_subsets 的输出
                target_bam = layout.UnitOutput(item.uid) / item.format_name(suffix="bam")
            else:
                # Exp: 使用 extra_info 里的原始路径
                target_bam = item.extra_info['src']

            # 确定输出文件 (.txt)
            output_txt = layout.UnitOutput(item.uid) / item.format_name(suffix="umi_count.txt")

            output_txt.unlink(missing_ok=True)

            if not target_bam.exists():
                raise FileNotFoundError(f"Target BAM for counting not found: {target_bam}")

            # 执行 samtools count
            try:
                # -c: count, -@ 1: threads
                cmd = ["samtools", "view", "-c", "-@", "1", str(target_bam)]
                count_kind = "polyA" if item.is_polyA else "expression"
                local_log = layout.LogSamtools / f"count.{item.uid}.{count_kind}.log"
                proc = run_tool_command(
                    cmd,
                    tool="samtools",
                    operation=f"count.{item.uid}.{count_kind}",
                    compatibility_log=local_log,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                count = int(proc.stdout.strip())

                with open(output_txt, "w") as f:
                    f.write(str(count))

                # 可选：记录到 logger (debug级别以免刷屏)
                logger_stage.debug(f"[{item.uid}] Count={count} ({'PolyA' if item.is_polyA else 'Exp'})")

            except (subprocess.CalledProcessError, ValueError) as e:
                loggers.raise_error(f"Failed to count {target_bam}: {e}")

        # 4. 汇总与计算 Factor (Global Step)
        # 这一步非常快，直接在主进程做，不需要封装成 Task，
        # 但我们需要把计算结果存入 tm.task_meta_set 以便持久化和后续查询。

        def _aggregate_and_store(items: List[ScItem], task_base: str):
            total_count = 0
            for item in items:
                txt_file = layout.UnitOutput(item.uid) / item.format_name(suffix="umi_count.txt")
                if txt_file.exists():
                    with open(txt_file, "r") as f:
                        total_count += int(f.read().strip())
                else:
                    loggers.warning(f"Missing count file: {txt_file}")

            # 计算 Factor
            factor = (1e6 / total_count) if total_count > 0 else 0.0

            # 构造 meta key
            # 为了区分 Exp 和 PolyA，我们可以用 meta_key_prefix
            # Exp: key="umi_count", "upm_factor"
            # PolyA: key="umi_count", "upm_factor" (但是 context/params 不同)

            # 这里为了简单，我们直接把结果存入 TaskMeta，使用特殊的 key 或 params
            # 或者更直接地：存入 Global Meta (run_meta_set) 或者 Cache

            type_label = "PolyA" if items[0].is_polyA else "Exp"

            # 存入 Cache (供当前进程后续使用)
            if items[0].is_polyA:
                cache.set(CACHE_upm_factor_polyA, factor)
                # 持久化到 DB (Global Task Meta)
                # 使用特殊的 task_base="global_calc" 和 variant="polyA" 来区分
                tm.task_meta_set(key="umi_count", value=total_count, stage=STAGE, task_base=task_base,
                                 context_params={"dedup_method": dedup_method},
                                 task_params={"polyA_status": polyA_status_str},
                                 )
                tm.task_meta_set(key="upm_factor", value=factor, stage=STAGE, task_base=task_base,
                                 context_params={"dedup_method": dedup_method},
                                 task_params={"polyA_status": polyA_status_str},
                                 )
            else:
                cache.set(CACHE_upm_factor, factor)
                tm.task_meta_set(key="umi_count", value=total_count, stage=STAGE, task_base=task_base,
                                 context_params={"dedup_method": dedup_method})
                tm.task_meta_set(key="upm_factor", value=factor, stage=STAGE, task_base=task_base,
                                 context_params={"dedup_method": dedup_method})

            loggers.info(f"[{type_label}] Total UMI: {total_count}, Factor: {factor}")

        # Batch 1: Exp Counts
        task1 = "count_umi_exp"
        with task_scope(task1):
            if items_exp:
                run_flex_batch(
                    tm=tm,
                    stage=STAGE,  # 或者 STAGE_UNIT
                    task_base=task1,
                    items=items_exp,
                    identity_keys=["uid", "dedup_method"],
                    param_keys=[],
                    fn=flex_count_worker,
                    desc="Count UMI (Exp)",
                    max_workers=args.n_workers,
                )

        # Batch 2: PolyA Counts
        task2 = "count_umi_polyA"
        with task_scope(task2):
            if items_polyA:
                run_flex_batch(
                    tm=tm,
                    stage=STAGE,
                    task_base=task2,
                    items=items_polyA,
                    identity_keys=["uid", "dedup_method", "polyA_status"],
                    param_keys=[],
                    fn=flex_count_worker,
                    desc="Count UMI (PolyA)",
                    max_workers=args.n_workers,
                )

        task3 = "calculate_umi"
        def calculate_umi_inline():
            if items_exp:
                _aggregate_and_store(items_exp, task3)

            if items_polyA:
                _aggregate_and_store(items_polyA, task3)

        with task_scope(task3):
            if items_exp or items_polyA:
                tm.run_inline_execution(
                    stage=STAGE,
                    task_base=task3,
                    item={},
                    fn=calculate_umi_inline,
                )
