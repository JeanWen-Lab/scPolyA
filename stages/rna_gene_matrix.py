from __future__ import annotations

import csv
import gzip
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List

import pandas as pd

from logging_config import (
    stage_scope, get_logger, LOG_MAIN, LoggersWrapper,
    LOG_STAGE_rna_gene_matrix, task_scope
)
from task_manage import ScItem
from task_manage.define import CACHE_dedup_method, CACHE_unit_inputs
from task_manage.effective_config import build_rna_gene_matrix_config
from task_manage.run_layout import FILTERED_ADJUSTED_GTF
from task_manage.task_runtime import run_flex_batch
from utils.feature_sidecar_utils import build_feature_assignment_sidecar
from utils.xr_utils import forked_umi_count_command

if TYPE_CHECKING:
    from task_manage.run_layout import RunLayout
    from task_manage import Resources, TaskManager

STAGE = LOG_STAGE_rna_gene_matrix
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)

RNA_GENE_CHUNK_ROWS_ENV = "SCPOLYA_RNA_GENE_CHUNK_ROWS"
DEFAULT_RNA_GENE_CHUNK_ROWS = 500_000
DENSE_CELL_GENE_MAX_CELLS_ENV = "SCPOLYA_MAX_DENSE_CELL_GENE_MATRIX_CELLS"
DEFAULT_DENSE_CELL_GENE_MAX_CELLS = 50_000_000
RNA_GENE_GZIP_COMPRESSLEVEL_ENV = "SCPOLYA_RNA_GENE_GZIP_COMPRESSLEVEL"
DEFAULT_RNA_GENE_GZIP_COMPRESSLEVEL = 1


def rna_gene_matrix(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    with stage_scope(STAGE):
        config_item = build_rna_gene_matrix_config(
            args,
            dedup_method=cache.get(CACHE_dedup_method),
        )
        config_signature = layout.RnaGeneMatrixNameFromDict(config_item)
        output_root = layout.RnaGeneMatrixSub(config_item)
        temp_root = layout.RnaGeneMatrixTempSub(config_item)
        sidecars_root = layout.RnaGeneSidecarsSub(config_item)
        units_counts_root = temp_root / "unit_counts"
        sidecars_root.mkdir(parents=True, exist_ok=True)
        units_counts_root.mkdir(parents=True, exist_ok=True)

        gene_gtf = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_GTF, args=args)
        if not gene_gtf.exists():
            raise FileNotFoundError(f"Filtered adjusted gene GTF not found: {gene_gtf}")

        csv_path = cache.get(CACHE_unit_inputs)
        unit_items: List[ScItem] = []
        sample_meta: dict[str, dict] = {}

        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                uid = r["unit_id"]
                ctg = r["contig_name"]
                sid = r.get("sample_id")
                if not sid or not sid.strip():
                    raise ValueError("Invalid unit inputs row: missing or empty sample_id.")
                item = ScItem(
                    uid=uid,
                    sid=sid,
                    ctg=ctg,
                    dedup_status=r["dedup_status"],
                    add_dedup_method=True,
                    args=args,
                    extra_info={"src": Path(r["resolved_path"])},
                    extra_key=config_signature,
                )
                unit_items.append(item)
                sample_meta.setdefault(sid, {"dedup_status": r["dedup_status"]})

        if not unit_items:
            logger_stage.warning("No units found for RNA gene matrix generation.")
            return

        sample_items: List[ScItem] = [
            ScItem(
                sid=sid,
                dedup_status=meta["dedup_status"],
                add_dedup_method=True,
                args=args,
                extra_key=config_signature,
            )
            for sid, meta in sample_meta.items()
        ]

        def annotate_gene_units_worker(item: ScItem):
            input_bam = Path(item.extra_info["src"])
            output_sidecar = sidecars_root / item.format_name(suffix="xr_to_gene.tsv.gz")
            build_feature_assignment_sidecar(
                input_bam=input_bam,
                annotation_gtf=gene_gtf,
                output_sidecar=output_sidecar,
                cell_barcode_tag=args.cb_tag,
                thread_count=max(1, args.n_threads_bam_annotation),
                feature_type="exon",
                group_attr="gene_name",
            )

        def count_gene_units_worker(item: ScItem):
            input_bam = Path(item.extra_info["src"])
            feature_map = sidecars_root / item.format_name(suffix="xr_to_gene.tsv.gz")
            if not feature_map.exists():
                raise FileNotFoundError(f"Gene sidecar not found: {feature_map}")

            counts_output = units_counts_root / item.format_name(suffix="gene-counts.tsv.gz")
            log_path = layout.LogUmitools / f"{item.uid}.rna_gene_matrix.count.log"
            forked_umi_count_command(
                input_bam=input_bam,
                output_counts=counts_output,
                log_path=log_path,
                feature_map=feature_map,
                args=args,
            )

        def summarize_gene_matrix_worker(item: ScItem):
            unit_count_paths: list[Path] = []
            for unit_item in unit_items:
                if unit_item.sid != item.sid:
                    continue
                unit_counts = units_counts_root / unit_item.format_name(suffix="gene-counts.tsv.gz")
                if not unit_counts.exists():
                    raise FileNotFoundError(f"Unit RNA gene counts not found: {unit_counts}")
                unit_count_paths.append(unit_counts)

            gene_cell_out = output_root / item.format_name(suffix="rna_gene_cell.tsv.gz", is_annotated_bam=False)
            cell_gene_out = output_root / item.format_name(suffix="rna_cell_gene.matrix.tsv.gz", is_annotated_bam=False)
            result = _summarize_gene_count_files_streaming(
                count_paths=unit_count_paths,
                gene_cell_out=gene_cell_out,
                cell_gene_matrix_out=cell_gene_out,
                sample_id=item.sid,
                scratch_parent=output_root,
            )
            logger_stage.info(
                f"{item.sid} RNA gene matrix wrote long gene-cell table to {gene_cell_out}; "
                f"dense matrix status {result['dense_result']['status']} "
                f"(estimated_dense_cells={result['dense_result']['estimated_dense_cells']}, "
                f"max_dense_cells={result['dense_result']['max_dense_cells']})."
            )

        with task_scope("annotate_gene_units"):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base="annotate_gene_units",
                items=unit_items,
                identity_keys=["sid", "ctg", "dedup_method"],
                param_keys=["extra_key"],
                fn=annotate_gene_units_worker,
                max_workers=args.n_workers_annotate or args.n_workers,
            )

        with task_scope("count_gene_units"):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base="count_gene_units",
                items=unit_items,
                identity_keys=["sid", "ctg", "dedup_method"],
                param_keys=["extra_key"],
                fn=count_gene_units_worker,
                max_workers=args.n_workers,
            )

        with task_scope("summarize_gene_matrix"):
            run_flex_batch(
                tm=tm,
                stage=STAGE,
                task_base="summarize_gene_matrix",
                items=sample_items,
                identity_keys=["sid", "dedup_method"],
                param_keys=["extra_key"],
                fn=summarize_gene_matrix_worker,
                max_workers=args.n_workers,
            )


def _summarize_gene_count_files_streaming(
    *,
    count_paths: Iterable[Path],
    gene_cell_out: Path,
    cell_gene_matrix_out: Path,
    sample_id: str,
    scratch_parent: Path,
    max_dense_cells: int | None = None,
) -> dict[str, object]:
    paths = tuple(Path(path) for path in count_paths)
    scratch_parent.mkdir(parents=True, exist_ok=True)
    chunks_processed = 0
    rows_read = 0
    rows_retained = 0

    with tempfile.TemporaryDirectory(prefix=f"{sample_id}.rna-gene.", dir=scratch_parent) as tmpdir:
        db_path = Path(tmpdir) / "rna_gene_matrix.sqlite"
        with sqlite3.connect(db_path) as con:
            _init_rna_gene_db(con)

            for count_path in paths:
                for chunk in _iter_gene_count_chunks(count_path, _rna_gene_chunk_rows()):
                    chunks_processed += 1
                    clean = _clean_gene_count_chunk(chunk)
                    if clean.empty:
                        continue
                    rows_read += len(clean)
                    gene_cell_chunk = (
                        clean.groupby(["gene_name", "cell_barcode"], as_index=False, observed=True)["umi_count"]
                        .sum()
                        .rename(columns={"umi_count": "gene_umi_count"})
                    )
                    rows_retained += len(gene_cell_chunk)
                    _upsert_rna_gene_cell(con, gene_cell_chunk)
                    if chunks_processed % 10 == 0:
                        con.commit()
            con.commit()

            _write_rna_gene_cell_table_from_db(con, gene_cell_out)
            dense_result = _write_rna_cell_gene_matrix_from_db_or_omit(
                con,
                output_path=cell_gene_matrix_out,
                sample_id=sample_id,
                max_dense_cells=max_dense_cells,
            )

    return {
        "count_files": len(paths),
        "chunks_processed": chunks_processed,
        "rows_read": rows_read,
        "grouped_rows_seen": rows_retained,
        "gene_cell_path": str(gene_cell_out),
        "dense_result": dense_result,
    }


def _rna_gene_chunk_rows() -> int:
    return _positive_int_from_env(RNA_GENE_CHUNK_ROWS_ENV, DEFAULT_RNA_GENE_CHUNK_ROWS)


def _max_dense_cell_gene_matrix_cells() -> int:
    return _non_negative_int_from_env(DENSE_CELL_GENE_MAX_CELLS_ENV, DEFAULT_DENSE_CELL_GENE_MAX_CELLS)


def _rna_gene_gzip_compresslevel() -> int:
    raw_value = os.environ.get(RNA_GENE_GZIP_COMPRESSLEVEL_ENV)
    if raw_value is None:
        return DEFAULT_RNA_GENE_GZIP_COMPRESSLEVEL
    try:
        value = int(raw_value)
    except ValueError:
        logger_stage.warning(
            f"Ignoring invalid {RNA_GENE_GZIP_COMPRESSLEVEL_ENV}={raw_value!r}; "
            f"using {DEFAULT_RNA_GENE_GZIP_COMPRESSLEVEL}."
        )
        return DEFAULT_RNA_GENE_GZIP_COMPRESSLEVEL
    if value < 0 or value > 9:
        logger_stage.warning(
            f"Ignoring out-of-range {RNA_GENE_GZIP_COMPRESSLEVEL_ENV}={raw_value!r}; "
            f"using {DEFAULT_RNA_GENE_GZIP_COMPRESSLEVEL}."
        )
        return DEFAULT_RNA_GENE_GZIP_COMPRESSLEVEL
    return value


def _positive_int_from_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger_stage.warning(f"Ignoring invalid {name}={raw_value!r}; using {default}.")
        return default
    if value <= 0:
        logger_stage.warning(f"Ignoring non-positive {name}={raw_value!r}; using {default}.")
        return default
    return value


def _non_negative_int_from_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger_stage.warning(f"Ignoring invalid {name}={raw_value!r}; using {default}.")
        return default
    if value < 0:
        logger_stage.warning(f"Ignoring negative {name}={raw_value!r}; using {default}.")
        return default
    return value


def _iter_gene_count_chunks(path: Path, chunk_rows: int):
    header = _read_tsv_header(path)
    compression = "gzip" if path.suffix == ".gz" else None
    if len(header) == 3 and header[2] in {"count", "umi_count"}:
        yield from pd.read_csv(
            path,
            sep="\t",
            compression=compression,
            names=["gene_name", "cell_barcode", "umi_count"],
            skiprows=1,
            chunksize=chunk_rows,
            dtype=str,
        )
        return

    feature_col = header[0] if header else "gene_name"
    for chunk in pd.read_csv(path, sep="\t", compression=compression, chunksize=chunk_rows, dtype=str):
        if chunk.empty:
            yield pd.DataFrame(columns=["gene_name", "cell_barcode", "umi_count"])
            continue
        long = chunk.melt(id_vars=[feature_col], var_name="cell_barcode", value_name="umi_count")
        long = long.rename(columns={feature_col: "gene_name"})
        long["umi_count"] = pd.to_numeric(long["umi_count"], errors="coerce")
        long = long.dropna(subset=["umi_count"])
        long = long[long["umi_count"] != 0].copy()
        yield long


def _read_tsv_header(path: Path) -> list[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        first_line = handle.readline()
    if not first_line:
        return []
    return first_line.rstrip("\n\r").split("\t")


def _clean_gene_count_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    if chunk.empty:
        return chunk
    clean = chunk[["gene_name", "cell_barcode", "umi_count"]].copy()
    clean["umi_count"] = pd.to_numeric(clean["umi_count"], errors="coerce")
    clean = clean.dropna(subset=["gene_name", "cell_barcode", "umi_count"])
    if clean.empty:
        return clean
    clean["gene_name"] = clean["gene_name"].astype(str)
    clean["cell_barcode"] = clean["cell_barcode"].astype(str)
    clean["umi_count"] = clean["umi_count"].astype(int)
    return clean


def _init_rna_gene_db(con: sqlite3.Connection) -> None:
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


def _upsert_rna_gene_cell(con: sqlite3.Connection, gene_cell_df: pd.DataFrame) -> None:
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


def _rna_gene_db_shape(con: sqlite3.Connection) -> tuple[int, int, int]:
    n_cells = int(con.execute("SELECT COUNT(*) FROM summary_cell").fetchone()[0])
    n_genes = int(con.execute("SELECT COUNT(*) FROM summary_gene").fetchone()[0])
    return n_cells, n_genes, n_cells * n_genes


def _atomic_tmp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _write_rna_gene_cell_table_from_db(con: sqlite3.Connection, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _atomic_tmp_path(output_path)
    with gzip.open(
        tmp_path,
        "wt",
        encoding="utf-8",
        newline="",
        compresslevel=_rna_gene_gzip_compresslevel(),
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


def _write_rna_cell_gene_matrix_from_db_or_omit(
    con: sqlite3.Connection,
    *,
    output_path: Path,
    sample_id: str,
    max_dense_cells: int | None = None,
) -> dict[str, object]:
    n_cells, n_genes, estimated_dense_cells = _rna_gene_db_shape(con)
    dense_limit = _max_dense_cell_gene_matrix_cells() if max_dense_cells is None else max_dense_cells
    if estimated_dense_cells > dense_limit:
        if output_path.exists():
            output_path.unlink()
        logger_stage.warning(
            f"{sample_id} omitted dense RNA cell-gene matrix: {n_cells} cells x {n_genes} genes "
            f"= {estimated_dense_cells} cells exceeds limit {dense_limit}. "
            "Use the RNA gene-cell long table instead."
        )
        return {
            "status": "omitted_dense",
            "path": "",
            "dense_matrix_path": str(output_path),
            "n_cells": n_cells,
            "n_genes": n_genes,
            "estimated_dense_cells": estimated_dense_cells,
            "max_dense_cells": dense_limit,
        }

    gene_cell_df = pd.read_sql_query(
        """
        SELECT gene_name, cell_barcode, gene_umi_count
        FROM gene_cell
        """,
        con,
    )
    _write_rna_cell_gene_matrix(gene_cell_df, output_path)
    return {
        "status": "written_dense",
        "path": str(output_path),
        "dense_matrix_path": str(output_path),
        "n_cells": n_cells,
        "n_genes": n_genes,
        "estimated_dense_cells": estimated_dense_cells,
        "max_dense_cells": dense_limit,
    }


def _write_rna_cell_gene_matrix(gene_cell_df: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _atomic_tmp_path(output_path)
    if gene_cell_df.empty:
        with gzip.open(
            tmp_path,
            "wt",
            encoding="utf-8",
            newline="",
            compresslevel=_rna_gene_gzip_compresslevel(),
        ) as handle:
            csv.writer(handle, delimiter="\t").writerow(["cell_barcode"])
        tmp_path.replace(output_path)
        return

    cell_gene_matrix_df = (
        gene_cell_df.pivot(index="cell_barcode", columns="gene_name", values="gene_umi_count")
        .fillna(0)
        .astype(int)
        .reset_index()
    )
    cell_gene_matrix_df.to_csv(
        tmp_path,
        sep="\t",
        index=False,
        compression={"method": "gzip", "compresslevel": _rna_gene_gzip_compresslevel()},
    )
    tmp_path.replace(output_path)
