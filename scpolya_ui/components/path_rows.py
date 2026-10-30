from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def render_path_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    name_key: str,
    path_key: str,
    status_key: str | None = None,
    extra_keys: tuple[str, ...] = (),
    empty_label: str | None = None,
    copy_label: str | None = None,
    copied_label: str | None = None,
) -> None:
    from nicegui import ui

    items = list(rows)
    if not items:
        if empty_label:
            ui.label(empty_label).classes("product-small-muted")
        return

    with ui.element("div").classes("path-row-list w-full"):
        for row in items:
            name = _display(row.get(name_key))
            path = _display(row.get(path_key))
            status = _display(row.get(status_key)) if status_key else ""
            extras = [_display(row.get(key)) for key in extra_keys if _display(row.get(key))]
            with ui.element("div").classes("path-row"):
                ui.label(name).classes("path-row-name")
                meta_parts = [part for part in (status, *extras) if part]
                if meta_parts:
                    meta = " · ".join(meta_parts)
                    ui.label(meta).classes(f"path-row-status {_status_class(status)}").tooltip(meta)
                else:
                    ui.element("span").classes("path-row-status path-row-status-empty")
                ui.label(path).classes("path-row-path")
                if copy_label and path:
                    copy_button = ui.button(
                        icon="content_copy",
                        on_click=lambda value=path: _copy_path(value, copied_label),
                    ).classes("path-row-copy").props("flat round dense")
                    copy_button.tooltip(copy_label)
                else:
                    ui.element("span").classes("path-row-copy-placeholder")


def _copy_path(path: str, copied_label: str | None) -> None:
    from nicegui import ui

    ui.clipboard.write(path)
    if copied_label:
        ui.notify(copied_label, color="positive")


def _display(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if text == "None" else text


def _status_class(status: str) -> str:
    normalized = status.strip().lower().replace("_", "-")
    if normalized in {"ready", "succeeded", "registered"}:
        return "path-row-status-ready"
    if normalized in {"missing", "failed", "stopped", "exited"}:
        return "path-row-status-missing"
    return ""
