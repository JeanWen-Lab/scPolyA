"""Fixed read/return bounds and non-following path checks for inspection."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

MAX_RESPONSE_BYTES = 256 * 1024  # Canonical ASCII JSON, including CLI newline.
RESPONSE_RESERVE_BYTES = 32 * 1024  # Issues and coverage summaries.
MAX_METADATA_BYTES = 32 * 1024
MAX_INVOCATION_BYTES = 64 * 1024
MAX_TABLE_BYTES = 64 * 1024
MAX_CONFIG_BYTES = 32 * 1024
MAX_COMPLETION_BYTES = 64 * 1024
MAX_COMPLETION_ROWS = 100
MAX_IDENTITY_ROWS = 100
MAX_NAMESPACE_ENTRIES = 128
MAX_STATE_ROWS = 128
MAX_STATE_TEXT_BYTES = 256 * 1024
MAX_INVOCATIONS = 100


def encode_inspection(value: object) -> str:
    """The response budget and CLI use this exact ASCII JSON encoding."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def lexical_path(value: str | Path) -> Path:
    expanded = os.path.expanduser(os.fspath(value))
    # Never erase a parent traversal before checking its existing ancestry.
    # `link/..` can name a different parent in the filesystem than abspath
    # would produce. V1 conservatively rejects all such raw representations.
    if ".." in Path(expanded).parts:
        raise ValueError("parent traversal '..' is not supported in inspection paths")
    return Path(os.path.abspath(expanded))


def checked_stat(path: Path, root: Path) -> os.stat_result | None:
    """Check each existing component with lstat, rejecting symlinks before descent.

    No resolve/exists/stat/target open is used. Missing ancestry returns None.
    This is a preflight for quiescent directories, not a concurrent path-swap
    sandbox for native functions that subsequently reopen paths.
    """
    path = lexical_path(path)
    root = lexical_path(root)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes explicit run root") from exc
    current = root
    parts = relative.parts
    for index in range(len(parts) + 1):
        if index:
            current = current / parts[index - 1]
        try:
            stamp = current.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(stamp.st_mode):
            raise ValueError(f"symlink component refused: {current}")
        if index != len(parts) and not stat.S_ISDIR(stamp.st_mode):
            raise ValueError(f"non-directory ancestor: {current}")
    return stamp


def bounded_file(path: Path, root: Path, limit: int) -> os.stat_result:
    stamp = checked_stat(path, root)
    if stamp is None or not stat.S_ISREG(stamp.st_mode):
        raise ValueError(f"not a regular non-symlink file: {path}")
    if stamp.st_size > limit:
        raise ValueError(f"file exceeds inspection limit ({limit} bytes): {path}")
    return stamp


def read_bounded(path: Path, root: Path, limit: int) -> bytes:
    bounded_file(path, root, limit)
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("file grew beyond inspection byte limit")
    return payload


def put_section(result: dict, key: str, value: object) -> bool:
    """Admit a bounded section before attaching it to the response."""
    current = len(encode_inspection(result))
    old = len(encode_inspection(result.get(key)))
    new = len(encode_inspection(value))
    if current - old + new + 1 > MAX_RESPONSE_BYTES - RESPONSE_RESERVE_BYTES:
        return False
    result[key] = value
    return True


def finalize(result: dict) -> dict:
    """Verify the shared API/CLI byte contract after bounded construction."""
    if len(encode_inspection(result)) + 1 <= MAX_RESPONSE_BYTES:
        return result
    # Last-resort structured failure, never a silently truncated scientific row
    # or configuration. Normal paths admit bounded sections incrementally.
    return {"schema_version": result["schema_version"], "status": "error",
            "issues": [{"section": "response", "code": "response_budget_exceeded",
                        "detail": "inspection omitted because its bounded envelope exceeds the response budget"}]}
