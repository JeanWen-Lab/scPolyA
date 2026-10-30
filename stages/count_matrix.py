from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, List

import pandas as pd

from logging_config import (
    stage_scope, get_logger, LOG_MAIN, LoggersWrapper,
    LOG_STAGE_count_matrix, task_scope
)
from task_manage import ScItem
from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_unit_inputs
from task_manage.effective_config import annotated_peak_signature, build_annotated_peak_config
from task_manage.task_runtime import run_flex_batch
from utils.assignment_diagnostics import (
    format_assignment_summary,
    read_peak_windows,
    write_sample_assignment_report,
)
from utils.xr_utils import forked_umi_count_command, load_long_count_matrix, write_grouped_count_matrix
from utils.terminal_quant_utils import quantification_artifact_suffix, quantification_mode

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

STAGE = LOG_STAGE_count_matrix
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)


def count_matrix(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    with stage_scope(STAGE):
        mode = quantification_mode(args)
        final_item = build_annotated_peak_config(
            args,
            dedup_method=cache.get(CACHE_dedup_method),
            polyA_status=cache.get(CACHE_polyA_status),
        )

        config_signature = annotated_peak_signature(layout, final_item)
        matrix_root = layout.CountMatrixConfigSub(final_item)
        units_counts_root = (
            layout.CountMatrixTempConfigSub(final_item) / "unit_counts"
        )
        units_counts_root.mkdir(parents=True, exist_ok=True)

        csv_path = cache.get(CACHE_unit_inputs)
        unit_items: List[ScItem] = []
        sample_meta: dict[str, dict] = {}
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                uid = r["unit_id"]
                ctg = r["contig_name"]
                sid = r.get("sample_id")
                if not sid or not sid.strip():
                    raise ValueError("Invalid unit inputs row: missing or empty sample_id.")
                unit_items.append(
                    ScItem(
                        uid=uid,
                        sid=sid,
                        ctg=ctg,
                        dedup_status=r["dedup_status"],
                        add_dedup_method=True,
                        args=args,
                        extra_info={"src": Path(r["resolved_path"])},
                        extra_key=config_signature,
                    )
                )
                sample_meta.setdefault(sid, {"dedup_status": r["dedup_status"]})

        if not unit_items:
            logger_stage.warning("No samples found in unit inputs CSV.")
            return

        sample_items: List[ScItem] = [
            ScItem(
                sid=sid,
                dedup_status=meta["dedup_status"],
                add_dedup_method=True,
                args=args,
                extra_key=config_signature,
            )
            for sid, meta in sample_meta.items()
        ]

        def count_unit_worker(item: ScItem):
            input_bam = Path(item.extra_info["src"])
            feature_map = layout.ResolvePeakSidecarFile(
                file_name=item.format_name(
                    suffix=quantification_artifact_suffix("xr_to_peak.tsv.gz", mode)
                ),
                d=final_item,
            )
            if not feature_map.exists():
                raise FileNotFoundError(f"Peak sidecar not found: {feature_map}")
            counts_output = units_counts_root / item.format_name(
                suffix=quantification_artifact_suffix("peak-counts.tsv.gz", mode)
            )
            log_path = layout.LogUmitools / f"{item.uid}.count_matrix.log"
            forked_umi_count_command(
                input_bam=input_bam,
                output_counts=counts_output,
                log_path=log_path,
                feature_map=feature_map,
                args=args,
            )

        def summarize_sample_worker(item: ScItem):
            frames = []
            for unit_item in unit_items:
                if unit_item.sid != item.sid:
                    continue
                unit_counts = units_counts_root / unit_item.format_name(
                    suffix=quantification_artifact_suffix("peak-counts.tsv.gz", mode)
                )
                if not unit_counts.exists():
                    raise FileNotFoundError(f"Unit peak counts not found: {unit_counts}")
                frames.append(load_long_count_matrix(unit_counts, feature_col="peak_name"))

            counts_df = pd.concat(frames, ignore_index=True)
            counts_output = matrix_root / item.format_name(
                suffix=quantification_artifact_suffix("counts.tsv.gz", mode),
                is_annotated_bam=True,
            )
            write_grouped_count_matrix(counts_df, counts_output, feature_col="peak_name")
            _write_sample_assignment_diagnostics(item, counts_df)

        def _write_sample_assignment_diagnostics(item: ScItem, counts_df: pd.DataFrame) -> None:
            """Per-PAS read-assignment table next to the counts (required output)."""
            peaks_gtf = layout.ResolveAnnotatedFinalOutputFile(
                suffix=quantification_artifact_suffix("___peaks-annotated.gtf", mode),
                d=final_item,
            )
            sidecars = [
                layout.ResolvePeakSidecarFile(
                    file_name=unit_item.format_name(
                        suffix=quantification_artifact_suffix("xr_to_peak.tsv.gz", mode)
                    ),
                    d=final_item,
                )
                for unit_item in unit_items
                if unit_item.sid == item.sid
            ]
            summary = write_sample_assignment_report(
                sidecars,
                assigned_umis=(
                    counts_df.groupby("peak_name", observed=True)["umi_count"].sum().astype(int).to_dict()
                ),
                output_tsv=matrix_root / item.format_name(
                    suffix=quantification_artifact_suffix("peak_assignment.tsv.gz", mode),
                    is_annotated_bam=True,
                ),
                summary_json=matrix_root / item.format_name(
                    suffix=quantification_artifact_suffix("peak_assignment_summary.json", mode),
                    is_annotated_bam=True,
                ),
                sample_id=item.sid,
                windows=read_peak_windows(peaks_gtf) if peaks_gtf.exists() else None,
            )
            logger_stage.info(format_assignment_summary(summary))

        with task_scope("count_unit_matrix"):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base="count_unit_matrix",
                items=unit_items,
                identity_keys=["sid", "ctg", "dedup_method"],
                param_keys=["extra_key"],
                fn=count_unit_worker,
                max_workers=args.n_workers,
            )

        with task_scope("summarize_count_matrix"):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base="summarize_count_matrix",
                items=sample_items,
                identity_keys=["sid", "dedup_method"],
                param_keys=["extra_key"],
                fn=summarize_sample_worker,
                max_workers=args.n_workers,
            )
