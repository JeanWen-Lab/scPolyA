from __future__ import annotations

import json
from dataclasses import replace
from html import escape
from pathlib import Path
from typing import Any

from scpolya_ui.errors import error_text
from scpolya_ui.components.status import artifact_label, localized_params, run_kind_text, status_text
from scpolya_ui.components.terminal import render_terminal
from scpolya_ui.components.confirm_dialog import open_confirmation_dialog
from scpolya_ui.components.config_panel import (
    config_workbench_section_keys,
    config_workbench_sections,
    render_config_diff_panel,
    render_structured_config_panel,
)
from scpolya_ui.config_schema import THRESHOLD_CONTROLLER_KEYS, project_active_run_config
from scpolya_ui.run_data import RunStateSummary
from scpolya_ui.run_records import resolve_execution_config
from scpolya_ui.services.diagnostics_service import run_state_diagnostics
from scpolya_ui.services.config_service import (
    annotation_schema_root_notice,
    apply_form_values,
    build_command_for_config,
    compare_execution_configs,
    config_to_json,
    config_validation_summary,
    effective_key_rows,
    execution_diff_rows,
    parse_config_json,
    preview_changed_rows,
    preview_impact_rows,
    preview_output_rows,
    preview_run_setup,
    proposed_output_rows,
)
from scpolya_ui.services.job_service import (
    ACTIVE_JOB_STATUSES,
    CONTINUE_STATE_READY_PAIRS,
    INTERRUPTED_RUN_STATUSES,
    create_run_setup_job,
    validate_continue_launch,
)
from scpolya_ui.services.run_service import (
    get_run_record,
    load_run_summary,
    refresh_run_record_statuses,
    run_record_summaries,
)
from scpolya_ui.services.workspace_service import (
    comparison_base_config,
    launch_input_problems,
    default_workspace,
    get_workspace,
    run_destination,
)
from scpolya_ui.state.app_state import ProductAppState
from scpolya_ui.state.persistence import load_product_registries


PAGE_KEY = "run_setup"
# Sections that always render their own grouped controls, even in the common view.
GROUPED_SECTIONS = frozenset({"resources", "workflow", "peak_calling"})
REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_BASE_SOURCE = "__workspace_state__"


def render_run_setup_page(state: ProductAppState) -> None:
    from nicegui import ui

    registries = state.registries
    if registries is None:
        registries = load_product_registries()
        state.registries = registries

    first_workspace = default_workspace(registries.workspaces)
    requested_base_source = str(state.payload.get("base_run_record_id") or "")
    requested_launch_mode = str(state.payload.get("launch_mode") or "rerun")
    page_state: dict[str, Any] = {
        "registries": registries,
        "workspace_id": state.selected_workspace_id or (first_workspace.id if first_workspace else None),
        "base_source_id": requested_base_source or WORKSPACE_BASE_SOURCE,
        "base_config": {},
        "base_label": "",
        "base_db_path": "",
        "draft_config": {},
        "proposed_config": {},
        "raw_json": "{}",
        "summary_error": "",
        "state_summary": None,
        "command": None,
        "command_error": "",
        "preview": None,
        "preview_error": "",
        "comparison": None,
        "comparison_error": "",
        "launch_result": None,
        "launch_error": "",
        "persisted_job_id": "",
        "persisted_run_record_id": "",
        "form_dirty": False,
        "active_section": "inputs",
        "field_view": "common",
        "run_note": "",
        "launch_mode": (
            requested_launch_mode
            if requested_launch_mode in {"rerun", "continue"}
            else "rerun"
        ),
    }
    refs: dict[str, Any] = {}

    def current_workspace():
        return get_workspace(page_state["registries"].workspaces, page_state["workspace_id"])

    def base_source_options() -> dict[str, str]:
        options = {WORKSPACE_BASE_SOURCE: state.t("run_setup.base_workspace")}
        for record in run_record_summaries(
            page_state["registries"].run_records,
            workspace_id=page_state["workspace_id"],
            limit=50,
        ):
            options[record.id] = state.t(
                "run_setup.base_run_option",
                label=record.label,
                kind=run_kind_text(state.t, record.kind),
                status=status_text(state.t, record.status),
            )
        return options

    def load_workspace_config() -> None:
        workspace = current_workspace()
        page_state.update(
            command=None,
            command_error="",
            preview=None,
            preview_error="",
            launch_result=None,
            launch_error="",
            persisted_job_id="",
            persisted_run_record_id="",
            form_dirty=False,
            comparison=None,
            comparison_error="",
        )
        if workspace is None:
            page_state.update(
                base_config={},
                base_label="",
                base_db_path="",
                draft_config={},
                proposed_config={},
                raw_json="{}",
                summary_error="",
                state_summary=None,
            )
            return
        source_options = base_source_options()
        if page_state["base_source_id"] not in source_options:
            page_state["base_source_id"] = WORKSPACE_BASE_SOURCE
        summary_error = ""
        if page_state["base_source_id"] == WORKSPACE_BASE_SOURCE:
            summary = load_run_summary(workspace.db_path)
            base_config = dict(summary.config or {})
            base_label = state.t("run_setup.base_workspace")
            base_db_path = workspace.db_path
            summary_error = summary.error or (
                "" if summary.exists else state.t("validation.state_db.missing", path=workspace.db_path)
            )
            state_summary = summary
        else:
            record = get_run_record(page_state["registries"].run_records, page_state["base_source_id"])
            resolution = resolve_execution_config(record) if record is not None else None
            base_config = dict(resolution.config) if resolution is not None and resolution.available else {}
            base_label = record.label if record is not None else ""
            base_db_path = (record.db_path if record is not None else "") or workspace.db_path
            state_summary = load_run_summary(base_db_path) if base_db_path else None
            summary_error = ((resolution.error if resolution is not None else "")
                             or (state_summary.error if state_summary is not None else ""))
        draft_config = dict(base_config)
        page_state.update(
            base_config=base_config,
            base_label=base_label,
            base_db_path=base_db_path,
            draft_config=draft_config,
            proposed_config=project_active_run_config(draft_config),
            raw_json=config_to_json(draft_config),
            summary_error=summary_error,
            state_summary=state_summary,
        )

    load_workspace_config()

    def select_base_source(source_id: str | None) -> None:
        target = source_id or WORKSPACE_BASE_SOURCE
        if target == page_state.get("base_source_id"):
            return

        def switch() -> None:
            page_state["base_source_id"] = target
            load_workspace_config()
            page_state["form_dirty"] = False
            ui.notify(state.t("run_setup.base_imported"), color="positive")
            set_unsaved_flag()
            refresh_all()

        # Fold field edits into the draft first so "Cancel" can rebuild the form without losing them.
        if refs.get("config_controls") and page_state["form_dirty"] and sync_form_config() is None:
            refresh_all()
            return
        changed = live_changed_keys()
        if not changed:
            switch()
            return
        # Loading another base replaces the draft; ask before throwing edits away.
        open_confirmation_dialog(
            title=state.t("run_setup.base_switch_title"),
            message=state.t("run_setup.base_switch_detail", count=len(changed)),
            confirm_label=state.t("run_setup.base_switch_confirm"),
            cancel_label=state.t("common.cancel"),
            on_confirm=switch,
            on_cancel=refresh_all,
            icon="o_restart_alt",
            danger=True,
        )

    def set_unsaved_flag() -> None:
        """Tell the browser whether leaving now would lose edits."""
        unsaved = bool(live_changed_keys())
        ui.run_javascript(f"window.scpolyaUnsaved = {'true' if unsaved else 'false'}")

    def selected_base_record():
        source_id = str(page_state.get("base_source_id") or "")
        if not source_id or source_id == WORKSPACE_BASE_SOURCE:
            return None
        return get_run_record(page_state["registries"].run_records, source_id)

    def set_launch_mode(value: str | None) -> None:
        if refs.get("config_controls"):
            sync_form_config()
        page_state["launch_mode"] = (
            "continue" if str(value or "").lower() == "continue" else "rerun"
        )
        page_state["launch_error"] = ""
        refresh_all()

    def reload_registries() -> None:
        page_state["registries"] = load_product_registries()
        state.registries = page_state["registries"]
        if current_workspace() is None:
            fallback = default_workspace(page_state["registries"].workspaces)
            page_state["workspace_id"] = fallback.id if fallback else None
            state.selected_workspace_id = page_state["workspace_id"]
        load_workspace_config()
        refresh_all()

    def reset_from_stored() -> None:
        load_workspace_config()
        page_state["form_dirty"] = False
        ui.notify(state.t("run_setup.reset_done"), color="positive")
        ui.run_javascript("window.scpolyaUnsaved = false")
        refresh_all()

    def mark_form_dirty() -> None:
        page_state.update(
            form_dirty=True,
            command=None,
            command_error="",
            preview=None,
            preview_error="",
            launch_result=None,
            launch_error="",
            comparison=None,
            comparison_error="",
        )
        bar_content.refresh()
        # Editing a value back to where it started leaves nothing to lose.
        set_unsaved_flag()

    def sync_form_config(*, notify: bool = False) -> dict[str, Any] | None:
        controls = refs.get("config_controls") or {}
        if not controls:
            ui.notify(state.t("run_setup.form_unavailable"), color="warning")
            return None
        try:
            values = {key: control.value for key, control in controls.items()}
            draft = apply_form_values(page_state["draft_config"], values)
            proposed = project_active_run_config(draft)
        except Exception as exc:
            ui.notify(error_text(state.t, exc), color="negative")
            return None
        page_state.update(
            draft_config=draft,
            proposed_config=proposed,
            raw_json=config_to_json(draft),
            form_dirty=False,
            command=None,
            command_error="",
            preview=None,
            preview_error="",
            launch_result=None,
            launch_error="",
            comparison=None,
            comparison_error="",
        )
        indicator = refs.get("dirty_indicator")
        if indicator is not None:
            indicator.set_visibility(False)
        missing, _advanced = config_validation_summary(proposed)
        if notify:
            ui.notify(
                state.t("run_setup.form_applied_missing", missing=", ".join(missing))
                if missing
                else state.t("run_setup.form_applied"),
                color="warning" if missing else "positive",
            )
        return proposed

    def current_run_note() -> str:
        """Read the live note control before creating a command snapshot."""
        control = refs.get("run_note")
        if control is not None:
            value = getattr(control, "value", None)
            if value is not None:
                page_state["run_note"] = str(value or "")
        return str(page_state.get("run_note") or "").strip()

    def handle_config_field_change(field_key: str) -> None:
        if field_key not in THRESHOLD_CONTROLLER_KEYS:
            return
        if sync_form_config() is not None:
            refresh_all()

    def apply_form_to_config() -> None:
        if sync_form_config(notify=True) is not None:
            refresh_all()

    def select_section(section_id: str) -> None:
        if refs.get("config_controls"):
            sync_form_config()
        page_state["active_section"] = section_id
        refresh_all()

    def apply_json_to_config() -> bool:
        raw_input = refs.get("raw_json")
        text = str(getattr(raw_input, "value", "") or "")
        try:
            draft = parse_config_json(text)
            proposed = project_active_run_config(draft)
        except json.JSONDecodeError as exc:
            # Keep the dialog and the typed text so the user can fix it.
            ui.notify(
                state.t("run_setup.json_invalid", line=exc.lineno, column=exc.colno, reason=exc.msg),
                color="negative",
            )
            return False
        except Exception as exc:
            ui.notify(error_text(state.t, exc), color="negative")
            return False
        page_state.update(
            draft_config=draft,
            proposed_config=proposed,
            raw_json=config_to_json(draft),
            command=None,
            command_error="",
            preview=None,
            preview_error="",
            launch_result=None,
            launch_error="",
            form_dirty=False,
            comparison=None,
            comparison_error="",
        )
        ui.notify(state.t("run_setup.json_applied"), color="positive")
        set_unsaved_flag()
        refresh_all()
        return True

    def prepare_command(*, sync: bool = True):
        proposed = sync_form_config() if sync else page_state["proposed_config"]
        if proposed is None:
            return None
        try:
            command = build_command_for_config(proposed, main_path=REPO_ROOT / "main.py")
        except Exception as exc:
            page_state["command"] = None
            page_state["command_error"] = error_text(state.t, exc)
            ui.notify(error_text(state.t, exc), color="negative")
            return None
        page_state["command"] = command
        page_state["command_error"] = ""
        return command

    def current_comparison():
        comparison = page_state.get("comparison")
        if comparison is not None:
            return comparison
        try:
            comparison = compare_execution_configs(
                comparison_base_config(page_state["base_config"], page_state["proposed_config"], current_workspace()),
                page_state["proposed_config"],
            )
        except Exception as exc:
            page_state["comparison_error"] = error_text(state.t, exc)
            page_state["comparison"] = None
            return None
        page_state["comparison"] = comparison
        page_state["comparison_error"] = ""
        return comparison

    def persist_job(command, *, launch: bool, note: str | None = None) -> None:
        workspace = current_workspace()
        if workspace is None:
            ui.notify(state.t("workspace.select_first"), color="warning")
            return
        run_note = current_run_note() if note is None else str(note).strip()
        try:
            result = create_run_setup_job(
                page_state["registries"].jobs,
                page_state["registries"].run_records,
                workspace=workspace,
                command=command,
                base_config=page_state["base_config"],
                launch=launch,
                base_run_record_id=(
                    str(page_state["base_source_id"])
                    if page_state["base_source_id"] != WORKSPACE_BASE_SOURCE else ""
                ),
                cwd=REPO_ROOT,
                existing_job_id=page_state["persisted_job_id"],
                existing_run_record_id=page_state["persisted_run_record_id"],
                note=run_note,
                launch_mode=page_state["launch_mode"],
                continue_from_run_record_id=(
                    str(page_state["base_source_id"])
                    if page_state["launch_mode"] == "continue"
                    else ""
                ),
            )
        except Exception as exc:
            page_state["launch_result"] = None
            page_state["launch_error"] = error_text(state.t, exc)
            ui.notify(error_text(state.t, exc), color="negative")
        else:
            page_state["command"] = command
            page_state["command_error"] = ""
            page_state["launch_result"] = result
            page_state["launch_error"] = ""
            page_state["persisted_job_id"] = result.job_id
            page_state["persisted_run_record_id"] = result.run_record_id
            ui.notify(state.t("run_setup.started" if launch else "run_setup.draft_saved"), color="positive")
            ui.run_javascript("window.scpolyaUnsaved = false")
        refresh_all()

    def poll_launch_status() -> None:
        result = page_state.get("launch_result")
        if result is None:
            return
        job = next(
            (item for item in page_state["registries"].jobs.jobs if item.id == result.job_id),
            None,
        )
        if job is None or (result.status not in ACTIVE_JOB_STATUSES and job.status not in ACTIVE_JOB_STATUSES):
            return
        changed = refresh_run_record_statuses(
            page_state["registries"].run_records,
            page_state["registries"].jobs,
        )
        job = next(
            (item for item in page_state["registries"].jobs.jobs if item.id == result.job_id),
            None,
        )
        if job is not None and result.status != job.status:
            page_state["launch_result"] = replace(result, status=job.status)
            changed = True
        if changed:
            refresh_all()

    def launch_is_active() -> bool:
        job_id = page_state.get("persisted_job_id")
        job = next(
            (item for item in page_state["registries"].jobs.jobs if item.id == job_id),
            None,
        )
        return bool(job is not None and job.status in ACTIVE_JOB_STATUSES)

    def save_job_draft() -> None:
        current_run_note()
        command = prepare_command()
        if command is None:
            refresh_all()
            return
        continue_error = continue_launch_error(command)
        if continue_error:
            page_state["launch_error"] = continue_error
            ui.notify(continue_error, color="negative")
            refresh_all()
            return
        persist_job(command, launch=False)

    def request_job_launch(*, confirm: bool = True) -> None:
        run_note = current_run_note()
        command = prepare_command()
        if command is None:
            refresh_all()
            return
        continue_error = continue_launch_error(command)
        if continue_error:
            page_state["launch_error"] = continue_error
            ui.notify(continue_error, color="negative")
            refresh_all()
            return
        destination = run_destination(command.config, current_workspace())
        if destination is not None and destination.blocked:
            message = state.t("run_setup.destination_recorded_blocked")
            page_state["launch_error"] = message
            ui.notify(message, color="negative")
            refresh_all()
            return
        if launch_input_problems(command.config, destination):
            message = state.t("run_setup.launch_inputs_blocked")
            page_state["launch_error"] = message
            ui.notify(message, color="negative")
            refresh_all()
            return
        comparison = current_comparison()
        changed_count = comparison.execution_change_count if comparison is not None else 0
        details = [
            (state.t("runs.metric.run_id"), command.config.get("run_id")),
            (state.t("runs.output_dir"), command.config.get("output_dir")),
            (state.t("run_setup.metric.changed"), changed_count),
            (
                state.t("run_setup.launch_mode"),
                state.t(f"run_setup.launch_mode.{page_state['launch_mode']}"),
            ),
        ]
        if run_note:
            details.append((state.t("runs.note"), run_note))
        if not confirm:
            persist_job(command, launch=True, note=run_note)
            return
        open_confirmation_dialog(
            title=state.t(
                "run_setup.confirm_continue_title"
                if page_state["launch_mode"] == "continue"
                else "run_setup.confirm_start_title"
            ),
            message=state.t(
                "run_setup.confirm_continue_message"
                if page_state["launch_mode"] == "continue"
                else "run_setup.confirm_start_message"
            ),
            confirm_label=state.t(
                "run_setup.confirm_continue_action"
                if page_state["launch_mode"] == "continue"
                else "run_setup.confirm_start_action"
            ),
            cancel_label=state.t("common.cancel"),
            on_confirm=lambda: persist_job(command, launch=True, note=run_note),
            details=details,
            icon="restart_alt" if page_state["launch_mode"] == "continue" else "play_arrow",
        )

    def continue_launch_error(command=None) -> str:
        if page_state["launch_mode"] != "continue":
            return ""
        source = selected_base_record()
        if source is None:
            return state.t("run_setup.continue_select_interrupted")
        if source.status not in INTERRUPTED_RUN_STATUSES:
            return state.t(
                "run_setup.continue_invalid_status",
                status=source.status,
            )
        if not source.db_path or not Path(source.db_path).expanduser().is_file():
            return state.t(
                "run_setup.continue_missing_state",
                path=source.db_path or "-",
            )
        summary = page_state.get("state_summary")
        if summary is not None and (
            summary.completion_state,
            summary.ledger_state,
        ) not in CONTINUE_STATE_READY_PAIRS:
            return state.t(
                "run_setup.continue_state_unreadable",
                completion=summary.completion_state,
                ledger=summary.ledger_state,
            )
        comparison = current_comparison()
        if comparison is not None and comparison.execution_change_count:
            return state.t("run_setup.continue_config_changed")
        if command is not None:
            try:
                validate_continue_launch(
                    page_state["registries"].run_records,
                    continue_from_run_record_id=source.id,
                    command=command,
                )
            except ValueError:
                return state.t("run_setup.continue_config_unavailable")
        return ""

    def build_command_preview() -> None:
        prepare_command()
        refresh_all()

    def preview_impact(*, sync: bool = True, notify: bool = True) -> None:
        workspace = current_workspace()
        if workspace is None:
            ui.notify(state.t("workspace.select_first"), color="warning")
            return
        proposed = sync_form_config() if sync else page_state["proposed_config"]
        if proposed is None:
            return
        try:
            base_for_comparison = comparison_base_config(page_state["base_config"], proposed, workspace)
            preview = preview_run_setup(
                page_state["base_db_path"] or workspace.db_path,
                proposed,
                base_config=base_for_comparison,
            )
            # Keep every review surface on the same parser-materialized comparison.
            # The wrapper call above remains the state-db-compatible preview path;
            # this comparison supplies the canonical rows and counts for the UI.
            comparison = compare_execution_configs(
                base_for_comparison,
                proposed,
            )
            page_state["comparison"] = comparison
            page_state["preview"] = comparison.preview
            page_state["preview_error"] = ""
            page_state["comparison_error"] = ""
        except Exception as exc:
            page_state["preview"] = None
            page_state["preview_error"] = error_text(state.t, exc)
            ui.notify(error_text(state.t, exc), color="negative")
        else:
            if notify:
                ui.notify(state.t("run_setup.preview_ready"), color="positive")
        refresh_all()

    def refresh_all() -> None:
        run_setup_content.refresh()
        review_content.refresh()

    def live_changed_keys() -> list[str]:
        """Changed keys including edits not yet applied to the draft."""
        controls = refs.get("config_controls") or {}
        draft = page_state["draft_config"]
        if controls and page_state["form_dirty"]:
            try:
                draft = apply_form_values(draft, {key: control.value for key, control in controls.items()})
            except Exception:
                pass
        base = page_state["base_config"]
        return sorted(key for key in set(base) | set(draft) if base.get(key) != draft.get(key))

    def live_value(key: str) -> Any:
        control = (refs.get("config_controls") or {}).get(key)
        if control is not None and page_state["form_dirty"]:
            try:
                return apply_form_values(page_state["draft_config"], {key: control.value}).get(key)
            except Exception:
                pass
        return page_state["draft_config"].get(key)

    @ui.refreshable
    def bar_content() -> None:
        with ui.row().classes("rs-bar w-full"):
            changed_list = live_changed_keys()
            ui.label(state.t("run_setup.bar_changed", count=len(changed_list))).classes("rs-bar-count")
            with ui.row().classes("rs-bar-diff"):
                for key in changed_list[:3]:
                    ui.label(
                        f"{key} {_short_value(page_state['base_config'].get(key))} → "
                        f"{_short_value(live_value(key))}"
                    ).classes("rs-bar-chip")
                if len(changed_list) > 3:
                    ui.label(f"+{len(changed_list) - 3}").classes("rs-bar-chip")
            missing, _advanced = config_validation_summary(page_state["proposed_config"])
            if missing:
                ui.label(state.t("run_setup.bar_missing", count=len(missing))).classes("rs-bar-missing")
            refs["dirty_indicator"] = ui.label(state.t("run_setup.unsaved_changes")).classes("rs-bar-dirty")
            refs["dirty_indicator"].set_visibility(
                page_state["form_dirty"] and changed_list != changed_config_keys()
            )
            ui.element("div").classes("workbench-toolbar-spacer")
            ui.button(state.t("run_setup.command_preview"), on_click=open_command_dialog).props(
                "flat dense no-caps"
            ).classes("rs-bar-button")
            save_button = ui.button(state.t("run_setup.save_draft"), on_click=save_job_draft).props(
                "flat dense no-caps"
            ).classes("rs-bar-button")
            review_button = ui.button(state.t("run_setup.review_and_run"), on_click=open_review).props(
                "unelevated dense no-caps"
            ).classes("rs-bar-primary")
            if launch_is_active():
                save_button.props("disable")
                review_button.props("disable")

    def changed_config_keys() -> list[str]:
        base = page_state["base_config"]
        draft = page_state["draft_config"]
        return sorted(key for key in set(base) | set(draft) if base.get(key) != draft.get(key))

    def set_field_view(value: str | None) -> None:
        if refs.get("config_controls"):
            sync_form_config()
        page_state["field_view"] = "all" if value == "all" else "common"
        run_setup_content.refresh()

    def open_review() -> None:
        current_run_note()
        if refs.get("config_controls") and sync_form_config() is None:
            return
        if prepare_command(sync=False) is None:
            refresh_all()
            return
        preview_impact(sync=False, notify=False)
        review_dialog.open()

    def open_command_dialog() -> None:
        prepare_command()
        command_content.refresh()
        command_dialog.open()

    def launch_from_review() -> None:
        review_dialog.close()
        request_job_launch(confirm=False)

    @ui.refreshable
    def run_setup_content() -> None:
        workspace = current_workspace()
        missing, advanced_keys = config_validation_summary(page_state["proposed_config"])
        sections = config_workbench_sections()
        include_all = page_state["field_view"] == "all"
        changed = set(changed_config_keys())
        missing_set = set(missing)
        refs["config_controls"] = {}

        with ui.element("div").classes("rs-page w-full"):
            with ui.row().classes("rs-toolbar w-full"):
                with ui.row().classes("rs-base"):
                    ui.label(state.t("run_setup.base_config")).classes("rs-inline-label")
                    ui.select(
                        options=base_source_options(),
                        value=page_state["base_source_id"],
                        on_change=lambda e: select_base_source(e.value),
                    ).classes("rs-base-select").props("dense outlined options-dense hide-bottom-space")
                refs["run_note"] = ui.input(
                    value=page_state["run_note"],
                    placeholder=state.t("runs.note_placeholder"),
                    on_change=lambda e: page_state.__setitem__("run_note", str(e.value or "")),
                ).classes("rs-note").props("dense outlined clearable hide-bottom-space")
                ui.element("div").classes("workbench-toolbar-spacer")
                ui.toggle(
                    {"common": state.t("run_setup.view.common"), "all": state.t("run_setup.view.all")},
                    value=page_state["field_view"],
                    on_change=lambda e: set_field_view(e.value),
                ).classes("rs-view-toggle").props("dense no-caps unelevated")
                reset = ui.button(icon="o_restart_alt", on_click=reset_from_stored).props(
                    f'flat dense aria-label="{state.t("run_setup.reset")}"'
                )
                reset.tooltip(state.t("run_setup.reset"))
                json_button = ui.button(icon="o_data_object", on_click=lambda: open_json_dialog()).props(
                    f'flat dense aria-label="{state.t("run_setup.raw_json")}"'
                )
                json_button.tooltip(state.t("run_setup.raw_json"))

            if workspace is None:
                with ui.column().classes("rs-empty gap-2"):
                    ui.label(state.t("workspace.empty")).classes("runs-empty-title")
                    ui.label(state.t("workspace.empty_detail")).classes("product-small-muted")
                return

            _render_state_diagnostics(page_state.get("state_summary"))

            with ui.element("div").classes("rs-layout"):
                with ui.element("nav").classes("rs-index"):
                    for section_id, _title, _icon, all_keys, _default in sections:
                        section_changed = len(changed & set(all_keys))
                        section_missing = len(missing_set & set(all_keys))
                        with ui.element("a").classes("rs-index-link").props(
                            f'href="#rs-sec-{section_id}"'
                        ):
                            ui.label(state.t(f"run_setup.section.{section_id}"))
                            if section_missing:
                                ui.label(state.t("run_setup.section_missing", count=section_missing)).classes(
                                    "rs-index-missing"
                                )
                            elif section_changed:
                                ui.label(state.t("run_setup.section_changed", count=section_changed)).classes(
                                    "rs-index-changed"
                                )
                with ui.column().classes("rs-sections gap-0"):
                    for section_id, _title, _icon, all_keys, _default in sections:
                        shown = config_workbench_section_keys(
                            section_id, page_state["draft_config"], include_all=include_all
                        )
                        hidden = len(all_keys) - len(set(shown) & set(all_keys))
                        with ui.element("section").classes("rs-section").props(f'id="rs-sec-{section_id}"'):
                            with ui.row().classes("rs-section-head"):
                                ui.label(state.t(f"run_setup.section.{section_id}")).classes("rs-section-title")
                                if hidden > 0 and not include_all:
                                    ui.label(state.t("run_setup.section_more", count=hidden)).classes(
                                        "rs-section-more"
                                    )
                            if not shown and section_id not in GROUPED_SECTIONS:
                                with ui.row().classes("rs-card rs-card-collapsed"):
                                    ui.label(state.t("run_setup.section_all_only", count=hidden)).classes(
                                        "product-small-muted"
                                    )
                                    ui.button(
                                        state.t("run_setup.view.all"),
                                        on_click=lambda: set_field_view("all"),
                                    ).props("flat dense no-caps").classes("ov-link-button")
                                continue
                            with ui.element("div").classes("rs-card"):
                                controls = render_structured_config_panel(
                                    page_state["draft_config"],
                                    mode="edit",
                                    missing_label=state.t("config.missing"),
                                    advanced_label=state.t("config.advanced_only"),
                                    on_change=mark_form_dirty,
                                    on_field_change=handle_config_field_change,
                                    section_id=section_id,
                                    include_all=include_all,
                                    section_title=state.t(f"run_setup.section.{section_id}"),
                                    translate=state.t,
                                )
                                refs["config_controls"].update(controls)
                    if advanced_keys:
                        ui.label(state.t("config.advanced_count", count=len(advanced_keys))).classes(
                            "rs-footnote"
                        )

            bar_content()
            _render_launch_state()

    @ui.refreshable
    def review_content() -> None:
        comparison = current_comparison()
        missing, _advanced = config_validation_summary(page_state["proposed_config"])
        command = page_state.get("command")
        with ui.column().classes("rs-review gap-0"):
            ui.label(state.t("run_setup.review_run")).classes("rs-review-title")
            ui.label(
                state.t("run_setup.review_base", base=page_state.get("base_label") or "-")
            ).classes("rs-review-sub")
            ui.label(state.t("run_setup.launch_mode")).classes("rs-review-label")
            ui.toggle(
                {
                    "rerun": state.t("run_setup.launch_mode.rerun"),
                    "continue": state.t("run_setup.launch_mode.continue"),
                },
                value=page_state["launch_mode"],
                on_change=lambda e: set_launch_mode(e.value),
            ).classes("rs-mode-toggle").props("no-caps unelevated")
            continue_error = continue_launch_error() if page_state["launch_mode"] == "continue" else ""
            if page_state["launch_mode"] == "continue":
                state_summary = page_state.get("state_summary")
                state_uninitialized = bool(
                    state_summary is not None
                    and state_summary.completion_state == "uninitialized"
                    and state_summary.ledger_state == "uninitialized"
                )
                with ui.row().classes("rs-notice " + ("rs-notice-bad" if continue_error else "rs-notice-warn")):
                    ui.icon("o_warning_amber")
                    ui.label(
                        continue_error
                        or state.t(
                            "run_setup.continue_uninitialized_warning"
                            if state_uninitialized
                            else "run_setup.continue_experimental_warning"
                        )
                    )
            preview = page_state.get("preview")
            if comparison is not None:
                preview = comparison.preview
            _render_destination(preview_output_rows(preview) if preview is not None else [])
            _render_launch_problems()
            if missing:
                with ui.row().classes("rs-notice rs-notice-bad"):
                    ui.icon("o_error_outline")
                    ui.label(state.t("run_setup.form_applied_missing", missing=", ".join(missing)))
            if page_state["proposed_config"].get("quantification_mode") == "terminal-aware":
                with ui.row().classes("rs-notice rs-notice-warn"):
                    ui.icon("o_info")
                    ui.label(state.t("run_setup.terminal_aware_note"))
                    ui.label(state.t("common.reference_only")).classes("sp-ref").tooltip(
                        state.t("help.term.reference_only.detail")
                    )
            schema_notice = annotation_schema_root_notice(
                comparison.proposed_execution_config if comparison is not None else page_state["proposed_config"]
            )
            if schema_notice:
                with ui.row().classes("rs-notice rs-notice-warn rs-annotation-schema-notice w-full"):
                    ui.icon("o_warning_amber")
                    with ui.column().classes("gap-1 min-w-0"):
                        ui.label(state.t("run_setup.annotation_schema_title")).classes("rs-destination-title")
                        ui.label(state.t("run_setup.annotation_schema_new_root")).classes("rs-destination-detail")
                        ui.label(schema_notice).classes("rs-destination-detail")
            with ui.expansion(state.t("run_setup.config_diff"), icon="o_difference", value=True).classes(
                "rs-review-section w-full"
            ).props("dense"):
                _render_effective_diff(comparison)
            _render_preview_state()
            with ui.expansion(state.t("run_setup.command_preview"), icon="o_terminal").classes(
                "rs-review-section w-full"
            ).props("dense"):
                _render_command_state()
            with ui.row().classes("rs-review-actions w-full"):
                ui.button(state.t("common.cancel"), on_click=review_dialog.close).props("flat no-caps")
                start = ui.button(
                    state.t(
                        "run_setup.continue_run" if page_state["launch_mode"] == "continue" else "run_setup.start_run"
                    ),
                    on_click=launch_from_review,
                ).props("unelevated no-caps color=primary")
                destination = run_destination(page_state["proposed_config"], current_workspace())
                if (command is None or missing or continue_error or launch_is_active()
                        or (destination is not None and destination.blocked)
                        or launch_input_problems(page_state["proposed_config"], destination)):
                    start.props("disable")

    @ui.refreshable
    def command_content() -> None:
        with ui.column().classes("rs-review gap-2"):
            ui.label(state.t("run_setup.command_preview")).classes("rs-review-title")
            _render_command_state()
            command = page_state.get("command")
            with ui.row().classes("rs-review-actions w-full"):
                if command is not None:
                    ui.button(
                        state.t("common.copy"),
                        on_click=lambda: (ui.clipboard.write(command.shell_command), ui.notify(state.t("common.copied"))),
                    ).props("flat no-caps")
                ui.button(state.t("common.close"), on_click=command_dialog.close).props("unelevated no-caps color=primary")

    def _shown_values(row: dict[str, Any]) -> dict[str, Any]:
        def shown(value: Any) -> Any:
            if value in ("true", "false"):
                return state.t(f"config.value.{value}")
            return state.t("config.value.unset") if value in ("-", "", None) else value

        return {**row, "old_value": shown(row.get("old_value")), "new_value": shown(row.get("new_value"))}

    def use_destination_output_dir(output_dir: str) -> None:
        """Explicitly point the run at the mapped local folder; nothing is redirected implicitly."""
        apply_explicit_draft_values({"output_dir": output_dir})

    def apply_explicit_draft_values(values: dict[str, Any]) -> None:
        """Apply settings the user chose with a button in the review, then refresh command and preview."""
        draft = dict(page_state["draft_config"])
        draft.update(values)
        try:
            proposed = project_active_run_config(draft)
        except Exception as exc:
            ui.notify(error_text(state.t, exc), color="negative")
            return
        page_state.update(
            draft_config=draft,
            proposed_config=proposed,
            raw_json=config_to_json(draft),
            command=None,
            command_error="",
            preview=None,
            preview_error="",
            launch_result=None,
            launch_error="",
            form_dirty=False,
            comparison=None,
            comparison_error="",
        )
        set_unsaved_flag()
        prepare_command(sync=False)
        preview_impact(sync=False, notify=False)

    def _render_destination(output_rows) -> None:
        """Say plainly where the run writes and whether existing results are touched.

        The target is judged exactly as the engine will use it (output_dir/run_id, no path mappings),
        so the notice, the impact preview and the launch all refer to the same folder.
        """
        destination = run_destination(page_state["proposed_config"], current_workspace())
        if destination is None:
            return
        if destination.blocked:
            with ui.row().classes("rs-notice rs-notice-bad rs-destination w-full"):
                ui.icon("o_report_problem")
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label(state.t("run_setup.destination_recorded_title")).classes("rs-destination-title")
                    ui.label(destination.target).classes("rs-destination-path")
                    ui.label(
                        state.t("run_setup.destination_recorded_detail", mapped=destination.mapped_view)
                    ).classes("rs-destination-detail")
                    if destination.suggested_output_dir:
                        ui.button(
                            state.t("run_setup.destination_use_mapped", path=destination.suggested_output_dir),
                            icon="o_drive_file_move",
                            on_click=lambda path=destination.suggested_output_dir: use_destination_output_dir(path),
                        ).props("flat dense no-caps").classes("rs-destination-action")
            return
        rewrites = sum(1 for row in output_rows if row.get("action") == "replace_in_scope")
        tone = "rs-notice-warn" if destination.exists else "rs-notice-info"
        with ui.row().classes(f"rs-notice {tone} rs-destination w-full"):
            ui.icon("o_folder_open")
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(
                    state.t("run_setup.destination_existing" if destination.exists else "run_setup.destination_new")
                ).classes("rs-destination-title")
                ui.label(destination.target).classes("rs-destination-path")
                if destination.exists:
                    ui.label(
                        state.t("run_setup.destination_rewrites", count=rewrites)
                        if rewrites
                        else state.t("run_setup.destination_no_rewrites")
                    ).classes(
                        "rs-destination-detail"
                    )
                else:
                    ui.label(state.t("run_setup.destination_new_detail")).classes("rs-destination-detail")
                recorded_dir = str(page_state["base_config"].get("output_dir") or "")
                compared_dir = comparison_base_config(
                    page_state["base_config"], page_state["proposed_config"], current_workspace()
                ).get("output_dir")
                if recorded_dir and compared_dir != recorded_dir:
                    ui.label(
                        state.t("run_setup.destination_local_copy", recorded=recorded_dir)
                    ).classes("rs-destination-detail")
                if destination.moved_history:
                    ui.label(state.t("run_setup.destination_moved_history")).classes("rs-destination-detail")

    def _render_launch_problems() -> None:
        """List what would make the engine stop before running any task; Start stays disabled meanwhile."""
        destination = run_destination(page_state["proposed_config"], current_workspace())
        if destination is not None and destination.blocked:
            return
        problems = launch_input_problems(page_state["proposed_config"], destination)
        if not problems:
            return
        with ui.row().classes("rs-notice rs-notice-bad rs-launch-problems w-full"):
            ui.icon("o_report_problem")
            with ui.column().classes("gap-1 min-w-0"):
                ui.label(state.t("run_setup.launch_inputs_title")).classes("rs-destination-title")
                for problem in problems:
                    ui.label(
                        state.t(
                            f"run_setup.launch_problem.{problem.kind}",
                            setting=problem.key,
                            value=problem.value,
                            stored=problem.stored,
                        )
                    ).classes("rs-destination-detail rs-launch-problem")
                    if problem.kind == "moved_without_repair":
                        ui.button(
                            state.t("run_setup.launch_enable_repair"),
                            icon="o_build",
                            on_click=lambda: apply_explicit_draft_values({"repair_moved_run": True}),
                        ).props("flat dense no-caps").classes("rs-destination-action")

    def _short_value(value: Any) -> str:
        text = "—" if value is None or value == "" else str(value)
        return text if len(text) <= 18 else text[:8] + "…" + text[-8:]

    def _render_effective_diff(comparison) -> None:
        if comparison is None:
            ui.label(
                page_state.get("comparison_error") or state.t("run_setup.no_changes")
            ).classes("product-small-muted product-status-missing")
            return
        rows = execution_diff_rows(comparison)
        if not rows:
            ui.label(state.t("run_setup.no_changes")).classes("product-small-muted")
        else:
            ui.table(
                columns=[
                    {"name": "key", "label": state.t("common.key"), "field": "key"},
                    {"name": "old_value", "label": state.t("config.stored"), "field": "old_value"},
                    {"name": "new_value", "label": state.t("config.proposed"), "field": "new_value"},
                ],
                rows=[_shown_values(row) for row in rows],
                row_key="row_key",
            ).classes("w-full run-setup-effective-diff-table").props(
                "dense flat bordered hide-pagination"
            )
        if comparison.normalized_only_keys:
            ui.label(
                state.t(
                    "run_setup.normalized_note",
                    keys=", ".join(comparison.normalized_only_keys),
                )
            ).classes("product-small-muted run-setup-normalized-note")

    def _render_state_diagnostics(summary: RunStateSummary | None) -> None:
        """Keep state-read warnings concise while preserving raw diagnostics."""
        if summary is None:
            return
        diagnostics = run_state_diagnostics(summary, scope="workspace")
        if not diagnostics and not page_state.get("summary_error"):
            return
        with ui.element("section").classes("run-setup-state-diagnostics"):
            with ui.row().classes("items-center gap-2 run-setup-state-heading"):
                ui.icon("info").classes("run-setup-state-icon")
                ui.label(state.t("run_setup.state_diagnostics")).classes(
                    "run-setup-state-title"
                )
                ui.label(state.t("run_setup.state_diagnostics_detail")).classes(
                    "product-small-muted"
                )
            if diagnostics:
                with ui.element("div").classes("run-setup-state-list"):
                    for item in diagnostics:
                        with ui.element("div").classes("run-setup-state-row"):
                            ui.label(state.t(item.message_key, **localized_params(state.t, item.params))).classes(
                                "run-setup-state-message"
                            )
            if page_state.get("summary_error"):
                with ui.expansion(
                    state.t("common.technical_details"),
                    icon="code",
                    value=False,
                ).classes("run-setup-state-technical").props("dense"):
                    ui.html(
                        f"<pre class='run-setup-state-technical-text'>"
                        f"{escape(str(page_state['summary_error']))}</pre>"
                    )

    def _render_command_state() -> None:
        command = page_state.get("command")
        if page_state["command_error"]:
            ui.label(page_state["command_error"]).classes("product-small-muted product-status-missing")
        if command is None:
            ui.button(
                state.t("run_setup.build_command"),
                icon="terminal",
                on_click=build_command_preview,
            ).props("outline no-caps")
            return
        render_terminal(command.shell_command, translate=state.t, command=True, max_height="360px")
        if command.ignored_keys:
            ui.label(state.t("run_setup.ignored_keys", keys=", ".join(command.ignored_keys))).classes(
                "product-small-muted"
            )

    def _render_launch_state() -> None:
        if page_state["launch_error"]:
            ui.label(page_state["launch_error"]).classes("product-small-muted product-status-missing")
        result = page_state.get("launch_result")
        if result is None:
            return
        with ui.column().classes("run-setup-result gap-1"):
            ui.label(state.t("run_setup.launch_result")).classes("product-section-title")
            ui.label(state.t("run_setup.launch_status", status=result.status)).classes("product-small-muted")
            ui.label(state.t("run_setup.launch_job", job_id=result.job_id)).classes("workspace-path")
            ui.label(state.t("run_setup.launch_run_record", run_record_id=result.run_record_id)).classes(
                "workspace-path"
            )

    def _render_preview_state() -> None:
        preview = page_state.get("preview")
        if page_state["preview_error"]:
            ui.label(page_state["preview_error"]).classes("product-small-muted product-status-missing")
            return
        if preview is None:
            return
        comparison = current_comparison()
        preview = comparison.preview if comparison is not None else preview
        changed_rows = execution_diff_rows(comparison) if comparison is not None else []
        effective_rows = effective_key_rows(comparison) if comparison is not None else []
        impact_rows = preview_impact_rows(preview)
        output_impact_rows = preview_output_rows(preview)
        expected_output_rows = proposed_output_rows(preview)
        physical_impact = preview.physical_output_impact
        projection_warnings = tuple(
            dict.fromkeys(
                [
                    *(
                        physical_impact.old_projection.warnings
                        if physical_impact is not None
                        else ()
                    ),
                    *(
                        physical_impact.new_projection.warnings
                        if physical_impact is not None
                        else ()
                    ),
                ]
            )
        )
        with ui.expansion(
            state.t("run_setup.preview"),
            icon="visibility",
            value=True,
        ).classes("run-setup-review-expansion w-full").props("dense"):
            with ui.element("div").classes("run-setup-preview-metrics"):
                _metric(
                    state.t("run_setup.metric.changed"),
                    comparison.execution_change_count if comparison is not None else len(changed_rows),
                )
                _metric(
                    state.t("run_setup.metric.effective"),
                    comparison.semantic_change_count if comparison is not None else len(preview.changed_effective_keys),
                )
                _metric(state.t("run_setup.metric.impacted"), len(impact_rows))
                _metric(state.t("run_setup.metric.outputs"), len(output_impact_rows))
            for warning in projection_warnings:
                with ui.row().classes("run-setup-warning w-full"):
                    ui.icon("warning").classes("product-status-missing")
                    ui.label(warning).classes("product-small-muted product-status-missing")
            if changed_rows:
                ui.table(
                    columns=[
                        {"name": "key", "label": state.t("common.key"), "field": "key"},
                        {"name": "old_value", "label": state.t("config.stored"), "field": "old_value"},
                        {"name": "new_value", "label": state.t("config.proposed"), "field": "new_value"},
                    ],
                    rows=[_shown_values(row) for row in changed_rows],
                    row_key="row_key",
                ).classes("w-full run-setup-preview-diff-table").props(
                    "dense flat bordered hide-pagination"
                )
            if effective_rows:
                ui.table(
                    columns=[
                        {"name": "key", "label": state.t("common.effective_keys"), "field": "key"},
                        {"name": "type", "label": state.t("common.kind"), "field": "type"},
                    ],
                    rows=effective_rows,
                    row_key="row_key",
                ).classes("w-full run-setup-effective-keys-table").props(
                    "dense flat bordered hide-pagination"
                )
            if impact_rows:
                ui.table(
                    columns=[
                        {"name": "stage", "label": state.t("common.stage"), "field": "stage"},
                        {"name": "task", "label": state.t("common.task"), "field": "task"},
                        {"name": "kind", "label": state.t("common.kind"), "field": "kind"},
                    ],
                    rows=impact_rows,
                    row_key="key",
                ).classes("w-full").props("dense flat bordered rows-per-page-options='[5, 10]'")
            if output_impact_rows:
                physical_rows = []
                for row in output_impact_rows:
                    physical_rows.append(
                        {
                            **row,
                            "label": artifact_label(state.t, str(row.get("label") or "")),
                            "action_label": state.t(
                                f"run_setup.output_action.{row['action']}"
                            ),
                        }
                    )
                ui.label(state.t("run_setup.physical_output_impact")).classes(
                    "product-section-title"
                )
                ui.table(
                    columns=[
                        {
                            "name": "action_label",
                            "label": state.t("run_setup.output_action"),
                            "field": "action_label",
                        },
                        {
                            "name": "label",
                            "label": state.t("run_setup.output_scope"),
                            "field": "label",
                        },
                        {
                            "name": "path",
                            "label": state.t("common.path"),
                            "field": "path",
                        },
                    ],
                    rows=physical_rows,
                    row_key="scope_id",
                ).classes("w-full").props(
                    "dense flat bordered rows-per-page-options='[5, 10]'"
                )
            with ui.expansion(
                state.t(
                    "run_setup.expected_outputs",
                    count=len(expected_output_rows),
                ),
                icon="inventory_2",
                value=False,
            ).classes("run-setup-review-expansion w-full").props("dense"):
                ui.table(
                    columns=[
                        {
                            "name": "label",
                            "label": state.t("run_setup.output_scope"),
                            "field": "label",
                        },
                        {
                            "name": "path",
                            "label": state.t("common.path"),
                            "field": "path",
                        },
                    ],
                    rows=[
                        {**row, "label": artifact_label(state.t, str(row.get("label") or ""))}
                        for row in expected_output_rows
                    ],
                    row_key="scope_id",
                ).classes("w-full").props(
                    "dense flat bordered rows-per-page-options='[5, 10]'"
                )

    def _metric(label: str, value: object) -> None:
        with ui.column().classes("workspace-metric gap-1"):
            ui.label(str(value)).classes("workspace-metric-value")
            ui.label(label).classes("product-small-muted")

    run_setup_content()
    with ui.dialog() as review_dialog, ui.card().classes("rs-dialog"):
        review_content()
    with ui.dialog() as command_dialog, ui.card().classes("rs-dialog"):
        command_content()
    @ui.refreshable
    def json_content() -> None:
        with ui.column().classes("rs-review gap-2"):
            ui.label(state.t("run_setup.raw_json")).classes("rs-review-title")
            ui.label(state.t("run_setup.raw_json_detail")).classes("rs-review-sub")
            refs["raw_json"] = ui.textarea(value=page_state["raw_json"]).classes(
                "w-full config-json-editor rs-json-editor"
            ).props("outlined")
            with ui.row().classes("rs-review-actions w-full"):
                ui.button(state.t("common.close"), on_click=json_dialog.close).props("flat no-caps")
                ui.button(
                    state.t("run_setup.apply_json"),
                    on_click=lambda: json_dialog.close() if apply_json_to_config() else None,
                ).props("unelevated no-caps color=primary")

    with ui.dialog() as json_dialog, ui.card().classes("rs-dialog"):
        json_content()

    def open_json_dialog() -> None:
        if refs.get("config_controls"):
            sync_form_config()
        json_content.refresh()
        json_dialog.open()
    ui.timer(2.0, poll_launch_status)
