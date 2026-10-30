"""Bounded, descriptive CS07 arithmetic on caller-supplied parsed records.

No I/O, native task execution, annotation reassignment, or provenance certification.
See gene_diagnostics_contract.json for the explicit completeness declarations.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
import math

SCHEMA = "scpolya.agent.gene_diagnostics.v2"
TERMINAL = frozenset({"Terminal_exon", "Downstream_of_terminal_exon"})
LIMITS = {"count_rows": 50_000, "catalogue_rows": 2_000,
          "registry_rows": 256, "output_units": 256, "output_records": 4_096,
          "input_text_bytes": 4 * 1024 * 1024, "input_nodes": 500_000,
          "output_json_bytes": 1024 * 1024}
JSON_ENCODING = {"ensure_ascii": True, "sort_keys": True,
                 "separators": (",", ":"), "allow_nan": False}
MAX_EXACT_COUNT = 2**53 - 1


class DiagnosticValidationError(ValueError):
    """A structured failure; no partial diagnostic result is returned."""

    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")

    def as_dict(self):
        return {"status": "validation_error", "code": self.code, "detail": self.detail}


def _fail(code, detail):
    raise DiagnosticValidationError(code, detail)


def _text(value, name, *, nullable=False):
    if isinstance(value, str) and len(value) > 512:
        _fail("invalid_text", name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if nullable:
            return None
        _fail("missing_identity", name)
    if not isinstance(value, str) or len(value) > 512:
        _fail("invalid_text", name)
    return value  # In particular, preserve 'NA', '001', and barcode suffixes.


def _integer(value, name, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_EXACT_COUNT:
        _fail("invalid_integer", name)
    return value


def _boolean(value, name):
    if type(value) is not bool:
        _fail("invalid_boolean", name)
    return value


def _number(value, name):
    if type(value) not in (int, float):
        _fail("invalid_number", name)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _fail("invalid_number", name)
    return value


def _records(rows, name, maximum):
    # Lists/tuples make bounds explicit; generators are deliberately unsupported.
    if not isinstance(rows, (list, tuple)):
        _fail("invalid_records", name)
    if len(rows) > maximum:
        _fail("limit_exceeded", name)
    if any(not isinstance(row, dict) for row in rows):
        _fail("invalid_record", name)
    return rows


def _get(row, name):
    if name not in row:
        _fail("missing_field", name)
    return row[name]


def _identity(row, samples, *, cell=False):
    sample = _text(_get(row, "sample_id"), "sample_id")
    if sample not in samples:
        _fail("sample_binding", sample)
    gene = _text(_get(row, "gene_id"), "gene_id", nullable=not cell)
    strand = _get(row, "strand")
    if strand not in ("+", "-"):
        if gene is not None or strand not in (None, "", "."):
            _fail("invalid_strand", str(strand))
    if cell:
        barcode = _text(_get(row, "cell_barcode"), "cell_barcode")
        return sample, barcode, gene, strand
    return sample, gene, strand


def _sum(values):
    total = sum(values)
    if total > MAX_EXACT_COUNT:
        _fail("count_sum_overflow", "unit total exceeds exact-count limit")
    return total



def _check_input_budget(arguments, limits):
    """Count all keys/text, including unused extras, without copying input trees."""
    text_bytes = nodes = 0

    def visit(value, depth):
        nonlocal text_bytes, nodes
        nodes += 1
        if nodes > limits["input_nodes"]:
            _fail("limit_exceeded", "input_nodes")
        if depth > 8:
            _fail("limit_exceeded", "input_depth (maximum 8)")
        if isinstance(value, str):
            # Every codepoint needs >=1 escaped ASCII byte. Slice only small
            # chunks, including for an already-created very large caller string.
            if len(value) > limits["input_text_bytes"] - text_bytes:
                _fail("limit_exceeded", "input_text_bytes")
            for start in range(0, len(value), 4096):
                text_bytes += len(json.dumps(value[start:start+4096], ensure_ascii=True)) - 2
                if text_bytes > limits["input_text_bytes"]:
                    _fail("limit_exceeded", "input_text_bytes")
        elif isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    _fail("invalid_input_key", "JSON object keys must be strings")
                visit(key, depth + 1)
                visit(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item, depth + 1)
        elif value is not None and type(value) not in (int, float, bool):
            _fail("invalid_input_value", "only JSON-like parsed records supported")
    visit(arguments, 0)


class _OutputBudget:
    """Exact canonical size accounting before each bounded record is retained.

    Never serialize the whole growing result. Start with the full empty-list
    envelope (including provenance/scope); additions replace [] with [row,...].
    """

    def __init__(self, result, maximum):
        self.result = result
        self.maximum = maximum
        self.used = self._size(result, maximum)

    @staticmethod
    def _size(value, remaining):
        total = 0
        for chunk in json.JSONEncoder(**JSON_ENCODING).iterencode(value):
            total += len(chunk)  # ensure_ascii => UTF-8 byte count equals length
            if total > remaining:
                _fail("limit_exceeded", "output_json_bytes")
        return total

    def append(self, field, row):
        comma = int(bool(self.result[field]))
        size = self._size(row, self.maximum - self.used - comma)
        self.used += size + comma
        self.result[field].append(row)


def diagnose_gene_units(count_rows, catalogue_rows, unit_registry, policy, provenance):
    """Return new JSON-safe records, or raise DiagnosticValidationError.

    Completeness, sample binding, files/hashes and producer state are declarations
    made by the caller. Only the supplied records and their consistency are checked.
    No omitted site is zero unless the required complete sparse scope is declared.
    """
    if not isinstance(policy, dict) or not isinstance(provenance, dict):
        _fail("invalid_object", "policy and provenance must be objects")
    if policy.get("quantification_mode") != "terminal-aware":
        _fail("unsupported_mode", "terminal-aware required")
    for name in ("complete_locus_counts", "complete_locus_catalogue", "sparse_omission_is_zero"):
        if policy.get(name) is not True:
            _fail("incomplete_scope", name)
    if policy.get("sample_binding") != "explicit_sample_id":
        _fail("sample_binding", "explicit_sample_id declaration required")
    samples = policy.get("sample_ids")
    if not isinstance(samples, list) or not samples or len(samples) > 1000:
        _fail("sample_binding", "bounded nonempty sample_ids list required")
    samples = [_text(s, "sample_ids") for s in samples]
    if len(set(samples)) != len(samples):
        _fail("sample_binding", "duplicate sample_id")
    samples = set(samples)
    thresholds = {}
    for name in ("general_mispriming_threshold", "intronic_mispriming_threshold"):
        value = _number(_get(policy, name), name)
        if not 0 <= value <= 1:
            _fail("invalid_threshold", name)
        thresholds[name] = value
    psi_min = _integer(_get(policy, "psi_min_gene_umis"), "psi_min_gene_umis")
    limits = dict(LIMITS)
    overrides = policy.get("limits", {})
    if (not isinstance(overrides, dict) or len(overrides) > len(limits)
            or set(overrides) - set(limits)):
        _fail("invalid_limits", "only documented limits may be reduced")
    for name, value in overrides.items():
        _integer(value, name, minimum=1)
        if value > limits[name]:
            _fail("invalid_limits", "limits may only be reduced")
        limits[name] = value
    if len(provenance) > 32:
        _fail("invalid_provenance", "at most 32 scalar entries")
    for key, value in provenance.items():
        _text(key, "provenance key")
        if value is not None and type(value) not in (str, int, float, bool):
            _fail("invalid_provenance", "only scalar JSON values supported")
        if isinstance(value, str) and len(value) > 4096:
            _fail("invalid_provenance", "value too long")
        if type(value) in (int, float):
            _number(value, "provenance numeric value")
    try:
        if len(json.dumps(provenance, allow_nan=False).encode()) > 65_536:
            _fail("invalid_provenance", "provenance too large")
    except (ValueError, TypeError, OverflowError) as exc:
        if isinstance(exc, DiagnosticValidationError):
            raise
        _fail("invalid_provenance", "not finite scalar JSON")

    catalogue_rows = _records(catalogue_rows, "catalogue_rows", limits["catalogue_rows"])
    count_rows = _records(count_rows, "count_rows", limits["count_rows"])
    registry = _records(unit_registry if unit_registry is not None else [],
                        "registry_rows", limits["registry_rows"])
    _check_input_budget({"count_rows": count_rows, "catalogue_rows": catalogue_rows,
                         "unit_registry": unit_registry, "policy": policy,
                         "provenance": provenance}, limits)
    result = {"schema": SCHEMA, "interpretation": "descriptive_arithmetic_only_not_native_exports",
            "provenance": {"trust": "caller_supplied_not_certified", "declared": deepcopy(provenance),
                           "not_verified": ["file_hashes", "producer_completion", "run_commit",
                                            "external_sample_binding", "external_scope_completeness"]},
            "scope": {"quantification_mode": "terminal-aware", "sample_ids": sorted(samples),
                      "complete_locus_counts": True, "complete_locus_catalogue": True,
                      "sparse_omission_is_zero": True, "sample_binding": "explicit_sample_id",
                      **thresholds, "psi_min_gene_umis": psi_min,
                      "declarations_source": "caller", "limits": limits},
            "denominator_rows": [], "metric_rows": [],
            "site_audit": [], "unassigned_count_audit": []}
    output_budget = _OutputBudget(result, limits["output_json_bytes"])
    catalogue = {}
    genes = defaultdict(list)
    audit = result["site_audit"]
    for row in catalogue_rows:
        sample, gene, strand = _identity(row, samples)
        peak = _text(_get(row, "peak_name"), "peak_name")
        key = sample, peak
        if key in catalogue:
            _fail("duplicate_catalogue_peak", f"{sample}/{peak}; duplicate or conflicting mapping")
        feature = _text(_get(row, "feature"), "feature")
        ratio = _get(row, "misprimed_ratio")
        if ratio is not None:
            _number(ratio, "misprimed_ratio")
            if not 0 <= ratio <= 1:
                _fail("invalid_ratio", peak)
        general = _boolean(_get(row, "general_mispriming_pass"), "general_mispriming_pass")
        intronic = _boolean(_get(row, "intronic_mispriming_pass"), "intronic_mispriming_pass")
        passed = _boolean(_get(row, "mispriming_pass"), "mispriming_pass")
        # Validate supplied native flags, never replace them or reassign context.
        if (general != (ratio is not None and ratio <= thresholds["general_mispriming_threshold"])
                or intronic != (ratio is not None and ratio <= thresholds["intronic_mispriming_threshold"])
                or passed != (intronic if feature == "Intron" else general)):
            _fail("inconsistent_native_flags", peak)
        distal = _get(row, "is_distal_peak")
        if distal is not None:
            _boolean(distal, "is_distal_peak")
        distance = _get(row, "distance_to_stop_codon")
        if distance is not None and type(distance) not in (int, float):
            _fail("invalid_distance_type", peak)
        if type(distance) is int and abs(distance) > MAX_EXACT_COUNT:
            _fail("invalid_distance_type", "integer distance out of range")
        eligible = gene is not None and feature in TERMINAL and general
        n = _get(row, "n_peaks_in_gene")
        order = _get(row, "peak_order_in_gene")
        for name, value in (("n_peaks_in_gene", n), ("peak_order_in_gene", order)):
            if value is not None:
                _integer(value, name, minimum=1)
        if eligible and (n is None or order is None):
            _fail("missing_terminal_membership", peak)
        meta = dict(sample=sample, peak=peak, gene=gene, strand=strand,
                    feature=feature, ratio=ratio, passed=passed, eligible=eligible,
                    distal=distal, distance=distance, n=n, order=order)
        catalogue[key] = meta
        if gene is not None:
            genes[sample, gene, strand].append(meta)
        output_budget.append("site_audit", {"sample_id": sample, "peak_name": peak, "gene_id": gene,
                      "strand": strand, "feature": feature,
                      "status": "unassigned" if gene is None else (
                          "retained" if passed else "not_evaluable" if ratio is None else "failed_filter")})
    # Validate membership once per gene, not for every cell.
    gene_info = {}
    for key, metas in genes.items():
        terminal = [m for m in metas if m["eligible"]]
        introns = [m for m in metas if m["feature"] == "Intron"]
        if terminal and (any(m["n"] != len(terminal) for m in terminal)
                         or sorted(m["order"] for m in terminal) != list(range(1, len(terminal) + 1))):
            _fail("inconsistent_terminal_membership", str(key))
        reason, distal_peak = None, None
        if not terminal:
            reason = "no_eligible_terminal"
        elif any(m["distance"] is None or not math.isfinite(m["distance"]) for m in terminal):
            reason = "unavailable_distal_order"
        else:
            max_distance = max(m["distance"] for m in terminal)
            maxima = [m for m in terminal if m["distance"] == max_distance]
            flagged = [m for m in terminal if m["distal"] is True]
            if len(maxima) > 1 or len(flagged) > 1:
                reason = "distal_tie"
            elif len(flagged) != 1 or flagged[0]["peak"] != maxima[0]["peak"]:
                reason = "inconsistent_native_distal"
            else:
                distal_peak = flagged[0]["peak"]
        gene_info[key] = terminal, introns, reason, distal_peak, sum(m["passed"] for m in introns)

    values = defaultdict(dict)
    seen_counts = set()
    unassigned = defaultdict(lambda: {"count_rows": 0, "umi_count": 0})
    for row in count_rows:
        sample = _text(_get(row, "sample_id"), "sample_id")
        if sample not in samples:
            _fail("sample_binding", sample)
        peak = _text(_get(row, "peak_name"), "peak_name")
        cell = _text(_get(row, "cell_barcode"), "cell_barcode")
        count = _integer(_get(row, "umi_count"), "umi_count")
        key = sample, cell, peak
        if key in seen_counts:
            _fail("duplicate_count_row", str(key))
        seen_counts.add(key)
        if (sample, peak) not in catalogue:
            _fail("unknown_peak_mapping", f"{sample}/{peak}")
        m = catalogue[sample, peak]
        if count and (m["ratio"] is None or (m["feature"] != "Intron" and not m["passed"])):
            _fail("inconsistent_native_count_population", peak)
        if m["gene"] is None:
            unassigned[sample]["count_rows"] += 1
            unassigned[sample]["umi_count"] = _sum([unassigned[sample]["umi_count"], count])
            continue
        unit_key = sample, cell, m["gene"], m["strand"]
        if unit_key not in values and len(values) >= limits["output_units"]:
            _fail("limit_exceeded", "output_units")
        values[unit_key][peak] = count
    seen_registry = set()
    for row in registry:
        key = _identity(row, samples, cell=True)
        if key in seen_registry:
            _fail("duplicate_registry_unit", str(key))
        seen_registry.add(key)
        if (key[0], key[2], key[3]) not in genes:
            _fail("unknown_registry_gene", str(key))
        if key not in values and len(values) >= limits["output_units"]:
            _fail("limit_exceeded", "output_units")
        values.setdefault(key, {})
    if len(values) > limits["output_units"]:
        _fail("limit_exceeded", "output_units")
    # One denominator and four metric records per unit, plus catalogue/sample audit.
    if 5 * len(values) + len(audit) + len(samples) > limits["output_records"]:
        _fail("limit_exceeded", "output_records")

    for (sample, cell, gene, strand), counts in sorted(values.items()):
        terminal, introns, distal_reason, distal_peak, retained_introns = gene_info[sample, gene, strand]
        raw_s = _sum(counts.values())
        retained_s = _sum(c for peak, c in counts.items() if catalogue[sample, peak]["passed"])
        t = _sum(c for peak, c in counts.items() if catalogue[sample, peak]["eligible"])
        i = _sum(c for peak, c in counts.items() if catalogue[sample, peak]["feature"] == "Intron" and catalogue[sample, peak]["passed"])
        failed_i = _sum(c for peak, c in counts.items() if catalogue[sample, peak]["feature"] == "Intron" and not catalogue[sample, peak]["passed"])
        d = counts.get(distal_peak, 0) if distal_peak is not None else None
        identity = dict(sample_id=sample, cell_barcode=cell, gene_id=gene, strand=strand)
        structure_and_depth = {
            "has_multiple_eligible_terminal_sites": len(terminal) >= 2,
            "has_unique_distal_metadata": distal_reason is None,
            "positive_terminal_denominator": t > 0,
            "terminal_depth_passes_native_mask": t >= psi_min,
            "recipe_native_comparable_distal_value_available": (
                len(terminal) >= 2 and distal_reason is None and t > 0 and t >= psi_min)}
        output_budget.append("denominator_rows", {**identity, "S_assigned_raw": raw_s, "S_retained": retained_s,
                                 "T": t, "I": i, "D": d, "O_retained": retained_s-t-i,
                                 "failed_intronic_audit_umis": failed_i,
                                 "eligible_terminal_site_count": len(terminal),
                                 "retained_intronic_site_count": retained_introns,
                                 "unique_distal_peak_name": distal_peak,
                                 **structure_and_depth})
        definitions = [
            ("distal_fraction_all_assigned", d, raw_s, "all_assigned_including_failed_intronic_audit"),
            ("distal_fraction_retained_all_contexts", d, retained_s, "native_pass_all_contexts"),
            ("distal_fraction_terminal_conditional", d, t, "native_eligible_terminal"),
            ("intronic_fraction_intronic_plus_terminal", i, i+t, "native_pass_intronic_plus_terminal")]
        for name, numerator, denominator, population in definitions:
            reasons = []
            terminal_metric = name == "distal_fraction_terminal_conditional"
            intron_metric = name == "intronic_fraction_intronic_plus_terminal"
            if intron_metric:
                if not terminal:
                    reasons.append("no_eligible_terminal")
                if introns and not retained_introns:
                    reasons.append("no_retained_intronic_evidence")
            elif distal_reason:
                reasons.append(distal_reason)
            if denominator == 0:
                reasons.append("zero_denominator")
            raw_value = numerator / denominator if numerator is not None and denominator else None
            if terminal_metric and len(terminal) >= 2 and t < psi_min:
                reasons.append("below_native_psi_threshold")
            status = "unavailable" if reasons else "computed"
            value = None if reasons else raw_value
            notes = []
            if intron_metric and not introns:
                notes.append("no_intronic_candidate_not_absence_of_biology")
            if not intron_metric and t < psi_min:
                notes.append("low_terminal_depth")
            if terminal_metric and len(terminal) == 1 and not reasons:
                status = "single_terminal_control"
                notes.append("single_terminal_structural_control")
            metric = {**identity, "metric_name": name, "numerator": numerator,
                      "denominator": denominator, "value_or_null": value,
                      "status": status, "reason_codes": reasons, "notes": notes,
                      "site_population": population}
            if terminal_metric:
                metric["raw_arithmetic_value_or_null"] = raw_value
                metric["recipe_native_comparable_distal_value_or_null"] = (
                    value if structure_and_depth["recipe_native_comparable_distal_value_available"] else None)
            output_budget.append("metric_rows", metric)
    result["metric_rows"].sort(key=lambda r: (r["sample_id"], r["cell_barcode"],
                                              r["gene_id"], r["strand"], r["metric_name"]))
    result["site_audit"].sort(key=lambda r: (r["sample_id"], r["peak_name"]))
    for sample in sorted(samples):
        output_budget.append("unassigned_count_audit", {"sample_id": sample, **unassigned[sample]})
    return result
