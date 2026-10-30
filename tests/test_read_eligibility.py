from __future__ import annotations

import gzip
import os
import subprocess
from argparse import Namespace
from pathlib import Path

import pysam
import shutil

import pytest

from read_rule_fixture import ELIGIBILITY_CASES, eligibility_bam, segment, write_bam
from utils import feature_sidecar_utils
from utils.bedtools_utils import bedtools_generate_coverage
from utils.feature_sidecar_utils import build_feature_assignment_sidecar
from utils.polya_extract import extract_polya_bam
from utils.read_eligibility import (
    REASON_MAPQ,
    REASON_MULTIMAPPER,
    REASON_SECONDARY,
    REASON_SUPPLEMENTARY,
    ReadRule,
    read_rule_from_args,
)

ENV_BIN = Path(os.environ.get("SCPOLYA_TOOL_BIN") or Path(shutil.which("samtools") or "/nonexistent/samtools").parent)
SAMTOOLS = ENV_BIN / "samtools"
FEATURECOUNTS = ENV_BIN / "featureCounts"
if not (SAMTOOLS.is_file() and FEATURECOUNTS.is_file()):
    pytest.skip("samtools and featureCounts are required", allow_module_level=True)

UNIQUE = ReadRule("unified", 30, "unique")
RESCUED = ReadRule("unified", 30, "rescued")

EXPECTED_REASONS = {
    "unique": (None, None),
    "no_tags": (None, None),
    "low_mapq": (REASON_MAPQ, REASON_MAPQ),
    "rescued": (REASON_MULTIMAPPER, None),
    "rescued_nh1": (REASON_MULTIMAPPER, None),
    "mm0_nh2": (REASON_MULTIMAPPER, None),
    "multi_low": (REASON_MAPQ, REASON_MAPQ),
    "secondary": (REASON_SECONDARY, REASON_SECONDARY),
    "supplementary": (REASON_SUPPLEMENTARY, REASON_SUPPLEMENTARY),
    "duplicate": (None, None),
    "reverse": (None, None),
}


def _names(bam):
    with pysam.AlignmentFile(str(bam), "rb") as handle:
        return sorted(r.query_name for r in handle.fetch(until_eof=True))


def test_rejection_reasons_cover_every_case(tmp_path):
    bam = eligibility_bam(tmp_path / "e.bam")
    with pysam.AlignmentFile(str(bam), "rb") as handle:
        got = {r.query_name: (UNIQUE.rejection_reason(r), RESCUED.rejection_reason(r)) for r in handle}
    assert got == EXPECTED_REASONS


@pytest.mark.parametrize("rule", [UNIQUE, RESCUED], ids=["unique", "rescued"])
def test_samtools_filter_matches_python_predicate(tmp_path, rule):
    bam = eligibility_bam(tmp_path / "e.bam")
    out = subprocess.run(
        [str(SAMTOOLS), "view", *rule.samtools_view_filter_args(), str(bam)],
        check=True, capture_output=True, text=True,
    ).stdout
    via_samtools = sorted(line.split("\t")[0] for line in out.splitlines())
    with pysam.AlignmentFile(str(bam), "rb") as handle:
        via_python = sorted(r.query_name for r in handle if rule.is_eligible(r))
    assert via_samtools == via_python
    assert ("rescued" in via_python) is (rule.multimapper_policy == "rescued")


def test_rule_validation_and_args():
    with pytest.raises(ValueError, match="requires --multimapper-policy"):
        ReadRule("unified", 0, None)
    with pytest.raises(ValueError, match="only to --count-read-rule unified"):
        ReadRule("legacy", 0, "unique")
    legacy = read_rule_from_args(Namespace(minMAPQ=30))
    assert not legacy.unified and legacy.token is None
    with pytest.raises(ValueError):
        legacy.rejection_reason(None)
    assert legacy.assignment == "overlap" and legacy.counting_token is None
    with pytest.raises(ValueError, match="legacy keeps overlap"):
        ReadRule("legacy", 0, None, "three_prime_end")
    rule = read_rule_from_args(Namespace(minMAPQ=30, count_read_rule="unified", multimapper_policy="rescued"))
    assert (rule.token, rule.counting_token) == ("rescued-q30-primary", "rescued-q30-primary-3p")
    assert rule.featurecounts_args() == ["-M", "--primary", "-Q", "30", "--read2pos", "3"]
    overlap = read_rule_from_args(Namespace(
        minMAPQ=0, count_read_rule="unified", multimapper_policy="unique", count_assignment="overlap",
    ))
    assert (overlap.token, overlap.counting_token) == ("unique-q0-primary", "unique-q0-primary-ovl")
    assert overlap.featurecounts_args() == ["-M", "--primary", "-Q", "0"]


def test_cli_requires_policy_only_with_unified(tmp_path):
    from arguments import parse_arguments

    base = ["run", "--output-dir", str(tmp_path), "--run-id", "r", "--inputs", "x.csv",
            "--species", "human", "--gene-gtf", "g.gtf", "--chrom-sizes", "c.sizes",
            "--merge-distance", "10", "--exp-peak-threshold-mode", "fixed",
            "--polyA-peak-threshold-mode", "fixed", "--unscaled-depth-threshold", "5",
            "--unscaled-depth-threshold-polyA", "3", "--coverage-scaling", "none"]
    assert parse_arguments(base).count_read_rule == "legacy"
    with pytest.raises(SystemExit):
        parse_arguments(base + ["--count-read-rule", "unified"])
    with pytest.raises(SystemExit):
        parse_arguments(base + ["--multimapper-policy", "unique"])
    parsed = parse_arguments(base + ["--count-read-rule", "unified", "--multimapper-policy", "unique"])
    assert (parsed.count_read_rule, parsed.multimapper_policy) == ("unified", "unique")
    assert parsed.count_assignment == "three_prime_end"
    assert parse_arguments(base).count_assignment == "overlap"
    assert parse_arguments(base + ["--count-assignment", "overlap"]).count_assignment == "overlap"
    with pytest.raises(SystemExit):
        parse_arguments(base + ["--count-assignment", "three_prime_end"])
    parsed = parse_arguments(base + ["--count-read-rule", "unified", "--multimapper-policy", "unique",
                                     "--count-assignment", "overlap"])
    assert parsed.count_assignment == "overlap"


def _tail_bam(path):
    tail = "C" * 40 + "A" * 12
    cigar = ((0, 40), (4, 12))
    return write_bam(path, [
        segment(name, flag, mapq, tags, start=100 + 7 * i, cigar=cigar, seq=tail)
        for i, (name, flag, mapq, tags) in enumerate(ELIGIBILITY_CASES)
        if not flag & 0x10
    ])


def test_polya_extract_legacy_is_unchanged_and_unified_uses_shared_rule(tmp_path):
    bam = _tail_bam(tmp_path / "tail.bam")
    legacy_default = extract_polya_bam(bam, tmp_path / "a.bam", 1, 10, 30)
    legacy_explicit = extract_polya_bam(bam, tmp_path / "b.bam", 1, 10, 30, read_rule=ReadRule("legacy", 30))
    assert legacy_default == legacy_explicit
    assert (tmp_path / "a.bam").read_bytes() == (tmp_path / "b.bam").read_bytes()
    # historical prefilter: mapped and MAPQ>=30, secondary/supplementary kept
    assert _names(tmp_path / "a.bam") == sorted(
        ["unique", "no_tags", "rescued", "rescued_nh1", "mm0_nh2", "secondary", "supplementary", "duplicate"]
    )
    extract_polya_bam(bam, tmp_path / "u.bam", 1, 10, 30, read_rule=UNIQUE)
    assert _names(tmp_path / "u.bam") == ["duplicate", "no_tags", "unique"]
    extract_polya_bam(bam, tmp_path / "r.bam", 1, 10, 30, read_rule=RESCUED)
    assert _names(tmp_path / "r.bam") == sorted(
        ["unique", "no_tags", "rescued", "rescued_nh1", "mm0_nh2", "duplicate"]
    )


def test_unified_coverage_equals_genomecov_of_eligible_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{ENV_BIN}:{os.environ['PATH']}")
    bam = eligibility_bam(tmp_path / "e.bam")
    out = tmp_path / "cov.bedgraph"
    bedtools_generate_coverage(bam, out, tmp_path / "cov.log", strand="plus", read_rule=UNIQUE)

    eligible = tmp_path / "eligible.bam"
    with pysam.AlignmentFile(str(bam), "rb") as src, pysam.AlignmentFile(str(eligible), "wb", template=src) as dst:
        for read in src:
            if UNIQUE.is_eligible(read):
                dst.write(read)
    expected = subprocess.run(
        ["bedtools", "genomecov", "-bg", "-split", "-strand", "+", "-ibam", str(eligible)],
        check=True, capture_output=True, text=True,
    ).stdout
    assert out.read_text() == expected and expected
    assert not (tmp_path / ".cov.bedgraph.tmp").exists()

    legacy = tmp_path / "legacy.bedgraph"
    bedtools_generate_coverage(bam, legacy, tmp_path / "cov.log", strand="plus")
    everything = subprocess.run(
        ["bedtools", "genomecov", "-bg", "-split", "-strand", "+", "-ibam", str(bam)],
        check=True, capture_output=True, text=True,
    ).stdout
    assert legacy.read_text() == everything != expected


def _gtf(path, rows):
    path.write_text("".join(
        f"chr1\tt\tpeak\t{s}\t{e}\t.\t{strand}\t.\tpeak_name {name};\n" for name, s, e, strand in rows
    ))
    return path


def _sidecar(path):
    with gzip.open(path, "rt") as handle:
        next(handle)
        return [tuple(line.rstrip("\n").split("\t")) for line in handle]


def test_legacy_featurecounts_command_is_historical(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        raise RuntimeError("stop")

    monkeypatch.setattr(feature_sidecar_utils, "run_tool_command", fake_run)
    with pytest.raises(RuntimeError, match="stop"):
        build_feature_assignment_sidecar(
            input_bam=tmp_path / "in.bam", annotation_gtf=tmp_path / "a.gtf",
            output_sidecar=tmp_path / "out" / "s.tsv.gz", cell_barcode_tag="CB",
            thread_count=1, feature_type="peak", group_attr="peak_name", count_features=True,
            read_rule=ReadRule("legacy", 30),
        )
    tmp = tmp_path / "out" / "in.peak.featureCounts_tmp"
    assert seen["cmd"] == [
        "featureCounts", "-a", str(tmp_path / "a.gtf"), "-o", str(tmp / "counts.txt"),
        "-t", "peak", "-g", "peak_name", "-F", "GTF", "-s", "1", "-R", "BAM", "-T", "1",
        "-f", str(tmp_path / "in.bam"),
    ]


@pytest.mark.parametrize(
    "rule, expected_assigned",
    [
        (None, {"unique", "supp"}),  # historical: supplementary records count
        (UNIQUE, {"unique"}),
        (RESCUED, {"unique", "rescued"}),
    ],
    ids=["legacy", "unique", "rescued"],
)
def test_featurecounts_counts_each_eligible_read_once(tmp_path, rule, expected_assigned):
    # "rescued" has a primary (MAPQ 255, mm:i:1) and a secondary record in the
    # same window; "lowq" is a MAPQ 1 multimapper; "supp" is supplementary.
    bam = write_bam(tmp_path / "in.bam", [
        segment("unique", 0, 255, {"NH": 1}, start=200),
        segment("rescued", 0, 255, {"NH": 2, "HI": 1, "mm": 1}, start=210),
        segment("rescued", 0x100, 3, {"NH": 2, "HI": 2}, start=220),
        segment("lowq", 0, 1, {"NH": 3}, start=230),
        segment("supp", 0x800, 255, {"NH": 1}, start=240),
    ])
    gtf = _gtf(tmp_path / "w.gtf", [("W", 150, 400, "+")])
    sidecar = tmp_path / "s.tsv.gz"
    build_feature_assignment_sidecar(
        input_bam=bam, annotation_gtf=gtf, output_sidecar=sidecar, cell_barcode_tag="CB",
        thread_count=1, feature_type="peak", group_attr="peak_name", count_features=True,
        feature_counts_exec=str(FEATURECOUNTS), read_rule=rule,
    )
    rows = _sidecar(sidecar)
    assert len(rows) == 5  # one sidecar row per source record
    assigned = [(name, int(flag)) for name, flag, feature in rows if feature == "W"]
    assert {name for name, _ in assigned} == expected_assigned
    if rule is not None:
        assert all(flag & 0x900 == 0 for _, flag in assigned)  # only primary records
    assert len(assigned) == len(expected_assigned)  # never twice per read


def _args(**extra):
    base = dict(minpolyA=10, nonA_allowed=1, minMAPQ=30, dedup_method="directional",
                exp_peak_threshold_mode="fixed", polyA_peak_threshold_mode="fixed",
                unscaled_depth_threshold=5, unscaled_depth_threshold_polyA=3,
                scaled_depth_threshold=None, scaled_depth_threshold_polyA=None,
                merge_distance=20, merge_distance_polyA=10)
    for prefix in ("exp", "polyA"):
        base.update({f"{prefix}_dynamic_threshold_stat": None, f"{prefix}_dynamic_threshold_alpha": 1.0,
                     f"{prefix}_dynamic_threshold_floor": 0.0, f"{prefix}_dynamic_threshold_normalized": 0.3})
    base.update(extra)
    return Namespace(**base)


def test_artifact_names_change_only_under_unified_rule():
    from task_manage import ScItem

    legacy, unified = _args(), _args(count_read_rule="unified", multimapper_policy="unique")
    assert ScItem.fmt_polyA(legacy) == "polyA-10-1-30"
    assert ScItem.fmt_polyA(unified) == "polyA-10-1-30-unique-q30-primary-3p"
    assert ScItem.fmt_polyA(_args(count_read_rule="unified", multimapper_policy="unique",
                                  count_assignment="overlap")) == "polyA-10-1-30-unique-q30-primary-ovl"

    def names(args):
        exp = ScItem(strand="plus", is_cohort=True, is_polyA=False, coverage_source="unstranded",
                     strand_filter="plus", add_dedup_method=True, args=args)
        polya = exp.replace(is_polyA=True, add_polyA=True)
        return (
            exp.format_name(suffix="bedgraph", is_coverage=True),
            exp.format_name(suffix="bam", strand=None, scaling_factor=None),
            polya.format_name(suffix="bedgraph", is_coverage=True),
            exp.read_rule, polya.read_rule,
        )

    assert names(legacy) == (
        "cohort.directional.plus.coverage.bedgraph", "cohort.directional.bam",
        "cohort.directional.polyA-10-1-30.plus.coverage.bedgraph", None, None,
    )
    assert names(unified) == (
        "cohort.directional.rr-unique-q30-primary.plus.coverage.bedgraph", "cohort.directional.bam",
        "cohort.directional.polyA-10-1-30-unique-q30-primary-3p.plus.coverage.bedgraph",
        "unique-q30-primary", None,
    )


COVERAGE_TASKS = (
    "generate_raw_coverage", "upm_scale_coverage", "exclude_cds_region",
    "negate_bedgraph", "generate_bigwig", "merge_call_peaks",
)


def _coverage_identity(args, task_base):
    from task_manage import ScItem
    from task_manage.pipeline_contracts import build_current_task_instance_contract_from_keys_v2
    from task_manage.task_coordinate import build_task_coordinate_v1_from_contract
    from task_manage.task_specs import task_specs_by_key

    item = ScItem(strand="plus", is_cohort=True, is_polyA=False, coverage_source="unstranded",
                  strand_filter="plus", add_dedup_method=True, args=args)
    spec = task_specs_by_key()[("call_peaks", task_base)]
    contract = build_current_task_instance_contract_from_keys_v2(
        stage=spec.stage, task_base=spec.task_base, item=item,
        identity_keys=spec.identity_keys, param_keys=spec.param_keys, level=spec.level,
    )
    return item.format_name(suffix="bedgraph", is_coverage=True), build_task_coordinate_v1_from_contract(contract)


@pytest.mark.parametrize("task_base", COVERAGE_TASKS)
def test_minmapq_change_never_reuses_unified_coverage(task_base):
    # Sol review item 1: run MAPQ 0, then 30, then 0 again; every pair must
    # differ in artifact name and task coordinate, and the repeat must match.
    def unified(mapq, policy="unique"):
        return _args(minMAPQ=mapq, count_read_rule="unified", multimapper_policy=policy)

    q0, q30, q0_again = (_coverage_identity(unified(m), task_base) for m in (0, 30, 0))
    assert q0[0] != q30[0] and q0[1] != q30[1]
    assert q0 == q0_again
    rescued = _coverage_identity(unified(30, "rescued"), task_base)
    assert rescued[0] != q30[0] and rescued[1] != q30[1]
    # minMAPQ does not touch legacy expression coverage (all reads), so the
    # legacy identity stays as before.
    assert _coverage_identity(_args(minMAPQ=0), task_base) == _coverage_identity(_args(minMAPQ=30), task_base)
    assert _coverage_identity(_args(), task_base) != q30


def test_unified_assigns_reads_by_their_three_prime_end(tmp_path):
    # Adjacent windows with a 1 bp gap (as at H3F3A_peak_423/424); GTF 1-based.
    gtf = _gtf(tmp_path / "w.gtf", [
        ("A", 101, 140, "+"), ("B", 142, 200, "+"),
        ("C", 101, 140, "-"), ("D", 142, 200, "-"),
    ])
    span = ((0, 50),)
    bam = write_bam(tmp_path / "in.bam", [
        segment("fwd_span", 0, 255, {"NH": 1}, start=120, cigar=span),       # 3' end 170 -> B
        segment("rev_span", 0x10, 255, {"NH": 1}, start=120, cigar=span),    # 3' end 121 -> C
        segment("fwd_inside", 0, 255, {"NH": 1}, start=100, cigar=((0, 30),)),  # all in A
        segment("fwd_gap_end", 0, 255, {"NH": 1}, start=111, cigar=((0, 30),)),  # 3' end 141 = gap
        segment("fwd_split", 0, 255, {"NH": 1}, start=105, cigar=((0, 20), (3, 50), (0, 20), (4, 10))),  # 3' end 195 -> B
        segment("fwd_past", 0, 255, {"NH": 1}, start=180, cigar=span),       # 3' end 230 -> none
    ])
    sidecars = {}
    overlap = ReadRule("unified", 30, "unique", "overlap")
    for label, rule in (("legacy", None), ("unified", UNIQUE), ("unified_overlap", overlap)):
        sidecar = tmp_path / f"{label}.tsv.gz"
        build_feature_assignment_sidecar(
            input_bam=bam, annotation_gtf=gtf, output_sidecar=sidecar, cell_barcode_tag="CB",
            thread_count=1, feature_type="peak", group_attr="peak_name", count_features=True,
            feature_counts_exec=str(FEATURECOUNTS), read_rule=rule,
        )
        sidecars[label] = {name: feature for name, _, feature in _sidecar(sidecar)}
    assert sidecars["legacy"] == {
        "fwd_span": "", "rev_span": "", "fwd_inside": "A", "fwd_gap_end": "A",
        "fwd_split": "", "fwd_past": "B",
    }
    assert sidecars["unified"] == {
        "fwd_span": "B", "rev_span": "C", "fwd_inside": "A", "fwd_gap_end": "",
        "fwd_split": "B", "fwd_past": "",
    }
    # --count-assignment overlap under the unified rule keeps overlap placement.
    assert sidecars["unified_overlap"] == sidecars["legacy"]
