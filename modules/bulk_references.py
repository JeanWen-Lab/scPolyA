import numpy as np
import pandas as pd
import pyBigWig
from tqdm import tqdm
import logging

logger = logging.getLogger("scPolyA.prepare-bulk")
logger.setLevel(logging.INFO)


def map_chrom_for_bw(chrom: str, bw_chroms: dict) -> str | None:
    """
    Map chrom names between GTF (often '1','X','MT') and bigWig (often 'chr1','chrX','chrM').
    Returns a chrom name that exists in bw_chroms, or None.
    """
    if chrom in bw_chroms:
        return chrom

    # add/remove 'chr' prefix
    if chrom.startswith("chr"):
        alt = chrom[3:]
        if alt in bw_chroms:
            return alt
    else:
        alt = "chr" + chrom
        if alt in bw_chroms:
            return alt

    # mitochondrial variants
    if chrom in ("MT", "chrMT", "M", "chrM"):
        for cand in ("chrM", "MT", "chrMT", "M"):
            if cand in bw_chroms:
                return cand

    return None


def parse_gtf_and_get_search_regions(gtf_path: str) -> pd.DataFrame:
    """
    Reads GTF keeping both 'stop_codon' and 'exon'.
    Determines a 3' anchor (stop_codon > exon boundary).
    Computes gene boundaries to define neighbor limits.
    All coordinates in returned genes_df are in a consistent hybrid:
      - start: 0-based
      - end:   0-based exclusive (numeric equals original GTF end)
      - utr_start (anchor): 0-based coordinate
      - next_gene_start: 0-based
      - prev_gene_end: 0-based exclusive (numeric equals original GTF end)
    """
    logger.info(f"Loading GTF: {gtf_path}")

    cols = ['seqname', 'source', 'feature', 'start', 'end', 'score', 'strand', 'frame', 'attribute']

    df = pd.read_csv(
        gtf_path,
        sep='\t',
        comment='#',
        names=cols,
        usecols=[0, 2, 3, 4, 6, 8],
        dtype={'seqname': str}
    )

    df['gene_id'] = df['attribute'].str.extract(r'gene_id "([^"]+)"')
    df['gene_name'] = df['attribute'].str.extract(r'gene_name "([^"]+)"')
    df['gene_name'] = df['gene_name'].fillna(df['gene_id'])

    # keep only what we need
    df = df[df['feature'].isin(['stop_codon', 'exon'])].copy()

    # convert GTF start 1-based -> 0-based
    df['start'] = df['start'] - 1
    # keep end numeric as 0-based exclusive endpoint (equal to original GTF end)

    gene_info = []
    grouped = df.groupby(['gene_id', 'seqname', 'strand'], sort=False)

    for (gid, chrom, strand), group in tqdm(grouped, desc="Indexing genes"):
        gname = group['gene_name'].iloc[0]
        stops = group[group['feature'] == 'stop_codon']

        if not stops.empty:
            if strand == '+':
                # stop_codon end: original 1-based inclusive end -> numeric equals 0-based exclusive end
                stop_pos = int(stops['end'].max())
            else:
                # stop_codon start: already 0-based after conversion
                stop_pos = int(stops['start'].min())
        else:
            # fallback: use exon boundary
            if strand == '+':
                stop_pos = int(group['end'].max())
            else:
                stop_pos = int(group['start'].min())

        gene_info.append({
            'gene_id': gid,
            'gene_name': gname,
            'chrom': chrom,
            'strand': strand,
            'utr_start': stop_pos
        })

    genes_df = pd.DataFrame(gene_info)

    # gene boundaries (use exons)
    exons_only = df[df['feature'] == 'exon']
    if exons_only.empty:
        exons_only = df.copy()

    gene_boundaries = (
        exons_only.groupby(['gene_id', 'seqname', 'strand'], as_index=False)
        .agg(gb_start0=('start', 'min'), gb_end0=('end', 'max'))
        .rename(columns={'seqname': 'chrom'})
    )

    genes_df = genes_df.merge(
        gene_boundaries,
        on=['gene_id', 'chrom', 'strand'],
        how='left'
    )

    # neighbor limits (robust to overlapping_locus, same-strand only):
    # + strand: next gene with gb_start0 strictly > anchor (utr_start)
    # - strand: prev gene with gb_end0 strictly < anchor (utr_start)
    genes_df = genes_df.sort_values(['chrom', 'strand', 'gb_start0']).reset_index(drop=True)

    SENTINEL_NEXT = 999_999_999

    def _compute_limits_for_chrom(g: pd.DataFrame) -> pd.DataFrame:
        starts = g['gb_start0'].to_numpy(dtype=np.int64)
        ends_sorted = np.sort(g['gb_end0'].to_numpy(dtype=np.int64))
        anchors = g['utr_start'].to_numpy(dtype=np.int64)

        idx_next = np.searchsorted(starts, anchors + 1, side='left')
        next_start = np.full(anchors.shape, SENTINEL_NEXT, dtype=np.int64)
        valid_next = idx_next < len(starts)
        next_start[valid_next] = starts[idx_next[valid_next]]

        idx_prev = np.searchsorted(ends_sorted, anchors, side='left') - 1
        prev_end = np.zeros(anchors.shape, dtype=np.int64)
        valid_prev = idx_prev >= 0
        prev_end[valid_prev] = ends_sorted[idx_prev[valid_prev]]

        out = g.copy()
        out['next_gene_start'] = next_start.astype(int)
        out['prev_gene_end'] = prev_end.astype(int)
        return out

    genes_df = genes_df.groupby(['chrom', 'strand'], group_keys=False, sort=False).apply(_compute_limits_for_chrom)

    return genes_df


def get_functional_end(bw, bw_chrom: str, start_search: int, end_search: int,
                       strand: str, step: int = 100, min_coverage: float = 0.1, continuity_ratio: float = 0.8) -> int:
    """
    Scan bigWig coverage to find 'functional' end within [start_search, end_search).
    Coordinates:
      - start_search: 0-based inclusive
      - end_search:   0-based exclusive
    Returns:
      - for '+': a 0-based coordinate within [start_search, end_search] (exclusive end)
      - for '-': a 0-based coordinate within [start_search, end_search] (inclusive start-like),
                 as used by caller.
    """
    if start_search >= end_search:
        return start_search

    try:
        chrom_len = bw.chroms(bw_chrom)
        end_search = min(int(end_search), int(chrom_len))
        start_search = max(0, int(start_search))
        if start_search >= end_search:
            return start_search

        vals = bw.values(bw_chrom, int(start_search), int(end_search), numpy=True)
    except Exception as e:
        logger.warning(f"bigWig query failed: chrom={bw_chrom} start={start_search} end={end_search} err={e}")
        return start_search

    vals = np.nan_to_num(vals)

    # For minus, scan from high->low, but bw array is low->high
    if strand == '-':
        vals = vals[::-1]

    n_windows = len(vals) // step
    if n_windows == 0:
        return start_search

    reshaped = vals[:n_windows * step].reshape(n_windows, step)
    binary_coverage = (reshaped >= min_coverage).astype(int)
    fracs = np.sum(binary_coverage, axis=1) / step
    valid_windows = fracs >= continuity_ratio

    if np.all(valid_windows):
        break_idx = n_windows
    else:
        break_idx = int(np.argmin(valid_windows))  # first False

    valid_len = break_idx * step

    if strand == '+':
        return start_search + valid_len
    else:
        return end_search - valid_len


def process_bigwig_to_gtf(bw_plus_path: str, bw_minus_path: str, gtf_path: str,
                          output_gene_gtf: str, output_window_gtf: str,
                          max_ext: int = 50_000):
    genes = parse_gtf_and_get_search_regions(gtf_path)

    bw_plus = pyBigWig.open(bw_plus_path)
    bw_minus = pyBigWig.open(bw_minus_path)
    plus_chroms = bw_plus.chroms()
    minus_chroms = bw_minus.chroms()

    utr_results = []
    locus_results = []
    stats = {
        "total": 0,
        "kept_plus": 0,
        "kept_minus": 0,
        "skip_plus_no_chrom": 0,
        "skip_minus_no_chrom": 0,
        "skip_plus_empty_window": 0,
        "skip_minus_empty_window": 0,
        "invalid_plus": 0,
        "invalid_minus": 0,
        "kept_locus": 0,
        "locus_fallback_gene_body": 0,
        "locus_extended_by_utr": 0,
    }

    logger.info("Scanning coverage to determine functional 3'UTR windows...")

    chrom_map_plus = {c: map_chrom_for_bw(c, plus_chroms) for c in genes['chrom'].unique()}
    chrom_map_minus = {c: map_chrom_for_bw(c, minus_chroms) for c in genes['chrom'].unique()}

    for _, row in tqdm(genes.iterrows(), total=genes.shape[0]):
        stats["total"] += 1

        gtf_chrom = row['chrom']
        strand = row['strand']
        gene_id = row['gene_id']
        gene_name = row['gene_name']

        # ✅ 修复：先获取 gene body 坐标
        gb_start0 = int(row["gb_start0"])
        gb_end0 = int(row["gb_end0"])

        # ✅ 修复：然后再进行 anchor 的边界 clamp
        anchor0 = int(row['utr_start'])
        anchor0 = max(gb_start0, min(anchor0, gb_end0))

        attr_string = f'gene_id "{gene_id}"; gene_name "{gene_name}";'

        locus_start0 = gb_start0
        locus_end0 = gb_end0
        locus_extended = False
        had_valid_utr = False

        if strand == '+':
            # 优化：直接查字典
            bw_chrom = chrom_map_plus.get(gtf_chrom)
            if bw_chrom is None:
                stats["skip_plus_no_chrom"] += 1
                # Still output locus (gene_body)
                stats["locus_fallback_gene_body"] += 1
            else:
                search_limit = int(row['next_gene_start'])
                if search_limit - anchor0 > max_ext:
                    search_limit = anchor0 + max_ext

                if search_limit <= anchor0:
                    stats["skip_plus_empty_window"] += 1
                    stats["locus_fallback_gene_body"] += 1
                else:
                    f_end0 = get_functional_end(bw_plus, bw_chrom, anchor0, search_limit, strand='+')

                    if f_end0 <= anchor0:
                        stats["invalid_plus"] += 1
                        stats["locus_fallback_gene_body"] += 1
                    else:
                        had_valid_utr = True
                        # three_prime_utr: [anchor0, f_end0) => (anchor0+1 .. f_end0) in GTF
                        utr_results.append([gtf_chrom, "PythonAPA", "three_prime_utr",
                                            anchor0 + 1, f_end0, ".", "+", ".", attr_string])
                        stats["kept_plus"] += 1

                        # extend locus end by utr window end
                        if f_end0 > locus_end0:
                            locus_end0 = f_end0
                            locus_extended = True

        else:
            bw_chrom = chrom_map_minus.get(gtf_chrom)
            if bw_chrom is None:
                stats["skip_minus_no_chrom"] += 1
                stats["locus_fallback_gene_body"] += 1
            else:
                search_limit = int(row['prev_gene_end'])
                if anchor0 - search_limit > max_ext:
                    search_limit = anchor0 - max_ext

                if anchor0 <= search_limit:
                    stats["skip_minus_empty_window"] += 1
                    stats["locus_fallback_gene_body"] += 1
                else:
                    f_start0 = get_functional_end(bw_minus, bw_chrom, search_limit, anchor0, strand='-')

                    if f_start0 >= anchor0:
                        stats["invalid_minus"] += 1
                        stats["locus_fallback_gene_body"] += 1
                    else:
                        had_valid_utr = True
                        # three_prime_utr: [f_start0, anchor0) => (f_start0+1 .. anchor0) in GTF
                        utr_results.append([gtf_chrom, "PythonAPA", "three_prime_utr",
                                            f_start0 + 1, anchor0, ".", "-", ".", attr_string])
                        stats["kept_minus"] += 1

                        # extend locus start by utr window start (more 5' in genome coordinates for minus)
                        if f_start0 < locus_start0:
                            locus_start0 = f_start0
                            locus_extended = True

        # ---- Write locus union for every gene (always) ----
        # Convert 0-based half-open locus to 1-based inclusive GTF
        locus_start1 = locus_start0 + 1
        locus_end1 = locus_end0
        if locus_end1 >= locus_start1:
            locus_results.append([gtf_chrom, "PythonAPA", "gene_locus",
                                  locus_start1, locus_end1, ".", strand, ".", attr_string])
            stats["kept_locus"] += 1
            if locus_extended and had_valid_utr:
                stats["locus_extended_by_utr"] += 1
        else:
            # This should be extremely rare unless gb_start/end are broken.
            stats["locus_fallback_gene_body"] += 1

    bw_plus.close()
    bw_minus.close()

    logger.info(f"Stats: {stats}")

    # OUTPUT 1: bulk gene-locus gtf
    locus_df = pd.DataFrame(locus_results, columns=[
        'seqname', 'source', 'feature', 'start', 'end', 'score', 'strand', 'frame', 'attribute'
    ])
    locus_df = locus_df[locus_df['end'] >= locus_df['start']]
    logger.info(f"Writing {len(locus_df)} locus rows to: {output_gene_gtf}")
    locus_df.to_csv(output_gene_gtf, sep='\t', header=False, index=False, quoting=3)

    # OUTPUT 2: bulk utr-window gtf
    utr_df = pd.DataFrame(utr_results, columns=[
        'seqname', 'source', 'feature', 'start', 'end', 'score', 'strand', 'frame', 'attribute'
    ])
    utr_df = utr_df[utr_df['end'] >= utr_df['start']]
    logger.info(f"Writing {len(utr_df)} UTR rows to: {output_window_gtf}")
    utr_df.to_csv(output_window_gtf, sep='\t', header=False, index=False, quoting=3)



def build_bulk_reference(arguments):
    output_gene_gtf = getattr(arguments, "output_gene_gtf", None)
    output_window_gtf = getattr(arguments, "output_window_gtf", None)
    legacy_output_gtf = getattr(arguments, "output_gtf", None)

    if output_gene_gtf and output_window_gtf:
        pass
    elif legacy_output_gtf:
        # Backward compatibility: old single output semantics
        output_gene_gtf = legacy_output_gtf
        if legacy_output_gtf.endswith(".gtf"):
            output_window_gtf = legacy_output_gtf[:-4] + ".utr_windows.gtf"
        else:
            output_window_gtf = legacy_output_gtf + ".utr_windows.gtf"
        logger.warning("Using deprecated --output-gtf. "
                       f"Auto-derived --output-window-gtf: {output_window_gtf}")
    else:
        raise ValueError("prepare-bulk requires either "
                         "(--output-gene-gtf and --output-window-gtf) or legacy --output-gtf.")

    process_bigwig_to_gtf(arguments.plus_bw, arguments.minus_bw, arguments.gene_gtf,
                          output_gene_gtf=output_gene_gtf,
                          output_window_gtf=output_window_gtf)
