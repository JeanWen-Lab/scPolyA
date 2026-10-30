from __future__ import annotations

import csv
import resource
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Dict, TYPE_CHECKING, List

import pysam

from logging_config import (
    LOG_MAIN,
    LOG_STAGE_merge_units,
    LoggersWrapper,
    get_logger,
    run_tool_command,
    stage_scope,
    task_scope,
)
from task_manage.define import CACHE_unit_inputs
from task_manage import ScItem
from task_manage.task_runtime import run_flex_batch  # <--- 使用 run_flex_batch

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

STAGE = LOG_STAGE_merge_units
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)


def _maximize_open_files_limit():
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(4096, hard)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except:
        pass


def _get_header_order(bam_path: Path) -> Dict[str, int]:
    if not bam_path.exists():
        raise FileNotFoundError(f"Missing file for header reading: {bam_path}")
    with pysam.AlignmentFile(str(bam_path), "rb") as f:
        return {chrom: i for i, chrom in enumerate(f.references)}


def merge_units(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    with stage_scope(STAGE):
        _maximize_open_files_limit()

        # 1. 准备目录
        if hasattr(args, "merge_temp_dir") and args.merge_temp_dir:
            temp_root = Path(args.merge_temp_dir)
            logger_stage.info(f"Using EXTERNAL temp directory: {temp_root}")
        else:
            temp_root = layout.MergedCohort
            logger_stage.info(f"Using local temp directory: {temp_root}")
        temp_root.mkdir(parents=True, exist_ok=True)

        # 2. 加载元数据 & 归类文件
        csv_path = cache.get(CACHE_unit_inputs)

        # grouping: key -> { ctg -> [path1, path2...] }
        # key 可以是简单的 tuple，方便后续解包
        # Key structure: (is_polyA, strand)
        # 注意：dedup_method 在整个任务中是统一的，所以不需要作为 key 区分，但在 item 里要记录
        grouped_files = defaultdict(lambda: defaultdict(list))

        template_bam = None

        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                _uid = r['unit_id']
                ctg = r['contig_name']
                _dedup_status = r['dedup_status']
                unit_base = layout.UnitOutput(_uid)

                # Unstranded expression and PolyA cohorts are always built.
                # They are the sole coverage inputs. Physical strand cohorts
                # are an explicit compatibility-output branch.
                variants = [
                    (False, None, Path(r["resolved_path"])),
                    (
                        True,
                        None,
                        unit_base / ScItem.cls_format_name(
                            suffix="bam",
                            uid=_uid,
                            dedup_status=_dedup_status,
                            is_polyA=True,
                            add_polyA=True,
                            args=args,
                        ),
                    ),
                ]
                if args.materialize_strand_bams:
                    for is_polyA in (False, True):
                        for strand in ("plus", "minus"):
                            variants.append((
                                is_polyA,
                                strand,
                                unit_base / ScItem.cls_format_name(
                                    suffix="bam",
                                    uid=_uid,
                                    dedup_status=_dedup_status,
                                    strand=strand,
                                    is_polyA=is_polyA,
                                    add_polyA=is_polyA,
                                    args=args,
                                ),
                            ))

                for is_polyA, _strand, _path in variants:
                    grouped_files[(is_polyA, _strand)][ctg].append(_path)

                    # 捕获一个存在的 BAM 用作 Header 模板
                    if template_bam is None and Path(_path).exists():
                        template_bam = Path(_path)

        if not template_bam:
            loggers.raise_error("No BAM fragments found.")

        # 3. 获取权威排序
        chrom_rank = _get_header_order(template_bam)
        sorted_chroms = sorted(chrom_rank.keys(), key=lambda k: chrom_rank[k])
        logger_stage.info(f"Chromosome order detected: {sorted_chroms} ...")

        # ---------------------------------------------------------------------
        # Phase 1: 构建 Merge Items (用于 run_flex_batch)
        # ---------------------------------------------------------------------
        merge_items: List[ScItem] = []

        for (is_polyA, _strand), ctg_map in grouped_files.items():
            for ctg, file_list in ctg_map.items():
                if not file_list: continue

                # 构建 ScItem
                # 我们把输入文件列表放在 extra_info 里，彻底解耦 worker 和外部变量
                _item = ScItem(
                    ctg=ctg,
                    strand=_strand,
                    is_polyA=is_polyA,
                    args=args,  # 如果 ScItem 内部需要 args 计算属性
                    add_dedup_method=True,
                    add_polyA=is_polyA,
                    is_cohort=True,  # 这是一个 Cohort 级别的合并操作
                    extra_info={"input_files": file_list}
                )
                merge_items.append(_item)

        # ---------------------------------------------------------------------
        # Phase 1 Worker: Merge (Strand/PolyA/Contig aware)
        # ---------------------------------------------------------------------
        def flex_merge_worker(item: ScItem):

            # 1. 获取输入
            inputs = item.extra_info.get("input_files", [])

            # 过滤掉不存在的文件 (防守性编程，虽然上面 append 时应该都是存在的路径对象，但不一定 exists)
            # valid_inputs = [str(p) for p in inputs if p.exists()]
            valid_inputs = inputs
            for _ in valid_inputs:
                if not _.exists():
                    loggers.raise_error(f"{_}")


            if not valid_inputs:
                return

            # 2. 定义输出：Temp Chunk
            # 这里的命名逻辑需要与 Cat 阶段一致。
            # 我们使用 NBAM 生成临时文件名，必须包含 sid(cohort_name) 和 ctg
            # item.cohort_name 会根据 is_polyA/dedup 自动生成类似 'cohort.row_UMI.polyA-xxx' 的名字
            # item.name 会包含 strand (如果 strand 不为 None)

            # 为了确保 chunk 文件名唯一且能在 Cat 阶段找到，我们需要一个确定的命名规则
            # 建议: temp_root / {cohort_name_with_strand}.{ctg}.chunk.bam

            # 构建一个代表 "Strand Level Cohort" 的名字
            # cohort_strand_name = item.cohort_name
            # if item.strand:
            #     cohort_strand_name += f".{item.strand}"

            out_bam = temp_root / item.format_name(suffix="bam", prefix="chunk")

            # 3. 执行 Merge
            out_bam.unlink(missing_ok=True)
            logger_stage.info(f"[{item.name}] Merging {len(valid_inputs)} files to {out_bam.name}")

            if len(valid_inputs) > 1:
                merge_cmd = (["samtools", "merge", "-@", str(args.merge_thread), str(out_bam)] + valid_inputs)
                local_log = layout.LogSamtools / f"merge.{item.name}.log"  # item.name 包含所有细节用于日志名

                try:
                    with local_log.open("a") as f:
                        run_tool_command(
                            merge_cmd,
                            tool="samtools",
                            operation=f"merge.{item.name}",
                            compatibility_log=local_log,
                            check=True,
                            stdout=f,
                            stderr=subprocess.STDOUT,
                            text=True,
                        )
                except subprocess.CalledProcessError:
                    raise Exception(f"Failed to merge into '{out_bam}'")
            elif len(valid_inputs) == 1:
                out_bam.symlink_to(Path(valid_inputs[0]).resolve())

        # ---------------------------------------------------------------------
        # Phase 2: 构建 Cat Items
        # ---------------------------------------------------------------------
        cat_items: List[ScItem] = []

        # Cat 任务是按 (is_polyA, strand) 聚合所有 Chromosomes
        # 我们只需遍历 grouped_files 的 key 即可，因为每个 key 对应一个最终文件
        for (is_polyA, _strand) in grouped_files.keys():
            _item = ScItem(
                # ctg
                strand=_strand,
                is_cohort=True,
                is_polyA=is_polyA,
                add_polyA=is_polyA,
                add_dedup_method=True,
                args=args
            )
            cat_items.append(_item)

        # ---------------------------------------------------------------------
        # Phase 2 Worker: Cat (Concatenate Chunks)
        # ---------------------------------------------------------------------
        def flex_cat_worker(item: ScItem):

            final_bam = layout.CohortOutput(item.cohort_name) / item.format_name("bam")
            final_bam.unlink(missing_ok=True)
            final_bam.with_suffix(".bam.bai").unlink(missing_ok=True)

            # 2. Collect the exact branch chunks in BAM-header order. Chunks
            # are normally deleted after a successful cat. If the retained
            # cohort later fails health checking, rebuild missing chunks from
            # their authoritative unit inputs before repairing the cohort.
            branch_merge_items = [
                merge_item
                for merge_item in merge_items
                if merge_item.is_polyA == item.is_polyA
                and merge_item.strand == item.strand
            ]
            branch_merge_items.sort(key=lambda merge_item: chrom_rank[merge_item.ctg])
            if not branch_merge_items:
                raise RuntimeError(f"[{item.name}] No merge inputs are defined for cohort cat")

            chunk_files = []
            for merge_item in branch_merge_items:
                chunk = temp_root / merge_item.format_name(suffix="bam", prefix="chunk")
                if not chunk.exists():
                    logger_stage.info(
                        f"[{item.name}] Rebuilding missing chunk before cohort repair: {chunk.name}"
                    )
                    flex_merge_worker(merge_item)
                if not chunk.exists():
                    raise FileNotFoundError(
                        f"[{item.name}] Required cohort chunk was not created: {chunk}"
                    )
                chunk_files.append(str(chunk))

            logger_stage.info(f"[{item.name}] Concatenating {len(chunk_files)} chunks...")
            logger_stage.info(f"[{item.name}] Concat order: {chunk_files}")

            # 3. Samtools Cat
            local_log = layout.LogSamtools / f"cat.{item.name}.log"
            cat_cmd = ["samtools", "cat", "-o", str(final_bam)] + chunk_files

            with local_log.open("a") as f:
                run_tool_command(
                    cat_cmd,
                    tool="samtools",
                    operation=f"cat.{item.name}",
                    compatibility_log=local_log,
                    check=True,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

            # 4. Index
            try:
                with local_log.open("a") as f:
                    run_tool_command(
                        ["samtools", "index", str(final_bam)],
                        tool="samtools",
                        operation=f"cat.{item.name}.index",
                        compatibility_log=local_log,
                        check=True,
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
            except subprocess.CalledProcessError as e:
                final_bam.with_suffix('.bam.bai').unlink(missing_ok=True)
                raise Exception(f"Failed to index '{final_bam}': {e}")

            # 5. Cleanup
            if not args.keep_merge_temp:
                for _ in chunk_files:
                    Path(_).unlink(missing_ok=True)

        unstranded_merge_items = [item for item in merge_items if item.strand is None]
        strand_merge_items = [item for item in merge_items if item.strand is not None]
        unstranded_cat_items = [item for item in cat_items if item.strand is None]
        strand_cat_items = [item for item in cat_items if item.strand is not None]

        # Default scientific branch. Keeping these task names and coordinates
        # preserves safe reuse of existing unstranded cohort outputs.
        task1 = "merge_chunks"
        with task_scope(task1):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base=task1,
                items=unstranded_merge_items,
                identity_keys=["dedup_method", "polyA_status", "strand", "ctg"],
                param_keys=[],  # 如果有 merge 特有参数 (如 threshold) 可以加在这里
                fn=flex_merge_worker,
                desc="Merge Chunks",
                max_workers=args.max_merge_workers,
            )

        task2 = "cat_chunks"
        with task_scope(task2):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base=task2,
                items=unstranded_cat_items,
                identity_keys=["dedup_method", "polyA_status", "strand"],
                param_keys=[],
                fn=flex_cat_worker,
                desc="Cat Final BAMs",
                max_workers=args.max_merge_workers,
            )

        # Explicit compatibility-output branch. Separate task names prevent
        # mode toggles from changing the default cohort or coverage lineage.
        if strand_merge_items:
            task3 = "merge_strand_chunks"
            with task_scope(task3):
                run_flex_batch(
                    tm=tm,
                    stage=STAGE,
                    task_base=task3,
                    items=strand_merge_items,
                    identity_keys=["dedup_method", "polyA_status", "strand", "ctg"],
                    param_keys=[],
                    fn=flex_merge_worker,
                    desc="Merge Materialized Strand Chunks",
                    max_workers=args.max_merge_workers,
                )

        if strand_cat_items:
            task4 = "cat_strand_chunks"
            with task_scope(task4):
                run_flex_batch(
                    tm=tm,
                    stage=STAGE,
                    task_base=task4,
                    items=strand_cat_items,
                    identity_keys=["dedup_method", "polyA_status", "strand"],
                    param_keys=[],
                    fn=flex_cat_worker,
                    desc="Cat Materialized Strand Cohorts",
                    max_workers=args.max_merge_workers,
                )
        # loggers.raise_error("manual")
