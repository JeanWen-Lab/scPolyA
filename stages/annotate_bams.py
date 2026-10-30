from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, List

from logging_config import (
    stage_scope, get_logger, LOG_MAIN, LoggersWrapper,
    LOG_STAGE_annotate_bams, task_scope
)
from task_manage import ScItem
from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_unit_inputs
from task_manage.effective_config import annotated_peak_signature, build_annotated_peak_config
from task_manage.task_runtime import run_flex_batch
from utils.assignment_diagnostics import AssignmentDiagnostics, read_peak_windows, unit_diagnostics_paths
from utils.feature_sidecar_utils import build_feature_assignment_sidecar
from utils.read_eligibility import read_rule_from_args
from utils.terminal_quant_utils import quantification_artifact_suffix, quantification_mode

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

STAGE = LOG_STAGE_annotate_bams
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)


def annotate_bams(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    with stage_scope(STAGE):
        mode = quantification_mode(args)
        read_rule = read_rule_from_args(args)
        final_item = build_annotated_peak_config(
            args,
            dedup_method=cache.get(CACHE_dedup_method),
            polyA_status=cache.get(CACHE_polyA_status),
        )

        annotate_peak_source = final_item["annotate_peak_source"]
        config_signature = annotated_peak_signature(layout, final_item)
        peaks_gtf = layout.ResolveAnnotatedFinalOutputFile(
            suffix=quantification_artifact_suffix("___peaks-annotated.gtf", mode),
            d=final_item,
        )
        if mode == "legacy" and annotate_peak_source == "resolved" and not peaks_gtf.exists():
            legacy_peak_name = layout.PeakNameRecord(
                final_item
            ).legacy_name
            legacy_peaks_gtf = (
                layout.FinalOutput
                / legacy_peak_name
                / f"{legacy_peak_name}.___peaks-annotated.gtf"
            )
            if legacy_peaks_gtf.exists():
                peaks_gtf = legacy_peaks_gtf
        if not peaks_gtf.exists():
            loggers.raise_error(f"Required GTF file not found: {peaks_gtf}. Please run annotate_peaks stage first.")

        sidecar_root = layout.PeakSidecarsConfigSub(final_item)

        csv_path = cache.get(CACHE_unit_inputs)
        unit_items: List[ScItem] = []
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

        if not unit_items:
            logger_stage.warning("No input slices found in CSV.")
            return

        # Shared, read-only window index for the per-PAS assignment diagnostics.
        windows = read_peak_windows(peaks_gtf)

        def annotate_unit_worker(item: ScItem):
            input_bam = Path(item.extra_info["src"])
            output_sidecar = sidecar_root / item.format_name(
                suffix=quantification_artifact_suffix("xr_to_peak.tsv.gz", mode)
            )
            diagnostics = AssignmentDiagnostics(
                windows,
                read_rule=read_rule,
                minpolyA=args.minpolyA,
                nonA_allowed=args.nonA_allowed,
            )
            build_feature_assignment_sidecar(
                input_bam=input_bam,
                annotation_gtf=peaks_gtf,
                output_sidecar=output_sidecar,
                cell_barcode_tag=args.cb_tag,
                thread_count=max(1, args.n_threads_bam_annotation),
                feature_type="peak",
                group_attr="peak_name",
                count_features=True,
                read_rule=read_rule,
                diagnostics=diagnostics,
                diagnostics_paths=unit_diagnostics_paths(output_sidecar),
            )

        with task_scope("annotate_slices"):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base="annotate_slices",
                items=unit_items,
                identity_keys=["sid", "ctg"],
                param_keys=["extra_key"],
                fn=annotate_unit_worker,
                max_workers=args.n_workers_annotate or args.n_workers,
            )
