"""Site feature labels and their display-only overview grouping.

Only TERMINAL_FEATURES and INTRON_FEATURE (utils.terminal_quant_utils) decide
metric eligibility and the intronic mispriming threshold. The "Other" group
below exists for overview tables only; it must never be used to filter sites,
reads or counts, or to decide eligibility.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

INTERGENIC_FEATURE = "Intergenic"

# Sites inside an assigned gene but outside its CDS-bearing canonical model.
# They are deliberately absent from TERMINAL_FEATURES and INTRON_FEATURE, so
# they keep the historical non-terminal, non-intronic treatment (general
# mispriming threshold, no stop codon, no 3' UTR length, no PSI/iPUI role).
NONCODING_EXON_FEATURE = "Noncoding_exon"
NONCODING_GENE_BODY_FEATURE = "Noncoding_gene_body"
NONCANONICAL_EXON_FEATURE = "Noncanonical_exon"
NONCANONICAL_GENE_BODY_FEATURE = "Noncanonical_gene_body"
# Diagnostic: shares a base with the canonical model (exon, intron or the
# downstream window) under exact BED/GTF conversion, yet the canonical
# classifier's historical boundary rule did not claim it. Kept outside the
# terminal and intronic metrics pending a decision on that boundary rule.
CANONICAL_MODEL_EDGE_FEATURE = "Canonical_model_edge"

# Overview group: exactly the labels that terminal-annotation-v3 reported as
# Intergenic, so an "Other" total equals the former Intergenic total.
OTHER_FEATURE_GROUP = "Other"
OTHER_FEATURE_SUBCATEGORIES = (
    INTERGENIC_FEATURE,
    NONCODING_EXON_FEATURE,
    NONCODING_GENE_BODY_FEATURE,
    NONCANONICAL_EXON_FEATURE,
    NONCANONICAL_GENE_BODY_FEATURE,
    CANONICAL_MODEL_EDGE_FEATURE,
)
_OTHER_SUBCATEGORY_SET = frozenset(OTHER_FEATURE_SUBCATEGORIES)
FEATURE_OVERVIEW_ORDER = (
    "Terminal_exon",
    "Downstream_of_terminal_exon",
    "Intron",
    "Other_exon",
    OTHER_FEATURE_GROUP,
)
FEATURE_OVERVIEW_COLUMNS = ["level", "group", "feature", "sites", "mispriming_pass_sites", "umis"]
MISSING_FEATURE_LABEL = "(missing)"
# Count-matrix UMIs on peaks absent from the site table (normally none).
UNMATCHED_COUNTS_LABEL = "(not in site table)"
FEATURE_OVERVIEW_FILE_COLUMNS = ["sample_id", *FEATURE_OVERVIEW_COLUMNS]


def feature_overview_group(feature: Any) -> str:
    """Display group of a site feature label (Intergenic and the five new labels -> Other)."""

    label = MISSING_FEATURE_LABEL if feature is None or pd.isna(feature) else str(feature)
    if label in _OTHER_SUBCATEGORY_SET:
        return OTHER_FEATURE_GROUP
    # A site literally labelled "Other" is not one of Other's subcategories;
    # keep it apart so the subcategory rows always add up to the group.
    return f"{label} (site label)" if label == OTHER_FEATURE_GROUP else label


def feature_overview_table(
    sites: pd.DataFrame,
    *,
    umis_by_peak: Mapping[str, int] | pd.Series | None = None,
    pass_column: str | None = "mispriming_pass",
) -> pd.DataFrame:
    """Overview rows per display group, plus one subcategory row per Other label.

    ``sites`` needs ``peak_name`` and ``feature`` (one row per site).
    ``mispriming_pass_sites`` counts truthy ``pass_column`` values and ``umis``
    sums ``umis_by_peak`` (missing peaks count 0); either is NA when not given.
    With ``umis_by_peak``, a final ``unmatched`` row holds UMIs on peaks that
    are not in ``sites``, so all ``group`` rows plus it add up to the input.
    Display only: the input is not filtered and nothing here feeds a metric.
    """

    frame = sites[["peak_name", "feature"]].copy()
    if frame["peak_name"].duplicated().any():
        raise ValueError("feature_overview_table needs one row per peak_name")
    frame["feature"] = frame["feature"].map(
        lambda value: MISSING_FEATURE_LABEL if value is None or pd.isna(value) else str(value)
    )
    frame["group"] = frame["feature"].map(feature_overview_group)
    if pass_column is not None and pass_column in sites.columns:
        frame["passed"] = sites[pass_column].astype("boolean").fillna(False).astype(int).to_numpy()
    else:
        frame["passed"] = pd.NA
    if umis_by_peak is not None:
        lookup = pd.Series(umis_by_peak, dtype="float64")
        frame["umis"] = frame["peak_name"].map(lookup).fillna(0).astype("int64")
    else:
        frame["umis"] = pd.NA

    has_pass = pass_column is not None and pass_column in sites.columns
    has_umis = umis_by_peak is not None

    def summarize(subset: pd.DataFrame) -> tuple[int, Any, Any]:
        passed = int(subset["passed"].sum()) if has_pass else pd.NA
        umis = int(subset["umis"].sum()) if has_umis else pd.NA
        return len(subset), passed, umis

    groups = list(FEATURE_OVERVIEW_ORDER) + sorted(
        set(frame["group"]) - set(FEATURE_OVERVIEW_ORDER)
    )
    rows = []
    for group in groups:
        subset = frame.loc[frame["group"].eq(group)]
        rows.append(("group", group, group, *summarize(subset)))
        if group == OTHER_FEATURE_GROUP:
            for feature in OTHER_FEATURE_SUBCATEGORIES:
                rows.append(("subcategory", group, feature, *summarize(subset.loc[subset["feature"].eq(feature)])))
    if has_umis:
        outside = lookup.loc[~lookup.index.isin(frame["peak_name"])]
        rows.append(("unmatched", UNMATCHED_COUNTS_LABEL, UNMATCHED_COUNTS_LABEL, 0,
                     0 if has_pass else pd.NA, int(outside.sum())))
    return pd.DataFrame(rows, columns=FEATURE_OVERVIEW_COLUMNS)


def write_feature_overview(table: pd.DataFrame, path: Path, *, sample_id: str) -> None:
    """Atomically write an overview table as gzip TSV; unavailable values are ``NA``."""

    out = table.copy()
    out.insert(0, "sample_id", sample_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        out[FEATURE_OVERVIEW_FILE_COLUMNS].to_csv(tmp, sep="\t", index=False, na_rep="NA", compression="gzip")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def format_feature_overview(table: pd.DataFrame) -> str:
    """One log line for an overview table: groups, then Other's subcategories."""

    def cell(row) -> str:
        text = f"{row.feature} {row.sites}"
        if not pd.isna(row.mispriming_pass_sites):
            text += f" (mispriming pass {row.mispriming_pass_sites})"
        if not pd.isna(row.umis):
            text += f" umis {row.umis}"
        return text

    groups = [cell(row) for row in table.itertuples() if row.level == "group"]
    groups += [cell(row) for row in table.itertuples() if row.level == "unmatched" and row.umis]
    details = [cell(row) for row in table.itertuples() if row.level == "subcategory"]
    return (
        "Feature overview (display grouping only; metrics use the site labels): "
        + "; ".join(groups)
        + (f". {OTHER_FEATURE_GROUP} = " + ", ".join(details) if details else "")
    )
