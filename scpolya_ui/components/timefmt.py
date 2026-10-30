"""Timestamp display helpers shared by product pages."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_timestamp(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def local_timestamp(value: str, *, seconds: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    try:
        parsed = parse_timestamp(raw)
    except ValueError:
        return raw.replace("T", " ")
    pattern = "%Y-%m-%d %H:%M:%S %Z" if seconds else "%Y-%m-%d %H:%M %Z"
    return parsed.astimezone().strftime(pattern)


def short_timestamp(value: str) -> str:
    """Month-day and time in local time, for dense tables."""
    try:
        parsed = parse_timestamp(value)
    except ValueError:
        return str(value or "-").replace("T", " ")
    if parsed is None:
        return "-"
    local = parsed.astimezone()
    if local.year != datetime.now().astimezone().year:
        return local.strftime("%Y-%m-%d")
    return local.strftime("%m-%d %H:%M")
