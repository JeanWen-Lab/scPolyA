"""Pipeline cache semantic epoch for *current* task coordinates.

Every current task-instance contract carries ``pipeline_cache_epoch`` as a task
parameter, so it is part of every new ``TaskCoordinateV1``. Bumping the epoch
makes all completions written under an earlier epoch miss the exact-coordinate
lookup. The entry guard refuses predecessor or untracked occupied run roots and
requires a new run root; wholly current roots resume normally.

This is an engineering cache-validity token, not a scientific setting: it is not
a CLI/config option, and users must not tune it.

Scope rules:
- Inject only when building *current* contracts
  (``build_current_task_instance_contract_from_keys_v2``). A
  ``CurrentTaskInstanceContractV2`` must carry exactly one epoch field whose
  value is ``PIPELINE_CACHE_EPOCH``; anything else fails closed.
- Never inject into historical decode, legacy-DONE migration, or stored-record
  reconstruction. Old completions keep their stored coordinates, without an
  epoch, so they cannot masquerade as current-epoch completions.
- Only explicitly known predecessor generations (``KNOWN_PREDECESSOR_CACHE_EPOCHS``)
  may be set aside in favour of current-epoch evidence. Any other explicit
  value (for example a future epoch written by newer code) is an incompatible
  run root: stop and use a new run root (``CacheEpochCompatibilityError``).

History:
- epoch absent (implicit 1): completions written before this module existed.
- "2": the accepted cache generation for UMI dedup grouping by the full
  corrected cell tag (``--cell-tag-split ''``).
- "3": the integrated cache generation for the accepted dedup semantics and
  the coordinated mispriming, zero-support and basewise-CDS semantics. Roots
  from absent/implicit-1 or epoch 2 are recognized read-only but must not be
  resumed: use a new run root so historical scientific products remain intact.
- "4": downstream sample IDs come from the authoritative unit table, and
  summary metadata is rebuilt from the current annotation with textual IDs.
  Canonical transcript selection sums inclusive GTF CDS lengths (end-start+1).
  Generation 3 products remain readable as history but cannot be resumed.
- "5": selectable read-eligibility rule (``--count-read-rule``/
  ``--multimapper-policy``) in the poly(A) extract, coverage and counting
  task identities; per-PAS assignment diagnostics; counting-status and
  iPUI-support columns. Legacy-rule outputs keep their historical values,
  but generation 4 roots must not be resumed by this version.

When bumping: add the previous value to ``KNOWN_PREDECESSOR_CACHE_EPOCHS``.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal

PIPELINE_CACHE_EPOCH_KEY = "pipeline_cache_epoch"
PIPELINE_CACHE_EPOCH = "5"
# ``None`` = field absent (implicit epoch 1). Explicit values are compared as strings.
KNOWN_PREDECESSOR_CACHE_EPOCHS: frozenset[str | None] = frozenset({None, "2", "3", "4"})

CacheEpochClass = Literal["current", "predecessor", "unknown"]

NEW_RUN_ROOT_ADVICE = (
    "This run root was written by a different, incompatible scPolyA cache "
    "generation. Do not resume it with this version: start a new run root "
    "(a new --output-dir or --run-id). Nothing was scheduled or overwritten."
)


class CacheEpochCompatibilityError(RuntimeError):
    """A run root holds cache-epoch evidence this code must not reinterpret."""


def _key_value(field: Any) -> tuple[str, Any]:
    if isinstance(field, (tuple, list)) and len(field) == 2:
        return str(field[0]), field[1]
    if isinstance(field, dict):
        return str(field.get("key")), field.get("value", field.get("legacy_value"))
    if hasattr(field, "value"):
        return str(field.key), field.value
    if hasattr(field, "legacy_value"):
        return str(field.key), field.legacy_value
    raise TypeError(f"Unsupported cache-epoch field: {type(field).__name__}")


def cache_epoch_values(fields: Iterable[Any]) -> tuple[Any, ...]:
    """All epoch values in a parameter field sequence (pairs, mappings or fields)."""

    return tuple(
        value for key, value in map(_key_value, fields) if key == PIPELINE_CACHE_EPOCH_KEY
    )


def require_current_cache_epoch(fields: Iterable[Any], *, label: str) -> None:
    """Fail closed unless ``fields`` carry exactly one current epoch value."""

    values = cache_epoch_values(fields)
    if len(values) != 1:
        raise ValueError(
            f"{label} must carry exactly one {PIPELINE_CACHE_EPOCH_KEY!r} "
            f"field, found {len(values)}"
        )
    if not isinstance(values[0], str) or values[0] != PIPELINE_CACHE_EPOCH:
        raise ValueError(
            f"{label} carries {PIPELINE_CACHE_EPOCH_KEY}={values[0]!r}; "
            f"current contracts require {PIPELINE_CACHE_EPOCH!r}"
        )


def classify_cache_epoch(fields: Iterable[Any]) -> CacheEpochClass:
    """Classify stored parameters as current, known predecessor, or unknown.

    Duplicate epoch fields or non-string values are ``unknown`` (fail closed).
    """

    values = cache_epoch_values(fields)
    if not values:
        return "predecessor" if None in KNOWN_PREDECESSOR_CACHE_EPOCHS else "unknown"
    if len(values) > 1 or not isinstance(values[0], str):
        return "unknown"
    if values[0] == PIPELINE_CACHE_EPOCH:
        return "current"
    if values[0] in KNOWN_PREDECESSOR_CACHE_EPOCHS:
        return "predecessor"
    return "unknown"


def describe_cache_epoch(fields: Iterable[Any]) -> str:
    values = cache_epoch_values(fields)
    if not values:
        return "absent (implicit 1)"
    return ",".join(repr(v) for v in values)
