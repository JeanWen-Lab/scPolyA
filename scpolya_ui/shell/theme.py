"""Product theme compositor.

Visual ownership lives in ``shell.styles``; browser behavior lives in
``shell.runtime``. Keeping this module small makes the shell entrypoint stable
without recreating a monolithic stylesheet.
"""

from __future__ import annotations

from .runtime import PRODUCT_RUNTIME_HEAD
from .styles import PRODUCT_CSS


PRODUCT_THEME_VERSION = "warm-ink-v1"


def apply_product_theme() -> None:
    from nicegui import ui

    ui.colors(primary="#1d1c19", secondary="#55524a", accent="#2a63d4", positive="#0a7a0a", negative="#b02a2a", warning="#8a5a00", info="#2a63d4")
    ui.add_head_html(PRODUCT_CSS)
    ui.add_head_html(PRODUCT_RUNTIME_HEAD)
