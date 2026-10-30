"""Explicit native-v3 -> five coverage files; prepare is the sole mutator.

File-load/direct-script only. No package/native/logging/SQLite initialization.
"""
from __future__ import annotations
from collections import Counter
from contextlib import contextmanager
import csv
from decimal import Decimal, DecimalException
import gzip
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import threading
import time
from urllib.parse import quote
import zlib

MODULE=Path(__file__).absolute()
RECIPE=MODULE.with_name('native_coverage_recipe.json')
COVERAGE=MODULE.with_name('coverage_diagnostics.py')
COVERAGE_METHOD=MODULE.with_name('coverage_diagnostics_contract.json')
COVERAGE_SHA='45d597c5955eb54ddc5ab80e03006edc0b11907432c28b4b20daf197fa04df29'
COVERAGE_METHOD_SHA='34729eca65284f509ca584a85a67ff4c13bbc87029cbc997bd648c84e6a7f653'
RECIPE_SHA='a7d3c1e17b5e98cbe6ee47607e1347c4edc96caec402ca1a6072ac24edf1bf9e'
# Execute only the fixed pinned adjacent stdlib-only helper bytes. No plugin path.
try:
    with COVERAGE.open('rb') as _handle:_cov_raw=_handle.read(262145)
except OSError:_cov_raw=b''
if len(_cov_raw)>262144 or hashlib.sha256(_cov_raw).hexdigest()!=COVERAGE_SHA:
    if __name__=='__main__':
        print(json.dumps(dict(schema='scpolya.native_fixed_coverage.refusal.v1',status='refused',code='stale_producer',detail='accepted coverage helper pin mismatch',fresh_analysis=False)));raise SystemExit(2)
    raise RuntimeError('accepted coverage helper pin mismatch')
_spec=importlib.util.spec_from_file_location('_native_coverage_fixed_helpers',COVERAGE)
c=importlib.util.module_from_spec(_spec)
exec(compile(_cov_raw,str(COVERAGE),'exec'),c.__dict__)
del _cov_raw,_spec
need,fail,keys,text,sha,encode,wire=c.need,c.fail,c.keys,c.text,c.sha,c.encode,c.wire
Refusal=c.Refusal
REQUIRED=('counts','catalogue','config','manifest','inputs','cohort')
ROLES=set(REQUIRED)|{'registry'}
RECIPE_ID='all_native_v3_general_pass_terminal_sites_for_selected_units_v1'
FRESHNESS='checked_at_preparation_only'
PROJECTION_ID='barcode_group_to_cell_index_utf8_v1'
PROJECTION_RULE={'schema': 'scpolya.native_fixed_coverage.cohort_projection.v1', 'recipe_id': 'barcode_group_to_cell_index_utf8_v1', 'input_header': ['barcode', 'group'], 'sort': 'raw_barcode_utf8_ascending', 'output_header': ['cell_index', 'barcode', 'major_class', 'cell_type'], 'mapping': {'cell_index': 'zero_based_decimal_contiguous', 'barcode': 'original_barcode', 'major_class': 'original_group', 'cell_type': 'original_group'}, 'encoding': 'UTF8_LF_exact_header_final_LF_no_BOM_no_normalization'}
PROJECTION_DIGEST=sha(encode(PROJECTION_RULE))
TERMINAL={'Terminal_exon','Downstream_of_terminal_exon'}
CAT_REQUIRED=['peak_name','gene_id','strand','feature','misprimed_ratio','general_mispriming_pass','intronic_mispriming_pass','mispriming_pass','is_distal_peak','distance_to_stop_codon','n_peaks_in_gene','peak_order_in_gene','chrom','start','end']
CAT_OPTIONAL=['gene_name','peak_length','canonical_transcript','stop_codon_position','distance_to_gene','three_prime_utr_length','max_cov','total_polyA_reads','total_reads']
PRIORITY=('outside_cohort','unassigned_gene','unselected_gene','nonterminal','general_fail_terminal','retained')
CAPS=dict(cells=512,genes=64,PAS=256,N_element_max=10000000,T_element_max=10000000,source_bytes_each=33554432,source_bytes_total=67108864,metadata_bytes_each=1048576,request_bytes=65536,count_rows=1000000,catalogue_rows=20000,source_or_registry_cells=10000,expanded_bytes_total=268435456,io_bytes_total=201326592,columns=64,row_bytes=65536,field_bytes=4096,coverage_identity_bytes=256,selected_context_rows=2000,selected_context_bytes=1048576,five_file_bytes_total=2097152,attempt_output_bytes_total=4194304,CPUs=2,memory_bytes=8589934592,swap_bytes=0,thread_variables=1,Python_B=True,childless_prepare_OS_fuse_seconds=110,systemd_runtime_seconds=115,systemd_stop_seconds=5)
REDUCIBLE=set(CAPS)-{'CPUs','memory_bytes','swap_bytes','thread_variables','Python_B','systemd_runtime_seconds','systemd_stop_seconds'}
SNAPSHOTS={'adapter.py':MODULE,'recipe.json':RECIPE,'coverage.py':COVERAGE,'coverage-method.json':COVERAGE_METHOD}
FINAL_MEMBERS=set(c.ROLES)|set(SNAPSHOTS)|{'request.json','attempt-start.json','input-receipt.json','native-config.json','eligibility-lineage.json','prepared-manifest.json'}
GUARD='.publication-guard.json'


def absolute(value):
    need(type(value) is str and len(value)<=4096 and '\x00' not in value,'bounded absolute path')
    p=Path(value);need(p.is_absolute() and '..' not in p.parts,'explicit absolute path');return p

def integer(value,maximum=2**53-1,nullable=False):
    if nullable and value=='':return None
    need(type(value) is str and len(value)<=4096,'integer text')
    try:d=Decimal(value)
    except DecimalException:fail('invalid_counts','invalid integer')
    need(d.is_finite() and d==d.to_integral_value() and 0<=d<=maximum,'nonnegative exact bounded integer','invalid_counts')
    return int(d)

def number(value,nullable=False):
    if nullable and value=='':return None
    result=float(value);need(math.isfinite(result),'finite number');return result

def boolean(value,nullable=False):
    if nullable and value=='':return None
    need(value in ('True','False'),'native boolean text');return value=='True'

def producer():
    files={};raws={}
    for name,p in SNAPSHOTS.items():
        raw,ev=c.read_file(p,262144);raws[name]=raw;files[name]={k:ev[k] for k in ('path','bytes','sha256')}
    need(files['recipe.json']['sha256']==RECIPE_SHA and files['coverage.py']['sha256']==COVERAGE_SHA and files['coverage-method.json']['sha256']==COVERAGE_METHOD_SHA,'pinned dependency closure','stale_producer')
    recipe=c.parse(raws['recipe.json']);need(recipe['recipe']==RECIPE_ID and recipe['caps']==CAPS and encode(recipe['projection'])==encode(PROJECTION_RULE),'pinned recipe semantics')
    return dict(schema='scpolya.native_fixed_coverage.producer.v1',files=files,environment=c.environment()),raws

@contextmanager
def prepare_fuse(budget):
    """Main-thread scoped alarm; read-only helpers never enter this context."""
    need(threading.current_thread() is threading.main_thread(),'prepare requires main thread','runtime_unavailable')
    need(type(budget) in (int,float) and 0<budget<=110,'bounded prepare OS deadline')
    need(signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0),'preexisting real timer must remain untouched','external_timer_active')
    old_handler=signal.getsignal(signal.SIGALRM)
    old_mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM});owned=False
    started=time.monotonic()
    try:
        need(signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0) and signal.SIGALRM not in signal.sigpending(),'preexisting alarm state','external_timer_active')
        signal.signal(signal.SIGALRM,signal.SIG_DFL)
        signal.setitimer(signal.ITIMER_REAL,budget);owned=True
        signal.pthread_sigmask(signal.SIG_SETMASK,old_mask-{signal.SIGALRM})
        actual,_=signal.getitimer(signal.ITIMER_REAL);need(0<actual<=budget,'OS timer armed','runtime_unavailable')
        yield dict(mechanism='Linux SIGALRM default action via ITIMER_REAL',origin='prepare_entry_monotonic',budget_seconds=budget,initial_timer_idle=True,elapsed_before_work=time.monotonic()-started)
    finally:
        if owned:
            signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM})
            signal.setitimer(signal.ITIMER_REAL,0)
            if signal.SIGALRM in signal.sigpending():signal.sigwait({signal.SIGALRM})
            signal.signal(signal.SIGALRM,old_handler)
        signal.pthread_sigmask(signal.SIG_SETMASK,old_mask)


def effective_sources(request):
    descriptions=dict(request['sources'])
    if 'cohort_origin' in request:descriptions['original_selection']=request['cohort_origin']['original_selection']
    return descriptions

def validate_request(request,physical=True):
    need(type(request) is dict and type(request.get('sources')) is dict,'request/source object')
    extra=({'registry_sample_id'} if 'registry' in request['sources'] else set())|({'cohort_origin'} if 'cohort_origin' in request else set())
    keys(request,{'schema','run_root','sample_id','namespace','artifact_base','invocation_inputs_path','sample_source_path','groups','selected_units','recipe','sources','assertions','attempt_dir','historical_run_commit','limits'}|extra,'native preparation request')
    need(request['schema']=='scpolya.native_fixed_coverage.request.v1' and request['recipe']==RECIPE_ID,'request/recipe schema')
    text(request['sample_id']);text(request['artifact_base'])
    for key in ('invocation_inputs_path','sample_source_path'):absolute(request[key])
    if request['historical_run_commit'] is not None:text(request['historical_run_commit'])
    groups=request['groups'];need(type(groups) is list and len(groups)==2 and len(set(map(text,groups)))==2,'two declared groups')
    keys(request['namespace'],{'peak','annotation','matrix'},'namespace')
    for scope,prefix in [('peak','pk'),('annotation','an'),('matrix','mx')]:need(type(request['namespace'][scope]) is str and re.fullmatch(prefix+'-[0-9a-f]{16}',request['namespace'][scope]),'native namespace code')
    limits=dict(CAPS);over=request['limits'];need(type(over) is dict and set(over)<=REDUCIBLE,'known reducing limits')
    for key,value in over.items():need(type(value) is int and 0<value<=CAPS[key],'positive integer reducing limits');limits[key]=value
    for value in [request['sample_id'],*request['groups']]:need(len(value.encode('utf-8'))<=limits['coverage_identity_bytes'],'reduced coverage identity bound','limit_exceeded')
    need(len(wire(request))<=limits['request_bytes'],'request bytes','limit_exceeded')
    selected=request['selected_units'];need(type(selected) is list and 1<=len(selected)<=limits['genes'],'selected gene bounds','limit_exceeded')
    units=set()
    for item in selected:
        keys(item,{'gene_id','strand'},'selected unit');text(item['gene_id']);need(item['strand'] in ('+','-'),'strand')
        need(len(item['gene_id'].encode('utf-8'))<=limits['coverage_identity_bytes'] and len(unit_id(item['gene_id'],item['strand']).encode('utf-8'))<=limits['coverage_identity_bytes'],'encoded unit identity bound','limit_exceeded')
        key=item['gene_id'],item['strand'];need(key not in units,'duplicate selected unit');units.add(key)
    desc=request['sources'];need(type(desc) is dict and set(REQUIRED)<=set(desc)<=ROLES,'six required and optional registry roles')
    if 'registry' in desc:need(request['registry_sample_id']==request['sample_id'],'declared registry sample must match native/request sample','sample_binding_mismatch')
    if 'cohort_origin' in request:
        origin=request['cohort_origin'];keys(origin,{'sample_id','original_selection','recipe_id'},'optional cohort origin')
        need(origin['sample_id']==request['sample_id'] and origin['recipe_id']==PROJECTION_ID,'declared origin sample and single built-in projection recipe','sample_binding_mismatch')
    assertions={'quiescent','complete_native_count_catalogue_scope','external_cohort_sample_binding'}|({'registry_sample_binding'} if 'registry' in desc else set())
    keys(request['assertions'],assertions,'scope assertions');need(all(v is True for v in request['assertions'].values()),'scope must be explicitly declared')
    root=absolute(request['run_root']);attempt=absolute(request['attempt_dir'])
    need(str(root)==request['run_root'] and str(attempt)==request['attempt_dir'],'canonical native/attempt path spelling','path_refused')
    need(not attempt.is_relative_to(root) and not root.is_relative_to(attempt),'attempt outside native tree','path_refused')
    total=0;identities=set();paths=set()
    for role,d in effective_sources(request).items():
        keys(d,{'path','bytes','sha256'},'source descriptor');p=absolute(d['path']);need(str(p)==d['path'],'canonical source path spelling','path_refused');c.whole(d['bytes'],limits['source_bytes_each']);need(c.valid_sha(d['sha256']),'source SHA required')
        cap=limits['source_bytes_each'] if role in ('counts','catalogue') else min(limits['metadata_bytes_each'],limits['source_bytes_each'])
        need(d['bytes']<=cap,'role byte cap','limit_exceeded');total+=d['bytes']
        need(str(p) not in paths,'source path aliases');paths.add(str(p))
        need(not attempt.is_relative_to(p.parent) and not p.is_relative_to(attempt),'attempt outside all source trees','path_refused')
        if physical:
            c.path(str(p));st=p.stat();need(st.st_size==d['bytes'],'declared source size','size_mismatch')
            identity=st.st_dev,st.st_ino;need(identity not in identities,'source inode aliases');identities.add(identity)
    need(total<=limits['source_bytes_total'],'total source bytes','limit_exceeded')
    if physical:c.path(str(root),directory=True);c.path(str(attempt),exists=False);c.path(str(attempt.parent),directory=True)
    return limits


class Meter(io.RawIOBase):
    def __init__(self,raw,session,maximum):self.raw=raw;self.session=session;self.maximum=maximum;self.digest=hashlib.sha256();self.bytes=0
    def readable(self):return True
    def readinto(self,buffer):
        n=self.raw.readinto(buffer);self.bytes+=n;self.session.io+=n
        need(self.bytes<=self.maximum,'source grew beyond declared bytes','changed_input')
        need(self.session.io<=self.session.limits['io_bytes_total'],'total read IO','limit_exceeded')
        self.digest.update(memoryview(buffer)[:n]);return n

class Sources:
    def __init__(self,request,limits):
        self.descriptions=effective_sources(request);self.limits=limits;self.io=0;self.expanded=0;self.headers={}
        self.before={role:self.frame(d) for role,d in self.descriptions.items()}
    def frame(self,d):
        p=c.path(d['path']);st=p.stat();need(st.st_size==d['bytes'],'source size changed','changed_input')
        return dict(path=str(p),bytes=d['bytes'],sha256=d['sha256'],fingerprint=c.fingerprint(st))
    def stream(self,role,lines=True):
        d=self.descriptions[role];p=c.path(d['path']);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb',buffering=0) as raw:
            need(stat.S_ISREG(os.fstat(raw.fileno()).st_mode) and c.fingerprint(os.fstat(raw.fileno()))==self.before[role]['fingerprint'],'source changed before read','changed_input')
            meter=Meter(raw,self,d['bytes'])
            with io.BufferedReader(meter,buffer_size=32768) as buf:
                compressed=lines and (role in ('counts','catalogue') or (role=='registry' and p.suffix=='.gz'))
                reader=gzip.GzipFile(fileobj=buf) if compressed else buf
                try:
                    while True:
                        data=reader.readline(self.limits['row_bytes']+1) if lines else reader.read(32768)
                        if not data:break
                        if lines:
                            self.expanded+=len(data)
                            need(len(data)<=self.limits['row_bytes'] and self.expanded<=self.limits['expanded_bytes_total'],'expanded/line byte cap','limit_exceeded')
                        yield data
                finally:
                    if reader is not buf:reader.close()
                need(meter.bytes==d['bytes'] and meter.digest.hexdigest()==d['sha256'],'complete original-byte SHA','hash_mismatch')
            need(c.fingerprint(os.fstat(raw.fileno()))==self.before[role]['fingerprint'],'source changed during read','changed_input')
        need(self.frame(d)==self.before[role],'source changed after read','changed_input')
    def data(self,role):
        data=bytearray()
        for chunk in self.stream(role):
            data.extend(chunk);need(len(data)<=self.limits['metadata_bytes_each'],'metadata bytes','limit_exceeded')
        return bytes(data)
    def table(self,role,required,max_rows,exact=False,delimiter='\t',known=None):
        header=None;n=0
        for raw in self.stream(role):
            need(b'\x00' not in raw,'table control bytes')
            row=next(csv.reader([raw.decode('utf-8')],delimiter=delimiter,strict=True))
            need(len(row)<=self.limits['columns'] and all(len(v.encode('utf-8'))<=self.limits['field_bytes'] for v in row),'table field bounds','limit_exceeded')
            if header is None:
                header=row;need(len(header)==len(set(header)) and set(required)<=set(header) and (not exact or header==required) and (known is None or set(header)<=set(known)),'supported exact native header','unsupported_schema');self.headers[role]=header;continue
            n+=1;need(n<=max_rows,'table row limit','limit_exceeded');need(len(row)==len(header),'native table width','unsupported_schema')
            yield dict(zip(header,row))
        need(header is not None,'table needs header','unsupported_schema')
    def final_verify(self):
        for role in self.descriptions:
            for _ in self.stream(role,False):pass
        return {role:self.frame(d) for role,d in self.descriptions.items()}


def native_payloads(cfg):
    """Port of accepted explicit native-v3 identity, with no current defaults."""
    need(type(cfg) is dict,'resolved config object')
    def required(key):
        need(key in cfg and cfg[key] is not None and cfg[key]!='','missing active native config: '+key,'config_mismatch');return cfg[key]
    def choice(key,values):
        value=required(key);need(type(value) is str and value in values,'config enum: '+key,'config_mismatch');return value
    def num(key,integral=False,ratio=False,nonnegative=False):
        v=required(key);need(type(v) in ((int,) if integral else (int,float)) and math.isfinite(v),'config number: '+key,'config_mismatch')
        need((not ratio or 0<=v<=1) and (not nonnegative or v>=0),'config range: '+key,'config_mismatch');return v
    scaling=choice('coverage_scaling',('none','expr','polyA','both'));mode=choice('quantification_mode',('terminal-aware',))
    dedup=choice('dedup_method',('directional','unique','cluster','adjacency'))
    poly=[num(k,True) for k in ('minpolyA','nonA_allowed','minMAPQ')]
    def threshold(prefix):
        suffix='_polyA' if prefix=='polyA' else ''
        if choice(prefix+'_peak_threshold_mode',('fixed','dynamic'))=='fixed':
            scaled=scaling in (('polyA','both') if prefix=='polyA' else ('expr','both'))
            return str(num(('scaled' if scaled else 'unscaled')+'_depth_threshold'+suffix,nonnegative=True))
        statistic=choice(prefix+'_dynamic_threshold_stat',('median','mean','median_nonzero','max_norm_fixed'))
        if statistic=='max_norm_fixed':return 'dyn-max_norm_fixed-n'+str(num(prefix+'_dynamic_threshold_normalized',ratio=True))
        return f"dyn-{statistic}-a{num(prefix+'_dynamic_threshold_alpha')}-f{num(prefix+'_dynamic_threshold_floor')}"
    pk=dict(dedup_method=dedup,polyA_status='polyA-'+'-'.join(map(str,poly)),coverage_scaling=scaling,depth_threshold=threshold('exp'),depth_threshold_polyA=threshold('polyA'),merge_distance=num('merge_distance',True,nonnegative=True),merge_distance_polyA=num('merge_distance_polyA',True,nonnegative=True),misprime_A_count=num('misprime_A_count',True),misprime_in=num('misprime_in',True),misprimed_ratio_threshold=num('misprimed_ratio_threshold',ratio=True),quantification_mode=mode)
    an=dict(peak=pk,annotate_peak_source=choice('annotate_peak_source',('resolved','merged')),terminal_annotation_schema='terminal-annotation-v3')
    mx=dict(annotation=an,psi_min_gene_umis=num('psi_min_gene_umis',True,nonnegative=True),quantification=dict(quantification_mode=mode,intronic_misprimed_ratio_threshold=float(num('intronic_misprimed_ratio_threshold',ratio=True))))
    return dict(peak=pk,annotation=an,matrix=mx)


def binding_from_config(request,cfg_raw):
    """Pure saved-byte/request consistency; never opens original sources."""
    need(len(cfg_raw)==request['sources']['config']['bytes'] and sha(cfg_raw)==request['sources']['config']['sha256'],'native config snapshot original-byte binding')
    cfgdoc=c.parse(cfg_raw);need(cfgdoc.get('schema_version')=='scpolya.logging.invocation.v2' and type(cfgdoc.get('config_schema_version')) is int and cfgdoc['config_schema_version']==1,'native invocation schema','unsupported_schema')
    cfg=cfgdoc['resolved_config'];need(type(cfg) is dict and cfgdoc['config_hash']==sha(encode(cfg)),'canonical native config hash','config_mismatch')
    root=Path(request['run_root']);ns=request['namespace'];sid=request['sample_id'];base=request['artifact_base']
    need(cfgdoc['run_root']==str(root) and cfgdoc['output_dir']==cfg['output_dir'] and cfgdoc['run_id']==cfg['run_id'] and Path(cfg['output_dir'])/cfg['run_id']==root and cfg['inputs']==request['invocation_inputs_path'],'native root/input path binding','sample_binding_mismatch')
    expected=native_payloads(cfg)
    need(base==sid+'.'+cfg['dedup_method']+'.annotated','native artifact sample base','sample_binding_mismatch')
    relative={'inputs':Path('input_tables/inputs.csv'),'manifest':Path('state/artifact_name_manifest.v1.json'),'counts':Path('count_matrix')/ns['peak']/ns['annotation']/(base+'.counts.terminal-aware.v2.tsv.gz'),'catalogue':Path('matrix_summary')/ns['peak']/ns['annotation']/ns['matrix']/(ns['annotation']+'.terminal_aware_peak_to_gene.v2.tsv.gz')}
    for role,value in relative.items():need(Path(request['sources'][role]['path'])==root/value,'native namespace path: '+role,'namespace_mismatch')
    cp=Path(request['sources']['config']['path']).relative_to(root).parts
    need(len(cp)==4 and cp[:2]==('logs','invocations') and cp[-1]=='invocation.json','explicit native invocation path')
    for scope,prefix in [('peak','pk'),('annotation','an'),('matrix','mx')]:
        digest=sha(encode(dict(schema_version='scpolya.artifact_name.v1',scope=scope,payload=expected[scope])))
        need(ns[scope]==prefix+'-'+digest[:16],'scientific payload/selected namespace: '+scope,'namespace_mismatch')
    return cfg,dict(sample_id=sid,run_root=str(root),namespace=ns,artifact_base=base,config_hash=cfgdoc['config_hash'],scientific_payloads=expected,observed='native text/config/namespace consistency; not historical execution authenticity')


def bind_native(request,sources):
    cfg_raw=sources.data('config');cfg,binding=binding_from_config(request,cfg_raw)
    ns=request['namespace'];sid=request['sample_id'];expected=binding['scientific_payloads']
    manifest=c.parse(sources.data('manifest'));keys(manifest,{'schema_version','mode','hash_algorithm','short_hex_length','records'},'native artifact manifest')
    need(manifest['schema_version']=='scpolya.artifact_name_manifest.v1' and manifest['mode']=='short' and manifest['hash_algorithm']=='sha256' and type(manifest['short_hex_length']) is int and manifest['short_hex_length']==16,'native manifest schema','unsupported_schema')
    records=manifest['records'];need(type(records) is list and len(records)<=1000,'bounded native manifest records')
    seen=set();chosen={}
    for record in records:
        keys(record,{'scope','code','full_digest','legacy_name','payload'},'artifact record');scope=record['scope'];need(scope in ('peak','annotation','matrix','rna_gene'),'known native scope')
        prefix={'peak':'pk','annotation':'an','matrix':'mx','rna_gene':'rn'}[scope]
        digest=sha(encode(dict(schema_version='scpolya.artifact_name.v1',scope=scope,payload=record['payload'])))
        need(record['full_digest']==digest and record['code']==prefix+'-'+digest[:16] and record['code'] not in seen,'native digest/code uniqueness','namespace_mismatch');seen.add(record['code']);text(record['legacy_name']);need(type(record['payload']) is dict and '/' not in record['legacy_name'] and '\\' not in record['legacy_name'],'native artifact record shape')
        if scope in ns and record['code']==ns[scope]:chosen[scope]=record
    need(set(chosen)==set(ns),'exact selected native scopes','namespace_mismatch')
    need(chosen['annotation']['payload'].get('terminal_annotation_schema')=='terminal-annotation-v3','annotation identity v3 required; historical v2/absent unsupported','unsupported_schema')
    for scope in ns:need(encode(chosen[scope]['payload'])==encode(expected[scope]),'full native scientific payload: '+scope,'config_mismatch')
    rows=list(sources.table('inputs',['sample_id','source_path','is_deduped','requires_split'],1,delimiter=','))
    need(len(rows)==1 and rows[0]['sample_id']==sid and rows[0]['source_path']==request['sample_source_path'] and rows[0]['is_deduped']=='0' and rows[0]['requires_split'] in ('0','1'),'single sample input CSV/dedup naming','sample_binding_mismatch')
    return cfg,binding,cfg_raw


def read_cohort_registry(request,sources,limits):
    raw=sources.data('cohort');rows=c.tsv_rows(raw,c.HEADERS['cell-index.tsv'])
    need(1<=len(rows)<=limits['cells'],'cohort size','limit_exceeded')
    ids=[]
    for i,row in enumerate(rows):
        need(row['cell_index']==str(i),'canonical cohort indices');ids.append(text(row['barcode']));text(row['major_class']);need(all(len(row[k].encode('utf-8'))<=limits['coverage_identity_bytes'] for k in ('barcode','major_class')),'reduced cohort ID cap','limit_exceeded')
        need(all(len(value.encode('utf-8'))<=limits['field_bytes'] for value in row.values()),'cohort source field budget','limit_exceeded')
        need(len(row['cell_type'].encode())<=limits['coverage_identity_bytes'],'cell metadata bound')
    need(ids==sorted(ids) and len(ids)==len(set(ids)),'canonical unique cohort barcodes')
    counts=Counter(row['major_class'] for row in rows)
    need(set(counts)==set(request['groups']) and all(counts[g]>0 for g in request['groups']),'cohort matches declared nonempty groups')
    sources.headers['cohort']=c.HEADERS['cell-index.tsv'];registry=None
    if 'registry' in request['sources']:
        registry={}
        for i,line in enumerate(sources.stream('registry')):
            need(line.endswith(b'\n') and b'\r' not in line and not line.startswith(b'\xef\xbb\xbf'),'registry UTF8 LF headerless format','unsupported_schema')
            cell=text(line.decode('utf-8').rstrip('\n').removesuffix('\r'));need(len(cell.encode('utf-8'))<=min(limits['field_bytes'],limits['coverage_identity_bytes']),'registry field/identity cap','limit_exceeded')
            need(cell not in registry,'unique one-column registry','unsupported_schema');registry[cell]=i
            need(len(registry)<=limits['source_or_registry_cells'],'registry cap','limit_exceeded')
        need(bool(registry) and set(ids)<=set(registry),'cohort must be in declared registry','registry_mismatch')
        sources.headers['registry']=['barcode']
    if 'cohort_origin' in request:
        original=c.tsv_rows(sources.data('original_selection'),['barcode','group'])
        need(1<=len(original)<=limits['cells'],'original cohort selection bound','limit_exceeded')
        need(all(len(value.encode('utf-8'))<=min(limits['field_bytes'],limits['coverage_identity_bytes']) for r in original for value in r.values()),'original selection source field/identity bounds','limit_exceeded')
        identifiers=[text(r['barcode']) for r in original]
        need(len(set(identifiers))==len(identifiers) and set(text(r['group']) for r in original)==set(request['groups']),'unique original barcodes and declared groups')
        ordered=sorted(enumerate(original),key=lambda pair:pair[1]['barcode'].encode('utf-8'))
        projected=[dict(cell_index=str(i),barcode=r['barcode'],major_class=r['group'],cell_type=r['group']) for i,(_,r) in enumerate(ordered)]
        need(table_bytes(projected,c.HEADERS['cell-index.tsv'])==raw,'exact original-selection projection/cohort bytes','cohort_projection_mismatch')
        sources.headers['original_selection']=['barcode','group']
        origin=dict(mode='checked_builtin_projection',sample_id=request['cohort_origin']['sample_id'],original_selection=request['cohort_origin']['original_selection'],canonical_cohort=request['sources']['cohort'],recipe_id=PROJECTION_ID,recipe=PROJECTION_RULE,recipe_digest=PROJECTION_DIGEST,projection_status='exact_bytes_match',sample_origin='caller_asserted',row_mapping=[dict(canonical_cell_index=i,original_data_row=j,barcode=r['barcode'],group=r['group']) for i,(j,r) in enumerate(ordered)])
    else:origin=dict(mode='direct_canonical_authority',canonical_cohort=request['sources']['cohort'],sample_origin='caller_asserted',projection_status='not_assessed')
    return raw,rows,registry,origin


def unit_id(gene,strand):return text(quote(gene,safe='')+'|'+quote(strand,safe=''))

def catalogue_semantics(row,cfg,limits):
    need(type(row) is dict and set(CAT_REQUIRED)<=set(row)<=set(CAT_REQUIRED+CAT_OPTIONAL),'saved native catalogue schema')
    need(all(type(v) is str and len(v.encode('utf-8'))<=limits['field_bytes'] for v in row.values()),'native catalogue text/field bounds')
    peak=text(row['peak_name']);need(len(peak.encode('utf-8'))<=limits['coverage_identity_bytes'],'PAS identity bound','limit_exceeded')
    gene=row['gene_id'];strand=row['strand']
    if gene:text(gene);need(strand in ('+','-'),'assigned gene strand')
    else:need(strand in ('+','-','.',''),'unassigned strand')
    text(row['feature']);text(row['chrom']);a=integer(row['start']);b=integer(row['end']);need(b>a,'native interval')
    ratio=number(row['misprimed_ratio'],True);need(ratio is None or 0<=ratio<=1,'ratio domain')
    general=boolean(row['general_mispriming_pass']);intronic=boolean(row['intronic_mispriming_pass']);passed=boolean(row['mispriming_pass'])
    need(general==(ratio is not None and ratio<=cfg['misprimed_ratio_threshold']) and intronic==(ratio is not None and ratio<=cfg['intronic_misprimed_ratio_threshold']) and passed==(intronic if row['feature']=='Intron' else general),'native pass flags vs bound config','config_mismatch')
    order=integer(row['peak_order_in_gene'],limits['catalogue_rows'],True);K=integer(row['n_peaks_in_gene'],limits['catalogue_rows'],True)
    need((order is None or order>=1) and (K is None or K>=1),'positive nullable native order/count')
    boolean(row['is_distal_peak'],True);number(row['distance_to_stop_codon'],True)
    key=gene,strand;is_eligible=bool(gene and row['feature'] in TERMINAL and general)
    return peak,key,general,order,K,is_eligible


def parse_catalogue(request,sources,cfg,limits):
    selected={(s['gene_id'],s['strand']) for s in request['selected_units']}
    selected_seen=set();catalogue={};eligible={key:[] for key in selected};contexts=[];context_bytes=0
    for i,row in enumerate(sources.table('catalogue',CAT_REQUIRED,limits['catalogue_rows'],known=CAT_REQUIRED+CAT_OPTIONAL)):
        peak,key,general,order,K,is_eligible=catalogue_semantics(row,cfg,limits)
        need(peak not in catalogue,'shared/duplicate PAS mapping','ambiguous_mapping')
        catalogue[peak]=dict(ordinal=i,key=key,feature=row['feature'],general=general,eligible=is_eligible)
        if key in selected:
            selected_seen.add(key)
            context=dict(catalogue_data_row=i,primary_key={'peak_name':peak},fields=row,eligible=is_eligible,exclusion_reason=None if is_eligible else 'nonterminal' if row['feature'] not in TERMINAL else 'general_fail_terminal')
            context_bytes+=len(wire(context));need(len(contexts)<limits['selected_context_rows'] and context_bytes<=limits['selected_context_bytes'],'selected context ledger cap','limit_exceeded');contexts.append(context)
            if is_eligible:eligible[key].append(dict(peak_name=peak,order=order,K=K,catalogue_data_row=i))
    need(selected_seen==selected,'selected gene unit absent','unsupported_selected_unit')
    features=[];genes=[]
    for key in sorted(selected,key=lambda key:unit_id(*key).encode('utf-8')):
        rows=eligible[key];K=len(rows)
        need(K>=2,'selected gene lacks two structurally eligible PAS: '+unit_id(*key),'unsupported_selected_unit')
        need(sorted(r['order'] for r in rows if r['order'] is not None)==list(range(1,K+1)) and all(r['K']==K for r in rows),'complete native eligible order/count','inconsistent_source')
        start=len(features);unit=unit_id(*key)
        for row in sorted(rows,key=lambda r:(r['order'],r['peak_name'].encode('utf-8'))):
            features.append(dict(feature_index=len(features),terminal_gene_unit_id=unit,gene_id=key[0],strand=key[1],peak_order_in_gene=row['order'],peak_name=row['peak_name'],catalogue_data_row=row['catalogue_data_row']))
        genes.append(dict(gene_index=len(genes),terminal_gene_unit_id=unit,gene_id=key[0],strand=key[1],start=start,stop=len(features),K=K))
    need(len(features)<=limits['PAS'] and len(genes)<=limits['genes'],'resolved PAS/gene bounds before allocation','limit_exceeded')
    return catalogue,features,genes,contexts


def build_counts(request,sources,cells,registry,catalogue,features,genes,limits):
    # Dimensions were resolved and checked before these small allocations.
    N=[[0]*len(features) for _ in cells];T=[[0]*len(genes) for _ in cells]
    cell_index={row['barcode']:i for i,row in enumerate(cells)};peak_index={row['peak_name']:i for i,row in enumerate(features)}
    selected={(row['gene_id'],row['strand']) for row in request['selected_units']}
    gene_index={f['peak_name']:g['gene_index'] for g in genes for f in features[g['start']:g['stop']]}
    observed={};seen=set();account={key:dict(rows=0,UMI=0) for key in PRIORITY}
    raw_total=0;count_rows=0
    for row in sources.table('counts',['peak_name','cell_barcode','umi_count'],limits['count_rows'],exact=True):
        peak=text(row['peak_name']);cell=text(row['cell_barcode']);need(len(cell.encode('utf-8'))<=limits['coverage_identity_bytes'],'source cell identity bound','limit_exceeded');value=integer(row['umi_count'],limits['N_element_max'])
        need(peak in catalogue,'count PAS absent from catalogue','unknown_peak')
        if registry is not None:need(cell in registry,'source count cell not in registry','registry_mismatch')
        if cell not in observed:
            need(len(observed)<limits['source_or_registry_cells'],'source cell cap','limit_exceeded');observed[cell]=len(observed)
        packed=catalogue[peak]['ordinal']*limits['source_or_registry_cells']+observed[cell]
        need(packed not in seen,'duplicate native count pair, including excluded rows','duplicate_count_pair');seen.add(packed)
        cat=catalogue[peak]
        reason=('outside_cohort' if cell not in cell_index else 'unassigned_gene' if not cat['key'][0] else 'unselected_gene' if cat['key'] not in selected else 'nonterminal' if cat['feature'] not in TERMINAL else 'general_fail_terminal' if not cat['general'] else 'retained')
        account[reason]['rows']+=1;account[reason]['UMI']+=value;raw_total+=value;count_rows+=1
        need(raw_total<=limits['count_rows']*limits['N_element_max']<2**53,'bounded raw integer accumulation')
        if reason=='retained':
            i=cell_index[cell];j=peak_index[peak];g=gene_index[peak]
            need(T[i][g]<=limits['T_element_max']-value,'selected T per-element cap','limit_exceeded')
            N[i][j]=value;T[i][g]+=value
    need(sum(r['rows'] for r in account.values())==count_rows and sum(r['UMI'] for r in account.values())==raw_total,'disjoint accounting partition')
    need(sum(map(sum,N))==sum(map(sum,T))==account['retained']['UMI'],'retained integer ledger')
    unobserved=[cell['barcode'] for cell in cells if cell['barcode'] not in observed]
    accounting=dict(catalogue_rows=len(catalogue),count_rows=count_rows,raw_UMI=raw_total,retained_UMI=account['retained']['UMI'],categories=account,retained_cells=len(cells),retained_genes=len(genes),retained_PAS=len(features),globally_zero_PAS_retained=sum(all(row[j]==0 for row in N) for j in range(len(features))),globally_zero_gene_retained=sum(all(row[j]==0 for row in T) for j in range(len(genes))),source_observed_cells=len(observed),selected_cells_without_source_rows=unobserved,selected_cells_zero_after_eligible_selection=[cells[i]['barcode'] for i,row in enumerate(T) if not any(row)],unobserved_membership='registered_and_declared' if registry is not None else 'declared_only_not_registry_proven')
    return N,T,accounting


def table_bytes(rows,fields):
    out=io.StringIO(newline='');writer=csv.DictWriter(out,fieldnames=fields,delimiter='\t',lineterminator='\n');writer.writeheader()
    for row in rows:writer.writerow({key:row[key] for key in fields})
    return out.getvalue().encode('utf-8')

def npy_bytes(rows):
    import numpy as np
    need(np.__version__==c.environment()['numpy_version'],'NumPy distribution/runtime version agreement','stale_environment')
    array=np.array(rows,dtype='<i8',order='C');out=io.BytesIO();np.save(out,array,allow_pickle=False);return out.getvalue()

def make_cohort_origin(path,sample_id):
    raw,ev=c.read_file(path,CAPS['metadata_bytes_each'])
    return dict(sample_id=sample_id,original_selection={k:ev[k] for k in ('path','bytes','sha256')},recipe_id=PROJECTION_ID)

def make_request(paths,*,run_root,sample_id,namespace,artifact_base,invocation_inputs_path,sample_source_path,groups,selected_units,attempt_dir,limits=None,historical_run_commit=None,cohort_origin=None,registry_sample_id=None):
    need(type(paths) is dict and set(REQUIRED)<=set(paths)<=ROLES,'explicit native source roles')
    sources={}
    for role,p in paths.items():
        raw,ev=c.read_file(p,CAPS['source_bytes_each']);sources[role]={k:ev[k] for k in ('path','bytes','sha256')}
    assertions=dict(quiescent=True,complete_native_count_catalogue_scope=True,external_cohort_sample_binding=True)
    if 'registry' in sources:assertions['registry_sample_binding']=True
    extra={}
    if 'registry' in sources:extra['registry_sample_id']=sample_id if registry_sample_id is None else registry_sample_id
    if cohort_origin is not None:extra['cohort_origin']=cohort_origin
    return dict(schema='scpolya.native_fixed_coverage.request.v1',run_root=run_root,sample_id=sample_id,namespace=namespace,artifact_base=artifact_base,invocation_inputs_path=invocation_inputs_path,sample_source_path=sample_source_path,groups=groups,selected_units=selected_units,recipe=RECIPE_ID,sources=sources,assertions=assertions,attempt_dir=attempt_dir,historical_run_commit=historical_run_commit,limits={} if limits is None else limits,**extra)


def _publish(root,name):
    os.link(root/('.'+name+'.pending'),root/name);(root/('.'+name+'.pending')).unlink()

def publication_failure(root,stage,durable,exc,request_digest,limit):
    try:(root/'prepared-manifest.json').lstat();visible=True
    except FileNotFoundError:visible=False
    except OSError:visible=None
    state=('visible_durable_but_operation_failed' if durable else 'visible_durability_uncertain') if visible is True else 'not_published' if visible is False else 'visibility_unknown'
    code='publication_uncertain' if visible is not False and not durable else exc.code if isinstance(exc,Refusal) else 'operation_failed'
    record=dict(schema='scpolya.native_fixed_coverage.failure.v1',status='failed_or_incomplete',request_digest=request_digest,code=code,detail=str(exc)[:240],complete_manifest_published=visible,publication_state=state,publication_stage=stage,manifest_directory_durability='confirmed' if durable else 'uncertain' if visible is not False else 'not_applicable')
    try:c.write_new(root,'failure.json',wire(record),limit);c.fsync_directory(root)
    except (OSError,Refusal):pass
    return code


def expected_provenance(request):
    return dict(observed='native byte/schema/config/sample/path/namespace consistency',declared=request['assertions'],historical_run_commit=request['historical_run_commit'],external_cohort_sample_origin='caller_asserted',external_registry_sample_origin='caller_asserted' if 'registry' in request['sources'] else 'not_supplied',registry_sample_id=request.get('registry_sample_id'),registry_membership_meaning='listed_in_supplied_declared_registry_not_BAM_proof' if 'registry' in request['sources'] else 'no_registry_proof',source_cell_registry_membership='checked_against_declared_registry' if 'registry' in request['sources'] else 'not_assessed',original_input_copy_origin='not_authenticated',native_completion='not_assessed',scientific_review='not_reviewed')


def prepare(request):
    """Sole mutator. Prefer a dedicated CLI; library signal state is scoped."""
    need(type(request) is dict and type(request.get('limits')) is dict,'request/limits object')
    budget=request['limits'].get('childless_prepare_OS_fuse_seconds',110)
    need(type(budget) is int and 0<budget<=110,'integer prepare fuse limit')
    with prepare_fuse(budget) as fuse:
        runtime=c.runtime_envelope();limits=validate_request(request);prod,snapshots=producer()
        root=Path(request['attempt_dir']);need(not root.exists(),'existing attempt cannot be reused','refused_existing_output')
        session=Sources(request,limits);digest=sha(encode(request));selection_digest=sha(encode(request['selected_units']))
        start=dict(schema='scpolya.native_fixed_coverage.attempt_start.v1',request_digest=digest,selection_digest=selection_digest,producer=prod,runtime=runtime,fuse=fuse,source_before=session.before,attempt_dir=str(root))
        need(len(wire(start))+len(wire(request))+sum(map(len,snapshots.values()))+request['sources']['config']['bytes']+32768<=limits['attempt_output_bytes_total'],'attempt metadata/failure budget before directory creation','limit_exceeded')
        try:root.mkdir()
        except FileExistsError:fail('refused_existing_output','exclusive attempt creation lost')
        stage='start';durable=False;started=time.monotonic()
        try:
            c.write_new(root,'attempt-start.json',wire(start),limits['attempt_output_bytes_total'])
            c.write_new(root,'request.json',wire(request),limits['attempt_output_bytes_total'])
            for name,data in snapshots.items():c.write_new(root,name,data,limits['attempt_output_bytes_total'])
            cfg,binding,cfg_raw=bind_native(request,session)
            cohort_raw,cells,registry,cohort_origin=read_cohort_registry(request,session,limits)
            catalogue,features,genes,contexts=parse_catalogue(request,session,cfg,limits)
            N,T,account=build_counts(request,session,cells,registry,catalogue,features,genes,limits)
            cell_map=[dict(row,cohort_data_row=i,registry_data_row=None if registry is None else registry[row['barcode']]) for i,row in enumerate(cells)]
            lineage=dict(schema='scpolya.native_fixed_coverage.lineage.v1',recipe=RECIPE_ID,selected_units=request['selected_units'],native_binding=binding,native_source_tables=session.headers,source_descriptors=effective_sources(request),cohort_origin=cohort_origin,columns=features,genes=genes,cells=cell_map,selected_context=contexts,accounting=account,N_lineage='unique original count primary key (peak_name,cell_barcode); absent pair is sparse zero under declared complete scope',T_lineage='sum N over each explicit half-open gene block',provenance=expected_provenance(request))
            need(len(wire(lineage))<=limits['selected_context_bytes'],'whole eligibility/lineage ledger bound','limit_exceeded')
            payloads={'N.npy':npy_bytes(N),'T.npy':npy_bytes(T),'cell-index.tsv':cohort_raw,'feature-index.tsv':table_bytes(features,c.HEADERS['feature-index.tsv']),'gene-index.tsv':table_bytes(genes,c.HEADERS['gene-index.tsv']),'eligibility-lineage.json':wire(lineage),'native-config.json':cfg_raw}
            need(sum(len(payloads[name]) for name in c.ROLES)<=limits['five_file_bytes_total'],'five-file aggregate byte cap','limit_exceeded')
            need(c.npy_header(payloads['N.npy'])==[len(cells),len(features)] and c.npy_header(payloads['T.npy'])==[len(cells),len(genes)],'saved NPY header/shape')
            for name in ('cell-index.tsv','feature-index.tsv','gene-index.tsv'):c.tsv_rows(payloads[name],c.HEADERS[name])
            for name,data in payloads.items():
                c.write_new(root,'.'+name+'.pending',data,limits['attempt_output_bytes_total'])
                need(c.read_file(root/('.'+name+'.pending'),limits['attempt_output_bytes_total'])[0]==data,'actual delivered payload readback','changed_output')
            after=session.final_verify();need(after==session.before,'source before/after agreement','changed_input')
            need(producer()[0]==prod,'producer/environment changed','stale_producer')
            receipt=wire(dict(schema='scpolya.native_fixed_coverage.input_receipt.v1',before=session.before,after=after,io_bytes=session.io,expanded_bytes=session.expanded,headers=session.headers))
            payloads['input-receipt.json']=receipt
            c.write_new(root,'.input-receipt.json.pending',receipt,limits['attempt_output_bytes_total'])
            need(c.read_file(root/'.input-receipt.json.pending',limits['attempt_output_bytes_total'])[0]==receipt,'receipt readback')
            for name in sorted(payloads):_publish(root,name)
            files={name:c.descriptor(root,name) for name in sorted(FINAL_MEMBERS-{'prepared-manifest.json'})}
            manifest=dict(schema='scpolya.native_fixed_coverage.prepared.v1',status='prepared',execution_state='completed',review_state='not_reviewed',native_input_completion_state='not_assessed',data_state='all_zero' if account['retained_UMI']==0 else 'contains_counts',attempt_dir=str(root),request_digest=digest,selection_digest=selection_digest,recipe=RECIPE_ID,producer=prod,limits=limits,shape=[len(cells),len(features)],gene_count=len(genes),sample_id=request['sample_id'],groups=request['groups'],sources=effective_sources(request),cohort_origin=cohort_origin,native_binding=binding,declared_assertions=request['assertions'],runtime=runtime,fuse=fuse,wall_seconds=time.monotonic()-started,files=files,checks=dict(full_native_streams_checked=True,source_before_after_unchanged=True,all_fixed_zeros_preserved=True,N_T_block_sums=True,disjoint_count_accounting=True,delivered_readback=True))
            c.write_new(root,GUARD,wire(dict(schema='scpolya.native_fixed_coverage.publication_guard.v1',request_digest=digest,purpose='Refuse reuse until final manifest directory sync succeeds.')),limits['attempt_output_bytes_total'])
            c.write_new(root,'.prepared-manifest.json.pending',wire(manifest),limits['attempt_output_bytes_total'])
            stage='syncing_before_final_link';c.fsync_directory(root)
            stage='linking_final_manifest';_publish(root,'prepared-manifest.json')
            stage='syncing_final_manifest_directory';c.fsync_directory(root);durable=True
            stage='removing_completion_guard';(root/GUARD).unlink();stage='complete'
            return dict(schema='scpolya.native_fixed_coverage.prepare_response.v1',status='prepared',fresh_preparation=True,fresh_analysis=False,prepared_ref=dict(path=str(root/'prepared-manifest.json'),sha256=sha(wire(manifest))),shape=manifest['shape'],gene_count=len(genes),data_state=manifest['data_state'],accounting=account,execution_state='completed',review_state='not_reviewed',native_input_completion_state='not_assessed',native_freshness=FRESHNESS)
        except BaseException as exc:
            code=publication_failure(root,stage,durable,exc,digest,limits['attempt_output_bytes_total'])
            if isinstance(exc,Refusal) and exc.code==code:raise
            fail(code,type(exc).__name__)


def _load_prepared(ref):
    prod,_=producer();raw,_=c.read_ref(ref,CAPS['attempt_output_bytes_total']);m=c.parse(raw)
    keys(m,{'schema','status','execution_state','review_state','native_input_completion_state','data_state','attempt_dir','request_digest','selection_digest','recipe','producer','limits','shape','gene_count','sample_id','groups','sources','cohort_origin','native_binding','declared_assertions','runtime','fuse','wall_seconds','files','checks'},'prepared manifest')
    need(m['schema']=='scpolya.native_fixed_coverage.prepared.v1' and m['status']=='prepared' and m['execution_state']=='completed' and m['review_state']=='not_reviewed' and m['native_input_completion_state']=='not_assessed','prepared state axes')
    need(m['producer']==prod and m['recipe']==RECIPE_ID,'current producer/recipe/environment identity','stale_prepared')
    root=c.path(m['attempt_dir'],directory=True);need(ref['path']==str(root/'prepared-manifest.json'),'explicit prepared identity')
    members=list(root.iterdir());need(len(members)==len(FINAL_MEMBERS) and {p.name for p in members}==FINAL_MEMBERS and all(p.is_file() and not p.is_symlink() for p in members),'complete exact prepared members; guard/failure refuses','stale_prepared')
    keys(m['files'],FINAL_MEMBERS-{'prepared-manifest.json'},'prepared file set');payload={}
    for name,d in m['files'].items():
        keys(d,{'path','bytes','sha256'},'prepared descriptor');need(d['path']==name,'flat member pointer')
        payload[name]=c.read_file(root/name,CAPS['metadata_bytes_each'] if name=='native-config.json' else CAPS['attempt_output_bytes_total'],d['bytes'],d['sha256'])[0]
    request=c.parse(payload['request.json']);limits=validate_request(request,False)
    need(sum(p.stat().st_size for p in members)<=limits['attempt_output_bytes_total'],'whole prepared bytes')
    need(m['limits']==limits and m['request_digest']==sha(encode(request)) and m['selection_digest']==sha(encode(request['selected_units'])),'request/selection/limit binding')
    need(m['attempt_dir']==request['attempt_dir'] and m['sources']==effective_sources(request) and m['sample_id']==request['sample_id'] and m['groups']==request['groups'] and m['declared_assertions']==request['assertions'],'prepared scope binding')
    need(len(payload['native-config.json'])<=limits['metadata_bytes_each'],'saved native config byte cap')
    cfg,expected_binding=binding_from_config(request,payload['native-config.json'])
    need(encode(m['native_binding'])==encode(expected_binding),'exact saved native binding/request/config consistency')
    for name in SNAPSHOTS:need(sha(payload[name])==prod['files'][name]['sha256'],'exact closure snapshots')
    start=c.parse(payload['attempt-start.json']);keys(start,{'schema','request_digest','selection_digest','producer','runtime','fuse','source_before','attempt_dir'},'start receipt')
    need(start['schema']=='scpolya.native_fixed_coverage.attempt_start.v1' and start['request_digest']==m['request_digest'] and start['selection_digest']==m['selection_digest'] and start['producer']==prod and start['runtime']==m['runtime'] and start['fuse']==m['fuse'] and start['attempt_dir']==str(root),'attempt start binding')
    receipt=c.parse(payload['input-receipt.json']);keys(receipt,{'schema','before','after','io_bytes','expanded_bytes','headers'},'input receipt')
    need(receipt['schema']=='scpolya.native_fixed_coverage.input_receipt.v1' and receipt['before']==receipt['after']==start['source_before'],'recorded source stability')
    keys(receipt['before'],effective_sources(request),'source frame roles')
    for role,d in effective_sources(request).items():
        frame=receipt['before'][role];keys(frame,{'path','bytes','sha256','fingerprint'},'source frame')
        need(all(frame[k]==d[k] for k in d) and type(frame['fingerprint']) is list and len(frame['fingerprint'])==5,'source evidence identities')
        for v in frame['fingerprint']:c.whole(v,2**64-1)
    c.whole(receipt['io_bytes'],limits['io_bytes_total']);c.whole(receipt['expanded_bytes'],limits['expanded_bytes_total'])
    c.verify_runtime_record(m['runtime'])
    fuse=m['fuse'];keys(fuse,{'mechanism','origin','budget_seconds','initial_timer_idle','elapsed_before_work'},'prepare fuse')
    need(fuse['mechanism']=='Linux SIGALRM default action via ITIMER_REAL' and fuse['origin']=='prepare_entry_monotonic' and type(fuse['budget_seconds']) is int and fuse['budget_seconds']==limits['childless_prepare_OS_fuse_seconds'] and fuse['initial_timer_idle'] is True and type(fuse['elapsed_before_work']) is float and 0<=fuse['elapsed_before_work']<110,'recorded own scoped OS fuse')
    need(type(m['wall_seconds']) is float and math.isfinite(m['wall_seconds']) and 0<=m['wall_seconds']<=120,'bounded recorded wall')
    checks=dict(full_native_streams_checked=True,source_before_after_unchanged=True,all_fixed_zeros_preserved=True,N_T_block_sums=True,disjoint_count_accounting=True,delivered_readback=True)
    need(encode(m['checks'])==encode(checks),'exact completed checks')
    ledger=c.parse(payload['eligibility-lineage.json'])
    keys(ledger,{'schema','recipe','selected_units','native_binding','native_source_tables','source_descriptors','cohort_origin','columns','genes','cells','selected_context','accounting','N_lineage','T_lineage','provenance'},'eligibility lineage')
    need(ledger['schema']=='scpolya.native_fixed_coverage.lineage.v1' and ledger['recipe']==RECIPE_ID and ledger['selected_units']==request['selected_units'] and encode(ledger['native_binding'])==encode(expected_binding) and ledger['source_descriptors']==m['sources'] and ledger['native_source_tables']==receipt['headers'] and ledger['cohort_origin']==m['cohort_origin'],'lineage source/selection binding')
    need(len(payload['eligibility-lineage.json'])<=limits['selected_context_bytes'],'lineage byte bound')
    cells,features,genes=ledger['cells'],ledger['columns'],ledger['genes']
    need(type(cells) is list and 1<=len(cells)<=limits['cells'] and type(features) is list and 2<=len(features)<=limits['PAS'] and type(genes) is list and 1<=len(genes)<=limits['genes'],'saved bounded shapes')
    need(type(m['shape']) is list and len(m['shape'])==2,'shape list')
    for value in m['shape']:c.whole(value,limits['cells']+limits['PAS'])
    c.whole(m['gene_count'],limits['genes'])
    need(m['shape']==[len(cells),len(features)] and m['gene_count']==len(genes),'saved scope dimensions')
    for role,shape in [('N.npy',[len(cells),len(features)]),('T.npy',[len(cells),len(genes)])]:need(c.npy_header(payload[role])==shape,'saved array header/schema')
    need(sum(len(payload[n]) for n in c.ROLES)<=limits['five_file_bytes_total'],'saved five-file byte bound')
    for i,row in enumerate(cells):
        keys(row,set(c.HEADERS['cell-index.tsv'])|{'cohort_data_row','registry_data_row'},'saved cell mapping')
        need(row['cell_index']==str(i) and type(row['cohort_data_row']) is int and row['cohort_data_row']==i,'cell order/row pointer');text(row['barcode']);text(row['major_class'])
        need((row['registry_data_row'] is None) if 'registry' not in request['sources'] else (type(row['registry_data_row']) is int and 0<=row['registry_data_row']<limits['source_or_registry_cells']),'registry row pointer/proof scope')
    ids=[r['barcode'] for r in cells];need(ids==sorted(ids) and len(ids)==len(set(ids)) and set(r['major_class'] for r in cells)==set(request['groups']),'saved cohort identity')
    expected_units={unit_id(s['gene_id'],s['strand']) for s in request['selected_units']};seen_units=[];cursor=0;seen_peaks=set();previous=None
    for i,g in enumerate(genes):
        keys(g,{'gene_index','terminal_gene_unit_id','gene_id','strand','start','stop','K'},'gene mapping')
        for k in ('gene_index','start','stop','K'):c.whole(g[k],limits['PAS'])
        need(g['gene_index']==i and g['terminal_gene_unit_id']==unit_id(g['gene_id'],g['strand']) and g['start']==cursor and 2<=g['K']==g['stop']-cursor and g['stop']<=len(features),'saved fixed gene block')
        seen_units.append(g['terminal_gene_unit_id']);cursor=g['stop']
        for j,f in enumerate(features[g['start']:g['stop']],g['start']):
            keys(f,{'feature_index','terminal_gene_unit_id','gene_id','strand','peak_order_in_gene','peak_name','catalogue_data_row'},'feature mapping')
            c.whole(f['feature_index'],limits['PAS']);c.whole(f['peak_order_in_gene'],limits['catalogue_rows']);c.whole(f['catalogue_data_row'],limits['catalogue_rows']-1)
            need(f['feature_index']==j and f['peak_order_in_gene']==j-g['start']+1 and (f['gene_id'],f['strand'],f['terminal_gene_unit_id'])==(g['gene_id'],g['strand'],g['terminal_gene_unit_id']),'feature gene/order identity')
            peak=text(f['peak_name']);need(peak not in seen_peaks,'unique saved PAS');seen_peaks.add(peak)
            order=(f['terminal_gene_unit_id'].encode(),f['peak_order_in_gene'],peak.encode());need(previous is None or previous<order,'saved canonical feature order');previous=order
    need(cursor==len(features) and len(seen_units)==len(set(seen_units)) and set(seen_units)==expected_units and seen_units==sorted(seen_units,key=lambda x:x.encode()),'exact selected unit set/order')
    for role,rows in [('cell-index.tsv',cells),('feature-index.tsv',features),('gene-index.tsv',genes)]:
        actual=c.tsv_rows(payload[role],c.HEADERS[role]);need(actual==[{k:str(row[k]) for k in c.HEADERS[role]} for row in rows],'actual saved table/header/key mapping')
    need(sha(payload['cell-index.tsv'])==request['sources']['cohort']['sha256'] and len(payload['cell-index.tsv'])==request['sources']['cohort']['bytes'],'cohort copied without selection')
    headers=receipt['headers'];need(headers['counts']==['peak_name','cell_barcode','umi_count'] and headers['cohort']==c.HEADERS['cell-index.tsv'] and set(CAT_REQUIRED)<=set(headers['catalogue'])<=set(CAT_REQUIRED+CAT_OPTIONAL) and len(headers['catalogue'])==len(set(headers['catalogue'])),'actual original table schemas')
    contexts=ledger['selected_context'];need(type(contexts) is list and len(contexts)<=limits['selected_context_rows'],'context bound')
    context_by_peak={};context_rows=set();selected={(r['gene_id'],r['strand']) for r in request['selected_units']}
    context_eligible={key:[] for key in selected};previous_row=-1
    catalogue_rows=ledger['accounting']['catalogue_rows'];c.whole(catalogue_rows,limits['catalogue_rows'])
    for context in contexts:
        keys(context,{'catalogue_data_row','primary_key','fields','eligible','exclusion_reason'},'context row')
        fields=context['fields'];keys(fields,headers['catalogue'],'actual source context fields')
        peak,key,general,order,K,eligible=catalogue_semantics(fields,cfg,limits)
        reason=None if eligible else 'nonterminal' if fields['feature'] not in TERMINAL else 'general_fail_terminal'
        need(context['primary_key']=={'peak_name':peak} and peak not in context_by_peak and type(context['eligible']) is bool,'context primary key')
        row=context['catalogue_data_row'];c.whole(row,limits['catalogue_rows']-1)
        need(key in selected and context['eligible'] is eligible and context['exclusion_reason']==reason,'saved selected context eligibility/selection semantics')
        need(previous_row<row<catalogue_rows and row not in context_rows,'unique original ordered context row');previous_row=row;context_rows.add(row)
        context_by_peak[peak]=context
        if eligible:context_eligible[key].append((peak,order,K))
    need({peak for peak,ctx in context_by_peak.items() if ctx['eligible']}==seen_peaks,'eligible contexts and feature columns must be bijective')
    for gene in genes:
        rows=context_eligible[(gene['gene_id'],gene['strand'])];K=gene['K']
        need(len(rows)==K and sorted(order for _,order,_ in rows if order is not None)==list(range(1,K+1)) and all(count==K for _,_,count in rows),'saved eligible native K/order completeness')
    for feature in features:
        ctx=context_by_peak[feature['peak_name']];fields=ctx['fields']
        need(ctx['catalogue_data_row']==feature['catalogue_data_row'] and fields['gene_id']==feature['gene_id'] and fields['strand']==feature['strand'] and integer(fields['peak_order_in_gene'])==feature['peak_order_in_gene'],'feature context/row/identity pointer')
    origin=m['cohort_origin']
    if 'cohort_origin' in request:
        keys(origin,{'mode','sample_id','original_selection','canonical_cohort','recipe_id','recipe','recipe_digest','projection_status','sample_origin','row_mapping'},'checked cohort origin')
        need(origin['mode']=='checked_builtin_projection' and origin['sample_id']==request['sample_id'] and origin['original_selection']==request['cohort_origin']['original_selection'] and origin['canonical_cohort']==request['sources']['cohort'] and origin['recipe_id']==PROJECTION_ID and encode(origin['recipe'])==encode(PROJECTION_RULE) and origin['recipe_digest']==PROJECTION_DIGEST and origin['projection_status']=='exact_bytes_match' and origin['sample_origin']=='caller_asserted' and headers['original_selection']==['barcode','group'],'projection source/recipe/cohort identity')
        mapping=origin['row_mapping'];need(type(mapping) is list and len(mapping)==len(cells),'projection row mapping count')
        seen=set()
        for i,row in enumerate(mapping):
            keys(row,{'canonical_cell_index','original_data_row','barcode','group'},'projection row key')
            c.whole(row['original_data_row'],len(cells)-1);need(type(row['canonical_cell_index']) is int and row['canonical_cell_index']==i and row['original_data_row'] not in seen and row['barcode']==cells[i]['barcode'] and row['group']==cells[i]['major_class']==cells[i]['cell_type'],'exact saved original projection mapping');seen.add(row['original_data_row'])
    else:
        need(origin==dict(mode='direct_canonical_authority',canonical_cohort=request['sources']['cohort'],sample_origin='caller_asserted',projection_status='not_assessed'),'direct authority must not invent original selection/projection')
    account=ledger['accounting'];keys(account,{'catalogue_rows','count_rows','raw_UMI','retained_UMI','categories','retained_cells','retained_genes','retained_PAS','globally_zero_PAS_retained','globally_zero_gene_retained','source_observed_cells','selected_cells_without_source_rows','selected_cells_zero_after_eligible_selection','unobserved_membership'},'accounting')
    for k in set(account)-{'categories','selected_cells_without_source_rows','selected_cells_zero_after_eligible_selection','unobserved_membership'}:c.whole(account[k],CAPS['count_rows']*CAPS['N_element_max'])
    keys(account['categories'],PRIORITY,'disjoint categories')
    for cat in account['categories'].values():
        keys(cat,{'rows','UMI'},'category');c.whole(cat['rows'],limits['count_rows']);c.whole(cat['UMI'],limits['count_rows']*limits['N_element_max'])
    need(sum(x['rows'] for x in account['categories'].values())==account['count_rows'] and sum(x['UMI'] for x in account['categories'].values())==account['raw_UMI'] and account['categories']['retained']['UMI']==account['retained_UMI'],'saved disjoint ledger consistency')
    need([account['retained_cells'],account['retained_genes'],account['retained_PAS']]==[len(cells),len(genes),len(features)] and account['globally_zero_PAS_retained']<=len(features) and account['globally_zero_gene_retained']<=len(genes),'saved accounting shape')
    need(m['data_state']==('all_zero' if account['retained_UMI']==0 else 'contains_counts') and account['unobserved_membership']==('registered_and_declared' if 'registry' in request['sources'] else 'declared_only_not_registry_proven'),'zero/unobserved semantics')
    need(type(account['selected_cells_without_source_rows']) is list and len(set(account['selected_cells_without_source_rows']))==len(account['selected_cells_without_source_rows']) and set(account['selected_cells_without_source_rows'])<=set(ids),'unobserved declared identities')
    zero_cells=account['selected_cells_zero_after_eligible_selection'];need(type(zero_cells) is list and len(set(zero_cells))==len(zero_cells) and set(account['selected_cells_without_source_rows'])<=set(zero_cells)<=set(ids),'source-row absence vs zero-after-eligible selection')
    need(encode(ledger['provenance'])==encode(expected_provenance(request)),'exact request/fixed-semantics provenance')
    return m,request,ledger,payload


def _citations(ref,m,ledger):
    citations=[]
    for role in sorted(m['sources']):
        pointer='/sources/'+c.escape_pointer(role);need(c.resolve_pointer(m,pointer)==m['sources'][role],'source pointer resolves','invalid_citation')
        citations.append(dict(path=ref['path'],sha256=ref['sha256'],schema=m['schema'],json_pointer=pointer,meaning='original bytes checked at preparation; not rehashed by bridge'))
    pointer='/cohort_origin';need(c.resolve_pointer(m,pointer)==m['cohort_origin'],'cohort origin pointer','invalid_citation')
    citations.append(dict(path=ref['path'],sha256=ref['sha256'],schema=m['schema'],json_pointer=pointer,meaning='explicit origin/projection binding; external sample origin remains declared'))
    for role,section,primary in [('cell-index.tsv','cells',['barcode']),('feature-index.tsv','columns',['feature_index','peak_name']),('gene-index.tsv','genes',['gene_index','terminal_gene_unit_id'])]:
        for i,row in enumerate(ledger[section]):
            pointer='/'+section+'/'+str(i);need(c.resolve_pointer(ledger,pointer)==row,'lineage record pointer resolves','invalid_citation')
            citations.append(dict(path=str(Path(m['attempt_dir'])/'eligibility-lineage.json'),sha256=m['files']['eligibility-lineage.json']['sha256'],schema=ledger['schema'],json_pointer=pointer,table=dict(path=str(Path(m['attempt_dir'])/role),sha256=m['files'][role]['sha256'],header=c.HEADERS[role],primary_key={key:str(row[key]) for key in primary})))
    return citations


def coverage_request_from_prepared(prepared_ref,new_coverage_attempt_dir):
    m,request,ledger,_=_load_prepared(prepared_ref);attempt=absolute(new_coverage_attempt_dir)
    need(not attempt.is_relative_to(Path(m['attempt_dir'])) and not attempt.is_relative_to(Path(request['run_root'])) and not Path(m['attempt_dir']).is_relative_to(attempt) and not Path(request['run_root']).is_relative_to(attempt),'coverage attempt outside native/prepared trees','path_refused')
    paths={role:str(Path(m['attempt_dir'])/role) for role in c.ROLES}
    coverage_request=c.make_request(paths,m['sample_id'],m['groups'],str(attempt))
    # Validate bounded metadata/index contract without numeric loads or calculation.
    c.inputs(coverage_request)
    binding={role:coverage_request['sources'][role] for role in c.ROLES}
    need(all(binding[role]['sha256']==m['files'][role]['sha256'] and binding[role]['bytes']==m['files'][role]['bytes'] for role in c.ROLES),'exact prepared five-file binding')
    envelope=dict(schema='scpolya.native_fixed_coverage.handoff.v1',stage='bridge',prepared_ref=prepared_ref,coverage_request=coverage_request,request_digest=sha(encode(coverage_request)),five_file_binding=binding,plan_ref=None,plan_digest=None,result_ref=None,cohort_origin=m['cohort_origin'],native_freshness=FRESHNESS,selection_freshness=FRESHNESS,native_origin_verifier='explicit adapter handoff verifier; plain coverage get does not verify native origin',fresh_analysis=False,execution_state='completed',review_state='not_reviewed',native_input_completion_state='not_assessed',provenance=ledger['provenance'],citations=_citations(prepared_ref,m,ledger))
    need(len(wire(envelope))<=CAPS['attempt_output_bytes_total'],'bounded handoff bytes');return envelope


def link_coverage(prepared_ref,plan_ref,result_ref=None):
    plan,_=c.checked_plan(plan_ref)
    envelope=coverage_request_from_prepared(prepared_ref,plan['request']['attempt_dir'])
    need(encode(plan['request'])==encode(envelope['coverage_request']),'plan request exactly matches generated native handoff','lineage_mismatch')
    envelope.update(stage='planned',plan_ref=plan_ref,plan_digest=plan['plan_digest'])
    if result_ref is not None:
        result=c.get_coverage(plan_ref,result_ref)
        need(result['plan_ref']==plan_ref and result['analysis_ref']==result_ref and result['fresh_analysis'] is False,'fixed coverage result identity','lineage_mismatch')
        envelope.update(stage='completed',result_ref=result_ref)
    need(len(wire(envelope))<=CAPS['attempt_output_bytes_total'],'bounded linked handoff bytes','limit_exceeded')
    return envelope


def verify_handoff(envelope_ref):
    raw,_=c.read_ref(envelope_ref,CAPS['attempt_output_bytes_total']);envelope=c.parse(raw)
    keys(envelope,{'schema','stage','prepared_ref','coverage_request','request_digest','five_file_binding','plan_ref','plan_digest','result_ref','cohort_origin','native_freshness','selection_freshness','native_origin_verifier','fresh_analysis','execution_state','review_state','native_input_completion_state','provenance','citations'},'handoff envelope')
    need(envelope['schema']=='scpolya.native_fixed_coverage.handoff.v1' and envelope['stage'] in ('bridge','planned','completed'),'handoff schema/stage')
    if envelope['stage']=='bridge':
        need(envelope['plan_ref'] is None and envelope['plan_digest'] is None and envelope['result_ref'] is None,'bridge has no inferred future refs')
        expected=coverage_request_from_prepared(envelope['prepared_ref'],envelope['coverage_request']['attempt_dir'])
    else:
        need(envelope['plan_ref'] is not None and (envelope['result_ref'] is not None if envelope['stage']=='completed' else envelope['result_ref'] is None),'explicit planned/completed refs')
        expected=link_coverage(envelope['prepared_ref'],envelope['plan_ref'],envelope['result_ref'])
    need(encode(envelope)==encode(expected),'strict generated envelope/refs/source pointers/header/key identity','lineage_mismatch')
    m,request,_,_=_load_prepared(envelope['prepared_ref']);p=absolute(envelope_ref['path'])
    for root in (m['attempt_dir'],request['run_root'],envelope['coverage_request']['attempt_dir']):need(not p.is_relative_to(Path(root)),'sidecar must live outside native/prepared/coverage roots','path_refused')
    return dict(schema='scpolya.native_fixed_coverage.verified_handoff.v1',status='verified_handoff',envelope_ref=envelope_ref,prepared_ref=envelope['prepared_ref'],plan_ref=envelope['plan_ref'],result_ref=envelope['result_ref'],stage=envelope['stage'],cohort_origin=envelope['cohort_origin'],native_freshness=FRESHNESS,selection_freshness=FRESHNESS,fresh_analysis=False,review_state='not_reviewed',native_input_completion_state='not_assessed',citations=envelope['citations'])


def guarded(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except Refusal as exc:return dict(schema='scpolya.native_fixed_coverage.refusal.v1',status='refused',code=exc.code,detail=exc.detail,fresh_analysis=False,native_freshness=FRESHNESS)
    except (OSError,ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError,DecimalException,csv.Error,EOFError,zlib.error) as exc:
        return dict(schema='scpolya.native_fixed_coverage.refusal.v1',status='refused',code='invalid_native_source_or_request',detail=type(exc).__name__,fresh_analysis=False,native_freshness=FRESHNESS)


def main(argv=None):
    def dispatch():
        parser=c.Parser(add_help=False,allow_abbrev=False);parser.add_argument('action',choices=['prepare','bridge','link','verify'])
        for flag in ('request','prepared','prepared-sha','attempt-dir','plan','plan-sha','result','result-sha','envelope','envelope-sha'):parser.add_argument('--'+flag)
        a=vars(parser.parse_args(sys.argv[1:] if argv is None else argv));action=a.pop('action');given={k for k,v in a.items() if v is not None}
        allowed={'prepare':{'request'},'bridge':{'prepared','prepared_sha','attempt_dir'},'link':{'prepared','prepared_sha','plan','plan_sha'},'verify':{'envelope','envelope_sha'}}
        if action=='link':need(given in (allowed['link'],allowed['link']|{'result','result_sha'}),'known link flags')
        else:need(given==allowed[action],'exact action arguments')
        if action=='prepare':return prepare(c.parse(c.read_file(a['request'],CAPS['request_bytes'])[0]))
        if action=='verify':return verify_handoff(dict(path=a['envelope'],sha256=a['envelope_sha']))
        ref=dict(path=a['prepared'],sha256=a['prepared_sha'])
        if action=='bridge':return coverage_request_from_prepared(ref,a['attempt_dir'])
        return link_coverage(ref,dict(path=a['plan'],sha256=a['plan_sha']),None if a['result'] is None else dict(path=a['result'],sha256=a['result_sha']))
    value=guarded(dispatch)
    if len(wire(value))>CAPS['attempt_output_bytes_total']:value=dict(schema='scpolya.native_fixed_coverage.refusal.v1',status='refused',code='limit_exceeded',detail='response bytes',fresh_analysis=False,native_freshness=FRESHNESS)
    sys.stdout.buffer.write(wire(value));return 2 if value.get('status')=='refused' else 0
if __name__=='__main__':raise SystemExit(main())
