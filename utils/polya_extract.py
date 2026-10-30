"""Pure scientific implementation for Poly(A) BAM extraction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pysam

from .read_eligibility import ReadRule


POLYA_EXTRACTION_RESULT_SCHEMA_V1 = "scpolya.polya_extraction.result.v1"


@dataclass(frozen=True)
class PolyAExtractionStats:
    total_reads: int
    retained_reads: int
    excluded_reads_polyA: int
    excluded_reads_nonA: int
    schema: str = POLYA_EXTRACTION_RESULT_SCHEMA_V1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PolyAExtractionStats":
        if payload.get("schema") != POLYA_EXTRACTION_RESULT_SCHEMA_V1:
            raise ValueError("Poly(A) extraction result schema mismatch")
        return cls(
            total_reads=int(payload["total_reads"]),
            retained_reads=int(payload["retained_reads"]),
            excluded_reads_polyA=int(payload["excluded_reads_polyA"]),
            excluded_reads_nonA=int(payload["excluded_reads_nonA"]),
        )


def bam_index_path(bam_path: str | Path) -> Path:
    return Path(f"{Path(bam_path)}.bai")


def polya_publish_marker_path(final_bam: str | Path) -> Path:
    final_bam = Path(final_bam)
    return final_bam.with_name(f".{final_bam.name}.polya-publish.json")


TAIL_RETAINED = "retained"
TAIL_SHORT = "polyA"
TAIL_NON_A = "nonA"


def polya_tail_status(read: pysam.AlignedSegment, nonA_allowed: int, minpolyA: int) -> str:
    """Historical strand-aware terminal soft-clip tail test for one read."""

    if read.is_reverse:
        soft_clipped_size = read.query_alignment_start
        if soft_clipped_size < minpolyA:
            return TAIL_SHORT
        soft_clipped_seq = read.query_sequence[:soft_clipped_size]
        num_A = soft_clipped_seq.upper().count("T")
    else:
        soft_clipped_size = read.query_length - read.query_alignment_end
        if soft_clipped_size < minpolyA:
            return TAIL_SHORT
        soft_clipped_seq = read.query_sequence[-soft_clipped_size:]
        num_A = soft_clipped_seq.upper().count("A")

    non_A = soft_clipped_size - num_A
    if num_A >= minpolyA and non_A <= nonA_allowed:
        return TAIL_RETAINED
    if num_A < minpolyA:
        return TAIL_SHORT
    return TAIL_NON_A


def extract_polya_bam(
    in_bam: str | Path,
    out_bam: str | Path,
    nonA_allowed: int,
    minpolyA: int,
    minMAPQ: int,
    read_rule: ReadRule | None = None,
) -> PolyAExtractionStats:
    """Copy reads passing the historical strand-aware terminal-tail filter.

    Without a unified ``read_rule`` the historical prefilter (mapped and
    MAPQ >= minMAPQ) applies unchanged. A unified rule replaces it with the
    read-eligibility rule shared with expression coverage and counting.
    """
    unified = read_rule is not None and read_rule.unified

    in_bam = Path(in_bam)
    out_bam = Path(out_bam)
    total_reads = 0
    retained_reads = 0
    excluded_reads_polyA = 0
    excluded_reads_nonA = 0

    with pysam.AlignmentFile(str(in_bam), "rb") as bam_in, pysam.AlignmentFile(
        str(out_bam), "wb", template=bam_in
    ) as bam_out:
        for read in bam_in.fetch(until_eof=True):
            total_reads += 1
            if unified:
                if not read_rule.is_eligible(read):
                    continue
            elif read.is_unmapped or read.mapping_quality < minMAPQ:
                continue

            status = polya_tail_status(read, nonA_allowed, minpolyA)
            if status == TAIL_RETAINED:
                bam_out.write(read)
                retained_reads += 1
            elif status == TAIL_SHORT:
                excluded_reads_polyA += 1
            else:
                excluded_reads_nonA += 1

    pysam.index(str(out_bam))
    return PolyAExtractionStats(
        total_reads=total_reads,
        retained_reads=retained_reads,
        excluded_reads_polyA=excluded_reads_polyA,
        excluded_reads_nonA=excluded_reads_nonA,
    )


def validate_polya_bam(bam_path: str | Path) -> None:
    """Require a non-empty quickcheck-clean BAM with a readable BAI."""

    bam_path = Path(bam_path)
    index_path = bam_index_path(bam_path)
    for label, path in (("BAM", bam_path), ("BAM index", index_path)):
        if not path.is_file():
            raise FileNotFoundError(f"Poly(A) {label} is missing: {path}")
        if path.stat().st_size <= 0:
            raise ValueError(f"Poly(A) {label} is empty: {path}")

    quickcheck_result = pysam.quickcheck(str(bam_path))
    if quickcheck_result:
        raise ValueError(
            f"Poly(A) BAM quickcheck failed for {bam_path}: {quickcheck_result}"
        )
    with pysam.AlignmentFile(str(bam_path), "rb") as bam:
        if not bam.has_index():
            raise ValueError(f"Poly(A) BAM index is not readable: {index_path}")
        bam.get_index_statistics()
