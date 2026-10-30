from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


UI_HOME_ENV = "SCPOLYA_UI_HOME"
REGISTRY_VERSION = 1


@dataclass
class PathMapping:
    old_path: str
    new_path: str
    note: str = ""
    created_at: str = ""


@dataclass
class WorkspaceRecord:
    id: str
    alias: str
    root_path: str
    state_dir: str
    db_path: str
    notes: str = ""
    path_mappings: List[PathMapping] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    last_opened_at: str = ""
    # Saved mappings that fail the save rules (e.g. written by an older version). They are kept on disk
    # and listed so they can be removed, but never applied. Stored back inside "path_mappings".
    ignored_path_mappings: List[PathMapping] = field(default_factory=list)


@dataclass
class WorkspaceRegistry:
    version: int = REGISTRY_VERSION
    workspaces: List[WorkspaceRecord] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def ui_home() -> Path:
    return Path(os.environ.get(UI_HOME_ENV, Path.home() / ".scpolya-ui")).expanduser()


def registry_path(home: Path | None = None) -> Path:
    return (home or ui_home()) / "workspaces.json"


def load_registry(home: Path | None = None) -> WorkspaceRegistry:
    path = registry_path(home)
    if not path.exists():
        return WorkspaceRegistry()
    data = json.loads(path.read_text(encoding="utf-8"))
    workspaces = []
    for item in data.get("workspaces", []):
        item = dict(item)
        item.pop("ignored_path_mappings", None)
        usable: List[PathMapping] = []
        ignored: List[PathMapping] = []
        for raw in item.get("path_mappings", []):
            mapping = PathMapping(**raw)
            problem = path_mapping_problem(mapping.old_path, mapping.new_path)
            if problem is None and any(
                Path(other.old_path).expanduser() == Path(mapping.old_path).expanduser() for other in usable
            ):
                problem = "duplicate"
            (ignored if problem else usable).append(mapping)
        item["path_mappings"] = usable
        item["ignored_path_mappings"] = ignored
        workspaces.append(WorkspaceRecord(**item))
    return WorkspaceRegistry(
        version=int(data.get("version", REGISTRY_VERSION)),
        workspaces=workspaces,
    )


def save_registry(registry: WorkspaceRegistry, home: Path | None = None) -> None:
    path = registry_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": registry.version,
        "workspaces": [_workspace_to_dict(workspace) for workspace in registry.workspaces],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def upsert_workspace(
    registry: WorkspaceRegistry,
    *,
    path: str | Path,
    alias: str | None = None,
    notes: str = "",
) -> WorkspaceRecord:
    now = utc_now()
    db_path = resolve_state_db(path)
    state_dir = db_path.parent
    root_path = infer_run_root(db_path)
    canonical_db = str(db_path.expanduser().resolve())

    for workspace in registry.workspaces:
        if str(Path(workspace.db_path).expanduser().resolve()) == canonical_db:
            workspace.alias = alias or workspace.alias or root_path.name
            workspace.root_path = str(root_path)
            workspace.state_dir = str(state_dir)
            workspace.db_path = str(db_path)
            workspace.notes = notes or workspace.notes
            workspace.updated_at = now
            workspace.last_opened_at = now
            return workspace

    workspace = WorkspaceRecord(
        id=f"W-{uuid.uuid4().hex[:12]}",
        alias=alias or root_path.name or "scpolyA run",
        root_path=str(root_path),
        state_dir=str(state_dir),
        db_path=str(db_path),
        notes=notes,
        created_at=now,
        updated_at=now,
        last_opened_at=now,
    )
    registry.workspaces.insert(0, workspace)
    return workspace


def touch_workspace(registry: WorkspaceRegistry, workspace_id: str) -> WorkspaceRecord | None:
    workspace = find_workspace(registry, workspace_id)
    if workspace is None:
        return None
    workspace.last_opened_at = utc_now()
    registry.workspaces.sort(key=lambda item: item.last_opened_at or item.created_at, reverse=True)
    return workspace


def find_workspace(registry: WorkspaceRegistry, workspace_id: str) -> WorkspaceRecord | None:
    return next((workspace for workspace in registry.workspaces if workspace.id == workspace_id), None)


def path_mapping_problem(old_path: str | Path, new_path: str | Path) -> str | None:
    """Why a mapping may not be saved or applied: 'relative', 'traversal' or 'same'; None if it is usable.

    The same rule guards saving and loading, so a mapping that is accepted can always be applied.
    """
    old = Path(old_path).expanduser()
    new = Path(new_path).expanduser()
    if not old.is_absolute() or not new.is_absolute():
        return "relative"
    if ".." in old.parts or ".." in new.parts:
        return "traversal"
    if old == new:
        return "same"
    return None


def add_path_mapping(
    workspace: WorkspaceRecord,
    *,
    old_path: str | Path,
    new_path: str | Path,
    note: str = "",
) -> PathMapping:
    problem = path_mapping_problem(old_path, new_path)
    if problem is not None:
        raise ValueError(f"Path mapping refused ({problem}): {old_path} -> {new_path}")
    mapping = PathMapping(
        old_path=str(Path(old_path).expanduser()),
        new_path=str(Path(new_path).expanduser()),
        note=note,
        created_at=utc_now(),
    )
    workspace.path_mappings.append(mapping)
    workspace.updated_at = utc_now()
    return mapping


def resolve_state_db(path: str | Path) -> Path:
    path = Path(path).expanduser()
    candidates = state_db_candidates(path)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def state_db_candidates(path: str | Path) -> List[Path]:
    path = Path(path).expanduser()
    if path.name == "state.sqlite":
        return [path]

    candidates = [
        path / "state.sqlite",
        path / "state" / "state.sqlite",
    ]
    if path.is_dir():
        candidates.extend(sorted(path.glob("*/state/state.sqlite")))
    return _dedupe_paths(candidates)


def find_state_dbs(path: str | Path, *, limit: int = 50) -> List[Path]:
    path = Path(path).expanduser()
    candidates = []
    if path.name == "state.sqlite":
        candidates.append(path)
    if path.is_dir():
        candidates.extend(path.glob("state.sqlite"))
        candidates.extend(path.glob("state/state.sqlite"))
        candidates.extend(path.glob("*/state/state.sqlite"))
    existing = [candidate.resolve() for candidate in _dedupe_paths(candidates) if candidate.exists()]
    return existing[:limit]


def infer_run_root(db_path: str | Path) -> Path:
    db_path = Path(db_path).expanduser()
    if db_path.name == "state.sqlite" and db_path.parent.name == "state":
        return db_path.parent.parent.resolve()
    return db_path.parent.resolve()


def apply_path_mappings_to_config(
    config: Dict[str, Any],
    mappings: Iterable[PathMapping],
) -> Dict[str, Any]:
    mapped = dict(config)
    for key, value in list(mapped.items()):
        if isinstance(value, str):
            mapped[key] = apply_path_mappings_to_value(value, mappings)
    return mapped


def apply_path_mappings_to_value(value: str, mappings: Iterable[PathMapping]) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        return value
    candidates = []
    for mapping in mappings:
        old = Path(mapping.old_path).expanduser()
        new = Path(mapping.new_path).expanduser()
        if not old.is_absolute() or not new.is_absolute():
            continue
        if not (path == old or old in path.parents):
            continue
        # Only a mapping that would rewrite this value can refuse it; an unrelated bad mapping must not.
        if ".." in old.parts or ".." in new.parts:
            raise ValueError("Workspace path mappings cannot contain parent traversal")
        if ".." in path.parts:
            raise ValueError("Mapped paths cannot contain parent traversal")
        candidates.append((len(old.parts), old, new))
    if not candidates:
        return value
    # Specific file/directory bindings take precedence over broad relocation.
    _, old, new = max(candidates, key=lambda item: item[0])
    if any(other_old == old and other_new != new for _, other_old, other_new in candidates):
        raise ValueError("Conflicting workspace path mappings for the same prefix")
    return str(new / path.relative_to(old))


def _workspace_to_dict(workspace: WorkspaceRecord) -> Dict[str, Any]:
    data = asdict(workspace)
    data.pop("ignored_path_mappings", None)
    data["path_mappings"] = [
        asdict(mapping) for mapping in [*workspace.path_mappings, *workspace.ignored_path_mappings]
    ]
    return data


def _dedupe_paths(paths: Iterable[Path]) -> List[Path]:
    seen = set()
    out = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out
