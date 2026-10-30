from __future__ import annotations


import pandas as pd
import pytest

from test_terminal_quant_utils import _counts, _metadata, _result
from utils.annotation_utils import convert_peaks_bed_to_gtf
from utils.terminal_quant_utils import (
    COUNTING_EXCLUSION_MISPRIMED,
    COUNTING_EXCLUSION_NO_TAIL_SUPPORT,
    IPUI_AUDIT_ONLY,
    IPUI_TRUSTED_SUPPORT,
    add_counting_status,
    compute_terminal_aware_tables,
)


def _bed(path, rows):
    # 15-column annotated BED: name, ratio, feature vary; the rest is filler.
    path.write_text("".join(
        f"chr1\t{100 + 10 * i}\t{105 + 10 * i}\t{name}\t9\t+\t3\t{ratio}\t9\tG\t0\tT1\t500\t20\t{feature}\n"
        for i, (name, ratio, feature) in enumerate(rows)
    ))
    return path


ROWS = [
    ("pass_terminal", "0.1", "Terminal_exon"),
    ("fail_terminal", "0.9", "Terminal_exon"),
    ("fail_intron", "0.9", "Intron"),
    ("no_tail", "NA", "Terminal_exon"),
    ("no_tail_intron", "NA", "Intron"),
]


@pytest.mark.parametrize("threshold", [None, 0.5], ids=["legacy", "terminal-aware"])
def test_status_columns_match_the_counting_gtf(tmp_path, threshold):
    bed = _bed(tmp_path / "a.bed", ROWS)
    gtf = tmp_path / "a.gtf"
    convert_peaks_bed_to_gtf(bed, gtf, non_intronic_misprimed_ratio_threshold=threshold)
    in_gtf = {line.split("peak_name ")[1].split(";")[0] for line in gtf.read_text().splitlines()}

    frame = pd.DataFrame(ROWS, columns=["peak_name", "misprimed_ratio", "feature"])
    flagged = add_counting_status(frame, non_intronic_threshold=threshold).set_index("peak_name")
    assert set(flagged.index[flagged["included_in_counting"]]) == in_gtf
    expected = {
        "pass_terminal": "",
        "fail_terminal": COUNTING_EXCLUSION_MISPRIMED if threshold is not None else "",
        "fail_intron": "",
        "no_tail": COUNTING_EXCLUSION_NO_TAIL_SUPPORT,
        "no_tail_intron": COUNTING_EXCLUSION_NO_TAIL_SUPPORT,
    }
    assert flagged["exclusion_reason"].to_dict() == expected
    assert (flagged["exclusion_reason"].eq("") == flagged["included_in_counting"]).all()


def test_ipui_rows_carry_support_status():
    # An audit-only observation: a failed intronic PAS creates a unit for
    # ENSG_AUDIT whose trusted I and U are both zero.
    meta = pd.concat([_metadata(), pd.DataFrame([
        ("audit_t", "ENSG_AUDIT", "AUDIT", "chr6", 100, 101, "+", "Terminal_exon", 0.01, 100, 100),
        ("audit_t2", "ENSG_AUDIT", "AUDIT", "chr6", 200, 201, "+", "Terminal_exon", 0.01, 200, 200),
        ("audit_ok_i", "ENSG_AUDIT", "AUDIT", "chr6", 50, 51, "+", "Intron", 0.10, -50, 0),
        ("audit_fail_i", "ENSG_AUDIT", "AUDIT", "chr6", 60, 61, "+", "Intron", 0.90, -40, 0),
    ], columns=list(_metadata().columns))], ignore_index=True)
    counts = pd.concat([_counts(), pd.DataFrame(
        [("audit_fail_i", "cell9", 3)], columns=["peak_name", "cell_barcode", "umi_count"]
    )], ignore_index=True)
    result = compute_terminal_aware_tables(
        counts, meta, general_threshold=0.20, intronic_threshold=0.50, psi_min_gene_umis=1,
    )
    total = result["gene_total"].set_index(["gene_id", "quantification_unit_id"])
    audit = total.loc[("ENSG_AUDIT", "cell9")]
    assert (audit["I_total"], audit["U"]) == (0, 0)
    assert audit["total_iPUI"] == pytest.approx(0.0)
    assert audit["ipui_support_status"] == IPUI_AUDIT_ONLY
    assert total.loc[("ENSG_PLUS", "cell1"), "ipui_support_status"] == IPUI_TRUSTED_SUPPORT
    assert set(total["ipui_support_status"]) == {IPUI_TRUSTED_SUPPORT, IPUI_AUDIT_ONLY}

    site = result["per_site"].set_index(["intronic_pas_id", "quantification_unit_id"])
    assert site.loc[("plus_i1", "cell2"), "ipui_support_status"] == IPUI_TRUSTED_SUPPORT  # I=0, U=2
    assert site.loc[("audit_ok_i", "cell9"), "ipui_support_status"] == IPUI_AUDIT_ONLY
    assert pd.isna(site.loc[("wrong_strand_i", "cell1"), "iPUI_i"])
    assert site.loc[("wrong_strand_i", "cell1"), "ipui_support_status"] == IPUI_AUDIT_ONLY


def test_existing_ipui_values_are_unchanged_by_the_status_column():
    result = _result()
    assert list(result["gene_total"].columns)[-1] == "ipui_support_status"
    assert list(result["per_site"].columns)[-1] == "ipui_support_status"
    assert result["gene_total"]["ipui_support_status"].eq(IPUI_TRUSTED_SUPPORT).all()
