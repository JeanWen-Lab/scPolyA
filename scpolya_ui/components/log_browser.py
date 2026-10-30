"""Read-only invocation and compatibility log browser."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from html import escape
from typing import Any

from scpolya_ui.components.interaction import make_keyboard_selectable
from scpolya_ui.components.status import status_text
from scpolya_ui.components.terminal import render_terminal
from scpolya_ui.registry import PathMapping, apply_path_mappings_to_value
from scpolya_ui.run_data import (
    InvocationLogSummary,
    LogFileSummary,
    PIPELINE_STAGE_LOG_ORDER,
    discover_run_log_catalog,
    read_log_tail,
)


COMPATIBILITY_SOURCE_KEY = "__compatibility_logs__"
_PIPELINE_STAGE_NAMES = frozenset(PIPELINE_STAGE_LOG_ORDER)


def render_run_log_browser(
    state,
    *,
    run_root: str,
    selected_invocation_key: str,
    selected_log_path: str,
    on_select_invocation: Callable[[str | None], Any],
    on_select_log: Callable[[str | None], Any],
    on_refresh: Callable[[], Any] | None = None,
    auto_refresh_enabled: bool = False,
    on_auto_refresh_change: Callable[[bool], Any] | None = None,
    path_mappings: tuple[PathMapping, ...] = (),
) -> tuple[str, str]:
    from nicegui import ui

    view_root = apply_path_mappings_to_value(run_root, path_mappings)
    catalog = discover_run_log_catalog(view_root, path_mappings=path_mappings) if run_root else None
    invocations = catalog.invocations if catalog is not None else ()
    compatibility_logs = catalog.compatibility_logs if catalog is not None else ()

    source_options = {
        invocation.key: _invocation_option_label(state, invocation)
        for invocation in invocations
    }
    if compatibility_logs:
        source_options[COMPATIBILITY_SOURCE_KEY] = state.t(
            "logs.compatibility_option",
            count=len(compatibility_logs),
        )
    if not source_options:
        with ui.column().classes("run-log-empty gap-2 w-full"):
            ui.icon("subject")
            ui.label(state.t("artifacts.no_logs")).classes("product-small-muted")
        return "", ""

    if selected_invocation_key not in source_options:
        selected_invocation_key = next(iter(source_options))
    selected_invocation = next(
        (item for item in invocations if item.key == selected_invocation_key),
        None,
    )
    selected_logs = (
        selected_invocation.logs
        if selected_invocation is not None
        else compatibility_logs
    )
    log_by_path = {item.path: item for item in selected_logs}
    if selected_log_path not in log_by_path:
        selected_log_path = (
            selected_invocation.run_log.path
            if selected_invocation is not None and selected_invocation.run_log is not None
            else (selected_logs[0].path if selected_logs else "")
        )
    selected_log = log_by_path.get(selected_log_path)

    with ui.column().classes("run-log-browser w-full"):
        with ui.row().classes("run-log-sourcebar w-full"):
            ui.label(state.t("logs.invocation")).classes("run-log-inline-label")
            ui.select(
                options=source_options,
                value=selected_invocation_key,
                on_change=lambda event: on_select_invocation(event.value),
            ).classes("run-log-source-select").props(
                "dense outlined options-dense hide-bottom-space"
            )
            ui.element("div").classes("workbench-toolbar-spacer")
            ui.label(
                state.t("logs.invocation_count", count=len(invocations))
            ).classes("product-small-muted")
            if on_auto_refresh_change is not None:
                live = ui.switch(
                    state.t("logs.live_refresh"),
                    value=auto_refresh_enabled,
                    on_change=lambda event: on_auto_refresh_change(bool(event.value)),
                ).classes("run-log-live-toggle").props("dense color=primary")
                live.tooltip(state.t("logs.live_refresh_detail"))
            if on_refresh is not None:
                refresh = ui.button(icon="refresh", on_click=on_refresh).classes(
                    "run-log-refresh-button"
                ).props(
                    f'flat dense aria-label="{state.t("logs.refresh")}"'
                )
                refresh.tooltip(state.t("logs.refresh"))

        if catalog is not None and catalog.ignored_metadata_count:
            with ui.row().classes("run-log-contract-warning w-full"):
                ui.icon("warning")
                ui.label(
                    state.t(
                        "logs.metadata_ignored",
                        count=catalog.ignored_metadata_count,
                    )
                ).classes("product-small-muted")

        if selected_invocation is not None:
            _render_invocation_summary(state, selected_invocation)
        else:
            with ui.row().classes("run-log-compatibility-summary w-full"):
                ui.icon("history")
                with ui.column().classes("gap-0 min-w-0"):
                    ui.label(state.t("logs.compatibility")).classes("run-log-summary-title")
                    ui.label(
                        state.t("logs.compatibility_detail", count=len(compatibility_logs))
                    ).classes("product-small-muted")

        with ui.element("div").classes("run-log-layout w-full"):
            with ui.element("nav").classes("run-log-tree"):
                if selected_invocation is not None:
                    _render_invocation_log_tree(
                        state,
                        selected_invocation,
                        selected_log_path,
                        on_select_log,
                    )
                else:
                    _render_log_group(
                        state,
                        state.t("logs.compatibility"),
                        "history",
                        compatibility_logs,
                        selected_log_path,
                        on_select_log,
                    )
            with ui.element("section").classes("run-log-viewer"):
                _render_log_viewer(state, selected_log, on_refresh=on_refresh)

    return selected_invocation_key, selected_log_path


def _render_invocation_summary(state, invocation: InvocationLogSummary) -> None:
    from nicegui import ui

    with ui.element("div").classes("run-log-invocation-summary w-full"):
        _summary_item(
            state.t("logs.started"),
            _format_local_timestamp(invocation),
        )
        _summary_item(
            state.t("common.status"),
            _invocation_status(state, invocation.status),
            _status_class(invocation.status),
        )
        _summary_item(
            state.t("logs.duration"),
            _format_duration(state, invocation.duration_seconds),
        )
        _summary_item(state.t("logs.invocation_id"), invocation.invocation_id[:12])
    with ui.expansion(
        state.t("logs.invocation_details"),
        icon="terminal",
    ).classes("run-log-invocation-details w-full").props("dense"):
        with ui.element("div").classes("run-log-detail-grid"):
            _detail_item(state.t("logs.pid"), invocation.pid)
            _detail_item(state.t("logs.config_hash"), invocation.config_hash)
        render_terminal(shlex.join(invocation.argv), translate=state.t, command=True, max_height="240px")

    if invocation.error:
        with ui.row().classes("run-log-invocation-error w-full"):
            ui.icon("error_outline")
            ui.label(_invocation_error(state, invocation.error)).classes("product-small-muted")


def _render_invocation_log_tree(
    state,
    invocation: InvocationLogSummary,
    selected_log_path: str,
    on_select_log: Callable[[str | None], Any],
) -> None:
    from nicegui import ui

    if invocation.run_log is not None:
        _render_log_group(
            state,
            state.t("logs.aggregate"),
            "subject",
            (invocation.run_log,),
            selected_log_path,
            on_select_log,
        )
    pipeline_stage_logs = tuple(
        log for log in invocation.stage_logs if _stage_log_name(log) in _PIPELINE_STAGE_NAMES
    )
    runtime_logs = tuple(
        log for log in invocation.stage_logs if _stage_log_name(log) not in _PIPELINE_STAGE_NAMES
    )
    if pipeline_stage_logs:
        _render_log_group(
            state,
            state.t("logs.stages"),
            "account_tree",
            pipeline_stage_logs,
            selected_log_path,
            on_select_log,
        )
    if runtime_logs:
        _render_log_group(
            state,
            state.t("logs.runtime"),
            "settings_suggest",
            runtime_logs,
            selected_log_path,
            on_select_log,
        )
    if invocation.tool_groups:
        with ui.element("div").classes("run-log-group"):
            _log_group_header(
                state.t("logs.tools"),
                "build",
                sum(len(group.logs) for group in invocation.tool_groups),
            )
            for group in invocation.tool_groups:
                expanded = any(log.path == selected_log_path for log in group.logs)
                with ui.expansion(
                    state.t("logs.tool_group", tool=group.name, count=len(group.logs)),
                    icon="construction",
                    value=expanded,
                ).classes("run-log-tool-group w-full").props("dense"):
                    for log in group.logs:
                        _render_log_row(state, log, selected_log_path, on_select_log)


def _render_log_group(
    state,
    title: str,
    icon: str,
    logs: tuple[LogFileSummary, ...],
    selected_log_path: str,
    on_select_log: Callable[[str | None], Any],
) -> None:
    from nicegui import ui

    if not logs:
        return
    with ui.element("div").classes("run-log-group"):
        _log_group_header(title, icon, len(logs))
        folders: dict[str, list[LogFileSummary]] = {}
        for log in logs:
            folder, _, _ = log.label.rpartition("/")
            folders.setdefault(folder, []).append(log)
        for log in folders.pop("", []):
            _render_log_row(state, log, selected_log_path, on_select_log)
        for folder, items in sorted(folders.items()):
            expanded = any(log.path == selected_log_path for log in items)
            with ui.expansion(
                state.t("logs.tool_group", tool=folder, count=len(items)),
                icon="o_folder",
                value=expanded,
            ).classes("run-log-tool-group w-full").props("dense"):
                for log in items:
                    _render_log_row(state, log, selected_log_path, on_select_log, short=True)


def _log_group_header(title: str, icon: str, count: int) -> None:
    from nicegui import ui

    with ui.element("div").classes("run-log-group-header"):
        ui.icon(icon)
        ui.label(title)
        ui.label(str(count)).classes("run-log-group-count")


def _render_log_row(
    state,
    log: LogFileSummary,
    selected_log_path: str,
    on_select_log: Callable[[str | None], Any],
    *,
    short: bool = False,
) -> None:
    from nicegui import ui

    active = log.path == selected_log_path
    row = ui.element("div").classes(
        "run-log-row run-log-row-active" if active else "run-log-row"
    )
    make_keyboard_selectable(
        row,
        lambda _, path=log.path: on_select_log(path),
        label=log.label,
    )
    with row:
        name = log.label.rpartition("/")[2] if short else log.label
        # The informative end of a long log name survives truncation.
        ui.label("\u200e" + name).classes("run-log-row-name")
        ui.label(_format_size(log.size_bytes)).classes("run-log-row-size")
    row.tooltip(log.label)


def _render_log_viewer(state, log: LogFileSummary | None, *, on_refresh: Callable[[], Any] | None = None) -> None:
    from nicegui import ui

    if log is None:
        with ui.column().classes("run-log-viewer-empty gap-2"):
            ui.icon("subject")
            ui.label(state.t("logs.no_file")).classes("product-small-muted")
        return
    with ui.element("header").classes("run-log-viewer-header"):
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(log.label).classes("run-log-viewer-title")
            ui.label(
                state.t("logs.file_modified", timestamp=log.modified_at)
            ).classes("product-small-muted")
        ui.element("div").classes("workbench-toolbar-spacer")
        ui.label(_format_size(log.size_bytes)).classes("product-small-muted")
        wrap = bool(state.payload.get("log_soft_wrap", True))

        def toggle_wrap(value: bool) -> None:
            state.payload["log_soft_wrap"] = bool(value)
            if on_refresh is not None:
                on_refresh()

        ui.switch(
            state.t("logs.soft_wrap"),
            value=wrap,
            on_change=lambda event: toggle_wrap(bool(event.value)),
        ).props("dense").classes("run-log-wrap-toggle")
        scale = int(state.payload.get("log_tail_scale") or 1)
        content = read_log_tail(
            log.path,
            allowed_root=log.containment_root,
            max_bytes=65536 * scale,
            max_lines=240 * scale,
        )
        copy = ui.button(
            icon="o_content_copy",
            on_click=lambda: (ui.clipboard.write(content), ui.notify(state.t("common.copied"), color="positive")),
        ).props(f'flat dense round size=sm aria-label="{state.t("logs.copy_shown")}"').classes("run-log-copy")
        copy.tooltip(state.t("logs.copy_shown"))
    shown_lines = content.count("\n") + 1 if content else 0
    if log.size_bytes > len(content.encode("utf-8")) + 1:
        with ui.row().classes("run-log-truncated w-full"):
            ui.label(
                state.t("logs.truncated", lines=shown_lines, size=_format_size(log.size_bytes))
            ).classes("run-log-truncated-text")
            if scale < 64:

                def show_more() -> None:
                    state.payload["log_tail_scale"] = scale * 4
                    if on_refresh is not None:
                        on_refresh()

                ui.button(state.t("logs.show_more"), on_click=show_more).props("flat dense no-caps").classes(
                    "run-log-truncated-button"
                )
    ui.html(
        f"<pre class='run-log-console{'' if wrap else ' run-log-console-nowrap'}'>"
        f"{escape(content or state.t('logs.empty_file'))}</pre>"
    ).classes("run-log-console-host")


def _summary_item(label: str, value: str, value_class: str = "") -> None:
    from nicegui import ui

    with ui.element("div").classes("run-log-summary-item"):
        ui.label(label).classes("run-log-summary-label")
        ui.label(value).classes(f"run-log-summary-value {value_class}".strip())


def _detail_item(label: str, value: object) -> None:
    from nicegui import ui

    with ui.element("div").classes("run-log-detail-item"):
        ui.label(label).classes("run-log-summary-label")
        ui.label(str(value)).classes("run-log-detail-value")


_SUPERVISOR_ERROR = re.compile(
    r"^Managed job (?P<job>\S+) ended with status (?P<status>\S+) \(exit_code=(?P<code>[^)]*)\); "
    r"invocation metadata was finalized by the job supervisor\.$"
)


def _invocation_status(state, status: str) -> str:
    # The supervisor records a user stop as "cancelled"; runs elsewhere call it "stopped".
    return status_text(state.t, "stopped" if status == "cancelled" else status)


def _invocation_error(state, error: str) -> str:
    match = _SUPERVISOR_ERROR.match(error)
    if match is None:
        return error
    return state.t(
        "logs.supervisor_error",
        job=match["job"],
        status=status_text(state.t, match["status"]),
        code=match["code"],
    )


def _invocation_option_label(state, invocation: InvocationLogSummary) -> str:
    return state.t(
        "logs.invocation_option",
        started=_format_local_timestamp(invocation),
        status=_invocation_status(state, invocation.status),
        duration=_format_duration(state, invocation.duration_seconds),
        invocation=invocation.invocation_id[:8],
    )


def _format_local_timestamp(invocation: InvocationLogSummary) -> str:
    return invocation.started_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_duration(state, seconds: float | None) -> str:
    if seconds is None:
        return state.t("logs.duration_active")
    if seconds < 60:
        return f"{seconds:.1f} s"
    whole_seconds = int(round(seconds))
    minutes, remainder = divmod(whole_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remainder:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _format_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _stage_log_name(log: LogFileSummary) -> str:
    return log.label.split("/", 1)[0].removesuffix(".log").casefold()


def _status_class(status: str) -> str:
    if status == "succeeded":
        return "run-log-status-success"
    if status == "failed":
        return "run-log-status-danger"
    if status == "running":
        return "run-log-status-info"
    return ""
