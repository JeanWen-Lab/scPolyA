from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConfigRow:
    key: str
    value: Any
    changed: bool = False
    old_value: Any = None
    new_value: Any = None


def config_rows(config: dict[str, Any]) -> tuple[ConfigRow, ...]:
    return tuple(ConfigRow(key=key, value=config[key]) for key in sorted(config))


def config_diff_rows(base: dict[str, Any], proposed: dict[str, Any]) -> tuple[ConfigRow, ...]:
    keys = sorted(set(base) | set(proposed))
    rows = []
    for key in keys:
        old = base.get(key)
        new = proposed.get(key)
        rows.append(ConfigRow(key=key, value=new, changed=old != new, old_value=old, new_value=new))
    return tuple(rows)
