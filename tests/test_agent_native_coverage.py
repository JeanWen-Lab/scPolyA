"""Frozen independent raw native hand oracle; synthetic only, no native/PBMC runs."""
import copy
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
MODULE=ROOT/'scpolya_agent/native_coverage.py'
spec=importlib.util.spec_from_file_location('native_fixed_under_test',MODULE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
c=m.c
ORACLE=ROOT/'tests/fixtures/native_coverage_independent_hand.json'
assert hashlib.sha256(ORACLE.read_bytes()).hexdigest()=='31fb010069d73fbd992e12e047fdda6c8cdae5c0aeb4812738ccf85a798f67c5'
HAND=json.loads(ORACLE.read_bytes())

def dump(value):return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()
def put(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())
def table(header,rows,delimiter='\t'):
    out=io.StringIO(newline='');w=csv.DictWriter(out,fieldnames=header,delimiter=delimiter,lineterminator='\n');w.writeheader();w.writerows(rows);return out.getvalue().encode()
def scoped(scope,payload):
    digest=hashlib.sha256(dump(dict(schema_version='scpolya.artifact_name.v1',scope=scope,payload=payload))).hexdigest()
    return dict(scope=scope,code={'peak':'pk','annotation':'an','matrix':'mx'}[scope]+'-'+digest[:16],full_digest=digest,legacy_name=scope,payload=payload)
def snapshot(root):
    return {str(p.relative_to(root)):(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns,p.stat().st_ctime_ns,p.stat().st_size) for p in root.rglob('*') if p.is_file()}


def native_fixture(tmp_path,annotation='terminal-annotation-v3'):
    root=tmp_path/'native/run';external=tmp_path/'external';external.mkdir(parents=True)
    sid=HAND['sample_id']
    cfg=dict(output_dir=str(root.parent),run_id=root.name,inputs=str(tmp_path/'original.csv'),quantification_mode='terminal-aware',dedup_method='directional',minpolyA=5,nonA_allowed=1,minMAPQ=10,coverage_scaling='none',exp_peak_threshold_mode='fixed',unscaled_depth_threshold=2,polyA_peak_threshold_mode='fixed',unscaled_depth_threshold_polyA=3,merge_distance=10,merge_distance_polyA=10,misprime_A_count=8,misprime_in=10,misprimed_ratio_threshold=.5,intronic_misprimed_ratio_threshold=.5,annotate_peak_source='merged',psi_min_gene_umis=1)
    # Independent fixed native identity; never call product native_payloads here.
    pk=dict(dedup_method='directional',polyA_status='polyA-5-1-10',coverage_scaling='none',depth_threshold='2',depth_threshold_polyA='3',merge_distance=10,merge_distance_polyA=10,misprime_A_count=8,misprime_in=10,misprimed_ratio_threshold=.5,quantification_mode='terminal-aware')
    an=dict(peak=pk,annotate_peak_source='merged',terminal_annotation_schema=annotation)
    mx=dict(annotation=an,psi_min_gene_umis=1,quantification=dict(quantification_mode='terminal-aware',intronic_misprimed_ratio_threshold=.5))
    records=[scoped(k,v) for k,v in [('peak',pk),('annotation',an),('matrix',mx)]]
    ns={r['scope']:r['code'] for r in records};base=sid+'.directional.annotated'
    paths=dict(config=root/'logs/invocations/synthetic/invocation.json',manifest=root/'state/artifact_name_manifest.v1.json',inputs=root/'input_tables/inputs.csv',counts=root/'count_matrix'/ns['peak']/ns['annotation']/(base+'.counts.terminal-aware.v2.tsv.gz'),catalogue=root/'matrix_summary'/ns['peak']/ns['annotation']/ns['matrix']/(ns['annotation']+'.terminal_aware_peak_to_gene.v2.tsv.gz'),cohort=external/'cohort.tsv',registry=external/'registry.txt')
    invocation=dict(schema_version='scpolya.logging.invocation.v2',config_schema_version=1,resolved_config=cfg,config_hash=hashlib.sha256(dump(cfg)).hexdigest(),run_root=str(root),output_dir=str(root.parent),run_id=root.name)
    manifest=dict(schema_version='scpolya.artifact_name_manifest.v1',mode='short',hash_algorithm='sha256',short_hex_length=16,records=records)
    put(paths['config'],dump(invocation));put(paths['manifest'],dump(manifest))
    put(paths['inputs'],table(['sample_id','source_path','is_deduped','requires_split'],[dict(sample_id=sid,source_path='/declared/source.bam',is_deduped='0',requires_split='1')],','))
    catalogue=[]
    for raw in HAND['catalogue_semantics_in_input_order']:
        # Distinct native PAS IDs deliberately share this coordinate.
        row=dict(peak_name=raw['peak'],gene_id=raw['gene_id'],strand=raw['strand'],feature=raw['feature'],misprimed_ratio=str(raw['misprimed_ratio']),general_mispriming_pass=str(raw['general_pass']),intronic_mispriming_pass=str(raw['general_pass']),mispriming_pass=str(raw['general_pass']),is_distal_peak='' if raw['native_order'] is None else str(raw['native_order']==raw['eligible_gene_K']),distance_to_stop_codon='' if raw['native_order'] is None else str(raw['native_order']*10),n_peaks_in_gene='' if raw['eligible_gene_K'] is None else str(raw['eligible_gene_K']),peak_order_in_gene='' if raw['native_order'] is None else str(raw['native_order']),chrom='chrSynthetic',start='100',end='101')
        catalogue.append(row)
    put(paths['catalogue'],gzip.compress(table(m.CAT_REQUIRED,catalogue),mtime=0))
    counts=[dict(peak_name=r['peak'],cell_barcode=r['cell'],umi_count=str(r['UMI'])) for r in HAND['raw_count_rows_in_input_order']]
    put(paths['counts'],gzip.compress(table(['peak_name','cell_barcode','umi_count'],counts),mtime=0))
    cells=[dict(cell_index=str(i),barcode=r['barcode'],major_class=r['group'],cell_type=r['cell_type']) for i,r in enumerate(HAND['selected_cells'])]
    put(paths['cohort'],table(c.HEADERS['cell-index.tsv'],cells));put(paths['registry'],('\n'.join(HAND['registry_cells'])+'\n').encode())
    req=m.make_request({k:str(v) for k,v in paths.items()},run_root=str(root),sample_id=sid,namespace=ns,artifact_base=base,invocation_inputs_path=cfg['inputs'],sample_source_path='/declared/source.bam',groups=list(HAND['declared_groups']),selected_units=copy.deepcopy(HAND['selected_gene_units']),attempt_dir=str(tmp_path/'prepared'))
    return req,paths

@pytest.fixture
def bundle(tmp_path):return native_fixture(tmp_path)

def refresh(req,paths):
    for role,p in paths.items():req['sources'][role].update(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
def saved(bundle):
    req,_=bundle;response=m.prepare(req);assert response['status']=='prepared';return req,response,Path(req['attempt_dir'])
def rewrite_table(path,change):
    raw=gzip.decompress(path.read_bytes());reader=csv.DictReader(io.StringIO(raw.decode()),delimiter='\t');header=reader.fieldnames;rows=list(reader);change(rows,header)
    put(path,gzip.compress(table(header,rows),mtime=0))


def test_independent_oracle_exact_raw_to_arrays_indexes_context_accounting(bundle):
    req,paths=bundle;before=snapshot(paths['counts'].parents[3])
    _,response,out=saved(bundle)
    assert np.load(out/'N.npy',allow_pickle=False).tolist()==HAND['expected_N']
    assert np.load(out/'T.npy',allow_pickle=False).tolist()==HAND['expected_T']
    ledger=json.loads((out/'eligibility-lineage.json').read_bytes());a=ledger['accounting']
    assert [r['barcode'] for r in ledger['cells']]==HAND['expected_cell_order']
    assert [r['terminal_gene_unit_id'] for r in ledger['genes']]==HAND['expected_gene_unit_ids']
    assert [r['peak_name'] for r in ledger['columns']]==HAND['expected_PAS_order']
    assert [[r['start'],r['stop']] for r in ledger['genes']]==HAND['expected_gene_blocks']
    want=HAND['expected_accounting']
    for key in ('catalogue_rows','count_rows','raw_UMI','retained_UMI','retained_cells','retained_genes','retained_PAS','globally_zero_PAS_retained','globally_zero_gene_retained'):assert a[key]==want[key]
    assert [a['categories'][key]['UMI'] for key in m.PRIORITY]==[11,0,13,17,19,21]
    assert a['selected_cells_without_source_rows']==['NA'] and a['unobserved_membership']=='registered_and_declared'
    assert len(ledger['selected_context'])==8 and all(r['fields']['start']=='100' for r in ledger['selected_context'])
    assert (out/'cell-index.tsv').read_bytes()==paths['cohort'].read_bytes()
    assert before==snapshot(paths['counts'].parents[3]) and response['review_state']=='not_reviewed' and response['native_input_completion_state']=='not_assessed'
    m.coverage_request_from_prepared(response['prepared_ref'],str(out.parent/'coverage'))


def test_all_zero_all_cells_genes_sites_stay(bundle):
    req,paths=bundle;rewrite_table(paths['counts'],lambda rows,header:[row.update(umi_count='0') for row in rows]);refresh(req,paths)
    _,response,out=saved(bundle)
    assert response['data_state']=='all_zero' and np.load(out/'N.npy').shape==(4,6) and not np.load(out/'N.npy').any()
    assert np.load(out/'T.npy').shape==(4,3) and not np.load(out/'T.npy').any()
    assert m.coverage_request_from_prepared(response['prepared_ref'],str(out.parent/'coverage'))['native_freshness']==m.FRESHNESS


def test_no_registry_declared_unobserved_no_registration_claim(bundle):
    req,paths=bundle;del req['sources']['registry'];del req['assertions']['registry_sample_binding'];del req['registry_sample_id']
    _,response,out=saved(bundle);view=m.coverage_request_from_prepared(response['prepared_ref'],str(out.parent/'coverage'))
    assert view['provenance']['source_cell_registry_membership']=='not_assessed'
    assert view['provenance']['external_registry_sample_origin']=='not_supplied'
    assert response['accounting']['unobserved_membership']=='declared_only_not_registry_proven'


@pytest.mark.parametrize('kind',['fractional','negative','overflow','raw_max','duplicate_excluded','unknown_peak','unknown_cell','shared_peak','K1','absent_unit','bad_flag','bad_order','bad_gene_K','cohort_order','cohort_group','registry_missing','catalogue_unknown_column','catalogue_duplicate_header','bad_gzip','truncated_gzip','source_hash','source_size','symlink'])
def test_invalid_raw_schema_identity_counts_refuse_preserving_partial(bundle,kind,tmp_path):
    req,paths=bundle
    if kind in ('fractional','negative','overflow','raw_max'):
        value={'fractional':'.5','negative':'-1','overflow':str(2**63),'raw_max':'10000001'}[kind]
        rewrite_table(paths['counts'],lambda rows,header:rows[0].update(umi_count=value))
    elif kind=='duplicate_excluded':rewrite_table(paths['counts'],lambda rows,header:rows.append(dict(rows[0])))
    elif kind=='unknown_peak':rewrite_table(paths['counts'],lambda rows,header:rows[0].update(peak_name='unknown'))
    elif kind=='unknown_cell':rewrite_table(paths['counts'],lambda rows,header:rows[0].update(cell_barcode='not-registered'))
    elif kind=='shared_peak':rewrite_table(paths['catalogue'],lambda rows,header:rows.append(dict(rows[0],gene_id='different')))
    elif kind=='K1':req['selected_units'].append({'gene_id':'unselected','strand':'+'})
    elif kind=='absent_unit':req['selected_units'].append({'gene_id':'absent','strand':'+'})
    elif kind=='bad_flag':rewrite_table(paths['catalogue'],lambda rows,header:rows[0].update(general_mispriming_pass='False'))
    elif kind=='bad_order':rewrite_table(paths['catalogue'],lambda rows,header:rows[0].update(peak_order_in_gene='1'))
    elif kind=='bad_gene_K':rewrite_table(paths['catalogue'],lambda rows,header:rows[0].update(n_peaks_in_gene='3'))
    elif kind=='cohort_order':paths['cohort'].write_bytes(paths['cohort'].read_bytes().replace(b'0\t001',b'9\t001'))
    elif kind=='cohort_group':req['groups']=['A','unknown']
    elif kind=='registry_missing':paths['registry'].write_bytes(b'001\nc3\nc4\nz_outside\n')
    elif kind=='catalogue_unknown_column':rewrite_table(paths['catalogue'],lambda rows,header:(header.append('unknown'),[row.update(unknown='x') for row in rows]))
    elif kind=='catalogue_duplicate_header':
        raw=gzip.decompress(paths['catalogue'].read_bytes());paths['catalogue'].write_bytes(gzip.compress(raw.replace(b'gene_id\t',b'peak_name\t',1),mtime=0))
    elif kind=='bad_gzip':paths['counts'].write_bytes(b'not gzip')
    elif kind=='truncated_gzip':paths['counts'].write_bytes(paths['counts'].read_bytes()[:-4])
    elif kind=='source_hash':req['sources']['counts']['sha256']='0'*64
    elif kind=='source_size':req['sources']['counts']['bytes']+=1
    else:
        source=paths['counts'];target=source.with_name('saved.gz');source.rename(target);source.symlink_to(target)
    if kind not in ('source_hash','source_size','symlink'):refresh(req,paths)
    response=m.guarded(m.prepare,req);out=Path(req['attempt_dir'])
    assert response['status']=='refused' and not (out/'prepared-manifest.json').exists()
    if out.exists():assert (out/'attempt-start.json').exists() and (out/'failure.json').exists()


@pytest.mark.parametrize('kind',['missing_threshold','config_hash','config_schema_bool','threshold_type','sample_path','native_namespace','unknown_request','unknown_role','legacy_annotation_v2'])
def test_native_binding_defaults_types_context_and_known_keys(bundle,tmp_path,kind):
    req,paths=bundle
    if kind=='legacy_annotation_v2':
        # Valid old payload and digests/paths, not merely a malformed digest.
        sub=tmp_path/'legacy';sub.mkdir();req,paths=native_fixture(sub,'terminal-annotation-v2')
    elif kind in ('missing_threshold','config_hash','config_schema_bool','threshold_type'):
        doc=json.loads(paths['config'].read_bytes())
        if kind=='missing_threshold':del doc['resolved_config']['intronic_misprimed_ratio_threshold']
        elif kind=='config_schema_bool':doc['config_schema_version']=True
        elif kind=='threshold_type':doc['resolved_config']['unscaled_depth_threshold']=2.0
        else:doc['config_hash']='0'*64
        if kind!='config_hash':doc['config_hash']=hashlib.sha256(dump(doc['resolved_config'])).hexdigest()
        put(paths['config'],dump(doc));refresh(req,paths)
    elif kind=='sample_path':req['sample_source_path']='/other.bam'
    elif kind=='native_namespace':req['namespace']['matrix']='mx-'+'0'*16
    elif kind=='unknown_request':req['command']='forbidden'
    else:req['sources']['psi']=req['sources']['counts']
    response=m.guarded(m.prepare,req)
    assert response['status']=='refused' and not (Path(req['attempt_dir'])/'prepared-manifest.json').exists()
    # The fixed-v3 config identity can reject the old namespace before manifest parsing.
    if kind=='legacy_annotation_v2':assert response['code'] in ('unsupported_schema','namespace_mismatch')


@pytest.mark.parametrize('key,value',[('cells',3),('genes',2),('PAS',5),('count_rows',8),('catalogue_rows',8),('source_or_registry_cells',4),('expanded_bytes_total',30),('io_bytes_total',10),('selected_context_rows',2),('selected_context_bytes',100),('five_file_bytes_total',100),('attempt_output_bytes_total',100),('metadata_bytes_each',50),('row_bytes',10),('field_bytes',3),('columns',2),('T_element_max',6),('N_element_max',10),('coverage_identity_bytes',1)])
def test_caps_refuse_before_publish(bundle,key,value):
    req,_=bundle;req['limits'][key]=value
    assert m.guarded(m.prepare,req)['status']=='refused'
    assert not (Path(req['attempt_dir'])/'prepared-manifest.json').exists()
    if key=='attempt_output_bytes_total':assert not Path(req['attempt_dir']).exists()


def test_known24column_profile_unicode_and_shared_coordinates(bundle,tmp_path):
    req,paths=bundle
    rewrite_table(paths['catalogue'],lambda rows,header:(header.extend(m.CAT_OPTIONAL),[r.update({k:'' for k in m.CAT_OPTIONAL}) for r in rows]))
    rewrite_table(paths['counts'],lambda rows,header:[r.update(cell_barcode='Ω' if r['cell_barcode']=='c4' else r['cell_barcode']) for r in rows])
    paths['cohort'].write_bytes(paths['cohort'].read_bytes().replace(b'c4','Ω'.encode()));paths['registry'].write_bytes(paths['registry'].read_bytes().replace(b'c4','Ω'.encode()));refresh(req,paths)
    _,response,out=saved(bundle)
    assert np.load(out/'N.npy').tolist()==HAND['expected_N'] and b'\xce\xa9' in (out/'cell-index.tsv').read_bytes()
    ledger=json.loads((out/'eligibility-lineage.json').read_bytes());assert len(ledger['native_source_tables']['catalogue'])==24
    assert m.unit_id('x|y','+')!=m.unit_id('x%7Cy','+') and m.unit_id('NA','-')=='NA|-'
    assert m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))['fresh_analysis'] is False


def test_no_global_ten_million_cap_when_all_elements_valid(bundle):
    req,paths=bundle
    rewrite_table(paths['counts'],lambda rows,header:[row.update(umi_count='3000000') for row in rows]);refresh(req,paths)
    response=m.prepare(req)
    assert response['accounting']['raw_UMI']==27_000_000
    # Global retained15M and raw27M are valid; each selected N/T stays<=10M.
    assert response['accounting']['retained_UMI']==15_000_000


def signal_state():return signal.getsignal(signal.SIGALRM),signal.pthread_sigmask(signal.SIG_BLOCK,set()),signal.getitimer(signal.ITIMER_REAL)

def test_library_prepare_restores_handler_mask_timer_on_success_and_refusal(bundle):
    req,_=bundle;old=signal_state()
    def handler(signum,frame):raise AssertionError('external handler should not run')
    signal.signal(signal.SIGALRM,handler);signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM})
    try:
        before=signal_state();m.prepare(req);assert signal_state()==before
        assert m.guarded(m.prepare,req)['status']=='refused' and signal_state()==before
        bad=copy.deepcopy(req);bad['attempt_dir']=str(Path(req['attempt_dir']).with_name('failed'));bad['sources']['counts']['sha256']='0'*64
        assert m.guarded(m.prepare,bad)['status']=='refused' and signal_state()==before
    finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old[0]);signal.pthread_sigmask(signal.SIG_SETMASK,old[1])


def test_active_external_alarm_not_disabled_or_extended(bundle):
    req,_=bundle;old=signal_state();signal.signal(signal.SIGALRM,lambda *args:None);signal.setitimer(signal.ITIMER_REAL,2,.5)
    try:
        before=signal_state();response=m.guarded(m.prepare,req);after=signal_state()
        assert response['code']=='external_timer_active' and after[0:2]==before[0:2]
        assert 0<after[2][0]<=before[2][0] and after[2][1]==before[2][1]==.5
        assert not Path(req['attempt_dir']).exists()
    finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old[0]);signal.pthread_sigmask(signal.SIG_SETMASK,old[1])


def test_actual_independent_prepare_fuse_expiry_retains_start(bundle,tmp_path):
    req,_=bundle;req['limits']['childless_prepare_OS_fuse_seconds']=1;ref=put(tmp_path/'request.json',m.wire(req))
    code='import importlib.util,json,signal,sys; s=importlib.util.spec_from_file_location("native_alarm_test",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.parse_catalogue=lambda *a:signal.pause(); m.prepare(json.load(open(sys.argv[2])))'
    started=time.monotonic();proc=subprocess.run([sys.executable,'-B','-c',code,str(MODULE),ref['path']],capture_output=True,timeout=3)
    assert proc.returncode==-signal.SIGALRM and time.monotonic()-started<2 and proc.stdout==proc.stderr==b''
    out=Path(req['attempt_dir']);assert (out/'attempt-start.json').exists() and not (out/'prepared-manifest.json').exists()
    before=snapshot(out);assert m.guarded(m.prepare,req)['code']=='refused_existing_output' and snapshot(out)==before
    put(tmp_path/'fuse-control.json',m.wire(dict(returncode=proc.returncode,wall_seconds=time.monotonic()-started,budget_seconds=1,partial_start_retained=True)))


@pytest.mark.parametrize('fault',['before_link','post_link_fsync','pending_unlink','failure_receipt_write'])
def test_publication_failure_truth_guard_retry_no_repair(bundle,tmp_path,monkeypatch,fault):
    req,_=bundle;out=Path(req['attempt_dir']);publish=m._publish;sync=m.c.fsync_directory;unlink=Path.unlink;write=m.c.write_new;calls=0
    def fail_sync(root):
        nonlocal calls
        calls+=1
        if calls==2:raise OSError('post-link fsync fault')
        return sync(root)
    def fail_publish(root,name):
        if name=='prepared-manifest.json':raise OSError('before final link')
        return publish(root,name)
    def fail_unlink(p,*args,**kw):
        if p==out/'.prepared-manifest.json.pending':raise OSError('link success; unlink fails')
        return unlink(p,*args,**kw)
    def fail_receipt(root,name,data,cap):
        if name=='failure.json':raise OSError('failure receipt fault')
        return write(root,name,data,cap)
    with monkeypatch.context() as patch:
        if fault=='before_link':patch.setattr(m,'_publish',fail_publish)
        elif fault=='pending_unlink':patch.setattr(Path,'unlink',fail_unlink)
        else:patch.setattr(m.c,'fsync_directory',fail_sync)
        if fault=='failure_receipt_write':patch.setattr(m.c,'write_new',fail_receipt)
        response=m.guarded(m.prepare,req)
    assert response['status']=='refused' and (out/m.GUARD).exists()
    if fault!='failure_receipt_write':
        failure=json.loads((out/'failure.json').read_bytes())
        assert failure['complete_manifest_published'] is (fault!='before_link')
        assert failure['publication_state']==('not_published' if fault=='before_link' else 'visible_durability_uncertain')
    if fault!='before_link':
        assert (out/'prepared-manifest.json').exists()
        ref={'path':str(out/'prepared-manifest.json'),'sha256':hashlib.sha256((out/'prepared-manifest.json').read_bytes()).hexdigest()}
        assert m.guarded(m.coverage_request_from_prepared,ref,str(tmp_path/'analysis'))['status']=='refused'
    before=snapshot(out);assert m.guarded(m.prepare,req)['code']=='refused_existing_output' and before==snapshot(out)
    retry=copy.deepcopy(req);retry['attempt_dir']=str(tmp_path/'retry');assert m.prepare(retry)['status']=='prepared' and before==snapshot(out)


def test_source_mutation_during_final_verification_refuses(bundle,monkeypatch):
    req,paths=bundle;old=m.Sources.final_verify
    def change(session):
        p=paths['inputs'];raw=p.read_bytes();p.write_bytes(raw.replace(b'/declared/source.bam',b'/declared/sourcf.bam'))
        return old(session)
    monkeypatch.setattr(m.Sources,'final_verify',change)
    assert m.guarded(m.prepare,req)['status']=='refused' and not (Path(req['attempt_dir'])/'prepared-manifest.json').exists()


@pytest.mark.parametrize('variable',c.THREADS)
def test_real_resource_controls_reject_thread_mismatch(bundle,monkeypatch,variable):
    req,_=bundle;before=signal_state();monkeypatch.setenv(variable,'2')
    assert m.guarded(m.prepare,req)['code']=='runtime_unavailable' and signal_state()==before
    assert not Path(req['attempt_dir']).exists()


def test_actual_cli_launcher_bridge_plan_result_chain_and_readonly_import(bundle,tmp_path,monkeypatch):
    req,_=bundle;request_ref=put(tmp_path/'request.json',m.wire(req))
    proc=subprocess.run(['bash',str(ROOT/'scripts/native_coverage_limited.sh'),request_ref['path']],capture_output=True,timeout=15)
    put(tmp_path/'launcher.stdout.log',proc.stdout);put(tmp_path/'launcher.stderr.log',proc.stderr)
    assert proc.returncode==0,proc.stdout+proc.stderr;prepared=json.loads(proc.stdout)['prepared_ref']
    runtime=json.loads(Path(prepared['path']).read_bytes())['runtime'];assert runtime['controls']=={'cpu.max':'200000 100000','memory.max':'8589934592','memory.swap.max':'0'}
    analysis_dir=str(tmp_path/'coverage');envelope=m.coverage_request_from_prepared(prepared,analysis_dir)
    bridge_ref=put(tmp_path/'bridge.json',m.wire(envelope));assert m.verify_handoff(bridge_ref)['stage']=='bridge'
    plan=c.plan_coverage(envelope['coverage_request']);plan_ref=put(tmp_path/'coverage-plan.json',c.wire(plan))
    planned=m.link_coverage(prepared,plan_ref);planned_ref=put(tmp_path/'planned.json',m.wire(planned));assert m.verify_handoff(planned_ref)['stage']=='planned'
    run=c.run_coverage(plan_ref);assert run['status']=='complete'
    done=m.link_coverage(prepared,plan_ref,run['analysis_ref']);done_ref=put(tmp_path/'completed.json',m.wire(done))
    before=snapshot(tmp_path);signals=signal_state()
    monkeypatch.setattr(m,'prepare',lambda *a:pytest.fail('read helper called prepare'));monkeypatch.setattr(np,'load',lambda *a,**k:pytest.fail('read helper loaded numerical arrays'));monkeypatch.setattr(c,'calculate_coverage',lambda *a:pytest.fail('read helper recalculated coverage'))
    for ref in (bridge_ref,planned_ref,done_ref):
        view=m.verify_handoff(ref);assert view['fresh_analysis'] is False and view['native_freshness']=='checked_at_preparation_only'
    assert before==snapshot(tmp_path) and signals==signal_state()
    for citation in done['citations']:
        raw=Path(citation['path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==citation['sha256']
        assert c.resolve_pointer(json.loads(raw),citation['json_pointer']) is not None
        if 'table' in citation:
            table=citation['table'];rows=list(csv.DictReader(io.StringIO(Path(table['path']).read_text()),delimiter='\t'))
            assert list(rows[0])==table['header'] and sum(all(r[k]==v for k,v in table['primary_key'].items()) for r in rows)==1
    code='import importlib.util,logging,signal,sys,types; before=(logging.getLogger().level,list(logging.getLogger().handlers),signal.getsignal(signal.SIGALRM),signal.getitimer(signal.ITIMER_REAL),signal.pthread_sigmask(signal.SIG_BLOCK,set())); poison=types.ModuleType("quantification_config"); sys.modules["quantification_config"]=poison; s=importlib.util.spec_from_file_location("isolated_native_adapter",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); assert "numpy" not in sys.modules and "sqlite3" not in sys.modules and "scpolya_agent" not in sys.modules; assert sys.modules["quantification_config"] is poison; assert before==(logging.getLogger().level,list(logging.getLogger().handlers),signal.getsignal(signal.SIGALRM),signal.getitimer(signal.ITIMER_REAL),signal.pthread_sigmask(signal.SIG_BLOCK,set()))'
    subprocess.run([sys.executable,'-B','-c',code,str(MODULE)],check=True,capture_output=True,timeout=5)


@pytest.mark.parametrize('kind',['missing_citation','wrong_pointer','wrong_header','wrong_key','wrong_request','wrong_provenance','wrong_schema','inside_prepared'])
def test_generated_lineage_envelope_tampering_refuses_no_writes(bundle,tmp_path,kind):
    req,response,out=saved(bundle);envelope=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    if kind=='missing_citation':envelope['citations'].pop()
    elif kind=='wrong_pointer':envelope['citations'][0]['json_pointer']='/absent'
    elif kind in ('wrong_header','wrong_key'):
        table=next(r['table'] for r in envelope['citations'] if 'table' in r)
        if kind=='wrong_header':table['header']=['other']
        else:table['primary_key']={'barcode':'unknown'}
    elif kind=='wrong_request':envelope['coverage_request']['sample_id']='wrong'
    elif kind=='wrong_provenance':envelope['provenance']['external_cohort_sample_origin']='verified'
    elif kind=='wrong_schema':envelope['schema']='other'
    where=out/'envelope.json' if kind=='inside_prepared' else tmp_path/'envelope.json'
    ref=put(where,m.wire(envelope));before=snapshot(tmp_path)
    assert m.guarded(m.verify_handoff,ref)['status']=='refused' and before==snapshot(tmp_path)


@pytest.mark.parametrize('kind',['changed_output','changed_producer','missing_output','unrelated_manifest','plan_request','result_ref'])
def test_prepared_identity_plan_and_result_mismatches_refuse(bundle,tmp_path,kind):
    req,response,out=saved(bundle);pref=response['prepared_ref'];envelope=m.coverage_request_from_prepared(pref,str(tmp_path/'coverage'))
    if kind=='changed_output':(out/'N.npy').write_bytes((out/'N.npy').read_bytes()[:-1]+b'\x01')
    elif kind=='missing_output':(out/'T.npy').unlink()
    elif kind=='changed_producer':
        manifest=json.loads((out/'prepared-manifest.json').read_bytes());manifest['producer']['files']['adapter.py']['sha256']='0'*64;pref=put(out/'prepared-manifest.json',m.wire(manifest))
    elif kind=='unrelated_manifest':pref=put(tmp_path/'foreign.json',b'{"schema":"other"}')
    else:
        request=envelope['coverage_request']
        if kind=='plan_request':request=copy.deepcopy(request);request['groups'].reverse()
        plan=c.plan_coverage(request);ref=put(tmp_path/'plan.json',c.wire(plan))
        result=None if kind=='plan_request' else put(tmp_path/'foreign-result.json',b'{"schema":"other"}')
        before=snapshot(tmp_path)
        assert m.guarded(m.link_coverage,pref,ref,result)['status']=='refused' and before==snapshot(tmp_path);return
    before=snapshot(tmp_path);assert m.guarded(m.coverage_request_from_prepared,pref,str(tmp_path/'another-analysis'))['status']=='refused' and before==snapshot(tmp_path)


def test_native_freshness_limited_to_preparation_not_silently_rechecked(bundle,tmp_path,monkeypatch):
    req,paths=bundle;_,response,out=saved(bundle)
    paths['counts'].write_bytes(b'changed original after completed immutable snapshot')
    original=m.Sources.stream
    monkeypatch.setattr(m.Sources,'stream',lambda *a,**kw:pytest.fail('bridge re-read native sources'))
    before=snapshot(out);view=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    assert view['native_freshness']=='checked_at_preparation_only' and before==snapshot(out)


def test_cli_known_actions_and_missing_resource_caps_refuse(bundle,tmp_path):
    req,_=bundle;ref=put(tmp_path/'request.json',m.wire(req))
    for argv in (['run'],['prepare','--request',ref['path'],'--command','forbidden'],['--worker']):
        proc=subprocess.run([sys.executable,'-B',str(MODULE),*argv],capture_output=True,timeout=5)
        assert proc.returncode==2 and proc.stderr==b'' and json.loads(proc.stdout)['status']=='refused'
    # A separate finite unit deliberately lacks leaf CPU/memory/swap caps.
    argv=['systemd-run','--user','--wait','--pipe','--collect','-p','RuntimeMaxSec=5s','-p','TimeoutStopSec=1s','--setenv=PYTHONDONTWRITEBYTECODE=1']
    argv+=['--setenv='+key+'=1' for key in c.THREADS]
    argv+=[sys.executable,'-B',str(MODULE),'prepare','--request',ref['path']]
    proc=subprocess.run(argv,capture_output=True,timeout=10)
    put(tmp_path/'uncapped.stdout.log',proc.stdout);put(tmp_path/'uncapped.stderr.log',proc.stderr)
    assert proc.returncode!=0 and json.loads(proc.stdout)['code']=='runtime_unavailable'
    assert not Path(req['attempt_dir']).exists()


def attach_projection(req,paths,tmp_path):
    original=tmp_path/'original-selection/selection.tsv'
    # Deliberately concatenated group order differs from global barcode order.
    pairs=[dict(barcode='c4',group='B'),dict(barcode='001',group='A'),dict(barcode='c3',group='B'),dict(barcode='NA',group='A')]
    put(original,table(['barcode','group'],pairs))
    canonical=[dict(cell_index=str(i),barcode=r['barcode'],major_class=r['group'],cell_type=r['group']) for i,r in enumerate(sorted(pairs,key=lambda r:r['barcode'].encode()))]
    paths['cohort'].write_bytes(table(c.HEADERS['cell-index.tsv'],canonical));refresh(req,paths)
    req['cohort_origin']=m.make_cohort_origin(str(original),req['sample_id'])
    return original


def test_projection_origin_exact_bytes_recipe_and_immutable_chain(bundle,tmp_path,monkeypatch):
    req,paths=bundle;original=attach_projection(req,paths,tmp_path)
    _,response,out=saved(bundle);manifest=json.loads((out/'prepared-manifest.json').read_bytes())
    origin=manifest['cohort_origin'];assert origin['projection_status']=='exact_bytes_match' and origin['recipe_digest']==m.PROJECTION_DIGEST
    assert [r['original_data_row'] for r in origin['row_mapping']]==[1,3,2,0]
    assert manifest['sources']['original_selection']==req['cohort_origin']['original_selection']
    assert np.load(out/'N.npy').tolist()==HAND['expected_N'] and np.load(out/'T.npy').tolist()==HAND['expected_T']
    bridge=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    assert bridge['cohort_origin']==origin and bridge['selection_freshness']==m.FRESHNESS
    ref=put(tmp_path/'projection-envelope.json',m.wire(bridge));assert m.verify_handoff(ref)['cohort_origin']==origin
    # Explicit recorded-only selection freshness: no original-selection reread.
    original.write_bytes(b'changed external original selection after preparation')
    monkeypatch.setattr(m.Sources,'stream',lambda *a,**kw:pytest.fail('read bridge reparsed original selection'))
    assert m.verify_handoff(ref)['selection_freshness']=='checked_at_preparation_only'


@pytest.mark.parametrize('fault',['label','order','index','hash','duplicate','sample','recipe','unknown_key','missing_final_lf','wrong_type'])
def test_projection_wrong_origin_or_cohort_refuses(bundle,tmp_path,fault):
    req,paths=bundle;original=attach_projection(req,paths,tmp_path)
    if fault=='label':paths['cohort'].write_bytes(paths['cohort'].read_bytes().replace(b'NA\tA\tA',b'NA\tB\tB'));refresh(req,paths)
    elif fault=='order':
        raw=paths['cohort'].read_bytes().splitlines(True);raw[-1],raw[-2]=raw[-2],raw[-1];paths['cohort'].write_bytes(b''.join(raw));refresh(req,paths)
    elif fault=='index':paths['cohort'].write_bytes(paths['cohort'].read_bytes().replace(b'0\t001',b'1\t001'));refresh(req,paths)
    elif fault=='hash':req['cohort_origin']['original_selection']['sha256']='0'*64
    elif fault=='duplicate':
        raw=original.read_bytes();original.write_bytes(raw+raw.splitlines(True)[1]);req['cohort_origin']=m.make_cohort_origin(str(original),req['sample_id'])
    elif fault=='sample':req['cohort_origin']['sample_id']='wrong-sample'
    elif fault=='recipe':req['cohort_origin']['recipe_id']='custom-plugin'
    elif fault=='unknown_key':req['cohort_origin']['module_path']='/forbidden'
    elif fault=='missing_final_lf':original.write_bytes(original.read_bytes().rstrip(b'\n'));req['cohort_origin']=m.make_cohort_origin(str(original),req['sample_id'])
    else:req['cohort_origin']=None
    response=m.guarded(m.prepare,req);assert response['status']=='refused' and not (Path(req['attempt_dir'])/'prepared-manifest.json').exists()


@pytest.mark.parametrize('fault',['sample_mismatch','missing_sample','duplicate','header','cohort_missing','excluded_count_unknown','no_final_lf'])
def test_supplied_registry_strict_declaration_format_membership(bundle,fault):
    req,paths=bundle
    if fault=='sample_mismatch':req['registry_sample_id']='wrong'
    elif fault=='missing_sample':del req['registry_sample_id']
    elif fault=='duplicate':paths['registry'].write_bytes(paths['registry'].read_bytes()+b'001\n');refresh(req,paths)
    elif fault=='header':paths['registry'].write_bytes(b'barcode\n001\nNA\nc3\nc4\n');refresh(req,paths)
    elif fault=='cohort_missing':paths['registry'].write_bytes(b'001\nc3\nc4\nz_outside\n');refresh(req,paths)
    elif fault=='excluded_count_unknown':paths['registry'].write_bytes(b'001\nNA\nc3\nc4\n');refresh(req,paths)
    else:paths['registry'].write_bytes(paths['registry'].read_bytes().rstrip(b'\n'));refresh(req,paths)
    assert m.guarded(m.prepare,req)['status']=='refused' and not (Path(req['attempt_dir'])/'prepared-manifest.json').exists()


def test_registry_order_harmless_and_zero_after_selection_distinct_from_source_absence(bundle,tmp_path):
    req,paths=bundle
    paths['registry'].write_bytes(b'z_outside\nc4\nc3\nNA\n001\n')
    rewrite_table(paths['counts'],lambda rows,header:rows.append(dict(peak_name='p_bad',cell_barcode='NA',umi_count='2')));refresh(req,paths)
    _,response,out=saved(bundle)
    assert np.load(out/'N.npy').tolist()==HAND['expected_N'] and np.load(out/'T.npy').tolist()==HAND['expected_T']
    a=response['accounting'];assert a['selected_cells_without_source_rows']==[] and a['selected_cells_zero_after_eligible_selection']==['NA']
    view=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    assert view['provenance']['registry_sample_id']==req['sample_id'] and view['provenance']['registry_membership_meaning']=='listed_in_supplied_declared_registry_not_BAM_proof'
    assert view['cohort_origin']['mode']=='direct_canonical_authority' and view['cohort_origin']['projection_status']=='not_assessed'


@pytest.mark.parametrize('fault',['missing','modified'])
def test_cold_cli_dependency_pin_refuses_without_executing_foreign_helpers(tmp_path,fault):
    isolated=tmp_path/'isolated';isolated.mkdir();entry=isolated/'native_coverage.py';entry.write_bytes(MODULE.read_bytes())
    if fault=='modified':(isolated/'coverage_diagnostics.py').write_bytes(b'raise AssertionError("must not execute foreign helper")\n')
    proc=subprocess.run([sys.executable,'-B',str(entry),'prepare','--request','/not-read'],capture_output=True,timeout=5)
    assert proc.returncode==2 and proc.stderr==b'' and json.loads(proc.stdout)['code']=='stale_producer'


@pytest.mark.parametrize('field',['run_root','attempt_dir','source','original_selection'])
def test_noncanonical_path_spelling_cannot_publish_unusable_bundle(bundle,tmp_path,field):
    req,paths=bundle
    if field=='original_selection':
        attach_projection(req,paths,tmp_path)
        desc=req['cohort_origin']['original_selection'];p=Path(desc['path']);desc['path']=str(p.parent)+'/./'+p.name
    elif field=='source':
        desc=req['sources']['counts'];p=Path(desc['path']);desc['path']=str(p.parent)+'/./'+p.name
    else:
        p=Path(req[field]);req[field]=str(p.parent)+'/./'+p.name
    assert m.guarded(m.prepare,req)['code']=='path_refused'
    assert not (tmp_path/'prepared').exists()


def test_max_cells_long_projection_manifest_remains_bridge_readable(bundle,tmp_path):
    req,paths=bundle
    labels=['A'+'a'*255,'B'+'b'*255]
    ids=['x'+str(i).zfill(4)+'_'+'c'*200 for i in range(512)]
    mapping=dict(zip(HAND['expected_cell_order'],ids[:4]))
    rewrite_table(paths['counts'],lambda rows,header:[r.update(cell_barcode=mapping.get(r['cell_barcode'],r['cell_barcode'])) for r in rows])
    registry='\n'.join([*ids,'z_outside'])+'\n';paths['registry'].write_text(registry)
    original=tmp_path/'large-original/selection.tsv'
    pairs=[dict(barcode=cell,group=labels[0 if i<256 else 1]) for i,cell in enumerate(ids)]
    put(original,table(['barcode','group'],list(reversed(pairs))))
    canonical=[dict(cell_index=str(i),barcode=row['barcode'],major_class=row['group'],cell_type=row['group']) for i,row in enumerate(pairs)]
    paths['cohort'].write_bytes(table(c.HEADERS['cell-index.tsv'],canonical));refresh(req,paths)
    req['groups']=labels;req['cohort_origin']=m.make_cohort_origin(str(original),req['sample_id'])
    response=m.prepare(req);manifest=Path(response['prepared_ref']['path'])
    assert manifest.stat().st_size>262144 and response['shape']==[512,6]
    view=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    assert view['coverage_request']['groups']==labels and view['cohort_origin']['projection_status']=='exact_bytes_match'
    assert view['native_freshness']==view['selection_freshness']=='checked_at_preparation_only'


def rewrite_saved_records(out,manifest,ledger):
    """Refresh all outer hashes: rejection must come from saved semantics."""
    (out/'eligibility-lineage.json').write_bytes(m.wire(ledger))
    manifest['files']['eligibility-lineage.json']=c.descriptor(out,'eligibility-lineage.json')
    return put(out/'prepared-manifest.json',m.wire(manifest))


def assert_saved_refuses_readonly(out,ref,envelope,tmp_path,monkeypatch):
    envelope['prepared_ref']=ref
    sidecar=put(tmp_path/'rehash-envelope.json',m.wire(envelope))
    before=snapshot(tmp_path);handler=signal.getsignal(signal.SIGALRM);timer=signal.getitimer(signal.ITIMER_REAL)
    def forbidden(*args,**kw):pytest.fail('read-only saved consistency check invoked numerical work, source read, or prepare fuse')
    for obj,name in [(m,'prepare'),(m,'prepare_fuse'),(m,'bind_native'),(m,'parse_catalogue'),(m,'build_counts'),(m,'npy_bytes'),(m.Sources,'stream'),(c,'calculate_coverage'),(np,'load')]:monkeypatch.setattr(obj,name,forbidden)
    bridge=m.guarded(m.coverage_request_from_prepared,ref,str(tmp_path/'coverage'))
    verified=m.guarded(m.verify_handoff,sidecar)
    assert bridge['status']==verified['status']=='refused'
    assert bridge['detail']==verified['detail']
    assert before==snapshot(tmp_path) and handler==signal.getsignal(signal.SIGALRM) and timer==signal.getitimer(signal.ITIMER_REAL)


@pytest.mark.parametrize('fault',['sample','root','namespace','artifact_base','config_hash','annotation_v2','missing_field','extra_field','empty','wrong_type','observed_claim','matrix_payload','peak_nested'])
def test_saved_binding_rehashed_internal_contradictions_refuse(bundle,tmp_path,monkeypatch,fault):
    _,response,out=saved(bundle);ref=response['prepared_ref']
    envelope=m.coverage_request_from_prepared(ref,str(tmp_path/'coverage'))
    manifest=json.loads((out/'prepared-manifest.json').read_bytes());ledger=json.loads((out/'eligibility-lineage.json').read_bytes())
    binding=ledger['native_binding']
    if fault=='sample':binding['sample_id']='wrong-native-sample'
    elif fault=='root':binding['run_root']=str(tmp_path/'wrong-native-root')
    elif fault=='namespace':binding['namespace']['peak']='pk-'+'0'*16
    elif fault=='artifact_base':binding['artifact_base']='wrong.directional.annotated'
    elif fault=='config_hash':binding['config_hash']='0'*64
    elif fault=='annotation_v2':binding['scientific_payloads']['annotation']['terminal_annotation_schema']='terminal-annotation-v2'
    elif fault=='missing_field':del binding['artifact_base']
    elif fault=='extra_field':binding['historical_authentication']='proven'
    elif fault=='empty':binding={}
    elif fault=='wrong_type':binding=[]
    elif fault=='observed_claim':binding['observed']='historical native execution authenticated'
    elif fault=='matrix_payload':binding['scientific_payloads']['matrix']['quantification']['intronic_misprimed_ratio_threshold']=.95
    else:binding['scientific_payloads']['annotation']['peak']['merge_distance']=999
    manifest['native_binding']=binding;ledger['native_binding']=binding
    ref=rewrite_saved_records(out,manifest,ledger)
    assert_saved_refuses_readonly(out,ref,envelope,tmp_path,monkeypatch)


@pytest.mark.parametrize('fault',['feature','ratio_and_flags','flag_contradiction','eligible_extra','false_eligible','wrong_reason','unselected_unit','K','order','field_type','missing_context','duplicate_row','out_of_range_row','row_order','boolean_type'])
def test_saved_context_rehashed_scientific_contradictions_refuse(bundle,tmp_path,monkeypatch,fault):
    _,response,out=saved(bundle);envelope=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    manifest=json.loads((out/'prepared-manifest.json').read_bytes());ledger=json.loads((out/'eligibility-lineage.json').read_bytes())
    contexts=ledger['selected_context'];ctx=next(r for r in contexts if r['eligible']);f=ctx['fields']
    if fault=='feature':f['feature']='Intron'
    elif fault=='ratio_and_flags':f.update(misprimed_ratio='.95',general_mispriming_pass='False',intronic_mispriming_pass='False',mispriming_pass='False')
    elif fault=='flag_contradiction':f['misprimed_ratio']='.95'
    elif fault=='eligible_extra':
        ctx=next(r for r in contexts if r['fields']['peak_name']=='p_bad')
        ctx['fields'].update(misprimed_ratio='.1',general_mispriming_pass='True',intronic_mispriming_pass='True',mispriming_pass='True',peak_order_in_gene='3',n_peaks_in_gene='3')
        ctx.update(eligible=True,exclusion_reason=None)
    elif fault=='false_eligible':ctx.update(eligible=False,exclusion_reason='nonterminal');f['feature']='Intron'
    elif fault=='wrong_reason':next(r for r in contexts if not r['eligible'])['exclusion_reason']='unselected_gene'
    elif fault=='unselected_unit':next(r for r in contexts if not r['eligible'])['fields']['gene_id']='not-selected'
    elif fault=='K':f['n_peaks_in_gene']='3'
    elif fault=='order':f['peak_order_in_gene']='1' if f['peak_order_in_gene']=='2' else '2'
    elif fault=='field_type':f['start']=100
    elif fault=='missing_context':contexts.remove(ctx)
    elif fault=='duplicate_row':contexts[1]['catalogue_data_row']=contexts[0]['catalogue_data_row']
    elif fault=='out_of_range_row':contexts[-1]['catalogue_data_row']=ledger['accounting']['catalogue_rows']
    elif fault=='row_order':contexts[0],contexts[1]=contexts[1],contexts[0]
    else:ctx['eligible']=1
    ref=rewrite_saved_records(out,manifest,ledger)
    assert_saved_refuses_readonly(out,ref,envelope,tmp_path,monkeypatch)


@pytest.mark.parametrize('fault',['scientific_review','registry_membership_meaning','historical_run_commit','observed','declared','external_cohort_sample_origin','external_registry_sample_origin','registry_sample_id','source_cell_registry_membership','original_input_copy_origin','native_completion','unknown_key','missing_key','wrong_type'])
def test_saved_provenance_rehashed_false_claims_or_schema_refuse(bundle,tmp_path,monkeypatch,fault):
    _,response,out=saved(bundle);envelope=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    manifest=json.loads((out/'prepared-manifest.json').read_bytes());ledger=json.loads((out/'eligibility-lineage.json').read_bytes())
    provenance=ledger['provenance']
    if fault=='unknown_key':provenance['authentication']='proven'
    elif fault=='missing_key':del provenance['scientific_review']
    elif fault=='wrong_type':ledger['provenance']=[]
    elif fault=='scientific_review':provenance[fault]='accepted'
    elif fault=='registry_membership_meaning':provenance[fault]='BAM_proven'
    elif fault=='historical_run_commit':provenance[fault]='fabricated'
    elif fault=='declared':provenance[fault]=dict(provenance[fault],external_cohort_sample_binding=1)
    else:provenance[fault]='authenticated'
    ref=rewrite_saved_records(out,manifest,ledger)
    assert_saved_refuses_readonly(out,ref,envelope,tmp_path,monkeypatch)


def test_cached_config_is_exact_original_bytes_and_reader_never_reopens_native(bundle,tmp_path,monkeypatch):
    req,paths=bundle;_,response,out=saved(bundle)
    assert (out/'native-config.json').read_bytes()==paths['config'].read_bytes()
    before=snapshot(out);paths['config'].write_bytes(b'external native config changed after preparation')
    monkeypatch.setattr(m.Sources,'stream',lambda *a,**kw:pytest.fail('reader reopened native sources'))
    monkeypatch.setattr(m,'prepare_fuse',lambda *a,**kw:pytest.fail('reader armed prepare fuse'))
    view=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    assert view['native_freshness']=='checked_at_preparation_only' and before==snapshot(out)


def test_saved_config_rehash_cannot_break_original_descriptor_binding(bundle,tmp_path,monkeypatch):
    _,response,out=saved(bundle);envelope=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    manifest=json.loads((out/'prepared-manifest.json').read_bytes());ledger=json.loads((out/'eligibility-lineage.json').read_bytes())
    cfg=json.loads((out/'native-config.json').read_bytes());cfg['resolved_config']['misprimed_ratio_threshold']=.99
    cfg['config_hash']=m.sha(m.encode(cfg['resolved_config']));(out/'native-config.json').write_bytes(m.wire(cfg))
    manifest['files']['native-config.json']=c.descriptor(out,'native-config.json')
    ref=rewrite_saved_records(out,manifest,ledger)
    assert_saved_refuses_readonly(out,ref,envelope,tmp_path,monkeypatch)


def test_saved_ledger_binding_rejects_numeric_type_coercion(bundle,tmp_path,monkeypatch):
    _,response,out=saved(bundle);envelope=m.coverage_request_from_prepared(response['prepared_ref'],str(tmp_path/'coverage'))
    manifest=json.loads((out/'prepared-manifest.json').read_bytes());ledger=json.loads((out/'eligibility-lineage.json').read_bytes())
    ledger['native_binding']['scientific_payloads']['peak']['misprime_A_count']=8.0
    ref=rewrite_saved_records(out,manifest,ledger)
    assert_saved_refuses_readonly(out,ref,envelope,tmp_path,monkeypatch)
