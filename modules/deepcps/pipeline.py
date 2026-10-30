"""Content-bound DeepCPS annotation. Original BEDs and counting coordinates are read-only."""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
import tempfile
from contextlib import ExitStack
from pathlib import Path

BED_COLUMNS = (
    'chrom', 'start', 'end', 'name', 'max_cov', 'strand', 'tail_reads',
    'ip_score', 'total_reads', 'gene', 'distance_to_gene',
    'canonical_transcript', 'stop_codon_pos', 'distance_to_stop_codon',
    'feature', 'gene_id',
)
SCORE_COLUMNS = (
    'deepcps_s1', 'deepcps_s2', 'deepcps_s3', 'deepcps_raw', 'deepcps_cal',
    'locate_offset', 'locate_site', 'locate_conf', 'locate_reason', 'score_reason',
)
REQUIRED_MODELS = tuple(
    f'{kind}_seed{k}/model.pt' for kind in ('score', 'locate') for k in (1, 2, 3)
) + ('calibration_isotonic_deepcps.tsv', 'score_operating_points.tsv',
     'locate_confidence_thresholds.tsv')
DEFAULT_MANIFEST = Path(__file__).with_name('supplied_candidate_manifest.json')
FLANK = 100
_COMP = str.maketrans('ACGTNacgtn', 'TGCANtgcan')


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_identity(path, *, optional=False):
    # Preserve the access path: a symlinked FASTA can have its .fai beside the
    # symlink rather than beside the target. Pin the resolved target separately
    # so retargeting a symlink during inference is detected before publication.
    path = Path(path).absolute()
    identity = {'path': str(path), 'resolved_path': str(path.resolve())}
    if optional and not path.exists():
        return dict(identity, missing=True)
    return dict(identity, sha256=sha256(path), size=path.stat().st_size)


def validate_model(model_dir, manifest_path=DEFAULT_MANIFEST):
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get('schema') != 'scpolya.deepcps.model.v1' or manifest.get('window_length') != 201:
        raise ValueError('Unsupported DeepCPS model manifest/window schema')
    if not manifest.get('model_id') or not manifest.get('status'):
        raise ValueError('DeepCPS manifest requires model_id and explicit model status')
    identities = {}
    for rel in REQUIRED_MODELS:
        expected = manifest.get('files', {}).get(rel)
        if not expected:
            raise ValueError(f'DeepCPS model manifest lacks {rel}')
        identities[rel] = file_identity(Path(model_dir) / rel)
        if identities[rel]['sha256'] != expected:
            raise ValueError(f'DeepCPS model content does not match manifest: {rel}')
    return {'manifest': manifest, 'manifest_file': file_identity(manifest_path), 'files': identities}


def read_sites(bed):
    sites, seen, width = [], set(), None
    with Path(bed).open() as handle:
        for line_number, row in enumerate(csv.reader(handle, delimiter='\t'), 1):
            if not row:
                continue
            if len(row) not in (15, 16) or (width is not None and len(row) != width):
                raise ValueError(f'Expected consistent native 15/16-column BED at row {line_number}')
            width = len(row)
            chrom, start, end, name, _, strand = row[:6]
            start, end = int(start), int(end)
            if not chrom or not name or start < 0 or end <= start or strand not in ('+', '-'):
                raise ValueError(f'Invalid DeepCPS BED identity/interval at row {line_number}')
            key = digest([name, chrom, start, end, strand])
            if key in seen:
                raise ValueError(f'Duplicate DeepCPS site identity at row {line_number}')
            seen.add(key)
            sites.append(dict(site_key=key, chrom=chrom, start=start, end=end, name=name,
                              strand=strand, site=end if strand == '+' else start + 1,
                              bed_row_1based=line_number, original_fields=row))
    return sites, width or 15


def reference_evidence(fasta, bam, chrom_sizes, *, expected_sha256=None):
    """Check exact reference names/lengths; verify BAM M5 sequence digests when available.

    Header concordance without M5 cannot establish reference base identity. That
    limit and the optional user-supplied alignment-reference SHA256 are recorded.
    The caller must supply the FASTA used for alignment; aliases are never guessed.
    """
    import pysam

    if not Path(f'{fasta}.fai').is_file():
        raise ValueError('DeepCPS needs an existing FASTA .fai index; source references are never modified')
    identity = file_identity(fasta)
    if expected_sha256 and identity['sha256'] != expected_sha256.lower():
        raise ValueError('DeepCPS FASTA does not match --deepcps-reference-sha256')
    with pysam.FastaFile(str(fasta)) as fa, pysam.AlignmentFile(str(bam), 'rb') as alignment:
        header = alignment.header.to_dict()
        sq = header.get('SQ', [])
        if not sq:
            raise ValueError('DeepCPS cohort BAM has no reference dictionary')
        lengths = dict(zip(fa.references, fa.lengths))
        m5_checked = []
        for record in sq:
            chrom, length = record['SN'], record['LN']
            if lengths.get(chrom) != length:
                raise ValueError(f'DeepCPS BAM/FASTA reference mismatch: {chrom} length {length}')
            if record.get('M5'):
                checksum = hashlib.md5()
                for start in range(0, length, 1024 * 1024):
                    checksum.update(fa.fetch(chrom, start, min(start + 1024 * 1024, length)).upper().encode())
                if checksum.hexdigest().lower() != record['M5'].lower():
                    raise ValueError(f'DeepCPS BAM/FASTA M5 mismatch: {chrom}')
                m5_checked.append(chrom)
        with Path(chrom_sizes).open() as handle:
            for line in handle:
                if not line.strip():
                    continue
                chrom, length = line.split()
                if lengths.get(chrom) != int(length):
                    raise ValueError(f'DeepCPS chrom-sizes/FASTA mismatch: {chrom}')
    return dict(fasta=identity, fasta_index=file_identity(f'{fasta}.fai'),
                fasta_gzi=file_identity(f'{fasta}.gzi', optional=True),
                bam_path=str(Path(bam).resolve()), bam_reference_dictionary_sha256=digest(sq),
                chrom_sizes=file_identity(chrom_sizes), m5_checked=m5_checked,
                expected_alignment_reference_sha256=expected_sha256,
                base_identity_limit=('BAM M5 checked for all references' if len(m5_checked) == len(sq)
                                     else 'Names/lengths checked; references without BAM M5 lack independent base-identity proof'))


def extract_windows(sites, fasta, cov_plus, cov_minus):
    """Return valid row indices, 201-base sequences, coverage and per-row NA reasons."""
    import numpy as np
    import pyBigWig
    import pysam

    indices, sequences, coverage, reasons = [], [], [], {}
    with ExitStack() as stack:
        fa = stack.enter_context(pysam.FastaFile(str(fasta)))
        lengths = dict(zip(fa.references, fa.lengths))
        tracks = {}
        for strand, path in (('+', cov_plus), ('-', cov_minus)):
            if Path(path).is_file():
                track = stack.enter_context(pyBigWig.open(str(path)))
                if not track.isBigWig():
                    raise ValueError(f'DeepCPS coverage is not a bigWig: {path}')
                tracks[strand] = track
                for chrom, length in track.chroms().items():
                    if lengths.get(chrom) != length:
                        raise ValueError(f'DeepCPS coverage/FASTA reference mismatch: {chrom}')
        for i, site in enumerate(sites):
            chrom, pos, strand = site['chrom'], site['site'], site['strand']
            if chrom in ('chrM', 'MT', 'M'):
                reasons[i] = 'mitochondrial_outside_training_scope'
                continue
            if chrom not in lengths:
                reasons[i] = 'reference_contig_missing'
                continue
            if site['end'] > lengths[chrom]:
                raise ValueError(f'DeepCPS BED interval outside reference: {site["site_key"]}')
            if strand not in tracks:
                reasons[i] = 'coverage_file_missing'
                continue
            if chrom not in tracks[strand].chroms():
                reasons[i] = 'coverage_contig_missing'
                continue
            a, b = pos - 1 - FLANK, pos + FLANK
            lo, hi = max(a, 0), min(b, lengths[chrom])
            seq = 'N' * max(0, -a) + fa.fetch(chrom, lo, hi).upper() + 'N' * max(0, b - lengths[chrom])
            cov = np.zeros(201, dtype=np.float32)
            values = np.asarray(tracks[strand].values(chrom, lo, hi, numpy=True), dtype=np.float32)
            if np.isinf(values).any() or (values < 0).any():
                raise ValueError('DeepCPS requires finite nonnegative total-read coverage, not a negated display track')
            cov[lo - a:hi - a] = np.nan_to_num(values, nan=0.0)
            if cov.max() <= 0:
                reasons[i] = 'zero_coverage_window'
                continue
            if not any(base in seq for base in 'ACGT'):
                reasons[i] = 'sequence_all_unknown'
                continue
            if strand == '-':
                seq, cov = seq.translate(_COMP)[::-1], cov[::-1].copy()
            assert len(seq) == len(cov) == 201
            indices.append(i)
            sequences.append(seq)
            coverage.append(cov)
    return indices, sequences, np.asarray(coverage, dtype=np.float32).reshape(-1, 201), reasons, lengths


def infer_scores(sites, indices, sequences, coverage, reasons, lengths, *, model_dir, threads, batch_size):
    records = []
    for i, site in enumerate(sites):
        record = {key: site[key] for key in ('site_key', 'name', 'chrom', 'start', 'end', 'strand', 'site')}
        record.update({key: 'NA' for key in SCORE_COLUMNS})
        record['score_reason'] = reasons.get(i, 'scored')
        record['locate_reason'] = reasons.get(i, 'located')
        records.append(record)
    if indices:
        from .model import DeepCPS
        model = DeepCPS(model_dir, device='cpu', threads=threads)
        scores = model.score(sequences, coverage, batch=batch_size)
        locations = model.locate(sequences, batch=batch_size)
        raw = scores.mean(1)
        calibrated = model.calibrate(raw)
        for j, i in enumerate(indices):
            record = records[i]
            offset = int(locations[j].argmax()) - FLANK
            predicted = record['site'] + (offset if record['strand'] == '+' else -offset)
            for seed in (1, 2, 3):
                record[f'deepcps_s{seed}'] = float(scores[j, seed - 1])
            record.update(deepcps_raw=float(raw[j]), deepcps_cal=float(calibrated[j]),
                          locate_offset=offset, locate_conf=float(locations[j].max()))
            if 1 <= predicted <= lengths[record['chrom']]:
                record['locate_site'] = predicted
            else:
                record['locate_reason'] = 'prediction_outside_reference'
    return records


def join_scores(sites, scores):
    """Join by stable key and verify name/coordinates/strand; preserve BED row order."""
    by_key = {}
    for score in scores:
        key = score['site_key']
        if key in by_key:
            raise ValueError('Duplicate DeepCPS sidecar key')
        by_key[key] = score
    if set(by_key) != {site['site_key'] for site in sites}:
        raise ValueError('DeepCPS sidecar/BED key set mismatch')
    joined = []
    for site in sites:
        score = by_key[site['site_key']]
        for field in ('name', 'chrom', 'start', 'end', 'strand', 'site'):
            if str(score[field]) != str(site[field]):
                raise ValueError(f'DeepCPS sidecar coordinate/strand mismatch: {field}')
        joined.append(site['original_fields'] + [site['site_key'], site['bed_row_1based'], site['site']]
                      + [score[key] for key in SCORE_COLUMNS])
    return joined


def prepare_request(*, bed, fasta, bam, chrom_sizes, cov_plus, cov_minus, model_dir,
                    model_manifest=DEFAULT_MANIFEST, threads=2, batch_size=128,
                    expected_reference_sha256=None):
    if not 1 <= threads <= 2 or not 1 <= batch_size <= 512:
        raise ValueError('DeepCPS CPU threads must be 1..2; batch size must be 1..512')
    model = validate_model(model_dir, model_manifest)
    reference = reference_evidence(fasta, bam, chrom_sizes, expected_sha256=expected_reference_sha256)
    versions = {}
    for package in ('torch', 'numpy', 'pandas', 'pysam', 'pyBigWig'):
        versions[package] = importlib.metadata.version(package)
    return dict(schema='scpolya.deepcps.request.v1', bed=file_identity(bed), reference=reference,
                coverage={'+': file_identity(cov_plus, optional=True), '-': file_identity(cov_minus, optional=True)},
                model=model, threads=threads, batch_size=batch_size, dependencies=versions,
                implementation={p.name: sha256(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
                policy=dict(window_length=201, missing_scores='NA+reason', filtering=False,
                            relocation='annotation_only', count_weighting=False, coverage='same-strand-total-read'))


def cached_result(output_dir, request):
    receipt_path = Path(output_dir) / 'provenance.json'
    if not receipt_path.is_file():
        return None
    try:
        receipt = json.loads(receipt_path.read_text())
        if receipt['request'] != request or receipt['fingerprint'] != digest(request):
            return None
        for name in ('scored.tsv', 'sites.deepcps.tsv'):
            if sha256(Path(output_dir) / name) != receipt['outputs'][name]['sha256']:
                return None
        return receipt
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run_annotation(*, request, output_root):
    """Use only content-verified completed outputs; commit provenance after data files."""
    output = Path(output_root) / digest(request)
    cached = cached_result(output, request)
    if cached is not None:
        return {'status': 'reused', 'output_dir': str(output), 'fingerprint': digest(request)}
    output.mkdir(parents=True, exist_ok=True)
    ref, cov = request['reference'], request['coverage']
    sites, width = read_sites(request['bed']['path'])
    indices, seqs, coverage, reasons, lengths = extract_windows(
        sites, ref['fasta']['path'], cov['+']['path'], cov['-']['path'])
    # Loading occurs only when at least one valid window can be scored.
    models = request['model']['files']
    model_dir = Path(models['score_seed1/model.pt']['path']).parent.parent
    scores = infer_scores(sites, indices, seqs, coverage, reasons, lengths, model_dir=model_dir,
                          threads=request['threads'], batch_size=request['batch_size'])
    joined = join_scores(sites, scores)
    from collections import Counter
    receipt = dict(schema='scpolya.deepcps.provenance.v1', fingerprint=digest(request), request=request,
                   n_sites=len(sites), n_scored=len(indices), reasons=dict(Counter(row['score_reason'] for row in scores)),
                   original_bed_unchanged=True, outputs={})
    # Refuse publication if any input/model changes while inference is running.
    all_inputs = [request['bed'], ref['fasta'], ref['fasta_index'], ref['chrom_sizes'],
                  request['model']['manifest_file'], *models.values(), *cov.values(), ref['fasta_gzi']]
    for identity in all_inputs:
        if file_identity(identity['path'], optional=identity.get('missing', False)) != identity:
            raise ValueError(f'DeepCPS input changed during annotation: {identity["path"]}')
    with tempfile.TemporaryDirectory(prefix='.deepcps-', dir=output) as temp:
        temp = Path(temp)
        with (temp / 'scored.tsv').open('w') as handle:
            fields = ('site_key', 'name', 'chrom', 'start', 'end', 'strand', 'site') + SCORE_COLUMNS
            writer = csv.DictWriter(handle, fields, delimiter='\t', lineterminator='\n')
            writer.writeheader()
            writer.writerows(scores)
        with (temp / 'sites.deepcps.tsv').open('w') as handle:
            writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
            writer.writerow(BED_COLUMNS[:width] + ('site_key', 'bed_row_1based', 'site') + SCORE_COLUMNS)
            writer.writerows(joined)
        for name in ('scored.tsv', 'sites.deepcps.tsv'):
            receipt['outputs'][name] = {'sha256': sha256(temp / name), 'size': (temp / name).stat().st_size}
        (temp / 'provenance.json').write_text(json.dumps(receipt, indent=2) + '\n')
        for name in ('scored.tsv', 'sites.deepcps.tsv', 'provenance.json'):
            os.replace(temp / name, output / name)
    return {'status': 'executed', 'output_dir': str(output), 'fingerprint': digest(request)}
