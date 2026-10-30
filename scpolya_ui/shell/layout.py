"""Top-level product shell and route layout."""

from __future__ import annotations

import json
from collections.abc import Callable
from itertools import groupby

from scpolya_ui.i18n import available_locales
from scpolya_ui.i18n.catalog import load_catalog
from scpolya_ui.services.workspace_service import default_workspace, get_workspace, workspace_options
from scpolya_ui.state.app_state import ProductAppState

from .navigation import NavigationItem, product_navigation, sidebar_navigation
from .theme import apply_product_theme

LOCALE_LABELS = {"en": "EN", "zh-CN": "中"}
HELP_TERMS = (
    "structured_status",
    "attempts",
    "variants",
    "run_kinds",
    "snapshot",
    "missing",
    "not_produced",
    "refresh_sync",
    "path_mapping",
    "rerun_continue",
    "output_actions",
    "terminal_aware",
    "compare_mode",
    "eligibility",
    "external_approval",
    "reference_only",
)
# Terms shown first on each page; the rest follow under "all terms".
PAGE_HELP_TERMS = {
    "workspaces": ("path_mapping", "run_kinds"),
    "runs": ("attempts", "variants", "run_kinds", "not_produced", "missing", "refresh_sync", "rerun_continue"),
    "run_setup": ("rerun_continue", "output_actions", "terminal_aware"),
    "parameter_studies": ("variants", "attempts"),
    "igv_compare": ("compare_mode", "not_produced"),
    "site_scores": ("reference_only",),
    "differential_apa": ("eligibility", "external_approval", "reference_only"),
    "settings": ("path_mapping", "reference_only"),
}


def navigation_items() -> tuple[NavigationItem, ...]:
    return product_navigation()


def render_product_shell(
    state: ProductAppState,
    render_page: Callable[[ProductAppState], None],
) -> None:
    from nicegui import ui

    from scpolya_ui.components.onboarding import render_onboarding
    from scpolya_ui.services.preferences_service import load_preferences

    apply_product_theme()
    _ensure_selected_workspace(state)
    preferences = load_preferences()
    open_guide = render_onboarding(state, auto_open=not preferences.onboarding_done)
    workspace = _current_workspace(state)
    workspace_select_options = _workspace_select_options(state)
    with ui.element("div").classes(f"product-root product-route-{state.active_page}"):
        ui.link(state.t("shell.skip_to_content"), "#product-main-content").classes("product-skip-link")
        with ui.element("div").classes("product-shell"):
            with ui.column().classes("product-sidebar"):
                with ui.row().classes("product-sidebar-brand w-full"):
                    ui.label("p").classes("product-brand-mark")
                    ui.label("scpolyA").classes("product-brand-wordmark")
                with ui.column().classes("product-workspace-card w-full"):
                    ui.label(state.t("shell.workspace")).classes("product-workspace-card-label")
                    if workspace_select_options:
                        ui.select(
                            options=workspace_select_options,
                            value=state.selected_workspace_id,
                            on_change=lambda e: ui.navigate.to(
                                state.page_href(state.active_page, workspace_id=e.value)
                            ),
                        ).classes("product-workspace-select w-full").props(
                            "dense borderless options-dense hide-bottom-space behavior=menu"
                        )
                    else:
                        ui.label(state.t("workspace.select_first")).classes("product-workspace-card-empty")
                with ui.column().classes("product-sidebar-nav w-full"):
                    for section_key, items in _navigation_groups():
                        with ui.column().classes("product-nav-group w-full"):
                            ui.label(state.t(section_key)).classes("product-nav-section-label")
                            for item in items:
                                active = item.page == state.active_page
                                label = state.t(item.label_key)
                                button = ui.button(
                                    label,
                                    icon=item.icon,
                                    on_click=lambda page=item.page: ui.navigate.to(state.page_href(page)),
                                ).props("flat no-caps")
                                classes = "product-nav-button product-nav-active" if active else "product-nav-button"
                                button.classes(classes)
                with ui.column().classes("product-sidebar-footer w-full"):
                    help_button = ui.button(
                        state.t("shell.help"),
                        icon="o_help_outline",
                        on_click=lambda: ui.run_javascript("window.scpolyaToggleHelp && window.scpolyaToggleHelp(true)"),
                    ).props("flat no-caps").classes("product-nav-button")
                    help_button.tooltip(state.t("shell.help_shortcut"))
                    ui.button(
                        state.t("onboarding.reopen"),
                        icon="o_flag",
                        on_click=open_guide,
                    ).props("flat no-caps").classes("product-nav-button").tooltip(state.t("onboarding.reopen"))

            with ui.column().classes("product-main"):
                with ui.row().classes("product-topbar w-full"):
                    with ui.row().classes("product-topbar-context"):
                        crumb = ui.label(
                            (workspace.alias or workspace.root_path) if workspace is not None else "scpolyA"
                        ).classes("product-crumb product-crumb-link")
                        crumb.on("click", lambda: ui.navigate.to(state.page_href("workspaces")))
                        ui.label("/").classes("product-crumb-sep")
                        ui.label(state.t(_page_title_key(state.active_page))).classes("product-crumb-current")
                    with ui.row().classes("product-topbar-controls"):
                        if preferences.agent_mode:
                            from scpolya_ui.components.annotations_panel import render_notes_button

                            render_notes_button(state, workspace)
                        def change_locale(event) -> None:
                            if event.value == state.locale:
                                return
                            ui.navigate.to(
                                state.page_href(state.active_page, locale=event.value, extra_query=_view_query(state))
                            )
                            # If the user stays (unsaved-changes prompt), show the language still in use.
                            ui.timer(1.5, lambda: locale_toggle.set_value(state.locale), once=True)

                        locale_toggle = ui.toggle(
                            {locale: LOCALE_LABELS.get(locale, locale) for locale in available_locales()},
                            value=state.locale,
                            on_change=change_locale,
                        ).classes("product-locale-toggle").props(
                            f'dense no-caps unelevated role="group" aria-label="{state.t("shell.language")}"'
                        )
                        theme_button = ui.button(
                            icon="light_mode",
                            on_click=lambda: ui.run_javascript("window.scpolyaToggleTheme && window.scpolyaToggleTheme()"),
                        ).classes("product-topbar-icon product-theme-toggle").props("flat dense")
                        theme_button.tooltip(state.t("shell.theme_toggle"))
                        ui.run_javascript(
                            "window.scpolyaThemeLabels = "
                            + json.dumps({"toLight": state.t("shell.theme_to_light"), "toDark": state.t("shell.theme_to_dark")})
                            + "; window.scpolyaSyncThemeControls && window.scpolyaSyncThemeControls();"
                        )
                with ui.element("main").classes(f"product-page product-page-{state.active_page}").props(
                    'id="product-main-content" tabindex="-1"'
                ):
                    with ui.column().classes("product-page-heading"):
                        ui.label(state.t(_page_title_key(state.active_page))).classes("product-page-title")
                        subtitle_key = f"page.subtitle.{state.active_page}"
                        if subtitle_key in load_catalog("en"):
                            ui.label(state.t(subtitle_key)).classes("product-page-subtitle")
                    with ui.element("div").classes(
                        f"product-page-content product-page-content-{state.active_page}"
                    ):
                        render_page(state)
        _render_help_drawer(state)


def _help_term(state: ProductAppState, term: str) -> None:
    from nicegui import ui

    with ui.column().classes("product-help-term w-full"):
        ui.label(state.t(f"help.term.{term}")).classes("product-help-term-title")
        ui.label(state.t(f"help.term.{term}.detail")).classes("product-help-term-detail")


def _render_help_drawer(state: ProductAppState) -> None:
    from nicegui import ui

    ui.element("div").classes("product-help-scrim").props('onclick="window.scpolyaToggleHelp(false)"')
    # Starts inert so the closed drawer is out of the Tab order; the toggle script flips it.
    with ui.element("aside").classes("product-help-drawer").props(f'aria-label="{state.t("shell.help")}" inert'):
        with ui.row().classes("product-help-header w-full"):
            ui.label(state.t("shell.help")).classes("product-help-title")
            ui.button(
                icon="close",
                on_click=lambda: ui.run_javascript("window.scpolyaToggleHelp(false)"),
            ).props(f'flat dense round aria-label="{state.t("common.close")}"')
        with ui.column().classes("product-help-body w-full"):
            ui.label(state.t("help.intro")).classes("product-help-intro")
            page_terms = PAGE_HELP_TERMS.get(state.active_page, ())
            if page_terms:
                ui.label(state.t("help.page_terms")).classes("product-help-section")
            for term in page_terms:
                _help_term(state, term)
            if page_terms:
                ui.label(state.t("help.all_terms")).classes("product-help-section")
            for term in HELP_TERMS:
                if term not in page_terms:
                    _help_term(state, term)
            ui.label(state.t("help.shortcuts")).classes("product-help-section")
            with ui.row().classes("product-help-shortcut w-full"):
                ui.label("?").classes("product-kbd")
                ui.label(state.t("help.shortcut.help"))
            with ui.row().classes("product-help-shortcut w-full"):
                ui.label("Esc").classes("product-kbd")
                ui.label(state.t("help.shortcut.close"))


def render_pending_page(state: ProductAppState) -> None:
    from nicegui import ui

    with ui.column().classes("product-panel gap-3"):
        ui.label(state.t(_page_title_key(state.active_page))).classes("product-section-title")
        ui.label(state.t("page.pending")).classes("product-small-muted")
        ui.button(
            state.t("common.open_workspaces"),
            icon="folder_open",
            on_click=lambda: ui.navigate.to(state.page_href("workspaces")),
        ).props("unelevated no-caps color=primary")


def _navigation_groups() -> tuple[tuple[str, tuple[NavigationItem, ...]], ...]:
    return tuple(
        (section_key, tuple(items))
        for section_key, items in groupby(sidebar_navigation(), key=lambda item: item.section_key)
    )


def _page_title_key(page: str) -> str:
    for item in navigation_items():
        if item.page == page:
            return item.label_key
    return "app.title"


def _ensure_selected_workspace(state: ProductAppState) -> None:
    if state.registries is None:
        return
    if get_workspace(state.registries.workspaces, state.selected_workspace_id) is not None:
        return
    workspace = default_workspace(state.registries.workspaces)
    state.selected_workspace_id = workspace.id if workspace is not None else None


def _current_workspace(state: ProductAppState):
    if state.registries is None:
        return None
    return get_workspace(state.registries.workspaces, state.selected_workspace_id)


def _workspace_select_options(state: ProductAppState) -> dict[str, str]:
    if state.registries is None:
        return {}
    return {item.id: item.label for item in workspace_options(state.registries.workspaces)}


def _view_query(state: ProductAppState) -> dict[str, str]:
    """Selection to keep when the page is reloaded in another language."""
    payload = state.payload
    if state.active_page == "runs":
        return {"run_record": payload.get("selected_run_record_id") or "", "tab": payload.get("runs_detail_tab") or ""}
    if state.active_page == "parameter_studies":
        return {"study": payload.get("selected_study_id") or ""}
    if state.active_page == "differential_apa":
        return {"analysis": payload.get("differential_analysis") or ""}
    if state.active_page == "igv_compare":
        return igv_view_query(payload)
    return {}


def igv_view_query(payload: dict) -> dict[str, str]:
    """Genome browser selection for links, reload and language switch."""
    return {
        "igv_source": payload.get("igv_source") or "",
        "study": payload.get("selected_study_id") or "",
        "run_record": payload.get("selected_run_record_id") or "",
        "gene": payload.get("igv_gene") or "",
    }
