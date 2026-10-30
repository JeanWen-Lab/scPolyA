from __future__ import annotations

from typing import Any

from scpolya_ui.components.interaction import make_keyboard_selectable
from scpolya_ui.components.timefmt import local_timestamp
from scpolya_ui.run_records import resolve_execution_config
from scpolya_ui.services.run_service import get_run_record, load_run_summary, logical_run_summaries
from scpolya_ui.services.site_score_service import (
    HISTOGRAM_BINS,
    discover_deepcps_results,
    empty_state,
    read_site_scores,
    scored_config,
)
from scpolya_ui.services.workspace_service import default_workspace, get_workspace
from scpolya_ui.state.app_state import ProductAppState
from scpolya_ui.state.persistence import load_product_registries

PAGE_KEY = "site_scores"
WORKSPACE_SOURCE = "__workspace__"
PAGE_SIZE = 50


def render_site_scores_page(state: ProductAppState) -> None:
    from nicegui import ui

    registries = state.registries
    if registries is None:
        registries = load_product_registries()
        state.registries = registries
    first_workspace = default_workspace(registries.workspaces)
    page_state: dict[str, Any] = {
        "registries": registries,
        "workspace_id": state.selected_workspace_id or (first_workspace.id if first_workspace else None),
        "source_id": state.payload.get("selected_run_record_id") or None,
        "result_index": 0,
        "query": "",
        "scored_only": False,
        "offset": 0,
    }

    def current_workspace():
        return get_workspace(page_state["registries"].workspaces, page_state["workspace_id"])

    def sources() -> list[dict[str, Any]]:
        """Workspace state plus each logical run; runs sharing outputs collapse to one entry."""
        workspace = current_workspace()
        if workspace is None:
            return []
        items = []
        summary = load_run_summary(workspace.db_path)
        if summary.config:
            items.append(
                {
                    "id": WORKSPACE_SOURCE,
                    "label": state.t("site_scores.source_workspace"),
                    "config": scored_config(summary.config, workspace.path_mappings),
                    "record_id": "",
                }
            )
        for logical_run in logical_run_summaries(
            page_state["registries"].run_records,
            workspace_id=workspace.id,
            limit=50,
        ):
            record = get_run_record(page_state["registries"].run_records, logical_run.latest_record_id)
            if record is None:
                continue
            resolution = resolve_execution_config(record)
            if not resolution.available:
                continue
            items.append(
                {
                    "id": record.id,
                    "label": logical_run.label,
                    "config": scored_config(resolution.config, workspace.path_mappings),
                    "record_id": record.id,
                }
            )
        return items

    def select_source(source_id: str | None) -> None:
        page_state.update(source_id=source_id, result_index=0, offset=0)
        content.refresh()

    def select_result(index: int | None) -> None:
        page_state.update(result_index=int(index or 0), offset=0)
        content.refresh()

    def set_query(value: str | None) -> None:
        page_state.update(query=str(value or ""), offset=0)
        table_rows.refresh()

    def set_scored_only(value: bool) -> None:
        page_state.update(scored_only=bool(value), offset=0)
        table_rows.refresh()

    def page_by(step: int) -> None:
        page_state["offset"] = max(0, page_state["offset"] + step * PAGE_SIZE)
        table_rows.refresh()

    def reason_text(reason: str) -> str:
        key = f"site_scores.reason.{reason}"
        label = state.t(key)
        return reason if label == f"[[{key}]]" else label

    def open_in_genome(gene: str) -> None:
        source = page_state.get("source")
        query = {"gene": gene, "igv_source": "single"}
        if source and source.get("record_id"):
            query["run_record"] = source["record_id"]
        ui.navigate.to(state.page_href("igv_compare", extra_query=query))

    @ui.refreshable
    def content() -> None:
        with ui.element("div").classes("env-page sc-page w-full"):
            with ui.row().classes("ov-header w-full"):
                with ui.column().classes("ov-heading gap-0"):
                    with ui.row().classes("sc-title-row"):
                        ui.label(state.t("nav.site_scores")).classes("ov-title")
                        ui.label(state.t("common.reference_only")).classes("sp-ref").tooltip(
                            state.t("site_scores.reference_note")
                        )
                    ui.label(state.t("page.subtitle.site_scores")).classes("env-subtitle")

            workspace = current_workspace()
            if workspace is None:
                with ui.column().classes("env-empty gap-1"):
                    ui.label(state.t("workspace.empty")).classes("runs-empty-title")
                    ui.label(state.t("workspace.empty_detail")).classes("ov-muted-small")
                return

            items = sources()
            if not items:
                _empty(state.t("site_scores.no_sources"), state.t("site_scores.no_sources_detail"))
                return
            discovered = [(item, discover_deepcps_results(item["config"])) for item in items]
            source_ids = {item["id"] for item in items}
            if page_state["source_id"] not in source_ids:
                with_results = next((item for item, results in discovered if results), None)
                page_state["source_id"] = (with_results or items[0])["id"]
            source, results = next(
                (item, found) for item, found in discovered if item["id"] == page_state["source_id"]
            )
            page_state["source"] = source

            with ui.row().classes("sc-toolbar w-full"):
                ui.label(state.t("site_scores.source")).classes("sc-toolbar-label")
                ui.select(
                    options={
                        item["id"]: (
                            f"{item['label']} · {state.t('site_scores.result_count', count=len(found))}"
                            if found
                            else item["label"]
                        )
                        for item, found in discovered
                    },
                    value=page_state["source_id"],
                    on_change=lambda e: select_source(e.value),
                ).classes("sc-source-select").props("dense outlined options-dense hide-bottom-space")
                ui.label(state.t("site_scores.scorer")).classes("sc-toolbar-label sc-toolbar-gap")
                ui.label("DeepCPS").classes("sc-scorer-chip")
                if len(results) > 1:
                    ui.label(state.t("site_scores.result")).classes("sc-toolbar-label sc-toolbar-gap")
                    ui.select(
                        options={
                            index: f"{item.fingerprint[:12]} · {local_timestamp(_iso(item.modified_at))}"
                            for index, item in enumerate(results)
                        },
                        value=min(page_state["result_index"], len(results) - 1),
                        on_change=lambda e: select_result(e.value),
                    ).classes("sc-result-select").props("dense outlined options-dense hide-bottom-space")

            if not results:
                _render_no_results(source)
                return
            result = results[min(page_state["result_index"], len(results) - 1)]
            page_state["result"] = result
            _render_result(result)

    def _empty(title: str, detail: str) -> None:
        with ui.column().classes("sc-empty gap-1"):
            ui.icon("o_bar_chart").classes("sc-empty-icon")
            ui.label(title).classes("sc-empty-title")
            ui.label(detail).classes("sc-empty-detail")

    def _render_no_results(source) -> None:
        kind = empty_state(source["config"])
        if kind == "unsupported":
            _empty(state.t("site_scores.unsupported_title"), state.t("site_scores.unsupported_detail"))
            return
        if kind == "missing":
            _empty(state.t("site_scores.missing_title"), state.t("site_scores.missing_detail"))
            return
        with ui.column().classes("sc-empty gap-1"):
            ui.icon("o_bar_chart").classes("sc-empty-icon")
            ui.label(state.t("site_scores.off_title")).classes("sc-empty-title")
            ui.label(state.t("site_scores.off_detail")).classes("sc-empty-detail")
            extra = {"base_run": source["record_id"], "launch_mode": "rerun"} if source["record_id"] else {}
            ui.button(
                state.t("site_scores.enable_action"),
                icon="o_tune",
                on_click=lambda: ui.navigate.to(state.page_href("run_setup", extra_query=extra)),
            ).props("unelevated no-caps color=primary").classes("sc-empty-action")

    def _render_result(result) -> None:
        if not result.readable:
            with ui.row().classes("ov-alert ov-alert-crit sc-alert w-full"):
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label(state.t("site_scores.integrity_failed")).classes("ov-alert-title")
                    ui.label(result.integrity_detail).classes("ov-alert-detail")
            return
        page = read_site_scores(result, limit=1)
        with ui.row().classes("ov-chips w-full"):
            ui.label(state.t("site_scores.chip.sites", count=result.n_sites or 0)).classes("ov-chip")
            ui.label(
                state.t("site_scores.chip.scored", scored=result.n_scored or 0, total=result.n_sites or 0)
            ).classes("ov-chip" + ("" if result.n_scored == result.n_sites else " ov-chip-warn"))
            ui.label(state.t("site_scores.chip.verified")).classes("ov-chip").tooltip(
                state.t("site_scores.verified_detail")
            )
            if result.bed_matches is False:
                ui.label(state.t("site_scores.chip.bed_changed")).classes("ov-chip ov-chip-warn").tooltip(
                    state.t("site_scores.bed_changed_detail")
                )
        with ui.element("div").classes("sc-grid"):
            with ui.element("section").classes("ov-card w-full"):
                with ui.row().classes("ov-card-head w-full"):
                    ui.label(state.t("site_scores.distribution")).classes("ov-card-title")
                    ui.label(state.t("site_scores.distribution_aside")).classes("ov-card-count")
                with ui.column().classes("ov-card-body gap-1 w-full"):
                    _histogram(page.histogram)
                    if page.unscored:
                        ui.label(state.t("site_scores.unscored_note", count=page.unscored)).classes("env-note")
            with ui.element("section").classes("ov-card w-full"):
                with ui.row().classes("ov-card-head w-full"):
                    ui.label(state.t("site_scores.about")).classes("ov-card-title")
                with ui.column().classes("ov-card-body gap-0 w-full"):
                    # display the scorer name; the full model identifier stays available as a tooltip
                    _kv(state.t("site_scores.model"), "DeepCPS" if result.model_id else "-", tooltip=result.model_id or None)
                    _kv(state.t("site_scores.model_status"), _model_status(result.model_status))
                    _kv(state.t("site_scores.reasons"), ", ".join(
                        f"{reason_text(key)} {value}" for key, value in sorted(result.reasons.items())
                    ) or "-")
                    _kv(state.t("site_scores.settings"), state.t(
                        "site_scores.settings_value",
                        threads=result.threads if result.threads is not None else "-",
                        batch=result.batch_size if result.batch_size is not None else "-",
                    ))
                    _kv(state.t("site_scores.result_dir"), result.directory, mono=True)
                    ui.label(state.t("site_scores.reference_note")).classes("env-note")
        table_content()

    def _model_status(status: str) -> str:
        key = f"site_scores.model_status.{status}"
        label = state.t(key)
        return status or "-" if label == f"[[{key}]]" else label

    def _histogram(counts) -> None:
        peak = max(counts) if counts else 0
        peak_label = f"{peak:,}"
        # widen the left margin so a large peak count is not clipped
        width, height, pad_left, pad_bottom = 560, 132, max(30, 10 + 7 * len(peak_label)), 22
        plot_w, plot_h = width - pad_left - 6, height - pad_bottom - 8
        bar_w = plot_w / HISTOGRAM_BINS
        bars = []
        for index, count in enumerate(counts):
            bar_h = 0 if not peak else max(2.0, plot_h * count / peak) if count else 0
            x = pad_left + index * bar_w + 1
            y = 8 + plot_h - bar_h
            low, high = index / HISTOGRAM_BINS, (index + 1) / HISTOGRAM_BINS
            tip = state.t("site_scores.bin_tip", low=f"{low:.2f}", high=f"{high:.2f}", count=count)
            bars.append(
                f'<g class="sc-bin"><rect x="{pad_left + index * bar_w:.1f}" y="8" width="{bar_w:.1f}" '
                f'height="{plot_h}" fill="transparent"><title>{tip}</title></rect>'
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(bar_w - 2, 1):.1f}" height="{bar_h:.1f}" '
                f'rx="2" class="sc-bar"><title>{tip}</title></rect></g>'
            )
        ticks = "".join(
            f'<text x="{pad_left + plot_w * t:.1f}" y="{height - 6}" text-anchor="middle">{t:g}</text>'
            for t in (0, 0.25, 0.5, 0.75, 1)
        )
        svg = (
            f'<svg viewBox="0 0 {width} {height}" class="sc-hist" role="img" '
            f'aria-label="{state.t("site_scores.distribution")}">'
            f'<line x1="{pad_left}" y1="{8 + plot_h}" x2="{pad_left + plot_w}" y2="{8 + plot_h}" class="sc-axis"/>'
            f'<text x="{pad_left - 6}" y="14" text-anchor="end">{peak_label}</text>'
            f'<text x="{pad_left - 6}" y="{8 + plot_h}" text-anchor="end">0</text>'
            f'{"".join(bars)}{ticks}</svg>'
        )
        ui.html(svg).classes("sc-hist-wrap w-full")

    def _kv(label: str, value: str, *, mono: bool = False, tooltip: str | None = None) -> None:
        with ui.row().classes("ov-detail w-full"):
            ui.label(label).classes("ov-detail-key")
            text = ui.label(value).classes("ov-detail-value" + (" ov-mono" if mono else ""))
            text.tooltip(tooltip or value)

    def table_content() -> None:
        # The search box sits outside the refreshable rows so typing keeps focus.
        result = page_state.get("result")
        if result is None or not result.readable:
            return
        with ui.element("section").classes("ov-card sc-table-card w-full"):
            with ui.row().classes("ov-card-head w-full"):
                ui.label(state.t("site_scores.sites")).classes("ov-card-title")
                ui.label(state.t("site_scores.sorted_note")).classes("ov-card-count")
                ui.element("div").classes("ov-spacer")
                ui.input(
                    value=page_state["query"],
                    placeholder=state.t("site_scores.search"),
                    on_change=lambda e: set_query(e.value),
                ).props("dense outlined hide-bottom-space debounce=250 clearable").classes("sc-search")
                ui.checkbox(
                    state.t("site_scores.scored_only"),
                    value=page_state["scored_only"],
                    on_change=lambda e: set_scored_only(e.value),
                ).props("dense").classes("sc-check")
            table_rows()

    @ui.refreshable
    def table_rows() -> None:
        result = page_state.get("result")
        if result is None or not result.readable:
            return
        page = read_site_scores(
            result,
            offset=page_state["offset"],
            limit=PAGE_SIZE,
            query=page_state["query"],
            scored_only=page_state["scored_only"],
        )
        with ui.element("div").classes("ov-table sc-table w-full"):
            with ui.element("div").classes("ov-tr ov-th"):
                for key in ("site", "position", "score", "seeds", "locate", "reason"):
                    ui.label(state.t(f"site_scores.col.{key}")).classes(f"ov-td sc-td-{key}")
            if not page.rows:
                ui.label(state.t("site_scores.no_matches")).classes("sc-no-rows")
            for row in page.rows:
                tr = ui.element("div").classes("ov-tr" + (" ov-tr-link" if row.gene else ""))
                if row.gene:
                    make_keyboard_selectable(
                        tr,
                        lambda _, gene=row.gene: open_in_genome(gene),
                        label=state.t("site_scores.open_in_genome", gene=row.gene),
                    )
                    tr.tooltip(state.t("site_scores.open_in_genome", gene=row.gene))
                with tr:
                    with ui.column().classes("ov-td sc-td-site gap-0"):
                        ui.label(row.gene or "-").classes("ov-run-name")
                        ui.label(row.name).classes("ov-mono sc-sub")
                    with ui.column().classes("ov-td sc-td-position gap-0"):
                        ui.label(f"{row.chrom}:{row.site if row.site is not None else '-'}").classes("ov-mono")
                        ui.label(f"{row.strand} · {row.feature or '-'}").classes("sc-sub")
                    with ui.row().classes("ov-td sc-td-score"):
                        if row.calibrated is None:
                            ui.label("NA").classes("ov-mono sc-na")
                        else:
                            with ui.column().classes("sc-score gap-0"):
                                ui.label(f"{row.calibrated:.3f}").classes("ov-mono")
                                ui.label(
                                    state.t("site_scores.raw_value", value=f"{row.raw:.4f}")
                                    if row.raw is not None
                                    else ""
                                ).classes("sc-sub")
                            with ui.element("div").classes("sc-meter"):
                                ui.element("div").classes("sc-meter-fill").style(
                                    f"width: {row.calibrated * 100:.1f}%"
                                )
                    ui.label(
                        " / ".join("NA" if seed is None else f"{seed:.3f}" for seed in row.seeds)
                    ).classes("ov-td ov-mono sc-td-seeds")
                    ui.label(
                        "NA"
                        if row.locate_offset is None
                        else f"{row.locate_offset:+d} nt · {row.locate_confidence or 0:.2f}"
                    ).classes("ov-td ov-mono sc-td-locate")
                    with ui.element("div").classes("ov-td sc-td-reason"):
                        tone = "good" if row.score_reason == "scored" else "idle"
                        with ui.element("span").classes(f"sp-status sp-status-{tone} sc-reason"):
                            ui.element("i")
                            ui.label(reason_text(row.score_reason))
        with ui.row().classes("sc-pager w-full"):
            last = min(page.offset + PAGE_SIZE, page.total)
            ui.label(
                state.t("site_scores.range", start=page.offset + 1 if page.total else 0, end=last, total=page.total)
            ).classes("ov-muted-small")
            ui.element("div").classes("ov-spacer")
            ui.button(icon="chevron_left", on_click=lambda: page_by(-1)).props(
                "flat dense" + (" disable" if page.offset == 0 else "")
            )
            ui.button(icon="chevron_right", on_click=lambda: page_by(1)).props(
                "flat dense" + (" disable" if last >= page.total else "")
            )

    content()


def _iso(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
