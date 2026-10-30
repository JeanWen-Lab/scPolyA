"""Run-level report of alignments kept or excluded by the standard-contig rule.

Reporting only: units are still built from the standard contigs exactly as
before. Counts come from the BAM index, so they are alignment records (primary,
secondary, supplementary and duplicate-flagged records alike), not molecules.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Iterable, Mapping

import pysam

CONTIG_READ_REPORT_NAME = "contig_read_report.tsv"
CONTIG_READ_REPORT_COLUMNS = (
    "sample_id",
    "pp_id",
    "contig",
    "mapped_alignments",
    "unmapped_placed",
    "status",
)
STATUS_KEPT = "kept"
STATUS_EXCLUDED = "excluded_non_standard_contig"
# A standard contig left out by --contigs or --remove-mt.
STATUS_NOT_ANALYSED = "excluded_standard_not_analysed"


def contig_read_rows(
    *,
    sample_id: str,
    pp_id: str,
    bam_path: Path,
    standard_chroms: Iterable[str],
    all_standard_chroms: Iterable[str] = (),
) -> list[dict]:
    standard = set(standard_chroms)
    all_standard = set(all_standard_chroms) | standard

    def status(contig: str) -> str:
        if contig in standard:
            return STATUS_KEPT
        return STATUS_NOT_ANALYSED if contig in all_standard else STATUS_EXCLUDED

    with pysam.AlignmentFile(str(bam_path), "rb") as bam:
        if not bam.has_index():
            raise ValueError(f"BAM index is unavailable: {bam_path}")
        stats = bam.get_index_statistics()
    return [
        {
            "sample_id": sample_id,
            "pp_id": pp_id,
            "contig": str(stat.contig),
            "mapped_alignments": int(stat.mapped),
            "unmapped_placed": int(stat.unmapped),
            "status": status(str(stat.contig)),
        }
        for stat in stats
    ]


def summarize_contig_rows(rows: Iterable[Mapping]) -> dict[tuple[str, str], dict]:
    summary: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["sample_id"], row["pp_id"])
        entry = summary.setdefault(
            key,
            {"mapped": 0, "excluded_mapped": 0, "excluded_contigs": 0, "not_analysed_mapped": 0},
        )
        entry["mapped"] += int(row["mapped_alignments"])
        if row["status"] == STATUS_EXCLUDED:
            entry["excluded_mapped"] += int(row["mapped_alignments"])
            if int(row["mapped_alignments"]) > 0:
                entry["excluded_contigs"] += 1
        elif row["status"] == STATUS_NOT_ANALYSED:
            entry["not_analysed_mapped"] += int(row["mapped_alignments"])
    return summary


def write_contig_read_report(rows: Iterable[Mapping], output_tsv: Path) -> None:
    output_tsv = Path(output_tsv)
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_tsv.with_name(f".{output_tsv.name}.tmp")
    with open(temporary, "w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CONTIG_READ_REPORT_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in CONTIG_READ_REPORT_COLUMNS})
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(output_tsv)


def format_contig_exclusion_warning(sample_id: str, pp_id: str, entry: Mapping) -> str:
    mapped = int(entry["mapped"])
    excluded = int(entry["excluded_mapped"])
    percent = 100.0 * excluded / mapped if mapped else 0.0
    return (
        f"[{sample_id}] {pp_id}: {excluded:,} of {mapped:,} mapped alignment records "
        f"({percent:.2f}%) lie on {int(entry['excluded_contigs'])} non-standard contigs "
        "and are excluded from discovery and counting."
        + (
            f" A further {int(entry['not_analysed_mapped']):,} lie on standard contigs "
            "left out by --contigs/--remove-mt."
            if entry.get("not_analysed_mapped")
            else ""
        )
    )
