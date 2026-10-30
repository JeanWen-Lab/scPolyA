from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from scpolya_ui.components.config_panel import render_config_inspector
from scpolya_ui.components.interaction import make_keyboard_selectable
from scpolya_ui.components.log_browser import render_run_log_browser
from scpolya_ui.registry import apply_path_mappings_to_value
from scpolya_ui.components.status import artifact_label, render_status, status_text
from scpolya_ui.components.terminal import render_terminal
from scpolya_ui.components.tables import readiness_cell
from scpolya_ui.i18n.catalog import load_catalog
from scpolya_ui.run_data import PIPELINE_STAGE_LOG_ORDER, StageStatus
from scpolya_ui.run_records import resolve_execution_config
from scpolya_ui.services.job_service import INTERRUPTED_RUN_STATUSES
from scpolya_ui.services.process_service import managed_process_inventory
from scpolya_ui.services.run_service import (
    get_run_record,
    load_run_summary,
    logical_run_summaries,
    refresh_run_record_statuses,
)
from scpolya_ui.services.workspace_service import default_workspace, get_workspace
from scpolya_ui.state.app_state import ProductAppState
from scpolya_ui.state.persistence import load_product_registries

PAGE_KEY = "runs"
ACTIVE_RUN_STATUSES = frozenset({"starting", "running", "stopping"})
GENOME_SUFFIXES = frozenset({".bed", ".bb", ".bigbed", ".gtf", ".bw", ".bigwig"})


def _catalog_keys() -> frozenset[str]:
    return frozenset(load_catalog("en"))


def render_runs_page(state: ProductAppState) -> None:
    from nicegui import ui

    registries = state.registries
    if registries is None:
        registries = load_product_registries()
        state.registries = registries
    refresh_run_record_statuses(
        registries.run_records,
        registries.jobs,
        workspaces=registries.workspaces,
    )

    first_workspace = default_workspace(registries.workspaces)
    page_state = {
        "registries": registries,
        "workspace_id": state.selected_workspace_id or (first_workspace.id if first_workspace else None),
        "logical_run_id": None,
        "run_record_id": state.payload.get("selected_run_record_id"),
        "query": "",
        "status_filter": "all",
        "detail_tab": state.payload.get("runs_detail_tab") or "overview",
        "artifact_filter": "all",
        "invocation_key": "",
        "log_path": "",
        "log_auto_refresh": True,
    }

    def mapped_path(value: str) -> str:
        """Recorded paths shown where they are now, through the workspace's path mappings."""
        workspace = current_workspace()
        if not value or workspace is None or not workspace.path_mappings:
            return value
        try:
            return apply_path_mappings_to_value(value, workspace.path_mappings)
        except ValueError:
            return value

    def current_workspace():
        return get_workspace(page_state["registries"].workspaces, page_state["workspace_id"])

    def current_logical_runs():
        return logical_run_summaries(
            page_state["registries"].run_records,
            workspace_id=page_state["workspace_id"],
            limit=50,
        )

    def job_for_record(record):
        if record is None or not record.job_id:
            return None
        return next(
            (
                job
                for job in page_state["registries"].jobs.jobs
                if job.id == record.job_id
            ),
            None,
        )

    def filtered_logical_runs(logical_runs):
        query = str(page_state["query"] or "").strip().casefold()
        status_filter = page_state["status_filter"]
        return [
            logical_run
            for logical_run in logical_runs
            if (
                status_filter == "all"
                or logical_run.status == status_filter
            )
            and (
                not query
                or query in (logical_run.label or "").casefold()
                or query in (logical_run.run_id or "").casefold()
                or query in (logical_run.config_hash or "").casefold()
                or query in (logical_run.latest.note or "").casefold()
                or any(
                    query in " ".join(
                        (
                            attempt.id,
                            attempt.kind,
                            attempt.root_path,
                            attempt.job_id,
                            attempt.invocation_id,
                            attempt.config_hash,
                            attempt.config_source,
                        )
                    ).casefold()
                    for attempt in logical_run.attempts
                )
            )
        ]

    def ensure_selected_record() -> None:
        logical_runs = current_logical_runs()
        selected_run = next(
            (
                logical_run
                for logical_run in logical_runs
                if any(attempt.id == page_state["run_record_id"] for attempt in logical_run.attempts)
            ),
            None,
        )
        if selected_run is None:
            selected_run = next(
                (logical_run for logical_run in logical_runs if logical_run.id == page_state["logical_run_id"]),
                logical_runs[0] if logical_runs else None,
            )
        page_state["logical_run_id"] = selected_run.id if selected_run is not None else None
        if selected_run is None or not any(
            attempt.id == page_state["run_record_id"] for attempt in selected_run.attempts
        ):
            page_state["run_record_id"] = selected_run.latest_record_id if selected_run is not None else None
        state.payload["selected_run_record_id"] = page_state["run_record_id"]

    def reload_registries() -> None:
        page_state["registries"] = load_product_registries()
        state.registries = page_state["registries"]
        refresh_run_record_statuses(
            page_state["registries"].run_records,
            page_state["registries"].jobs,
            workspaces=page_state["registries"].workspaces,
        )
        if current_workspace() is None:
            fallback = default_workspace(page_state["registries"].workspaces)
            page_state["workspace_id"] = fallback.id if fallback else None
            state.selected_workspace_id = page_state["workspace_id"]
        ensure_selected_record()
        runs_content.refresh()

    def refresh_statuses() -> None:
        changed = refresh_run_record_statuses(
            page_state["registries"].run_records,
            page_state["registries"].jobs,
            workspaces=page_state["registries"].workspaces,
        )
        ui.notify(state.t("runs.status_refreshed" if changed else "runs.status_current"), color="positive")
        runs_content.refresh()

    def poll_active_statuses() -> None:
        has_active_job = any(
            job.status in ACTIVE_RUN_STATUSES
            and (page_state["workspace_id"] is None or job.workspace_id == page_state["workspace_id"])
            for job in page_state["registries"].jobs.jobs
        )
        if not has_active_job:
            return
        changed = refresh_run_record_statuses(
            page_state["registries"].run_records,
            page_state["registries"].jobs,
        )
        if changed:
            runs_content.refresh()
            return
        selected = get_run_record(
            page_state["registries"].run_records,
            page_state["run_record_id"],
        )
        if (
            selected is not None
            and selected.status in ACTIVE_RUN_STATUSES
        ):
            if page_state["detail_tab"] == "overview":
                process_panel_content.refresh()
            elif (
                page_state["detail_tab"] == "logs"
                and page_state["log_auto_refresh"]
            ):
                log_panel_content.refresh()

    def select_logical_run(logical_run_id: str) -> None:
        logical_run = next(
            (item for item in current_logical_runs() if item.id == logical_run_id),
            None,
        )
        if logical_run is None:
            return
        page_state["logical_run_id"] = logical_run.id
        page_state["run_record_id"] = logical_run.latest_record_id
        page_state["invocation_key"] = ""
        page_state["log_path"] = ""
        state.payload["selected_run_record_id"] = logical_run.latest_record_id
        # Only the detail is rebuilt; the list keeps its rows so a quick second click is not lost.
        for row_id, row in (page_state.get("row_elements") or {}).items():
            if row_id == logical_run.id:
                row.classes(add="runs-list-row-active")
            else:
                row.classes(remove="runs-list-row-active")
        sync_url()
        detail_view.refresh()

    def sync_url() -> None:
        """Keep the selection in the address bar so reload, Back and links return here."""
        tab = page_state["detail_tab"]
        href = state.page_href(
            "runs",
            extra_query={"run_record": page_state["run_record_id"], "tab": "" if tab == "overview" else tab},
        )
        ui.run_javascript(f"history.replaceState(history.state, '', {json.dumps(href)})")

    def select_run_record(record_id: str) -> None:
        page_state["run_record_id"] = record_id
        logical_run = next(
            (
                item
                for item in current_logical_runs()
                if any(attempt.id == record_id for attempt in item.attempts)
            ),
            None,
        )
        if logical_run is not None:
            page_state["logical_run_id"] = logical_run.id
        page_state["invocation_key"] = ""
        page_state["log_path"] = ""
        state.payload["selected_run_record_id"] = record_id
        # Before the refresh: it deletes the clicked element that owns this handler's slot.
        sync_url()
        runs_content.refresh()

    def set_query(value: str | None) -> None:
        # Only the list re-renders, so the search box keeps focus while typing.
        page_state["query"] = str(value or "")
        list_refresh = page_state.get("list_refresh")
        if list_refresh is None:
            runs_content.refresh()
        else:
            list_refresh()

    def set_status_filter(value: str | None) -> None:
        page_state["status_filter"] = str(value or "all")
        runs_content.refresh()

    def set_detail_tab(value: str | None) -> None:
        page_state["detail_tab"] = str(value or "overview")
        state.payload["runs_detail_tab"] = page_state["detail_tab"]
        sync_url()

    def set_log_auto_refresh(value: bool) -> None:
        page_state["log_auto_refresh"] = bool(value)

    def refresh_logs() -> None:
        log_panel_content.refresh()

    def select_log(value: str | None) -> None:
        state.payload["log_tail_scale"] = 1
        page_state["log_path"] = str(value or "")
        page_state["detail_tab"] = "logs"
        runs_content.refresh()

    def select_invocation(value: str | None) -> None:
        page_state["invocation_key"] = str(value or "")
        page_state["log_path"] = ""
        page_state["detail_tab"] = "logs"
        runs_content.refresh()

    @ui.refreshable
    def log_panel_content(selected=None, workspace=None) -> None:
        if selected is not None and workspace is not None:
            _render_logs(selected, workspace)

    @ui.refreshable
    def process_panel_content(job=None) -> None:
        if job is not None:
            _render_process_inventory(job)

    def resolve_selection():
        """Current workspace, runs and selection; fixes a selection the filters hide."""
        workspace = current_workspace()
        all_logical_runs = current_logical_runs()
        valid_statuses = {logical_run.status for logical_run in all_logical_runs}
        if page_state["status_filter"] != "all" and page_state["status_filter"] not in valid_statuses:
            page_state["status_filter"] = "all"
        logical_runs = filtered_logical_runs(all_logical_runs)
        visible_ids = {logical_run.id for logical_run in logical_runs}
        selected_logical_run = next(
            (
                logical_run
                for logical_run in all_logical_runs
                if any(attempt.id == page_state["run_record_id"] for attempt in logical_run.attempts)
            ),
            None,
        )
        if page_state["logical_run_id"] not in visible_ids:
            page_state["logical_run_id"] = (
                selected_logical_run.id
                if selected_logical_run is not None and selected_logical_run.id in visible_ids
                else logical_runs[0].id if logical_runs else None
            )
        selected_logical_run = next(
            (item for item in logical_runs if item.id == page_state["logical_run_id"]),
            None,
        )
        if selected_logical_run is None:
            page_state["run_record_id"] = None
        elif not any(
            attempt.id == page_state["run_record_id"] for attempt in selected_logical_run.attempts
        ):
            page_state["run_record_id"] = selected_logical_run.latest_record_id
        state.payload["selected_run_record_id"] = page_state["run_record_id"]
        selected = get_run_record(page_state["registries"].run_records, page_state["run_record_id"])
        selected_summary = next(
            (
                attempt
                for attempt in (selected_logical_run.attempts if selected_logical_run is not None else ())
                if selected and attempt.id == selected.id
            ),
            None,
        )
        return workspace, all_logical_runs, logical_runs, selected_logical_run, selected, selected_summary

    @ui.refreshable
    def detail_view() -> None:
        workspace, _all, _visible, logical_run, selected, selected_summary = resolve_selection()
        if workspace is not None:
            _render_detail(selected, selected_summary, logical_run, workspace)

    @ui.refreshable
    def runs_content() -> None:
        workspace, all_logical_runs, logical_runs, selected_logical_run, selected, selected_summary = resolve_selection()

        if workspace is None:
            with ui.column().classes("product-panel-muted gap-2"):
                ui.label(state.t("workspace.empty")).classes("product-section-title")
                ui.label(state.t("workspace.empty_detail")).classes("product-small-muted")
            return

        status_counts: dict[str, int] = {}
        for logical_run in all_logical_runs:
            status_counts[logical_run.status] = status_counts.get(logical_run.status, 0) + 1

        if workspace.ignored_path_mappings:
            with ui.row().classes("ov-alert ov-alert-warn runs-mapping-alert w-full"):
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label(
                        state.t("workspace.mapping.ignored_title", count=len(workspace.ignored_path_mappings))
                    ).classes("ov-alert-title")
                    ui.label(state.t("workspace.mapping.ignored_detail")).classes("ov-alert-detail")
                    ui.button(
                        state.t("workspace.mapping.review"),
                        on_click=lambda: ui.navigate.to(state.page_href("workspaces")),
                    ).props("flat dense no-caps").classes("ov-alert-link")
        with ui.element("div").classes("runs-layout"):
            _render_master(logical_runs, all_logical_runs, status_counts)
            detail_view()

    def _render_list_rows(logical_runs, all_logical_runs) -> None:
        page_state["row_elements"] = {}
        with ui.element("div").classes("runs-list-scroll"):
            if not logical_runs:
                with ui.column().classes("runs-list-empty gap-2"):
                    ui.label(
                        state.t("runs.no_matches") if all_logical_runs else state.t("runs.empty")
                    ).classes("runs-empty-title")
                    ui.label(
                        state.t("runs.adjust_filters") if all_logical_runs else state.t("runs.empty_detail")
                    ).classes("product-small-muted")
            for logical_run in logical_runs:
                active = logical_run.id == page_state["logical_run_id"]
                row = ui.element("div").classes("runs-list-row runs-list-row-active" if active else "runs-list-row")
                page_state["row_elements"][logical_run.id] = row
                make_keyboard_selectable(
                    row,
                    lambda _, logical_run_id=logical_run.id: select_logical_run(logical_run_id),
                    label=logical_run.label,
                )
                with row:
                    with ui.row().classes("runs-list-row-top w-full"):
                        ui.label(logical_run.label).classes("runs-list-name")
                        render_status(state.t, logical_run.status, classes="runs-list-status")
                    started_at, _ = _run_timestamp(logical_run.latest)
                    meta = [_kind_label(logical_run.latest.kind), _local_timestamp(started_at)]
                    if logical_run.attempt_count > 1:
                        meta.append(state.t("runs.attempt_count", count=logical_run.attempt_count))
                    ui.label(" · ".join(part for part in meta if part and part != "-")).classes(
                        "runs-list-meta"
                    )

    def _render_master(logical_runs, all_logical_runs, status_counts) -> None:
        with ui.element("section").classes("runs-list"):
            with ui.column().classes("runs-list-head w-full"):
                ui.input(
                    value=page_state["query"],
                    placeholder=state.t("runs.search"),
                    on_change=lambda e: set_query(e.value),
                ).classes("runs-list-search w-full").props(
                    "dense outlined clearable hide-bottom-space debounce=200"
                ).props('prepend-icon=""')
                with ui.row().classes("runs-filter-chips w-full"):
                    _filter_chip("all", state.t("common.all"), len(all_logical_runs))
                    for status in sorted(status_counts):
                        _filter_chip(status, status_text(state.t, status), status_counts[status])
            @ui.refreshable
            def list_rows() -> None:
                _render_list_rows(filtered_logical_runs(all_logical_runs), all_logical_runs)

            page_state["list_refresh"] = list_rows.refresh
            list_rows()
            with ui.row().classes("runs-list-foot w-full"):
                ui.button(
                    state.t("runs.new_run"),
                    icon="add",
                    on_click=lambda: ui.navigate.to(state.page_href("run_setup")),
                ).classes("runs-new-button").props("unelevated no-caps color=primary")
                refresh = ui.button(icon="refresh", on_click=reload_registries).props(
                    f'flat dense aria-label="{state.t("common.refresh")}"'
                ).classes("runs-icon-button")
                refresh.tooltip(state.t("runs.refresh_tooltip"))
                sync = ui.button(icon="sync", on_click=refresh_statuses).props(
                    f'flat dense aria-label="{state.t("runs.refresh_status")}"'
                ).classes("runs-icon-button")
                sync.tooltip(state.t("runs.sync_tooltip"))

    def _filter_chip(value: str, label: str, count: int) -> None:
        active = page_state["status_filter"] == value
        chip = ui.button(
            f"{label} {count}",
            on_click=lambda value=value: set_status_filter(value),
        ).props("flat dense no-caps").classes("runs-chip runs-chip-active" if active else "runs-chip")
        chip.props(f'aria-pressed="{str(active).lower()}"')

    def _render_detail(selected, selected_summary, logical_run, workspace) -> None:
        with ui.element("section").classes("runs-doc"):
            if selected is None or logical_run is None:
                with ui.column().classes("runs-doc-empty gap-2"):
                    ui.icon("o_format_list_bulleted")
                    ui.label(state.t("runs.select_record")).classes("runs-empty-title")
                    ui.label(state.t("runs.select_record_detail")).classes("product-small-muted")
                return

            is_latest = selected.id == logical_run.latest_record_id
            with ui.row().classes("runs-doc-header w-full"):
                with ui.column().classes("runs-doc-heading gap-0"):
                    ui.label(_kind_label(selected.kind)).classes("runs-doc-eyebrow")
                    with ui.row().classes("runs-doc-title-row"):
                        # Attempts of one run root can be different study variants; title the one shown.
                        ui.label(selected.label or logical_run.label).classes("runs-doc-title")
                        render_status(state.t, selected.status, classes="runs-doc-status")
                    with ui.row().classes("runs-doc-chips"):
                        started_at, timestamp_key = _run_timestamp(selected_summary or selected)
                        _chip(f"{state.t(timestamp_key)} {_local_timestamp(started_at)}")
                        duration = _duration(started_at, selected.finished_at)
                        if duration:
                            _chip(state.t("runs.duration", duration=duration))
                        config_hash = (
                            (selected_summary.config_hash if selected_summary is not None else "")
                            or selected.execution_config_hash
                            or selected.config_hash
                        )
                        if config_hash:
                            chip = _chip(f"config {config_hash[:8]}", mono=True)
                            chip.tooltip(config_hash)
                        if logical_run.attempt_count > 1:
                            ui.select(
                                options=_attempt_options(logical_run),
                                value=selected.id,
                                on_change=lambda e: select_run_record(e.value),
                            ).classes("runs-attempt-select").props(
                                "dense borderless options-dense hide-bottom-space"
                            )
                        else:
                            _chip(state.t("runs.attempt_position", current=1, total=1))
                with ui.row().classes("runs-doc-actions"):
                    if selected.status in INTERRUPTED_RUN_STATUSES:
                        continue_button = ui.button(
                            state.t("runs.continue_run"),
                            on_click=lambda: ui.navigate.to(
                                state.page_href(
                                    "run_setup",
                                    extra_query={"base_run": selected.id, "launch_mode": "continue"},
                                )
                            ),
                        ).props("unelevated no-caps color=primary")
                        continue_button.tooltip(state.t("runs.continue_run_detail"))
                    ui.button(
                        state.t("runs.rerun_from"),
                        on_click=lambda: ui.navigate.to(
                            state.page_href(
                                "run_setup",
                                extra_query={"base_run": selected.id, "launch_mode": "rerun"},
                            )
                        ),
                    ).props("outline no-caps").classes("runs-action")
                    ui.button(
                        state.t("runs.open_genome"),
                        on_click=lambda: ui.navigate.to(
                            state.page_href(
                                "igv_compare",
                                extra_query={"igv_source": "single", "run_record": selected.id},
                            )
                        ),
                    ).props("outline no-caps").classes("runs-action")

            if selected.invocation_error:
                with ui.row().classes("runs-callout runs-callout-warn w-full"):
                    ui.icon("o_report")
                    with ui.column().classes("gap-0"):
                        ui.label(state.t("runs.invocation_error")).classes("runs-callout-title")
                        ui.label(selected.invocation_error)

            if is_latest:
                _render_stage_strip(selected)

            with ui.tabs(
                value=page_state["detail_tab"],
                on_change=lambda e: set_detail_tab(e.value),
            ).classes("runs-doc-tabs w-full").props("dense no-caps align=left inline-label") as detail_tabs:
                ui.tab("overview", label=state.t("common.overview"))
                ui.tab("artifacts", label=state.t("common.artifacts"))
                ui.tab("logs", label=state.t("common.logs"))
                ui.tab("configuration", label=state.t("common.configuration"))
                ui.tab(
                    "attempts",
                    label=state.t("runs.attempts_tab", count=logical_run.attempt_count),
                )

            with ui.tab_panels(
                detail_tabs,
                value=page_state["detail_tab"],
                animated=False,
                keep_alive=False,
            ).classes("runs-doc-panels w-full"):
                with ui.tab_panel("overview"):
                    _render_overview(selected, selected_summary, job_for_record(selected))
                with ui.tab_panel("attempts"):
                    _render_attempts(logical_run, selected.id)
                with ui.tab_panel("artifacts"):
                    _render_artifacts(selected, selected_summary)
                with ui.tab_panel("configuration"):
                    _render_configuration(selected)
                with ui.tab_panel("logs"):
                    log_panel_content(selected, workspace)

    def _render_stage_strip(selected) -> None:
        if not selected.db_path:
            return
        summary = load_run_summary(selected.db_path)
        if not summary.exists or not summary.stages:
            return
        stages = {stage.stage: stage for stage in summary.stages}
        optional = {"bulk_references", "rna_gene_matrix"}
        ordered = [name for name in PIPELINE_STAGE_LOG_ORDER if name in stages or name not in optional]
        ordered += [name for name in stages if name not in ordered]
        with ui.element("div").classes("runs-stages w-full").style(
            f"grid-template-columns: repeat({len(ordered)}, minmax(0, 1fr))"
        ):
            for name in ordered:
                stage = stages.get(name) or StageStatus(stage=name)
                known = max(stage.ledger_rows, stage.done_rows)
                if known and stage.done_rows >= known:
                    tone = "good"
                elif stage.done_rows:
                    tone = "part"
                else:
                    tone = "none"
                with ui.column().classes("runs-stage gap-0") as cell:
                    bar = ui.element("div").classes(f"runs-stage-bar runs-stage-{tone}")
                    if tone == "part":
                        percent = round(100 * stage.done_rows / known)
                        bar.style(
                            f"background: linear-gradient(90deg, var(--sp-good-dot) {percent}%, var(--sp-border) {percent}%)"
                        )
                    ui.label(state.t(f"stage.{name}") if f"stage.{name}" in _catalog_keys() else name).classes(
                        "runs-stage-name"
                    )
                    ui.label(f"{stage.done_rows}/{known}" if known else "-").classes("runs-stage-count")
                cell.tooltip(state.t("runs.stage_tooltip", stage=name, done=stage.done_rows, known=known))

    def _render_overview(selected, selected_summary, job) -> None:
        ready = selected_summary.ready_artifacts if selected_summary is not None else 0
        total = selected_summary.total_artifacts if selected_summary is not None else 0
        readiness_available = selected_summary is not None and selected_summary.artifact_readiness_available
        with ui.element("div").classes("runs-overview"):
            with ui.column().classes("runs-overview-main gap-0"):
                with ui.row().classes("runs-section-title w-full"):
                    ui.label(state.t("runs.artifact_readiness"))
                    if readiness_available:
                        ui.label(state.t("artifacts.ready_summary", ready=ready, total=total)).classes(
                            "runs-section-count"
                        )
                if not readiness_available:
                    ui.label(state.t("artifacts.readiness_unavailable")).classes("runs-muted-line")
                    if selected.history_source == "invocation":
                        ui.label(state.t("runs.artifacts_cli_detail")).classes("runs-muted-line")
                    elif selected_summary is not None and selected_summary.artifact_readiness_error:
                        ui.label(selected_summary.artifact_readiness_error).classes("runs-muted-line")
                else:
                    for artifact in selected_summary.artifacts:
                        with ui.row().classes("runs-line w-full"):
                            render_status(state.t, _artifact_status(artifact, selected), classes="runs-line-status")
                            name = artifact_label(state.t, artifact.label)
                            ui.label(name).classes("runs-line-label").tooltip(name)
                if job is not None and job.status in ACTIVE_RUN_STATUSES:
                    process_panel_content(job)
            with ui.column().classes("runs-overview-side gap-0"):
                ui.label(state.t("runs.locations")).classes("runs-section-title")
                _location_row(state.t("runs.output_dir"), mapped_path(selected.output_dir))
                _location_row(state.t("runs.root_path"), mapped_path(selected.root_path))
                _location_row(state.t("workspace.state_db"), mapped_path(selected.db_path))
                _location_row(state.t("runs.job_id"), selected.job_id)
                if selected.invocation_id:
                    _location_row(state.t("runs.invocation_id"), selected.invocation_id)
                ui.label(state.t("runs.run_details")).classes("runs-section-title runs-section-spaced")
                _detail_row(state.t("runs.metric.run_id"), selected.run_id)
                if selected.study_id:
                    study = next(
                        (item for item in page_state["registries"].studies.studies if item.id == selected.study_id),
                        None,
                    )
                    with ui.row().classes("runs-location w-full"):
                        ui.label(state.t("runs.study_variant")).classes("runs-location-key")
                        link = ui.label(
                            f"{study.name if study else selected.study_id} / {selected.variant_id or '-'}"
                        ).classes("runs-detail-value runs-detail-link")
                        link.tooltip(f"{selected.study_id} / {selected.variant_id}")
                        make_keyboard_selectable(
                            link,
                            lambda _: ui.navigate.to(
                                state.page_href("parameter_studies", extra_query={"study": selected.study_id})
                            ),
                            label=state.t("runs.open_study"),
                        )
                _detail_row(state.t("runs.note"), selected.note)
                if selected.source_run_binding:
                    source = selected.source_run_binding
                    _detail_row(state.t("run_setup.base_source"), source.get("run_record_id"))
                    _location_row(state.t("evidence.base_invocation"), source.get("invocation_id"))
                    _location_row(state.t("evidence.base_root"), source.get("root_path"))
                if selected.invocation_id:
                    _detail_row(
                        state.t("runs.config_source"),
                        state.t(f"runs.config_source.{selected.config_source or 'unavailable'}"),
                    )
                    if selected.raw_status and selected.raw_status != selected.status:
                        _detail_row(state.t("runs.recorded_status"), selected.raw_status)
                with ui.expansion(state.t("runs.command"), icon="o_terminal").classes(
                    "runs-command-expansion w-full"
                ).props("dense"):
                    render_terminal(selected.command or "-", translate=state.t, command=True, max_height="360px")

    def _chip(text: str, *, mono: bool = False):
        return ui.label(text).classes("runs-chip-static runs-chip-mono" if mono else "runs-chip-static")

    def _location_row(label: str, value: object) -> None:
        if value is None or value == "":
            return
        with ui.row().classes("runs-location w-full"):
            ui.label(label).classes("runs-location-key")
            path_label = ui.label(str(value)).classes("runs-location-value")
            path_label.tooltip(str(value))
            copy = ui.button(
                icon="o_content_copy",
                on_click=lambda value=str(value): _copy_value(value),
            ).props(f'flat dense round size=sm aria-label="{state.t("common.copy_path")}"').classes(
                "runs-copy"
            )
            copy.tooltip(state.t("common.copy_path"))

    def _detail_row(label: str, value: object) -> None:
        if value is None or value == "" or value == "-":
            return
        with ui.row().classes("runs-location w-full"):
            ui.label(label).classes("runs-location-key")
            ui.label(str(value)).classes("runs-detail-value")

    def _copy_value(value: str) -> None:
        ui.clipboard.write(value)
        ui.notify(state.t("common.path_copied"), color="positive")

    def _kind_label(kind: str) -> str:
        key = f"runs.kind.{kind}"
        label = state.t(key)
        return kind if label == f"[[{key}]]" else label

    def _duration(start: str, end: str) -> str:
        try:
            started = _parse_timestamp(start)
            finished = _parse_timestamp(end)
        except ValueError:
            return ""
        if started is None or finished is None or finished < started:
            return ""
        seconds = int((finished - started).total_seconds())
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {seconds:02d}s"
        return f"{seconds}s"

    def _render_process_inventory(job) -> None:
        inventory = managed_process_inventory(job)
        with ui.element("section").classes("runs-process-panel"):
            with ui.row().classes("runs-process-heading w-full"):
                ui.label(state.t("runs.processes")).classes("product-section-title")
                ui.label(
                    state.t("runs.process_count", count=len(inventory.processes))
                ).classes("product-small-muted")
                ui.element("div").classes("workbench-toolbar-spacer")
                refresh = ui.button(
                    icon="refresh",
                    on_click=process_panel_content.refresh,
                ).classes("runs-process-refresh").props(
                    f'flat dense aria-label="{state.t("runs.refresh_processes")}"'
                )
                refresh.tooltip(state.t("runs.refresh_processes"))
            if inventory.error:
                ui.label(inventory.error).classes(
                    "product-small-muted product-status-missing"
                )
                return
            if not inventory.processes:
                ui.label(state.t("runs.no_live_processes")).classes(
                    "product-small-muted"
                )
                return
            with ui.element("div").classes("runs-process-table"):
                with ui.element("div").classes("runs-process-header"):
                    ui.label(state.t("runs.process_role"))
                    ui.label("PID")
                    ui.label("PPID")
                    ui.label(state.t("common.status"))
                    ui.label(state.t("runs.process_command"))
                for process in inventory.processes:
                    with ui.element("div").classes("runs-process-row"):
                        ui.label(
                            state.t(f"runs.process_role.{process.role}")
                        ).classes("runs-process-role")
                        ui.label(str(process.pid)).classes("runs-process-pid")
                        ui.label(str(process.ppid)).classes("runs-process-pid")
                        ui.label(
                            state.t(
                                f"runs.process_state.{_process_state_key(process.state)}"
                            )
                        ).classes("runs-process-state")
                        command = ui.label(process.command or "-").classes(
                            "runs-process-command"
                        )
                        if process.command:
                            command.tooltip(process.command)

    def _render_attempts(logical_run, selected_record_id: str) -> None:
        with ui.column().classes("runs-tab-content gap-3 w-full"):
            with ui.row().classes("items-center justify-between w-full"):
                ui.label(state.t("runs.attempt_history")).classes("product-section-title")
                ui.label(
                    state.t("runs.attempt_count", count=logical_run.attempt_count)
                ).classes("product-small-muted")
            with ui.element("div").classes("runs-attempt-table w-full"):
                with ui.element("div").classes("runs-attempt-header"):
                    ui.label(state.t("runs.attempt"))
                    ui.label(state.t("common.status"))
                    ui.label(state.t("runs.updated"))
                    ui.label(state.t("common.artifacts")).classes("data-column-ratio-heading")
                for index, attempt in enumerate(logical_run.attempts):
                    number = logical_run.attempt_count - index
                    active = attempt.id == selected_record_id
                    row = ui.element("div").classes(
                        "runs-attempt-row runs-attempt-row-active" if active else "runs-attempt-row"
                    )
                    make_keyboard_selectable(
                        row,
                        lambda _, record_id=attempt.id: select_run_record(record_id),
                        label=state.t("runs.attempt_number", number=number),
                    )
                    with row:
                        with ui.column().classes("runs-attempt-primary gap-0"):
                            ui.label(
                                state.t("runs.attempt_number", number=number)
                            ).classes("runs-attempt-name")
                            # Study variants share one results folder; say which variant each attempt ran.
                            origin = [_kind_label(attempt.kind)]
                            if attempt.variant_id:
                                origin.append(attempt.variant_id)
                            ui.label(" · ".join(part for part in origin if part)).classes("runs-attempt-origin")
                            ui.label(attempt.id).classes("runs-master-id")
                            if attempt.invocation_id:
                                invocation_label = ui.label(
                                    f"{attempt.invocation_id[:8]} · {attempt.config_hash[:8]}"
                                ).classes("runs-master-id")
                                invocation_label.tooltip(
                                    f"{attempt.invocation_id} · {attempt.config_hash}"
                                )
                        render_status(state.t, attempt.status)
                        ui.label(_short_timestamp(attempt.updated_at)).classes(
                            "runs-attempt-updated"
                        )
                        readiness_cell(
                            attempt.ready_artifacts,
                            attempt.total_artifacts,
                            available=attempt.artifact_readiness_available,
                            unavailable_label=state.t("artifacts.readiness_unavailable"),
                            classes="runs-master-cell runs-master-artifacts",
                        )

    def set_artifact_filter(value: str) -> None:
        page_state["artifact_filter"] = value
        runs_content.refresh()

    def _artifact_status(artifact, selected) -> str:
        # An unfinished run never produced its outputs; "missing" is reserved for finished runs.
        if artifact.status == "missing" and str(selected.status or "").casefold() != "succeeded":
            return "not_produced"
        return artifact.status

    def _render_artifacts(selected, selected_summary) -> None:
        with ui.column().classes("runs-tab-content gap-3 w-full"):
            ui.button(state.t("nav.run_evidence"), icon="o_manage_search",
                      on_click=lambda: ui.navigate.to(state.page_href("run_evidence", extra_query={"run_record": selected.id}))
                      ).props("outline no-caps").classes("runs-action")
            if selected_summary is not None and not selected_summary.artifact_readiness_available:
                cli = selected.history_source == "invocation"
                with ui.row().classes("ov-alert ov-alert-info w-full"):
                    with ui.column().classes("gap-1 min-w-0"):
                        ui.label(
                            state.t("runs.artifacts_cli_title" if cli else "artifacts.readiness_unavailable")
                        ).classes("ov-alert-title")
                        ui.label(
                            state.t("runs.artifacts_cli_detail")
                            if cli
                            else selected_summary.artifact_readiness_error or "-"
                        ).classes("ov-alert-detail")
                        if cli:
                            output = mapped_path(selected.output_dir or selected.root_path or "")
                            with ui.row().classes("ov-untracked-actions"):
                                ui.button(
                                    state.t("runs.open_genome"),
                                    icon="o_ssid_chart",
                                    on_click=lambda: ui.navigate.to(
                                        state.page_href(
                                            "igv_compare",
                                            extra_query={"igv_source": "single", "run_record": selected.id},
                                        )
                                    ),
                                ).props("outline dense no-caps")
                                if output:
                                    ui.button(
                                        state.t("runs.copy_output_dir"),
                                        icon="o_content_copy",
                                        on_click=lambda: (
                                            ui.clipboard.write(output),
                                            ui.notify(state.t("common.copied"), color="positive"),
                                        ),
                                    ).props("flat dense no-caps").tooltip(output)
                return
            artifacts = list(selected_summary.artifacts if selected_summary is not None else ())
            if not artifacts:
                ui.label(state.t("runs.no_artifacts")).classes("product-small-muted")
                return
            counts = {"all": len(artifacts)}
            for artifact in artifacts:
                status = _artifact_status(artifact, selected)
                counts[status] = counts.get(status, 0) + 1
            if page_state["artifact_filter"] not in counts:
                page_state["artifact_filter"] = "all"
            with ui.row().classes("runs-art-head w-full"):
                with ui.row().classes("runs-filter-chips"):
                    for value in ("all", "ready", "missing", "not_produced"):
                        if value != "all" and not counts.get(value):
                            continue
                        active = page_state["artifact_filter"] == value
                        label = state.t("common.all") if value == "all" else status_text(state.t, value)
                        chip = ui.button(
                            f"{label} {counts.get(value, 0)}",
                            on_click=lambda value=value: set_artifact_filter(value),
                        ).props("flat dense no-caps").classes("runs-chip runs-chip-active" if active else "runs-chip")
                        chip.props(f'aria-pressed="{str(active).lower()}"')
                ui.element("div").classes("ov-spacer")
                ui.label(
                    state.t("artifacts.ready_summary", ready=counts.get("ready", 0), total=len(artifacts))
                ).classes("ov-muted-small")
            if counts.get("missing") and selected.status in INTERRUPTED_RUN_STATUSES:
                ui.label(state.t("runs.artifacts_missing_interrupted")).classes("env-note")
            elif counts.get("missing") and selected.status in ACTIVE_RUN_STATUSES:
                ui.label(state.t("runs.artifacts_missing_running")).classes("env-note")
            visible = [
                artifact
                for artifact in artifacts
                if page_state["artifact_filter"] == "all"
                or _artifact_status(artifact, selected) == page_state["artifact_filter"]
            ]
            with ui.element("div").classes("ov-table runs-art-table w-full"):
                with ui.element("div").classes("ov-tr ov-th"):
                    for key in ("artifacts.name", "common.status", "runs.artifact_size", "runs.artifact_location", ""):
                        ui.label(state.t(key) if key else "")
                for artifact in visible:
                    path = Path(artifact.path) if artifact.path else None
                    with ui.element("div").classes("ov-tr"):
                        with ui.column().classes("ov-td gap-0 runs-art-name"):
                            ui.label(
                                artifact_label(state.t, artifact.label) if artifact.label else (path.name if path else "-")
                            ).classes("ov-run-name")
                            if path is not None:
                                ui.label("\u200e" + path.name).classes("runs-art-file").tooltip(path.name)
                        with ui.element("div").classes("ov-td"):
                            render_status(state.t, _artifact_status(artifact, selected))
                        ui.label(_format_bytes(artifact.size_bytes) if artifact.exists else "-").classes(
                            "ov-td runs-art-size"
                        )
                        location = ui.label("\u200e" + str(path.parent) if path is not None else "-").classes(
                            "ov-td runs-art-dir"
                        )
                        if path is not None:
                            location.tooltip(str(path))
                        with ui.row().classes("runs-art-actions"):
                            if path is not None and artifact.status == "ready" and artifact.role.startswith("native_table."):
                                ui.button(icon="o_manage_search", on_click=lambda path=path: ui.navigate.to(
                                    state.page_href("run_evidence", extra_query={"run_record": selected.id, "evidence_table": path.name}))
                                          ).props(f'flat dense round size=sm aria-label="{state.t("evidence.preview_download")}"').tooltip(state.t("evidence.preview_download"))
                            if path is not None and artifact.status == "ready" and path.suffix.lower() in GENOME_SUFFIXES:
                                genome = ui.button(
                                    icon="o_ssid_chart",
                                    on_click=lambda: ui.navigate.to(
                                        state.page_href(
                                            "igv_compare",
                                            extra_query={"igv_source": "single", "run_record": selected.id},
                                        )
                                    ),
                                ).props(f'flat dense round size=sm aria-label="{state.t("runs.open_genome")}"').classes(
                                    "runs-copy"
                                )
                                genome.tooltip(state.t("runs.open_genome"))
                            if path is not None:
                                copy = ui.button(
                                    icon="o_content_copy",
                                    on_click=lambda value=str(path): _copy_value(value),
                                ).props(f'flat dense round size=sm aria-label="{state.t("common.copy_path")}"').classes(
                                    "runs-copy"
                                )
                                copy.tooltip(state.t("common.copy_path"))

    def _render_configuration(selected) -> None:
        execution_config = resolve_execution_config(selected)
        with ui.column().classes("runs-tab-content gap-3 w-full"):
            ui.label(state.t("runs.config_snapshot")).classes("product-section-title")
            if selected.invocation_id:
                ui.label(
                    state.t(
                        "runs.config_provenance",
                        source=state.t(
                            f"runs.config_source.{selected.config_source or 'unavailable'}"
                        ),
                    )
                ).classes("product-small-muted")
            if not execution_config.available:
                ui.label(execution_config.error).classes("product-small-muted product-status-missing")
            render_config_inspector(
                execution_config.config if execution_config.available else selected.config,
                missing_label=state.t("config.missing"),
                advanced_label=state.t("config.advanced_only"),
                full_config_label=state.t("runs.full_config"),
                translate=state.t,
            )

    def _render_logs(selected, workspace) -> None:
        log_root = selected.root_path or selected.workspace_root or workspace.root_path
        invocation_key, log_path = render_run_log_browser(
            state,
            run_root=log_root,
            path_mappings=tuple(workspace.path_mappings),
            selected_invocation_key=page_state["invocation_key"],
            selected_log_path=page_state["log_path"],
            on_select_invocation=select_invocation,
            on_select_log=select_log,
            on_refresh=refresh_logs,
            auto_refresh_enabled=page_state["log_auto_refresh"],
            # Finished runs never refresh, so the live switch would be a no-op there.
            on_auto_refresh_change=(
                set_log_auto_refresh if selected.status in ACTIVE_RUN_STATUSES else None
            ),
        )
        page_state["invocation_key"] = invocation_key
        page_state["log_path"] = log_path

    def _study_variant(study_id: str, variant_id: str) -> str:
        if study_id and variant_id:
            return f"{study_id} / {variant_id}"
        return study_id or variant_id or "-"

    def _attempt_number(logical_run, record_id: str) -> int:
        index = next(
            (index for index, attempt in enumerate(logical_run.attempts) if attempt.id == record_id),
            0,
        )
        return logical_run.attempt_count - index

    def _attempt_options(logical_run) -> dict[str, str]:
        labels = {attempt.label for attempt in logical_run.attempts}
        options = {}
        for index, attempt in enumerate(logical_run.attempts):
            text = state.t(
                "runs.attempt_option",
                number=logical_run.attempt_count - index,
                status=status_text(state.t, attempt.status),
                updated=_local_timestamp(_run_timestamp(attempt)[0]),
            )
            if len(labels) > 1 and attempt.label:
                text = f"{text} · {attempt.label}"
            options[attempt.id] = text
        return options

    def _short_timestamp(value: str) -> str:
        return _local_timestamp(value)

    def _run_timestamp(record) -> tuple[str, str]:
        job_id = str(getattr(record, "job_id", "") or "")
        if job_id:
            job = next(
                (
                    item
                    for item in page_state["registries"].jobs.jobs
                    if item.id == job_id
                ),
                None,
            )
            if job is not None and job.started_at:
                return job.started_at, "runs.started"
        return str(getattr(record, "created_at", "") or ""), "runs.created"

    def _local_timestamp(value: str, *, seconds: bool = False) -> str:
        raw = str(value or "").strip()
        if not raw:
            return "-"
        try:
            parsed = datetime.fromisoformat(
                raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            pattern = "%Y-%m-%d %H:%M:%S %Z" if seconds else "%Y-%m-%d %H:%M %Z"
            return parsed.astimezone().strftime(pattern)
        except ValueError:
            return raw.replace("T", " ")

    def _parse_timestamp(value: str):
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    def _process_state_key(value: str) -> str:
        return {
            "R": "running",
            "S": "sleeping",
            "D": "waiting",
            "T": "stopped",
            "t": "tracing",
            "Z": "zombie",
            "I": "idle",
        }.get(str(value or ""), "unknown")

    runs_content()
    ui.timer(2.0, poll_active_statuses)


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
