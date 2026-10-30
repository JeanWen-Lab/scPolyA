"""JSON plan/execute/status/result. GUI schedules execute in its background task pool.

This module deliberately imports no SQLite or scientific modules. The isolated
worker starts with scratch environment set before any SQLite-loading import.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SCHEMA = 'scpolya.cs2.saved_analysis.v1'
ARMS = ('coverage3', 'coverage1')  # Frozen output role names; labels live in request.
MODES = ('unmasked', 'a_risk_false', 'a_risk_false_or_unknown')
PRIMARY25 = [f'chr{x}' for x in [*range(1, 23), 'X', 'Y', 'M']]
EXECUTION_KEYS = {'output_dir', 'outdir', 'workdir', 'threads', 'n_jobs', 'log_level', 'tmpdir'}
BOUND_MAX = {'catalogue_rows': 250000, 'raw_rows_per_arm': 25000000,
             'observations_per_arm_mode_definition': 2500000, 'overlap_edges': 2000000}
RESOURCE_MAX = {'cpu_cores': 2, 'memory_bytes': 16 * 1024**3, 'wall_seconds': 7200,
                'disk_bytes': 20 * 1024**3, 'swap_bytes': 0}
IDENTITY_KEYS = {'assembly', 'bam_sha256', 'gtf_sha256', 'chrom_sizes_sha256',
                 'registry_sha256', 'runtime_sha256'}
REFERENCE_ROLES = {'method', 'fasta', 'fai', 'atlas', 'genome_receipt', 'atlas_receipt'}


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':')).encode()


def regular(path, directory=False):
    p = Path(path)
    need(p.is_absolute() and '..' not in p.parts, 'absolute lexical path required')
    need(not any(x.is_symlink() for x in [p, *p.parents]), 'symlink path refused: ' + str(p))
    need(p.is_dir() if directory else p.is_file(), 'missing regular input: ' + str(p))
    return p


def pin(desc):
    need(isinstance(desc, dict) and re.fullmatch('[0-9a-f]{64}', desc.get('sha256', '')),
         'explicit SHA256 descriptor required')
    p = regular(desc['path']); before = p.stat()
    need(before.st_size <= 8 * 1024**3, 'source file size bound')
    need(desc.get('bytes', before.st_size) == before.st_size and digest(p) == desc['sha256'],
         'input pin mismatch: ' + str(p))
    after = p.stat()
    stable = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    need(stable(before) == stable(after), 'input changed while hashing: ' + str(p))
    return {'path': str(p), 'sha256': desc['sha256'], 'bytes': before.st_size,
            'stat': [before.st_dev, before.st_ino, before.st_size,
                     before.st_mtime_ns, before.st_ctime_ns]}


def small(path):
    p = regular(path); need(p.stat().st_size <= 4 * 1024**2, 'JSON size bound')
    return json.loads(p.read_text())


def emit(path, value, replace=False):
    path = Path(path)
    if not replace:
        with path.open('x') as f:
            json.dump(value, f, indent=2, sort_keys=True, allow_nan=False); f.write('\n')
        return
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        json.dump(value, f, indent=2, sort_keys=True, allow_nan=False); f.write('\n')
    os.replace(tmp, path)


def method_pins():
    lock = small(ROOT / 'SOURCE-LOCK.json')
    records = []
    for entry in lock['sources']:
        records.append(pin({'path': str(ROOT / entry['local']), 'sha256': entry['sha256']}))
    # Also bind wrapper code; preview is invalidated on any installed method/wrapper change.
    for name in ['__init__.py', 'api.py', 'worker.py', 'engine.py', 'method/__init__.py', 'SOURCE-LOCK.json']:
        p = ROOT / name
        records.append(pin({'path': str(p), 'sha256': digest(p)}))
    return records


def registry_counts(desc):
    """Metadata preview uses the exact frozen cohort rule (paired.cohort_registry)."""
    with open(desc['path'], newline='') as f:
        r = csv.DictReader(f, delimiter='\t')
        need(r.fieldnames and len(r.fieldnames) == len(set(r.fieldnames)) and
             {'barcode', 'pass_qc', 'cell_type'} <= set(r.fieldnames), 'registry schema')
        rows = []
        for row in r:
            need(len(rows) < 100000 and None not in row and None not in row.values(), 'registry bound/width')
            need(row['barcode'] and row['pass_qc'].strip().lower() in {'true', 'false'}, 'registry barcode/bool')
            rows.append(row)
    need(len({r['barcode'] for r in rows}) == len(rows), 'registry duplicates')
    assigned = [r for r in rows if r['pass_qc'].strip().lower() == 'true' and
                not r['cell_type'].startswith(('Unassigned', 'QC_fail'))]
    counts = Counter(r['cell_type'] for r in assigned)
    minimum = 20 if len(assigned) >= 500 else 1
    return len(rows), sum(counts[r['cell_type']] >= minimum for r in assigned)


def plan(request):
    """Hash and validate saved inputs, returning a serializable confirmable preview.

    No scientific analysis, native process, reference indexing, or output creation.
    Call in a GUI background task: full input hashing is deliberate.
    """
    r = json.loads(canonical(request))
    need(r.get('schema') == SCHEMA and set(r['arms']) == set(ARMS), 'request schema/arms')
    need(set(r) <= {'schema','arms','registry','references','contigs','compared_key',
                    'output_dir','bounds','resources','qc_input'}, 'unexpected request field')
    need(r.get('qc_input', 'compute') in {'compute', 'saved'}, 'QC input mode')
    contigs = r['contigs']
    need(isinstance(contigs, list) and contigs and len(contigs) == len(set(contigs)) and
         set(contigs) <= set(PRIMARY25), 'QC method supports explicit human primary25 contigs only')
    need(set(r['references']) == REFERENCE_ROLES, 'reference roles/schema')
    need(r['references']['method']['sha256'] ==
         'f6f4db8364e7792abb7d8de5e8cbf1727b7df99a698a195b0e50b91c703156c3', 'frozen QC method')
    output = Path(r['output_dir'])
    need(output.name == 'analysis', 'output_dir must be task_root/analysis')
    need(output.is_absolute() and '..' not in output.parts and not output.exists() and
         not output.is_symlink(), 'fresh absolute output directory required')
    regular(str(output.parent), directory=True)
    # An execution-only field cannot be selected as the scientific contrast.
    key = r['compared_key']
    need(isinstance(key, str) and key and key not in EXECUTION_KEYS, 'scientific compared_key required')
    a, b = (r['arms'][arm] for arm in ARMS)
    for arm in ARMS:
        x = r['arms'][arm]
        run = x['run']
        need(all(run.get(k) for k in ('record_id','invocation_id','root','config','metadata_pin')), 'selected run binding required')
        need(run['config'] == x['config'], 'run configuration binding mismatch')
        regular(run['root'], directory=True)
        need(set(x['files']) == {'peak_to_gene', 'counts', 'psi', 'gene_cell'}, 'four native files required')
        need(IDENTITY_KEYS <= set(x['identity']), 'complete scientific identity required')
        need(x['identity']['assembly'] == 'GRCh38', 'frozen human QC reference assembly')
        for name in IDENTITY_KEYS - {'assembly'}:
            need(re.fullmatch('[0-9a-f]{64}', x['identity'][name]), 'invalid identity SHA: ' + name)
        need(x['identity']['registry_sha256'] == r['registry']['sha256'], 'identity/registry pin')
        need(x['config'].get('quantification_mode') == 'terminal-aware' and x['config'].get('psi_min_gene_umis') == 1, 'frozen native terminal/PSI semantics required')
        need(x['config'].get('intronic_misprimed_ratio_threshold') == 0.7, 'frozen intronic threshold is 0.7')
        need(key in x['config'] and 'misprimed_ratio_threshold' in x['config'], 'required config key')
    need(a['identity'] == b['identity'], 'two arms must share scientific input/reference/runtime identity')
    ca, cb = ({k:v for k,v in x['config'].items() if k not in EXECUTION_KEYS} for x in (a,b))
    changed = {k for k in set(ca) | set(cb) if k not in ca or k not in cb or ca[k] != cb[k]}
    need(changed == {key}, 'exactly the selected scientific config key may differ')
    r['bounds'] = {**BOUND_MAX, **r.get('bounds', {})}
    r['resources'] = {**RESOURCE_MAX, **r.get('resources', {})}
    for values, maxima in [(r['bounds'], BOUND_MAX), (r['resources'], RESOURCE_MAX)]:
        need(set(values) == set(maxima) and all(type(v) is int and (v == 0 if k == 'swap_bytes' else 0 < v <= maxima[k])
             for k,v in values.items()), 'bounds/resources may only tighten')
    records = []
    def add(desc):
        record = pin(desc); records.append(record)
        need(not Path(record['path']).is_relative_to(output), 'output/source containment')
    add(r['registry'])
    n, selected = registry_counts(r['registry'])
    need(r['registry'].get('expected_rows', n) == n and
         r['registry'].get('expected_selected', selected) == selected, 'registry declared cohort mismatch')
    r['registry'].update(expected_rows=n, expected_selected=selected)
    for desc in r['references'].values(): add(desc)
    for arm in ARMS:
        for desc in r['arms'][arm]['files'].values(): add(desc)
        add(r['arms'][arm]['run']['metadata_pin'])
        if 'source_receipt' in r['arms'][arm]: add(r['arms'][arm]['source_receipt'])
        if r.get('qc_input', 'compute') == 'saved':
            add(r['arms'][arm]['qc_manifest'])
            manifest = small(r['arms'][arm]['qc_manifest']['path'])
            need([x['contig'] for x in manifest['contig_results']] == contigs, 'saved QC contig coverage')
            for entry in manifest['contig_results']:
                for name in ['sites.tsv', 'source-manifest.json']:
                    add({'path': str(Path(entry['output_dir']) / name), **entry['output_files'][name]})
    methods = method_pins()
    for record in records + methods:
        need(pin(record) == record, 'preview input changed during plan')
    result = {'schema': SCHEMA + '.plan', 'method':'cs2.qc_compare', 'status': 'ready', 'request': r,
              'inputs': records, 'methods': methods, 'parameters': {
                  'cutoffs': [3,5,10], 'modes': list(MODES), 'atlas_distance_max': 25,
                  'definitions': ['native_terminal', 'representative_terminal_exon']},
              'preview': {'comparison': {arm: r['arms'][arm]['config'][key] for arm in ARMS},
                          'compared_key': key, 'registry_rows': n, 'selected_label_cells': selected,
                          'qc_input': r.get('qc_input', 'compute'),
                          'output_dir': str(output), 'resources': r['resources']},
              'limitations': ['Descriptive sensitivity only; no FDR, optimal threshold or causal claim.',
                         'coverage3/coverage1 are fixed schema role names; consult selected run labels.',
                         'Identity declarations require the GUI run-binding receipt; hashes do not authenticate provenance.']}
    result.update(method_sources=methods, input_sources=records, scope={'contigs':contigs, 'registry_rows':n, 'selected_label_cells':selected}, resources=r['resources'])
    result['plan_sha256'] = hashlib.sha256(canonical(result)).hexdigest()
    return result


def validate_plan(p):
    need(p.get('schema') == SCHEMA + '.plan' and p.get('status') == 'ready', 'plan schema')
    need(hashlib.sha256(canonical({k:v for k,v in p.items() if k != 'plan_sha256'})).hexdigest()
         == p.get('plan_sha256'), 'plan digest mismatch')
    for record in p['inputs'] + p['methods']:
        need(pin(record) == record, 'preview is stale: ' + record['path'])
    # Revalidate structural science contract, not just the caller supplied digest.
    current = plan(p['request'])
    need(current['plan_sha256'] == p['plan_sha256'], 'preview contract changed')


def execute(p, output_dir):
    """Blocking saved-output worker; schedule in existing GUI background task pool.

    External scheduler applies CPU/MemoryMax/cgroup wall bounds in preview. This
    worker also enforces wall timeout, address-space and file/disk checks.
    """
    need(str(Path(output_dir)) == p['request']['output_dir'], 'execute output differs from preview')
    validate_plan(p)
    output = Path(p['request']['output_dir']); output.mkdir(exist_ok=False)
    scratch = output.parent / 'scratch'
    if not scratch.exists(): scratch.mkdir()
    regular(str(scratch), directory=True)
    emit(output / 'PLAN.json', p)
    emit(output / 'STATUS.json', {'status':'running', 'started_utc': now()}, replace=True)
    env = dict(os.environ, TMPDIR=str(scratch), SQLITE_TMPDIR=str(scratch),
               OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2')
    argv = [sys.executable, '-I', '-B', str(ROOT / 'worker.py'), str(output / 'PLAN.json'), p['plan_sha256']]
    emit(output / 'LAUNCH.json', {'argv': argv, 'scratch': str(scratch),
                                  'resources': p['request']['resources'], 'no_automatic_retry': True})
    try:
        with (output/'stdout.log').open('xb') as stdout, (output/'stderr.log').open('xb') as stderr:
            child = subprocess.run(argv, env=env, cwd=output, stdout=stdout, stderr=stderr,
                                   timeout=p['request']['resources']['wall_seconds'] + 30)
        need(child.returncode == 0, 'worker failed; see REFUSAL.json and stderr.log')
        need(status(output)['status'] == 'succeeded', 'worker missing terminal result')
    except BaseException as error:
        if not (output / 'REFUSAL.json').exists():
            emit(output / 'REFUSAL.json', {'status':'REFUSED_PARTIAL_OUTPUT_PRESERVED',
                  'error':str(error), 'error_type': type(error).__name__, 'no_automatic_retry':True})
        emit(output/'STATUS.json', {'status':'failed','ended_utc':now()}, replace=True)
        raise
    return result(output)


def status(output):
    return small(Path(output) / 'STATUS.json')


def result(output):
    need(status(output)['status'] == 'succeeded', 'result unavailable before successful terminal state')
    return small(Path(output) / 'index.json')


def capabilities():
    return [{'id':'cs2.qc_compare','label':'Paired PAS quality and APA sensitivity',
        'description':'Two saved native runs on the same source and references, with one scientific configuration contrast.',
        'parameters':{'cutoffs':{'value':[3,5,10],'read_only':True},
            'masks':{'value':list(MODES),'read_only':True},
            'atlas_distance_max':{'value':25,'read_only':True}},
        'input_schema':{'schema':SCHEMA,'required':['arms','registry','references','contigs','compared_key','output_dir'],
            'arms':'coverage3 and coverage1 role keys; each has run{record_id,invocation_id,root,config,metadata_pin}, config, identity, files{peak_to_gene,counts,psi,gene_cell}, optional label',
            'identity':'assembly=GRCh38, bam_sha256,gtf_sha256,chrom_sizes_sha256,registry_sha256,runtime_sha256; complete identities equal across arms',
            'registry':'TSV barcode/pass_qc/cell_type; descriptor path/sha256, optional expected_rows/expected_selected',
            'references':'descriptors method/fasta/fai/atlas/genome_receipt/atlas_receipt; original frozen method and intake schemas',
            'qc_input':'compute(default), or saved with per-arm qc_manifest descriptor; saved rows still closure/reference checked',
            'output_dir':'fresh task_root/analysis; sibling scratch configured before worker import'},
        'resource_defaults':dict(RESOURCE_MAX),
        'limitations':['Human GRCh38 primary25 QC method only.','Descriptive; no FDR, optimum or causal inference.','GUI must validate actual selected run snapshot; this API checks byte/config bindings, not authorship.']} ]
