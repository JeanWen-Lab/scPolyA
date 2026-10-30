"""Regression: UMI dedup must group by the full corrected cell barcode.

umi_tools defaults to ``--cell-tag-split='-'``; without overriding it, reads from
``AAAC...-1`` and ``AAAC...-2`` (different cells in an aggregated 10x BAM) with
the same UMI and position were deduplicated together.
"""
from __future__ import annotations

from collections import Counter
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from stages.preprocess_units import build_dedup_task_parameter, build_umi_dedup_command

ARGS = SimpleNamespace(cb_tag="CB", umi_tag="UB", tools_seed=7, dedup_stats=False)


def _command(tmp_path: Path, in_bam: Path, out_bam: Path) -> list[str]:
    return build_umi_dedup_command(
        args=ARGS, in_bam=in_bam, out_bam=out_bam, local_log=tmp_path / "dedup.log",
        dedup_method="directional", chrom="chr19",
    )


def test_dedup_command_disables_cell_tag_split(tmp_path):
    cmd = _command(tmp_path, tmp_path / "in.bam", tmp_path / "out.bam")
    i = cmd.index("--cell-tag-split")
    assert cmd[i + 1] == ""
    assert cmd[cmd.index("--cell-tag") + 1] == "CB"


def test_dedup_task_parameter_records_cell_split_semantics():
    key = build_dedup_task_parameter(args=ARGS, source_type="raw_region")
    assert key == "source=raw_region;cb_tag=CB;umi_tag=UB;seed=7;stats=0;cell_split=none"


@pytest.mark.skipif(shutil.which("umi_tools") is None, reason="umi_tools executable not on PATH")
@pytest.mark.parametrize("cb_tag,umi_tag", [("CB", "UB"), ("XC", "XM")])
def test_aggregated_gem_wells_are_not_collapsed(tmp_path, cb_tag, umi_tag):
    pysam = pytest.importorskip("pysam")
    src, dst = tmp_path / "aggr.bam", tmp_path / "aggr.dedup.bam"
    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "chr19", "LN": 100000}]}
    cbs = ["AAAACCCCGGGGTTTT-1", "AAAACCCCGGGGTTTT-2", "CCCCAAAAGGGGTTTT-1", "CCCCAAAAGGGGTTTT-1"]
    with pysam.AlignmentFile(str(src), "wb", header=header) as out:
        for i, cb in enumerate(cbs):
            a = pysam.AlignedSegment(out.header)
            a.query_name, a.flag, a.reference_id, a.reference_start = f"r{i}", 0, 0, 1000
            a.mapping_quality, a.cigartuples = 255, [(0, 40)]
            a.query_sequence = "ACGT" * 10
            a.query_qualities = pysam.qualitystring_to_array("I" * 40)
            a.set_tags([(cb_tag, cb), (umi_tag, "ACGTACGTAC")])
            out.write(a)
    pysam.index(str(src))
    args = SimpleNamespace(cb_tag=cb_tag, umi_tag=umi_tag, tools_seed=7, dedup_stats=False)
    cmd = build_umi_dedup_command(args=args, in_bam=src, out_bam=dst, local_log=tmp_path / "dedup.log",
                                  dedup_method="directional", chrom="chr19")
    subprocess.run(cmd, check=True, capture_output=True)
    with pysam.AlignmentFile(str(dst)) as f:
        kept = sorted(r.get_tag(cb_tag) for r in f)
    # two distinct GEM-well cells kept; the true same-cell duplicate collapsed
    assert kept == ["AAAACCCCGGGGTTTT-1", "AAAACCCCGGGGTTTT-2", "CCCCAAAAGGGGTTTT-1"]
    assert Counter(cbs) == {
        "AAAACCCCGGGGTTTT-1": 1,
        "AAAACCCCGGGGTTTT-2": 1,
        "CCCCAAAAGGGGTTTT-1": 2,
    }
    assert Counter(kept) == {
        "AAAACCCCGGGGTTTT-1": 1,
        "AAAACCCCGGGGTTTT-2": 1,
        "CCCCAAAAGGGGTTTT-1": 1,
    }
