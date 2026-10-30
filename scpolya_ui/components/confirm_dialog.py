"""Reusable confirmation dialog for consequential product actions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


def open_confirmation_dialog(
    *,
    title: str,
    message: str,
    confirm_label: str,
    cancel_label: str,
    on_confirm: Callable[[], Any],
    details: Sequence[tuple[str, object]] = (),
    icon: str = "warning",
    danger: bool = False,
    on_cancel: Callable[[], Any] | None = None,
):
    """Open a modal confirmation dialog and run ``on_confirm`` once accepted."""
    from nicegui import ui

    with ui.dialog() as dialog:
        with ui.card().classes("product-confirm-dialog"):
            with ui.row().classes("product-confirm-heading w-full"):
                ui.icon(icon).classes("product-confirm-icon")
                ui.label(title).classes("product-confirm-title")
            ui.label(message).classes("product-confirm-message")
            if details:
                with ui.element("div").classes("product-confirm-details w-full"):
                    for label, value in details:
                        with ui.element("div").classes("product-confirm-detail-row"):
                            ui.label(label).classes("product-confirm-detail-label")
                            display_value = "-" if value is None or value == "" else str(value)
                            ui.label(display_value).classes("product-confirm-detail-value")

            state = {"confirmed": False}

            def confirm() -> None:
                state["confirmed"] = True
                dialog.close()
                on_confirm()

            with ui.row().classes("product-confirm-actions w-full"):
                ui.button(cancel_label, icon="close", on_click=dialog.close).props("flat no-caps")
                color = "red" if danger else "teal"
                ui.button(confirm_label, icon="check", on_click=confirm).props(
                    f"unelevated no-caps color={color}"
                )
    if on_cancel is not None:
        # Escape, a click outside and the cancel button all end here.
        dialog.on("hide", lambda: None if state["confirmed"] else on_cancel())
    dialog.open()
    return dialog
