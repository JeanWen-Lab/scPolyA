from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


def _load_summary_module():
    source = Path(__file__).resolve().parents[1] / "stages" / "summarize_matrix.py"
    spec = importlib.util.spec_from_file_location("terminal_summary_under_test", source)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_terminal_dense_matrix_uses_unambiguous_gene_id_strand_columns(tmp_path):
    summary = _load_summary_module()
    gene_cell = pd.DataFrame(
        [
            ("G1", "DUP", "+", "G1|%2B", "cell", 2),
            ("G2", "DUP", "+", "G2|%2B", "cell", 3),
            ("G1", "DUP", "-", "G1|-", "cell", 4),
        ],
        columns=[
            "gene_id",
            "gene_name",
            "strand",
            "terminal_gene_unit_id",
            "cell_barcode",
            "gene_umi_count",
        ],
    )
    output = tmp_path / "terminal-dense.tsv.gz"
    result = summary._write_terminal_cell_gene_matrix_or_skip(
        gene_cell,
        output_path=output,
        manifest_path=tmp_path / "terminal-dense.skipped.json",
        long_gene_cell_path=tmp_path / "terminal-long.tsv.gz",
        sample_id="sample",
    )
    assert result["status"] == "written_dense"
    assert result["dense_column_key"] == "terminal_gene_unit_id"

    dense = pd.read_csv(output, sep="\t")
    assert list(dense.columns) == ["cell_barcode", "G1|%2B", "G1|-", "G2|%2B"]
    assert dense.iloc[0].to_dict() == {
        "cell_barcode": "cell",
        "G1|%2B": 2,
        "G1|-": 4,
        "G2|%2B": 3,
    }
