"""Hand-calculated distal fractions and sparse coverage, independent of PSI."""
from __future__ import annotations

import math

import pandas as pd
import pandas.testing as pdt
import pytest

from utils.terminal_quant_utils import (
    TERMINAL_DISTAL_USAGE_COLUMNS,
    compute_terminal_aware_tables,
)


def metadata(rows):
    frame = pd.DataFrame(rows, columns=[
        "peak_name", "gene_id", "strand", "feature", "distance_to_stop_codon",
        "misprimed_ratio",
    ])
    frame["gene_name"] = "SAME_DISPLAY_NAME"
    frame["chrom"] = "chr1"
    frame["start"] = range(len(frame))
    frame["end"] = frame["start"] + 1
    frame["three_prime_utr_length"] = pd.to_numeric(
        frame["distance_to_stop_codon"], errors="coerce"
    ).clip(lower=0)
    return frame


def quantify(rows, counts, minimum=3):
    return compute_terminal_aware_tables(
        pd.DataFrame(counts, columns=["peak_name", "cell_barcode", "umi_count"]),
        metadata(rows), general_threshold=0.2, intronic_threshold=0.5,
        psi_min_gene_umis=minimum,
    )


BASE = [
    ("p", "G", "+", "Terminal_exon", 100, 0.1),
    ("d", "G", "+", "Downstream_of_terminal_exon", 200, 0.1),
    ("i", "G", "+", "Intron", -50, 0.1),
    ("other", "G", "+", "Other_exon", 900, 0.1),
    ("failed", "G", "+", "Terminal_exon", 1000, 0.9),
]


def test_distal_fraction_denominator_and_meaningful_zero_and_na():
    result = quantify(BASE, [
        ("p", "normal", 3), ("d", "normal", 1),
        ("i", "normal", 40), ("other", "normal", 100),
        ("failed", "normal", 50),
        ("p", "zero-distal", 3), ("p", "low", 2),
        ("i", "intron-only", 2),
    ])
    table = result["distal_usage"].set_index("cell_barcode")
    assert list(result["distal_usage"].columns) == TERMINAL_DISTAL_USAGE_COLUMNS
    assert table.loc["normal", "gene_umi_count"] == 4
    assert table.loc["normal", "distal_umi_count"] == 1
    assert table.loc["normal", "distal_site_usage"] == 0.25
    assert table.loc["zero-distal", "distal_site_usage"] == 0
    assert table.loc["zero-distal", "usage_status"] == "ok"
    assert table.loc["low", "usage_status"] == "below_min_gene_umis"
    assert table.loc["intron-only", "gene_umi_count"] == 0
    assert table.loc["intron-only", "usage_status"] == "zero_terminal_umis"
    assert table.loc[["low", "intron-only"], "distal_site_usage"].isna().all()
    # iPUI has its own eligibility: an observed intron unit with U=0 is defined.
    ipui = result["gene_total"].set_index("quantification_unit_id")
    assert ipui.loc["intron-only", "total_iPUI"] == pytest.approx(math.log2(3))
    assert result["weighted_utr"].set_index("cell_barcode").loc[
        "normal", "weighted_three_prime_utr_length"
    ] == 125


@pytest.mark.parametrize("strand", ["+", "-"])
def test_all_tied_distal_pas_are_summed_and_input_order_is_irrelevant(strand):
    rows = [
        ("p", "G", strand, "Terminal_exon", -20, 0.1),
        ("d1", "G", strand, "Terminal_exon", 200, 0.1),
        ("d2", "G", strand, "Downstream_of_terminal_exon", 200, 0.1),
    ]
    counts = [("p", "c", 2), ("d1", "c", 1), ("d2", "c", 3)]
    result = quantify(rows, counts)
    row = result["distal_usage"].iloc[0]
    assert row["distal_pas_count"] == 2
    assert row["retained_terminal_pas_count"] == 3
    assert row["distal_umi_count"] == 4
    assert row["gene_umi_count"] == 6
    assert row["distal_site_usage"] == pytest.approx(4 / 6)
    assert result["weighted_utr"].iloc[0]["weighted_three_prime_utr_length"] == pytest.approx(800 / 6)
    pdt.assert_frame_equal(result["distal_usage"], quantify(rows[::-1], counts[::-1])["distal_usage"])


def test_true_identity_ineligibility_and_observation_space():
    rows = BASE[:3] + [
        ("single", "SINGLE", "+", "Terminal_exon", 100, 0.1),
        ("q1", "G", "-", "Terminal_exon", 100, 0.1),
        ("q2", "G", "-", "Terminal_exon", 200, 0.1),
        ("r1", "OTHER_GENE", "+", "Terminal_exon", 100, 0.1),
        ("r2", "OTHER_GENE", "+", "Terminal_exon", 200, 0.1),
        ("bad", "NO_CATALOGUE", "+", "Terminal_exon", 100, 0.9),
        ("j", "INTRON_ONLY", "+", "Intron", 10, 0.1),
    ]
    counts = [("p", "a", 3), ("q2", "b", 4), ("r1", "a", 1),
              ("r2", "a", 3), ("single", "a", 9), ("bad", "a", 8),
              ("j", "a", 2)]
    table = quantify(rows, counts)["distal_usage"].set_index(["gene_id", "strand"])
    assert len(table) == 6  # six observed pairs, no gene-by-two-cells grid
    assert table.loc[("G", "+"), "distal_site_usage"] == 0
    assert table.loc[("G", "-"), "distal_site_usage"] == 1
    assert table.loc[("OTHER_GENE", "+"), "distal_site_usage"] == 0.75
    assert table["terminal_gene_unit_id"].nunique() == 6
    assert table.loc[("SINGLE", "+"), "usage_status"] == "fewer_than_two_terminal_pas"
    for gene in ("NO_CATALOGUE", "INTRON_ONLY"):
        assert table.loc[(gene, "+"), "usage_status"] == "no_eligible_terminal_pas"
        assert pd.isna(table.loc[(gene, "+"), "gene_umi_count"])
        assert pd.isna(table.loc[(gene, "+"), "distal_site_usage"])


@pytest.mark.parametrize("distance", [float("nan"), float("inf"), -float("inf")])
def test_incomplete_distance_does_not_guess_a_distal_site(distance):
    rows = [BASE[0], ("d", "G", "+", "Terminal_exon", distance, 0.1)]
    row = quantify(rows, [("p", "a", 3), ("d", "a", 2)])["distal_usage"].iloc[0]
    assert row["usage_status"] == "missing_terminal_distance"
    assert row["gene_umi_count"] == 5
    assert pd.isna(row["distal_site_usage"])
    assert pd.isna(row["distal_umi_count"])
    assert pd.isna(row["distal_pas_count"])


def test_no_observed_unit_and_empty_catalogue_are_explicit():
    row = quantify(BASE, [])["distal_usage"].iloc[0]
    assert pd.isna(row["cell_barcode"])
    assert row["usage_status"] == "no_observed_unit"
    assert pd.isna(row["gene_umi_count"])
    assert pd.isna(row["distal_site_usage"])
    empty = quantify([], [])["distal_usage"]
    assert empty.empty
    assert list(empty.columns) == TERMINAL_DISTAL_USAGE_COLUMNS


def test_zero_denominator_stays_na_even_when_threshold_zero():
    row = quantify(BASE, [("i", "a", 2)], minimum=0)["distal_usage"].iloc[0]
    assert row["usage_status"] == "zero_terminal_umis"
    assert pd.isna(row["distal_site_usage"])
