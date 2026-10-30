from __future__ import annotations

import importlib
from pathlib import Path

import pandas as pd
import pytest

from arguments import parse_arguments
from task_manage import ScItem
from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_unit_inputs
from task_manage.effective_config import build_annotated_peak_config
from task_manage.run_layout import RunLayout
from utils.terminal_quant_utils import quantification_artifact_suffix, quantification_summary_suffix


@pytest.mark.parametrize("mode", ["legacy", "terminal-aware"])
def test_summary_recovers_numeric_peak_names_without_changing_counts(tmp_path, monkeypatch, mode):
    module = importlib.import_module("stages.summarize_matrix")
    args = parse_arguments([
        "run", "--output-dir", str(tmp_path), "--run-id", "run",
        "--species", "mouse", "--inputs", "inputs.csv",
        "--gene-gtf", "genes.gtf", "--chrom-sizes", "chrom.sizes",
        "--coverage-scaling", "none", "--merge-distance", "0",
        "--unscaled-depth-threshold", "1", "--unscaled-depth-threshold-polyA", "1",
        "--quantification-mode", mode, "--psi-min-gene-umis", "1",
    ])
    layout = RunLayout(out=tmp_path, run_id="run")
    layout.ensure_base_dirs()
    units = tmp_path / "units.csv"
    units.write_text("unit_id,sample_id,contig_name,dedup_status\nS.chr1,S,chr1,external\n")
    cache = {CACHE_unit_inputs: units, CACHE_dedup_method: "directional",
             CACHE_polyA_status: "polyA-10-1-0"}
    final = build_annotated_peak_config(args, dedup_method="directional",
                                       polyA_status="polyA-10-1-0")
    layout.AnnotatedFinalOutputSub(final)
    bed = layout.ResolveAnnotatedFinalOutputFile(
        suffix=quantification_artifact_suffix("___peaks-annotated.bed", mode), d=final,
    )
    rows = []
    for name, start, distance in [("001", 100, 100), ("002", 200, 200)]:
        row = ["022", start, start + 1, name, 1, "+", 2, 0.1, 2,
               "GENE", 0, "tx", 1, distance, "Terminal_exon"]
        rows.append(row + (["G1"] if mode == "terminal-aware" else []))
    bed.write_text("".join("\t".join(map(str, row)) + "\n" for row in rows))
    item = ScItem(sid="S", dedup_status="external", args=args)
    counts = layout.CountMatrixConfigSub(final) / item.format_name(
        suffix=quantification_artifact_suffix("counts.tsv.gz", mode), is_annotated_bam=True,
    )
    pd.DataFrame([("001", "cell", 2), ("002", "cell", 6)],
                 columns=["peak_name", "cell_barcode", "umi_count"]).to_csv(counts, sep="\t", index=False)

    def execute_batch(**kwargs):
        for entry in kwargs["items"]:
            kwargs["fn"](entry)

    monkeypatch.setattr(module, "run_flex_batch", execute_batch)
    root = layout.MatrixSummaryConfigSub(final, psi_min_gene_umis=1)
    gene_path = root / item.format_name(
        suffix=quantification_summary_suffix("gene_cell.tsv.gz", mode), is_annotated_bam=True,
    )
    psi_path = root / item.format_name(
        suffix=quantification_summary_suffix("psi.tsv.gz", mode), is_annotated_bam=True,
    )
    for execution in ["cold", "metadata-recovery"]:
        module.summarize_matrix(None, layout, args, cache)
        genes = pd.read_csv(gene_path, sep="\t")
        psi = pd.read_csv(psi_path, sep="\t", dtype={"peak_name": str})
        assert genes["gene_umi_count"].tolist() == [8], execution
        assert dict(zip(psi["peak_name"], psi["psi"])) == {"001": 0.25, "002": 0.75}, execution

    # An upstream annotation rerun may update this same configuration path.
    # A surviving derived metadata file cannot override the current BED.
    bed.write_text(bed.read_text().replace("GENE", "UPDATED"))
    module.summarize_matrix(None, layout, args, cache)
    assert pd.read_csv(gene_path, sep="\t")["gene_name"].tolist() == ["UPDATED"]

    metadata_path = next(root.glob("*peak_to_gene*.tsv.gz"))
    before = metadata_path.read_bytes()
    to_csv = pd.DataFrame.to_csv

    def interrupted_export(frame, path, *positional, **kwargs):
        if Path(path).name == metadata_path.name + ".tmp":
            Path(path).write_bytes(b"partial")
            raise OSError("injected metadata export interruption")
        return to_csv(frame, path, *positional, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", interrupted_export)
    with pytest.raises(OSError, match="injected metadata export interruption"):
        module.summarize_matrix(None, layout, args, cache)
    assert metadata_path.read_bytes() == before
    assert not metadata_path.with_name(metadata_path.name + ".tmp").exists()


def test_metadata_preserves_distinct_numeric_gene_ids(tmp_path):
    module = importlib.import_module("stages.summarize_matrix")
    path = tmp_path / "annotated.bed"
    cases = [(100, "001", 0.1), (200, "1", 0.1), (300, "NA", 0.1),
             (400, "", "NA"), (500, "001", "NA")]
    path.write_text("".join(
        "\t".join(map(str, ["chr1", start, start + 1, f"p{i}", 1, "+", 2, ratio, 2,
                             "SAME", 0, "tx", 1, start, "Terminal_exon", gene_id])) + "\n"
        for i, (start, gene_id, ratio) in enumerate(cases)
    ))
    metadata = module._load_peak_metadata(path)
    assert metadata["gene_id"].iloc[:2].tolist() == ["001", "1"]
    assert metadata["gene_id"].iloc[2:4].isna().all()
    assert metadata["misprimed_ratio"].iloc[3:].isna().all()
    from utils.terminal_quant_utils import prepare_terminal_peak_metadata
    prepared = prepare_terminal_peak_metadata(metadata, general_threshold=0.2, intronic_threshold=0.5)
    assert prepared["terminal_all"]["gene_id"].tolist() == ["001", "1"]
    assert not prepared["audit"].set_index("peak_name").loc["p4", "mispriming_pass"]
