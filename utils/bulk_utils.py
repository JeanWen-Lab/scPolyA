import subprocess

from logging_config import get_logger, LoggersWrapper, LOG_MAIN, LOG_UTILS

suffix = lambda x: f" (bulk_utils:{x})"
logger_main = get_logger(LOG_MAIN)
logger_utils = get_logger(LOG_UTILS)
loggers = LoggersWrapper(main=logger_main, sub=logger_utils)

import pyBigWig
import pandas as pd

def bigwig_to_peaks(bw_path: str, cutoff: float, strand: str) -> pd.DataFrame:
    """
    Convert bigWig signal to merged intervals where signal >= cutoff,
    and compute length-weighted mean signal per interval (like binnedAverage on Rle).

    Returns a DataFrame with columns:
      seqnames, start, end, peakid, binn_score, strand

    Coordinates:
      start/end are 0-based, half-open [start, end) (standard BED / pyBigWig)
    """
    assert strand in {"+", "-"}, "strand must be '+' or '-'"

    rows = []
    peak_i = 1

    with pyBigWig.open(bw_path) as bw:
        chrom_sizes = bw.chroms()  # dict: chrom -> length

        for chrom, chrom_len in chrom_sizes.items():
            # intervals(chrom) returns list of (start, end, value) with constant value
            ivs = bw.intervals(chrom, 0, chrom_len)
            if not ivs:
                continue

            cur_start = None
            cur_end = None
            sum_w = 0.0        # sum(value * length)
            sum_len = 0        # sum(length)

            def flush():
                nonlocal peak_i, cur_start, cur_end, sum_w, sum_len
                if cur_start is None:
                    return
                mean_val = (sum_w / sum_len) if sum_len > 0 else 0.0
                peakid = f"peak_{peak_i}_{strand}"
                rows.append([chrom, cur_start, cur_end, peakid, mean_val, strand])
                peak_i += 1
                cur_start = cur_end = None
                sum_w = 0.0
                sum_len = 0

            for s, e, v in ivs:
                if v is None:
                    continue
                if v >= cutoff:
                    seg_len = e - s
                    if cur_start is None:
                        # start a new peak
                        cur_start, cur_end = s, e
                        sum_w = v * seg_len
                        sum_len = seg_len
                    else:
                        if s == cur_end:
                            # contiguous -> merge
                            cur_end = e
                            sum_w += v * seg_len
                            sum_len += seg_len
                        else:
                            # gap -> close previous and start new
                            flush()
                            cur_start, cur_end = s, e
                            sum_w = v * seg_len
                            sum_len = seg_len
                else:
                    # below cutoff -> close current peak if any
                    flush()

            flush()

    df = pd.DataFrame(rows, columns=["seqnames", "start", "end", "peakid", "binn_score", "strand"])
    return df


# Example:
# peaks_plus = bigwig_to_peaks("RNAseq_cardiomyocyte_mergeall_plus.bw", cutoff=5.0, strand="+")
# peaks_minus = bigwig_to_peaks("RNAseq_cardiomyocyte_mergeall_minus.bw", cutoff=5.0, strand="-")
# peaks_plus.to_csv("peaks_plus.bed", sep="\t", index=False, header=False)
