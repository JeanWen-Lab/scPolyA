"""Small independent hand-oracle checks for the versioned full envelope."""
from collections import Counter
import ast
import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

old_tests = module('old_native_fixture_for_v2', ROOT/'tests/test_agent_native_coverage.py')
old = old_tests.m
full = module('native_full_v2_under_test', ROOT/'scpolya_ui/analysis/cs1_backend/vendor_full_v2/native_coverage.py')


def bundle(tmp_path, monkeypatch):
    request, paths = old_tests.native_fixture(tmp_path)
    scratch = tmp_path/'scratch'
    scratch.mkdir()
    monkeypatch.setenv('SQLITE_TMPDIR', str(scratch))
    monkeypatch.setenv('TMPDIR', str(scratch))
    current = copy.deepcopy(request)
    current['schema'] = 'scpolya.native_fixed_coverage.request.v2'
    current['recipe'] = full.RECIPE_ID
    return request, current, paths


def aggregate(implementation, request):
    limits = implementation.validate_request(request)
    sources = implementation.Sources(request, limits)
    cfg, binding, _ = implementation.bind_native(request, sources)
    _, cells, registry, _ = implementation.read_cohort_registry(request, sources, limits)
    catalogue, features, genes, _ = implementation.parse_catalogue(request, sources, cfg, limits)
    N, T, accounting = implementation.build_counts(request, sources, cells, registry, catalogue, features, genes, limits)
    c = implementation.c
    meta = dict(cells=cells, features=c.tsv_rows(implementation.table_bytes(features, c.HEADERS['feature-index.tsv']), c.HEADERS['feature-index.tsv']),
                genes=c.tsv_rows(implementation.table_bytes(genes, c.HEADERS['gene-index.tsv']), c.HEADERS['gene-index.tsv']),
                groups=request['groups'], group_counts=dict(Counter(row['major_class'] for row in cells)))
    result = c.calculate_coverage(N, T, meta, c.CAPS)
    return N, T, accounting, result


def test_full_v2_exact_small_oracle_and_v1_arithmetic(tmp_path, monkeypatch):
    original, current, _ = bundle(tmp_path, monkeypatch)
    expected = aggregate(old, original)
    observed = aggregate(full, current)
    assert observed[:3] == expected[:3]
    assert observed[0] == old_tests.HAND['expected_N']
    assert observed[1] == old_tests.HAND['expected_T']
    observed[3]['schema'] = expected[3]['schema']
    assert observed[3] == expected[3]
    assert any(row['median_T_positive_cells'] is None for row in observed[3]['gene_group'])
    assert any(row['n_cells_T_zero'] > 0 for row in observed[3]['gene_group'])


def test_explicit_v2_bounds_leave_v1_rejection_unchanged(tmp_path, monkeypatch):
    original, current, _ = bundle(tmp_path, monkeypatch)
    units = [dict(gene_id='synthetic'+str(i), strand='+') for i in range(65)]
    original['selected_units'] = current['selected_units'] = units
    with pytest.raises(old.Refusal):
        old.validate_request(original)
    assert full.validate_request(current)['genes'] == 1024
    current['selected_units'] = [dict(gene_id='synthetic'+str(i), strand='+') for i in range(1025)]
    with pytest.raises(full.Refusal):
        full.validate_request(current)
    assert old.CAPS['genes'] == 64 and old.CAPS['cells'] == 512
    assert old.CAPS['source_bytes_each'] == 33554432


def test_new_count_duplicate_detector_keeps_excluded_rows(tmp_path, monkeypatch):
    _, current, paths = bundle(tmp_path, monkeypatch)
    old_tests.rewrite_table(paths['counts'], lambda rows, header: rows.append(dict(rows[0])))
    old_tests.refresh(current, paths)
    with pytest.raises(full.Refusal) as error:
        aggregate(full, current)
    assert error.value.code == 'duplicate_count_pair'


def test_full_registry_duplicate_is_refused(tmp_path, monkeypatch):
    _, current, paths = bundle(tmp_path, monkeypatch)
    data = paths['registry'].read_bytes()
    paths['registry'].write_bytes(data+data.splitlines(keepends=True)[0])
    old_tests.refresh(current, paths)
    with pytest.raises(full.Refusal, match='unique one-column registry'):
        aggregate(full, current)


def test_source_tamper_and_count_element_overflow_refuse(tmp_path, monkeypatch):
    _, current, paths = bundle(tmp_path, monkeypatch)
    old_tests.rewrite_table(paths['counts'], lambda rows, header: rows[0].update(umi_count='10000001'))
    old_tests.refresh(current, paths)
    with pytest.raises(full.Refusal, match='nonnegative exact bounded integer'):
        aggregate(full, current)


def test_frozen_scientific_function_spans_are_identical():
    for filename, names in [('coverage_diagnostics.py', ['_median','calculate_coverage','verify_result']),
                            ('native_coverage.py', ['native_payloads','binding_from_config','bind_native','read_cohort_registry','catalogue_semantics','parse_catalogue','build_counts'])]:
        original = (ROOT/'scpolya_agent'/filename).read_text()
        derived = (ROOT/'scpolya_ui/analysis/cs1_backend/vendor_full_v2'/filename).read_text()
        def spans(raw):
            tree = ast.parse(raw)
            return {node.name: ast.get_source_segment(raw,node) for node in tree.body if isinstance(node,ast.FunctionDef)}
        left, right = spans(original), spans(derived)
        for name in names:
            assert re.sub(r'(scpolya\.(?:native_fixed_coverage|fixed_coverage)\.[a-z_]+)\.v2', r'\1.v1', right[name]) == left[name]


def test_full_integer_proofs_and_pinned_producer_closure():
    assert full.CAPS['cells']*full.CAPS['PAS']*full.CAPS['N_element_max'] < 2**53-1 < 2**63-1
    assert full.CAPS['count_rows']*full.CAPS['N_element_max'] < 2**53-1
    assert full.producer()[0]['files']['coverage.py']['sha256'] == full.COVERAGE_SHA
    assert full.c.producer()[1]['caps'] == full.c.CAPS


def gui_request(tmp_path, annotation='terminal-annotation-v3'):
    req, paths = old_tests.native_fixture(tmp_path, annotation)
    payload = json.loads(paths['config'].read_text())
    payload['resolved_config'].update(gene_gtf=str(tmp_path/'fixture.gtf'),chrom_sizes=str(tmp_path/'chrom.sizes'),species='human',summarize_matrix=True,count_matrix=True)
    payload['config_hash']=old_tests.hashlib.sha256(old_tests.dump(payload['resolved_config'])).hexdigest()
    payload.update(invocation_id='53333333123442348234123456789012',status='succeeded',started_at='2026-10-26T00:00:00+00:00',finished_at='2026-10-26T00:00:01+00:00',duration_seconds=1,pid=123,argv=['synthetic'],error='',paths={})
    original_metadata=paths['config']
    paths['config']=original_metadata.parent.parent/'20261026T000000.000000Z-53333333/invocation.json'
    paths['config'].parent.mkdir()
    original_metadata.unlink()
    paths['config'].write_text(json.dumps(payload))
    from scpolya_ui.run_records import RunRecord, config_hash
    from scpolya_ui.services import run_evidence_service as evidence
    cfg=payload['resolved_config']
    record=RunRecord(id='R-new-full-fixture',kind='cli_invocation',status='succeeded',workspace_id='W-new',workspace_root=req['run_root'],label='new fixture',config=cfg,config_hash=config_hash(cfg),created_at='',updated_at='',run_id=cfg['run_id'],root_path=req['run_root'],invocation_id=payload['invocation_id'],execution_config=cfg,execution_config_hash=config_hash(cfg))
    _,binding=evidence.discover(record)
    selected=tmp_path/'selected_units.tsv'
    selected.write_text('gene_id\tstrand\n'+''.join(r['gene_id']+'\t'+r['strand']+'\n' for r in req['selected_units']))
    return dict(method='cs1.fixed_coverage_full',runs=[binding],sample_id=req['sample_id'],cohort=str(paths['cohort']),registry=str(paths['registry']),selected_units=str(selected),groups=req['groups'])


def test_real_bound_gui_plan_is_metadata_only_and_pins_registry(tmp_path, monkeypatch):
    from scpolya_ui.analysis.cs1_backend import backend
    request=gui_request(tmp_path)
    planned=backend.plan(request)
    assert planned['status']=='ready' and planned['scope']['selected_units']==len(old_tests.HAND['selected_gene_units'])
    assert planned['scope']['source_barcode_count']=='not_inferred'
    assert planned['request']['runs'][0]['run_record_id']=='R-new-full-fixture'
    assert {i['role'] for i in planned['input_sources']} >= {'registry','cohort','selected_units','counts','catalogue'}
    assert not (tmp_path/'prepared').exists()
    registry=Path(request['registry']);registry.write_bytes(registry.read_bytes()+b'NEW\n')
    with pytest.raises(ValueError,match='Source changed|budget exceeded'):
        backend.execute(planned,tmp_path/'refused-analysis')
    refusal=json.loads((tmp_path/'refused-analysis/REFUSAL.json').read_text())
    assert refusal['status']=='failed'
    assert not (tmp_path/'refused-analysis/prepared').exists()


@pytest.mark.parametrize('limit,equal', [('cells',4),('genes',3),('PAS',6),('count_rows',9),('catalogue_rows',9)])
def test_reduced_dimension_and_row_boundaries_equal_then_plus_one(tmp_path,monkeypatch,limit,equal):
    _,current,_=bundle(tmp_path,monkeypatch)
    current['limits'][limit]=equal
    aggregate(full,current)
    current['limits'][limit]=equal-1
    with pytest.raises(full.Refusal):aggregate(full,current)


@pytest.mark.parametrize('peak,cell', [('p_other_gene','001'),('p0','z_outside'),('p1','001')])
def test_retained_and_excluded_duplicate_codes_match_v1(tmp_path,monkeypatch,peak,cell):
    original,current,paths=bundle(tmp_path,monkeypatch)
    old_tests.rewrite_table(paths['counts'],lambda rows,header:rows.append(dict(next(r for r in rows if r['peak_name']==peak and r['cell_barcode']==cell))))
    old_tests.refresh(current,paths);old_tests.refresh(original,paths)
    codes=[]
    for implementation,request in [(old,original),(full,current)]:
        with pytest.raises(implementation.Refusal) as error:aggregate(implementation,request)
        codes.append(error.value.code)
    assert codes==['duplicate_count_pair','duplicate_count_pair']


def test_exact_T_cap_then_overflow_refused(tmp_path,monkeypatch):
    _,current,paths=bundle(tmp_path,monkeypatch)
    def change(rows,header):
        for row in rows:
            if row['cell_barcode']=='001' and row['peak_name']=='p0':row['umi_count']='9999997'
    old_tests.rewrite_table(paths['counts'],change);old_tests.refresh(current,paths)
    assert aggregate(full,current)[1][0][0]==10_000_000
    def increase(rows,header):
        for row in rows:
            if row['cell_barcode']=='001' and row['peak_name']=='p0':row['umi_count']='9999998'
    old_tests.rewrite_table(paths['counts'],increase);old_tests.refresh(current,paths)
    with pytest.raises(full.Refusal,match='T per-element cap'):aggregate(full,current)


def test_zero_counts_and_no_observation_remain_distinct_in_accounting(tmp_path,monkeypatch):
    _,current,paths=bundle(tmp_path,monkeypatch)
    old_tests.rewrite_table(paths['counts'],lambda rows,header:[row.update(umi_count='0') for row in rows])
    old_tests.refresh(current,paths)
    N,T,account,result=aggregate(full,current)
    assert not any(map(any,N)) and not any(map(any,T))
    assert account['retained_UMI']==0 and account['selected_cells_without_source_rows']==['NA']
    assert account['selected_cells_zero_after_eligible_selection']==['001','NA','c3','c4']
    assert all(r['median_T_positive_cells'] is None for r in result['gene_group'])


def test_source_byte_reduction_and_single_file_exact_bound(tmp_path,monkeypatch):
    _,current,paths=bundle(tmp_path,monkeypatch)
    maximum=max(d['bytes'] for d in current['sources'].values())
    current['limits']['source_bytes_each']=maximum
    full.validate_request(current)
    current['limits']['source_bytes_each']=maximum-1
    with pytest.raises(full.Refusal):full.validate_request(current)
    directory=tmp_path/'payload';directory.mkdir()
    full.c.write_new(directory,'exact',b'1234',4)
    assert full.c.read_file(directory/'exact',4)[0]==b'1234'
    with pytest.raises(full.Refusal):full.c.write_new(directory,'over',b'12345',4)


def test_original_v1_source_and_contract_pins_unchanged():
    source=ROOT/'scpolya_ui/analysis/cs1_backend/vendor_full_v2/SOURCE-INDEX.json'
    import hashlib
    for item in json.loads(source.read_text()):
        derived=source.parent/item['derived_path']
        assert hashlib.sha256(derived.read_bytes()).hexdigest()==item['derived_sha256']
    assert full.RECIPE_ID==old.RECIPE_ID and full.PROJECTION_RULE==old.PROJECTION_RULE
    assert full.PROJECTION_DIGEST==old.PROJECTION_DIGEST


def test_selection_receipt_label_pin_is_in_plan_and_not_old_answer(tmp_path):
    from scpolya_ui.analysis.cs1_backend import backend
    request=gui_request(tmp_path)
    label=tmp_path/'labels.tsv';label.write_text('barcode\tmajor_class\n001\tA\n')
    import hashlib
    def descriptor(path):return dict(path=str(path),bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    receipt=tmp_path/'selection.json'
    value=dict(schema='scpolya.gui.cs1.selection_preparation.v1',label_source=descriptor(label),selected_units=3,selected_cells=4,
               structural_eligible_units=4,excluded_unit_ids=['excluded|+'],selection_rule='fixed prior positive-support selection',
               files=[descriptor(Path(request[k])) for k in ['cohort','registry','selected_units']],
               unit_selection_authority=dict(path='/not/read/previous-answer.json',sha256='f'*64))
    receipt.write_text(json.dumps(value));request['selection_receipt']=str(receipt)
    planned=backend.plan(request)
    assert {'label_source','selection_receipt'} <= {p['role'] for p in planned['input_sources']}
    assert all(p['path']!='/not/read/previous-answer.json' for p in planned['input_sources'])
    assert planned['parameters']['selection_provenance']['excluded_unit_ids']==['excluded|+']
    assert planned['scope']['cohort_cells']==4
    label.write_text('changed')
    with pytest.raises(ValueError,match='Label source differs'):backend.plan(request)


def test_v4_run_is_reported_unsupported_not_planned(tmp_path):
    from scpolya_ui.analysis.cs1_backend import backend
    request=gui_request(tmp_path, 'terminal-annotation-v4')
    with pytest.raises(ValueError, match=r'Not supported \(unsupported_schema\).*terminal-annotation-v4'):
        backend.plan(request)
    assert not (tmp_path/'prepared').exists()
