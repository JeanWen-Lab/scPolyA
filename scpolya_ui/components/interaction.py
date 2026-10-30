"""Small interaction helpers shared by selectable product rows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def prop_text(value: Any) -> str:
    """Quote text for a NiceGUI props string; HTML escaping would reach screen readers verbatim."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def make_keyboard_selectable(element, on_activate: Callable[..., Any], *, label: str):
    """Give a clickable row button semantics and Enter/Space activation."""
    element.classes("product-keyboard-selectable")
    element.props(f'role="button" tabindex="0" aria-label={prop_text(label)}')
    element.on("click", on_activate)
    element.on(
        "keydown",
        on_activate,
        js_handler="""(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            window.scpolyaRememberFocus && window.scpolyaRememberFocus(event.currentTarget);
            emit();
          }
        }""",
    )
    return element
