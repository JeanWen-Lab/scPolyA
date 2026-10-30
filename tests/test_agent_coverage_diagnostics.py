"""Independent hand oracle + fixed-input/lifecycle/citation/resource controls.

All datasets synthetic. No PBMC/native execution. Run in the real capped launcher.
"""
import copy
import csv
import hashlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'scpolya_agent/coverage_diagnostics.py'
spec=importlib.util.spec_from_file_location('fixed_coverage_under_test',MODULE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ORACLE=ROOT/'tests/fixtures/coverage_operation_independent_hand.json'
assert hashlib.sha256(ORACLE.read_bytes()).hexdigest()=='82f7b45863e0a32b1a95deab145bc71c8d531d7fd7ac717d30418a4fab8a842e'
HAND=json.loads(ORACLE.read_bytes())

def put(path,data):
    path.write_bytes(data);return {'path':str(path),'sha256':m.sha(data)}
def table(header,rows):
    h=io.StringIO(newline='');w=csv.DictWriter(h,fieldnames=header,delimiter='\t',lineterminator='\n');w.writeheader();w.writerows(rows);return h.getvalue().encode()
def snapshot(root):
    return {str(p.relative_to(root)):(m.sha(p.read_bytes()),p.stat().st_mtime_ns,p.stat().st_size) for p in root.rglob('*') if p.is_file()}

@pytest.fixture
def bundle(tmp_path):
    source=tmp_path/'inputs';source.mkdir()
    np.save(source/'N.npy',np.array(HAND['N'],dtype='<i8'),allow_pickle=False);np.save(source/'T.npy',np.array(HAND['T'],dtype='<i8'),allow_pickle=False)
    cells=[dict(cell_index=str(i),barcode=c,major_class=HAND['cell_groups'][i],cell_type='synthetic') for i,c in enumerate(HAND['cell_ids'])]
    genes=[];features=[]
    for j,(gene,(a,b)) in enumerate(zip(HAND['gene_ids'],HAND['gene_blocks'])):
        genes.append(dict(gene_index=str(j),terminal_gene_unit_id=gene,start=str(a),stop=str(b),K=str(b-a)))
        for k in range(a,b):features.append(dict(feature_index=str(k),terminal_gene_unit_id=gene,peak_order_in_gene=str(k-a+1 if gene!='NA' else [1,2,4][k-a]),peak_name=f'SYNTHETIC_P{k}'))
    for role,rows in [('cell-index.tsv',cells),('feature-index.tsv',features),('gene-index.tsv',genes)]:put(source/role,table(m.HEADERS[role],rows))
    request=m.make_request({role:str(source/role) for role in m.ROLES},'declared-synthetic',HAND['groups'],str(tmp_path/'attempt'))
    return request,source

def plan_ref(request,tmp_path,name='plan.json'):
    plan=m.plan_coverage(request);ref=put(tmp_path/name,m.wire(plan));return plan,ref

def completed(bundle,tmp_path):
    req,source=bundle;plan,ref=plan_ref(req,tmp_path);run=m.run_coverage(ref)
    assert run['status']=='complete',run
    return req,plan,ref,run,Path(req['attempt_dir'])

def test_hand_arithmetic_joint_pooled_missingness_and_roundtrip(bundle,tmp_path):
    req,plan,ref,run,out=completed(bundle,tmp_path)
    assert run['summary']==HAND['expected_summary']
    data=json.loads((out/'result.json').read_bytes())
    assert [(r['total_retained_gene_UMI'],r['n_genes_T_positive'],r['n_genes_with_at_least_two_positive_PAS']) for r in data['per_cell']]==[tuple(x) for x in HAND['expected_cell_triples_UMI_positive_multi']]
    for row,want in zip(data['gene_group'],HAND['expected_gene_group_rows']):
        assert (row['terminal_gene_unit_id'],row['group'])==(want['gene'],want['group'])
        for key,expected in [('pooled_PAS_counts_in_frozen_order','pooled'),('n_cells_T_positive','T_positive'),('n_cells_T_zero','T_zero'),('median_T_all_cells','median_all'),('median_T_positive_cells','median_positive'),('n_cells_with_at_least_two_positive_PAS','multi_cells'),('second_largest_pooled_PAS_count_in_group','second_largest')]:assert row[key]==want[expected]
    assert data['gene_group'][3]['pooled_PAS_counts_in_frozen_order'][-1]==0
    assert data['gene_group'][6]['n_PAS_positive_pooled_within_group']==2 and data['gene_group'][6]['n_cells_with_at_least_two_positive_PAS']==0
    assert b'0.33333333333333331' in (out/'gene-group.tsv').read_bytes()
    assert run['summary']['n_genes_at_least_two_positive_pooled_PAS_both_groups']==1
    assert [sum(r['n_PAS_positive_pooled_within_group']>=2 for r in data['gene_group'] if r['group']==g) for g in HAND['groups']]==[2,3]
    assert all('both_groups' not in key for group in run['summary']['groups'].values() for key in group)


def test_plan_deterministic_read_only_no_numeric_load(bundle,monkeypatch):
    req,source=bundle;before=snapshot(source)
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('plan loaded arrays'))
    assert m.plan_coverage(req)==m.plan_coverage(req)
    assert before==snapshot(source) and not Path(req['attempt_dir']).exists()


def test_real_runtime_envelope_and_get_no_calculation_or_writes(bundle,tmp_path,monkeypatch):
    caps=m.runtime_envelope();assert caps['controls']=={'cpu.max':'200000 100000','memory.max':'8589934592','memory.swap.max':'0'}
    req,plan,ref,run,out=completed(bundle,tmp_path);before=snapshot(tmp_path)
    monkeypatch.setattr(m,'calculate_coverage',lambda *a,**k:pytest.fail('get calculated'))
    monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('get loaded arrays'))
    response=m.get_coverage(ref,run['analysis_ref']);again=m.get_coverage(ref,run['analysis_ref'])
    assert response==again and response['fresh_analysis'] is False and response['status']=='verified_existing_result'
    assert response['execution_state']=='completed' and response['review_state']=='not_reviewed' and response['native_input_completion_state']=='not_assessed'
    assert response['data']==HAND['expected_summary'];assert snapshot(tmp_path)==before
    for citation in response['citations']:
        raw=Path(citation['path']).read_bytes();assert m.sha(raw)==citation['sha256']
        assert m.resolve_pointer(json.loads(raw),citation['json_pointer']) is not None
    assert m.get_coverage(ref,run['analysis_ref'],'gene-group')['data'][0]['terminal_gene_unit_id']=='001'
    assert m.get_coverage(ref,run['analysis_ref'],'per-cell')['data'][2]['barcode']=='NA'
    assert 'fraction_cells_T_positive' in m.get_coverage(ref,run['analysis_ref'],'field-definitions')['data']


@pytest.mark.parametrize('kind',['formula','R2','command','argv','module_path','extra_role','limits','groups','sample'])
def test_strict_request_and_caps(bundle,kind):
    req,_=bundle
    if kind=='extra_role':req['sources']['other']=req['sources']['N.npy']
    elif kind=='limits':req['limits']['cells']=513
    elif kind=='groups':req['groups']=['cohort-A','cohort-B','third']
    elif kind=='sample':req['sample_id']=''
    else:req[kind]='forbidden'
    assert m.guarded(m.plan_coverage,req)['status']=='refused'


@pytest.mark.parametrize('kind',['N_float','N_negative','N_fortran','T_mismatch','N_shape','max_count','overflow','index_duplicate','gene_partition','group_missing','source_hash','source_size','symlink'])
def test_malformed_sources_rejected_without_unsafe_sum(bundle,tmp_path,kind):
    req,source=bundle
    if kind.startswith('N_') or kind in ('max_count','overflow','T_mismatch'):
        N=np.array(HAND['N'],dtype='<i8');T=np.array(HAND['T'],dtype='<i8')
        if kind=='N_float':N=N.astype('<f8')
        elif kind=='N_negative':N[0,0]=-1
        elif kind=='N_fortran':N=np.asfortranarray(N)
        elif kind=='N_shape':N=N[:,:8]
        elif kind=='T_mismatch':T[0,0]+=1
        elif kind=='max_count':N[0,0]=10_000_001
        else:N[:]=2**62
        np.save(source/'N.npy',N,allow_pickle=False);np.save(source/'T.npy',T,allow_pickle=False)
    elif kind=='index_duplicate':
        p=source/'cell-index.tsv';raw=p.read_bytes();p.write_bytes(raw.replace(b'\t002\t',b'\t001\t'))
    elif kind=='gene_partition':
        p=source/'gene-index.tsv';p.write_bytes(p.read_bytes().replace(b'\t2\t5\t3',b'\t1\t5\t3'))
    elif kind=='group_missing':req['groups']=['cohort-A','absent']
    elif kind=='source_hash':req['sources']['N.npy']['sha256']='0'*64
    elif kind=='source_size':req['sources']['N.npy']['bytes']+=1
    else:
        p=source/'N.npy';saved=tmp_path/'saved.npy';p.rename(saved);p.symlink_to(saved)
    if kind not in ('source_hash','source_size','symlink'):
        req['sources']={role:{'path':str(source/role),'bytes':(source/role).stat().st_size,'sha256':m.sha((source/role).read_bytes())} for role in m.ROLES}
    planned=m.guarded(m.plan_coverage,req)
    if planned.get('status')=='refused':assert not Path(req['attempt_dir']).exists()
    else:
        ref=put(tmp_path/'bad-plan.json',m.wire(planned));r=m.guarded(m.run_coverage,ref)
        assert r['status']=='refused' and not (Path(req['attempt_dir'])/'result-manifest.json').exists()
        assert (Path(req['attempt_dir'])/'failure.json').exists()


@pytest.mark.parametrize('field',['producer','environment','plan_digest','groups'])
def test_plan_code_method_environment_group_staleness(bundle,tmp_path,field):
    req,_=bundle;plan,ref=plan_ref(req,tmp_path)
    if field=='producer':plan['producer']['method_sha256']='0'*64
    elif field=='environment':plan['environment']['numpy_version']='changed'
    elif field=='groups':plan['request']['groups'].reverse()
    else:plan[field]='0'*64
    ref=put(tmp_path/'stale.json',m.wire(plan));r=m.guarded(m.run_coverage,ref)
    assert r['status']=='refused' and not Path(req['attempt_dir']).exists()


def test_source_change_plan_to_run_and_get(bundle,tmp_path):
    req,source=bundle;plan,ref=plan_ref(req,tmp_path)
    (source/'N.npy').write_bytes((source/'N.npy').read_bytes()[:-1]+b'\x01')
    assert m.guarded(m.run_coverage,ref)['status']=='refused' and not Path(req['attempt_dir']).exists()


def test_existing_complete_failed_partial_nonoverwrite_and_new_retry(bundle,tmp_path,monkeypatch):
    req,source=bundle;plan,ref=plan_ref(req,tmp_path)
    original=m.run_trusted_child
    def fail(*args):raise m.Refusal('synthetic_failure','closed test failure')
    monkeypatch.setattr(m,'run_trusted_child',fail)
    assert m.guarded(m.run_coverage,ref)['status']=='refused';out=Path(req['attempt_dir']);assert (out/'attempt-start.json').exists() and (out/'failure.json').exists()
    before=snapshot(out);assert m.guarded(m.run_coverage,ref)['code']=='refused_existing_output';assert snapshot(out)==before
    monkeypatch.setattr(m,'run_trusted_child',original)
    retry=copy.deepcopy(req);retry['attempt_dir']=str(tmp_path/'retry');_,nextref=plan_ref(retry,tmp_path,'retry-plan.json')
    done=m.run_coverage(nextref);assert done['status']=='complete' and snapshot(out)==before
    before=snapshot(Path(retry['attempt_dir']));assert m.guarded(m.run_coverage,nextref)['code']=='refused_existing_output';assert snapshot(Path(retry['attempt_dir']))==before


def test_publish_interruption_preserves_partial_no_success(bundle,tmp_path,monkeypatch):
    req,_=bundle;_,ref=plan_ref(req,tmp_path);original=m._publish_pending
    def interrupted(root,name):
        if name=='result-manifest.json':raise OSError('synthetic publication fault')
        return original(root,name)
    monkeypatch.setattr(m,'_publish_pending',interrupted)
    assert m.guarded(m.run_coverage,ref)['status']=='refused'
    out=Path(req['attempt_dir']);assert not (out/'result-manifest.json').exists() and (out/'result.json').exists() and (out/'failure.json').exists()
    before=snapshot(out);assert m.guarded(m.run_coverage,ref)['code']=='refused_existing_output';assert snapshot(out)==before


def test_timeout_fixed_trusted_child_preserves_failure(bundle,tmp_path,monkeypatch):
    req,_=bundle;_,ref=plan_ref(req,tmp_path);original=m.run_trusted_child
    # Actual fixed worker process receives an intentionally tiny time budget;
    # no arbitrary executable or injected command is used.
    monkeypatch.setattr(m,'run_trusted_child',lambda p,r,b:original(p,r,.001))
    response=m.guarded(m.run_coverage,ref);assert response['code']=='worker_timeout'
    out=Path(req['attempt_dir']);assert (out/'failure.json').exists() and not (out/'result-manifest.json').exists()


@pytest.mark.parametrize('kind',['corrupt','missing','unexpected','foreign_json_valid_sha','joint','foreign_tsv_valid_sha','manifest_sha'])
def test_get_refuses_invalid_results_even_with_updated_payload_hash(bundle,tmp_path,kind):
    req,plan,ref,run,out=completed(bundle,tmp_path);manifest=json.loads((out/'result-manifest.json').read_bytes())
    if kind=='corrupt':(out/'result.json').write_bytes((out/'result.json').read_bytes()+b' ')
    elif kind=='missing':(out/'per-cell.tsv').unlink()
    elif kind=='unexpected':(out/'unlisted.json').write_text('{}')
    elif kind in ('foreign_json_valid_sha','joint'):
        obj=json.loads((out/'result.json').read_bytes())
        if kind=='foreign_json_valid_sha':obj={'schema':'wrong','irrelevant':123}
        else:obj['summary']['n_genes_at_least_two_positive_pooled_PAS_both_groups']=2
        data=m.wire(obj);(out/'result.json').write_bytes(data);manifest['files']['result.json'].update(bytes=len(data),sha256=m.sha(data))
        run['analysis_ref']=put(out/'result-manifest.json',m.wire(manifest))
    elif kind=='foreign_tsv_valid_sha':
        data=b'other\n123\n';(out/'gene-group.tsv').write_bytes(data);manifest['files']['gene-group.tsv'].update(bytes=len(data),sha256=m.sha(data));run['analysis_ref']=put(out/'result-manifest.json',m.wire(manifest))
    else:run['analysis_ref']['sha256']='0'*64
    before=snapshot(out);r=m.guarded(m.get_coverage,ref,run['analysis_ref']);assert r['status']=='refused' and snapshot(out)==before


def test_pointer_escaping_source_schema_and_defined_units(bundle,tmp_path):
    req,source=bundle;req['groups']=['A/~','B'];p=source/'cell-index.tsv';p.write_bytes(p.read_bytes().replace(b'cohort-A',b'A/~').replace(b'cohort-B',b'B'))
    req['sources']['cell-index.tsv'].update(bytes=p.stat().st_size,sha256=m.sha(p.read_bytes()))
    _,ref=plan_ref(req,tmp_path);run=m.run_coverage(ref);response=m.get_coverage(ref,run['analysis_ref'])
    assert any('/groups/A~1~0/' in c['json_pointer'] for c in response['citations'])
    assert response['field_definitions']['fraction_cells_T_positive']['unit']=='fraction_0_to_1'
    with pytest.raises(m.Refusal):m.resolve_pointer({'other':1},'/absent')
    with pytest.raises(m.Refusal):m.resolve_pointer({'x':1},'/~2')


def test_cli_and_file_import_no_inspector_logging_or_numeric_side_effects(bundle,tmp_path):
    req,_=bundle;request=put(tmp_path/'request.json',m.wire(req))
    code='import importlib.util,logging,sys; from pathlib import Path; p=Path(sys.argv[1]); before=(logging.getLogger().level,list(logging.getLogger().handlers)); s=importlib.util.spec_from_file_location("isolated_coverage",p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); assert "numpy" not in sys.modules; assert "sqlite3" not in sys.modules; assert not any(k=="scpolya_agent" or k.startswith("scpolya_agent.") for k in sys.modules); assert before==(logging.getLogger().level,list(logging.getLogger().handlers))'
    subprocess.run([sys.executable,'-B','-c',code,str(MODULE)],check=True,capture_output=True,timeout=5)
    proc=subprocess.run([sys.executable,'-B',str(MODULE),'plan','--request',request['path']],capture_output=True,timeout=5)
    assert proc.returncode==0 and proc.stderr==b'' and proc.stdout==m.wire(m.plan_coverage(req))
    ref=put(tmp_path/'plan.json',proc.stdout)
    proc=subprocess.run([sys.executable,'-B',str(MODULE),'run','--plan',ref['path'],'--plan-sha',ref['sha256']],capture_output=True,timeout=10)
    assert proc.returncode==0,proc.stdout+proc.stderr;done=json.loads(proc.stdout)
    proc=subprocess.run([sys.executable,'-B',str(MODULE),'get','--plan',ref['path'],'--plan-sha',ref['sha256'],'--manifest',done['analysis_ref']['path'],'--manifest-sha',done['analysis_ref']['sha256']],capture_output=True,timeout=5)
    assert proc.returncode==0 and proc.stderr==b'' and json.loads(proc.stdout)['fresh_analysis'] is False
    for argv in (['execute'],['plan','--request',request['path'],'--command','ls'],['--worker']):
        proc=subprocess.run([sys.executable,'-B',str(MODULE),*argv],capture_output=True,timeout=5)
        assert proc.returncode==2 and proc.stderr==b'' and json.loads(proc.stdout)['status']=='refused'

@pytest.mark.parametrize('variable',m.THREADS)
def test_bad_inherited_thread_setting_refuses_before_attempt(bundle,tmp_path,monkeypatch,variable):
    req,_=bundle;_,ref=plan_ref(req,tmp_path)
    monkeypatch.setenv(variable,'2')
    assert m.guarded(m.run_coverage,ref)['code']=='runtime_unavailable'
    assert not Path(req['attempt_dir']).exists()


def test_per_element_cap_allows_global_total_above_ten_million(bundle,tmp_path):
    req,source=bundle
    N=np.array(HAND['N'],dtype='<i8');T=np.array(HAND['T'],dtype='<i8')
    N[:,:2]=0;N[:,0]=10_000_000;T[:,0]=10_000_000
    np.save(source/'N.npy',N,allow_pickle=False);np.save(source/'T.npy',T,allow_pickle=False)
    req=m.make_request({role:str(source/role) for role in m.ROLES},req['sample_id'],req['groups'],req['attempt_dir'])
    _,ref=plan_ref(req,tmp_path);run=m.run_coverage(ref)
    assert run['summary']['global_terminal_UMI']>50_000_000


def test_parent_refuses_source_mutation_after_real_worker(bundle,tmp_path,monkeypatch):
    req,source=bundle;_,ref=plan_ref(req,tmp_path);original=m.run_trusted_child
    def change_after(*args):
        child=original(*args)
        p=source/'N.npy';raw=p.read_bytes();p.write_bytes(raw[:-1]+b'\x01')
        return child
    monkeypatch.setattr(m,'run_trusted_child',change_after)
    result=m.guarded(m.run_coverage,ref);out=Path(req['attempt_dir'])
    assert result['status']=='refused' and not (out/'result-manifest.json').exists()
    assert (out/'failure.json').exists() and (out/'.result.json.pending').exists()


def test_concurrent_fresh_runs_have_one_exclusive_winner(bundle,tmp_path):
    req,_=bundle;_,ref=plan_ref(req,tmp_path)
    argv=[sys.executable,'-B',str(MODULE),'run','--plan',ref['path'],'--plan-sha',ref['sha256']]
    processes=[subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    results=[]
    for p in processes:
        stdout,stderr=p.communicate(timeout=10);assert not stderr
        results.append(json.loads(stdout))
    assert sorted(r['status'] for r in results)==['complete','refused']
    success=next(r for r in results if r['status']=='complete')
    assert m.get_coverage(ref,success['analysis_ref'])['fresh_analysis'] is False


def test_fixed_systemd_launcher_smoke_and_definition_references(bundle,tmp_path):
    req,_=bundle;_,ref=plan_ref(req,tmp_path)
    proc=subprocess.run(['bash',str(ROOT/'scripts/fixed_coverage_limited.sh'),ref['path'],ref['sha256']],capture_output=True,timeout=15)
    put(tmp_path/'launcher.stdout.log',proc.stdout);put(tmp_path/'launcher.stderr.log',proc.stderr)
    assert proc.returncode==0,proc.stderr.decode()
    run=json.loads(proc.stdout);response=m.get_coverage(ref,run['analysis_ref'])
    manifest=json.loads(Path(run['analysis_ref']['path']).read_bytes())
    assert manifest['runtime']['controls']=={'cpu.max':'200000 100000','memory.max':'8589934592','memory.swap.max':'0'}
    for citation in response['citations']:
        d=citation['definition_ref'];raw=Path(d['path']).read_bytes()
        assert m.sha(raw)==d['sha256'] and m.resolve_pointer(json.loads(raw),d['json_pointer'])['unit']==d['unit']
    detail=m.get_coverage(ref,run['analysis_ref'],'gene-group')
    assert set(detail['citations'][0]['field_contracts'])==set(m.GG_FIELDS)
    assert detail['citations'][0]['field_contracts']['fraction_cells_T_positive']['unit']=='fraction_0_to_1'


def test_get_current_source_change_refuses_and_private_worker_cannot_resume(bundle,tmp_path):
    req,source=bundle;_,_,ref,run,out=completed(bundle,tmp_path);before=snapshot(out)
    proc=subprocess.run([sys.executable,'-B',str(MODULE),'--worker',ref['path'],ref['sha256']],capture_output=True,timeout=5)
    assert proc.returncode==2 and json.loads(proc.stdout)['status']=='refused' and before==snapshot(out)
    p=source/'T.npy';raw=p.read_bytes();p.write_bytes(raw[:-1]+b'\x01')
    assert m.guarded(m.get_coverage,ref,run['analysis_ref'])['status']=='refused' and before==snapshot(out)


@pytest.mark.parametrize('role',['N.npy','T.npy'])
@pytest.mark.parametrize('length',[10,11])
def test_real_cli_truncated_npy_v2_is_structured_read_only(bundle,tmp_path,role,length):
    req,source=bundle
    (source/role).write_bytes(b'\x93NUMPY\x02\x00'+b'\x00'*(length-8))
    req=m.make_request({r:str(source/r) for r in m.ROLES},req['sample_id'],req['groups'],req['attempt_dir'])
    ref=put(tmp_path/'request.json',m.wire(req));before=snapshot(tmp_path)
    proc=subprocess.run([sys.executable,'-B',str(MODULE),'plan','--request',ref['path']],capture_output=True,timeout=5)
    assert proc.returncode==2 and proc.stderr==b''
    assert json.loads(proc.stdout)['status']=='refused'
    assert snapshot(tmp_path)==before and not Path(req['attempt_dir']).exists()


@pytest.mark.parametrize('fault',['post_link_fsync','pending_unlink','failure_receipt_write'])
def test_visible_uncertain_manifest_truth_guard_nonoverwrite_and_retry(bundle,tmp_path,monkeypatch,fault):
    req,_=bundle;_,ref=plan_ref(req,tmp_path);out=Path(req['attempt_dir'])
    fsync=m.fsync_directory;unlink=Path.unlink;write=m.write_new;calls=0
    def bad_sync(root):
        nonlocal calls
        calls+=1
        if calls==2:raise OSError('post-link directory fsync failure')
        return fsync(root)
    def bad_unlink(p,*args,**kwargs):
        if p==out/'.result-manifest.json.pending':raise OSError('link succeeded; pending unlink failed')
        return unlink(p,*args,**kwargs)
    def bad_failure_write(root,name,data,limit):
        if name=='failure.json':raise OSError('failure receipt unavailable')
        return write(root,name,data,limit)
    with monkeypatch.context() as context:
        if fault=='pending_unlink':context.setattr(Path,'unlink',bad_unlink)
        else:context.setattr(m,'fsync_directory',bad_sync)
        if fault=='failure_receipt_write':context.setattr(m,'write_new',bad_failure_write)
        response=m.guarded(m.run_coverage,ref)
    assert response['status']=='refused' and response['code']=='publication_uncertain'
    assert (out/'result-manifest.json').exists() and (out/m.PUBLICATION_GUARD).exists()
    if fault!='failure_receipt_write':
        receipt=json.loads((out/'failure.json').read_bytes())
        assert receipt['complete_manifest_published'] is True
        assert receipt['publication_state']=='visible_durability_uncertain'
        assert receipt['manifest_directory_durability']=='uncertain'
        assert receipt['publication_stage']==('linking_final_manifest' if fault=='pending_unlink' else 'syncing_final_manifest_directory')
    else:assert not (out/'failure.json').exists()
    if fault=='pending_unlink':assert (out/'.result-manifest.json.pending').exists()
    existing={'path':str(out/'result-manifest.json'),'sha256':m.sha((out/'result-manifest.json').read_bytes())}
    before=snapshot(out)
    assert m.guarded(m.get_coverage,ref,existing)['status']=='refused'
    assert m.guarded(m.run_coverage,ref)['code']=='refused_existing_output'
    assert before==snapshot(out)
    retry=copy.deepcopy(req);retry['attempt_dir']=str(tmp_path/'retry')
    _,retryref=plan_ref(retry,tmp_path,'retry-plan.json')
    run=m.run_coverage(retryref)
    assert run['status']=='complete' and m.get_coverage(retryref,run['analysis_ref'])['fresh_analysis'] is False
    assert not (Path(retry['attempt_dir'])/m.PUBLICATION_GUARD).exists() and before==snapshot(out)


def forge_disk_handoff(req,tmp_path,name='forged-plan.json'):
    """Reproduce the reviewer's old PID/member-only bypass, using synthetic files."""
    plan,ref=plan_ref(req,tmp_path,name);out=Path(req['attempt_dir']);out.mkdir()
    _,_,before,limits=m.inputs(req)
    start=dict(schema='scpolya.fixed_coverage.attempt_start.v1',parent_pid=os.getpid(),plan_ref=ref,plan_digest=plan['plan_digest'],producer=plan['producer'],environment=plan['environment'],runtime=m.runtime_envelope(),input_before=before,attempt_dir=str(out))
    for member,data in {'attempt-start.json':m.wire(start),'plan.json':Path(ref['path']).read_bytes(),'producer.py':MODULE.read_bytes(),'method.json':m.METHOD.read_bytes(),'worker.stdout.log':b'','worker.stderr.log':b''}.items():
        m.write_new(out,member,data,limits['output_bytes_total'])
    return plan,ref,out,m.wire(start)


def private_worker_argv(ref):return [sys.executable,'-B',str(MODULE),'--worker',ref['path'],ref['sha256']]
def channel_env(fd=None,nonce=None):
    env=dict(os.environ)
    for key in m.HANDOFF_ENV:env.pop(key,None)
    if fd is not None:env[m.HANDOFF_ENV[0]]=str(fd)
    if nonce is not None:env[m.HANDOFF_ENV[1]]=nonce
    return env


def test_direct_cli_forged_disk_pid_cannot_claim_or_import_numpy(bundle,tmp_path):
    req,_=bundle;_,ref,out,_=forge_disk_handoff(req,tmp_path);before=snapshot(out)
    proc=subprocess.run(private_worker_argv(ref),env=channel_env(),capture_output=True,timeout=5)
    assert proc.returncode==2 and proc.stderr==b'' and json.loads(proc.stdout)['code']=='invalid_worker_handoff'
    # Real direct CLI import trace corroborates refusal before any numeric import.
    traced=subprocess.run([sys.executable,'-B','-v',*private_worker_argv(ref)[2:]],env=channel_env(),capture_output=True,timeout=5)
    assert traced.returncode==2 and json.loads(traced.stdout)['status']=='refused'
    assert b'numpy' not in traced.stderr and b'Traceback' not in traced.stderr
    assert before==snapshot(out) and not (out/'worker-claim.json').exists() and not list(out.glob('.*.pending'))


@pytest.mark.parametrize('fault',['bad_fd','regular_fd','write_fd','wrong_nonce','wrong_plan','wrong_start','wrong_parent','eof','duplicate_message'])
def test_private_cli_bad_fd_nonce_binding_eof_refuses(bundle,tmp_path,fault):
    req,_=bundle;plan,ref,out,start_raw=forge_disk_handoff(req,tmp_path);before=snapshot(out)
    read_fd,write_fd=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK);regular=None
    nonce='a'*64
    message=dict(schema='scpolya.fixed_coverage.handoff.v1',parent_pid=os.getpid(),plan_ref=ref,plan_digest=plan['plan_digest'],attempt_start_sha256=m.sha(start_raw),nonce=nonce)
    if fault=='wrong_nonce':message['nonce']='b'*64
    elif fault=='wrong_plan':message['plan_digest']='0'*64
    elif fault=='wrong_start':message['attempt_start_sha256']='0'*64
    elif fault=='wrong_parent':message['parent_pid']+=1
    try:
        fd=read_fd;pass_fds=(read_fd,)
        if fault=='bad_fd':fd=999999;pass_fds=()
        elif fault=='regular_fd':regular=os.open(ref['path'],os.O_RDONLY);fd=regular;pass_fds=(regular,)
        elif fault=='write_fd':fd=write_fd;pass_fds=(write_fd,)
        if fault!='eof':
            raw=m.wire(message)*(2 if fault=='duplicate_message' else 1)
            assert os.write(write_fd,raw)==len(raw)
        if fault!='write_fd':os.close(write_fd);write_fd=-1
        proc=subprocess.run(private_worker_argv(ref),env=channel_env(fd,nonce),pass_fds=pass_fds,capture_output=True,timeout=5)
    finally:
        for fd in (read_fd,write_fd,regular):
            if fd is not None and fd>=0:os.close(fd)
    assert proc.returncode==2 and proc.stderr==b'' and json.loads(proc.stdout)['status']=='refused'
    assert before==snapshot(out) and not (out/'worker-claim.json').exists() and not list(out.glob('.*.pending'))


def test_valid_parent_handoff_digest_fuse_and_consumed_pipe_replay(bundle,tmp_path,monkeypatch):
    req,_=bundle;_,ref=plan_ref(req,tmp_path)
    original_pipe=m.os.pipe2;original_send=m.send_worker_handoff;observed={}
    def capture_pipe(flags):
        r,w=original_pipe(flags);observed['duplicate']=os.dup(r);observed['fds']=(r,w);return r,w
    def capture_message(fd,message,deadline):
        observed['nonce']=message['nonce'];return original_send(fd,message,deadline)
    with monkeypatch.context() as context:
        context.setattr(m.os,'pipe2',capture_pipe);context.setattr(m,'send_worker_handoff',capture_message)
        run=m.run_coverage(ref)
    out=Path(req['attempt_dir']);claim=json.loads((out/'worker-claim.json').read_bytes())
    assert claim['handoff']=={'protocol':m.HANDOFF_PROTOCOL,'nonce_sha256':m.sha(bytes.fromhex(observed['nonce']))}
    assert claim['child_fuse']==m.worker_fuse_contract(110)
    assert all(observed['nonce'].encode() not in p.read_bytes() for p in out.iterdir() if p.is_file())
    assert m.get_coverage(ref,run['analysis_ref'])['fresh_analysis'] is False
    for fd in observed['fds']:
        with pytest.raises(OSError):os.fstat(fd)
    replay=copy.deepcopy(req);replay['attempt_dir']=str(tmp_path/'replay-attempt')
    _,replayref,replayout,_=forge_disk_handoff(replay,tmp_path,'replay-plan.json');before=snapshot(replayout)
    fd=observed['duplicate']
    try:proc=subprocess.run(private_worker_argv(replayref),env=channel_env(fd,observed['nonce']),pass_fds=(fd,),capture_output=True,timeout=5)
    finally:os.close(fd)
    assert proc.returncode==2 and proc.stderr==b'' and json.loads(proc.stdout)['status']=='refused'
    assert before==snapshot(replayout) and not (replayout/'worker-claim.json').exists()


def test_private_handoff_with_leaked_writer_is_bounded_by_plan_fuse(bundle,tmp_path):
    req,_=bundle;req['limits']['wall_seconds_max']=1
    _,ref,out,_=forge_disk_handoff(req,tmp_path);before=snapshot(out)
    read_fd,write_fd=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK);start=time.monotonic()
    try:
        proc=subprocess.run(private_worker_argv(ref),env=channel_env(read_fd,'a'*64),pass_fds=(read_fd,),capture_output=True,timeout=3)
    finally:os.close(read_fd);os.close(write_fd)
    elapsed=time.monotonic()-start
    assert elapsed<2 and before==snapshot(out) and not (out/'worker-claim.json').exists()
    # OS alarm or an earlier bounded receive refusal are both fail-closed paths.
    assert proc.returncode==-signal.SIGALRM or (proc.returncode==2 and json.loads(proc.stdout)['status']=='refused')
    assert proc.stderr==b''
    put(tmp_path/'leaked-writer-result.json',m.wire(dict(returncode=proc.returncode,elapsed=elapsed,no_claim=True,no_payloads=True)))


def test_actual_independent_os_child_fuse_default_action(tmp_path):
    # This is a test process, not a new executable/callback option in production.
    # No supervising run_trusted_child loop is involved; a blocked process dies.
    code='import importlib.util,signal,sys,time; s=importlib.util.spec_from_file_location("fuse_probe",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM}); m.arm_worker_fuse(time.monotonic(),.05); signal.pause()'
    start=time.monotonic();proc=subprocess.run([sys.executable,'-B','-c',code,str(MODULE)],capture_output=True,timeout=2);elapsed=time.monotonic()-start
    assert proc.returncode==-signal.SIGALRM and proc.stdout==proc.stderr==b'' and elapsed<1
    put(tmp_path/'independent-fuse-result.json',m.wire(dict(returncode=proc.returncode,elapsed=elapsed,budget_seconds=.05,mechanism='SIGALRM default action, inherited blocked mask explicitly unblocked')))
