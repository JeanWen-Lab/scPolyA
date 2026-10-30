"""Per-user UI preferences kept beside the workspace registry.

These are presentation choices only (first-run guide seen, agent mode). They
never change a run, its configuration or ``state.sqlite``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from scpolya_ui.registry import ui_home

PREFERENCES_FILE = "preferences.json"


@dataclass(frozen=True)
class Preferences:
    onboarding_done: bool = False
    agent_mode: bool = False


def preferences_path(home: Path | None = None) -> Path:
    return (home or ui_home()) / PREFERENCES_FILE


def load_preferences(home: Path | None = None) -> Preferences:
    path = preferences_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Preferences()
    if not isinstance(raw, dict):
        return Preferences()
    known = {item.name for item in fields(Preferences)}
    return Preferences(**{key: bool(value) for key, value in raw.items() if key in known})


def update_preferences(home: Path | None = None, **changes: bool) -> Preferences:
    current = replace(load_preferences(home), **{key: bool(value) for key, value in changes.items()})
    path = preferences_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(asdict(current), indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return current
