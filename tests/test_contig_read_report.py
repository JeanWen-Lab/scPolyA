from __future__ import annotations

import csv

import pysam

from utils.contig_report import (
    STATUS_EXCLUDED,
    STATUS_KEPT,
    contig_read_rows,
    format_contig_exclusion_warning,
    summarize_contig_rows,
    write_contig_read_report,
)


def _bam(path, counts):
    header = {"HD": {"VN": "1.6", "SO": "coordinate"},
              "SQ": [{"SN": name, "LN": 10_000} for name in counts]}
    with pysam.AlignmentFile(str(path), "wb", header=header) as out:
        for tid, (name, n) in enumerate(counts.items()):
            for i in range(n):
                seg = pysam.AlignedSegment()
                seg.query_name = f"{name}-{i}"
                seg.query_sequence = "A" * 20
                seg.flag = 0
                seg.reference_id = tid
                seg.reference_start = 100 + i
                seg.mapping_quality = 255
                seg.cigartuples = [(0, 20)]
                seg.query_qualities = pysam.qualitystring_to_array("I" * 20)
                out.write(seg)
    pysam.index(str(path))
    return path


def test_rows_mark_non_standard_contigs_and_warning_reports_share(tmp_path):
    bam = _bam(tmp_path / "in.bam", {"chr1": 6, "chr2": 2, "KI270728.1": 2, "GL000195.1": 0})
    rows = contig_read_rows(sample_id="s", pp_id="s.all", bam_path=bam,
                            standard_chroms=["chr1", "chr2"])
    status = {row["contig"]: (row["mapped_alignments"], row["status"]) for row in rows}
    assert status == {
        "chr1": (6, STATUS_KEPT),
        "chr2": (2, STATUS_KEPT),
        "KI270728.1": (2, STATUS_EXCLUDED),
        "GL000195.1": (0, STATUS_EXCLUDED),
    }
    entry = summarize_contig_rows(rows)[("s", "s.all")]
    assert entry == {"mapped": 10, "excluded_mapped": 2, "excluded_contigs": 1, "not_analysed_mapped": 0}
    message = format_contig_exclusion_warning("s", "s.all", entry)
    assert "2 of 10" in message and "(20.00%)" in message and "1 non-standard" in message

    out = tmp_path / "tables" / "contig_read_report.tsv"
    write_contig_read_report(rows, out)
    with open(out, newline="") as handle:
        written = list(csv.DictReader(handle, delimiter="\t"))
    assert [r["contig"] for r in written] == ["chr1", "chr2", "KI270728.1", "GL000195.1"]
    assert written[2]["status"] == STATUS_EXCLUDED and written[2]["mapped_alignments"] == "2"


def test_standard_contigs_outside_the_requested_scope_are_reported_separately(tmp_path):
    from utils.contig_report import STATUS_NOT_ANALYSED

    bam = _bam(tmp_path / "in.bam", {"chr1": 3, "chr2": 4, "chrM": 1, "KI270728.1": 2})
    rows = contig_read_rows(sample_id="s", pp_id="s.all", bam_path=bam, standard_chroms=["chr1"],
                            all_standard_chroms=["chr1", "chr2", "chrM"])
    assert [row["status"] for row in rows] == [STATUS_KEPT, STATUS_NOT_ANALYSED, STATUS_NOT_ANALYSED, STATUS_EXCLUDED]
    entry = summarize_contig_rows(rows)[("s", "s.all")]
    assert entry == {"mapped": 10, "excluded_mapped": 2, "excluded_contigs": 1, "not_analysed_mapped": 5}
    assert "A further 5 lie on standard contigs" in format_contig_exclusion_warning("s", "s.all", entry)
