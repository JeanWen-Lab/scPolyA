"""Explicit, pinned CS3 tasks; orchestration is separate from frozen algorithms."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone

SCHEMA = 'scpolya.gui.cs3.v1'
HERE = Path(__file__).resolve().parent
RESOURCES = dict(cpu_cores=2, memory_bytes=17179869184, swap_bytes=0, wall_seconds=7200, disk_bytes=21474836480)
LIMITATIONS = [
    'Descriptive evidence only: atlas absence is not novelty, validation or biological absence.',
    'Pooled mouse data do not establish neuron specificity.',
    'Coordinate base and assembly are supplied declarations, not inferred or independently certified.',
    'Sequence A-richness and stored native read-level risk are separate channels; missing evidence remains NA.',
    'Current retained-file snapshot associates a run/config; it is not an execution-time artifact seal.',
    'Resource enforcement and terminal process-death receipts belong to the calling supervisor; no automatic retry.'
]
TRANSFORM_ROLES = {'source_table', 'chain', 'source_sizes', 'target_sizes', 'liftover'}
CANDIDATE_ROLES = {'catalogue', 'counts', 'reference', 'reference_contract', 'coverage', 'fasta', 'fai', 'target_sizes', 'sequence_contract', 'projection_receipt', 'sequence_sizes'}
PROJECTION_ROLES = {'status', 'source_map', 'audit_mapped', 'chain', 'source_sizes', 'target_sizes', 'source_coverage', 'sequence_contract', 'fasta', 'fai', 'catalogue', 'transform_manifest', 'transform_contract', 'sequence_sizes'}

class Refusal(ValueError):
    pass

def need(ok, why):
    if not ok:
        raise Refusal(why)

def now():
    return datetime.now(timezone.utc).isoformat()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()

def sha(path):
    value = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1048576), b''):
            value.update(chunk)
    return value.hexdigest()

def state(path):
    s = Path(path).stat()
    return dict(dev=s.st_dev, ino=s.st_ino, bytes=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)

def pin(path):
    p = Path(path)
    need(p.is_absolute() and not p.is_symlink() and p.is_file(), 'absolute regular non-symlink source required')
    before = state(p)
    value = dict(path=str(p), bytes=before['bytes'], sha256=sha(p), stat=before)
    need(state(p) == before, 'source changed during pinning')
    return value

def check_pin(value):
    need(isinstance(value, dict) and re.fullmatch('[0-9a-f]{64}', value.get('sha256', '')) is not None, 'explicit source SHA256 required')
    actual = pin(value['path'])
    need(actual['sha256'] == value['sha256'], 'source hash mismatch: '+value['path'])
    if 'stat' in value:
        need(actual['stat'] == value['stat'], 'source stat changed: '+value['path'])
    if 'bytes' in value:
        need(actual['bytes'] == value['bytes'], 'source size changed: '+value['path'])
    return actual

def read_json(path, cap=8388608):
    need(Path(path).stat().st_size <= cap, 'JSON exceeds bounded metadata size')
    return json.loads(Path(path).read_text())

def write_json(path, value):
    p = Path(path)
    tmp = p.with_name(p.name+'.tmp')
    tmp.write_bytes(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode()+b'\n')
    os.replace(tmp, p)

def method_pins():
    paths=[p for p in sorted(HERE.iterdir()) if p.suffix == '.py' or p.name == 'METHOD-SOURCES.json']
    paths += [HERE.parents[1]/name for name in ('__init__.py','services/run_evidence_service.py','registry.py','run_data.py','run_records.py')]
    return [pin(p) for p in paths]

def capabilities():
    common = {'schema': SCHEMA, 'mode': 'synthetic | historical_export | native_snapshot', 'assembly': 'target assembly',
              'scope': 'actual source population', 'atlas': {'name':'atlas name','release':'version','source_assembly':'source build','target_assembly':'target build','source_coordinate_base':'0based | 1based','coordinate_status':'documented | operational_inference | user_declared','strand_semantics':'transcribed_RNA_strand','reference_population':'named assessed population','source_provenance':'source description/URL','mitochondrial_contig':'chrM (v1 only)','mitochondrial_policy':'ucsc_chrM_conservative_v1 (read-only)'},
              'inputs': '{role: {path: absolute regular file, sha256: 64 hex}}', 'limits': 'positive integer per-operation row/block bounds',
              'native': 'mode-specific binding; see docs/cs3_backend.md'}
    return [dict(id='cs3.'+op, label=label, description=label, input_schema={**common, **extra}, resource_defaults=dict(RESOURCES), limitations=list(LIMITATIONS), parameters={'atlas_distance_nt':100,'motif_window':'upstream 50..1; AAUAAA or AUUAAA','arich_window':'downstream 1..20; A>=12/20, frozen; ambiguous positive is lower_bound'})
            for op,label,extra in [('transform','Atlas coordinate transform',{'format':'polyadb32 | normalized_points','accepted_target_contigs':'explicit list','chain_direction':{'source_side':'t','target_side':'q'},'liftover_version':'explicit pinned binary version'}),
                                   ('project','Project accounted reference',{'source_build':'assembly','target_build':'assembly','normalized_source_coordinate_base':'1based (read-only; source_position column is normalized)' ,'assessed_target_contigs':'explicit list; no implicit contig aliases','sequence_sizes':'pin: exact FASTA/FAI contigs and lengths','target_sizes':'pin: official chain q-side sizes; distinct from sequence_sizes','transform_receipt':'pin to this transform index.json'}),
                                   ('candidates','Atlas, sequence and native risk evidence',{'pair_semantics':'unknown | unique_pairs_required'})]]

def validate_atlas(request):
    a=request['atlas']
    need(all(isinstance(a.get(k), str) and a[k] for k in ('name','release','source_assembly','target_assembly','source_coordinate_base','coordinate_status','strand_semantics','reference_population','source_provenance','mitochondrial_contig','mitochondrial_policy')), 'complete atlas declaration required')
    need(a['source_coordinate_base'] in {'0based','1based'} and a['coordinate_status'] in {'documented','operational_inference','user_declared'}, 'explicit coordinate declaration/status required')
    need(a['strand_semantics']=='transcribed_RNA_strand' and a['target_assembly']==request['assembly'], 'atlas strand/target declaration mismatch')
    need(a['mitochondrial_contig']=='chrM' and a['mitochondrial_policy']=='ucsc_chrM_conservative_v1', 'v1 supports only declared UCSC chrM conservative exclusion; other mitochondrial naming/semantics unsupported')
    if 'poly' in a['name'].lower() and '3.2' in a['release']:
        need(a['source_coordinate_base']=='1based' and a['coordinate_status']=='operational_inference', 'PolyA_DB3.2 uses disclosed operational one-based inference, not certified base')

def validate_native(request):
    native=request.get('native', {})
    mode=request['mode']
    if request['method']!='cs3.candidates':
        return
    if mode=='native_snapshot':
        from scpolya_ui.services.run_evidence_service import verify_binding
        need(native.get('classification')=='new_snapshot_of_current_retained_files', 'native mode requires current snapshot binding')
        need(all(native.get(k) for k in ('run_record_id','run_id','view_root','invocation_id','config','invocation')), 'selected run/invocation/config required')
        verify_binding(native)
        root=Path(native['view_root']).resolve()
        for role in ('catalogue','counts'):
            need(Path(request['inputs'][role]['path']).resolve().is_relative_to(root), 'native table outside selected run root')
    else:
        need(native.get('classification')==mode and native.get('source_provenance') and not native.get('invocation_id'), 'historical/synthetic identity must be explicit without fabricated native invocation')

def bounded_limits(request, keys):
    v=request.get('limits',{})
    need(set(v)==set(keys) and all(type(x) is int and 0<x<=100000000 for x in v.values()), 'explicit bounded row/block limits required: '+str(keys))

def plan(request):
    request=json.loads(canonical(request))
    need(request.get('schema')==SCHEMA and request.get('method') in {'cs3.transform','cs3.project','cs3.candidates'}, 'wrong method/schema')
    need(request.get('mode') in {'synthetic','historical_export','native_snapshot'} and request.get('assembly') and request.get('scope'), 'mode/assembly/scope required')
    validate_atlas(request)
    method=request['method']
    roles=TRANSFORM_ROLES if method=='cs3.transform' else PROJECTION_ROLES if method=='cs3.project' else CANDIDATE_ROLES
    need(set(request['inputs'])==roles or method=='cs3.candidates' and set(request['inputs'])==roles|{'labels'}, 'input roles mismatch')
    if method=='cs3.transform':
        bounded_limits(request,{'source_rows'})
        need(request.get('format') in {'polyadb32','normalized_points'}, 'source format required')
        need(request.get('chain_direction')=={'source_side':'t','target_side':'q'}, 'explicit chain direction required')
        need(isinstance(request.get('liftover_version'),str) and request['liftover_version'], 'binary version declaration required')
        need('chrM' not in request.get('accepted_target_contigs',[]), 'v1 mitochondrial chrM cannot be an accepted target')
        need(isinstance(request.get('accepted_target_contigs'),list) and request['accepted_target_contigs'] and len(set(request['accepted_target_contigs']))==len(request['accepted_target_contigs']), 'accepted target contigs required')
    elif method=='cs3.project':
        bounded_limits(request,{'source_rows','audit_rows','chain_blocks','catalogue_rows'})
        need(request['source_build']==request['atlas']['source_assembly'] and request['target_build']==request['assembly'], 'projection build mismatch')
        need(request.get('normalized_source_coordinate_base')=='1based', 'projection source_position is explicitly normalized 1based; original source declaration is separate')
        check_pin(request['transform_receipt'])
    else:
        bounded_limits(request,{'catalogue_rows','count_rows','reference_rows','label_rows'})
        need(request.get('pair_semantics') in {'unknown','unique_pairs_required'}, 'explicit count-pair semantics required')
    sources=[dict(role=role,**check_pin(value)) for role,value in sorted(request['inputs'].items())]
    # Binding metadata is captured as input evidence as well as verified by the run reader.
    if method=='cs3.candidates' and request['mode']=='native_snapshot':
        for role in ('invocation','namespace_manifest'):
            if role in request['native']:
                sources.append(dict(role='native.'+role,**check_pin(request['native'][role])))
    if method=='cs3.project':
        sources.append(dict(role='transform_receipt',**check_pin(request['transform_receipt'])))
    for role in ('target_sizes','sequence_sizes'):
        if role in request['inputs']:
            need(not ({'MT','chrMT','M'} & sizes(request['inputs'][role]['path']).keys()), 'v1 does not support mitochondrial MT/chrMT/M naming; no alias conversion')
    validate_native(request)
    value=dict(schema=SCHEMA+'.plan',method=method,status='ready',request=request,parameters={k:v for k,v in request.items() if k not in {'inputs','native'}},method_sources=method_pins(),input_sources=sources,scope={'assembly':request['assembly'],'population':request['scope'],'atlas':request['atlas']},resources=dict(RESOURCES),limitations=list(LIMITATIONS))
    if method in {'cs3.project','cs3.candidates'}:
        value['scope']['contig_compatibility']=contig_compatibility(sizes(request['inputs']['target_sizes']['path']),sizes(request['inputs']['sequence_sizes']['path']))
    value['runtime']={'python':sys.version,'executable':pin(Path(sys.executable).resolve())}
    value['plan_sha256']=hashlib.sha256(canonical(value)).hexdigest()
    return value

def verify_plan(value):
    unsigned={k:v for k,v in value.items() if k!='plan_sha256'}
    need(value.get('schema')==SCHEMA+'.plan' and hashlib.sha256(canonical(unsigned)).hexdigest()==value.get('plan_sha256'), 'plan was changed')
    need(value.get('runtime')=={'python':sys.version,'executable':pin(Path(sys.executable).resolve())}, 'interpreter changed since preview')
    need(value['method_sources']==method_pins(), 'method source changed; preview again')
    for p in value['input_sources']:
        check_pin(p)
    validate_native(value['request'])

def sizes(path):
    result={}
    for line in Path(path).read_text().splitlines():
        fields=line.split('\t')
        need(len(fields)==2 and fields[0] not in result and fields[1].isdigit() and int(fields[1])>0, 'invalid/duplicate contig size')
        result[fields[0]]=int(fields[1])
    need(result,'empty contig sizes')
    return result

def contig_compatibility(chain_sizes, sequence_sizes):
    shared=sorted(chain_sizes.keys() & sequence_sizes.keys())
    return {'chain_only':sorted(chain_sizes.keys()-sequence_sizes.keys()),
            'sequence_only':sorted(sequence_sizes.keys()-chain_sizes.keys()),
            'shared':shared,'shared_count':len(shared),
            'shared_length_mismatches':[{'contig':k,'chain_length':chain_sizes[k],'sequence_length':sequence_sizes[k]} for k in shared if chain_sizes[k]!=sequence_sizes[k]],
            'policy':'actual assessed catalogue/reference/exclusion contigs must be shared with equal lengths; no aliases or dropped rows'}

def require_actual_contigs(request, contigs):
    inputs=request['inputs']; official=sizes(inputs['target_sizes']['path']); seq=sizes(inputs['sequence_sizes']['path'])
    for chrom in contigs:
        need(chrom in official and chrom in seq and official[chrom]==seq[chrom], 'actual evaluated contig not shared/equal between chain and sequence sizes: '+chrom)
    return contig_compatibility(official,seq)

def sequence_contract(request):
    from .adapter import IndexedFasta
    inputs=request['inputs']; definition=read_json(inputs['sequence_contract']['path'])
    need(definition.get('assembly')==request['assembly'] and definition.get('source_provenance') and definition.get('identity_scope'), 'sequence identity declaration required')
    for role in ('fasta','fai'):
        need(definition.get(role+'_sha256')==inputs[role]['sha256'], 'FASTA/FAI contract pin mismatch')
    genome=IndexedFasta(inputs['fasta']['path'],inputs['fai']['path'])
    try:
        actual={k:v[0] for k,v in genome.index.items()}
        need(actual==sizes(inputs['sequence_sizes']['path']), 'FASTA/FAI vs sequence target contig names/lengths mismatch')
        # Verify FAI header offsets, line widths and complete physical sequence bytes, bounded by FASTA bytes.
        seen={}; current=None; length=0
        with open(inputs['fasta']['path'],'rb') as handle:
            while True:
                offset=handle.tell(); line=handle.readline()
                if not line: break
                if line.startswith(b'>'):
                    if current is not None: seen[current]=length
                    current=line[1:].split()[0].decode('ascii'); length=0
                    need(current in genome.index and current not in seen,'FASTA header/FAI mismatch')
                    need(handle.tell()==genome.index[current][1],'FAI sequence offset mismatch')
                else:
                    need(current is not None and line.rstrip(b'\r\n'),'empty/invalid FASTA line')
                    bases=line.rstrip(b'\r\n'); item=genome.index[current]
                    need(len(bases)<=item[2] and (length%item[2])==0,'FASTA line/FAI mismatch')
                    if len(bases)==item[2] and length+len(bases)<item[0]:
                        need(len(line)==item[3],'FASTA line width mismatch')
                    length+=len(bases)
            if current is not None: seen[current]=length
        need(seen==actual,'FASTA sequence lengths mismatch')
    finally:
        genome.close()
    return definition

def transform(request,out):
    from . import run_transform as t
    pins=request['inputs']; path=lambda r:pins[r]['path']
    source,target=sizes(path('source_sizes')),sizes(path('target_sizes'))
    need(set(request['accepted_target_contigs'])<=target.keys(),'accepted target absent from sizes')
    index=t.build_chain_index(path('chain'),source,target)
    out.mkdir()
    ordinal=0
    if request['format']=='polyadb32':
        need(request['atlas']['source_coordinate_base']=='1based','PolyA_DB parser requires operational 1based')
        values=t.parse_original_source(path('source_table'))
    else:
        from .adapter import rows,integer
        def points():
            for n,row in rows(path('source_table'),{'pas_id','chrom','position','strand'}):
                yield n+1,row['pas_id'],row['chrom'],integer(row['position'],'source position'),row['strand']
        values=points()
    with (out/'input.mm9.bed').open('w') as bed,(out/'source-map.tsv').open('w') as sidecar:
        sidecar.write('\t'.join(t.SOURCE_HEADER)+'\n')
        for ordinal,pas_id,chrom,position,strand in values:
            need(ordinal<=request['limits']['source_rows'],'source row limit exceeded')
            pos0=position-int(request['atlas']['source_coordinate_base']=='1based')
            need(pas_id and strand in {'+','-'} and chrom in source and 0<=pos0<source[chrom], 'invalid source point identity/strand/bounds')
            bed.write(f'{chrom}\t{pos0}\t{pos0+1}\t{ordinal}\t1000\t{strand}\n')
            row=dict(ordinal=ordinal,source_pas_id=pas_id,source_chromosome=chrom,source_position_1based=pos0+1,source_position_0based=pos0,source_strand=strand,source_bed_start=pos0,source_bed_end=pos0+1)
            sidecar.write('\t'.join(str(row[k]) for k in t.SOURCE_HEADER)+'\n')
    need(ordinal>0,'empty source atlas')
    checks,_=t.run_accounted(out,source,target,input_bed=out/'input.mm9.bed',chain_path=path('chain'),index=index,accepted_targets=set(request['accepted_target_contigs']),binary=path('liftover'))
    manifest=dict(schema=SCHEMA+'.transform',input_ordinal_count=ordinal,source_table=pins['source_table'],chain=pins['chain'],liftOver=pins['liftover'],status_counts=checks['status_counts'],all_checks_pass=checks['all_checks_pass'],candidate_queries_run=0,audit_metrics={k:checks[k] for k in ('audit_mapped_rows','audit_unmapped_rows','audit_reason_codes','raw_audit_rows_preserved')},atlas=request['atlas'])
    write_json(out/'RUN-MANIFEST.json',manifest)
    write_json(out/'TRANSFORM-CONTRACT.json',{'status_contract':{'columns':t.STATUS_HEADER}})
    need(checks['all_checks_pass'],'transform accounting failed; partial evidence retained')
    return manifest

def project(request,out):
    from . import project_reference as p
    inputs=request['inputs']; receipt=read_json(request['transform_receipt']['path'])
    need(receipt.get('status')=='succeeded' and receipt.get('method')=='cs3.transform', 'successful transform result receipt required')
    # Every reused transform product is bound to the referenced task manifest, not just a caller assertion.
    artifacts={x['path']:x for x in receipt['artifacts']+receipt['tables']}
    for role,name in [('status','final-status.tsv'),('source_map','source-map.tsv'),('audit_mapped','audit.mapped.bed'),('transform_manifest','RUN-MANIFEST.json'),('transform_contract','TRANSFORM-CONTRACT.json')]:
        need(artifacts.get('data/'+name,{}).get('sha256')==inputs[role]['sha256'],'transform receipt does not bind '+role)
    for role in ('chain','source_sizes','target_sizes'):
        prior={x['role']:x for x in receipt['input_sources']}
        need(prior.get(role,{}).get('sha256')==inputs[role]['sha256'],'transform source identity mismatch: '+role)
    need(receipt['parameters']['atlas']==request['atlas'],'atlas declaration changed since transform')
    sequence_contract(request)
    from .adapter import rows
    actual=set()
    for ordinal,row in rows(inputs['catalogue']['path'],{'chrom'}):
        need(ordinal<request['limits']['catalogue_rows'],'catalogue row limit exceeded')
        actual.add(row['chrom'])
    require_actual_contigs(request, actual | set(request['assessed_target_contigs']))
    metadata={k:read_json(inputs[k]['path']) for k in ('source_coverage','sequence_contract','transform_manifest','transform_contract')}
    req=dict(request, transform_receipt=request['transform_receipt'])
    req['point_semantics']=(f"source singleton normalized to {request['normalized_source_coordinate_base']} from declared {request['atlas']['source_coordinate_base']} ({request['atlas']['coordinate_status']}); catalogue last-templated-base proxy distance")
    snapshots={r:p.state(v['path']) for r,v in inputs.items()}
    p.project(req,out,snapshots,metadata)
    # Contract source_position is normalized 1based in the projected table; the original declaration is retained separately.
    definition=read_json(out/'reference-contract.json'); definition['source_coordinate_declaration']=request['atlas']
    write_json(out/'reference-contract.json',definition)
    return read_json(out/'SUMMARY.json')

def candidates(request,out):
    from . import adapter as a
    inputs=request['inputs']; sequence_contract(request)
    definition=read_json(inputs['reference_contract']['path']); coverage=read_json(inputs['coverage']['path'])
    receipt=read_json(inputs['projection_receipt']['path'])
    need(receipt.get('method')=='cs3.project' and receipt.get('status')=='succeeded','successful projection receipt required')
    artifacts={x['path']:x for x in receipt['artifacts']+receipt['tables']}
    for role,name in [('reference','reference.tsv'),('coverage','coverage.json'),('reference_contract','reference-contract.json')]:
        need(artifacts.get('data/'+name,{}).get('sha256')==inputs[role]['sha256'],'projection receipt does not bind '+role)
    need(receipt['parameters']['atlas']==request['atlas'],'atlas declaration changed since projection')
    need(definition['build']==coverage['build']==request['assembly'],'reference build mismatch')
    prior={x['role']:x for x in receipt['input_sources']}
    for role in ('target_sizes','sequence_sizes'):
        need(prior.get(role,{}).get('sha256')==inputs[role]['sha256'],'reference/sequence size contract changed after projection: '+role)
    sequence_sizes=sizes(inputs['sequence_sizes']['path'])
    actual=set()
    for ordinal,row in a.rows(inputs['catalogue']['path'],{'chrom','end'}):
        need(ordinal<request['limits']['catalogue_rows'],'catalogue row limit exceeded')
        actual.add(row['chrom'])
        need(row['chrom'] in sequence_sizes and a.integer(row['end'],'catalogue end')<=sequence_sizes[row['chrom']], 'catalogue contig/bounds absent from sequence')
    for ordinal,row in a.rows(inputs['reference']['path'],{'chrom','position'}):
        need(ordinal<request['limits']['reference_rows'],'reference row limit exceeded')
        actual.add(row['chrom'])
        pos=a.integer(row['position'],'reference position')-int(definition['coordinate_base']=='1based')
        need(row['chrom'] in sequence_sizes and 0<=pos<sequence_sizes[row['chrom']], 'reference contig/bounds absent from sequence')
    for chrom,entry in coverage['contigs'].items():
        actual.add(chrom)
        need(chrom in sequence_sizes,'coverage contig absent from sequence')
        for interval in entry.get('exclusions',[]):
            end=interval['end']
            need(type(end) is int and end<=sequence_sizes[chrom],'coverage exclusion outside sequence')
    require_actual_contigs(request,actual)
    req=dict(request)
    cached={r:Path(inputs[r]['path']).read_bytes() for r in ('reference_contract','coverage','sequence_contract')}
    observations={'inputs':{r:a.file_state(v['path']) for r,v in inputs.items()}}
    a.run(req,out,observations,cached)
    # Scientific output metadata retains explicit source scope, not the original atlas's label.
    summary=read_json(out/'SUMMARY.json'); summary['claims']='absence from declared assessed atlas subset only; no biological absence/discovery/neuron-specific/validation claim'; summary['status']='COMPLETED_'+request['mode'].upper()
    write_json(out/'SUMMARY.json',summary)
    # Complete order is the accepted follow-up order: detected cells descending, literal peak UTF-8 ascending.
    with (out/'candidate_universe.jsonl').open() as handle:
        universe=[json.loads(line) for line in handle]
    universe_source_order=list(universe)
    universe.sort(key=lambda r:(-r['support']['n_cells'],r['peak_name'].encode('utf-8')))
    columns=['rank','peak_name','gene_id','chrom','strand','start0','end0','locus','n_cells','assigned_umis','reference_assessment','motif','sequence_arich','native_misprimed_ratio']
    with (out/'candidates.tsv').open('w') as handle:
        writer=csv.writer(handle,delimiter='\t',lineterminator='\n'); writer.writerow(columns)
        for rank,r in enumerate(universe,1):
            c=r['coordinates']; lo,hi=c['interval0']; locus=f"{c['chrom']}:{lo+1}-{hi}"
            values=[rank,r['peak_name'],r['gene_annotation']['gene_id'],c['chrom'],c['strand'],lo,hi,locus,r['support']['n_cells'],r['support']['assigned_umis'],r['reference']['assessment'],r['sequence']['context']['motif']['status'],r['sequence']['context']['arich']['status'],r['native_evidence']['misprimed_ratio']['value']]
            writer.writerow(['NA' if x is None else x for x in values])
    # Exact historical follow-up selection: two joint-QC strata, three literal distinct genes
    # and three missing-gene examples per stratum. No extra risk/quality threshold.
    strata={'primary': [r for r in universe if (r['sequence']['context']['motif']['status'],r['sequence']['context']['arich']['status'])==('positive','negative')],
            'risk': [r for r in universe if (r['sequence']['context']['motif']['status'],r['sequence']['context']['arich']['status'])==('negative','positive')]}
    selected=[]; selection_audit=[]
    for name,records in strata.items():
        seen=set(); null_count=0
        with (out/(name+'-all-sorted.jsonl')).open('w') as handle:
            for record in records: a.emit(handle,record)
        for rank,row in enumerate(records,1):
            gid=row['gene_annotation']['gene_id']
            if gid is None or gid=='':
                null_count+=1; keep=null_count<=3
                decision='selected_null_gene' if keep else 'null_gene_after_top3'; kind='null_gene'
            elif gid in seen:
                keep=False; decision='duplicate_previously_selected_gene'; kind='known_gene'
            elif len(seen)<3:
                seen.add(gid);keep=True;decision='selected_distinct_gene';kind='known_gene'
            else:
                keep=False;decision='distinct_gene_after_top3';kind='known_gene'
            selection_audit.append(dict(stratum=name,stratum_rank=rank,peak_name=row['peak_name'],gene_id=gid,n_cells=row['support']['n_cells'],selected=keep,decision=decision))
            if keep: selected.append(dict(stratum=name,selection_kind=kind,stratum_rank=rank,source=row))
    covered={r['peak_name'] for r in selection_audit}
    selection_audit.extend(dict(stratum='other_joint_QC',stratum_rank=None,peak_name=r['peak_name'],gene_id=r['gene_annotation']['gene_id'],n_cells=r['support']['n_cells'],selected=False,decision='outside_two_frozen_example_strata') for r in universe_source_order if r['peak_name'] not in covered)
    write_json(out/'shortlist.json',{'selection':'historical exploratory primary/risk strata; up to 3 distinct literal genes + 3 null gene rows per stratum', 'sort':'n_cells descending; peak_name UTF-8 ascending','stratum_counts':{k:len(v) for k,v in strata.items()},'rows':selected})
    with (out/'selection-audit.jsonl').open('w') as handle:
        for record in selection_audit:a.emit(handle,record)
    return summary

def output_index(value,out,summary):
    tables=[]; artifacts=[]
    for path in sorted((out/'data').rglob('*')):
        if not path.is_file(): continue
        need(not path.is_symlink(),'symlink output forbidden')
        item={k:v for k,v in pin(path).items() if k!='stat'}; item['path']=str(path.relative_to(out)); item['name']=path.name
        if path.suffix in {'.tsv','.jsonl','.bed'}:
            with path.open() as handle:
                first=handle.readline(); n=sum(1 for _ in handle)+(1 if first else 0)
            fmt='jsonl' if path.suffix=='.jsonl' else 'tsv' if path.suffix=='.tsv' else 'bed'
            item.update(format=fmt,columns=first.rstrip('\n').split('\t') if fmt=='tsv' else [],row_count=max(0,n-(fmt=='tsv')))
            tables.append(item)
        else: artifacts.append(item)
    return dict(schema=SCHEMA+'.result',method=value['method'],status='succeeded',summary=summary,tables=tables,artifacts=artifacts,method_sources=value['method_sources'],input_sources=value['input_sources'],parameters=value['parameters'],scope=value['scope'],limitations=value['limitations'])

def execute(value,output_dir):
    out=Path(output_dir)
    need(out.is_absolute() and not out.exists() and out.parent.is_dir(),'fresh absolute output directory with existing task parent required')
    out.mkdir()
    write_json(out/'status.json',{'status':'running','phase':'validate','time':now()})
    try:
        write_json(out/'PLAN.json',value)
        verify_plan(value)
        for p in value['input_sources']:
            need(not Path(p['path']).resolve().is_relative_to(out.resolve()),'input/output overlap')
        request=value['request']
        if request['method']=='cs3.candidates':
            scratch=os.environ.get('TMPDIR')
            need(scratch and Path(scratch).is_absolute() and Path(scratch).is_dir() and os.environ.get('SQLITE_TMPDIR')==scratch,'launch interpreter with TMPDIR=SQLITE_TMPDIR=private scratch')
            need(Path(tempfile.gettempdir()).resolve()==Path(scratch).resolve(),'scratch was cached before worker startup')
            need(not Path(scratch).resolve().is_relative_to(out.resolve()),'scratch must be sibling of analysis output')
        write_json(out/'status.json',{'status':'running','phase':request['method'],'time':now()})
        summary={'cs3.transform':transform,'cs3.project':project,'cs3.candidates':candidates}[request['method']](request,out/'data')
        verify_plan(value)
        index=output_index(value,out,summary)
        write_json(out/'index.json',index)
        write_json(out/'status.json',{'status':'succeeded','time':now()})
        return index
    except Exception as error:
        record={'status':'failed','time':now(),'error_code':type(error).__name__,'error':str(error),'input_observations':value.get('input_sources',[]),'partial_outputs':'retained; never valid as a successful result'}
        write_json(out/'REFUSAL.json',record); write_json(out/'status.json',record)
        raise

def status(output_dir):
    return read_json(Path(output_dir)/'status.json')

def result(output_dir):
    out=Path(output_dir)
    need(status(out)['status']=='succeeded','task did not succeed')
    value=read_json(out/'index.json',2097152)
    for p in value['tables']+value['artifacts']:
        path=out/p['path']
        need(not path.is_symlink() and path.resolve().is_relative_to(out.resolve()),'result path escapes output')
        need(sha(path)==p['sha256'] and path.stat().st_size==p['bytes'],'result artifact changed')
    return value

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['capabilities','plan','execute','status','result']); parser.add_argument('--request'); parser.add_argument('--plan'); parser.add_argument('--output'); parser.add_argument('--write')
    args=parser.parse_args()
    value=capabilities() if args.action=='capabilities' else plan(read_json(args.request)) if args.action=='plan' else execute(read_json(args.plan),args.output) if args.action=='execute' else status(args.output) if args.action=='status' else result(args.output)
    if args.write: write_json(args.write,value)
    else: print(json.dumps(value,ensure_ascii=False,allow_nan=False))
