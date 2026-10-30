"""Small coordinate-sorted BAMs covering every read-eligibility case."""
from __future__ import annotations

from pathlib import Path

import pysam

HEADER = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "chr1", "LN": 5_000}]}

# name, flag, mapq, tags
ELIGIBILITY_CASES = (
    ("unique", 0, 255, {"NH": 1}),
    ("no_tags", 0, 255, {}),
    ("low_mapq", 0, 10, {"NH": 1}),
    ("rescued", 0, 255, {"NH": 3, "mm": 1}),
    ("rescued_nh1", 0, 255, {"NH": 1, "mm": 1}),
    ("mm0_nh2", 0, 255, {"NH": 2, "mm": 0}),
    ("multi_low", 0, 1, {"NH": 3}),
    ("secondary", 0x100, 255, {"NH": 3, "mm": 1}),
    ("supplementary", 0x800, 255, {"NH": 1}),
    ("duplicate", 0x400, 255, {"NH": 1}),
    ("reverse", 0x10, 255, {"NH": 1}),
)


def segment(name, flag, mapq, tags, *, start, cigar=((0, 50),), seq=None, cb="AAAC-1", umi=None):
    seg = pysam.AlignedSegment()
    seg.query_name = name
    length = sum(n for op, n in cigar if op in (0, 1, 4, 7, 8))
    seg.query_sequence = seq or ("C" * length)
    seg.flag = flag
    seg.reference_id = 0
    seg.reference_start = start
    seg.mapping_quality = mapq
    seg.cigartuples = list(cigar)
    seg.query_qualities = pysam.qualitystring_to_array("I" * length)
    seg.set_tags(list(tags.items()) + [("CB", cb), ("UB", umi or f"U{abs(hash(name)) % 10**8:08d}")])
    return seg


def write_bam(path: Path, segments) -> Path:
    segments = sorted(segments, key=lambda s: (s.reference_start, s.query_name, s.flag))
    with pysam.AlignmentFile(str(path), "wb", header=HEADER) as out:
        for seg in segments:
            out.write(seg)
    pysam.index(str(path))
    return path


def eligibility_bam(path: Path) -> Path:
    return write_bam(path, [
        segment(name, flag, mapq, tags, start=100 + 7 * i)
        for i, (name, flag, mapq, tags) in enumerate(ELIGIBILITY_CASES)
    ])
