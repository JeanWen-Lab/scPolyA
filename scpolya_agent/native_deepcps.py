"""Admission identities and bounded reading for the existing DeepCPS producer.

No inference implementation lives here. Importing this module loads only stdlib.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import subprocess
from collections import Counter
from itertools import zip_longest
from pathlib import Path

MAX_TABLE_BYTES = 64 << 20
MAX_PROVENANCE_BYTES = 1 << 20
MAX_RESULT_ROWS = 500
PACKAGES = ('torch', 'numpy', 'pandas', 'pysam', 'pyBigWig')


def capture_identity(config, samples, python):
    """Validate actual resources with native helpers, without importing torch."""
    if not config.get('deepcps', False):
        return {'enabled': False}
    from modules.deepcps import pipeline as p

    for key in ('reference_fasta', 'deepcps_model_dir', 'deepcps_model_manifest'):
        value = config.get(key)
        if value is not None and (not isinstance(value, str) or not Path(value).is_absolute()):
            raise ValueError(f'{key} must be an absolute path')
    model = p.validate_model(config['deepcps_model_dir'], config.get('deepcps_model_manifest') or p.DEFAULT_MANIFEST)
    architecture_hash = p.sha256(Path(p.__file__).with_name('model.py'))
    declared = model['manifest'].get('architecture_source_sha256')
    if declared is not None and (not isinstance(declared, str) or not re.fullmatch('[a-fA-F0-9]{64}', declared)
                                 or declared.lower() != architecture_hash):
        raise ValueError('DeepCPS manifest architecture_source_sha256 differs from actual model.py or is malformed')
    architecture = dict(status='checked' if declared is not None else 'not_declared',
                        declared_sha256=declared, actual_sha256=architecture_hash)
    # The producer checks the generated cohort BAM too. Here each original BAM
    # is checked before any execution is admitted; no aliases or indexing writes.
    references = [dict(sample_id=s['sample_id'], evidence=p.reference_evidence(
        config['reference_fasta'], s['bam']['path'], config['chrom_sizes'],
        expected_sha256=config.get('deepcps_reference_sha256'))) for s in samples]
    probe = subprocess.run(
        [python, '-I', '-B', '-c',
         'import importlib.metadata as m,json;print(json.dumps({p:m.version(p) for p in ' + repr(PACKAGES) + '}))'],
        capture_output=True, text=True, timeout=60,
    )
    if probe.returncode:
        raise ValueError('DeepCPS dependency version probe failed: ' + probe.stderr[-300:])
    return dict(enabled=True, schema='scpolya.native_deepcps_identity.v1', model=model, architecture=architecture,
                sample_references=references, dependencies=json.loads(probe.stdout),
                threads=config['deepcps_threads'], batch_size=config['deepcps_batch_size'],
                implementation={f.name: p.sha256(f) for f in sorted(Path(p.__file__).parent.glob('*.py'))},
                scope='Supplied candidate annotation; formal manuscript model identity is unresolved. '
                      'Plan checks source BAM headers; native stage separately checks generated cohort. '
                      'Version records do not hash installed dependency code.')


def _bounded_bytes(path, maximum, root):
    path = Path(path)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f'result path escapes run root or is absent: {path}')
    with path.open('rb') as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f'result exceeds {maximum} byte limit: {path}')
    return data


def _table(path, expected, root):
    data = _bounded_bytes(path, MAX_TABLE_BYTES, root)
    if len(data) != expected['size'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
        raise ValueError(f'result hash/size mismatch: {path}')
    return csv.reader(io.StringIO(data.decode('utf-8')), delimiter='\t')


def _fingerprint_for_invocation(root, invocation_id):
    db = root / 'state' / 'state.sqlite'
    with sqlite3.connect(f'{db.resolve().as_uri()}?mode=ro', uri=True) as con:
        con.execute('PRAGMA query_only=ON')
        rows = con.execute(
            "SELECT observation_id, substr(instance_json, 1, ?) FROM inline_execution_observations_v1 "
            "WHERE invocation_id=? AND stage='deepcps' AND task_base='score_and_join' AND status='completed' "
            'ORDER BY observation_id DESC LIMIT 2', (MAX_PROVENANCE_BYTES + 1, invocation_id),
        ).fetchall()
    if len(rows) != 1 or len(rows[0][1]) > MAX_PROVENANCE_BYTES:
        raise ValueError('expected one bounded DeepCPS completion observation for own invocation')
    payload = json.loads(rows[0][1])
    identity = {field['key']: field['value'] for field in payload['identity']}
    fingerprint = identity['deepcps_fingerprint']
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise ValueError('invalid observed DeepCPS fingerprint')
    return fingerprint, rows[0][0]


def _provenance_path(root, fingerprint):
    # Native RunLayout puts annotated outputs two named levels below final_output.
    # Bound traversal too; never recursively scan unrelated output trees.
    candidates, visited = [], 0
    final = root / 'final_output'
    if final.is_dir():
        for peak in final.iterdir():
            visited += 1
            if visited > 256:
                raise ValueError('too many output directories for bounded result discovery')
            if not peak.is_dir() or peak.is_symlink():
                continue
            for annotation in peak.iterdir():
                visited += 1
                if visited > 256:
                    raise ValueError('too many output directories for bounded result discovery')
                path = annotation / 'deepcps' / fingerprint / 'provenance.json'
                if annotation.is_dir() and not annotation.is_symlink() and path.is_file():
                    candidates.append(path)
    if len(candidates) != 1:
        raise ValueError('expected exactly one observed DeepCPS provenance under run root')
    return candidates[0]


def read_result(plan, status, *, offset=0, limit=100):
    """Verify native provenance, whole tables and original BED; return a page."""
    from modules.deepcps import pipeline as p

    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_RESULT_ROWS:
        raise ValueError(f'offset must be nonnegative; limit must be 1..{MAX_RESULT_ROWS}')
    root = Path(plan['run_root'])
    admitted = plan['deepcps']
    fingerprint, observation_id = _fingerprint_for_invocation(root, status['invocation']['invocation_id'])
    receipt_path = _provenance_path(root, fingerprint)
    receipt_bytes = _bounded_bytes(receipt_path, MAX_PROVENANCE_BYTES, root)
    receipt = json.loads(receipt_bytes)
    request = receipt['request']
    if (receipt['schema'] != 'scpolya.deepcps.provenance.v1' or receipt['fingerprint'] != fingerprint
            or p.digest(request) != fingerprint or not receipt['original_bed_unchanged']):
        raise ValueError('DeepCPS provenance/fingerprint mismatch')
    if request['model'] != admitted['model'] or request['dependencies'] != admitted['dependencies']:
        raise ValueError('DeepCPS provenance model/dependencies differ from admitted plan')
    for key in ('threads', 'batch_size', 'implementation'):
        if request[key] != admitted[key]:
            raise ValueError(f'DeepCPS provenance {key} differs from admitted plan')
    ref = admitted['sample_references'][0]['evidence']
    for key in ('fasta', 'fasta_index', 'fasta_gzi', 'expected_alignment_reference_sha256'):
        if request['reference'][key] != ref[key]:
            raise ValueError(f'DeepCPS provenance reference {key} differs from admitted plan')
    expected_policy = dict(window_length=201, missing_scores='NA+reason', filtering=False,
                           relocation='annotation_only', count_weighting=False, coverage='same-strand-total-read')
    if request['policy'] != expected_policy:
        raise ValueError('unexpected DeepCPS scientific policy')
    bed_data = _bounded_bytes(request['bed']['path'], MAX_TABLE_BYTES, root)
    if hashlib.sha256(bed_data).hexdigest() != request['bed']['sha256']:
        raise ValueError('original BED hash mismatch')
    scores = _table(receipt_path.parent / 'scored.tsv', receipt['outputs']['scored.tsv'], root)
    joined = _table(receipt_path.parent / 'sites.deepcps.tsv', receipt['outputs']['sites.deepcps.tsv'], root)
    score_header = ['site_key', 'name', 'chrom', 'start', 'end', 'strand', 'site', *p.SCORE_COLUMNS]
    if next(scores) != score_header:
        raise ValueError('unexpected DeepCPS score table header')
    joined_header = next(joined)
    width = len(joined_header) - 3 - len(p.SCORE_COLUMNS)
    if width not in (15, 16) or joined_header != [*p.BED_COLUMNS[:width], 'site_key', 'bed_row_1based', 'site', *p.SCORE_COLUMNS]:
        raise ValueError('unexpected DeepCPS joined table header')
    bed = ((line, row) for line, row in enumerate(csv.reader(io.StringIO(bed_data.decode('utf-8')), delimiter='\t'), 1) if row)
    seen, reasons, page, count, scored = set(), Counter(), [], 0, 0
    integer_keys = {'start', 'end', 'site', 'locate_offset', 'locate_site'}
    float_keys = {'deepcps_s1', 'deepcps_s2', 'deepcps_s3', 'deepcps_raw', 'deepcps_cal', 'locate_conf'}
    for native, values, join in zip_longest(bed, scores, joined):
        if native is None or values is None or join is None or len(values) != len(score_header):
            raise ValueError('DeepCPS BED/score/join row count or width mismatch')
        line, original = native
        if len(original) != width:
            raise ValueError('DeepCPS original BED width mismatch')
        row = dict(zip(score_header, values))
        chrom, start, end, name, _, strand = original[:6]
        start, end = int(start), int(end)
        site = end if strand == '+' else start + 1
        key = p.digest([name, chrom, start, end, strand])
        if key in seen or [row[k] for k in score_header[:7]] != [key, name, chrom, str(start), str(end), strand, str(site)]:
            raise ValueError('DeepCPS duplicate stable key or BED coordinate mismatch')
        if join != original + [key, str(line), str(site)] + [row[k] for k in p.SCORE_COLUMNS]:
            raise ValueError('DeepCPS joined values/order differ from original BED and scores')
        seen.add(key)
        reasons[row['score_reason']] += 1
        for k in integer_keys | float_keys:
            value = row[k]
            if value == 'NA':
                row[k] = None
            elif k in integer_keys:
                row[k] = int(value)
            else:
                row[k] = float(value)
                if not math.isfinite(row[k]) or not 0 <= row[k] <= 1:
                    raise ValueError(f'invalid DeepCPS probability: {k}')
        has_scores = all(row[k] is not None for k in float_keys - {'locate_conf'})
        if has_scores != (row['score_reason'] == 'scored') or (not has_scores and any(row[k] is not None for k in float_keys)):
            raise ValueError('DeepCPS NA/score reason inconsistency')
        # Mirror infer_scores' localization/NA contract without re-running
        # inference or opening the external reference. The native provenance
        # records no chromosome lengths, so an outside-reference reason is
        # checked for field consistency rather than re-proving its boundary.
        if has_scores:
            shift, located = row['locate_offset'], row['locate_site']
            if row['locate_conf'] is None or shift is None or not -p.FLANK <= shift <= p.FLANK:
                raise ValueError('DeepCPS scored row requires confidence and an in-window localization offset')
            predicted = row['site'] + (shift if row['strand'] == '+' else -shift)
            if located is None:
                if row['locate_reason'] != 'prediction_outside_reference':
                    raise ValueError('DeepCPS NA localization site requires prediction_outside_reference')
            elif row['locate_reason'] != 'located' or located < 1 or located != predicted:
                raise ValueError('DeepCPS localization reason/site/strand arithmetic mismatch')
        elif (any(row[k] is not None for k in ('locate_offset', 'locate_site', 'locate_conf'))
              or row['locate_reason'] != row['score_reason'] or row['score_reason'] not in {
                  'mitochondrial_outside_training_scope', 'reference_contig_missing', 'coverage_file_missing',
                  'coverage_contig_missing', 'zero_coverage_window', 'sequence_all_unknown',
              }):
            raise ValueError('DeepCPS unscored row requires NA localization and matching native reasons')
        scored += has_scores
        if offset <= count < offset + limit:
            page.append(dict(row, bed_row_1based=line))
        count += 1
    if count != receipt['n_sites'] or scored != receipt['n_scored'] or dict(reasons) != receipt['reasons']:
        raise ValueError('DeepCPS provenance totals/reasons mismatch')
    return dict(schema='scpolya.native_deepcps_result.v1', status='observed', plan_sha256=plan['plan_sha256'],
                run_root=str(root), invocation=status['invocation'], observation_id=observation_id,
                fingerprint=fingerprint, provenance_path=str(receipt_path),
                provenance_sha256=hashlib.sha256(receipt_bytes).hexdigest(), provenance=receipt,
                n_sites=count, n_scored=scored, reasons=dict(reasons), rows=page,
                offset=offset, limit=limit, next_offset=offset + len(page) if offset + len(page) < count else None,
                verified_whole_tables=True, na_representation='null with unchanged explicit reason; numeric zero is zero',
                limitations=['Completion observation records stage completion, not fresh inference versus verified reuse.',
                             'Scores annotate; counts and counting intervals are unchanged.',
                             'Formal manuscript model identity remains unresolved.',
                             'Readonly byte verification is not protection against concurrent malicious filesystem mutation.'])
