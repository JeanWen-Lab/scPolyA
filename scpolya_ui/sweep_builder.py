from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from .config_schema import CONFIG_FIELDS, ConfigField, FIELD_BY_KEY, coerce_field_value
from .errors import UserError


COMMON_SWEEP_KEYS: tuple[str, ...] = (
    "coverage_scaling",
    "exp_peak_threshold_mode",
    "polyA_peak_threshold_mode",
    "merge_distance",
    "merge_distance_polyA",
    "unscaled_depth_threshold",
    "scaled_depth_threshold",
    "unscaled_depth_threshold_polyA",
    "scaled_depth_threshold_polyA",
    "exp_dynamic_threshold_stat",
    "polyA_dynamic_threshold_stat",
    "exp_dynamic_threshold_alpha",
    "polyA_dynamic_threshold_alpha",
    "exp_dynamic_threshold_floor",
    "polyA_dynamic_threshold_floor",
    "exp_dynamic_threshold_normalized",
    "polyA_dynamic_threshold_normalized",
    "misprime_A_count",
    "misprime_in",
    "misprimed_ratio_threshold",
    "quantification_mode",
    "intronic_misprimed_ratio_threshold",
    "annotate_peak_source",
)


@dataclass
class SweepRow:
    key: str
    values_text: str = ""
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# First threshold the base config actually uses becomes the default first axis.
_DEFAULT_FIRST_AXIS = (
    ("unscaled_depth_threshold", "0.5, 1.0, 2.0"),
    ("exp_dynamic_threshold_alpha", "0.5, 1.0, 2.0"),
    ("scaled_depth_threshold", "0.5, 1.0, 2.0"),
    ("merge_distance", "10, 20, 50"),
)


def default_sweep_rows(config: Mapping[str, Any] | None = None) -> list[SweepRow]:
    first = _DEFAULT_FIRST_AXIS[0]
    if config:
        from scpolya_ui.config_schema import inactive_sweep_keys

        first = next(
            (item for item in _DEFAULT_FIRST_AXIS if not inactive_sweep_keys(config, [item[0]])),
            _DEFAULT_FIRST_AXIS[-1],
        )
    return [
        SweepRow(*first),
        SweepRow("misprimed_ratio_threshold", "0.8, 1.0"),
    ]


def rows_from_grid(grid: Mapping[str, Iterable[Any]]) -> list[SweepRow]:
    rows: list[SweepRow] = []
    for key, values in grid.items():
        rendered = ", ".join(_render_sweep_value(value) for value in values)
        rows.append(SweepRow(str(key), rendered, True))
    return rows


def load_sweep_rows(items: Iterable[Mapping[str, Any]]) -> list[SweepRow]:
    rows: list[SweepRow] = []
    for item in items:
        key = str(item.get("key") or "")
        if not key:
            continue
        rows.append(
            SweepRow(
                key=key,
                values_text=str(item.get("values_text") or ""),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return rows


def dump_sweep_rows(rows: Iterable[SweepRow]) -> list[dict[str, Any]]:
    return [row.as_dict() for row in rows]


# Site scoring annotates finished peaks; sweeping its paths or switches would
# only produce identical counts with different annotation sidecars.
UNSWEPT_SECTIONS = frozenset({"Site scoring"})


def sweep_parameter_options() -> dict[str, str]:
    fields = {field.key: field for field in CONFIG_FIELDS}
    ordered = [key for key in COMMON_SWEEP_KEYS if key in fields]
    ordered.extend(
        field.key
        for field in CONFIG_FIELDS
        if field.key not in ordered and field.section not in UNSWEPT_SECTIONS
    )
    return {key: format_parameter_key(key, fields[key].label) for key in ordered}


DERIVED_PARAMETER_LABELS: dict[str, str] = {
    "peak_extra_key": "Peak-calling identity",
    "depth_threshold": "Effective depth threshold",
    "depth_threshold_polyA": "Effective PolyA depth threshold",
    "dynamic_threshold_stat": "Effective dynamic-threshold statistic",
    "dynamic_threshold_alpha": "Effective dynamic-threshold alpha",
    "dynamic_threshold_floor": "Effective dynamic-threshold floor",
    "dynamic_threshold_normalized": "Effective normalized threshold",
    "rna_gene_extra_key": "RNA-to-gene identity",
    "summary_extra_key": "Summary identity",
}


def format_parameter_key(key: str, label: str | None = None) -> str:
    """Keep domain wording and the exact technical key visible together."""

    domain_label = label or DERIVED_PARAMETER_LABELS.get(key) or key.replace("_", " ").title()
    return f"{domain_label} ({key})"


def parameter_display_label(key: str) -> str:
    fields = {field.key: field for field in CONFIG_FIELDS}
    field = fields.get(key)
    return format_parameter_key(key, field.label if field is not None else None)


def grid_from_sweep_rows(rows: Iterable[SweepRow]) -> dict[str, list[Any]]:
    grid: dict[str, list[Any]] = {}
    for row in rows:
        if not row.enabled:
            continue
        key = str(row.key).strip()
        if not key:
            continue
        if key not in FIELD_BY_KEY:
            raise KeyError(f"Unknown sweep parameter: {key}")
        if key in grid:
            # A second row for the same key would silently replace the first row's values.
            raise UserError("study.error.duplicate_key", f"Sweep parameter {key!r} appears twice", name=key)
        values = parse_sweep_values(FIELD_BY_KEY[key], row.values_text)
        if not values:
            raise ValueError(f"Sweep parameter {key!r} has no values")
        grid[key] = values
    return grid


def parse_sweep_values(field: ConfigField, text: str) -> list[Any]:
    raw = str(text or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise TypeError(f"Sweep values for {field.key!r} must be a JSON array")
        tokens = parsed
    else:
        tokens = [token.strip() for token in re.split(r"[\n,]+", raw) if token.strip()]
    values = [_coerce_sweep_value(field, token) for token in tokens]
    deduped: list[Any] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _coerce_sweep_value(field: ConfigField, value: Any) -> Any:
    if isinstance(value, str) and value.lower() in {"none", "null"}:
        return None
    if field.kind == "bool" and isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    coerced = coerce_field_value(field, value)
    _check_sweep_value(field, coerced)
    return coerced


def _check_sweep_value(field: ConfigField, value: Any) -> None:
    """Reject values the pipeline would refuse later, naming the parameter and the value."""
    if value is None:
        return
    if field.kind == "enum" and field.choices and value not in field.choices:
        raise UserError(
            "study.error.bad_choice",
            f"{value!r} is not an allowed value for {field.key}",
            name=field.key,
            value=str(value),
            choices=", ".join(field.choices),
        )
    if field.kind in ("int", "float") and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise UserError("study.error.not_finite", f"{field.key} must be a finite number", name=field.key, value=str(value))
        if field.minimum is not None and number < field.minimum:
            raise UserError(
                "study.error.below_minimum",
                f"{field.key} must be at least {field.minimum}, got {value}",
                name=field.key,
                value=str(value),
                limit=str(field.minimum),
            )
        if field.maximum is not None and number > field.maximum:
            raise UserError(
                "study.error.above_maximum",
                f"{field.key} must be at most {field.maximum}, got {value}",
                name=field.key,
                value=str(value),
                limit=str(field.maximum),
            )


def _render_sweep_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
