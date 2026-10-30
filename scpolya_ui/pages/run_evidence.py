"""Run-scoped native evidence, using the existing product workbench shell."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scpolya_ui.components.confirm_dialog import open_confirmation_dialog
from scpolya_ui.services import run_evidence_service as service
from scpolya_ui.services.run_service import get_run_record
from scpolya_ui.services.workspace_service import get_workspace
from scpolya_ui.state.app_state import ProductAppState

PAGE_KEY = "run_evidence"


def render_run_evidence_page(state: ProductAppState) -> None:
    from nicegui import run, ui

    registries = state.registries
    records = [r for r in registries.run_records.records
               if r.workspace_id == state.selected_workspace_id and r.status == "succeeded"] if registries else []
    selected = state.payload.get("selected_run_record_id")
    ps: dict[str, Any] = {"record": (selected if any(r.id == selected for r in records) else "") if selected else (records[0].id if records else ""),
                          "source": "", "plan": None, "target": None, "busy": False, "error": "",
                          "requested_table": str(state.payload.get("evidence_table") or "")}
    filters = {key: "" for key in ("gene_id", "cell_barcode", "strand", "peak_name")}
    workspace = get_workspace(registries.workspaces, state.selected_workspace_id) if registries else None
    mappings = tuple(workspace.path_mappings) if workspace else ()

    def error_payload(exc: Exception) -> dict[str, str]:
        return {"code": getattr(exc, "code", "invalid_input"), "detail": str(exc)}

    def show_error(error: dict[str, str]) -> None:
        ui.label(state.t("evidence.error." + error.get("code", "invalid_input"))).classes("ov-alert ov-alert-info w-full")
        if error.get("detail"):
            with ui.expansion(state.t("evidence.error_details"), icon="o_info").classes("w-full"):
                ui.label(error["detail"]).classes("product-small-muted break-all")

    async def download(target: Path, name: str, filename: str | None = None) -> None:
        try:
            raw = await run.io_bound(service.download_bytes, target, name)
            ui.download(raw, filename=filename or name)
        except Exception as exc:
            ps["error"] = error_payload(exc)
            preview_panel.refresh()

    def invalidate() -> None:
        ps["plan"] = None
        ps["error"] = ""
        preview_panel.refresh()

    def filter_changed(key: str, value: Any) -> None:
        filters[key] = str(value or "")
        invalidate()

    def choose_record(value: str) -> None:
        ps.update(record=value, source="", target=None, plan=None, error="", requested_table="")
        source_panel.refresh()
        preview_panel.refresh()
        result_panel.refresh()

    async def prepare() -> None:
        if ps["busy"]:
            return
        record = get_run_record(registries.run_records, ps["record"])
        identity = (ps["record"], ps["source"], dict(filters))
        ps.update(busy=True, error="", plan=None)
        preview_panel.refresh()
        try:
            field_columns = ps.get("field_columns", {})
            query = {field_columns.get(key, key): value for key, value in filters.items()}
            plan = await run.io_bound(service.preview, record, mappings, ps["source"], query)
            if identity == (ps["record"], ps["source"], filters):
                ps["plan"] = plan
        except Exception as exc:
            ps["error"] = error_payload(exc)
        finally:
            ps["busy"] = False
            preview_panel.refresh()

    def execute(plan: dict[str, Any]) -> None:
        try:
            ps["target"] = service.start(plan)
            ps.update(plan=None, error="")
        except Exception as exc:
            ps["error"] = error_payload(exc)
        preview_panel.refresh()
        result_panel.refresh()

    def confirm() -> None:
        plan = ps.get("plan")
        if plan is None:
            return
        open_confirmation_dialog(
            title=state.t("evidence.confirm_title"), message=state.t("evidence.confirm_detail"),
            confirm_label=state.t("evidence.start"), cancel_label=state.t("common.cancel"),
            details=((state.t("evidence.table"), Path(plan["source"]["path"]).name),
                     (state.t("evidence.filters"), json.dumps(plan["query"], ensure_ascii=False) or "{}"),
                     (state.t("evidence.source_size"), f"{plan['source']['bytes'] / 1024**2:.2f} MiB"),
                     (state.t("evidence.cost"), state.t("evidence.cost_detail"))),
            on_confirm=lambda: execute(plan), icon="o_manage_search")

    @ui.refreshable
    def source_panel() -> None:
        if not ps["record"]:
            ui.label(state.t("evidence.choose_execution")).classes("env-note")
            return
        try:
            record = get_run_record(registries.run_records, ps["record"])
            sources, binding = service.discover(record, mappings)
            if not sources:
                ui.label(state.t("evidence.no_tables")).classes("env-note")
                return
            if not ps["source"] and ps["requested_table"]:
                matches = [s for s in sources if Path(s.path).name == ps["requested_table"]]
                service.require(len(matches) == 1, "The requested table is unavailable or ambiguous in this execution", "source_missing")
                ps["source"] = matches[0].path
            if ps["source"] not in {s.path for s in sources}:
                ps["source"] = sources[0].path
            active = next(s for s in sources if s.path == ps["source"])
            ps["field_columns"] = {key: key for key in filters}
            if "quantification_unit_id" in active.columns:
                ps["field_columns"]["cell_barcode"] = "quantification_unit_id"
            if "intronic_pas_id" in active.columns:
                ps["field_columns"]["peak_name"] = "intronic_pas_id"
            with ui.row().classes("w-full items-center gap-3"):
                def select_table(e):
                    ps["source"] = e.value
                    invalidate()
                    source_panel.refresh()
                ui.select({s.path: f"{state.t('evidence.table.' + s.key)} · {Path(s.path).name}" for s in sources},
                          value=ps["source"], label=state.t("evidence.table"), on_change=select_table
                          ).props("dense outlined options-dense").classes("w-full")
            ui.label(state.t("evidence.association", invocation=binding["invocation_id"][:12],
                             config=binding["config_hash"])).classes("product-small-muted")
            with ui.row().classes("w-full items-center gap-3"):
                for key in filters:
                    ui.input(state.t("evidence.filter." + ps["field_columns"].get(key, key)), value=filters[key],
                             on_change=lambda e, key=key: filter_changed(key, e.value)
                             ).props("dense outlined clearable").classes("flex-1 min-w-40")
            ui.label(state.t("evidence.filter_help")).classes("product-small-muted")
            ui.button(state.t("evidence.preview"), icon="o_manage_search", on_click=prepare
                      ).props("outline no-caps")
        except Exception as exc:
            with ui.column().classes("ov-alert ov-alert-info w-full gap-1"):
                show_error(error_payload(exc))
                ui.label(state.t("evidence.recover")).classes("ov-alert-detail")

    @ui.refreshable
    def preview_panel() -> None:
        if ps["error"]:
            show_error(ps["error"])
        if ps["busy"]:
            with ui.row().classes("items-center gap-2"):
                ui.spinner(size="sm")
                ui.label(state.t("evidence.preparing"))
        plan = ps["plan"]
        if plan:
            with ui.column().classes("env-card w-full gap-2"):
                ui.label(state.t("evidence.preview_title")).classes("ov-run-name")
                ui.label(state.t("evidence.cost_detail")).classes("product-small-muted")
                ui.label(f"SHA256 {plan['source']['sha256']}").classes("product-small-muted break-all")
                ui.label(state.t("evidence.exact_filters", filters=json.dumps(plan["query"], ensure_ascii=False))).classes("product-small-muted")
                ui.button(state.t("evidence.start"), icon="o_play_arrow", on_click=confirm).props("unelevated no-caps color=primary")

    def choose_attempt(value: str) -> None:
        ps["target"] = Path(value)
        result_panel.refresh()

    @ui.refreshable
    def result_panel() -> None:
        recent = []
        labels = {}
        for target in service.recent():
            try:
                plan = json.loads((target / "preview.json").read_text())
                if plan["binding"]["run_record_id"] == ps["record"]:
                    recent.append(target)
                    filters_label = ", ".join(f"{k}={v}" for k, v in plan.get("query", {}).items()) or state.t("common.all")
                    labels[str(target)] = (f"{plan.get('created_at', '')} · {state.t('evidence.table.' + plan['table']['key'])}"
                                           f" · {filters_label[:70]} · {target.name}")
            except (OSError, ValueError, KeyError):
                pass
        if not recent:
            return
        target = ps["target"] if ps["target"] in recent else recent[0]
        ps["target"] = target
        current = service.status(target)
        with ui.row().classes("w-full items-center gap-3"):
            ui.label(state.t("evidence.results")).classes("ov-run-name")
            ui.select(labels, value=str(target),
                      on_change=lambda e: choose_attempt(e.value)).props("dense outlined options-dense").classes("flex-1 min-w-0")
            ui.label(state.t("evidence.status." + current["status"])).classes("sc-scorer-chip")
        if current["status"] in {"starting", "running"}:
            ui.linear_progress().props("indeterminate")
            ui.label(state.t("evidence.working")).classes("product-small-muted")
            return
        if current["status"] == "failed":
            show_error({"code": current.get("error_code", "worker_failed"), "detail": current.get("error", "")})
            ui.label(state.t("evidence.failed_help")).classes("product-small-muted")
            ui.button(state.t("evidence.download_log"), icon="o_download", on_click=lambda: download(target, "worker.log")).props("flat no-caps")
            return
        try:
            result = service.read_result(target)
        except Exception as exc:
            show_error(error_payload(exc))
            return
        ui.label(state.t("evidence.row_count", matched=result["matched_rows"], scanned=result["scanned_rows"],
                         displayed=result["displayed_rows"])).classes("product-small-muted")
        if result["truncated"]:
            ui.label(state.t("evidence.truncated")).classes("env-note")
        with ui.row().classes("w-full items-center gap-2"):
            ui.button(state.t("evidence.download_report"), icon="o_download", on_click=lambda: download(target, "report.zip")).props("outline no-caps")
            ui.button(state.t("evidence.download_rows"), icon="o_table_view", on_click=lambda: download(target, "rows.tsv")).props("flat no-caps")
            ui.button(state.t("evidence.download_source"), icon="o_download", on_click=lambda: download(target, "source.tsv.gz", Path(result["source"]["path"]).name)).props("flat no-caps")
            gene = result["query"].get("gene_id")
            ui.button(state.t("runs.open_genome"), icon="o_ssid_chart", on_click=lambda: ui.navigate.to(
                state.page_href("igv_compare", extra_query={"igv_source": "single", "run_record": ps["record"], "gene": gene})
            )).props("flat no-caps")
        columns = [{"name": k, "label": k, "field": k, "align": "left", "sortable": True} for k in result["columns"]]
        ui.table(columns=columns, rows=[dict(row, _row=i) for i, row in enumerate(result["rows"])], row_key="_row", pagination=25
                 ).classes("w-full re-table").props("flat bordered dense wrap-cells")
        with ui.expansion(state.t("evidence.provenance"), icon="o_info").classes("w-full"):
            ui.label(result["source"]["path"]).classes("product-small-muted break-all")
            ui.label(f"SHA256 {result['source']['sha256']}").classes("product-small-muted break-all")
            ui.label(state.t("evidence.snapshot_note")).classes("product-small-muted")
            ui.label(state.t("evidence.science_note")).classes("product-small-muted")

    with ui.column().classes("env-page sc-page w-full gap-4"):
        with ui.row().classes("ov-header w-full"):
            with ui.column().classes("ov-heading gap-0"):
                ui.label(state.t("nav.run_evidence")).classes("ov-title")
                ui.label(state.t("page.subtitle.run_evidence")).classes("env-subtitle")
        ui.label(state.t("evidence.intro")).classes("env-note")
        if not records:
            ui.label(state.t("evidence.no_runs")).classes("env-note")
            ui.button(state.t("nav.runs"), on_click=lambda: ui.navigate.to(state.page_href("runs"))).props("outline no-caps")
            return
        ui.select({r.id: f"{r.label} · {(r.invocation_id or r.job_id or r.id)[:12]}" for r in records},
                  value=ps["record"] or None, label=state.t("evidence.run"), on_change=lambda e: choose_record(e.value)
                  ).props("dense outlined options-dense").classes("w-full")
        ui.label(state.t("evidence.snapshot_note")).classes("product-small-muted")
        source_panel()
        preview_panel()
        result_panel()

    def poll() -> None:
        target = ps["target"]
        if target:
            value = service.status(target)
            stamp = json.dumps(value, sort_keys=True)
            if stamp != ps.get("last_status"):
                ps["last_status"] = stamp
                result_panel.refresh()
    ui.timer(1.0, poll)
