from __future__ import annotations

import gzip
import importlib

import pandas as pd
import pytest

from arguments import parse_arguments
from task_manage import Plan, ScItem, TaskManager
from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_unit_inputs
from task_manage.effective_config import build_annotated_peak_config
from task_manage.run_layout import RunLayout
from utils.terminal_quant_utils import (
    TERMINAL_DISTAL_USAGE_SUFFIX,
    TERMINAL_FEATURE_OVERVIEW_SUFFIX,
    quantification_artifact_suffix,
)


def native_fixture(tmp_path, mode):
    args = parse_arguments([
        "run", "--output-dir", str(tmp_path), "--run-id", "distal",
        "--species", "mouse", "--inputs", "inputs.csv",
        "--gene-gtf", "genes.gtf", "--chrom-sizes", "chrom.sizes",
        "--coverage-scaling", "none", "--merge-distance", "0",
        "--unscaled-depth-threshold", "1", "--unscaled-depth-threshold-polyA", "1",
        "--quantification-mode", mode, "--psi-min-gene-umis", "1",
        "--n-workers", "1",
    ])
    layout = RunLayout(out=tmp_path, run_id="distal")
    layout.ensure_base_dirs()
    units = tmp_path / "units.csv"
    units.write_text("unit_id,sample_id,contig_name,dedup_status\nS.chr1,S,chr1,external\n")
    cache = {CACHE_unit_inputs: units, CACHE_dedup_method: "directional",
             CACHE_polyA_status: "polyA-10-1-0"}
    final = build_annotated_peak_config(args, dedup_method="directional",
                                       polyA_status="polyA-10-1-0")
    tm = TaskManager(state_dir=layout.State, config={**vars(args), **final})
    tm.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    layout.AnnotatedFinalOutputSub(final)
    bed = layout.ResolveAnnotatedFinalOutputFile(
        suffix=quantification_artifact_suffix("___peaks-annotated.bed", mode), d=final,
    )
    rows = []
    for name, start in [("proximal", 100), ("distal", 200)]:
        row = ["chr1", start, start + 1, name, 1, "+", 2, 0.1, 2,
               "GENE", 0, "tx", 1, start, "Terminal_exon"]
        rows.append(row + (["G1"] if mode == "terminal-aware" else []))
    bed.write_text("".join("\t".join(map(str, row)) + "\n" for row in rows))
    item = ScItem(sid="S", dedup_status="external", args=args)
    counts = layout.CountMatrixConfigSub(final) / item.format_name(
        suffix=quantification_artifact_suffix("counts.tsv.gz", mode), is_annotated_bam=True,
    )
    with gzip.open(counts, "wt") as handle:
        handle.write("proximal\tc\t3\ndistal\tc\t1\n")
    root = layout.MatrixSummaryConfigSub(final, psi_min_gene_umis=1)
    return args, layout, cache, tm, root, item


@pytest.mark.parametrize("mode", ["legacy", "terminal-aware"])
@pytest.mark.parametrize("hybrid_gate", ["0", "1"])
@pytest.mark.parametrize("required_suffix", [TERMINAL_DISTAL_USAGE_SUFFIX, TERMINAL_FEATURE_OVERVIEW_SUFFIX])
def test_native_done_reuse_and_missing_distal_migration(tmp_path, monkeypatch, mode, hybrid_gate, required_suffix):
    summary = importlib.import_module("stages.summarize_matrix")
    # Exercise DONE-only reuse: the new guard must work without optional health gates.
    monkeypatch.setenv("SCPOLYA_REUSE_V2_HYBRID_GATE", hybrid_gate)
    args, layout, cache, tm, root, item = native_fixture(tmp_path, mode)
    summary.summarize_matrix(tm, layout, args, cache)
    output = root / item.format_name(suffix=TERMINAL_DISTAL_USAGE_SUFFIX, is_annotated_bam=True)
    if mode == "legacy":
        assert not output.exists()
    else:
        assert pd.read_csv(output, sep="\t")["distal_site_usage"].tolist() == [0.25]
    before = {p.name: gzip.open(p, "rb").read() for p in root.glob("*.tsv.gz")}

    writer = summary._write_terminal_aware_summary

    def forbidden(**kwargs):
        raise AssertionError("a complete DONE summary should be reused")

    monkeypatch.setattr(summary, "_write_terminal_aware_summary", forbidden)
    summary.summarize_matrix(tm, layout, args, cache)
    assert before == {p.name: gzip.open(p, "rb").read() for p in root.glob("*.tsv.gz")}
    if mode == "legacy":
        return

    output = root / item.format_name(suffix=required_suffix, is_annotated_bam=True)
    output.unlink()  # old DONE remains, but the new required file is absent
    calls = []

    def counted(**kwargs):
        calls.append(kwargs["sample_id"])
        return writer(**kwargs)

    monkeypatch.setattr(summary, "_write_terminal_aware_summary", counted)
    summary.summarize_matrix(tm, layout, args, cache)
    assert calls == ["S"]
    assert before == {p.name: gzip.open(p, "rb").read() for p in root.glob("*.tsv.gz")}


def test_new_export_is_atomic_and_failed_export_cannot_claim_done(tmp_path, monkeypatch):
    summary = importlib.import_module("stages.summarize_matrix")
    args, layout, cache, tm, root, item = native_fixture(tmp_path, "terminal-aware")
    output = root / item.format_name(suffix=TERMINAL_DISTAL_USAGE_SUFFIX, is_annotated_bam=True)
    original = pd.DataFrame.to_csv

    def interrupted(frame, path, *args, **kwargs):
        if str(path) == str(output) + ".tmp":
            path.write_bytes(b"partial")
            raise OSError("injected distal write interruption")
        return original(frame, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", interrupted)
    with pytest.raises(Exception, match="(?i)(failed|interruption)"):
        summary.summarize_matrix(tm, layout, args, cache)
    assert not output.exists()
    assert not output.with_name(output.name + ".tmp").exists()
    monkeypatch.setattr(pd.DataFrame, "to_csv", original)
    summary.summarize_matrix(tm, layout, args, cache)
    assert output.exists()
    assert pd.read_csv(output, sep="\t")["usage_status"].tolist() == ["ok"]


def test_gzip_distal_export_distinguishes_zero_from_literal_na(tmp_path):
    summary = importlib.import_module("stages.summarize_matrix")
    args, layout, cache, tm, root, item = native_fixture(tmp_path, "terminal-aware")
    counts = next(layout.Root.glob("count_matrix/**/*.tsv.gz"))
    with gzip.open(counts, "wt") as handle:
        handle.write("proximal\tzero-distal\t3\nproximal\tu0\t0\n")
    summary.summarize_matrix(tm, layout, args, cache)
    output = root / item.format_name(suffix=TERMINAL_DISTAL_USAGE_SUFFIX, is_annotated_bam=True)
    exported = pd.read_csv(output, sep="\t", keep_default_na=False).set_index("cell_barcode")
    assert float(exported.loc["zero-distal", "distal_site_usage"]) == 0
    assert exported.loc["zero-distal", "usage_status"] == "ok"
    assert exported.loc["u0", "distal_site_usage"] == "NA"
    assert exported.loc["u0", "usage_status"] == "zero_terminal_umis"


def _overview(root, item):
    path = root / item.format_name(suffix=TERMINAL_FEATURE_OVERVIEW_SUFFIX, is_annotated_bam=True)
    return pd.read_csv(path, sep="\t", keep_default_na=False).set_index(["level", "feature"])


@pytest.mark.parametrize("chunk_rows", ["1", "2", "500000"])
def test_overview_umis_sum_every_streamed_chunk(tmp_path, monkeypatch, chunk_rows):
    summary = importlib.import_module("stages.summarize_matrix")
    monkeypatch.setenv("SCPOLYA_SUMMARY_CHUNK_ROWS", chunk_rows)
    args, layout, cache, tm, root, item = native_fixture(tmp_path, "terminal-aware")
    counts = next(layout.Root.glob("count_matrix/**/*.tsv.gz"))
    large = 3_000_000_000
    with gzip.open(counts, "wt") as handle:
        handle.write("peak_name\tcell_barcode\tumi_count\n"
                     "proximal\tc1\t3\ndistal\tc1\t1\nproximal\tc2\tNA\nproximal\tc2\t5\n"
                     f"unknown\tc1\t7\ndistal\tc3\t{large}\nproximal\tc1\t2\n")
    summary.summarize_matrix(tm, layout, args, cache)
    overview = _overview(root, item)
    assert overview.loc[("group", "Terminal_exon"), ["sites", "umis"]].tolist() == [2, 3 + 1 + 5 + large + 2]
    assert overview.loc[("unmatched", "(not in site table)"), "umis"] == 7


def test_overview_umis_from_a_wide_count_matrix(tmp_path):
    summary = importlib.import_module("stages.summarize_matrix")
    args, layout, cache, tm, root, item = native_fixture(tmp_path, "terminal-aware")
    counts = next(layout.Root.glob("count_matrix/**/*.tsv.gz"))
    with gzip.open(counts, "wt") as handle:
        handle.write("peak\tc1\tc2\tc3\nproximal\t3\t4\t1\ndistal\t1\t0\t0\nunknown\t2\t2\t0\n")
    summary.summarize_matrix(tm, layout, args, cache)
    overview = _overview(root, item)
    assert overview.loc[("group", "Terminal_exon"), "umis"] == 9
    assert overview.loc[("unmatched", "(not in site table)"), "umis"] == 4


def test_failed_overview_write_is_not_published_or_done(tmp_path, monkeypatch):
    summary = importlib.import_module("stages.summarize_matrix")
    args, layout, cache, tm, root, item = native_fixture(tmp_path, "terminal-aware")
    output = root / item.format_name(suffix=TERMINAL_FEATURE_OVERVIEW_SUFFIX, is_annotated_bam=True)
    original = pd.DataFrame.to_csv

    def interrupted(frame, path, *args, **kwargs):
        if str(path) == str(output) + ".tmp":
            path.write_bytes(b"partial")
            raise OSError("injected overview write interruption")
        return original(frame, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", interrupted)
    with pytest.raises(Exception, match="(?i)(failed|interruption)"):
        summary.summarize_matrix(tm, layout, args, cache)
    assert not output.exists()
    assert not output.with_name(output.name + ".tmp").exists()
    monkeypatch.setattr(pd.DataFrame, "to_csv", original)
    calls = []
    writer = summary._write_terminal_aware_summary
    monkeypatch.setattr(summary, "_write_terminal_aware_summary",
                        lambda **kwargs: (calls.append(kwargs["sample_id"]), writer(**kwargs))[1])
    summary.summarize_matrix(tm, layout, args, cache)
    assert calls == ["S"]
    assert output.exists()
