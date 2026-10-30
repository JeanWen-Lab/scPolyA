from __future__ import annotations

from scpolya_ui.components.confirm_dialog import open_confirmation_dialog
from scpolya_ui.errors import error_text
from scpolya_ui.components.interaction import make_keyboard_selectable
from scpolya_ui.components.status import artifact_label, render_status, status_text
from scpolya_ui.components.timefmt import local_timestamp, short_timestamp
from scpolya_ui.components.workspace_path_picker import WorkspacePathPicker
from scpolya_ui.services.artifact_service import load_artifact_index, refresh_artifact_index
from scpolya_ui.services.job_service import INTERRUPTED_RUN_STATUSES
from scpolya_ui.services.run_service import logical_run_summaries, refresh_run_record_statuses
from scpolya_ui.services.workspace_service import (
    add_workspace_mapping,
    remove_workspace_mapping,
    default_workspace,
    get_workspace,
    register_workspace,
    select_workspace,
    moved_run_roots,
    untracked_outputs,
    workspace_mapping_summaries,
    workspace_summaries,
)
from scpolya_ui.state.app_state import ProductAppState
from scpolya_ui.state.persistence import load_product_registries


PAGE_KEY = "workspaces"
ACTIVE_RUN_STATUSES = frozenset({"starting", "running", "stopping", "queued"})
ATTENTION_RUN_STATUSES = INTERRUPTED_RUN_STATUSES | {"unknown", "missing", "error"}
RECENT_RUN_LIMIT = 6
ATTENTION_LIMIT = 4


def render_workspaces_page(state: ProductAppState) -> None:
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

    page_state = {
        "registries": registries,
        "selected_workspace_id": state.selected_workspace_id
        or (default_workspace(registries.workspaces).id if default_workspace(registries.workspaces) else None),
    }
    refs: dict[str, object] = {}

    def _compact_input(label: str):
        with ui.element("div").classes("compact-field-row w-full"):
            field_label = ui.label(label).classes("compact-field-label")
            field_label.tooltip(label)
            return ui.input().classes("compact-field-control w-full").props(
                f'dense outlined hide-bottom-space aria-label="{label}"'
            )

    def current_workspace():
        return get_workspace(page_state["registries"].workspaces, page_state["selected_workspace_id"])

    def current_summary():
        summaries = workspace_summaries(
            page_state["registries"].workspaces,
            run_records=page_state["registries"].run_records,
            studies=page_state["registries"].studies,
            jobs=page_state["registries"].jobs,
        )
        selected_id = page_state["selected_workspace_id"]
        return next((item for item in summaries if item.id == selected_id), summaries[0] if summaries else None)

    def reload_registries() -> None:
        page_state["registries"] = load_product_registries()
        state.registries = page_state["registries"]
        if current_workspace() is None:
            page_state["selected_workspace_id"] = (
                default_workspace(page_state["registries"].workspaces).id
                if default_workspace(page_state["registries"].workspaces)
                else None
            )
        workspace_content.refresh()

    def select_workspace_row(workspace_id: str) -> None:
        workspace = select_workspace(page_state["registries"].workspaces, workspace_id)
        if workspace is None:
            ui.notify(state.t("workspace.not_found"), color="warning")
            return
        page_state["selected_workspace_id"] = workspace.id
        state.selected_workspace_id = workspace.id
        ui.navigate.to(state.page_href(PAGE_KEY, workspace_id=workspace.id))

    def add_workspace_from_form() -> None:
        workspace_path = refs.get("workspace_path")
        workspace_alias = refs.get("workspace_alias")
        workspace_notes = refs.get("workspace_notes")
        path_value = str(getattr(workspace_path, "value", "") or "").strip()
        if not path_value:
            ui.notify(state.t("workspace.path_required"), color="warning")
            return
        try:
            workspace = register_workspace(
                page_state["registries"].workspaces,
                path=path_value,
                alias=str(getattr(workspace_alias, "value", "") or "").strip() or None,
                notes=str(getattr(workspace_notes, "value", "") or "").strip(),
            )
        except Exception as exc:
            ui.notify(error_text(state.t, exc), color="negative")
            return
        page_state["selected_workspace_id"] = workspace.id
        state.selected_workspace_id = workspace.id
        if workspace_path is not None:
            workspace_path.value = ""
        if workspace_alias is not None:
            workspace_alias.value = ""
        if workspace_notes is not None:
            workspace_notes.value = ""
        dialog = refs.get("add_workspace_dialog")
        if dialog is not None:
            dialog.close()
        ui.notify(state.t("workspace.add_success"), color="positive")
        ui.navigate.to(state.page_href(PAGE_KEY, workspace_id=workspace.id))

    def select_workspace_path(path: str) -> None:
        workspace_path = refs.get("workspace_path")
        if workspace_path is not None:
            workspace_path.value = path

    path_picker = WorkspacePathPicker(
        translate=state.t,
        on_select=select_workspace_path,
    )

    def remove_mapping(old_path: str, new_path: str | None = None, ignored: bool | None = None) -> None:
        workspace = current_workspace()
        if workspace is None:
            return

        def remove() -> None:
            remove_workspace_mapping(
                page_state["registries"].workspaces,
                workspace.id,
                old_path=old_path,
                new_path=new_path,
                ignored=ignored,
            )
            ui.notify(state.t("workspace.mapping_removed"), color="positive")
            mapping_content.refresh()
            reload_registries()

        open_confirmation_dialog(
            title=state.t("workspace.mapping.remove_title"),
            message=state.t("workspace.mapping.remove_detail"),
            confirm_label=state.t("workspace.mapping.remove"),
            cancel_label=state.t("common.cancel"),
            on_confirm=remove,
            details=((state.t("workspace.mapping.old"), old_path),),
            icon="o_link_off",
            danger=True,
        )

    def add_mapping_from_form() -> None:
        workspace = current_workspace()
        if workspace is None:
            ui.notify(state.t("workspace.select_first"), color="warning")
            return
        mapping_old = refs.get("mapping_old")
        mapping_new = refs.get("mapping_new")
        mapping_note = refs.get("mapping_note")
        old_path = str(getattr(mapping_old, "value", "") or "").strip()
        new_path = str(getattr(mapping_new, "value", "") or "").strip()
        if not old_path or not new_path:
            ui.notify(state.t("workspace.mapping_required"), color="warning")
            return
        try:
            add_workspace_mapping(
                page_state["registries"].workspaces,
                workspace.id,
                old_path=old_path,
                new_path=new_path,
                note=str(getattr(mapping_note, "value", "") or "").strip(),
            )
        except Exception as exc:
            ui.notify(error_text(state.t, exc), color="negative")
            return
        if mapping_old is not None:
            mapping_old.value = ""
        if mapping_new is not None:
            mapping_new.value = ""
        if mapping_note is not None:
            mapping_note.value = ""
        ui.notify(state.t("workspace.mapping_saved"), color="positive")
        mapping_content.refresh()
        # Reloading re-reads invocation history through the new mapping.
        reload_registries()

    with ui.dialog() as add_workspace_dialog:
        with ui.card().classes("product-confirm-dialog workspace-add-dialog"):
            with ui.row().classes("product-confirm-heading w-full"):
                ui.icon("create_new_folder").classes("product-confirm-icon")
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label(state.t("workspace.add_workspace")).classes("product-confirm-title")
                    ui.label(state.t("workspace.empty_detail")).classes("product-confirm-message")
            with ui.element("div").classes("compact-field-row workspace-path-field w-full"):
                path_label = ui.label(state.t("workspace.path_short")).classes("compact-field-label")
                path_label.tooltip(state.t("workspace.path"))
                with ui.row().classes("workspace-path-control w-full"):
                    refs["workspace_path"] = ui.input().classes(
                        "compact-field-control workspace-path-input w-full"
                    ).props(
                        f'dense outlined hide-bottom-space aria-label="{state.t("workspace.path")}"'
                    )
                    ui.button(
                        state.t("workspace.browse"),
                        icon="folder_open",
                        on_click=lambda: path_picker.open(
                            str(getattr(refs.get("workspace_path"), "value", "") or "")
                        ),
                    ).props("outline no-caps")
            refs["workspace_alias"] = _compact_input(state.t("workspace.alias"))
            refs["workspace_notes"] = _compact_input(state.t("workspace.notes"))
            with ui.row().classes("product-confirm-actions w-full"):
                ui.button(state.t("common.cancel"), on_click=add_workspace_dialog.close).props("flat no-caps")
                ui.button(
                    state.t("common.add"),
                    icon="add",
                    on_click=add_workspace_from_form,
                ).props("unelevated no-caps color=primary")
    refs["add_workspace_dialog"] = add_workspace_dialog


    @ui.refreshable
    def mapping_content() -> None:
        workspace = current_workspace()
        if workspace is None:
            ui.label(state.t("workspace.select_first")).classes("product-small-muted")
            return
        rows = workspace_mapping_summaries(workspace)
        if rows:
            with ui.column().classes("ov-mapping-list gap-0 w-full"):
                for item in rows:
                    with ui.column().classes("ov-mapping-row gap-0 w-full"):
                        with ui.row().classes("ov-mapping-paths w-full"):
                            ui.label(item.old_path).classes("ov-mono")
                            ui.icon("arrow_forward").classes("ov-mapping-arrow")
                            ui.label(item.new_path).classes("ov-mono")
                            ui.element("div").classes("ov-spacer")
                            remove = ui.button(
                                icon="o_delete",
                                # The row's own list (applied or ignored) identifies it, so removing an
                                # ignored copy never takes an identical applied mapping with it.
                                on_click=lambda _, old=item.old_path, new=item.new_path, ignored=bool(
                                    item.problem
                                ): remove_mapping(old, new, ignored),
                            ).props(f'flat dense size=sm aria-label="{state.t("workspace.mapping.remove")}"')
                            remove.tooltip(state.t("workspace.mapping.remove"))
                        if item.problem:
                            ui.label(
                                state.t("workspace.mapping.ignored", reason=state.t(f"workspace.mapping_problem.{item.problem}"))
                            ).classes("ov-mapping-ignored")
                        if item.note:
                            ui.label(item.note).classes("ov-muted-small")
        else:
            ui.label(state.t("workspace.mapping.empty")).classes("ov-muted-small ov-mapping-empty")
        with ui.element("div").classes("ov-mapping-form w-full"):
            refs["mapping_old"] = _compact_input(state.t("workspace.mapping.old"))
            refs["mapping_new"] = _compact_input(state.t("workspace.mapping.new"))
            refs["mapping_note"] = _compact_input(state.t("workspace.mapping.note"))

    with ui.dialog() as mapping_dialog:
        with ui.card().classes("product-confirm-dialog ov-mapping-dialog"):
            with ui.row().classes("product-confirm-heading w-full"):
                ui.icon("link").classes("product-confirm-icon")
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label(state.t("workspace.path_mappings")).classes("product-confirm-title")
                    ui.label(state.t("help.term.path_mapping.detail")).classes("product-confirm-message")
            mapping_content()
            with ui.row().classes("product-confirm-actions w-full"):
                ui.button(state.t("common.close"), on_click=mapping_dialog.close).props("flat no-caps")
                ui.button(
                    state.t("common.save"),
                    icon="link",
                    on_click=add_mapping_from_form,
                ).props("unelevated no-caps color=primary")

    def open_mapping_dialog() -> None:
        mapping_content.refresh()
        mapping_dialog.open()

    def open_run(record_id: str) -> None:
        state.payload["selected_run_record_id"] = record_id
        ui.navigate.to(state.page_href("runs", extra_query={"run_record": record_id}))

    def open_help() -> None:
        ui.run_javascript("window.scpolyaToggleHelp && window.scpolyaToggleHelp(true)")

    def go(page: str):
        return lambda: ui.navigate.to(state.page_href(page))

    def run_started_at(record) -> str:
        job_id = str(getattr(record, "job_id", "") or "")
        if job_id:
            job = next((item for item in page_state["registries"].jobs.jobs if item.id == job_id), None)
            if job is not None and job.started_at:
                return job.started_at
        return str(getattr(record, "created_at", "") or "")

    def kind_label(kind: str) -> str:
        key = f"runs.kind.{kind}"
        label = state.t(key)
        return kind if label == f"[[{key}]]" else label

    @ui.refreshable
    def workspace_content() -> None:
        summaries = workspace_summaries(
            page_state["registries"].workspaces,
            run_records=page_state["registries"].run_records,
            studies=page_state["registries"].studies,
            jobs=page_state["registries"].jobs,
        )
        workspace = current_workspace()
        summary = current_summary()

        with ui.element("div").classes("ov-page w-full"):
            if workspace is None or summary is None:
                _render_welcome_without_workspace()
                return

            logical_runs = logical_run_summaries(
                page_state["registries"].run_records,
                workspace_id=workspace.id,
                limit=max(1, len(page_state["registries"].run_records.records)),
            )
            studies = [
                study
                for study in page_state["registries"].studies.studies
                if study.workspace_id == workspace.id
            ]
            attention = [run for run in logical_runs if str(run.status).casefold() in ATTENTION_RUN_STATUSES]
            db_lost = bool(logical_runs) and not summary.db_exists
            ignored_mappings = len(workspace.ignored_path_mappings)
            attention_count = len(attention) + (1 if db_lost else 0) + (1 if ignored_mappings else 0)
            active_count = sum(1 for run in logical_runs if str(run.status).casefold() in ACTIVE_RUN_STATUSES)
            succeeded = [run for run in logical_runs if str(run.status).casefold() == "succeeded"]

            _render_header(summary, logical_runs, studies, succeeded, active_count, attention_count)

            with ui.element("div").classes("ov-grid"):
                with ui.column().classes("ov-col gap-0"):
                    moved_roots = moved_run_roots(workspace)
                    for moved in moved_roots:
                        _render_moved_root(workspace, moved)
                    existing = None if logical_runs else untracked_outputs(workspace)
                    if logical_runs:
                        _render_recent_runs(logical_runs)
                    elif existing is not None:
                        _render_untracked_outputs(existing)
                    elif not moved_roots:
                        _render_first_steps()
                    _render_workspace_table(summaries)
                with ui.column().classes("ov-col gap-0"):
                    _render_attention(summary, attention, db_lost=db_lost, ignored_mappings=ignored_mappings)
                    _render_get_started(succeeded, studies)
                    _render_details(workspace, summary)
                    if logical_runs:
                        _render_artifact_index(workspace)

    def _render_header(summary, logical_runs, studies, succeeded, active_count, attention_count) -> None:
        with ui.row().classes("ov-header w-full"):
            with ui.column().classes("ov-heading gap-0"):
                ui.label(state.t("overview.eyebrow")).classes("ov-eyebrow")
                with ui.row().classes("ov-title-row"):
                    ui.label(summary.label).classes("ov-title")
                ui.label(summary.root_path).classes("ov-path")
            with ui.row().classes("ov-actions"):
                ui.button(
                    state.t("nav.igv_compare"),
                    icon="o_ssid_chart",
                    on_click=go("igv_compare"),
                ).props("outline no-caps").classes("ov-action")
                ui.button(
                    state.t("runs.new_run"),
                    icon="add",
                    on_click=go("run_setup"),
                ).props("unelevated no-caps color=primary")
        with ui.row().classes("ov-chips w-full"):
            _chip(state.t("overview.chip.runs", count=len(logical_runs)))
            _chip(state.t("overview.chip.studies", count=len(studies)))
            if active_count:
                _chip(state.t("overview.chip.active", count=active_count), tone="run")
            if succeeded:
                last = max(succeeded, key=lambda run: run.latest.finished_at or run.latest.updated_at or "")
                finished = last.latest.finished_at or last.latest.updated_at
                text = state.t("overview.chip.last_success", run=last.label, time=short_timestamp(finished))
                _chip(text)
            if attention_count:
                _chip(state.t("overview.chip.attention", count=attention_count), tone="warn")

    def _chip(text: str, *, tone: str = "") -> None:
        ui.label(text).classes(f"ov-chip ov-chip-{tone}" if tone else "ov-chip")

    def _card(title: str, *, count: str = ""):
        card = ui.element("section").classes("ov-card w-full")
        with card:
            with ui.row().classes("ov-card-head w-full") as head:
                ui.label(title).classes("ov-card-title")
                if count:
                    ui.label(count).classes("ov-card-count")
        return card, head

    def _render_recent_runs(logical_runs) -> None:
        card, head = _card(state.t("overview.recent_runs"))
        with head:
            ui.element("div").classes("ov-spacer")
            ui.button(state.t("overview.all_runs"), on_click=go("runs")).props("flat dense no-caps").classes(
                "ov-link-button"
            )
        with card:
            with ui.element("div").classes("ov-table ov-runs-table w-full"):
                with ui.element("div").classes("ov-tr ov-th"):
                    for key in ("run", "status", "kind", "artifacts", "started"):
                        ui.label(state.t(f"overview.col.{key}")).classes(f"ov-td ov-td-{key}")
                for run in logical_runs[:RECENT_RUN_LIMIT]:
                    row = ui.element("div").classes("ov-tr ov-tr-link")
                    make_keyboard_selectable(
                        row,
                        lambda _, record_id=run.latest_record_id: open_run(record_id),
                        label=run.label,
                    )
                    with row:
                        with ui.row().classes("ov-td ov-td-run"):
                            ui.label(run.label).classes("ov-run-name")
                            if run.attempt_count > 1:
                                ui.label(state.t("runs.attempt_count", count=run.attempt_count)).classes(
                                    "ov-run-attempts"
                                )
                        with ui.element("div").classes("ov-td ov-td-status"):
                            render_status(state.t, run.status)
                        ui.label(kind_label(run.latest.kind)).classes("ov-td ov-td-kind")
                        latest = run.latest
                        artifacts = (
                            f"{latest.ready_artifacts}/{latest.total_artifacts}"
                            if latest.artifact_readiness_available
                            else "—"
                        )
                        ui.label(artifacts).classes("ov-td ov-td-artifacts")
                        ui.label(short_timestamp(run_started_at(latest))).classes("ov-td ov-td-started")
            hidden = len(logical_runs) - RECENT_RUN_LIMIT
            if hidden > 0:
                ui.button(
                    state.t("overview.more_runs", count=hidden),
                    on_click=go("runs"),
                ).props("flat dense no-caps").classes("ov-more")

    def _render_attention(summary, attention, *, db_lost: bool, ignored_mappings: int = 0) -> None:
        card, _ = _card(state.t("overview.attention"))
        with card:
            with ui.column().classes("ov-card-body gap-2 w-full"):
                if db_lost:
                    with ui.row().classes("ov-alert ov-alert-crit w-full"):
                        with ui.column().classes("gap-1 min-w-0"):
                            ui.label(state.t("overview.attention.db_missing")).classes("ov-alert-title")
                            ui.label(
                                state.t("overview.attention.db_missing_detail", path=summary.db_path)
                            ).classes("ov-alert-detail")
                            ui.button(
                                state.t("workspace.path_mappings"),
                                on_click=open_mapping_dialog,
                            ).props("flat dense no-caps").classes("ov-alert-link")
                if ignored_mappings:
                    with ui.row().classes("ov-alert ov-alert-warn w-full"):
                        with ui.column().classes("gap-1 min-w-0"):
                            ui.label(
                                state.t("workspace.mapping.ignored_title", count=ignored_mappings)
                            ).classes("ov-alert-title")
                            ui.label(state.t("workspace.mapping.ignored_detail")).classes("ov-alert-detail")
                            ui.button(
                                state.t("workspace.path_mappings"),
                                on_click=open_mapping_dialog,
                            ).props("flat dense no-caps").classes("ov-alert-link")
                for run in attention[:ATTENTION_LIMIT]:
                    with ui.row().classes("ov-alert ov-alert-warn w-full"):
                        with ui.column().classes("gap-1 min-w-0"):
                            ui.label(
                                state.t(
                                    "overview.attention.run",
                                    run=run.label,
                                    status=status_text(state.t, run.status),
                                )
                            ).classes("ov-alert-title")
                            detail = " · ".join(
                                part
                                for part in (
                                    kind_label(run.latest.kind),
                                    local_timestamp(run.latest.finished_at or run_started_at(run.latest)),
                                )
                                if part and part != "-"
                            )
                            ui.label(detail).classes("ov-alert-detail")
                            ui.button(
                                state.t("overview.attention.open_run"),
                                on_click=lambda _, record_id=run.latest_record_id: open_run(record_id),
                            ).props("flat dense no-caps").classes("ov-alert-link")
                hidden = len(attention) - ATTENTION_LIMIT
                if hidden > 0:
                    ui.button(
                        state.t("overview.attention.more", count=hidden),
                        on_click=go("runs"),
                    ).props("flat dense no-caps").classes("ov-more")
                if not db_lost and not attention and not ignored_mappings:
                    with ui.row().classes("ov-quiet w-full"):
                        render_status(state.t, "ready", classes="ov-quiet-dot")
                        ui.label(state.t("overview.attention_empty"))

    def _render_get_started(succeeded, studies) -> None:
        steps = (
            ("workspace", True, None),
            ("first_run", bool(succeeded), "run_setup"),
            ("study", bool(studies), "parameter_studies"),
        )
        done = sum(1 for _, complete, _ in steps if complete)
        if done == len(steps):
            return
        card, _ = _card(
            state.t("overview.get_started"),
            count=state.t("overview.get_started_progress", done=done, total=len(steps)),
        )
        with card:
            with ui.column().classes("ov-card-body ov-steps gap-0 w-full"):
                for key, complete, page in steps:
                    with ui.row().classes("ov-step w-full" + (" ov-step-done" if complete else "")):
                        ui.icon("check_circle" if complete else "radio_button_unchecked").classes("ov-step-icon")
                        ui.label(state.t(f"overview.step.{key}")).classes("ov-step-label")
                        if not complete and page:
                            ui.button(state.t("overview.step.go"), on_click=go(page)).props(
                                "flat dense no-caps"
                            ).classes("ov-step-go")

    def _render_artifact_index(workspace) -> None:
        """Workspace-wide final-output check across all recorded runs; scanned on request."""
        index = page_state.get("artifact_index")
        if index is None:
            stored = load_artifact_index()
            index = stored if stored.workspace_id == workspace.id and stored.total_count else None

        def scan() -> None:
            try:
                page_state["artifact_index"] = refresh_artifact_index(
                    page_state["registries"].run_records, workspace_id=workspace.id
                )
            except Exception as exc:
                ui.notify(error_text(state.t, exc), color="negative")
                return
            workspace_content.refresh()

        card, head = _card(state.t("overview.artifact_index"))
        with head:
            ui.element("div").classes("ov-spacer")
            ui.button(state.t("artifacts.refresh_index"), icon="refresh", on_click=scan).props(
                "flat dense no-caps"
            ).classes("ov-link-button")
        with card:
            with ui.column().classes("ov-card-body gap-1 w-full"):
                if index is None:
                    ui.label(state.t("overview.artifact_index_empty")).classes("ov-muted-small")
                    return
                ui.label(
                    state.t(
                        "artifacts.index_summary",
                        ready=index.ready_count,
                        missing=index.missing_count,
                        total=index.total_count,
                    )
                ).classes("ov-welcome-text")
                if index.unavailable_count:
                    ui.label(state.t("artifacts.index_unavailable", count=index.unavailable_count)).classes(
                        "ov-muted-small"
                    )
                missing = [entry for entry in index.entries if entry.status == "missing"]
                for entry in missing[:8]:
                    with ui.row().classes("ov-index-missing w-full"):
                        ui.label(artifact_label(state.t, entry.label)).classes("ov-index-label")
                        ui.label("\u200e" + entry.path).classes("ov-mono ov-index-path").tooltip(entry.path)
                if len(missing) > 8:
                    ui.label(state.t("overview.artifact_index_more", count=len(missing) - 8)).classes(
                        "ov-muted-small"
                    )

    def _render_details(workspace, summary) -> None:
        card, head = _card(state.t("overview.details"))
        with head:
            ui.element("div").classes("ov-spacer")
            ui.button(
                state.t("overview.mappings_button", count=summary.mapping_count),
                icon="link",
                on_click=open_mapping_dialog,
            ).props("flat dense no-caps").classes("ov-link-button")
        with card:
            with ui.column().classes("ov-card-body gap-0 w-full"):
                _detail_row(state.t("overview.results_dir"), summary.root_path, mono=True, copy=True)
                _detail_row(state.t("workspace.state_db"), summary.db_path, mono=True, copy=True)
                _detail_row(state.t("workspace.created"), local_timestamp(getattr(workspace, "created_at", "")))
                _detail_row(state.t("workspace.last_opened"), local_timestamp(summary.last_opened_at))
                _detail_row(state.t("workspace.notes"), workspace.notes or state.t("workspace.notes_empty"))

    def _render_workspace_table(summaries) -> None:
        card, head = _card(state.t("overview.workspaces"), count=str(len(summaries)))
        with head:
            ui.element("div").classes("ov-spacer")
            refresh = ui.button(icon="refresh", on_click=reload_registries).props(
                f'flat dense aria-label="{state.t("common.refresh")}"'
            ).classes("ov-icon-button")
            refresh.tooltip(state.t("common.refresh"))
            ui.button(
                state.t("workspace.add_workspace"),
                icon="add",
                on_click=add_workspace_dialog.open,
            ).props("flat dense no-caps").classes("ov-link-button")
        with card:
            with ui.element("div").classes("ov-table ov-ws-table w-full"):
                with ui.element("div").classes("ov-tr ov-th"):
                    for key in ("workspace", "runs", "studies", "state", "last_opened"):
                        ui.label(state.t(f"overview.col.{key}")).classes(f"ov-td ov-td-{key}")
                for item in summaries:
                    active = item.id == page_state["selected_workspace_id"]
                    row = ui.element("div").classes("ov-tr ov-tr-link" + (" ov-tr-active" if active else ""))
                    make_keyboard_selectable(
                        row,
                        lambda _, workspace_id=item.id: select_workspace_row(workspace_id),
                        label=item.label,
                    )
                    with row:
                        with ui.column().classes("ov-td ov-td-workspace gap-0"):
                            ui.label(item.label).classes("ov-run-name")
                            ui.label(item.root_path).classes("ov-mono ov-ws-path")
                        ui.label(str(item.run_count)).classes("ov-td ov-td-runs")
                        ui.label(str(item.study_count)).classes("ov-td ov-td-studies")
                        with ui.element("div").classes("ov-td ov-td-state"):
                            _db_status(item.db_exists, has_runs=item.run_count > 0)
                        ui.label(short_timestamp(item.last_opened_at or item.updated_at)).classes(
                            "ov-td ov-td-last_opened"
                        )

    def _db_status(exists: bool, *, has_runs: bool) -> None:
        if exists:
            tone, key = "good", "overview.db_ready"
        elif has_runs:
            tone, key = "crit", "overview.db_missing"
        else:
            tone, key = "idle", "overview.db_not_created"
        with ui.element("span").classes(f"sp-status sp-status-{tone}"):
            ui.element("i")
            ui.label(state.t(key))

    def _render_first_steps() -> None:
        card, _ = _card(state.t("overview.welcome.title"))
        with card:
            with ui.column().classes("ov-card-body gap-3 w-full"):
                ui.label(state.t("overview.welcome.no_runs")).classes("ov-welcome-text")
                _steps_grid(
                    (
                        ("references", go("settings"), False),
                        ("terms", open_help, False),
                        ("first_run", go("run_setup"), True),
                    )
                )

    def _render_moved_root(workspace, moved) -> None:
        def accept() -> None:
            try:
                add_workspace_mapping(
                    page_state["registries"].workspaces,
                    workspace.id,
                    old_path=moved.old_prefix,
                    new_path=moved.new_prefix,
                    note=state.t("overview.moved.note"),
                )
            except Exception as exc:
                ui.notify(error_text(state.t, exc), color="negative")
                return
            ui.notify(state.t("workspace.mapping_saved"), color="positive")
            reload_registries()

        card, _ = _card(state.t("overview.moved.title", count=moved.invocation_count))
        with card:
            with ui.column().classes("ov-card-body gap-2 w-full"):
                ui.label(state.t("overview.moved.detail", count=moved.invocation_count)).classes("ov-welcome-text")
                with ui.column().classes("ov-moved-map gap-0 w-full"):
                    ui.label(state.t("overview.moved.recorded")).classes("ov-muted-small")
                    ui.label(moved.old_prefix).classes("ov-mono ov-moved-path").tooltip(moved.recorded_root)
                    ui.label(state.t("overview.moved.now")).classes("ov-muted-small")
                    ui.label(moved.new_prefix).classes("ov-mono ov-moved-path")
                ui.label(state.t("overview.moved.safe")).classes("ov-muted-small")
                with ui.row().classes("ov-untracked-actions w-full"):
                    ui.button(state.t("overview.moved.accept"), icon="o_link", on_click=accept).props(
                        "unelevated no-caps color=primary"
                    )
                    ui.button(state.t("workspace.path_mappings"), on_click=open_mapping_dialog).props(
                        "flat no-caps"
                    ).classes("ov-link-button")

    def _render_untracked_outputs(existing) -> None:
        card, _ = _card(state.t("overview.untracked.title"))
        with card:
            with ui.column().classes("ov-card-body gap-3 w-full"):
                found = [state.t("overview.untracked.logs", count=existing.log_files)] if existing.log_files else []
                if existing.has_state_db:
                    found.append(state.t("overview.untracked.state_db"))
                found.extend(existing.output_dirs)
                ui.label(state.t("overview.untracked.found", items=" · ".join(found))).classes("ov-welcome-text")
                ui.label(state.t("overview.untracked.detail")).classes("ov-muted-small")
                with ui.row().classes("ov-untracked-actions w-full"):
                    ui.button(state.t("overview.untracked.register"), icon="o_create_new_folder", on_click=add_workspace_dialog.open).props(
                        "outline no-caps"
                    )
                    ui.button(state.t("overview.untracked.new_run"), icon="add", on_click=go("run_setup")).props(
                        "flat no-caps"
                    ).classes("ov-link-button")

    def _render_welcome_without_workspace() -> None:
        with ui.column().classes("ov-welcome gap-0 w-full"):
            ui.label(state.t("overview.welcome.title")).classes("ov-title")
            ui.label(state.t("overview.welcome.no_workspace")).classes("ov-welcome-text")
            _steps_grid(
                (
                    ("register", add_workspace_dialog.open, True),
                    ("references", go("settings"), False),
                    ("terms", open_help, False),
                )
            )

    def _steps_grid(steps) -> None:
        with ui.element("div").classes("ov-step-cards w-full"):
            for index, (key, action, primary) in enumerate(steps, start=1):
                with ui.column().classes("ov-step-card gap-0"):
                    ui.label(str(index)).classes("ov-step-number")
                    ui.label(state.t(f"overview.welcome.{key}.title")).classes("ov-step-title")
                    ui.label(state.t(f"overview.welcome.{key}.detail")).classes("ov-step-detail")
                    ui.button(state.t(f"overview.welcome.{key}.action"), on_click=action).props(
                        "unelevated no-caps color=primary" if primary else "outline no-caps"
                    ).classes("ov-step-action")

    def _detail_row(label: str, value: str, *, mono: bool = False, copy: bool = False) -> None:
        with ui.row().classes("ov-detail w-full"):
            ui.label(label).classes("ov-detail-key")
            ui.label(value or "-").classes("ov-detail-value" + (" ov-mono" if mono else ""))
            if copy and value:
                button = ui.button(
                    icon="o_content_copy",
                    on_click=lambda _, text=value: _copy(text),
                ).props(f'flat dense size=sm aria-label="{state.t("common.copy")}"').classes("ov-copy")
                button.tooltip(state.t("common.copy"))

    def _copy(text: str) -> None:
        ui.clipboard.write(text)
        ui.notify(state.t("common.copied"), color="positive")

    workspace_content()
