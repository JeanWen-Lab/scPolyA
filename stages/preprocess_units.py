from __future__ import annotations

import csv
import os
import subprocess
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Tuple, Any, TYPE_CHECKING

import pysam

from logging_config import (
    LOG_MAIN,
    LOG_STAGE_preprocess_units,
    LoggersWrapper,
    get_logger,
    run_tool_command,
    stage_scope,
    task_scope,
)
from task_manage.define import (
    PREPROCESS_INPUTS_CSV_NAME,
    UNIT_INPUTS_CSV_NAME, META_STANDARD_CHROMOSOMES,
    CACHE_unit_inputs, FLAG_EXTERNAL, CACHE_dedup_method
)
from task_manage import ScItem  # <--- 使用 ScItem
from task_manage.task_runtime import Plan, run_flex_batch  # <--- 使用 run_flex_batch
from utils.contig_report import (
    CONTIG_READ_REPORT_NAME,
    contig_read_rows,
    format_contig_exclusion_warning,
    summarize_contig_rows,
    write_contig_read_report,
)

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

Pair = Tuple[str, str]
STAGE = LOG_STAGE_preprocess_units
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)


@dataclass(frozen=True)
class PreprocessRoute:
    should_split: bool
    should_dedup: bool
    direct_contig_dedup: bool
    split_reason: str | None


def resolve_preprocess_route(
    *,
    raw_contig: str,
    csv_requires_split: int,
    csv_is_deduped: int,
    force_split_bams: bool,
    force_dedup_bams: bool,
) -> PreprocessRoute:
    """Choose one physical producer for every downstream contig unit."""
    whole_bam = raw_contig == "all"
    should_dedup = csv_is_deduped == 0 or force_dedup_bams
    explicitly_split = whole_bam and (csv_requires_split == 1 or force_split_bams)

    if explicitly_split:
        split_reason = "requested"
    elif whole_bam and not should_dedup:
        # No dedup task will produce per-contig outputs for an already-deduped BAM.
        split_reason = "pre_deduplicated_input"
    else:
        split_reason = None

    should_split = split_reason is not None
    return PreprocessRoute(
        should_split=should_split,
        should_dedup=should_dedup,
        direct_contig_dedup=whole_bam and should_dedup and not should_split,
        split_reason=split_reason,
    )


def build_umi_dedup_command(
    *,
    args,
    in_bam: Path,
    out_bam: Path,
    local_log: Path,
    dedup_method: str,
    chrom: str | None = None,
    output_stats: str | None = None,
    temp_dir: Path | None = None,
) -> List[str]:
    cmd = [
        "umi_tools", "dedup",
        "-I", str(in_bam),
        "-S", str(out_bam),
        "--cell-tag", args.cb_tag,
        # umi_tools defaults to --cell-tag-split='-', which groups AAAC-1 and AAAC-2
        # (different GEM wells/cells in aggregated 10x BAMs) as one cell. Keep the
        # full corrected barcode, matching the per-cell counting stage.
        "--cell-tag-split", "",
        "--umi-tag", args.umi_tag,
        "--method", dedup_method,
        "--extract-umi-method", "tag",
        "--per-cell",
        "--log", str(local_log),
        "--random-seed", str(args.tools_seed),
    ]
    if chrom is not None:
        if not str(chrom).strip() or chrom == "all":
            raise ValueError(f"Direct dedup requires one concrete contig, got: {chrom!r}")
        cmd.extend(["--chrom", chrom])
    if output_stats:
        cmd.extend(["--output-stats", output_stats])
    if temp_dir is not None:
        cmd.extend(["--temp-dir", str(temp_dir)])
    return cmd


def build_dedup_task_parameter(*, args, source_type: str) -> str:
    return ";".join(
        (
            f"source={source_type}",
            f"cb_tag={args.cb_tag}",
            f"umi_tag={args.umi_tag}",
            f"seed={args.tools_seed}",
            f"stats={int(bool(args.dedup_stats))}",
            # Dedup groups by the full cell tag; completions grouped by the
            # pre-'-' prefix must not be reused.
            "cell_split=none",
        )
    )


def resolve_dedup_input(item: ScItem, layout: RunLayout) -> tuple[Path, str | None]:
    """Return the physical BAM and optional indexed region for one dedup task."""

    src_type = item.extra_info["src_type"]
    if src_type in {"raw", "raw_region"}:
        in_bam = Path(item.extra_info["raw_src"])
        chrom = item.extra_info.get("region_contig") if src_type == "raw_region" else None
        if chrom is not None and chrom != item.ctg:
            raise RuntimeError(
                f"[{item.name}] Dedup region {chrom!r} does not match unit contig "
                f"{item.ctg!r}"
            )
        return in_bam, chrom

    base_status = item.extra_info["base_dedup_status"]
    in_bam = layout.UnitOutput(item.uid) / ScItem.cls_format_name(
        suffix="bam",
        uid=item.uid,
        dedup_status=base_status,
    )
    return in_bam, None


def _load_bai_mapped_counts(bam_path: Path) -> dict[str, int]:
    with pysam.AlignmentFile(str(bam_path), "rb") as bam:
        if not bam.has_index():
            raise ValueError(f"BAM index is unavailable: {bam_path}")
        return {
            str(stat.contig): int(stat.mapped)
            for stat in bam.get_index_statistics()
        }


def order_dedup_items_by_bai_mapped_count(
    items: List[ScItem],
    layout: RunLayout,
    *,
    warning: Callable[[str], None] | None = None,
) -> List[ScItem]:
    """Use deterministic longest-processing-time dispatch without changing tasks."""

    original = list(items)
    if len(original) < 2:
        return original

    counts_by_path: dict[Path, dict[str, int]] = {}
    weighted: list[tuple[int, int, ScItem]] = []
    try:
        for original_index, item in enumerate(original):
            bam_path, chrom = resolve_dedup_input(item, layout)
            resolved_path = bam_path.expanduser().resolve()
            if resolved_path not in counts_by_path:
                counts_by_path[resolved_path] = _load_bai_mapped_counts(resolved_path)
            reference = str(chrom if chrom is not None else item.ctg)
            reference_counts = counts_by_path[resolved_path]
            if reference not in reference_counts:
                raise ValueError(
                    f"BAM index has no reference {reference!r}: {resolved_path}"
                )
            weighted.append(
                (int(reference_counts[reference]), original_index, item)
            )
    except (OSError, ValueError) as exc:
        if warning is not None:
            warning(
                "Dedup BAI-count scheduling is unavailable; preserving the "
                f"canonical task order: {exc}"
            )
        return original

    weighted.sort(key=lambda entry: (-entry[0], entry[1]))
    return [entry[2] for entry in weighted]


@contextmanager
def dedup_temp_directory(output_bam: Path) -> Iterator[Path]:
    """Provide one output-adjacent UMI-tools scratch directory per task."""

    output_bam = Path(output_bam)
    output_bam.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".scpolya-umi-tools-",
        dir=output_bam.parent,
    ) as temp_dir:
        yield Path(temp_dir)


# ---- helpers: atomic write + simple validation ----

def atomic_write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def validate_unit_rows(rows: List[Dict[str, Any]]) -> None:
    for r in rows:
        if r.get("contig_name") == "all" and int(r.get("requires_split", 0)) == 1:
            raise RuntimeError("Invalid unit row: contig=all with requires_split=1 is not allowed.")
        if not r.get("unit_id"):
            raise RuntimeError("Invalid unit row: empty unit_id.")
        if not r.get("sample_id"):
            raise RuntimeError("Invalid unit row: empty sample_id.")
        if not r.get("contig_name"):
            raise RuntimeError("Invalid unit row: empty contig_name.")


def _report_excluded_contigs(
    input_rows: List[Dict[str, str]],
    standard_chroms: List[str],
    layout: RunLayout,
    *,
    species: str,
) -> None:
    """Write per-contig kept/excluded alignment counts; never changes units."""
    from stages.init_references import resolve_standard_chromosomes

    # The full standard set separates contigs left out by --contigs or
    # --remove-mt from non-standard contigs.
    all_standard = resolve_standard_chromosomes(
        species=species,
        remove_mt=False,
        bam_has_chr_prefix=any(str(c).startswith("chr") for c in standard_chroms),
    )
    rows: List[Dict[str, Any]] = []
    for r in input_rows:
        if str(r.get("contig_name", "")).strip() != "all":
            continue  # a pre-split contig input holds only its own contig
        try:
            rows.extend(contig_read_rows(
                sample_id=r["sample_id"],
                pp_id=r["pp_id"],
                bam_path=Path(r["pp_path"]).expanduser(),
                standard_chroms=standard_chroms,
                all_standard_chroms=all_standard,
            ))
        except (OSError, ValueError) as exc:
            loggers.warning(f"[{r['sample_id']}] Contig read report skipped for {r['pp_id']}: {exc}")
    report = layout.InputTables / CONTIG_READ_REPORT_NAME
    write_contig_read_report(rows, report)
    for (sample_id, pp_id), entry in summarize_contig_rows(rows).items():
        if entry["excluded_mapped"] or entry["not_analysed_mapped"]:
            loggers.warning(format_contig_exclusion_warning(sample_id, pp_id, entry))
    loggers.info(f"Contig read report written to {report}")


def preprocess_inputs(tm: TaskManager, layout: RunLayout, args, cache: Resources) -> None:
    """
    Stage preprocess_units (Flex Version):
      - Read preprocess_inputs.csv
      - Construct ScItems for Split, Filter, and Dedup tasks
      - Execute Flex Batches
      - Finalize unit_inputs.csv
    """

    in_csv = layout.InputTables / PREPROCESS_INPUTS_CSV_NAME

    # 容器：用于存放不同任务的 Items
    items_split: List[ScItem] = []
    items_filter: List[ScItem] = []
    items_dedup: List[ScItem] = []
    items_rewrite: List[ScItem] = []

    # 容器：用于 Finalize 阶段写 CSV
    # 我们保留这个列表来记录所有的 Units 信息
    unit_rows_for_csv: List[Dict[str, Any]] = []

    with stage_scope(STAGE):
        # ----------------------------
        # 0) Prepare Plan & Items
        # ----------------------------
        with task_scope("prepare_plan"):
            if not in_csv.exists():
                raise FileNotFoundError(f"Missing input csv: {in_csv}")

            # A) Read Input
            input_rows: List[Dict[str, str]] = []
            with open(in_csv, "r", newline="") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    input_rows.append(r)

            standard_chroms: List[str] = cache.get(META_STANDARD_CHROMOSOMES) or []
            if not standard_chroms:
                raise RuntimeError(f"Missing standard chromosomes meta. Check init_references.")

            _report_excluded_contigs(input_rows, standard_chroms, layout, species=args.species)

            # B) Expand to Units & Build ScItems
            samples = set()
            contigs_by_sample = {}

            for r in input_rows:
                raw_contig = r["contig_name"]
                sample_id = r["sample_id"]
                pp_path = r["pp_path"]
                barcode_suffix = str(r.get("barcode_suffix", "")).strip()

                # Logic Flags
                csv_requires_split = int(r["requires_split"])
                csv_is_deduped = int(r["is_deduped"])
                base_dedup_status = r["dedup_status"]  # 原始文件的去重状态 (e.g. "undeduped" or "external")

                route = resolve_preprocess_route(
                    raw_contig=raw_contig,
                    csv_requires_split=csv_requires_split,
                    csv_is_deduped=csv_is_deduped,
                    force_split_bams=args.force_split_bams,
                    force_dedup_bams=args.force_dedup_bams,
                )
                should_split = route.should_split
                # The prior whole-BAM filter branch has no active route. Keep its
                # empty batch registered for historical task-contract readers.
                should_filter = False
                should_dedup = route.should_dedup

                if route.split_reason == "pre_deduplicated_input":
                    loggers.info(
                        f"[{sample_id}] Input is already deduplicated; materializing contig BAMs "
                        "because no dedup task will produce downstream contig units."
                    )

                # Target Dedup Method (for dedup task)
                target_dedup_method = args.dedup_method

                # Whole BAMs always expand to physical downstream contig units.
                target_contigs = (
                    standard_chroms
                    if should_split or route.direct_contig_dedup
                    else [raw_contig]
                )

                for contig in target_contigs:
                    unit_id = f"{sample_id}.{contig}"

                    # 更新 Universe 记录
                    samples.add(sample_id)
                    contigs_by_sample.setdefault(sample_id, []).append(contig)

                    # 记录 CSV 信息 (供 finalize 使用)
                    row_info = {
                        "unit_id": unit_id,
                        "pp_id": r["pp_id"],
                        "sample_id": sample_id,
                        "source_type": r["source_type"],
                        "contig_name": contig,
                        "dedup_method": target_dedup_method,
                        "base_dedup_status": base_dedup_status,
                        "pp_path": pp_path,
                        "barcode_suffix": barcode_suffix,
                        # 逻辑标记，用于 finalize 决定最终路径
                        "executed_split": 1 if should_split else 0,
                        "executed_filter": 1 if should_filter else 0,
                        "executed_dedup": 1 if should_dedup else 0,
                        "executed_rewrite": 1 if barcode_suffix else 0,
                    }
                    unit_rows_for_csv.append(row_info)

                    # --- 构建 Task Items ---

                    # 1. Split Item
                    if should_split:
                        # 目标：生成 unit.undeduped.bam (或者 base status)
                        # 输入：extra_info['src'] = pp_path
                        item_split = ScItem(
                            uid=unit_id,
                            sid=sample_id,
                            ctg=contig,
                            dedup_status=base_dedup_status,  # Split 输出通常继承输入的去重状态
                            extra_info={"src": Path(pp_path).expanduser().resolve()}
                        )
                        items_split.append(item_split)

                    # 2. Filter Item
                    if should_filter:
                        # 目标：生成 unit.undeduped.bam (filtered)
                        item_filter = ScItem(
                            uid=unit_id,
                            sid=sample_id,
                            ctg=contig,
                            dedup_status=base_dedup_status,
                            extra_info={"src": Path(pp_path).expanduser().resolve()}
                        )
                        items_filter.append(item_filter)

                    # 3. Dedup Item
                    if should_dedup:
                        # 目标：生成 unit.row_UMI.bam (target dedup method)
                        # Input is either a materialized unit, an already split
                        # raw unit, or one indexed region of a whole raw BAM.

                        if route.direct_contig_dedup:
                            src_type = "raw_region"
                        elif should_split or should_filter:
                            src_type = "derived"
                        else:
                            src_type = "raw"

                        item_dedup = ScItem(
                            uid=unit_id,
                            sid=sample_id,
                            ctg=contig,
                            dedup_status=target_dedup_method,  # 这是 dedup 后的状态
                            args=args,
                            add_dedup_method=True,
                            extra_key=build_dedup_task_parameter(args=args, source_type=src_type),
                            extra_info={
                                "src_type": src_type,
                                "base_dedup_status": base_dedup_status,  # 用于寻找 Case A 的输入文件
                                "raw_src": (
                                    Path(pp_path).expanduser().resolve()
                                    if src_type in {"raw", "raw_region"}
                                    else None
                                ),
                                "region_contig": contig if src_type == "raw_region" else None,
                            }
                        )
                        items_dedup.append(item_dedup)

                    if barcode_suffix:
                        items_rewrite.append(
                            ScItem(
                                uid=unit_id,
                                sid=sample_id,
                                ctg=contig,
                                dedup_status=target_dedup_method if should_dedup else base_dedup_status,
                                extra_key=f"barcode_suffix={barcode_suffix}",
                                extra_info={
                                    "barcode_suffix": barcode_suffix,
                                    "base_dedup_status": base_dedup_status,
                                    "src_type": "dedup" if should_dedup else ("derived" if (should_split or should_filter) else "raw"),
                                    "raw_src": Path(pp_path).expanduser().resolve(),
                                },
                            )
                        )

            # Register Plan (Idempotent)
            tm.load_or_init(Plan(samples=sorted(samples), contigs_by_sample=contigs_by_sample))

            loggers.info(
                f"Plan: {len(samples)} samples, {len(unit_rows_for_csv)} units. "
                f"Tasks: Split={len(items_split)}, Filter={len(items_filter)}, Dedup={len(items_dedup)}, Rewrite={len(items_rewrite)}"
            )

    # ---------------------------------------
    # Workers (Flex)
    # ---------------------------------------

    def flex_split_worker(item: ScItem):
        in_bam = item.extra_info['src']
        # 输出使用 item.format_name (它包含了 uid 和 dedup_status)
        out_bam = layout.UnitOutput(item.uid) / item.format_name(suffix="bam")
        out_bam.parent.mkdir(parents=True, exist_ok=True)

        # Cleanup
        out_bam.unlink(missing_ok=True)
        out_bam.with_suffix(".bam.bai").unlink(missing_ok=True)
        out_bam.with_suffix(".bai").unlink(missing_ok=True)

        local_log = layout.LogSamtools / f"split.{item.uid}.log"

        # Action: samtools view -b -o OUT IN CONTIG
        cmd = ["samtools", "view", "-b", "-o", str(out_bam), str(in_bam), item.ctg]

        try:
            with local_log.open("a") as f:
                run_tool_command(
                    cmd,
                    tool="samtools",
                    operation=f"split.{item.uid}",
                    compatibility_log=local_log,
                    check=True,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            # Index
            run_tool_command(
                ["samtools", "index", str(out_bam)],
                tool="samtools",
                operation=f"split.{item.uid}.index",
                compatibility_log=local_log,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            logger_stage.info(f"[{item.uid}] Split done -> {out_bam.name}")
        except subprocess.CalledProcessError as e:
            loggers.raise_error(f"[{item.uid}] Split failed: {e}")

    def flex_filter_worker(item: ScItem):
        in_bam = item.extra_info['src']
        out_bam = layout.UnitOutput(item.uid) / item.format_name(suffix="bam")
        out_bam.parent.mkdir(parents=True, exist_ok=True)
        out_bam.unlink(missing_ok=True)

        local_log = layout.LogSamtools / f"filter.{item.uid}.log"
        standard_chroms = cache.get(META_STANDARD_CHROMOSOMES)

        # Action: samtools view -b -o OUT IN [CHROMS...]
        cmd = ["samtools", "view", "-b", "-o", str(out_bam), str(in_bam)] + standard_chroms

        try:
            with local_log.open("a") as f:
                run_tool_command(
                    cmd,
                    tool="samtools",
                    operation=f"filter.{item.uid}",
                    compatibility_log=local_log,
                    check=True,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                run_tool_command(
                    ["samtools", "index", str(out_bam)],
                    tool="samtools",
                    operation=f"filter.{item.uid}.index",
                    compatibility_log=local_log,
                    check=True,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                )
            logger_stage.info(f"[{item.uid}] Filter done -> {out_bam.name}")
        except subprocess.CalledProcessError as e:
            loggers.raise_error(f"[{item.uid}] Filter failed: {e}")

    def flex_dedup_worker(item: ScItem):
        # 1. Determine Input
        in_bam, chrom = resolve_dedup_input(item, layout)

        if not in_bam.exists():
            raise FileNotFoundError(f"[{item.name}] Dedup input missing: {in_bam}")
        if chrom is not None and not (
            Path(str(in_bam) + ".bai").exists() or in_bam.with_suffix(".bai").exists()
        ):
            raise FileNotFoundError(f"[{item.name}] Direct contig dedup requires a BAM index: {in_bam}")

        # 2. Determine Output
        # item.dedup_status is already the TARGET method (e.g. row_UMI)
        out_bam = layout.UnitOutput(item.uid) / item.format_name(suffix="bam")

        output_stats = None
        if args.dedup_stats:
            output_stats = str(out_bam) + ".stats"

        local_log = layout.LogUmitools / f"dedup.{item.uid}.log"

        try:
            logger_stage.info(f"[{item.uid}] Dedup started")
            with dedup_temp_directory(out_bam) as temp_dir, local_log.open("a") as f:
                dedup_cmd = build_umi_dedup_command(
                    args=args,
                    in_bam=in_bam,
                    out_bam=out_bam,
                    local_log=local_log,
                    dedup_method=item.dedup_method,
                    chrom=chrom,
                    output_stats=output_stats,
                    temp_dir=temp_dir,
                )
                # UMI-tools writes its own log, while stdout/stderr remain useful
                # for failures before that logger is initialized.
                run_tool_command(
                    dedup_cmd,
                    tool="umi_tools",
                    operation=f"dedup.{item.uid}",
                    compatibility_log=local_log,
                    check=True,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

                run_tool_command(
                    ["samtools", "index", str(out_bam)],
                    tool="samtools",
                    operation=f"dedup.{item.uid}.index",
                    compatibility_log=local_log,
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
            logger_stage.info(f"[{item.uid}] Dedup done -> {out_bam.name}")
        except subprocess.CalledProcessError as e:
            loggers.raise_error(f"[{item.uid}] Dedup failed: {e}")

    def _rewrite_output_path(uid: str, dedup_status: str, barcode_suffix: str) -> Path:
        label = re.sub(r"[^A-Za-z0-9._-]+", "_", barcode_suffix).strip("_") or "suffix"
        return layout.UnitOutput(uid) / ScItem(uid=uid, dedup_status=dedup_status).format_name(
            suffix=f"cb-sfx-{label}.bam"
        )

    def _resolve_rewrite_input(item: ScItem) -> Path:
        src_type = item.extra_info["src_type"]
        if src_type == "dedup":
            return layout.UnitOutput(item.uid) / ScItem(uid=item.uid, dedup_status=item.dedup_status).format_name(suffix="bam")
        if src_type == "derived":
            base_status = item.extra_info["base_dedup_status"]
            return layout.UnitOutput(item.uid) / ScItem(uid=item.uid, dedup_status=base_status).format_name(suffix="bam")
        return Path(item.extra_info["raw_src"])

    def flex_rewrite_barcode_worker(item: ScItem):
        in_bam = _resolve_rewrite_input(item)
        if not in_bam.exists():
            raise FileNotFoundError(f"[{item.uid}] Barcode rewrite input missing: {in_bam}")

        barcode_suffix = item.extra_info["barcode_suffix"]
        out_bam = _rewrite_output_path(item.uid, item.dedup_status, barcode_suffix)
        out_bam.parent.mkdir(parents=True, exist_ok=True)
        out_bam.unlink(missing_ok=True)
        Path(str(out_bam) + ".bai").unlink(missing_ok=True)

        local_log = layout.LogSamtools / f"barcode_suffix.{item.uid}.log"
        n_reads = 0
        n_cb = 0
        n_cr = 0

        with pysam.AlignmentFile(str(in_bam), "rb") as reader, pysam.AlignmentFile(str(out_bam), "wb", header=reader.header) as writer:
            for read in reader:
                n_reads += 1
                if read.has_tag(args.cb_tag):
                    read.set_tag(args.cb_tag, f"{read.get_tag(args.cb_tag)}{barcode_suffix}", value_type="Z")
                    n_cb += 1
                if read.has_tag("CR"):
                    read.set_tag("CR", f"{read.get_tag('CR')}{barcode_suffix}", value_type="Z")
                    n_cr += 1
                writer.write(read)

        pysam.index(str(out_bam))
        with local_log.open("a") as handle:
            handle.write(
                f"input_bam\t{in_bam}\noutput_bam\t{out_bam}\nbarcode_suffix\t{barcode_suffix}\n"
                f"reads_processed\t{n_reads}\ncb_updated\t{n_cb}\ncr_updated\t{n_cr}\n"
            )
        logger_stage.info(f"[{item.uid}] Barcode suffix rewrite done -> {out_bam.name}")

    # ---------------------------------------
    # Execute Batches
    # ---------------------------------------
    with stage_scope(STAGE):

        # 1. Split
        task1 = "split_to_units"
        with task_scope(task1):
            run_flex_batch(
                tm=tm,
                stage=STAGE,  # STAGE_UNIT
                task_base=task1,
                items=items_split,
                identity_keys=["sid", "ctg"],  # 谁
                param_keys=[],  # 怎么跑 (standard split, no extra params)
                fn=flex_split_worker,
                desc="Split Units",
                max_workers=args.max_split_workers,
            )

        # 2. Filter
        task2 = "filter_units"
        with task_scope(task2):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base=task2,
                items=items_filter,
                identity_keys=["sid", "ctg"],
                param_keys=[],
                fn=flex_filter_worker,
                desc="Filter Units",
                max_workers=args.max_filter_workers,
            )

        # 3. Dedup
        task3 = "dedup_units"
        with task_scope(task3):
            # Dedup method is part of identity or param?
            # Usually param, but here we output a specific file, so it acts like identity for the file.
            # ScItem puts dedup_method in filename.

            cache.set(CACHE_dedup_method, args.dedup_method)
            cache.contextualize("dedup_method", args.dedup_method)

            ordered_dedup_items = order_dedup_items_by_bai_mapped_count(
                items_dedup,
                layout,
                warning=logger_stage.warning,
            )
            if ordered_dedup_items != items_dedup:
                logger_stage.info(
                    "Dedup dispatch reordered %d task(s) by descending BAI "
                    "mapped-read count.",
                    len(ordered_dedup_items),
                )
                logger_stage.debug(
                    "Dedup LPT order: %s",
                    ",".join(item.uid for item in ordered_dedup_items),
                )

            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base=task3,
                items=ordered_dedup_items,
                identity_keys=["sid", "ctg", "dedup_method"],
                param_keys=["extra_key"],
                fn=flex_dedup_worker,
                desc=f"Dedup Units ({args.dedup_method})",
                max_workers=args.max_dedup_workers,
            )

            # Update global cache for next stages
            cache.set(CACHE_unit_inputs, layout.InputTables / UNIT_INPUTS_CSV_NAME(args.dedup_method))

        task4 = "rewrite_barcode_suffix"
        with task_scope(task4):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base=task4,
                items=items_rewrite,
                identity_keys=["sid", "ctg", "dedup_status"],
                param_keys=["extra_key"],
                fn=flex_rewrite_barcode_worker,
                desc="Rewrite Barcode Suffix",
                max_workers=args.max_filter_workers,
            )

        # 4. Finalize (Global Logic)
        def finalize_units():
            output_csv = cache.get(CACHE_unit_inputs)
            fieldnames = [
                "unit_id", "pp_id", "sample_id", "source_type", "contig_name",
                "is_deduped_unit", "dedup_status", "barcode_suffix", "resolved_path"
            ]

            final_rows = []
            for r in unit_rows_for_csv:
                # Copy base info
                rr = {k: r.get(k) for k in fieldnames if k in r}

                uid = r["unit_id"]

                # Resolve Path based on what we executed
                if r["executed_dedup"] == 1:
                    # Final result is deduped
                    status = args.dedup_method
                    rr["dedup_status"] = status
                    rr["is_deduped_unit"] = 1
                    # Use ScItem logic to resolve path
                    fname = ScItem(uid=uid, dedup_status=status).format_name(suffix="bam")
                    rr["resolved_path"] = str(layout.UnitOutput(uid) / fname)

                elif r["executed_split"] == 1 or r["executed_filter"] == 1:
                    # Result is split/filtered (base status)
                    status = r["base_dedup_status"]
                    rr["dedup_status"] = status
                    rr["is_deduped_unit"] = 1 if status == FLAG_EXTERNAL else 0
                    fname = ScItem(uid=uid, dedup_status=status).format_name(suffix="bam")
                    rr["resolved_path"] = str(layout.UnitOutput(uid) / fname)

                else:
                    # Raw
                    rr["dedup_status"] = r["base_dedup_status"]
                    rr["is_deduped_unit"] = 1 if r["base_dedup_status"] == FLAG_EXTERNAL else 0
                    rr["resolved_path"] = r["pp_path"]

                if r["executed_rewrite"] == 1:
                    rr["resolved_path"] = str(_rewrite_output_path(uid, rr["dedup_status"], r["barcode_suffix"]))
                rr["barcode_suffix"] = r.get("barcode_suffix", "")

                final_rows.append(rr)

            atomic_write_csv(output_csv, fieldnames, final_rows)
            loggers.info(f"Finalized unit_inputs.csv: {output_csv}")

        # Execute finalize (in-process)
        with task_scope("finalize_units"):
            tm.run_inline_execution(
                stage=STAGE,
                task_base="finalize_units",
                item={},
                fn=finalize_units,
            )
