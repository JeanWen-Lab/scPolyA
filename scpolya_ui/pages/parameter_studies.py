from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from scpolya_ui.errors import error_text
from scpolya_ui.components.terminal import render_terminal
from scpolya_ui.components.confirm_dialog import open_confirmation_dialog
from scpolya_ui.components.interaction import make_keyboard_selectable
from scpolya_ui.components.status import render_status, run_kind_text, status_text
from scpolya_ui.components.timefmt import short_timestamp
from scpolya_ui.services.run_service import load_run_summary
from scpolya_ui.services.run_service import get_run_record, run_record_summaries
from scpolya_ui.services.study_service import (
    advance_study_queue,
    build_study_preview_bundle,
    get_study,
    save_study_draft,
    stop_study_queue,
    study_annotation_schema_notices,
    study_summaries,
    study_variant_summaries,
)
from scpolya_ui.services.workspace_service import default_workspace, get_workspace
from scpolya_ui.state.app_state import ProductAppState
from scpolya_ui.state.persistence import load_product_registries
from task_manage.parameter_studies import expand_parameter_grid
from scpolya_ui.config_schema import inactive_sweep_keys
from scpolya_ui.sweep_builder import (
    SweepRow,
    default_sweep_rows,
    grid_from_sweep_rows,
    parameter_display_label,
    rows_from_grid,
    sweep_parameter_options,
)


PAGE_KEY = "parameter_studies"
REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_BASE_SOURCE = "__workspace_state__"


def render_parameter_studies_page(state: ProductAppState) -> None:
    from nicegui import ui

    registries = state.registries
    if registries is None:
        registries = load_product_registries()
        state.registries = registries

    first_workspace = default_workspace(registries.workspaces)
    page_state: dict[str, Any] = {
        "registries": registries,
        "workspace_id": state.selected_workspace_id or (first_workspace.id if first_workspace else None),
        "base_source_id": WORKSPACE_BASE_SOURCE,
        "study_id": state.payload.get("selected_study_id"),
        "study_name": state.payload.get("study_name") or state.t("study.default_name"),
        "sweep_rows": default_sweep_rows(),
        "preview_bundle": None,
        "error": "",
        "queue_result": None,
        "builder_dirty": True,
        "view": "build",
        "unsaved_draft": None,
    }
    refs: dict[str, Any] = {}

    def current_workspace():
        return get_workspace(page_state["registries"].workspaces, page_state["workspace_id"])

    def current_base_config() -> dict[str, Any]:
        config, _db_path, _label = current_base_context()
        return config

    def current_base_context() -> tuple[dict[str, Any], str, str]:
        workspace = current_workspace()
        if workspace is None:
            return {}, "", ""
        source_options = base_source_options()
        if page_state["base_source_id"] not in source_options:
            page_state["base_source_id"] = WORKSPACE_BASE_SOURCE
        if page_state["base_source_id"] == WORKSPACE_BASE_SOURCE:
            summary = load_run_summary(workspace.db_path)
            return dict(summary.config or {}), workspace.db_path, state.t("study.base_workspace")
        record = get_run_record(page_state["registries"].run_records, page_state["base_source_id"])
        if record is None:
            summary = load_run_summary(workspace.db_path)
            return dict(summary.config or {}), workspace.db_path, state.t("study.base_workspace")
        return dict(record.config or {}), record.db_path or workspace.db_path, record.label

    def base_source_options() -> dict[str, str]:
        options = {WORKSPACE_BASE_SOURCE: state.t("study.base_workspace")}
        for record in run_record_summaries(
            page_state["registries"].run_records,
            workspace_id=page_state["workspace_id"],
            limit=50,
        ):
            options[record.id] = state.t(
                "study.base_run_option",
                label=record.label,
                kind=run_kind_text(state.t, record.kind),
                status=status_text(state.t, record.status),
            )
        return options

    # After the helpers above: the fallback grid reads the base config.
    initial_study = get_study(registries.studies, page_state["study_id"])
    if initial_study is None:
        page_state["study_id"] = None
    else:
        page_state["view"] = "saved"
        page_state["study_name"] = initial_study.name
        page_state["base_source_id"] = initial_study.base_run_record_id or WORKSPACE_BASE_SOURCE
        page_state["sweep_rows"] = rows_from_grid(initial_study.grid) or default_sweep_rows(current_base_config())
        page_state["builder_dirty"] = False

    def current_study():
        return get_study(page_state["registries"].studies, page_state["study_id"])

    def current_sweep_rows() -> list[SweepRow]:
        rows: list[SweepRow] = []
        for index, fallback in enumerate(page_state["sweep_rows"]):
            enabled = bool(getattr(refs.get(f"enabled_{index}"), "value", fallback.enabled))
            key = str(getattr(refs.get(f"key_{index}"), "value", fallback.key) or "").strip()
            values_text = str(getattr(refs.get(f"values_{index}"), "value", fallback.values_text) or "")
            rows.append(SweepRow(key=key, values_text=values_text, enabled=enabled))
        return rows

    def capture_builder_inputs() -> None:
        page_state["sweep_rows"] = current_sweep_rows()
        page_state["study_name"] = str(
            getattr(refs.get("study_name"), "value", page_state["study_name"])
            or page_state["study_name"]
        )

    def select_view(view: str | None) -> None:
        if page_state["view"] == "build" and refs.get("study_name") is not None:
            capture_builder_inputs()
        page_state["view"] = str(view or "build")
        studies_content.refresh()

    def mark_builder_dirty() -> None:
        page_state["builder_dirty"] = True
        page_state["preview_bundle"] = None
        page_state["error"] = ""
        page_state["queue_result"] = None
        indicator = refs.get("builder_dirty_indicator")
        if indicator is not None:
            indicator.set_visibility(True)

    def select_workspace(workspace_id: str | None) -> None:
        page_state["workspace_id"] = workspace_id
        state.selected_workspace_id = workspace_id
        page_state["base_source_id"] = WORKSPACE_BASE_SOURCE
        page_state["study_id"] = None
        page_state["preview_bundle"] = None
        page_state["queue_result"] = None
        page_state["builder_dirty"] = True
        studies_content.refresh()

    def reload_registries() -> None:
        page_state["registries"] = load_product_registries()
        state.registries = page_state["registries"]
        if current_workspace() is None:
            fallback = default_workspace(page_state["registries"].workspaces)
            page_state["workspace_id"] = fallback.id if fallback else None
        studies_content.refresh()

    def select_base_source(source_id: str | None) -> None:
        capture_builder_inputs()
        page_state["base_source_id"] = source_id or WORKSPACE_BASE_SOURCE
        page_state["study_id"] = None
        page_state["preview_bundle"] = None
        page_state["builder_dirty"] = True
        studies_content.refresh()

    def add_sweep_row() -> None:
        rows = current_sweep_rows()
        used = {str(row.key) for row in rows}
        if "merge_distance" not in used:
            new_row = SweepRow("merge_distance", "0, 50")
        else:
            # Never default to a key already in the grid; the user fills in values.
            key = next((key for key in sweep_parameter_options() if key not in used), "merge_distance")
            new_row = SweepRow(key, "")
        page_state["sweep_rows"] = rows + [new_row]
        page_state["study_id"] = None
        page_state["preview_bundle"] = None
        page_state["builder_dirty"] = True
        studies_content.refresh()

    def remove_sweep_row(index: int) -> None:
        rows = current_sweep_rows()
        if len(rows) <= 1:
            return
        rows.pop(index)
        page_state["sweep_rows"] = rows
        page_state["study_id"] = None
        page_state["preview_bundle"] = None
        page_state["builder_dirty"] = True
        studies_content.refresh()

    def build_current_preview(*, notify: bool) -> Any:
        workspace = current_workspace()
        if workspace is None:
            ui.notify(state.t("workspace.select_first"), color="warning")
            return None
        try:
            rows = current_sweep_rows()
            capture_builder_inputs()
            grid = grid_from_sweep_rows(rows)
            base_config, base_db_path, _base_label = current_base_context()
            bundle = build_study_preview_bundle(
                base_db_path or workspace.db_path,
                grid,
                base_config=base_config,
                main_path=REPO_ROOT / "main.py",
            )
        except Exception as exc:
            page_state["preview_bundle"] = None
            page_state["error"] = error_text(state.t, exc)
            ui.notify(error_text(state.t, exc), color="negative")
            return None
        else:
            page_state["sweep_rows"] = rows
            page_state["preview_bundle"] = bundle
            page_state["error"] = ""
            if notify:
                ui.notify(state.t("study.preview_ready", count=bundle.preview.variant_count), color="positive")
            return bundle

    def preview_study() -> None:
        bundle = build_current_preview(notify=True)
        if bundle is not None and page_state["builder_dirty"]:
            page_state["study_id"] = None
            state.payload["selected_study_id"] = None
        studies_content.refresh()

    def save_draft() -> Any:
        workspace = current_workspace()
        if workspace is None:
            ui.notify(state.t("workspace.select_first"), color="warning")
            return None
        if page_state["preview_bundle"] is None or page_state["builder_dirty"]:
            build_current_preview(notify=False)
        bundle = page_state["preview_bundle"]
        if bundle is None:
            return None
        try:
            _base_config, _base_db_path, base_label = current_base_context()
            study = save_study_draft(
                page_state["registries"].studies,
                workspace=workspace,
                bundle=bundle,
                name=str(getattr(refs.get("study_name"), "value", page_state["study_name"]) or page_state["study_name"]),
                base_run_record_id="" if page_state["base_source_id"] == WORKSPACE_BASE_SOURCE else page_state["base_source_id"],
                base_run_label=base_label,
            )
        except Exception as exc:
            page_state["error"] = error_text(state.t, exc)
            ui.notify(error_text(state.t, exc), color="negative")
            return None
        page_state["study_id"] = study.id
        state.payload["selected_study_id"] = study.id
        page_state["study_name"] = study.name
        page_state["sweep_rows"] = rows_from_grid(study.grid) or page_state["sweep_rows"]
        page_state["builder_dirty"] = False
        page_state["unsaved_draft"] = None
        ui.notify(state.t("study.draft_saved"), color="positive")
        studies_content.refresh()
        return study

    def advance_selected_study(study_id: str) -> None:
        workspace = current_workspace()
        if workspace is None:
            ui.notify(state.t("workspace.select_first"), color="warning")
            return
        study = get_study(page_state["registries"].studies, study_id)
        if study is None:
            ui.notify(state.t("study.select_first"), color="warning")
            return
        try:
            result = advance_study_queue(
                page_state["registries"].jobs,
                page_state["registries"].run_records,
                page_state["registries"].studies,
                workspace=workspace,
                study=study,
                base_config=current_base_config(),
                base_run_record_id="" if page_state["base_source_id"] == WORKSPACE_BASE_SOURCE else page_state["base_source_id"],
                cwd=REPO_ROOT,
            )
        except Exception as exc:
            page_state["error"] = error_text(state.t, exc)
            ui.notify(error_text(state.t, exc), color="negative")
        else:
            page_state["queue_result"] = result
            ui.notify(state.t("study.queue_advanced"), color="positive")
        studies_content.refresh()

    def save_and_start_builder() -> None:
        study = save_draft()
        if study is not None:
            page_state["view"] = "saved"
            advance_selected_study(study.id)

    def request_start_builder() -> None:
        bundle = build_current_preview(notify=False)
        if bundle is None:
            studies_content.refresh()
            return
        open_confirmation_dialog(
            title=state.t("study.confirm_start_title"),
            message=state.t("study.confirm_start_message"),
            confirm_label=state.t("study.start_new"),
            cancel_label=state.t("common.cancel"),
            on_confirm=save_and_start_builder,
            details=(
                (state.t("study.name"), page_state["study_name"]),
                (state.t("study.metric.variants"), bundle.preview.variant_count),
                (state.t("study.base_run"), current_base_context()[2]),
                *(
                    (state.t("run_setup.annotation_schema_title"), notice)
                    for notice in study_annotation_schema_notices(bundle)
                ),
            ),
            icon="play_arrow",
        )

    def request_continue_selected() -> None:
        study = current_study()
        if study is None:
            ui.notify(state.t("study.select_first"), color="warning")
            return
        open_confirmation_dialog(
            title=state.t("study.confirm_continue_title"),
            message=state.t("study.confirm_continue_message"),
            confirm_label=state.t("study.continue_selected"),
            cancel_label=state.t("common.cancel"),
            on_confirm=lambda: advance_selected_study(study.id),
            details=(
                (state.t("study.name"), study.name),
                (state.t("study.metric.variants"), len(study.variants)),
                (state.t("study.metric.status"), study.status),
            ),
            icon="play_arrow",
        )

    def stop_selected_study(study_id: str) -> None:
        study = get_study(page_state["registries"].studies, study_id)
        if study is None:
            ui.notify(state.t("study.select_first"), color="warning")
            return
        try:
            result = stop_study_queue(
                page_state["registries"].jobs,
                page_state["registries"].run_records,
                page_state["registries"].studies,
                study=study,
            )
        except Exception as exc:
            page_state["error"] = error_text(state.t, exc)
            ui.notify(error_text(state.t, exc), color="negative")
        else:
            page_state["queue_result"] = result
            ui.notify(state.t("study.queue_stopped"), color="warning")
        studies_content.refresh()

    def request_stop_selected() -> None:
        study = current_study()
        if study is None:
            ui.notify(state.t("study.select_first"), color="warning")
            return
        open_confirmation_dialog(
            title=state.t("study.confirm_stop_title"),
            message=state.t("study.confirm_stop_message"),
            confirm_label=state.t("study.stop"),
            cancel_label=state.t("common.cancel"),
            on_confirm=lambda: stop_selected_study(study.id),
            details=(
                (state.t("study.name"), study.name),
                (state.t("study.metric.status"), study.status),
            ),
            icon="stop",
            danger=True,
        )

    def stash_unsaved_draft() -> None:
        """Keep an edited, never-saved draft so switching studies does not lose it."""
        if page_state["view"] != "build" or page_state["study_id"] is not None:
            return
        if refs.get("study_name") is not None:
            capture_builder_inputs()
        draft = {
            "study_name": page_state["study_name"],
            "sweep_rows": list(page_state["sweep_rows"]),
            "base_source_id": page_state["base_source_id"],
        }
        pristine = (
            draft["study_name"] == state.t("study.default_name")
            and draft["sweep_rows"] == default_sweep_rows(current_base_config())
            and draft["base_source_id"] == WORKSPACE_BASE_SOURCE
        )
        page_state["unsaved_draft"] = None if pristine else draft

    def resume_unsaved_draft() -> None:
        draft = page_state["unsaved_draft"]
        if draft is None:
            start_new_builder()
            return
        page_state.update(
            study_id=None,
            preview_bundle=None,
            error="",
            queue_result=None,
            builder_dirty=True,
            view="build",
            **draft,
        )
        state.payload["selected_study_id"] = None
        studies_content.refresh()

    def discard_unsaved_draft() -> None:
        page_state["unsaved_draft"] = None
        start_new_builder()

    def select_study(study_id: str) -> None:
        study = get_study(page_state["registries"].studies, study_id)
        if study is None:
            return
        stash_unsaved_draft()
        page_state["study_id"] = study.id
        state.payload["selected_study_id"] = study.id
        page_state["study_name"] = study.name
        page_state["sweep_rows"] = rows_from_grid(study.grid) or default_sweep_rows(current_base_config())
        page_state["base_source_id"] = study.base_run_record_id or WORKSPACE_BASE_SOURCE
        page_state["preview_bundle"] = None
        page_state["builder_dirty"] = False
        page_state["view"] = "saved"
        studies_content.refresh()

    def edit_selected_study() -> None:
        study = current_study()
        if study is None:
            ui.notify(state.t("study.select_first"), color="warning")
            return
        # Saved studies are fixed records of what ran, so "edit" starts a new draft
        # from this definition; saving it creates a separate study.
        page_state["study_id"] = None
        state.payload["selected_study_id"] = None
        page_state["study_name"] = state.t("study.copy_name", name=study.name)
        page_state["sweep_rows"] = rows_from_grid(study.grid) or default_sweep_rows(current_base_config())
        page_state["base_source_id"] = study.base_run_record_id or WORKSPACE_BASE_SOURCE
        page_state["preview_bundle"] = None
        page_state["builder_dirty"] = True
        page_state["view"] = "build"
        studies_content.refresh()

    def start_new_builder() -> None:
        page_state.update(
            study_id=None,
            study_name=state.t("study.default_name"),
            sweep_rows=default_sweep_rows(current_base_config()),
            base_source_id=WORKSPACE_BASE_SOURCE,
            preview_bundle=None,
            error="",
            queue_result=None,
            builder_dirty=True,
            view="build",
        )
        state.payload["selected_study_id"] = None
        studies_content.refresh()

    def param_label(key: str) -> str:
        catalog_key = f"config.field.{key}.label"
        label = state.t(catalog_key)
        return parameter_display_label(key) if label == f"[[{catalog_key}]]" else label

    def param_options() -> dict[str, str]:
        return {key: param_label(key) for key in sweep_parameter_options()}

    def on_builder_change() -> None:
        mark_builder_dirty()
        variants_content.refresh()

    def current_grid() -> tuple[dict[str, list[Any]], str]:
        try:
            return grid_from_sweep_rows(current_sweep_rows()), ""
        except Exception as exc:
            return {}, error_text(state.t, exc)

    def open_run_record(record_id: str) -> None:
        if not record_id:
            return
        ui.navigate.to(state.page_href("runs", extra_query={"run_record": record_id}))

    @ui.refreshable
    def studies_content() -> None:
        workspace = current_workspace()
        study_rows = study_summaries(
            page_state["registries"].studies,
            workspace_id=page_state["workspace_id"],
            limit=40,
        )
        study_options = {item.id for item in study_rows}
        if page_state["study_id"] not in study_options:
            page_state["study_id"] = None
        if page_state["view"] == "saved" and page_state["study_id"] is None:
            if study_rows:
                page_state["study_id"] = study_rows[0].id
                state.payload["selected_study_id"] = page_state["study_id"]
            else:
                page_state["view"] = "build"
        selected_study = current_study()

        with ui.element("div").classes("st-page w-full"):
            with ui.row().classes("st-header w-full"):
                with ui.column().classes("st-heading gap-0"):
                    ui.label(state.t("nav.parameter_studies")).classes("st-title")
                    ui.label(state.t("page.subtitle.parameter_studies")).classes("st-subtitle")
                with ui.row().classes("st-header-actions"):
                    refresh = ui.button(icon="refresh", on_click=reload_registries).props(
                        f'flat dense aria-label="{state.t("common.refresh")}"'
                    ).classes("st-icon-button")
                    refresh.tooltip(state.t("common.refresh"))
                    ui.button(
                        state.t("study.new_definition"),
                        icon="add",
                        on_click=resume_unsaved_draft,
                    ).props("unelevated no-caps color=primary")

            if workspace is None:
                with ui.column().classes("st-empty gap-1"):
                    ui.label(state.t("workspace.empty")).classes("st-empty-title")
                    ui.label(state.t("workspace.empty_detail")).classes("st-muted")
                return

            _render_study_cards(study_rows, selected_study)
            if page_state["view"] == "saved" and selected_study is not None:
                _render_saved_detail(selected_study, study_rows)
            else:
                _base_config, _base_db_path, base_label = current_base_context()
                _render_builder_layout(base_label)

    def _render_study_cards(study_rows, selected_study) -> None:
        building_new = page_state["view"] == "build" and page_state["study_id"] is None
        stashed = None if building_new else page_state["unsaved_draft"]
        if not study_rows and not building_new and stashed is None:
            return
        with ui.element("div").classes("st-cards w-full"):
            if building_new:
                grid, _error = current_grid()
                count = len(expand_parameter_grid(grid)) if grid else 0
                with ui.column().classes("st-card st-card-active gap-0"):
                    with ui.row().classes("st-card-top w-full"):
                        ui.label(page_state["study_name"]).classes("st-card-name")
                        render_status(state.t, "draft", classes="st-card-status")
                    ui.label(state.t("study.card_unsaved", count=count)).classes("st-card-meta")
            elif stashed is not None:
                try:
                    count = len(expand_parameter_grid(grid_from_sweep_rows(stashed["sweep_rows"])))
                except Exception:
                    count = 0
                card = ui.column().classes("st-card st-card-stashed gap-0")
                make_keyboard_selectable(card, lambda _: resume_unsaved_draft(), label=stashed["study_name"])
                with card:
                    with ui.row().classes("st-card-top w-full"):
                        ui.label(stashed["study_name"]).classes("st-card-name")
                        render_status(state.t, "draft", classes="st-card-status")
                    ui.label(state.t("study.card_unsaved", count=count)).classes("st-card-meta")
            for item in study_rows:
                active = (
                    selected_study is not None
                    and selected_study.id == item.id
                    and not building_new
                )
                card = ui.column().classes("st-card gap-0" + (" st-card-active" if active else ""))
                make_keyboard_selectable(
                    card,
                    lambda _, study_id=item.id: select_study(study_id),
                    label=item.name,
                )
                study = get_study(page_state["registries"].studies, item.id)
                with card:
                    with ui.row().classes("st-card-top w-full"):
                        ui.label(item.name).classes("st-card-name")
                        render_status(state.t, item.status, classes="st-card-status")
                    meta = [state.t("study.card_variants", count=item.variant_count)]
                    when = short_timestamp(getattr(study, "updated_at", "") if study else "")
                    if when != "-":
                        meta.append(when)
                    ui.label(" · ".join(meta)).classes("st-card-meta")

    # ---------- builder ----------
    def _render_builder_layout(base_label: str) -> None:
        with ui.element("div").classes("st-layout"):
            with ui.column().classes("st-side gap-0"):
                ui.label(state.t("study.swept_parameters")).classes("st-section-title")
                with ui.column().classes("st-panel gap-0 w-full"):
                    _render_builder_fields()
                    _render_sweep_rows()
                    ui.label(state.t("study.other_parameters", base=base_label or "-")).classes(
                        "st-hint"
                    )
            with ui.column().classes("st-main gap-0"):
                variants_content()

    def _render_builder_fields() -> None:
        with ui.column().classes("st-field gap-1 w-full"):
            ui.label(state.t("study.name")).classes("st-field-label")
            refs["study_name"] = ui.input(
                value=page_state["study_name"],
                on_change=lambda _: on_builder_change(),
            ).classes("w-full").props(
                f'dense outlined hide-bottom-space aria-label="{state.t("study.name")}"'
            )
        with ui.column().classes("st-field gap-1 w-full"):
            ui.label(state.t("study.base_run")).classes("st-field-label")
            ui.select(
                options=base_source_options(),
                value=page_state["base_source_id"],
                on_change=lambda e: select_base_source(e.value),
            ).classes("w-full").props(
                f'dense outlined options-dense hide-bottom-space aria-label="{state.t("study.base_run")}"'
            )

    def _render_sweep_rows() -> None:
        options = param_options()
        enabled_rows = [index for index, row in enumerate(page_state["sweep_rows"]) if row.enabled]
        axis_by_index = {}
        if len(enabled_rows) <= 2:
            for axis, index in zip(("rows", "columns"), enabled_rows):
                axis_by_index[index] = axis
        with ui.column().classes("st-params gap-0 w-full"):
            for index, row in enumerate(page_state["sweep_rows"]):
                with ui.column().classes("st-param gap-1 w-full" + ("" if row.enabled else " st-param-off")):
                    with ui.row().classes("st-param-top w-full"):
                        refs[f"enabled_{index}"] = ui.checkbox(
                            value=row.enabled,
                            on_change=lambda _: (capture_builder_inputs(), on_builder_change(), studies_content.refresh()),
                        ).props("dense")
                        refs[f"key_{index}"] = ui.select(
                            options,
                            value=row.key,
                            # Re-render so the key label and the inactive-parameter warning follow the choice.
                            on_change=lambda _: (capture_builder_inputs(), on_builder_change(), studies_content.refresh()),
                        ).classes("st-param-key").props(
                            f'dense borderless options-dense hide-bottom-space aria-label="{state.t("study.parameter")}"'
                        )
                        if index in axis_by_index:
                            ui.label(state.t(f"study.axis.{axis_by_index[index]}")).classes("st-axis-chip")
                        remove = ui.button(
                            icon="o_delete",
                            on_click=lambda i=index: remove_sweep_row(i),
                        ).props(f'flat dense size=sm aria-label="{state.t("common.remove")}"').classes(
                            "st-param-remove"
                        )
                        remove.tooltip(state.t("common.remove"))
                    ui.label(row.key).classes("st-param-code")
                    if row.enabled and inactive_sweep_keys(current_base_config(), [row.key]):
                        ui.label(state.t("study.param_inactive")).classes("st-param-inactive")
                    refs[f"values_{index}"] = ui.input(
                        value=row.values_text,
                        placeholder=state.t("study.values_placeholder"),
                        on_change=lambda _: on_builder_change(),
                    ).classes("st-param-values w-full").props(
                        f'dense outlined hide-bottom-space aria-label="{state.t("study.values")}"'
                    )
            ui.button(
                state.t("study.add_parameter"),
                icon="add",
                on_click=add_sweep_row,
            ).props("flat dense no-caps").classes("st-add-param")

    @ui.refreshable
    def variants_content() -> None:
        grid, grid_error = current_grid()
        bundle = page_state.get("preview_bundle")
        preview_ok = bundle is not None
        overrides = expand_parameter_grid(grid) if grid and not grid_error else ()
        cells = {}
        for index, override in enumerate(overrides, start=1):
            variant_id = f"V{index:03d}"
            preview_variant = None
            if preview_ok:
                preview_variant = next(
                    (item for item in bundle.preview.variants if item.id == variant_id),
                    None,
                )
            cells[_override_key(grid, override)] = (variant_id, override, preview_variant)

        sizes = " × ".join(str(len(values)) for values in grid.values())
        count_text = f"{sizes} = {len(overrides)}" if len(grid) > 1 else str(len(overrides))
        with ui.row().classes("st-section-title st-section-row w-full"):
            ui.label(state.t("study.variants"))
            if overrides:
                ui.label(count_text).classes("st-section-count")
        if grid_error:
            with ui.row().classes("st-callout st-callout-crit w-full"):
                ui.icon("o_error")
                ui.label(grid_error)
        elif not overrides:
            ui.label(state.t("study.no_parameters")).classes("st-muted")
        else:
            _render_matrix(grid, cells, _builder_cell)

        if page_state["error"] and not grid_error:
            with ui.row().classes("st-callout st-callout-crit w-full"):
                ui.icon("o_error")
                ui.label(page_state["error"])

        with ui.row().classes("st-actions w-full"):
            note = state.t("study.preview_current") if preview_ok else state.t("study.preview_needed")
            refs["builder_dirty_indicator"] = ui.label(note).classes(
                "st-preview-note" + ("" if preview_ok else " st-preview-note-stale")
            )
            ui.element("div").classes("st-spacer")
            if page_state["study_id"] is None and page_state["unsaved_draft"] is not None:
                ui.button(state.t("study.discard_draft"), on_click=discard_unsaved_draft).props(
                    "flat no-caps"
                ).classes("st-button")
            ui.button(
                state.t("study.preview_impact"),
                icon="o_visibility",
                on_click=preview_study,
            ).props("outline no-caps").classes("st-button")
            ui.button(
                state.t("study.save"),
                on_click=save_draft,
            ).props("outline no-caps").classes("st-button")
            ui.button(
                state.t("study.save_and_start", count=len(overrides)),
                icon="play_arrow",
                on_click=request_start_builder,
            ).props("unelevated no-caps color=primary").set_enabled(bool(overrides) and not grid_error)
        ui.label(state.t("study.sequential_note")).classes("st-hint st-hint-right")

    def _builder_cell(variant_id: str, override, preview_variant) -> None:
        if preview_variant is None:
            tone = ""
            detail = state.t("study.cell_unpreviewed")
        elif preview_variant.impacted_task_count:
            tone = " st-cell-work"
            detail = state.t("study.cell_impacted", count=preview_variant.impacted_task_count)
        else:
            tone = " st-cell-reuse"
            detail = state.t("study.cell_no_rerun")
        with ui.column().classes("st-cell gap-0" + tone):
            with ui.row().classes("st-cell-top w-full"):
                ui.label(variant_id).classes("st-cell-id")
                if preview_variant is not None:
                    inspect = ui.button(
                        icon="code",
                        on_click=lambda v=variant_id: _open_variant_command(v),
                    ).props(f'flat dense size=xs aria-label="{state.t("study.inspect_command")}"').classes(
                        "st-cell-button"
                    )
                    inspect.tooltip(state.t("study.inspect_command"))
            ui.label(detail).classes("st-cell-detail")

    # ---------- matrix ----------
    def _override_key(grid, override) -> tuple[str, ...]:
        return tuple(repr(override.get(key)) for key in grid)

    def _render_matrix(grid, cells, render_cell) -> None:
        keys = list(grid)
        if len(keys) == 1:
            key = keys[0]
            columns = len(grid[key])
            with ui.element("div").classes("st-matrix").style(
                f"grid-template-columns: 120px repeat({columns}, minmax(140px, 240px))"
            ):
                ui.element("div")
                with ui.row().classes("st-col-axis").style("grid-column: 2 / -1"):
                    ui.label(param_label(key)).classes("st-axis-name")
                    ui.label(key).classes("st-axis-key")
                ui.element("div")
                for value in grid[key]:
                    with ui.row().classes("st-matrix-head"):
                        ui.label(_render_value(value)).classes("st-axis-value")
                ui.element("div")
                for value in grid[key]:
                    entry = cells.get(_override_key(grid, {key: value}))
                    if entry:
                        render_cell(*entry)
                    else:
                        ui.element("div")
        elif len(keys) == 2:
            row_key, col_key = keys
            columns = len(grid[col_key])
            with ui.element("div").classes("st-matrix").style(
                f"grid-template-columns: 120px repeat({columns}, minmax(0, 1fr))"
            ):
                ui.element("div")
                with ui.row().classes("st-col-axis").style("grid-column: 2 / -1"):
                    ui.label(param_label(col_key)).classes("st-axis-name")
                    ui.label(col_key).classes("st-axis-key")
                with ui.column().classes("st-matrix-corner gap-0"):
                    ui.label(param_label(row_key)).classes("st-axis-name st-row-axis-name")
                    ui.label(f"{row_key} ↓").classes("st-axis-key")
                for col_value in grid[col_key]:
                    with ui.row().classes("st-matrix-head"):
                        ui.label(_render_value(col_value)).classes("st-axis-value")
                for row_value in grid[row_key]:
                    _axis_value(row_key, row_value)
                    for col_value in grid[col_key]:
                        entry = cells.get(_override_key(grid, {row_key: row_value, col_key: col_value}))
                        if entry:
                            render_cell(*entry)
                        else:
                            ui.element("div")
        else:
            with ui.element("div").classes("st-matrix").style(
                f"grid-template-columns: repeat({len(keys)}, minmax(90px, 0.6fr)) minmax(0, 1.4fr)"
            ):
                for key in keys:
                    with ui.row().classes("st-matrix-head"):
                        ui.label(param_label(key)).classes("st-matrix-head-key")
                ui.element("div")
                for entry in cells.values():
                    _variant_id, override, _extra = entry
                    for key in keys:
                        with ui.row().classes("st-matrix-axis"):
                            ui.label(_render_value(override.get(key))).classes("st-axis-value")
                    render_cell(*entry)

    def _axis_value(key: str, value) -> None:
        with ui.row().classes("st-matrix-axis"):
            ui.label(_render_value(value)).classes("st-axis-value")

    def _render_value(value) -> str:
        if value is None:
            return state.t("study.value_default")
        return str(value)

    # ---------- saved study ----------
    def _render_saved_detail(selected_study, study_rows) -> None:
        selected_summary = next((item for item in study_rows if item.id == selected_study.id), None)
        variants = study_variant_summaries(page_state["registries"].studies, selected_study.id)
        job_by_id = {job.id: job for job in page_state["registries"].jobs.jobs}
        planned = _planned_count(selected_study)
        running = any(
            job_by_id.get(variant.job_id) is not None
            and job_by_id[variant.job_id].status in {"starting", "running", "stopping"}
            for variant in variants
        )
        with ui.row().classes("st-detail-head w-full"):
            with ui.column().classes("gap-0 min-w-0"):
                with ui.row().classes("st-detail-title-row"):
                    ui.label(selected_study.name).classes("st-detail-title")
                    render_status(state.t, selected_study.status)
                with ui.row().classes("st-detail-chips"):
                    _chip(state.t("study.card_variants", count=len(selected_study.variants)))
                    _chip(
                        state.t(
                            "study.chip_ready",
                            ready=selected_summary.ready_variant_count if selected_summary else 0,
                            total=len(selected_study.variants),
                        )
                    )
                    if planned:
                        _chip(state.t("study.chip_planned", count=planned))
                    base_label = getattr(selected_study, "base_run_label", "") or state.t("study.base_workspace")
                    _chip(state.t("study.chip_base", base=base_label))
                    _chip(selected_study.id, mono=True)
            ui.element("div").classes("st-spacer")
            with ui.row().classes("st-detail-actions"):
                ui.button(
                    state.t("study.edit_definition"),
                    icon="o_content_copy",
                    on_click=edit_selected_study,
                ).props("outline no-caps").classes("st-button").tooltip(state.t("study.edit_definition_tip"))
                ui.button(
                    state.t("study.compare_in_genome"),
                    icon="o_ssid_chart",
                    on_click=lambda: ui.navigate.to(
                        state.page_href(
                            "igv_compare",
                            extra_query={"study": selected_study.id, "igv_source": "study"},
                        )
                    ),
                ).props("outline no-caps").classes("st-button")
                if running:
                    ui.button(
                        state.t("study.stop"),
                        icon="stop",
                        on_click=request_stop_selected,
                    ).props("outline no-caps color=negative").classes("st-button")
                if not planned:
                    ui.label(state.t("study.all_variants_run")).classes("st-preview-note")
                else:
                    # Disabled buttons swallow hover, so the reason sits on a wrapper.
                    with ui.element("span") as wrapper:
                        ui.button(
                            state.t("study.run_next"),
                            icon="play_arrow",
                            on_click=request_continue_selected,
                        ).props("unelevated no-caps color=primary").set_enabled(not running)
                    if running:
                        wrapper.tooltip(state.t("study.run_next_wait"))

        result = page_state.get("queue_result")
        if result is not None:
            with ui.row().classes("st-callout st-callout-info w-full"):
                ui.icon("o_info")
                ui.label(
                    state.t("study.queue_result", status=status_text(state.t, result.status), job=result.launched_job_id or "-")
                )

        grid = dict(selected_study.grid or {})
        variant_by_id = {variant.id: variant for variant in variants}
        cells = {}
        for record in selected_study.variants:
            cells[_override_key(grid, dict(record.overrides or {}))] = (
                record.id,
                dict(record.overrides or {}),
                (variant_by_id.get(record.id), job_by_id.get(record.job_id)),
            )
        ui.label(state.t("study.variants")).classes("st-section-title st-section-spaced")
        if grid and cells:
            _render_matrix(grid, cells, _saved_cell)
        else:
            ui.label(state.t("study.no_parameters")).classes("st-muted")
        if planned:
            ui.label(state.t("study.sequential_note")).classes("st-hint")

    def _saved_cell(variant_id: str, override, extra) -> None:
        summary, job = extra
        status = job.status if job is not None else "draft"
        record_id = summary.run_record_id if summary is not None else ""
        cell = ui.column().classes("st-cell gap-0" + (" st-cell-link" if record_id else ""))
        if record_id:
            make_keyboard_selectable(cell, lambda _, rid=record_id: open_run_record(rid), label=variant_id)
        with cell:
            with ui.row().classes("st-cell-top w-full"):
                ui.label(variant_id).classes("st-cell-id")
                render_status(state.t, status, classes="st-cell-status")
            parts = []
            if summary is not None:
                parts.append(
                    state.t("study.cell_impacted", count=summary.impacted_task_count)
                    if summary.impacted_task_count
                    else state.t("study.cell_no_rerun_short")
                )
                parts.append(
                    state.t(
                        "study.cell_artifacts",
                        ready=summary.ready_artifacts,
                        total=len(summary.artifacts),
                    )
                )
            ui.label(" · ".join(parts)).classes("st-cell-detail")

    def _chip(text: str, *, mono: bool = False) -> None:
        ui.label(text).classes("st-chip" + (" st-chip-mono" if mono else ""))

    def _open_variant_command(variant_id: str) -> None:
        bundle = page_state.get("preview_bundle")
        command = bundle.commands.get(variant_id) if bundle is not None else None
        if command is None:
            ui.notify(state.t("study.command_unavailable"), color="warning")
            return
        with ui.dialog() as dialog, ui.card().classes("rs-dialog st-command-dialog"):
            with ui.column().classes("rs-review gap-2"):
                ui.label(state.t("study.command_title")).classes("rs-review-title")
                ui.label(variant_id).classes("rs-review-sub st-cell-id")
                render_terminal(
                    command.shell_command,
                    translate=state.t,
                    command=True,
                    max_height="min(520px, calc(100vh - 260px))",
                )
                with ui.row().classes("rs-review-actions w-full"):
                    ui.button(
                        state.t("common.copy"),
                        on_click=lambda: (
                            ui.clipboard.write(command.shell_command),
                            ui.notify(state.t("common.copied")),
                        ),
                    ).props("flat no-caps")
                    ui.button(state.t("common.close"), on_click=dialog.close).props(
                        "unelevated no-caps color=primary"
                    )
        dialog.open()

    def _planned_count(study) -> int:
        job_by_id = {job.id: job for job in page_state["registries"].jobs.jobs}
        return sum(
            1
            for variant in study.variants
            if job_by_id.get(variant.job_id) and job_by_id[variant.job_id].status == "planned"
        )

    if page_state["study_id"] is None and page_state["sweep_rows"] == default_sweep_rows():
        page_state["sweep_rows"] = default_sweep_rows(current_base_config())
    studies_content()
