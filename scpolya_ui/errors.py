"""Errors that carry a catalog key, and translation of known backend messages.

``str(exc)`` stays English so logs and tests are unchanged; pages show
``error_text(state.t, exc)`` instead.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


class UserError(ValueError):
    """A user-facing error with a translation key and its parameters."""

    def __init__(self, key: str, message: str, **params: Any) -> None:
        super().__init__(message)
        self.key = key
        self.params = params


# English messages produced outside the UI (adapter, sweep parsing) -> catalog keys.
# Named groups become translation parameters.
BACKEND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), key)
    for pattern, key in (
        (r"^Manifest must contain 1\.\.128 libraries$", "error.backend.manifest_size"),
        (r"^Input file missing: (?P<path>.+)$", "error.backend.input_missing"),
        (r"^Absolute paths required: (?P<path>.+)$", "error.backend.absolute_path"),
        (r"^Hash/size/path mismatch: (?P<path>.+)$", "error.backend.input_changed"),
        (r"^Raw input bytes exceed 2 GiB$", "error.backend.input_too_large"),
        (r"^Metadata exceeds 200000 rows$", "error.backend.metadata_too_large"),
        (r"^Requested condition absent from actual metadata$", "error.backend.condition_absent"),
        (r"^Contrast labels must differ$", "error.backend.contrast_same"),
        (r"^Donor occurs in both groups; paired=true required$", "error.backend.donor_both_groups"),
        (r"^Batch requires >=2 nonempty levels$", "error.backend.batch_levels"),
        (r"^Design confounded or no residual degrees of freedom$", "error.backend.design_confounded"),
        (r"^Condition confounded with recorded donor/batch$", "error.backend.condition_confounded"),
        (r"^Explicit R runtime required for a contrast$", "error.backend.r_runtime_required"),
        (r"^Rscript executable missing$", "error.backend.rscript_missing"),
        (r"^R library directory missing$", "error.backend.r_library_missing"),
        (r"^R runtime preflight failed: (?P<detail>.*)$", "error.backend.r_preflight"),
        (r"^Stale R runtime binding$", "error.backend.stale_runtime"),
        (r"^Stale source/Python binding$", "error.backend.stale_source"),
        (r"^Stale manifest path bindings$", "error.backend.stale_manifest"),
        (r"^Manifest path bindings changed while planning$", "error.backend.stale_manifest"),
        (r"^Source changed during execution$", "error.backend.source_changed_running"),
        (r"^Runtime changed during execution$", "error.backend.runtime_changed_running"),
        (r"^Plan is not executable$", "error.backend.plan_not_executable"),
        (r"^Moved plan directory$", "diff.backend.moved_plan"),
        (r"^min_pas_samples exceeds selected pseudobulks$", "error.backend.min_pas_samples"),
        (r"^timeout_seconds exceeds 1200$", "error.backend.timeout_limit"),
        (r"^Descriptive-only requires default methods$", "error.backend.descriptive_methods"),
        (r"^Invalid methods$", "error.backend.invalid_methods"),
        (r"^The plan's Python is missing: (?P<path>.+)$", "error.diff.python_missing"),
        (r"^The plan's Python changed: (?P<path>.+)$", "error.diff.python_changed"),
        (r"^The adapter source in this checkout differs from the plan's source binding$", "error.diff.source_differs"),
        (r"^Comparison unavailable: (?P<detail>.+)$", "error.igv.generic"),
        (r"^Sweep parameter '(?P<name>[^']+)' has no values$", "error.sweep.no_values"),
        (r"^'?Unknown sweep parameter: (?P<name>[^']+)'?$", "error.sweep.unknown"),
        (r"^invalid literal for int\(\) with base 10: '(?P<value>[^']*)'$", "error.sweep.not_integer"),
        (r"^could not convert string to float: '(?P<value>[^']*)'$", "error.sweep.not_number"),
    )
)

_EXCEPTION_PREFIX = re.compile(r"^(?:[A-Z][A-Za-z]*(?:Error|Exception)): (?P<message>.+)$", re.S)


def error_text(translate: Callable[..., str], error: BaseException | str) -> str:
    """The message for ``error`` in the user's language; unknown messages pass through."""
    key = getattr(error, "key", None)
    if key:
        text = translate(key, **getattr(error, "params", {}))
        if not text.startswith("[["):
            return text
    raw = (error.args[0] if isinstance(error, KeyError) and error.args else str(error)).strip()
    prefixed = _EXCEPTION_PREFIX.match(raw)
    message = prefixed["message"] if prefixed else raw
    for pattern, catalog_key in BACKEND_PATTERNS:
        match = pattern.match(message)
        if match is not None:
            text = translate(catalog_key, **match.groupdict())
            if not text.startswith("[["):
                return text
    return raw
