from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict
from typing import Any
from typing import Literal

from task_manage.runtime_controls import (
    RUNTIME_CONTROL_GROUPS,
    RUNTIME_CONTROL_SPECS,
    runtime_control_specs_by_group,
)

from scpolya_ui.components.interaction import prop_text
from scpolya_ui.config_schema import (
    FIELD_BY_KEY,
    PEAK_CALLING_ALWAYS_ACTIVE_KEYS,
    PEAK_CALLING_FIELD_KEYS,
    ConfigField,
    active_peak_calling_keys,
    field_value,
    required_missing,
    unmapped_keys,
)
from scpolya_ui.view_models.config_vm import ConfigRow, config_diff_rows, config_rows

ConfigPanelMode = Literal["edit", "read", "diff"]


_RUNTIME_CONTROL_BY_KEY = {spec.key: spec for spec in RUNTIME_CONTROL_SPECS}
_RUNTIME_GROUP_BY_KEY = {group.key: group for group in RUNTIME_CONTROL_GROUPS}
_VISIBLE_RUNTIME_CONTROL_KEYS = tuple(
    spec.key for spec in RUNTIME_CONTROL_SPECS if spec.visible_by_default
)
_LEGACY_RUNTIME_CONTROL_KEYS = tuple(
    spec.key for spec in RUNTIME_CONTROL_SPECS if not spec.visible_by_default
)
_RESOURCE_DIAGNOSTIC_KEYS = ("tools_seed", "debug", "verbose")


def _resource_workbench_keys() -> tuple[str, ...]:
    keys: list[str] = []
    for group, specs in runtime_control_specs_by_group():
        keys.extend(spec.key for spec in specs)
        if group.key == "cohort_merge":
            keys.append("merge_temp_dir")
    keys.extend(_RESOURCE_DIAGNOSTIC_KEYS)
    return tuple(keys)


RESOURCE_WORKBENCH_KEYS = _resource_workbench_keys()
RESOURCE_CONFIG_KEYS = RESOURCE_WORKBENCH_KEYS + _LEGACY_RUNTIME_CONTROL_KEYS

PEAK_CALLING_CONTROL_GROUPS: tuple[
    tuple[str, str, str, tuple[str, ...]], ...
] = (
    (
        "shared",
        "Shared peak rules",
        "Settings shared by expression and PolyA peak calling.",
        ("coverage_scaling",),
    ),
    (
        "expression",
        "Expression signal",
        "Expression-signal merge and threshold controls.",
        (
            "exp_peak_threshold_mode",
            "merge_distance",
            "unscaled_depth_threshold",
            "scaled_depth_threshold",
            "exp_dynamic_threshold_stat",
            "exp_dynamic_threshold_alpha",
            "exp_dynamic_threshold_floor",
            "exp_dynamic_threshold_normalized",
        ),
    ),
    (
        "polya",
        "PolyA signal",
        "PolyA-signal merge and threshold controls.",
        (
            "polyA_peak_threshold_mode",
            "merge_distance_polyA",
            "unscaled_depth_threshold_polyA",
            "scaled_depth_threshold_polyA",
            "polyA_dynamic_threshold_stat",
            "polyA_dynamic_threshold_alpha",
            "polyA_dynamic_threshold_floor",
            "polyA_dynamic_threshold_normalized",
        ),
    ),
)

WORKFLOW_CONTROL_GROUPS: tuple[
    tuple[str, str, str, tuple[str, ...]], ...
] = (
    (
        "analysis_outputs",
        "run_setup.workflow_group.analysis_outputs",
        "run_setup.workflow_group.analysis_outputs_help",
        (
            "annotate_bam",
            "count_matrix",
            "rna_gene_matrix",
            "summarize_matrix",
            "psi_min_gene_umis",
            "annotate_peak_source",
        ),
    ),
    (
        "retained_compatibility",
        "run_setup.workflow_group.retained_compatibility",
        "run_setup.workflow_group.retained_compatibility_help",
        (
            "materialize_strand_bams",
            "keep_annotate_bam_temp",
            "keep_merge_temp",
        ),
    ),
)


CONFIG_PANEL_GROUPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("Run Identity", "form-section-full", "form-grid-main", ("run_id", "species")),
    (
        "Paths",
        "form-section-full",
        "form-grid-paths",
        ("output_dir", "inputs", "gene_gtf", "chrom_sizes", "bulk_window_gtf", "bulk_gene_gtf"),
    ),
    (
        "Tags And Dedup",
        "form-section-tags",
        "form-grid-tags",
        ("cb_tag", "umi_tag", "dedup_method", "force_split_bams", "force_dedup_bams", "dedup_stats"),
    ),
    ("Filtering", "form-section-filtering", "form-grid-filtering", ("minpolyA", "nonA_allowed", "minMAPQ", "remove_mt")),
    (
        "Peak Rules",
        "form-section-half",
        "form-grid-three",
        (
            "coverage_scaling",
            "exp_peak_threshold_mode",
            "polyA_peak_threshold_mode",
            "merge_distance",
            "merge_distance_polyA",
            "unscaled_depth_threshold",
            "scaled_depth_threshold",
            "unscaled_depth_threshold_polyA",
            "scaled_depth_threshold_polyA",
        ),
    ),
    (
        "Dynamic Thresholds",
        "form-section-half",
        "form-grid-three",
        (
            "exp_dynamic_threshold_stat",
            "polyA_dynamic_threshold_stat",
            "exp_dynamic_threshold_alpha",
            "polyA_dynamic_threshold_alpha",
            "exp_dynamic_threshold_floor",
            "polyA_dynamic_threshold_floor",
            "exp_dynamic_threshold_normalized",
            "polyA_dynamic_threshold_normalized",
        ),
    ),
    (
        "Mispriming",
        "form-section-full",
        "form-grid-three",
        ("misprime_A_count", "misprime_in", "misprimed_ratio_threshold"),
    ),
    (
        "Quantification",
        "form-section-full",
        "form-grid-three",
        ("quantification_mode", "intronic_misprimed_ratio_threshold"),
    ),
    (
        "Resources",
        "form-section-wide",
        "form-grid-compact",
        RESOURCE_CONFIG_KEYS,
    ),
    (
        "Workflow",
        "form-section-narrow",
        "form-grid-compact",
        (
            "annotate_bam",
            "count_matrix",
            "rna_gene_matrix",
            "summarize_matrix",
            "materialize_strand_bams",
            "keep_annotate_bam_temp",
            "keep_merge_temp",
            "psi_min_gene_umis",
            "annotate_peak_source",
        ),
    ),
    (
        "Repair",
        "form-section-narrow",
        "form-grid-compact",
        ("repair_moved_run", "repair_moved_run_dry_run"),
    ),
)

DEEPCPS_FIELD_KEYS: tuple[str, ...] = (
    "deepcps",
    "reference_fasta",
    "deepcps_model_dir",
    "deepcps_model_manifest",
    "deepcps_reference_sha256",
    "deepcps_threads",
    "deepcps_batch_size",
)

CONFIG_WORKBENCH_SECTIONS: tuple[
    tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "identity",
        "Identity",
        "person_outline",
        ("run_id", "species"),
        ("run_id", "species"),
    ),
    (
        "inputs",
        "Inputs & references",
        "description",
        ("output_dir", "inputs", "gene_gtf", "chrom_sizes", "bulk_window_gtf", "bulk_gene_gtf"),
        ("output_dir", "inputs", "gene_gtf", "chrom_sizes", "bulk_window_gtf", "bulk_gene_gtf"),
    ),
    (
        "read_processing",
        "Read processing",
        "content_cut",
        (
            "cb_tag",
            "umi_tag",
            "dedup_method",
            "force_split_bams",
            "force_dedup_bams",
            "dedup_stats",
            "minpolyA",
            "nonA_allowed",
            "minMAPQ",
            "remove_mt",
        ),
        ("cb_tag", "umi_tag", "dedup_method", "minpolyA", "nonA_allowed", "minMAPQ", "remove_mt"),
    ),
    (
        "peak_calling",
        "Peak calling",
        "area_chart",
        PEAK_CALLING_FIELD_KEYS,
        PEAK_CALLING_ALWAYS_ACTIVE_KEYS,
    ),
    (
        "mispriming",
        "Mispriming",
        "my_location",
        ("misprime_A_count", "misprime_in", "misprimed_ratio_threshold"),
        ("misprime_A_count", "misprime_in", "misprimed_ratio_threshold"),
    ),
    (
        "quantification",
        "Quantification",
        "analytics",
        ("quantification_mode", "intronic_misprimed_ratio_threshold"),
        ("quantification_mode", "intronic_misprimed_ratio_threshold"),
    ),
    (
        "resources",
        "Resources",
        "dns",
        RESOURCE_WORKBENCH_KEYS,
        RESOURCE_WORKBENCH_KEYS,
    ),
    (
        "site_scoring",
        "Site scoring",
        "bar_chart",
        DEEPCPS_FIELD_KEYS,
        ("deepcps", "reference_fasta", "deepcps_model_dir"),
    ),
    (
        "workflow",
        "Workflow",
        "account_tree",
        (
            "annotate_bam",
            "count_matrix",
            "rna_gene_matrix",
            "summarize_matrix",
            "materialize_strand_bams",
            "keep_annotate_bam_temp",
            "keep_merge_temp",
            "psi_min_gene_umis",
            "annotate_peak_source",
        ),
        (
            "annotate_bam",
            "count_matrix",
            "rna_gene_matrix",
            "summarize_matrix",
            "materialize_strand_bams",
            "psi_min_gene_umis",
            "annotate_peak_source",
        ),
    ),
    (
        "repair",
        "Repair",
        "build_circle",
        ("repair_moved_run", "repair_moved_run_dry_run"),
        (),
    ),
)

CONFIG_INSPECTOR_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Run Identity",
        ("run_id", "species", "dedup_method", "cb_tag", "umi_tag"),
    ),
    (
        "Inputs / Reference",
        ("inputs", "gene_gtf", "chrom_sizes", "bulk_window_gtf", "bulk_gene_gtf"),
    ),
    (
        "Peak Calling",
        (
            "coverage_scaling",
            "exp_peak_threshold_mode",
            "polyA_peak_threshold_mode",
            "merge_distance",
            "merge_distance_polyA",
            "unscaled_depth_threshold",
            "unscaled_depth_threshold_polyA",
        ),
    ),
    (
        "Dynamic Thresholds",
        (
            "exp_dynamic_threshold_stat",
            "polyA_dynamic_threshold_stat",
            "exp_dynamic_threshold_alpha",
            "polyA_dynamic_threshold_alpha",
            "exp_dynamic_threshold_floor",
            "polyA_dynamic_threshold_floor",
            "exp_dynamic_threshold_normalized",
            "polyA_dynamic_threshold_normalized",
        ),
    ),
    (
        "Filtering / Mispriming",
        (
            "minpolyA",
            "nonA_allowed",
            "minMAPQ",
            "remove_mt",
            "misprime_A_count",
            "misprime_in",
            "misprimed_ratio_threshold",
        ),
    ),
    (
        "Workflow / Resources",
        (
            "annotate_bam",
            "count_matrix",
            "rna_gene_matrix",
            "summarize_matrix",
            "materialize_strand_bams",
            "psi_min_gene_umis",
            "annotate_peak_source",
            "n_workers",
            "max_extract_workers",
            "n_threads_bam_annotation",
        ),
    ),
    (
        "Quantification",
        ("quantification_mode", "intronic_misprimed_ratio_threshold"),
    ),
)


def config_panel_mode_label(mode: ConfigPanelMode) -> str:
    return {
        "edit": "common.config",
        "read": "common.config",
        "diff": "common.config",
    }[mode]


def display_config_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _inspector_value_text(value: Any, translate: Callable[[str], str] | None) -> str:
    if value is None or value == "":
        return _translated_text(translate, "config.value.unset", "-")
    if isinstance(value, bool):
        name = "config.value.true" if value else "config.value.false"
        return _translated_text(translate, name, display_config_value(value))
    return display_config_value(value)


def config_table_rows(rows: Iterable[ConfigRow]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        payload = asdict(row)
        payload["value"] = display_config_value(row.value)
        payload["old_value"] = display_config_value(row.old_value)
        payload["new_value"] = display_config_value(row.new_value)
        out.append(payload)
    return out


def render_config_panel(
    config: dict[str, Any],
    *,
    mode: ConfigPanelMode = "read",
    proposed_config: dict[str, Any] | None = None,
    key_label: str = "Key",
    value_label: str = "Value",
    old_label: str = "Stored",
    new_label: str = "Proposed",
    changed_label: str = "Changed",
) -> None:
    from nicegui import ui

    if mode == "diff":
        render_config_diff_panel(
            config,
            proposed_config or {},
            key_label=key_label,
            old_label=old_label,
            new_label=new_label,
            changed_label=changed_label,
        )
        return

    rows = config_table_rows(config_rows(config))
    columns = [
        {"name": "key", "label": key_label, "field": "key", "sortable": True},
        {"name": "value", "label": value_label, "field": "value"},
    ]
    ui.table(columns=columns, rows=rows, row_key="key").classes("w-full").props(
        "dense flat bordered rows-per-page-options='[10, 25, 50]'"
    )


def render_config_diff_panel(
    base_config: dict[str, Any],
    proposed_config: dict[str, Any],
    *,
    key_label: str = "Key",
    old_label: str = "Stored",
    new_label: str = "Proposed",
    changed_label: str = "Changed",
    changed_only: bool = False,
    empty_label: str = "No config changes.",
) -> None:
    from nicegui import ui

    rows = config_table_rows(config_diff_rows(base_config, proposed_config))
    if changed_only:
        rows = [row for row in rows if row["changed"]]
    if not rows:
        ui.label(empty_label).classes("product-small-muted config-diff-empty")
        return
    columns = [
        {"name": "key", "label": key_label, "field": "key", "sortable": True},
        {"name": "old_value", "label": old_label, "field": "old_value"},
        {"name": "new_value", "label": new_label, "field": "new_value"},
        {"name": "changed", "label": changed_label, "field": "changed", "sortable": True},
    ]
    ui.table(columns=columns, rows=rows, row_key="key").classes("w-full").props(
        "dense flat bordered rows-per-page-options='[10, 25, 50]'"
    )


def render_structured_config_panel(
    config: dict[str, Any],
    *,
    mode: ConfigPanelMode = "read",
    missing_label: str = "Missing",
    advanced_label: str = "Raw-only keys",
    on_change: Callable[[], Any] | None = None,
    on_field_change: Callable[[str], Any] | None = None,
    section_id: str | None = None,
    include_all: bool = True,
    section_title: str | None = None,
    translate: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    from nicegui import ui

    controls: dict[str, Any] = {}
    readonly = mode != "edit"

    def render_fields(keys: Iterable[str]) -> None:
        for key in keys:
            field = FIELD_BY_KEY.get(key)
            if field is None:
                continue
            change_handler = None
            if not readonly and (on_change is not None or on_field_change is not None):
                def change_handler(_event: Any = None, *, field_key: str = field.key) -> None:
                    if on_change is not None:
                        on_change()
                    if on_field_change is not None:
                        on_field_change(field_key)

            control = render_config_control(
                field,
                config,
                readonly=readonly,
                on_change=change_handler,
                translate=translate,
            )
            if not readonly:
                controls[field.key] = control

    with ui.element("div").classes("structured-form"):
        groups = CONFIG_PANEL_GROUPS
        if section_id is not None:
            section = next(
                (item for item in CONFIG_WORKBENCH_SECTIONS if item[0] == section_id),
                CONFIG_WORKBENCH_SECTIONS[0],
            )
            _section_id, default_title, _icon, _all_keys, _primary_keys = section
            keys = config_workbench_section_keys(
                _section_id,
                config,
                include_all=include_all,
            )
            groups = (
                (
                    section_title or default_title,
                    "form-section-full form-section-workbench",
                    "form-grid-workbench",
                    keys,
                ),
            )
        for title, section_class, grid_class, keys in groups:
            with ui.element("section").classes(f"form-section {section_class}"):
                shown_title = title if section_id is not None else _group_text(translate, _inspector_group_key(title), title)
                ui.label(shown_title).classes("form-section-title")
                if section_id == "resources" or (section_id is None and title == "Resources"):
                    _render_runtime_resource_groups(
                        config,
                        readonly=readonly,
                        render_fields=render_fields,
                        translate=translate,
                    )
                elif section_id == "peak_calling":
                    _render_peak_calling_groups(
                        config,
                        include_all=include_all,
                        render_fields=render_fields,
                        translate=translate,
                    )
                elif section_id == "workflow":
                    _render_workflow_groups(
                        render_fields=render_fields,
                        translate=translate,
                    )
                else:
                    with ui.element("div").classes(grid_class):
                        render_fields(keys)

    missing = required_missing(config)
    advanced = unmapped_keys(config)
    if section_id is None and (missing or advanced):
        with ui.row().classes("setup-actions"):
            if missing:
                ui.label(_label_value(translate, missing_label, ", ".join(missing))).classes("product-small-muted product-status-missing")
            if advanced:
                ui.label(_label_value(translate, advanced_label, str(len(advanced)))).classes("product-small-muted")
    return controls


def config_workbench_sections() -> tuple[
    tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...
]:
    return CONFIG_WORKBENCH_SECTIONS


def config_workbench_section_keys(
    section_id: str,
    config: dict[str, Any],
    *,
    include_all: bool,
) -> tuple[str, ...]:
    section = next(
        (item for item in CONFIG_WORKBENCH_SECTIONS if item[0] == section_id),
        CONFIG_WORKBENCH_SECTIONS[0],
    )
    _section_id, _title, _icon, all_keys, default_keys = section
    if section_id == "peak_calling":
        primary_keys = active_peak_calling_keys(config)
    else:
        primary_keys = default_keys
    if not include_all:
        return primary_keys
    primary_set = set(primary_keys)
    return primary_keys + tuple(key for key in all_keys if key not in primary_set)


def peak_calling_groups(
    config: dict[str, Any],
    *,
    include_all: bool,
) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    active_keys = set(active_peak_calling_keys(config))
    groups: list[tuple[str, str, str, tuple[str, ...]]] = []
    for group_key, label, description, keys in PEAK_CALLING_CONTROL_GROUPS:
        active_group_keys = tuple(key for key in keys if key in active_keys)
        if include_all:
            active_group_set = set(active_group_keys)
            visible_keys = active_group_keys + tuple(
                key for key in keys if key not in active_group_set
            )
        else:
            visible_keys = active_group_keys
        groups.append((group_key, label, description, visible_keys))
    return tuple(groups)


def _render_peak_calling_groups(
    config: dict[str, Any],
    *,
    include_all: bool,
    render_fields: Callable[[Iterable[str]], None],
    translate: Callable[[str], str] | None = None,
) -> None:
    from nicegui import ui

    with ui.element("div").classes("peak-calling-control-groups"):
        for group_key, label, description, keys in peak_calling_groups(
            config,
            include_all=include_all,
        ):
            with ui.element("section").classes(
                f"peak-calling-control-group peak-calling-control-group-{group_key}"
            ):
                group_label = ui.label(_group_text(translate, group_key, label)).classes(
                    "peak-calling-control-group-title"
                )
                group_label.tooltip(_group_text(translate, f"{group_key}_help", description))
                with ui.element("div").classes("peak-calling-control-grid"):
                    render_fields(keys)


def _render_workflow_groups(
    *,
    render_fields: Callable[[Iterable[str]], None],
    translate: Callable[[str], str] | None,
) -> None:
    from nicegui import ui

    with ui.element("div").classes("runtime-control-groups workflow-control-groups"):
        for group_key, label_key, help_key, keys in WORKFLOW_CONTROL_GROUPS:
            label = _translated_text(translate, label_key, label_key)
            description = _translated_text(translate, help_key, help_key)
            with ui.element("section").classes(
                f"runtime-control-group workflow-control-group workflow-control-group-{group_key}"
            ):
                group_label = ui.label(label).classes("runtime-control-group-title")
                group_label.tooltip(description)
                with ui.element("div").classes("runtime-control-grid workflow-control-grid"):
                    render_fields(keys)


def runtime_resource_groups(
    config: dict[str, Any],
    *,
    readonly: bool,
) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    groups: list[tuple[str, str, str, tuple[str, ...]]] = []
    for group, specs in runtime_control_specs_by_group():
        keys = tuple(spec.key for spec in specs)
        if group.key == "cohort_merge":
            keys += ("merge_temp_dir",)
        if keys:
            groups.append((group.key, group.label, group.description, keys))
    groups.append(
        (
            "advanced_diagnostics",
            "Advanced diagnostics",
            "Reproducibility seed and diagnostic output controls.",
            _RESOURCE_DIAGNOSTIC_KEYS,
        )
    )
    if readonly:
        legacy_specs = next(
            specs
            for group, specs in runtime_control_specs_by_group(include_legacy=True)
            if group.key == "legacy"
        )
        legacy_keys = tuple(
            spec.key for spec in legacy_specs if spec.key in config
        )
        if legacy_keys:
            legacy_group = _RUNTIME_GROUP_BY_KEY["legacy"]
            groups.append(
                (
                    legacy_group.key,
                    legacy_group.label,
                    legacy_group.description,
                    legacy_keys,
                )
            )
    return tuple(groups)


def _render_runtime_resource_groups(
    config: dict[str, Any],
    *,
    readonly: bool,
    render_fields: Callable[[Iterable[str]], None],
    translate: Callable[[str], str] | None = None,
) -> None:
    from nicegui import ui

    with ui.element("div").classes("runtime-control-groups"):
        for group_key, label, description, keys in runtime_resource_groups(
            config,
            readonly=readonly,
        ):
            with ui.element("section").classes(
                f"runtime-control-group runtime-control-group-{group_key}"
            ):
                group_label = ui.label(_group_text(translate, group_key, label)).classes(
                    "runtime-control-group-title"
                )
                group_label.tooltip(_group_text(translate, f"{group_key}_help", description))
                with ui.element("div").classes("runtime-control-grid"):
                    render_fields(keys)


def render_config_inspector(
    config: dict[str, Any],
    *,
    missing_label: str = "Missing",
    advanced_label: str = "Raw-only keys",
    full_config_label: str = "Full structured config",
    translate: Callable[[str], str] | None = None,
) -> None:
    from nicegui import ui

    with ui.element("div").classes("config-inspector"):
        inspector_groups = list(CONFIG_INSPECTOR_GROUPS)
        legacy_keys = tuple(
            key for key in _LEGACY_RUNTIME_CONTROL_KEYS if key in config
        )
        if legacy_keys:
            inspector_groups.append(
                (_RUNTIME_GROUP_BY_KEY["legacy"].label, legacy_keys)
            )
        for title, keys in inspector_groups:
            with ui.element("section").classes("config-inspector-section"):
                ui.label(_group_text(translate, _inspector_group_key(title), title)).classes(
                    "config-inspector-title"
                )
                with ui.element("div").classes("config-inspector-rows"):
                    for key in keys:
                        field = FIELD_BY_KEY.get(key)
                        if field is None:
                            continue
                        value = field_value(config, field)
                        with ui.element("div").classes("config-inspector-row"):
                            with ui.element("div").classes("config-inspector-key-stack"):
                                ui.label(_field_label(field, translate)).classes("config-inspector-key")
                                if key in _RUNTIME_CONTROL_BY_KEY:
                                    ui.label(key).classes("config-inspector-technical-key")
                            ui.label(_inspector_value_text(value, translate)).classes(_inspector_value_classes(value))

        missing = required_missing(config)
        advanced = unmapped_keys(config)
        if missing or advanced:
            with ui.element("div").classes("config-inspector-alerts"):
                if missing:
                    ui.label(_label_value(translate, missing_label, ", ".join(missing))).classes(
                        "product-small-muted product-status-missing"
                    )
                if advanced:
                    ui.label(_label_value(translate, advanced_label, str(len(advanced)))).classes(
                        "product-small-muted"
                    )

        with ui.expansion(full_config_label, icon="tune", value=False).classes("config-full-expansion w-full").props(
            "dense"
        ):
            render_structured_config_panel(
                config,
                mode="read",
                missing_label=missing_label,
                advanced_label=advanced_label,
                translate=translate,
            )


def _inspector_value_classes(value: Any) -> str:
    classes = ["config-inspector-value"]
    if isinstance(value, bool):
        classes.append("config-inspector-bool")
        classes.append("config-inspector-bool-on" if value else "config-inspector-bool-off")
    return " ".join(classes)


def render_config_control(
    field: ConfigField,
    config: dict[str, Any],
    *,
    readonly: bool = False,
    on_change: Callable[..., Any] | None = None,
    translate: Callable[[str], str] | None = None,
):
    from nicegui import ui

    value = field_value(config, field)
    field_label_text = _field_label(field, translate)
    field_help_text = _field_help(field, translate)
    label = field_label_text + (" *" if field.required else "")
    control_classes = ["form-control"]
    if field.kind == "path":
        control_classes.append("form-control-long")
    if field.kind == "bool":
        control_classes.append("form-control-toggle")
    with ui.element("div").classes(" ".join(control_classes)):
        with ui.element("div").classes("form-control-label-stack"):
            with ui.row().classes("form-control-label-row"):
                field_label = ui.label(label).classes("form-control-label")
                if field_help_text:
                    help_icon = ui.icon("o_help_outline").classes("form-control-help").props(
                        f'tabindex="0" role="img" aria-label={prop_text(field_help_text)}'
                    )
                    with help_icon:
                        help_tip = ui.tooltip(field_help_text)
                    # Quasar tooltips only open on hover; mirror that for keyboard focus.
                    help_icon.on("focus", lambda _, tip=help_tip: tip.run_method("show"))
                    help_icon.on("blur", lambda _, tip=help_tip: tip.run_method("hide"))
                else:
                    field_label.tooltip(label)
            ui.label(field.key).classes("form-control-technical-key")
        if field.kind == "bool":
            control = ui.checkbox(value=bool(value), on_change=on_change).classes(
                "form-control-value form-control-checkbox"
            )
            if readonly:
                control.props("disable")
        elif field.kind == "enum":
            choices = list(field.choices)
            if readonly and value not in choices and value not in {None, ""}:
                choices.append(value)
            control = ui.select(
                choices,
                value=value,
                on_change=on_change,
            ).classes("form-control-value w-full").props(
                "dense outlined hide-bottom-space readonly"
                if readonly
                else "dense outlined hide-bottom-space"
            )
        elif field.kind in {"int", "float"}:
            control = ui.number(value=value, on_change=on_change).classes(
                "form-control-value w-full"
            ).props(
                "dense outlined hide-bottom-space readonly"
                if readonly
                else "dense outlined hide-bottom-space"
            )
            if field.minimum is not None:
                control.props(f"min={field.minimum}")
            if field.maximum is not None:
                control.props(f"max={field.maximum}")
            if field.kind == "int" and not readonly:
                # The parser truncates 20.5 to 20; say so instead of silently changing the value.
                control.props("step=1")
                control.validation = {
                    _translated_text(translate, "config.error.integer", "Enter a whole number"): (
                        lambda value: value is None or value == "" or float(value).is_integer()
                    )
                }
        else:
            control = ui.input(
                value="" if value is None else str(value),
                on_change=on_change,
            ).classes("form-control-value w-full").props(
                "dense outlined hide-bottom-space readonly"
                if readonly
                else "dense outlined hide-bottom-space"
            )
        control.props(f'aria-label="{label}"')
        if field_help_text:
            control.tooltip(field_help_text)
    return control


def _field_label(
    field: ConfigField,
    translate: Callable[[str], str] | None,
) -> str:
    return _translated_text(translate, field.label_key or f"config.field.{field.key}.label", field.label)


def _field_help(
    field: ConfigField,
    translate: Callable[[str], str] | None,
) -> str:
    return _translated_text(translate, field.help_key or f"config.field.{field.key}.help", field.help)


def _label_value(translate: Callable[..., str] | None, label: str, value: str) -> str:
    if translate is None:
        return f"{label}: {value}"
    text = translate("common.label_value", label=label, value=value)
    return f"{label}: {value}" if text.startswith("[[") else text


def _group_text(translate: Callable[[str], str] | None, key: str, fallback: str) -> str:
    return _translated_text(translate, f"config.group.{key}", fallback)


def _inspector_group_key(title: str) -> str:
    if title == _RUNTIME_GROUP_BY_KEY["legacy"].label:
        return "legacy"
    return re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_")


def _translated_text(
    translate: Callable[[str], str] | None,
    key: str,
    fallback: str,
) -> str:
    if translate is None or not key:
        return fallback
    value = translate(key)
    return fallback if value.startswith("[[") and value.endswith("]]") else value
