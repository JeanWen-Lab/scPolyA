from __future__ import annotations

from html import escape

from scpolya_ui.components.status import localized_params, render_status
from scpolya_ui.components.terminal import render_terminal
from scpolya_ui.services.environment_service import (
    job_python_executable,
    pipeline_tool_checks,
    reference_input_checks,
)
from scpolya_ui.services.diagnostics_service import product_preflight
from scpolya_ui.services.preferences_service import load_preferences, update_preferences
from scpolya_ui.services.reference_service import reference_status
from scpolya_ui.services.run_service import load_run_summary
from scpolya_ui.services.workspace_service import default_workspace, get_workspace
from scpolya_ui.state.app_state import ProductAppState
from scpolya_ui.state.persistence import load_product_registries


PAGE_KEY = "settings"


def render_settings_page(state: ProductAppState) -> None:
    from nicegui import ui

    registries = state.registries
    if registries is None:
        registries = load_product_registries()
        state.registries = registries

    first_workspace = default_workspace(registries.workspaces)
    page_state = {
        "registries": registries,
        "workspace_id": state.selected_workspace_id or (first_workspace.id if first_workspace else None),
    }

    def current_workspace():
        return get_workspace(page_state["registries"].workspaces, page_state["workspace_id"])

    def reload_registries() -> None:
        page_state["registries"] = load_product_registries()
        state.registries = page_state["registries"]
        settings_content.refresh()

    @ui.refreshable
    def settings_content() -> None:
        workspace = current_workspace()
        with ui.element("div").classes("env-page w-full"):
            with ui.row().classes("ov-header w-full"):
                with ui.column().classes("ov-heading gap-0"):
                    ui.label(state.t("nav.reference_settings")).classes("ov-title")
                    ui.label(state.t("page.subtitle.settings")).classes("env-subtitle")
                with ui.row().classes("ov-actions"):
                    ui.button(
                        state.t("settings.recheck"),
                        icon="refresh",
                        on_click=reload_registries,
                    ).props("outline no-caps").classes("ov-action")

            if workspace is None:
                with ui.column().classes("env-empty gap-1"):
                    ui.label(state.t("workspace.empty")).classes("runs-empty-title")
                    ui.label(state.t("workspace.empty_detail")).classes("ov-muted-small")
                _render_environment_only()
                _render_agent_card()
                return

            summary = load_run_summary(workspace.db_path)
            config = summary.config or {}
            status = reference_status(config, workspace.root_path)
            diagnostics = product_preflight(page_state["registries"], workspace=workspace)
            issues = sorted(
                (item for item in diagnostics if item.level != "ok"),
                key=lambda item: {"error": 0, "warning": 1}.get(item.level, 2),
            )
            healthy = [item for item in diagnostics if item.level == "ok"]
            tools = pipeline_tool_checks()
            inputs = reference_input_checks(config, workspace.path_mappings)

            with ui.row().classes("ov-chips w-full"):
                ref_key = "settings.chip.reference_ready" if status.status == "ready" else "settings.chip.reference_missing"
                _chip(state.t(ref_key, genome=status.genome or "-"), tone="" if status.status == "ready" else "warn")
                found = sum(1 for tool in tools if tool.found)
                _chip(
                    state.t("settings.chip.tools", found=found, total=len(tools)),
                    tone="" if found == len(tools) else "warn",
                )
                missing_inputs = sum(1 for item in inputs if not item.exists)
                if missing_inputs:
                    _chip(state.t("settings.chip.inputs_missing", count=missing_inputs), tone="warn")
                _chip(
                    state.t("settings.chip.checks", count=len(diagnostics), issues=len(issues)),
                    tone="warn" if issues else "",
                )

            with ui.element("div").classes("env-grid"):
                with ui.column().classes("ov-col gap-0"):
                    _render_inputs_card(inputs)
                    _render_reference_card(status)
                    _render_agent_card()
                with ui.column().classes("ov-col gap-0"):
                    _render_checks_card(issues, healthy)
                    _render_tools_card(tools)
            _render_inventory_card()

    def _render_environment_only() -> None:
        with ui.element("div").classes("env-grid"):
            with ui.column().classes("ov-col gap-0"):
                _render_tools_card(pipeline_tool_checks())

    def _chip(text: str, *, tone: str = "") -> None:
        ui.label(text).classes(f"ov-chip ov-chip-{tone}" if tone else "ov-chip")

    def _card(title: str, *, aside: str = "", status: str = ""):
        card = ui.element("section").classes("ov-card w-full")
        with card:
            with ui.row().classes("ov-card-head w-full"):
                ui.label(title).classes("ov-card-title")
                if aside:
                    ui.label(aside).classes("ov-card-count")
                ui.element("div").classes("ov-spacer")
                if status:
                    render_status(state.t, status)
        return card

    def _field_label(key: str) -> str:
        catalog_key = f"config.field.{key}.label"
        label = state.t(catalog_key)
        return key if label == f"[[{catalog_key}]]" else label

    def _render_inputs_card(inputs) -> None:
        all_found = bool(inputs) and all(item.exists for item in inputs)
        card = _card(
            state.t("settings.inputs_title"),
            status=("ready" if all_found else "missing") if inputs else "",
        )
        with card:
            with ui.column().classes("ov-card-body gap-0 w-full"):
                if not inputs:
                    ui.label(state.t("settings.inputs_empty")).classes("ov-muted-small")
                for item in inputs:
                    with ui.row().classes("env-file w-full"):
                        tone = "good" if item.exists else "crit"
                        with ui.element("span").classes(f"sp-status sp-status-{tone} env-file-dot"):
                            ui.element("i")
                        with ui.column().classes("env-file-text gap-0"):
                            with ui.row().classes("env-file-name"):
                                ui.label(_field_label(item.key))
                                ui.label(item.key).classes("env-key")
                                if item.mapped:
                                    ui.label(state.t("settings.mapped")).classes("env-tag")
                            path_label = ui.label(item.resolved).classes("ov-mono env-file-path")
                            path_label.tooltip(
                                item.resolved
                                if not item.mapped
                                else state.t("settings.mapped_from", path=item.configured)
                            )
                            if not item.exists:
                                ui.label(state.t("settings.file_not_found")).classes("env-file-missing")
                        _copy_button(item.resolved)
                ui.label(state.t("settings.inputs_detail")).classes("env-note")

    def _render_reference_card(status) -> None:
        card = _card(
            state.t("settings.igv_reference_title"),
            status="ready" if status.status == "ready" else "missing",
        )
        with card:
            with ui.column().classes("ov-card-body gap-0 w-full"):
                _detail_row(state.t("settings.metric.genome"), status.genome or "-")
                _detail_row(state.t("settings.metric.source"), status.source or "-")
                if status.status == "ready":
                    _detail_row(state.t("settings.fasta"), status.fasta_path, mono=True, copy=True)
                    _detail_row(state.t("settings.fai"), status.index_path, mono=True, copy=True)
                else:
                    _detail_row(state.t("settings.expected_cache"), status.expected_cache_path, mono=True, copy=True)
                _detail_row(state.t("settings.metric.cache"), status.cache_dir, mono=True, copy=True)
                ui.label(state.t("settings.igv_reference_detail")).classes("env-note")

    def _render_checks_card(issues, healthy) -> None:
        aside = state.t("settings.checks_aside", count=len(issues) + len(healthy), issues=len(issues))
        card = _card(state.t("settings.checks_title"), aside=aside)
        with card:
            with ui.column().classes("ov-card-body gap-2 w-full"):
                for item in issues:
                    tone = "crit" if item.level == "error" else "warn"
                    with ui.column().classes(f"ov-alert ov-alert-{tone} gap-1 w-full"):
                        ui.label(state.t(item.message_key, **localized_params(state.t, item.params))).classes("ov-alert-detail")
                        if item.detail:
                            with ui.expansion(state.t("common.technical_details"), value=False).classes(
                                "env-detail-expansion w-full"
                            ).props("dense"):
                                ui.html(
                                    f"<pre class='reference-diagnostic-detail'>{escape(item.detail)}</pre>"
                                )
                with ui.column().classes("env-healthy gap-0 w-full"):
                    for item in healthy:
                        with ui.row().classes("env-healthy-row w-full"):
                            with ui.element("span").classes("sp-status sp-status-good env-file-dot"):
                                ui.element("i")
                            message = ui.label(state.t(item.message_key, **localized_params(state.t, item.params))).classes("env-healthy-text")
                            message.tooltip(state.t(item.message_key, **localized_params(state.t, item.params)))

    def _render_tools_card(tools) -> None:
        found = sum(1 for tool in tools if tool.found)
        card = _card(
            state.t("settings.tools_title"),
            aside=state.t("settings.tools_aside", found=found, total=len(tools)),
        )
        with card:
            with ui.column().classes("ov-card-body gap-0 w-full"):
                _detail_row(state.t("settings.python"), job_python_executable(), mono=True, copy=True)
                for tool in tools:
                    with ui.row().classes("env-tool w-full"):
                        tone = "good" if tool.found else "crit"
                        with ui.element("span").classes(f"sp-status sp-status-{tone} env-file-dot"):
                            ui.element("i")
                        ui.label(tool.name).classes("env-tool-name")
                        if tool.found:
                            ui.label(tool.path).classes("ov-mono env-tool-path")
                        else:
                            ui.label(state.t("settings.tool_missing")).classes("env-file-missing")
                ui.label(state.t("settings.tools_detail")).classes("env-note")

    def set_agent_mode(value: bool) -> None:
        update_preferences(agent_mode=bool(value))
        ui.navigate.to(state.page_href(PAGE_KEY))

    def _render_agent_card() -> None:
        enabled = load_preferences().agent_mode
        card = _card(state.t("settings.agent.title"), aside=state.t("onboarding.agent.experimental"))
        with card:
            with ui.column().classes("ov-card-body gap-2 w-full"):
                ui.switch(
                    state.t("settings.agent.switch"),
                    value=enabled,
                    on_change=lambda e: set_agent_mode(e.value),
                ).classes("env-agent-switch")
                ui.label(state.t("settings.agent.detail")).classes("env-note")
                if enabled:
                    ui.label(state.t("settings.agent.cli")).classes("df-code-label")
                    render_terminal(
                        (
                            "scpolya-operator annotate list --workspace RESULTS_DIR\n"
                            "scpolya-operator annotate add --workspace RESULTS_DIR \\\n"
                            "  --anchor-type run --anchor RUN_RECORD_ID --type question \\\n"
                            "  --body '...' --option '...' --evidence FILE\n"
                            "scpolya-operator annotate reply --workspace RESULTS_DIR \\\n"
                            "  --thread ID --body '...'\n"
                            "scpolya-operator annotate wait --workspace RESULTS_DIR \\\n"
                            "  --thread ID --timeout 300"
                        ),
                        translate=state.t,
                    )

    def _render_inventory_card() -> None:
        workspaces = page_state["registries"].workspaces.workspaces
        missing = 0
        rows = []
        for item in workspaces:
            item_summary = load_run_summary(item.db_path)
            item_status = reference_status(item_summary.config or {}, item.root_path)
            missing += item_status.status == "missing"
            rows.append((item, item_status))
        card = _card(
            state.t("settings.all_references"),
            aside=state.t("settings.reference_inventory_summary", total=len(rows), missing=missing),
        )
        with card:
            with ui.element("div").classes("ov-table env-ref-table w-full"):
                with ui.element("div").classes("ov-tr ov-th"):
                    for key in ("workspace", "genome", "state", "path"):
                        ui.label(state.t(f"settings.col.{key}")).classes("ov-td")
                for item, item_status in rows:
                    with ui.element("div").classes(
                        "ov-tr" + (" ov-tr-active" if item.id == page_state["workspace_id"] else "")
                    ):
                        ui.label(item.alias or item.root_path).classes("ov-td ov-run-name")
                        ui.label(item_status.genome or "-").classes("ov-td")
                        with ui.element("div").classes("ov-td"):
                            render_status(state.t, "ready" if item_status.status == "ready" else "missing")
                        ui.label(item_status.fasta_path or item_status.expected_cache_path).classes(
                            "ov-td ov-mono"
                        )

    def _detail_row(label: str, value: str, *, mono: bool = False, copy: bool = False) -> None:
        with ui.row().classes("ov-detail w-full"):
            ui.label(label).classes("ov-detail-key")
            value_label = ui.label(value or "-").classes("ov-detail-value" + (" ov-mono" if mono else ""))
            value_label.tooltip(value or "-")
            if copy and value:
                _copy_button(value)

    def _copy_button(value: str) -> None:
        button = ui.button(
            icon="o_content_copy",
            on_click=lambda _, text=value: (
                ui.clipboard.write(text),
                ui.notify(state.t("common.copied"), color="positive"),
            ),
        ).props(f'flat dense size=sm aria-label="{state.t("common.copy")}"').classes("ov-copy")
        button.tooltip(state.t("common.copy"))

    settings_content()
