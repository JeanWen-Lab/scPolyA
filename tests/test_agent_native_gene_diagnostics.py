import csv
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

MODULE=Path(__file__).resolve().parents[1]/'scpolya_agent/native_gene_diagnostics.py'
spec=importlib.util.spec_from_file_location('reader_under_test',MODULE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def put(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def gztable(path,columns,rows):
    import io
    f=io.StringIO(newline='');w=csv.DictWriter(f,fieldnames=columns,delimiter='\t',lineterminator='\n')
    w.writeheader();w.writerows(rows)
    return put(path,gzip.compress(f.getvalue().encode(),mtime=0))


@pytest.fixture
def fixture(tmp_path,request):
    schema=getattr(request,'param','terminal-annotation-v3')
    root=tmp_path/'run';root.mkdir()
    naming,effective,_=m.helpers()
    cfg=dict(quantification_mode='terminal-aware',coverage_scaling='expr',dedup_method='directional',
        minpolyA=5,nonA_allowed=1,minMAPQ=10,merge_distance=10,merge_distance_polyA=10,
        misprime_A_count=8,misprime_in=10,misprimed_ratio_threshold=.9,
        intronic_misprimed_ratio_threshold=.7,annotate_peak_source='merged',psi_min_gene_umis=1,
        exp_peak_threshold_mode='dynamic',exp_dynamic_threshold_stat='max_norm_fixed',exp_dynamic_threshold_normalized=.05,
        polyA_peak_threshold_mode='fixed',unscaled_depth_threshold_polyA=3.0,
        output_dir=str(tmp_path),run_id='run',inputs=str(tmp_path/'original.csv'))
    pk=naming.build_scoped_artifact_name_v1('peak',naming.peak_name_payload(effective.build_annotated_peak_config(m.SimpleNamespace(**cfg),dedup_method='directional',polyA_status='polyA-5-1-10')),legacy_name='peak')
    an=naming.build_scoped_artifact_name_v1('annotation',naming.annotation_name_payload(pk.payload,annotate_peak_source='merged',terminal_annotation_schema=schema),legacy_name='annotation')
    mx=naming.build_scoped_artifact_name_v1('matrix',naming.matrix_name_payload(an.payload,psi_min_gene_umis=1,quantification_config={'quantification_mode':'terminal-aware','intronic_misprimed_ratio_threshold':.7}),legacy_name='matrix')
    ns={'peak':pk.code,'annotation':an.code,'matrix':mx.code}
    base='s.directional.annotated';summary=Path('matrix_summary')/pk.code/an.code/mx.code
    paths={'inputs':Path('input_tables/inputs.csv'),'manifest':Path('state/artifact_name_manifest.v1.json'),
      'config':Path('logs/invocations/frozen/invocation.json'),'registry':tmp_path/'registry.tsv',
      'counts':Path('count_matrix')/pk.code/an.code/(base+'.counts.terminal-aware.v2.tsv.gz'),
      'catalogue':summary/(an.code+'.terminal_aware_peak_to_gene.v2.tsv.gz'),
      'psi':summary/(base+'.terminal_aware_psi.v2.tsv.gz')}
    manifest={'schema_version':'scpolya.artifact_name_manifest.v1','mode':'short','hash_algorithm':'sha256','short_hex_length':16,'records':[x.as_dict() for x in [pk,an,mx]]}
    invocation={'schema_version':'scpolya.logging.invocation.v2','run_root':str(root),'resolved_config':cfg,'config_schema_version':1,'config_hash':m.config_digest(cfg),'output_dir':cfg['output_dir'],'run_id':cfg['run_id']}
    hashes={}
    for role,value in [('manifest',manifest),('config',invocation)]:hashes[role]=put(root/paths[role],json.dumps(value).encode())
    hashes['inputs']=put(root/paths['inputs'],b'sample_id,source_path,is_deduped,requires_split\ns,/source.bam,0,1\n')
    hashes['registry']=put(paths['registry'],b'c0\nc1\n')
    catalogue=[]
    for peak,feature,distance,distal,n,order in [('P','Terminal_exon',10,False,2,1),('D','Terminal_exon',100,True,2,2),('I','Intron',-10,None,None,None)]:
        catalogue.append(dict(peak_name=peak,gene_id='001',strand='+',feature=feature,misprimed_ratio='0.1',general_mispriming_pass=True,intronic_mispriming_pass=True,mispriming_pass=True,is_distal_peak=distal if distal is not None else '',distance_to_stop_codon=distance,n_peaks_in_gene=n or '',peak_order_in_gene=order or ''))
    counts=[dict(peak_name=p,cell_barcode='c0',umi_count=c) for p,c in [('P',40),('D',40),('I',20)]]
    hashes['catalogue']=gztable(root/paths['catalogue'],m.CAT_FIELDS,catalogue)
    hashes['counts']=gztable(root/paths['counts'],['peak_name','cell_barcode','umi_count'],counts)
    psis=[dict(peak_name=p,gene_id='001',strand='+',cell_barcode='c0',peak_umi_count=40,gene_umi_count=80,psi=.5) for p in ['P','D']]
    hashes['psi']=gztable(root/paths['psi'],list(psis[0]),psis)
    req=dict(schema='scpolya.native_gene_diagnostics.request.v1',run_root=str(root),namespace=ns,sample_id='s',registry_sample_id='s',artifact_base=base,invocation_inputs_path=cfg['inputs'],sample_source_path='/source.bam',historical_run_commit=None,
      assertions={'quiescent':True,'complete_count_catalogue_scope':True,'registry_sample_binding':True},
      selection=[{'gene_id':'001','strand':'+','cell_barcodes':['c0','c1']}],sources={k:{'path':str(p),'sha256':hashes[k]} for k,p in paths.items()})
    def rewrite(role,value):
        path=paths[role] if role=='registry' else root/paths[role]
        req['sources'][role]['sha256']=put(path,value)
    return req,root,paths,catalogue,counts,rewrite


def test_arithmetic_known_unobserved_and_cli(fixture,tmp_path):
    req,root,paths,cat,counts,rewrite=fixture
    r=m.diagnose_native_gene_selection(req)
    assert r['status']=='ok',r
    g=r['gene_results'][0];d=g['engine_result']['denominator_rows']
    assert [(x['S_assigned_raw'],x['S_retained'],x['T'],x['I'],x['D']) for x in d]==[(100,100,80,20,40),(0,0,0,0,0)]
    assert g['native_distal_comparisons'][0]['comparison']=='matched'
    assert g['native_distal_comparisons'][1]['cell_status']=='registered_unobserved'
    request=tmp_path/'request.json';request.write_text(json.dumps(req))
    proc=subprocess.run([sys.executable,'-B',str(MODULE),'--request',str(request)],capture_output=True,text=True,timeout=10)
    assert proc.returncode==0,proc.stderr+proc.stdout
    assert json.loads(proc.stdout)==r
    assert len(proc.stdout.encode())<=1048576
    assert not list(root.rglob('*-wal'))


@pytest.mark.parametrize('kind',['unknown_cell','symbol','units','sample','namespace','hash','config','payload','schema','quiescent'])
def test_binding_refusals(fixture,kind):
    req,root,paths,cat,counts,rewrite=fixture
    if kind=='unknown_cell':req['selection'][0]['cell_barcodes']=['unknown']
    if kind=='symbol':req['selection'][0]['gene_name']='X';del req['selection'][0]['gene_id']
    if kind=='units':req['selection']=[dict(gene_id=str(i),strand='+',cell_barcodes=[str(j) for j in range(96)]) for i in range(3)]
    if kind=='sample':req['sample_id']='other'
    if kind=='namespace':req['namespace']['peak']='pk-'+'0'*16
    if kind=='hash':req['sources']['counts']['sha256']='0'*64
    if kind in ('config','schema'):
        obj=json.loads((root/paths['config']).read_text())
        if kind=='config':obj['resolved_config']['misprimed_ratio_threshold']=.8
        else:obj['schema_version']='unknown'
        rewrite('config',json.dumps(obj).encode())
    if kind=='payload':
        obj=json.loads((root/paths['manifest']).read_text());obj['records'][1]['payload']['peak']['misprime_in']=11
        rewrite('manifest',json.dumps(obj).encode())
    if kind=='quiescent':req['assertions']['quiescent']=False
    r=m.diagnose_native_gene_selection(req)
    assert r['status']=='refused',r
    assert 'gene_results' not in r


@pytest.mark.parametrize('kind',['duplicate','unknown_peak','unassigned','negative','registry'])
def test_counts_mapping(fixture,kind):
    req,root,paths,cat,counts,rewrite=fixture
    if kind=='duplicate':counts.append(counts[0])
    if kind=='unknown_peak':counts[0]['peak_name']='unknown'
    if kind=='unassigned':cat[2]['gene_id']=''
    if kind=='negative':counts[0]['umi_count']=-1
    if kind=='registry':counts[0]['cell_barcode']='outside'
    req['sources']['catalogue']['sha256']=gztable(root/paths['catalogue'],m.CAT_FIELDS,cat)
    req['sources']['counts']['sha256']=gztable(root/paths['counts'],['peak_name','cell_barcode','umi_count'],counts)
    r=m.diagnose_native_gene_selection(req)
    assert r['status']==('ok' if kind=='unassigned' else 'refused'),r
    if kind=='unassigned':assert r['count_accounting']['unassigned_umis']==20


@pytest.mark.parametrize('kind',['bad_crc','expansion','field','rows','output','json_depth','json_nodes'])
def test_source_and_aggregate_limits(fixture,kind):
    req,root,paths,cat,counts,rewrite=fixture
    if kind=='bad_crc':
        data=bytearray((root/paths['counts']).read_bytes());data[-8]^=1;rewrite('counts',bytes(data))
    if kind=='expansion':rewrite('counts',gzip.compress(b'x'*100000))
    if kind=='field':req['limits']={'field_bytes':10}
    if kind=='rows':req['limits']={'count_rows':2}
    if kind=='output':req['limits']={'output_bytes':6000}
    if kind=='json_depth':rewrite('config',b'['*30+b'0'+b']'*30)
    if kind=='json_nodes':req['limits']={'json_nodes':10}
    r=m.diagnose_native_gene_selection(req)
    assert r['status']=='refused',r


def test_changed_input_and_symlink(fixture,monkeypatch):
    req,root,paths,cat,counts,rewrite=fixture
    original=m.Sources.verify_again
    def change(self):
        (root/paths['inputs']).write_bytes(b'changed')
        original(self)
    monkeypatch.setattr(m.Sources,'verify_again',change)
    r=m.diagnose_native_gene_selection(req)
    assert r['code']=='changed_input',r
    target=root/paths['counts'];data=target.read_bytes();target.unlink()
    alt=root/'alternate.gz';alt.write_bytes(data);target.symlink_to(alt)
    r=m.diagnose_native_gene_selection(req)
    assert r['code']=='path_refused'


def test_intronic_only_and_missing_optional(fixture):
    req,root,paths,cat,counts,rewrite=fixture
    cat[2]['gene_id']='INTRON'
    req['sources']['catalogue']['sha256']=gztable(root/paths['catalogue'],m.CAT_FIELDS,cat)
    req['selection']=[dict(gene_id='INTRON',strand='+',cell_barcodes=['c0'])]
    del req['sources']['psi']
    r=m.diagnose_native_gene_selection(req)
    assert r['status']=='ok',r
    d=r['gene_results'][0]['engine_result']['denominator_rows'][0]
    assert d['I']==20 and d['T']==0 and d['D'] is None
    assert r['catalogue_accounting']['empty_native_ipui_explanation']=='missing_terminal_partner'
    for metric in r['gene_results'][0]['engine_result']['metric_rows']:
        assert metric['value_or_null'] is None
        assert 'no_eligible_terminal' in metric['reason_codes']


def test_engine_aggregate_text_limit_and_input_unchanged(fixture):
    import copy
    req,*_=fixture;before=copy.deepcopy(req)
    assert m.diagnose_native_gene_selection(req)['status']=='ok'
    assert req==before
    req['limits']={'engine_text_bytes':200}
    assert m.diagnose_native_gene_selection(req)['code']=='limit_exceeded'


def test_rehashed_but_nested_payload_inconsistent(fixture):
    req,root,paths,cat,counts,rewrite=fixture
    naming,_,_=m.helpers()
    obj=json.loads((root/paths['manifest']).read_text())
    an=obj['records'][1];an['payload']['peak']['misprime_in']=11
    replacement=naming.build_scoped_artifact_name_v1('annotation',an['payload'],legacy_name=an['legacy_name']).as_dict()
    obj['records'][1]=replacement;req['namespace']['annotation']=replacement['code']
    rewrite('manifest',json.dumps(obj).encode())
    r=m.diagnose_native_gene_selection(req)
    assert r['code']=='namespace_mismatch',r


def test_two_genes_cannot_bypass_cumulative_input_budget(fixture):
    req,root,paths,cat,counts,rewrite=fixture
    # Two isolated valid multi-terminal genes, no sample/cell cross-products.
    newcat=[dict(x,peak_name=x['peak_name']+'2',gene_id='002') for x in cat]
    newcounts=[dict(x,peak_name=x['peak_name']+'2') for x in counts]
    req['sources']['catalogue']['sha256']=gztable(root/paths['catalogue'],m.CAT_FIELDS,cat+newcat)
    req['sources']['counts']['sha256']=gztable(root/paths['counts'],['peak_name','cell_barcode','umi_count'],counts+newcounts)
    req['selection']=[dict(gene_id=g,strand='+',cell_barcodes=['c0']) for g in ['001','002']]
    del req['sources']['psi']
    successful=m.diagnose_native_gene_selection(req)
    assert successful['status']=='ok',successful
    used=successful['budget_usage']['engine_input_text_bytes']
    req['limits']={'engine_text_bytes':used-1}
    refused=m.diagnose_native_gene_selection(req)
    assert refused['code']=='limit_exceeded',refused
    assert refused['detail']=='aggregate engine text/nodes'


def test_no_inspection_or_sqlite_open_and_timeout(fixture,monkeypatch,tmp_path):
    req,*_=fixture
    import sqlite3
    def forbidden(*args,**kwargs):raise AssertionError('SQLite access forbidden')
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    assert m.diagnose_native_gene_selection(req)['status']=='ok'
    # Other test modules may already import inspection in this interpreter.
    # Verify the adapter's imports and I/O in the same strict fresh host guard.
    request=tmp_path/'no-inspection-request.json';request.write_text(json.dumps(req))
    proc=subprocess.run([sys.executable,'-B','-c',HOST_GUARD,str(MODULE),str(request)],capture_output=True,text=True,timeout=10)
    assert proc.returncode==0 and not proc.stderr,proc.stderr
    assert json.loads(proc.stdout)['status']=='ok'
    def expired(self):m.fail('timeout','cooperative deadline')
    monkeypatch.setattr(m.Sources,'tick',expired)
    assert m.diagnose_native_gene_selection(req)['code']=='timeout'


def test_native_summary_denominator_mismatch_and_unknown_metric(fixture):
    req,root,paths,cat,counts,rewrite=fixture
    psis=[dict(peak_name='D',gene_id='001',strand='+',cell_barcode='c0',peak_umi_count=40,gene_umi_count=999,psi=.5)]
    req['sources']['psi']['sha256']=gztable(root/paths['psi'],list(psis[0]),psis)
    assert m.diagnose_native_gene_selection(req)['code']=='native_summary_mismatch'
    req['metrics']=['nonsense']
    assert m.diagnose_native_gene_selection(req)['code']=='unsupported_metric_selection'


def test_combined_output_bytes_not_per_gene(fixture):
    req,root,paths,cat,counts,rewrite=fixture
    newcat=[dict(x,peak_name=x['peak_name']+'2',gene_id='002') for x in cat]
    newcounts=[dict(x,peak_name=x['peak_name']+'2') for x in counts]
    req['sources']['catalogue']['sha256']=gztable(root/paths['catalogue'],m.CAT_FIELDS,cat+newcat)
    req['sources']['counts']['sha256']=gztable(root/paths['counts'],['peak_name','cell_barcode','umi_count'],counts+newcounts)
    del req['sources']['psi']
    one=m.diagnose_native_gene_selection(req)
    assert one['status']=='ok'
    limit=len(json.dumps(one,**m.ENC))+2500
    req['limits']={'output_bytes':limit}
    assert m.diagnose_native_gene_selection(req)['status']=='ok'
    req['selection'].append(dict(gene_id='002',strand='+',cell_barcodes=['c0','c1']))
    r=m.diagnose_native_gene_selection(req)
    assert r['code']=='limit_exceeded',r


def test_external_dedup_naming_refused(fixture):
    req,root,paths,cat,counts,rewrite=fixture
    rewrite('inputs',b'sample_id,source_path,is_deduped,requires_split\ns,/source.bam,1,1\n')
    assert m.diagnose_native_gene_selection(req)['code']=='unsupported_schema'


@pytest.mark.parametrize('value',[None,[],False,17,'text'])
def test_non_object_requests_structured(value):
    assert m.diagnose_native_gene_selection(value)['code']=='invalid_request'


@pytest.mark.parametrize('role',['manifest','config'])
def test_non_object_source_json_structured(fixture,role):
    req,root,paths,cat,counts,rewrite=fixture
    rewrite(role,b'[]')
    assert m.diagnose_native_gene_selection(req)['code']=='unsupported_schema'


def test_registry_cannot_point_at_state_database(fixture):
    req,root,paths,cat,counts,rewrite=fixture
    db=root/'state/state.sqlite';put(db,b'not read')
    req['sources']['registry']={'path':str(db),'sha256':hashlib.sha256(b'not read').hexdigest()}
    assert m.diagnose_native_gene_selection(req)['code']=='unsupported_schema'


@pytest.mark.parametrize('field,value',[
    ('config_schema_version',None),('config_schema_version',True),('config_schema_version',2),
    ('config_schema_version','1'),('config_hash',None),('config_hash','bad'),
    ('config_hash','0'*64),('output_dir','/wrong'),('run_id','wrong'),
    ('output_dir',None),('run_id',None)])
def test_snapshot_integrity(fixture,field,value):
    req,root,paths,cat,counts,rewrite=fixture
    obj=json.loads((root/paths['config']).read_text())
    if value is None:obj.pop(field)
    else:obj[field]=value
    rewrite('config',json.dumps(obj).encode())
    r=m.diagnose_native_gene_selection(req)
    assert r['status']=='refused' and 'gene_results' not in r,r


@pytest.mark.parametrize('role,mutation',[
    ('ipui_site','blank_unit'),('ipui_total','blank_gene'),
    ('ipui_site','unit_shadow'),('ipui_site','pas_shadow'),
    ('ipui_total','unit_shadow'),('ipui_site','bad_strand'),
    ('ipui_site','extra_ignored'),('ipui_total','extra_ignored')])
def test_optional_native_identity(fixture,role,mutation):
    req,root,paths,cat,counts,rewrite=fixture
    row=dict(gene_id='001',strand='+',quantification_unit_id='c0',U=80)
    if role=='ipui_site':row.update(intronic_pas_id='I',I_i=20,iPUI_i=-1)
    else:row.update(I_total=20,total_iPUI=-1)
    if mutation=='blank_unit':row['quantification_unit_id']=''
    if mutation=='blank_gene':row['gene_id']=''
    if mutation=='bad_strand':row['strand']='.'
    if mutation=='unit_shadow':row.update(quantification_unit_id='unknown',cell_barcode='c0')
    if mutation=='pas_shadow':row.update(intronic_pas_id='unknown',peak_name='I')
    if mutation=='extra_ignored':row.update(cell_barcode='unknown',peak_name='unknown')
    suffix='per_site' if role=='ipui_site' else 'gene_total'
    path=paths['psi'].parent/f's.directional.annotated.intronic_ipui_{suffix}.v2.tsv.gz'
    req['sources'][role]={'path':str(path),'sha256':gztable(root/path,list(row),[row])}
    r=m.diagnose_native_gene_selection(req)
    assert r['status']==('ok' if mutation=='extra_ignored' else 'refused'),r
    if mutation!='extra_ignored':assert 'gene_results' not in r


@pytest.mark.parametrize('count',['1e1000000','1e-1000000','-1e1000000','-1e-1000000','1e999999999999999999999'])
def test_decimal_extremes_api_cli(fixture,tmp_path,count):
    req,root,paths,cat,counts,rewrite=fixture
    counts[0]['umi_count']=count
    req['sources']['counts']['sha256']=gztable(root/paths['counts'],list(counts[0]),counts)
    r=m.diagnose_native_gene_selection(req)
    assert r['status']=='refused' and r['code']=='invalid_counts' and 'gene_results' not in r,r
    request=tmp_path/'extreme.json';request.write_text(json.dumps(req))
    proc=subprocess.run([sys.executable,'-B',str(MODULE),'--request',str(request)],capture_output=True,text=True,timeout=10)
    assert proc.returncode==2 and not proc.stderr,proc.stderr
    assert json.loads(proc.stdout)==r


HOST_GUARD = r'''
import sys, json, logging, importlib.util, os
class HostLogger(logging.Logger):
    def host_method(self): return 'host-preserved'
logging.setLoggerClass(HostLogger)
def state():
    module=sys.modules.get('logging_config.runtime_session')
    return (logging.getLoggerClass(),dict(logging.Logger.manager.loggerDict),
            tuple(logging.root.handlers),getattr(module,'_ACTIVE_SESSION',None),
            tuple(sorted(n for n in sys.modules if n=='logging_config' or n.startswith('logging_config.') or n=='tqdm' or n.startswith('tqdm.') or 'inspection' in n)))
before=state();events=[]
def guard(event,args):
    if event=='sqlite3.connect': events.append(event);raise AssertionError(event)
    if event=='open':
        path,mode,flags=args
        if (isinstance(path,(str,bytes)) and 'state.sqlite' in str(path)) or (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
            events.append(str(args));raise AssertionError(args)
sys.addaudithook(guard)
spec=importlib.util.spec_from_file_location('isolated_diagnostic',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with open(sys.argv[2]) as f:request=json.load(f)
result=m.diagnose_native_gene_selection(request)
assert result['status']=='ok',result
assert state()==before,(before,state())
assert not events and not state()[-1]
assert logging.getLogger('host.after.diagnostic').host_method()=='host-preserved'
print(json.dumps(result,**m.ENC),end='')
'''


def test_success_preserves_host_process(fixture,tmp_path):
    req,*_=fixture
    request=tmp_path/'host-request.json';request.write_text(json.dumps(req))
    proc=subprocess.run([sys.executable,'-B','-c',HOST_GUARD,str(MODULE),str(request)],capture_output=True,text=True,timeout=10)
    assert proc.returncode==0 and not proc.stderr,proc.stderr
    assert json.loads(proc.stdout)['status']=='ok'


def test_canonical_json_native_parity():
    vectors=[{}, {'nested':[{},[],None,True,False,{'unicode':'中文é𝄞','escape':'\n\t\r\b\f\u0000"\\'}]},
             {'numbers':[0,-0.0,0.0,1,-1,2**53,1.2345678901234567,1e-300,1e300]},
             {'z':{},'a':[],'empty':'','null':None}]
    # Native imports occur only in this separate parity interpreter.
    code='from logging_config.runtime_session import _config_hash; import json,sys; print(json.dumps([_config_hash(v) for v in json.load(sys.stdin)]))'
    proc=subprocess.run([sys.executable,'-B','-c',code],input=json.dumps(vectors),cwd=MODULE.parents[1],capture_output=True,text=True,timeout=10)
    assert proc.returncode==0,proc.stderr
    assert [m.config_digest(v) for v in vectors]==json.loads(proc.stdout)


@pytest.mark.parametrize('fixture',['terminal-annotation-v4'],indirect=True)
def test_recorded_v4_run_is_read_like_v3(fixture):
    req,*_=fixture
    r=m.diagnose_native_gene_selection(req)
    assert r['status']=='ok',r


@pytest.mark.parametrize('fixture',['terminal-annotation-v2','terminal-annotation-v9'],indirect=True)
def test_unreadable_recorded_schema_is_refused(fixture):
    req,*_=fixture
    r=m.diagnose_native_gene_selection(req)
    assert (r['status'],r['code'])==('refused','unsupported_schema'),r
