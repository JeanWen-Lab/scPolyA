"""Per-PAS read-assignment diagnostics written next to the peak counts.

For every featureCounts-annotated record the collector knows the final
assignment (after the read rule) and featureCounts' own status (``XS``). Each
record is attributed to every same-strand PAS window its aligned blocks
overlap, with one reason per (record, window). The reason is decided in this
order:

1. Unified rule: the shared eligibility decision comes first. A record the
   rule rejects gets the rule's reason (``unmapped``, ``secondary``,
   ``supplementary``, ``mapq``, ``multimapper_policy``, in that order),
   whatever featureCounts said about it, so an ineligible read is never
   reported as an assignment outcome.
2. ``assigned``: counted at this window.
3. Only for eligible records (unified) or for every record (legacy, where
   featureCounts itself is the counting rule), featureCounts' status:
   ``three_prime_end_in_other_window`` (counted at another window that holds
   its 3' end; ``three_prime_end`` assignment), ``three_prime_end_outside_window``
   (touches a window, 3' end in none), ``ambiguity`` (more than one window;
   legacy or ``overlap`` assignment), ``multimapping`` (NH>1, legacy),
   ``mapq``/``secondary``/``unmapped`` (legacy featureCounts filters) and
   ``other``.

featureCounts' own ``.summary`` totals are kept unchanged, as a separate
layer, under ``featurecounts_summary`` in the JSON summaries.

Sums over windows are incidences, not distinct reads: one read can touch two
windows. The record-level summary counts each record once and adds
``no_feature`` for records that touch no window. Multimapper support (NH>1 or
``mm:i:1``) and poly(A)-tail support are counted over primary mapped records.

The multimapper/tail support columns describe every primary mapped record
whatever its MAPQ or multimapper status (they exist to show how much of a
window's support the rule removes).

Descriptive columns for resolved catalogues:

- ``nearest_window_gap``: uncovered nt between this window and the closest
  same-strand window of the counting GTF (any gene; <= 0 means touching or
  overlapping), and ``abutting_fragment`` (gap <= 2 nt). Both are pure window
  geometry: they read no alignments, so the read rule cannot affect them, and
  they are empty when the PAS has no same-strand neighbour on its contig.
- ``tail_end_mode`` / ``tail_end_mode_reads``: the most frequent templated
  3' end (1-based; last aligned base before the tail, transcript orientation)
  among poly(A)-tail reads that are eligible for discovery and whose tail end
  lies inside this window; empty when there are none. Eligible means the
  unified rule's predicate, or under legacy the poly(A) extract prefilter
  (mapped, MAPQ >= --minMAPQ) restricted to primary records. Ties go to the
  lower coordinate. It is a position inside the current window, so a
  different threshold or merge distance (other windows) can change it.
"""
from __future__ import annotations

import bisect
import csv
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping

import pysam

from utils.polya_extract import TAIL_RETAINED, polya_tail_status
from utils.read_eligibility import ReadRule, is_multimapper

ASSIGNMENT_DIAGNOSTICS_SCHEMA = "scpolya.peak_assignment_diagnostics.v1"

REASON_ASSIGNED = "assigned"
REASON_OTHER_WINDOW = "three_prime_end_in_other_window"
REASON_OUTSIDE_WINDOW = "three_prime_end_outside_window"
REASON_NO_FEATURE = "no_feature"

FEATURECOUNTS_REASONS = {
    "Unassigned_Ambiguity": "ambiguity",
    "Unassigned_MultiMapping": "multimapping",
    "Unassigned_MappingQuality": "mapq",
    "Unassigned_Secondary": "secondary",
    "Unassigned_Unmapped": "unmapped",
    "Unassigned_NoFeatures": REASON_NO_FEATURE,
}

PER_PAS_REASONS = (
    REASON_ASSIGNED,
    REASON_OTHER_WINDOW,
    REASON_OUTSIDE_WINDOW,
    "ambiguity",
    "multimapping",
    "mapq",
    "secondary",
    "supplementary",
    "multimapper_policy",
    "unmapped",
    "other",
)
SUPPORT_COLUMNS = (
    "primary_overlap_reads",
    "multimapper_overlap_reads",
    "tail_reads",
    "multimapper_tail_reads",
)
COUNT_COLUMNS = ("overlap_records", *(f"{r}_reads" for r in PER_PAS_REASONS), *SUPPORT_COLUMNS)
UNIT_COLUMNS = ("peak_name", *COUNT_COLUMNS, "tail_end_positions")
SAMPLE_COLUMNS = (
    "peak_name",
    "assigned_umis",
    *COUNT_COLUMNS,
    "multimapper_overlap_fraction",
    "multimapper_tail_fraction",
    "nearest_window_gap",
    "abutting_fragment",
    "tail_end_mode",
    "tail_end_mode_reads",
)
ABUTTING_MAX_GAP = 2


def read_peak_windows(gtf: Path, *, feature_type: str = "peak", group_attr: str = "peak_name"):
    """GTF windows as 0-based half-open intervals keyed by (contig, strand)."""

    windows: dict[tuple[str, str], list[tuple[int, int, str]]] = defaultdict(list)
    with open(gtf) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 9 or fields[2] != feature_type:
                continue
            name = None
            for attribute in fields[8].split(";"):
                parts = attribute.strip().split(None, 1)
                if len(parts) == 2 and parts[0] == group_attr:
                    name = parts[1].strip().strip('"')
                    break
            if name is None:
                continue
            windows[(fields[0], fields[6])].append((int(fields[3]) - 1, int(fields[4]), name))
    return WindowIndex(windows)


class WindowIndex:
    def __init__(self, windows: Mapping[tuple[str, str], list[tuple[int, int, str]]]):
        self._index = {}
        self.names: list[str] = []
        self.bounds: dict[str, tuple[int, int]] = {}
        for key, rows in windows.items():
            rows = sorted(rows)
            starts = [s for s, _, _ in rows]
            ends = [e for _, e, _ in rows]
            prefix_max = []
            best = -1
            for end in ends:
                best = max(best, end)
                prefix_max.append(best)
            self._index[key] = (starts, ends, prefix_max, [n for _, _, n in rows])
            self.names.extend(n for _, _, n in rows)
            self.bounds.update((n, (s, e)) for s, e, n in rows)
        self.gaps: dict[str, int] = {}
        for starts, ends, prefix_max, names in self._index.values():
            for i, name in enumerate(names):
                gaps = []
                if i > 0:
                    gaps.append(starts[i] - prefix_max[i - 1])
                if i + 1 < len(names):
                    gaps.append(starts[i + 1] - ends[i])
                if gaps:
                    self.gaps[name] = min(gaps)

    def overlapping(self, contig: str, strand: str, blocks: Iterable[tuple[int, int]]) -> list[str]:
        entry = self._index.get((contig, strand))
        if entry is None:
            return []
        starts, ends, prefix_max, names = entry
        hits: list[str] = []
        for block_start, block_end in blocks:
            hi = bisect.bisect_left(starts, block_end)
            lo = bisect.bisect_right(prefix_max, block_start, 0, hi)
            for i in range(lo, hi):
                if ends[i] > block_start and names[i] not in hits:
                    hits.append(names[i])
        return hits


class AssignmentDiagnostics:
    def __init__(self, windows: WindowIndex, *, read_rule: ReadRule | None, minpolyA: int, nonA_allowed: int):
        self.windows = windows
        self.read_rule = read_rule if read_rule is not None and read_rule.unified else None
        # Legacy discovery prefilter for tail-end support (poly(A) extract).
        self.legacy_min_mapq = int(read_rule.min_mapq) if read_rule is not None else 0
        self.minpolyA = int(minpolyA)
        self.nonA_allowed = int(nonA_allowed)
        self.reset()

    def reset(self) -> None:
        self.per_pas: dict[str, Counter] = defaultdict(Counter)
        self.tail_ends: dict[str, Counter] = defaultdict(Counter)
        self.records = Counter()

    def _rule_reason(self, read: pysam.AlignedSegment) -> str | None:
        return self.read_rule.rejection_reason(read) if self.read_rule else None

    def _discovery_eligible(self, read: pysam.AlignedSegment, rule_reason: str | None) -> bool:
        if self.read_rule is not None:
            return rule_reason is None
        return (
            not (read.is_unmapped or read.is_secondary or read.is_supplementary)
            and read.mapping_quality >= self.legacy_min_mapq
        )

    def observe(self, read: pysam.AlignedSegment, feature: str) -> None:
        rule_reason = self._rule_reason(read)
        hits = []
        if not read.is_unmapped:
            strand = "-" if read.is_reverse else "+"
            hits = self.windows.overlapping(read.reference_name, strand, read.get_blocks())
        # Reason order: shared rule, assignment, then featureCounts' status.
        if rule_reason is not None:
            reason = rule_reason
            feature = ""  # the sidecar never counts a rejected record
        elif feature:
            reason = REASON_ASSIGNED
        else:
            status = read.get_tag("XS") if read.has_tag("XS") else ""
            reason = FEATURECOUNTS_REASONS.get(status, "other")
            if reason == REASON_NO_FEATURE and hits:
                reason = REASON_OUTSIDE_WINDOW  # touches a window, 3' end outside
        self.records[reason] += 1
        if not hits:
            return
        primary = not (read.is_secondary or read.is_supplementary)
        multimapper = primary and is_multimapper(read)
        tail = primary and polya_tail_status(read, self.nonA_allowed, self.minpolyA) == TAIL_RETAINED
        tail_end = None
        if tail and self._discovery_eligible(read, rule_reason):
            # 1-based last templated base before the tail, in transcript orientation.
            tail_end = read.reference_start + 1 if read.is_reverse else read.reference_end
        for name in hits:
            if feature == name:
                pas_reason = REASON_ASSIGNED
            elif feature:
                pas_reason = REASON_OTHER_WINDOW
            else:
                pas_reason = reason if reason in PER_PAS_REASONS else "other"
            counter = self.per_pas[name]
            counter["overlap_records"] += 1
            counter[f"{pas_reason}_reads"] += 1
            if primary:
                counter["primary_overlap_reads"] += 1
                counter["multimapper_overlap_reads"] += int(multimapper)
                counter["tail_reads"] += int(tail)
                counter["multimapper_tail_reads"] += int(multimapper and tail)
            if tail_end is not None:
                start, end = self.windows.bounds[name]
                if start < tail_end <= end:
                    self.tail_ends[name][tail_end] += 1

    def write(self, output_tsv: Path, summary_json: Path, featurecounts_summary: Mapping[str, int]) -> None:
        output_tsv = Path(output_tsv)
        temporary = output_tsv.with_name(f".{output_tsv.name}.tmp")
        with gzip.open(temporary, "wt", encoding="ascii", compresslevel=1) as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(UNIT_COLUMNS)
            for name in sorted(self.per_pas):
                counter = self.per_pas[name]
                positions = ";".join(f"{pos}:{n}" for pos, n in sorted(self.tail_ends[name].items()))
                writer.writerow([name, *(int(counter[c]) for c in COUNT_COLUMNS), positions])
        temporary.replace(output_tsv)
        _write_json(summary_json, {
            "schema": ASSIGNMENT_DIAGNOSTICS_SCHEMA,
            "read_rule": _rule_payload(self.read_rule),
            "record_reasons": dict(sorted(self.records.items())),
            "featurecounts_summary": dict(featurecounts_summary),
        })


def _rule_payload(read_rule: ReadRule | None) -> dict:
    if read_rule is None:
        return {"count_read_rule": "legacy"}
    return {
        "count_read_rule": read_rule.mode,
        "multimapper_policy": read_rule.multimapper_policy,
        "minMAPQ": read_rule.min_mapq,
        "count_assignment": read_rule.assignment,
    }


def _write_json(path: Path, payload: Mapping) -> None:
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def read_featurecounts_summary(path: Path) -> dict[str, int]:
    """Parse a featureCounts ``.summary`` file (one BAM column)."""

    totals: dict[str, int] = {}
    with open(path) as handle:
        next(handle, None)
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            if len(fields) >= 2 and fields[1].strip():
                totals[fields[0]] = int(fields[1])
    return totals


def unit_diagnostics_paths(sidecar: Path) -> tuple[Path, Path]:
    """Per-PAS TSV and record summary JSON stored next to a peak sidecar."""

    sidecar = Path(sidecar)
    stem = sidecar.name
    for suffix in (".tsv.gz", ".gz"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return (
        sidecar.with_name(f"{stem}.assignment.tsv.gz"),
        sidecar.with_name(f"{stem}.assignment_summary.json"),
    )


def write_sample_assignment_report(
    unit_sidecars: Iterable[Path],
    *,
    assigned_umis: Mapping[str, int],
    output_tsv: Path,
    summary_json: Path,
    sample_id: str,
    windows: WindowIndex | None = None,
) -> dict:
    """Write the sample report from the diagnostics beside each unit sidecar.

    Any earlier report is removed first, so a failed re-summary never leaves
    a stale report beside new counts; missing unit diagnostics are an error
    (they are a required annotate_bams output).
    """

    for stale in (output_tsv, summary_json):
        Path(stale).unlink(missing_ok=True)
    unit_pairs = []
    for sidecar in unit_sidecars:
        pair = unit_diagnostics_paths(sidecar)
        missing = [str(path) for path in pair if not path.exists()]
        if missing:
            raise FileNotFoundError(
                f"{sample_id}: assignment diagnostics missing for {Path(sidecar).name}: "
                f"{', '.join(missing)}. Re-run annotate_bams for this unit."
            )
        unit_pairs.append(pair)
    return merge_sample_diagnostics(
        unit_pairs,
        assigned_umis=assigned_umis,
        output_tsv=output_tsv,
        summary_json=summary_json,
        sample_id=sample_id,
        windows=windows,
    )


def merge_sample_diagnostics(
    unit_pairs: Iterable[tuple[Path, Path]],
    *,
    assigned_umis: Mapping[str, int],
    output_tsv: Path,
    summary_json: Path,
    sample_id: str,
    windows: WindowIndex | None = None,
) -> dict:
    """Combine unit diagnostics, add assigned UMIs per PAS and run totals.

    With ``windows`` (the counting GTF) every counting PAS gets a row, and the
    gap/abutting columns are filled.
    """

    per_pas: dict[str, Counter] = defaultdict(Counter)
    tail_ends: dict[str, Counter] = defaultdict(Counter)
    records = Counter()
    featurecounts = Counter()
    read_rule = None
    for tsv, summary in unit_pairs:
        with gzip.open(tsv, "rt", encoding="ascii") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                counter = per_pas[row["peak_name"]]
                for column in COUNT_COLUMNS:
                    counter[column] += int(row[column])
                for item in filter(None, row.get("tail_end_positions", "").split(";")):
                    pos, n = item.split(":")
                    tail_ends[row["peak_name"]][int(pos)] += int(n)
        payload = json.loads(Path(summary).read_text())
        records.update(payload["record_reasons"])
        featurecounts.update(payload["featurecounts_summary"])
        read_rule = payload["read_rule"]
    for name in assigned_umis:
        per_pas.setdefault(name, Counter())
    for name in windows.names if windows is not None else ():
        per_pas.setdefault(name, Counter())

    output_tsv = Path(output_tsv)
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_tsv.with_name(f".{output_tsv.name}.tmp")
    with gzip.open(temporary, "wt", encoding="ascii", compresslevel=1) as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(SAMPLE_COLUMNS)
        for name in sorted(per_pas):
            counter = per_pas[name]
            gap = windows.gaps.get(name) if windows is not None else None
            # Mode of tail ends; ties go to the lower coordinate.
            mode = min(tail_ends[name].items(), key=lambda kv: (-kv[1], kv[0])) if tail_ends[name] else None
            writer.writerow([
                name,
                int(assigned_umis.get(name, 0)),
                *(int(counter[c]) for c in COUNT_COLUMNS),
                _fraction(counter["multimapper_overlap_reads"], counter["primary_overlap_reads"]),
                _fraction(counter["multimapper_tail_reads"], counter["tail_reads"]),
                "" if gap is None else gap,
                "" if windows is None else str(gap is not None and gap <= ABUTTING_MAX_GAP),
                "" if mode is None else mode[0],
                "" if mode is None else mode[1],
            ])
    temporary.replace(output_tsv)

    zero_umi_pas = sorted(
        name for name, counter in per_pas.items()
        if not assigned_umis.get(name) and counter["overlap_records"]
    )
    summary = {
        "schema": ASSIGNMENT_DIAGNOSTICS_SCHEMA,
        "sample_id": sample_id,
        "read_rule": read_rule,
        "record_reasons": dict(sorted(records.items())),
        "featurecounts_summary": dict(sorted(featurecounts.items())),
        "assigned_umis_total": int(sum(assigned_umis.values())),
        "pas_with_assignment_rows": len(per_pas),
        "pas_with_overlapping_reads_but_zero_umis": len(zero_umi_pas),
        "incidence_totals": {
            f"{r}_reads": int(sum(c[f"{r}_reads"] for c in per_pas.values())) for r in PER_PAS_REASONS
        },
    }
    _write_json(summary_json, summary)
    return summary


def _fraction(numerator: int, denominator: int) -> str:
    return "" if not denominator else repr(numerator / denominator)


def format_assignment_summary(summary: Mapping) -> str:
    reasons = summary["record_reasons"]
    total = sum(reasons.values())
    parts = ", ".join(f"{k}={v:,}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
    return (
        f"{summary['sample_id']} read assignment ({summary['read_rule']}): "
        f"{total:,} records; {parts}. Assigned UMIs={summary['assigned_umis_total']:,}; "
        f"PAS with overlapping reads but zero UMIs="
        f"{summary['pas_with_overlapping_reads_but_zero_umis']:,}."
    )

