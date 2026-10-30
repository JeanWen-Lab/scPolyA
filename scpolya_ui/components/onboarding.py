"""First-run guide: what scpolyA does, a workspace, the environment, agent mode."""

from __future__ import annotations

from typing import Any

from scpolya_ui.components.interaction import prop_text
from scpolya_ui.errors import error_text
from scpolya_ui.state.app_state import ProductAppState

STEPS = ("intro", "workspace", "environment", "agent")


def render_onboarding(state: ProductAppState, *, auto_open: bool):
    """Build the guide dialog; returns an ``open()`` callable."""
    from nicegui import ui

    from scpolya_ui.components.status import render_status
    from scpolya_ui.components.workspace_path_picker import WorkspacePathPicker
    from scpolya_ui.registry import find_state_dbs
    from scpolya_ui.services.environment_service import pipeline_tool_checks
    from scpolya_ui.services.preferences_service import load_preferences, update_preferences
    from scpolya_ui.services.workspace_service import register_workspace
    from scpolya_ui.state.persistence import load_product_registries

    guide: dict[str, Any] = {"step": 0, "goal": "results", "path": "", "alias": "", "registered": None, "agent": False}

    def open_guide() -> None:
        guide.update(step=0, path="", alias="", registered=None, agent=load_preferences().agent_mode)
        body.refresh()
        dialog.open()

    def finish(*, skipped: bool = False) -> None:
        update_preferences(onboarding_done=True, **({} if skipped else {"agent_mode": guide["agent"]}))
        dialog.close()
        if skipped:
            return
        workspace = guide["registered"]
        if workspace is not None:
            ui.navigate.to(state.page_href("workspaces", workspace_id=workspace.id))
        elif guide["goal"] == "new":
            ui.navigate.to(state.page_href("run_setup"))
        else:
            ui.navigate.to(state.page_href(state.active_page))

    def go(delta: int) -> None:
        if STEPS[guide["step"]] == "workspace" and delta > 0 and not register_if_needed():
            return
        guide["step"] = max(0, min(len(STEPS) - 1, guide["step"] + delta))
        body.refresh()

    def register_if_needed() -> bool:
        path = guide["path"].strip()
        if not path or (guide["registered"] is not None and guide["registered"].root_path == path):
            return True
        registries = state.registries or load_product_registries()
        try:
            workspace = register_workspace(registries.workspaces, path=path, alias=guide["alias"].strip() or None)
        except Exception as exc:
            ui.notify(error_text(state.t, exc), color="negative")
            return False
        state.registries = registries
        state.selected_workspace_id = workspace.id
        guide["registered"] = workspace
        return True

    def set_path(value: str) -> None:
        guide["path"] = value or ""
        found.refresh()

    picker = WorkspacePathPicker(translate=state.t, on_select=lambda path: (set_path(path), body.refresh()))

    @ui.refreshable
    def found() -> None:
        path = guide["path"].strip()
        if not path:
            return
        try:
            count = len(find_state_dbs(path))
        except OSError:
            count = 0
        if count:
            ui.label(state.t("onboarding.workspace.found", count=count)).classes("ob-hint ob-hint-good")
        else:
            ui.label(state.t("onboarding.workspace.not_found")).classes("ob-hint ob-hint-warn")

    def choice_group(label_key: str):
        return ui.element("div").classes("ob-choice-group").props(
            f"role=radiogroup aria-label={prop_text(state.t(label_key))}"
        )

    def choice(key: str, value: Any, title: str, detail: str, *, chip: str = "") -> None:
        active = guide[key] == value
        card = ui.element("div").classes("ob-choice" + (" ob-choice-on" if active else ""))
        card.props(
            f'role="radio" aria-checked="{str(active).lower()}" tabindex="{0 if active else -1}" '
            f"aria-label={prop_text(title)}"
        )
        card.on("click", lambda: (guide.__setitem__(key, value), body.refresh()))
        # Enter/Space pick this card; arrow keys move to the neighbouring card, as in a radio group.
        card.on(
            "keydown",
            lambda: (guide.__setitem__(key, value), body.refresh()),
            js_handler="""(event) => {
              const card = event.currentTarget;
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                window.scpolyaRememberFocus(card);
                emit();
                return;
              }
              const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[event.key];
              if (!step) return;
              event.preventDefault();
              const cards = [...card.parentElement.querySelectorAll(':scope > .ob-choice')];
              const next = cards[(cards.indexOf(card) + step + cards.length) % cards.length];
              if (next && next !== card) {
                window.scpolyaRememberFocus(next);
                next.click();
              }
            }""",
        )
        with card:
            ui.element("span").classes("ob-radio")
            with ui.column().classes("gap-0 min-w-0"):
                with ui.row().classes("ob-choice-title-row"):
                    ui.label(title).classes("ob-choice-title")
                    if chip:
                        ui.label(chip).classes("st-chip")
                ui.label(detail).classes("ob-choice-detail")

    @ui.refreshable
    def body() -> None:
        step = guide["step"]
        key = STEPS[step]
        with ui.element("div").classes("ob-layout"):
            with ui.column().classes("ob-rail gap-0"):
                ui.label(state.t("onboarding.welcome")).classes("ob-rail-title")
                ui.label(state.t("onboarding.welcome_detail")).classes("ob-rail-detail")
                for index, name in enumerate(STEPS):
                    tone = "on" if index == step else "ok" if index < step else ""
                    with ui.row().classes(f"ob-step ob-step-{tone}".strip()):
                        ui.label("✓" if index < step else str(index + 1)).classes("ob-step-dot")
                        ui.label(state.t(f"onboarding.{name}.title"))
            with ui.column().classes("ob-panel gap-3"):
                ui.label(state.t(f"onboarding.{key}.title")).classes("ob-title")
                ui.label(state.t(f"onboarding.{key}.detail")).classes("ob-detail")
                if key == "intro":
                    with choice_group(f"onboarding.{key}.choice_label"):
                        choice("goal", "results", state.t("onboarding.intro.results"), state.t("onboarding.intro.results_detail"))
                        choice("goal", "new", state.t("onboarding.intro.new"), state.t("onboarding.intro.new_detail"))
                elif key == "workspace":
                    if guide["goal"] == "new":
                        ui.label(state.t("onboarding.workspace.new_note")).classes("env-note")
                    with ui.row().classes("df-path-row w-full"):
                        ui.input(
                            state.t("onboarding.workspace.path"),
                            value=guide["path"],
                            on_change=lambda e: set_path(e.value),
                        ).classes("df-path-input df-mono-input").props("dense outlined hide-bottom-space")
                        ui.button(
                            state.t("workspace.browse"),
                            icon="folder_open",
                            on_click=lambda: picker.open(guide["path"]),
                        ).props("outline no-caps")
                    found()
                    ui.input(
                        state.t("onboarding.workspace.alias"),
                        value=guide["alias"],
                        on_change=lambda e: guide.__setitem__("alias", e.value or ""),
                    ).classes("w-full").props("dense outlined hide-bottom-space")
                    if guide["registered"] is not None:
                        ui.label(state.t("onboarding.workspace.registered")).classes("ob-hint ob-hint-good")
                    ui.label(state.t("onboarding.workspace.read_only")).classes("env-note")
                elif key == "environment":
                    checks = pipeline_tool_checks()
                    with ui.column().classes("ob-checks gap-0 w-full"):
                        for check in checks:
                            with ui.row().classes("ob-check w-full"):
                                render_status(state.t, "ready" if check.found else "missing")
                                ui.label(check.name).classes("ov-mono")
                                ui.label(check.path or state.t("onboarding.environment.not_on_path")).classes(
                                    "ob-check-path"
                                )
                    missing = sum(1 for check in checks if not check.found)
                    ui.label(
                        state.t("onboarding.environment.missing", count=missing)
                        if missing
                        else state.t("onboarding.environment.all_found")
                    ).classes("env-note")
                    ui.button(
                        state.t("onboarding.environment.open"),
                        icon="o_fact_check",
                        on_click=lambda: (finish(skipped=True), ui.navigate.to(state.page_href("settings"))),
                    ).props("flat dense no-caps").classes("ov-link-button self-start")
                else:
                    with choice_group(f"onboarding.{key}.choice_label"):
                        choice("agent", False, state.t("onboarding.agent.off"), state.t("onboarding.agent.off_detail"))
                        choice(
                            "agent",
                            True,
                            state.t("onboarding.agent.on"),
                            state.t("onboarding.agent.on_detail"),
                            chip=state.t("onboarding.agent.experimental"),
                        )
                ui.element("div").classes("ov-spacer")
                with ui.row().classes("ob-actions w-full"):
                    if step == 0:
                        ui.button(state.t("onboarding.skip"), on_click=lambda: finish(skipped=True)).props(
                            "flat no-caps"
                        )
                    else:
                        ui.button(state.t("onboarding.back"), on_click=lambda: go(-1)).props("flat no-caps")
                    ui.element("div").classes("ov-spacer")
                    if step < len(STEPS) - 1:
                        ui.button(state.t("onboarding.next"), on_click=lambda: go(1)).props(
                            "unelevated no-caps color=primary"
                        )
                    else:
                        ui.button(state.t("onboarding.finish"), on_click=lambda: finish()).props(
                            "unelevated no-caps color=primary"
                        )

    def dismissed() -> None:
        # Escape or a click outside counts as "seen"; the sidebar button reopens the guide.
        if not load_preferences().onboarding_done:
            update_preferences(onboarding_done=True)

    with ui.dialog() as dialog, ui.card().classes("ob-dialog"):
        body()
    dialog.on("hide", dismissed)
    if auto_open:
        ui.timer(0.4, open_guide, once=True)
    return open_guide
