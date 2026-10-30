from __future__ import annotations
from typing import Any, Mapping, Iterable, Optional, Set
import inspect

def fmt_kwargs(
    kv: Mapping[str, Any],
    *,
    include: Optional[Iterable[str]] = None,
    exclude: Iterable[str] = ("self", "cls", "kwargs"),
    with_types: bool = True,
    sort_keys: bool = True,
) -> str:
    """
    Format a mapping (typically locals()) into a reproducible string.
    Produces key=value[:type] pairs.
    """
    inc: Optional[Set[str]] = set(include) if include is not None else None
    exc: Set[str] = set(exclude)

    items = []
    for k, v in kv.items():
        if k in exc:
            continue
        if inc is not None and k not in inc:
            continue
        if k.startswith("_"):
            continue

        val = _repr_stable(v)
        if with_types:
            t = type(v).__name__
            items.append(f"- {k}:\n  {val}:{t}\n")
        else:
            items.append(f"- {k}:\n  {val}\n")

    if sort_keys:
        items.sort()
    return "".join(items)

def _repr_stable(v: Any) -> str:
    """A conservative stable repr for common JSON-ish types."""
    if v is None:
        return "None"
    if isinstance(v, (bool, int, float)):
        return repr(v)
    if isinstance(v, str):
        return repr(v)  # keep quotes
    if isinstance(v, (list, tuple, set)):
        return "[" + ", ".join(_repr_stable(x) for x in v) + "]"
    if isinstance(v, dict):
        # stable order
        return "{" + ", ".join(f"{_repr_stable(k)}:{_repr_stable(v[k])}" for k in sorted(v)) + "}"
    # fallback (may include memory addresses for some objects)
    return repr(v)
