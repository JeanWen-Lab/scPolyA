from __future__ import annotations

import argparse
from pathlib import Path

from .components.config_panel import CONFIG_PANEL_GROUPS
from .i18n import normalize_locale, validate_catalog_parity
from .pages.differential_apa import PAGE_KEY as DIFFERENTIAL_APA_PAGE_KEY
from .pages.differential_apa import render_differential_apa_page
from .pages.igv_compare import PAGE_KEY as IGV_COMPARE_PAGE_KEY
from .pages.igv_compare import render_igv_compare_page
from .pages.parameter_studies import PAGE_KEY as PARAMETER_STUDIES_PAGE_KEY
from .pages.parameter_studies import render_parameter_studies_page
from .pages.run_setup import PAGE_KEY as RUN_SETUP_PAGE_KEY
from .pages.run_setup import render_run_setup_page
from .pages.analyses import PAGE_KEY as ANALYSES_PAGE_KEY, render_analyses_page
from .pages.run_evidence import PAGE_KEY as RUN_EVIDENCE_PAGE_KEY
from .pages.run_evidence import render_run_evidence_page
from .pages.runs import PAGE_KEY as RUNS_PAGE_KEY
from .pages.runs import render_runs_page
from .pages.settings import PAGE_KEY as SETTINGS_PAGE_KEY
from .pages.site_scores import PAGE_KEY as SITE_SCORES_PAGE_KEY
from .pages.site_scores import render_site_scores_page
from .pages.settings import render_settings_page
from .pages.workspaces import PAGE_KEY as WORKSPACES_PAGE_KEY
from .pages.workspaces import render_workspaces_page
from .shell.layout import render_pending_page, render_product_shell
from .shell.navigation import product_navigation
from .services.workspace_service import default_workspace, get_workspace
from .state.app_state import ProductAppState
from .state.persistence import ProductRegistries, load_product_registries

ARTIFACTS_PAGE_KEY = "artifacts"


APP_TITLE = "scpolyA Control"
DEFAULT_PORT = 24120
CONFIG_FORM_GROUPS = CONFIG_PANEL_GROUPS
PACKAGE_ROOT = Path(__file__).resolve().parent
_PRODUCT_ROUTES_REGISTERED = False


def validate_product_bootstrap() -> None:
    missing = validate_catalog_parity(("en", "zh-CN"))
    if missing:
        details = ", ".join(f"{locale}: {len(keys)} missing" for locale, keys in missing.items())
        raise RuntimeError(f"Locale catalog parity failed: {details}")


def create_product_ui() -> None:
    """Create the product UI."""
    validate_product_bootstrap()
    register_product_static_files()
    register_product_routes()


def create_ui() -> None:
    create_product_ui()


def register_product_routes() -> None:
    global _PRODUCT_ROUTES_REGISTERED
    if _PRODUCT_ROUTES_REGISTERED:
        return

    from nicegui import ui

    @ui.page("/")
    def product_root(locale: str = "en", workspace: str | None = None) -> None:
        render_product_page(WORKSPACES_PAGE_KEY, locale=locale, workspace_id=workspace)

    @ui.page("/product")
    def product_index(locale: str = "en", workspace: str | None = None) -> None:
        render_product_page(WORKSPACES_PAGE_KEY, locale=locale, workspace_id=workspace)

    @ui.page("/product/{page}")
    def product_page(
        page: str,
        locale: str = "en",
        workspace: str | None = None,
        study: str | None = None,
        run_record: str | None = None,
        base_run: str | None = None,
        launch_mode: str | None = None,
        igv_source: str | None = None,
        gene: str | None = None,
        analysis: str | None = None,
        tab: str | None = None,
        evidence_table: str | None = None,
    ) -> None:
        render_product_page(
            page,
            locale=locale,
            workspace_id=workspace,
            study_id=study,
            run_record_id=run_record,
            base_run_record_id=base_run,
            launch_mode=launch_mode,
            igv_source=igv_source,
            gene=gene,
            analysis=analysis,
            tab=tab,
            evidence_table=evidence_table,
        )

    _PRODUCT_ROUTES_REGISTERED = True


def register_product_static_files() -> None:
    from nicegui import app

    static_dir = PACKAGE_ROOT / "static"
    vendor_dir = PACKAGE_ROOT / "vendor"
    if static_dir.exists():
        app.add_static_file(
            local_file=str(static_dir / "genomes.json"),
            url_path="/_scpolya_genomes.json",
        )
        app.add_static_file(
            local_file=str(static_dir / "empty.fai"),
            url_path="/_scpolya_empty.fai",
        )
    if vendor_dir.exists():
        app.add_static_files("/_scpolya_vendor", str(vendor_dir))


RUN_DETAIL_TABS = frozenset({"overview", "artifacts", "logs", "configuration", "attempts"})


def render_product_page(
    page: str,
    *,
    locale: str = "en",
    workspace_id: str | None = None,
    study_id: str | None = None,
    run_record_id: str | None = None,
    base_run_record_id: str | None = None,
    launch_mode: str | None = None,
    igv_source: str | None = None,
    gene: str | None = None,
    analysis: str | None = None,
    tab: str | None = None,
    evidence_table: str | None = None,
) -> None:
    page = normalize_product_page(page)
    registries = load_product_registries()
    payload = {}
    if page == ARTIFACTS_PAGE_KEY:
        # Artifacts and logs now live on the run detail; keep old links working.
        page = RUNS_PAGE_KEY
        payload["runs_detail_tab"] = "artifacts"
    if study_id:
        payload["selected_study_id"] = study_id
    if run_record_id:
        payload["selected_run_record_id"] = run_record_id
    if base_run_record_id:
        payload["base_run_record_id"] = base_run_record_id
    if launch_mode in {"rerun", "continue"}:
        payload["launch_mode"] = launch_mode
    if igv_source:
        payload["igv_source"] = igv_source
    if gene:
        payload["igv_gene"] = gene
    if analysis:
        payload["differential_analysis"] = analysis
    if evidence_table:
        payload["evidence_table"] = evidence_table
    if tab in RUN_DETAIL_TABS and page == RUNS_PAGE_KEY:
        payload["runs_detail_tab"] = tab
    state = ProductAppState(
        locale=normalize_locale(locale),
        active_page=page,
        registries=registries,
        selected_workspace_id=normalize_workspace_id(registries, workspace_id),
        payload=payload,
    )
    renderer = {
        WORKSPACES_PAGE_KEY: render_workspaces_page,
        RUNS_PAGE_KEY: render_runs_page,
        RUN_SETUP_PAGE_KEY: render_run_setup_page,
        PARAMETER_STUDIES_PAGE_KEY: render_parameter_studies_page,
        IGV_COMPARE_PAGE_KEY: render_igv_compare_page,
        SETTINGS_PAGE_KEY: render_settings_page,
        SITE_SCORES_PAGE_KEY: render_site_scores_page,
        RUN_EVIDENCE_PAGE_KEY: render_run_evidence_page,
        ANALYSES_PAGE_KEY: render_analyses_page,
        DIFFERENTIAL_APA_PAGE_KEY: render_differential_apa_page,
    }.get(page, render_pending_page)
    render_product_shell(state, renderer)


def normalize_product_page(page: str | None) -> str:
    valid_pages = {item.page for item in product_navigation()}
    if page in valid_pages:
        return str(page)
    return WORKSPACES_PAGE_KEY


def normalize_workspace_id(registries: ProductRegistries, workspace_id: str | None) -> str | None:
    if workspace_id and get_workspace(registries.workspaces, workspace_id) is not None:
        return workspace_id
    fallback = default_workspace(registries.workspaces)
    return fallback.id if fallback is not None else None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Start the local scpolyA web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    from nicegui import ui

    create_product_ui()
    ui.run(
        host=args.host,
        port=args.port,
        reload=args.reload,
        show=not args.no_browser,
        title=APP_TITLE,
    )
