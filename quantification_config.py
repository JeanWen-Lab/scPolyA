from __future__ import annotations

from typing import Any, Mapping


QUANTIFICATION_MODES: tuple[str, ...] = ("legacy", "terminal-aware")
DEFAULT_QUANTIFICATION_MODE = "legacy"
DEFAULT_INTRONIC_MISPRIMED_RATIO_THRESHOLD = 0.5

# Internal identity revision for terminal annotation artifacts and coordinates.
# This is deliberately not a user-facing setting.
TERMINAL_ANNOTATION_SCHEMA_REVISION = "v4"
TERMINAL_ANNOTATION_SCHEMA_IDENTITY = (
    f"terminal-annotation-{TERMINAL_ANNOTATION_SCHEMA_REVISION}"
)
TERMINAL_ANNOTATION_COORDINATE_MODE = (
    f"terminal-aware@{TERMINAL_ANNOTATION_SCHEMA_IDENTITY}"
)
# Schemas whose recorded run outputs readers may open as current tables.
# v3 and v4 share terminal/intronic semantics; v4 only relabels gene-assigned
# PASs outside the CDS canonical model that v3 called Intergenic.
READABLE_TERMINAL_ANNOTATION_SCHEMAS: frozenset[str] = frozenset(
    {"terminal-annotation-v3", TERMINAL_ANNOTATION_SCHEMA_IDENTITY}
)


def resolved_quantification_mode(config: Mapping[str, Any]) -> str:
    value = config.get("quantification_mode", DEFAULT_QUANTIFICATION_MODE)
    if value in (None, ""):
        return DEFAULT_QUANTIFICATION_MODE
    normalized = str(value)
    if terminal_annotation_schema_from_coordinate_mode(normalized) is not None:
        return "terminal-aware"
    return normalized


def terminal_annotation_schema_payload(
    config: Mapping[str, Any],
) -> dict[str, str]:
    if resolved_quantification_mode(config) != "terminal-aware":
        return {}
    return {
        "terminal_annotation_schema": TERMINAL_ANNOTATION_SCHEMA_IDENTITY,
    }


def terminal_annotation_coordinate_mode(value: Any) -> Any:
    """Project only terminal annotation task coordinates onto the current schema."""
    if resolved_quantification_mode({"quantification_mode": value}) == "terminal-aware":
        return TERMINAL_ANNOTATION_COORDINATE_MODE
    return value


def terminal_annotation_schema_from_coordinate_mode(value: Any) -> str | None:
    """Return schema provenance encoded in a persisted annotation coordinate.

    Plain ``terminal-aware`` is deliberately historical/no-schema here. Callers
    reconstructing persisted identity must not infer the current schema from
    that public mode token.
    """
    normalized = str(value)
    prefix = "terminal-aware@"
    if normalized.startswith(prefix):
        schema = normalized[len(prefix):]
        if schema.startswith("terminal-annotation-") and len(schema) > len(
            "terminal-annotation-"
        ):
            return schema
    return None


def resolved_intronic_misprimed_ratio_threshold(
    config: Mapping[str, Any],
) -> float:
    value = config.get(
        "intronic_misprimed_ratio_threshold",
        DEFAULT_INTRONIC_MISPRIMED_RATIO_THRESHOLD,
    )
    if value in (None, ""):
        return DEFAULT_INTRONIC_MISPRIMED_RATIO_THRESHOLD
    return float(value)


def terminal_aware_quantification_payload(
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Return summary identity fields only when they affect behavior.

    Omitting the payload for legacy mode preserves pre-profile summary cache
    identities and output paths. The independently configurable intronic
    threshold is intentionally inactive in legacy mode.
    """

    mode = resolved_quantification_mode(config)
    if mode != "terminal-aware":
        return {}
    return {
        "quantification_mode": mode,
        "intronic_misprimed_ratio_threshold": (
            resolved_intronic_misprimed_ratio_threshold(config)
        ),
    }


def mode_scoped_quantification_payload(
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the mode identity only when it changes upstream semantics."""

    mode = resolved_quantification_mode(config)
    if mode == DEFAULT_QUANTIFICATION_MODE:
        return {}
    return {"quantification_mode": mode}


def summary_quantification_signature(config: Mapping[str, Any]) -> str:
    payload = terminal_aware_quantification_payload(config)
    if not payload:
        return ""
    return (
        ".quant-terminal-aware"
        ".intronic-misprimed-rt"
        f"{payload['intronic_misprimed_ratio_threshold']}"
    )


def quantification_log_message(config: Mapping[str, Any]) -> str:
    return (
        "QUANTIFICATION CONFIG "
        f"mode={resolved_quantification_mode(config)} "
        "intronic_misprimed_ratio_threshold="
        f"{resolved_intronic_misprimed_ratio_threshold(config)}"
    )
