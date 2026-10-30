from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scpolya_ui.command_builder import RunCommand, build_run_command, materialize_execution_config
from scpolya_ui.errors import UserError
from scpolya_ui.config_schema import (
    overlay_form_values,
    project_active_run_config,
    required_missing,
    unmapped_keys,
)
from scpolya_ui.run_data import (
    RunConfigPreview,
    changed_key_rows,
    output_projection_rows,
    preview_to_impact_rows,
    preview_to_output_impact_rows,
)
from task_manage import preview_rerun_from_state
from task_manage import load_run_config_snapshot, preview_rerun_from_run_configs
from task_manage.rerun_preview import diff_param_values
from task_manage.output_projection import terminal_annotation_root_notice
from task_manage.run_layout import RunLayout


def config_to_json(config: Mapping[str, Any]) -> str:
    return json.dumps(dict(config), indent=2, sort_keys=True)


def parse_config_json(text: str) -> dict[str, Any]:
    payload = json.loads(text or "{}")
    if not isinstance(payload, dict):
        raise UserError("run_setup.json_not_object", "Config JSON must be an object")
    return payload


def apply_form_values(config: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    return overlay_form_values(config, values)


def config_validation_summary(config: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return required_missing(config), unmapped_keys(config)


def build_command_for_config(
    config: Mapping[str, Any],
    *,
    python_executable: str | None = None,
    main_path: str | Path = "main.py",
) -> RunCommand:
    return build_run_command(
        project_active_run_config(config),
        python_executable=python_executable or sys.executable,
        main_path=main_path,
    )


def materialize_config_for_execution(
    config: Mapping[str, Any],
    *,
    python_executable: str | None = None,
    main_path: str | Path = "main.py",
) -> dict[str, Any]:
    command = build_command_for_config(
        config,
        python_executable=python_executable,
        main_path=main_path,
    )
    return materialize_execution_config(command.argv)


@dataclass(frozen=True)
class ExecutionConfigComparison:
    """One comparison contract for Run Setup review, preview, and confirmation.

    ``config`` is the sparse user-facing draft.  The two execution configs are
    parser-materialized and are therefore the only values used for the primary
    change count.  A sparse draft can legitimately differ from a materialized
    config because an omitted value is an inactive or parser-defaulted field.
    """

    base_config: dict[str, Any]
    proposed_config: dict[str, Any]
    base_execution_config: dict[str, Any]
    proposed_execution_config: dict[str, Any]
    preview: RunConfigPreview
    intent_changed_keys: tuple[str, ...] = ()
    execution_changed_keys: tuple[str, ...] = ()
    normalized_only_keys: tuple[str, ...] = ()
    materialization_warnings: tuple[str, ...] = ()

    @property
    def effective_changed_keys(self) -> tuple[str, ...]:
        return self.preview.changed_effective_keys

    @property
    def impacted_task_count(self) -> int:
        return len(self.preview.rerun_preview.impacted_tasks)

    @property
    def output_impact_count(self) -> int:
        impact = self.preview.physical_output_impact
        return len(impact.impacts) if impact is not None else 0

    @property
    def execution_change_count(self) -> int:
        return len(self.execution_changed_keys)

    @property
    def semantic_change_count(self) -> int:
        return len(self.effective_changed_keys)


def compare_execution_configs(
    base_config: Mapping[str, Any],
    proposed_config: Mapping[str, Any],
) -> ExecutionConfigComparison:
    """Compare two Run Setup configs using parser-materialized values.

    This helper is intentionally independent of a state database so a selected
    historical Run Record and the workspace snapshot use the same semantics.
    """

    base = dict(base_config)
    proposed = dict(proposed_config)
    base_execution, base_warning = _materialize_or_fallback(base)
    proposed_execution, proposed_warning = _materialize_or_fallback(proposed)
    preview = preview_rerun_from_run_configs(base_execution, proposed_execution)
    intent_changes = tuple(
        change.key for change in diff_param_values(base, proposed)
    )
    execution_changes = tuple(
        change.key for change in diff_param_values(base_execution, proposed_execution)
    )
    execution_change_set = set(execution_changes)
    normalized_only = tuple(
        key for key in intent_changes if key not in execution_change_set
    )
    warnings = tuple(
        warning for warning in (base_warning, proposed_warning) if warning
    )
    return ExecutionConfigComparison(
        base_config=base,
        proposed_config=proposed,
        base_execution_config=base_execution,
        proposed_execution_config=proposed_execution,
        preview=preview,
        intent_changed_keys=intent_changes,
        execution_changed_keys=execution_changes,
        normalized_only_keys=normalized_only,
        materialization_warnings=warnings,
    )


def execution_diff_rows(comparison: ExecutionConfigComparison) -> list[dict[str, Any]]:
    """Return only parser-effective direct config rows for the review table."""

    rows: list[dict[str, Any]] = []
    keys = set(comparison.execution_changed_keys)
    for key in comparison.execution_changed_keys:
        rows.append(
            {
                "row_key": f"execution:{key}",
                "key": key,
                "old_value": _display_value(comparison.base_execution_config.get(key)),
                "new_value": _display_value(comparison.proposed_execution_config.get(key)),
                "changed": True,
            }
        )
    # Keep deterministic behavior if a future comparison implementation adds
    # keys outside the direct diff order.
    return [row for row in rows if row["key"] in keys]


def effective_key_rows(comparison: ExecutionConfigComparison) -> list[dict[str, str]]:
    return [
        {
            "row_key": f"effective:{key}",
            "key": key,
            "type": "task",
        }
        for key in comparison.effective_changed_keys
    ]


def preview_run_setup(
    state_db_path: str | Path,
    config: Mapping[str, Any],
    *,
    base_config: Mapping[str, Any] | None = None,
) -> RunConfigPreview:
    stored = (
        dict(base_config)
        if base_config is not None
        else load_run_config_snapshot(state_db_path)
    )
    return compare_execution_configs(stored, config).preview


def preview_changed_rows(preview: RunConfigPreview) -> list[dict[str, Any]]:
    return changed_key_rows(preview)


def preview_impact_rows(preview: RunConfigPreview) -> list[dict[str, Any]]:
    return preview_to_impact_rows(preview)


def annotation_schema_root_notice(config: Mapping[str, Any]) -> str:
    """Pre-launch notice when the target run root records another annotation schema.

    Same read-only check as ``main.py`` startup, evaluated on the root the
    config would run in, so it disappears once a new run root is chosen.
    """

    output_dir = str(config.get("output_dir") or "").strip()
    run_id = str(config.get("run_id") or "").strip()
    if not output_dir or not run_id:
        return ""
    layout = RunLayout(out=Path(output_dir).expanduser(), run_id=run_id)
    return terminal_annotation_root_notice(layout, dict(config))


def preview_output_rows(preview: RunConfigPreview) -> list[dict[str, Any]]:
    return preview_to_output_impact_rows(preview)


def proposed_output_rows(preview: RunConfigPreview) -> list[dict[str, Any]]:
    return output_projection_rows(preview)


def _materialize_or_fallback(config: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    try:
        return materialize_config_for_execution(config), ""
    except Exception as exc:
        # Run Setup can still be rendered while required fields are incomplete;
        # validation will surface the actionable error in the form.
        return dict(config), f"Execution defaults could not be materialized: {exc}"


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    return str(value)
