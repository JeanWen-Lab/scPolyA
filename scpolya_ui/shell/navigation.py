from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavigationItem:
    key: str
    section_key: str
    label_key: str
    icon: str
    page: str
    in_sidebar: bool = True


def product_navigation() -> tuple[NavigationItem, ...]:
    """Every routable product page, in sidebar order.

    Pages with ``in_sidebar=False`` stay routable (deep links, run detail
    shortcuts) but are reached from other pages instead of the sidebar.
    """
    return (
        NavigationItem("workspaces", "nav.section.workspaces", "nav.workspaces", "o_home", "workspaces"),
        NavigationItem("runs", "nav.section.pipeline", "nav.runs", "o_format_list_bulleted", "runs"),
        NavigationItem("run_setup", "nav.section.pipeline", "nav.run_setup", "o_tune", "run_setup"),
        NavigationItem(
            "parameter_studies",
            "nav.section.pipeline",
            "nav.parameter_studies",
            "o_grid_view",
            "parameter_studies",
        ),
        NavigationItem(
            "igv_compare",
            "nav.section.analysis",
            "nav.igv_compare",
            "o_ssid_chart",
            "igv_compare",
        ),
        NavigationItem("analyses", "nav.section.extensions", "nav.analyses", "o_science", "analyses"),
        NavigationItem("run_evidence", "nav.section.extensions", "nav.run_evidence", "o_manage_search", "run_evidence"),
        NavigationItem("site_scores", "nav.section.extensions", "nav.site_scores", "o_bar_chart", "site_scores"),
        NavigationItem(
            "differential_apa",
            "nav.section.extensions",
            "nav.differential_apa",
            "o_compare_arrows",
            "differential_apa",
        ),
        NavigationItem(
            "reference_settings",
            "nav.section.operations",
            "nav.reference_settings",
            "o_fact_check",
            "settings",
        ),
        NavigationItem(
            "artifacts",
            "nav.section.operations",
            "nav.artifacts",
            "o_folder_copy",
            "artifacts",
            in_sidebar=False,
        ),
    )


def sidebar_navigation() -> tuple[NavigationItem, ...]:
    return tuple(item for item in product_navigation() if item.in_sidebar)
