from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyBigWig
import pysam
import pytest

from modules.deepcps import pipeline as P


def bed_row(chrom='chr1', start=200, end=210, name='peak1', strand='+', width=16):
    return [chrom, str(start), str(end), name, '4', strand, '2', '0.1', '10', 'GENE',
            '0', 'TX', '100', '110', 'UTR3', 'ENSG1'][:width]


def write_bed(path, rows):
    path.write_text(''.join('\t'.join(row) + '\n' for row in rows))


def write_track(path, *, value=3.0, chrom='chr1', length=600, gradient=False):
    with pyBigWig.open(str(path), 'w') as bw:
        bw.addHeader([(chrom, length)])
        if gradient:
            bw.addEntries([chrom] * length, list(range(length)), ends=list(range(1, length + 1)),
                          values=[float(i + 1) for i in range(length)])
        else:
            bw.addEntries([chrom], [0], ends=[length], values=[float(value)])


@pytest.fixture
def sources(tmp_path):
    fasta = tmp_path / 'genome.fa'
    fasta.write_text('>chr1\n' + 'ACGTTG' * 100 + '\n>chr2\n' + 'C' * 600 + '\n>chrM\n' + 'G' * 600 + '\n')
    pysam.faidx(str(fasta))
    sizes = tmp_path / 'chrom.sizes'
    sizes.write_text('chr1\t600\nchr2\t600\nchrM\t600\n')
    bam = tmp_path / 'cohort.bam'
    with pysam.AlignmentFile(str(bam), 'wb', header={'HD': {'VN': '1.6'}, 'SQ': [
            {'SN': chrom, 'LN': 600} for chrom in ('chr1', 'chr2', 'chrM')]}):
        pass
    plus, minus = tmp_path / 'plus.bw', tmp_path / 'minus.bw'
    write_track(plus, gradient=True)
    write_track(minus, gradient=True)
    bed = tmp_path / 'sites.bed'
    write_bed(bed, [bed_row()])
    return dict(bed=bed, fasta=fasta, bam=bam, chrom_sizes=sizes, cov_plus=plus, cov_minus=minus)


@pytest.fixture
def fake_model(tmp_path):
    model_dir = tmp_path / 'models'
    for rel in P.REQUIRED_MODELS:
        path = model_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema='scpolya.deepcps.model.v1', window_length=201,
                                       model_id='test-candidate', status='candidate-unconfirmed',
                                       files={rel: P.sha256(model_dir / rel) for rel in P.REQUIRED_MODELS})))
    return dict(model_dir=model_dir, model_manifest=manifest)


def test_window_orientation_boundaries_and_missing(sources):
    write_bed(sources['bed'], [bed_row(start=0, end=1, name='left'),
                               bed_row(start=599, end=600, name='right', strand='-'),
                               bed_row(chrom='chr2', name='missing'), bed_row(chrom='chrM', name='mt')])
    sites, width = P.read_sites(sources['bed'])
    indices, seqs, cov, reasons, lengths = P.extract_windows(sites, sources['fasta'], sources['cov_plus'], sources['cov_minus'])
    assert indices == [0, 1] and width == 16
    assert seqs[0][:100] == 'N' * 100 and seqs[0][100] == 'A'
    assert seqs[1][:100] == 'N' * 100 and seqs[1][100] == 'C'  # complement of chr1 base600 G
    np.testing.assert_array_equal(cov[0], [0] * 100 + list(range(1, 102)))
    np.testing.assert_array_equal(cov[1], [0] * 100 + list(range(600, 499, -1)))
    assert reasons == {2: 'coverage_contig_missing', 3: 'mitochondrial_outside_training_scope'}


def test_missing_zero_negative_and_reference_coverage_mismatch(sources):
    sites, _ = P.read_sites(sources['bed'])
    sources['cov_plus'].unlink()
    assert P.extract_windows(sites, sources['fasta'], sources['cov_plus'], sources['cov_minus'])[3][0] == 'coverage_file_missing'
    write_track(sources['cov_plus'], value=0)
    assert P.extract_windows(sites, sources['fasta'], sources['cov_plus'], sources['cov_minus'])[3][0] == 'zero_coverage_window'
    write_track(sources['cov_plus'], value=-2)
    with pytest.raises(ValueError, match='nonnegative'):
        P.extract_windows(sites, sources['fasta'], sources['cov_plus'], sources['cov_minus'])
    write_track(sources['cov_plus'], length=599)
    with pytest.raises(ValueError, match='coverage/FASTA'):
        P.extract_windows(sites, sources['fasta'], sources['cov_plus'], sources['cov_minus'])


def test_reference_checks_names_lengths_m5_and_pin(sources):
    ev = P.reference_evidence(sources['fasta'], sources['bam'], sources['chrom_sizes'])
    assert ev['m5_checked'] == [] and 'lack independent' in ev['base_identity_limit']
    with pytest.raises(ValueError, match='reference-sha256'):
        P.reference_evidence(sources['fasta'], sources['bam'], sources['chrom_sizes'], expected_sha256='0' * 64)
    sources['chrom_sizes'].write_text('chr1\t599\n')
    with pytest.raises(ValueError, match='chrom-sizes/FASTA'):
        P.reference_evidence(sources['fasta'], sources['bam'], sources['chrom_sizes'])
    sources['chrom_sizes'].write_text('chr1\t600\n')
    with pysam.AlignmentFile(str(sources['bam']), 'wb', header={'SQ': [{'SN': 'chr1', 'LN': 599}]}):
        pass
    with pytest.raises(ValueError, match='BAM/FASTA reference'):
        P.reference_evidence(sources['fasta'], sources['bam'], sources['chrom_sizes'])
    correct = hashlib.md5(('ACGTTG' * 100).encode()).hexdigest()
    for checksum, should_pass in ((correct, True), ('0' * 32, False)):
        with pysam.AlignmentFile(str(sources['bam']), 'wb', header={'SQ': [{'SN': 'chr1', 'LN': 600, 'M5': checksum}]}):
            pass
        if should_pass:
            assert P.reference_evidence(sources['fasta'], sources['bam'], sources['chrom_sizes'])['m5_checked'] == ['chr1']
        else:
            with pytest.raises(ValueError, match='M5 mismatch'):
                P.reference_evidence(sources['fasta'], sources['bam'], sources['chrom_sizes'])


@pytest.mark.parametrize('width', [15, 16])
def test_old_bed_join_contract_and_safe_keys(tmp_path, width):
    bed = tmp_path / 'old.bed'
    write_bed(bed, [bed_row(width=width), bed_row(name='second', strand='-', width=width)])
    original = bed.read_bytes()
    sites, actual_width = P.read_sites(bed)
    scores = P.infer_scores(sites, [], [], np.empty((0, 201)), {0: 'zero_coverage_window', 1: 'coverage_file_missing'}, {}, model_dir=None, threads=1, batch_size=1)
    joined = P.join_scores(sites, list(reversed(scores)))
    assert actual_width == width and joined[0][:width] == bed_row(width=width)
    assert joined[0][-1] == 'zero_coverage_window' and joined[0][width + 3] == 'NA'
    assert bed.read_bytes() == original
    scores[0]['strand'] = '-'
    with pytest.raises(ValueError, match='coordinate/strand'):
        P.join_scores(sites, scores)
    with pytest.raises(ValueError, match='Duplicate'):
        P.join_scores(sites, [scores[1], scores[1]])


@pytest.mark.parametrize('row', [bed_row(start=-1), bed_row(start=210), bed_row(strand='.'), bed_row(width=16) + ['extra']])
def test_invalid_bed_refused(tmp_path, row):
    bed = tmp_path / 'invalid.bed'
    write_bed(bed, [row])
    with pytest.raises(ValueError):
        P.read_sites(bed)


def test_disabled_stage_and_parser_need_no_optional_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = '''
import builtins, importlib.util
from types import SimpleNamespace
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('torch', 'pysam', 'pyBigWig', 'numpy', 'pandas'):
        raise AssertionError('optional dependency imported while disabled: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from arguments import build_parser
parser = build_parser()
assert parser.get_default('deepcps') is None  # flag belongs to run subparser
spec = importlib.util.spec_from_file_location('isolated_deepcps', 'stages/deepcps.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert m.deepcps(None, None, SimpleNamespace(deepcps=False), None) is None
from modules.deepcps.pipeline import read_sites
'''
    subprocess.run([sys.executable, '-c', code], cwd=root, check=True)


def test_cache_binds_content_and_checks_output_hashes(sources, fake_model, tmp_path, monkeypatch):
    # A counting stub isolates cache behavior; the supplied real six-network fixture is verified separately.
    calls = []
    original_infer = P.infer_scores
    def stub(sites, indices, seqs, coverage, reasons, lengths, **kwargs):
        calls.append(1)
        rows = original_infer(sites, [], [], np.empty((0, 201)), reasons, lengths, **kwargs)
        for i in indices:
            rows[i].update(deepcps_raw=.5, locate_offset=0, locate_site=sites[i]['site'])
        return rows
    monkeypatch.setattr(P, 'infer_scores', stub)
    def request():
        return P.prepare_request(**sources, **fake_model)
    first = request()
    a = P.run_annotation(request=first, output_root=tmp_path / 'out')
    assert a['status'] == 'executed'
    assert P.run_annotation(request=request(), output_root=tmp_path / 'out')['status'] == 'reused'
    assert len(calls) == 1
    Path(a['output_dir'], 'scored.tsv').write_text('corrupted')
    assert P.run_annotation(request=request(), output_root=tmp_path / 'out')['status'] == 'executed'
    assert len(calls) == 2
    # Each changed byte source at the same path changes the identity.
    write_bed(sources['bed'], [bed_row(name='peak2')])
    changed_bed = request()
    assert P.digest(changed_bed) != P.digest(first)
    write_track(sources['cov_plus'], value=5)
    changed_track = request()
    assert P.digest(changed_track) != P.digest(changed_bed)
    sources['fasta'].write_text(sources['fasta'].read_text().replace('ACGTTG', 'TCGTTG'))
    changed_fasta = request()
    assert P.digest(changed_fasta) != P.digest(changed_track)
    weight = fake_model['model_dir'] / 'score_seed1/model.pt'
    weight.write_text('changed model')
    with pytest.raises(ValueError, match='does not match manifest'):
        request()
    manifest = json.loads(fake_model['model_manifest'].read_text())
    manifest['files']['score_seed1/model.pt'] = P.sha256(weight)
    fake_model['model_manifest'].write_text(json.dumps(manifest))
    assert P.digest(request()) != P.digest(changed_fasta)


def test_all_missing_rows_never_load_torch(sources, fake_model, tmp_path, monkeypatch):
    write_bed(sources['bed'], [bed_row(chrom='chr2'), bed_row(chrom='chrM', name='mt')])
    import builtins
    original = builtins.__import__
    def guard(name, *args, **kwargs):
        if name == 'torch' or name.endswith('.model'):
            raise AssertionError('No valid windows; model should not load')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guard)
    result = P.run_annotation(request=P.prepare_request(**sources, **fake_model), output_root=tmp_path / 'out')
    with Path(result['output_dir'], 'sites.deepcps.tsv').open() as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    assert len(rows) == 2 and all(r['deepcps_raw'] == 'NA' for r in rows)
    assert rows[0]['score_reason'] == 'coverage_contig_missing'


def test_midrun_input_change_refused(sources, fake_model, tmp_path, monkeypatch):
    request = P.prepare_request(**sources, **fake_model)
    original = P.infer_scores
    def mutate(sites, indices, seqs, cov, reasons, lengths, **kwargs):
        sources['cov_plus'].unlink()
        return original(sites, [], [], np.empty((0, 201)), reasons, lengths, **kwargs)
    monkeypatch.setattr(P, 'infer_scores', mutate)
    with pytest.raises((ValueError, FileNotFoundError)):
        P.run_annotation(request=request, output_root=tmp_path / 'out')
    assert not list((tmp_path / 'out').rglob('provenance.json'))


def test_native_track_resolution_uses_scaling_dedup_and_annotation_suffix(tmp_path):
    from arguments import build_parser
    from stages.deepcps import resolve_deepcps_inputs
    from task_manage.run_layout import RunLayout
    from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_upm_factor
    parser = build_parser()
    base = ['run', '--output-dir', str(tmp_path), '--run-id', 'fixture', '--species', 'human',
            '--inputs', 'in.csv', '--gene-gtf', 'g.gtf', '--chrom-sizes', 's.txt']
    for scaling, expected in [('none', '.coverage.bw'), ('expr', '.coverage.scaled.bw'), ('polyA', '.coverage.bw'), ('both', '.coverage.scaled.bw')]:
        args = parser.parse_args(base + ['--coverage-scaling', scaling, '--dedup-method', 'unique', '--quantification-mode', 'terminal-aware'])
        args.reference_fasta, args.deepcps_model_dir = 'ref.fa', 'models'
        args.merge_distance = args.merge_distance_polyA = 0
        args.unscaled_depth_threshold = args.unscaled_depth_threshold_polyA = 1
        args.scaled_depth_threshold = args.scaled_depth_threshold_polyA = 1
        cache = {CACHE_dedup_method: 'unique', CACHE_polyA_status: 'polyA-5-0-10', CACHE_upm_factor: .2}
        layout = RunLayout(out=tmp_path, run_id='fixture')
        layout.ensure_base_dirs()
        paths, _ = resolve_deepcps_inputs(layout, args, cache)
        assert paths['cov_plus'].name == 'cohort.unique.plus' + expected
        assert paths['cov_minus'].name == 'cohort.unique.minus' + expected
        assert paths['bam'].name == 'cohort.unique.bam'
        assert paths['bed'].name.endswith('___peaks-annotated.terminal-aware.v2.bed')
        assert 'removeCDS' not in str(paths['cov_plus']) and 'polyA-' not in str(paths['cov_plus'])


def test_localisation_is_annotation_only_and_outside_reference_is_na(monkeypatch):
    class FixedModel:
        def __init__(self, *args, **kwargs):
            pass
        def score(self, seqs, cov, batch):
            return np.asarray([[.1, .2, .3]] * len(seqs), dtype=np.float32)
        def locate(self, seqs, batch):
            profiles = np.zeros((len(seqs), 201), dtype=np.float32)
            profiles[:, 0] = 1
            return profiles
        def calibrate(self, scores):
            return scores
    monkeypatch.setitem(sys.modules, 'modules.deepcps.model', SimpleNamespace(DeepCPS=FixedModel))
    sites = [dict(site_key='p', name='p', chrom='chr1', start=0, end=1, strand='+', site=1),
             dict(site_key='m', name='m', chrom='chr1', start=0, end=1, strand='-', site=1)]
    scores = P.infer_scores(sites, [0, 1], ['A' * 201] * 2, np.ones((2, 201)), {}, {'chr1': 600}, model_dir='unused', threads=1, batch_size=2)
    assert scores[0]['locate_site'] == 'NA' and scores[0]['locate_reason'] == 'prediction_outside_reference'
    assert scores[1]['locate_site'] == 101 and scores[1]['locate_offset'] == -100
    assert sites[0]['start'] == scores[0]['start'] == 0 and scores[0]['end'] == 1
    assert scores[0]['deepcps_raw'] == pytest.approx(.2)


def test_bigwig_schema_changes_only_conversion_task_identity():
    from task_manage.sc_item import ScItem
    from task_manage.task_specs import task_specs_by_key, validate_task_specs
    from task_manage.output_projection import validate_output_projection_contract
    from task_manage.pipeline_contracts import validate_pipeline_contracts
    validate_task_specs()
    validate_output_projection_contract()
    validate_pipeline_contracts()
    assert ScItem().bigwig_conversion_schema == 'validated-sorted-bedgraph-v1'
    for key, spec in task_specs_by_key().items():
        assert ('bigwig_conversion_schema' in spec.param_keys) == (key == ('call_peaks', 'generate_bigwig'))


def test_explicit_disable_and_required_enabled_inputs():
    from arguments import build_parser, _validate_deepcps_args
    parser = build_parser()
    base = ['run', '--output-dir', 'out', '--run-id', 'fixture', '--species', 'human',
            '--inputs', 'in.csv', '--gene-gtf', 'g.gtf', '--chrom-sizes', 's.txt']
    disabled = parser.parse_args(base + ['--deepcps', '--no-deepcps'])
    assert disabled.deepcps is False
    _validate_deepcps_args(parser, disabled)
    with pytest.raises(SystemExit):
        _validate_deepcps_args(parser, parser.parse_args(base + ['--deepcps']))


def test_deepcps_completion_is_persisted_with_content_identity(tmp_path):
    import sqlite3
    from task_manage import TaskManager, Plan
    tm = TaskManager(state_dir=tmp_path / 'fresh' / 'state', config={})
    tm.load_or_init(Plan(samples=[], contigs_by_sample={}))
    item = {'deepcps_fingerprint': 'f' * 64}
    def completed():
        return {'status': 'reused'}
    result = tm.run_inline_execution(stage='deepcps', task_base='score_and_join', item=item, fn=completed)
    assert result == {'status': 'reused'}
    with sqlite3.connect(tm.db_path) as connection:
        rows = connection.execute("SELECT stage, task_base, instance_json FROM inline_execution_observations_v1").fetchall()
    assert len(rows) == 1 and rows[0][:2] == ('deepcps', 'score_and_join')
    assert 'f' * 64 in rows[0][2]


def test_symlinked_fasta_with_index_beside_access_path_and_retarget_detection(sources, fake_model, tmp_path):
    # The underlying FASTA deliberately has no adjacent index; only its access
    # symlink has one. Resolving away that path would break otherwise valid input.
    target = tmp_path / 'target' / 'unindexed-reference.fa'
    target.parent.mkdir()
    sources['fasta'].replace(target)
    sources['fasta'].symlink_to(target)
    request = P.prepare_request(**sources, **fake_model)
    assert request['reference']['fasta']['path'] == str(sources['fasta'])
    assert request['reference']['fasta']['resolved_path'] == str(target)
    # All rows unscored avoids model loading while exercising full extraction/commit.
    write_bed(sources['bed'], [bed_row(chrom='chr2')])
    request = P.prepare_request(**sources, **fake_model)
    assert P.run_annotation(request=request, output_root=tmp_path / 'out')['status'] == 'executed'
    other = target.with_name('other.fa')
    other.write_bytes(target.read_bytes())
    original = request['reference']['fasta']
    sources['fasta'].unlink()
    sources['fasta'].symlink_to(other)
    assert P.file_identity(sources['fasta']) != original
