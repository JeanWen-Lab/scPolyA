from __future__ import annotations

import math
from typing import Any
from urllib.parse import quote

import pandas as pd

TERMINAL_FEATURES = frozenset(
    {"Terminal_exon", "Downstream_of_terminal_exon"}
)
INTRON_FEATURE = "Intron"
TERMINAL_GENE_KEY_COLUMNS = ["gene_id", "strand"]

_TERMINAL_ARTIFACT_SUFFIXES = {
    "___peaks-mispriming-filtered.bed": "___peaks-mispriming-audited.terminal-aware.v1.bed",
    "___peaks-assigned.bed": "___peaks-assigned.terminal-aware.v2.bed",
    "___peaks-assigned-with-stop-codon.df.pkl": "___peaks-assigned-with-stop-codon.terminal-aware.v2.df.pkl",
    "___peaks-annotated.bed": "___peaks-annotated.terminal-aware.v2.bed",
    "___peaks-annotated.bb": "___peaks-annotated.terminal-aware.v2.bb",
    "___peaks-annotated.gtf": "___peaks-annotated.terminal-aware.v2.gtf",
    "xr_to_peak.tsv.gz": "xr_to_peak.terminal-aware.v2.tsv.gz",
    "peak-counts.tsv.gz": "peak-counts.terminal-aware.v2.tsv.gz",
    "counts.tsv.gz": "counts.terminal-aware.v2.tsv.gz",
    "peak_assignment.tsv.gz": "peak_assignment.terminal-aware.v2.tsv.gz",
    "peak_assignment_summary.json": "peak_assignment_summary.terminal-aware.v2.json",
}

_TERMINAL_SUMMARY_SUFFIXES = {
    "peak_to_gene.tsv.gz": "terminal_aware_peak_to_gene.v2.tsv.gz",
    "gene_cell.tsv.gz": "terminal_aware_gene_cell.v2.tsv.gz",
    "cell_gene.matrix.tsv.gz": "terminal_aware_cell_gene.matrix.v2.tsv.gz",
    "cell_gene.matrix.skipped.json": "terminal_aware_cell_gene.matrix.v2.skipped.json",
    "weighted_three_prime_utr_length.tsv.gz": (
        "terminal_aware_weighted_three_prime_utr_length.v2.tsv.gz"
    ),
    "psi.tsv.gz": "terminal_aware_psi.v2.tsv.gz",
}

TERMINAL_IPUI_PER_SITE_SUFFIX = "intronic_ipui_per_site.v2.tsv.gz"
TERMINAL_IPUI_GENE_TOTAL_SUFFIX = "intronic_ipui_gene_total.v2.tsv.gz"
TERMINAL_DISTAL_USAGE_SUFFIX = "terminal_aware_distal_site_usage.v1.tsv.gz"
# Display-only site/UMI overview by feature group (see utils.feature_labels).
TERMINAL_FEATURE_OVERVIEW_SUFFIX = "terminal_aware_feature_overview.v1.tsv.gz"

TERMINAL_DISTAL_USAGE_COLUMNS = [
    "gene_id", "gene_name", "strand", "terminal_gene_unit_id", "cell_barcode",
    "retained_terminal_pas_count", "distal_pas_count", "distal_umi_count",
    "gene_umi_count", "psi_min_gene_umis", "distal_site_usage", "usage_status",
]

# Counting-GTF membership of each annotated PAS. Rows that were never counted
# stay in site tables for audit but must not be read as measured zeros.
COUNTING_EXCLUSION_NO_TAIL_SUPPORT = "no_same_strand_tail_support"
COUNTING_EXCLUSION_MISPRIMED = "non_intronic_misprimed_ratio_above_threshold"


def counting_inclusion(
    misprimed_ratio: pd.Series,
    feature: pd.Series,
    *,
    non_intronic_threshold: float | None,
) -> tuple[pd.Series, pd.Series]:
    """Which PASs enter the counting GTF, and why the others do not.

    This is the single definition used both to write the counting GTF and to
    flag site tables: a PAS without same-strand tail support (NA ratio) is
    never counted; with a threshold (terminal-aware mode) a non-intronic PAS
    is counted only when ``misprimed_ratio <= threshold``.
    """
    ratio = pd.to_numeric(misprimed_ratio, errors="coerce")
    included = ratio.notna()
    reason = pd.Series("", index=ratio.index, dtype="object")
    reason[~included] = COUNTING_EXCLUSION_NO_TAIL_SUPPORT
    if non_intronic_threshold is not None:
        failed = included & ~(feature.eq(INTRON_FEATURE) | ratio.le(float(non_intronic_threshold)))
        reason[failed] = COUNTING_EXCLUSION_MISPRIMED
        included = included & ~failed
    return included.astype(bool), reason


def add_counting_status(peak_df: pd.DataFrame, *, non_intronic_threshold: float | None) -> pd.DataFrame:
    included, reason = counting_inclusion(
        peak_df["misprimed_ratio"], peak_df["feature"], non_intronic_threshold=non_intronic_threshold
    )
    out = peak_df.copy()
    out["included_in_counting"] = included
    out["exclusion_reason"] = reason
    return out


IPUI_TRUSTED_SUPPORT = "trusted_support"
IPUI_AUDIT_ONLY = "audit_only"


PER_SITE_COLUMNS = [
    "gene_id",
    "gene_name",
    "intronic_pas_id",
    "chromosome",
    "coordinate",
    "strand",
    "quantification_unit_id",
    "I_i",
    "U",
    "iPUI_i",
    "misprimed_ratio",
    "mispriming_pass",
    "ipui_support_status",
]

GENE_TOTAL_COLUMNS = [
    "gene_id",
    "gene_name",
    "strand",
    "quantification_unit_id",
    "retained_intronic_pas_count",
    "I_total",
    "U",
    "total_iPUI",
    "ipui_support_status",
]

TERMINAL_GENE_CELL_COLUMNS = [
    "gene_id",
    "gene_name",
    "strand",
    "terminal_gene_unit_id",
    "cell_barcode",
    "gene_umi_count",
]

TERMINAL_PSI_COLUMNS = [
    "peak_name",
    "gene_id",
    "gene_name",
    "strand",
    "terminal_gene_unit_id",
    "cell_barcode",
    "peak_umi_count",
    "gene_umi_count",
    "three_prime_utr_length",
    "psi",
]

TERMINAL_WEIGHTED_COLUMNS = [
    "gene_id",
    "gene_name",
    "strand",
    "terminal_gene_unit_id",
    "cell_barcode",
    "gene_umi_count",
    "weighted_three_prime_utr_length",
]


def quantification_mode(args: Any) -> str:
    """Return the optional quantification mode without requiring parser plumbing."""
    mode = str(getattr(args, "quantification_mode", "legacy")).strip().lower()
    if mode not in {"legacy", "terminal-aware"}:
        raise ValueError(
            "quantification_mode must be one of: legacy, terminal-aware "
            f"(received {mode!r})"
        )
    return mode


def intronic_misprimed_ratio_threshold(args: Any) -> float:
    """Return the optional intronic threshold with its integration default."""
    return float(getattr(args, "intronic_misprimed_ratio_threshold", 0.5))


def quantification_artifact_suffix(legacy_suffix: str, mode: str) -> str:
    """Keep terminal-aware intermediates from overwriting legacy artifacts."""
    if mode == "legacy":
        return legacy_suffix
    if mode != "terminal-aware":
        raise ValueError(f"Unsupported quantification mode: {mode!r}")
    try:
        return _TERMINAL_ARTIFACT_SUFFIXES[legacy_suffix]
    except KeyError as exc:
        raise ValueError(
            f"No terminal-aware artifact suffix registered for {legacy_suffix!r}"
        ) from exc


def quantification_summary_suffix(legacy_suffix: str, mode: str) -> str:
    """Return the stable summary filename suffix for a quantification mode."""
    if mode == "legacy":
        return legacy_suffix
    if mode != "terminal-aware":
        raise ValueError(f"Unsupported quantification mode: {mode!r}")
    try:
        return _TERMINAL_SUMMARY_SUFFIXES[legacy_suffix]
    except KeyError as exc:
        raise ValueError(
            f"No terminal-aware summary suffix registered for {legacy_suffix!r}"
        ) from exc


def terminal_gene_unit_id(gene_id: Any, strand: Any) -> str:
    """Return a reversible, collision-safe dense-column identifier."""
    return f"{quote(str(gene_id), safe='')}|{quote(str(strand), safe='')}"


def _validate_threshold(value: float, name: str) -> float:
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1 (received {value})")
    return value


def prepare_terminal_peak_metadata(
    peak_meta_df: pd.DataFrame,
    *,
    general_threshold: float,
    intronic_threshold: float,
) -> dict[str, Any]:
    """Audit feature-aware mispriming and prepare terminal/iPUI metadata.

    The general comparator intentionally matches the legacy filter: a PAS passes
    when ``misprimed_ratio <= threshold``. Intronic PASs use the same comparator
    with an independent threshold.
    """
    general_threshold = _validate_threshold(
        general_threshold, "misprimed_ratio_threshold"
    )
    intronic_threshold = _validate_threshold(
        intronic_threshold, "intronic_misprimed_ratio_threshold"
    )

    required = {
        "peak_name",
        "gene_name",
        "chrom",
        "start",
        "end",
        "strand",
        "feature",
        "misprimed_ratio",
        "distance_to_stop_codon",
        "three_prime_utr_length",
    }
    missing = sorted(required - set(peak_meta_df.columns))
    if missing:
        raise ValueError(f"Peak metadata missing columns: {', '.join(missing)}")

    audit = peak_meta_df.copy()
    if "gene_id" not in audit.columns:
        audit["gene_id"] = pd.NA
    audit["misprimed_ratio"] = pd.to_numeric(
        audit["misprimed_ratio"], errors="coerce"
    )
    audit["general_mispriming_pass"] = (
        audit["misprimed_ratio"].notna()
        & audit["misprimed_ratio"].le(general_threshold)
    )
    audit["intronic_mispriming_pass"] = (
        audit["misprimed_ratio"].notna()
        & audit["misprimed_ratio"].le(intronic_threshold)
    )
    audit["mispriming_pass"] = audit["general_mispriming_pass"]
    intronic_mask = audit["feature"].eq(INTRON_FEATURE)
    audit.loc[intronic_mask, "mispriming_pass"] = audit.loc[
        intronic_mask, "intronic_mispriming_pass"
    ]

    terminal_before_ratio = audit.loc[
        audit["feature"].isin(TERMINAL_FEATURES)
    ].copy()
    terminal_all = terminal_before_ratio.loc[
        terminal_before_ratio["general_mispriming_pass"]
        & terminal_before_ratio["gene_id"].notna()
        & terminal_before_ratio["gene_id"].astype(str).str.strip().ne("")
    ].copy()
    terminal_all = terminal_all.drop_duplicates(subset=["peak_name"], keep="first")
    terminal_all["terminal_gene_unit_id"] = terminal_all.apply(
        lambda row: terminal_gene_unit_id(row["gene_id"], row["strand"]),
        axis=1,
    )

    terminal_all["peak_order_in_gene"] = (
        terminal_all.sort_values(
            [
                "gene_id",
                "strand",
                "distance_to_stop_codon",
                "chrom",
                "start",
                "end",
                "peak_name",
            ],
            na_position="last",
        )
        .groupby(TERMINAL_GENE_KEY_COLUMNS, observed=True)
        .cumcount()
        + 1
    )
    terminal_all["n_peaks_in_gene"] = terminal_all.groupby(
        TERMINAL_GENE_KEY_COLUMNS, observed=True
    )["peak_name"].transform("nunique")
    terminal_all["is_distal_peak"] = (
        terminal_all.groupby(TERMINAL_GENE_KEY_COLUMNS, observed=True)[
            "distance_to_stop_codon"
        ]
        .transform("max")
        .eq(terminal_all["distance_to_stop_codon"])
    )

    recomputed_order = terminal_all[
        ["peak_name", "peak_order_in_gene", "n_peaks_in_gene", "is_distal_peak"]
    ].copy()
    audit = audit.drop(
        columns=["peak_order_in_gene", "n_peaks_in_gene", "is_distal_peak"],
        errors="ignore",
    ).merge(recomputed_order, on="peak_name", how="left")

    terminal_for_psi = terminal_all.loc[
        terminal_all["n_peaks_in_gene"].ge(2)
    ].copy()
    intronic = audit.loc[intronic_mask].drop_duplicates(
        subset=["peak_name"], keep="first"
    ).copy()

    gene_peak_counts = terminal_all.groupby(
        TERMINAL_GENE_KEY_COLUMNS, observed=True
    )["peak_name"].nunique()
    stats = {
        "annotated_pas_count": int(audit["peak_name"].nunique()),
        "terminal_feature_pas_count": int(terminal_before_ratio["peak_name"].nunique()),
        "retained_terminal_pas_count": int(terminal_all["peak_name"].nunique()),
        "terminal_pas_for_psi_count": int(terminal_for_psi["peak_name"].nunique()),
        "terminal_pas_missing_gene_id": int(
            terminal_before_ratio.loc[
                terminal_before_ratio["general_mispriming_pass"]
                & (
                    terminal_before_ratio["gene_id"].isna()
                    | terminal_before_ratio["gene_id"].astype(str).str.strip().eq("")
                ),
                "peak_name",
            ].nunique()
        ),
        "genes_with_at_least_two_terminal_pas": int(gene_peak_counts.ge(2).sum()),
        "genes_excluded_with_one_terminal_pas": int(gene_peak_counts.eq(1).sum()),
        "intronic_pas_before_filter": int(intronic["peak_name"].nunique()),
        "intronic_pas_after_filter": int(
            intronic.loc[intronic["intronic_mispriming_pass"], "peak_name"].nunique()
        ),
        "intronic_pas_failed_filter": int(
            intronic.loc[~intronic["intronic_mispriming_pass"], "peak_name"].nunique()
        ),
        # No same-strand tail support: the ratio is not evaluable, so these
        # PASs fail both comparators and stay audit-only.
        "terminal_pas_not_evaluable": int(
            terminal_before_ratio.loc[
                terminal_before_ratio["misprimed_ratio"].isna(), "peak_name"
            ].nunique()
        ),
        "intronic_pas_not_evaluable": int(
            intronic.loc[intronic["misprimed_ratio"].isna(), "peak_name"].nunique()
        ),
    }
    return {
        "audit": audit,
        "terminal_all": terminal_all,
        "terminal_for_psi": terminal_for_psi,
        "intronic": intronic,
        "stats": stats,
    }


def _normalize_counts(counts_df: pd.DataFrame) -> pd.DataFrame:
    required = {"peak_name", "cell_barcode", "umi_count"}
    missing = sorted(required - set(counts_df.columns))
    if missing:
        raise ValueError(f"Count matrix missing columns: {', '.join(missing)}")
    counts = counts_df[["peak_name", "cell_barcode", "umi_count"]].copy()
    counts["umi_count"] = pd.to_numeric(counts["umi_count"], errors="coerce")
    counts = counts.dropna(subset=["peak_name", "cell_barcode", "umi_count"])
    if counts.empty:
        return counts.astype(
            {"peak_name": "string", "cell_barcode": "string", "umi_count": "int64"}
        )
    counts["peak_name"] = counts["peak_name"].astype(str)
    counts["cell_barcode"] = counts["cell_barcode"].astype(str)
    counts["umi_count"] = counts["umi_count"].astype(int)
    return (
        counts.groupby(
            ["peak_name", "cell_barcode"], as_index=False, observed=True
        )["umi_count"]
        .sum()
    )


def _empty_terminal_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    gene_cell = pd.DataFrame(columns=TERMINAL_GENE_CELL_COLUMNS)
    psi = pd.DataFrame(columns=TERMINAL_PSI_COLUMNS)
    weighted = pd.DataFrame(columns=TERMINAL_WEIGHTED_COLUMNS)
    return gene_cell, psi, weighted


def _deterministic_gene_labels(frame: pd.DataFrame) -> pd.DataFrame:
    """Choose one display label without allowing it to define identity/order."""
    candidates = frame[["peak_name", "gene_id", "gene_name", "strand"]].copy()
    candidates["_gene_name_missing"] = (
        candidates["gene_name"].isna()
        | candidates["gene_name"].fillna("").astype(str).str.strip().eq("")
    )
    candidates["_gene_name_sort"] = candidates["gene_name"].fillna("").astype(str)
    return (
        candidates.sort_values(
            [
                "gene_id",
                "strand",
                "_gene_name_missing",
                "_gene_name_sort",
                "peak_name",
            ],
            kind="stable",
        )
        .drop_duplicates(subset=TERMINAL_GENE_KEY_COLUMNS, keep="first")
        [["gene_id", "strand", "gene_name"]]
    )


def compute_terminal_psi_tables(
    counts_df: pd.DataFrame,
    terminal_for_psi: pd.DataFrame,
    *,
    psi_min_gene_umis: int,
) -> dict[str, pd.DataFrame]:
    """Compute PSI and weighted length with the legacy formulas."""
    counts = _normalize_counts(counts_df)
    if counts.empty or terminal_for_psi.empty:
        gene_cell, psi, weighted = _empty_terminal_tables()
        return {"gene_cell": gene_cell, "psi": psi, "weighted_utr": weighted}

    lookup = terminal_for_psi[
        [
            "peak_name",
            "gene_id",
            "strand",
            "terminal_gene_unit_id",
            "three_prime_utr_length",
        ]
    ].drop_duplicates(subset=["peak_name"])
    gene_labels = _deterministic_gene_labels(terminal_for_psi)
    merged = counts.merge(lookup, on="peak_name", how="inner")
    merged = merged.merge(gene_labels, on=["gene_id", "strand"], how="left")
    if merged.empty:
        gene_cell, psi, weighted = _empty_terminal_tables()
        return {"gene_cell": gene_cell, "psi": psi, "weighted_utr": weighted}

    gene_totals = (
        merged.groupby(
            [
                "gene_id",
                "strand",
                "terminal_gene_unit_id",
                "cell_barcode",
            ],
            as_index=False,
            observed=True,
        )["umi_count"]
        .sum()
        .rename(columns={"umi_count": "gene_umi_count"})
    )
    psi = merged.merge(
        gene_totals,
        on=[
            "gene_id",
            "strand",
            "terminal_gene_unit_id",
            "cell_barcode",
        ],
        how="left",
    )
    gene_cell = gene_totals.merge(
        gene_labels, on=["gene_id", "strand"], how="left"
    )
    psi["psi"] = psi["umi_count"] / psi["gene_umi_count"]
    psi.loc[psi["gene_umi_count"] < psi_min_gene_umis, "psi"] = pd.NA
    psi = psi.rename(columns={"umi_count": "peak_umi_count"})

    weighted = (
        psi.assign(
            weighted_three_prime_utr_length=lambda frame: (
                frame["peak_umi_count"]
                * frame["three_prime_utr_length"]
                / frame["gene_umi_count"]
            )
        )
        .groupby(
            [
                "gene_id",
                "strand",
                "terminal_gene_unit_id",
                "cell_barcode",
                "gene_umi_count",
            ],
            as_index=False,
            observed=True,
        )["weighted_three_prime_utr_length"]
        .sum()
    )
    weighted = weighted.merge(
        gene_labels, on=["gene_id", "strand"], how="left"
    )
    weighted.loc[
        weighted["gene_umi_count"] < psi_min_gene_umis,
        "weighted_three_prime_utr_length",
    ] = pd.NA

    psi = psi[TERMINAL_PSI_COLUMNS]
    return {
        "gene_cell": gene_cell[TERMINAL_GENE_CELL_COLUMNS].sort_values(
            ["gene_id", "gene_name", "strand", "cell_barcode"]
        ).reset_index(drop=True),
        "psi": psi.sort_values(
            ["gene_id", "gene_name", "strand", "cell_barcode", "peak_name"]
        ).reset_index(drop=True),
        "weighted_utr": weighted[TERMINAL_WEIGHTED_COLUMNS].sort_values(
            ["gene_id", "gene_name", "strand", "cell_barcode"]
        ).reset_index(drop=True),
    }


def _pas_coordinate(frame: pd.DataFrame) -> pd.Series:
    coordinate = pd.to_numeric(frame["end"], errors="coerce")
    minus = frame["strand"].eq("-")
    coordinate.loc[minus] = pd.to_numeric(
        frame.loc[minus, "start"], errors="coerce"
    )
    return coordinate.astype("Int64")


def compute_terminal_distal_usage(
    counts_df: pd.DataFrame,
    *,
    audit: pd.DataFrame,
    terminal_all: pd.DataFrame,
    psi_min_gene_umis: int,
) -> pd.DataFrame:
    """Sum all tied distal PAS counts over eligible terminal counts.

    Cover only observed gene/strand/cell pairs in the terminal/intronic audit
    catalogue, plus one blank-cell audit row for each otherwise unobserved gene.
    An absent sparse count is zero only inside an observed pair and an eligible
    terminal catalogue. Missing rows never define zero usage.
    """
    keys = TERMINAL_GENE_KEY_COLUMNS
    pair_keys = [*keys, "cell_barcode"]
    relevant = audit.loc[
        audit["feature"].isin(TERMINAL_FEATURES | {INTRON_FEATURE})
        & audit["gene_id"].notna()
        & audit["gene_id"].astype(str).str.strip().ne("")
        & audit["strand"].isin(["+", "-"])
    ].drop_duplicates("peak_name").copy()
    if relevant.empty:
        return pd.DataFrame(columns=TERMINAL_DISTAL_USAGE_COLUMNS)

    labels = _deterministic_gene_labels(relevant)
    counts = _normalize_counts(counts_df)
    observed = counts.merge(relevant[["peak_name", *keys]], on="peak_name")
    pairs = observed[pair_keys].drop_duplicates()
    # Left join adds only one audit row for an unobserved gene, never a
    # catalogue-by-all-cells Cartesian product.
    result = labels.merge(pairs, on=keys, how="left")
    result["terminal_gene_unit_id"] = [
        terminal_gene_unit_id(gene, strand)
        for gene, strand in zip(result["gene_id"], result["strand"])
    ]

    terminal = terminal_all.loc[terminal_all["strand"].isin(["+", "-"])].copy()
    distance = pd.to_numeric(terminal["distance_to_stop_codon"], errors="coerce")
    terminal["_finite_distance"] = distance.map(math.isfinite)
    terminal["_distance"] = distance
    terminal["_distal"] = distance.eq(
        terminal.groupby(keys, observed=True)["_distance"].transform("max")
    ) & terminal["_finite_distance"]
    catalogue = terminal.groupby(keys, as_index=False, observed=True).agg(
        retained_terminal_pas_count=("peak_name", "nunique"),
        distal_pas_count=("_distal", "sum"),
        _complete_distance=("_finite_distance", "all"),
    )
    result = result.merge(catalogue, on=keys, how="left")
    for col in ("retained_terminal_pas_count", "distal_pas_count"):
        result[col] = result[col].fillna(0).astype(int)

    terminal_counts = counts.merge(terminal[["peak_name", *keys, "_distal"]], on="peak_name")
    terminal_counts["distal_umi_count"] = terminal_counts["umi_count"].where(
        terminal_counts["_distal"], 0
    )
    totals = terminal_counts.groupby(pair_keys, as_index=False, observed=True).agg(
        gene_umi_count=("umi_count", "sum"),
        distal_umi_count=("distal_umi_count", "sum"),
    )
    result = result.merge(totals, on=pair_keys, how="left")
    has_catalogue = result["retained_terminal_pas_count"].gt(0)
    has_unit = result["cell_barcode"].notna()
    for col in ("gene_umi_count", "distal_umi_count"):
        result.loc[has_catalogue & has_unit, col] = result.loc[
            has_catalogue & has_unit, col
        ].fillna(0)
        result[col] = result[col].astype("Int64")
    complete_distance = result["_complete_distance"].eq(True)
    # A partial distance catalogue cannot identify the true maximum.
    result.loc[has_catalogue & ~complete_distance, "distal_umi_count"] = pd.NA
    result.loc[has_catalogue & ~complete_distance, "distal_pas_count"] = pd.NA
    result["distal_pas_count"] = result["distal_pas_count"].astype("Int64")
    result["psi_min_gene_umis"] = psi_min_gene_umis
    result["distal_site_usage"] = pd.Series(pd.NA, index=result.index, dtype="Float64")
    result["usage_status"] = "ok"
    # Assign in reverse priority so each unavailable row has a stable reason.
    for mask, reason in (
        (result["gene_umi_count"].lt(psi_min_gene_umis), "below_min_gene_umis"),
        (result["gene_umi_count"].le(0), "zero_terminal_umis"),
        (~complete_distance, "missing_terminal_distance"),
        (~has_unit, "no_observed_unit"),
        (result["retained_terminal_pas_count"].lt(2), "fewer_than_two_terminal_pas"),
        (~has_catalogue, "no_eligible_terminal_pas"),
    ):
        result.loc[mask.fillna(False), "usage_status"] = reason
    computable = result["usage_status"].eq("ok")
    result.loc[computable, "distal_site_usage"] = (
        result.loc[computable, "distal_umi_count"]
        / result.loc[computable, "gene_umi_count"]
    )
    return result[TERMINAL_DISTAL_USAGE_COLUMNS].sort_values(
        [*keys, "cell_barcode"], na_position="last"
    ).reset_index(drop=True)


def compute_intronic_ipui_tables(
    counts_df: pd.DataFrame,
    *,
    terminal_all: pd.DataFrame,
    intronic: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Compute audited per-site and gene-total iPUI tables.

    Rows are generated for every intronic PAS and every observed quantification
    unit for the same true gene ID and strand. This deliberately creates zero-I
    rows when a unit has terminal usage but no count at a particular intronic PAS.
    Intronic PASs with no observed unit receive one audit-only row with a blank
    quantification unit and blank iPUI.
    """
    counts = _normalize_counts(counts_df)
    if intronic.empty:
        return {
            "per_site": pd.DataFrame(columns=PER_SITE_COLUMNS),
            "gene_total": pd.DataFrame(columns=GENE_TOTAL_COLUMNS),
        }

    intron_meta = intronic[
        [
            "peak_name",
            "gene_id",
            "gene_name",
            "chrom",
            "start",
            "end",
            "strand",
            "misprimed_ratio",
            "intronic_mispriming_pass",
        ]
    ].copy()
    intron_meta["coordinate"] = _pas_coordinate(intron_meta)
    intron_meta = intron_meta.rename(
        columns={
            "peak_name": "intronic_pas_id",
            "chrom": "chromosome",
            "intronic_mispriming_pass": "mispriming_pass",
        }
    )

    valid_intron_meta = intron_meta.loc[
        intron_meta["gene_id"].notna()
        & intron_meta["gene_id"].astype(str).str.strip().ne("")
    ].copy()
    terminal_meta = terminal_all[
        ["peak_name", "gene_id", "strand"]
    ].dropna(subset=["gene_id"])
    terminal_meta = terminal_meta.drop_duplicates(subset=["peak_name"])
    terminal_gene_keys = terminal_meta[
        ["gene_id", "strand"]
    ].drop_duplicates()

    label_candidates = pd.concat(
        [
            terminal_all[["peak_name", "gene_id", "gene_name", "strand"]],
            intronic[["peak_name", "gene_id", "gene_name", "strand"]],
        ],
        ignore_index=True,
    )
    label_candidates = label_candidates.loc[
        label_candidates["gene_id"].notna()
        & label_candidates["gene_id"].astype(str).str.strip().ne("")
    ].copy()
    gene_labels = _deterministic_gene_labels(label_candidates)

    terminal_counts = counts.merge(terminal_meta, on="peak_name", how="inner")
    terminal_totals = (
        terminal_counts.groupby(
            ["gene_id", "strand", "cell_barcode"],
            as_index=False,
            observed=True,
        )["umi_count"]
        .sum()
        .rename(columns={"cell_barcode": "quantification_unit_id", "umi_count": "U"})
    )

    intron_lookup = valid_intron_meta[
        ["intronic_pas_id", "gene_id", "strand"]
    ].rename(columns={"intronic_pas_id": "peak_name"})
    observed_intronic = counts.merge(intron_lookup, on="peak_name", how="inner")
    observed_intronic = (
        observed_intronic.groupby(
            [
                "peak_name",
                "gene_id",
                "strand",
                "cell_barcode",
            ],
            as_index=False,
            observed=True,
        )["umi_count"]
        .sum()
        .rename(
            columns={
                "peak_name": "intronic_pas_id",
                "cell_barcode": "quantification_unit_id",
                "umi_count": "I_i",
            }
        )
    )

    unit_pairs = pd.concat(
        [
            terminal_totals[
                ["gene_id", "strand", "quantification_unit_id"]
            ],
            observed_intronic[
                ["gene_id", "strand", "quantification_unit_id"]
            ],
        ],
        ignore_index=True,
    ).drop_duplicates()

    if valid_intron_meta.empty or unit_pairs.empty:
        expanded = valid_intron_meta.iloc[0:0].copy()
        expanded["quantification_unit_id"] = pd.Series(dtype="string")
    else:
        expanded = valid_intron_meta.merge(
            unit_pairs,
            on=["gene_id", "strand"],
            how="inner",
        )

    expanded = expanded.merge(
        observed_intronic,
        on=[
            "intronic_pas_id",
            "gene_id",
            "strand",
            "quantification_unit_id",
        ],
        how="left",
    )
    if "I_i" not in expanded.columns:
        expanded["I_i"] = 0
    expanded["I_i"] = expanded["I_i"].fillna(0).astype(int)
    expanded = expanded.merge(
        terminal_totals,
        on=["gene_id", "strand", "quantification_unit_id"],
        how="left",
    )
    expanded = expanded.merge(
        terminal_gene_keys.assign(has_eligible_terminal_pas=True),
        on=["gene_id", "strand"],
        how="left",
    )
    expanded["has_eligible_terminal_pas"] = expanded[
        "has_eligible_terminal_pas"
    ].astype("boolean").fillna(False).astype(bool)
    expanded.loc[expanded["has_eligible_terminal_pas"], "U"] = expanded.loc[
        expanded["has_eligible_terminal_pas"], "U"
    ].fillna(0)

    expanded["iPUI_i"] = pd.NA
    computable = (
        expanded["mispriming_pass"].astype(bool)
        & expanded["has_eligible_terminal_pas"]
        & expanded["quantification_unit_id"].notna()
    )
    expanded.loc[computable, "iPUI_i"] = expanded.loc[computable].apply(
        lambda row: math.log2((int(row["I_i"]) + 1) / (int(row["U"]) + 1)),
        axis=1,
    )

    represented_sites = set(expanded.get("intronic_pas_id", pd.Series(dtype=str)))
    missing_audit = intron_meta.loc[
        ~intron_meta["intronic_pas_id"].isin(represented_sites)
    ].copy()
    if not missing_audit.empty:
        missing_audit["quantification_unit_id"] = pd.NA
        missing_audit["I_i"] = 0
        missing_audit["U"] = pd.NA
        missing_audit["iPUI_i"] = pd.NA
        missing_audit["has_eligible_terminal_pas"] = False
        expanded = pd.concat([expanded, missing_audit], ignore_index=True, sort=False)

    valid_totals = expanded.loc[
        expanded["mispriming_pass"].fillna(False).astype(bool)
        & expanded["has_eligible_terminal_pas"].fillna(False).astype(bool)
        & expanded["quantification_unit_id"].notna()
    ].copy()
    if valid_totals.empty:
        gene_total = pd.DataFrame(columns=GENE_TOTAL_COLUMNS)
    else:
        gene_total = (
            valid_totals.groupby(
                ["gene_id", "strand", "quantification_unit_id"],
                as_index=False,
                observed=True,
            )
            .agg(
                retained_intronic_pas_count=("intronic_pas_id", "nunique"),
                I_total=("I_i", "sum"),
                U=("U", "first"),
            )
        )
        gene_total = gene_total.merge(
            gene_labels,
            on=["gene_id", "strand"],
            how="left",
        )
        gene_total["retained_intronic_pas_count"] = gene_total[
            "retained_intronic_pas_count"
        ].astype(int)
        gene_total["I_total"] = gene_total["I_total"].astype(int)
        gene_total["U"] = gene_total["U"].astype(int)
        gene_total["total_iPUI"] = gene_total.apply(
            lambda row: math.log2(
                (int(row["I_total"]) + 1) / (int(row["U"]) + 1)
            ),
            axis=1,
        )
        # A finite iPUI with I=U=0 rests only on audit observations.
        gene_total["ipui_support_status"] = (
            (gene_total["I_total"] + gene_total["U"]).gt(0)
            .map({True: IPUI_TRUSTED_SUPPORT, False: IPUI_AUDIT_ONLY})
        )
        gene_total = gene_total[GENE_TOTAL_COLUMNS].sort_values(
            ["gene_id", "gene_name", "strand", "quantification_unit_id"]
        ).reset_index(drop=True)

    per_site = expanded.rename(columns={"cell_barcode": "quantification_unit_id"})
    trusted = per_site["iPUI_i"].notna() & (
        pd.to_numeric(per_site["I_i"], errors="coerce").fillna(0)
        + pd.to_numeric(per_site["U"], errors="coerce").fillna(0)
    ).gt(0)
    per_site["ipui_support_status"] = trusted.map({True: IPUI_TRUSTED_SUPPORT, False: IPUI_AUDIT_ONLY})
    per_site = per_site[PER_SITE_COLUMNS].sort_values(
        [
            "gene_id",
            "gene_name",
            "strand",
            "intronic_pas_id",
            "quantification_unit_id",
        ],
        na_position="last",
    ).reset_index(drop=True)
    return {"per_site": per_site, "gene_total": gene_total}


def compute_terminal_aware_tables(
    counts_df: pd.DataFrame,
    peak_meta_df: pd.DataFrame,
    *,
    general_threshold: float,
    intronic_threshold: float,
    psi_min_gene_umis: int,
) -> dict[str, Any]:
    prepared = prepare_terminal_peak_metadata(
        peak_meta_df,
        general_threshold=general_threshold,
        intronic_threshold=intronic_threshold,
    )
    psi_tables = compute_terminal_psi_tables(
        counts_df,
        prepared["terminal_for_psi"],
        psi_min_gene_umis=psi_min_gene_umis,
    )
    ipui_tables = compute_intronic_ipui_tables(
        counts_df,
        terminal_all=prepared["terminal_all"],
        intronic=prepared["intronic"],
    )
    distal_usage = compute_terminal_distal_usage(
        counts_df,
        audit=prepared["audit"],
        terminal_all=prepared["terminal_all"],
        psi_min_gene_umis=psi_min_gene_umis,
    )
    return {**prepared, **psi_tables, **ipui_tables, "distal_usage": distal_usage}
