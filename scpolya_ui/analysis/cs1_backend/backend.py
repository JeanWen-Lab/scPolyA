"""Versioned full envelope around the accepted prepare -> plan/run/get chain."""
from __future__ import annotations

import csv
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from typing import Any
import zipfile

METHOD = 'cs1.fixed_coverage_full'
RESOURCES = dict(cpu_cores=2, memory_bytes=16*2**30, swap_bytes=0, wall_seconds=7200, disk_bytes=20*2**30)
LIMITATIONS = [
    'Descriptive fixed-cohort coverage only; zero terminal counts mean missing APA evidence.',
    'The two groups and selected gene-strand units are supplied explicitly; no barcode registry is inferred from counts.',
    'Native distal usage is consumed as produced, without recalculation or biological inference.',
    'Current retained bytes are captured now; invocation/config binding does not certify an execution-time snapshot.',
    'Successful computation and independent scientific review are separate states.',
]


def capabilities() -> list[dict]:
    """Metadata only: no method imports, reads or writes."""
    return [dict(id=METHOD, label='Fixed-cohort coverage and native report',
                 description='All supplied cells and gene-strand units; complete coverage tables and retained native distal source.',
                 parameters=[dict(name='sample_id', type='string', required=True),
                             dict(name='cohort', type='file', required=True, description='Canonical cell-index TSV: cell_index, barcode, major_class, cell_type.'),
                             dict(name='registry', type='file', required=True, description='Complete sample barcode registry, one barcode per line, no header.'),
                             dict(name='selected_units', type='file', required=True, description='Explicit gene_id and strand TSV; no data-derived default.'),
                             dict(name='selection_receipt', type='file', required=False, description='Optional pinned cohort/selected-unit provenance receipt.'),
                             dict(name='groups', type='string_array', required=True, min_items=2, max_items=2),
                             dict(name='maximum_count', type='integer', value=10_000_000, read_only=True)],
                 input_schema=dict(required=['method','runs','sample_id','cohort','registry','selected_units','groups']),
                 resource_defaults=dict(RESOURCES), limitations=list(LIMITATIONS))]


# The vendored, hash-pinned coverage adapter is validated only on this schema.
COVERAGE_ANNOTATION_SCHEMA = 'terminal-annotation-v3'


def _native():
    source = Path(__file__).with_name('vendor_full_v2')/'native_coverage.py'
    spec = importlib.util.spec_from_file_location('_gui_native_full_v2', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _need(condition, detail):
    if not condition:
        raise ValueError(detail)


def _dump(path: Path, value: Any):
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(raw, encoding='utf-8')
    temp.replace(path)


def _pin(path: Path, role: str, maximum: int) -> dict:
    _need(path.is_absolute() and path.is_file() and not path.is_symlink(), 'Explicit regular source file required: '+str(path))
    before = path.stat()
    _need(before.st_size <= maximum, 'Source budget exceeded: '+role)
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            h.update(block)
    after = path.stat()
    frame = lambda st: [st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
    _need(frame(before) == frame(after), 'Source changed while hashing: '+role)
    return dict(role=role, path=str(path), bytes=before.st_size, sha256=h.hexdigest(), stat=frame(after))


def _verify(items):
    for item in items:
        _need(_pin(Path(item['path']), item['role'], item['bytes']) == item, 'Source changed after preview: '+item['role'])


def _units(path: Path, maximum=1024):
    _need(path.stat().st_size <= 256*2**10, 'Selected-units file exceeds 256KiB')
    with path.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle, delimiter='\t', quoting=csv.QUOTE_NONE)
        _need(reader.fieldnames == ['gene_id','strand'], 'Selected-units header must be gene_id, strand')
        rows = []
        for row in reader:
            _need(set(row) == {'gene_id','strand'} and None not in row.values(), 'Ragged selected-units TSV')
            rows.append(row)
            _need(len(rows) <= maximum, 'Selected-units bound exceeded')
    _need(rows, 'Explicit nonempty selected-units file required')
    return rows


def plan(request: dict) -> dict:
    from scpolya_ui.services import run_evidence_service as evidence
    _need(request.get('method') == METHOD, 'Unsupported coverage method')
    required={'method','runs','sample_id','cohort','registry','selected_units','groups'}
    _need(required <= set(request) <= required|{'selection_receipt'}, 'Exact coverage request fields required')
    _need(isinstance(request['runs'], list) and len(request['runs']) == 1, 'Select one successful native execution')
    binding = request['runs'][0]
    evidence.verify_binding(binding)
    native = _native()
    root = Path(binding['view_root'])
    _need(root == Path(binding['config']['output_dir'])/binding['config']['run_id'], 'Coverage requires a directly bound native root; relocated roots need an explicit reviewed adapter')
    # Exact native paths derive from the bound namespace, never caller-supplied expected answers.
    from task_manage import project_run_outputs_v1
    projection = project_run_outputs_v1(binding['config'], annotation_schema='recorded')
    _need(not projection.warnings, 'Native projection has warnings: '+'; '.join(projection.warnings))
    summary_scope = projection.by_scope_id['matrix_summary']
    schema = next(name.payload.get('terminal_annotation_schema')
                  for name in summary_scope.artifact_names if name.scope == 'annotation')
    _need(schema == COVERAGE_ANNOTATION_SCHEMA,
          f'Not supported (unsupported_schema): CS1 fixed coverage is validated only for '
          f'{COVERAGE_ANNOTATION_SCHEMA} runs, and this run records {schema}. '
          'Its tables stay readable elsewhere; this analysis was not run.')
    directory = Path(summary_scope.path)
    parts = directory.relative_to(root).parts
    _need(len(parts) == 4 and parts[0] == 'matrix_summary', 'Unexpected native namespace')
    ns = dict(peak=parts[1], annotation=parts[2], matrix=parts[3])
    sid = request['sample_id'];base = sid+'.'+binding['config']['dedup_method']+'.annotated'
    paths = dict(counts=str(root/'count_matrix'/ns['peak']/ns['annotation']/(base+'.counts.terminal-aware.v2.tsv.gz')),
                 catalogue=str(directory/(ns['annotation']+'.terminal_aware_peak_to_gene.v2.tsv.gz')),
                 config=binding['invocation']['path'], manifest=str(root/'state/artifact_name_manifest.v1.json'),
                 inputs=str(root/'input_tables/inputs.csv'), cohort=request['cohort'], registry=request['registry'])
    units = _units(Path(request['selected_units']))
    # This validates metadata and pins only. Full table parsing/aggregation starts after confirmation.
    _need(Path(paths['inputs']).stat().st_size <= native.CAPS['metadata_bytes_each'], 'Native input CSV exceeds metadata budget')
    with Path(paths['inputs']).open(encoding='utf-8',newline='') as handle:
        inputs = list(csv.DictReader(handle))
    _need(len(inputs) == 1 and inputs[0]['sample_id'] == sid, 'Exact single native sample required')
    prepared_request = native.make_request(paths, run_root=str(root), sample_id=sid, namespace=ns, artifact_base=base,
        invocation_inputs_path=binding['config']['inputs'], sample_source_path=inputs[0]['source_path'],
        groups=request['groups'], selected_units=units, attempt_dir='/__scpolya_gui_planned_output__/prepared')
    metadata_limits=native.validate_request(prepared_request, physical=False)
    metadata_session=native.Sources(prepared_request,metadata_limits)
    native.bind_native(prepared_request,metadata_session)
    _,cohort_cells,registry_cells,_=native.read_cohort_registry(prepared_request,metadata_session,metadata_limits)
    input_sources = [_pin(Path(p), role, native.CAPS['source_bytes_each'] if role in ('counts','catalogue') else native.CAPS['metadata_bytes_each']) for role,p in paths.items()]
    input_sources.append(_pin(Path(request['selected_units']), 'selected_units', native.CAPS['request_bytes']))
    selection_provenance=dict(mode='caller_supplied_fixed_files',selection_rule='Declared by caller; not recomputed here')
    if request.get('selection_receipt'):
        receipt_pin=_pin(Path(request['selection_receipt']),'selection_receipt',native.CAPS['metadata_bytes_each'])
        receipt=json.loads(Path(receipt_pin['path']).read_text())
        _need(receipt.get('schema')=='scpolya.gui.cs1.selection_preparation.v1','Unsupported selection provenance schema')
        input_sources.append(receipt_pin)
        _need(receipt['selected_units']==len(units) and receipt['selected_cells']==len(cohort_cells),'Selection dimensions differ from supplied fixed files')
        label=receipt['label_source'];label_pin=_pin(Path(label['path']),'label_source',native.CAPS['metadata_bytes_each'])
        _need(all(label_pin[key]==label[key] for key in ('path','bytes','sha256')),'Label source differs from selection receipt')
        input_sources.append(label_pin)
        # Bind supplied fixed files to the receipt without loading any old result values.
        described={item['path']:item for item in receipt['files']}
        for role in ('cohort','registry','selected_units'):
            pin=next(item for item in input_sources if item['role']==role)
            _need(pin['path'] in described and all(pin[k]==described[pin['path']][k] for k in ('bytes','sha256')),'Selection file differs from provenance receipt: '+role)
        selection_provenance=dict(mode='pinned_selection_receipt',selection_rule=receipt['selection_rule'],
            selected_units=receipt['selected_units'],structural_eligible_units=receipt['structural_eligible_units'],
            excluded_unit_ids=receipt.get('excluded_unit_ids',[]),label_source=label,receipt=receipt_pin,
            observed='Receipt, fixed files and label source byte pins verified',
            declared='Prior shared-support selection and label/source origin; selection is not recomputed by the coverage method',
            limitation='Shared-support is conditioned on positive pooled counts in both groups; not an unbiased structural-only candidate universe.')

    distal = directory/(base+'.terminal_aware_distal_site_usage.v1.tsv.gz')
    if distal.is_file():
        input_sources.append(_pin(distal, 'native_distal', native.CAPS['source_bytes_each']))
    method_sources = [_pin(p, 'method:'+p.name, 2**20) for p in sorted(Path(__file__).parent.rglob('*')) if p.suffix in ('.py','.json')]
    evidence.verify_binding(binding)
    return dict(schema='scpolya.ui.cs1.full_plan.v2', method=METHOD, status='ready', request=request,
                parameters=dict(groups=request['groups'],selected_unit_count=len(units),fixed_registry=True,native_distal_recomputed=False,selection_provenance=selection_provenance),
                native_request=prepared_request, input_sources=input_sources, method_sources=method_sources,
                scope=dict(selected_units=len(units),cohort_cells=len(cohort_cells),group_counts=dict(Counter(row['major_class'] for row in cohort_cells)),registry_cells=len(registry_cells),cohort_authority=request['cohort'],registry_authority=request['registry'],counts_rows='read_after_confirmation',
                           source_barcode_count='not_inferred',terminal_observation_barcode_count='not_inferred'),
                resources=dict(RESOURCES), limitations=list(LIMITATIONS))


def execute(preview: dict, output_dir: str | Path) -> dict:
    from scpolya_ui.services import run_evidence_service as evidence
    out = Path(output_dir)
    _need(out.is_absolute() and not out.exists(), 'Fresh nonexistent analysis directory required')
    out.mkdir()
    _dump(out/'status.json',dict(status='running',phase='Verifying full coverage inputs'))
    try:
        _need(preview['method'] == METHOD and preview['schema'] == 'scpolya.ui.cs1.full_plan.v2', 'Unsupported plan')
        _verify(preview['method_sources']);_verify(preview['input_sources'])
        binding = preview['request']['runs'][0];evidence.verify_binding(binding)
        native = _native();c = native.c
        request = json.loads(json.dumps(preview['native_request']))
        request['attempt_dir'] = str(out/'prepared')
        _dump(out/'preview.json',preview)
        _dump(out/'status.json',dict(status='running',phase='Preparing all fixed native counts'))
        prepared = native.prepare(request)
        bridge = native.coverage_request_from_prepared(prepared['prepared_ref'],str(out/'coverage'))
        _dump(out/'handoff.json',bridge)
        coverage_plan = c.plan_coverage(bridge['coverage_request']);_dump(out/'coverage-plan.json',coverage_plan)
        plan_ref = dict(path=str(out/'coverage-plan.json'),sha256=c.sha((out/'coverage-plan.json').read_bytes()))
        _dump(out/'status.json',dict(status='running',phase='Computing fixed-cohort coverage'))
        completed = c.run_coverage(plan_ref)
        checked = c.get_coverage(plan_ref,completed['analysis_ref'])
        _dump(out/'coverage-get.json',checked)
        _dump(out/'native-handoff.json',native.link_coverage(prepared['prepared_ref'],plan_ref,completed['analysis_ref']))
        tables = []
        for filename, columns in [('gene-group.tsv',c.GG_FIELDS),('per-cell.tsv',c.CELL_FIELDS)]:
            path = out/'coverage'/filename
            pin = _pin(path,filename,128*2**20)
            count = len(coverage_plan['group_counts']) * coverage_plan['n_genes'] if filename=='gene-group.tsv' else sum(coverage_plan['group_counts'].values())
            tables.append(dict(name=filename[:-4],path=str(path.relative_to(out)),format='tsv',columns=columns,row_count=count,bytes=pin['bytes'],sha256=pin['sha256']))
        distal = next((i for i in preview['input_sources'] if i['role']=='native_distal'),None)
        if distal:
            shutil.copyfile(distal['path'],out/'native-distal.tsv.gz')
            copy_pin = _pin(out/'native-distal.tsv.gz','native_distal',distal['bytes'])
            _need(copy_pin['sha256']==distal['sha256'], 'Native distal changed during snapshot')
        _verify(preview['input_sources']);_verify(preview['method_sources']);evidence.verify_binding(binding)
        inventory = [dict(table=key, inclusion='included' if any(Path(i['path']).name.endswith(suffix) for i in preview['input_sources']) else 'not_included',
                          note='Bound source included' if key in ('counts','catalogue','distal') else 'Not part of this coverage report; use native table reader')
                     for key,(suffix,_) in evidence.TABLES.items()]
        _dump(out/'source-inventory.json',inventory)
        index = dict(schema='scpolya.ui.cs1.full_result.v2',method=METHOD,status='succeeded',summary=completed['summary'],
                     tables=tables,artifacts=[],input_sources=preview['input_sources'],method_sources=preview['method_sources'],
                     parameters=preview['parameters'],accounting=prepared['accounting'],limitations=preview['limitations'],
                     source_inventory=inventory,native_distal_status='source_snapshot' if distal else 'missing',review_state='not_reviewed')
        if distal:
            index['artifacts'].append(dict(name='native_distal',path='native-distal.tsv.gz',bytes=copy_pin['bytes'],sha256=copy_pin['sha256']))
        (out/'REPORT.txt').write_text('Fixed-cohort coverage and native report\n\n'+json.dumps(index['summary'],indent=2)+'\n\n'+'\n'.join(index['limitations'])+'\n',encoding='utf-8')
        _dump(out/'index.json',index)
        with zipfile.ZipFile(out/'report.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(out.rglob('*')):
                if path.is_file() and path.name not in ('report.zip','status.json'):
                    _need(not path.is_symlink(), 'Output symlink refused')
                    archive.write(path,str(path.relative_to(out)))
        files = {str(p.relative_to(out)):_pin(p,p.name,512*2**20)['sha256'] for p in out.rglob('*') if p.is_file() and p.name != 'status.json'}
        _dump(out/'files.json',files)
        _dump(out/'status.json',dict(status='succeeded',phase='Complete'))
        return result(out)
    except BaseException as exc:
        from scpolya_ui.registry import utc_now
        _dump(out/'REFUSAL.json',dict(status='failed',time=utc_now(),type=type(exc).__name__,reason=str(exc),partial_outputs='Retained; no automatic retry'))
        _dump(out/'status.json',dict(status='failed',error=str(exc)))
        raise


def status(output_dir: str | Path) -> dict:
    path = Path(output_dir)/'status.json'
    return json.loads(path.read_text()) if path.is_file() else dict(status='ready')


def result(output_dir: str | Path) -> dict:
    out = Path(output_dir)
    _need(status(out)['status']=='succeeded', 'No complete result')
    seals = json.loads((out/'files.json').read_text())
    path = out/'index.json'
    _need(path.stat().st_size <= 2*2**20, 'Result index exceeds display budget')
    _need(hashlib.sha256(path.read_bytes()).hexdigest()==seals['index.json'], 'Result index changed')
    return json.loads(path.read_text())
