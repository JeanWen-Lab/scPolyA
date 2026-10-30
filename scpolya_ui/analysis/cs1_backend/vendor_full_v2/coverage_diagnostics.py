"""Bounded fixed-five-file coverage: direct-script plan/run/get; stdlib until worker.

Load by file with importlib.util, not through scpolya_agent (which imports inspection).
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter
import csv
import fcntl
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import stat
import struct
import subprocess
import sys
import time

MODULE = Path(__file__).absolute()
METHOD = MODULE.with_name('coverage_diagnostics_contract.json')
EXPECTED_METHOD_SHA256 = 'a8a1f42b00f4742fbd2af3c53fbb0a8af9200eea7a99b6e1ecebefd8cca62b04'
ENC = dict(sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
ROLES = ('N.npy', 'T.npy', 'cell-index.tsv', 'feature-index.tsv', 'gene-index.tsv')
HEADERS = {
 'cell-index.tsv': ['cell_index','barcode','major_class','cell_type'],
 'feature-index.tsv': ['feature_index','terminal_gene_unit_id','peak_order_in_gene','peak_name'],
 'gene-index.tsv': ['gene_index','terminal_gene_unit_id','start','stop','K']}
CAPS = {'cells': 8192, 'genes': 1024, 'PAS': 4096, 'maximum_count': 10000000, 'input_bytes_total': 402653184, 'output_bytes_total': 536870912, 'wall_seconds_max': 6900}
THREADS = ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','VECLIB_MAXIMUM_THREADS')
GG_FIELDS = ['terminal_gene_unit_id','group','n_cells_group','total_terminal_UMI','n_cells_T_positive','n_cells_T_zero','median_T_all_cells','median_T_positive_cells','n_fixed_eligible_PAS','n_PAS_positive_pooled_within_group','pooled_PAS_counts_in_frozen_order','PAS_identities_in_frozen_order','second_largest_pooled_PAS_count_in_group','n_cells_with_at_least_two_positive_PAS','fraction_cells_T_positive','fraction_cells_with_at_least_two_positive_PAS']
CELL_FIELDS = ['barcode','major_class','total_retained_gene_UMI','n_genes_T_positive','n_genes_with_at_least_two_positive_PAS']
FLOATS = {'median_T_all_cells','median_T_positive_cells','fraction_cells_T_positive','fraction_cells_with_at_least_two_positive_PAS'}
ARRAYS = {'pooled_PAS_counts_in_frozen_order','PAS_identities_in_frozen_order'}
TEXT = {'terminal_gene_unit_id','group','barcode','major_class'}
PAYLOADS = {'result.json','gene-group.tsv','per-cell.tsv','input-receipt.json'}
FINAL_MEMBERS = PAYLOADS | {'attempt-start.json','plan.json','producer.py','method.json','worker-claim.json','worker.stdout.log','worker.stderr.log','result-manifest.json'}
HANDOFF_ENV = ('SCPOLYA_COVERAGE_HANDOFF_FD','SCPOLYA_COVERAGE_HANDOFF_NONCE')
HANDOFF_PROTOCOL = 'one_use_anonymous_pipe_nonce_v1'
HANDOFF_BYTES_MAX = 8192
HANDOFF_SECONDS_MAX = 5
PUBLICATION_GUARD = '.publication-guard.json'
HEX = re.compile(r'[0-9a-f]{64}\Z')

class Refusal(ValueError):
    def __init__(self, code, detail): self.code,self.detail=code,str(detail)[:240];super().__init__(self.detail)
def fail(code, detail): raise Refusal(code,detail)
def need(ok,detail,code='invalid_input'):
    if not ok: fail(code,detail)
def sha(raw): return hashlib.sha256(raw).hexdigest()
def encode(value): return json.dumps(value,**ENC).encode('ascii')
def wire(value): return encode(value)+b'\n'
def keys(obj,expected,where): need(type(obj) is dict and set(obj)==set(expected),where+' fields')
def valid_sha(x): return type(x) is str and HEX.fullmatch(x) is not None
def text(x):
    need(type(x) is str and bool(x.strip()) and 0<len(x.encode('utf-8'))<=256 and not any(c in x for c in '\t\r\n\x00'),'bounded nonblank identity')
    return x
def whole(x, maximum=2**53-1):
    need(type(x) is int and 0<=x<=maximum,'bounded integer');return x
def ordinal(x):
    need(type(x) is str and x.isascii() and x.isdigit() and str(int(x))==x,'canonical index');return int(x)
def parse(raw):
    def pairs(items):
        result={}
        for k,v in items:
            need(k not in result,'duplicate JSON key');result[k]=v
        return result
    def constant(_): fail('invalid_input','nonfinite JSON')
    try: value=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    except (ValueError,UnicodeError,RecursionError) as exc:
        if isinstance(exc,Refusal):raise
        fail('invalid_input','invalid JSON')
    pending=[(value,0)];nodes=0
    while pending:
        item,depth=pending.pop();nodes+=1
        need(depth<=24 and nodes<=2000000,'JSON tree bound')
        if type(item) is dict:pending.extend((v,depth+1) for v in item.values())
        elif type(item) is list:pending.extend((v,depth+1) for v in item)
        elif type(item) is float:need(math.isfinite(item),'nonfinite JSON number')
    need(type(value) is dict,'JSON object');return value

def path(value, exists=True, directory=False):
    need(type(value) is str and len(value)<=4096 and '\x00' not in value,'absolute bounded path','path_refused')
    p=Path(value);need(p.is_absolute() and '..' not in p.parts,'explicit absolute path','path_refused')
    for item in reversed([p,*p.parents]):
        try: info=item.lstat()
        except FileNotFoundError:
            need(item==p and not exists,'missing path','missing_input');continue
        need(not stat.S_ISLNK(info.st_mode),'symlink component','path_refused')
    if exists:
        st=p.stat();need(stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode),'wrong file kind','path_refused')
    return p

def fingerprint(st): return [st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
def read_file(p,cap,expected_size=None,expected_hash=None):
    p=path(str(p));st=p.stat();need(st.st_size<=cap,'input bytes exceeded','limit_exceeded')
    if expected_size is not None:need(type(expected_size) is int and st.st_size==expected_size,'declared size mismatch','size_mismatch')
    if expected_hash is not None:need(valid_sha(expected_hash),'explicit SHA256 required')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        initial=os.fstat(fd);need(stat.S_ISREG(initial.st_mode) and fingerprint(initial)==fingerprint(st),'source changed before read','changed_input')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(cap+1)
        need(len(raw)<=cap,'input byte cap','limit_exceeded')
        need(fingerprint(st)==fingerprint(os.fstat(fd))==fingerprint(p.stat()),'source changed during read','changed_input')
    finally:os.close(fd)
    if expected_hash is not None:need(sha(raw)==expected_hash,'source SHA mismatch','hash_mismatch')
    return raw,dict(path=str(p),bytes=len(raw),sha256=sha(raw),fingerprint=fingerprint(st))
def read_ref(desc,cap):
    keys(desc,{'path','sha256'},'file reference');need(valid_sha(desc['sha256']),'explicit reference SHA');return read_file(desc['path'],cap,expected_hash=desc['sha256'])
def environment():
    return dict(python_executable=str(Path(sys.executable).absolute()),python_version=list(sys.version_info[:3]),numpy_version=importlib.metadata.version('numpy'))
def producer():
    code,_=read_file(MODULE,262144);method_raw,_=read_file(METHOD,65536);method=parse(method_raw)
    need(sha(method_raw)==EXPECTED_METHOD_SHA256,'shipped method SHA pin','stale_method')
    need(method['schema']=='scpolya.fixed_coverage.method.v2' and method['caps']==CAPS,'shipped method contract')
    return dict(module_path=str(MODULE),module_sha256=sha(code),method_path=str(METHOD),method_sha256=sha(method_raw)),method

def tsv_rows(raw,header):
    need(raw.endswith(b'\n') and b'\r' not in raw and not raw.startswith(b'\xef\xbb\xbf'),'canonical TSV LF')
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8')),delimiter='\t');need(reader.fieldnames==header,'supported exact TSV header')
    result=list(reader);need(all(set(r)==set(header) and all(v is not None for v in r.values()) for r in result),'TSV width')
    return result

def npy_header(raw):
    need(raw[:6]==b'\x93NUMPY' and len(raw)>=10,'NPY magic')
    version=tuple(raw[6:8]);need(version in ((1,0),(2,0)),'supported NPY format')
    prefix=10 if version==(1,0) else 12
    need(len(raw)>=prefix,'complete NPY version-specific prefix')
    try:size=struct.unpack('<H' if prefix==10 else '<I',raw[8:prefix])[0]
    except struct.error:fail('invalid_input','invalid NPY length prefix')
    need(size<=16384 and prefix+size<=len(raw),'bounded NPY header')
    try:meta=ast.literal_eval(raw[prefix:prefix+size].decode('latin1').strip())
    except (ValueError,SyntaxError,RecursionError):fail('invalid_input','invalid NPY header')
    keys(meta,{'descr','fortran_order','shape'},'NPY')
    need(meta['descr']=='<i8' and meta['fortran_order'] is False,'C-order little-endian int64 only')
    shape=meta['shape'];need(type(shape) is tuple and len(shape)==2 and all(type(x) is int and x>0 for x in shape),'NPY2D shape')
    need(len(raw)-prefix-size==math.prod(shape)*8,'NPY exact payload length');return list(shape)

def validate_request(request):
    keys(request,{'schema','sample_id','groups','sources','attempt_dir','limits'},'request')
    need(request['schema']=='scpolya.fixed_coverage.request.v2','request schema');text(request['sample_id'])
    groups=request['groups'];need(type(groups) is list and len(groups)==2 and len(set(map(text,groups)))==2,'exactly two distinct groups')
    keys(request['sources'],ROLES,'five source roles')
    limits=dict(CAPS);need(type(request['limits']) is dict and set(request['limits'])<=set(CAPS),'known limits')
    for k,v in request['limits'].items():need(type(v) is int and 0<v<=CAPS[k],'limits only reduce');limits[k]=v
    attempt=path(request['attempt_dir'],exists=False);path(str(attempt.parent),directory=True)
    for desc in request['sources'].values():
        keys(desc,{'path','bytes','sha256'},'source descriptor');whole(desc['bytes'],limits['input_bytes_total']);need(valid_sha(desc['sha256']),'source SHA')
        p=path(desc['path']);need(not attempt.is_relative_to(p.parent) and not p.is_relative_to(attempt),'attempt outside source input trees','path_refused')
    need(sum(d['bytes'] for d in request['sources'].values())<=limits['input_bytes_total'],'total input bytes','limit_exceeded')
    return limits

def inputs(request):
    limits=validate_request(request);raw={};evidence={};identities=set()
    for role in ROLES:
        d=request['sources'][role];data,ev=read_file(d['path'],limits['input_bytes_total'],d['bytes'],d['sha256'])
        ident=tuple(ev['fingerprint'][:2]);need(ident not in identities,'aliased sources');identities.add(ident);raw[role]=data;evidence[role]=ev
    cells=tsv_rows(raw['cell-index.tsv'],HEADERS['cell-index.tsv']);features=tsv_rows(raw['feature-index.tsv'],HEADERS['feature-index.tsv']);genes=tsv_rows(raw['gene-index.tsv'],HEADERS['gene-index.tsv'])
    need(1<=len(cells)<=limits['cells'] and 1<=len(genes)<=limits['genes'] and 2<=len(features)<=limits['PAS'],'bounded matrix/index dimensions','limit_exceeded')
    for rows,col in [(cells,'cell_index'),(features,'feature_index'),(genes,'gene_index')]:need([ordinal(r[col]) for r in rows]==list(range(len(rows))),'contiguous fixed index')
    ids=[text(c['barcode']) for c in cells];need(len(set(ids))==len(ids) and ids==sorted(ids),'canonical cell identity/order')
    counts=Counter(text(c['major_class']) for c in cells);need(set(counts)==set(request['groups']) and all(counts[g]>0 for g in request['groups']),'declared nonempty group set')
    for c in cells:need(len(c['cell_type'].encode())<=256,'cell metadata field bound')
    peaks=[text(f['peak_name']) for f in features];need(len(set(peaks))==len(peaks),'unique PAS strings')
    fkeys=[(text(f['terminal_gene_unit_id']).encode('utf-8'),ordinal(f['peak_order_in_gene']),f['peak_name'].encode('utf-8')) for f in features]
    need(fkeys==sorted(fkeys) and len(set(fkeys))==len(fkeys) and all(k[1]>=1 for k in fkeys),'canonical feature order/identity')
    cursor=0;units=[]
    for gene in genes:
        unit=text(gene['terminal_gene_unit_id']);units.append(unit);a,b,k=[ordinal(gene[x]) for x in ('start','stop','K')]
        need(a==cursor and 2<=k==b-a and b<=len(features),'fixed gene block partition')
        need(all(f['terminal_gene_unit_id']==unit for f in features[a:b]),'gene/feature identity');cursor=b
    need(cursor==len(features) and len(set(units))==len(units) and units==sorted(units,key=lambda x:x.encode('utf-8')),'gene identities/order cover all PAS')
    need(npy_header(raw['N.npy'])==[len(cells),len(features)] and npy_header(raw['T.npy'])==[len(cells),len(genes)],'matrix/index shape mismatch')
    meta=dict(cells=cells,features=features,genes=genes,shape=[len(cells),len(features)],groups=request['groups'],group_counts=dict(counts))
    return raw,meta,evidence,limits

def make_request(paths,sample_id,groups,attempt_dir,limits=None):
    """Generate descriptors; paths are an explicit mapping, never a glob/discovery."""
    keys(paths,ROLES,'explicit source paths');sources={}
    for role in ROLES:
        raw,ev=read_file(paths[role],CAPS['input_bytes_total']);sources[role]={k:ev[k] for k in ('path','bytes','sha256')}
    return dict(schema='scpolya.fixed_coverage.request.v2',sample_id=sample_id,groups=groups,sources=sources,attempt_dir=attempt_dir,limits={} if limits is None else limits)

def plan_coverage(request):
    _,meta,_,limits=inputs(request);prod,_=producer()
    result=dict(schema='scpolya.fixed_coverage.plan.v2',request=request,limits=limits,shape=meta['shape'],n_genes=len(meta['genes']),group_counts=meta['group_counts'],producer=prod,environment=environment(),scope='all supplied fixed rows/features/genes; sample identity declared only')
    result['plan_digest']=sha(encode(result));return result

def checked_plan(ref):
    raw,_=read_ref(ref,131072);plan=parse(raw);keys(plan,{'schema','request','limits','shape','n_genes','group_counts','producer','environment','scope','plan_digest'},'plan')
    need(plan['schema']=='scpolya.fixed_coverage.plan.v2','plan schema')
    need(plan==plan_coverage(plan['request']),'plan/input/code/method/environment stale','stale_plan')
    return plan,raw

def runtime_envelope():
    need(sys.dont_write_bytecode,'Python-B required','runtime_unavailable')
    need(all(os.environ.get(k)=='1' for k in THREADS),'six thread variables must equal1','runtime_unavailable')
    line=Path('/proc/self/cgroup').read_text().strip().splitlines();need(len(line)==1 and line[0].startswith('0::/'),'cgroup v2 required','runtime_unavailable')
    relative=line[0].split(':',2)[2].lstrip('/');need('..' not in Path(relative).parts,'cgroup path','runtime_unavailable')
    root=Path('/sys/fs/cgroup')/relative
    try:values={k:(root/k).read_text().strip() for k in ('cpu.max','memory.max','memory.swap.max')}
    except OSError:fail('runtime_unavailable','current capped cgroup controls unavailable')
    parts=values['cpu.max'].split();need(len(parts)==2 and parts[0]!='max','finite CPU quota required','runtime_unavailable')
    try:q,p=int(parts[0]),int(parts[1]);mem=int(values['memory.max']);swap=int(values['memory.swap.max'])
    except ValueError:fail('runtime_unavailable','finite memory/CPU/swap controls required')
    need(0<q<=2*p and p>0 and 0<mem<=17179869184 and swap==0,'CPU<=2,memory<=16GiB,swap0 required','runtime_unavailable')
    record=dict(cgroup=str(root),controls=values,threads={k:os.environ[k] for k in THREADS},python_B=True,external_supervisor='not independently observed; use documented outer guard')
    verify_runtime_record(record)
    return record

def verify_runtime_record(record):
    keys(record,{'cgroup','controls','threads','python_B','external_supervisor'},'runtime record')
    need(type(record['cgroup']) is str and Path(record['cgroup']).is_relative_to('/sys/fs/cgroup') and '..' not in Path(record['cgroup']).parts,'recorded leaf cgroup path')
    keys(record['controls'],{'cpu.max','memory.max','memory.swap.max'},'recorded controls')
    need(all(type(v) is str for v in record['controls'].values()),'recorded control text')
    control=record['controls'];cpu=control['cpu.max'].split()
    need(len(cpu)==2 and all(v.isascii() and v.isdigit() for v in cpu),'recorded finite CPU quota')
    q,p=map(int,cpu)
    need(0<q<=2*p and p>0 and control['memory.max'].isascii() and control['memory.max'].isdigit() and 0<int(control['memory.max'])<=17179869184 and control['memory.swap.max']=='0','recorded resource limits')
    need(record['threads']==dict.fromkeys(THREADS,'1') and record['python_B'] is True and record['external_supervisor']=='not independently observed; use documented outer guard','recorded thread/Python/supervisor claims')


def _median(values):
    if not values:return None
    s=sorted(values);n=len(s)
    return float(s[n//2]) if n%2 else (s[n//2-1]+s[n//2])/2

def calculate_coverage(N,T,meta,limits):
    """Pure accepted arithmetic on bounded parsed integer lists; no source IO."""
    cells,features,genes=meta['cells'],meta['features'],meta['genes'];groups=meta['groups']
    need(len(N)==len(T)==len(cells) and all(len(r)==len(features) for r in N) and all(len(r)==len(genes) for r in T),'numeric shape')
    need(CAPS['cells']*CAPS['PAS']*CAPS['maximum_count']<2**53-1<2**63-1,'safe integer accumulation proof')
    for rows in (N,T):
        for r in rows:
            for x in r:whole(x,limits['maximum_count'])
    for j,g in enumerate(genes):
        a,b=ordinal(g['start']),ordinal(g['stop'])
        need(all(sum(N[i][a:b])==T[i][j] for i in range(len(cells))),'T differs from N gene-block sums')
    multi=[[0]*len(genes) for _ in cells];gg=[]
    for j,g in enumerate(genes):
        a,b=ordinal(g['start']),ordinal(g['stop'])
        for i in range(len(cells)):multi[i][j]=int(sum(x>0 for x in N[i][a:b])>=2)
        for label in groups:
            selected=[i for i,c in enumerate(cells) if c['major_class']==label]
            totals=[T[i][j] for i in selected];pooled=[sum(N[i][k] for i in selected) for k in range(a,b)]
            n=len(selected);positive=sum(t>0 for t in totals);m=sum(multi[i][j] for i in selected)
            gg.append(dict(terminal_gene_unit_id=g['terminal_gene_unit_id'],group=label,n_cells_group=n,
                total_terminal_UMI=sum(totals),n_cells_T_positive=positive,n_cells_T_zero=n-positive,
                median_T_all_cells=_median(totals),median_T_positive_cells=_median([t for t in totals if t>0]),
                n_fixed_eligible_PAS=b-a,n_PAS_positive_pooled_within_group=sum(x>0 for x in pooled),
                pooled_PAS_counts_in_frozen_order=pooled,
                PAS_identities_in_frozen_order=[dict(feature_index=ordinal(f['feature_index']),peak_order_in_gene=ordinal(f['peak_order_in_gene']),peak_name=f['peak_name']) for f in features[a:b]],
                second_largest_pooled_PAS_count_in_group=sorted(pooled,reverse=True)[1],
                n_cells_with_at_least_two_positive_PAS=m,fraction_cells_T_positive=positive/n,
                fraction_cells_with_at_least_two_positive_PAS=m/n))
    per_cell=[dict(barcode=c['barcode'],major_class=c['major_class'],total_retained_gene_UMI=sum(T[i]),n_genes_T_positive=sum(t>0 for t in T[i]),n_genes_with_at_least_two_positive_PAS=sum(multi[i])) for i,c in enumerate(cells)]
    by_group={}
    for label in groups:
        rows=[r for r in per_cell if r['major_class']==label]
        by_group[label]=dict(n_cells=len(rows),total_terminal_UMI=sum(r['total_retained_gene_UMI'] for r in rows),
            per_cell={k:dict(min=min(r[k] for r in rows),median=_median([r[k] for r in rows]),max=max(r[k] for r in rows)) for k in CELL_FIELDS[2:]})
    by_gene=[gg[j*2:j*2+2] for j in range(len(genes))]
    summary=dict(n_cells=len(cells),n_genes=len(genes),n_PAS=len(features),global_terminal_UMI=sum(r['total_retained_gene_UMI'] for r in per_cell),groups=by_group,
        n_genes_positive_T_both_groups=sum(all(r['total_terminal_UMI']>0 for r in rs) for rs in by_gene),
        n_genes_at_least_two_positive_pooled_PAS_both_groups=sum(all(r['n_PAS_positive_pooled_within_group']>=2 for r in rs) for rs in by_gene))
    result=dict(schema='scpolya.fixed_coverage.result.v2',summary=summary,gene_group=gg,per_cell=per_cell)
    verify_result(result,meta)
    return result

def verify_result(result,meta):
    """Validate saved-output structure and ledger consistency, never load/recompute N/T."""
    keys(result,{'schema','summary','gene_group','per_cell'},'result');need(result['schema']=='scpolya.fixed_coverage.result.v2','result schema')
    cells,features,genes=meta['cells'],meta['features'],meta['genes'];groups=meta['groups']
    gg,pc,s=result['gene_group'],result['per_cell'],result['summary']
    need(type(gg) is list and len(gg)==len(genes)*2 and type(pc) is list and len(pc)==len(cells),'output row counts')
    keys(s,{'n_cells','n_genes','n_PAS','global_terminal_UMI','groups','n_genes_positive_T_both_groups','n_genes_at_least_two_positive_pooled_PAS_both_groups'},'summary')
    for k in set(s)-{'groups'}:whole(s[k],CAPS['cells']*CAPS['PAS']*CAPS['maximum_count'])
    need([s['n_cells'],s['n_genes'],s['n_PAS']]==[len(cells),len(genes),len(features)],'summary dimensions')
    safe=CAPS['cells']*CAPS['PAS']*CAPS['maximum_count']
    for r,c in zip(pc,cells):
        keys(r,CELL_FIELDS,'per-cell');need(r['barcode']==c['barcode'] and r['major_class']==c['major_class'],'per-cell identity/order')
        for k in CELL_FIELDS[2:]:whole(r[k],safe)
        need(0<=r['n_genes_with_at_least_two_positive_PAS']<=r['n_genes_T_positive']<=len(genes),'per-cell count nesting')
    for j,g in enumerate(genes):
        a,b=ordinal(g['start']),ordinal(g['stop'])
        expected_sites=[dict(feature_index=ordinal(f['feature_index']),peak_order_in_gene=ordinal(f['peak_order_in_gene']),peak_name=f['peak_name']) for f in features[a:b]]
        for offset,label in enumerate(groups):
            r=gg[j*2+offset];keys(r,GG_FIELDS,'gene-group')
            need(r['terminal_gene_unit_id']==g['terminal_gene_unit_id'] and r['group']==label,'gene-group identity/order')
            need(r['PAS_identities_in_frozen_order']==expected_sites and r['n_fixed_eligible_PAS']==b-a,'frozen PAS identity/block')
            for k in set(GG_FIELDS)-FLOATS-ARRAYS-TEXT:whole(r[k],safe)
            counts=r['pooled_PAS_counts_in_frozen_order'];need(type(counts) is list and len(counts)==b-a,'all pooled PAS retained')
            for n in counts:whole(n,safe)
            need(sum(counts)==r['total_terminal_UMI'] and sum(n>0 for n in counts)==r['n_PAS_positive_pooled_within_group'],'pooled PAS ledger')
            need(sorted(counts,reverse=True)[1]==r['second_largest_pooled_PAS_count_in_group'],'second-largest support semantics')
            n=meta['group_counts'][label];need(r['n_cells_group']==n and r['n_cells_T_positive']+r['n_cells_T_zero']==n,'group cell partition')
            need(0<=r['n_cells_with_at_least_two_positive_PAS']<=r['n_cells_T_positive']<=n,'multi-cell support nesting')
            for k in FLOATS:
                v=r[k];need((k=='median_T_positive_cells' and v is None) or (type(v) is float and math.isfinite(v) and v>=0),'typed finite float64/null')
            need((r['median_T_positive_cells'] is None)==(r['n_cells_T_positive']==0),'positive-only null semantics')
            need(r['fraction_cells_T_positive']==r['n_cells_T_positive']/n and r['fraction_cells_with_at_least_two_positive_PAS']==r['n_cells_with_at_least_two_positive_PAS']/n,'fraction denominator/unit')
    keys(s['groups'],groups,'summary groups')
    for label in groups:
        x=s['groups'][label];keys(x,{'n_cells','total_terminal_UMI','per_cell'},'group marginal')
        whole(x['n_cells'],len(cells));need(x['n_cells']==meta['group_counts'][label],'group size');whole(x['total_terminal_UMI'],safe)
        chosen=[r for r in pc if r['major_class']==label];gr=[r for r in gg if r['group']==label]
        need(x['total_terminal_UMI']==sum(r['total_retained_gene_UMI'] for r in chosen)==sum(r['total_terminal_UMI'] for r in gr),'group UMI ledger')
        need(sum(r['n_cells_T_positive'] for r in gr)==sum(r['n_genes_T_positive'] for r in chosen),'positive gene-cell ledger')
        need(sum(r['n_cells_with_at_least_two_positive_PAS'] for r in gr)==sum(r['n_genes_with_at_least_two_positive_PAS'] for r in chosen),'multi-PAS gene-cell ledger')
        keys(x['per_cell'],CELL_FIELDS[2:],'per-cell marginal stats')
        for field,stats in x['per_cell'].items():
            keys(stats,{'min','median','max'},'summary stats');values=[r[field] for r in chosen]
            whole(stats['min'],safe);whole(stats['max'],safe);need(type(stats['median']) is float and math.isfinite(stats['median']),'summary median float64')
            need(stats==dict(min=min(values),median=_median(values),max=max(values)),'saved per-cell summary consistency')
    need(s['global_terminal_UMI']==sum(r['total_retained_gene_UMI'] for r in pc),'global UMI ledger')
    by_gene=[gg[j*2:j*2+2] for j in range(len(genes))]
    need(s['n_genes_positive_T_both_groups']==sum(all(r['total_terminal_UMI']>0 for r in rs) for rs in by_gene),'joint positive intersection')
    need(s['n_genes_at_least_two_positive_pooled_PAS_both_groups']==sum(all(r['n_PAS_positive_pooled_within_group']>=2 for r in rs) for rs in by_gene),'joint multi-site intersection')

def tsv_bytes(records,fields):
    stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=fields,delimiter='\t',lineterminator='\n');w.writeheader()
    for r in records:
        w.writerow({k:('null' if r[k] is None else json.dumps(r[k],**ENC) if k in ARRAYS else format(r[k],'.17g') if k in FLOATS else str(r[k])) for k in fields})
    return stream.getvalue().encode('utf-8')
def verify_tsv(raw,expected,fields):
    actual=tsv_rows(raw,fields);need(len(actual)==len(expected),'TSV row count')
    for parsed,original in zip(actual,expected):
        for k in fields:
            v=parsed[k]
            if k in FLOATS:
                value=None if v=='null' else float(v)
                need(value==original[k],'TSV float/null roundtrip')
                if value is not None:need(v==format(original[k],'.17g') and struct.pack('<d',value)==struct.pack('<d',original[k]),'TSV17g float64 bits')
            elif k in ARRAYS:need(json.loads(v)==original[k],'TSV array identity/counts')
            elif k in TEXT:need(v==original[k],'TSV literal identity')
            else:need(ordinal(v)==original[k],'TSV raw integer roundtrip')

def total_attempt_bytes(root):
    return sum(p.stat().st_size for p in root.iterdir() if p.is_file())
def write_new(root,name,data,limit):
    need(Path(name).name==name,'flat output member')
    need(total_attempt_bytes(root)+len(data)<=limit,'total attempt output bytes','limit_exceeded')
    with (root/name).open('xb') as h:h.write(data);h.flush();os.fsync(h.fileno())
def fsync_directory(root):
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
def descriptor(root,name):
    raw,ev=read_file(root/name,CAPS['output_bytes_total']);return dict(path=name,bytes=len(raw),sha256=sha(raw))

def worker_fuse_contract(budget):
    return dict(mechanism='Linux SIGALRM default action via ITIMER_REAL',origin='worker_entry_monotonic',initial_ceiling_seconds=CAPS['wall_seconds_max'],budget_seconds=budget,handoff_max_seconds=HANDOFF_SECONDS_MAX)

def arm_worker_fuse(started,budget):
    """Private child only: OS default action, independent of Python/parent loops."""
    need(type(budget) in (int,float) and 0<budget<=CAPS['wall_seconds_max'],'bounded child fuse budget')
    signal.signal(signal.SIGALRM,signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK,{signal.SIGALRM})
    remaining=started+budget-time.monotonic()
    need(remaining>0,'worker entry deadline expired','worker_timeout')
    signal.setitimer(signal.ITIMER_REAL,remaining)
    actual,_=signal.getitimer(signal.ITIMER_REAL)
    need(0<actual<=budget,'child OS fuse armed','runtime_unavailable')
    return worker_fuse_contract(budget)

def take_worker_channel():
    """Internal pipe handoff, not same-user/host authentication."""
    number=os.environ.pop(HANDOFF_ENV[0],'');nonce=os.environ.pop(HANDOFF_ENV[1],'')
    need(number.isascii() and number.isdigit() and len(number)<=8 and valid_sha(nonce),'private inherited pipe and nonce required','invalid_worker_handoff')
    fd=int(number);need(3<=fd<1048576,'inherited FD range','invalid_worker_handoff')
    try:
        need(stat.S_ISFIFO(os.fstat(fd).st_mode) and re.fullmatch(r'pipe:\[[0-9]+\]',os.readlink('/proc/self/fd/'+str(fd))) is not None,'anonymous pipe required','invalid_worker_handoff')
        need(fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE==os.O_RDONLY,'read-only handoff endpoint','invalid_worker_handoff')
        os.set_blocking(fd,False);os.set_inheritable(fd,False)
    except OSError:fail('invalid_worker_handoff','inherited anonymous pipe unavailable')
    return fd,nonce

def receive_worker_handoff(fd,nonce,plan_ref,plan,start_raw,started):
    deadline=min(started+plan['limits']['wall_seconds_max'],time.monotonic()+HANDOFF_SECONDS_MAX)
    chunks=[];size=0
    while True:
        remaining=deadline-time.monotonic()
        need(remaining>0,'handoff receive deadline','invalid_worker_handoff')
        ready,_,_=select.select([fd],[],[],remaining)
        need(bool(ready),'handoff receive timeout','invalid_worker_handoff')
        try:chunk=os.read(fd,min(4096,HANDOFF_BYTES_MAX+1-size))
        except BlockingIOError:continue
        if not chunk:break
        chunks.append(chunk);size+=len(chunk)
        need(size<=HANDOFF_BYTES_MAX,'bounded handoff message','invalid_worker_handoff')
    need(size>0,'handoff pipe EOF without message','invalid_worker_handoff')
    message=parse(b''.join(chunks))
    keys(message,{'schema','parent_pid','plan_ref','plan_digest','attempt_start_sha256','nonce'},'private handoff')
    need(message==dict(schema='scpolya.fixed_coverage.handoff.v2',parent_pid=os.getppid(),plan_ref=plan_ref,plan_digest=plan['plan_digest'],attempt_start_sha256=sha(start_raw),nonce=nonce),'handoff parent/nonce/plan/start binding','invalid_worker_handoff')
    return dict(protocol=HANDOFF_PROTOCOL,nonce_sha256=sha(bytes.fromhex(nonce)))

def _worker(plan_ref):
    started=time.monotonic();arm_worker_fuse(started,CAPS['wall_seconds_max'])
    runtime=runtime_envelope()   # BEFORE NumPy import and blocking handoff.
    fd,nonce=take_worker_channel()
    try:
        plan,_=checked_plan(plan_ref);fuse=arm_worker_fuse(started,plan['limits']['wall_seconds_max'])
        raw,meta,before,limits=inputs(plan['request']);root=path(plan['request']['attempt_dir'],directory=True)
        start_raw=read_file(root/'attempt-start.json',65536)[0];start=parse(start_raw)
        need(start['parent_pid']==os.getppid() and start['plan_ref']==plan_ref and start['plan_digest']==plan['plan_digest'],'worker requires active fixed-parent handoff','invalid_worker_handoff')
        need({p.name for p in root.iterdir()}=={'attempt-start.json','plan.json','producer.py','method.json','worker.stdout.log','worker.stderr.log'},'worker cannot resume an existing/started attempt','invalid_worker_handoff')
        handoff=receive_worker_handoff(fd,nonce,plan_ref,plan,start_raw,started)
    finally:os.close(fd)
    write_new(root,'worker-claim.json',wire(dict(schema='scpolya.fixed_coverage.worker_claim.v2',plan_ref=plan_ref,parent_pid=os.getppid(),worker_pid=os.getpid(),attempt_start_sha256=sha(start_raw),handoff=handoff,child_fuse=fuse)),limits['output_bytes_total'])
    import numpy as np
    need(np.__version__==plan['environment']['numpy_version'],'NumPy version changed','stale_plan')
    N=np.load(io.BytesIO(raw['N.npy']),allow_pickle=False);T=np.load(io.BytesIO(raw['T.npy']),allow_pickle=False)
    need(N.dtype.str==T.dtype.str=='<i8' and N.flags.c_contiguous and T.flags.c_contiguous,'numeric dtype/order')
    need(bool(np.all(N>=0)) and bool(np.all(T>=0)) and bool(np.all(N<=limits['maximum_count'])) and bool(np.all(T<=limits['maximum_count'])),'per-element integer bound before sum')
    result=calculate_coverage(N.tolist(),T.tolist(),meta,limits)
    payloads={'result.json':wire(result),'gene-group.tsv':tsv_bytes(result['gene_group'],GG_FIELDS),'per-cell.tsv':tsv_bytes(result['per_cell'],CELL_FIELDS)}
    _,_,after,_=inputs(plan['request']);need(before==after,'inputs changed during worker','changed_input')
    need(producer()[0]==plan['producer'],'producer/method changed','stale_plan')
    receipt=dict(schema='scpolya.fixed_coverage.input_receipt.v2',before=before,after=after,runtime=runtime,shape=meta['shape'],gene_count=len(meta['genes']),groups=meta['groups'])
    payloads['input-receipt.json']=wire(receipt)
    for name,data in payloads.items():write_new(root,'.'+name+'.pending',data,limits['output_bytes_total'])
    return dict(status='worker_complete')

def stop_child(proc,grace):
    if proc.poll() is not None:return
    try:os.killpg(proc.pid,signal.SIGTERM)
    except ProcessLookupError:return
    try:proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        proc.wait()

def send_worker_handoff(fd,message,deadline):
    data=wire(message);need(len(data)<=HANDOFF_BYTES_MAX,'bounded parent handoff')
    while data:
        remaining=deadline-time.monotonic()
        need(remaining>0,'parent handoff deadline expired','worker_timeout')
        _,ready,_=select.select([],[fd],[],remaining)
        need(bool(ready),'parent handoff write timeout','worker_timeout')
        try:count=os.write(fd,data)
        except BlockingIOError:continue
        need(count>0,'parent handoff pipe closed','invalid_worker_handoff');data=data[count:]

def run_trusted_child(plan_ref,root,budget):
    """Closed argv only; one inherited anonymous pipe, fresh nonce, fixed child."""
    start=time.monotonic();grace=min(5.0,budget/4);deadline=start+budget
    nonce=os.urandom(32).hex();read_fd,write_fd=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK)
    proc=None
    try:
        start_raw=read_file(root/'attempt-start.json',65536)[0];receipt=parse(start_raw)
        message=dict(schema='scpolya.fixed_coverage.handoff.v2',parent_pid=os.getpid(),plan_ref=plan_ref,plan_digest=receipt['plan_digest'],attempt_start_sha256=sha(start_raw),nonce=nonce)
        env=dict(os.environ);env[HANDOFF_ENV[0]]=str(read_fd);env[HANDOFF_ENV[1]]=nonce
        with (root/'worker.stdout.log').open('xb') as out,(root/'worker.stderr.log').open('xb') as err:
            proc=subprocess.Popen([sys.executable,'-B',str(MODULE),'--worker',plan_ref['path'],plan_ref['sha256']],stdout=out,stderr=err,start_new_session=True,env=env,pass_fds=(read_fd,))
            os.close(read_fd);read_fd=-1
            try:
                try:send_worker_handoff(write_fd,message,deadline-grace)
                finally:os.close(write_fd);write_fd=-1
                while proc.poll() is None:
                    if (root/'worker.stdout.log').stat().st_size+(root/'worker.stderr.log').stat().st_size>262144:
                        stop_child(proc,min(grace,max(0,deadline-time.monotonic())));fail('limit_exceeded','worker log bytes')
                    left=deadline-time.monotonic()-grace
                    if left<=0:
                        stop_child(proc,grace);fail('worker_timeout','trusted child deadline expired')
                    try:proc.wait(timeout=min(.05,left))
                    except subprocess.TimeoutExpired:pass
                if proc.returncode!=0:
                    try:message=parse(read_file(root/'worker.stdout.log',262144)[0])
                    except (Refusal,OSError):message={}
                    if message.get('status')=='refused':fail(message.get('code','worker_failed'),message.get('detail','worker refused'))
                    fail('worker_failed','trusted worker failed; see retained logs')
            except BaseException:
                stop_child(proc,min(grace,max(0,deadline-time.monotonic())));raise
    finally:
        for fd in (read_fd,write_fd):
            if fd>=0:os.close(fd)
    need((root/'worker.stdout.log').stat().st_size+(root/'worker.stderr.log').stat().st_size<=262144,'worker log bytes','limit_exceeded')
    claim=parse(read_file(root/'worker-claim.json',65536)[0])
    handoff=dict(protocol=HANDOFF_PROTOCOL,nonce_sha256=sha(bytes.fromhex(nonce)))
    need(claim==dict(schema='scpolya.fixed_coverage.worker_claim.v2',plan_ref=plan_ref,parent_pid=os.getpid(),worker_pid=proc.pid,attempt_start_sha256=sha(start_raw),handoff=handoff,child_fuse=worker_fuse_contract(budget)),'fixed child claim matches active pipe/nonce/fuse','invalid_worker_handoff')
    return dict(returncode=proc.returncode,wall_seconds=time.monotonic()-start,supervision='parent monotonic deadline,TERM then process-group KILL',budget_seconds=budget,handoff=handoff,child_fuse=claim['child_fuse'])

def _publish_pending(root,name):
    os.link(root/('.'+name+'.pending'),root/name)  # no replacement
    (root/('.'+name+'.pending')).unlink()

def run_coverage(plan_ref):
    """Fresh only. Requires a pre-existing capped runtime; starts one fixed child."""
    runtime=runtime_envelope();plan,raw_plan=checked_plan(plan_ref)
    root=path(plan['request']['attempt_dir'],exists=False)
    need(not root.exists(),'run never overwrites/reuses any existing directory','refused_existing_output')
    _,meta,before,limits=inputs(plan['request']);code,_=read_file(MODULE,262144);method_raw,_=read_file(METHOD,65536)
    start=dict(schema='scpolya.fixed_coverage.attempt_start.v2',parent_pid=os.getpid(),plan_ref=plan_ref,plan_digest=plan['plan_digest'],producer=plan['producer'],environment=plan['environment'],runtime=runtime,input_before=before,attempt_dir=str(root))
    need(len(wire(start))+len(raw_plan)+len(code)+len(method_raw)+32768<=limits['output_bytes_total'],'output budget too small for bounded attempt metadata','limit_exceeded')
    try:root.mkdir()
    except FileExistsError:fail('refused_existing_output','exclusive attempt directory already exists')
    started=time.monotonic();publication_stage='not_started';manifest_durable=False
    try:
        write_new(root,'attempt-start.json',wire(start),limits['output_bytes_total'])
        write_new(root,'plan.json',raw_plan,limits['output_bytes_total'])
        write_new(root,'producer.py',code,limits['output_bytes_total']);write_new(root,'method.json',method_raw,limits['output_bytes_total'])
        child=run_trusted_child(plan_ref,root,limits['wall_seconds_max'])
        pending={name:read_file(root/('.'+name+'.pending'),limits['output_bytes_total'])[0] for name in PAYLOADS}
        result=parse(pending['result.json']);verify_result(result,meta)
        verify_tsv(pending['gene-group.tsv'],result['gene_group'],GG_FIELDS);verify_tsv(pending['per-cell.tsv'],result['per_cell'],CELL_FIELDS)
        receipt=parse(pending['input-receipt.json']);keys(receipt,{'schema','before','after','runtime','shape','gene_count','groups'},'input receipt')
        need(receipt['schema']=='scpolya.fixed_coverage.input_receipt.v2' and receipt['before']==receipt['after']==before,'worker input stability receipt','changed_input')
        need(receipt['runtime']==runtime,'worker runtime envelope differs','runtime_unavailable')
        _,_,after,_=inputs(plan['request']);need(before==after,'parent final input stability check','changed_input')
        need(producer()[0]==plan['producer'] and environment()==plan['environment'],'producer/environment changed','stale_plan')
        for name in sorted(PAYLOADS):_publish_pending(root,name)
        outputs={name:descriptor(root,name) for name in sorted(FINAL_MEMBERS-{'result-manifest.json'})}
        manifest=dict(schema='scpolya.fixed_coverage.result_manifest.v2',status='complete',execution_state='completed',review_state='not_reviewed',native_input_completion_state='not_assessed',
            attempt_dir=str(root),plan_ref=plan_ref,plan_digest=plan['plan_digest'],producer=plan['producer'],environment=plan['environment'],
            sources=plan['request']['sources'],sample_id=plan['request']['sample_id'],sample_identity='caller_declared_only',groups=meta['groups'],shape=meta['shape'],gene_count=len(meta['genes']),
            files=outputs,rows={'gene-group.tsv':len(result['gene_group']),'per-cell.tsv':len(result['per_cell'])},
            runtime=runtime,child_supervision=child,wall_seconds=time.monotonic()-started,checks={'T_equals_N_gene_block_sums':True,'source_before_after_unchanged':True,'integer_partitions':True,'joint_intersection':True,'delivered_roundtrip':True})
        write_new(root,PUBLICATION_GUARD,wire(dict(schema='scpolya.fixed_coverage.publication_guard.v2',plan_ref=plan_ref,plan_digest=plan['plan_digest'],purpose='Reject get until final-link directory sync succeeds.')),limits['output_bytes_total'])
        write_new(root,'.result-manifest.json.pending',wire(manifest),limits['output_bytes_total'])
        publication_stage='syncing_before_final_link';fsync_directory(root)
        publication_stage='linking_final_manifest';_publish_pending(root,'result-manifest.json')
        publication_stage='syncing_final_manifest_directory';fsync_directory(root);manifest_durable=True
        # Removal needs no completion claim: the manifest link was already synced.
        # A crash may restore this guard and conservatively refuse a durable result.
        publication_stage='removing_completion_guard';(root/PUBLICATION_GUARD).unlink()
        publication_stage='complete'

        return dict(schema='scpolya.fixed_coverage.run_response.v2',status='complete',fresh_analysis=True,execution_state='completed',review_state='not_reviewed',native_input_completion_state='not_assessed',analysis_ref={'path':str(root/'result-manifest.json'),'sha256':sha(wire(manifest))},plan_ref=plan_ref,summary=result['summary'])
    except BaseException as exc:
        try:
            (root/'result-manifest.json').lstat();visible=True
        except FileNotFoundError:visible=False
        except OSError:visible=None
        state=('visible_durable_but_operation_failed' if manifest_durable else 'visible_durability_uncertain') if visible is True else ('not_published' if visible is False else 'visibility_unknown')
        failure_code='publication_uncertain' if visible is not False and not manifest_durable else (exc.code if isinstance(exc,Refusal) else 'operation_failed')
        failure=dict(schema='scpolya.fixed_coverage.failure.v2',status='failed_or_incomplete',plan_ref=plan_ref,plan_digest=plan['plan_digest'],code=failure_code,detail=str(exc)[:240],complete_manifest_published=visible,publication_state=state,publication_stage=publication_stage,manifest_directory_durability='confirmed' if manifest_durable else 'uncertain' if visible is not False else 'not_applicable')
        try:write_new(root,'failure.json',wire(failure),limits['output_bytes_total']);fsync_directory(root)
        except (OSError,Refusal):pass
        if isinstance(exc,Refusal) and failure_code==exc.code:raise
        fail(failure_code,type(exc).__name__)

def _member_names(root):
    names=set()
    for p in root.iterdir():
        need(len(names)<len(FINAL_MEMBERS)+1,'unexpected result members','stale_or_invalid_result')
        need(p.is_file() and not p.is_symlink(),'result regular members only','stale_or_invalid_result');names.add(p.name)
    need(names==FINAL_MEMBERS,'exact complete result member set required','stale_or_invalid_result')

def escape_pointer(key):return str(key).replace('~','~0').replace('/','~1')
def resolve_pointer(value,pointer):
    need(pointer=='' or pointer.startswith('/'),'JSON pointer syntax','invalid_citation')
    for token in ([] if pointer=='' else pointer[1:].split('/')):
        need(re.search(r'~(?![01])',token) is None,'JSON pointer escape','invalid_citation')
        token=token.replace('~1','/').replace('~0','~')
        if type(value) is list:
            need(token.isascii() and token.isdigit() and str(int(token))==token and int(token)<len(value),'JSON pointer array index','invalid_citation');value=value[int(token)]
        else:need(type(value) is dict and token in value,'JSON pointer field absent','invalid_citation');value=value[token]
    return value

def _leaf_pointers(value,prefix):
    if type(value) is dict:
        for k,v in value.items():yield from _leaf_pointers(v,prefix+'/'+escape_pointer(k))
    elif type(value) is list:
        for i,v in enumerate(value):yield from _leaf_pointers(v,prefix+'/'+str(i))
    else:yield prefix,value

def get_coverage(plan_ref,manifest_ref,view='summary'):
    """Exact verified reuse of an explicit complete attempt. No worker, NumPy or writes."""
    try:return _get_coverage(plan_ref,manifest_ref,view)
    except Refusal as exc:
        if exc.code in ('invalid_citation','invalid_view'):raise
        fail('stale_or_invalid_result',exc.code+': '+exc.detail)

def _get_coverage(plan_ref,manifest_ref,view):
    need(view in ('summary','gene-group','per-cell','field-definitions'),'known get view','invalid_view')
    plan,_=checked_plan(plan_ref);_,meta,evidence,limits=inputs(plan['request'])
    raw,_=read_ref(manifest_ref,131072);m=parse(raw)
    keys(m,{'schema','status','execution_state','review_state','native_input_completion_state','attempt_dir','plan_ref','plan_digest','producer','environment','sources','sample_id','sample_identity','groups','shape','gene_count','files','rows','runtime','child_supervision','wall_seconds','checks'},'result manifest')
    need(m['schema']=='scpolya.fixed_coverage.result_manifest.v2' and m['status']=='complete' and m['execution_state']=='completed' and m['review_state']=='not_reviewed' and m['native_input_completion_state']=='not_assessed','complete separate state axes')
    root=path(plan['request']['attempt_dir'],directory=True)
    need(manifest_ref['path']==str(root/'result-manifest.json') and m['attempt_dir']==str(root),'explicit planned output identity')
    need(m['plan_ref']==plan_ref and m['plan_digest']==plan['plan_digest'] and m['producer']==plan['producer'] and m['environment']==plan['environment'],'same exact plan/producer/method/environment')
    need(m['sources']==plan['request']['sources'] and m['sample_id']==plan['request']['sample_id'] and m['sample_identity']=='caller_declared_only' and m['groups']==meta['groups'] and m['shape']==meta['shape'] and m['gene_count']==len(meta['genes']),'source/scope identity')
    _member_names(root);keys(m['files'],FINAL_MEMBERS-{'result-manifest.json'},'allowed output files')
    need(total_attempt_bytes(root)<=limits['output_bytes_total'],'complete output total cap')
    payload={}
    for name,d in m['files'].items():
        keys(d,{'path','bytes','sha256'},'output descriptor');need(d['path']==name and valid_sha(d['sha256']),'flat declared output identity')
        payload[name]=read_file(root/name,limits['output_bytes_total'],d['bytes'],d['sha256'])[0]
    need(sha(payload['plan.json'])==plan_ref['sha256'] and parse(payload['plan.json'])==plan,'saved plan identity')
    need(sha(payload['producer.py'])==plan['producer']['module_sha256'] and sha(payload['method.json'])==plan['producer']['method_sha256'],'producer/method snapshots')
    start=parse(payload['attempt-start.json']);keys(start,{'schema','parent_pid','plan_ref','plan_digest','producer','environment','runtime','input_before','attempt_dir'},'attempt start')
    need(start['schema']=='scpolya.fixed_coverage.attempt_start.v2' and start['plan_ref']==plan_ref and start['plan_digest']==plan['plan_digest'] and start['producer']==plan['producer'] and start['environment']==plan['environment'] and start['attempt_dir']==str(root),'attempt receipt identity')
    claim=parse(payload['worker-claim.json']);keys(claim,{'schema','plan_ref','parent_pid','worker_pid','attempt_start_sha256','handoff','child_fuse'},'worker claim')
    need(claim['schema']=='scpolya.fixed_coverage.worker_claim.v2' and claim['plan_ref']==plan_ref and claim['parent_pid']==start['parent_pid'] and type(claim['worker_pid']) is int and claim['worker_pid']>0 and claim['attempt_start_sha256']==sha(payload['attempt-start.json']),'single fixed-worker handoff identity')
    receipt=parse(payload['input-receipt.json']);keys(receipt,{'schema','before','after','runtime','shape','gene_count','groups'},'input receipt')
    need(receipt['schema']=='scpolya.fixed_coverage.input_receipt.v2' and receipt['before']==receipt['after']==start['input_before'],'recorded source stability')
    for role,desc in plan['request']['sources'].items():
        for k in ('path','bytes','sha256'):need(receipt['before'][role][k]==desc[k],'source receipt binding')
    verify_runtime_record(m['runtime'])
    whole(start['parent_pid']);need(start['parent_pid']>0,'positive parent process identity')
    child=m['child_supervision'];keys(child,{'returncode','wall_seconds','supervision','budget_seconds','handoff','child_fuse'},'fixed child supervision')
    keys(claim['handoff'],{'protocol','nonce_sha256'},'one-use handoff receipt')
    need(claim['handoff']['protocol']==HANDOFF_PROTOCOL and valid_sha(claim['handoff']['nonce_sha256']) and child['handoff']==claim['handoff'] and child['child_fuse']==claim['child_fuse']==worker_fuse_contract(limits['wall_seconds_max']),'recorded pipe/nonce/independent child fuse')
    need(type(child['returncode']) is int and child['returncode']==0 and type(child['budget_seconds']) is int and child['budget_seconds']==limits['wall_seconds_max'] and child['supervision']=='parent monotonic deadline,TERM then process-group KILL','fixed child completion/budget')
    need(type(child['wall_seconds']) is float and math.isfinite(child['wall_seconds']) and 0<=child['wall_seconds']<=120 and type(m['wall_seconds']) is float and math.isfinite(m['wall_seconds']) and child['wall_seconds']<=m['wall_seconds']<=120,'bounded recorded completion wall times')
    need(receipt['runtime']==start['runtime']==m['runtime'] and receipt['shape']==meta['shape'] and receipt['gene_count']==len(meta['genes']) and receipt['groups']==meta['groups'],'receipt scope/runtime binding')
    result=parse(payload['result.json']);verify_result(result,meta)
    verify_tsv(payload['gene-group.tsv'],result['gene_group'],GG_FIELDS);verify_tsv(payload['per-cell.tsv'],result['per_cell'],CELL_FIELDS)
    need(m['rows']=={'gene-group.tsv':len(meta['genes'])*2,'per-cell.tsv':len(meta['cells'])},'manifest row counts')
    need(m['checks']=={'T_equals_N_gene_block_sums':True,'source_before_after_unchanged':True,'integer_partitions':True,'joint_intersection':True,'delivered_roundtrip':True},'manifest checks')
    _,_,after,_=inputs(plan['request']);need(evidence==after,'inputs changed during get','changed_input')
    method=parse(payload['method.json']);need(method==producer()[1],'frozen method definitions')
    role='result.json';prefix={'summary':'/summary','gene-group':'/gene_group','per-cell':'/per_cell','field-definitions':'/fields'}[view]
    source_obj=method if view=='field-definitions' else result
    if view=='field-definitions':role='method.json'
    data=resolve_pointer(source_obj,prefix)
    def definition_ref(field):
        need(field in method['fields'],'known field definition','invalid_citation')
        pointer='/fields/'+escape_pointer(field)
        need(resolve_pointer(method,pointer)==method['fields'][field],'definition pointer resolves','invalid_citation')
        return dict(path=str(root/'method.json'),sha256=m['files']['method.json']['sha256'],schema=method['schema'],json_pointer=pointer,unit=method['fields'][field]['unit'])
    citations=[]
    if view=='summary':nodes=list(_leaf_pointers(data,prefix))
    elif view=='field-definitions':nodes=[(prefix+'/'+escape_pointer(k),v) for k,v in data.items()]
    else:nodes=[(prefix+'/'+str(i),row) for i,row in enumerate(data)]
    for pointer,value in nodes:
        need(resolve_pointer(source_obj,pointer)==value,'citation pointer resolves','invalid_citation')
        citation=dict(output_role=role,path=str(root/role),sha256=m['files'][role]['sha256'],schema=source_obj['schema'],json_pointer=pointer)
        if view=='summary':
            tokens=pointer.split('/')
            field=tokens[-2] if tokens[-1] in ('min','median','max') else tokens[-1]
            citation['definition_ref']=definition_ref(field)
            if tokens[-1] in ('min','median','max'):citation['statistic']=tokens[-1]
        elif view=='field-definitions':citation['definition_ref']=definition_ref(pointer.split('/')[-1].replace('~1','/').replace('~0','~'))
        if view in ('gene-group','per-cell'):
            fields=GG_FIELDS if view=='gene-group' else CELL_FIELDS;table='gene-group.tsv' if view=='gene-group' else 'per-cell.tsv'
            primary={k:value[k] for k in (('terminal_gene_unit_id','group') if view=='gene-group' else ('barcode',))}
            table_rows=tsv_rows(payload[table],fields);matches=[r for r in table_rows if all(r[k]==v for k,v in primary.items())]
            need(len(matches)==1,'citation primary key exists uniquely','invalid_citation')
            citation['field_contracts']={field:definition_ref(field if field in method['fields'] else 'identity') for field in fields}
            citation['table']=dict(path=str(root/table),sha256=m['files'][table]['sha256'],header=fields,primary_key=primary,columns=fields)
        citations.append(citation)
    response=dict(schema='scpolya.fixed_coverage.get_response.v2',status='verified_existing_result',fresh_analysis=False,
        execution_state='completed',review_state='not_reviewed',native_input_completion_state='not_assessed',
        analysis_ref=manifest_ref,plan_ref=plan_ref,sample_id=m['sample_id'],sample_identity='caller_declared_only',groups=meta['groups'],view=view,data=data,
        field_definitions=method['fields'],interpretation_limits=method['interpretation_limits'],source_references=plan['request']['sources'],citations=citations,
        detail_descriptors={k:dict(m['files'][k],schema='fixed TSV',header=GG_FIELDS if k=='gene-group.tsv' else CELL_FIELDS,rows=m['rows'][k]) for k in ('gene-group.tsv','per-cell.tsv')})
    need(len(wire(response))<=CAPS['output_bytes_total'],'get response byte cap','limit_exceeded');return response

def guarded(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except Refusal as exc:return dict(schema='scpolya.fixed_coverage.refusal.v2',status='refused',code=exc.code,detail=exc.detail,fresh_analysis=False)
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError,OverflowError,csv.Error,EOFError,struct.error) as exc:
        return dict(schema='scpolya.fixed_coverage.refusal.v2',status='refused',code='invalid_source_or_request',detail=type(exc).__name__,fresh_analysis=False)

class Parser(argparse.ArgumentParser):
    def error(self,message):fail('invalid_cli',message)
def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    def dispatch():
        if len(args)==3 and args[0]=='--worker':
            need(valid_sha(args[2]),'worker plan SHA');return _worker(dict(path=args[1],sha256=args[2]))
        parser=Parser(add_help=False,allow_abbrev=False);parser.add_argument('action',choices=['plan','run','get'])
        parser.add_argument('--request');parser.add_argument('--plan');parser.add_argument('--plan-sha');parser.add_argument('--manifest');parser.add_argument('--manifest-sha');parser.add_argument('--view',choices=['summary','gene-group','per-cell','field-definitions'])
        a=parser.parse_args(args)
        if a.action=='plan':
            need(a.request and not any((a.plan,a.plan_sha,a.manifest,a.manifest_sha,a.view)),'plan takes only request')
            request=parse(read_file(a.request,65536)[0]);return plan_coverage(request)
        need(a.plan and valid_sha(a.plan_sha) and not a.request,'explicit plan path/SHA')
        ref=dict(path=a.plan,sha256=a.plan_sha)
        if a.action=='run':
            need(not any((a.manifest,a.manifest_sha,a.view)),'run takes only plan');return run_coverage(ref)
        need(a.manifest and valid_sha(a.manifest_sha),'explicit manifest path/SHA')
        return get_coverage(ref,dict(path=a.manifest,sha256=a.manifest_sha),a.view or 'summary')
    result=guarded(dispatch);raw=wire(result)
    if len(raw)>CAPS['output_bytes_total']:
        result=dict(schema='scpolya.fixed_coverage.refusal.v2',status='refused',code='limit_exceeded',detail='response bytes',fresh_analysis=False);raw=wire(result)
    sys.stdout.buffer.write(raw);return 2 if result.get('status')=='refused' else 0
if __name__=='__main__':raise SystemExit(main())
