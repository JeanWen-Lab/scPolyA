from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest

from read_rule_fixture import segment, write_bam
from utils.assignment_diagnostics import (
    AssignmentDiagnostics,
    WindowIndex,
    merge_sample_diagnostics,
    read_peak_windows,
    unit_diagnostics_paths,
)
from utils.feature_sidecar_utils import build_feature_assignment_sidecar
from utils.read_eligibility import ReadRule

import os
import shutil

FEATURECOUNTS = Path(os.environ.get("SCPOLYA_FEATURECOUNTS") or shutil.which("featureCounts") or "featureCounts")
if not FEATURECOUNTS.is_file():
    pytest.skip("featureCounts (Subread) is not available", allow_module_level=True)
TAIL = "C" * 40 + "A" * 12
TAIL_CIGAR = ((0, 40), (4, 12))


def _gtf(path):
    path.write_text(
        "chr1\tt\tpeak\t101\t140\t.\t+\t.\tpeak_name A;\n"
        "chr1\tt\tpeak\t142\t200\t.\t+\t.\tpeak_name B;\n"
        "chr1\tt\tpeak\t1001\t1100\t.\t+\t.\tpeak_name Z;\n"
    )
    return path


def _bam(path):
    return write_bam(path, [
        segment("span", 0, 255, {"NH": 1}, start=120, cigar=((0, 50),)),            # A+B, 3' in B
        segment("inside_a", 0, 255, {"NH": 1}, start=95, cigar=TAIL_CIGAR, seq=TAIL),  # A, tail read
        segment("rescued", 0, 255, {"NH": 2, "mm": 1}, start=150, cigar=TAIL_CIGAR, seq=TAIL),  # B
        segment("past_b", 0, 255, {"NH": 1}, start=180, cigar=((0, 50),)),          # B, 3' end outside
        segment("lowq", 0, 1, {"NH": 1}, start=110, cigar=((0, 20),)),              # A, MAPQ 1
        segment("nowhere", 0, 255, {"NH": 1}, start=3000, cigar=((0, 50),)),        # no window
    ])


def _run(tmp_path, rule, label):
    bam = _bam(tmp_path / "in.bam")
    gtf = _gtf(tmp_path / "w.gtf")
    sidecar = tmp_path / label / "unit.xr_to_peak.tsv.gz"
    paths = unit_diagnostics_paths(sidecar)
    sidecar.parent.mkdir()
    build_feature_assignment_sidecar(
        input_bam=bam, annotation_gtf=gtf, output_sidecar=sidecar, cell_barcode_tag="CB",
        thread_count=1, feature_type="peak", group_attr="peak_name", count_features=True,
        feature_counts_exec=str(FEATURECOUNTS), read_rule=rule,
        diagnostics=AssignmentDiagnostics(read_peak_windows(gtf), read_rule=rule, minpolyA=10, nonA_allowed=1),
        diagnostics_paths=paths,
    )
    with gzip.open(paths[0], "rt") as handle:
        table = {row["peak_name"]: row for row in csv.DictReader(handle, delimiter="\t")}
    return paths, table, json.loads(paths[1].read_text())


def _nonzero(row):
    return {k: int(v) for k, v in row.items() if k not in ("peak_name", "tail_end_positions") and int(v)}


def test_unit_paths_sit_next_to_the_sidecar(tmp_path):
    tsv, summary = unit_diagnostics_paths(tmp_path / "s.c.xr_to_peak.terminal-aware.v2.tsv.gz")
    assert tsv.name == "s.c.xr_to_peak.terminal-aware.v2.assignment.tsv.gz"
    assert summary.name == "s.c.xr_to_peak.terminal-aware.v2.assignment_summary.json"


def test_legacy_diagnostics_explain_zero_counts(tmp_path):
    _, table, summary = _run(tmp_path, None, "legacy")
    assert _nonzero(table["A"]) == {
        "overlap_records": 3, "assigned_reads": 2, "ambiguity_reads": 1,
        "primary_overlap_reads": 3, "tail_reads": 1,
    }
    assert _nonzero(table["B"]) == {
        "overlap_records": 3, "assigned_reads": 1, "ambiguity_reads": 1, "multimapping_reads": 1,
        "primary_overlap_reads": 3, "multimapper_overlap_reads": 1, "tail_reads": 1,
        "multimapper_tail_reads": 1,
    }
    assert "Z" not in table
    assert summary["read_rule"] == {"count_read_rule": "legacy"}
    assert summary["record_reasons"] == {"ambiguity": 1, "assigned": 3, "multimapping": 1, "no_feature": 1}
    assert summary["featurecounts_summary"]["Assigned"] == 3
    assert summary["featurecounts_summary"]["Unassigned_Ambiguity"] == 1


@pytest.mark.parametrize("policy", ["unique", "rescued"])
def test_unified_diagnostics_report_three_prime_and_policy_reasons(tmp_path, policy):
    rule = ReadRule("unified", 30, policy)
    _, table, summary = _run(tmp_path, rule, policy)
    assert _nonzero(table["A"]) == {
        "overlap_records": 3, "assigned_reads": 1, "three_prime_end_in_other_window_reads": 1,
        "mapq_reads": 1, "primary_overlap_reads": 3, "tail_reads": 1,
    }
    rescued_reason = "assigned_reads" if policy == "rescued" else "multimapper_policy_reads"
    expected_b = {
        "overlap_records": 3, "assigned_reads": 1, "three_prime_end_outside_window_reads": 1,
        "primary_overlap_reads": 3, "multimapper_overlap_reads": 1, "tail_reads": 1,
        "multimapper_tail_reads": 1,
    }
    expected_b[rescued_reason] = expected_b.get(rescued_reason, 0) + 1
    assert _nonzero(table["B"]) == expected_b
    reasons = summary["record_reasons"]
    assert reasons.pop("assigned") == (3 if policy == "rescued" else 2)
    assert reasons == {
        "mapq": 1, "no_feature": 1, "three_prime_end_outside_window": 1,
        **({} if policy == "rescued" else {"multimapper_policy": 1}),
    }
    assert summary["read_rule"] == {
        "count_read_rule": "unified", "multimapper_policy": policy, "minMAPQ": 30,
        "count_assignment": "three_prime_end",
    }


def _diagnose(tmp_path, rule, reads, gtf_text, label="d"):
    gtf = tmp_path / f"{label}.gtf"
    gtf.write_text(gtf_text)
    bam = write_bam(tmp_path / f"{label}.bam", reads)
    sidecar = tmp_path / f"{label}.xr_to_peak.tsv.gz"
    paths = unit_diagnostics_paths(sidecar)
    diagnostics = AssignmentDiagnostics(read_peak_windows(gtf), read_rule=rule, minpolyA=10, nonA_allowed=1)
    build_feature_assignment_sidecar(
        input_bam=bam, annotation_gtf=gtf, output_sidecar=sidecar, cell_barcode_tag="CB",
        thread_count=1, feature_type="peak", group_attr="peak_name", count_features=True,
        feature_counts_exec=str(FEATURECOUNTS), read_rule=rule, diagnostics=diagnostics,
        diagnostics_paths=paths,
    )
    out = tmp_path / f"{label}.sample.tsv.gz"
    merge_sample_diagnostics([paths], assigned_umis={}, output_tsv=out, summary_json=tmp_path / f"{label}.json",
                             sample_id="s", windows=read_peak_windows(gtf))
    with gzip.open(out, "rt") as handle:
        rows = {row["peak_name"]: row for row in csv.DictReader(handle, delimiter="\t")}
    return diagnostics, rows


WINDOW_A = "chr1\tt\tpeak\t101\t140\t.\t+\t.\tpeak_name A;\n"


@pytest.mark.parametrize(
    "policy, expected_records, expected_a",
    [
        ("unique",
         {"multimapper_policy": 2, "supplementary": 1},
         {"multimapper_policy_reads": 1, "supplementary_reads": 1}),
        ("rescued",
         {"three_prime_end_outside_window": 1, "supplementary": 1, "no_feature": 1},
         {"three_prime_end_outside_window_reads": 1, "supplementary_reads": 1}),
    ],
)
def test_shared_rule_decides_the_reason_before_featurecounts(tmp_path, policy, expected_records, expected_a):
    # Sol review item 2: ineligible records whose 3' end misses every window
    # (featureCounts: Unassigned_NoFeatures) keep the rule's reason; only an
    # eligible record is "three_prime_end_outside_window".
    reads = [
        segment("multimapper_outside", 0, 255, {"NH": 2, "mm": 1}, start=120, cigar=((0, 50),)),
        segment("supplementary_outside", 0x800, 255, {"NH": 1}, start=120, cigar=((0, 50),)),
        segment("multimapper_no_window", 0, 255, {"NH": 2, "mm": 1}, start=400, cigar=((0, 50),)),
    ]
    diagnostics, _ = _diagnose(tmp_path, ReadRule("unified", 30, policy), reads, WINDOW_A)
    assert dict(diagnostics.records) == expected_records
    counts = {k: v for k, v in diagnostics.per_pas["A"].items() if v and k.endswith("_reads")
              and not k.startswith(("primary", "multimapper_overlap", "tail", "multimapper_tail"))}
    assert counts == expected_a


def test_overlap_assignment_reports_ambiguity_for_eligible_reads(tmp_path):
    gtf = WINDOW_A + "chr1\tt\tpeak\t142\t200\t.\t+\t.\tpeak_name B;\n"
    reads = [
        segment("span", 0, 255, {"NH": 1}, start=120, cigar=((0, 50),)),
        segment("span_multimapper", 0, 255, {"NH": 2, "mm": 1}, start=120, cigar=((0, 50),)),
    ]
    diagnostics, _ = _diagnose(tmp_path, ReadRule("unified", 30, "unique", "overlap"), reads, gtf)
    assert dict(diagnostics.records) == {"ambiguity": 1, "multimapper_policy": 1}


def _tail_read(name, end, *, mapq=255, tags=None):
    # 40 templated bases ending at 1-based ``end`` followed by a 12 nt tail.
    return segment(name, 0, mapq, tags or {"NH": 1}, start=end - 40, cigar=TAIL_CIGAR, seq=TAIL)


TAIL_MODE_READS = [
    _tail_read("unique_120", 120),
    *(_tail_read(f"rescued_130_{i}", 130, tags={"NH": 2, "mm": 1}) for i in range(3)),
    *(_tail_read(f"lowq_125_{i}", 125, mapq=1, tags={"NH": 3}) for i in range(5)),
    # Start inside A (101-140) but the templated end (150) lies outside it.
    *(_tail_read(f"outside_150_{i}", 150) for i in range(6)),
]


@pytest.mark.parametrize(
    "rule, expected",
    [
        (ReadRule("unified", 30, "unique"), ("120", "1")),
        (ReadRule("unified", 30, "rescued"), ("130", "3")),
        (ReadRule("legacy", 30), ("130", "3")),     # extract prefilter: MAPQ >= 30
        (ReadRule("legacy", 0), ("125", "5")),
    ],
    ids=["unique", "rescued", "legacy_q30", "legacy_q0"],
)
def test_tail_end_mode_uses_eligible_reads_ending_inside_the_window(tmp_path, rule, expected):
    # Sol review item 5: excluded low-MAPQ multimappers and tail ends outside
    # the window never set the mode; the support columns still see them all.
    _, rows = _diagnose(tmp_path, rule, TAIL_MODE_READS, WINDOW_A)
    row = rows["A"]
    assert (row["tail_end_mode"], row["tail_end_mode_reads"]) == expected
    assert row["tail_reads"] == "15" and row["multimapper_tail_reads"] == "8"
    assert (row["nearest_window_gap"], row["abutting_fragment"]) == ("", "False")


def test_tail_end_mode_is_empty_without_eligible_in_window_ends(tmp_path):
    reads = [_tail_read(f"outside_{i}", 150) for i in range(3)]
    reads += [_tail_read(f"rescued_{i}", 130, tags={"NH": 2, "mm": 1}) for i in range(3)]
    _, rows = _diagnose(tmp_path, ReadRule("unified", 30, "unique"), reads, WINDOW_A)
    assert (rows["A"]["tail_end_mode"], rows["A"]["tail_end_mode_reads"], rows["A"]["tail_reads"]) == ("", "", "6")


def test_sample_merge_adds_umis_fractions_and_zero_umi_pas(tmp_path):
    pair, _, _ = _run(tmp_path, None, "legacy")
    out = tmp_path / "sample.peak_assignment.tsv.gz"
    summary = merge_sample_diagnostics(
        [pair, pair], assigned_umis={"A": 4}, output_tsv=out,
        summary_json=tmp_path / "sample.peak_assignment_summary.json", sample_id="s1",
        windows=read_peak_windows(_gtf(tmp_path / "w.gtf")),
    )
    with gzip.open(out, "rt") as handle:
        rows = {row["peak_name"]: row for row in csv.DictReader(handle, delimiter="\t")}
    assert rows["A"]["assigned_umis"] == "4" and rows["A"]["assigned_reads"] == "4"
    assert rows["B"]["assigned_umis"] == "0"
    assert float(rows["B"]["multimapper_overlap_fraction"]) == pytest.approx(1 / 3)
    assert rows["B"]["multimapper_tail_fraction"] == "1.0"
    assert rows["Z"]["overlap_records"] == "0" and rows["Z"]["multimapper_tail_fraction"] == ""
    # A ends at 140 and B starts at 142 (1-based): a 1 nt gap; Z is isolated.
    assert (rows["A"]["nearest_window_gap"], rows["A"]["abutting_fragment"]) == ("1", "True")
    assert (rows["B"]["nearest_window_gap"], rows["B"]["abutting_fragment"]) == ("1", "True")
    assert (rows["Z"]["nearest_window_gap"], rows["Z"]["abutting_fragment"]) == ("800", "False")
    # inside_a: 40M at 0-based 95 -> last templated base 135; rescued: 150 + 40 -> 190.
    assert (rows["A"]["tail_end_mode"], rows["A"]["tail_end_mode_reads"]) == ("135", "2")
    assert (rows["B"]["tail_end_mode"], rows["B"]["tail_end_mode_reads"]) == ("190", "2")
    assert rows["Z"]["tail_end_mode"] == ""
    assert summary["assigned_umis_total"] == 4
    assert summary["pas_with_overlapping_reads_but_zero_umis"] == 1  # B
    assert summary["record_reasons"]["assigned"] == 6
    assert summary["incidence_totals"]["ambiguity_reads"] == 4


def test_tail_end_mode_uses_transcript_orientation(tmp_path):
    gtf = tmp_path / "m.gtf"
    gtf.write_text("chr1\tt\tpeak\t101\t300\t.\t-\t.\tpeak_name M;\n")
    rev_tail = "T" * 12 + "C" * 40
    bam = write_bam(tmp_path / "m.bam", [
        segment(f"r{i}", 0x10, 255, {"NH": 1}, start=start, cigar=((4, 12), (0, 40)), seq=rev_tail)
        for i, start in enumerate((150, 150, 160))
    ])
    sidecar = tmp_path / "m.xr_to_peak.tsv.gz"
    paths = unit_diagnostics_paths(sidecar)
    build_feature_assignment_sidecar(
        input_bam=bam, annotation_gtf=gtf, output_sidecar=sidecar, cell_barcode_tag="CB",
        thread_count=1, feature_type="peak", group_attr="peak_name", count_features=True,
        feature_counts_exec=str(FEATURECOUNTS),
        diagnostics=AssignmentDiagnostics(read_peak_windows(gtf), read_rule=None, minpolyA=10, nonA_allowed=1),
        diagnostics_paths=paths,
    )
    out = tmp_path / "m.sample.tsv.gz"
    merge_sample_diagnostics([paths], assigned_umis={}, output_tsv=out,
                             summary_json=tmp_path / "m.json", sample_id="s", windows=read_peak_windows(gtf))
    with gzip.open(out, "rt") as handle:
        row = next(csv.DictReader(handle, delimiter="\t"))
    # reverse reads: the tail precedes the alignment; 0-based 150 -> 1-based 151
    assert (row["tail_end_mode"], row["tail_end_mode_reads"], row["tail_reads"]) == ("151", "2", "3")
    assert (row["nearest_window_gap"], row["abutting_fragment"]) == ("", "False")


def test_window_index_handles_nested_and_overlapping_windows():
    index = WindowIndex({("c", "+"): [(0, 1000, "big"), (10, 20, "small"), (500, 600, "mid")]})
    assert index.overlapping("c", "+", [(700, 710)]) == ["big"]
    assert sorted(index.overlapping("c", "+", [(15, 16), (550, 551)])) == ["big", "mid", "small"]
    assert index.overlapping("c", "-", [(15, 16)]) == []
    assert index.overlapping("c", "+", [(1000, 1005)]) == []


def test_sample_report_clears_stale_output_and_requires_unit_diagnostics(tmp_path):
    from utils.assignment_diagnostics import write_sample_assignment_report

    pair, _, _ = _run(tmp_path, None, "legacy")
    sidecar = tmp_path / "legacy" / "unit.xr_to_peak.tsv.gz"
    out, summary = tmp_path / "s.peak_assignment.tsv.gz", tmp_path / "s.peak_assignment_summary.json"
    report = write_sample_assignment_report([sidecar], assigned_umis={"A": 1}, output_tsv=out,
                                            summary_json=summary, sample_id="s")
    assert out.exists() and summary.exists() and report["assigned_umis_total"] == 1
    pair[1].unlink()  # delete only one unit diagnostics file
    with pytest.raises(FileNotFoundError, match="assignment diagnostics missing"):
        write_sample_assignment_report([sidecar], assigned_umis={"A": 2}, output_tsv=out,
                                       summary_json=summary, sample_id="s")
    assert not out.exists() and not summary.exists()  # no stale report is left behind
