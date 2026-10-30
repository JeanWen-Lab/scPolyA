"""Synthetic interface controls; no production native job or accuracy claim."""
from __future__ import annotations

import hashlib
import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pyBigWig
import pysam
import pytest

from modules.deepcps import pipeline as p
from scpolya_agent import native_deepcps as deep
from scpolya_agent import native_task_driver as drv
from scpolya_ui.command_builder import build_run_command, materialize_execution_config
from task_manage.run_config_preview import preview_rerun_from_run_configs
from test_agent_native_task_driver import (  # reuse bounded native supervisor fixtures
    CHR_LEN, _approve, _write_plan, case, gone, inline_launcher,
    only_inline_cgroup_is_known,
)


@pytest.fixture
def enabled(case):
    fasta = case['tmp'] / 'reference.fa'
    fasta.write_text('>chr1\n' + 'ACGT' * (CHR_LEN // 4) + '\n')
    pysam.faidx(str(fasta))
    models = case['tmp'] / 'models'
    for rel in p.REQUIRED_MODELS:
        path = models / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic resource: ' + rel)
    manifest = case['tmp'] / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema='scpolya.deepcps.model.v1', window_length=201,
        model_id='synthetic-interface-control', status='synthetic-unconfirmed',
        files={rel: p.sha256(models / rel) for rel in p.REQUIRED_MODELS})))
    case['request']['run'].update(deepcps=True, reference_fasta=str(fasta), deepcps_model_dir=str(models),
        deepcps_model_manifest=str(manifest), deepcps_reference_sha256=p.sha256(fasta),
        deepcps_threads=1, deepcps_batch_size=7)
    case.update(fasta=fasta, models=models, manifest=manifest)
    return case


def test_exact_materialization_of_seven_fields_and_explicit_disable(enabled):
    config, _ = drv.resolve_config(enabled['request'])
    command = build_run_command(config)
    parsed = materialize_execution_config(command.argv)
    fields = ('deepcps', 'reference_fasta', 'deepcps_model_dir', 'deepcps_model_manifest',
              'deepcps_reference_sha256', 'deepcps_threads', 'deepcps_batch_size')
    assert not command.ignored_keys
    assert {k: parsed[k] for k in fields} == {k: config[k] for k in fields}
    disabled = build_run_command(dict(config, deepcps=False))
    assert '--no-deepcps' in disabled.argv and '--deepcps' not in disabled.argv
    assert materialize_execution_config(disabled.argv)['deepcps'] is False


@pytest.mark.parametrize('update', [
    {'deepcps': 'false'}, {'deepcps': 1}, {'deepcps_threads': True}, {'deepcps_threads': 0},
    {'deepcps_threads': 3}, {'deepcps_batch_size': False}, {'deepcps_batch_size': 513},
    {'deepcps_batch_size': '4'}, {'deepcps_reference_sha256': 'x'*64},
    {'deepcps_reference_sha256': False}, {'deepcps_model_dir': ''}, {'reference_fasta': None},
])
def test_lossy_or_invalid_settings_refused(enabled, update):
    enabled['request']['run'].update(update)
    with pytest.raises(drv.DriverError) as error:
        drv.build_plan(enabled['request'], repo_root=enabled['fake'])
    assert error.value.code == 'invalid_run_config'


def test_disabled_plan_never_reads_deep_resources_or_probes_dependencies(case, monkeypatch):
    from modules.deepcps import pipeline
    monkeypatch.setattr(pipeline, 'validate_model', lambda *a, **k: pytest.fail('disabled model access'))
    case['request']['run'].update(deepcps=False, reference_fasta='/nonexistent/genome.fa',
                                deepcps_model_dir='/nonexistent/models')
    plan = drv.build_plan(case['request'], repo_root=case['fake'])
    assert plan['deepcps'] == {'enabled': False}
    assert plan['status'] == 'ready_for_external_approval'


def test_enabled_plan_pins_actual_reference_nine_resources_and_versions(enabled):
    plan = drv.build_plan(enabled['request'], repo_root=enabled['fake'])
    identity = plan['deepcps']
    assert identity['enabled'] and len(identity['model']['files']) == 9
    assert identity['model']['manifest_file']['sha256'] == p.sha256(enabled['manifest'])
    ref = identity['sample_references'][0]['evidence']
    assert ref['fasta']['sha256'] == p.sha256(enabled['fasta'])
    assert ref['fasta_index']['sha256'] == p.sha256(str(enabled['fasta']) + '.fai')
    assert ref['fasta_gzi']['missing'] is True
    assert 'lack independent' in ref['base_identity_limit']
    assert set(identity['dependencies']) == set(deep.PACKAGES)
    assert identity['threads'] == 1 and identity['batch_size'] == 7
    assert identity['architecture']['status'] == 'not_declared'
    assert plan['plan_sha256'] == drv.plan_hash(plan)


@pytest.mark.parametrize('declaration', ['valid', 'mismatch', 'malformed'])
def test_declared_architecture_is_enforced(enabled, declaration):
    manifest = json.loads(enabled['manifest'].read_text())
    manifest['architecture_source_sha256'] = (p.sha256(Path(p.__file__).with_name('model.py'))
        if declaration == 'valid' else '0'*64 if declaration == 'mismatch' else 'bad')
    enabled['manifest'].write_text(json.dumps(manifest))
    if declaration == 'valid':
        assert drv.build_plan(enabled['request'], repo_root=enabled['fake'])['deepcps']['architecture']['status'] == 'checked'
    else:
        with pytest.raises(drv.DriverError, match='architecture_source_sha256'):
            drv.build_plan(enabled['request'], repo_root=enabled['fake'])


@pytest.mark.parametrize('kind', ['fasta', 'index', 'model', 'manifest', 'wrong-sha', 'relative', 'reference-length'])
def test_invalid_actual_inputs_refused_at_plan(enabled, kind):
    if kind == 'fasta':
        enabled['fasta'].unlink()
    elif kind == 'index':
        Path(str(enabled['fasta']) + '.fai').unlink()
    elif kind == 'model':
        (enabled['models'] / p.REQUIRED_MODELS[-1]).write_text('changed calibration')
    elif kind == 'manifest':
        enabled['manifest'].write_text('{}')
    elif kind == 'wrong-sha':
        enabled['request']['run']['deepcps_reference_sha256'] = '0' * 64
    elif kind == 'relative':
        enabled['request']['run']['reference_fasta'] = 'relative.fa'
    else:
        enabled['sizes'].write_text('chr1\t49999\n')
    with pytest.raises(drv.DriverError) as error:
        drv.build_plan(enabled['request'], repo_root=enabled['fake'])
    assert error.value.code == 'invalid_deepcps'


@pytest.mark.parametrize('kind', ['fasta', 'index', 'gzi-added', 'model', 'manifest', 'dependency-version'])
def test_run_admission_refuses_deep_drift_before_attempt(enabled, monkeypatch, kind):
    plan_path = _write_plan(enabled)
    approval = _approve(plan_path)
    monkeypatch.setattr(drv, '_verify_product', lambda plan: plan['runtime_identity'])
    if kind == 'fasta':
        enabled['fasta'].write_text(enabled['fasta'].read_text().replace('ACGT', 'TCGT', 1))
    elif kind == 'index':
        Path(str(enabled['fasta']) + '.fai').write_text('bad index')
    elif kind == 'gzi-added':
        Path(str(enabled['fasta']) + '.gzi').write_text('new index')
    elif kind == 'model':
        (enabled['models'] / p.REQUIRED_MODELS[0]).write_text('changed weight')
    elif kind == 'manifest':
        content = json.loads(enabled['manifest'].read_text())
        content['model_id'] = 'changed-candidate'
        enabled['manifest'].write_text(json.dumps(content))
    else:
        original = deep.capture_identity
        def changed(*args):
            identity = original(*args)
            identity['dependencies']['torch'] = 'changed'
            return identity
        monkeypatch.setattr(deep, 'capture_identity', changed)
    with pytest.raises(drv.DriverError) as error:
        drv.run_plan(plan_path, approval, launcher=lambda *a: pytest.fail('must not launch'))
    assert error.value.code == 'deepcps_input_drift'
    assert not drv.list_attempts(json.loads(plan_path.read_text()))


def test_recover_admission_refuses_deep_model_drift(enabled):
    (enabled['fake'] / 'behaviour').write_text('fail')
    plan_path = _write_plan(enabled)
    approval = _approve(plan_path)
    drv.run_plan(plan_path, approval, launcher=inline_launcher)
    (enabled['models'] / p.REQUIRED_MODELS[1]).unlink()
    with pytest.raises(drv.DriverError) as error:
        drv.recover_plan(plan_path, approval, launcher=lambda *a: pytest.fail('must not recover'), query=gone)
    assert error.value.code == 'deepcps_input_drift'
    assert len(drv.list_attempts(json.loads(plan_path.read_text()))) == 1


def test_plan_imports_no_torch_model_in_parent(enabled):
    # Separate process so other tests cannot hide accidental eager imports.
    request_path = enabled['tmp'] / 'request.json'
    request_path.write_text(json.dumps(enabled['request']))
    code = ('import json,sys;sys.path.insert(0,sys.argv[1]);'
            'from scpolya_agent.native_task_driver import build_plan;'
            'build_plan(json.load(open(sys.argv[2])),repo_root=__import__("pathlib").Path(sys.argv[3]));'
            'assert "torch" not in sys.modules;assert "modules.deepcps.model" not in sys.modules;print("PASS")')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(Path(drv.__file__).parents[1]),
                             str(request_path), str(enabled['fake'])], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_unit_entry_automatically_seals_all_na_native_annotation(enabled):
    """Exercise worker closure, not a reader-side lazy seal."""
    main = enabled['fake']/'main.py'
    snippet = '''
if "--deepcps" in args:
    from modules.deepcps import pipeline as p
    def value(flag):
        return args[args.index(flag) + 1]
    folder = out / "final_output" / "p-synthetic" / "a-synthetic"
    folder.mkdir(parents=True)
    bed = folder / "sites.bed"
    bed.write_text("chr1\\t100\\t110\\tall-na\\t4\\t+\\t2\\t0.1\\t10\\tG\\t0\\tT\\t100\\t10\\tUTR3\\tG1\\n")
    request = p.prepare_request(bed=bed, fasta=value("--reference-fasta"),
        bam=__import__("csv").DictReader(open(value("--inputs"))).__next__()["source_path"],
        chrom_sizes=value("--chrom-sizes"), cov_plus=folder/"absent-plus.bw", cov_minus=folder/"absent-minus.bw",
        model_dir=value("--deepcps-model-dir"), model_manifest=value("--deepcps-model-manifest"),
        expected_reference_sha256=value("--deepcps-reference-sha256"),
        threads=int(value("--deepcps-threads")), batch_size=int(value("--deepcps-batch-size")))
    tm.run_inline_execution(stage="deepcps", task_base="score_and_join",
        item={"deepcps_fingerprint": p.digest(request)}, fn=p.run_annotation,
        call_kwargs=dict(request=request, output_root=folder/"deepcps"))
'''
    main.write_text(main.read_text().replace('if behaviour == "succeed":', snippet + '\nif behaviour == "succeed":'))
    plan_path = _write_plan(enabled)
    drv.run_plan(plan_path, _approve(plan_path), launcher=inline_launcher)
    status = drv.status_plan(plan_path, query=gone)
    assert status['state'] == 'succeeded_verified', status
    assert status['attempts'][0]['resources']['deepcps_result_seal']['status'] == 'sealed', status
    result = drv.deepcps_result_plan(plan_path, query=gone)
    assert (result['n_sites'], result['n_scored']) == (1, 0)
    assert result['rows'][0]['deepcps_cal'] is None
    assert result['rows'][0]['score_reason'] == 'coverage_file_missing'


@pytest.mark.parametrize('field,value', [('deepcps', True), ('reference_fasta', '/other.fa'),
    ('deepcps_model_dir', '/other'), ('deepcps_model_manifest', '/other.json'),
    ('deepcps_reference_sha256', '0'*64), ('deepcps_threads', 2), ('deepcps_batch_size', 8)])
def test_deep_preview_changes_only_deep_stage(enabled, field, value):
    config, _ = drv.resolve_config(enabled['request'])
    old = dict(config, deepcps=False) if field == 'deepcps' else config
    preview = preview_rerun_from_run_configs(old, dict(old, **{field: value}))
    tasks = {impact.key for impact in preview.rerun_preview.impacted_tasks}
    assert tasks == {('deepcps', 'score_and_join')}


@pytest.fixture
def result_case(enabled, monkeypatch, request):
    # Real native supervisor completion and real sidecar writer/inline ledger;
    # only inference is replaced by explicit synthetic probabilities.
    mode = getattr(request, 'param', '+')
    scored_strand = '-' if mode == '-' else '+'
    plan_path = _write_plan(enabled)
    drv.run_plan(plan_path, _approve(plan_path), launcher=inline_launcher)
    plan = json.loads(plan_path.read_text())
    status = drv._native_completion(Path(plan['run_root']), 'unit-case-a001')
    assert status['state'] == 'succeeded_verified'
    assert drv.status_plan(plan_path, query=gone)['state'] == 'succeeded_unverified'
    folder = Path(plan['run_root']) / 'final_output' / 'p-test' / 'a-test'
    folder.mkdir(parents=True)
    bed = folder / 'sites.bed'
    def row(chrom, strand, name):
        start, end = ('0', '10') if mode == 'boundary' and name == 'zero-score' else ('100', '110')
        return [chrom, start, end, name, '4', strand, '2', '0.1', '10', 'G', '0', 'T', '100', '10', 'UTR3', 'G1']
    bed.write_text('\n'.join('\t'.join(r) for r in [row('chr1', scored_strand, 'zero-score'),
                        row('chr1', '+' if scored_strand == '-' else '-', 'missing-coverage'), row('chrM', '+', 'mito')]) + '\n')
    coverage = folder / 'plus.bw'
    with pyBigWig.open(str(coverage), 'w') as track:
        track.addHeader([('chr1', CHR_LEN)])
        track.addEntries(['chr1'], [0], ends=[CHR_LEN], values=[1.0])
    request = p.prepare_request(bed=bed, fasta=enabled['fasta'], bam=enabled['bam'],
        chrom_sizes=enabled['sizes'], cov_plus=coverage if scored_strand == '+' else folder/'absent.bw',
        cov_minus=coverage if scored_strand == '-' else folder/'absent.bw',
        model_dir=enabled['models'], model_manifest=enabled['manifest'], threads=1, batch_size=7,
        expected_reference_sha256=enabled['request']['run']['deepcps_reference_sha256'])
    original = p.infer_scores
    def synthetic(sites, indices, sequences, cov, reasons, lengths, **kwargs):
        rows = original(sites, [], [], [], reasons, lengths, **kwargs)
        for index in indices:
            rows[index].update(deepcps_s1=0.0, deepcps_s2=0.0, deepcps_s3=0.0, deepcps_raw=0.0,
                deepcps_cal=0.0, locate_offset=0, locate_site=sites[index]['site'], locate_conf=1.0)
        return rows
    monkeypatch.setattr(p, 'infer_scores', synthetic)
    from task_manage import TaskManager
    tm = TaskManager(state_dir=Path(plan['run_root'])/'state', config={'run_id': 'r1'})
    tm.inline_execution_invocation_id = status['invocation']['invocation_id']
    result = tm.run_inline_execution(stage='deepcps', task_base='score_and_join',
        item={'deepcps_fingerprint': p.digest(request)}, fn=p.run_annotation,
        call_kwargs=dict(request=request, output_root=folder/'deepcps'))
    attempt = drv.list_attempts(plan)[-1]
    sealed = drv._seal_deepcps_result(plan, status, attempt)
    resource_path = attempt/'resources.json'
    resources = json.loads(resource_path.read_text())
    resources['deepcps_result_seal'] = sealed
    resource_path.write_text(json.dumps(resources))
    assert drv.status_plan(plan_path, query=gone)['state'] == 'succeeded_verified'
    return dict(enabled, plan_path=plan_path, plan=plan, status=status, output=Path(result['output_dir']), bed=bed)


def test_result_preserves_keys_provenance_zero_na_reasons_and_paginates(result_case):
    case = result_case
    result = drv.deepcps_result_plan(case['plan_path'], limit=2, query=gone)
    assert (result['n_sites'], result['n_scored'], result['next_offset']) == (3, 1, 2)
    assert result['rows'][0]['deepcps_cal'] == 0.0
    assert result['rows'][1]['deepcps_cal'] is None
    assert result['rows'][1]['score_reason'] == 'coverage_file_missing'
    assert result['rows'][0]['site_key'] == p.digest(['zero-score', 'chr1', 100, 110, '+'])
    assert result['verified_whole_tables'] and result['observation_id'] > 0
    assert hashlib.sha256((case['output']/'provenance.json').read_bytes()).hexdigest() == result['provenance_sha256']
    last = drv.deepcps_result_plan(case['plan_path'], offset=2, limit=1, query=gone)
    assert last['next_offset'] is None and last['rows'][0]['score_reason'] == 'mitochondrial_outside_training_scope'


@pytest.mark.parametrize('kind', ['scored', 'joined', 'bed', 'receipt', 'ledger', 'limit', 'offset', 'oversized', 'outside-root', 'rewritten-hashes', 'missing-seal'])
def test_result_rejects_tampering_missing_binding_and_bounds(result_case, monkeypatch, kind):
    case = result_case
    kwargs = {}
    if kind in ('scored', 'joined'):
        path = case['output'] / ('scored.tsv' if kind == 'scored' else 'sites.deepcps.tsv')
        path.write_text(path.read_text().replace('0.0', '0.5', 1))
    elif kind == 'bed':
        case['bed'].write_text(case['bed'].read_text().replace('zero-score', 'changed-site'))
    elif kind == 'receipt':
        path = case['output']/'provenance.json'
        receipt = json.loads(path.read_text())
        receipt['request']['model']['manifest']['model_id'] = 'other-model'
        path.write_text(json.dumps(receipt))
    elif kind == 'ledger':
        with sqlite3.connect(Path(case['plan']['run_root'])/'state/state.sqlite') as con:
            con.execute("DELETE FROM inline_execution_observations_v1 WHERE stage='deepcps'")
    elif kind == 'limit':
        kwargs['limit'] = 501
    elif kind == 'offset':
        kwargs['offset'] = -1
    elif kind == 'oversized':
        monkeypatch.setattr(deep, 'MAX_TABLE_BYTES', 10)
    elif kind == 'missing-seal':
        (drv.list_attempts(case['plan'])[-1]/'deepcps-result-seal.json').unlink()
    elif kind == 'rewritten-hashes':
        receipt_path = case['output']/'provenance.json'
        receipt = json.loads(receipt_path.read_text())
        for name in ('scored.tsv', 'sites.deepcps.tsv'):
            path = case['output']/name
            path.write_text(path.read_text().replace('0.0', '0.5'))
            receipt['outputs'][name] = dict(sha256=p.sha256(path), size=path.stat().st_size)
        receipt_path.write_text(json.dumps(receipt))
    else:
        table = case['output']/'scored.tsv'
        outside = case['tmp']/'escaped.tsv'
        table.rename(outside)
        table.symlink_to(outside)
    with pytest.raises(drv.DriverError) as error:
        drv.deepcps_result_plan(case['plan_path'], query=gone, **kwargs)
    assert error.value.code == ('deepcps_result_unavailable' if kind == 'missing-seal' else 'invalid_deepcps_result')


def test_result_refuses_disabled_or_uncompleted_plan(case, enabled):
    # The shared fixture is enabled by enabled; check before launch first.
    plan_path = _write_plan(enabled)
    with pytest.raises(drv.DriverError) as error:
        drv.deepcps_result_plan(plan_path, query=gone)
    assert error.value.code == 'deepcps_result_unavailable'
    enabled['request']['run']['deepcps'] = False
    enabled['request']['job_dir'] = str(enabled['tmp']/'disabled-job')
    disabled = _write_plan(enabled)
    with pytest.raises(drv.DriverError) as error:
        drv.deepcps_result_plan(disabled, query=gone)
    assert error.value.code == 'deepcps_disabled'


def _rewrite_consistent_result(case, site_name, updates):
    """Engineering fixture: regenerate both tables, provenance and local seal.

    Deliberately bypass semantic sealing to test the reader's row contract,
    rather than merely showing the earlier byte-drift guard works.
    """
    receipt_path = case['output']/'provenance.json'
    receipt = json.loads(receipt_path.read_text())
    for table in ('scored.tsv', 'sites.deepcps.tsv'):
        path = case['output']/table
        with path.open(newline='') as handle:
            reader = csv.DictReader(handle, delimiter='\t')
            fields, rows = reader.fieldnames, list(reader)
        next(row for row in rows if row['name'] == site_name).update(updates)
        with path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter='\t', lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        receipt['outputs'][table] = dict(sha256=p.sha256(path), size=path.stat().st_size)
    receipt_path.write_text(json.dumps(receipt))
    attempt = drv.list_attempts(case['plan'])[-1]
    seal_path = attempt/'deepcps-result-seal.json'
    seal = json.loads(seal_path.read_text())
    seal.update(provenance_sha256=p.sha256(receipt_path), outputs=receipt['outputs'])
    seal_path.write_text(json.dumps(seal))
    resources_path = attempt/'resources.json'
    resources = json.loads(resources_path.read_text())
    resources['deepcps_result_seal']['sha256'] = p.sha256(seal_path)
    resources_path.write_text(json.dumps(resources))
    assert drv.status_plan(case['plan_path'], query=gone)['state'] == 'succeeded_verified'


@pytest.mark.parametrize('name,updates', [
    ('zero-score', dict(locate_offset='NA', locate_site='NA', locate_conf='NA')),
    ('missing-coverage', dict(locate_offset='2', locate_site='101', locate_conf='NA')),
    ('zero-score', dict(locate_conf='NA')),
    ('zero-score', dict(locate_offset='101', locate_site='211')),
    ('zero-score', dict(locate_offset='-101', locate_site='9')),
    ('zero-score', dict(locate_offset='1.5')),
    ('zero-score', dict(locate_offset='2', locate_site='108')),
    ('zero-score', dict(locate_reason='coverage_file_missing')),
    ('zero-score', dict(locate_site='NA', locate_reason='located')),
    ('zero-score', dict(locate_site='110', locate_reason='prediction_outside_reference')),
    ('missing-coverage', dict(locate_reason='sequence_all_unknown')),
    ('missing-coverage', dict(locate_offset='0', locate_site='NA', locate_conf='NA')),
])
def test_consistently_sealed_malformed_localization_is_refused(result_case, name, updates):
    _rewrite_consistent_result(result_case, name, updates)
    with pytest.raises(drv.DriverError) as error:
        drv.deepcps_result_plan(result_case['plan_path'], query=gone)
    assert error.value.code == 'invalid_deepcps_result'
    # The same semantic guard also prevents worker-side seal publication.
    with pytest.raises(ValueError):
        drv._seal_deepcps_result(result_case['plan'], result_case['status'], drv.list_attempts(result_case['plan'])[-1])


@pytest.mark.parametrize('result_case', ['boundary'], indirect=True)
def test_outside_reference_keeps_confidence_offset_and_na_site(result_case, monkeypatch):
    # Place a plus-strand prediction beyond the lower boundary while retaining
    # valid native +/-100 offsets; no FASTA or model access is needed to read it.
    _rewrite_consistent_result(result_case, 'zero-score', dict(
        locate_offset='-100', locate_site='NA', locate_conf='0.9', locate_reason='prediction_outside_reference'))
    monkeypatch.setattr(p, 'reference_evidence', lambda *a, **k: pytest.fail('reader must not reopen reference'))
    result = drv.deepcps_result_plan(result_case['plan_path'], limit=1, query=gone)
    row = result['rows'][0]
    assert row['locate_offset'] == -100 and row['locate_site'] is None
    assert row['locate_conf'] == 0.9 and row['locate_reason'] == 'prediction_outside_reference'


@pytest.mark.parametrize('result_case', ['+', '-'], indirect=True)
def test_located_site_uses_native_strand_arithmetic(result_case):
    result = drv.deepcps_result_plan(result_case['plan_path'], limit=1, query=gone)
    original = result['rows'][0]
    predicted = original['site'] + (7 if original['strand'] == '+' else -7)
    _rewrite_consistent_result(result_case, 'zero-score', dict(locate_offset='7', locate_site=str(predicted)))
    row = drv.deepcps_result_plan(result_case['plan_path'], limit=1, query=gone)['rows'][0]
    assert row['locate_offset'] == 7 and row['locate_site'] == predicted and row['locate_reason'] == 'located'
