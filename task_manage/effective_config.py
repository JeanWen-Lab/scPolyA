from __future__ import annotations

from typing import Any, Dict

from quantification_config import (
    resolved_intronic_misprimed_ratio_threshold,
    resolved_quantification_mode,
    terminal_annotation_schema_payload,
)


def exp_coverage_scaled(args: Any) -> bool:
    return args.coverage_scaling in ("expr", "both")


def polyA_coverage_scaled(args: Any) -> bool:
    return args.coverage_scaling in ("polyA", "both")


def peak_threshold_label(args: Any, *, is_polyA: bool) -> str:
    if is_polyA:
        if args.polyA_peak_threshold_mode == "dynamic":
            if args.polyA_dynamic_threshold_stat == "max_norm_fixed":
                return f"dyn-max_norm_fixed-n{args.polyA_dynamic_threshold_normalized}"
            return (
                f"dyn-{args.polyA_dynamic_threshold_stat}"
                f"-a{args.polyA_dynamic_threshold_alpha}"
                f"-f{args.polyA_dynamic_threshold_floor}"
            )
        return str(
            args.scaled_depth_threshold_polyA
            if polyA_coverage_scaled(args)
            else args.unscaled_depth_threshold_polyA
        )

    if args.exp_peak_threshold_mode == "dynamic":
        if args.exp_dynamic_threshold_stat == "max_norm_fixed":
            return f"dyn-max_norm_fixed-n{args.exp_dynamic_threshold_normalized}"
        return (
            f"dyn-{args.exp_dynamic_threshold_stat}"
            f"-a{args.exp_dynamic_threshold_alpha}"
            f"-f{args.exp_dynamic_threshold_floor}"
        )
    return str(args.scaled_depth_threshold if exp_coverage_scaled(args) else args.unscaled_depth_threshold)


def build_final_peak_config(
    args: Any,
    *,
    dedup_method: str,
    polyA_status: str,
    include_threshold_params: bool = False,
    include_annotate_peak_source: bool = False,
) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "dedup_method": dedup_method,
        "coverage_scaling": args.coverage_scaling,
        "polyA_status": polyA_status,
        "depth_threshold": peak_threshold_label(args, is_polyA=False),
        "depth_threshold_polyA": peak_threshold_label(args, is_polyA=True),
        "merge_distance": args.merge_distance,
        "merge_distance_polyA": args.merge_distance_polyA,
        "misprime_A_count": args.misprime_A_count,
        "misprime_in": args.misprime_in,
        "misprimed_ratio_threshold": args.misprimed_ratio_threshold,
    }

    if include_threshold_params:
        config.update(
            {
                "exp_peak_threshold_mode": args.exp_peak_threshold_mode,
                "polyA_peak_threshold_mode": args.polyA_peak_threshold_mode,
                "exp_dynamic_threshold_stat": args.exp_dynamic_threshold_stat,
                "polyA_dynamic_threshold_stat": args.polyA_dynamic_threshold_stat,
                "exp_dynamic_threshold_alpha": args.exp_dynamic_threshold_alpha,
                "polyA_dynamic_threshold_alpha": args.polyA_dynamic_threshold_alpha,
                "exp_dynamic_threshold_floor": args.exp_dynamic_threshold_floor,
                "polyA_dynamic_threshold_floor": args.polyA_dynamic_threshold_floor,
                "exp_dynamic_threshold_normalized": args.exp_dynamic_threshold_normalized,
                "polyA_dynamic_threshold_normalized": args.polyA_dynamic_threshold_normalized,
            }
        )

    if include_annotate_peak_source:
        config["annotate_peak_source"] = args.annotate_peak_source

    config["quantification_mode"] = resolved_quantification_mode(vars(args))

    return config


def build_annotated_peak_config(args: Any, *, dedup_method: str, polyA_status: str) -> Dict[str, Any]:
    config = build_final_peak_config(
        args,
        dedup_method=dedup_method,
        polyA_status=polyA_status,
        include_annotate_peak_source=True,
    )
    config.update(
        {
            "intronic_misprimed_ratio_threshold": (
                resolved_intronic_misprimed_ratio_threshold(vars(args))
            ),
        }
    )
    config.update(terminal_annotation_schema_payload(config))
    return config


def annotated_peak_signature(layout: Any, final_item: Dict[str, Any]) -> str:
    return layout.AnnotatedPeakSignatureFromDict(final_item)


def build_rna_gene_matrix_config(args: Any, *, dedup_method: str) -> Dict[str, Any]:
    return {
        "dedup_method": dedup_method,
        "cb_tag": args.cb_tag,
        "umi_tag": args.umi_tag,
    }
