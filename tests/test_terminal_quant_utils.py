from __future__ import annotations

import math
from types import SimpleNamespace

import pandas as pd
import pandas.testing as pdt
import pytest

from utils.terminal_quant_utils import (
    compute_terminal_aware_tables,
    intronic_misprimed_ratio_threshold,
    quantification_artifact_suffix,
    quantification_mode,
)


def _metadata() -> pd.DataFrame:
    columns = [
        "peak_name",
        "gene_id",
        "gene_name",
        "chrom",
        "start",
        "end",
        "strand",
        "feature",
        "misprimed_ratio",
        "distance_to_stop_codon",
    ]
    rows = [
        ("plus_t1", "ENSG_PLUS", "PLUS", "chr1", 100, 101, "+", "Terminal_exon", 0.10, 100),
        ("plus_t2", "ENSG_PLUS", "PLUS", "chr1", 200, 201, "+", "Downstream_of_terminal_exon", 0.20, 200),
        ("plus_i1", "ENSG_PLUS", "PLUS", "chr1", 50, 51, "+", "Intron", 0.40, -50),
        ("plus_i2", "ENSG_PLUS", "PLUS", "chr1", 70, 71, "+", "Intron", 0.50, -30),
        ("plus_i_fail", "ENSG_PLUS", "PLUS", "chr1", 80, 81, "+", "Intron", 0.51, -20),
        ("plus_other", "ENSG_PLUS", "PLUS", "chr1", 300, 301, "+", "Other_exon", 0.01, 300),
        ("plus_unknown", "ENSG_PLUS", "PLUS", "chr1", 400, 401, "+", "Unknown", 0.01, 400),
        ("single_t", "ENSG_SINGLE", "SINGLE", "chr2", 100, 101, "+", "Terminal_exon", 0.01, 100),
        ("single_i", "ENSG_SINGLE", "SINGLE", "chr2", 50, 51, "+", "Intron", 0.10, -50),
        ("minus_t1", "ENSG_MINUS", "MINUS", "chr3", 900, 901, "-", "Terminal_exon", 0.10, 100),
        ("minus_t2", "ENSG_MINUS", "MINUS", "chr3", 800, 801, "-", "Downstream_of_terminal_exon", 0.10, 200),
        ("minus_i", "ENSG_MINUS", "MINUS", "chr3", 950, 951, "-", "Intron", 0.10, -50),
        ("filtered_t", "ENSG_FILTERED", "FILTERED", "chr4", 100, 101, "+", "Terminal_exon", 0.21, 100),
        ("filtered_i", "ENSG_FILTERED", "FILTERED", "chr4", 50, 51, "+", "Intron", 0.40, -50),
        ("wrong_strand_i", "ENSG_PLUS", "PLUS", "chr1", 500, 501, "-", "Intron", 0.10, 500),
        ("intergenic", pd.NA, "INTERGENIC", "chr5", 10, 11, "+", "Intergenic", 0.01, 10),
    ]
    frame = pd.DataFrame(rows, columns=columns)
    frame["three_prime_utr_length"] = frame["distance_to_stop_codon"].clip(lower=0)
    return frame


def _counts() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ("plus_t1", "cell1", 3),
            ("plus_t2", "cell1", 1),
            ("plus_i1", "cell1", 2),
            ("plus_i2", "cell1", 4),
            ("plus_i_fail", "cell1", 8),
            ("plus_other", "cell1", 100),
            ("plus_unknown", "cell1", 100),
            ("plus_t1", "cell2", 2),
            ("single_t", "cell1", 5),
            ("single_i", "cell1", 1),
            ("minus_t1", "cell1", 2),
            ("minus_t2", "cell1", 1),
            ("minus_i", "cell1", 1),
            ("filtered_t", "cell1", 20),
            ("filtered_i", "cell1", 3),
            ("wrong_strand_i", "cell1", 9),
            ("intergenic", "cell1", 100),
        ],
        columns=["peak_name", "cell_barcode", "umi_count"],
    )


def _result(counts: pd.DataFrame | None = None):
    return compute_terminal_aware_tables(
        _counts() if counts is None else counts,
        _metadata(),
        general_threshold=0.20,
        intronic_threshold=0.50,
        psi_min_gene_umis=1,
    )


def test_legacy_is_default_and_terminal_mode_is_opt_in():
    assert quantification_mode(SimpleNamespace()) == "legacy"
    assert intronic_misprimed_ratio_threshold(SimpleNamespace()) == 0.5
    assert quantification_mode(SimpleNamespace(quantification_mode="terminal-aware")) == "terminal-aware"
    assert (
        quantification_artifact_suffix("counts.tsv.gz", "legacy")
        == "counts.tsv.gz"
    )
    assert quantification_artifact_suffix(
        "counts.tsv.gz", "terminal-aware"
    ).endswith("terminal-aware.v2.tsv.gz")
    with pytest.raises(ValueError):
        quantification_mode(SimpleNamespace(quantification_mode="replacement"))


def test_terminal_feature_filtering_regrouping_and_independent_thresholds():
    result = _result()
    retained = set(result["terminal_for_psi"]["peak_name"])
    assert retained == {"plus_t1", "plus_t2", "minus_t1", "minus_t2"}
    assert "single_t" not in retained
    assert "filtered_t" not in set(result["terminal_all"]["peak_name"])
    assert set(result["terminal_for_psi"]["peak_order_in_gene"]) == {1, 2}

    stats = result["stats"]
    assert stats["retained_terminal_pas_count"] == 5
    assert stats["genes_with_at_least_two_terminal_pas"] == 2
    assert stats["genes_excluded_with_one_terminal_pas"] == 1
    assert stats["intronic_pas_before_filter"] == 7
    assert stats["intronic_pas_after_filter"] == 6
    assert stats["intronic_pas_failed_filter"] == 1

    per_site = result["per_site"].set_index(
        ["intronic_pas_id", "quantification_unit_id"]
    )
    # General threshold 0.20 is stricter, but the independent intronic
    # threshold retains a ratio of 0.40 and includes equality at 0.50.
    assert bool(per_site.loc[("plus_i1", "cell1"), "mispriming_pass"])
    assert bool(per_site.loc[("plus_i2", "cell1"), "mispriming_pass"])
    assert not bool(per_site.loc[("plus_i_fail", "cell1"), "mispriming_pass"])
    assert pd.isna(per_site.loc[("plus_i_fail", "cell1"), "iPUI_i"])


def test_psi_and_weighted_length_use_legacy_formulas_on_terminal_counts_only():
    result = _result()
    plus_psi = result["psi"].query(
        "gene_name == 'PLUS' and cell_barcode == 'cell1'"
    ).set_index("peak_name")
    assert plus_psi.loc["plus_t1", "gene_umi_count"] == 4
    assert plus_psi.loc["plus_t1", "psi"] == pytest.approx(3 / 4)
    assert plus_psi.loc["plus_t2", "psi"] == pytest.approx(1 / 4)

    weighted = result["weighted_utr"].query(
        "gene_name == 'PLUS' and cell_barcode == 'cell1'"
    ).iloc[0]
    assert weighted["weighted_three_prime_utr_length"] == pytest.approx(
        (3 * 100 + 1 * 200) / 4
    )
    assert "SINGLE" not in set(result["psi"]["gene_name"])
    assert "Other_exon" not in set(result["terminal_for_psi"]["feature"])


def test_ipui_site_total_zero_pseudocount_strand_and_shared_u():
    result = _result()
    per_site = result["per_site"].set_index(
        ["intronic_pas_id", "quantification_unit_id"]
    )
    assert per_site.loc[("plus_i1", "cell1"), "I_i"] == 2
    assert per_site.loc[("plus_i1", "cell1"), "U"] == 4
    assert per_site.loc[("plus_i1", "cell1"), "iPUI_i"] == pytest.approx(
        math.log2(3 / 5)
    )
    assert per_site.loc[("plus_i2", "cell1"), "U"] == 4

    # cell2 has terminal usage but no intronic UMI, so the pseudocount case is
    # represented rather than silently omitted.
    assert per_site.loc[("plus_i1", "cell2"), "I_i"] == 0
    assert per_site.loc[("plus_i1", "cell2"), "U"] == 2
    assert per_site.loc[("plus_i1", "cell2"), "iPUI_i"] == pytest.approx(
        math.log2(1 / 3)
    )

    # Plus uses BED end and minus uses BED start as the strand-aware PAS coordinate.
    assert per_site.loc[("plus_i1", "cell1"), "coordinate"] == 51
    assert per_site.loc[("minus_i", "cell1"), "coordinate"] == 950
    assert per_site.loc[("minus_i", "cell1"), "U"] == 3
    assert per_site.loc[("minus_i", "cell1"), "iPUI_i"] == pytest.approx(
        math.log2(2 / 4)
    )

    plus_total = result["gene_total"].query(
        "gene_id == 'ENSG_PLUS' and strand == '+' and quantification_unit_id == 'cell1'"
    ).iloc[0]
    assert plus_total["retained_intronic_pas_count"] == 2
    assert plus_total["I_total"] == 6
    assert plus_total["U"] == 4
    assert plus_total["total_iPUI"] == pytest.approx(math.log2(7 / 5))

    # The same gene name/ID on the opposite strand cannot borrow plus-strand U.
    wrong_strand = per_site.loc[("wrong_strand_i", "cell1")]
    assert pd.isna(wrong_strand["U"])
    assert pd.isna(wrong_strand["iPUI_i"])

    # A passing intron whose only terminal PAS failed the general threshold is audited
    # but is not assigned a denominator.
    filtered = per_site.loc[("filtered_i", "cell1")]
    assert bool(filtered["mispriming_pass"])
    assert pd.isna(filtered["U"])
    assert pd.isna(filtered["iPUI_i"])


def test_dense_and_chunked_long_semantics_are_equivalent():
    dense_counts = _counts()
    chunked_long = pd.concat(
        [
            dense_counts.assign(umi_count=lambda frame: frame["umi_count"] // 2),
            dense_counts.assign(
                umi_count=lambda frame: frame["umi_count"] - frame["umi_count"] // 2
            ),
        ],
        ignore_index=True,
    )
    dense = _result(dense_counts)
    streamed = _result(chunked_long)
    for key in ("gene_cell", "psi", "weighted_utr", "per_site", "gene_total"):
        pdt.assert_frame_equal(
            dense[key].reset_index(drop=True),
            streamed[key].reset_index(drop=True),
            check_dtype=False,
        )


def _same_name_metadata() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            ("g1_single", "G1", "DUP", "+", 100),
            ("g2_single", "G2", "DUP", "+", 100),
            ("g3_plus_1", "G3", "DUP", "+", 100),
            ("g3_plus_2", "G3", "DUP", "+", 200),
            ("g3_minus_single", "G3", "DUP", "-", 300),
        ],
        columns=[
            "peak_name",
            "gene_id",
            "gene_name",
            "strand",
            "distance_to_stop_codon",
        ],
    )
    frame["chrom"] = "chr9"
    frame["start"] = range(10, 15)
    frame["end"] = frame["start"] + 1
    frame["feature"] = "Terminal_exon"
    frame["misprimed_ratio"] = 0.0
    frame["three_prime_utr_length"] = frame["distance_to_stop_codon"]
    return frame


def test_terminal_grouping_uses_true_gene_identity_and_strand():
    metadata = _same_name_metadata()
    counts = pd.DataFrame(
        [(peak_name, "cell", 1) for peak_name in metadata["peak_name"]],
        columns=["peak_name", "cell_barcode", "umi_count"],
    )
    result = compute_terminal_aware_tables(
        counts,
        metadata,
        general_threshold=1.0,
        intronic_threshold=0.5,
        psi_min_gene_umis=1,
    )

    assert set(result["terminal_for_psi"]["peak_name"]) == {
        "g3_plus_1",
        "g3_plus_2",
    }
    assert result["stats"]["genes_with_at_least_two_terminal_pas"] == 1
    assert result["stats"]["genes_excluded_with_one_terminal_pas"] == 3
    assert result["stats"]["terminal_pas_for_psi_count"] == 2

    psi = result["psi"]
    assert set(psi["gene_id"]) == {"G3"}
    assert set(psi["strand"]) == {"+"}
    assert set(psi["gene_umi_count"]) == {2}
    assert set(psi["psi"]) == {0.5}


def test_terminal_long_and_dense_identity_is_unambiguous_for_duplicate_names():
    rows = []
    for gene_id, strand, offset in (("G1", "+", 0), ("G2", "+", 10), ("G1", "-", 20)):
        rows.extend(
            [
                (f"{gene_id}_{strand}_1", gene_id, "DUP", strand, 100 + offset),
                (f"{gene_id}_{strand}_2", gene_id, "DUP", strand, 200 + offset),
            ]
        )
    metadata = pd.DataFrame(
        rows,
        columns=[
            "peak_name",
            "gene_id",
            "gene_name",
            "strand",
            "distance_to_stop_codon",
        ],
    )
    metadata["chrom"] = "chr10"
    metadata["start"] = range(100, 100 + len(metadata))
    metadata["end"] = metadata["start"] + 1
    metadata["feature"] = "Terminal_exon"
    metadata["misprimed_ratio"] = 0.0
    metadata["three_prime_utr_length"] = metadata["distance_to_stop_codon"]
    counts = pd.DataFrame(
        [(peak_name, "cell", 1) for peak_name in metadata["peak_name"]],
        columns=["peak_name", "cell_barcode", "umi_count"],
    )

    result = compute_terminal_aware_tables(
        counts,
        metadata,
        general_threshold=1.0,
        intronic_threshold=0.5,
        psi_min_gene_umis=1,
    )
    long_table = result["gene_cell"]
    assert {
        "gene_id",
        "gene_name",
        "strand",
        "terminal_gene_unit_id",
    }.issubset(long_table.columns)
    assert len(long_table) == 3
    assert long_table["terminal_gene_unit_id"].nunique() == 3

    dense = long_table.pivot(
        index="cell_barcode",
        columns="terminal_gene_unit_id",
        values="gene_umi_count",
    )
    assert dense.shape == (1, 3)
    assert set(dense.iloc[0]) == {2}


def test_terminal_pas_without_true_gene_id_is_not_silently_grouped():
    metadata = _same_name_metadata().iloc[:2].copy()
    metadata["gene_id"] = pd.NA
    counts = pd.DataFrame(
        [(peak_name, "cell", 1) for peak_name in metadata["peak_name"]],
        columns=["peak_name", "cell_barcode", "umi_count"],
    )
    result = compute_terminal_aware_tables(
        counts,
        metadata,
        general_threshold=1.0,
        intronic_threshold=0.5,
        psi_min_gene_umis=1,
    )
    assert result["terminal_all"].empty
    assert result["psi"].empty
    assert result["stats"]["terminal_pas_missing_gene_id"] == 2


def test_ipui_joins_use_gene_id_and_strand_not_display_name():
    metadata = pd.DataFrame(
        [
            ("terminal_a", "G1", "DISPLAY_A", "chr1", 100, 101, "+", "Terminal_exon", 0.0, 100),
            ("intron_a", "G1", "DISPLAY_B", "chr1", 50, 51, "+", "Intron", 0.0, -50),
        ],
        columns=[
            "peak_name",
            "gene_id",
            "gene_name",
            "chrom",
            "start",
            "end",
            "strand",
            "feature",
            "misprimed_ratio",
            "distance_to_stop_codon",
        ],
    )
    metadata["three_prime_utr_length"] = metadata[
        "distance_to_stop_codon"
    ].clip(lower=0)
    counts = pd.DataFrame(
        [("terminal_a", "cell", 4), ("intron_a", "cell", 2)],
        columns=["peak_name", "cell_barcode", "umi_count"],
    )

    result = compute_terminal_aware_tables(
        counts,
        metadata,
        general_threshold=1.0,
        intronic_threshold=1.0,
        psi_min_gene_umis=1,
    )

    site = result["per_site"].iloc[0]
    assert site["gene_id"] == "G1"
    assert site["gene_name"] == "DISPLAY_B"
    assert site["U"] == 4
    assert site["iPUI_i"] == pytest.approx(math.log2(3 / 5))
    assert len(result["gene_total"]) == 1
    assert result["gene_total"].iloc[0]["total_iPUI"] == pytest.approx(
        math.log2(3 / 5)
    )


def test_pas_order_uses_distance_before_conflicting_or_missing_aliases():
    metadata = pd.DataFrame(
        [
            ("proximal", "G1", "Z_ALIAS", 100, 10),
            ("distal", "G1", "A_ALIAS", 200, 20),
            ("middle", "G1", pd.NA, 150, 15),
        ],
        columns=["peak_name", "gene_id", "gene_name", "distance_to_stop_codon", "start"],
    )
    metadata["chrom"] = "chr1"
    metadata["end"] = metadata["start"] + 1
    metadata["strand"] = "+"
    metadata["feature"] = "Terminal_exon"
    metadata["misprimed_ratio"] = 0.0
    metadata["three_prime_utr_length"] = metadata["distance_to_stop_codon"]
    counts = pd.DataFrame(
        [(peak_name, "cell", 1) for peak_name in metadata["peak_name"]],
        columns=["peak_name", "cell_barcode", "umi_count"],
    )

    result = compute_terminal_aware_tables(
        counts,
        metadata,
        general_threshold=1.0,
        intronic_threshold=1.0,
        psi_min_gene_umis=1,
    )
    ordered = result["terminal_all"].sort_values("peak_order_in_gene")
    assert ordered["peak_name"].tolist() == ["proximal", "middle", "distal"]
    assert set(result["psi"]["gene_name"]) == {"A_ALIAS"}
    assert set(result["weighted_utr"]["gene_name"]) == {"A_ALIAS"}
