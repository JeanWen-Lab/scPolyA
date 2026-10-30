from __future__ import annotations

import csv
import gzip
import sys
from pathlib import Path

import pandas as pd
import pysam

from logging_config import run_tool_command


LOCAL_REPO_ROOT = Path(__file__).resolve().parents[1].resolve()


def forked_umi_count_command(
    *,
    input_bam: Path,
    output_counts: Path,
    log_path: Path,
    feature_map: Path,
    args,
) -> None:
    input_bam = input_bam.resolve()
    output_counts = output_counts.resolve()
    feature_map = feature_map.resolve()
    log_path = log_path.resolve()
    umi_tools_cmd = [
        sys.executable,
        "-m",
        "umi_count_xr.count",
        "--per-gene",
        "--per-cell",
        "--extract-umi-method=tag",
        f"--umi-tag={args.umi_tag}",
        f"--cell-tag={args.cb_tag}",
        f"--feature-map={feature_map}",
        "-I", str(input_bam),
        "-S", str(output_counts),
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as log_f:
        run_tool_command(
            umi_tools_cmd,
            tool="umi_count_xr",
            operation=log_path.stem,
            compatibility_log=log_path,
            check=True,
            stdout=log_f,
            stderr=log_f,
            text=True,
            cwd=str(LOCAL_REPO_ROOT),
        )


def load_long_count_matrix(path: Path, feature_col: str) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="ascii") as f:
        header = f.readline().strip().split("\t")

    if len(header) == 3 and header[2] == "count":
        raw_df = pd.read_csv(
            path,
            sep="\t",
            compression="gzip",
            names=[feature_col, "cell_barcode", "umi_count"],
            skiprows=1,
            dtype={feature_col: "string", "cell_barcode": "string", "umi_count": "string"},
        )
        raw_df["umi_count"] = pd.to_numeric(raw_df["umi_count"], errors="coerce")
        raw_df = raw_df.dropna(subset=["umi_count"])
        raw_df["umi_count"] = raw_df["umi_count"].astype(int)
        return raw_df

    wide_df = pd.read_csv(path, sep="\t", compression="gzip")
    matrix_feature_col = wide_df.columns[0]
    long_df = wide_df.melt(id_vars=[matrix_feature_col], var_name="cell_barcode", value_name="umi_count")
    long_df = long_df.rename(columns={matrix_feature_col: feature_col})
    long_df["umi_count"] = pd.to_numeric(long_df["umi_count"], errors="coerce")
    long_df = long_df.dropna(subset=["umi_count"])
    long_df = long_df[long_df["umi_count"] != 0].copy()
    long_df[feature_col] = long_df[feature_col].astype(str)
    long_df["cell_barcode"] = long_df["cell_barcode"].astype(str)
    long_df["umi_count"] = long_df["umi_count"].astype(int)
    return long_df


def write_grouped_count_matrix(df: pd.DataFrame, output_path: Path, feature_col: str) -> None:
    grouped_df = (
        df.groupby([feature_col, "cell_barcode"], as_index=False, observed=True)["umi_count"]
        .sum()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output_path, "wt", encoding="ascii") as out:
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        writer.writerow([feature_col, "cell_barcode", "umi_count"])
        for row in grouped_df.itertuples(index=False):
            writer.writerow([getattr(row, feature_col), row.cell_barcode, int(row.umi_count)])
