from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Mapping

from utils.read_eligibility import ReadRule

from .cache_epoch_status import RunRootCacheEpochStatus, inspect_run_root_cache_epoch
from .config_keys import raw_preview_config_keys
from .output_projection import (
    PhysicalOutputImpactPreviewV1,
    preview_physical_output_impact_v1,
    terminal_annotation_root_notice,
)
from .pipeline_contracts import PipelineContract
from .run_layout import RunLayout
from quantification_config import (
    mode_scoped_quantification_payload,
    resolved_quantification_mode,
    terminal_aware_quantification_payload,
)
from .rerun_preview import RerunPreview, diff_param_values, preview_rerun_from_params


DEFAULT_RUN_CONFIG_SNAPSHOT = "full_config"

_RAW_PREVIEW_KEYS = raw_preview_config_keys()


@dataclass(frozen=True)
class EffectiveConfigProjection:
    raw_config: Mapping[str, Any]
    preview_params: Mapping[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "raw_config": dict(self.raw_config),
            "preview_params": _normalize_value(self.preview_params),
        }


@dataclass(frozen=True)
class RunConfigPreview:
    old_projection: EffectiveConfigProjection
    new_projection: EffectiveConfigProjection
    rerun_preview: RerunPreview
    physical_output_impact: PhysicalOutputImpactPreviewV1 | None = None
    # Run-root cache generation (not a config key). When ``rebuild_required``
    # the config-only impact below is overridden by a one-time full rebuild.
    cache_epoch: RunRootCacheEpochStatus | None = None
    # Pre-run notice: the run root records another terminal annotation schema.
    annotation_schema_notice: str = ""

    @property
    def full_cache_rebuild(self) -> bool:
        return self.cache_epoch is not None and self.cache_epoch.rebuild_required

    @property
    def changed_effective_keys(self) -> tuple[str, ...]:
        return self.rerun_preview.changed_keys

    @property
    def changed_raw_keys(self) -> tuple[str, ...]:
        changes = diff_param_values(
            self.old_projection.raw_config,
            self.new_projection.raw_config,
        )
        return tuple(change.key for change in changes)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "full_cache_rebuild": self.full_cache_rebuild,
            "cache_epoch": None if self.cache_epoch is None else self.cache_epoch.as_dict(),
            "annotation_schema_notice": self.annotation_schema_notice,
            "changed_raw_keys": self.changed_raw_keys,
            "changed_effective_keys": self.changed_effective_keys,
            "old_projection": self.old_projection.as_dict(),
            "new_projection": self.new_projection.as_dict(),
            "impacted_tasks": [
                {
                    "stage": impact.stage,
                    "task_base": impact.task_base,
                    "kind": impact.kind,
                    "changed_param_keys": impact.changed_param_keys,
                    "changed_identity_keys": impact.changed_identity_keys,
                    "changed_input_keys": impact.changed_input_keys,
                    "upstream_task_keys": impact.upstream_task_keys,
                    "consumed_artifacts": impact.consumed_artifacts,
                }
                for impact in self.rerun_preview.impacted_tasks
            ],
            "physical_output_impact": (
                None
                if self.physical_output_impact is None
                else self.physical_output_impact.as_dict()
            ),
        }


def build_effective_config_projection(config: Mapping[str, Any] | Namespace) -> EffectiveConfigProjection:
    raw = _to_mapping(config)
    preview_params: Dict[str, Any] = {}

    for key in _RAW_PREVIEW_KEYS:
        if key in raw:
            preview_params[key] = raw[key]

    exp_threshold = _peak_threshold_label(raw, is_polyA=False)
    polyA_threshold = _peak_threshold_label(raw, is_polyA=True)
    exp_mode = _get(raw, "exp_peak_threshold_mode", "fixed")
    polyA_mode = _get(raw, "polyA_peak_threshold_mode", "fixed")
    exp_merge = _get(raw, "merge_distance")
    polyA_merge = _get(raw, "merge_distance_polyA", exp_merge)

    # Preview only: the executing stage still builds its full byte fingerprint.
    preview_params["deepcps_fingerprint"] = (
        ("enabled", bool(raw.get("deepcps", False))),
        *((key, raw.get(key, default)) for key, default in (
            ("reference_fasta", None), ("deepcps_model_dir", None),
            ("deepcps_model_manifest", None), ("deepcps_reference_sha256", None),
            ("deepcps_threads", 2), ("deepcps_batch_size", 128),
        ) if raw.get("deepcps", False)),
    )

    preview_params.update(
        {
            "dedup_method": _get(raw, "dedup_method"),
            "polyA_status": _polyA_status(raw),
            "coverage_scaling": _get(raw, "coverage_scaling", "none"),
            "quantification_mode": resolved_quantification_mode(raw),
            "scaling_factor": (
                ("exp", _scaling_state(raw, is_polyA=False)),
                ("polyA", _scaling_state(raw, is_polyA=True)),
            ),
            "depth_threshold": (
                ("exp", exp_threshold),
                ("polyA", polyA_threshold),
            ),
            "depth_threshold_polyA": polyA_threshold,
            "merge_distance": (
                ("exp", exp_merge),
                ("polyA", polyA_merge),
            ),
            "merge_distance_polyA": polyA_merge,
            "peak_threshold_mode": (
                ("exp", exp_mode),
                ("polyA", polyA_mode),
            ),
            "dynamic_threshold_stat": (
                ("exp", _get(raw, "exp_dynamic_threshold_stat")),
                ("polyA", _get(raw, "polyA_dynamic_threshold_stat")),
            ),
            "dynamic_threshold_alpha": (
                ("exp", _get(raw, "exp_dynamic_threshold_alpha")),
                ("polyA", _get(raw, "polyA_dynamic_threshold_alpha")),
            ),
            "dynamic_threshold_floor": (
                ("exp", _get(raw, "exp_dynamic_threshold_floor")),
                ("polyA", _get(raw, "polyA_dynamic_threshold_floor")),
            ),
            "dynamic_threshold_normalized": (
                ("exp", _get(raw, "exp_dynamic_threshold_normalized")),
                ("polyA", _get(raw, "polyA_dynamic_threshold_normalized")),
            ),
            "peak_extra_key": _peak_extra_key_signature(raw, exp_threshold, polyA_threshold),
            "rna_gene_extra_key": _rna_gene_extra_key_signature(raw),
            "summary_extra_key": _summary_extra_key_signature(raw),
        }
    )

    return EffectiveConfigProjection(raw_config=raw, preview_params=preview_params)


def preview_rerun_from_run_configs(
    old_config: Mapping[str, Any] | Namespace,
    new_config: Mapping[str, Any] | Namespace,
    *,
    contract: PipelineContract | None = None,
) -> RunConfigPreview:
    old_projection = build_effective_config_projection(old_config)
    new_projection = build_effective_config_projection(new_config)
    keys = list(dict.fromkeys([*old_projection.preview_params, *new_projection.preview_params]))
    rerun_preview = preview_rerun_from_params(
        old_projection.preview_params,
        new_projection.preview_params,
        keys=keys,
        contract=contract,
    )
    physical_output_impact = preview_physical_output_impact_v1(
        old_projection.raw_config,
        new_projection.raw_config,
        rerun_preview=rerun_preview,
        old_effective_params=old_projection.preview_params,
        new_effective_params=new_projection.preview_params,
    )
    return RunConfigPreview(
        old_projection=old_projection,
        new_projection=new_projection,
        rerun_preview=rerun_preview,
        physical_output_impact=physical_output_impact,
    )


def preview_rerun_from_state(
    state_or_db_path: str | Path,
    proposed_config: Mapping[str, Any] | Namespace,
    *,
    snapshot_key: str = DEFAULT_RUN_CONFIG_SNAPSHOT,
    contract: PipelineContract | None = None,
) -> RunConfigPreview:
    stored_config = load_run_config_snapshot(state_or_db_path, snapshot_key=snapshot_key)
    preview = preview_rerun_from_run_configs(
        stored_config,
        proposed_config,
        contract=contract,
    )
    preview = with_run_root_cache_epoch(preview, state_or_db_path)
    layout = _layout_for_proposal(state_or_db_path, proposed_config)
    if layout is None:
        return preview
    return replace(
        preview,
        annotation_schema_notice=terminal_annotation_root_notice(layout, proposed_config),
    )


def _layout_for_proposal(
    state_or_db_path: str | Path,
    proposed_config: Mapping[str, Any] | Namespace,
) -> RunLayout | None:
    """The run root the proposed config would run in (no directory is created)."""

    proposed = _to_mapping(proposed_config)
    output_dir, run_id = proposed.get("output_dir"), proposed.get("run_id")
    if output_dir not in (None, "") and run_id not in (None, ""):
        return RunLayout(out=Path(str(output_dir)).expanduser(), run_id=str(run_id))
    db_path = _resolve_db_path(state_or_db_path)
    if db_path.parent.name != "state":
        return None
    root = db_path.parent.parent
    return RunLayout(out=root.parent, run_id=root.name)


def with_run_root_cache_epoch(
    preview: RunConfigPreview,
    state_or_db_path: str | Path,
) -> RunConfigPreview:
    """Attach the run root's cache-generation status (bounded read-only check)."""

    return replace(preview, cache_epoch=inspect_run_root_cache_epoch(state_or_db_path))


def load_run_config_snapshot(
    state_or_db_path: str | Path,
    *,
    snapshot_key: str = DEFAULT_RUN_CONFIG_SNAPSHOT,
) -> Dict[str, Any]:
    db_path = _resolve_db_path(state_or_db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"State database does not exist: {db_path}")
    with sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro",
        uri=True,
    ) as con:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        row = None
        if _table_exists(con, "run_config_ledger"):
            row = con.execute(
                "SELECT value_json FROM run_config_ledger WHERE snapshot_key=?",
                (snapshot_key,),
            ).fetchone()
        if row is None:
            row = _load_legacy_meta_config(con, snapshot_key)
    if row is None:
        raise KeyError(f"Run config snapshot not found: {snapshot_key!r} in {db_path}")
    value = json.loads(row[0])
    if not isinstance(value, dict):
        raise TypeError(f"Run config snapshot {snapshot_key!r} is not a JSON object")
    return value


def _table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _load_legacy_meta_config(
    con: sqlite3.Connection,
    snapshot_key: str,
) -> tuple[str] | None:
    if not _table_exists(con, "meta"):
        return None
    legacy_key_by_snapshot = {
        DEFAULT_RUN_CONFIG_SNAPSHOT: "config_json",
        "immutable_config": "immutable_config_json",
    }
    legacy_key = legacy_key_by_snapshot.get(snapshot_key)
    if legacy_key is None:
        return None
    return con.execute(
        "SELECT value FROM meta WHERE key=?",
        (legacy_key,),
    ).fetchone()


def _to_mapping(config: Mapping[str, Any] | Namespace) -> Dict[str, Any]:
    if isinstance(config, Namespace):
        return dict(vars(config))
    return dict(config)


def _get(config: Mapping[str, Any], key: str, default: Any = None) -> Any:
    return config.get(key, default)


def _polyA_status(config: Mapping[str, Any]) -> str | None:
    minpolyA = _get(config, "minpolyA")
    nonA_allowed = _get(config, "nonA_allowed")
    minMAPQ = _get(config, "minMAPQ")
    if minpolyA is None or nonA_allowed is None or minMAPQ is None:
        return None
    rule = None
    if _get(config, "count_read_rule") == "unified":
        rule = ReadRule(
            "unified",
            int(minMAPQ),
            _get(config, "multimapper_policy"),
            _get(config, "count_assignment"),
        ).counting_token
    return f"polyA-{minpolyA}-{nonA_allowed}-{minMAPQ}" + (f"-{rule}" if rule else "")


def _coverage_scaled(config: Mapping[str, Any], *, is_polyA: bool) -> bool:
    scaling = _get(config, "coverage_scaling", "none")
    if is_polyA:
        return scaling in ("polyA", "both")
    return scaling in ("expr", "both")


def _scaling_state(config: Mapping[str, Any], *, is_polyA: bool) -> str:
    return "scaled" if _coverage_scaled(config, is_polyA=is_polyA) else "unscaled"


def _peak_threshold_label(config: Mapping[str, Any], *, is_polyA: bool) -> str | None:
    if is_polyA:
        mode = _get(config, "polyA_peak_threshold_mode", "fixed")
        if mode == "dynamic":
            stat = _get(config, "polyA_dynamic_threshold_stat")
            if stat == "max_norm_fixed":
                return f"dyn-max_norm_fixed-n{_get(config, 'polyA_dynamic_threshold_normalized')}"
            return (
                f"dyn-{stat}"
                f"-a{_get(config, 'polyA_dynamic_threshold_alpha')}"
                f"-f{_get(config, 'polyA_dynamic_threshold_floor')}"
            )
        key = "scaled_depth_threshold_polyA" if _coverage_scaled(config, is_polyA=True) else "unscaled_depth_threshold_polyA"
        value = _get(config, key)
        return None if value is None else str(value)

    mode = _get(config, "exp_peak_threshold_mode", "fixed")
    if mode == "dynamic":
        stat = _get(config, "exp_dynamic_threshold_stat")
        if stat == "max_norm_fixed":
            return f"dyn-max_norm_fixed-n{_get(config, 'exp_dynamic_threshold_normalized')}"
        return (
            f"dyn-{stat}"
            f"-a{_get(config, 'exp_dynamic_threshold_alpha')}"
            f"-f{_get(config, 'exp_dynamic_threshold_floor')}"
        )
    key = "scaled_depth_threshold" if _coverage_scaled(config, is_polyA=False) else "unscaled_depth_threshold"
    value = _get(config, key)
    return None if value is None else str(value)


def _peak_extra_key_signature(
    config: Mapping[str, Any],
    exp_threshold: str | None,
    polyA_threshold: str | None,
) -> tuple[tuple[str, Any], ...]:
    keys = (
        "dedup_method",
        "coverage_scaling",
        "merge_distance",
        "merge_distance_polyA",
        "misprime_A_count",
        "misprime_in",
        "misprimed_ratio_threshold",
        "annotate_peak_source",
    )
    values = [(key, _get(config, key)) for key in keys]
    values.extend(mode_scoped_quantification_payload(config).items())
    values.extend(
        [
            ("polyA_status", _polyA_status(config)),
            ("depth_threshold", exp_threshold),
            ("depth_threshold_polyA", polyA_threshold),
        ]
    )
    return tuple(values)


def _rna_gene_extra_key_signature(config: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    return (
        ("dedup_method", _get(config, "dedup_method")),
        ("cb_tag", _get(config, "cb_tag")),
        ("umi_tag", _get(config, "umi_tag")),
    )


def _summary_extra_key_signature(
    config: Mapping[str, Any],
) -> tuple[tuple[str, Any], ...]:
    values: list[tuple[str, Any]] = [
        ("psi_min_gene_umis", _get(config, "psi_min_gene_umis")),
    ]
    values.extend(terminal_aware_quantification_payload(config).items())
    return tuple(values)


def _resolve_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path)
    if path.is_dir() or path.suffix != ".sqlite":
        return path / "state.sqlite"
    return path


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_value(val) for key, val in value.items()}
    if isinstance(value, tuple):
        return [_normalize_value(val) for val in value]
    if isinstance(value, list):
        return [_normalize_value(val) for val in value]
    if isinstance(value, Path):
        return str(value)
    return value
