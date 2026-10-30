"""Build backend inputs from selected executions and recorded provenance."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path

from scpolya_ui.services import run_evidence_service as evidence


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode()


def file_pin(value, maximum=16*2**30):
    original=Path(value).expanduser()
    if not original.is_absolute():raise ValueError('Choose an absolute source path')
    path=original.resolve(strict=True)
    if not path.is_file() or path.stat().st_size>maximum:raise ValueError('Source file kind/size exceeded')
    before=path.stat();h=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(2**20),b''):h.update(block)
    after=path.stat()
    frame=lambda st:[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
    if frame(before)!=frame(after):raise ValueError('Source changed while hashing')
    return dict(path=str(path),bytes=before.st_size,sha256=h.hexdigest())


def recorded_runtime(package: str, binding: dict):
    """Observed job/runtime records, with their original timing and limitations."""
    base=Path(package).resolve(strict=True)
    manifest=base/'SHA256SUMS'
    if manifest.stat().st_size>4*2**20:raise ValueError('Provenance manifest too large')
    entries={}
    for line in manifest.read_text().splitlines():
        digest,name=line.split('  ',1)
        if len(digest)!=64:raise ValueError('Invalid provenance manifest')
        entries[name]=digest
    used=[]
    def document(name):
        path=base/name
        if not path.resolve().is_relative_to(base) or name not in entries:raise ValueError('Unmanifested provenance record')
        pin=file_pin(str(path),4*2**20)
        if pin['sha256']!=entries[name]:raise ValueError('Provenance record changed: '+name)
        used.append(pin)
        return json.loads(path.read_text())
    identities=document('evidence/RUN-IDENTITIES.json')
    session=document('evidence/SESSION.json');environment=document('evidence/ENVIRONMENT.json')
    matches=[item for item in identities['runs'] if item.get('qualified_run_root')==binding['view_root'] and isinstance(item.get('config'),dict) and all(k in binding['config'] and binding['config'][k]==v for k,v in item['config'].items())]
    if len(matches)!=1:raise ValueError('Provenance package does not bind the selected run/config')
    job=matches[0]['job']
    # Folder names are not trusted as case IDs: select the submitted spec by its job.
    specs=[]
    for name in entries:
        if name.startswith('evidence/') and name.endswith('/GUI-JOB-SPEC.submitted.json'):
            spec=document(name)
            if spec.get('job_id')==job:specs.append(spec)
    if len(specs)!=1:raise ValueError('Unique submitted job specification required')
    spec=specs[0];metadata=json.loads(Path(binding['invocation']['path']).read_text())
    if spec['run_root']!=binding['view_root'] or spec['argv'][1:]!=metadata['argv']:raise ValueError('Submitted argv/root does not match selected native invocation')
    if environment['python_executable']!=spec['argv'][0] or spec['cwd']!=session['ui_worktree']:raise ValueError('Recorded Python/source directory mismatch')
    commits=[environment['native_and_ui_git_commit'],session['ui_commit'],identities['ui_native_commit']]
    if len(set(commits))!=1:raise ValueError('Recorded producer commits disagree')
    descriptor=dict(identity_kind='recorded_runtime_descriptor_v1',evidence_level='submitted_job_spec_and_later_environment',
                    python_executable=spec['argv'][0],source_checkout=spec['cwd'],recorded_commit=commits[0],
                    environment=environment,session_pin=next(p for p in used if Path(p['path']).name=='SESSION.json'),
                    limits=['Environment capture time/scope are retained; this is not a start-time byte seal.',
                            'Original executable, libraries and scientific validity are not independently authenticated.'])
    # Per-job pins remain separate, so the two arms compare the common evidenced level.
    descriptor_sha=hashlib.sha256(canonical(descriptor)).hexdigest()
    return dict(descriptor=descriptor,runtime_sha256=descriptor_sha,job_id=job,
                evidence_pins=[file_pin(str(manifest),4*2**20),*used],recorded_config_comparison=dict(recorded_subset_matches_native=True,native_only_fields=sorted(set(binding['config'])-set(matches[0]['config']))),
                retained_entry_point=file_pin(spec['argv'][1],2**20))


def cs1_request(record, mappings, fields):
    _,binding=evidence.discover(record,mappings)
    request=dict(method='cs1.fixed_coverage_full',runs=[binding],sample_id=fields['sample_id'],
                cohort=fields['cohort'],registry=fields['registry'],selected_units=fields['selected_units'],
                groups=[fields['group_a'],fields['group_b']])
    if fields.get('selection_receipt'):request['selection_receipt']=fields['selection_receipt']
    return request


def cs2_request(records, mappings, fields, task_root: Path):
    from scpolya_ui.analysis.cs2_backend import api
    if len(records)!=2 or records[0].id==records[1].id:raise ValueError('Choose two distinct executions')
    registry=file_pin(fields['registry'],8*2**20)
    references={name:file_pin(fields[name]) for name in ['fasta','fai','atlas','genome_receipt','atlas_receipt']}
    references['method']=file_pin(str(Path(api.__file__).parent/'method/method.frozen.json'))
    arms={};cache={}
    def cached(path):
        key=str(Path(path).resolve(strict=True))
        if key not in cache:cache[key]=file_pin(key)
        return cache[key]
    for role,record in zip(api.ARMS,records):
        sources,binding=evidence.discover(record,mappings)
        provenance=recorded_runtime(fields['provenance_package'],binding)
        cfg=binding['config'];root=Path(binding['view_root'])
        table_map={}
        for key,destination in [('catalogue','peak_to_gene'),('counts','counts'),('psi','psi'),('gene_cell','gene_cell')]:
            matches=[s for s in sources if s.key==key]
            if len(matches)!=1:raise ValueError('Select a run with one complete native sample: '+key)
            table_map[destination]=cached(matches[0].path)
        inputs=root/'input_tables/inputs.csv'
        if inputs.stat().st_size>2**20:raise ValueError('Input CSV metadata too large')
        with inputs.open(newline='') as handle:rows=list(csv.DictReader(handle))
        if len(rows)!=1:raise ValueError('One native sample required')
        bam=cached(rows[0]['source_path']);gtf=cached(cfg['gene_gtf']);chrom=cached(cfg['chrom_sizes'])
        identity=dict(assembly='GRCh38',bam_sha256=bam['sha256'],gtf_sha256=gtf['sha256'],chrom_sizes_sha256=chrom['sha256'],
                      registry_sha256=registry['sha256'],runtime_sha256=provenance['runtime_sha256'])
        receipt=dict(schema='scpolya.ui.analysis.source_binding.v1',run_binding=binding,recorded_runtime=provenance,
                     observed_inputs=dict(bam=bam,gtf=gtf,chrom_sizes=chrom,registry=registry),
                     identity_kind='observed_source_bytes_and_recorded_runtime_descriptor',
                     limits=['Assembly remains the selected GRCh38 QC method contract, not independent alignment certification.'])
        path=task_root/(role+'-source-binding.json');evidence.emit(path,receipt)
        arms[role]=dict(run=dict(record_id=record.id,invocation_id=binding['invocation_id'],root=binding['view_root'],config=cfg,metadata_pin=binding['invocation']),
                        label=record.label,config=cfg,identity=identity,files=table_map,source_receipt=file_pin(str(path)))
    return dict(method='cs2.qc_compare',schema=api.SCHEMA,arms=arms,registry=registry,references=references,
                contigs=[x.strip() for x in fields['contigs'].split(',') if x.strip()],compared_key=fields['compared_key'])


def historical_table(record, mappings, key):
    from scpolya_ui.registry import apply_path_mappings_to_value
    root=Path(apply_path_mappings_to_value(record.root_path,mappings))
    if not root.is_absolute() or not root.is_dir():raise ValueError('Selected historical run root is unavailable')
    suffix=evidence.TABLES[key][0]
    pattern='count_matrix/*/*/*'+suffix if key=='counts' else 'matrix_summary/*/*/*/*'+suffix
    matches=list(root.glob(pattern))
    if len(matches)!=1:raise ValueError('Select a historical run with one unambiguous '+key+' table')
    # Resolve copied-view links explicitly; historical mode asserts file association,
    # never a newly certified execution snapshot.
    return file_pin(str(matches[0])),dict(record_id=record.id,selected_root=str(root),lexical_path=str(matches[0]),
                                        canonical_path=str(matches[0].resolve()),classification='historical_export')


def cs3_request(method, records, mappings, fields, task_root: Path):
    from scpolya_ui.analysis.cs3_backend import api
    native=dict(classification='historical_export',source_provenance=fields.get('source_provenance','Declared reference inputs'))
    request=dict(schema=api.SCHEMA,method=method,mode='historical_export',scope=fields['source_scope'],native=native)
    if method=='cs3.transform':
        atlas={key:fields[key] for key in ['name','release','source_assembly','target_assembly','source_coordinate_base','coordinate_status','reference_population','source_provenance']}
        atlas.update(strand_semantics='transcribed_RNA_strand',mitochondrial_contig='chrM',mitochondrial_policy='ucsc_chrM_conservative_v1')
        request.update(assembly=atlas['target_assembly'],atlas=atlas,
                       inputs={role:file_pin(fields[role]) for role in api.TRANSFORM_ROLES},
                       format=fields['source_format'],accepted_target_contigs=[s.strip() for s in fields['contigs'].split(',') if s.strip()],
                       chain_direction=dict(source_side='t',target_side='q'),liftover_version=fields['liftover_version'],limits=dict(source_rows=1_000_000))
        return request
    if len(records)!=1:raise ValueError('Choose one catalogue source execution')
    record=records[0]
    if method=='cs3.project':
        prior=Path(fields['transform_result']).resolve(strict=True);index=api.result(prior)
        if index['method']!='cs3.transform':raise ValueError('Choose a successful atlas transformation result')
        previous=json.loads((prior/'PLAN.json').read_text())['request']
        catalogue,association=historical_table(record,mappings,'catalogue')
        inputs={role:previous['inputs'][role] for role in ['chain','source_sizes','target_sizes']}
        for role,name in [('status','final-status.tsv'),('source_map','source-map.tsv'),('audit_mapped','audit.mapped.bed'),
                          ('transform_manifest','RUN-MANIFEST.json'),('transform_contract','TRANSFORM-CONTRACT.json')]:
            inputs[role]=file_pin(str(prior/'data'/name))
        inputs.update({role:file_pin(fields[role]) for role in ['source_coverage','sequence_contract','fasta','fai','sequence_sizes']})
        inputs['catalogue']=catalogue
        request.update(assembly=previous['assembly'],atlas=previous['atlas'],inputs=inputs,source_build=previous['atlas']['source_assembly'],
                       target_build=previous['assembly'],normalized_source_coordinate_base='1based',
                       assessed_target_contigs=[s.strip() for s in fields['contigs'].split(',') if s.strip()],
                       transform_receipt=file_pin(str(prior/'index.json')),
                       limits=dict(source_rows=1_000_000,audit_rows=2_000_000,chain_blocks=5_000_000,catalogue_rows=250_000))
        evidence.emit(task_root/'historical-catalogue-binding.json',association)
        return request
    prior=Path(fields['projection_result']).resolve(strict=True);index=api.result(prior)
    if index['method']!='cs3.project':raise ValueError('Choose a successful reference projection')
    previous=json.loads((prior/'PLAN.json').read_text())['request']
    mode=fields['source_mode']
    if mode=='native_snapshot':
        tables,binding=evidence.discover(record,mappings)
        selected={}
        for role in ['catalogue','counts']:
            matching=[table for table in tables if table.key==role]
            if len(matching)!=1:raise ValueError('Ambiguous native table: '+role)
            selected[role]=file_pin(matching[0].path)
        request.update(mode=mode,native=binding)
    elif mode in ('historical_export','synthetic'):
        selected={};associations=[]
        for role in ['catalogue','counts']:
            selected[role],association=historical_table(record,mappings,role);associations.append(association)
        evidence.emit(task_root/'historical-source-binding.json',dict(mode=mode,observations=associations,limits='Selected retained-file association only; no execution-time byte seal'))
        request.update(mode=mode,native=dict(classification=mode,source_provenance=fields['source_provenance']))
    else:raise ValueError('Declare the source mode explicitly')
    inputs={role:previous['inputs'][role] for role in ['fasta','fai','target_sizes','sequence_sizes','sequence_contract']}
    for role,name in [('reference','reference.tsv'),('reference_contract','reference-contract.json'),('coverage','coverage.json')]:
        inputs[role]=file_pin(str(prior/'data'/name))
    inputs.update(selected);inputs['projection_receipt']=file_pin(str(prior/'index.json'))
    if fields.get('labels'):inputs['labels']=file_pin(fields['labels'])
    request.update(assembly=previous['assembly'],atlas=previous['atlas'],inputs=inputs,pair_semantics=fields['pair_semantics'],
                   limits=dict(catalogue_rows=250_000,count_rows=50_000_000,reference_rows=1_000_000,label_rows=100_000))
    return request
