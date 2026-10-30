from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


FALLBACK_LOCALE = "en"
DEFAULT_LOCALE = "en"
LOCALES_DIR = Path(__file__).resolve().parent / "locales"


def available_locales() -> tuple[str, ...]:
    if not LOCALES_DIR.exists():
        return (FALLBACK_LOCALE,)
    locales = sorted(path.stem for path in LOCALES_DIR.glob("*.json"))
    return tuple(locales) or (FALLBACK_LOCALE,)


@lru_cache(maxsize=None)
def load_catalog(locale: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{locale}.json"
    if not path.exists():
        if locale == FALLBACK_LOCALE:
            return {}
        return load_catalog(FALLBACK_LOCALE)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Locale catalog must be a JSON object: {path}")
    return {str(key): str(value) for key, value in data.items()}


# "{count|run|runs}" picks the singular form when the named value is 1.
_PLURAL = re.compile(r"\{(\w+)\|([^|{}]*)\|([^|{}]*)\}")


def _plural(match: re.Match[str], params: Mapping[str, Any]) -> str:
    name, one, other = match.groups()
    value = params.get(name)
    try:
        single = value is not None and float(value) == 1
    except (TypeError, ValueError):
        single = False
    return one if single else other


def translate(locale: str, key: str, **params: Any) -> str:
    catalog = load_catalog(locale)
    template = catalog.get(key)
    if template is None and locale != FALLBACK_LOCALE:
        template = load_catalog(FALLBACK_LOCALE).get(key)
    if template is None:
        return f"[[{key}]]"
    template = _PLURAL.sub(lambda match: _plural(match, params), template)
    try:
        return template.format(**params)
    except Exception:
        return template


def validate_catalog_parity(
    locales: tuple[str, ...] | None = None,
    *,
    fallback_locale: str = FALLBACK_LOCALE,
) -> Mapping[str, tuple[str, ...]]:
    """Return missing keys per locale relative to the fallback catalog."""
    fallback = load_catalog(fallback_locale)
    expected = set(fallback)
    missing: dict[str, tuple[str, ...]] = {}
    for locale in locales or available_locales():
        if locale == fallback_locale:
            continue
        keys = set(load_catalog(locale))
        gap = tuple(sorted(expected - keys))
        if gap:
            missing[locale] = gap
    return missing
