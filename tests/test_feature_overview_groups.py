"""Display-only Other grouping of former-Intergenic site labels."""
from __future__ import annotations

import random

import pandas as pd
import pytest

from utils import annotation_utils
from utils.feature_labels import (
    FEATURE_OVERVIEW_ORDER,
    OTHER_FEATURE_GROUP,
    OTHER_FEATURE_SUBCATEGORIES,
    feature_overview_group,
    feature_overview_table,
    format_feature_overview,
)
from utils.terminal_quant_utils import INTRON_FEATURE, TERMINAL_FEATURES

NEW = OTHER_FEATURE_SUBCATEGORIES[1:]


def test_other_is_exactly_intergenic_plus_the_five_new_labels():
    assert OTHER_FEATURE_SUBCATEGORIES == (
        "Intergenic", "Noncoding_exon", "Noncoding_gene_body",
        "Noncanonical_exon", "Noncanonical_gene_body", "Canonical_model_edge",
    )
    # The classifier writes the same strings it is grouped by.
    assert set(NEW) == {
        annotation_utils.NONCODING_EXON_FEATURE, annotation_utils.NONCODING_GENE_BODY_FEATURE,
        annotation_utils.NONCANONICAL_EXON_FEATURE, annotation_utils.NONCANONICAL_GENE_BODY_FEATURE,
        annotation_utils.CANONICAL_MODEL_EDGE_FEATURE,
    }
    for label in OTHER_FEATURE_SUBCATEGORIES:
        assert feature_overview_group(label) == OTHER_FEATURE_GROUP
    for label in (*TERMINAL_FEATURES, INTRON_FEATURE, "Other_exon"):
        assert feature_overview_group(label) == label
    # Grouping never touches metric eligibility sets.
    assert not set(OTHER_FEATURE_SUBCATEGORIES) & (set(TERMINAL_FEATURES) | {INTRON_FEATURE})
    assert OTHER_FEATURE_GROUP not in set(TERMINAL_FEATURES) | {INTRON_FEATURE}
    assert FEATURE_OVERVIEW_ORDER[-1] == OTHER_FEATURE_GROUP


def _sites(n=400, seed=7):
    rng = random.Random(seed)
    labels = ["Terminal_exon", "Downstream_of_terminal_exon", "Intron", "Other_exon", "Intergenic"]
    rows = [(f"p{i}", rng.choice(labels), rng.random() < 0.8) for i in range(n)]
    umis = {name: rng.randrange(0, 1000) for name, _, _ in rows if rng.random() < 0.9}
    return pd.DataFrame(rows, columns=["peak_name", "feature", "mispriming_pass"]), umis


def test_other_totals_equal_former_intergenic_totals_and_subcategories_partition():
    old, umis = _sites()
    rng = random.Random(3)
    new = old.copy()
    intergenic = new["feature"].eq("Intergenic")
    new.loc[intergenic, "feature"] = [rng.choice(OTHER_FEATURE_SUBCATEGORIES) for _ in range(int(intergenic.sum()))]
    before = feature_overview_table(old, umis_by_peak=umis).set_index(["level", "feature"])
    after = feature_overview_table(new, umis_by_peak=umis).set_index(["level", "feature"])
    cols = ["sites", "mispriming_pass_sites", "umis"]
    assert after.loc[("group", OTHER_FEATURE_GROUP), cols].tolist() == before.loc[("group", OTHER_FEATURE_GROUP), cols].tolist()
    assert before.loc[("group", OTHER_FEATURE_GROUP), cols].tolist() == before.loc[("subcategory", "Intergenic"), cols].tolist()
    subcats = after.loc["subcategory", cols]
    assert subcats.sum().tolist() == after.loc[("group", OTHER_FEATURE_GROUP), cols].tolist()
    for group in ("Terminal_exon", "Downstream_of_terminal_exon", "Intron", "Other_exon"):
        assert after.loc[("group", group), cols].tolist() == before.loc[("group", group), cols].tolist()


def test_overview_needs_one_row_per_site_and_marks_missing_inputs():
    sites = pd.DataFrame({"peak_name": ["a", "a"], "feature": ["Intron", "Intron"]})
    with pytest.raises(ValueError, match="one row per peak_name"):
        feature_overview_table(sites)
    sites = pd.DataFrame({"peak_name": ["a", "b"], "feature": ["Noncoding_exon", None]})
    table = feature_overview_table(sites, pass_column=None)
    assert table["umis"].isna().all() and table["mispriming_pass_sites"].isna().all()
    assert "(missing)" in set(table["group"])
    line = format_feature_overview(table)
    assert line.startswith("Feature overview (display grouping only") and "Other 1" in line
    assert "Noncoding_exon 1" in line


def test_overview_numbers_on_a_hand_checked_fixture():
    sites = pd.DataFrame({
        "peak_name": ["t1", "t2", "i1", "o1", "g1", "n1", "n2", "e1", "x1"],
        "feature": ["Terminal_exon", "Terminal_exon", "Intron", "Other_exon", "Intergenic",
                    "Noncoding_exon", "Noncoding_exon", "Canonical_model_edge", "Other"],
        "mispriming_pass": [True, False, True, True, pd.NA, True, False, True, True],
    })
    before = sites.copy(deep=True)
    umis = {"t1": 10, "t2": 3, "i1": 4, "g1": 7, "n1": 100, "e1": 1, "x1": 9}  # o1, n2 have no counts
    table = feature_overview_table(sites, umis_by_peak=umis).set_index(["level", "feature"])
    cols = ["sites", "mispriming_pass_sites", "umis"]
    expected = {
        ("group", "Terminal_exon"): [2, 1, 13],
        ("group", "Intron"): [1, 1, 4],
        ("group", "Other_exon"): [1, 1, 0],
        ("group", "Other"): [4, 2, 108],              # NA pass is not a pass
        ("subcategory", "Intergenic"): [1, 0, 7],
        ("subcategory", "Noncoding_exon"): [2, 1, 100],
        ("subcategory", "Canonical_model_edge"): [1, 1, 1],
        ("subcategory", "Noncanonical_exon"): [0, 0, 0],
        ("group", "Other (site label)"): [1, 1, 9],   # a literal "Other" label stays apart
        ("unmatched", "(not in site table)"): [0, 0, 0],
    }
    for key, values in expected.items():
        assert table.loc[key, cols].tolist() == values, key
    pd.testing.assert_frame_equal(sites, before)
