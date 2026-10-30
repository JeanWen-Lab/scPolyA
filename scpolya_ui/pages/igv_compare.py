from __future__ import annotations

import json
import re
from copy import deepcopy
from html import escape
from typing import Any

from scpolya_ui.shell.layout import igv_view_query
from scpolya_ui.errors import error_text
from scpolya_ui.components.terminal import render_terminal
from scpolya_ui.components.path_rows import render_path_rows
from scpolya_ui.components.status import artifact_label, status_text
from scpolya_ui.igv_manifest import artifact_route, manifest_as_igv_options, coordinate_locus
from scpolya_ui.run_records import resolve_execution_config
from scpolya_ui.services.igv_service import (
    manifest_payload_with_reference,
    register_artifact_url,
    manifest_summary,
    single_run_manifest,
    study_manifest,
    comparison_manifest,
    comparison_run_label,
    run_view_paths,
)
from scpolya_ui.services.run_service import (
    default_run_record,
    get_run_record,
    load_run_summary,
    run_record_summaries,
)
from scpolya_ui.services.study_service import (
    get_study,
    study_summaries,
    study_variant_summaries,
)
from scpolya_ui.services.workspace_service import default_workspace, get_workspace
from scpolya_ui.state.app_state import ProductAppState
from scpolya_ui.state.persistence import load_product_registries


PAGE_KEY = "igv_compare"


def render_igv_compare_page(state: ProductAppState) -> None:
    from nicegui import run, ui
    client = ui.context.client

    def _translated(key: str, fallback: str) -> str:
        label = state.t(key)
        return fallback if label == f"[[{key}]]" else label

    registries = state.registries
    if registries is None:
        registries = load_product_registries()
        state.registries = registries

    first_workspace = default_workspace(registries.workspaces)
    source = state.payload.get("igv_source")
    if source not in {"study", "single", "compare"}:
        # Open where there is something to show: studies first, otherwise single runs.
        workspace_id = state.selected_workspace_id or (first_workspace.id if first_workspace else None)
        has_study = any(study.workspace_id == workspace_id for study in registries.studies.studies)
        source = "study" if has_study else "single"
    page_state: dict[str, Any] = {
        "registries": registries,
        "workspace_id": state.selected_workspace_id or (first_workspace.id if first_workspace else None),
        "source": source,
        "study_id": state.payload.get("selected_study_id"),
        "run_record_id": state.payload.get("selected_run_record_id"),
        "compare_first_id": None,
        "compare_second_id": None,
        # No species-neutral default gene exists; an empty box shows the placeholder hint.
        "gene": state.payload.get("igv_gene") or "",
        "loaded_gene": "",
        "viewer_stale": False,
        "loading": False,
        "loading_gene": "",
        "load_request_id": 0,
        "manifest": None,
        "manifest_payload": None,
        "options": None,
        "error": "",
        "prerequisite": "",
        "reference_status": "pending",
        "tracks_status": "pending",
        "locus_status": "pending",
        "locus_message": "",
        "notification": None,
        "render_token": 0,
    }
    refs: dict[str, Any] = {}

    def current_workspace():
        return get_workspace(page_state["registries"].workspaces, page_state["workspace_id"])

    def current_base_config() -> dict[str, Any]:
        workspace = current_workspace()
        if workspace is None:
            return {}
        return dict(load_run_summary(workspace.db_path).config or {})

    def current_study():
        study = get_study(page_state["registries"].studies, page_state["study_id"])
        if study is None or study.workspace_id != page_state["workspace_id"]:
            return None
        return study

    def current_run_record():
        record = get_run_record(
            page_state["registries"].run_records,
            page_state["run_record_id"],
        )
        if record is not None and record.workspace_id == page_state["workspace_id"]:
            return record
        return default_run_record(
            page_state["registries"].run_records,
            workspace_id=page_state["workspace_id"],
        )

    def clear_manifest() -> None:
        _dismiss_page_notification()
        page_state["load_request_id"] += 1
        page_state.update(
            manifest=None,
            manifest_payload=None,
            options=None,
            error="",
            reference_status="pending",
            tracks_status="pending",
            locus_status="pending",
            locus_message="",
            loaded_gene="",
            viewer_stale=False,
            loading=False,
            loading_gene="",
        )

    def _dismiss_page_notification() -> None:
        notification = page_state.get("notification")
        if notification is None:
            return
        try:
            notification.dismiss()
        except Exception:
            pass
        page_state["notification"] = None

    def _notify(message: str, *, color: str) -> None:
        _dismiss_page_notification()
        # The command bar is refreshed before asynchronous discovery finishes.
        # Its original event slot is then deleted; use the stable page client
        # when reporting a missing-data or compatibility diagnostic.
        with client:
            page_state["notification"] = ui.notification(
                message,
                color=color,
                position="top",
                timeout=4,
            )

    def reload_registries() -> None:
        page_state["registries"] = load_product_registries()
        state.registries = page_state["registries"]
        if current_workspace() is None:
            fallback = default_workspace(page_state["registries"].workspaces)
            page_state["workspace_id"] = fallback.id if fallback else None
        clear_manifest()
        igv_content.refresh()

    def set_source(source: str | None) -> None:
        page_state["source"] = str(source or "study")
        state.payload["igv_source"] = page_state["source"]
        clear_manifest()
        igv_content.refresh()

    def select_study(study_id: str | None) -> None:
        page_state["study_id"] = study_id
        state.payload["selected_study_id"] = study_id
        clear_manifest()
        igv_content.refresh()

    def select_run_record(run_record_id: str | None) -> None:
        page_state["run_record_id"] = run_record_id
        state.payload["selected_run_record_id"] = run_record_id
        clear_manifest()
        igv_content.refresh()

    def select_comparison_record(key: str, record_id: str | None) -> None:
        page_state[key] = record_id
        clear_manifest()
        igv_content.refresh()

    def comparison_records():
        return tuple(get_run_record(page_state["registries"].run_records, page_state[key])
                     for key in ("compare_first_id", "compare_second_id"))

    async def load_manifest(gene_value: object | None = None, *, reload_locus: str = "") -> None:
        workspace = current_workspace()
        # The input uses NiceGUI's non-loopback mode, so a click can reach the
        # server before the preceding keystroke event. The load action passes
        # the native value explicitly to make the review target deterministic.
        if gene_value is None:
            gene_value = getattr(refs.get("gene"), "value", page_state["gene"])
        gene = str(gene_value or "").strip()
        # Only an explicit, matching integer Reload request overrides the
        # usual peak focus. Ordinary Gene/coordinate Load keeps its behavior.
        view_window = coordinate_locus(reload_locus) if reload_locus == gene else None
        if workspace is None:
            _notify(state.t("workspace.select_first"), color="warning")
            return
        prerequisite = _load_prerequisite(gene)
        if prerequisite:
            page_state["prerequisite"] = prerequisite
            _notify(prerequisite, color="warning")
            igv_content.refresh()
            return

        # Manifest discovery touches state databases, reference annotations,
        # and potentially many output paths. Invalidate the old viewer before
        # handing that work to a thread so a stale track set cannot look like
        # the result for the newly entered gene.
        page_state["load_request_id"] += 1
        request_id = page_state["load_request_id"]
        page_state["gene"] = gene
        state.payload["igv_gene"] = gene
        # Put the loaded view in the address bar so reload and shared links come back to it.
        href = state.page_href("igv_compare", extra_query=igv_view_query(state.payload))
        ui.run_javascript(f"history.replaceState(history.state, '', {json.dumps(href)})")
        page_state.update(
            manifest=None,
            manifest_payload=None,
            options=None,
            error="",
            prerequisite="",
            reference_status="pending",
            tracks_status="pending",
            locus_status="pending",
            locus_message="",
            loading=True,
            loading_gene=gene,
            viewer_stale=False,
        )
        _dismiss_page_notification()
        igv_content.refresh()

        source = page_state["source"]
        study_id = page_state.get("study_id")
        run_record_id = page_state.get("run_record_id")
        # Freeze all registry objects used by the worker. Page controls can
        # change workspace/source while the manifest is being discovered; a
        # worker must finish against one coherent selection.
        registries_snapshot = deepcopy(page_state["registries"])
        workspace_snapshot = deepcopy(workspace)
        selected_study = get_study(registries_snapshot.studies, study_id)
        if selected_study is None or selected_study.workspace_id != workspace_snapshot.id:
            selected_study = next(
                (
                    study
                    for study in registries_snapshot.studies.studies
                    if study.workspace_id == workspace_snapshot.id
                ),
                None,
            )
        selected_record = get_run_record(
            registries_snapshot.run_records,
            run_record_id,
        )
        if selected_record is None or selected_record.workspace_id != workspace_snapshot.id:
            selected_record = default_run_record(
                registries_snapshot.run_records,
                workspace_id=workspace_snapshot.id,
            )
        workspace_db_path = workspace_snapshot.db_path
        workspace_root_fallback = workspace_snapshot.root_path
        compare_records = tuple(get_run_record(registries_snapshot.run_records, page_state[key])
                                for key in ("compare_first_id", "compare_second_id"))

        def _build_manifest_snapshot():
            if source == "compare":
                if any(record is None for record in compare_records):
                    raise ValueError(state.t("igv.compare_required"))
                manifest = comparison_manifest(*compare_records, gene=gene,
                                               url_for_file=lambda path: artifact_route(path)[2],
                                               workspaces={w.id: w for w in registries_snapshot.workspaces.workspaces},
                                               view_window=view_window)
                return manifest, {}, ""
            elif source == "study":
                if selected_study is None:
                    raise ValueError(state.t("igv.study_required"))
                base_config = dict(load_run_summary(workspace_db_path).config or {})
                manifest = study_manifest(
                    selected_study,
                    gene=gene,
                    base_config=base_config,
                    url_for_file=lambda path: artifact_route(path)[2],
                    view_window=view_window,
                )
                workspace_root = selected_study.workspace_root
            else:
                if selected_record is None:
                    raise ValueError(state.t("igv.run_required"))
                execution_config = resolve_execution_config(selected_record)
                if not execution_config.available:
                    raise ValueError(execution_config.error)
                original_root = (
                    selected_record.root_path
                    or selected_record.workspace_root
                    or workspace_root_fallback
                )
                base_config, workspace_root = run_view_paths(
                    selected_record, workspace_root=original_root,
                    path_mappings=workspace_snapshot.path_mappings,
                )
                manifest = single_run_manifest(
                    selected_record,
                    gene=gene,
                    base_config=base_config,
                    workspace_root=original_root,
                    path_mappings=workspace_snapshot.path_mappings,
                    url_for_file=lambda path: artifact_route(path)[2],
                    view_window=view_window,
                )
            return manifest, base_config, workspace_root

        try:
            manifest, base_config, workspace_root = await run.io_bound(
                _build_manifest_snapshot
            )
            if request_id != page_state["load_request_id"]:
                return
            for track in manifest.tracks:
                register_artifact_url(track.path)
            if manifest.comparison_id:
                register_artifact_url(manifest.run_sources[0]["reference_fasta"])
                register_artifact_url(manifest.run_sources[0]["reference_index"])
            payload = manifest_payload_with_reference(
                manifest,
                base_config=base_config,
                workspace_root=workspace_root,
            )
            reference_ready = payload.get("reference_status") == "ready"
            options = manifest_as_igv_options(payload) if reference_ready else None
        except Exception as exc:
            if request_id != page_state["load_request_id"]:
                return
            page_state.update(
                error=error_text(state.t, exc),
                manifest=None,
                manifest_payload=None,
                options=None,
                reference_status="error",
                tracks_status="error",
                locus_status="error",
                locus_message="",
                loaded_gene="",
                viewer_stale=False,
                loading=False,
                loading_gene="",
            )
            _notify(error_text(state.t, exc), color="negative")
        else:
            if request_id != page_state["load_request_id"]:
                return
            tracks_status = (
                "missing" if not manifest.tracks else "partial" if manifest.missing_tracks else "ready"
            )
            locus_status = "ready" if manifest.locus else "missing"
            locus_message = (
                ""
                if manifest.locus
                else state.t(
                    "igv.locus_missing_detail",
                    gene=gene,
                    genome=str(payload.get("genome") or "-"),
                    gtf=str(payload.get("reference_gtf") or "-"),
                )
            )
            can_load_igv = reference_ready and bool(manifest.tracks) and bool(manifest.locus)
            page_state.update(
                manifest=manifest,
                manifest_payload=payload,
                options=options if can_load_igv else None,
                error="",
                prerequisite="",
                reference_status="ready" if reference_ready else "missing",
                tracks_status=tracks_status,
                locus_status=locus_status,
                locus_message=locus_message,
                loaded_gene=gene,
                viewer_stale=False,
                loading=False,
                loading_gene="",
                render_token=page_state["render_token"] + 1,
            )
            _dismiss_page_notification()
        igv_content.refresh()

    def _load_prerequisite(gene: str) -> str:
        if page_state["source"] == "compare":
            first, second = comparison_records()
            if first is None or second is None:
                return state.t("igv.compare_required")
            if first.id == second.id or first.invocation_id == second.invocation_id:
                return state.t("igv.compare_distinct")
        elif page_state["source"] == "study":
            if not study_summaries(
                page_state["registries"].studies,
                workspace_id=page_state["workspace_id"],
                limit=1,
            ):
                return state.t("igv.prerequisite.study")
            if current_study() is None:
                return state.t("igv.study_required")
        else:
            if not run_record_summaries(
                page_state["registries"].run_records,
                workspace_id=page_state["workspace_id"],
                limit=1,
            ):
                return state.t("igv.prerequisite.run")
            if current_run_record() is None:
                return state.t("igv.run_required")
        if not gene:
            return state.t("igv.prerequisite.gene")
        return ""

    @ui.refreshable
    def igv_content() -> None:
        workspace = current_workspace()
        study_rows = study_summaries(
            page_state["registries"].studies,
            workspace_id=page_state["workspace_id"],
            limit=40,
        )
        run_rows = run_record_summaries(
            page_state["registries"].run_records,
            workspace_id=page_state["workspace_id"],
            limit=50,
        )
        study_options = {
            item.id: f"{item.name} ({item.variant_count})"
            for item in study_rows
        }
        run_options = {item.id: item.label for item in run_rows}
        compare_options = {record.id: comparison_run_label(record)
                           for record in page_state["registries"].run_records.records
                           if record.status == "succeeded" and record.history_source == "invocation"
                           and record.invocation_id}
        for key, offset in (("compare_first_id", 0), ("compare_second_id", 1)):
            if page_state[key] not in compare_options:
                ids = list(compare_options)
                page_state[key] = ids[offset] if len(ids) > offset else None

        if page_state["study_id"] not in study_options and study_rows:
            page_state["study_id"] = study_rows[0].id
        if page_state["run_record_id"] not in run_options and run_rows:
            page_state["run_record_id"] = run_rows[0].id

        page_state["prerequisite"] = _load_prerequisite(
            str(page_state.get("gene") or "").strip()
        )

        if workspace is None:
            with ui.column().classes("product-panel-muted gap-2"):
                ui.label(state.t("workspace.empty")).classes("product-section-title")
                ui.label(state.t("workspace.empty_detail")).classes("product-small-muted")
            return

        with ui.element("div").classes("genome-workbench"):
            _render_commandbar(study_options, run_options, compare_options)
            if page_state["prerequisite"]:
                with ui.element("div").classes("genome-prerequisite-banner"):
                    ui.icon("info").classes("genome-prerequisite-icon")
                    ui.label(page_state["prerequisite"]).classes(
                        "genome-prerequisite-message"
                    )
            with ui.element("div").classes("genome-stale-banner") as stale_banner:
                ui.icon("sync_problem")
                stale_message = ui.label().classes("genome-stale-message")
            refs["stale_banner"] = stale_banner
            refs["stale_message"] = stale_message
            _sync_gene_status_controls()
            with ui.element("div").classes("genome-main"):
                _render_viewer()
                _render_inspector()
            _render_statusbar()

    def _render_commandbar(
        study_options: dict[str, str],
        run_options: dict[str, str],
        compare_options: dict[str, str],
    ) -> None:
        with ui.element("div").classes("genome-commandbar genome-commandbar-compare"
                                       if page_state["source"] == "compare" else "genome-commandbar"):
            ui.toggle(
                {
                    "study": state.t("igv.source.study_mode"),
                    "single": state.t("igv.source.run_mode"),
                    "compare": state.t("igv.source.compare_mode"),
                },
                value=page_state["source"],
                on_change=lambda e: set_source(e.value),
            ).classes("genome-source-toggle").props("no-caps")
            if page_state["source"] == "compare":
                with ui.column().classes("genome-compare-controls gap-1"):
                    for key, label_key, slot in (("compare_first_id", "igv.compare_first", "A"),
                                                 ("compare_second_id", "igv.compare_second", "B")):
                        ui.select(options=compare_options, value=page_state[key],
                                  on_change=lambda e, key=key: select_comparison_record(key, e.value)
                                  ).classes("genome-context-control genome-compare-select").props(
                            f'dense outlined options-dense hide-bottom-space prefix="{slot}" '
                            f'aria-label="{state.t(label_key)}"')
                    if len(compare_options) < 2:
                        ui.label(state.t("igv.compare_eligible", count=len(compare_options))).classes(
                            "genome-compare-hint"
                        )
            elif page_state["source"] == "study":
                ui.select(
                    options=study_options,
                    value=page_state["study_id"],
                    on_change=lambda e: select_study(e.value),
                ).classes("genome-context-control").props(
                    f'dense outlined options-dense hide-bottom-space aria-label="{state.t("igv.saved_studies")}"'
                )
            else:
                ui.select(
                    options=run_options,
                    value=page_state["run_record_id"],
                    on_change=lambda e: select_run_record(e.value),
                ).classes("genome-context-control").props(
                    f'dense outlined options-dense hide-bottom-space aria-label="{state.t("igv.run_records")}"'
                )
            with ui.element("div").classes("genome-gene-field"):
                ui.label(state.t("igv.gene")).classes("genome-inline-label")
                refs["gene"] = ui.input(
                    value=page_state["gene"],
                    placeholder=state.t("igv.gene_placeholder"),
                    on_change=lambda e: _capture_gene_input(e.value),
                ).classes("genome-gene-control").props(
                    f'dense outlined hide-bottom-space aria-label="{state.t("igv.gene")}"'
                )
            async def load_from_current_input() -> None:
                # QInput is non-loopback: read the browser's native value at
                # click time instead of relying on a possibly queued model
                # update from the preceding keystroke.
                request = await ui.run_javascript("""(() => {
                    const button = document.querySelector('.genome-load-action');
                    const reload = button?.dataset.scpolyaReloadLocus || '';
                    if (button) delete button.dataset.scpolyaReloadLocus;
                    return {gene: document.querySelector('.genome-gene-control input')?.value || '', reload};
                })()""")
                await load_manifest(request["gene"], reload_locus=request["reload"])

            load_button = ui.button(
                state.t("igv.load"),
                icon="visibility",
                on_click=load_from_current_input,
            ).classes("genome-load-action").props("unelevated no-caps color=primary")
            refs["load_button"] = load_button
            refs["gene"].on("keydown.enter", load_from_current_input)
            blocked_reason = page_state["prerequisite"] or (state.t("igv.loading") if page_state["loading"] else "")
            with load_button:
                # Kept even when enabled so typing can update or clear the reason in place.
                refs["load_tip"] = ui.tooltip(blocked_reason)
            refs["load_tip"].set_visibility(bool(blocked_reason))
            if blocked_reason:
                # disable() rather than the raw prop, so set_enabled(True) after typing really re-enables it.
                load_button.disable()
            if page_state["loading"]:
                load_button.props("loading")
            refresh = ui.button(
                icon="refresh",
                on_click=reload_registries,
            ).props(f'flat dense aria-label="{state.t("common.refresh")}"')
            refresh.tooltip(state.t("common.refresh"))

    def _capture_gene_input(value: object = "") -> None:
        """Keep typing local to the input; rebuilding the workbench per key loses focus/value."""
        page_state["gene"] = str(value or "").strip()
        state.payload["igv_gene"] = page_state["gene"]
        if page_state["loading"] and page_state.get("gene") != page_state.get("loading_gene"):
            # A new query supersedes the in-flight worker. This prevents a
            # slower old lookup from replacing the state for the newly typed
            # gene when it eventually returns.
            page_state["load_request_id"] += 1
            page_state["loading"] = False
            page_state["loading_gene"] = ""
        prerequisite = _load_prerequisite(page_state["gene"])
        page_state["prerequisite"] = prerequisite
        page_state.update(
            error="",
            locus_message="",
            viewer_stale=bool(page_state.get("loaded_gene"))
            and page_state["gene"] != page_state["loaded_gene"],
        )
        load_button = refs.get("load_button")
        if load_button is not None:
            load_button.set_enabled(not prerequisite and not page_state["loading"])
        load_tip = refs.get("load_tip")
        if load_tip is not None:
            load_tip.text = prerequisite
            load_tip.set_visibility(bool(prerequisite))

        _sync_gene_status_controls()
        if page_state.get("manifest") is None:
            message, _error = _status_message(None)
            ui.run_javascript(
                "const s = document.getElementById('scpolya-product-igv-status');"
                f" if (s) s.textContent = {json.dumps(message)};"
            )

    def _sync_gene_status_controls() -> None:
        stale = bool(page_state.get("viewer_stale"))
        loading = bool(page_state.get("loading"))
        banner = refs.get("stale_banner")
        message = refs.get("stale_message")
        if banner is not None:
            banner.set_visibility(stale and not loading)
        if message is not None and stale and not loading:
            message.text = state.t(
                "igv.gene_changed",
                loaded_gene=page_state.get("loaded_gene") or "-",
                gene=page_state.get("gene") or "-",
            )

    def _render_viewer() -> None:
        manifest = page_state.get("manifest")
        options = page_state.get("options")
        summary = manifest_summary(manifest) if manifest is not None else None

        with ui.element("section").classes("genome-viewer"):
            with ui.row().classes("genome-viewer-meta w-full"):
                ui.label(page_state["gene"]).classes("genome-viewer-gene")
                ui.label(f"{state.t('igv.metric.locus')}: {summary.locus if summary is not None else '-'}").classes(
                    "genome-viewer-locus"
                )
                ui.element("div").classes("workbench-toolbar-spacer")
                if page_state["error"]:
                    ui.label(page_state["error"]).classes("genome-viewer-error")
                elif page_state["locus_message"]:
                    ui.label(page_state["locus_message"]).classes(
                        "genome-viewer-warning"
                    )
            with ui.element("div").classes(
                "genome-viewer-canvas genome-viewer-canvas-ready"
                if manifest is not None and options is not None
                else "genome-viewer-canvas genome-viewer-canvas-empty"
            ):
                ui.html(
                    "<div id='scpolya-product-igv-viewer' "
                    "class='igv-viewer genome-igv-viewer'></div>"
                ).classes("genome-igv-wrapper")
                if manifest is None or options is None:
                    with ui.column().classes("genome-empty-state gap-2"):
                        ui.icon(
                            "sync"
                            if page_state["loading"]
                            else "warning"
                            if manifest is not None
                            else "visibility"
                        )
                        ui.label(_viewer_empty_title()).classes("product-section-title")
                        ui.label(_viewer_empty_detail()).classes("product-small-muted")
                    ui.run_javascript(
                        "window.scpolyaProductClearIgv && "
                        "window.scpolyaProductClearIgv()"
                    )
                elif options:
                    messages = {
                        name: state.t(f"igv.browser.{name}", count="{count}", error="{error}", range="{range}")
                        for name in ("not_loaded", "no_tracks", "loaded", "failed", "locus_search",
                                     "transcript_scope", "transcript_reload", "transcript_reload_invalid", "loading", "partial", "empty_selection")
                    }
                    shown = {
                        **options,
                        "tracks": [
                            {**track, "name": _track_name(state, str(track.get("name") or ""))}
                            for track in options.get("tracks") or []
                        ],
                    }
                    ui.run_javascript(
                        f"window.scpolyaProductLoadIgv({json.dumps(shown)}, {json.dumps(messages)})"
                    )

    def _render_inspector() -> None:
        selection_label = (
            state.t("igv.variants")
            if page_state["source"] == "study"
            else state.t("igv.selection")
        )
        with ui.element("aside").classes("genome-inspector"):
            _render_track_controls()
            with ui.tabs(value="selection").classes(
                "genome-inspector-tabs w-full"
            ) as inspector_tabs:
                ui.tab("selection", label=selection_label)
                ui.tab("tracks", label=state.t("igv.tracks"))
            with ui.tab_panels(
                inspector_tabs,
                value="selection",
                animated=False,
                keep_alive=True,
            ).classes("genome-inspector-panels w-full"):
                with ui.tab_panel("selection"):
                    _render_selection_panel()
                with ui.tab_panel("tracks"):
                    _render_tracks_panel()

    def _render_selection_panel() -> None:
        if page_state["source"] == "compare":
            ui.label(state.t("igv.compare_scope")).classes("product-small-muted genome-inspector-note")
            for slot, record in zip(("A", "B"), comparison_records()):
                if record is None:
                    continue
                with ui.element("div").classes("genome-inspector-summary"):
                    ui.label(f"{slot} · {comparison_run_label(record)}").classes("product-section-title")
                with ui.element("div").classes("genome-selection-details"):
                    _detail_row(state.t("igv.compare_invocation"), record.invocation_id)
                    _detail_row(state.t("common.status"), status_text(state.t, record.status))
                    _detail_row(state.t("common.path"), record.root_path or record.workspace_root)
                    manifest = page_state.get("manifest")
                    if manifest is not None:
                        source = next((s for s in manifest.run_sources if s["run_record_id"] == record.id), {})
                        if source.get("view_paths"):
                            _detail_row(state.t("igv.view_path"), source["view_paths"]["root_path"])
                    resolution = resolve_execution_config(record)
                    _detail_row(state.t("igv.compare_config"), resolution.config_hash or resolution.error)
            manifest = page_state.get("manifest")
            if manifest is not None:
                ui.label(manifest.reference_compatibility).classes("product-small-muted")
        elif page_state["source"] == "study":
            study = current_study()
            if study is None:
                ui.label(state.t("igv.no_studies")).classes("product-small-muted genome-inspector-empty")
                return
            with ui.element("div").classes("genome-inspector-summary"):
                ui.label(study.name).classes("product-section-title")
                ui.label(study.id).classes("workspace-path workspace-path-compact")
            with ui.element("div").classes("genome-variant-list"):
                for variant in study_variant_summaries(
                    page_state["registries"].studies,
                    study.id,
                ):
                    with ui.element("div").classes("genome-variant-row"):
                        with ui.row().classes("items-center justify-between gap-2 w-full"):
                            ui.label(variant.id).classes("study-variant-id")
                            ui.label(
                                state.t(
                                    "igv.variant_artifacts",
                                    ready=variant.ready_artifacts,
                                )
                            ).classes("genome-ready-count")
                        ui.label(variant.label).classes(
                            "product-small-muted genome-variant-label"
                        )
        else:
            record = current_run_record()
            if record is None:
                ui.label(state.t("igv.no_runs")).classes("product-small-muted genome-inspector-empty")
                return
            with ui.element("div").classes("genome-inspector-summary"):
                ui.label(record.label).classes("product-section-title")
                ui.label(record.id).classes("workspace-path workspace-path-compact")
            with ui.element("div").classes("genome-selection-details"):
                _detail_row(state.t("common.status"), status_text(state.t, record.status))
                _detail_row(state.t("common.kind"), _translated(f"runs.kind.{record.kind}", record.kind))
                _detail_row(state.t("runs.metric.run_id"), record.run_id or "-")
                _detail_row(
                    state.t("common.path"),
                    record.root_path or record.workspace_root or "-",
                )
                manifest = page_state.get("manifest")
                if manifest is not None and manifest.view_paths:
                    _detail_row(state.t("igv.view_path"), manifest.view_paths["root_path"])

    def _render_track_controls() -> None:
        if page_state.get("options") is None:
            return
        # Browser-owned controls change actual loaded tracks without rebuilding
        # the viewer or losing its locus. Their labels use the page locale.
        labels = {key: state.t(f"igv.preset.{key}") for key in ("label", "core", "all", "custom", "apply", "hint")}
        ui.html(
            '<div class="genome-track-controls">'
            f'<label for="scpolya-track-preset">{escape(labels["label"])}</label>'
            '<select id="scpolya-track-preset" disabled>'
            + ''.join(f'<option value="{key}">{escape(labels[key])}</option>' for key in ("core", "all", "custom"))
            + '</select>'
            f'<p class="product-small-muted">{escape(labels["hint"])}</p>'
            f'<details id="scpolya-track-custom"><summary>{escape(labels["custom"])}</summary>'
            '<div id="scpolya-track-choices"></div></details>'
            f'<button id="scpolya-track-apply" type="button" disabled>{escape(labels["apply"])}</button>'
            '</div>'
        )

    def _render_tracks_panel() -> None:
        manifest = page_state.get("manifest")
        payload = page_state.get("manifest_payload")
        if manifest is None or payload is None:
            ui.label(state.t("igv.tracks_empty")).classes("product-small-muted genome-inspector-empty")
            return
        summary = manifest_summary(manifest)
        with ui.element("div").classes("genome-inspector-summary"):
            ui.label(
                state.t(
                    "igv.compact_status",
                    reference=status_text(state.t, payload.get("reference_status") or "-"),
                    tracks=summary.track_count,
                    missing=summary.missing_count,
                )
            ).classes("product-small-muted")
        if manifest.missing_tracks:
            with ui.column().classes("genome-missing-list gap-1"):
                ui.label(state.t("igv.missing_title", count=len(manifest.missing_tracks))).classes(
                    "genome-missing-title"
                )
                record = current_run_record() if page_state["source"] == "single" else None
                run_status = str(getattr(record, "status", "") or "")
                for missing in manifest.missing_tracks:
                    ui.label(_missing_track_text(state, missing, run_status=run_status)).classes(
                        "genome-missing-item"
                    )
        if manifest.historical_evidence:
            ui.label(state.t("igv.historical_evidence")).classes("product-small-muted")
            if manifest.historical_evidence.get("unavailable"):
                ui.label(manifest.historical_evidence["unavailable"]).classes("product-small-muted product-status-missing")
        track_rows = [
            {
                "name": _track_name(state, track.name),
                "role": track.role,
                "format": track.format,
                "path": track.path,
            }
            for track in summary.tracks
        ]
        render_path_rows(
            track_rows,
            name_key="name",
            status_key="role",
            extra_keys=("format",),
            path_key="path",
        )
        with ui.expansion(
            state.t("igv.manifest"),
            icon="data_object",
        ).classes("genome-manifest-expansion w-full").props("dense"):
            render_terminal(json.dumps(payload, indent=2, sort_keys=True), translate=state.t, max_height="420px")

    def _render_statusbar() -> None:
        manifest = page_state.get("manifest")
        payload = page_state.get("manifest_payload")
        summary = manifest_summary(manifest) if manifest is not None else None
        status, status_error = _status_message(summary)
        with ui.element("footer").classes("genome-statusbar"):
            dot = (
                "genome-status-error"
                if status_error
                else "genome-status-idle"
                if page_state.get("manifest") is None
                else "genome-status-partial"
                if page_state["tracks_status"] == "partial"
                else ""
            )
            ui.icon("circle").classes(f"genome-status-dot {dot}".strip())
            ui.html(
                f"<span id='scpolya-product-igv-status'>{escape(status)}</span>"
            ).classes("genome-runtime-status")
            ui.element("div").classes("workbench-toolbar-spacer")
            _status_item(
                state.t("igv.metric.locus"),
                (
                    summary.locus
                    if summary is not None and page_state["locus_status"] == "ready"
                    else page_state["locus_status"]
                ),
                "genome-status-locus genome-status-locus-missing"
                if page_state["locus_status"] == "missing"
                else "genome-status-locus",
            )
            _status_item(
                state.t("igv.metric.reference"),
                page_state["reference_status"],
                "genome-status-reference genome-status-reference-missing"
                if page_state["reference_status"] == "missing"
                else "genome-status-reference",
            )
            _status_item(
                state.t("igv.metric.tracks"),
                page_state["tracks_status"],
                "genome-status-tracks genome-status-tracks-missing"
                if page_state["tracks_status"] == "missing"
                else "genome-status-tracks genome-status-tracks-partial"
                if page_state["tracks_status"] == "partial"
                else "genome-status-tracks",
            )
            _status_item(
                state.t("igv.metric.missing"),
                summary.missing_count if summary is not None else 0,
            )

    def _viewer_empty_title() -> str:
        if page_state["loading"]:
            return state.t("igv.loading")
        if page_state["prerequisite"]:
            return page_state["prerequisite"]
        if page_state["reference_status"] == "missing":
            return state.t("igv.reference_required")
        if page_state["locus_status"] == "missing":
            return state.t("igv.locus_missing", gene=page_state["gene"])
        if page_state["tracks_status"] == "missing":
            return state.t("igv.tracks_missing")
        return state.t("igv.viewer_empty")

    def _viewer_empty_detail() -> str:
        if page_state["error"]:
            return page_state["error"]
        if page_state["loading"]:
            return state.t("igv.loading_detail")
        if page_state["prerequisite"]:
            return state.t("igv.viewer_empty_detail")
        if page_state["reference_status"] == "missing":
            return state.t("igv.reference_missing_detail")
        if page_state["locus_status"] == "missing":
            return page_state["locus_message"] or state.t(
                "igv.locus_missing_detail",
                gene=page_state["gene"],
                genome="-",
                gtf="-",
            )
        if page_state["tracks_status"] == "missing":
            return state.t("igv.tracks_missing_detail")
        return state.t("igv.viewer_empty_detail")

    def _status_message(summary) -> tuple[str, bool]:
        if page_state["error"]:
            return page_state["error"], True
        if page_state["loading"]:
            return state.t("igv.loading"), False
        if page_state["prerequisite"]:
            # A missing choice (gene, study, run) is guidance, not a failure.
            return page_state["prerequisite"], False
        if page_state.get("viewer_stale"):
            return state.t(
                "igv.gene_changed",
                loaded_gene=page_state.get("loaded_gene") or "-",
                gene=page_state.get("gene") or "-",
            ), True
        if page_state["reference_status"] == "missing":
            return state.t("igv.reference_missing_detail"), True
        if page_state["locus_status"] == "missing":
            return state.t("igv.locus_missing", gene=page_state["gene"]), True
        if page_state["tracks_status"] == "missing":
            return state.t("igv.tracks_missing"), True
        if summary is None:
            return state.t("igv.viewer_empty"), False
        # Discovery is ready, but IGV has not yet downloaded / painted tracks.
        # Only the browser runtime can publish the final loaded count.
        options = page_state.get("options") or {}
        core_count = sum(bool(track.get("scpolyaCore")) for track in options.get("tracks", ()))
        return state.t("igv.browser.loading", count=f"0/{core_count}"), False

    def _detail_row(label: str, value: object) -> None:
        with ui.element("div").classes("genome-detail-row"):
            ui.label(label).classes("genome-detail-label")
            ui.label(str(value)).classes("genome-detail-value")

    def _status_item(
        label: str,
        value: object,
        extra_class: str = "",
    ) -> None:
        display_value = "-" if value is None or value == "" else status_text(state.t, str(value))
        with ui.element("div").classes(f"genome-status-item {extra_class}".strip()):
            ui.label(label).classes("genome-status-label")
            ui.label(display_value).classes("genome-status-value")

    igv_content()


# Output scope ids in manifest notes -> the pipeline's artifact names (translated via the catalog).
_SCOPE_LABELS = {
    "final.filtered_peaks": "Mispriming-filtered peaks",
    "final.filtered_peaks_merged": "Merged mispriming-filtered peaks",
    "final.assigned_peaks": "Assigned peaks",
    "final.annotated_peaks_bed": "Resolved annotated peaks BED",
    "final.annotated_peaks_bigbed": "Resolved annotated peaks BigBed",
    "final.annotated_peaks_gtf": "Resolved annotated peaks GTF",
    "cohort_output.bigwigs": "Cohort BigWig tracks",
    "cohort_output.merged_peaks": "Strand-specific merged peaks",
}

_MISSING_PATTERNS = (
    (re.compile(r"^(?P<who>.+): no annotated BED/BigBed artifact$"), "igv.missing.no_bed"),
    (re.compile(r"^current run: (?P<role>\S+) (?P<strand>[+-]) is unavailable for this configuration$"), "igv.missing.role"),
    (re.compile(r"^current run: output namespace integrity error: (?P<message>.+)$"), "igv.missing.integrity"),
    (re.compile(r"^current run: (?P<scope>\S+) is unavailable$"), "igv.missing.scope"),
)


def _missing_track_text(state: ProductAppState, text: str, *, run_status: str = "") -> str:
    """Manifest notes are stored in English; show them in the user's language.

    The manifest cannot tell "never produced by this config" from "the run stopped
    first", so an unfinished run gets the latter wording.
    """
    unfinished = bool(run_status) and run_status.casefold() != "succeeded"
    for pattern, key in _MISSING_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        params = match.groupdict()
        if unfinished and key in {"igv.missing.role", "igv.missing.scope", "igv.missing.no_bed"}:
            key = f"{key}_unfinished"
            params["status"] = status_text(state.t, run_status)
            if state.locale == "en":
                params["status"] = params["status"].casefold()
        if "scope" in params:
            params["scope"] = artifact_label(state.t, _SCOPE_LABELS.get(params["scope"], params["scope"]))
        if params.get("who") == "current run":
            params["who"] = state.t("igv.missing.current_run")
        if "role" in params:
            label_key = f"igv.track_role.{params['role']}"
            label = state.t(label_key)
            params["role"] = params["role"] if label == f"[[{label_key}]]" else label
        return state.t(key, **params)
    return text


_TRACK_COVERAGE = re.compile(r"^(?P<source>.+) coverage(?P<suffix> [+-])?$")


def _track_name(state: ProductAppState, name: str) -> str:
    """Fixed manifest track names in the user's language; run and variant labels stay as they are."""
    key = "igv.track_name." + re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
    text = state.t(key)
    if text != f"[[{key}]]":
        return text
    match = _TRACK_COVERAGE.match(name)
    if match is not None:
        return state.t("igv.track_name.coverage", source=match["source"], suffix=match["suffix"] or "")
    return name
