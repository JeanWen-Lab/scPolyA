from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from scpolya_ui.components.confirm_dialog import open_confirmation_dialog
from scpolya_ui.errors import error_text
from scpolya_ui.components.interaction import make_keyboard_selectable, prop_text
from scpolya_ui.components.terminal import render_terminal
from scpolya_ui.components.timefmt import local_timestamp, short_timestamp
from scpolya_ui.components.workspace_path_picker import WorkspacePathPicker
from scpolya_ui.services.differential_service import (
    ENVELOPE_PROPERTIES,
    TABLE_PAGE_ROWS,
    add_entry,
    default_attempt_dir,
    describe,
    launch_for,
    load_entries,
    load_launches,
    log_tail,
    review_and_execute,
    process_alive,
    read_table,
    read_table_rows,
    readable_value,
    remove_entry,
    runtime_mismatch,
    sort_key,
)
from scpolya_ui.state.app_state import ProductAppState

PAGE_KEY = "differential_apa"
SUMMARY_TABLE = "differential_apa_summary.tsv"
DIAGNOSTIC_STATUSES = frozenset({"PREPARED_ELIGIBILITY_ONLY", "DESCRIPTIVE_ONLY"})
ACTIVE_STATUSES = frozenset({"starting", "RUNNING"})
POLL_SECONDS = 3.0
ELIGIBILITY_KEYS = (
    "mode",
    "reason",
    "observed_conditions",
    "donors_per_condition",
    "independent_donors",
    "eligible_pseudobulks",
    "excluded_pseudobulks",
    "metadata_cells",
    "design_rank",
    "residual_df",
    "units_after_declared_coverage_gates",
)
# Fixed English sentences from the adapter, shown in the user's language.
BACKEND_SENTENCES = {
    "Supplied donor, condition and upstream catalogue provenance cannot be independently authenticated by this adapter.": "diff.limit.provenance",
    "Approval hashes bind intent and bytes; issued_by is not cryptographic issuer authentication.": "diff.limit.issuer",
    "Execution is bound to the exact plan file bytes (plan sha256 recorded in attempt.json).": "diff.limit.plan_binding",
    "One donor or one observed condition does not establish population differential APA.": "diff.limit.single_donor",
    "Eligibility diagnostics are preflight checks, not statistical results or calibrated FDR evidence.": "diff.limit.preflight",
    "Existing native denominators, distal ties, unavailable values and separate endpoint families remain authoritative.": "diff.limit.native",
    "Length availability/moderation and actual inference remain backend decisions.": "diff.endpoint_testability_note",
    "Moved plan directory": "diff.backend.moved_plan",
}
VALUE_KEYS = ("mode", "reason", "backend_mode", "inference_reason")


# What the user can do after an attempt that did not produce inference results.
_ATTEMPT_NEXT_STEPS = {
    "FAILED": "diff.next.failed",
    "BLOCKED_RUNTIME": "diff.next.blocked_runtime",
    "BLOCKED_ELIGIBILITY": "diff.next.blocked_eligibility",
}


def render_differential_apa_page(state: ProductAppState) -> None:
    from nicegui import ui

    page_state: dict[str, Any] = {
        "selected": "",
        "table": SUMMARY_TABLE,
        "offset": 0,
        "readable": True,
        "filter": "",
        "sort": None,
        "descending": False,
    }
    refs: dict[str, Any] = {}
    requested = str(state.payload.get("differential_analysis") or "")

    def backend_text(text: Any) -> str:
        raw = str(text)
        prefix, _, message = raw.rpartition(": ")
        key = BACKEND_SENTENCES.get(raw) or (BACKEND_SENTENCES.get(message) if prefix.endswith("Error") else None)
        return state.t(key) if key else error_text(state.t, raw)

    def coded(value: Any) -> str:
        """Machine codes such as ``inference_candidate`` with a localized label when one exists."""
        raw = _value(value)
        key = f"diff.value.{raw}"
        label = state.t(key)
        return raw if label == f"[[{key}]]" else label
    if requested:
        try:
            page_state["selected"] = add_entry(requested, workspace_id=state.selected_workspace_id or "").path
        except Exception as exc:
            page_state["selected"] = ""
            # A link (for example from a note) named a folder that cannot be opened; say so.
            page_state["link_error"] = (requested, error_text(state.t, exc))

    def entries():
        return load_entries()

    def select(path: str) -> None:
        page_state.update(selected=path, table=SUMMARY_TABLE, offset=0)
        content.refresh()

    def open_from_dialog() -> None:
        value = str(getattr(refs.get("path_input"), "value", "") or "").strip()
        if not value:
            ui.notify(state.t("diff.path_required"), color="warning")
            return
        try:
            entry = add_entry(value, workspace_id=state.selected_workspace_id or "")
        except Exception as exc:
            ui.notify(error_text(state.t, exc), color="negative")
            return
        open_dialog.close()
        refs["path_input"].value = ""
        select(entry.path)

    def forget(path: str) -> None:
        remove_entry(path)
        if page_state["selected"] == path:
            page_state["selected"] = ""
        content.refresh()

    def set_table(name: str | None) -> None:
        page_state.update(table=str(name or SUMMARY_TABLE), offset=0, sort=None, filter="")
        table_content.refresh()

    def set_table_option(**changes: Any) -> None:
        page_state.update(offset=0, **changes)
        table_rows_view.refresh()

    def page_by(offset: int) -> None:
        page_state["offset"] = max(0, offset)
        table_content.refresh()

    picker = WorkspacePathPicker(
        translate=state.t,
        on_select=lambda path: setattr(refs["path_input"], "value", path),
    )

    with ui.dialog() as open_dialog, ui.card().classes("rs-dialog"):
        with ui.column().classes("rs-review gap-2"):
            ui.label(state.t("diff.open_title")).classes("rs-review-title")
            ui.label(state.t("diff.open_detail")).classes("rs-review-sub")
            with ui.row().classes("df-path-row w-full"):
                refs["path_input"] = ui.input(placeholder="/…/plan  ·  /…/attempt-001").classes(
                    "df-path-input"
                ).props("dense outlined hide-bottom-space")
                ui.button(
                    state.t("workspace.browse"),
                    icon="folder_open",
                    on_click=lambda: picker.open(str(refs["path_input"].value or "")),
                ).props("outline no-caps")
            with ui.row().classes("rs-review-actions w-full"):
                ui.button(state.t("common.cancel"), on_click=open_dialog.close).props("flat no-caps")
                ui.button(state.t("diff.open_action"), on_click=open_from_dialog).props(
                    "unelevated no-caps color=primary"
                )

    def open_approval(view) -> None:
        page_state["approve_view"] = view
        page_state["approve_problem"] = runtime_mismatch(view.plan)
        refs["approve_outdir"] = default_attempt_dir(view.path)
        approval_body.refresh()
        approve_dialog.open()

    def submit_approval() -> None:
        view = page_state.get("approve_view")
        if view is None:
            return
        if not refs["approve_check"].value:
            ui.notify(state.t("diff.approve.check_required"), color="warning")
            return
        try:
            launch = review_and_execute(
                view,
                str(refs["approve_dir"].value or ""),
                workspace_id=state.selected_workspace_id or "",
            )
        except Exception as exc:
            ui.notify(error_text(state.t, exc), color="negative", multi_line=True)
            return
        approve_dialog.close()
        ui.notify(state.t("diff.approve.started"), color="positive")
        select(launch.outdir)

    with ui.dialog() as approve_dialog, ui.card().classes("rs-dialog df-approve-dialog"):
        @ui.refreshable
        def approval_body() -> None:
            view = page_state.get("approve_view")
            if view is None:
                return
            request = view.request
            contrast = request.get("contrast") if isinstance(request.get("contrast"), dict) else {}
            options = request.get("options") if isinstance(request.get("options"), dict) else {}
            problem = page_state.get("approve_problem") or ""
            with ui.column().classes("rs-review gap-2 w-full"):
                ui.label(state.t("diff.approve.title")).classes("rs-review-title")
                ui.label(state.t("diff.approve.detail")).classes("rs-review-sub")
                with ui.column().classes("df-approve-summary gap-0 w-full"):
                    _kv(state.t("diff.field.plan"), str(Path(view.path) / "plan.json"), mono=True)
                    _kv(state.t("diff.plan_sha256"), view.plan_sha256, mono=True)
                    _kv(
                        state.t("diff.approve.contrast"),
                        state.t("diff.contrast", control=contrast.get("control"), case=contrast.get("case")),
                    )
                    _kv(state.t("diff.approve.cell_type"), _value(request.get("cell_type")))
                    _kv(state.t("diff.approve.methods"), _value(view.plan.get("runtime_methods") or options.get("methods")))
                    if view.provenance_kind:
                        _kv(state.t("diff.approve.provenance"), state.t(f"diff.provenance.{view.provenance_kind}"))
                    _kv(state.t("diff.approve.envelope"), " · ".join(ENVELOPE_PROPERTIES))
                # The exact file the SHA256 above binds; submit re-checks the hash before launching.
                with ui.expansion(state.t("diff.approve.full_plan"), icon="o_description", value=False).classes(
                    "run-setup-review-expansion df-plan-expansion w-full"
                ).props("dense"):
                    try:
                        plan_text = (Path(view.path) / "plan.json").read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as exc:
                        ui.label(error_text(state.t, exc)).classes("env-file-missing")
                    else:
                        ui.label(state.t("diff.approve.full_plan_note")).classes("env-note")
                        ui.label(plan_text).classes("df-plan-raw").props(
                            f"aria-label={prop_text(state.t('diff.approve.full_plan'))} tabindex=0"
                        )
                if problem:
                    with ui.row().classes("ov-alert ov-alert-crit w-full"):
                        with ui.column().classes("gap-1 min-w-0"):
                            ui.label(state.t("diff.approve.stale")).classes("ov-alert-title")
                            ui.label(problem).classes("ov-alert-detail")
                refs["approve_dir"] = ui.input(
                    state.t("diff.approve.outdir"), value=refs.get("approve_outdir", "")
                ).classes("w-full df-mono-input").props("dense outlined")
                ui.label(state.t("diff.approve.outdir_hint")).classes("env-note")
                refs["approve_check"] = ui.checkbox(state.t("diff.approve.confirm")).classes("df-approve-check")
                with ui.row().classes("rs-review-actions w-full"):
                    ui.button(state.t("common.cancel"), on_click=approve_dialog.close).props("flat no-caps")
                    submit = ui.button(
                        state.t("diff.approve.submit"), icon="o_play_arrow", on_click=submit_approval
                    ).props("unelevated no-caps color=primary")
                    if problem:
                        submit.props("disable")
                    else:
                        submit.bind_enabled_from(refs["approve_check"], "value")

        approval_body()

    def poll() -> None:
        path = page_state.get("selected") or ""
        if page_state.get("active_status") not in ACTIVE_STATUSES or not path:
            return
        view = describe(path)
        if view.status != page_state.get("active_status"):
            content.refresh()
        else:
            progress_content.refresh()

    ui.timer(POLL_SECONDS, poll)

    def status_label(status: str) -> str:
        key = f"diff.status.{status}"
        label = state.t(key)
        return status if label == f"[[{key}]]" else label

    def status_badge(status: str, *, classes: str = "") -> None:
        tone_status = status.casefold()
        with ui.element("span").classes(f"sp-status sp-status-{_tone(tone_status)} {classes}".strip()) as badge:
            ui.element("i")
            ui.label(status_label(status))

    @ui.refreshable
    def content() -> None:
        items = entries()
        paths = [entry.path for entry in items]
        if page_state["selected"] not in paths:
            page_state["selected"] = paths[0] if paths else ""
        with ui.element("div").classes("env-page df-page w-full"):
            with ui.row().classes("ov-header w-full"):
                with ui.column().classes("ov-heading gap-0"):
                    ui.label(state.t("nav.differential_apa")).classes("ov-title")
                    ui.label(state.t("page.subtitle.differential_apa")).classes("env-subtitle")
                with ui.row().classes("ov-actions"):
                    ui.button(
                        state.t("diff.open_action"),
                        icon="o_folder_open",
                        on_click=open_dialog.open,
                    ).props("unelevated no-caps color=primary")
            link_error = page_state.get("link_error")
            if link_error:
                with ui.row().classes("ov-alert ov-alert-warn sc-alert w-full"):
                    with ui.column().classes("gap-1 min-w-0"):
                        ui.label(state.t("diff.link_error", path=link_error[0])).classes("ov-alert-title")
                        ui.label(link_error[1]).classes("ov-alert-detail")

            if not items:
                _render_intro()
                return

            views = {entry.path: describe(entry.path) for entry in items}
            launch_counts: dict[str, int] = {}
            for launch in load_launches().values():
                plan_dir = str(Path(launch.plan).parent)
                launch_counts[plan_dir] = launch_counts.get(plan_dir, 0) + 1
            with ui.element("div").classes("df-layout"):
                with ui.column().classes("df-list gap-0"):
                    for entry in items:
                        view = views[entry.path]
                        active = entry.path == page_state["selected"]
                        row = ui.column().classes("df-item gap-0" + (" df-item-active" if active else ""))
                        make_keyboard_selectable(row, lambda _, path=entry.path: select(path), label=entry.path)
                        with row:
                            with ui.row().classes("df-item-top w-full"):
                                ui.label(Path(entry.path).name).classes("df-item-name").tooltip(Path(entry.path).name)
                                ui.label(state.t(f"diff.kind.{view.kind}")).classes("df-kind")
                            with ui.row().classes("df-item-status-row"):
                                status_badge(view.status, classes="df-item-status")
                                launched = launch_counts.get(str(Path(entry.path).resolve()), 0)
                                if view.kind == "plan" and launched:
                                    ui.label(state.t("diff.launched_count", count=launched)).classes("df-launched").tooltip(
                                        state.t("diff.launched_tip")
                                    )
                            ui.label("\u200e" + str(Path(entry.path).parent)).classes("df-item-path").tooltip(entry.path)
                with ui.column().classes("df-detail gap-0"):
                    _render_detail(views[page_state["selected"]])

    def _render_intro() -> None:
        with ui.element("div").classes("ov-step-cards df-intro"):
            for index, key in enumerate(("plan", "approve", "read"), start=1):
                with ui.column().classes("ov-step-card gap-0"):
                    ui.label(str(index)).classes("ov-step-number")
                    ui.label(state.t(f"diff.intro.{key}.title")).classes("ov-step-title")
                    ui.label(state.t(f"diff.intro.{key}.detail")).classes("ov-step-detail")
        ui.label(state.t("diff.intro.note")).classes("env-note df-intro-note")

    def _render_detail(view) -> None:
        request = view.request
        contrast = request.get("contrast") if isinstance(request.get("contrast"), dict) else None
        with ui.row().classes("df-head w-full"):
            with ui.column().classes("gap-0 min-w-0"):
                with ui.row().classes("df-title-row"):
                    ui.label(Path(view.path).name).classes("st-detail-title")
                    status_badge(view.status)
                    if view.status in DIAGNOSTIC_STATUSES:
                        ui.label(state.t("common.reference_only")).classes("sp-ref").tooltip(
                            state.t("diff.diagnostic_note")
                        )
                with ui.row().classes("st-detail-chips"):
                    _chip(state.t(f"diff.kind.{view.kind}"))
                    if view.provenance_kind:
                        _chip(
                            state.t(f"diff.provenance.{view.provenance_kind}"),
                            warn=view.provenance_kind == "synthetic",
                        )
                    _chip(
                        state.t("diff.contrast", control=contrast.get("control"), case=contrast.get("case"))
                        if contrast
                        else state.t("diff.no_contrast")
                    )
                    if request.get("cell_type"):
                        _chip(state.t("diff.cell_type", value=request.get("cell_type")))
                    if view.kind == "attempt" and view.attempt.get("verified"):
                        _chip(state.t("diff.verified")).tooltip(state.t("diff.verified_detail"))
            ui.element("div").classes("ov-spacer")
            ui.button(
                state.t("diff.forget"),
                on_click=lambda: open_confirmation_dialog(
                    title=state.t("diff.forget_title"),
                    message=state.t("diff.forget_detail"),
                    confirm_label=state.t("diff.forget"),
                    cancel_label=state.t("common.cancel"),
                    on_confirm=lambda: forget(view.path),
                    details=((state.t("common.path"), view.path),),
                    icon="o_playlist_remove",
                ),
            ).props("flat dense no-caps").classes("ov-link-button")

        page_state["active_status"] = view.status
        launch = launch_for(view.path)
        if view.error:
            title = "diff.error.refused" if view.status == "refused" else f"diff.error.{view.kind}"
            with ui.row().classes("ov-alert ov-alert-crit sc-alert w-full"):
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label(state.t(title)).classes("ov-alert-title")
                    ui.label(backend_text(view.error)).classes("ov-alert-detail")
                    ui.label(state.t(f"{title}_next")).classes("ov-alert-detail df-next-hint")
            if launch is not None:
                page_state["progress_path"] = view.path
                progress_content()
            return

        if view.status in ACTIVE_STATUSES and launch is not None:
            page_state["progress_path"] = view.path
            progress_content()
            if view.status == "starting":
                return

        with ui.element("div").classes("df-grid"):
            _render_eligibility(view)
            if view.kind == "plan":
                _render_next_step(view)
            else:
                _render_attempt_summary(view)
        if view.kind == "attempt":
            page_state["view"] = view
            table_content()
        _render_limits(view)

    def _render_eligibility(view) -> None:
        eligibility = view.eligibility
        with ui.element("section").classes("ov-card w-full"):
            with ui.row().classes("ov-card-head w-full"):
                ui.label(state.t("diff.eligibility")).classes("ov-card-title")
                if "inference_eligible" in eligibility:
                    ui.label(
                        state.t("diff.inference_yes" if eligibility.get("inference_eligible") else "diff.inference_no")
                    ).classes("ov-card-count")
            with ui.column().classes("ov-card-body gap-0 w-full"):
                for key in ELIGIBILITY_KEYS:
                    if key in eligibility:
                        value = coded(eligibility[key]) if key in VALUE_KEYS else _value(eligibility[key])
                        _kv(state.t(f"diff.field.{key}"), value)
                if eligibility.get("endpoint_testability"):
                    ui.label(backend_text(eligibility["endpoint_testability"])).classes("env-note")

    def _render_next_step(view) -> None:
        with ui.element("section").classes("ov-card w-full"):
            with ui.row().classes("ov-card-head w-full"):
                ui.label(state.t("diff.next_step")).classes("ov-card-title")
            with ui.column().classes("ov-card-body gap-2 w-full"):
                if view.status != "READY_FOR_EXTERNAL_APPROVAL":
                    ui.label(state.t(f"diff.next.{view.status}") if _has(f"diff.next.{view.status}") else state.t("diff.next.other")).classes("df-text")
                    return
                ui.label(state.t("diff.next.ready")).classes("df-text")
                _kv(state.t("diff.plan_sha256"), view.plan_sha256, mono=True, copy=True)
                plan_path = str(Path(view.path) / "plan.json")
                launches = [item for item in load_launches().values() if item.plan == str(Path(plan_path).resolve())]
                with ui.row().classes("df-next-actions w-full"):
                    ui.button(
                        state.t("diff.approve.open"),
                        icon="o_verified",
                        on_click=lambda: open_approval(view),
                    ).props("unelevated no-caps color=primary")
                if launches:
                    ui.label(state.t("diff.approve.previous", count=len(launches))).classes("df-code-label")
                    for item in sorted(launches, key=lambda launch: launch.started_at, reverse=True):
                        link = ui.row().classes("df-launch-link")
                        make_keyboard_selectable(link, lambda _, path=item.outdir: select(path), label=item.outdir)
                        with link:
                            ui.icon("o_subdirectory_arrow_right", size="1em")
                            ui.label(Path(item.outdir).name).classes("ov-mono")
                            ui.label(short_timestamp(item.started_at)).classes("ov-muted-small")
                with ui.expansion(state.t("diff.manual.title"), value=False).classes("df-limits w-full").props("dense"):
                    ui.label(state.t("diff.execute_command")).classes("df-code-label")
                    _code(
                        "scpolya-operator --manifest /absolute/operator.json differential-apa execute \\\n"
                        f"  --plan {plan_path} \\\n"
                        "  --outdir /absolute/new-attempt-dir"
                    )

    @ui.refreshable
    def progress_content() -> None:
        path = page_state.get("progress_path") or ""
        launch = launch_for(path) if path else None
        if launch is None:
            return
        view = describe(path)
        alive = process_alive(launch)
        with ui.element("section").classes("ov-card df-progress w-full"):
            with ui.row().classes("ov-card-head w-full"):
                ui.label(state.t("diff.progress.title")).classes("ov-card-title")
                if view.status in ACTIVE_STATUSES and alive:
                    ui.spinner(size="1em").classes("df-spinner")
                    ui.label(state.t("diff.progress.polling", seconds=int(POLL_SECONDS))).classes("ov-card-count")
            with ui.column().classes("ov-card-body gap-0 w-full"):
                _kv(state.t("diff.progress.started"), local_timestamp(launch.started_at, seconds=True))
                if launch.issued_by:
                    _kv(state.t("diff.progress.issued_by"), launch.issued_by)
                _kv(
                    state.t("diff.progress.envelope"),
                    state.t("diff.progress.envelope_scope") if launch.envelope == "systemd-scope" else state.t("diff.progress.envelope_none"),
                )
                if launch.approval:
                    _kv(state.t("diff.progress.approval"), launch.approval, mono=True, copy=True)
                _kv(state.t("diff.progress.log"), launch.log, mono=True, copy=True)
                if view.status == "RUNNING" and not alive:
                    ui.label(state.t("diff.progress.orphaned")).classes("env-note df-warn-note")
                tail = log_tail(launch, 2048).strip()
                if tail:
                    ui.label(state.t("diff.progress.log_tail")).classes("df-code-label")
                    render_terminal(tail, translate=state.t, max_height="260px")

    def _render_attempt_summary(view) -> None:
        attempt = view.attempt
        with ui.element("section").classes("ov-card w-full"):
            with ui.row().classes("ov-card-head w-full"):
                ui.label(state.t("diff.attempt")).classes("ov-card-title")
            with ui.column().classes("ov-card-body gap-0 w-full"):
                _kv(state.t("diff.field.backend_mode"), coded(attempt.get("backend_mode")))
                _kv(state.t("diff.field.inference_reason"), coded(attempt.get("inference_reason")))
                if view.issued_by:
                    _kv(state.t("diff.progress.issued_by"), view.issued_by)
                plan = attempt.get("plan") if isinstance(attempt.get("plan"), dict) else {}
                _kv(state.t("diff.field.plan"), str(plan.get("path") or "-"), mono=True, copy=bool(plan.get("path")))
                _kv(state.t("diff.field.tables"), str(len(attempt.get("tables") or [])))
                if attempt.get("error"):
                    _kv(state.t("diff.field.error"), backend_text(attempt["error"]))
            if view.status in _ATTEMPT_NEXT_STEPS:
                with ui.row().classes("rs-notice rs-notice-info df-attempt-next w-full"):
                    ui.icon("o_tips_and_updates", size="16px")
                    ui.label(state.t(_ATTEMPT_NEXT_STEPS[view.status]))

    def _render_limits(view) -> None:
        limits = view.attempt.get("limits") or view.plan.get("limits") or []
        if not limits:
            return
        with ui.expansion(state.t("diff.limits", count=len(limits)), value=False).classes(
            "df-limits w-full"
        ).props("dense"):
            for item in limits:
                ui.label(backend_text(item)).classes("df-limit")

    @ui.refreshable
    def table_content() -> None:
        view = page_state.get("view")
        if view is None or view.kind != "attempt":
            return
        tables = list(view.attempt.get("tables") or [])
        if not tables:
            return
        if page_state["table"] not in tables:
            page_state["table"] = SUMMARY_TABLE if SUMMARY_TABLE in tables else tables[0]
        with ui.element("section").classes("ov-card df-table-card w-full"):
            with ui.row().classes("ov-card-head w-full"):
                ui.label(state.t("diff.tables")).classes("ov-card-title")
                ui.element("div").classes("ov-spacer")
                ui.select(
                    options={name: name for name in tables},
                    value=page_state["table"],
                    on_change=lambda e: set_table(e.value),
                ).classes("sc-source-select").props("dense outlined options-dense hide-bottom-space")
                ui.switch(
                    state.t("diff.readable"),
                    value=page_state["readable"],
                    on_change=lambda e: (page_state.update(readable=bool(e.value), offset=0), table_content.refresh()),
                ).props("dense").classes("df-readable-switch").tooltip(state.t("diff.readable_tip"))
            if page_state["readable"]:
                table_tools()
                return
            try:
                page = read_table(view.path, page_state["table"], offset=page_state["offset"])
            except Exception as exc:
                with ui.column().classes("ov-card-body w-full"):
                    ui.label(error_text(state.t, exc)).classes("env-file-missing")
                return
            columns = page.get("columns") or []
            rows = page.get("rows") or []
            header = "".join(f"<th>{escape(str(column))}</th>" for column in columns)
            body = "".join(
                "<tr>"
                + "".join(
                    f"<td class='{'df-na' if row.get(column) == 'NA' else ''}' "
                    f"title='{escape(str(row.get(column) or ''), quote=True)}'>{escape(_cell(row.get(column)))}</td>"
                    for column in columns
                )
                + "</tr>"
                for row in rows
            )
            ui.html(
                f"<div class='df-table-scroll'><table class='df-table'><thead><tr>{header}</tr></thead>"
                f"<tbody>{body}</tbody></table></div>"
            ).classes("w-full")
            with ui.row().classes("sc-pager w-full"):
                start = page_state["offset"] + 1 if rows else 0
                end = page_state["offset"] + len(rows)
                ui.label(state.t("diff.rows", start=start, end=end)).classes("ov-muted-small")
                ui.label(state.t("diff.exact_values")).classes("ov-muted-small df-exact")
                ui.element("div").classes("ov-spacer")
                ui.button(
                    icon="chevron_left",
                    on_click=lambda: page_by(page_state["offset"] - TABLE_PAGE_ROWS),
                ).props("flat dense" + (" disable" if page_state["offset"] == 0 else ""))
                next_offset = page.get("next_offset")
                ui.button(
                    icon="chevron_right",
                    on_click=lambda: page_by(next_offset or 0),
                ).props("flat dense" + ("" if next_offset else " disable"))

    def table_tools() -> None:
        """Readable view controls; outside the refreshed rows so the filter keeps focus while typing."""
        view = page_state.get("view")
        try:
            columns, all_rows, truncated = read_table_rows(view.path, page_state["table"])
        except Exception as exc:
            page_state["readable_rows"] = None
            with ui.column().classes("ov-card-body w-full"):
                ui.label(error_text(state.t, exc)).classes("env-file-missing")
            return
        page_state["readable_rows"] = (columns, all_rows, truncated)
        if page_state["sort"] not in columns:
            page_state["sort"] = next((c for c in columns if c.endswith("_FDR")), None)
            page_state["descending"] = False

        def toggle_direction(button) -> None:
            set_table_option(descending=not page_state["descending"])
            button.props(f'icon={"north" if page_state["descending"] else "south"}')
            direction_tip.text = state.t("diff.sort_desc" if page_state["descending"] else "diff.sort_asc")

        with ui.row().classes("df-table-tools w-full"):
            ui.input(
                value=page_state["filter"],
                placeholder=state.t("diff.filter_placeholder"),
                on_change=lambda e: set_table_option(filter=str(e.value or "")),
            ).props("dense outlined clearable hide-bottom-space debounce=250").classes("df-filter")
            ui.select(
                options={column: column for column in columns},
                value=page_state["sort"],
                label=state.t("diff.sort_by"),
                on_change=lambda e: set_table_option(sort=e.value),
            ).props("dense outlined options-dense hide-bottom-space").classes("df-sort")
            direction = ui.button(
                icon="south" if not page_state["descending"] else "north",
                on_click=lambda: toggle_direction(direction),
            ).props(f'flat dense aria-label="{state.t("diff.sort_direction")}"')
            with direction:
                direction_tip = ui.tooltip(state.t("diff.sort_desc" if page_state["descending"] else "diff.sort_asc"))
            if truncated:
                ui.label(state.t("diff.sort_capped", count=len(all_rows))).classes("ov-muted-small")
        table_rows_view()

    @ui.refreshable
    def table_rows_view() -> None:
        """Readable rows: sorted and filtered here, numbers rounded; exact text in the cell tooltip."""
        cached = page_state.get("readable_rows")
        if cached is None:
            return
        columns, all_rows, truncated = cached
        query = page_state["filter"].strip().casefold()
        # Match what the cell shows (decoded unit ids) as well as the stored text.
        rows = [
            row for row in all_rows
            if not query
            or any(
                query in str(row.get(key) or "").casefold()
                or query in readable_value(key, row.get(key)).casefold()
                for key in ("unit_id", "gene_id")
            )
        ]
        if page_state["sort"]:
            rows.sort(key=lambda row: sort_key(row.get(page_state["sort"])))
            if page_state["descending"]:
                filled = [row for row in rows if sort_key(row.get(page_state["sort"]))[0] < 2]
                rows = filled[::-1] + rows[len(filled):]
        start = page_state["offset"]
        shown = rows[start:start + TABLE_PAGE_ROWS]
        header = "".join(
            f"<th class='{'df-sorted' if column == page_state['sort'] else ''}'>{escape(str(column))}</th>"
            for column in columns
        )
        body = "".join(
            "<tr>"
            + "".join(
                f"<td class='{'df-na' if row.get(column) == 'NA' else ''}' "
                f"title='{escape(str(row.get(column) or ''), quote=True)}'>"
                f"{escape(_cell(readable_value(column, row.get(column))))}</td>"
                for column in columns
            )
            + "</tr>"
            for row in shown
        )
        ui.html(
            f"<div class='df-table-scroll'><table class='df-table'><thead><tr>{header}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>"
        ).classes("w-full")
        with ui.row().classes("sc-pager w-full"):
            ui.label(
                state.t(
                    "diff.rows_of_capped" if truncated else "diff.rows_of",
                    start=start + 1 if shown else 0,
                    end=start + len(shown),
                    total=len(rows),
                )
            ).classes("ov-muted-small")
            ui.label(state.t("diff.readable_note")).classes("ov-muted-small df-exact")
            ui.element("div").classes("ov-spacer")
            ui.button(
                icon="chevron_left",
                on_click=lambda: (page_state.update(offset=max(0, start - TABLE_PAGE_ROWS)), table_rows_view.refresh()),
            ).props("flat dense" + (" disable" if start == 0 else ""))
            ui.button(
                icon="chevron_right",
                on_click=lambda: (page_state.update(offset=start + TABLE_PAGE_ROWS), table_rows_view.refresh()),
            ).props("flat dense" + ("" if start + TABLE_PAGE_ROWS < len(rows) else " disable"))

    def _chip(text: str, *, warn: bool = False):
        return ui.label(text).classes("st-chip" + (" df-chip-warn" if warn else ""))

    def _kv(label: str, value: str, *, mono: bool = False, copy: bool = False) -> None:
        with ui.row().classes("ov-detail w-full"):
            ui.label(label).classes("ov-detail-key")
            text = ui.label(value).classes("ov-detail-value" + (" ov-mono" if mono else ""))
            text.tooltip(value)
            if copy and value and value != "-":
                button = ui.button(
                    icon="o_content_copy",
                    on_click=lambda _, v=value: (ui.clipboard.write(v), ui.notify(state.t("common.copied"))),
                ).props(f'flat dense size=sm aria-label="{state.t("common.copy")}"').classes("ov-copy")
                button.tooltip(state.t("common.copy"))

    def _code(text: str) -> None:
        render_terminal(text, translate=state.t, max_height="320px")

    def _has(key: str) -> bool:
        return state.t(key) != f"[[{key}]]"

    content()


def _tone(status: str) -> str:
    from scpolya_ui.components.status import status_tone

    return status_tone(status)


def _value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, dict):
        return " · ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= 40 else text[:18] + "…" + text[-18:]
