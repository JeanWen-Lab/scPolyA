from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .run_config_preview import (
    RunConfigPreview,
    build_effective_config_projection,
    load_run_config_snapshot,
    preview_rerun_from_run_configs,
)
from .run_layout import RunLayout
from quantification_config import (
    resolved_intronic_misprimed_ratio_threshold,
    resolved_quantification_mode,
    terminal_annotation_schema_payload,
)
from utils.terminal_quant_utils import quantification_artifact_suffix


DEFAULT_MAX_VARIANTS = 128


@dataclass(frozen=True)
class PeakArtifactPreview:
    final_name: str
    annotate_peak_source: str
    final_dir: str
    mispriming_filtered_bed: str
    mispriming_filtered_merged_bed: str
    annotated_dir: str
    annotated_bed: str
    annotated_bigbed: str
    annotated_gtf: str
    final_name_candidates: tuple[str, ...] = ()
    mispriming_filtered_bed_candidates: tuple[str, ...] = ()
    mispriming_filtered_merged_bed_candidates: tuple[str, ...] = ()
    annotated_bed_candidates: tuple[str, ...] = ()
    annotated_bigbed_candidates: tuple[str, ...] = ()
    annotated_gtf_candidates: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "final_name": self.final_name,
            "annotate_peak_source": self.annotate_peak_source,
            "final_dir": self.final_dir,
            "mispriming_filtered_bed": self.mispriming_filtered_bed,
            "mispriming_filtered_merged_bed": self.mispriming_filtered_merged_bed,
            "annotated_dir": self.annotated_dir,
            "annotated_bed": self.annotated_bed,
            "annotated_bigbed": self.annotated_bigbed,
            "annotated_gtf": self.annotated_gtf,
        }


@dataclass(frozen=True)
class ParameterStudyVariantPreview:
    id: str
    label: str
    overrides: Mapping[str, Any]
    config: Mapping[str, Any]
    rerun_preview: RunConfigPreview
    peak_artifacts: PeakArtifactPreview | None = None

    @property
    def changed_raw_keys(self) -> tuple[str, ...]:
        return self.rerun_preview.changed_raw_keys

    @property
    def changed_effective_keys(self) -> tuple[str, ...]:
        return self.rerun_preview.changed_effective_keys

    @property
    def impacted_task_count(self) -> int:
        return len(self.rerun_preview.rerun_preview.impacted_tasks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "overrides": _normalize_value(self.overrides),
            "config": _normalize_value(self.config),
            "changed_raw_keys": self.changed_raw_keys,
            "changed_effective_keys": self.changed_effective_keys,
            "impacted_task_count": self.impacted_task_count,
            "peak_artifacts": self.peak_artifacts.as_dict() if self.peak_artifacts else None,
        }


@dataclass(frozen=True)
class ParameterStudyPreview:
    base_config: Mapping[str, Any]
    variants: tuple[ParameterStudyVariantPreview, ...]

    @property
    def variant_count(self) -> int:
        return len(self.variants)

    @property
    def max_impacted_task_count(self) -> int:
        return max((variant.impacted_task_count for variant in self.variants), default=0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_config": _normalize_value(self.base_config),
            "variant_count": self.variant_count,
            "max_impacted_task_count": self.max_impacted_task_count,
            "variants": [variant.as_dict() for variant in self.variants],
        }


def expand_parameter_grid(
    grid: Mapping[str, Sequence[Any]],
    *,
    max_variants: int = DEFAULT_MAX_VARIANTS,
) -> tuple[dict[str, Any], ...]:
    """Expand a parameter grid into deterministic override dictionaries."""

    keys = tuple(str(key) for key in grid)
    values_by_key = []
    for key in keys:
        values = _grid_values(grid[key])
        if not values:
            raise ValueError(f"Parameter grid key {key!r} has no values")
        values_by_key.append(values)

    variant_count = 1
    for values in values_by_key:
        variant_count *= len(values)
        if variant_count > max_variants:
            raise ValueError(f"Parameter grid expands to more than {max_variants} variants")

    variants = []
    for values in itertools.product(*values_by_key):
        variants.append(dict(zip(keys, values)))
    return tuple(variants)


def preview_parameter_study_from_state(
    state_or_db_path: str | Path,
    variant_overrides: Iterable[Mapping[str, Any]],
    *,
    base_config: Mapping[str, Any] | None = None,
) -> ParameterStudyPreview:
    old_config = load_run_config_snapshot(state_or_db_path)
    base = dict(base_config) if base_config is not None else dict(old_config)
    variants = []
    for index, overrides in enumerate(variant_overrides, start=1):
        override_dict = dict(overrides)
        config = apply_variant_overrides(base, override_dict)
        variants.append(
            ParameterStudyVariantPreview(
                id=f"V{index:03d}",
                label=variant_label(index, override_dict),
                overrides=override_dict,
                config=config,
                rerun_preview=preview_rerun_from_run_configs(old_config, config),
                peak_artifacts=preview_peak_artifacts_for_config(config),
            )
        )
    return ParameterStudyPreview(base_config=base, variants=tuple(variants))


def apply_variant_overrides(
    base_config: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    config = dict(base_config)
    for key, value in overrides.items():
        if value is None:
            config.pop(str(key), None)
        else:
            config[str(key)] = value
    return config


def variant_label(index: int, overrides: Mapping[str, Any]) -> str:
    if not overrides:
        return f"V{index:03d}"
    parts = [f"{key}={_short_value(value)}" for key, value in overrides.items()]
    return f"V{index:03d} " + ", ".join(parts)


def preview_peak_artifacts_for_config(config: Mapping[str, Any]) -> PeakArtifactPreview | None:
    final_item = final_peak_item_for_config(config)
    if final_item is None:
        return None
    output_dir = config.get("output_dir")
    run_id = config.get("run_id")
    if output_dir in (None, "") or run_id in (None, ""):
        return None

    layout = RunLayout(out=Path(str(output_dir)).expanduser(), run_id=str(run_id))
    final_names = layout.FinalNameCandidatesFromDict(final_item)
    final_name = final_names[0]
    final_dir = layout.FinalOutput / final_name
    source = str(config.get("annotate_peak_source") or "resolved")
    mode = resolved_quantification_mode(config)
    mispriming_candidates = layout.FinalOutputFileCandidates(
        suffix=quantification_artifact_suffix(
            "___peaks-mispriming-filtered.bed", mode
        ),
        d=final_item,
    )
    merged_candidates = tuple(
        path.with_name(f"{path.stem}-merged.bed")
        for path in mispriming_candidates
    )
    annotated_bed_candidates = layout.AnnotatedFinalOutputFileCandidates(
        suffix=quantification_artifact_suffix("___peaks-annotated.bed", mode),
        d=final_item,
    )
    annotated_bigbed_candidates = (
        layout.AnnotatedFinalOutputFileCandidates(
            suffix=quantification_artifact_suffix("___peaks-annotated.bb", mode),
            d=final_item,
        )
    )
    annotated_gtf_candidates = layout.AnnotatedFinalOutputFileCandidates(
        suffix=quantification_artifact_suffix("___peaks-annotated.gtf", mode),
        d=final_item,
    )
    annotated_dir = annotated_bed_candidates[0].parent
    return PeakArtifactPreview(
        final_name=final_name,
        annotate_peak_source=source,
        final_dir=str(final_dir),
        mispriming_filtered_bed=str(mispriming_candidates[0]),
        mispriming_filtered_merged_bed=str(merged_candidates[0]),
        annotated_dir=str(annotated_dir),
        annotated_bed=str(annotated_bed_candidates[0]),
        annotated_bigbed=str(annotated_bigbed_candidates[0]),
        annotated_gtf=str(annotated_gtf_candidates[0]),
        final_name_candidates=final_names,
        mispriming_filtered_bed_candidates=tuple(
            str(path) for path in mispriming_candidates
        ),
        mispriming_filtered_merged_bed_candidates=tuple(
            str(path) for path in merged_candidates
        ),
        annotated_bed_candidates=tuple(
            str(path) for path in annotated_bed_candidates
        ),
        annotated_bigbed_candidates=tuple(
            str(path) for path in annotated_bigbed_candidates
        ),
        annotated_gtf_candidates=tuple(
            str(path) for path in annotated_gtf_candidates
        ),
    )


def artifact_exists(path: str | Path) -> bool:
    return Path(path).expanduser().exists()


def final_peak_item_for_config(
    config: Mapping[str, Any],
) -> dict[str, Any] | None:
    def _apply_annotation_schema(item: dict[str, Any]) -> None:
        explicit_schema = config.get("terminal_annotation_schema")
        if explicit_schema not in (None, ""):
            item["terminal_annotation_schema"] = explicit_schema
        else:
            item.update(terminal_annotation_schema_payload(item))

    resolved_keys = (
        "dedup_method",
        "polyA_status",
        "coverage_scaling",
        "depth_threshold",
        "merge_distance",
    )
    if all(config.get(key) not in (None, "") for key in resolved_keys) and not isinstance(
        config.get("depth_threshold"), (Mapping, list, tuple)
    ) and not isinstance(config.get("merge_distance"), (Mapping, list, tuple)):
        # Artifact manifests and structured DONE coordinates already contain
        # resolved peak values. Consuming them directly avoids replacing a
        # historical coordinate with values re-derived from the current CLI.
        item = {
            "dedup_method": config["dedup_method"],
            "polyA_status": config["polyA_status"],
            "coverage_scaling": config["coverage_scaling"],
            "depth_threshold": config["depth_threshold"],
            "depth_threshold_polyA": config.get("depth_threshold_polyA"),
            "merge_distance": config["merge_distance"],
            "merge_distance_polyA": config.get("merge_distance_polyA"),
            "misprime_A_count": config.get("misprime_A_count"),
            "misprime_in": config.get("misprime_in"),
            "misprimed_ratio_threshold": config.get("misprimed_ratio_threshold"),
            "annotate_peak_source": config.get("annotate_peak_source") or "resolved",
            "quantification_mode": resolved_quantification_mode(config),
            "intronic_misprimed_ratio_threshold": (
                resolved_intronic_misprimed_ratio_threshold(config)
            ),
        }
        _apply_annotation_schema(item)
        return item
    projection = build_effective_config_projection(config)
    params = projection.preview_params
    required = ("dedup_method", "polyA_status", "coverage_scaling", "depth_threshold", "merge_distance")
    if any(params.get(key) in (None, "") for key in required):
        return None
    depth_threshold = _paired_value(params["depth_threshold"], "exp")
    merge_distance = _paired_value(params["merge_distance"], "exp")
    if depth_threshold is None or merge_distance is None:
        return None
    item = {
        "dedup_method": params["dedup_method"],
        "polyA_status": params["polyA_status"],
        "coverage_scaling": params["coverage_scaling"],
        "depth_threshold": depth_threshold,
        "depth_threshold_polyA": params.get("depth_threshold_polyA"),
        "merge_distance": merge_distance,
        "merge_distance_polyA": params.get("merge_distance_polyA"),
        "misprime_A_count": config.get("misprime_A_count"),
        "misprime_in": config.get("misprime_in"),
        "misprimed_ratio_threshold": config.get("misprimed_ratio_threshold"),
        "annotate_peak_source": config.get("annotate_peak_source") or "resolved",
        "quantification_mode": resolved_quantification_mode(config),
        "intronic_misprimed_ratio_threshold": (
            resolved_intronic_misprimed_ratio_threshold(config)
        ),
    }
    _apply_annotation_schema(item)
    return item


def _paired_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == key:
                return item[1]
    return None


def _grid_values(value: Sequence[Any]) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("Parameter grid values must be arrays")
    return tuple(value)


def _short_value(value: Any) -> str:
    text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= 32:
        return text
    return text[:29] + "..."


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_normalize_value(val) for val in value]
    if isinstance(value, list):
        return [_normalize_value(val) for val in value]
    if isinstance(value, Path):
        return str(value)
    return value
