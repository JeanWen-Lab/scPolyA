"""Right-side notes panel shown in agent mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scpolya_ui.errors import error_text
from scpolya_ui.state.app_state import ProductAppState

FILTERS = ("all", "attention", "open", "resolved")
POLL_SECONDS = 8.0
NOTE_ICONS = {"note": "o_sticky_note_2", "flag": "o_flag", "question": "o_help_outline", "approval_request": "o_verified"}
ANCHOR_ICONS = {
    "workspace": "o_home",
    "run": "o_format_list_bulleted",
    "task": "o_task_alt",
    "artifact": "o_description",
    "locus": "o_ssid_chart",
    "plan": "o_compare_arrows",
    "figure": "o_image",
}


def render_notes_button(state: ProductAppState, workspace) -> None:
    """Topbar button plus the panel it opens; renders nothing without a workspace."""
    from nicegui import ui

    from scpolya_agent.annotations import store_path
    from scpolya_ui.components.interaction import make_keyboard_selectable
    from scpolya_ui.components.timefmt import short_timestamp
    from scpolya_ui.services import annotation_service as notes

    if workspace is None:
        return
    root = workspace.root_path
    view: dict[str, Any] = {"filter": "all", "mtime": None, "draft": "", "anchor": "workspace"}
    current_run = str(state.payload.get("selected_run_record_id") or "")

    def store_mtime() -> float | None:
        try:
            return store_path(root).stat().st_mtime
        except OSError:
            return None

    def refresh_all() -> None:
        view["mtime"] = store_mtime()
        badge.refresh()
        panel_body.refresh()

    def open_panel() -> None:
        notes.mark_seen(root)
        refresh_all()
        dialog.open()

    def act(action) -> bool:
        try:
            action()
        except Exception as exc:
            ui.notify(error_text(state.t, exc), color="negative")
            return False
        notes.mark_seen(root)
        refresh_all()
        return True

    def poll() -> None:
        if store_mtime() != view["mtime"]:
            if dialog.value:
                notes.mark_seen(root)
            refresh_all()

    @ui.refreshable
    def badge() -> None:
        summary = notes.load_notes(root)
        button = ui.button(state.t("notes.button"), icon="o_forum", on_click=open_panel).props(
            "flat dense no-caps"
        ).classes("product-topbar-notes")
        with button:
            if summary.attention:
                ui.badge(str(summary.attention)).props("floating rounded").classes("nt-badge")
        button.tooltip(state.t("notes.button_tooltip", count=summary.unresolved))

    def send_note() -> None:
        body = str(view.get("draft") or "").strip()
        if not body:
            return
        if view["anchor"] == "run" and current_run:
            anchor = dict(anchor_type="run", anchor_ref=current_run, anchor_label=current_run)
        else:
            anchor = dict(anchor_type="workspace", anchor_ref=root, anchor_label=workspace.alias or Path(root).name)
        view["draft"] = ""
        act(lambda: notes.add_user_note(root, body=body, **anchor))

    @ui.refreshable
    def panel_body() -> None:
        summary = notes.load_notes(root)
        with ui.row().classes("nt-head w-full"):
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(state.t("notes.title")).classes("nt-title")
                ui.label(workspace.alias or root).classes("nt-subtitle")
            ui.element("div").classes("ov-spacer")
            ui.button(icon="close", on_click=dialog.close).props(
                f'flat dense round aria-label="{state.t("common.close")}"'
            )
        ui.label(state.t("notes.async_note")).classes("nt-note")
        if summary.integrity:
            with ui.row().classes("ov-alert ov-alert-crit w-full"):
                with ui.column().classes("gap-0 min-w-0"):
                    ui.label(state.t("notes.integrity_title")).classes("ov-alert-title")
                    ui.label(summary.integrity).classes("ov-alert-detail")
        with ui.column().classes("nt-composer gap-2 w-full"):
            composer = ui.textarea(
                placeholder=state.t("notes.compose_placeholder"),
                value=view["draft"],
                on_change=lambda e: view.__setitem__("draft", e.value or ""),
            ).props("outlined dense autogrow").classes("w-full nt-textarea")
            composer.on("keydown.ctrl.enter", lambda: send_note())
            composer.on("keydown.meta.enter", lambda: send_note())
            with ui.row().classes("nt-composer-actions w-full"):
                options = {"workspace": state.t("notes.anchor.workspace")}
                if current_run:
                    options["run"] = state.t("notes.anchor.current_run", run=current_run)
                if view["anchor"] not in options:
                    view["anchor"] = "workspace"
                ui.select(
                    options=options,
                    value=view["anchor"],
                    on_change=lambda e: view.__setitem__("anchor", e.value or "workspace"),
                ).props("dense outlined options-dense hide-bottom-space").classes("nt-anchor-select")
                ui.element("div").classes("ov-spacer")
                ui.button(state.t("notes.send"), icon="o_send", on_click=send_note).props(
                    "unelevated dense no-caps color=primary"
                )
        counts = {
            "all": len(summary.threads),
            "attention": sum(1 for item in summary.threads if item["awaiting_user"]),
            "open": summary.unresolved,
            "resolved": len(summary.threads) - summary.unresolved,
        }
        with ui.row().classes("runs-filter-chips w-full nt-filters"):
            for key in FILTERS:
                active = view["filter"] == key
                ui.button(
                    f'{state.t(f"notes.filter.{key}")} {counts[key]}',
                    on_click=lambda key=key: (view.__setitem__("filter", key), panel_body.refresh()),
                ).props("flat dense no-caps").classes("runs-chip runs-chip-active" if active else "runs-chip")
        visible = [
            item
            for item in summary.threads
            if view["filter"] == "all"
            or (view["filter"] == "attention" and item["awaiting_user"])
            or (view["filter"] == "open" and not item["resolved"])
            or (view["filter"] == "resolved" and item["resolved"])
        ]
        # Threads waiting on the user come first; sort is stable, so order within each half is kept.
        visible.sort(key=lambda item: not (item["awaiting_user"] and not item["resolved"]))
        if not visible:
            ui.label(state.t("notes.empty" if not summary.threads else "notes.no_matches")).classes(
                "nt-empty"
            )
        with ui.column().classes("nt-list gap-2 w-full"):
            for thread in visible:
                _thread_card(thread)

    def _author(event: dict) -> None:
        author = event.get("author") or {}
        is_agent = author.get("type") == "agent"
        name = str(author.get("name") or "?")
        with ui.row().classes("nt-author"):
            ui.label(name[:1].upper()).classes("nt-avatar " + ("nt-avatar-agent" if is_agent else "nt-avatar-user"))
            ui.label(name).classes("nt-author-name")
            ui.label(state.t("notes.role.agent" if is_agent else "notes.role.user")).classes("nt-role")
            ui.label(short_timestamp(str(event.get("created_at") or ""))).classes("nt-time")

    def _body(event: dict) -> None:
        text = str(event.get("body") or "")
        if text:
            ui.label(text).classes("nt-body")

    def _thread_card(thread: dict) -> None:
        note = thread["note"]
        anchor = note.get("anchor") or {}
        is_agent = (note.get("author") or {}).get("type") == "agent"
        root_id = note["id"]
        classes = "nt-card" + (" nt-card-resolved" if thread["resolved"] else "")
        classes += " nt-card-attention" if thread["awaiting_user"] else ""
        with ui.column().classes(classes + " gap-1 w-full"):
            with ui.row().classes("nt-card-top w-full"):
                _author(note)
                ui.element("div").classes("ov-spacer")
                with ui.row().classes("nt-type"):
                    ui.icon(NOTE_ICONS.get(note.get("note_type"), "o_sticky_note_2"), size="14px")
                    ui.label(state.t(f'notes.type.{note.get("note_type", "note")}'))
                if is_agent:
                    ui.label(state.t("common.reference_only")).classes("sp-ref").tooltip(state.t("notes.reference_tip"))
            target = notes.anchor_target(anchor)
            anchor_ref = str(anchor.get("ref") or "")
            anchor_text = str(anchor.get("label") or ((Path(anchor_ref).name or anchor_ref) if "/" in anchor_ref else anchor_ref))
            with ui.row().classes("nt-anchor" + (" nt-anchor-link" if target else "")) as anchor_row:
                ui.icon(ANCHOR_ICONS.get(anchor.get("type"), "o_place"), size="14px")
                ui.label(state.t(f'notes.anchor_type.{anchor.get("type", "workspace")}')).classes("nt-anchor-kind")
                ui.label(anchor_text).classes("nt-anchor-ref").tooltip(str(anchor.get("ref") or ""))
            if target:
                page, query = target
                make_keyboard_selectable(
                    anchor_row,
                    lambda _, page=page, query=query: ui.navigate.to(state.page_href(page, extra_query=query)),
                    label=state.t("notes.open_anchor", target=anchor_text),
                )
            _body(note)
            evidence = note.get("evidence") or []
            if evidence:
                with ui.column().classes("nt-evidence gap-0 w-full"):
                    for record in evidence:
                        result = notes.evidence_state(record)
                        with ui.row().classes("nt-evidence-row w-full"):
                            ui.label(Path(str(record.get("path") or "")).name).classes("nt-evidence-name").tooltip(
                                str(record.get("path") or "")
                            )
                            ui.label(state.t(f"notes.evidence.{result}")).classes(
                                f"nt-evidence-state nt-evidence-{result}"
                            ).tooltip(state.t("notes.evidence_tip", sha=str(record.get("sha256") or "")[:12]))
            for item in thread["replies"]:
                with ui.column().classes("nt-reply gap-0 w-full"):
                    _author(item)
                    if item.get("choice"):
                        ui.label(state.t("notes.chose", choice=item["choice"])).classes("nt-choice")
                    _body(item)
            if note.get("note_type") == "question" and thread["awaiting_user"] and note.get("options"):
                with ui.row().classes("nt-options w-full"):
                    for option in note["options"]:
                        ui.button(
                            option,
                            on_click=lambda option=option: act(lambda: notes.user_reply(root, root_id, choice=option)),
                        ).props("outline dense no-caps").classes("nt-option")
            if note.get("note_type") == "approval_request" and target and not thread["resolved"]:
                page, query = target
                ui.button(
                    state.t("notes.review_plan"),
                    icon="o_verified",
                    on_click=lambda page=page, query=query: ui.navigate.to(state.page_href(page, extra_query=query)),
                ).props("unelevated dense no-caps color=primary").classes("self-start")
            last_user = next(
                (item for item in reversed([note, *thread["replies"]]) if item["author"]["type"] == "user"), None
            )
            with ui.row().classes("nt-card-foot w-full"):
                if last_user is not None:
                    ui.label(
                        state.t("notes.read_by_agent" if thread["read_by_agent"] else "notes.unread_by_agent")
                    ).classes("nt-read")
                ui.element("div").classes("ov-spacer")
                ui.button(
                    state.t("notes.reopen" if thread["resolved"] else "notes.resolve"),
                    on_click=lambda resolved=not thread["resolved"]: act(
                        lambda: notes.set_resolved(root, root_id, resolved)
                    ),
                ).props("flat dense no-caps").classes("ov-link-button")
            if not thread["resolved"]:
                # Drafts live outside the refreshable so polling, filters and other threads keep them.
                drafts = view.setdefault("reply_drafts", {})

                def send_reply(thread_id=root_id) -> None:
                    text = str(drafts.get(thread_id) or "").strip()
                    if not text:
                        return
                    # Clear before the refresh that act() triggers; put it back if sending failed.
                    drafts.pop(thread_id, None)
                    if not act(lambda: notes.user_reply(root, thread_id, body=text)):
                        drafts[thread_id] = text

                with ui.row().classes("nt-reply-box w-full"):
                    ui.input(
                        value=drafts.get(root_id, ""),
                        placeholder=state.t("notes.reply_placeholder"),
                        on_change=lambda e, thread_id=root_id: drafts.__setitem__(thread_id, e.value or ""),
                    ).props("dense outlined hide-bottom-space").classes("nt-reply-input").on(
                        "keydown.enter", lambda send=send_reply: send()
                    )
                    ui.button(icon="o_send", on_click=lambda send=send_reply: send()).props(
                        f'flat dense round aria-label="{state.t("notes.send_reply")}"'
                    )

    with ui.dialog().props("position=right full-height") as dialog, ui.card().classes("nt-panel"):
        panel_body()
    badge()
    view["mtime"] = store_mtime()
    ui.timer(POLL_SECONDS, poll)
