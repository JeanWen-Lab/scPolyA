"""Same-strand mispriming evidence, not-evaluable support and failure publication."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pysam
import pytest

from stages.summarize_matrix import _load_peak_metadata
from utils.annotation_utils import convert_peaks_bed_to_gtf, resolve_peak_issues
from utils.io_utils import convert_bed_to_bigbed
from utils.peak_utils import (
    NOT_EVALUABLE_RATIO_TOKEN,
    not_evaluable_audit_path,
    assess_peak_mispriming,
    combine_and_intersect_peaks,
    filter_peaks_by_mispriming,
    merge_filtered_peaks_posthoc,
    read_resolved_peaks_bed,
)
from utils.terminal_quant_utils import (
    compute_intronic_ipui_tables,
    compute_terminal_aware_tables,
    prepare_terminal_peak_metadata,
)

CHROM_LENGTH = 2000
CLEAN = "GCGTACGTCAGCGTACGTCAGCGTACGTCAGCGTACGTCA"  # 40 nt, no A/T run
A_RUN_END = CLEAN[:30] + "AAAAAAAAAA"  # last 10 aligned bases are A
T_RUN_START = "TTTTTTTTTT" + CLEAN[:30]  # first 10 aligned bases are T


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TailRead:
    name: str
    pos: int  # 0-based leftmost aligned base
    aligned: str
    is_reverse: bool
    tail: int = 12

    def segment(self, header):
        seg = pysam.AlignedSegment(header)
        seg.query_name = self.name
        seg.reference_id = 0
        seg.reference_start = self.pos
        seg.mapping_quality = 60
        seg.is_reverse = self.is_reverse
        if self.is_reverse:
            seg.query_sequence = "T" * self.tail + self.aligned
            seg.cigar = [(4, self.tail), (0, len(self.aligned))]
        else:
            seg.query_sequence = self.aligned + "A" * self.tail
            seg.cigar = [(0, len(self.aligned)), (4, self.tail)]
        seg.query_qualities = pysam.qualitystring_to_array("I" * len(seg.query_sequence))
        return seg


def _write_bam(path: Path, reads, *, index=True) -> Path:
    header = pysam.AlignmentHeader.from_dict(
        {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "chr1", "LN": CHROM_LENGTH}]}
    )
    with pysam.AlignmentFile(str(path), "wb", header=header) as out:
        for read in sorted(reads, key=lambda r: r.pos):
            out.write(read.segment(header))
    if index:
        pysam.index(str(path))
    return path


def _plus_reads():
    # Peak '+' covers [100, 300]; peak '-' covers [620, 900].
    return [
        TailRead("plus_clean", 250, CLEAN, is_reverse=False),
        TailRead("plus_misprimed", 255, A_RUN_END, is_reverse=False),
    ]


def _antisense_reads():
    # Reverse (minus-strand) tail reads inside the '+' peak and forward
    # (plus-strand) tail reads inside the '-' peak.  Under the old all-read
    # rule the first would count as misprimed for the '+' peak.
    return [
        TailRead("anti_in_plus_1", 230, A_RUN_END, is_reverse=True),
        TailRead("anti_in_plus_2", 240, A_RUN_END, is_reverse=True),
        TailRead("anti_in_minus", 700, CLEAN, is_reverse=False),
    ]


@pytest.fixture
def bedtools_on_path(monkeypatch):
    env_bin = Path(sys.executable).parent
    if not (env_bin / "bedtools").exists() and shutil.which("bedtools") is None:
        pytest.skip("bedtools unavailable")
    monkeypatch.setenv("PATH", f"{env_bin}{os.pathsep}{os.environ.get('PATH', '')}")


def _combine_inputs(tmp_path: Path, tail_reads, *, index_tail=True):
    beds = {
        "exp_plus": "chr1\t100\t400\texp_plus\t50\t+\n",
        "exp_minus": "chr1\t600\t900\texp_minus\t50\t-\n",
        "pa_plus": "chr1\t280\t300\tpa_plus\t5\t+\n",
        "pa_minus": "chr1\t620\t640\tpa_minus\t5\t-\n",
    }
    paths = {}
    for key, text in beds.items():
        paths[key] = tmp_path / f"{key}.bed"
        paths[key].write_text(text)
    chrom_sizes = tmp_path / "chrom.sizes"
    chrom_sizes.write_text(f"chr1\t{CHROM_LENGTH}\n")
    tail_bam = _write_bam(tmp_path / "tail.bam", tail_reads, index=index_tail)
    exp_bam = _write_bam(tmp_path / "exp.bam", tail_reads)
    return paths, chrom_sizes, tail_bam, exp_bam


def _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, *, threshold, audit=False):
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    combine_and_intersect_peaks(
        exp_plus_peaks_bed=paths["exp_plus"],
        exp_minus_peaks_bed=paths["exp_minus"],
        polyA_plus_peaks_bed=paths["pa_plus"],
        polyA_minus_peaks_bed=paths["pa_minus"],
        final_output_bed=out,
        polyA_bam=tail_bam,
        exp_bam=exp_bam,
        standardized_chrom_sizes=chrom_sizes,
        standard_chroms={"chr1"},
        merge_distance=0,
        misprime_A_count=8,
        misprime_in=10,
        misprimed_ratio_threshold=threshold,
        include_unassessed_for_audit=audit,
    )
    return out, out.with_name(f"{out.stem}-merged.bed")


def _write_combined(path: Path, rows):
    path.write_text("".join("\t".join(map(str, row)) + "\n" for row in rows))


def _assess(tmp_path, strand, reads):
    combined = tmp_path / f"combined_{strand}.bed"
    _write_combined(combined, [
        ("chr1", 100, 300, "seg_plus", 50, "+"),
        ("chr1", 620, 900, "seg_minus", 50, "-"),
    ])
    tail = _write_bam(tmp_path / f"tail_{len(reads)}_{strand}.bam", reads)
    exp = _write_bam(tmp_path / f"exp_{len(reads)}_{strand}.bam", reads)
    resolved = tmp_path / f"resolved_{len(reads)}_{strand}.bed"
    assess_peak_mispriming(
        str(combined), str(resolved), merge_distance=0, strand=strand,
        misprime_A_count=8, misprime_in=10, polyA_bam=str(tail), original_bam=str(exp),
    )
    return resolved


# --------------------------------------------------------------------------
# function level: strand scope and not-evaluable support
# --------------------------------------------------------------------------

def test_plus_peak_counts_only_forward_tail_reads(tmp_path):
    resolved = _assess(tmp_path, "+", _plus_reads())
    perturbed = _assess(tmp_path, "+", _plus_reads() + _antisense_reads())
    for path in (resolved, perturbed):
        row = read_resolved_peaks_bed(path).iloc[0]
        assert row["total_polyA_reads"] == 2
        assert row["misprimed_ratio"] == pytest.approx(0.5)
    # Only the unstranded expression-read column may differ.
    base = read_resolved_peaks_bed(resolved).drop(columns=["total_reads"])
    pert = read_resolved_peaks_bed(perturbed).drop(columns=["total_reads"])
    pd.testing.assert_frame_equal(base, pert)


def test_minus_peak_counts_only_reverse_tail_reads_and_is_symmetric(tmp_path):
    minus_reads = [
        TailRead("minus_clean", 650, CLEAN, is_reverse=True),
        TailRead("minus_misprimed", 660, T_RUN_START, is_reverse=True),
    ]
    base = read_resolved_peaks_bed(_assess(tmp_path, "-", minus_reads)).iloc[0]
    # Forward reads with a T-rich start must not change the '-' evidence.
    forward_noise = [
        TailRead("fwd_t_start_1", 640, T_RUN_START, is_reverse=False),
        TailRead("fwd_t_start_2", 680, T_RUN_START, is_reverse=False),
    ]
    perturbed = read_resolved_peaks_bed(
        _assess(tmp_path, "-", minus_reads + forward_noise)
    ).iloc[0]
    for row in (base, perturbed):
        assert row["total_polyA_reads"] == 2
        assert row["misprimed_ratio"] == pytest.approx(0.5)


def test_zero_same_strand_support_is_not_evaluable_not_zero(tmp_path):
    # Only antisense reads overlap the '-' peak.
    resolved = _assess(tmp_path, "-", _plus_reads() + [_antisense_reads()[2]])
    fields = resolved.read_text().rstrip("\n").split("\t")
    assert len(fields) == 9
    assert fields[6] == "0"
    assert fields[7] == NOT_EVALUABLE_RATIO_TOKEN
    assert pd.isna(read_resolved_peaks_bed(resolved).loc[0, "misprimed_ratio"])


# --------------------------------------------------------------------------
# failure propagation
# --------------------------------------------------------------------------

class _FailingOnSecondFetch:
    def __init__(self, error):
        self.error = error
        self.calls = 0
        self.closed = False

    def fetch(self, chrom, start, end):
        self.calls += 1
        if self.calls == 2:
            raise self.error
        return iter(())

    def close(self):
        self.closed = True


class _EmptyBam:
    closed = False

    def fetch(self, *args):
        return iter(())

    def close(self):
        self.closed = True


@pytest.mark.parametrize("error", [ValueError("bad contig"), OSError("truncated BAM")])
def test_tail_fetch_error_on_later_peak_raises_original_exception(tmp_path, monkeypatch, error):
    combined = tmp_path / "combined.bed"
    _write_combined(combined, [
        ("chr1", 100, 200, "p1", 5, "+"),
        ("chr1", 300, 400, "p2", 5, "+"),
    ])
    resolved = tmp_path / "resolved.bed"
    tail = _FailingOnSecondFetch(error)
    original = _EmptyBam()
    monkeypatch.setattr(
        "utils.peak_utils.pysam.AlignmentFile",
        lambda path, mode: tail if Path(path).name == "tail.bam" else original,
    )
    with pytest.raises(type(error)) as exc_info:
        assess_peak_mispriming(
            str(combined), str(resolved), merge_distance=0, strand="+",
            misprime_A_count=8, misprime_in=10,
            polyA_bam=str(tmp_path / "tail.bam"), original_bam=str(tmp_path / "exp.bam"),
        )
    assert exc_info.value is error
    assert tail.calls == 2
    assert tail.closed and original.closed
    assert not resolved.exists()


def _published(out):
    return [out, out.with_name(f"{out.stem}-merged.bed"), not_evaluable_audit_path(out)]


def _write_old_set(out):
    out.parent.mkdir(parents=True, exist_ok=True)
    contents = {}
    for path in _published(out):
        path.write_text(f"old complete {path.name}\n")
        contents[path] = path.read_text()
    return contents


def _no_leftovers(out):
    hidden = [p.name for p in out.parent.iterdir() if p.name.startswith(".")]
    assert hidden == [], hidden


@pytest.mark.parametrize("with_old_set", [False, True])
def test_stage_failure_publishes_nothing_and_keeps_previous_outputs(tmp_path, bedtools_on_path, with_old_set):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(
        tmp_path, _plus_reads(), index_tail=False
    )
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    old = _write_old_set(out) if with_old_set else {}
    # A tail BAM without an index makes pysam fetch raise ValueError.
    with pytest.raises(ValueError):
        _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)
    for path in _published(out):
        if with_old_set:
            assert path.read_text() == old[path]
        else:
            assert not path.exists()
    _no_leftovers(out)


def test_input_validation_failure_keeps_previous_outputs(tmp_path, bedtools_on_path):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(tmp_path, _plus_reads())
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    old = _write_old_set(out)
    paths["pa_minus"].unlink()
    with pytest.raises(FileNotFoundError):
        _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)
    assert all(path.read_text() == old[path] for path in _published(out))


@pytest.mark.parametrize("with_old_set", [False, True])
@pytest.mark.parametrize("failing_publish", [2, 3])
def test_publication_failure_rolls_back_to_previous_set(
    tmp_path, bedtools_on_path, monkeypatch, with_old_set, failing_publish
):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(
        tmp_path, _plus_reads() + _antisense_reads()
    )
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    old = _write_old_set(out) if with_old_set else {}
    staging_marker = f".{out.name}.staging."
    real_replace = os.replace
    publishes = []
    injected = OSError("injected publish failure")

    def flaky_replace(src, dst):
        if staging_marker in str(src):  # staged file -> published destination
            publishes.append(Path(dst).name)
            if len(publishes) == failing_publish:
                raise injected
        return real_replace(src, dst)

    monkeypatch.setattr("utils.peak_utils.os.replace", flaky_replace)
    with pytest.raises(OSError) as exc_info:
        _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)
    assert exc_info.value is injected
    assert len(publishes) == failing_publish
    for path in _published(out):
        if with_old_set:
            assert path.read_text() == old[path]
        else:
            assert not path.exists()
    _no_leftovers(out)


def test_rollback_failure_keeps_previous_set_recoverable(tmp_path, bedtools_on_path, monkeypatch):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(tmp_path, _plus_reads())
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    old = _write_old_set(out)
    real_replace = os.replace
    injected = OSError("injected publish failure")

    def flaky_replace(src, dst):
        if f".{out.name}.staging." in str(src) and Path(dst) == out:
            raise injected
        if ".publish-backup." in str(src):  # restoring a backup also fails
            raise OSError("injected restore failure")
        return real_replace(src, dst)

    monkeypatch.setattr("utils.peak_utils.os.replace", flaky_replace)
    with pytest.raises(OSError) as exc_info:
        _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)
    assert exc_info.value is injected
    backups = [p for p in out.parent.iterdir() if ".publish-backup." in p.name]
    assert len(backups) == 1
    assert sorted(p.name for p in backups[0].iterdir()) == sorted(p.name for p in _published(out))
    for path in _published(out):
        assert (backups[0] / path.name).read_text() == old[path]


def test_normal_publication_replaces_previous_set(tmp_path, bedtools_on_path):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(
        tmp_path, _plus_reads() + _antisense_reads()
    )
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    old = _write_old_set(out)
    _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)
    for path in _published(out):
        assert path.read_text() != old[path]
    assert out.read_text().split("\t")[5] == "+"
    assert not_evaluable_audit_path(out).read_text().split("\t")[7] == NOT_EVALUABLE_RATIO_TOKEN
    _no_leftovers(out)


# --------------------------------------------------------------------------
# stage level normal path on a real BAM fixture
# --------------------------------------------------------------------------

@pytest.mark.parametrize("threshold", [1.0, 0.6])
def test_stage_excludes_not_evaluable_by_default_and_records_audit(tmp_path, bedtools_on_path, threshold):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(
        tmp_path, _plus_reads() + _antisense_reads()
    )
    out, merged = _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=threshold)
    rows = [line.split("\t") for line in out.read_text().splitlines()]
    assert all(len(row) == 9 for row in rows)
    assert [row[5] for row in rows] == ["+"]
    assert rows[0][6] == "2" and float(rows[0][7]) == pytest.approx(0.5)
    audit_rows = [line.split("\t") for line in not_evaluable_audit_path(out).read_text().splitlines()]
    assert [(row[5], row[6], row[7]) for row in audit_rows] == [("-", "0", NOT_EVALUABLE_RATIO_TOKEN)]
    assert [line.split("\t")[5] for line in merged.read_text().splitlines()] == ["+"]


def test_stage_audit_pass_through_is_explicit_and_kept_out_of_merged_view(tmp_path, bedtools_on_path):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(
        tmp_path, _plus_reads() + _antisense_reads()
    )
    out, merged = _run_combine(
        tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0, audit=True
    )
    by_strand = {row[5]: row for row in (line.split("\t") for line in out.read_text().splitlines())}
    assert by_strand["+"][6] == "2" and float(by_strand["+"][7]) == pytest.approx(0.5)
    assert by_strand["-"][6] == "0" and by_strand["-"][7] == NOT_EVALUABLE_RATIO_TOKEN
    assert [line.split("\t")[5] for line in merged.read_text().splitlines()] == ["+"]


# --------------------------------------------------------------------------
# filter and merge semantics
# --------------------------------------------------------------------------

def _resolved_rows(path: Path, rows):
    _write_combined(path, rows)
    return path


@pytest.mark.parametrize(
    ("threshold", "audit", "expected"),
    [
        (1.0, False, ["a", "b"]),
        (0.2, False, ["a"]),
        (1.0, True, ["a", "b", "na"]),
        (0.2, True, ["a", "na"]),
    ],
)
def test_filter_excludes_not_evaluable_unless_audit_is_explicit(tmp_path, threshold, audit, expected):
    src = _resolved_rows(tmp_path / "in.bed", [
        ("NA", 10, 20, "a", 5, "+", 3, 0.1, 4),
        ("chr1", 30, 40, "b", 5, "+", 3, 0.5, 4),
        ("chr1", 50, 60, "na", 5, "+", 0, NOT_EVALUABLE_RATIO_TOKEN, 4),
    ])
    dst = tmp_path / "out.bed"
    audit_bed = tmp_path / "audit.bed"
    filter_peaks_by_mispriming(
        src, dst, threshold=threshold,
        include_unassessed_for_audit=audit, not_evaluable_audit_bed=audit_bed,
    )
    kept = read_resolved_peaks_bed(dst)
    assert list(kept["name"]) == expected
    assert kept.loc[kept["name"].eq("a"), "chrom"].item() == "NA"  # contig text kept
    assert not kept.loc[kept["name"].eq("na"), "misprimed_ratio"].fillna(-1).ge(0).any()
    assert [line.split("\t")[3] for line in audit_bed.read_text().splitlines()] == ["na"]
    if audit:
        assert dst.read_text().splitlines()[-1].split("\t")[7] == NOT_EVALUABLE_RATIO_TOKEN


def test_posthoc_merge_never_joins_or_widens_with_unsupported_peaks(tmp_path):
    src = _resolved_rows(tmp_path / "in.bed", [
        ("chr1", 10, 20, "e1", 5, "+", 4, 0.25, 9),
        ("chr1", 20, 30, "na_bridge", 5, "+", 0, NOT_EVALUABLE_RATIO_TOKEN, 9),
        ("chr1", 30, 40, "e2", 5, "+", 4, 0.75, 9),
        ("chr1", 40, 60, "na_extend", 5, "+", 0, NOT_EVALUABLE_RATIO_TOKEN, 9),
        ("chr1", 100, 110, "na_alone", 5, "+", 0, NOT_EVALUABLE_RATIO_TOKEN, 9),
    ])
    dst = tmp_path / "merged.bed"
    merge_filtered_peaks_posthoc(src, dst, merge_distance=0)
    merged = read_resolved_peaks_bed(dst).sort_values("start").reset_index(drop=True)
    # The unsupported bridge cannot join e1 and e2, nor extend e2 to 60.
    assert merged[["start", "end"]].values.tolist() == [[10, 20], [30, 40]]
    assert merged["misprimed_ratio"].tolist() == pytest.approx([0.25, 0.75])
    assert merged["misprimed_ratio"].notna().all()

    dst_wide = tmp_path / "merged_wide.bed"
    merge_filtered_peaks_posthoc(src, dst_wide, merge_distance=15)
    wide = read_resolved_peaks_bed(dst_wide)
    assert wide[["start", "end"]].values.tolist() == [[10, 40]]
    assert wide.loc[0, "total_polyA_reads"] == 8
    assert wide.loc[0, "misprimed_ratio"] == pytest.approx(0.5)


# --------------------------------------------------------------------------
# annotation and terminal-aware consumers
# --------------------------------------------------------------------------

ANNOTATED_COLUMNS = [
    "chrom", "start", "end", "peak_name", "max_cov", "strand", "total_polyA_reads",
    "misprimed_ratio", "total_reads", "gene_name", "distance_to_gene",
    "canonical_transcript", "stop_codon_position", "distance_to_stop_codon", "feature", "gene_id",
]


def _annotated_frame():
    rows = [
        ["chr1", 100, 110, "t_ok", 10, "+", 4, 0.0, 5, "G", 0, "TX", 90, 20, "Terminal_exon", "ENSG1"],
        ["chr1", 200, 210, "t_ok2", 10, "+", 4, 0.1, 5, "G", 0, "TX", 90, 120, "Downstream_of_terminal_exon", "ENSG1"],
        ["chr1", 300, 310, "t_na", 10, "+", 0, float("nan"), 5, "G", 0, "TX", 90, 220, "Terminal_exon", "ENSG1"],
        ["chr1", 50, 60, "i_ok", 10, "+", 4, 0.1, 5, "G", 0, "TX", 90, -30, "Intron", "ENSG1"],
        ["chr1", 70, 80, "i_na", 10, "+", 0, float("nan"), 5, "G", 0, "TX", 90, -10, "Intron", "ENSG1"],
    ]
    return pd.DataFrame(rows, columns=ANNOTATED_COLUMNS)


def test_annotated_bed_and_gtf_keep_token_and_exclude_not_evaluable_counting(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "utils.annotation_utils.bedtools_sort",
        lambda input_bed, sorted_bed, chrom_sizes_sorted: shutil.copyfile(input_bed, sorted_bed),
    )
    chrom_sizes = tmp_path / "chrom.sizes"
    chrom_sizes.write_text("chr1\t1000\n")
    frame = _annotated_frame().rename(columns={"stop_codon_position": "stop_codon_pos"})
    frame["name"] = frame["peak_name"]
    bed = tmp_path / "annotated.bed"
    resolve_peak_issues(frame, output_bed=bed, chrom_sizes_sorted=chrom_sizes, include_gene_id=True)
    lines = [line.split("\t") for line in bed.read_text().splitlines()]
    assert len(lines) == 5
    assert all(len(fields) == 16 for fields in lines)
    unsupported = [fields for fields in lines if fields[6] == "0"]
    assert [fields[7] for fields in unsupported] == [NOT_EVALUABLE_RATIO_TOKEN] * 2
    assert "" not in [fields[7] for fields in lines]

    gtf = tmp_path / "annotated.gtf"
    convert_peaks_bed_to_gtf(bed, gtf, non_intronic_misprimed_ratio_threshold=1.0)
    text = gtf.read_text()
    # Non-intronic not-evaluable PAS is never counted, even with threshold 1.
    assert text.count("feature Terminal_exon;") == 1
    assert "feature Downstream_of_terminal_exon;" in text
    # Intronic PASs without support are not counted either; they stay in the
    # annotated BED (and the iPUI audit table) only.
    assert text.count("feature Intron;") == 1
    assert len(text.splitlines()) == 3
    assert f"misprimed_ratio {NOT_EVALUABLE_RATIO_TOKEN};" not in text
    assert "misprimed_ratio nan;" not in text

    loaded = _load_peak_metadata(bed)
    assert loaded["misprimed_ratio"].isna().sum() == 2


def test_terminal_psi_and_ipui_never_use_not_evaluable_pas():
    meta = _annotated_frame()
    meta["three_prime_utr_length"] = meta["distance_to_stop_codon"].clip(lower=0)
    prepared = prepare_terminal_peak_metadata(meta, general_threshold=1.0, intronic_threshold=1.0)
    assert set(prepared["terminal_all"]["peak_name"]) == {"t_ok", "t_ok2"}
    assert prepared["stats"]["terminal_pas_not_evaluable"] == 1
    assert prepared["stats"]["intronic_pas_not_evaluable"] == 1
    audit = prepared["audit"].set_index("peak_name")
    assert not audit.loc["t_na", "mispriming_pass"]
    assert not audit.loc["i_na", "mispriming_pass"]
    assert pd.isna(audit.loc["t_na", "misprimed_ratio"])  # not filled with 0

    counts = pd.DataFrame(
        [("t_ok", "c1", 3), ("t_ok2", "c1", 1), ("t_na", "c1", 50), ("i_ok", "c1", 2), ("i_na", "c1", 40)],
        columns=["peak_name", "cell_barcode", "umi_count"],
    )
    result = compute_terminal_aware_tables(
        counts, meta, general_threshold=1.0, intronic_threshold=1.0, psi_min_gene_umis=1,
    )
    psi = result["psi"]
    assert set(psi["peak_name"]) == {"t_ok", "t_ok2"}
    assert psi["gene_umi_count"].unique().tolist() == [4]

    per_site = result["per_site"].set_index("intronic_pas_id")
    assert pd.isna(per_site.loc["i_na", "iPUI_i"])
    assert pd.isna(per_site.loc["i_na", "misprimed_ratio"])
    assert not per_site.loc["i_na", "mispriming_pass"]
    gene_total = result["gene_total"].iloc[0]
    assert gene_total["I_total"] == 2
    assert gene_total["retained_intronic_pas_count"] == 1
    assert gene_total["U"] == 4


# --------------------------------------------------------------------------
# end to end: 9-column resolved BED -> assignment -> annotation -> GTF
# --------------------------------------------------------------------------

def test_not_evaluable_and_contig_text_survive_assignment_annotation_and_gtf(tmp_path, bedtools_on_path):
    import gffutils

    from utils.annotation_utils import (
        annotate_peaks_with_features,
        assign_peaks_to_genes,
        get_canonical_transcripts,
        get_genomic_features,
        get_stop_codon_positions,
        load_peak_assignments,
        merge_stop_codon_info,
    )
    from utils.io_utils import convert_gtf_to_bed

    specs = (
        # contig, gene_id, strand, exons; the last exon is terminal
        # Listed in genome-file order (022 before NA) as sorted references are.
        ("022", "G022", "-", ((100, 400), (500, 580))),
        ("NA", "GNA", "+", ((100, 180), (300, 600))),
    )
    rows = []
    for contig, gene_id, strand, exons in specs:
        attrs = f'gene_id "{gene_id}"; gene_name "{gene_id}_name";'
        tx = f'gene_id "{gene_id}"; transcript_id "{gene_id}.1"; gene_name "{gene_id}_name";'
        start, end = exons[0][0], exons[-1][1]
        rows.append([contig, "t", "gene", start, end, ".", strand, ".", attrs])
        rows.append([contig, "t", "transcript", start, end, ".", strand, ".", tx])
        for exon_start, exon_end in exons:
            rows.append([contig, "t", "exon", exon_start, exon_end, ".", strand, ".", tx])
        cds_exon = exons[0] if strand == "+" else exons[-1]
        rows.append([contig, "t", "CDS", cds_exon[0] + 10, cds_exon[1] - 10, ".", strand, "0", tx])
    gtf = tmp_path / "genes.gtf"
    gtf.write_text("".join("\t".join(map(str, r)) + "\n" for r in rows))
    db_path = tmp_path / "genes.db"
    gffutils.create_db(str(gtf), dbfn=str(db_path), force=True, keep_order=True,
                       disable_infer_genes=True, disable_infer_transcripts=True)
    db = gffutils.FeatureDB(str(db_path), keep_order=True)
    canonical = get_canonical_transcripts(db)
    stop_codons = get_stop_codon_positions(db, canonical)
    features = get_genomic_features(db, canonical)

    gene_bed = tmp_path / "genes.bed"
    convert_gtf_to_bed(gtf, gene_bed)
    chrom_sizes = tmp_path / "chrom.sizes"
    chrom_sizes.write_text("022\t2000\nNA\t2000\n")
    bulk_gtf = tmp_path / "bulk.gtf"
    bulk_gtf.write_text(
        '022\tbulk\tgene_locus\t1\t600\t.\t-\t.\tgene_id "G022"; gene_name "G022_name";\n'
        'NA\tbulk\tgene_locus\t90\t700\t.\t+\t.\tgene_id "GNA"; gene_name "GNA_name";\n'
    )

    # Resolved 9-column peaks as call_peaks publishes them (sorted by genome file).
    peaks = tmp_path / "final.bed"
    peaks.write_text(
        "022\t100\t130\tm_terminal\t5\t-\t3\t0.0\t9\n"
        "022\t150\t170\tm_terminal_na\t5\t-\t0\tNA\t9\n"
        "NA\t200\t250\tp_intron_na\t5\t+\t0\tNA\t9\n"
        "NA\t500\t560\tp_terminal\t5\t+\t4\t0.25\t9\n"
        "NA\t570\t590\tp_terminal_na\t5\t+\t0\tNA\t9\n"
    )
    assigned = tmp_path / "assigned.bed"
    assign_peaks_to_genes(
        peaks_bed=peaks, gene_bed=gene_bed, chrom_sizes=chrom_sizes, assigned_peaks_bed=assigned,
        bulk_gene_gtf=bulk_gtf, include_gene_id=True, identity_gtf=gtf,
    )
    assigned_rows = [line.split("\t") for line in assigned.read_text().splitlines()]
    assert {row[0] for row in assigned_rows} == {"NA", "022"}
    assert sorted(row[7] for row in assigned_rows if row[6] == "0") == [NOT_EVALUABLE_RATIO_TOKEN] * 3

    loaded = load_peak_assignments(assigned)
    assert set(loaded["chrom"]) == {"NA", "022"}
    assert loaded.loc[loaded["total_polyA_reads"].eq(0), "misprimed_ratio"].isna().all()
    assert loaded.loc[loaded["total_polyA_reads"].gt(0), "misprimed_ratio"].notna().all()

    merged = merge_stop_codon_info(loaded[loaded["gate"].eq("PASS")].copy(), canonical, stop_codons)
    annotated = annotate_peaks_with_features(merged, features)
    feature_by_coord = dict(zip(zip(annotated["chrom"], annotated["start"]), annotated["feature"]))
    assert feature_by_coord[("NA", 200)] == "Intron"
    assert feature_by_coord[("NA", 500)] == "Terminal_exon"
    assert feature_by_coord[("022", 100)] == "Terminal_exon"

    annotated_bed = tmp_path / "annotated.bed"
    resolve_peak_issues(annotated, output_bed=annotated_bed, chrom_sizes_sorted=chrom_sizes, include_gene_id=True)
    annotated_rows = [line.split("\t") for line in annotated_bed.read_text().splitlines()]
    assert all(len(row) == 16 for row in annotated_rows)
    assert {row[0] for row in annotated_rows} == {"NA", "022"}
    assert sorted(row[7] for row in annotated_rows if row[6] == "0") == [NOT_EVALUABLE_RATIO_TOKEN] * 3
    assert {row[15] for row in annotated_rows} == {"GNA", "G022"}

    gtf_out = tmp_path / "counting.gtf"
    convert_peaks_bed_to_gtf(annotated_bed, gtf_out, non_intronic_misprimed_ratio_threshold=1.0)
    gtf_rows = [line.split("\t") for line in gtf_out.read_text().splitlines()]
    # Only the two supported terminal PASs are countable; contig text is kept.
    assert sorted((row[0], row[3]) for row in gtf_rows) == [("022", "101"), ("NA", "501")]
    assert all(f"misprimed_ratio {NOT_EVALUABLE_RATIO_TOKEN}" not in row[8] for row in gtf_rows)


def test_gtf_conversion_keeps_missing_gene_id_missing_and_contig_text(tmp_path):
    bed = tmp_path / "annotated.bed"
    bed.write_text(
        "NA\t10\t20\tp1\t5\t+\t3\t0.1\t9\tG\t0\tTX\t30\t5\tTerminal_exon\tNA\n"
        "NA\t40\t50\tp2\t5\t+\t3\t0.1\t9\tG\t0\tTX\t30\t25\tTerminal_exon\tGREAL\n"
    )
    gtf = tmp_path / "out.gtf"
    convert_peaks_bed_to_gtf(bed, gtf, non_intronic_misprimed_ratio_threshold=1.0)
    lines = gtf.read_text().splitlines()
    assert [line.split("\t")[0] for line in lines] == ["NA", "NA"]
    assert "gene_id" not in lines[0]  # missing identity is not a gene called "NA"
    assert lines[1].endswith("gene_id GREAL;")


# --------------------------------------------------------------------------
# task level: a failed combine is not marked DONE and is retried
# --------------------------------------------------------------------------

def test_failed_combine_task_is_not_done_and_retries(tmp_path, bedtools_on_path):
    from task_manage import Plan, TaskManager
    from task_manage.run_layout import RunLayout
    from task_manage.task_runtime import run_flex_batch

    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(
        tmp_path, _plus_reads() + _antisense_reads(), index_tail=False
    )
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    item = {
        "dedup_method": "directional",
        "polyA_status": "polyA-10-1-0",
        "coverage_scaling": "none",
        "depth_threshold": "1",
        "depth_threshold_polyA": "1",
        "merge_distance": 0,
        "merge_distance_polyA": 0,
        "misprime_A_count": 8,
        "misprime_in": 10,
        "misprimed_ratio_threshold": 1.0,
        "exp_peak_threshold_mode": "fixed",
        "polyA_peak_threshold_mode": "fixed",
        "exp_dynamic_threshold_stat": None,
        "polyA_dynamic_threshold_stat": None,
        "exp_dynamic_threshold_alpha": 1.0,
        "polyA_dynamic_threshold_alpha": 1.0,
        "exp_dynamic_threshold_floor": 0.0,
        "polyA_dynamic_threshold_floor": 0.0,
        "exp_dynamic_threshold_normalized": 0.3,
        "polyA_dynamic_threshold_normalized": 0.3,
        "quantification_mode": "legacy",
    }
    calls = []

    def worker(_item):
        calls.append(1)
        _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)

    layout = RunLayout(out=tmp_path / "run", run_id="m1", artifact_naming_mode="short")
    tm = TaskManager(state_dir=layout.State, config={"run_id": "m1"})
    tm.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))

    def run():
        run_flex_batch(
            tm=tm, stage="call_peaks", task_base="combine_and_intersect_peaks", items=[item],
            identity_keys=["dedup_method", "polyA_status", "coverage_scaling"],
            param_keys=["misprimed_ratio_threshold"], fn=worker, max_workers=1,
        )

    from task_manage.pipeline_contracts import build_current_task_instance_contract_from_keys_v2
    from task_manage.task_coordinate import build_task_coordinate_v1_from_contract
    from task_manage.task_runtime import stage_level
    from task_manage.task_specs import task_specs_by_key

    spec = task_specs_by_key()[("call_peaks", "combine_and_intersect_peaks")]
    coordinate = build_task_coordinate_v1_from_contract(
        build_current_task_instance_contract_from_keys_v2(
            stage="call_peaks", task_base="combine_and_intersect_peaks", item=item,
            identity_keys=spec.identity_keys, param_keys=spec.param_keys,
            level=stage_level("call_peaks"),
        )
    )

    with pytest.raises(Exception, match="ValueError|index"):
        run()  # unindexed tail BAM: fetch raises inside the worker
    assert len(calls) == 1
    assert not out.exists()
    assert not tm.check_task_done(coordinate)  # failure leaves no DONE record

    pysam.index(str(tail_bam))
    run()  # not DONE after the failure, so the task executes again
    assert len(calls) == 2
    assert out.is_file()
    assert tm.check_task_done(coordinate)  # DONE recorded only after success


# --------------------------------------------------------------------------
# cleanup never masks the primary exception and never drops a backup
# --------------------------------------------------------------------------

def _break_cleanup(monkeypatch, which):
    """Make backup and/or staging directory removal raise."""
    errors = []

    def failing_rmtree(path, *args, **kwargs):
        errors.append(path)
        raise OSError(f"injected rmtree failure: {path}")

    def failing_rmdir(path, *args, **kwargs):
        errors.append(path)
        raise OSError(f"injected rmdir failure: {path}")

    if which in ("staging", "both"):
        monkeypatch.setattr("utils.peak_utils.shutil.rmtree", failing_rmtree)
    if which in ("backup", "both"):
        monkeypatch.setattr("utils.peak_utils.os.rmdir", failing_rmdir)
    return errors


@pytest.mark.parametrize("which", ["backup", "staging", "both"])
def test_cleanup_failure_after_rollback_keeps_publication_exception(
    tmp_path, bedtools_on_path, monkeypatch, which
):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(tmp_path, _plus_reads())
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    old = _write_old_set(out)
    real_replace = os.replace
    injected = OSError("injected publish failure")

    def flaky_replace(src, dst):
        if f".{out.name}.staging." in str(src) and Path(dst) == out:
            raise injected
        return real_replace(src, dst)

    monkeypatch.setattr("utils.peak_utils.os.replace", flaky_replace)
    cleanup_errors = _break_cleanup(monkeypatch, which)
    with pytest.raises(OSError) as exc_info:
        _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)
    assert exc_info.value is injected
    assert cleanup_errors  # the injected cleanup failure really happened
    # Rollback already restored the previous set in place.
    assert all(path.read_text() == old[path] for path in _published(out))
    # A leftover backup directory (when its rmdir failed) is empty: nothing lost.
    for leftover in out.parent.glob(".*publish-backup.*"):
        assert list(leftover.iterdir()) == []


@pytest.mark.parametrize("which", ["backup", "staging", "both"])
def test_cleanup_failure_after_tail_exception_keeps_tail_exception(
    tmp_path, bedtools_on_path, monkeypatch, which
):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(tmp_path, _plus_reads())
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    old = _write_old_set(out)
    injected = OSError("injected tail fetch failure")

    class _FailingTail(_EmptyBam):
        def fetch(self, *args):
            raise injected

    monkeypatch.setattr(
        "utils.peak_utils.pysam.AlignmentFile",
        lambda path, mode: _FailingTail() if Path(path) == tail_bam else _EmptyBam(),
    )
    _break_cleanup(monkeypatch, which)
    with pytest.raises(OSError) as exc_info:
        _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)
    assert exc_info.value is injected
    assert all(path.read_text() == old[path] for path in _published(out))
    assert not list(out.parent.glob(".*publish-backup.*"))  # publication never started


def test_rollback_failure_with_cleanup_failure_keeps_backup_and_exception(
    tmp_path, bedtools_on_path, monkeypatch
):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(tmp_path, _plus_reads())
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    old = _write_old_set(out)
    real_replace = os.replace
    injected = OSError("injected publish failure")

    def flaky_replace(src, dst):
        if f".{out.name}.staging." in str(src) and Path(dst) == out:
            raise injected
        if ".publish-backup." in str(src):
            raise OSError("injected restore failure")
        return real_replace(src, dst)

    monkeypatch.setattr("utils.peak_utils.os.replace", flaky_replace)
    _break_cleanup(monkeypatch, "both")
    with pytest.raises(OSError) as exc_info:
        _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)
    assert exc_info.value is injected
    (backup,) = out.parent.glob(".*publish-backup.*")
    for path in _published(out):
        assert (backup / path.name).read_text() == old[path]


def test_cleanup_failure_after_successful_commit_is_logged_not_raised(
    tmp_path, bedtools_on_path, monkeypatch
):
    paths, chrom_sizes, tail_bam, exp_bam = _combine_inputs(
        tmp_path, _plus_reads() + _antisense_reads()
    )
    out = tmp_path / "out" / "final.___peaks-mispriming-filtered.bed"
    old = _write_old_set(out)
    # On success both the backup and the staging directory go through rmtree.
    errors = _break_cleanup(monkeypatch, "both")
    _run_combine(tmp_path, paths, chrom_sizes, tail_bam, exp_bam, threshold=1.0)
    assert errors
    for path in _published(out):
        assert path.read_text() != old[path]
    assert len(read_resolved_peaks_bed(out)) >= 1
    # The superseded set stays in the leftover backup directory, never half-deleted.
    (backup,) = out.parent.glob(".*publish-backup.*")
    for path in _published(out):
        assert (backup / path.name).read_text() == old[path]


# --------------------------------------------------------------------------
# readers outside the peak stage keep contig/peak text
# --------------------------------------------------------------------------

_TEXT_CONTIG_ANNOTATED = (
    "022\t100\t130\tm1\t5\t-\t3\t0.0\t9\tG\t0\tTX\t30\t5\tTerminal_exon\tG022\n"
    "NA\t500\t560\tNA\t5\t+\t4\tNA\t9\tG\t0\tTX\t30\t25\tTerminal_exon\tNA\n"
)


def test_bigbed_conversion_keeps_text_contigs_with_real_tool(tmp_path, bedtools_on_path):
    if shutil.which("bedToBigBed") is None:
        pytest.skip("bedToBigBed unavailable")
    pyBigWig = pytest.importorskip("pyBigWig")
    bed = tmp_path / "annotated.bed"
    bed.write_text(_TEXT_CONTIG_ANNOTATED)
    sizes = tmp_path / "chrom.sizes"
    sizes.write_text("022\t2000\nNA\t2000\n")
    bb = tmp_path / "annotated.bb"
    convert_bed_to_bigbed(bed, bb, sizes)
    handle = pyBigWig.open(str(bb))
    try:
        assert handle.isBigBed()
        assert handle.chroms() == {"022": 2000, "NA": 2000}
        rows = {chrom: handle.entries(chrom, 0, 2000) for chrom in ("022", "NA")}
    finally:
        handle.close()
    assert [(s, e, rest.split("\t")[0]) for s, e, rest in rows["022"]] == [(100, 130, "m1")]
    assert [(s, e, rest.split("\t")[0]) for s, e, rest in rows["NA"]] == [(500, 560, "NA")]


def test_summary_peak_metadata_keeps_contig_text_and_missing_semantics(tmp_path):
    bed = tmp_path / "annotated.bed"
    bed.write_text(_TEXT_CONTIG_ANNOTATED)
    meta = _load_peak_metadata(bed)
    assert meta["chrom"].tolist() == ["022", "NA"]
    assert meta["peak_name"].tolist() == ["m1", "NA"]
    assert meta["gene_id"].iloc[0] == "G022"
    assert pd.isna(meta["gene_id"].iloc[1])  # missing identity, not a gene called "NA"
    assert meta["misprimed_ratio"].iloc[0] == 0.0
    assert pd.isna(meta["misprimed_ratio"].iloc[1])
    for column in ("start", "end", "max_cov", "total_polyA_reads", "distance_to_stop_codon", "peak_length"):
        assert pd.api.types.is_integer_dtype(meta[column]), column
    assert pd.api.types.is_float_dtype(meta["misprimed_ratio"])
