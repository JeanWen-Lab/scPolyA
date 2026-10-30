"""Read-only branch-selection diagnostics for coverage artifacts.

These descriptors explain which symbolic coverage variant a persisted task
would select. They are intentionally separate from dependency edges, artifact
health, and reuse eligibility.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, Tuple


TaskKey = Tuple[str, str]
BranchStatus = Literal["active", "no_op", "unresolved", "unsupported"]
StrictEffect = Literal["diagnostic_only"]
BranchArtifactManifestStatus = Literal["available", "unavailable", "unsupported", "error"]

BRANCH_ARTIFACT_MANIFEST_V1_KEY = "branch_artifact_manifest_v1"
BRANCH_ARTIFACT_MANIFEST_V1_SCHEMA = "branch-artifact-manifest-v1"

COVERAGE_BRANCH_TASK_KEYS: Tuple[TaskKey, ...] = (
    ("call_peaks", "generate_raw_coverage"),
    ("call_peaks", "upm_scale_coverage"),
    ("call_peaks", "exclude_cds_region"),
    ("call_peaks", "negate_bedgraph"),
    ("call_peaks", "generate_bigwig"),
    ("call_peaks", "merge_call_peaks"),
)

_RAW_COVERAGE = "cohort_coverage:raw_bedgraph"
_SCALED_COVERAGE = "cohort_coverage:scaled_bedgraph"
_REMOVE_CDS_COVERAGE = "cohort_coverage:remove_cds_bedgraph"
_NEGATED_COVERAGE = "cohort_coverage:negated_bedgraph"
_BIGWIG = "cohort_coverage:bigwig"
_MERGED_PEAKS = "cohort_coverage:merged_peaks_bed"
_MISSING = "<missing>"
_NONE = "<none>"


@dataclass(frozen=True)
class BranchDescriptor:
    """One persisted branch selection, never strict reuse evidence."""

    instance_id: str
    task_key: TaskKey
    family: str
    branch_status: BranchStatus
    branch_role: str
    branch_key: Tuple[Tuple[str, str], ...]
    effective_input_artifact: str | None
    effective_output_artifact: str | None
    selected_fields: Tuple[Tuple[str, str], ...]
    reasons: Tuple[str, ...]
    strict_effect: StrictEffect = "diagnostic_only"

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "task_key": {"stage": self.task_key[0], "task_base": self.task_key[1]},
            "family": self.family,
            "branch_status": self.branch_status,
            "branch_role": self.branch_role,
            "branch_key": _field_dicts(self.branch_key),
            "effective_input_artifact": self.effective_input_artifact,
            "effective_output_artifact": self.effective_output_artifact,
            "selected_fields": _field_dicts(self.selected_fields),
            "reasons": list(self.reasons),
            "strict_effect": self.strict_effect,
        }


@dataclass(frozen=True)
class BranchDescriptorReport:
    """A grouped read-only view of recognized branch decisions."""

    state_db_path: str
    source_node_count: int
    descriptors: Tuple[BranchDescriptor, ...]

    @property
    def descriptor_count(self) -> int:
        return len(self.descriptors)

    @property
    def status_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(descriptor.branch_status for descriptor in self.descriptors).items()))

    @property
    def role_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(descriptor.branch_role for descriptor in self.descriptors).items()))

    def as_dict(self, *, limit: int | None = None) -> dict[str, Any]:
        descriptors = self.descriptors if limit is None else self.descriptors[: max(0, int(limit))]
        return {
            "state_db_path": self.state_db_path,
            "source_node_count": self.source_node_count,
            "descriptor_count": self.descriptor_count,
            "status_counts": self.status_counts,
            "role_counts": self.role_counts,
            "descriptors": [descriptor.as_dict() for descriptor in descriptors],
        }


def branch_descriptors_for_node(node: Any) -> Tuple[BranchDescriptor, ...]:
    """Describe recognized coverage branches from persisted node fields only."""

    task_key = (str(getattr(node, "stage", "")), str(getattr(node, "task_base", "")))
    if task_key[0] != "call_peaks":
        return ()
    fields = _node_fields(node)
    descriptor_builder = _COVERAGE_DESCRIPTOR_BUILDERS.get(task_key)
    if descriptor_builder is None:
        return ()
    return (descriptor_builder(node, task_key, fields),)


def build_branch_descriptor_report(nodes_or_graph: Iterable[Any] | Any) -> BranchDescriptorReport:
    """Build branch diagnostics without consulting config, paths, or health."""

    if hasattr(nodes_or_graph, "nodes"):
        source_nodes = tuple(getattr(nodes_or_graph, "nodes"))
        state_db_path = str(getattr(nodes_or_graph, "state_db_path", ""))
    else:
        source_nodes = tuple(nodes_or_graph)
        state_db_path = ""
    descriptors = tuple(
        sorted(
            (
                descriptor
                for node in source_nodes
                for descriptor in branch_descriptors_for_node(node)
            ),
            key=lambda descriptor: (
                descriptor.task_key[0],
                descriptor.task_key[1],
                descriptor.instance_id,
            ),
        )
    )
    return BranchDescriptorReport(
        state_db_path=state_db_path,
        source_node_count=len(source_nodes),
        descriptors=descriptors,
    )


def format_branch_descriptor_report(report: BranchDescriptorReport, *, limit: int = 20) -> str:
    lines = [
        f"Branch artifact descriptors: {report.state_db_path or '-'}",
        f"source nodes: {report.source_node_count}",
        f"descriptors: {report.descriptor_count}",
    ]
    if report.status_counts:
        lines.append("statuses:")
        for status, count in report.status_counts.items():
            lines.append(f"  {status}: {count}")
    descriptors = report.descriptors[: max(0, int(limit))]
    if descriptors:
        lines.append("instances:")
        for descriptor in descriptors:
            lines.append(
                f"  {descriptor.instance_id[:12]} {descriptor.task_key[0]}:{descriptor.task_key[1]} "
                f"role={descriptor.branch_role} status={descriptor.branch_status} "
                f"strict={descriptor.strict_effect}"
            )
            lines.append(
                f"    input={descriptor.effective_input_artifact or '-'} "
                f"output={descriptor.effective_output_artifact or '-'}"
            )
            if descriptor.reasons:
                lines.append(f"    reasons={'; '.join(descriptor.reasons)}")
    return "\n".join(lines)


def build_branch_artifact_manifest_v1(
    *,
    stage: str,
    task_base: str,
    identity: Iterable[tuple[str, Any]],
    params: Iterable[tuple[str, Any]],
    implementation_version: str,
) -> dict[str, Any] | None:
    """Capture one coverage branch selection from frozen decision-time fields.

    The manifest is deliberately limited to recognized coverage tasks. It does
    not read current configuration, inspect output paths, or alter graph and
    scheduler evidence. Other task families return ``None`` so historical and
    out-of-scope decisions remain distinguishable from a captured snapshot.
    """

    task_key = str(stage), str(task_base)
    if task_key not in _COVERAGE_DESCRIPTOR_BUILDERS:
        return None
    try:
        fields = _manifest_fields(identity=identity, params=params)
        descriptors = branch_descriptors_for_node(
            _ManifestNode(
                instance_id="",
                stage=task_key[0],
                task_base=task_key[1],
                fields_dict=fields,
            )
        )
        if len(descriptors) != 1:
            return _manifest_error(
                task_key=task_key,
                implementation_version=implementation_version,
                reason=f"recognized coverage task yielded {len(descriptors)} branch descriptors",
            )
        descriptor = descriptors[0]
    except Exception as exc:  # Diagnostics must not interrupt legacy scheduling.
        return _manifest_error(
            task_key=task_key,
            implementation_version=implementation_version,
            reason=f"branch descriptor construction failed: {type(exc).__name__}: {exc}",
        )

    status = _manifest_status(descriptor.branch_status)
    descriptor_payload = _manifest_descriptor_payload(descriptor)
    return {
        "schema_version": BRANCH_ARTIFACT_MANIFEST_V1_SCHEMA,
        "status": status,
        "strict_effect": "diagnostic_only",
        "generated_from": {
            "source": "decision_task_instance_v2",
            "implementation_version": str(implementation_version),
        },
        "task_key": {"stage": task_key[0], "task_base": task_key[1]},
        "descriptor": descriptor_payload,
        "reasons": list(descriptor.reasons),
    }


@dataclass(frozen=True)
class _ManifestNode:
    instance_id: str
    stage: str
    task_base: str
    fields_dict: Mapping[str, Any]


def _manifest_fields(
    *,
    identity: Iterable[tuple[str, Any]],
    params: Iterable[tuple[str, Any]],
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for collection in (identity, params):
        for key, value in collection:
            fields.setdefault(str(key), value)
    return fields


def _manifest_status(branch_status: BranchStatus) -> BranchArtifactManifestStatus:
    if branch_status in {"active", "no_op"}:
        return "available"
    if branch_status == "unresolved":
        return "unavailable"
    return "unsupported"


def _manifest_descriptor_payload(descriptor: BranchDescriptor) -> dict[str, Any]:
    return {
        "family": descriptor.family,
        "branch_status": descriptor.branch_status,
        "branch_role": descriptor.branch_role,
        "branch_key": _field_dicts(descriptor.branch_key),
        "effective_input_artifact": descriptor.effective_input_artifact,
        "effective_output_artifact": descriptor.effective_output_artifact,
        "selected_fields": _field_dicts(descriptor.selected_fields),
        "reasons": list(descriptor.reasons),
        "strict_effect": descriptor.strict_effect,
    }


def _manifest_error(
    *,
    task_key: TaskKey,
    implementation_version: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": BRANCH_ARTIFACT_MANIFEST_V1_SCHEMA,
        "status": "error",
        "strict_effect": "diagnostic_only",
        "generated_from": {
            "source": "decision_task_instance_v2",
            "implementation_version": str(implementation_version),
        },
        "task_key": {"stage": task_key[0], "task_base": task_key[1]},
        "descriptor": None,
        "reasons": [reason],
    }


def _describe_raw_coverage(node: Any, task_key: TaskKey, fields: Mapping[str, Any]) -> BranchDescriptor:
    context, error = _coverage_context(fields, require_scaling=False)
    selected_keys = (*_BASE_BRANCH_KEYS, "coverage_source", "strand_filter")
    if error:
        return _descriptor(
            node,
            task_key,
            branch_status="unresolved",
            branch_role="raw_coverage",
            fields=fields,
            selected_keys=selected_keys,
            reasons=(error,),
        )
    source = fields.get("coverage_source")
    strand_filter = fields.get("strand_filter")
    if source is not None or strand_filter is not None:
        if source != "unstranded" or strand_filter != context["strand"]:
            return _descriptor(
                node,
                task_key,
                branch_status="unresolved",
                branch_role="raw_coverage",
                fields=fields,
                selected_keys=selected_keys,
                reasons=(
                    "raw coverage requires coverage_source=unstranded and a matching strand_filter",
                ),
            )
        reason = "raw coverage selects an unstranded cohort BAM with an explicit strand filter"
    else:
        reason = "legacy raw coverage source signature is unavailable (diagnostic only)"
    return _descriptor(
        node,
        task_key,
        branch_status="active",
        branch_role="raw_coverage",
        fields=fields,
        selected_keys=selected_keys,
        effective_output_artifact=_RAW_COVERAGE,
        reasons=(reason,),
    )


def _describe_upm_scale(node: Any, task_key: TaskKey, fields: Mapping[str, Any]) -> BranchDescriptor:
    scaling_factor, scaling_error = _scaling_factor(fields)
    selected_keys = (*_BASE_BRANCH_KEYS, "scaling_factor")
    if scaling_error:
        return _descriptor(
            node,
            task_key,
            branch_status="unresolved",
            branch_role="scale",
            fields=fields,
            selected_keys=selected_keys,
            reasons=(scaling_error,),
        )
    if scaling_factor is None:
        return _descriptor(
            node,
            task_key,
            branch_status="no_op",
            branch_role="scale_no_op_unscaled",
            fields=fields,
            selected_keys=selected_keys,
            reasons=("persisted scaling_factor selects the worker's no-op branch",),
        )
    context, error = _coverage_context(fields, require_scaling=True)
    if error:
        return _descriptor(
            node,
            task_key,
            branch_status="unresolved",
            branch_role="scale",
            fields=fields,
            selected_keys=selected_keys,
            reasons=(error,),
        )
    return _descriptor(
        node,
        task_key,
        branch_status="active",
        branch_role="scale",
        fields=fields,
        selected_keys=selected_keys,
        effective_input_artifact=_RAW_COVERAGE,
        effective_output_artifact=_SCALED_COVERAGE,
        reasons=("scaled coverage branch selected from persisted scaling_factor",),
    )


def _describe_exclude_cds(node: Any, task_key: TaskKey, fields: Mapping[str, Any]) -> BranchDescriptor:
    is_polyA, polyA_error = _boolean_field(fields, "is_polyA")
    selected_keys = (*_BASE_BRANCH_KEYS, "scaling_factor")
    if polyA_error:
        return _descriptor(
            node,
            task_key,
            branch_status="unresolved",
            branch_role="exclude_cds",
            fields=fields,
            selected_keys=selected_keys,
            reasons=(polyA_error,),
        )
    if is_polyA:
        return _descriptor(
            node,
            task_key,
            branch_status="no_op",
            branch_role="exclude_cds_no_op_polyA",
            fields=fields,
            selected_keys=selected_keys,
            reasons=("persisted is_polyA selects the worker's early-return branch",),
        )
    context, error = _coverage_context(fields, require_scaling=True)
    if error:
        return _descriptor(
            node,
            task_key,
            branch_status="unresolved",
            branch_role="exclude_cds",
            fields=fields,
            selected_keys=selected_keys,
            reasons=(error,),
        )
    return _descriptor(
        node,
        task_key,
        branch_status="active",
        branch_role="exclude_cds",
        fields=fields,
        selected_keys=selected_keys,
        effective_input_artifact=_coverage_input_artifact(context["scaling_factor"]),
        effective_output_artifact=_REMOVE_CDS_COVERAGE,
        reasons=("expression coverage selects CDS-removal output",),
    )


def _describe_negate(node: Any, task_key: TaskKey, fields: Mapping[str, Any]) -> BranchDescriptor:
    selected_keys = (*_BASE_BRANCH_KEYS, "scaling_factor", "remove_CDS")
    context, error = _coverage_context(fields, require_scaling=True, require_remove_cds=True)
    if error:
        return _descriptor(
            node,
            task_key,
            branch_status="unresolved",
            branch_role="negate",
            fields=fields,
            selected_keys=selected_keys,
            reasons=(error,),
        )
    if context["strand"] != "minus":
        return _descriptor(
            node,
            task_key,
            branch_status="unsupported",
            branch_role="negate",
            fields=fields,
            selected_keys=selected_keys,
            reasons=("negate worker is only scheduled for minus-strand branch items",),
        )
    if context["is_polyA"] and context["remove_CDS"]:
        return _descriptor(
            node,
            task_key,
            branch_status="unsupported",
            branch_role="negate",
            fields=fields,
            selected_keys=selected_keys,
            reasons=("PolyA negate branch does not schedule a remove_CDS variant",),
        )
    input_artifact = (
        _REMOVE_CDS_COVERAGE
        if context["remove_CDS"]
        else _coverage_input_artifact(context["scaling_factor"])
    )
    return _descriptor(
        node,
        task_key,
        branch_status="active",
        branch_role="negate",
        fields=fields,
        selected_keys=selected_keys,
        effective_input_artifact=input_artifact,
        effective_output_artifact=_NEGATED_COVERAGE,
        reasons=("minus-strand negated coverage branch selected",),
    )


def _describe_bigwig(node: Any, task_key: TaskKey, fields: Mapping[str, Any]) -> BranchDescriptor:
    selected_keys = (*_BASE_BRANCH_KEYS, "scaling_factor", "remove_CDS", "is_negate")
    context, error = _coverage_context(
        fields,
        require_scaling=True,
        require_remove_cds=True,
        require_is_negate=True,
    )
    if error:
        return _descriptor(
            node,
            task_key,
            branch_status="unresolved",
            branch_role="bigwig",
            fields=fields,
            selected_keys=selected_keys,
            reasons=(error,),
        )
    if context["is_negate"] and context["strand"] != "minus":
        return _descriptor(
            node,
            task_key,
            branch_status="unsupported",
            branch_role="bigwig",
            fields=fields,
            selected_keys=selected_keys,
            reasons=("negated bigWig variant is only scheduled for minus-strand items",),
        )
    if context["is_polyA"] and context["remove_CDS"]:
        return _descriptor(
            node,
            task_key,
            branch_status="unsupported",
            branch_role="bigwig",
            fields=fields,
            selected_keys=selected_keys,
            reasons=("PolyA bigWig branch does not schedule a remove_CDS variant",),
        )
    input_artifact = _NEGATED_COVERAGE if context["is_negate"] else (
        _REMOVE_CDS_COVERAGE
        if context["remove_CDS"]
        else _coverage_input_artifact(context["scaling_factor"])
    )
    return _descriptor(
        node,
        task_key,
        branch_status="active",
        branch_role="bigwig",
        fields=fields,
        selected_keys=selected_keys,
        effective_input_artifact=input_artifact,
        effective_output_artifact=_BIGWIG,
        reasons=("bigWig input variant selected from persisted coverage flags",),
    )


def _describe_merge_call_peaks(node: Any, task_key: TaskKey, fields: Mapping[str, Any]) -> BranchDescriptor:
    selected_keys = (*_BASE_BRANCH_KEYS, "scaling_factor", "remove_CDS")
    context, error = _coverage_context(fields, require_scaling=True)
    if error:
        return _descriptor(
            node,
            task_key,
            branch_status="unresolved",
            branch_role="peak_call",
            fields=fields,
            selected_keys=selected_keys,
            reasons=(error,),
        )
    worker_remove_cds = not context["is_polyA"]
    input_artifact = (
        _REMOVE_CDS_COVERAGE
        if worker_remove_cds
        else _coverage_input_artifact(context["scaling_factor"])
    )
    reasons = ["peak-call worker derives remove_CDS from is_polyA"]
    persisted_remove_cds, persisted_remove_cds_error = _optional_boolean_field(fields, "remove_CDS")
    if persisted_remove_cds_error is None and persisted_remove_cds is not None and persisted_remove_cds != worker_remove_cds:
        reasons.append("persisted remove_CDS is not used for this worker input selection")
    return _descriptor(
        node,
        task_key,
        branch_status="active",
        branch_role="peak_call",
        fields=fields,
        selected_keys=selected_keys,
        effective_input_artifact=input_artifact,
        effective_output_artifact=_MERGED_PEAKS,
        reasons=tuple(reasons),
        derived_fields=(("worker_remove_CDS", _boolean_text(worker_remove_cds)),),
    )


def _descriptor(
    node: Any,
    task_key: TaskKey,
    *,
    branch_status: BranchStatus,
    branch_role: str,
    fields: Mapping[str, Any],
    selected_keys: Tuple[str, ...],
    effective_input_artifact: str | None = None,
    effective_output_artifact: str | None = None,
    reasons: Tuple[str, ...],
    derived_fields: Tuple[Tuple[str, str], ...] = (),
) -> BranchDescriptor:
    selected_fields = (*_field_snapshot(fields, selected_keys), *derived_fields)
    return BranchDescriptor(
        instance_id=str(getattr(node, "instance_id", "")),
        task_key=task_key,
        family="call_peaks_coverage",
        branch_status=branch_status,
        branch_role=branch_role,
        branch_key=selected_fields,
        effective_input_artifact=effective_input_artifact,
        effective_output_artifact=effective_output_artifact,
        selected_fields=selected_fields,
        reasons=reasons,
    )


def _coverage_context(
    fields: Mapping[str, Any],
    *,
    require_scaling: bool,
    require_remove_cds: bool = False,
    require_is_negate: bool = False,
) -> tuple[dict[str, Any], str | None]:
    dedup_method, dedup_error = _required_text_field(fields, "dedup_method")
    if dedup_error:
        return {}, dedup_error
    is_polyA, polyA_error = _boolean_field(fields, "is_polyA")
    if polyA_error:
        return {}, polyA_error
    polyA_status, polyA_status_error = _optional_text_field(fields, "polyA_status")
    if polyA_status_error:
        return {}, polyA_status_error
    if is_polyA and polyA_status is None:
        return {}, "is_polyA=true requires a nonblank persisted polyA_status"
    if not is_polyA and polyA_status is not None:
        return {}, "is_polyA=false conflicts with nonblank persisted polyA_status"
    strand, strand_error = _required_text_field(fields, "strand")
    if strand_error:
        return {}, strand_error
    if strand not in {"plus", "minus"}:
        return {}, f"persisted strand is unsupported: {strand!r}"

    context: dict[str, Any] = {
        "dedup_method": dedup_method,
        "is_polyA": is_polyA,
        "polyA_status": polyA_status,
        "strand": strand,
    }
    if require_scaling:
        scaling_factor, scaling_error = _scaling_factor(fields)
        if scaling_error:
            return {}, scaling_error
        context["scaling_factor"] = scaling_factor
    if require_remove_cds:
        remove_cds, remove_cds_error = _boolean_field(fields, "remove_CDS")
        if remove_cds_error:
            return {}, remove_cds_error
        context["remove_CDS"] = remove_cds
    if require_is_negate:
        is_negate, is_negate_error = _boolean_field(fields, "is_negate")
        if is_negate_error:
            return {}, is_negate_error
        context["is_negate"] = is_negate
    return context, None


def _coverage_input_artifact(scaling_factor: float | None) -> str:
    return _RAW_COVERAGE if scaling_factor is None else _SCALED_COVERAGE


def _scaling_factor(fields: Mapping[str, Any]) -> tuple[float | None, str | None]:
    if "scaling_factor" not in fields:
        return None, "consumer lacks persisted scaling_factor"
    value = fields["scaling_factor"]
    if value is None:
        return None, None
    if isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
        return None, None
    if isinstance(value, bool):
        return None, "persisted scaling_factor must not be boolean"
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, f"persisted scaling_factor is not numeric: {value!r}"
    if not math.isfinite(parsed):
        return None, f"persisted scaling_factor is not finite: {value!r}"
    return parsed, None


def _boolean_field(fields: Mapping[str, Any], key: str) -> tuple[bool | None, str | None]:
    value, error = _optional_boolean_field(fields, key)
    if error:
        return None, error
    if value is None:
        return None, f"consumer lacks persisted {key}"
    return value, None


def _optional_boolean_field(fields: Mapping[str, Any], key: str) -> tuple[bool | None, str | None]:
    if key not in fields:
        return None, None
    value = fields[key]
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, None
    if isinstance(value, int) and value in {0, 1}:
        return bool(value), None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "none", "null"}:
            return None, None
        if normalized in {"true", "1"}:
            return True, None
        if normalized in {"false", "0"}:
            return False, None
    return None, f"persisted {key} is not a boolean: {value!r}"


def _required_text_field(fields: Mapping[str, Any], key: str) -> tuple[str | None, str | None]:
    value, error = _optional_text_field(fields, key)
    if error:
        return None, error
    if value is None:
        return None, f"consumer lacks nonblank persisted {key}"
    return value, None


def _optional_text_field(fields: Mapping[str, Any], key: str) -> tuple[str | None, str | None]:
    if key not in fields:
        return None, None
    value = fields[key]
    if value is None:
        return None, None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.lower() in {"", "none", "null"}:
            return None, None
        return normalized, None
    return str(value), None


def _node_fields(node: Any) -> Mapping[str, Any]:
    fields = getattr(node, "fields_dict", None)
    if isinstance(fields, Mapping):
        return fields
    identity = getattr(node, "identity", ())
    params = getattr(node, "params", ())
    combined: dict[str, Any] = {}
    for key, value in (*identity, *params):
        combined.setdefault(str(key), value)
    return combined


def _field_snapshot(fields: Mapping[str, Any], keys: Iterable[str]) -> Tuple[Tuple[str, str], ...]:
    return tuple((key, _field_text(fields[key]) if key in fields else _MISSING) for key in keys)


def _field_text(value: Any) -> str:
    if value is None:
        return _NONE
    return str(value)


def _boolean_text(value: bool) -> str:
    return "true" if value else "false"


def _field_dicts(fields: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"key": key, "value": value} for key, value in fields]


_BASE_BRANCH_KEYS = ("dedup_method", "polyA_status", "is_polyA", "strand")

_COVERAGE_DESCRIPTOR_BUILDERS = {
    ("call_peaks", "generate_raw_coverage"): _describe_raw_coverage,
    ("call_peaks", "upm_scale_coverage"): _describe_upm_scale,
    ("call_peaks", "exclude_cds_region"): _describe_exclude_cds,
    ("call_peaks", "negate_bedgraph"): _describe_negate,
    ("call_peaks", "generate_bigwig"): _describe_bigwig,
    ("call_peaks", "merge_call_peaks"): _describe_merge_call_peaks,
}
