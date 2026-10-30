#!/usr/bin/env python3
"""Reproduce supplied infer fixture and reconstruct its model inputs via native BED + FASTA + bigWigs.

Run in a task environment with torch/numpy/pandas/pysam/pyBigWig and native
pipeline imports installed. Fixture reconstruction is engineered input-path
validation, not a new biological sample or an independent accuracy benchmark.
"""
from __future__ import annotations

import argparse
import csv
import json
import platform
import sqlite3
import sys
from pathlib import Path

# Support direct execution from the repository without altering the environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd
import pyBigWig
import pysam

from modules.deepcps.model import DeepCPS
from modules.deepcps.pipeline import extract_windows, read_sites, sha256, validate_model
from arguments import build_parser
from stages.deepcps import deepcps, resolve_deepcps_inputs
from task_manage import Plan, TaskManager
from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_upm_factor
from task_manage.run_layout import RunLayout


def compare(expected, scores, offsets, confidence):
    differences = {f'seed{k}': float(np.max(np.abs(scores[:, k - 1] - expected[f'exp_s{k}']))) for k in (1, 2, 3)}
    mask = expected.exp_offset.notna()
    identical = int(np.sum(offsets[mask] == expected.loc[mask, 'exp_offset'].to_numpy()))
    conf_diff = float(np.max(np.abs(confidence[mask] - expected.loc[mask, 'exp_conf'].to_numpy())))
    result = dict(per_seed_max_abs_diff=differences, localisation_expected_rows=int(mask.sum()),
                  localisation_identical_offsets=identical, localisation_conf_max_abs_diff=conf_diff,
                  tolerance=2e-3)
    result['pass'] = all(d < 2e-3 for d in differences.values()) and identical == int(mask.sum()) and conf_diff < 2e-3
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--handoff', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    a = parser.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    expected = pd.read_csv(a.handoff / 'test/example.windows.tsv', sep='\t')
    z = np.load(a.handoff / 'test/example.windows.npz')
    seqs = [row.tobytes().decode() for row in z['seq']]
    coverage = z['cov']
    model = DeepCPS(a.handoff / 'models', threads=2)
    direct_scores = model.score(seqs, coverage, batch=128)
    direct_location = model.locate(seqs, batch=128)
    direct = compare(expected, direct_scores, direct_location.argmax(1) - 100, direct_location.max(1))
    np.savez_compressed(a.output / 'direct-infer.npz', scores=direct_scores, location=direct_location)
    del model
    # Reconstruct each original model window on a separate 201-nt contig.
    # The BED always calls its center; reverse-strand inputs are converted back
    # to genomic orientation before writing FASTA and the minus-strand track.
    args = build_parser().parse_args([
        'run', '--output-dir', str(a.output), '--run-id', 'reconstructed', '--species', 'human',
        '--inputs', 'engineered-fixture.csv', '--gene-gtf', 'engineered-fixture.gtf', '--chrom-sizes', 'engineered-fixture.sizes',
        '--deepcps', '--reference-fasta', str(a.output / 'reconstructed.fa'),
        '--deepcps-model-dir', str(a.handoff / 'models'), '--quantification-mode', 'terminal-aware',
    ])
    args.merge_distance = args.merge_distance_polyA = 0
    args.unscaled_depth_threshold = args.unscaled_depth_threshold_polyA = 1
    cache = {CACHE_dedup_method: 'directional', CACHE_polyA_status: 'polyA-5-0-10', CACHE_upm_factor: .2}
    layout = RunLayout(out=a.output, run_id='reconstructed')
    layout.ensure_base_dirs()
    tm = TaskManager(state_dir=layout.State, config=vars(args))
    tm.load_or_init(Plan(samples=[], contigs_by_sample={}))
    paths, peak_item = resolve_deepcps_inputs(layout, args, cache)
    layout.AnnotatedFinalOutputSub(peak_item)
    for key in ('bed', 'chrom_sizes'):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
    contigs = [f'chrFixture{i:04d}' for i in range(len(seqs))]
    strands = expected.strand.to_list()
    complement = str.maketrans('ACGTN', 'TGCAN')
    with Path(paths['fasta']).open('w') as fa, paths['chrom_sizes'].open('w') as sizes, paths['bed'].open('w') as bed:
        for i, (chrom, strand, seq) in enumerate(zip(contigs, strands, seqs)):
            genomic = seq if strand == '+' else seq.translate(complement)[::-1]
            fa.write(f'>{chrom}\n{genomic}\n')
            sizes.write(f'{chrom}\t201\n')
            row = [chrom, '100', '101', str(expected.iloc[i]['name']), '4', strand, '2', '0.1', '10',
                   'FIXTURE', '0', 'TX', '1', '100', 'UTR3', 'FIXTURE_ID']
            bed.write('\t'.join(row) + '\n')
    pysam.faidx(str(paths['fasta']))
    with pysam.AlignmentFile(str(paths['bam']), 'wb', header={'HD': {'VN': '1.6'}, 'SQ': [{'SN': c, 'LN': 201} for c in contigs]}):
        pass
    for strand, key in (('+', 'cov_plus'), ('-', 'cov_minus')):
        with pyBigWig.open(str(paths[key]), 'w') as bw:
            bw.addHeader([(c, 201) for c in contigs])
            for i, (chrom, input_strand) in enumerate(zip(contigs, strands)):
                if input_strand != strand:
                    continue
                genomic_cov = coverage[i] if strand == '+' else coverage[i, ::-1]
                bw.addEntries([chrom] * 201, list(range(201)), ends=list(range(1, 202)), values=genomic_cov.astype(float).tolist())
    sites, _ = read_sites(paths['bed'])
    indices, extracted_seq, extracted_cov, reasons, _ = extract_windows(sites, paths['fasta'], paths['cov_plus'], paths['cov_minus'])
    assert indices == list(range(len(sites))) and not reasons
    assert seqs == extracted_seq
    np.testing.assert_array_equal(coverage, extracted_cov)
    original_bed_sha = sha256(paths['bed'])
    first = deepcps(tm, layout, args, cache)
    second = deepcps(tm, layout, args, cache)
    assert first['status'] == 'executed' and second['status'] == 'reused'
    with sqlite3.connect(tm.db_path) as connection:
        observations = connection.execute("SELECT observation_id, producer_instance_id FROM inline_execution_observations_v1 WHERE stage='deepcps' AND task_base='score_and_join' ORDER BY observation_id").fetchall()
    assert len(observations) == 2 and observations[0][1] == observations[1][1]
    with Path(first['output_dir'], 'scored.tsv').open() as handle:
        scored = list(csv.DictReader(handle, delimiter='\t'))
    scored_values = np.asarray([[float(row[f'deepcps_s{k}']) for k in (1, 2, 3)] for row in scored])
    offsets = np.asarray([int(row['locate_offset']) for row in scored])
    confidence = np.asarray([float(row['locate_conf']) for row in scored])
    integrated = compare(expected, scored_values, offsets, confidence)
    calibrated = np.asarray([float(row['deepcps_cal']) for row in scored])
    raw = np.asarray([float(row['deepcps_raw']) for row in scored])
    assert np.all(np.diff(calibrated[np.argsort(raw)]) >= -1e-6) and np.all((calibrated >= 0) & (calibrated <= 1))
    assert sha256(paths['bed']) == original_bed_sha
    receipt = dict(scope='Supplied-handoff candidate parity; engineered BED/FASTA/coverage input-path validation only',
                   python=sys.version, platform=platform.platform(), direct_infer=direct, native_stage=integrated,
                   n_windows=len(seqs), reconstructed_sequence_exact=True, reconstructed_coverage_exact=True,
                   count_intervals_unchanged=True, bed_sha256=original_bed_sha, stage_first=first, stage_second=second,
                   persisted_inline_observations=observations,
                   model=validate_model(a.handoff / 'models'),
                   fixture_inputs={name: sha256(a.handoff / 'test' / name) for name in ('example.windows.npz', 'example.windows.tsv')})
    receipt['pass'] = direct['pass'] and integrated['pass']
    (a.output / 'validation.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: v for k, v in receipt.items() if k in ('direct_infer', 'native_stage', 'pass', 'stage_first', 'stage_second')}, indent=2))
    return 0 if receipt['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
