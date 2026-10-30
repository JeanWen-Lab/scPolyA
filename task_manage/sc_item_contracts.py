from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from utils.read_eligibility import read_rule_counting_token, read_rule_token


@dataclass(frozen=True)
class ScItemIdentity:
    uid: str | None = None
    sid: str | None = None
    ctg: str | None = None
    dedup_status: str | None = None
    strand: Literal["plus", "minus"] | str | None = None
    is_cohort: bool = False
    is_polyA: bool = False


@dataclass(frozen=True)
class ScItemArtifactFlags:
    prefix: str | None = None
    is_coverage: bool = False
    is_negate: bool = False
    remove_CDS: bool = False
    is_peak: bool = False
    is_merged_peak: bool = False
    is_assigned_peak: bool = False
    is_annotated_bam: bool = False
    add_polyA: bool = False
    add_threshold: bool = False
    add_merge_dist: bool = False
    scaling_factor: float | None = None


@dataclass(frozen=True)
class ScItemEffectiveParams:
    dedup_method: str | None = None
    polyA_token: str | None = None
    polyA_status: str | None = None
    depth_threshold: float | str | None = None
    peak_threshold_mode: str = "fixed"
    dynamic_threshold_stat: str | None = "median"
    dynamic_threshold_alpha: float | None = 1.0
    dynamic_threshold_floor: float | None = 0.0
    dynamic_threshold_normalized: float | None = 0.3
    merge_distance: int | None = None
    # Unified read-rule token for expression coverage artifacts; poly(A)
    # artifacts carry it inside polyA_token. None in legacy mode.
    read_rule: str | None = None


@dataclass(frozen=True)
class ScItemWorkerPayload:
    extra_info: Mapping[str, Any] | None = None
    extra_key: str | None = None


def resolve_dedup_method(
    *,
    dedup_status: str | None,
    fallback_dedup_method: str | None,
) -> str | None:
    return dedup_status if dedup_status is not None else fallback_dedup_method


def polyA_token(*, is_polyA: bool, args: Any) -> str:
    if not is_polyA or not args:
        return "no_polyA"
    token = f"{args.minpolyA}-{args.nonA_allowed}-{args.minMAPQ}"
    rule = read_rule_counting_token(args)
    # The unified read rule changes which reads the extract keeps and the
    # count assignment changes where they are counted; both are bound here
    # because every peak/annotation/matrix name hashes the poly(A) status.
    # Legacy names stay exactly as before.
    return f"{token}-{rule}" if rule else token


def format_polyA_status(token: str | None) -> str | None:
    return f"polyA-{token}" if token and token != "no_polyA" else None


def peak_threshold_mode(*, args: Any, is_polyA: bool) -> str:
    if not args:
        return "fixed"
    return args.polyA_peak_threshold_mode if is_polyA else args.exp_peak_threshold_mode


def dynamic_threshold_stat(*, args: Any, is_polyA: bool) -> str | None:
    if not args:
        return "median"
    return args.polyA_dynamic_threshold_stat if is_polyA else args.exp_dynamic_threshold_stat


def dynamic_threshold_alpha(*, args: Any, is_polyA: bool) -> float | None:
    if not args:
        return 1.0
    return args.polyA_dynamic_threshold_alpha if is_polyA else args.exp_dynamic_threshold_alpha


def dynamic_threshold_floor(*, args: Any, is_polyA: bool) -> float | None:
    if not args:
        return 0.0
    return args.polyA_dynamic_threshold_floor if is_polyA else args.exp_dynamic_threshold_floor


def dynamic_threshold_normalized(*, args: Any, is_polyA: bool) -> float | None:
    if not args:
        return 0.3
    return args.polyA_dynamic_threshold_normalized if is_polyA else args.exp_dynamic_threshold_normalized


def depth_threshold(*, args: Any, is_polyA: bool, scaling_factor: float | None) -> float | str | None:
    if not args:
        return None

    mode = peak_threshold_mode(args=args, is_polyA=is_polyA)
    if is_polyA:
        if mode == "dynamic":
            stat = args.polyA_dynamic_threshold_stat
            if stat == "max_norm_fixed":
                return f"dyn-{stat}-n{args.polyA_dynamic_threshold_normalized}"
            return f"dyn-{stat}-a{args.polyA_dynamic_threshold_alpha}-f{args.polyA_dynamic_threshold_floor}"
        return args.scaled_depth_threshold_polyA if scaling_factor is not None else args.unscaled_depth_threshold_polyA

    if mode == "dynamic":
        stat = args.exp_dynamic_threshold_stat
        if stat == "max_norm_fixed":
            return f"dyn-{stat}-n{args.exp_dynamic_threshold_normalized}"
        return f"dyn-{stat}-a{args.exp_dynamic_threshold_alpha}-f{args.exp_dynamic_threshold_floor}"
    return args.scaled_depth_threshold if scaling_factor is not None else args.unscaled_depth_threshold


def merge_distance(*, args: Any, is_polyA: bool) -> int | None:
    if not args:
        return None
    if not is_polyA:
        return args.merge_distance
    return args.merge_distance_polyA if args.merge_distance_polyA is not None else args.merge_distance


def build_effective_params(
    *,
    args: Any,
    is_polyA: bool,
    dedup_status: str | None,
    fallback_dedup_method: str | None,
    scaling_factor: float | None,
) -> ScItemEffectiveParams:
    token = polyA_token(is_polyA=is_polyA, args=args)
    return ScItemEffectiveParams(
        dedup_method=resolve_dedup_method(
            dedup_status=dedup_status,
            fallback_dedup_method=fallback_dedup_method,
        ),
        polyA_token=token,
        polyA_status=format_polyA_status(token) if is_polyA else None,
        depth_threshold=depth_threshold(args=args, is_polyA=is_polyA, scaling_factor=scaling_factor),
        peak_threshold_mode=peak_threshold_mode(args=args, is_polyA=is_polyA),
        dynamic_threshold_stat=dynamic_threshold_stat(args=args, is_polyA=is_polyA),
        dynamic_threshold_alpha=dynamic_threshold_alpha(args=args, is_polyA=is_polyA),
        dynamic_threshold_floor=dynamic_threshold_floor(args=args, is_polyA=is_polyA),
        dynamic_threshold_normalized=dynamic_threshold_normalized(args=args, is_polyA=is_polyA),
        merge_distance=merge_distance(args=args, is_polyA=is_polyA),
        read_rule=None if is_polyA else read_rule_token(args),
    )


def format_sc_item_base_name(
    *,
    identity: ScItemIdentity,
    flags: ScItemArtifactFlags,
    params: ScItemEffectiveParams,
) -> str:
    cat_list = [
        flags.prefix,
        "cohort" if identity.is_cohort else None,
        identity.uid,
        identity.sid if identity.uid is None else None,
        identity.ctg if identity.uid is None else None,
        params.dedup_method,
        params.polyA_status if flags.add_polyA else None,
        (
            f"rr-{params.read_rule}"
            if params.read_rule and flags.is_coverage and not flags.add_polyA
            else None
        ),
        identity.strand,
        "coverage" if flags.is_coverage else None,
        "scaled" if flags.scaling_factor is not None else None,
        "removeCDS" if flags.remove_CDS else None,
        "negate" if flags.is_negate else None,
        "peaks" if flags.is_peak else None,
        "peaks-merged" if flags.is_merged_peak else None,
        "peaks-assigned" if flags.is_assigned_peak else None,
        "annotated" if flags.is_annotated_bam else None,
        f"-{params.depth_threshold}" if flags.add_threshold else None,
        f"-{params.merge_distance}" if flags.add_merge_dist else None,
    ]
    cat_list = [str(part) for part in cat_list if part is not None]
    return ".".join(cat_list).replace(".-", "-").replace("._", "_")


def format_cohort_name(
    *,
    is_polyA: bool,
    dedup_method: str | None,
    polyA_status: str | None,
) -> str:
    return f"cohort.{dedup_method}.{polyA_status}" if is_polyA else f"cohort.{dedup_method}"
