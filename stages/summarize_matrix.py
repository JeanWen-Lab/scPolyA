from __future__ import annotations

import csv
import gzip
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, List

import pandas as pd

from logging_config import (
    stage_scope, get_logger, LOG_MAIN, LoggersWrapper,
    LOG_STAGE_summarize_matrix, task_scope
)
from task_manage.define import (
    CACHE_unit_inputs, CACHE_dedup_method, CACHE_polyA_status
)
from task_manage import ScItem
from task_manage.effective_config import annotated_peak_signature, build_annotated_peak_config
from task_manage.task_runtime import run_flex_batch
from utils.feature_labels import feature_overview_table, format_feature_overview, write_feature_overview
from utils.terminal_quant_utils import (
    INTRON_FEATURE,
    TERMINAL_DISTAL_USAGE_SUFFIX,
    TERMINAL_FEATURE_OVERVIEW_SUFFIX,
    TERMINAL_IPUI_GENE_TOTAL_SUFFIX,
    TERMINAL_IPUI_PER_SITE_SUFFIX,
    TERMINAL_FEATURES,
    add_counting_status,
    compute_terminal_aware_tables,
    intronic_misprimed_ratio_threshold,
    prepare_terminal_peak_metadata,
    quantification_artifact_suffix,
    quantification_mode,
    quantification_summary_suffix,
)

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

STAGE = LOG_STAGE_summarize_matrix
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)

DENSE_CELL_GENE_MAX_CELLS_ENV = "SCPOLYA_MAX_DENSE_CELL_GENE_MATRIX_CELLS"
DEFAULT_DENSE_CELL_GENE_MAX_CELLS = 50_000_000
SUMMARY_CHUNK_ROWS_ENV = "SCPOLYA_SUMMARY_CHUNK_ROWS"
DEFAULT_SUMMARY_CHUNK_ROWS = 500_000
SUMMARY_GZIP_COMPRESSLEVEL_ENV = "SCPOLYA_SUMMARY_GZIP_COMPRESSLEVEL"
DEFAULT_SUMMARY_GZIP_COMPRESSLEVEL = 1


def summarize_matrix(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    with stage_scope(STAGE):
        mode = quantification_mode(args)
        final_item = build_annotated_peak_config(
            args,
            dedup_method=cache.get(CACHE_dedup_method),
            polyA_status=cache.get(CACHE_polyA_status),
        )

        config_signature = layout.MatrixSignatureFromDict(
            final_item,
            psi_min_gene_umis=args.psi_min_gene_umis,
        )
        summary_root = layout.MatrixSummaryConfigSub(
            final_item,
            psi_min_gene_umis=args.psi_min_gene_umis,
        )

        peak_bed = layout.ResolveAnnotatedFinalOutputFile(
            suffix=quantification_artifact_suffix("___peaks-annotated.bed", mode),
            d=final_item,
        )
        if not peak_bed.exists():
            raise FileNotFoundError(f"Annotated peaks BED not found: {peak_bed}")

        peak_metadata_suffix = quantification_summary_suffix(
            "peak_to_gene.tsv.gz",
            mode,
        )
        peak_to_gene_path = summary_root / (
            f"{annotated_peak_signature(layout, final_item)}.{peak_metadata_suffix}"
        )
        # This derived export has no independent freshness signature. Rebuild it
        # from the current annotation, including after an upstream task rerun.
        peak_df = _load_peak_metadata(peak_bed)
        if mode == "terminal-aware":
            peak_df = prepare_terminal_peak_metadata(
                peak_df,
                general_threshold=args.misprimed_ratio_threshold,
                intronic_threshold=intronic_misprimed_ratio_threshold(args),
            )["audit"]
            logger_stage.info(format_feature_overview(
                feature_overview_table(
                    peak_df.drop_duplicates(subset=["peak_name"]),
                    pass_column="mispriming_pass",
                )
            ))
            # The per-sample overview file adds UMIs; peak_df keeps every label.
        # Flag PASs that never entered the counting GTF: their absent counts
        # mean "not quantified", not a measured zero.
        peak_df = add_counting_status(
            peak_df,
            non_intronic_threshold=(
                args.misprimed_ratio_threshold if mode == "terminal-aware" else None
            ),
        )
        metadata_tmp = _atomic_tmp_path(peak_to_gene_path)
        pending_invalidation = summary_root / ".peak-metadata.pending.json"
        pending_tmp = _atomic_tmp_path(pending_invalidation)
        try:
            peak_df.to_csv(metadata_tmp, sep="\t", index=False, compression="gzip")
            metadata_changed = not _same_gzip_content(metadata_tmp, peak_to_gene_path)
            if metadata_changed:
                # Keep the invalidation durable across a failure after metadata
                # publication or after only some sample workers have completed.
                pending_tmp.write_text(json.dumps({
                    "schema": "scpolya.summary_metadata_invalidation.v1",
                    "metadata_file": peak_to_gene_path.name,
                }) + "\n")
                pending_tmp.replace(pending_invalidation)
                metadata_tmp.replace(peak_to_gene_path)
            else:
                metadata_tmp.unlink()
        except Exception:
            metadata_tmp.unlink(missing_ok=True)
            pending_tmp.unlink(missing_ok=True)
            raise
        logger_stage.info(f"Validated current peak-to-gene metadata at {peak_to_gene_path}")

        peak_meta_df = peak_df.drop_duplicates(subset=["peak_name"]).copy()

        csv_path = cache.get(CACHE_unit_inputs)
        sample_meta: dict[str, dict] = {}
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                sid = r.get("sample_id")
                if not sid or not sid.strip():
                    raise ValueError("Invalid unit inputs row: missing or empty sample_id.")
                if sid not in sample_meta:
                    sample_meta[sid] = {"dedup_status": r["dedup_status"]}

        if not sample_meta:
            logger_stage.warning("No samples found in unit inputs CSV.")
            return

        items: List[ScItem] = [
            ScItem(
                sid=sid,
                dedup_status=meta["dedup_status"],
                add_dedup_method=True,
                args=args,
                extra_key=config_signature
            )
            for sid, meta in sample_meta.items()
        ]

        def summarize_worker(item: ScItem):
            peak_counts = layout.ResolveCountMatrixFile(
                file_name=item.format_name(
                    suffix=quantification_artifact_suffix("counts.tsv.gz", mode),
                    is_annotated_bam=True,
                ),
                d=final_item,
            )
            if not peak_counts.exists():
                raise FileNotFoundError(f"Peak-cell matrix not found: {peak_counts}")

            if mode == "terminal-aware":
                gene_cell_out = summary_root / item.format_name(
                    suffix=quantification_summary_suffix("gene_cell.tsv.gz", mode),
                    is_annotated_bam=True,
                )
                cell_gene_matrix_out = summary_root / item.format_name(
                    suffix=quantification_summary_suffix("cell_gene.matrix.tsv.gz", mode),
                    is_annotated_bam=True,
                )
                cell_gene_matrix_manifest = summary_root / item.format_name(
                    suffix=quantification_summary_suffix(
                        "cell_gene.matrix.skipped.json", mode
                    ),
                    is_annotated_bam=True,
                )
                weighted_utr_out = summary_root / item.format_name(
                    suffix=quantification_summary_suffix(
                        "weighted_three_prime_utr_length.tsv.gz", mode
                    ),
                    is_annotated_bam=True,
                )
                psi_out = summary_root / item.format_name(
                    suffix=quantification_summary_suffix("psi.tsv.gz", mode),
                    is_annotated_bam=True,
                )
                ipui_per_site_out = summary_root / item.format_name(
                    suffix=TERMINAL_IPUI_PER_SITE_SUFFIX,
                    is_annotated_bam=True,
                )
                ipui_gene_total_out = summary_root / item.format_name(
                    suffix=TERMINAL_IPUI_GENE_TOTAL_SUFFIX,
                    is_annotated_bam=True,
                )
                distal_usage_out = summary_root / item.format_name(
                    suffix=TERMINAL_DISTAL_USAGE_SUFFIX,
                    is_annotated_bam=True,
                )
                feature_overview_out = summary_root / item.format_name(
                    suffix=TERMINAL_FEATURE_OVERVIEW_SUFFIX,
                    is_annotated_bam=True,
                )

                if _is_three_column_count_matrix(peak_counts):
                    counts_df, stream_stats = _load_terminal_relevant_counts_streaming(
                        peak_counts,
                        peak_meta_df=peak_meta_df,
                        scratch_parent=summary_root,
                        sample_id=item.sid,
                    )
                    umis_by_peak = stream_stats["umis_by_peak"]
                else:
                    counts_df = _load_count_matrix(peak_counts)
                    stream_stats = None
                    umis_by_peak = counts_df.groupby("peak_name", observed=True)["umi_count"].sum()

                result = _write_terminal_aware_summary(
                    counts_df=counts_df,
                    peak_meta_df=peak_meta_df,
                    gene_cell_out=gene_cell_out,
                    cell_gene_matrix_out=cell_gene_matrix_out,
                    cell_gene_matrix_manifest=cell_gene_matrix_manifest,
                    weighted_utr_out=weighted_utr_out,
                    psi_out=psi_out,
                    ipui_per_site_out=ipui_per_site_out,
                    ipui_gene_total_out=ipui_gene_total_out,
                    distal_usage_out=distal_usage_out,
                    general_threshold=args.misprimed_ratio_threshold,
                    intronic_threshold=intronic_misprimed_ratio_threshold(args),
                    psi_min_gene_umis=args.psi_min_gene_umis,
                    sample_id=item.sid,
                )
                stats = result["stats"]
                # Display only: written after, and never read by, the metrics.
                overview = feature_overview_table(peak_meta_df, umis_by_peak=umis_by_peak)
                write_feature_overview(overview, feature_overview_out, sample_id=item.sid)
                stream_detail = (
                    ""
                    if stream_stats is None
                    else (
                        f", input_chunks={stream_stats['chunks_processed']}, "
                        f"relevant_count_pairs={stream_stats['relevant_count_pairs']}"
                    )
                )
                logger_stage.info(
                    f"{item.sid} terminal-aware quantification used "
                    f"{stats['terminal_pas_for_psi_count']} terminal PASs for PSI/wLen; "
                    f"eligible_terminal_pas_before_minimum={stats['retained_terminal_pas_count']}; "
                    f"genes_with_at_least_two={stats['genes_with_at_least_two_terminal_pas']}; "
                    f"genes_excluded_with_one={stats['genes_excluded_with_one_terminal_pas']}; "
                    f"terminal_pas_missing_gene_id={stats['terminal_pas_missing_gene_id']}; "
                    f"intronic_before={stats['intronic_pas_before_filter']}; "
                    f"intronic_after={stats['intronic_pas_after_filter']}; "
                    f"intronic_failed={stats['intronic_pas_failed_filter']}; "
                    f"dense_matrix_status={result['dense_result']['status']}"
                    f"{stream_detail}. PSI={psi_out}; weighted_length={weighted_utr_out}; "
                    f"iPUI_per_site={ipui_per_site_out}; iPUI_gene_total={ipui_gene_total_out}; "
                    f"distal_usage={distal_usage_out}; feature_overview={feature_overview_out}"
                )
                return

            gene_cell_out = summary_root / f"{item.format_name(suffix='gene_cell.tsv.gz', is_annotated_bam=True)}"
            cell_gene_matrix_out = summary_root / f"{item.format_name(suffix='cell_gene.matrix.tsv.gz', is_annotated_bam=True)}"
            cell_gene_matrix_manifest = (
                summary_root / f"{item.format_name(suffix='cell_gene.matrix.skipped.json', is_annotated_bam=True)}"
            )
            weighted_utr_out = summary_root / f"{item.format_name(suffix='weighted_three_prime_utr_length.tsv.gz', is_annotated_bam=True)}"
            psi_out = summary_root / f"{item.format_name(suffix='psi.tsv.gz', is_annotated_bam=True)}"

            if _is_three_column_count_matrix(peak_counts):
                result = _summarize_long_count_matrix_streaming(
                    peak_counts=peak_counts,
                    peak_meta_df=peak_meta_df,
                    gene_cell_out=gene_cell_out,
                    cell_gene_matrix_out=cell_gene_matrix_out,
                    cell_gene_matrix_manifest=cell_gene_matrix_manifest,
                    weighted_utr_out=weighted_utr_out,
                    psi_out=psi_out,
                    psi_min_gene_umis=args.psi_min_gene_umis,
                    sample_id=item.sid,
                    scratch_parent=summary_root,
                )
                logger_stage.info(
                    f"{item.sid} streaming summary wrote gene-cell table to {gene_cell_out}, "
                    f"cell-gene matrix status {result['dense_result']['status']} "
                    f"(estimated_dense_cells={result['dense_result']['estimated_dense_cells']}) to "
                    f"{result['dense_result']['path']}, "
                    f"weighted 3'UTR table to {weighted_utr_out}, and PSI table to {psi_out}. "
                    f"Chunks={result['chunks_processed']}, missing_peak_rows={result['missing_peak_rows']}"
                )
                return

            counts_df = _load_count_matrix(peak_counts)

            merged_df = counts_df.merge(peak_meta_df, on="peak_name", how="left")
            missing_gene = merged_df["gene_name"].isna()
            if missing_gene.any():
                missing_count = int(missing_gene.sum())
                logger_stage.warning(f"{item.sid} has {missing_count} peak-count rows without gene mapping; dropping them.")
                merged_df = merged_df.loc[~missing_gene].copy()

            gene_cell_df = (
                merged_df.groupby(["gene_name", "cell_barcode"], as_index=False, observed=True)["umi_count"]
                .sum()
                .rename(columns={"umi_count": "gene_umi_count"})
            )

            psi_df = merged_df.merge(gene_cell_df, on=["gene_name", "cell_barcode"], how="left")
            psi_df["psi"] = psi_df["umi_count"] / psi_df["gene_umi_count"]
            psi_df.loc[psi_df["gene_umi_count"] < args.psi_min_gene_umis, "psi"] = pd.NA
            psi_df = psi_df.rename(columns={"umi_count": "peak_umi_count"})

            weighted_utr_df = (
                psi_df.assign(
                    weighted_three_prime_utr_length=lambda df_: (
                        df_["peak_umi_count"] * df_["three_prime_utr_length"] / df_["gene_umi_count"]
                    )
                )
                .groupby(["gene_name", "cell_barcode", "gene_umi_count"], as_index=False, observed=True)[
                    "weighted_three_prime_utr_length"
                ]
                .sum()
            )
            weighted_utr_df.loc[
                weighted_utr_df["gene_umi_count"] < args.psi_min_gene_umis,
                "weighted_three_prime_utr_length"
            ] = pd.NA

            gene_cell_df.to_csv(gene_cell_out, sep="\t", index=False, compression="gzip")
            dense_result = _write_cell_gene_matrix_or_skip(
                gene_cell_df,
                output_path=cell_gene_matrix_out,
                manifest_path=cell_gene_matrix_manifest,
                long_gene_cell_path=gene_cell_out,
                sample_id=item.sid,
            )
            weighted_utr_df.to_csv(weighted_utr_out, sep="\t", index=False, compression="gzip")
            psi_df.to_csv(
                psi_out,
                sep="\t",
                index=False,
                compression="gzip",
                columns=[
                    "peak_name", "gene_name", "cell_barcode", "peak_umi_count", "gene_umi_count",
                    "three_prime_utr_length", "psi"
                ]
            )
            logger_stage.info(
                f"{item.sid} wrote gene-cell table to {gene_cell_out}, "
                f"cell-gene matrix status {dense_result['status']} "
                f"(estimated_dense_cells={dense_result['estimated_dense_cells']}) to "
                f"{dense_result['path']}, "
                f"weighted 3'UTR table to {weighted_utr_out}, and PSI table to {psi_out}"
            )

        task = "summarize_matrix"
        # A pre-upgrade DONE coordinate does not certify the newer required
        # outputs (distal usage, feature overview). Force the summary batch
        # even under legacy DONE-only reuse.
        missing_required_output = mode == "terminal-aware" and any(
            not (summary_root / item.format_name(
                suffix=suffix, is_annotated_bam=True,
            )).is_file()
            for item in items
            for suffix in (TERMINAL_DISTAL_USAGE_SUFFIX, TERMINAL_FEATURE_OVERVIEW_SUFFIX)
        )
        with task_scope(task):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base=task,
                items=items,
                identity_keys=["sid", "dedup_method"],
                param_keys=["extra_key"],
                fn=summarize_worker,
                max_workers=args.n_workers,
                force=pending_invalidation.exists() or missing_required_output,
            )
        # A pending marker is not completion evidence. Only the successful
        # return of the full scope's worker batch makes it safe to clear.
        pending_invalidation.unlink(missing_ok=True)


def _same_gzip_content(left: Path, right: Path) -> bool:
    if not right.exists():
        return False
    try:
        with gzip.open(left, "rb") as first, gzip.open(right, "rb") as second:
            while True:
                chunk = first.read(1024 * 1024)
                if chunk != second.read(1024 * 1024):
                    return False
                if not chunk:
                    return True
    except (gzip.BadGzipFile, EOFError):
        return False


def _load_peak_metadata(peak_bed: Path) -> pd.DataFrame:
    base_columns = [
        "chrom", "start", "end", "peak_name", "max_cov", "strand",
        "total_polyA_reads", "misprimed_ratio", "total_reads", "gene_name",
        "distance_to_gene", "canonical_transcript", "stop_codon_position",
        "distance_to_stop_codon", "feature"
    ]
    # Contig and peak-name text stay verbatim (e.g. "022", "NA"); every other
    # column keeps pandas' default missing values, so a NA ratio or gene_id is
    # still missing. Identifiers stay textual while numeric columns keep their
    # types (gene IDs such as "001" and "1" are distinct).
    with open(peak_bed, "r") as handle:
        first = next((line for line in handle if line.strip()), "")
    n_columns = len(first.rstrip("\n").split("\t")) if first else 0
    default_na = sorted(pd._libs.parsers.STR_NA_VALUES)
    df = pd.read_csv(
        peak_bed,
        sep="\t",
        header=None,
        dtype={i: str for i in (0, 3, 9, 11, 15) if i < n_columns},
        keep_default_na=False,
        na_values={i: default_na for i in range(n_columns) if i not in (0, 3)},
    )
    if df.shape[1] not in {len(base_columns), len(base_columns) + 1}:
        raise ValueError(
            f"Annotated peak BED must have 15 legacy columns or 16 columns "
            f"with gene_id; found {df.shape[1]} in {peak_bed}"
        )
    df.columns = base_columns + (["gene_id"] if df.shape[1] == 16 else [])
    df["peak_length"] = df["end"] - df["start"]
    df["three_prime_utr_length"] = pd.to_numeric(df["distance_to_stop_codon"], errors="coerce").clip(lower=0)
    df["peak_order_in_gene"] = (
        df.sort_values(["gene_name", "distance_to_stop_codon", "chrom", "start", "end", "peak_name"])
        .groupby("gene_name", observed=True)
        .cumcount() + 1
    )
    df["n_peaks_in_gene"] = df.groupby("gene_name", observed=True)["peak_name"].transform("nunique")
    df["is_distal_peak"] = (
        df.groupby("gene_name", observed=True)["distance_to_stop_codon"]
        .transform("max")
        .eq(df["distance_to_stop_codon"])
    )
    output_columns = [
        "peak_name", "gene_name", "chrom", "start", "end", "peak_length", "strand",
        "feature", "canonical_transcript", "stop_codon_position", "distance_to_gene",
        "distance_to_stop_codon", "three_prime_utr_length", "peak_order_in_gene", "n_peaks_in_gene",
        "is_distal_peak", "max_cov", "total_polyA_reads", "total_reads", "misprimed_ratio"
    ]
    if "gene_id" in df.columns:
        output_columns.insert(2, "gene_id")
    return df[output_columns].sort_values(
        ["gene_name", "peak_order_in_gene", "peak_name"]
    ).reset_index(drop=True)


def _load_terminal_relevant_counts_streaming(
    peak_counts: Path,
    *,
    peak_meta_df: pd.DataFrame,
    scratch_parent: Path,
    sample_id: str,
) -> tuple[pd.DataFrame, dict[str, int]]:
    relevant_names = set(
        peak_meta_df.loc[
            peak_meta_df["feature"].isin(set(TERMINAL_FEATURES) | {INTRON_FEATURE}),
            "peak_name",
        ].astype(str)
    )
    chunk_rows = _summary_chunk_rows()
    chunks_processed = 0
    rows_read = 0
    relevant_rows = 0
    # UMIs per peak over every count row (all features), for the overview.
    umis_by_peak = pd.Series(dtype="int64")
    scratch_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=f"{sample_id}.terminal-counts.", dir=scratch_parent
    ) as tmpdir:
        db_path = Path(tmpdir) / "terminal-counts.sqlite"
        with sqlite3.connect(db_path) as con:
            con.execute("PRAGMA journal_mode=OFF")
            con.execute("PRAGMA synchronous=OFF")
            con.execute("PRAGMA temp_store=MEMORY")
            con.execute(
                """
                CREATE TABLE relevant_count (
                    peak_name TEXT NOT NULL,
                    cell_barcode TEXT NOT NULL,
                    umi_count INTEGER NOT NULL,
                    PRIMARY KEY (peak_name, cell_barcode)
                ) WITHOUT ROWID
                """
            )
            for chunk in _iter_long_count_chunks(peak_counts, chunk_rows):
                chunks_processed += 1
                clean = _clean_long_count_chunk(chunk)
                rows_read += len(clean)
                if not clean.empty:
                    umis_by_peak = umis_by_peak.add(
                        clean.groupby("peak_name", observed=True)["umi_count"].sum(), fill_value=0
                    )
                clean = clean.loc[clean["peak_name"].isin(relevant_names)]
                relevant_rows += len(clean)
                if clean.empty:
                    continue
                grouped = clean.groupby(
                    ["peak_name", "cell_barcode"],
                    as_index=False,
                    observed=True,
                )["umi_count"].sum()
                con.executemany(
                    """
                    INSERT INTO relevant_count(peak_name, cell_barcode, umi_count)
                    VALUES (?, ?, ?)
                    ON CONFLICT(peak_name, cell_barcode) DO UPDATE SET
                        umi_count = relevant_count.umi_count + excluded.umi_count
                    """,
                    (
                        (str(row.peak_name), str(row.cell_barcode), int(row.umi_count))
                        for row in grouped.itertuples(index=False)
                    ),
                )
                if chunks_processed % 10 == 0:
                    con.commit()
            con.commit()
            counts_df = pd.read_sql_query(
                """
                SELECT peak_name, cell_barcode, umi_count
                FROM relevant_count
                ORDER BY peak_name, cell_barcode
                """,
                con,
            )

    return counts_df, {
        "chunks_processed": chunks_processed,
        "rows_read": rows_read,
        "relevant_rows": relevant_rows,
        "relevant_count_pairs": len(counts_df),
        "umis_by_peak": umis_by_peak.astype("int64"),
    }


def _write_terminal_aware_summary(
    *,
    counts_df: pd.DataFrame,
    peak_meta_df: pd.DataFrame,
    gene_cell_out: Path,
    cell_gene_matrix_out: Path,
    cell_gene_matrix_manifest: Path,
    weighted_utr_out: Path,
    psi_out: Path,
    ipui_per_site_out: Path,
    ipui_gene_total_out: Path,
    distal_usage_out: Path,
    general_threshold: float,
    intronic_threshold: float,
    psi_min_gene_umis: int,
    sample_id: str,
) -> dict[str, Any]:
    result = compute_terminal_aware_tables(
        counts_df,
        peak_meta_df,
        general_threshold=general_threshold,
        intronic_threshold=intronic_threshold,
        psi_min_gene_umis=psi_min_gene_umis,
    )
    gene_cell_out.parent.mkdir(parents=True, exist_ok=True)
    result["gene_cell"].to_csv(
        gene_cell_out, sep="\t", index=False, compression="gzip"
    )
    dense_result = _write_terminal_cell_gene_matrix_or_skip(
        result["gene_cell"],
        output_path=cell_gene_matrix_out,
        manifest_path=cell_gene_matrix_manifest,
        long_gene_cell_path=gene_cell_out,
        sample_id=sample_id,
    )
    result["weighted_utr"].to_csv(
        weighted_utr_out, sep="\t", index=False, compression="gzip"
    )
    result["psi"].to_csv(psi_out, sep="\t", index=False, compression="gzip")
    result["per_site"].to_csv(
        ipui_per_site_out, sep="\t", index=False, compression="gzip"
    )
    result["gene_total"].to_csv(
        ipui_gene_total_out, sep="\t", index=False, compression="gzip"
    )
    distal_tmp = _atomic_tmp_path(distal_usage_out)
    try:
        result["distal_usage"].to_csv(
            distal_tmp, sep="\t", index=False, compression="gzip", na_rep="NA"
        )
        distal_tmp.replace(distal_usage_out)
    except Exception:
        distal_tmp.unlink(missing_ok=True)
        raise
    result["dense_result"] = dense_result
    return result


def _max_dense_cell_gene_matrix_cells() -> int:
    raw_value = os.environ.get(DENSE_CELL_GENE_MAX_CELLS_ENV)
    if raw_value is None:
        return DEFAULT_DENSE_CELL_GENE_MAX_CELLS
    try:
        value = int(raw_value)
    except ValueError:
        logger_stage.warning(
            f"Ignoring invalid {DENSE_CELL_GENE_MAX_CELLS_ENV}={raw_value!r}; "
            f"using {DEFAULT_DENSE_CELL_GENE_MAX_CELLS}."
        )
        return DEFAULT_DENSE_CELL_GENE_MAX_CELLS
    if value < 0:
        logger_stage.warning(
            f"Ignoring negative {DENSE_CELL_GENE_MAX_CELLS_ENV}={raw_value!r}; "
            f"using {DEFAULT_DENSE_CELL_GENE_MAX_CELLS}."
        )
        return DEFAULT_DENSE_CELL_GENE_MAX_CELLS
    return value


def _dense_cell_gene_shape(gene_cell_df: pd.DataFrame) -> tuple[int, int, int]:
    n_cells = int(gene_cell_df["cell_barcode"].nunique())
    n_genes = int(gene_cell_df["gene_name"].nunique())
    return n_cells, n_genes, n_cells * n_genes


def _write_cell_gene_matrix_skip_manifest(
    *,
    output_path: Path,
    manifest_path: Path,
    long_gene_cell_path: Path,
    sample_id: str,
    n_cells: int,
    n_genes: int,
    estimated_dense_cells: int,
    max_dense_cells: int,
    long_gene_cell_columns: list[str] | None = None,
    dense_column_key: str | None = None,
) -> dict[str, Any]:
    manifest = {
        "status": "skipped_dense",
        "reason": "estimated dense cell-by-gene matrix exceeds configured limit",
        "sample_id": sample_id,
        "dense_matrix_path": str(output_path),
        "long_gene_cell_path": str(long_gene_cell_path),
        "long_gene_cell_columns": long_gene_cell_columns
        or ["gene_name", "cell_barcode", "gene_umi_count"],
        "n_cells": n_cells,
        "n_genes": n_genes,
        "estimated_dense_cells": estimated_dense_cells,
        "max_dense_cells": max_dense_cells,
        "override_env": DENSE_CELL_GENE_MAX_CELLS_ENV,
    }
    if dense_column_key is not None:
        manifest["dense_column_key"] = dense_column_key
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="ascii") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    logger_stage.warning(
        f"{sample_id} skipped dense cell-gene matrix: {n_cells} cells x {n_genes} genes "
        f"= {estimated_dense_cells} cells exceeds limit {max_dense_cells}. "
        f"Use long gene-cell table: {long_gene_cell_path}"
    )
    return {**manifest, "path": str(manifest_path)}


def _write_cell_gene_matrix_or_skip(
    gene_cell_df: pd.DataFrame,
    *,
    output_path: Path,
    manifest_path: Path,
    long_gene_cell_path: Path,
    sample_id: str,
    max_dense_cells: int | None = None,
) -> dict[str, Any]:
    n_cells, n_genes, estimated_dense_cells = _dense_cell_gene_shape(gene_cell_df)
    dense_limit = _max_dense_cell_gene_matrix_cells() if max_dense_cells is None else max_dense_cells

    if estimated_dense_cells <= dense_limit:
        cell_gene_matrix_df = (
            gene_cell_df.pivot(index="cell_barcode", columns="gene_name", values="gene_umi_count")
            .fillna(0)
            .astype(int)
            .reset_index()
        )
        cell_gene_matrix_df.to_csv(output_path, sep="\t", index=False, compression="gzip")
        return {
            "status": "written_dense",
            "path": str(output_path),
            "n_cells": n_cells,
            "n_genes": n_genes,
            "estimated_dense_cells": estimated_dense_cells,
            "max_dense_cells": dense_limit,
        }

    return _write_cell_gene_matrix_skip_manifest(
        output_path=output_path,
        manifest_path=manifest_path,
        long_gene_cell_path=long_gene_cell_path,
        sample_id=sample_id,
        n_cells=n_cells,
        n_genes=n_genes,
        estimated_dense_cells=estimated_dense_cells,
        max_dense_cells=dense_limit,
    )


def _write_terminal_cell_gene_matrix_or_skip(
    gene_cell_df: pd.DataFrame,
    *,
    output_path: Path,
    manifest_path: Path,
    long_gene_cell_path: Path,
    sample_id: str,
    max_dense_cells: int | None = None,
) -> dict[str, Any]:
    n_cells = int(gene_cell_df["cell_barcode"].nunique())
    n_genes = int(gene_cell_df["terminal_gene_unit_id"].nunique())
    estimated_dense_cells = n_cells * n_genes
    dense_limit = _max_dense_cell_gene_matrix_cells() if max_dense_cells is None else max_dense_cells

    if estimated_dense_cells <= dense_limit:
        cell_gene_matrix_df = (
            gene_cell_df.pivot(
                index="cell_barcode",
                columns="terminal_gene_unit_id",
                values="gene_umi_count",
            )
            .fillna(0)
            .astype(int)
            .reset_index()
        )
        cell_gene_matrix_df.to_csv(
            output_path, sep="\t", index=False, compression="gzip"
        )
        return {
            "status": "written_dense",
            "path": str(output_path),
            "n_cells": n_cells,
            "n_genes": n_genes,
            "estimated_dense_cells": estimated_dense_cells,
            "max_dense_cells": dense_limit,
            "dense_column_key": "terminal_gene_unit_id",
        }

    return _write_cell_gene_matrix_skip_manifest(
        output_path=output_path,
        manifest_path=manifest_path,
        long_gene_cell_path=long_gene_cell_path,
        sample_id=sample_id,
        n_cells=n_cells,
        n_genes=n_genes,
        estimated_dense_cells=estimated_dense_cells,
        max_dense_cells=dense_limit,
        long_gene_cell_columns=list(gene_cell_df.columns),
        dense_column_key="terminal_gene_unit_id",
    )


def _summary_chunk_rows() -> int:
    raw_value = os.environ.get(SUMMARY_CHUNK_ROWS_ENV)
    if raw_value is None:
        return DEFAULT_SUMMARY_CHUNK_ROWS
    try:
        value = int(raw_value)
    except ValueError:
        logger_stage.warning(
            f"Ignoring invalid {SUMMARY_CHUNK_ROWS_ENV}={raw_value!r}; "
            f"using {DEFAULT_SUMMARY_CHUNK_ROWS}."
        )
        return DEFAULT_SUMMARY_CHUNK_ROWS
    if value <= 0:
        logger_stage.warning(
            f"Ignoring non-positive {SUMMARY_CHUNK_ROWS_ENV}={raw_value!r}; "
            f"using {DEFAULT_SUMMARY_CHUNK_ROWS}."
        )
        return DEFAULT_SUMMARY_CHUNK_ROWS
    return value


def _summary_gzip_compresslevel() -> int:
    raw_value = os.environ.get(SUMMARY_GZIP_COMPRESSLEVEL_ENV)
    if raw_value is None:
        return DEFAULT_SUMMARY_GZIP_COMPRESSLEVEL
    try:
        value = int(raw_value)
    except ValueError:
        logger_stage.warning(
            f"Ignoring invalid {SUMMARY_GZIP_COMPRESSLEVEL_ENV}={raw_value!r}; "
            f"using {DEFAULT_SUMMARY_GZIP_COMPRESSLEVEL}."
        )
        return DEFAULT_SUMMARY_GZIP_COMPRESSLEVEL
    if value < 0 or value > 9:
        logger_stage.warning(
            f"Ignoring out-of-range {SUMMARY_GZIP_COMPRESSLEVEL_ENV}={raw_value!r}; "
            f"using {DEFAULT_SUMMARY_GZIP_COMPRESSLEVEL}."
        )
        return DEFAULT_SUMMARY_GZIP_COMPRESSLEVEL
    return value


def _is_three_column_count_matrix(path: Path) -> bool:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        first_line = handle.readline()
    if not first_line:
        return False
    return len(first_line.rstrip("\n\r").split("\t")) == 3


def _atomic_tmp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _prepare_peak_lookup(peak_meta_df: pd.DataFrame) -> pd.DataFrame:
    required = {"peak_name", "gene_name", "three_prime_utr_length"}
    missing = sorted(required - set(peak_meta_df.columns))
    if missing:
        raise ValueError(f"Peak metadata missing columns: {', '.join(missing)}")

    peak_lookup = peak_meta_df[["peak_name", "gene_name", "three_prime_utr_length"]].drop_duplicates(
        subset=["peak_name"]
    ).copy()
    peak_lookup = peak_lookup.dropna(subset=["peak_name", "gene_name"])
    peak_lookup["peak_name"] = peak_lookup["peak_name"].astype(str)
    peak_lookup["gene_name"] = peak_lookup["gene_name"].astype(str)
    peak_lookup["three_prime_utr_length"] = (
        pd.to_numeric(peak_lookup["three_prime_utr_length"], errors="coerce").fillna(0)
    )
    return peak_lookup


def _iter_long_count_chunks(path: Path, chunk_rows: int):
    yield from pd.read_csv(
        path,
        sep="\t",
        compression="gzip" if path.suffix == ".gz" else None,
        header=None,
        names=["peak_name", "cell_barcode", "umi_count"],
        chunksize=chunk_rows,
        dtype=str,
    )


def _clean_long_count_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    if chunk.empty:
        return chunk
    clean = chunk[["peak_name", "cell_barcode", "umi_count"]].copy()
    clean["umi_count"] = pd.to_numeric(clean["umi_count"], errors="coerce")
    clean = clean.dropna(subset=["peak_name", "cell_barcode", "umi_count"])
    if clean.empty:
        return clean
    clean["peak_name"] = clean["peak_name"].astype(str)
    clean["cell_barcode"] = clean["cell_barcode"].astype(str)
    clean["umi_count"] = clean["umi_count"].astype(int)
    return clean


def _init_summary_db(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-200000")
    con.execute(
        """
        CREATE TABLE gene_cell (
            gene_name TEXT NOT NULL,
            cell_barcode TEXT NOT NULL,
            gene_umi_count INTEGER NOT NULL,
            PRIMARY KEY (gene_name, cell_barcode)
        ) WITHOUT ROWID
        """
    )
    con.execute(
        """
        CREATE TABLE weighted_utr (
            gene_name TEXT NOT NULL,
            cell_barcode TEXT NOT NULL,
            gene_umi_count INTEGER NOT NULL,
            weighted_three_prime_utr_length REAL NOT NULL,
            PRIMARY KEY (gene_name, cell_barcode)
        ) WITHOUT ROWID
        """
    )
    con.execute(
        """
        CREATE TABLE summary_gene (
            gene_name TEXT NOT NULL PRIMARY KEY
        ) WITHOUT ROWID
        """
    )
    con.execute(
        """
        CREATE TABLE summary_cell (
            cell_barcode TEXT NOT NULL PRIMARY KEY
        ) WITHOUT ROWID
        """
    )
    con.execute(
        """
        CREATE TEMP TABLE lookup_pairs (
            gene_name TEXT NOT NULL,
            cell_barcode TEXT NOT NULL,
            PRIMARY KEY (gene_name, cell_barcode)
        ) WITHOUT ROWID
        """
    )


def _upsert_gene_cell(con: sqlite3.Connection, gene_cell_df: pd.DataFrame) -> None:
    if gene_cell_df.empty:
        return
    con.executemany(
        """
        INSERT INTO gene_cell(gene_name, cell_barcode, gene_umi_count)
        VALUES (?, ?, ?)
        ON CONFLICT(gene_name, cell_barcode) DO UPDATE SET
            gene_umi_count = gene_cell.gene_umi_count + excluded.gene_umi_count
        """,
        (
            (str(row.gene_name), str(row.cell_barcode), int(row.gene_umi_count))
            for row in gene_cell_df.itertuples(index=False)
        ),
    )
    con.executemany(
        "INSERT OR IGNORE INTO summary_gene(gene_name) VALUES (?)",
        ((str(gene_name),) for gene_name in gene_cell_df["gene_name"].drop_duplicates()),
    )
    con.executemany(
        "INSERT OR IGNORE INTO summary_cell(cell_barcode) VALUES (?)",
        ((str(cell_barcode),) for cell_barcode in gene_cell_df["cell_barcode"].drop_duplicates()),
    )


def _upsert_weighted_utr(con: sqlite3.Connection, weighted_df: pd.DataFrame) -> None:
    if weighted_df.empty:
        return
    con.executemany(
        """
        INSERT INTO weighted_utr(
            gene_name, cell_barcode, gene_umi_count, weighted_three_prime_utr_length
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(gene_name, cell_barcode) DO UPDATE SET
            gene_umi_count = excluded.gene_umi_count,
            weighted_three_prime_utr_length = (
                weighted_utr.weighted_three_prime_utr_length
                + excluded.weighted_three_prime_utr_length
            )
        """,
        (
            (
                str(row.gene_name),
                str(row.cell_barcode),
                int(row.gene_umi_count),
                float(row.weighted_three_prime_utr_length),
            )
            for row in weighted_df.itertuples(index=False)
        ),
    )


def _gene_cell_db_shape(con: sqlite3.Connection) -> tuple[int, int, int]:
    try:
        n_cells = int(con.execute("SELECT COUNT(*) FROM summary_cell").fetchone()[0])
        n_genes = int(con.execute("SELECT COUNT(*) FROM summary_gene").fetchone()[0])
    except sqlite3.OperationalError:
        n_cells = int(con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT cell_barcode FROM gene_cell)").fetchone()[0])
        n_genes = int(con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT gene_name FROM gene_cell)").fetchone()[0])
    return n_cells, n_genes, n_cells * n_genes


def _write_gene_cell_table_from_db(con: sqlite3.Connection, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _atomic_tmp_path(output_path)
    with gzip.open(
        tmp_path,
        "wt",
        encoding="utf-8",
        newline="",
        compresslevel=_summary_gzip_compresslevel(),
    ) as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["gene_name", "cell_barcode", "gene_umi_count"])
        for row in con.execute(
            """
            SELECT gene_name, cell_barcode, gene_umi_count
            FROM gene_cell
            ORDER BY gene_name, cell_barcode
            """
        ):
            writer.writerow(row)
    tmp_path.replace(output_path)


def _write_cell_gene_matrix_from_db_or_skip(
    con: sqlite3.Connection,
    *,
    output_path: Path,
    manifest_path: Path,
    long_gene_cell_path: Path,
    sample_id: str,
) -> dict[str, Any]:
    n_cells, n_genes, estimated_dense_cells = _gene_cell_db_shape(con)
    dense_limit = _max_dense_cell_gene_matrix_cells()
    if estimated_dense_cells <= dense_limit:
        gene_cell_df = pd.read_sql_query(
            """
            SELECT gene_name, cell_barcode, gene_umi_count
            FROM gene_cell
            """,
            con,
        )
        return _write_cell_gene_matrix_or_skip(
            gene_cell_df,
            output_path=output_path,
            manifest_path=manifest_path,
            long_gene_cell_path=long_gene_cell_path,
            sample_id=sample_id,
            max_dense_cells=dense_limit,
        )

    return _write_cell_gene_matrix_skip_manifest(
        output_path=output_path,
        manifest_path=manifest_path,
        long_gene_cell_path=long_gene_cell_path,
        sample_id=sample_id,
        n_cells=n_cells,
        n_genes=n_genes,
        estimated_dense_cells=estimated_dense_cells,
        max_dense_cells=dense_limit,
    )


def _attach_gene_totals_from_db(con: sqlite3.Connection, mapped_df: pd.DataFrame) -> pd.DataFrame:
    if mapped_df.empty:
        return mapped_df

    pairs = mapped_df[["gene_name", "cell_barcode"]].drop_duplicates()
    con.execute("DELETE FROM lookup_pairs")
    con.executemany(
        "INSERT OR IGNORE INTO lookup_pairs(gene_name, cell_barcode) VALUES (?, ?)",
        ((str(row.gene_name), str(row.cell_barcode)) for row in pairs.itertuples(index=False)),
    )
    totals_df = pd.read_sql_query(
        """
        SELECT g.gene_name, g.cell_barcode, g.gene_umi_count
        FROM gene_cell AS g
        JOIN lookup_pairs AS p
          ON g.gene_name = p.gene_name
         AND g.cell_barcode = p.cell_barcode
        """,
        con,
    )
    if totals_df.empty:
        return mapped_df.iloc[0:0].copy()
    totals_df["gene_name"] = totals_df["gene_name"].astype(str)
    totals_df["cell_barcode"] = totals_df["cell_barcode"].astype(str)
    return mapped_df.merge(totals_df, on=["gene_name", "cell_barcode"], how="inner")


def _write_weighted_utr_table_from_db(
    con: sqlite3.Connection,
    output_path: Path,
    *,
    psi_min_gene_umis: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _atomic_tmp_path(output_path)
    with gzip.open(
        tmp_path,
        "wt",
        encoding="utf-8",
        newline="",
        compresslevel=_summary_gzip_compresslevel(),
    ) as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["gene_name", "cell_barcode", "gene_umi_count", "weighted_three_prime_utr_length"])
        for gene_name, cell_barcode, gene_umi_count, weighted_length in con.execute(
            """
            SELECT gene_name, cell_barcode, gene_umi_count, weighted_three_prime_utr_length
            FROM weighted_utr
            ORDER BY gene_name, cell_barcode
            """
        ):
            if int(gene_umi_count) < psi_min_gene_umis:
                writer.writerow([gene_name, cell_barcode, gene_umi_count, ""])
            else:
                writer.writerow([gene_name, cell_barcode, gene_umi_count, weighted_length])
    tmp_path.replace(output_path)


def _summarize_long_count_matrix_streaming(
    *,
    peak_counts: Path,
    peak_meta_df: pd.DataFrame,
    gene_cell_out: Path,
    cell_gene_matrix_out: Path,
    cell_gene_matrix_manifest: Path,
    weighted_utr_out: Path,
    psi_out: Path,
    psi_min_gene_umis: int,
    sample_id: str,
    scratch_parent: Path,
) -> dict[str, Any]:
    chunk_rows = _summary_chunk_rows()
    peak_lookup = _prepare_peak_lookup(peak_meta_df)
    scratch_parent.mkdir(parents=True, exist_ok=True)

    chunks_processed = 0
    rows_read = 0
    mapped_rows = 0
    missing_peak_rows = 0
    psi_rows = 0

    with tempfile.TemporaryDirectory(prefix=f"{sample_id}.summary.", dir=scratch_parent) as tmpdir:
        db_path = Path(tmpdir) / "summary.sqlite"
        with sqlite3.connect(db_path) as con:
            _init_summary_db(con)

            for chunk in _iter_long_count_chunks(peak_counts, chunk_rows):
                chunks_processed += 1
                clean = _clean_long_count_chunk(chunk)
                if clean.empty:
                    continue

                rows_read += len(clean)
                merged = clean.merge(peak_lookup, on="peak_name", how="left")
                missing_gene = merged["gene_name"].isna()
                if missing_gene.any():
                    missing_peak_rows += int(missing_gene.sum())
                    merged = merged.loc[~missing_gene].copy()
                if merged.empty:
                    continue

                mapped_rows += len(merged)
                gene_cell_chunk = (
                    merged.groupby(["gene_name", "cell_barcode"], as_index=False, observed=True)["umi_count"]
                    .sum()
                    .rename(columns={"umi_count": "gene_umi_count"})
                )
                _upsert_gene_cell(con, gene_cell_chunk)
                if chunks_processed % 10 == 0:
                    con.commit()
            con.commit()

            _write_gene_cell_table_from_db(con, gene_cell_out)
            dense_result = _write_cell_gene_matrix_from_db_or_skip(
                con,
                output_path=cell_gene_matrix_out,
                manifest_path=cell_gene_matrix_manifest,
                long_gene_cell_path=gene_cell_out,
                sample_id=sample_id,
            )

            psi_out.parent.mkdir(parents=True, exist_ok=True)
            psi_tmp = _atomic_tmp_path(psi_out)
            with gzip.open(
                psi_tmp,
                "wt",
                encoding="utf-8",
                newline="",
                compresslevel=_summary_gzip_compresslevel(),
            ) as psi_handle:
                psi_handle.write(
                    "\t".join(
                        [
                            "peak_name", "gene_name", "cell_barcode", "peak_umi_count",
                            "gene_umi_count", "three_prime_utr_length", "psi"
                        ]
                    )
                    + "\n"
                )
                for chunk in _iter_long_count_chunks(peak_counts, chunk_rows):
                    clean = _clean_long_count_chunk(chunk)
                    if clean.empty:
                        continue

                    merged = clean.merge(peak_lookup, on="peak_name", how="left")
                    merged = merged.loc[~merged["gene_name"].isna()].copy()
                    if merged.empty:
                        continue

                    psi_chunk = _attach_gene_totals_from_db(con, merged)
                    if psi_chunk.empty:
                        continue

                    psi_chunk["peak_umi_count"] = psi_chunk["umi_count"].astype(int)
                    psi_chunk["gene_umi_count"] = psi_chunk["gene_umi_count"].astype(int)
                    denominator = psi_chunk["gene_umi_count"].where(psi_chunk["gene_umi_count"] != 0, pd.NA)
                    psi_chunk["psi"] = psi_chunk["peak_umi_count"] / denominator
                    psi_chunk.loc[psi_chunk["gene_umi_count"] < psi_min_gene_umis, "psi"] = pd.NA

                    weighted_denominator = psi_chunk["gene_umi_count"].where(
                        psi_chunk["gene_umi_count"] != 0,
                        pd.NA,
                    )
                    psi_chunk["weighted_three_prime_utr_length"] = (
                        psi_chunk["peak_umi_count"]
                        * psi_chunk["three_prime_utr_length"]
                        / weighted_denominator
                    ).fillna(0)
                    weighted_chunk = (
                        psi_chunk.groupby(
                            ["gene_name", "cell_barcode", "gene_umi_count"],
                            as_index=False,
                            observed=True,
                        )["weighted_three_prime_utr_length"]
                        .sum()
                    )
                    _upsert_weighted_utr(con, weighted_chunk)

                    psi_chunk = psi_chunk.rename(columns={"umi_count": "old_umi_count"})
                    psi_chunk[
                        [
                            "peak_name", "gene_name", "cell_barcode", "peak_umi_count",
                            "gene_umi_count", "three_prime_utr_length", "psi"
                        ]
                    ].to_csv(psi_handle, sep="\t", index=False, header=False)
                    psi_rows += len(psi_chunk)
                    if psi_rows and psi_rows % (chunk_rows * 10) == 0:
                        con.commit()

            psi_tmp.replace(psi_out)
            con.commit()
            _write_weighted_utr_table_from_db(
                con,
                weighted_utr_out,
                psi_min_gene_umis=psi_min_gene_umis,
            )

    if missing_peak_rows:
        logger_stage.warning(
            f"{sample_id} has {missing_peak_rows} peak-count rows without gene mapping; dropping them."
        )

    return {
        "chunks_processed": chunks_processed,
        "rows_read": rows_read,
        "mapped_rows": mapped_rows,
        "missing_peak_rows": missing_peak_rows,
        "psi_rows": psi_rows,
        "chunk_rows": chunk_rows,
        "dense_result": dense_result,
    }


def _load_count_matrix(path: Path) -> pd.DataFrame:
    raw_df = pd.read_csv(path, sep="\t", compression="gzip", header=None)

    if raw_df.shape[1] == 3:
        raw_df.columns = ["peak_name", "cell_barcode", "umi_count"]
        first_count = str(raw_df.iloc[0]["umi_count"]) if not raw_df.empty else ""
        if not first_count.isdigit():
            raw_df = raw_df.iloc[1:].copy()
        raw_df["peak_name"] = raw_df["peak_name"].astype(str)
        raw_df["cell_barcode"] = raw_df["cell_barcode"].astype(str)
        raw_df["umi_count"] = pd.to_numeric(raw_df["umi_count"], errors="coerce")
        raw_df = raw_df.dropna(subset=["umi_count"])
        raw_df["umi_count"] = raw_df["umi_count"].astype(int)
        return raw_df

    wide_df = pd.read_csv(path, sep="\t", compression="gzip")
    feature_col = wide_df.columns[0]
    long_df = wide_df.melt(id_vars=[feature_col], var_name="cell_barcode", value_name="umi_count")
    long_df = long_df.rename(columns={feature_col: "peak_name"})
    long_df["umi_count"] = pd.to_numeric(long_df["umi_count"], errors="coerce")
    long_df = long_df.dropna(subset=["umi_count"])
    long_df = long_df[long_df["umi_count"] != 0].copy()
    long_df["peak_name"] = long_df["peak_name"].astype(str)
    long_df["cell_barcode"] = long_df["cell_barcode"].astype(str)
    long_df["umi_count"] = long_df["umi_count"].astype(int)
    return long_df
