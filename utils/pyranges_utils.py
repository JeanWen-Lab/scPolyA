import pandas as pd
import pyranges as pr
from pathlib import Path

from logging_config import get_logger, LoggersWrapper, LOG_MAIN, LOG_UTILS

suffix = lambda x: f" (pyranges_utils:{x})"
logger_main = get_logger(LOG_MAIN)
logger_utils = get_logger(LOG_UTILS)
loggers = LoggersWrapper(main=logger_main, sub=logger_utils)


def read_bedgraph_as_pyranges(file_path):
    """
    Reads a BEDGRAPH file into a PyRanges object.

    Parameters:
    - file_path (str): Path to the BEDGRAPH file.

    Returns:
    - PyRanges: PyRanges object containing the BEDGRAPH data.
    """
    try:
        # Load the BEDGRAPH file into a DataFrame
        df = pd.read_csv(
            file_path,
            sep="\t",
            header=None,
            names=["Chromosome", "Start", "End", "Coverage"],
            dtype={"Chromosome": str, "Start": int, "End": int, "Coverage": float},
        )
        logger_utils.info(f"Loaded BEDGRAPH file '{file_path}' with {len(df)} rows.")
        return pr.PyRanges(df)
    except Exception as e:
        loggers.raise_error(f"Failed to read BEDGRAPH file '{file_path}': {e}")


def _read_bedgraph_as_df(file_path: str | Path) -> pd.DataFrame:
    return pd.read_csv(
        file_path,
        sep="\t",
        header=None,
        names=["Chromosome", "Start", "End", "Coverage"],
        dtype={"Chromosome": str, "Start": int, "End": int, "Coverage": float},
    )


def _read_gene_bed_as_df(gene_bed_file: str | Path) -> pd.DataFrame:
    return pd.read_csv(
        gene_bed_file,
        sep="\t",
        header=None,
        usecols=range(6),
        names=["Chromosome", "Start", "End", "Gene", "Score", "Strand"],
        dtype={"Chromosome": str, "Start": int, "End": int, "Gene": str, "Score": str, "Strand": str},
    )


def _weighted_median_from_pairs(cov_weight_pairs: list[tuple[float, int]], total_len: int) -> float:
    if total_len <= 0:
        return 0.0
    pairs = sorted(cov_weight_pairs, key=lambda x: x[0])
    halfway = total_len / 2.0
    acc = 0
    for cov, weight in pairs:
        acc += weight
        if acc >= halfway:
            return float(cov)
    return float(pairs[-1][0]) if pairs else 0.0


def compute_gene_dynamic_thresholds(
        coverage_file: str | Path,
        gene_bed_file: str | Path,
        output_tsv: str | Path,
        strand: str,
        stat: str = "median",
        alpha: float = 1.0,
        floor: float = 0.0,
        normalized: float = 0.3,
) -> Path:
    """
    Compute per-gene dynamic thresholds from a bedGraph coverage file and write a TSV:
    gene_name, threshold, statistic, strand.
    The statistic is length-weighted across gene span and includes implicit zero-coverage bases.
    """
    strand_symbol = "+" if strand == "plus" else "-"
    coverage_df = _read_bedgraph_as_df(coverage_file).sort_values(["Chromosome", "Start", "End"]).reset_index(drop=True)
    genes_df = _read_gene_bed_as_df(gene_bed_file)
    genes_df = genes_df[genes_df["Strand"] == strand_symbol].copy()
    genes_df = genes_df.sort_values(["Chromosome", "Start", "End"]).reset_index(drop=True)

    out_rows: list[dict] = []
    if genes_df.empty:
        pd.DataFrame(columns=["gene_name", "threshold", "stat_value", "strand"]).to_csv(
            output_tsv, sep="\t", index=False
        )
        return Path(output_tsv)

    for chrom, genes_chr in genes_df.groupby("Chromosome", sort=False):
        cov_chr = coverage_df[coverage_df["Chromosome"] == chrom]
        cov_records = list(cov_chr[["Start", "End", "Coverage"]].itertuples(index=False, name=None))
        i = 0
        n_cov = len(cov_records)

        for g in genes_chr.itertuples(index=False):
            g_start = int(g.Start)
            g_end = int(g.End)
            gene_len = g_end - g_start
            if gene_len <= 0:
                continue

            while i < n_cov and cov_records[i][1] <= g_start:
                i += 1

            j = i
            covered_bp = 0
            weighted_sum = 0.0
            weights: dict[float, int] = {}

            while j < n_cov and cov_records[j][0] < g_end:
                c_start, c_end, c_cov = cov_records[j]
                ov_start = max(g_start, int(c_start))
                ov_end = min(g_end, int(c_end))
                ov_len = ov_end - ov_start
                if ov_len > 0:
                    c_cov_f = float(c_cov)
                    covered_bp += ov_len
                    weighted_sum += c_cov_f * ov_len
                    weights[c_cov_f] = weights.get(c_cov_f, 0) + ov_len
                j += 1

            zero_bp = max(0, gene_len - covered_bp)
            if zero_bp > 0:
                weights[0.0] = weights.get(0.0, 0) + zero_bp

            if stat == "mean":
                stat_value = weighted_sum / float(gene_len)
            elif stat == "median":
                stat_value = _weighted_median_from_pairs(list(weights.items()), gene_len)
            elif stat == "median_nonzero":
                nz_pairs = [(cov, w) for cov, w in weights.items() if cov > 0]
                nz_total = sum(w for _, w in nz_pairs)
                stat_value = _weighted_median_from_pairs(nz_pairs, nz_total) if nz_total > 0 else 0.0
            elif stat == "max_norm_fixed":
                stat_value = max(weights.keys(), default=0.0)
            else:
                raise ValueError(f"Unsupported dynamic threshold stat: {stat}")

            if stat == "max_norm_fixed":
                threshold = float(normalized) * float(stat_value)
            else:
                threshold = max(float(floor), float(alpha) * float(stat_value))
            out_rows.append({
                "gene_name": str(g.Gene),
                "threshold": threshold,
                "stat_value": float(stat_value),
                "strand": strand_symbol,
            })

    pd.DataFrame(out_rows).to_csv(output_tsv, sep="\t", index=False)
    logger_utils.info(f"Per-gene dynamic thresholds saved to '{output_tsv}' ({len(out_rows)} genes).")
    return Path(output_tsv)


def call_and_merge_peaks_pyranges(coverage_file,
                                  merged_peaks_bed,
                                  depth_threshold,
                                  strand,
                                  slack,
                                  threshold_mode: str = "fixed",
                                  gene_bed_file: str | Path | None = None,
                                  dynamic_threshold_tsv: str | Path | None = None):
    """
    Calls peaks from coverage data using PyRanges, clusters them, and sums up coverage for overlapping
    or adjacent regions within a specified slack distance.

    Parameters:
    - coverage_file (str): Path to the coverage bedgraph file.
    - merged_peaks_bed (str): Path to save the merged peaks BED file.
    - depth_threshold (float or int): Minimum coverage to consider a position as part of a peak.
    - strand (str): Strand information ('+' or '-').
    - slack (int): Maximum distance between intervals to consider them part of the same cluster.

    Returns:
    - None
    """
    logger_utils.info(
        f"Calling and merging peaks for strand {strand} using mode={threshold_mode}, "
        f"depth threshold {depth_threshold}, slack {slack}, coverage='{coverage_file}'.")

    if threshold_mode == "fixed":
        coverage = read_bedgraph_as_pyranges(coverage_file)
        filtered = coverage[coverage.Coverage >= depth_threshold]
    elif threshold_mode == "dynamic":
        if gene_bed_file is None or dynamic_threshold_tsv is None:
            raise ValueError("Dynamic threshold mode requires gene_bed_file and dynamic_threshold_tsv.")

        strand_symbol = "+" if strand == "plus" else "-"
        coverage_df = _read_bedgraph_as_df(coverage_file)
        genes_df = _read_gene_bed_as_df(gene_bed_file)
        genes_df = genes_df[genes_df["Strand"] == strand_symbol][["Chromosome", "Start", "End", "Gene"]].copy()
        th_df = pd.read_csv(
            dynamic_threshold_tsv,
            sep="\t",
            dtype={"gene_name": str, "threshold": float, "stat_value": float, "strand": str},
        )
        th_df = th_df[["gene_name", "threshold"]].rename(columns={"gene_name": "Gene", "threshold": "Threshold"})
        genes_df = genes_df.merge(th_df, on="Gene", how="inner")

        if genes_df.empty:
            logger_utils.warning("No genes with dynamic thresholds after merge. No peaks will be called.")
            return

        cov_pr = pr.PyRanges(coverage_df)
        gene_pr = pr.PyRanges(genes_df)
        joined = cov_pr.join(gene_pr).df
        if joined.empty:
            logger_utils.warning(f"No coverage overlaps genes for dynamic mode in '{coverage_file}'.")
            return

        joined["FragStart"] = joined[["Start", "Start_b"]].max(axis=1)
        joined["FragEnd"] = joined[["End", "End_b"]].min(axis=1)
        joined = joined[(joined["FragEnd"] > joined["FragStart"]) & (joined["Coverage"] >= joined["Threshold"])]
        if joined.empty:
            logger_utils.warning(f"No dynamic-threshold peaks identified in '{coverage_file}'.")
            return

        filtered_df = joined[["Chromosome", "FragStart", "FragEnd", "Coverage"]].drop_duplicates()
        filtered_df = filtered_df.rename(columns={"FragStart": "Start", "FragEnd": "End"})
        filtered = pr.PyRanges(filtered_df)
    else:
        raise ValueError(f"Unsupported threshold mode: {threshold_mode}")

    if filtered.df.empty:
        logger_utils.warning(f"No peaks identified in '{coverage_file}'.")
        return

    logger_utils.debug(f"Filtered PyRanges DataFrame preview:\n{filtered.df.head()}")
    logger_utils.debug(f"Filtered PyRanges DataFrame columns: {filtered.df.columns.tolist()}")

    # Use cluster to group overlapping/adjacent regions while preserving the Coverage column
    try:
        # Cluster intervals using the slack parameter
        clustered = filtered.cluster(slack=slack)  # Include strand in clustering
        clustered_df = clustered.df

        # Group by Cluster to merge intervals and sum Coverage
        merged_df = clustered_df.groupby("Cluster").agg({
            "Chromosome": "first",  # Chromosome is constant within a cluster
            "Start": "min",  # Take the minimum start position
            "End": "max",  # Take the maximum end position
            "Coverage": "max"  # Sum coverage for all intervals in the cluster
        }).reset_index(drop=True)

        logger_utils.debug(f"Merged peaks DataFrame preview:\n{merged_df.head()}")
    except Exception as e:
        loggers.raise_error(f"Failed to cluster and merge peaks: {e}")

    # Add peak names
    merged_df["name"] = [f"merged_peak_{i + 1}_{strand}" for i in range(len(merged_df))]
    merged_df["strand"] = strand

    # Select and reorder columns for BED format
    merged_df = merged_df[["Chromosome", "Start", "End", "name", "Coverage", "strand"]]

    # Save the merged peaks to a BED file
    try:
        merged_df.to_csv(merged_peaks_bed, sep="\t", header=False, index=False)
        logger_utils.info(f"Merged peaks BED file saved to '{merged_peaks_bed}'.")
    except Exception as e:
        loggers.raise_error(f"Failed to write merged peaks BED file '{merged_peaks_bed}': {e}")
