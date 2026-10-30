"""Reusable table cells for product workbenches."""

from __future__ import annotations


def ratio_cell(numerator: object, denominator: object, *, classes: str = ""):
    """Render a ratio with its separator fixed on the column centerline."""
    from nicegui import ui

    cell = ui.element("span").classes(f"data-column-ratio {classes}".strip())
    with cell:
        ui.label(str(numerator)).classes("data-column-ratio-leading")
        ui.label("/").classes("data-column-ratio-separator")
        ui.label(str(denominator)).classes("data-column-ratio-trailing")
    return cell


def readiness_cell(
    numerator: object,
    denominator: object,
    *,
    available: bool,
    unavailable_label: str,
    classes: str = "",
):
    """Render a ratio or a centered unavailable marker in the same stable cell."""
    if available:
        return ratio_cell(numerator, denominator, classes=classes)

    from nicegui import ui

    cell = ui.label("-").classes(f"{classes} text-center w-full".strip())
    cell.tooltip(unavailable_label)
    return cell
