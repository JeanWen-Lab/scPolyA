from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence, Tuple

from .dependency_graph import StaticDependencyGraph, build_static_dependency_graph
from .finalized_unit_inputs import (
    FinalizedUnitInputRow,
    FinalizedUnitInputTable,
    FinalizedUnitInputTableLoad,
    load_finalized_unit_input_table_for_state,
)
from .input_provenance import input_artifact_records_from_declared_inputs
from .artifact_naming import (
    annotation_name_payload,
    build_scoped_artifact_name_v1,
    peak_name_payload,
)
from .pipeline_contracts import PipelineContract, PipelineTaskContract, TaskKey
from .reuse_report import (
    CurrentReuseDecisionSummaryV2,
    current_reuse_decision_summary_v2_from_v1_compatibility,
    load_current_reuse_decision_summaries_v2,
)
from .run_config_preview import load_run_config_snapshot
from .run_layout import RunLayout
from .sc_item_contracts import resolve_dedup_method
from .task_completion import (
    TaskCompletionLoadError,
    load_task_completion_snapshot_from_connection_v2,
)
from .task_parameter_ledger import (
    TaskParameterLedgerError,
    load_current_task_parameter_ledger_records_v3,
)
from quantification_config import (
    resolved_quantification_mode,
    terminal_annotation_schema_from_coordinate_mode,
)


DynamicInstanceEdgeKind = Literal[
    "resolved",
    "inline_observed",
    "fan_in",
    "fan_in_complete",
    "heuristic_matched",
    "singleton",
    "ambiguous",
    "unmatched",
    "producer_unobserved",
    "static_ambiguous",
    "static_self",
    "external",
    "external_current_ready",
    "external_current_missing",
    "external_current_empty",
    "external_current_not_applicable",
    "external_current_unresolved",
]
ObserveOnlyPlanAction = Literal["would_reuse", "would_run", "blocked"]
DerivedExtraKeyMode = Literal["none", "summary_psi_suffix", "consumer_extra_key_exact"]
ProducerSignatureMode = Literal["none", "annotated_peak_signature"]
ConsumerArtifactSelectionMode = Literal["none", "annotate_peak_source_resolved"]
UnitOutputLineageMode = Literal[
    "dedup_source",
    "rewrite_source",
    "strand_source",
    "merge_sources",
]


_BASE_FINAL_PEAK_FIELD_KEYS = (
    "dedup_method",
    "polyA_status",
    "coverage_scaling",
    "depth_threshold",
    "depth_threshold_polyA",
    "merge_distance",
    "merge_distance_polyA",
    "misprime_A_count",
    "misprime_in",
    "misprimed_ratio_threshold",
)

_ANNOTATED_PEAK_FIELD_KEYS = (*_BASE_FINAL_PEAK_FIELD_KEYS, "annotate_peak_source")


@dataclass(frozen=True)
class InstanceEdgeResolver:
    """Explicit semantic join rule for one declared producer/consumer edge."""

    producer_key: TaskKey
    consumer_key: TaskKey
    artifact_key: str
    match_field_keys: Tuple[str, ...]
    fan_in_identity_field_keys: Tuple[str, ...] = ()
    producer_identity_constants: Tuple[Tuple[str, Any], ...] = ()
    derived_extra_key_mode: DerivedExtraKeyMode = "none"
    producer_signature_mode: ProducerSignatureMode = "none"
    consumer_artifact_selection_mode: ConsumerArtifactSelectionMode = "none"

    def __post_init__(self) -> None:
        if self.derived_extra_key_mode not in {
            "none",
            "summary_psi_suffix",
            "consumer_extra_key_exact",
        }:
            raise ValueError(f"Unknown derived extra-key mode: {self.derived_extra_key_mode!r}")
        if self.producer_signature_mode not in {"none", "annotated_peak_signature"}:
            raise ValueError(f"Unknown producer signature mode: {self.producer_signature_mode!r}")
        if self.consumer_artifact_selection_mode not in {"none", "annotate_peak_source_resolved"}:
            raise ValueError(
                "Unknown consumer artifact selection mode: "
                f"{self.consumer_artifact_selection_mode!r}"
            )
        if self.producer_signature_mode != "none" and self.derived_extra_key_mode == "none":
            raise ValueError("Producer-signature resolvers require a derived consumer extra_key")
        if (
            self.derived_extra_key_mode == "consumer_extra_key_exact"
            and self.producer_signature_mode != "annotated_peak_signature"
        ):
            raise ValueError(
                "Exact consumer extra-key resolvers require an annotated peak producer signature"
            )
        signature_only = (
            self.producer_signature_mode == "annotated_peak_signature"
            and self.derived_extra_key_mode == "consumer_extra_key_exact"
            and not self.fan_in_identity_field_keys
        )
        if not self.match_field_keys and not self.producer_identity_constants and not signature_only:
            raise ValueError(
                "InstanceEdgeResolver requires a match field, an explicit producer constant, "
                "or a non-fan-in annotated peak signature-to-consumer-key rule"
            )
        if len(set(self.match_field_keys)) != len(self.match_field_keys):
            raise ValueError("InstanceEdgeResolver match fields must be unique")
        if len(set(self.fan_in_identity_field_keys)) != len(self.fan_in_identity_field_keys):
            raise ValueError("InstanceEdgeResolver fan-in fields must be unique")
        if set(self.match_field_keys).intersection(self.fan_in_identity_field_keys):
            raise ValueError("InstanceEdgeResolver fan-in fields must not also be match fields")
        constant_keys = tuple(field_key for field_key, _value in self.producer_identity_constants)
        if any(not isinstance(field_key, str) or not field_key.strip() for field_key in constant_keys):
            raise ValueError("InstanceEdgeResolver producer constant keys must be nonblank strings")
        if len(set(constant_keys)) != len(constant_keys):
            raise ValueError("InstanceEdgeResolver producer constant fields must be unique")
        if set(constant_keys).intersection(self.fan_in_identity_field_keys):
            raise ValueError("Producer constant fields must not also be fan-in fields")
        if self.derived_extra_key_mode != "none" and "extra_key" in self.match_field_keys:
            raise ValueError("Derived extra-key resolvers must not also equality-match extra_key")
        if self.consumer_artifact_selection_mode == "annotate_peak_source_resolved" and (
            self.producer_key != ("call_peaks", "combine_and_intersect_peaks")
            or self.consumer_key != ("annotate_peaks", "assign_peaks_to_genes")
            or self.artifact_key != "final_output/*peaks-mispriming-*.bed"
            or self.match_field_keys != _BASE_FINAL_PEAK_FIELD_KEYS
        ):
            raise ValueError(
                "The resolved annotated-peak source selector is only valid for the base final-BED "
                "binding from combine_and_intersect_peaks to assign_peaks_to_genes"
            )

    @property
    def key(self) -> tuple[TaskKey, TaskKey, str]:
        return self.producer_key, self.consumer_key, self.artifact_key

    @property
    def label(self) -> str:
        producer = f"{self.producer_key[0]}:{self.producer_key[1]}"
        consumer = f"{self.consumer_key[0]}:{self.consumer_key[1]}"
        return f"{producer}->{consumer}:{self.artifact_key}"


@dataclass(frozen=True)
class UnitOutputLineageResolver:
    """Task-specific selector for the overloaded ``unit_outputs/*.bam`` key."""

    consumer_key: TaskKey
    mode: UnitOutputLineageMode
    artifact_key: str = "unit_outputs/*.bam"

    def __post_init__(self) -> None:
        expected_modes: Mapping[TaskKey, UnitOutputLineageMode] = {
            ("preprocess_units", "dedup_units"): "dedup_source",
            ("preprocess_units", "rewrite_barcode_suffix"): "rewrite_source",
            ("extract_subsets", "split_strand"): "strand_source",
            ("merge_units", "merge_chunks"): "merge_sources",
            ("merge_units", "merge_strand_chunks"): "merge_sources",
        }
        expected = expected_modes.get(self.consumer_key)
        if expected != self.mode:
            raise ValueError(
                f"Unsupported unit-output lineage resolver: {self.consumer_key!r} "
                f"mode={self.mode!r}"
            )
        expected_artifact = (
            "unit_outputs/*strand.bam"
            if self.consumer_key == ("merge_units", "merge_strand_chunks")
            else "unit_outputs/*.bam"
        )
        if self.artifact_key != expected_artifact:
            raise ValueError(
                "Unit-output lineage resolver artifact mismatch: "
                f"expected {expected_artifact!r}, got {self.artifact_key!r}"
            )

    @property
    def key(self) -> tuple[TaskKey, str]:
        return self.consumer_key, self.artifact_key

    @property
    def label(self) -> str:
        return f"{self.consumer_key[0]}:{self.consumer_key[1]}:{self.mode}"


_BASE_INSTANCE_EDGE_RESOLVERS: Tuple[InstanceEdgeResolver, ...] = (
    InstanceEdgeResolver(
        producer_key=("extract_subsets", "extract_polyA"),
        consumer_key=("calculate_umi", "count_umi_polyA"),
        artifact_key="unit_outputs/*.polyA*.bam",
        match_field_keys=("uid", "dedup_method", "polyA_status"),
    ),
    InstanceEdgeResolver(
        producer_key=("merge_units", "merge_chunks"),
        consumer_key=("merge_units", "cat_chunks"),
        artifact_key="merged_cohort/chunk*.bam",
        match_field_keys=("dedup_method", "polyA_status", "strand"),
        fan_in_identity_field_keys=("ctg",),
    ),
    InstanceEdgeResolver(
        producer_key=("merge_units", "merge_chunks"),
        consumer_key=("merge_units", "cat_chunks"),
        artifact_key="merged_cohort/unstranded_chunk*.bam",
        match_field_keys=("dedup_method", "polyA_status", "strand"),
        fan_in_identity_field_keys=("ctg",),
    ),
    InstanceEdgeResolver(
        producer_key=("merge_units", "merge_strand_chunks"),
        consumer_key=("merge_units", "cat_strand_chunks"),
        artifact_key="merged_cohort/strand_chunk*.bam",
        match_field_keys=("dedup_method", "polyA_status", "strand"),
        fan_in_identity_field_keys=("ctg",),
    ),
    InstanceEdgeResolver(
        producer_key=("annotate_bams", "annotate_slices"),
        consumer_key=("count_matrix", "count_unit_matrix"),
        artifact_key="peak_sidecars/*.xr_to_peak*.tsv.gz",
        match_field_keys=("sid", "ctg", "extra_key"),
    ),
    InstanceEdgeResolver(
        producer_key=("rna_gene_matrix", "annotate_gene_units"),
        consumer_key=("rna_gene_matrix", "count_gene_units"),
        artifact_key="rna_gene_matrix_temp/sidecars/*.xr_to_gene.tsv.gz",
        match_field_keys=("sid", "ctg", "dedup_method", "extra_key"),
    ),
    InstanceEdgeResolver(
        producer_key=("count_matrix", "count_unit_matrix"),
        consumer_key=("count_matrix", "summarize_count_matrix"),
        artifact_key="count_matrix_temp/unit_counts/*.tsv.gz",
        match_field_keys=("sid", "dedup_method", "extra_key"),
        fan_in_identity_field_keys=("ctg",),
    ),
    InstanceEdgeResolver(
        producer_key=("rna_gene_matrix", "count_gene_units"),
        consumer_key=("rna_gene_matrix", "summarize_gene_matrix"),
        artifact_key="rna_gene_matrix_temp/unit_counts/*.tsv.gz",
        match_field_keys=("sid", "dedup_method", "extra_key"),
        fan_in_identity_field_keys=("ctg",),
    ),
    InstanceEdgeResolver(
        producer_key=("count_matrix", "summarize_count_matrix"),
        consumer_key=("summarize_matrix", "summarize_matrix"),
        artifact_key="count_matrix/*.counts*.tsv.gz",
        match_field_keys=("sid", "dedup_method"),
        derived_extra_key_mode="summary_psi_suffix",
    ),
    InstanceEdgeResolver(
        producer_key=("annotate_peaks", "resolve_peak_issues"),
        consumer_key=("summarize_matrix", "summarize_matrix"),
        artifact_key="final_output/*peaks-annotated*.bed",
        match_field_keys=("dedup_method",),
        derived_extra_key_mode="summary_psi_suffix",
        producer_signature_mode="annotated_peak_signature",
    ),
    InstanceEdgeResolver(
        producer_key=("call_peaks", "combine_and_intersect_peaks"),
        consumer_key=("annotate_peaks", "assign_peaks_to_genes"),
        artifact_key="final_output/*peaks-mispriming-*.bed",
        match_field_keys=_BASE_FINAL_PEAK_FIELD_KEYS,
        consumer_artifact_selection_mode="annotate_peak_source_resolved",
    ),
    InstanceEdgeResolver(
        producer_key=("annotate_peaks", "assign_peaks_to_genes"),
        consumer_key=("annotate_peaks", "merge_assigned_peaks"),
        artifact_key="final_output/*peaks-assigned*.bed",
        match_field_keys=_ANNOTATED_PEAK_FIELD_KEYS,
    ),
    InstanceEdgeResolver(
        producer_key=("annotate_peaks", "resolve_peak_issues"),
        consumer_key=("annotate_peaks", "convert_peaks_bed_to_bigbed"),
        artifact_key="final_output/*peaks-annotated*.bed",
        match_field_keys=_ANNOTATED_PEAK_FIELD_KEYS,
    ),
    InstanceEdgeResolver(
        producer_key=("annotate_peaks", "resolve_peak_issues"),
        consumer_key=("annotate_peaks", "convert_peaks_bed_to_gtf"),
        artifact_key="final_output/*peaks-annotated*.bed",
        match_field_keys=_ANNOTATED_PEAK_FIELD_KEYS,
    ),
    InstanceEdgeResolver(
        producer_key=("annotate_peaks", "convert_peaks_bed_to_gtf"),
        consumer_key=("annotate_bams", "annotate_slices"),
        artifact_key="final_output/*peaks-annotated*.gtf",
        match_field_keys=(),
        derived_extra_key_mode="consumer_extra_key_exact",
        producer_signature_mode="annotated_peak_signature",
    ),
)

_GLOBAL_UID = (("uid", "global"),)


def _same_global_uid_resolver(
    producer_key: TaskKey,
    consumer_key: TaskKey,
    artifact_key: str,
) -> InstanceEdgeResolver:
    return InstanceEdgeResolver(
        producer_key=producer_key,
        consumer_key=consumer_key,
        artifact_key=artifact_key,
        match_field_keys=("uid",),
        producer_identity_constants=_GLOBAL_UID,
    )


def _global_broadcast_resolver(
    producer_key: TaskKey,
    consumer_key: TaskKey,
    artifact_key: str,
) -> InstanceEdgeResolver:
    return InstanceEdgeResolver(
        producer_key=producer_key,
        consumer_key=consumer_key,
        artifact_key=artifact_key,
        match_field_keys=(),
        producer_identity_constants=_GLOBAL_UID,
    )


DEFAULT_GLOBAL_FLEX_EDGE_RESOLVERS: Tuple[InstanceEdgeResolver, ...] = (
    _same_global_uid_resolver(
        ("register_inputs", "parse_and_link"),
        ("init_references", "adjust_chrom_sizes"),
        "task_meta:bam_has_chr_prefix",
    ),
    _same_global_uid_resolver(
        ("register_inputs", "parse_and_link"),
        ("init_references", "check_adjust_gtf"),
        "inputs/*.bam",
    ),
    _same_global_uid_resolver(
        ("register_inputs", "parse_and_link"),
        ("init_references", "check_adjust_gtf"),
        "task_meta:bam_has_chr_prefix",
    ),
    _same_global_uid_resolver(
        ("init_references", "adjust_chrom_sizes"),
        ("init_references", "check_adjust_gtf"),
        "task_meta:standard_chroms",
    ),
    _same_global_uid_resolver(
        ("init_references", "adjust_chrom_sizes"),
        ("init_references", "filter_chrom_sizes"),
        "task_meta:standard_chroms",
    ),
    _same_global_uid_resolver(
        ("init_references", "check_adjust_gtf"),
        ("init_references", "prefilter_validate_gtf"),
        "reference:adjusted_gtf",
    ),
    _same_global_uid_resolver(
        ("init_references", "prefilter_validate_gtf"),
        ("init_references", "gtf_to_bed"),
        "reference:filtered_adjusted_gtf",
    ),
    _same_global_uid_resolver(
        ("init_references", "prefilter_validate_gtf"),
        ("init_references", "build_gffdb"),
        "reference:filtered_adjusted_gtf",
    ),
    _same_global_uid_resolver(
        ("init_references", "prefilter_validate_gtf"),
        ("init_references", "prepare_cds"),
        "reference:filtered_adjusted_gtf",
    ),
    _same_global_uid_resolver(
        ("init_references", "gtf_to_bed"),
        ("init_references", "sort_gene_bed"),
        "reference:gene_bed",
    ),
    _same_global_uid_resolver(
        ("init_references", "filter_chrom_sizes"),
        ("init_references", "sort_gene_bed"),
        "reference:filtered_standard_chrom_sizes",
    ),
    _same_global_uid_resolver(
        ("init_references", "build_gffdb"),
        ("init_references", "cache_canonical_transcripts"),
        "reference:gffdb",
    ),
    _same_global_uid_resolver(
        ("init_references", "build_gffdb"),
        ("init_references", "cache_stop_codon"),
        "reference:gffdb",
    ),
    _same_global_uid_resolver(
        ("init_references", "cache_canonical_transcripts"),
        ("init_references", "cache_stop_codon"),
        "cache:canonical_transcripts",
    ),
    _same_global_uid_resolver(
        ("init_references", "cache_stop_codon"),
        ("init_references", "generate_stop_codon_bigbed"),
        "reference:stop_codon_positions_tsv",
    ),
    _same_global_uid_resolver(
        ("init_references", "filter_chrom_sizes"),
        ("init_references", "generate_stop_codon_bigbed"),
        "reference:filtered_standard_chrom_sizes",
    ),
    _same_global_uid_resolver(
        ("init_references", "filter_chrom_sizes"),
        ("init_references", "prepare_cds"),
        "reference:filtered_standard_chrom_sizes",
    ),
    _global_broadcast_resolver(
        ("register_inputs", "write_preprocess_csv"),
        ("preprocess_units", "split_to_units"),
        "input_tables/preprocess_inputs.csv",
    ),
    _global_broadcast_resolver(
        ("register_inputs", "write_preprocess_csv"),
        ("preprocess_units", "filter_units"),
        "input_tables/preprocess_inputs.csv",
    ),
    _global_broadcast_resolver(
        ("init_references", "adjust_chrom_sizes"),
        ("preprocess_units", "filter_units"),
        "task_meta:standard_chroms",
    ),
    _global_broadcast_resolver(
        ("init_references", "filter_chrom_sizes"),
        ("call_peaks", "generate_bigwig"),
        "reference:filtered_standard_chrom_sizes",
    ),
    _global_broadcast_resolver(
        ("init_references", "prefilter_validate_gtf"),
        ("rna_gene_matrix", "annotate_gene_units"),
        "reference:filtered_adjusted_gtf",
    ),
    _global_broadcast_resolver(
        ("init_references", "prepare_cds"),
        ("call_peaks", "exclude_cds_region"),
        "reference:sorted_cds_bed",
    ),
    _global_broadcast_resolver(
        ("init_references", "sort_gene_bed"),
        ("call_peaks", "merge_call_peaks"),
        "reference:sorted_gene_bed",
    ),
    _global_broadcast_resolver(
        ("init_references", "sort_gene_bed"),
        ("annotate_peaks", "assign_peaks_to_genes"),
        "reference:sorted_gene_bed",
    ),
    _global_broadcast_resolver(
        ("init_references", "cache_canonical_transcripts"),
        ("annotate_peaks", "merge_assigned_peaks"),
        "cache:canonical_transcripts",
    ),
    _global_broadcast_resolver(
        ("init_references", "cache_stop_codon"),
        ("annotate_peaks", "merge_assigned_peaks"),
        "cache:stop_codon_positions",
    ),
)

DEFAULT_INSTANCE_EDGE_RESOLVERS: Tuple[InstanceEdgeResolver, ...] = (
    *_BASE_INSTANCE_EDGE_RESOLVERS,
    *DEFAULT_GLOBAL_FLEX_EDGE_RESOLVERS,
)

DEFAULT_UNIT_OUTPUT_LINEAGE_RESOLVERS: Tuple[UnitOutputLineageResolver, ...] = (
    UnitOutputLineageResolver(
        consumer_key=("preprocess_units", "dedup_units"),
        mode="dedup_source",
    ),
    UnitOutputLineageResolver(
        consumer_key=("preprocess_units", "rewrite_barcode_suffix"),
        mode="rewrite_source",
    ),
    UnitOutputLineageResolver(
        consumer_key=("extract_subsets", "split_strand"),
        mode="strand_source",
    ),
    UnitOutputLineageResolver(
        consumer_key=("merge_units", "merge_chunks"),
        mode="merge_sources",
    ),
    UnitOutputLineageResolver(
        consumer_key=("merge_units", "merge_strand_chunks"),
        mode="merge_sources",
        artifact_key="unit_outputs/*strand.bam",
    ),
)

_SPLIT_UNIT_KEY: TaskKey = ("preprocess_units", "split_to_units")
_FILTER_UNIT_KEY: TaskKey = ("preprocess_units", "filter_units")
_DEDUP_UNIT_KEY: TaskKey = ("preprocess_units", "dedup_units")
_REWRITE_UNIT_KEY: TaskKey = ("preprocess_units", "rewrite_barcode_suffix")
_EXTRACT_POLYA_KEY: TaskKey = ("extract_subsets", "extract_polyA")
_SPLIT_STRAND_KEY: TaskKey = ("extract_subsets", "split_strand")


_BLOCKING_EDGE_KINDS: frozenset[DynamicInstanceEdgeKind] = frozenset(
    {
        "fan_in",
        "heuristic_matched",
        "singleton",
        "ambiguous",
        "unmatched",
        "producer_unobserved",
        "static_ambiguous",
        "static_self",
        "external",
        "external_current_ready",
        "external_current_missing",
        "external_current_empty",
        "external_current_not_applicable",
        "external_current_unresolved",
    }
)

OPERATOR_MANAGED_EXTERNAL_EDGE_KINDS: frozenset[DynamicInstanceEdgeKind] = frozenset(
    {
        "external",
        "external_current_ready",
        "external_current_missing",
        "external_current_empty",
        "external_current_not_applicable",
        "external_current_unresolved",
    }
)


@dataclass(frozen=True)
class ObserveOnlyPlanPolicy:
    """Named diagnostic policy for classifying otherwise blocking edges."""

    name: str
    nonblocking_edge_kinds: frozenset[DynamicInstanceEdgeKind] = frozenset()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Observe-only plan policy name must be nonblank")
        unsupported = self.nonblocking_edge_kinds.difference(_BLOCKING_EDGE_KINDS)
        if unsupported:
            raise ValueError(
                "Observe-only plan policy contains non-blocking classifications: "
                + ", ".join(sorted(unsupported))
            )

    @property
    def blocking_edge_kinds(self) -> frozenset[DynamicInstanceEdgeKind]:
        return _BLOCKING_EDGE_KINDS.difference(self.nonblocking_edge_kinds)


DEFAULT_OBSERVE_ONLY_PLAN_POLICY = ObserveOnlyPlanPolicy(name="strict_baseline")
OPERATOR_MANAGED_EXTERNAL_PLAN_POLICY = ObserveOnlyPlanPolicy(
    name="operator_managed_external_inputs",
    nonblocking_edge_kinds=OPERATOR_MANAGED_EXTERNAL_EDGE_KINDS,
)

_FINALIZED_UNIT_FAN_IN_RESOLVER_KEYS = frozenset(
    {
        (
            ("count_matrix", "count_unit_matrix"),
            ("count_matrix", "summarize_count_matrix"),
            "count_matrix_temp/unit_counts/*.tsv.gz",
        ),
        (
            ("rna_gene_matrix", "count_gene_units"),
            ("rna_gene_matrix", "summarize_gene_matrix"),
            "rna_gene_matrix_temp/unit_counts/*.tsv.gz",
        ),
    }
)

_COHORT_CHUNK_FAN_IN_RESOLVER_KEYS = frozenset(
    {
        (
            ("merge_units", "merge_chunks"),
            ("merge_units", "cat_chunks"),
            "merged_cohort/chunk*.bam",
        ),
        (
            ("merge_units", "merge_chunks"),
            ("merge_units", "cat_chunks"),
            "merged_cohort/unstranded_chunk*.bam",
        ),
        (
            ("merge_units", "merge_strand_chunks"),
            ("merge_units", "cat_strand_chunks"),
            "merged_cohort/strand_chunk*.bam",
        ),
    }
)


@dataclass(frozen=True)
class DynamicInstanceNode:
    """Latest observed Reuse v2 decision for one stable task instance."""

    instance_id: str
    decision_id: int
    stage: str
    task_base: str
    observed_action: str
    legacy_done: bool
    queued: bool
    force: bool
    identity: Tuple[Tuple[str, Any], ...]
    params: Tuple[Tuple[str, Any], ...]
    consumes: Tuple[str, ...]
    produces: Tuple[str, ...]
    reasons: Tuple[str, ...]
    worker_payload: Mapping[str, Any]
    created_at: str
    reuse_policy_version: str | None = None
    implementation_version: str | None = None
    declaration_source: str | None = None
    legacy_completion_reconciled: bool = False

    @property
    def key(self) -> TaskKey:
        return self.stage, self.task_base

    @property
    def identity_dict(self) -> dict[str, Any]:
        return dict(self.identity)

    @property
    def params_dict(self) -> dict[str, Any]:
        return dict(self.params)

    @property
    def fields_dict(self) -> dict[str, Any]:
        """Return fields across identity and parameter scopes for matching.

        A semantic field such as ``dedup_method`` legitimately appears in an
        identity scope for one task and a parameter scope for another. The
        dynamic graph therefore compares the combined mapping, while retaining
        original scopes for display.
        """

        fields = self.identity_dict
        for key, value in self.params:
            fields.setdefault(key, value)
        return fields

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "decision_id": self.decision_id,
            "stage": self.stage,
            "task_base": self.task_base,
            "observed_action": self.observed_action,
            "legacy_done": self.legacy_done,
            "queued": self.queued,
            "force": self.force,
            "identity": _field_dicts(self.identity),
            "params": _field_dicts(self.params),
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "reasons": list(self.reasons),
            "created_at": self.created_at,
            "reuse_policy_version": self.reuse_policy_version,
            "implementation_version": self.implementation_version,
            "declaration_source": self.declaration_source,
            "legacy_completion_reconciled": (
                self.legacy_completion_reconciled
            ),
        }


@dataclass(frozen=True)
class DynamicInstanceEdge:
    """One concrete or explicitly unresolved input relationship."""

    artifact_key: str
    consumer_instance_id: str
    consumer_key: TaskKey
    kind: DynamicInstanceEdgeKind
    producer_instance_id: str | None = None
    producer_task_keys: Tuple[TaskKey, ...] = ()
    candidate_instance_ids: Tuple[str, ...] = ()
    matched_field_keys: Tuple[str, ...] = ()
    detail: str = ""

    @property
    def is_concrete(self) -> bool:
        return (
            self.kind
            in {
                "resolved",
                "inline_observed",
                "fan_in",
                "fan_in_complete",
            }
            and self.producer_instance_id is not None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_key": self.artifact_key,
            "consumer_instance_id": self.consumer_instance_id,
            "consumer": _task_key_dict(self.consumer_key),
            "kind": self.kind,
            "producer_instance_id": self.producer_instance_id,
            "producer_tasks": [_task_key_dict(key) for key in self.producer_task_keys],
            "candidate_instance_ids": list(self.candidate_instance_ids),
            "matched_field_keys": list(self.matched_field_keys),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DynamicInstanceGraphReport:
    state_db_path: str
    topology_source: str
    edge_resolver_count: int
    declaration_source_counts: Mapping[str, int]
    source_decision_count: int
    node_count: int
    task_count: int
    edge_count: int
    concrete_edge_count: int
    edge_kind_counts: Mapping[str, int]

    @property
    def blocking_edge_count(self) -> int:
        return sum(int(self.edge_kind_counts.get(kind, 0)) for kind in _BLOCKING_EDGE_KINDS)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state_db_path": self.state_db_path,
            "topology_source": self.topology_source,
            "edge_resolver_count": self.edge_resolver_count,
            "declaration_source_counts": dict(self.declaration_source_counts),
            "source_decision_count": self.source_decision_count,
            "node_count": self.node_count,
            "task_count": self.task_count,
            "edge_count": self.edge_count,
            "concrete_edge_count": self.concrete_edge_count,
            "blocking_edge_count": self.blocking_edge_count,
            "edge_kind_counts": dict(self.edge_kind_counts),
        }


@dataclass(frozen=True)
class DynamicInstanceGraph:
    state_db_path: str
    topology_source: str
    edge_resolver_count: int
    source_decision_count: int
    nodes: Tuple[DynamicInstanceNode, ...]
    edges: Tuple[DynamicInstanceEdge, ...]

    @property
    def nodes_by_instance_id(self) -> dict[str, DynamicInstanceNode]:
        return {node.instance_id: node for node in self.nodes}

    @property
    def nodes_by_task_key(self) -> dict[TaskKey, Tuple[DynamicInstanceNode, ...]]:
        grouped: dict[TaskKey, list[DynamicInstanceNode]] = defaultdict(list)
        for node in self.nodes:
            grouped[node.key].append(node)
        return {
            key: tuple(sorted(values, key=_node_sort_key))
            for key, values in grouped.items()
        }

    @property
    def concrete_edges(self) -> Tuple[DynamicInstanceEdge, ...]:
        return tuple(edge for edge in self.edges if edge.is_concrete)

    def incoming_edges(self, instance_id: str) -> Tuple[DynamicInstanceEdge, ...]:
        self._require_node(instance_id)
        return tuple(edge for edge in self.edges if edge.consumer_instance_id == instance_id)

    def outgoing_edges(self, instance_id: str) -> Tuple[DynamicInstanceEdge, ...]:
        self._require_node(instance_id)
        return tuple(edge for edge in self.edges if edge.producer_instance_id == instance_id)

    def report(self) -> DynamicInstanceGraphReport:
        edge_kind_counts = Counter(edge.kind for edge in self.edges)
        declaration_source_counts = Counter(node.declaration_source or "unknown" for node in self.nodes)
        return DynamicInstanceGraphReport(
            state_db_path=self.state_db_path,
            topology_source=self.topology_source,
            edge_resolver_count=self.edge_resolver_count,
            declaration_source_counts=dict(sorted(declaration_source_counts.items())),
            source_decision_count=self.source_decision_count,
            node_count=len(self.nodes),
            task_count=len({node.key for node in self.nodes}),
            edge_count=len(self.edges),
            concrete_edge_count=len(self.concrete_edges),
            edge_kind_counts=dict(sorted(edge_kind_counts.items())),
        )

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        return {
            "report": self.report().as_dict(),
            "nodes": [node.as_dict() for node in _latest_nodes(self.nodes, limit=limit)],
            "edges": [edge.as_dict() for edge in _latest_edges(self.edges, self.nodes_by_instance_id, limit=limit)],
        }

    def _require_node(self, instance_id: str) -> None:
        if instance_id not in self.nodes_by_instance_id:
            raise KeyError(f"Unknown dynamic instance: {instance_id!r}")


@dataclass(frozen=True)
class ObserveOnlyReusePlanEntry:
    instance_id: str
    action: ObserveOnlyPlanAction
    observed_action: str
    strict_reuse_eligible: bool
    blockers: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "action": self.action,
            "observed_action": self.observed_action,
            "strict_reuse_eligible": self.strict_reuse_eligible,
            "blockers": list(self.blockers),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ObserveOnlyReusePlan:
    entries: Tuple[ObserveOnlyReusePlanEntry, ...]

    @property
    def action_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(entry.action for entry in self.entries).items()))

    @property
    def strict_reuse_eligible_count(self) -> int:
        return sum(entry.strict_reuse_eligible for entry in self.entries)

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        ordered = tuple(sorted(self.entries, key=lambda entry: entry.instance_id))
        if limit is not None:
            ordered = ordered[: max(0, int(limit))]
        return {
            "entry_count": len(self.entries),
            "action_counts": self.action_counts,
            "strict_reuse_eligible_count": self.strict_reuse_eligible_count,
            "entries": [entry.as_dict() for entry in ordered],
        }


def build_dynamic_instance_graph(
    state_or_db_path: str | Path,
    *,
    decisions: Iterable[CurrentReuseDecisionSummaryV2] | None = None,
    static_graph: StaticDependencyGraph | None = None,
    edge_resolvers: Iterable[InstanceEdgeResolver] | None = None,
    unit_output_lineage_resolvers: Iterable[UnitOutputLineageResolver] | None = None,
) -> DynamicInstanceGraph:
    """Build a read-only concrete-instance graph from Reuse v2 observations.

    Without an explicit static graph, topology is reconstructed from the
    latest observed decision payloads. This keeps historical reports stable
    when the current TaskSpec registry later changes.

    When a persisted run config is available, ``args.*`` edges also expose
    current-snapshot diagnostics. They remain blocking because current
    fingerprints do not prove decision-time input equivalence.
    """

    state_db_path = _resolve_db_path(state_or_db_path)
    decision_rows = _require_current_decision_summaries_v2(
        tuple(decisions)
        if decisions is not None
        else load_current_reuse_decision_summaries_v2(
            state_or_db_path,
            limit=None,
            latest_first=False,
        )
    )
    completion_instance_ids = (
        _load_completion_instance_ids(
            state_db_path,
            decision_rows,
        )
    )
    external_input_config: Mapping[str, Any] | None = None
    external_input_config_error: str | None = None
    if state_db_path.exists():
        try:
            external_input_config = load_run_config_snapshot(state_db_path)
        except (KeyError, OSError, TypeError, ValueError, sqlite3.Error) as exc:
            external_input_config_error = f"{type(exc).__name__}: {exc}"
    else:
        external_input_config_error = f"state database does not exist: {state_db_path}"
    return build_dynamic_instance_graph_from_decisions(
        decision_rows,
        state_db_path=str(state_db_path),
        static_graph=static_graph,
        edge_resolvers=edge_resolvers,
        unit_output_lineage_resolvers=unit_output_lineage_resolvers,
        finalized_unit_input=load_finalized_unit_input_table_for_state(state_db_path),
        external_input_config=external_input_config,
        external_input_config_error=external_input_config_error,
        legacy_completion_instance_ids=completion_instance_ids,
    )


def build_dynamic_instance_graph_from_decisions(
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
    *,
    state_db_path: str = "",
    static_graph: StaticDependencyGraph | None = None,
    edge_resolvers: Iterable[InstanceEdgeResolver] | None = None,
    unit_output_lineage_resolvers: Iterable[UnitOutputLineageResolver] | None = None,
    finalized_unit_input: FinalizedUnitInputTableLoad | None = None,
    external_input_config: Mapping[str, Any] | None = None,
    external_input_config_error: str | None = None,
    legacy_completion_instance_ids: Iterable[str] = (),
) -> DynamicInstanceGraph:
    """Fold decision history and resolve only resolver-backed concrete edges."""

    decision_rows = _require_current_decision_summaries_v2(decisions)
    latest_by_instance_id: dict[str, CurrentReuseDecisionSummaryV2] = {}
    for decision in decision_rows:
        previous = latest_by_instance_id.get(decision.instance_id)
        if previous is None or decision.decision_id >= previous.decision_id:
            latest_by_instance_id[decision.instance_id] = decision

    reconciled_instance_ids = frozenset(legacy_completion_instance_ids)
    nodes = tuple(
        sorted(
            (
                _node_from_decision(
                    decision,
                    legacy_completion_reconciled=(
                        decision.instance_id in reconciled_instance_ids
                    ),
                )
                for decision in latest_by_instance_id.values()
            ),
            key=_node_sort_key,
        )
    )
    topology_source = "explicit_static_graph" if static_graph is not None else _observed_topology_source(nodes)
    graph = static_graph or _build_observed_static_graph(nodes)
    resolver_registry = _resolver_registry(edge_resolvers)
    lineage_registry = _unit_output_lineage_registry(unit_output_lineage_resolvers)
    edges = _resolve_dynamic_edges(
        nodes,
        graph,
        resolver_registry,
        lineage_registry,
        topology_source=topology_source,
        finalized_unit_input=finalized_unit_input,
        external_input_config=external_input_config,
        external_input_config_error=external_input_config_error,
    )
    return DynamicInstanceGraph(
        state_db_path=state_db_path,
        topology_source=topology_source,
        edge_resolver_count=len(resolver_registry) + len(lineage_registry),
        source_decision_count=len(decision_rows),
        nodes=nodes,
        edges=edges,
    )


def build_dynamic_instance_graph_from_v1_compatibility_decisions(
    decisions: Iterable[Any],
    *,
    state_db_path: str = "",
    static_graph: StaticDependencyGraph | None = None,
    edge_resolvers: Iterable[InstanceEdgeResolver] | None = None,
    unit_output_lineage_resolvers: Iterable[
        UnitOutputLineageResolver
    ] | None = None,
    finalized_unit_input: FinalizedUnitInputTableLoad | None = None,
    external_input_config: Mapping[str, Any] | None = None,
    external_input_config_error: str | None = None,
    legacy_completion_instance_ids: Iterable[str] = (),
) -> DynamicInstanceGraph:
    """Explicitly normalize released v1 summaries for graph diagnostics."""

    return build_dynamic_instance_graph_from_decisions(
        tuple(
            current_reuse_decision_summary_v2_from_v1_compatibility(decision)
            for decision in decisions
        ),
        state_db_path=state_db_path,
        static_graph=static_graph,
        edge_resolvers=edge_resolvers,
        unit_output_lineage_resolvers=unit_output_lineage_resolvers,
        finalized_unit_input=finalized_unit_input,
        external_input_config=external_input_config,
        external_input_config_error=external_input_config_error,
        legacy_completion_instance_ids=legacy_completion_instance_ids,
    )


def _require_current_decision_summaries_v2(
    decisions: Iterable[CurrentReuseDecisionSummaryV2],
) -> tuple[CurrentReuseDecisionSummaryV2, ...]:
    rows = tuple(decisions)
    if any(
        not isinstance(decision, CurrentReuseDecisionSummaryV2)
        for decision in rows
    ):
        raise TypeError(
            "Current graph construction requires "
            "CurrentReuseDecisionSummaryV2; use the explicitly named v1 "
            "compatibility entry for released summaries"
        )
    return rows


def build_observe_only_reuse_plan(
    graph: DynamicInstanceGraph,
    *,
    policy: ObserveOnlyPlanPolicy = DEFAULT_OBSERVE_ONLY_PLAN_POLICY,
) -> ObserveOnlyReusePlan:
    """Preview a strict reuse decision without changing the legacy scheduler."""

    incoming_by_instance: dict[str, list[DynamicInstanceEdge]] = defaultdict(list)
    for edge in graph.edges:
        incoming_by_instance[edge.consumer_instance_id].append(edge)

    entries_by_instance_id: dict[str, ObserveOnlyReusePlanEntry] = {}
    for node in graph.nodes:
        incoming = tuple(incoming_by_instance.get(node.instance_id, ()))
        entries_by_instance_id[node.instance_id] = _local_observe_only_plan_entry(
            node,
            incoming,
            policy=policy,
        )

    strict_eligible_instance_ids = {
        instance_id
        for instance_id, entry in entries_by_instance_id.items()
        if entry.strict_reuse_eligible
    }
    while True:
        upstream_blockers_by_instance_id = {
            instance_id: _upstream_blockers_for_edges(
                incoming_by_instance.get(instance_id, ()),
                strict_eligible_instance_ids,
            )
            for instance_id in sorted(strict_eligible_instance_ids)
        }
        invalidated = {
            instance_id: blockers
            for instance_id, blockers in upstream_blockers_by_instance_id.items()
            if blockers
        }
        if not invalidated:
            break
        for instance_id, upstream_blockers in invalidated.items():
            previous = entries_by_instance_id[instance_id]
            entries_by_instance_id[instance_id] = ObserveOnlyReusePlanEntry(
                instance_id=previous.instance_id,
                action="blocked",
                observed_action=previous.observed_action,
                strict_reuse_eligible=False,
                blockers=tuple(dict.fromkeys((*previous.blockers, *upstream_blockers))),
                reasons=(
                    "latest_observation_reused",
                    "strict_reuse_upstream_ineligible",
                    "legacy_scheduler_remains_authoritative",
                    *_policy_reasons(policy),
                ),
            )
        strict_eligible_instance_ids.difference_update(invalidated)

    return ObserveOnlyReusePlan(
        entries=tuple(entries_by_instance_id[node.instance_id] for node in graph.nodes),
    )


def _local_observe_only_plan_entry(
    node: DynamicInstanceNode,
    incoming: Iterable[DynamicInstanceEdge],
    *,
    policy: ObserveOnlyPlanPolicy,
) -> ObserveOnlyReusePlanEntry:
    active_run_observation = (
        node.queued
        or node.observed_action
        in {
            "would_run",
            "hybrid_recompute",
            "v2_recompute",
        }
    ) and not node.legacy_completion_reconciled
    if active_run_observation:
        reason = "latest_observation_queued" if node.queued else f"latest_observation_{node.observed_action}"
        return ObserveOnlyReusePlanEntry(
            instance_id=node.instance_id,
            action="would_run",
            observed_action=node.observed_action,
            strict_reuse_eligible=False,
            reasons=(reason, *_policy_reasons(policy)),
        )

    blockers = _blockers_for_edges(
        incoming,
        blocking_edge_kinds=policy.blocking_edge_kinds,
    )
    supported_completed_run = (
        node.observed_action == "would_run"
        and node.legacy_completion_reconciled
    )
    if node.observed_action not in {
        "legacy_reuse",
        "hybrid_reuse",
        "v2_reuse",
        "v2_recompute_completed",
        "v2_fallback_legacy",
    } and not supported_completed_run:
        blockers = tuple(dict.fromkeys((*blockers, f"unsupported_observed_action:{node.observed_action}")))
    if blockers:
        return ObserveOnlyReusePlanEntry(
            instance_id=node.instance_id,
            action="blocked",
            observed_action=node.observed_action,
            strict_reuse_eligible=False,
            blockers=blockers,
            reasons=(
                "strict_reuse_evidence_incomplete",
                "legacy_scheduler_remains_authoritative",
                *_policy_reasons(policy),
            ),
        )

    return ObserveOnlyReusePlanEntry(
        instance_id=node.instance_id,
        action="would_reuse",
        observed_action=node.observed_action,
        strict_reuse_eligible=True,
        reasons=(
            "latest_observation_reused",
            "all_declared_inputs_have_concrete_evidence",
            *_policy_reasons(policy),
        ),
    )


def _upstream_blockers_for_edges(
    edges: Iterable[DynamicInstanceEdge],
    strict_eligible_instance_ids: set[str],
) -> Tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            f"upstream_not_strict_reuse:{edge.artifact_key}:{edge.producer_instance_id}"
            for edge in edges
            if edge.is_concrete
            and edge.kind != "inline_observed"
            and edge.producer_instance_id not in strict_eligible_instance_ids
        )
    )


def format_dynamic_instance_graph(graph: DynamicInstanceGraph, *, limit: int = 20) -> str:
    report = graph.report()
    lines = [
        f"Dynamic instance graph: {report.state_db_path or '-'}",
        f"topology source: {report.topology_source}",
        f"explicit edge resolvers: {report.edge_resolver_count}",
        f"source decision rows: {report.source_decision_count}",
        f"latest nodes: {report.node_count} across {report.task_count} task types",
        f"edges: {report.edge_count} ({report.concrete_edge_count} concrete)",
        f"blocking evidence gaps: {report.blocking_edge_count}",
    ]
    if report.edge_kind_counts:
        lines.append("edge kinds:")
        for kind, count in report.edge_kind_counts.items():
            lines.append(f"  {kind}: {count}")
    if report.declaration_source_counts:
        lines.append("declaration sources:")
        for source, count in report.declaration_source_counts.items():
            lines.append(f"  {source}: {count}")
    edges = _latest_edges(graph.edges, graph.nodes_by_instance_id, limit=limit)
    if edges:
        lines.append("latest edges:")
        for edge in edges:
            consumer = graph.nodes_by_instance_id[edge.consumer_instance_id]
            producer = edge.producer_instance_id or "-"
            lines.append(
                f"  {edge.kind} {consumer.stage}:{consumer.task_base} "
                f"<- {producer[:12] if producer != '-' else producer} artifact={edge.artifact_key}"
            )
            if edge.detail:
                lines.append(f"    detail={edge.detail}")
    return "\n".join(lines)


def format_observe_only_reuse_plan(plan: ObserveOnlyReusePlan, *, limit: int = 20) -> str:
    lines = [
        f"Observe-only reuse plan: {len(plan.entries)} instances",
        f"strict reuse eligible: {plan.strict_reuse_eligible_count}",
    ]
    if plan.action_counts:
        lines.append("actions:")
        for action, count in plan.action_counts.items():
            lines.append(f"  {action}: {count}")
    entries = tuple(sorted(plan.entries, key=lambda entry: entry.instance_id))[: max(0, int(limit))]
    if entries:
        lines.append("instances:")
        for entry in entries:
            lines.append(
                f"  {entry.instance_id[:12]} {entry.action} observed={entry.observed_action} "
                f"eligible={int(entry.strict_reuse_eligible)}"
            )
            if entry.blockers:
                lines.append(f"    blockers={','.join(entry.blockers)}")
    return "\n".join(lines)


def _observed_topology_source(nodes: Sequence[DynamicInstanceNode]) -> str:
    if not nodes:
        return "no_decision_declarations"
    sources = tuple(node.declaration_source or "unknown" for node in nodes)
    if all(source == "instance_payload" for source in sources):
        return "latest_decision_payloads"
    if any("current_task_specs" in source for source in sources):
        return "latest_decisions_current_task_specs_fallback"
    if any("stored_task_spec_registry" in source for source in sources):
        return "latest_decisions_stored_registry_fallback"
    return "latest_decisions_mixed_declarations"


def _build_observed_static_graph(nodes: Sequence[DynamicInstanceNode]) -> StaticDependencyGraph:
    declarations: dict[TaskKey, dict[str, set[str]]] = {}
    for node in nodes:
        declaration = declarations.setdefault(node.key, {"consumes": set(), "produces": set()})
        declaration["consumes"].update(node.consumes)
        declaration["produces"].update(node.produces)
    tasks = tuple(
        PipelineTaskContract(
            stage=task_key[0],
            task_base=task_key[1],
            level="unit",
            execution_mode="flex",
            consumes=tuple(sorted(declaration["consumes"])),
            produces=tuple(sorted(declaration["produces"])),
        )
        for task_key, declaration in sorted(declarations.items())
    )
    return build_static_dependency_graph(PipelineContract(tasks=tasks, artifact_edges=()))


def _resolver_registry(
    edge_resolvers: Iterable[InstanceEdgeResolver] | None,
) -> dict[tuple[TaskKey, TaskKey, str], InstanceEdgeResolver]:
    selected = DEFAULT_INSTANCE_EDGE_RESOLVERS if edge_resolvers is None else tuple(edge_resolvers)
    registry: dict[tuple[TaskKey, TaskKey, str], InstanceEdgeResolver] = {}
    for resolver in selected:
        if resolver.key in registry:
            raise ValueError(f"Duplicate InstanceEdgeResolver: {resolver.label}")
        registry[resolver.key] = resolver
    return registry


def _unit_output_lineage_registry(
    resolvers: Iterable[UnitOutputLineageResolver] | None,
) -> dict[tuple[TaskKey, str], UnitOutputLineageResolver]:
    selected = (
        DEFAULT_UNIT_OUTPUT_LINEAGE_RESOLVERS
        if resolvers is None
        else tuple(resolvers)
    )
    registry: dict[tuple[TaskKey, str], UnitOutputLineageResolver] = {}
    for resolver in selected:
        if resolver.key in registry:
            raise ValueError(f"Duplicate UnitOutputLineageResolver: {resolver.label}")
        registry[resolver.key] = resolver
    return registry


def _resolve_dynamic_edges(
    nodes: Sequence[DynamicInstanceNode],
    static_graph: StaticDependencyGraph,
    resolver_registry: Mapping[tuple[TaskKey, TaskKey, str], InstanceEdgeResolver],
    lineage_registry: Mapping[tuple[TaskKey, str], UnitOutputLineageResolver],
    *,
    topology_source: str,
    finalized_unit_input: FinalizedUnitInputTableLoad | None,
    external_input_config: Mapping[str, Any] | None,
    external_input_config_error: str | None,
) -> Tuple[DynamicInstanceEdge, ...]:
    nodes_by_task: dict[TaskKey, tuple[DynamicInstanceNode, ...]] = defaultdict(tuple)
    grouped: dict[TaskKey, list[DynamicInstanceNode]] = defaultdict(list)
    for node in nodes:
        grouped[node.key].append(node)
    for task_key, task_nodes in grouped.items():
        nodes_by_task[task_key] = tuple(sorted(task_nodes, key=_node_sort_key))

    edges: list[DynamicInstanceEdge] = []
    for consumer in nodes:
        for artifact_key in consumer.consumes:
            lineage_resolver = lineage_registry.get((consumer.key, artifact_key))
            if lineage_resolver is not None:
                edges.extend(
                    _resolve_unit_output_lineage(
                        consumer=consumer,
                        resolver=lineage_resolver,
                        nodes_by_task=nodes_by_task,
                        finalized_unit_input=finalized_unit_input,
                    )
                )
                continue
            producer_keys = static_graph.artifact_producers.get(artifact_key, ())
            if not producer_keys:
                external = artifact_key.startswith("args.")
                if external:
                    kind, detail = _current_external_input_kind_and_detail(
                        consumer=consumer,
                        artifact_key=artifact_key,
                        config=external_input_config,
                        config_error=external_input_config_error,
                    )
                else:
                    kind = "producer_unobserved"
                    detail = "no observed producer declaration for this non-external input"
                edges.append(
                    DynamicInstanceEdge(
                        artifact_key=artifact_key,
                        consumer_instance_id=consumer.instance_id,
                        consumer_key=consumer.key,
                        kind=kind,
                        detail=detail,
                    )
                )
                continue
            if len(producer_keys) > 1:
                kind: DynamicInstanceEdgeKind = (
                    "static_ambiguous" if topology_source == "explicit_static_graph" else "ambiguous"
                )
                edges.append(
                    DynamicInstanceEdge(
                        artifact_key=artifact_key,
                        consumer_instance_id=consumer.instance_id,
                        consumer_key=consumer.key,
                        kind=kind,
                        producer_task_keys=tuple(producer_keys),
                        detail="multiple task types declare this symbolic artifact",
                    )
                )
                continue

            producer_key = producer_keys[0]
            if producer_key == consumer.key:
                edges.append(
                    DynamicInstanceEdge(
                        artifact_key=artifact_key,
                        consumer_instance_id=consumer.instance_id,
                        consumer_key=consumer.key,
                        kind="static_self",
                        producer_task_keys=(producer_key,),
                        detail="producer and consumer are the same declared task type",
                    )
                )
                continue

            producer_nodes = tuple(
                node
                for node in nodes_by_task.get(producer_key, ())
                if artifact_key in node.produces
            )
            resolver = resolver_registry.get((producer_key, consumer.key, artifact_key))
            edges.extend(
                _resolve_declared_input(
                    artifact_key=artifact_key,
                    consumer=consumer,
                    producer_key=producer_key,
                    producer_nodes=producer_nodes,
                    resolver=resolver,
                    finalized_unit_input=finalized_unit_input,
                )
            )
    return tuple(sorted(edges, key=_edge_sort_key))


def _resolve_unit_output_lineage(
    *,
    consumer: DynamicInstanceNode,
    resolver: UnitOutputLineageResolver,
    nodes_by_task: Mapping[TaskKey, Sequence[DynamicInstanceNode]],
    finalized_unit_input: FinalizedUnitInputTableLoad | None,
) -> Tuple[DynamicInstanceEdge, ...]:
    if resolver.mode == "dedup_source":
        return (
            _resolve_preprocess_source(
                consumer=consumer,
                nodes_by_task=nodes_by_task,
                source_mode="dedup",
            ),
        )
    if resolver.mode == "rewrite_source":
        return (
            _resolve_preprocess_source(
                consumer=consumer,
                nodes_by_task=nodes_by_task,
                source_mode="rewrite",
            ),
        )
    if resolver.mode == "strand_source":
        return (
            _resolve_strand_source(
                consumer=consumer,
                nodes_by_task=nodes_by_task,
                finalized_unit_input=finalized_unit_input,
            ),
        )
    if resolver.mode == "merge_sources":
        return _resolve_merge_sources(
            consumer=consumer,
            nodes_by_task=nodes_by_task,
            finalized_unit_input=finalized_unit_input,
        )
    raise ValueError(f"Unsupported unit-output lineage mode: {resolver.mode!r}")


def _resolve_preprocess_source(
    *,
    consumer: DynamicInstanceNode,
    nodes_by_task: Mapping[TaskKey, Sequence[DynamicInstanceNode]],
    source_mode: Literal["dedup", "rewrite"],
) -> DynamicInstanceEdge:
    fields = consumer.fields_dict
    sid = _nonblank_string(fields.get("sid"))
    ctg = _nonblank_string(fields.get("ctg"))
    info = _worker_extra_info(consumer)
    if sid is None or ctg is None or info is None:
        return _lineage_gap_edge(
            consumer,
            kind="unmatched",
            detail="unit preprocessing consumer lacks sid/ctg or worker extra_info",
        )
    src_type = _nonblank_string(info.get("src_type"))
    if src_type in {"raw", "raw_region"}:
        if src_type == "raw_region":
            region_contig = _nonblank_string(info.get("region_contig"))
            if region_contig != ctg:
                return _lineage_gap_edge(
                    consumer,
                    kind="unmatched",
                    detail=(
                        f"{source_mode} raw-region payload contig={region_contig!r} "
                        f"does not match consumer ctg={ctg!r}"
                    ),
                )
            detail = (
                f"{source_mode} worker payload selects contig {region_contig!r} "
                "from an indexed operator-managed raw BAM"
            )
        else:
            detail = f"{source_mode} worker payload selects an operator-managed raw BAM"
        return _operator_managed_unit_path_edge(
            consumer,
            info.get("raw_src"),
            detail=detail,
        )
    if src_type == "derived":
        producer_keys = (_SPLIT_UNIT_KEY, _FILTER_UNIT_KEY)
        matches = _matching_unit_nodes(
            nodes_by_task,
            producer_keys,
            sid=sid,
            ctg=ctg,
        )
        return _unique_lineage_edge(
            consumer,
            matches,
            producer_keys=producer_keys,
            matched_field_keys=("sid", "ctg", "worker_payload.src_type"),
            detail=f"{source_mode} worker payload selects a derived split/filter BAM",
        )
    if source_mode == "rewrite" and src_type == "dedup":
        dedup_status = _nonblank_string(fields.get("dedup_status"))
        matches = _matching_unit_nodes(
            nodes_by_task,
            (_DEDUP_UNIT_KEY,),
            sid=sid,
            ctg=ctg,
            dedup_method=dedup_status,
        )
        return _unique_lineage_edge(
            consumer,
            matches,
            producer_keys=(_DEDUP_UNIT_KEY,),
            matched_field_keys=("sid", "ctg", "dedup_status"),
            detail="barcode rewrite worker payload selects the exact dedup producer",
        )
    return _lineage_gap_edge(
        consumer,
        kind="unmatched",
        detail=f"unsupported {source_mode} worker src_type: {src_type!r}",
    )


def _resolve_strand_source(
    *,
    consumer: DynamicInstanceNode,
    nodes_by_task: Mapping[TaskKey, Sequence[DynamicInstanceNode]],
    finalized_unit_input: FinalizedUnitInputTableLoad | None,
) -> DynamicInstanceEdge:
    fields = consumer.fields_dict
    uid = _nonblank_string(fields.get("uid"))
    dedup_method = _nonblank_string(fields.get("dedup_method"))
    poly_a_status = fields.get("polyA_status")
    if uid is None or dedup_method is None:
        return _lineage_gap_edge(
            consumer,
            kind="unmatched",
            detail="strand consumer lacks uid or dedup_method",
        )
    if poly_a_status not in (None, ""):
        matches = _matching_unit_nodes(
            nodes_by_task,
            (_EXTRACT_POLYA_KEY,),
            uid=uid,
            dedup_method=dedup_method,
            poly_a_status=str(poly_a_status),
        )
        return _unique_lineage_edge(
            consumer,
            matches,
            producer_keys=(_EXTRACT_POLYA_KEY,),
            matched_field_keys=("uid", "dedup_method", "polyA_status"),
            detail="PolyA strand split selects the exact extraction producer",
        )

    table, error = _ready_finalized_unit_table(finalized_unit_input)
    if table is None:
        return _lineage_gap_edge(consumer, kind="unmatched", detail=error)
    row, error = _finalized_row_by_uid(table, uid)
    if row is None:
        return _lineage_gap_edge(consumer, kind="unmatched", detail=error)
    if row.dedup_status != dedup_method:
        return _lineage_gap_edge(
            consumer,
            kind="unmatched",
            detail=(
                f"raw strand consumer dedup_method={dedup_method!r} does not match "
                f"finalized unit dedup_status={row.dedup_status!r}"
            ),
        )
    info = _worker_extra_info(consumer)
    source_path = None if info is None else info.get("src")
    if not _same_path(source_path, row.resolved_path):
        return _lineage_gap_edge(
            consumer,
            kind="unmatched",
            detail="raw strand worker source does not match the finalized unit path",
        )
    return _resolve_final_raw_unit_source(
        consumer=consumer,
        row=row,
        table=table,
        nodes_by_task=nodes_by_task,
    )


def _resolve_merge_sources(
    *,
    consumer: DynamicInstanceNode,
    nodes_by_task: Mapping[TaskKey, Sequence[DynamicInstanceNode]],
    finalized_unit_input: FinalizedUnitInputTableLoad | None,
) -> Tuple[DynamicInstanceEdge, ...]:
    table, error = _ready_finalized_unit_table(finalized_unit_input)
    if table is None:
        return (_lineage_gap_edge(consumer, kind="unmatched", detail=error),)
    fields = consumer.fields_dict
    ctg = _nonblank_string(fields.get("ctg"))
    poly_a_status = fields.get("polyA_status")
    strand = fields.get("strand")
    if ctg is None or strand not in (None, "", "plus", "minus"):
        return (
            _lineage_gap_edge(
                consumer,
                kind="unmatched",
                detail="merge consumer lacks ctg or has an unsupported strand",
            ),
        )
    rows = tuple(row for row in table.rows if row.contig_name == ctg)
    if not rows or len({row.unit_id for row in rows}) != len(rows):
        return (
            _lineage_gap_edge(
                consumer,
                kind="unmatched",
                detail=f"finalized unit rows for contig {ctg!r} are missing or duplicated",
            ),
        )
    info = _worker_extra_info(consumer)
    input_files = None if info is None else info.get("input_files")
    if not isinstance(input_files, (tuple, list)) or not input_files:
        return (
            _lineage_gap_edge(
                consumer,
                kind="unmatched",
                detail="merge worker payload lacks a non-empty input_files manifest",
            ),
        )
    actual_paths = tuple(_canonical_path(value) for value in input_files)
    if any(path is None for path in actual_paths) or len(set(actual_paths)) != len(actual_paths):
        return (
            _lineage_gap_edge(
                consumer,
                kind="unmatched",
                detail="merge worker input_files contain blank or duplicate paths",
            ),
        )
    expected_paths = tuple(
        _canonical_path(
            _expected_unit_branch_path(
                table,
                row,
                poly_a_status=poly_a_status,
                strand=strand,
            )
        )
        for row in rows
    )
    if any(path is None for path in expected_paths) or actual_paths != expected_paths:
        return (
            _lineage_gap_edge(
                consumer,
                kind="unmatched",
                detail="merge worker input_files do not exactly match finalized unit branch paths",
            ),
        )

    source_edges: list[DynamicInstanceEdge] = []
    for row in rows:
        if poly_a_status in (None, "") and strand in (None, ""):
            edge = _resolve_final_raw_unit_source(
                consumer=consumer,
                row=row,
                table=table,
                nodes_by_task=nodes_by_task,
            )
        else:
            edge = _resolve_unit_branch_producer(
                consumer=consumer,
                row=row,
                nodes_by_task=nodes_by_task,
                poly_a_status=poly_a_status,
                strand=strand,
            )
        if edge.kind not in {
            "resolved",
            "external_current_ready",
            "external_current_missing",
            "external_current_empty",
            "external_current_unresolved",
        }:
            return (edge,)
        if edge.producer_instance_id is not None:
            edge = DynamicInstanceEdge(
                artifact_key=edge.artifact_key,
                consumer_instance_id=edge.consumer_instance_id,
                consumer_key=edge.consumer_key,
                kind="fan_in_complete",
                producer_instance_id=edge.producer_instance_id,
                producer_task_keys=edge.producer_task_keys,
                candidate_instance_ids=edge.candidate_instance_ids,
                matched_field_keys=edge.matched_field_keys,
                detail=(
                    "exact merge input manifest covers every finalized unit; "
                    + edge.detail
                ),
            )
        source_edges.append(edge)
    producer_ids = [
        edge.producer_instance_id
        for edge in source_edges
        if edge.producer_instance_id is not None
    ]
    if len(producer_ids) != len(set(producer_ids)):
        return (
            _lineage_gap_edge(
                consumer,
                kind="ambiguous",
                detail="merge lineage selected a duplicate producer instance",
                candidate_instance_ids=tuple(producer_ids),
            ),
        )
    return tuple(source_edges)


def _resolve_unit_branch_producer(
    *,
    consumer: DynamicInstanceNode,
    row: FinalizedUnitInputRow,
    nodes_by_task: Mapping[TaskKey, Sequence[DynamicInstanceNode]],
    poly_a_status: Any,
    strand: Any,
) -> DynamicInstanceEdge:
    if poly_a_status in (None, ""):
        producer_keys = (_SPLIT_STRAND_KEY,)
        matches = _matching_unit_nodes(
            nodes_by_task,
            producer_keys,
            uid=row.unit_id,
            dedup_method=row.dedup_status,
            poly_a_status=None,
        )
        detail = "raw stranded merge input selects the exact strand producer"
    elif strand in (None, ""):
        producer_keys = (_EXTRACT_POLYA_KEY,)
        matches = _matching_unit_nodes(
            nodes_by_task,
            producer_keys,
            uid=row.unit_id,
            dedup_method=row.dedup_status,
            poly_a_status=str(poly_a_status),
        )
        detail = "unstranded PolyA merge input selects the exact extraction producer"
    else:
        producer_keys = (_SPLIT_STRAND_KEY,)
        matches = _matching_unit_nodes(
            nodes_by_task,
            producer_keys,
            uid=row.unit_id,
            dedup_method=row.dedup_status,
            poly_a_status=str(poly_a_status),
        )
        detail = "stranded PolyA merge input selects the exact strand producer"
    return _unique_lineage_edge(
        consumer,
        matches,
        producer_keys=producer_keys,
        matched_field_keys=("unit_id", "dedup_status", "polyA_status", "strand"),
        detail=detail,
    )


def _resolve_final_raw_unit_source(
    *,
    consumer: DynamicInstanceNode,
    row: FinalizedUnitInputRow,
    table: FinalizedUnitInputTable,
    nodes_by_task: Mapping[TaskKey, Sequence[DynamicInstanceNode]],
) -> DynamicInstanceEdge:
    resolved_path = _canonical_path(row.resolved_path)
    if resolved_path is None:
        return _lineage_gap_edge(
            consumer,
            kind="unmatched",
            detail=f"finalized unit {row.unit_id!r} lacks resolved_path",
        )
    unit_output_root = table.path.parent.parent / "unit_outputs"
    if not _path_is_within(resolved_path, unit_output_root):
        return _operator_managed_unit_path_edge(
            consumer,
            resolved_path,
            detail="finalized unit selects an operator-managed raw BAM",
        )

    expected_unit_dir = unit_output_root / row.unit_id
    filename = Path(resolved_path).name
    if ".cb-sfx-" in filename:
        if not _same_path(resolved_path, expected_unit_dir / filename):
            return _lineage_gap_edge(
                consumer,
                kind="producer_unobserved",
                detail=(
                    "finalized barcode rewrite path is outside its canonical unit directory: "
                    f"{resolved_path}"
                ),
                producer_task_keys=(_REWRITE_UNIT_KEY,),
            )
        producer_keys = (_REWRITE_UNIT_KEY,)
        matches = tuple(
            node
            for node in _matching_unit_nodes(
                nodes_by_task,
                producer_keys,
                sid=row.sample_id,
                ctg=row.contig_name,
                dedup_status=row.dedup_status,
            )
            if _rewrite_node_matches_path(node, row, resolved_path)
        )
        return _unique_lineage_edge(
            consumer,
            matches,
            producer_keys=producer_keys,
            matched_field_keys=("sample_id", "contig_name", "dedup_status", "barcode_suffix"),
            detail="finalized unit path selects the exact barcode rewrite producer",
        )

    expected_base_path = expected_unit_dir / f"{row.unit_id}.{row.dedup_status}.bam"
    if not _same_path(resolved_path, expected_base_path):
        return _lineage_gap_edge(
            consumer,
            kind="producer_unobserved",
            detail=(
                "finalized pipeline-owned unit path does not match a canonical base output: "
                f"{resolved_path}"
            ),
            producer_task_keys=(
                _DEDUP_UNIT_KEY,
                _SPLIT_UNIT_KEY,
                _FILTER_UNIT_KEY,
            ),
        )

    dedup_matches = _matching_unit_nodes(
        nodes_by_task,
        (_DEDUP_UNIT_KEY,),
        sid=row.sample_id,
        ctg=row.contig_name,
        dedup_method=row.dedup_status,
    )
    if dedup_matches:
        return _unique_lineage_edge(
            consumer,
            dedup_matches,
            producer_keys=(_DEDUP_UNIT_KEY,),
            matched_field_keys=("sample_id", "contig_name", "dedup_status"),
            detail="finalized unit path selects the exact dedup producer",
        )

    base_producer_keys = (_SPLIT_UNIT_KEY, _FILTER_UNIT_KEY)
    base_matches = _matching_unit_nodes(
        nodes_by_task,
        base_producer_keys,
        sid=row.sample_id,
        ctg=row.contig_name,
    )
    if base_matches:
        return _unique_lineage_edge(
            consumer,
            base_matches,
            producer_keys=base_producer_keys,
            matched_field_keys=("sample_id", "contig_name"),
            detail="finalized unit path selects one split/filter producer",
        )

    return _lineage_gap_edge(
        consumer,
        kind="producer_unobserved",
        detail=f"finalized unit path is pipeline-owned but its producer is unobserved: {resolved_path}",
        producer_task_keys=(
            _DEDUP_UNIT_KEY,
            _SPLIT_UNIT_KEY,
            _FILTER_UNIT_KEY,
        ),
    )


def _matching_unit_nodes(
    nodes_by_task: Mapping[TaskKey, Sequence[DynamicInstanceNode]],
    producer_keys: Sequence[TaskKey],
    *,
    sid: str | None = None,
    ctg: str | None = None,
    uid: str | None = None,
    dedup_method: str | None = None,
    dedup_status: str | None = None,
    poly_a_status: str | None = None,
) -> Tuple[DynamicInstanceNode, ...]:
    matches: list[DynamicInstanceNode] = []
    for producer_key in producer_keys:
        for node in nodes_by_task.get(producer_key, ()):
            fields = node.fields_dict
            if sid is not None and fields.get("sid") != sid:
                continue
            if ctg is not None and fields.get("ctg") != ctg:
                continue
            if uid is not None and fields.get("uid") != uid:
                continue
            if dedup_method is not None and fields.get("dedup_method") != dedup_method:
                continue
            if dedup_status is not None and fields.get("dedup_status") != dedup_status:
                continue
            if poly_a_status is not None and fields.get("polyA_status") != poly_a_status:
                continue
            if poly_a_status is None and uid is not None and fields.get("polyA_status") not in (None, ""):
                continue
            matches.append(node)
    return tuple(sorted(matches, key=_node_sort_key))


def _unique_lineage_edge(
    consumer: DynamicInstanceNode,
    matches: Sequence[DynamicInstanceNode],
    *,
    producer_keys: Sequence[TaskKey],
    matched_field_keys: Tuple[str, ...],
    detail: str,
) -> DynamicInstanceEdge:
    if len(matches) == 1:
        producer = matches[0]
        return DynamicInstanceEdge(
            artifact_key="unit_outputs/*.bam",
            consumer_instance_id=consumer.instance_id,
            consumer_key=consumer.key,
            kind="resolved",
            producer_instance_id=producer.instance_id,
            producer_task_keys=(producer.key,),
            candidate_instance_ids=(producer.instance_id,),
            matched_field_keys=matched_field_keys,
            detail=detail,
        )
    kind: DynamicInstanceEdgeKind = "producer_unobserved" if not matches else "ambiguous"
    return _lineage_gap_edge(
        consumer,
        kind=kind,
        detail=(
            f"{detail}; expected exactly one producer, observed {len(matches)}"
        ),
        producer_task_keys=tuple(producer_keys),
        candidate_instance_ids=tuple(node.instance_id for node in matches),
    )


def _lineage_gap_edge(
    consumer: DynamicInstanceNode,
    *,
    kind: DynamicInstanceEdgeKind,
    detail: str,
    producer_task_keys: Tuple[TaskKey, ...] = (),
    candidate_instance_ids: Tuple[str, ...] = (),
) -> DynamicInstanceEdge:
    return DynamicInstanceEdge(
        artifact_key="unit_outputs/*.bam",
        consumer_instance_id=consumer.instance_id,
        consumer_key=consumer.key,
        kind=kind,
        producer_task_keys=producer_task_keys,
        candidate_instance_ids=candidate_instance_ids,
        detail=detail,
    )


def _operator_managed_unit_path_edge(
    consumer: DynamicInstanceNode,
    raw_path: Any,
    *,
    detail: str,
) -> DynamicInstanceEdge:
    path = _canonical_path(raw_path)
    if path is None:
        kind: DynamicInstanceEdgeKind = "external_current_unresolved"
        status_detail = "path_status=unresolved; path is blank"
    else:
        candidate = Path(path)
        try:
            if not candidate.exists():
                kind = "external_current_missing"
                status_detail = "path_status=missing"
            elif not candidate.is_file():
                kind = "external_current_unresolved"
                status_detail = "path_status=not_regular_file"
            else:
                metadata = candidate.stat()
                kind = (
                    "external_current_empty"
                    if metadata.st_size == 0
                    else "external_current_ready"
                )
                status_detail = (
                    f"path_status={'empty' if metadata.st_size == 0 else 'ready'}; "
                    f"size={metadata.st_size}; mtime_ns={metadata.st_mtime_ns}; "
                    "fingerprint_policy=metadata-v1"
                )
        except OSError as exc:
            kind = "external_current_unresolved"
            status_detail = f"path_status=unresolved; path metadata failed: {exc}"
    return DynamicInstanceEdge(
        artifact_key="unit_outputs/*.bam",
        consumer_instance_id=consumer.instance_id,
        consumer_key=consumer.key,
        kind=kind,
        detail=f"{detail}; {status_detail}; path={path or '-'}",
    )


def _ready_finalized_unit_table(
    load: FinalizedUnitInputTableLoad | None,
) -> tuple[FinalizedUnitInputTable | None, str]:
    if load is None:
        return None, "finalized unit input evidence was not supplied"
    if load.table is None:
        return None, load.detail or f"finalized unit input evidence is unavailable: {load.path}"
    return load.table, ""


def _finalized_row_by_uid(
    table: FinalizedUnitInputTable,
    uid: str,
) -> tuple[FinalizedUnitInputRow | None, str]:
    matches = [row for row in table.rows if row.unit_id == uid]
    if len(matches) == 1:
        return matches[0], ""
    return None, f"finalized unit table has {len(matches)} rows for unit_id={uid!r}"


def _expected_unit_branch_path(
    table: FinalizedUnitInputTable,
    row: FinalizedUnitInputRow,
    *,
    poly_a_status: Any,
    strand: Any,
) -> str | None:
    if poly_a_status in (None, "") and strand in (None, ""):
        return row.resolved_path
    labels = [row.unit_id, row.dedup_status]
    if poly_a_status not in (None, ""):
        labels.append(str(poly_a_status))
    if strand not in (None, ""):
        labels.append(str(strand))
    filename = ".".join(labels) + ".bam"
    return str(table.path.parent.parent / "unit_outputs" / row.unit_id / filename)


def _rewrite_node_matches_path(
    node: DynamicInstanceNode,
    row: FinalizedUnitInputRow,
    resolved_path: str,
) -> bool:
    info = _worker_extra_info(node)
    if info is None:
        return False
    barcode_suffix = _nonblank_string(info.get("barcode_suffix"))
    if barcode_suffix is None:
        return False
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", barcode_suffix).strip("_") or "suffix"
    expected = f"{row.unit_id}.{row.dedup_status}.cb-sfx-{label}.bam"
    return Path(resolved_path).name == expected


def _worker_extra_info(node: DynamicInstanceNode) -> Mapping[str, Any] | None:
    value = node.worker_payload.get("extra_info")
    return value if isinstance(value, Mapping) else None


def _nonblank_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _canonical_path(value: Any) -> str | None:
    text = _nonblank_string(value)
    if text is None:
        return None
    try:
        return str(Path(text).expanduser().resolve())
    except OSError:
        return str(Path(text).expanduser().absolute())


def _same_path(left: Any, right: Any) -> bool:
    left_path = _canonical_path(left)
    right_path = _canonical_path(right)
    return left_path is not None and left_path == right_path


def _path_is_within(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _current_external_input_kind_and_detail(
    *,
    consumer: DynamicInstanceNode,
    artifact_key: str,
    config: Mapping[str, Any] | None,
    config_error: str | None,
) -> tuple[DynamicInstanceEdgeKind, str]:
    if config is None and config_error is None:
        return "external", "no declared producer; external input fingerprinting is separate evidence"

    records = input_artifact_records_from_declared_inputs(
        stage=consumer.stage,
        task_base=consumer.task_base,
        consumes=(artifact_key,),
        config=config,
    ).records
    if len(records) != 1:
        return (
            "external_current_unresolved",
            "current external snapshot evidence is malformed; decision-time equivalence is unproven",
        )
    record = records[0]
    fingerprint_mode = record.fingerprint.mode if record.fingerprint is not None else "none"
    detail = (
        "current external snapshot evidence only; "
        f"expectation={record.expectation_status}; path_status={record.path_status}; "
        f"fingerprint_mode={fingerprint_mode}; decision-time equivalence is unproven"
    )
    if config_error:
        detail = f"{detail}; snapshot_error={config_error}"
    if record.fingerprint is None:
        return "external_current_unresolved", detail
    if not record.required:
        if record.path_status == "ready" and record.expectation_status == "config_path_not_applicable":
            return "external_current_not_applicable", detail
        return "external_current_unresolved", detail
    if record.path_status == "ready":
        return "external_current_ready", detail
    if record.path_status == "missing":
        return "external_current_missing", detail
    if record.path_status == "empty":
        return "external_current_empty", detail
    return "external_current_unresolved", detail


def _resolve_declared_input(
    *,
    artifact_key: str,
    consumer: DynamicInstanceNode,
    producer_key: TaskKey,
    producer_nodes: Sequence[DynamicInstanceNode],
    resolver: InstanceEdgeResolver | None,
    finalized_unit_input: FinalizedUnitInputTableLoad | None,
) -> Tuple[DynamicInstanceEdge, ...]:
    if not producer_nodes:
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind="producer_unobserved",
                producer_task_keys=(producer_key,),
                detail="declared producer has no latest Reuse v2 decision",
            ),
        )

    if resolver is not None:
        return _resolve_resolver_backed_input(
            artifact_key=artifact_key,
            consumer=consumer,
            producer_key=producer_key,
            producer_nodes=producer_nodes,
            resolver=resolver,
            finalized_unit_input=finalized_unit_input,
        )
    return _resolve_heuristic_input(
        artifact_key=artifact_key,
        consumer=consumer,
        producer_key=producer_key,
        producer_nodes=producer_nodes,
    )


def _derived_producer_extra_key(
    consumer: DynamicInstanceNode,
    resolver: InstanceEdgeResolver,
) -> tuple[str | None, str | None]:
    if resolver.derived_extra_key_mode == "none":
        return None, None
    if resolver.derived_extra_key_mode == "summary_psi_suffix":
        consumer_extra_key = consumer.fields_dict.get("extra_key")
        if consumer_extra_key is None:
            return None, "consumer lacks summary extra_key"
        text = _legacy_field_value(consumer_extra_key)
        peak_extra_key, separator, psi_min_gene_umis = text.rpartition(".psi-min-gene-umis-")
        if not separator or not peak_extra_key or not psi_min_gene_umis:
            return None, "consumer extra_key is not a valid summary PSI suffix value"
        quantification_marker = (
            ".quant-terminal-aware.intronic-misprimed-rt"
        )
        if quantification_marker in psi_min_gene_umis:
            (
                psi_min_gene_umis,
                marker,
                intronic_threshold,
            ) = psi_min_gene_umis.partition(quantification_marker)
            if not marker or not psi_min_gene_umis or not intronic_threshold:
                return None, "consumer extra_key has malformed terminal-aware quantification fields"
            try:
                parsed_intronic_threshold = float(intronic_threshold)
            except ValueError:
                return None, "consumer extra_key intronic threshold is not numeric"
            if (
                str(parsed_intronic_threshold) != intronic_threshold
                or not 0.0 <= parsed_intronic_threshold <= 1.0
            ):
                return None, "consumer extra_key intronic threshold is not canonical"
        try:
            parsed_psi_min_gene_umis = int(psi_min_gene_umis)
        except ValueError:
            return None, "consumer extra_key PSI suffix is not an integer"
        if str(parsed_psi_min_gene_umis) != psi_min_gene_umis:
            return None, "consumer extra_key PSI suffix is not a canonical integer"
        return peak_extra_key, None
    if resolver.derived_extra_key_mode == "consumer_extra_key_exact":
        consumer_extra_key = consumer.fields_dict.get("extra_key")
        if consumer_extra_key is None:
            return None, "consumer lacks exact extra_key"
        text = _legacy_field_value(consumer_extra_key)
        if not text.strip():
            return None, "consumer exact extra_key is blank"
        return text, None
    return None, f"unsupported derived extra-key mode: {resolver.derived_extra_key_mode}"


def _consumer_artifact_selection_failure(
    consumer: DynamicInstanceNode,
    resolver: InstanceEdgeResolver,
) -> tuple[DynamicInstanceEdgeKind | None, str | None]:
    if resolver.consumer_artifact_selection_mode == "none":
        return None, None
    if resolver.consumer_artifact_selection_mode != "annotate_peak_source_resolved":
        return "ambiguous", (
            "unsupported consumer artifact selection mode: "
            f"{resolver.consumer_artifact_selection_mode}"
        )

    annotate_peak_source = consumer.fields_dict.get("annotate_peak_source")
    if annotate_peak_source is None or not _legacy_field_value(annotate_peak_source).strip():
        return "ambiguous", "consumer lacks nonblank annotate_peak_source for base peak input selection"
    if _legacy_field_value(annotate_peak_source) != "resolved":
        return (
            "unmatched",
            "consumer annotate_peak_source selects a merged or otherwise undeclared peak input: "
            f"{_legacy_field_value(annotate_peak_source)!r}",
        )
    return None, None


def _resolver_match_detail(resolver: InstanceEdgeResolver) -> str:
    details = [f"resolver={resolver.label}"]
    if resolver.producer_identity_constants:
        constants = ",".join(
            f"{field_key}={_legacy_field_value(value)}"
            for field_key, value in resolver.producer_identity_constants
        )
        details.append(f"producer_identity_constants={constants}")
    if resolver.derived_extra_key_mode != "none":
        details.append(f"derived_extra_key={resolver.derived_extra_key_mode}")
    if resolver.consumer_artifact_selection_mode != "none":
        details.append(f"consumer_selection={resolver.consumer_artifact_selection_mode}")
    if resolver.producer_signature_mode == "annotated_peak_signature":
        details.append("producer_signature=annotated_peak_signature")
        details.append("artifact_health=separate")
    if len(details) == 1:
        details.append("explicit exact match")
    return "; ".join(details)


def _producer_signature_for_resolver(
    producer: DynamicInstanceNode,
    resolver: InstanceEdgeResolver,
) -> tuple[tuple[str, ...] | None, str | None]:
    if resolver.producer_signature_mode == "none":
        return None, None
    if resolver.producer_signature_mode != "annotated_peak_signature":
        return None, f"unsupported producer signature mode: {resolver.producer_signature_mode}"

    fields = dict(producer.fields_dict)
    raw_coordinate_mode = fields.get("quantification_mode", "legacy")
    fields["quantification_mode"] = resolved_quantification_mode(
        {"quantification_mode": raw_coordinate_mode}
    )
    annotation_schema = terminal_annotation_schema_from_coordinate_mode(
        raw_coordinate_mode
    )
    missing_fields = tuple(
        key
        for key in _ANNOTATED_PEAK_FIELD_KEYS
        if key not in fields or fields[key] is None or not _legacy_field_value(fields[key]).strip()
    )
    if missing_fields:
        return None, f"producer lacks annotated peak signature fields: {','.join(missing_fields)}"
    try:
        final_name = RunLayout.FinalName(
            dedup_method=fields["dedup_method"],
            polyA_status=fields["polyA_status"],
            coverage_scaling=fields["coverage_scaling"],
            depth_threshold=fields["depth_threshold"],
            depth_threshold_polyA=fields["depth_threshold_polyA"],
            merge_distance=fields["merge_distance"],
            merge_distance_polyA=fields["merge_distance_polyA"],
            misprime_A_count=fields["misprime_A_count"],
            misprime_in=fields["misprime_in"],
            misprimed_ratio_threshold=fields["misprimed_ratio_threshold"],
            quantification_mode=fields["quantification_mode"],
        )
        peak_record = build_scoped_artifact_name_v1(
            "peak",
            peak_name_payload(fields),
            legacy_name=final_name,
        )
        legacy_signature = (
            f"{final_name}.annotate-src-"
            f"{_legacy_field_value(fields['annotate_peak_source'])}"
        )
        if annotation_schema is not None:
            legacy_signature = f"{legacy_signature}.{annotation_schema}"
        annotation_record = build_scoped_artifact_name_v1(
            "annotation",
            annotation_name_payload(
                peak_record.payload,
                annotate_peak_source=fields["annotate_peak_source"],
                terminal_annotation_schema=annotation_schema,
            ),
            legacy_name=legacy_signature,
        )
    except (TypeError, ValueError) as exc:
        return None, f"could not build annotated peak signature: {exc}"
    return (legacy_signature, annotation_record.code), None


def _fan_in_kind_and_detail(
    *,
    consumer: DynamicInstanceNode,
    resolver: InstanceEdgeResolver,
    fan_in_values: Sequence[tuple[str, ...]],
    producer_nodes: Sequence[DynamicInstanceNode],
    finalized_unit_input: FinalizedUnitInputTableLoad | None,
) -> tuple[DynamicInstanceEdgeKind, str]:
    base_detail = (
        f"resolver={resolver.label}; producer fan-in fields: "
        f"{','.join(resolver.fan_in_identity_field_keys)}"
    )
    if resolver.key in _COHORT_CHUNK_FAN_IN_RESOLVER_KEYS:
        return _cohort_chunk_fan_in_kind_and_detail(
            consumer=consumer,
            base_detail=base_detail,
            fan_in_values=fan_in_values,
            producer_nodes=producer_nodes,
            finalized_unit_input=finalized_unit_input,
        )
    if resolver.key not in _FINALIZED_UNIT_FAN_IN_RESOLVER_KEYS:
        return "fan_in", f"{base_detail}; completeness unverified"
    if finalized_unit_input is None:
        return "fan_in", f"{base_detail}; completeness unverified: no finalized unit input evidence supplied"
    if finalized_unit_input.table is None:
        detail = finalized_unit_input.detail or "finalized unit input table is unavailable"
        return "fan_in", f"{base_detail}; completeness unverified: {detail}"

    expected_contigs, unresolved_detail = _expected_finalized_unit_contigs(
        consumer=consumer,
        finalized_unit_input=finalized_unit_input,
    )
    if expected_contigs is None:
        return "fan_in", f"{base_detail}; completeness unverified: {unresolved_detail}"
    if any(len(values) != 1 for values in fan_in_values):
        return "fan_in", f"{base_detail}; completeness unverified: unsupported fan-in identity shape"

    observed_contigs = frozenset(values[0] for values in fan_in_values)
    missing_contigs = tuple(sorted(expected_contigs.difference(observed_contigs)))
    unexpected_contigs = tuple(sorted(observed_contigs.difference(expected_contigs)))
    expected_label = ",".join(sorted(expected_contigs))
    observed_label = ",".join(sorted(observed_contigs))
    if missing_contigs or unexpected_contigs:
        mismatch_parts: list[str] = []
        if missing_contigs:
            mismatch_parts.append(f"missing={','.join(missing_contigs)}")
        if unexpected_contigs:
            mismatch_parts.append(f"unexpected={','.join(unexpected_contigs)}")
        return (
            "fan_in",
            f"{base_detail}; finalized unit table={finalized_unit_input.table.path}; "
            f"expected_contigs={expected_label}; observed_contigs={observed_label}; "
            f"{' '.join(mismatch_parts)}",
        )
    return (
        "fan_in_complete",
        f"{base_detail}; finalized unit table={finalized_unit_input.table.path}; "
        f"expected_contigs={expected_label}; observed_contigs={observed_label}; "
        "completeness verified; artifact health remains separate",
    )


def _cohort_chunk_fan_in_kind_and_detail(
    *,
    consumer: DynamicInstanceNode,
    base_detail: str,
    fan_in_values: Sequence[tuple[str, ...]],
    producer_nodes: Sequence[DynamicInstanceNode],
    finalized_unit_input: FinalizedUnitInputTableLoad | None,
) -> tuple[DynamicInstanceEdgeKind, str]:
    if finalized_unit_input is None:
        return "fan_in", f"{base_detail}; completeness unverified: no finalized unit input evidence supplied"
    if finalized_unit_input.table is None:
        detail = finalized_unit_input.detail or "finalized unit input table is unavailable"
        return "fan_in", f"{base_detail}; completeness unverified: {detail}"
    if len(producer_nodes) != len(fan_in_values) or any(len(values) != 1 for values in fan_in_values):
        return "fan_in", f"{base_detail}; completeness unverified: unsupported fan-in identity shape"

    expected_input_counts, unresolved_detail = _expected_finalized_cohort_input_counts(
        consumer=consumer,
        finalized_unit_input=finalized_unit_input,
    )
    if expected_input_counts is None:
        return "fan_in", f"{base_detail}; completeness unverified: {unresolved_detail}"

    expected_contigs = frozenset(expected_input_counts)
    observed_contigs = frozenset(values[0] for values in fan_in_values)
    missing_contigs = tuple(sorted(expected_contigs.difference(observed_contigs)))
    unexpected_contigs = tuple(sorted(observed_contigs.difference(expected_contigs)))
    expected_label = ",".join(sorted(expected_contigs))
    observed_label = ",".join(sorted(observed_contigs))
    if missing_contigs or unexpected_contigs:
        mismatch_parts: list[str] = []
        if missing_contigs:
            mismatch_parts.append(f"missing={','.join(missing_contigs)}")
        if unexpected_contigs:
            mismatch_parts.append(f"unexpected={','.join(unexpected_contigs)}")
        return (
            "fan_in",
            f"{base_detail}; finalized unit table={finalized_unit_input.table.path}; "
            f"expected_contigs={expected_label}; observed_contigs={observed_label}; "
            f"{' '.join(mismatch_parts)}",
        )

    payload_errors: list[str] = []
    for producer, values in zip(producer_nodes, fan_in_values):
        contig = values[0]
        input_files, payload_error = _merge_chunk_input_files(producer)
        if payload_error:
            payload_errors.append(f"{contig}:{payload_error}")
            continue
        expected_input_count = expected_input_counts[contig]
        if len(input_files) != expected_input_count:
            payload_errors.append(
                f"{contig}: input_files count={len(input_files)} expected={expected_input_count}"
            )
    if payload_errors:
        return (
            "fan_in",
            f"{base_detail}; finalized unit table={finalized_unit_input.table.path}; "
            f"expected_contigs={expected_label}; observed_contigs={observed_label}; "
            f"merge input manifest unverified: {'; '.join(sorted(payload_errors))}",
        )

    return (
        "fan_in_complete",
        f"{base_detail}; finalized unit table={finalized_unit_input.table.path}; "
        f"expected_contigs={expected_label}; observed_contigs={observed_label}; "
        "historical task-set completeness verified; merge input manifests verified; "
        "chunk artifact health remains separate",
    )


def _merge_chunk_input_files(producer: DynamicInstanceNode) -> tuple[tuple[str, ...], str | None]:
    payload = producer.worker_payload
    if not isinstance(payload, Mapping):
        return (), "worker payload is not a mapping"
    extra_info = payload.get("extra_info")
    if not isinstance(extra_info, Mapping):
        return (), "worker payload lacks extra_info mapping"
    raw_input_files = extra_info.get("input_files")
    if not isinstance(raw_input_files, (list, tuple)):
        return (), "worker payload lacks input_files list"
    input_files = tuple(raw_input_files)
    if not input_files:
        return (), "worker payload input_files is empty"
    if any(not isinstance(path, str) or not path.strip() for path in input_files):
        return (), "worker payload input_files must contain nonblank strings"
    if len(set(input_files)) != len(input_files):
        return (), "worker payload input_files has duplicate paths"
    return input_files, None


def _expected_finalized_cohort_input_counts(
    *,
    consumer: DynamicInstanceNode,
    finalized_unit_input: FinalizedUnitInputTableLoad,
) -> tuple[dict[str, int] | None, str]:
    table = finalized_unit_input.table
    if table is None:
        return None, finalized_unit_input.detail or "finalized unit input table is unavailable"

    consumer_dedup_method = consumer.fields_dict.get("dedup_method")
    if consumer_dedup_method is None or not _legacy_field_value(consumer_dedup_method).strip():
        return None, "consumer lacks nonblank dedup_method required for cohort fan-in evidence"
    dedup_method = _legacy_field_value(consumer_dedup_method)

    invalid_rows: list[str] = []
    seen_sample_contigs: set[tuple[str, str]] = set()
    input_counts: Counter[str] = Counter()
    for row in table.rows:
        required_values = {
            "unit_id": row.unit_id,
            "sample_id": row.sample_id,
            "contig_name": row.contig_name,
            "dedup_status": row.dedup_status,
            "resolved_path": row.resolved_path,
        }
        blank_fields = sorted(
            key for key, value in required_values.items() if not _legacy_field_value(value).strip()
        )
        if blank_fields:
            invalid_rows.append(f"{row.unit_id or '<blank-unit-id>'}: blank {','.join(blank_fields)}")
            continue
        unit_suffix = f".{row.contig_name}"
        if not row.unit_id.endswith(unit_suffix):
            invalid_rows.append(
                f"{row.unit_id}: unit_id does not end with expected contig suffix {unit_suffix!r}"
            )
            continue
        unit_sid = row.unit_id[: -len(unit_suffix)]
        if unit_sid != row.sample_id:
            invalid_rows.append(
                f"{row.unit_id}: sample_id={row.sample_id!r} does not match unit_id sid={unit_sid!r}"
            )
            continue
        sample_contig = (row.sample_id, row.contig_name)
        if sample_contig in seen_sample_contigs:
            invalid_rows.append(
                f"{row.unit_id}: duplicate sample_id/contig_name {row.sample_id!r}/{row.contig_name!r}"
            )
            continue
        seen_sample_contigs.add(sample_contig)
        effective_dedup_method = resolve_dedup_method(
            dedup_status=row.dedup_status,
            fallback_dedup_method=None,
        )
        if effective_dedup_method is None:
            invalid_rows.append(f"{row.unit_id}: no effective dedup method")
            continue
        if effective_dedup_method != dedup_method:
            invalid_rows.append(
                f"{row.unit_id}: effective dedup method {effective_dedup_method!r} does not match "
                f"consumer dedup_method {dedup_method!r}"
            )
            continue
        input_counts[row.contig_name] += 1

    if invalid_rows:
        return None, f"finalized cohort metadata is invalid: {'; '.join(sorted(invalid_rows))}"
    if not input_counts:
        return None, "finalized unit metadata has no usable cohort contigs"
    return dict(sorted(input_counts.items())), ""


def _expected_finalized_unit_contigs(
    *,
    consumer: DynamicInstanceNode,
    finalized_unit_input: FinalizedUnitInputTableLoad,
) -> tuple[frozenset[str] | None, str]:
    table = finalized_unit_input.table
    if table is None:
        return None, finalized_unit_input.detail or "finalized unit input table is unavailable"

    consumer_sid = consumer.fields_dict.get("sid")
    consumer_dedup_method = consumer.fields_dict.get("dedup_method")
    if consumer_sid is None or consumer_dedup_method is None:
        return None, "consumer lacks sid or dedup_method required for finalized unit fan-in evidence"
    sid = _legacy_field_value(consumer_sid)
    dedup_method = _legacy_field_value(consumer_dedup_method)

    relevant_rows = [row for row in table.rows if row.sample_id == sid]
    if not relevant_rows:
        return None, f"finalized unit input table has no rows for sid={sid!r}: {table.path}"

    invalid_rows: list[str] = []
    effective_dedup_methods: set[str] = set()
    contigs: list[str] = []
    for row in relevant_rows:
        required_values = {
            "unit_id": row.unit_id,
            "sample_id": row.sample_id,
            "contig_name": row.contig_name,
            "dedup_status": row.dedup_status,
        }
        blank_fields = sorted(key for key, value in required_values.items() if not value.strip())
        if blank_fields:
            invalid_rows.append(f"{row.unit_id or '<blank-unit-id>'}: blank {','.join(blank_fields)}")
            continue
        unit_suffix = f".{row.contig_name}"
        if not row.unit_id.endswith(unit_suffix):
            invalid_rows.append(
                f"{row.unit_id}: unit_id does not end with expected contig suffix {unit_suffix!r}"
            )
            continue
        unit_sid = row.unit_id[: -len(unit_suffix)]
        if unit_sid != sid:
            invalid_rows.append(
                f"{row.unit_id}: sample_id={row.sample_id!r} does not match unit_id sid={unit_sid!r}"
            )
            continue
        effective_dedup_method = resolve_dedup_method(
            dedup_status=row.dedup_status,
            fallback_dedup_method=None,
        )
        if effective_dedup_method is None:
            invalid_rows.append(f"{row.unit_id}: no effective dedup method")
            continue
        effective_dedup_methods.add(effective_dedup_method)
        contigs.append(row.contig_name)

    if invalid_rows:
        return None, f"finalized unit metadata is invalid for sid={sid!r}: {'; '.join(sorted(invalid_rows))}"
    if len(effective_dedup_methods) != 1:
        methods = ",".join(sorted(effective_dedup_methods)) or "<none>"
        return None, f"finalized unit metadata has mixed dedup methods for sid={sid!r}: {methods}"
    effective_dedup_method = next(iter(effective_dedup_methods))
    if effective_dedup_method != dedup_method:
        return None, (
            f"finalized unit metadata dedup method {effective_dedup_method!r} does not match "
            f"consumer dedup_method {dedup_method!r} for sid={sid!r}"
        )
    if len(set(contigs)) != len(contigs):
        duplicates = sorted(contig for contig, count in Counter(contigs).items() if count > 1)
        return None, f"finalized unit metadata has duplicate contigs for sid={sid!r}: {','.join(duplicates)}"
    if not contigs:
        return None, f"finalized unit metadata has no usable contigs for sid={sid!r}"
    return frozenset(contigs), ""


def _resolve_resolver_backed_input(
    *,
    artifact_key: str,
    consumer: DynamicInstanceNode,
    producer_key: TaskKey,
    producer_nodes: Sequence[DynamicInstanceNode],
    resolver: InstanceEdgeResolver,
    finalized_unit_input: FinalizedUnitInputTableLoad | None,
) -> Tuple[DynamicInstanceEdge, ...]:
    consumer_fields = consumer.fields_dict
    missing_consumer_fields = tuple(
        field_key
        for field_key in resolver.match_field_keys
        if not _match_field_present(consumer_fields, field_key)
    )
    candidate_ids = tuple(node.instance_id for node in producer_nodes)
    selection_kind, selection_detail = _consumer_artifact_selection_failure(consumer, resolver)
    if selection_kind is not None:
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind=selection_kind,
                producer_task_keys=(producer_key,),
                candidate_instance_ids=candidate_ids,
                matched_field_keys=resolver.match_field_keys,
                detail=f"resolver={resolver.label}; {selection_detail}",
            ),
        )
    if missing_consumer_fields:
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind="unmatched",
                producer_task_keys=(producer_key,),
                candidate_instance_ids=candidate_ids,
                matched_field_keys=resolver.match_field_keys,
                detail=(
                    f"resolver={resolver.label}; consumer lacks required fields: "
                    f"{','.join(missing_consumer_fields)}"
                ),
            ),
        )

    derived_producer_extra_key, derived_extra_key_error = _derived_producer_extra_key(consumer, resolver)
    if derived_extra_key_error:
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind="unmatched",
                producer_task_keys=(producer_key,),
                candidate_instance_ids=candidate_ids,
                matched_field_keys=resolver.match_field_keys,
                detail=f"resolver={resolver.label}; {derived_extra_key_error}",
            ),
        )

    compatible: list[DynamicInstanceNode] = []
    incomplete_ids: list[str] = []
    producer_constants = dict(resolver.producer_identity_constants)
    for producer in producer_nodes:
        producer_fields = producer.fields_dict
        producer_identity = producer.identity_dict
        if any(field_key not in producer_identity for field_key in producer_constants):
            incomplete_ids.append(producer.instance_id)
            continue
        if any(
            _legacy_field_value(producer_identity[field_key])
            != _legacy_field_value(expected_value)
            for field_key, expected_value in producer_constants.items()
        ):
            continue
        if resolver.producer_signature_mode == "none":
            if any(
                not _match_field_present(producer_fields, field_key)
                for field_key in resolver.match_field_keys
            ) or (
                derived_producer_extra_key is not None and "extra_key" not in producer_fields
            ):
                incomplete_ids.append(producer.instance_id)
                continue
            if all(
                _match_field_value(producer_fields, field_key)
                == _match_field_value(consumer_fields, field_key)
                for field_key in resolver.match_field_keys
            ) and _quantification_mode_matches(
                producer_fields,
                consumer_fields,
                resolver,
            ) and (
                derived_producer_extra_key is None
                or _legacy_field_value(producer_fields["extra_key"]) == derived_producer_extra_key
            ):
                compatible.append(producer)
            continue

        if any(
            not _match_field_present(producer_fields, field_key)
            for field_key in resolver.match_field_keys
        ):
            incomplete_ids.append(producer.instance_id)
            continue
        if not all(
            _match_field_value(producer_fields, field_key)
            == _match_field_value(consumer_fields, field_key)
            for field_key in resolver.match_field_keys
        ) or not _quantification_mode_matches(
            producer_fields,
            consumer_fields,
            resolver,
        ):
            # Explicitly different producer identities cannot satisfy this edge,
            # so absent derived evidence on those unrelated candidates is safe.
            continue

        producer_signatures, producer_signature_error = _producer_signature_for_resolver(producer, resolver)
        if producer_signature_error:
            incomplete_ids.append(producer.instance_id)
        elif (
            derived_producer_extra_key is not None
            and producer_signatures is not None
            and derived_producer_extra_key in producer_signatures
        ):
            compatible.append(producer)

    if incomplete_ids:
        required_evidence = (
            "semantic signature evidence"
            if resolver.producer_signature_mode != "none"
            else "explicit match/constant evidence"
        )
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind="ambiguous",
                producer_task_keys=(producer_key,),
                candidate_instance_ids=candidate_ids,
                matched_field_keys=resolver.match_field_keys,
                detail=(
                    f"resolver={resolver.label}; producer candidates lack required fields or {required_evidence}: "
                    f"{','.join(sorted(incomplete_ids))}"
                ),
            ),
        )
    if not compatible:
        required_evidence = (
            "semantic signature evidence"
            if resolver.producer_signature_mode != "none"
            else "explicit match/constant evidence"
        )
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind="unmatched",
                producer_task_keys=(producer_key,),
                candidate_instance_ids=candidate_ids,
                matched_field_keys=resolver.match_field_keys,
                detail=f"resolver={resolver.label}; no producer matches required fields or {required_evidence}",
            ),
        )

    compatible_ids = tuple(node.instance_id for node in compatible)
    if not resolver.fan_in_identity_field_keys:
        if len(compatible) == 1:
            return (
                DynamicInstanceEdge(
                    artifact_key=artifact_key,
                    consumer_instance_id=consumer.instance_id,
                    consumer_key=consumer.key,
                    kind="resolved",
                    producer_instance_id=compatible[0].instance_id,
                    producer_task_keys=(producer_key,),
                    candidate_instance_ids=compatible_ids,
                    matched_field_keys=resolver.match_field_keys,
                    detail=_resolver_match_detail(resolver),
                ),
            )
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind="ambiguous",
                producer_task_keys=(producer_key,),
                candidate_instance_ids=compatible_ids,
                matched_field_keys=resolver.match_field_keys,
                detail=f"{_resolver_match_detail(resolver)}; multiple candidates",
            ),
        )

    fan_in_values: list[tuple[str, ...]] = []
    for producer in compatible:
        identity = producer.identity_dict
        if any(field_key not in identity for field_key in resolver.fan_in_identity_field_keys):
            return (
                DynamicInstanceEdge(
                    artifact_key=artifact_key,
                    consumer_instance_id=consumer.instance_id,
                    consumer_key=consumer.key,
                    kind="ambiguous",
                    producer_task_keys=(producer_key,),
                    candidate_instance_ids=compatible_ids,
                    matched_field_keys=resolver.match_field_keys,
                    detail=(
                        f"resolver={resolver.label}; fan-in fields are not producer identity fields: "
                        f"{','.join(resolver.fan_in_identity_field_keys)}"
                    ),
                ),
            )
        fan_in_values.append(
            tuple(_legacy_field_value(identity[field_key]) for field_key in resolver.fan_in_identity_field_keys)
        )
    if len(set(fan_in_values)) != len(fan_in_values):
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind="ambiguous",
                producer_task_keys=(producer_key,),
                candidate_instance_ids=compatible_ids,
                matched_field_keys=resolver.match_field_keys,
                detail=(
                    f"resolver={resolver.label}; duplicate producer fan-in identities for "
                    f"{','.join(resolver.fan_in_identity_field_keys)}"
                ),
            ),
        )
    fan_in_kind, fan_in_detail = _fan_in_kind_and_detail(
        consumer=consumer,
        resolver=resolver,
        fan_in_values=fan_in_values,
        producer_nodes=compatible,
        finalized_unit_input=finalized_unit_input,
    )
    return tuple(
        DynamicInstanceEdge(
            artifact_key=artifact_key,
            consumer_instance_id=consumer.instance_id,
            consumer_key=consumer.key,
            kind=fan_in_kind,
            producer_instance_id=producer.instance_id,
            producer_task_keys=(producer_key,),
            candidate_instance_ids=compatible_ids,
            matched_field_keys=resolver.match_field_keys,
            detail=fan_in_detail,
        )
        for producer in compatible
    )


def _resolve_heuristic_input(
    *,
    artifact_key: str,
    consumer: DynamicInstanceNode,
    producer_key: TaskKey,
    producer_nodes: Sequence[DynamicInstanceNode],
) -> Tuple[DynamicInstanceEdge, ...]:
    compatible: list[tuple[DynamicInstanceNode, Tuple[str, ...]]] = []
    for producer in producer_nodes:
        matched_field_keys = _shared_field_keys(producer, consumer)
        if matched_field_keys and all(
            _legacy_field_value(producer.fields_dict[field_key])
            == _legacy_field_value(consumer.fields_dict[field_key])
            for field_key in matched_field_keys
        ):
            compatible.append((producer, matched_field_keys))

    if compatible:
        candidate_nodes = tuple(node for node, _ in compatible)
        candidate_ids = tuple(node.instance_id for node in candidate_nodes)
        matched_field_keys = tuple(sorted({key for _, keys in compatible for key in keys}))
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind="heuristic_matched",
                producer_task_keys=(producer_key,),
                candidate_instance_ids=candidate_ids,
                matched_field_keys=matched_field_keys,
                detail="matching same-name fields has no registered semantic resolver",
            ),
        )

    all_shared_fields = tuple(
        sorted(
            {
                field_key
                for producer in producer_nodes
                for field_key in _shared_field_keys(producer, consumer)
            }
        )
    )
    candidate_ids = tuple(node.instance_id for node in producer_nodes)
    if not all_shared_fields and len(producer_nodes) == 1:
        return (
            DynamicInstanceEdge(
                artifact_key=artifact_key,
                consumer_instance_id=consumer.instance_id,
                consumer_key=consumer.key,
                kind="singleton",
                producer_instance_id=producer_nodes[0].instance_id,
                producer_task_keys=(producer_key,),
                candidate_instance_ids=candidate_ids,
                detail="only observed producer has no resolver-backed join fields",
            ),
        )
    if not all_shared_fields:
        detail = "multiple observed producers have no resolver-backed join fields"
    else:
        detail = "producer decisions exist but no same-name fields are compatible"
    return (
        DynamicInstanceEdge(
            artifact_key=artifact_key,
            consumer_instance_id=consumer.instance_id,
            consumer_key=consumer.key,
            kind="unmatched" if all_shared_fields else "ambiguous",
            producer_task_keys=(producer_key,),
            candidate_instance_ids=candidate_ids,
            matched_field_keys=all_shared_fields,
            detail=detail,
        ),
    )


def _node_from_decision(
    decision: CurrentReuseDecisionSummaryV2,
    *,
    legacy_completion_reconciled: bool = False,
) -> DynamicInstanceNode:
    return DynamicInstanceNode(
        instance_id=decision.instance_id,
        decision_id=decision.decision_id,
        stage=decision.stage,
        task_base=decision.task_base,
        observed_action=decision.action,
        legacy_done=(
            decision.legacy_done or legacy_completion_reconciled
        ),
        queued=decision.queued and not legacy_completion_reconciled,
        force=decision.force,
        identity=decision.identity,
        params=decision.params,
        consumes=decision.consumes,
        produces=decision.produces,
        reasons=decision.reasons,
        worker_payload=decision.worker_payload,
        created_at=decision.created_at,
        reuse_policy_version=decision.reuse_policy_version,
        implementation_version=decision.implementation_version,
        declaration_source=decision.declaration_source,
        legacy_completion_reconciled=legacy_completion_reconciled,
    )


def _shared_field_keys(producer: DynamicInstanceNode, consumer: DynamicInstanceNode) -> Tuple[str, ...]:
    return tuple(sorted(set(producer.fields_dict).intersection(consumer.fields_dict)))


def _blockers_for_edges(
    edges: Iterable[DynamicInstanceEdge],
    *,
    blocking_edge_kinds: frozenset[DynamicInstanceEdgeKind] = _BLOCKING_EDGE_KINDS,
) -> Tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            f"{edge.kind}:{edge.artifact_key}"
            for edge in edges
            if edge.kind in blocking_edge_kinds
        )
    )


def _policy_reasons(policy: ObserveOnlyPlanPolicy) -> Tuple[str, ...]:
    if policy == DEFAULT_OBSERVE_ONLY_PLAN_POLICY:
        return ()
    return (f"observe_only_plan_policy:{policy.name}",)


def _field_dicts(fields: Iterable[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [{"key": key, "value": value} for key, value in fields]


def _task_key_dict(key: TaskKey) -> dict[str, str]:
    return {"stage": key[0], "task_base": key[1]}


def _legacy_field_value(value: Any) -> str:
    return "" if value is None else str(value)


def _match_field_present(fields: Mapping[str, Any], key: str) -> bool:
    return key in fields or key == "quantification_mode"


def _match_field_value(fields: Mapping[str, Any], key: str) -> str:
    if key == "quantification_mode":
        return _legacy_field_value(fields.get(key, "legacy"))
    return _legacy_field_value(fields[key])


def _quantification_mode_matches(
    producer_fields: Mapping[str, Any],
    consumer_fields: Mapping[str, Any],
    resolver: InstanceEdgeResolver,
) -> bool:
    final_peak_chain = {"call_peaks", "annotate_peaks"}
    if (
        resolver.producer_key[0] not in final_peak_chain
        or resolver.consumer_key[0] not in final_peak_chain
    ):
        return True
    producer_mode = resolved_quantification_mode(
        {
            "quantification_mode": _match_field_value(
                producer_fields,
                "quantification_mode",
            )
        }
    )
    consumer_mode = resolved_quantification_mode(
        {
            "quantification_mode": _match_field_value(
                consumer_fields,
                "quantification_mode",
            )
        }
    )
    return producer_mode == consumer_mode


def _node_sort_key(node: DynamicInstanceNode) -> tuple[str, str, str]:
    return node.stage, node.task_base, node.instance_id


def _edge_sort_key(edge: DynamicInstanceEdge) -> tuple[str, str, str, str, str]:
    return (
        edge.consumer_key[0],
        edge.consumer_key[1],
        edge.consumer_instance_id,
        edge.artifact_key,
        edge.producer_instance_id or "",
    )


def _latest_nodes(nodes: Sequence[DynamicInstanceNode], *, limit: int | None) -> Tuple[DynamicInstanceNode, ...]:
    ordered = tuple(sorted(nodes, key=lambda node: (-node.decision_id, node.instance_id)))
    if limit is None:
        return ordered
    return ordered[: max(0, int(limit))]


def _latest_edges(
    edges: Sequence[DynamicInstanceEdge],
    nodes_by_instance_id: Mapping[str, DynamicInstanceNode],
    *,
    limit: int | None,
) -> Tuple[DynamicInstanceEdge, ...]:
    ordered = tuple(
        sorted(
            edges,
            key=lambda edge: (
                -nodes_by_instance_id[edge.consumer_instance_id].decision_id,
                edge.consumer_instance_id,
                edge.artifact_key,
                edge.producer_instance_id or "",
            ),
        )
    )
    if limit is None:
        return ordered
    return ordered[: max(0, int(limit))]


def _load_completion_instance_ids(
    db_path: Path,
    decisions: Sequence[CurrentReuseDecisionSummaryV2],
) -> frozenset[str]:
    """Reconcile a completed initial dispatch from active completion truth."""

    latest_by_instance_id: dict[str, CurrentReuseDecisionSummaryV2] = {}
    for decision in decisions:
        previous = latest_by_instance_id.get(decision.instance_id)
        if previous is None or decision.decision_id >= previous.decision_id:
            latest_by_instance_id[decision.instance_id] = decision
    candidates = tuple(
        decision
        for decision in latest_by_instance_id.values()
        if (
            decision.action == "would_run"
            and decision.queued
            and not decision.legacy_done
            and not decision.force
        )
    )
    if not candidates or not db_path.is_file():
        return frozenset()

    try:
        with sqlite3.connect(
            f"{db_path.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as con:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            completion_snapshot = (
                load_task_completion_snapshot_from_connection_v2(
                    con,
                    state_db_path=db_path,
                )
            )
            ledger_records = load_current_task_parameter_ledger_records_v3(con)
    except (
        OSError,
        sqlite3.Error,
        TaskCompletionLoadError,
        TaskParameterLedgerError,
    ):
        return frozenset()

    ledger_by_coordinate = {
        record.task_coordinate_id: (
            record.done_known,
            record.queued,
            record.force,
            record.updated_at,
        )
        for record in ledger_records
    }

    done_coordinate_ids = {
        record.task_coordinate_id
        for record in completion_snapshot.records
    }
    reconciled: set[str] = set()
    for decision in candidates:
        if (
            decision.coordinate_status != "available"
            or decision.task_coordinate_id not in done_coordinate_ids
        ):
            continue
        ledger = ledger_by_coordinate.get(
            decision.task_coordinate_id
        )
        if ledger is None:
            continue
        (
            done_known,
            queued,
            force,
            updated_at,
        ) = ledger
        if (
            not done_known
            or queued
            or force
            or not updated_at
            or updated_at < decision.created_at
        ):
            continue
        reconciled.add(decision.instance_id)
    return frozenset(reconciled)


def _resolve_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path).expanduser()
    if path.is_dir():
        if path.name == "state":
            return path / "state.sqlite"
        candidate = path / "state" / "state.sqlite"
        if candidate.exists():
            return candidate
        return path / "state.sqlite"
    return path
