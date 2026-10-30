"""Terminal-style blocks: soft-wrapped, height-bounded, scrolled on their own, copyable."""

from __future__ import annotations

import shlex
from html import escape
from typing import Callable


def format_command(command: str) -> str:
    """One ``--flag value`` per line with ``\\`` continuations; copy text is unchanged."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) < 4:
        return command
    lines: list[list[str]] = [[]]
    for part in parts:
        if part.startswith("-") and lines[-1]:
            lines.append([])
        lines[-1].append(shlex.quote(part))
    return " \\\n  ".join(" ".join(line) for line in lines)


def render_terminal(
    text: str,
    *,
    translate: Callable[..., str],
    copy_text: str | None = None,
    command: bool = False,
    max_height: str = "320px",
    classes: str = "",
) -> None:
    from nicegui import ui

    shown = format_command(text) if command else text
    value = text if copy_text is None else copy_text
    with ui.element("div").classes(f"sp-terminal {classes}".strip()):
        ui.html(f"<pre class='sp-terminal-text' style='max-height:{max_height}'>{escape(shown)}</pre>").classes(
            "sp-terminal-host"
        )
        button = ui.button(
            icon="o_content_copy",
            on_click=lambda: (ui.clipboard.write(value), ui.notify(translate("common.copied"), color="positive")),
        ).props(f'flat dense round size=sm aria-label="{translate("common.copy")}"').classes("sp-terminal-copy")
        button.tooltip(translate("common.copy"))
