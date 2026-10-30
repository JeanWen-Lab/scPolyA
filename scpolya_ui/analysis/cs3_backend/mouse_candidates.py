"""CS3 r2 preparation helpers: strict boundaries, evidence axes, and reference joins.

This module is preparation-only.  It reads no scientific source file, performs no
candidate aggregation, and makes no biological claim.  The official PolyA_DB 3.2
mouse payload is mm9 with **unresolved Position base**; therefore it must not be
joined to the mm10/GRCm38 candidate catalogue.
"""
from __future__ import annotations

import bisect
import math
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

HELPER_INTERFACE_VERSION = "r3.1"
UNASSESSABLE = "unassessable"
ABSENT = "absent"
PROXIMAL = "proximal"

_INTEGER_TEXT = re.compile(r"^[+-]?[0-9]+$")
_DNA = set("ACGTRYSWKMBDHVN")
_STRICT_DNA = set("ACGT")
_COMP = str.maketrans("ACGTRYSWKMBDHVNacgtryswkmbdhvn", "TGCAYRSWMKVHDBNtgcayrswmkvhdbn")


def strict_integer(value: object, field_name: str) -> int:
    """Accept only an int or an exact integer text token; never truncate."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field_name} must be an integer, not bool/null")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER_TEXT.fullmatch(value):
        return int(value)
    raise ValueError(f"{field_name} must be an integer without truncation")


def finite_nonnegative(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be finite and nonnegative")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field_name} must be finite and nonnegative")
    return result


def strict_strand(value: object, field_name: str = "strand") -> str:
    if value not in ("+", "-"):
        raise ValueError(f"{field_name} must be '+' or '-'")
    return value


def nonempty_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a nonempty string")
    return value


def validate_interval(start: object, end: object) -> tuple[int, int]:
    s = strict_integer(start, "start")
    e = strict_integer(end, "end")
    if s < 0 or not s < e:
        raise ValueError("require 0 <= start < end")
    return s, e


def pas_boundary_0based(start: object, end: object, strand: object) -> int:
    """Oriented last-templated-base *proxy*, not a measured cleavage point."""
    s, e = validate_interval(start, end)
    return e - 1 if strict_strand(strand) == "+" else s


def pas_boundary_1based(start: object, end: object, strand: object) -> int:
    return pas_boundary_0based(start, end, strand) + 1


@dataclass(frozen=True)
class CandidateCore:
    peak_name: str
    chrom: str
    start: int
    end: int
    strand: str
    gene_id: str | None = None
    gene_name: str | None = None
    transcript_id: str | None = None
    source_ordinal: int | None = None


def _optional_text(row: Mapping[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text or null")
    return value


def candidate_core(row: Mapping[str, object]) -> CandidateCore:
    """Validate site identity while keeping gene annotation missingness explicit."""
    for key in ("peak_name", "chrom", "start", "end", "strand"):
        if key not in row:
            raise ValueError(f"missing required site field: {key}")
    source_ordinal = row.get("source_ordinal")
    start, end = validate_interval(row["start"], row["end"])
    return CandidateCore(
        peak_name=nonempty_text(row["peak_name"], "peak_name"),
        chrom=nonempty_text(row["chrom"], "chrom"),
        start=start,
        end=end,
        strand=strict_strand(row["strand"]),
        gene_id=_optional_text(row, "gene_id"),
        gene_name=_optional_text(row, "gene_name"),
        transcript_id=_optional_text(row, "transcript_id"),
        source_ordinal=None if source_ordinal is None else strict_integer(source_ordinal, "source_ordinal"),
    )


def nearest_same_strand_distance(
    candidate_chrom: object,
    candidate_strand: object,
    candidate_site_0based: object,
    atlas_rows: Iterable[tuple[object, object, object]],
) -> tuple[int | None, tuple[str, str, int] | None, int]:
    """Legacy compatibility API: one minimum ordinal tie, plus same-strand row count.

    New callers should prefer :class:`ReferenceIndex`, which retains every equal-
    distance source row and its original ordinal.
    """
    chrom = nonempty_text(candidate_chrom, "candidate_chrom")
    strand = strict_strand(candidate_strand)
    site = strict_integer(candidate_site_0based, "candidate_site_0based")
    if site < 0:
        raise ValueError("candidate_site_0based must be nonnegative")
    nearest = None
    distance = None
    n = 0
    for ordinal, (row_chrom, row_strand, row_site) in enumerate(atlas_rows):
        row_chrom = nonempty_text(row_chrom, f"atlas_rows[{ordinal}].chrom")
        row_strand = strict_strand(row_strand, f"atlas_rows[{ordinal}].strand")
        row_site = strict_integer(row_site, f"atlas_rows[{ordinal}].site")
        if row_site < 0:
            raise ValueError(f"atlas_rows[{ordinal}].site must be nonnegative")
        if row_chrom != chrom or row_strand != strand:
            continue
        n += 1
        candidate_distance = abs(row_site - site)
        if distance is None or candidate_distance < distance or (
            candidate_distance == distance and row_site < nearest[2]
        ):
            nearest = (row_chrom, row_strand, row_site)
            distance = candidate_distance
    return distance, nearest, n


def atlas_assessment(
    *,
    atlas_chroms: set[str],
    candidate_chrom: object,
    same_strand_atlas_n: object,
    distance: object | None,
    threshold: object = 100,
) -> str:
    """Classify proximity only after an explicitly complete contig assessment.

    ``atlas_chroms`` must mean complete assessed contigs, not merely files opened
    or target-genome contigs.  A positive row count with missing distance is not
    absence.  A zero count with finite distance is contradictory.
    """
    chrom = nonempty_text(candidate_chrom, "candidate_chrom")
    n = strict_integer(same_strand_atlas_n, "same_strand_atlas_n")
    if n < 0:
        raise ValueError("same_strand_atlas_n must be nonnegative")
    checked_threshold = finite_nonnegative(threshold, "threshold")
    if distance is None:
        if chrom not in atlas_chroms:
            return UNASSESSABLE
        return ABSENT if n == 0 else UNASSESSABLE
    checked_distance = finite_nonnegative(distance, "distance")
    if n == 0:
        raise ValueError("n=0 is inconsistent with a finite distance")
    if chrom not in atlas_chroms:
        return UNASSESSABLE
    return ABSENT if checked_distance > checked_threshold else PROXIMAL


@dataclass(frozen=True)
class ReferenceContract:
    source_name: str
    build: str
    coordinate_base: str
    source_sha256: str
    verified: bool
    release: str | None = None

    def __post_init__(self) -> None:
        if not nonempty_text(self.source_name, "source_name"):
            raise ValueError("source_name must be nonempty")
        if not nonempty_text(self.build, "build"):
            raise ValueError("build must be nonempty")
        if self.coordinate_base not in {"0based", "1based", "UNRESOLVED"}:
            raise ValueError("coordinate_base must be 0based, 1based, or UNRESOLVED")
        nonempty_text(self.source_sha256, "source_sha256")
        if not isinstance(self.verified, bool):
            raise ValueError("verified must be boolean")


@dataclass(frozen=True)
class ReferenceRecord:
    pas_id: str
    chrom: str
    position: int
    strand: str
    ordinal: int
    normalized_key: str | None = None
    source_pas_id: str | None = None


def validate_position_for_base(value: object, coordinate_base: str, field_name: str) -> int:
    position = strict_integer(value, field_name)
    if coordinate_base == "0based" and position < 0:
        raise ValueError(f"{field_name} must be >=0 for a 0based contract")
    if coordinate_base == "1based" and position < 1:
        raise ValueError(f"{field_name} must be >=1 for a 1based contract")
    if coordinate_base == "UNRESOLVED":
        raise ValueError("position cannot be computed under an UNRESOLVED coordinate base")
    return position


@dataclass
class ReferenceIndex:
    """Small immutable per-key position arrays retaining every source row.

    ``pas_id`` is always the immutable source identifier.  For a raw untransformed
    table it may equal ``chrom:position:strand``.  After a future approved
    transform, the adapter must either retain that source ID and supply an explicit
    ``normalized_key`` sidecar value or reject ingestion; this helper never rewrites
    the source ID.
    """

    contract: ReferenceContract
    records: list[ReferenceRecord] = field(default_factory=list)
    _by_key: dict[tuple[str, str], list[tuple[int, ReferenceRecord]]] = field(default_factory=dict)
    _positions: dict[tuple[str, str], list[int]] = field(default_factory=dict)
    _records_by_position: dict[tuple[str, str, int], list[ReferenceRecord]] = field(default_factory=dict)

    @classmethod
    def build(cls, rows: Iterable[Mapping[str, object]], contract: ReferenceContract) -> "ReferenceIndex":
        if not contract.verified:
            raise ValueError("refusing to index an unverified reference contract")
        if contract.coordinate_base == "UNRESOLVED":
            raise ValueError("cannot build a computable index with unresolved coordinate base")
        index = cls(contract=contract)
        ordinal_mode: str | None = None
        seen_ordinals: set[int] = set()
        for raw_ordinal, row in enumerate(rows):
            required = {"pas_id", "chrom", "position", "strand"}
            missing = sorted(required - row.keys())
            if missing:
                raise ValueError(f"reference row {raw_ordinal} missing fields: {','.join(missing)}")
            pas_id = nonempty_text(row["pas_id"], f"reference row {raw_ordinal} pas_id")
            source_pas_id = pas_id
            if row.get("source_pas_id") is not None:
                supplied_source = nonempty_text(row["source_pas_id"], f"reference row {raw_ordinal} source_pas_id")
                if supplied_source != pas_id:
                    raise ValueError(f"reference row {raw_ordinal} pas_id/source_pas_id conflict")
            chrom = nonempty_text(row["chrom"], f"reference row {raw_ordinal} chrom")
            position = validate_position_for_base(row["position"], contract.coordinate_base, f"reference row {raw_ordinal} position")
            strand = strict_strand(row["strand"], f"reference row {raw_ordinal} strand")
            normalized = f"{chrom}:{position}:{strand}"
            normalized_key = normalized
            if row.get("normalized_key") is not None:
                normalized_key = nonempty_text(row["normalized_key"], f"reference row {raw_ordinal} normalized_key")
            if normalized_key != normalized:
                raise ValueError(f"reference row {raw_ordinal} normalized_key/chrom/position/strand conflict")
            if row.get("normalized_key") is None and pas_id != normalized:
                raise ValueError(f"reference row {raw_ordinal} PAS_ID is not normalized; supply normalized_key sidecar")
            explicit = "source_ordinal" in row
            if explicit:
                ordinal = strict_integer(row["source_ordinal"], f"reference row {raw_ordinal} source_ordinal")
                if ordinal < 0:
                    raise ValueError(f"reference row {raw_ordinal} source_ordinal must be nonnegative")
                if ordinal in seen_ordinals:
                    raise ValueError(f"duplicate reference source_ordinal: {ordinal}")
            else:
                ordinal = raw_ordinal
                while ordinal in seen_ordinals:
                    ordinal += 1
            seen_ordinals.add(ordinal)
            record = ReferenceRecord(
                pas_id=pas_id, chrom=chrom, position=position, strand=strand,
                ordinal=ordinal, normalized_key=normalized_key,
                source_pas_id=source_pas_id,
            )
            index.records.append(record)
            key = (chrom, strand)
            index._by_key.setdefault(key, []).append((position, record))
            index._records_by_position.setdefault(key + (position,), []).append(record)
        for key, values in index._by_key.items():
            values.sort(key=lambda item: (item[0], item[1].ordinal))
            index._positions[key] = [item[0] for item in values]
        for records in index._records_by_position.values():
            records.sort(key=lambda record: record.ordinal)
        return index

    def _values(self, chrom: str, strand: str) -> list[tuple[int, ReferenceRecord]] | None:
        return self._by_key.get((chrom, strand))

    def nearest_with_all_ties(
        self, chrom: object, strand: object, position: object
    ) -> dict[str, object]:
        chrom = nonempty_text(chrom, "candidate_chrom")
        strand = strict_strand(strand)
        site = validate_position_for_base(position, self.contract.coordinate_base, "candidate_position")
        key = (chrom, strand)
        positions = self._positions.get(key)
        if not positions:
            return {
                "status": "no_same_strand_rows",
                "same_strand_atlas_n": 0,
                "distance": None,
                "nearest": [],
                "coordinate_base": self.contract.coordinate_base,
            }
        left = bisect_left(positions, site)
        candidates: list[int] = []
        if left < len(positions):
            candidates.append(positions[left])
        if left > 0:
            candidates.append(positions[left - 1])
        nearest_distance = min(abs(point - site) for point in candidates)
        lower = bisect_left(positions, site - nearest_distance)
        upper = bisect_right(positions, site + nearest_distance)
        nearest: list[dict[str, object]] = []
        for point in dict.fromkeys(positions[lower:upper]):
            if abs(point - site) != nearest_distance:
                continue
            for record in self._records_by_position[key + (point,)]:
                nearest.append({
                    "pas_id": record.pas_id,
                    "source_pas_id": record.source_pas_id,
                    "normalized_key": record.normalized_key,
                    "position": point,
                    "source_ordinal": record.ordinal,
                    "distance": abs(point - site),
                })
        return {
            "status": "proximal_or_absent_pending_threshold",
            "same_strand_atlas_n": len(positions),
            "distance": nearest_distance,
            "nearest": nearest,
            "coordinate_base": self.contract.coordinate_base,
        }


def reference_schema_inventory(header: Sequence[object], contract: ReferenceContract) -> dict[str, object]:
    """Freeze schema state; joining also requires an exact header and resolved base."""
    expected = [
        "PAS_ID", "Chromosome", "Position", "Strand", "Mean RPM",
        "Intron/exon location", "Ensemble ID", "RefSeq Gene ID", "Gene Symbol",
        "Gene Name", "Extension", "PAS type", "PSE", "PAS Signal", "Conservation",
        "intergenic_TE",
    ]
    observed = [str(x) for x in header]
    exact = observed == expected
    join_allowed = bool(contract.verified and exact and contract.coordinate_base != "UNRESOLVED")
    return {
        "schema": "scpolya.cs3.reference.schema.inventory.v3",
        "source_name": contract.source_name,
        "declared_build": contract.build,
        "verified": contract.verified,
        "source_sha256": contract.source_sha256,
        "coordinate_base": contract.coordinate_base,
        "expected_header": expected,
        "observed_header": observed,
        "exact_header_match": exact,
        "join_allowed": join_allowed,
        "source_pas_id_role": "immutable source identifier; never rewritten by this helper",
        "normalized_key_role": "chrom:position:strand under the declared contract/base",
    }


def parse_stored_mispriming(value: object) -> bool | None:
    if value is None or (isinstance(value, str) and value.strip().upper() in {"", "NA", "NULL"}):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError("stored mispriming_pass must be true, false, or NA")


def export_scope_status(stored_mispriming: object) -> dict[str, object]:
    """Keep all rows auditable; no total_polyA_reads depth-proxy filter is used."""
    flag = parse_stored_mispriming(stored_mispriming)
    if flag is True:
        status = "retained_export_stored_pass"
        reason = "stored mispriming_pass is true"
    elif flag is False:
        status = "excluded_export_stored_false"
        reason = "stored mispriming_pass is false"
    else:
        status = "excluded_export_stored_unknown"
        reason = "stored mispriming_pass is NA/unresolved"
    return {"status": status, "reason": reason, "stored_mispriming_pass": flag}


def _window_transcript_coords(site_1based: object, strand: object, upstream: int, downstream: int) -> tuple[int, int]:
    site = strict_integer(site_1based, "site_1based")
    if site < 1 or upstream < 1 or downstream < 1:
        raise ValueError("require 1-based site and positive window offsets")
    if strict_strand(strand) == "+":
        start, end = site - upstream, site - downstream
    else:
        start, end = site + downstream, site + upstream
    if start < 1:
        raise ValueError("window crosses chromosome start")
    return start, end


def window_sequence(sequence: object, chrom_start_1based: object, chrom_end_1based: object, strand: object) -> str:
    """Extract and reverse-complement only the requested closed 1-based window."""
    start = strict_integer(chrom_start_1based, "chrom_start_1based")
    end = strict_integer(chrom_end_1based, "chrom_end_1based")
    if start < 1 or end < start:
        raise ValueError("invalid 1-based window")
    if not isinstance(sequence, str) or not sequence:
        raise ValueError("sequence must be a nonempty string")
    if end > len(sequence):
        raise ValueError("window exceeds supplied sequence")
    # Validate only the selected slice.  A future chromosome-sized string must not
    # force an O(chromosome) validation for every candidate window.
    piece = sequence[start - 1:end]
    if not set(piece.upper()) <= _DNA:
        raise ValueError("observed window contains characters outside DNA/IUPAC")
    return piece if strict_strand(strand) == "+" else piece.translate(_COMP)[::-1]


def _requested_window(site_1based: int, strand: str, channel: str) -> tuple[int, int]:
    if channel == "motif":
        return (site_1based - 50, site_1based - 1) if strand == "+" else (site_1based + 1, site_1based + 50)
    return (site_1based + 1, site_1based + 20) if strand == "+" else (site_1based - 20, site_1based - 1)


def _channel_result(
    sequence: str,
    *,
    requested_start: int,
    requested_end: int,
    strand: str,
    window_name: str,
) -> dict[str, object]:
    """Assess one channel over the actually observed overlap with a requested window."""
    observed_start = max(1, requested_start)
    observed_end = min(len(sequence), requested_end)
    has_observed = observed_start <= observed_end
    observed_piece = sequence[observed_start - 1:observed_end] if has_observed else ""
    if has_observed and not set(observed_piece.upper()) <= _DNA:
        raise ValueError("observed window contains characters outside DNA/IUPAC")
    oriented = "" if not has_observed else (
        observed_piece if strand == "+" else observed_piece.translate(_COMP)[::-1]
    ).upper()
    requested_length = requested_end - requested_start + 1
    observed_length = len(oriented)
    missing_length = max(0, requested_length - observed_length)
    ambiguous = sum(base not in _STRICT_DNA for base in oriented)
    complete = observed_length == requested_length and ambiguous == 0
    is_motif = window_name in ("motif", "-50..-1")
    if is_motif:
        rna = oriented.replace("T", "U")
        first = rna.count("AAUAAA")
        second = rna.count("AUUAAA")
        positive = first > 0 or second > 0
    else:
        first = None
        second = None
        positive = oriented.count("A") >= 12
    if positive:
        status = "positive" if complete and ambiguous == 0 else "positive_lower_bound"
        reason = (
            "complete_strict_ACGT_positive_window" if complete and ambiguous == 0 else
            "positive_lower_bound_from_ambiguous_or_truncated_window"
        )
    elif complete and ambiguous == 0:
        status = "negative"
        reason = "complete_strict_ACGT_window_without_positive_evidence"
    elif observed_length == 0:
        status = "not_assessable"
        reason = "no_requested_window_bases_observed"
    else:
        status = "not_assessable"
        reason = "ambiguous_or_truncated_window_precludes_clean_negative"
    result: dict[str, object] = {
        # Backward-compatible aliases point to the requested full window.
        "chrom_start_1based": requested_start,
        "chrom_end_1based": requested_end,
        "window_length": requested_length,
        "status": status,
        "reason": reason,
        "window": window_name,
        "requested_chrom_start_1based": requested_start,
        "requested_chrom_end_1based": requested_end,
        "observed_chrom_start_1based": observed_start if has_observed else None,
        "observed_chrom_end_1based": observed_end if has_observed else None,
        "requested_length": requested_length,
        "observed_length": observed_length,
        "missing_window_length": missing_length,
        "truncated_window": missing_length > 0,
        "oriented_sequence": oriented,
        "ambiguous_base_count": ambiguous,
        "complete_strict_acgt": complete,
    }
    if window_name in ("motif", "-50..-1"):
        result.update({
            "AAUAAA": first,
            "AUUAAA": second,
            "canonical": True if positive else None,
        })
        if not positive and complete and ambiguous == 0:
            result["canonical"] = False
    else:
        count = oriented.count("A")
        result.update({
            "a_count": count,
            "high_arich": True if positive else None,
        })
        if not positive and complete and ambiguous == 0:
            result["high_arich"] = False
    return result


def canonical_motif(sequence: object, site_1based: object, strand: object) -> dict[str, object]:
    """Full-window canonical motif helper; caller-facing wrapper handles edges."""
    start, end = _window_transcript_coords(site_1based, strand, 50, 1)
    if not isinstance(sequence, str) or end > len(sequence):
        raise ValueError("window exceeds supplied sequence")
    return _channel_result(sequence, requested_start=start, requested_end=end, strand=strict_strand(strand), window_name="-50..-1")


def downstream_a_richness(sequence: object, site_1based: object, strand: object) -> dict[str, object]:
    """Full-window A-richness helper; caller-facing wrapper handles edges."""
    site = strict_integer(site_1based, "site_1based")
    checked_strand = strict_strand(strand)
    if site < 1:
        raise ValueError("site_1based must be positive")
    if not isinstance(sequence, str) or not sequence:
        raise ValueError("sequence must be a nonempty string")
    start, end = _requested_window(site, checked_strand, "arich")
    if start < 1 or end > len(sequence):
        raise ValueError("window exceeds supplied sequence")
    return _channel_result(sequence, requested_start=start, requested_end=end, strand=checked_strand, window_name="+1..+20")


def sequence_context(sequence: object | None, chrom: object, observed_contigs: set[str], site_1based: object, strand: object) -> dict[str, object]:
    """Per-channel caller wrapper: preserve edge evidence and reject invalid input.

    A window crossing a real contig boundary is missingness.  Invalid site/strand/
    observed sequence characters raise instead of being converted to scientific NA.
    Coordinate fields are named ``requested_chrom_*`` and ``observed_chrom_*``.
    """
    chrom_text = nonempty_text(chrom, "chrom")
    checked_strand = strict_strand(strand)
    site = strict_integer(site_1based, "site_1based")
    if site < 1:
        raise ValueError("site_1based must be positive")
    if sequence is not None and (not isinstance(sequence, str) or (len(sequence) == 0 and chrom_text in observed_contigs)):
        raise ValueError("sequence must be null or a nonempty string")
    if sequence is None or chrom_text not in observed_contigs:
        reason = "sequence_not_available" if sequence is None else "contig_not_observed"
        unavailable_channel = {
            "status": "not_assessable", "reason": reason,
            "requested_chrom_start_1based": None, "requested_chrom_end_1based": None,
            "observed_chrom_start_1based": None, "observed_chrom_end_1based": None,
            "requested_length": None, "observed_length": 0,
            "missing_window_length": None, "truncated_window": None,
            "oriented_sequence": "", "ambiguous_base_count": None,
            "complete_strict_acgt": False,
        }
        motif = {**unavailable_channel, "window": "-50..-1", "AAUAAA": None, "AUUAAA": None, "canonical": None}
        arich = {**unavailable_channel, "window": "+1..+20", "a_count": None, "high_arich": None}
        return {
            "status": "not_assessable", "reason": reason, "motif": motif, "arich": arich,
            "canonical": None, "high_arich": None, "AAUAAA": None, "AUUAAA": None,
            "a_count": None, "combined_status": "channels_not_assessable",
        }
    if site > len(sequence):
        raise ValueError("site_1based exceeds supplied sequence length")
    motif = _channel_result(
        sequence,
        requested_start=_requested_window(site, checked_strand, "motif")[0],
        requested_end=_requested_window(site, checked_strand, "motif")[1],
        strand=checked_strand, window_name="-50..-1",
    )
    arich = _channel_result(
        sequence,
        requested_start=_requested_window(site, checked_strand, "arich")[0],
        requested_end=_requested_window(site, checked_strand, "arich")[1],
        strand=checked_strand, window_name="+1..+20",
    )
    reason = "independent motif and A-richness missingness/positive bounds"
    if any(x["status"] == "not_assessable" for x in (motif, arich)):
        reason = "window_unavailable_or_ambiguous: per-channel missingness retained"
    if motif["canonical"] is True and arich["high_arich"] is True:
        combined = "conflict_positive_both"
    elif motif["status"] == "not_assessable" and arich["status"] == "not_assessable":
        combined = "channels_not_assessable"
    else:
        combined = "per_channel_evidence_preserved"
    return {
        "status": "per_channel_preserved",
        "reason": reason,
        "motif": motif,
        "arich": arich,
        "canonical": motif["canonical"],
        "high_arich": arich["high_arich"],
        "AAUAAA": motif["AAUAAA"], "AUUAAA": motif["AUUAAA"],
        "a_count": arich["a_count"],
        "combined_status": combined,
    }


def parse_pass_qc(value: object) -> bool | None:
    if value is None or (isinstance(value, str) and value.strip().upper() in {"", "NA", "NULL"}):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError("pass_qc must be true, false, or NA")


def cell_support(count_rows: Iterable[Mapping[str, object]], label_rows: Iterable[Mapping[str, object]] | None = None) -> dict[str, object]:
    """One-pass positive support and one-pass label/QC binding for streaming rows."""
    positives: set[str] = set()
    positive_rows = 0
    count_row_n = 0
    for ordinal, row in enumerate(count_rows):
        count_row_n += 1
        missing = sorted({"peak_name", "cell_barcode", "umi_count"} - row.keys())
        if missing:
            raise ValueError(f"count row {ordinal} missing fields: {','.join(missing)}")
        nonempty_text(row["peak_name"], f"count row {ordinal} peak_name")
        barcode = nonempty_text(row["cell_barcode"], f"count row {ordinal} cell_barcode")
        count = strict_integer(row["umi_count"], f"count row {ordinal} umi_count")
        if count < 0:
            raise ValueError(f"count row {ordinal} umi_count must be nonnegative")
        if count > 0:
            positives.add(barcode)
            positive_rows += 1
    qc: dict[str, bool | None] = {}
    label_availability = "label_metadata_not_supplied"
    qc_availability = "not_checkable"
    label_set: set[str] = set()
    duplicate_barcodes: set[str] = set()
    duplicate_rows = 0
    label_row_n = 0
    supplied = label_rows is not None
    if supplied:
        for ordinal, row in enumerate(label_rows):
            label_row_n += 1
            barcode = nonempty_text(row.get("barcode"), f"label row {ordinal} barcode")
            state = parse_pass_qc(row.get("pass_qc"))
            if barcode in label_set:
                duplicate_barcodes.add(barcode)
                duplicate_rows += 1
                if qc[barcode] != state:
                    raise ValueError(f"conflicting pass_qc for duplicate label barcode {barcode}")
            else:
                label_set.add(barcode)
                qc[barcode] = state
    if label_row_n:
        label_availability = "label_metadata_present"
    labelled_positive = positives & label_set
    if positives:
        support_availability = "observed_positive_detection"
    elif count_row_n:
        support_availability = "observed_zero_or_na_rows_without_positive_detection"
    else:
        support_availability = "no_count_rows_observed"
    if not supplied:
        label_availability = "label_metadata_not_supplied"
        qc_availability = "not_checkable"
    elif label_row_n == 0:
        label_availability = "label_metadata_empty"
        qc_availability = "not_checkable"
    elif any(state is not None for state in qc.values()):
        qc_availability = "reported"
    else:
        qc_availability = "all_rows_pass_qc_na"
    return {
        "schema": "scpolya.cs3.cell.support.v3",
        "observed_positive_barcodes": sorted(positives),
        "n_cells": len(positives),
        "observed_positive_rows": positive_rows,
        "support_availability": support_availability,
        "count_rows_seen": count_row_n,
        "labels_supplied": supplied,
        "label_availability": label_availability,
        "label_rows_seen": label_row_n,
        "labelled_barcodes": sorted(label_set),
        "labelled_positive_intersection": sorted(labelled_positive),
        "n_cells_labelled_intersection": len(labelled_positive),
        "unlabelled_positive_barcodes": sorted(positives - label_set),
        "qc_availability": qc_availability,
        "pass_qc_true_positive_intersection": sorted(x for x in labelled_positive if qc.get(x) is True),
        "n_cells_pass_qc_true": len([x for x in labelled_positive if qc.get(x) is True]),
        "duplicate_label_barcodes": sorted(duplicate_barcodes),
        "duplicate_label_rows": duplicate_rows,
        "duplicate_pair_semantics": "distinct barcode once for n_cells; repeated positive rows retained as row count only",
        "no_count_rows_interpretation": "no_recorded_positive_detection; not biological_zero",
        "total_umi": None,
        "total_umi_reason": "pair duplicate/dedup semantics not frozen",
        "full_25m_count_scan_performed": False,
    }


def evidence_axes(
    *,
    core: CandidateCore,
    reference_assessment: object | None = None,
    support: Mapping[str, object] | None = None,
    motif: Mapping[str, object] | None = None,
    arich: Mapping[str, object] | None = None,
    stored_mispriming_pass: object | None = None,
    misprimed_ratio: object | None = None,
    secondary_reference: object | None = None,
) -> dict[str, object]:
    """Return independent evidence axes; deliberately no A/B/C/D risk class."""
    if misprimed_ratio is not None:
        checked_ratio = finite_nonnegative(misprimed_ratio, "misprimed_ratio")
        if checked_ratio > 1:
            raise ValueError("misprimed_ratio must be a fraction in [0,1]")
        misprimed_ratio = checked_ratio
    if not isinstance(support, Mapping) and support is not None:
        raise ValueError("support must be a mapping or null")
    if not isinstance(motif, Mapping) and motif is not None:
        raise ValueError("motif must be a mapping or null")
    if not isinstance(arich, Mapping) and arich is not None:
        raise ValueError("arich must be a mapping or null")
    if secondary_reference not in (None, "not_acquired", "not_assessed", "observed", "concordant", "discordant"):
        raise ValueError("invalid secondary_reference state")
    return {
        "schema": "scpolya.cs3.multiaxis.evidence.v2",
        "site_identity": {
            "peak_name": core.peak_name, "chrom": core.chrom, "start": core.start,
            "end": core.end, "strand": core.strand,
            "source_ordinal": core.source_ordinal,
        },
        "gene_annotation": {
            "gene_id": core.gene_id, "gene_name": core.gene_name,
            "transcript_id": core.transcript_id,
            "status": "present" if core.gene_id is not None else "missing_nullable",
        },
        "reference_assessment": reference_assessment,
        "read_cell_umi_support": support,
        "canonical_motif": motif,
        "downstream_a_richness": arich,
        "stored_native_flag": export_scope_status(stored_mispriming_pass),
        "raw_misprimed_ratio": misprimed_ratio,
        "secondary_reference_status": secondary_reference or "not_acquired",
        "display_class": None,
        "risk_score": None,
        "missingness": {
            "reference": reference_assessment is None,
            "support": support is None,
            "motif": motif is None,
            "arich": arich is None,
            "misprimed_ratio": misprimed_ratio is None,
            "secondary_reference": secondary_reference in (None, "not_acquired"),
        },
    }


def evidence_sort_key(row: Mapping[str, object], numeric_key: str, ordinal_key: str = "source_ordinal") -> tuple[object, ...]:
    """Deterministic ascending order with numeric/text NA last and ordinal tiebreak."""
    if numeric_key not in row:
        raise ValueError(f"missing sort field: {numeric_key}")
    value = row[numeric_key]
    ordinal = strict_integer(row.get(ordinal_key, 0), ordinal_key)
    if value is None:
        primary = (1, 0, "")
    elif isinstance(value, bool):
        primary = (0, 1 if value else 0, "")
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            primary = (1, 0, "")
        else:
            primary = (0, value, "")
    elif isinstance(value, str):
        primary = (0, 0, value)
    else:
        raise ValueError("unsupported sort value")
    return primary + (ordinal,)


def candidate_reference_query(
    index: ReferenceIndex,
    candidate_build: str,
    candidate_coordinate_base: str,
    chrom: object,
    strand: object,
    position: object,
) -> dict[str, object]:
    """Join gate: exact build and explicit matching coordinate base are required."""
    nonempty_text(candidate_build, "candidate_build")
    if candidate_build != index.contract.build:
        raise ValueError("reference/candidate build mismatch; refusing direct join")
    if candidate_coordinate_base != index.contract.coordinate_base:
        raise ValueError("reference/candidate coordinate-base mismatch; refusing direct join")
    return index.nearest_with_all_ties(chrom, strand, position)
