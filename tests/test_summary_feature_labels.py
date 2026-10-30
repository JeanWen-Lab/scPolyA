"""Summarize keeps detailed site labels; Other grouping is display-only."""
from __future__ import annotations

import importlib

import pandas as pd

from arguments import parse_arguments
from task_manage import ScItem
from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_unit_inputs
from task_manage.effective_config import build_annotated_peak_config
from task_manage.run_layout import RunLayout
from utils.terminal_quant_utils import (
    TERMINAL_DISTAL_USAGE_SUFFIX,
    TERMINAL_FEATURE_OVERVIEW_SUFFIX,
    TERMINAL_IPUI_GENE_TOTAL_SUFFIX,
    TERMINAL_IPUI_PER_SITE_SUFFIX,
    quantification_artifact_suffix,
    quantification_summary_suffix,
)

# (name, start, gene_id, distance, v4 label); the last three were Intergenic in v3.
SITES = [
    ("t1", 100, "G1", 100, "Terminal_exon"),
    ("t2", 200, "G1", 200, "Terminal_exon"),
    ("in", 150, "G1", -50, "Intron"),
    ("nc", 900, "G2", "nan", "Noncoding_exon"),
    ("edge", 250, "G1", "nan", "Canonical_model_edge"),
    ("ig", 5000, "", "nan", "Intergenic"),
]


def _run(tmp_path, monkeypatch, labels):
    module = importlib.import_module("stages.summarize_matrix")
    args = parse_arguments([
        "run", "--output-dir", str(tmp_path), "--run-id", "run",
        "--species", "mouse", "--inputs", "inputs.csv",
        "--gene-gtf", "genes.gtf", "--chrom-sizes", "chrom.sizes",
        "--coverage-scaling", "none", "--merge-distance", "0",
        "--unscaled-depth-threshold", "1", "--unscaled-depth-threshold-polyA", "1",
        "--quantification-mode", "terminal-aware", "--psi-min-gene-umis", "1",
    ])
    layout = RunLayout(out=tmp_path, run_id="run")
    layout.ensure_base_dirs()
    units = tmp_path / "units.csv"
    units.write_text("unit_id,sample_id,contig_name,dedup_status\nS.chr1,S,chr1,external\n")
    cache = {CACHE_unit_inputs: units, CACHE_dedup_method: "directional", CACHE_polyA_status: "polyA-10-1-0"}
    final = build_annotated_peak_config(args, dedup_method="directional", polyA_status="polyA-10-1-0")
    layout.AnnotatedFinalOutputSub(final)
    bed = layout.ResolveAnnotatedFinalOutputFile(
        suffix=quantification_artifact_suffix("___peaks-annotated.bed", "terminal-aware"), d=final)
    bed.write_text("".join(
        "\t".join(map(str, ["chr1", start, start + 1, name, 1, "+", 2, 0.1, 2,
                            "GENE" if gene else "", 0, "tx", 1, distance, label, gene])) + "\n"
        for (name, start, gene, distance, _), label in zip(SITES, labels)))
    item = ScItem(sid="S", dedup_status="external", args=args)
    counts = layout.CountMatrixConfigSub(final) / item.format_name(
        suffix=quantification_artifact_suffix("counts.tsv.gz", "terminal-aware"), is_annotated_bam=True)
    rows = [(name, "cell", n) for (name, *_), n in zip(SITES, [2, 6, 3, 50, 7, 9])] + [("not_a_site", "cell", 4)]
    pd.DataFrame(rows, columns=["peak_name", "cell_barcode", "umi_count"]).to_csv(counts, sep="\t", index=False)
    monkeypatch.setattr(module, "run_flex_batch", lambda **kw: [kw["fn"](entry) for entry in kw["items"]])
    logged = []
    monkeypatch.setattr(module.logger_stage, "info", lambda message, *a, **k: logged.append(str(message)))
    module.summarize_matrix(None, layout, args, cache)
    root = layout.MatrixSummaryConfigSub(final, psi_min_gene_umis=1)
    suffixes = [quantification_summary_suffix(s, "terminal-aware") for s in
                ("gene_cell.tsv.gz", "weighted_three_prime_utr_length.tsv.gz", "psi.tsv.gz")]
    suffixes += [TERMINAL_IPUI_PER_SITE_SUFFIX, TERMINAL_IPUI_GENE_TOTAL_SUFFIX, TERMINAL_DISTAL_USAGE_SUFFIX]
    tables = {s: pd.read_csv(root / item.format_name(suffix=s, is_annotated_bam=True), sep="\t", dtype=str,
                             keep_default_na=False) for s in suffixes}
    peak_to_gene = pd.read_csv(next(root.glob("*peak_to_gene*.tsv.gz")), sep="\t", dtype=str, keep_default_na=False)
    overview = pd.read_csv(root / item.format_name(suffix=TERMINAL_FEATURE_OVERVIEW_SUFFIX, is_annotated_bam=True),
                           sep="\t", keep_default_na=False)
    return tables, peak_to_gene, logged, overview


def test_new_labels_keep_site_detail_and_leave_every_metric_unchanged(tmp_path, monkeypatch):
    v4 = [label for *_, label in SITES]
    v3 = [label if label in ("Terminal_exon", "Intron") else "Intergenic" for label in v4]
    new_tables, new_ptg, logged, new_overview = _run(tmp_path / "v4", monkeypatch, v4)
    old_tables, old_ptg, _, old_overview = _run(tmp_path / "v3", monkeypatch, v3)
    for suffix, table in new_tables.items():
        pd.testing.assert_frame_equal(table, old_tables[suffix], obj=suffix)
    # Site-level output keeps every site and its detailed label.
    assert dict(zip(new_ptg["peak_name"], new_ptg["feature"])) == {name: label for (name, *_), label in zip(SITES, v4)}
    pd.testing.assert_frame_equal(new_ptg.drop(columns="feature"), old_ptg.drop(columns="feature"))
    overview = next(line for line in logged if line.startswith("Feature overview"))
    assert "Other 3 (mispriming pass 3)" in overview
    assert "Noncoding_exon 1" in overview and "Canonical_model_edge 1" in overview and "Intergenic 1" in overview


def test_overview_file_groups_new_labels_as_other_with_sample_umis(tmp_path, monkeypatch):
    v4 = [label for *_, label in SITES]
    v3 = [label if label in ("Terminal_exon", "Intron") else "Intergenic" for label in v4]
    *_, new = _run(tmp_path / "v4", monkeypatch, v4)
    *_, old = _run(tmp_path / "v3", monkeypatch, v3)
    assert list(new.columns) == ["sample_id", "level", "group", "feature", "sites", "mispriming_pass_sites", "umis"]
    assert set(new["sample_id"]) == {"S"}
    cols = ["sites", "mispriming_pass_sites", "umis"]
    rows = new.set_index(["level", "feature"])[cols]
    assert rows.loc[("group", "Terminal_exon")].tolist() == [2, 2, 8]
    assert rows.loc[("group", "Intron")].tolist() == [1, 1, 3]
    assert rows.loc[("group", "Other")].tolist() == [3, 3, 66]
    assert rows.loc[("subcategory", "Noncoding_exon")].tolist() == [1, 1, 50]
    assert rows.loc[("subcategory", "Canonical_model_edge")].tolist() == [1, 1, 7]
    assert rows.loc[("subcategory", "Intergenic")].tolist() == [1, 1, 9]
    assert rows.loc[("unmatched", "(not in site table)")].tolist() == [0, 0, 4]
    # Every count-matrix UMI is accounted for once by the group rows.
    assert rows.xs("group")["umis"].sum() + 4 == 2 + 6 + 3 + 50 + 7 + 9 + 4
    old_rows = old.set_index(["level", "feature"])[cols]
    assert old_rows.loc[("group", "Other")].tolist() == rows.loc[("group", "Other")].tolist()
    assert old_rows.loc[("subcategory", "Intergenic")].tolist() == [3, 3, 66]
    assert old_rows.xs("group").drop(index="Other").equals(rows.xs("group").drop(index="Other"))
