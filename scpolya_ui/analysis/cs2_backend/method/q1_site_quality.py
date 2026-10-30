#!/usr/bin/env python3
"""Independent Q1 research calculator. Default CLI action is metadata-only plan.

Frozen windows/thresholds are constants. No pipeline imports, index creation,
count filtering, optimization, gene-name matching, or combined quality score.
Linux regular files only; quiescent inputs and external timeout are required.
"""
import argparse
import bisect
from collections import Counter, defaultdict
import csv
import copy
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import zlib

VERSION = 'scpolya.q1_site_quality.v1'
REQUEST = 'scpolya.q1_site_quality.request.v1'
FROZEN_SHA = 'f6f4db8364e7792abb7d8de5e8cbf1727b7df99a698a195b0e50b91c703156c3'
PARAMETERS = dict(chromosomes=['chr1'], atlas_alias={'1': 'chr1'}, upstream_nt=50,
                  motifs=['AATAAA', 'ATTAAA'], downstream_nt=10,
                  a_run_risk_min=7, a_count_risk_min=8, atlas_distance_max=25,
                  boundary_proxy='plus=end;minus=start', base_proxy='plus=end-1;minus=start',
                  atlas_rep0='clusterID position_1based minus 1',
                  motif_offset='start_index minus 50; complete 6mer; indices 0..44',
                  endpoint_identity='chrom,strand,b0', no_combined_score=True,
                  native_context_mapping={'terminal':['Downstream_of_terminal_exon','Terminal_exon'],
                                          'intronic':['Intron'],'other':'all remaining native features'})
LIMITS = dict(native_rows=10000, atlas_rows=1000000, atlas_bytes=64*1024**2,
              atlas_expanded_bytes=512*1024**2, catalogue_bytes=16*1024**2,
              catalogue_expanded_bytes=64*1024**2, fasta_bytes=8*1024**3,
              small_source_bytes=1024**2, line_bytes=65536, field_bytes=4096,
              columns=64, request_bytes=65536, json_nodes=20000, seconds=120,
              interval_work=20000000, site_output_bytes=64*1024**2,
              summary_output_bytes=8*1024**2, manifest_output_bytes=2*1024**2,
              output_total_bytes=96*1024**2)
ROLES = {'method','catalogue','fasta','fai','atlas','genome_receipt','atlas_receipt'}
NATIVE_REQUIRED = {'peak_name','gene_id','chrom','start','end','strand','feature',
                   'misprimed_ratio','general_mispriming_pass','intronic_mispriming_pass','mispriming_pass'}
ENC = dict(sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)


class Refusal(Exception):
    def __init__(self, code, detail):
        self.code, self.detail = code, str(detail)[:300]
        super().__init__(self.detail)


def fail(code, detail):
    raise Refusal(code, detail)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, **ENC).encode('ascii')


def json_object(data, max_nodes=20000):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: fail('invalid_json', 'duplicate key')
            result[key] = value
        return result
    def nonfinite(value): fail('invalid_json', value)
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)
    pending = [(value, 0)]; nodes = 0
    while pending:
        item, depth = pending.pop(); nodes += 1
        if nodes > max_nodes or depth > 24: fail('limit_exceeded', 'JSON tree')
        if isinstance(item, dict): pending.extend((x, depth+1) for x in item.values())
        elif isinstance(item, list): pending.extend((x, depth+1) for x in item)
    return value


def regular(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts: fail('path_refused', 'absolute lexical path required')
    for part in reversed([path, *path.parents]):
        if part.is_symlink(): fail('path_refused', 'symlink component')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode): fail('path_refused', 'regular file required')
    return info


def fingerprint(info):
    return dict(device=info.st_dev, inode=info.st_ino, bytes=info.st_size,
                mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)


def integer(value, minimum=0):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,16}', value):
        fail('invalid_coordinates', 'unsigned bounded integer')
    result = int(value)
    if not minimum <= result <= 2**53-1: fail('invalid_coordinates', 'integer range')
    return result


def checked_request(request, allow_missing_hash=False):
    bounded_json(request, LIMITS['request_bytes'])
    if not isinstance(request, dict) or set(request)-{'schema','arm','output_dir','sources','limits'}:
        fail('invalid_request', 'request fields')
    if request.get('schema') != REQUEST: fail('unsupported_schema', 'request')
    if not isinstance(request.get('arm'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', request['arm']):
        fail('invalid_request', 'arm label')
    if not isinstance(request.get('sources'), dict) or set(request['sources']) != ROLES:
        fail('invalid_request', 'seven explicit sources required')
    limits = dict(LIMITS)
    overrides = request.get('limits', {})
    if not isinstance(overrides, dict) or set(overrides)-set(limits): fail('invalid_request', 'limits')
    for key, value in overrides.items():
        if type(value) is not int or not 1 <= value <= limits[key]: fail('invalid_request', 'limits only decrease')
        limits[key] = value
    for role, desc in request['sources'].items():
        if not isinstance(desc, dict) or set(desc) != {'path','sha256'} or not isinstance(desc['path'], str):
            fail('invalid_request', 'source descriptor '+role)
        expected = desc['sha256']
        if not (allow_missing_hash and expected is None) and not (isinstance(expected,str) and re.fullmatch('[0-9a-f]{64}', expected)):
            fail('invalid_request', 'expected SHA256 '+role)
        if role == 'method' and expected != FROZEN_SHA: fail('method_mismatch', 'frozen protocol hash')
    output = Path(request['output_dir'])
    if not output.is_absolute() or '..' in output.parts: fail('path_refused', 'absolute new output directory')
    for part in reversed([output.parent,*output.parent.parents]):
        if part.is_symlink(): fail('path_refused','output parent symlink')
    if not allow_missing_hash and not output.parent.is_dir(): fail('path_refused', 'output parent must already exist')
    if output.exists() or output.is_symlink(): fail('output_exists', str(output))
    # An output must not be an ancestor of any source.
    for desc in request['sources'].values():
        if output in Path(desc['path']).parents: fail('path_refused', 'output/source containment')
    return limits


def plan(request):
    """Stats only: never opens catalogue, FASTA, FAI, or atlas, never hashes them."""
    limits = checked_request(request, allow_missing_hash=True)
    sources = {}; blockers = []; identities = set()
    if not Path(request['output_dir']).parent.is_dir(): blockers.append('output_parent_not_created')
    for role, desc in sorted(request['sources'].items()):
        info = regular(desc['path']); identity = info.st_dev, info.st_ino
        if identity in identities: fail('invalid_request','source alias')
        identities.add(identity)
        cap = source_cap(role, limits)
        if info.st_size > cap: fail('limit_exceeded', role+' bytes')
        sources[role] = dict(path=desc['path'], expected_sha256=desc['sha256'], observed_stat=fingerprint(info))
        if desc['sha256'] is None: blockers.append('missing_expected_sha256:'+role)
    return dict(schema=VERSION+'.plan',status='planned',quality_analysis_executed=False,
                arm=request['arm'],parameters=copy.deepcopy(PARAMETERS),limits=limits,sources=sources,
                output_dir=request['output_dir'],execution_ready=not blockers,blockers=blockers,
                instructions='Explicit --execute only after review; external timeout 120s; 2CPU/8GiB/swap0; no index creation.',
                fingerprint_limit='Expected hashes are byte bindings, not scientific truth or proof of donor independence.')


def source_cap(role, limits):
    if role == 'fasta': return limits['fasta_bytes']
    if role == 'atlas': return limits['atlas_bytes']
    if role == 'catalogue': return limits['catalogue_bytes']
    return limits['small_source_bytes']


class Sources:
    def __init__(self, request, limits):
        self.limits = limits; self.records = {}; self.started = time.monotonic()
        self.work = 0
        try:
            identities = set()
            for role, desc in sorted(request['sources'].items()):
                info = regular(desc['path'])
                identity = info.st_dev, info.st_ino
                if identity in identities: fail('invalid_request','source alias')
                identities.add(identity)
                if info.st_size > source_cap(role,limits): fail('limit_exceeded',role+' bytes')
                fd = os.open(desc['path'], os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                self.records[role] = dict(path=desc['path'],fd=fd,before=fingerprint(info),expected_sha256=desc['sha256'])
                if fingerprint(os.fstat(fd)) != fingerprint(info): fail('changed_input',role)
                self.verify(role, 'after_initial_hash')
        except BaseException:
            self.close(); raise

    def check(self):
        if time.monotonic()-self.started > self.limits['seconds']: fail('limit_exceeded','cooperative deadline')

    def close(self):
        for record in self.records.values(): os.close(record['fd'])
        self.records.clear()

    def verify(self, role, label):
        self.check(); record=self.records[role]; fd=record['fd']; position=0; h=hashlib.sha256()
        before=record['before']
        if fingerprint(regular(record['path'])) != before or fingerprint(os.fstat(fd)) != before:
            fail('changed_input',role)
        while True:
            self.check(); data=os.pread(fd,1024**2,position)
            if not data: break
            position += len(data)
            if position > source_cap(role,self.limits): fail('limit_exceeded',role+' bytes')
            h.update(data)
        if fingerprint(os.fstat(fd)) != before or fingerprint(regular(record['path'])) != before:
            fail('changed_input',role)
        if h.hexdigest() != record['expected_sha256']: fail('hash_mismatch',role)
        record[label] = dict(sha256=h.hexdigest(),stat=fingerprint(os.fstat(fd)))

    def small(self, role):
        record=self.records[role]
        return os.pread(record['fd'], min(record['before']['bytes'],self.limits['small_source_bytes']), 0)

    def lines(self, role, compressed=False):
        record=self.records[role]; expanded=0
        # dup() shares seek position, so rewind before opening each sequential reader.
        os.lseek(record['fd'],0,os.SEEK_SET)
        with os.fdopen(os.dup(record['fd']),'rb') as raw:
            reader=gzip.GzipFile(fileobj=raw,mode='rb') if compressed else raw
            try:
                while True:
                    self.check(); data=reader.readline(self.limits['line_bytes']+1)
                    if not data: break
                    if len(data)>self.limits['line_bytes']: fail('limit_exceeded',role+' line bytes')
                    expanded += len(data)
                    cap=self.limits.get(role+'_expanded_bytes',self.limits['small_source_bytes'])
                    if expanded>cap: fail('limit_exceeded',role+' expanded bytes')
                    if b'\x00' in data: fail('invalid_source',role+' NUL')
                    try: line=data.decode('ascii').rstrip('\n').removesuffix('\r')
                    except UnicodeDecodeError: fail('invalid_source',role+' non-ASCII')
                    if any(ord(c)<32 and c!='\t' for c in line) or '\x7f' in line:
                        fail('invalid_source',role+' control character')
                    yield line
            finally:
                if compressed: reader.close()
        record['expanded_bytes']=expanded

    def fields(self, line, role):
        fields=next(csv.reader([line],delimiter='\t',strict=True))
        if len(fields)>self.limits['columns'] or any(len(x)>self.limits['field_bytes'] for x in fields):
            fail('limit_exceeded',role+' fields')
        return fields

    def manifest(self):
        return {role:{k:v for k,v in record.items() if k!='fd'} for role,record in sorted(self.records.items())}


class Fasta:
    """Read-only random access using existing .fai; never creates/rebuilds indexes."""
    def __init__(self, sources):
        self.sources=sources; self.index={}
        for line in sources.lines('fai'):
            fields=sources.fields(line,'fai')
            if len(fields)!=5 or not fields[0] or fields[0] in self.index: fail('invalid_fai','header/duplicate')
            length,offset,bases,width=[integer(x) for x in fields[1:]]
            if length<1 or bases<1 or width<bases or width>bases+2: fail('invalid_fai','line geometry')
            last=offset+(length-1)//bases*width+(length-1)%bases
            if last>=sources.records['fasta']['before']['bytes']: fail('invalid_fai','offset outside file')
            self.index[fields[0]]=(length,offset,bases,width)
        if not self.index: fail('invalid_fai','empty index')

    def window(self, chrom, lo, hi, strand):
        if chrom not in PARAMETERS['chromosomes']: return None,'outside_frozen_chr1_scope'
        if chrom not in self.index: return None,'contig_absent_from_fai'
        length,offset,bases,width=self.index[chrom]
        if lo<0 or hi>length: return None,'boundary_truncation'
        chunks=[]; position=lo
        while position<hi:
            self.sources.check()
            n=min(hi-position,bases-position%bases)
            byte=offset+position//bases*width+position%bases
            piece=os.pread(self.sources.records['fasta']['fd'],n,byte)
            if len(piece)!=n: fail('invalid_fai','short fetch')
            chunks.append(piece);position+=n
        sequence=b''.join(chunks).upper()
        if any(c not in b'ACGT' for c in sequence):
            return None,'unsupported_alphabet_N' if b'N' in sequence else 'unsupported_alphabet_other'
        value=sequence.decode('ascii')
        return (reverse_complement(value) if strand=='-' else value),None


def reverse_complement(value):
    return value.translate(str.maketrans('ACGT','TGCA'))[::-1]


def sequence_evidence(fasta, chrom, start, end, strand):
    b0=end if strand=='+' else start; p0=end-1 if strand=='+' else start
    up=(b0-50,b0) if strand=='+' else (b0,b0+50)
    down=(b0,b0+10) if strand=='+' else (b0-10,b0)
    upstream,ureason=fasta.window(chrom,*up,strand)
    downstream,dreason=fasta.window(chrom,*down,strand)
    motifs={motif:([i-50 for i in range(45) if upstream[i:i+6]==motif] if upstream is not None else None)
            for motif in PARAMETERS['motifs']}
    offsets=[x for values in motifs.values() for x in (values or [])]
    count=run=None
    if downstream is not None:
        count=downstream.count('A'); run=current=0
        for base in downstream:
            current=current+1 if base=='A' else 0;run=max(run,current)
    return dict(b0=b0,p0=p0,upstream_start0=up[0],upstream_end0=up[1],
                downstream_start0=down[0],downstream_end0=down[1],
                motif_offsets=motifs,motif_nearest_start_offset=max(offsets) if offsets else None,
                motif_present=bool(offsets) if upstream is not None else None,motif_reason=ureason,
                downstream_a_count=count,downstream_a_max_run=run,
                downstream_a_risk=(run>=7 or count>=8) if downstream is not None else None,
                downstream_reason=dreason)


class AtlasIndex:
    def __init__(self, records, sources):
        self.sources=sources
        self.by_point=sorted(records,key=lambda r:(r['rep0'],r['id']))
        self.points=[r['rep0'] for r in self.by_point]
        self.by_start=sorted(records,key=lambda r:(r['start'],r['id']))
        self.tree=[0]*(4*len(records)+4)
        def build(node,lo,hi):
            if lo+1==hi: self.tree[node]=self.by_start[lo]['end'];return
            mid=(lo+hi)//2;build(node*2,lo,mid);build(node*2+1,mid,hi)
            self.tree[node]=max(self.tree[node*2],self.tree[node*2+1])
        if records: build(1,0,len(records))

    def query(self,start,end,p0):
        insertion=bisect.bisect_left(self.points,p0)
        near=[self.points[i] for i in (insertion-1,insertion) if 0<=i<len(self.points)]
        distance=min(abs(p0-point) for point in near)
        tied=[]
        for point in sorted({p0-distance,p0+distance}):
            lo=bisect.bisect_left(self.points,point);hi=bisect.bisect_right(self.points,point)
            tied.extend(self.by_point[lo:hi])
        tied.sort(key=lambda r:r['id'])
        overlaps=0
        def visit(node,lo,hi):
            nonlocal overlaps
            self.sources.check();self.sources.work+=1
            if self.sources.work>self.sources.limits['interval_work']: fail('limit_exceeded','interval query work')
            if self.tree[node]<=start or self.by_start[lo]['start']>=end: return
            if lo+1==hi: overlaps+=1;return
            mid=(lo+hi)//2;visit(node*2,lo,mid);visit(node*2+1,mid,hi)
        visit(1,0,len(self.by_start))
        return dict(atlas_distance=distance,atlas_concordant=distance<=25,atlas_reason=None,
                    atlas_tie_count=len(tied),atlas_tied_cluster_ids=[r['id'] for r in tied],
                    atlas_nearest_cluster_id=tied[0]['id'],
                    atlas_nearest_cluster_width=tied[0]['end']-tied[0]['start'],atlas_overlap_count=overlaps)


def read_atlas(sources):
    groups=defaultdict(list);ids=set();audit=Counter();rows=0
    for line in sources.lines('atlas',compressed=True):
        rows+=1
        if rows>sources.limits['atlas_rows']: fail('limit_exceeded','atlas rows')
        fields=sources.fields(line,'atlas')
        if len(fields)!=11: fail('invalid_atlas','expected 11 columns')
        chrom,start,end,cid,_,strand=fields[:6]
        start,end=integer(start),integer(end,1)
        if end<=start or strand not in ('+','-'): fail('invalid_atlas','interval/strand')
        pieces=cid.rsplit(':',2)
        if len(pieces)!=3 or pieces[0]!=chrom or pieces[2]!=strand: fail('invalid_atlas','representative identity')
        rep0=integer(pieces[1],1)-1
        if not start<=rep0<end: fail('invalid_atlas','representative outside cluster interval')
        # Only this explicit alias is accepted; chr1 itself is exact identity.
        mapped=PARAMETERS['atlas_alias'].get(chrom,chrom)
        if mapped not in PARAMETERS['chromosomes']:
            audit['outside_frozen_scope:'+chrom]+=1;continue
        if cid in ids: fail('duplicate_atlas_cluster_id',cid)
        ids.add(cid);audit['retained:'+mapped+':'+strand]+=1
        groups[mapped,strand].append(dict(start=start,end=end,rep0=rep0,id=cid))
    return {key:AtlasIndex(records,sources) for key,records in groups.items()},dict(rows=rows,counts=dict(sorted(audit.items())))


def atlas_evidence(indexes,chrom,start,end,strand,p0):
    index=indexes.get((chrom,strand))
    if index is not None: return index.query(start,end,p0)
    return dict(atlas_distance=None,atlas_concordant=None,atlas_reason='no_same_contig_strand_reference',
                atlas_tie_count=None,atlas_tied_cluster_ids=None,atlas_nearest_cluster_id=None,
                atlas_nearest_cluster_width=None,atlas_overlap_count=None)


def native_evidence(row):
    flags={}
    for field in ('general_mispriming_pass','intronic_mispriming_pass','mispriming_pass'):
        raw=row[field]
        if raw not in ('True','False',''): fail('invalid_catalogue','native flag text')
        flags[field]={'True':True,'False':False,'':None}[raw]
    raw=row['misprimed_ratio']
    if raw in ('','NaN','nan'):
        ratio=None
    else:
        ratio=float(raw)
        if not math.isfinite(ratio) or not 0<=ratio<=1: fail('invalid_catalogue','misprimed_ratio')
    value=flags['mispriming_pass'] if ratio is not None else None
    reason='missing_native_ratio' if ratio is None else ('missing_native_pass' if value is None else None)
    return dict(native_read_end_pass=value,native_read_end_reason=reason,
                native_read_end_state=('not_evaluable' if value is None else ('pass' if value else 'fail')))


def read_catalogue(sources):
    iterator=iter(sources.lines('catalogue',compressed=True))
    try: header=sources.fields(next(iterator),'catalogue')
    except StopIteration: fail('invalid_catalogue','missing header')
    if len(set(header))!=len(header) or not NATIVE_REQUIRED.issubset(header): fail('invalid_catalogue','header')
    rows=[];peaks=set()
    for line in iterator:
        if len(rows)>=sources.limits['native_rows']: fail('limit_exceeded','native PAS rows')
        fields=sources.fields(line,'catalogue')
        if len(fields)!=len(header): fail('invalid_catalogue','row width')
        row=dict(zip(header,fields));peak=row['peak_name']
        if not peak or peak in peaks: fail('duplicate_native_peak_name',peak)
        peaks.add(peak)
        if not row['chrom'] or row['strand'] not in ('+','-'): fail('invalid_catalogue','chrom/strand')
        start,end=integer(row['start']),integer(row['end'],1)
        if end<=start: fail('invalid_catalogue','interval')
        native_evidence(row)  # validate all rows, regardless of native pass or counts
        rows.append((row,start,end))
    return header,rows


def verify_receipts(sources):
    genome=json_object(sources.small('genome_receipt'),sources.limits['json_nodes'])
    atlas=json_object(sources.small('atlas_receipt'),sources.limits['json_nodes'])
    if genome.get('schema')!='scpolya.genome_reference_intake.v1' or atlas.get('schema')!='scpolya.polyasite_v2_reference_intake.v1':
        fail('receipt_mismatch','schema')
    for role,receipt,field in [('fasta',genome,'local_sha256'),('fai',genome,'local_sha256'),('atlas',atlas,'sha256_observed')]:
        record=sources.records[role]
        matches=[r for r in receipt['files'] if r.get('name')==Path(record['path']).name]
        if len(matches)!=1 or matches[0].get(field)!=record['expected_sha256']:
            fail('receipt_mismatch',role)
    protocol=json_object(sources.small('method'),sources.limits['json_nodes'])
    if protocol.get('schema')!='scpolya.q1_quality_protocol.v1': fail('method_mismatch','schema')
    return protocol


METRICS={'motif_present':'motif_reason','downstream_a_risk':'downstream_reason',
         'atlas_concordant':'atlas_reason','native_read_end_pass':'native_read_end_reason'}


def summarize_group(rows):
    result={'rows':len(rows),'metrics':{}}
    for metric,reason in METRICS.items():
        counts=Counter('NA' if r[metric] is None else ('true' if r[metric] else 'false') for r in rows)
        result['metrics'][metric]=dict(evaluable_n=counts['true']+counts['false'],true_n=counts['true'],
            false_n=counts['false'],na_n=counts['NA'],
            na_reasons=dict(sorted(Counter(r[reason] for r in rows if r[metric] is None).items())))
    return result


def summarize(rows):
    result=summarize_group(rows)
    for name,key in [('by_feature',lambda r:r['source_feature']),('by_context',lambda r:r['context_group']),
                     ('by_strand',lambda r:r['source_strand']),('by_native_pass',lambda r:r['native_read_end_state'])]:
        groups=defaultdict(list)
        for row in rows: groups[key(row)].append(row)
        result[name]={group:summarize_group(values) for group,values in sorted(groups.items())}
    result['cross_tabs']=[dict(motif=key[0],downstream_a_risk=key[1],atlas_concordant=key[2],native_pass=key[3],rows=n)
        for key,n in sorted(Counter(tuple('NA' if r[k] is None else ('true' if r[k] else 'false') for k in METRICS) for r in rows).items())]
    result['atlas_distance_histogram']={str(k):v for k,v in sorted(Counter(r['atlas_distance'] for r in rows if r['atlas_distance'] is not None).items())}
    result['native_width_histogram']={str(k):v for k,v in sorted(Counter(r['native_width'] for r in rows).items())}
    for name,field in [('endpoint','endpoint_record_count'),('coordinate','coordinate_record_count')]:
        multiplicities=Counter(r[field] for r in rows)
        result[name+'_duplicates']=dict(rows_in_repeated_groups=sum(n for m,n in multiplicities.items() if m>1),
            repeated_groups=sum(n//m for m,n in multiplicities.items() if m>1),
            excess_rows=sum(n-n//m for m,n in multiplicities.items() if m>1))
    return result


def site_cell(value):
    if value is None: return ''
    if isinstance(value,(list,dict)): return json.dumps(value,**ENC)
    if type(value) is bool: return 'true' if value else 'false'
    return str(value)


def tsv_line(values):
    handle=io.StringIO(newline='')
    csv.writer(handle,delimiter='\t',lineterminator='\n').writerow(values)
    return handle.getvalue().encode('ascii')


def write_outputs(request,sources,header,rows,summary,protocol):
    limits=sources.limits;output=Path(request['output_dir']);stage=None;created=[];made_output=False
    try:
        stage=Path(tempfile.mkdtemp(prefix='.q1-staging-',dir=output.parent))
        site=stage/'sites.tsv';total=0
        # A header-only catalogue still emits the full schema.
        fields=['source_'+x for x in header]+list(sequence_evidence_fields())+list(atlas_evidence({},'chr1',0,1,'+',0))
        fields+=['native_read_end_pass','native_read_end_reason','native_read_end_state',
                 'context_group','native_width','endpoint_record_count','coordinate_record_count']
        with site.open('wb') as handle:
            for values in [fields]:
                data=tsv_line(values);handle.write(data);total+=len(data)
            for row in rows:
                sources.check()
                data=tsv_line([site_cell(row[field]) for field in fields])
                total+=len(data)
                if len(data)>limits['line_bytes']*4 or total>limits['site_output_bytes']:
                    fail('limit_exceeded','site output')
                handle.write(data)
        (stage/'summary.json').write_bytes(bounded_json(summary,limits['summary_output_bytes']))
        (stage/'method.json').write_bytes(sources.small('method'))
        # Re-read all original source bytes, including full FASTA, before committing any output.
        for role in sorted(sources.records): sources.verify(role,'after_analysis_final_hash')
        manifest=dict(schema=VERSION+'.sources',request_sha256=sha(encoded(request)),
                      method_sha256=FROZEN_SHA,parameters_sha256=sha(encoded(PARAMETERS)),
                      implementation_sha256=sha(Path(__file__).read_bytes()),
                      sources=sources.manifest(),arm=request['arm'],parameters=copy.deepcopy(PARAMETERS),
                      output_schema=VERSION+'.sites',source_access='read-only; no index creation',
                      limitations=protocol['limitations'],
                      index_validation='Existing receipt-bound FAI geometry and file bounds checked; no full FASTA-to-index semantic reconstruction.',
                      integrity_limit='Hashes bind caller-selected file bytes; no scientific truth or donor independence certification; no concurrent-writer sandbox.',
                      output_files={p.name:dict(bytes=p.stat().st_size,sha256=sha(p.read_bytes())) for p in sorted(stage.iterdir())})
        (stage/'source-manifest.json').write_bytes(bounded_json(manifest,limits['manifest_output_bytes']))
        if sum(p.stat().st_size for p in stage.iterdir())>limits['output_total_bytes']: fail('limit_exceeded','total output')
        sources.check()
        os.mkdir(output);made_output=True
        # Exclusive hard links publish already-complete files without replacing any name.
        for path in sorted(stage.iterdir()):
            target=output/path.name;os.link(path,target);created.append(target)
        return dict(schema=VERSION,status='ok',arm=request['arm'],site_rows=len(rows),output_dir=str(output),
                    files=[p.name for p in created],quality_analysis_executed=True)
    except BaseException:
        for path in created: path.unlink(missing_ok=True)
        if made_output:
            try: output.rmdir()
            except OSError: pass
        raise
    finally:
        if stage is not None: shutil.rmtree(stage)


def bounded_json(value,cap):
    chunks=[];size=0
    for chunk in json.JSONEncoder(**ENC).iterencode(value):
        data=chunk.encode('ascii');size+=len(data)
        if size>cap: fail('limit_exceeded','JSON output')
        chunks.append(data)
    return b''.join(chunks)


def sequence_evidence_fields():
    return ('b0','p0','upstream_start0','upstream_end0','downstream_start0','downstream_end0',
            'motif_offsets','motif_nearest_start_offset','motif_present','motif_reason',
            'downstream_a_count','downstream_a_max_run','downstream_a_risk','downstream_reason')


def execute(request):
    limits=checked_request(request)
    sources=Sources(request,limits)
    try:
        protocol=verify_receipts(sources);fasta=Fasta(sources);indexes,atlas_audit=read_atlas(sources)
        header,native=read_catalogue(sources)
        endpoint_counts=Counter((r['chrom'],r['strand'],end if r['strand']=='+' else start) for r,start,end in native)
        coord_counts=Counter((r['chrom'],r['strand'],start,end) for r,start,end in native)
        rows=[]; aggregate_site_bytes=0
        for row,start,end in native:
            sources.check();chrom,strand=row['chrom'],row['strand']
            result={'source_'+key:value for key,value in row.items()}
            result.update(sequence_evidence(fasta,chrom,start,end,strand))
            result.update(atlas_evidence(indexes,chrom,start,end,strand,result['p0']))
            result.update(native_evidence(row))
            result.update(context_group='terminal' if row['feature'] in PARAMETERS['native_context_mapping']['terminal'] else ('intronic' if row['feature']=='Intron' else 'other'),
                          native_width=end-start,endpoint_record_count=endpoint_counts[chrom,strand,result['b0']],
                          coordinate_record_count=coord_counts[chrom,strand,start,end])
            aggregate_site_bytes+=len(bounded_json(result,limits['site_output_bytes']))
            if aggregate_site_bytes>limits['site_output_bytes']: fail('limit_exceeded','aggregate site records')
            rows.append(result)
        summary=dict(schema=VERSION+'.summary',arm=request['arm'],method_sha256=FROZEN_SHA,
                     parameters=copy.deepcopy(PARAMETERS),atlas_audit=atlas_audit,**summarize(rows),
                     interpretation='Four separate evidence dimensions; NA excluded from evaluable denominator; no single score or optimization.',
                     native_count_filter_used=False,interval_overlap='secondary description only; never combined with distance')
        return write_outputs(request,sources,header,rows,summary,protocol)
    finally: sources.close()


def response(request,execute_analysis=False):
    try:
        return execute(request) if execute_analysis else plan(request)
    except Refusal as exc:
        return dict(schema=VERSION,status='refused',code=exc.code,detail=exc.detail,quality_analysis_executed=False)
    except (OSError,ValueError,TypeError,KeyError,AttributeError,EOFError,RecursionError,OverflowError,zlib.error,csv.Error) as exc:
        return dict(schema=VERSION,status='refused',code='invalid_source_or_request',detail=type(exc).__name__,quality_analysis_executed=False)


class Parser(argparse.ArgumentParser):
    def error(self,message): fail('invalid_cli',message)


def main(argv=None):
    try:
        parser=Parser(description=__doc__);parser.add_argument('--request',required=True)
        action=parser.add_mutually_exclusive_group();action.add_argument('--plan',action='store_true')
        action.add_argument('--execute',action='store_true')
        args=parser.parse_args(argv)
        info=regular(args.request)
        if info.st_size>LIMITS['request_bytes']: fail('limit_exceeded','request bytes')
        with open(args.request,'rb') as handle: data=handle.read(LIMITS['request_bytes']+1)
        if len(data)>LIMITS['request_bytes']: fail('limit_exceeded','request bytes')
        result=response(json_object(data),args.execute)
    except Refusal as exc:
        result=dict(schema=VERSION,status='refused',code=exc.code,detail=exc.detail,quality_analysis_executed=False)
    except (OSError,ValueError,TypeError,RecursionError) as exc:
        result=dict(schema=VERSION,status='refused',code='invalid_request',detail=type(exc).__name__,quality_analysis_executed=False)
    sys.stdout.write(json.dumps(result,**ENC)+'\n')
    return 2 if result['status']=='refused' else 0


if __name__=='__main__':
    raise SystemExit(main())
