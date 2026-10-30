from __future__ import annotations

from typing import Any, Dict, Mapping
from urllib.parse import quote

from .define import Params, TASK_PARAM_SCOPES


def stringify_params(params: Mapping[str, Any] | None) -> Dict[str, str]:
    if not params:
        return {}
    return {str(k): str(v) for k, v in params.items()}


def encode_params(params: Mapping[str, Any]) -> str:
    """Encode params to a stable, URL-escaped key=value&key=value string."""
    p = stringify_params(params)
    if not p:
        return ""
    parts = [f"{quote(k, safe='')}={quote(p[k], safe='')}" for k in sorted(p)]
    return "&".join(parts)


def canonical_variant_str(
    task_base: str,
    *,
    context_params: Params | None = None,
    task_params: Params | None = None,
) -> str:
    """Merge + scope-filter params then encode to a stable variant_str."""

    merged: Dict[str, str] = {}
    merged.update(stringify_params(context_params))
    merged.update(stringify_params(task_params))

    scope = TASK_PARAM_SCOPES.get(task_base)
    if scope is not None:
        merged = {k: v for k, v in merged.items() if k in scope}

    return encode_params(merged)
