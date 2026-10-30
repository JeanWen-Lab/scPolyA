"""Explicit export-only recovery of a closed CS3 candidate packaging failure.

This module verifies saved facts using stdlib; it never imports a science backend.
The configured source root is read-only to this code, including status/closure.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
from . import report_export as exports

SCHEMA = 'scpolya.ui.export_recovery.v1'
RESOURCES = dict(cpu_cores=2, memory_bytes=2**30, swap_bytes=0,
                 wall_seconds=7200, disk_bytes=10*2**30)
LEGACY_ERROR = 'File size too large, try using force_zip64'


def read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2*2**20:
        raise ValueError('Missing or oversized recovery metadata: '+str(path))
    return json.loads(path.read_text())


def source_root():
    value = os.environ.get('SCPOLYA_UI_EXPORT_SOURCE_ROOT', '')
    if not value:
        raise ValueError('An explicit read-only export source root is required')
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError('Invalid export source root')
    return path


def source_task(path):
    path = Path(path)
    if (not path.is_absolute() or not re.fullmatch(r'A-[0-9a-f]{16}', path.name)
            or path.parent != source_root() or path.is_symlink() or not path.is_dir()):
        raise ValueError('Task outside configured read-only export source root')
    return path


def sources(workspace_id):
    """Cheap listing only; listing a failure is not permission to recover it."""
    if not os.environ.get('SCPOLYA_UI_EXPORT_SOURCE_ROOT'):
        return {}
    choices = {}
    for path in sorted(source_root().glob('A-*'))[:100]:
        try:
            source_task(path)
            preview = read(path/'preview.json')
            if (preview.get('workspace_id') == workspace_id
                    and preview.get('method') == 'cs3.candidates'
                    and read(path/'status.json').get('status') == 'failed'):
                choices[str(path)] = path.name+' · cs3.candidates · FAILED'
        except (OSError, ValueError, KeyError):
            continue
    return choices


def file_pin(path, expected=None):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('Regular absolute source file required: '+str(path))
    before = exports.stamp(path)
    desc = dict(path=str(path), bytes=before[2], sha256=exports.digest(path), stat=before)
    if before != exports.stamp(path):
        raise ValueError('Source changed while verifying: '+str(path))
    if expected is not None:
        if any(desc[k] != expected[k] for k in ('bytes', 'sha256') if k in expected):
            raise ValueError('Source hash/size mismatch: '+str(path))
        if 'stat' in expected:
            s = expected['stat']
            wanted = [s[k] for k in ('dev','ino','bytes','mtime_ns','ctime_ns')] if isinstance(s, dict) else s
            if before != wanted:raise ValueError('Source stat mismatch: '+str(path))
    return desc


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def code_revision(checkout):
    result=subprocess.run(['git','-C',str(checkout),'rev-parse','HEAD'],capture_output=True,text=True,timeout=10,check=True)
    commit=result.stdout.strip()
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('Missing code revision')
    return commit


def verify_source(path):
    source = source_task(path)
    outer = read(source/'status.json'); closure = read(source/'closure.json')
    refusal = read(source/'REFUSAL.json'); meta = read(source/'task.json')
    plan = read(source/'plan.json'); preview = read(source/'preview.json')
    index = read(source/'analysis/index.json')
    if outer.get('status') != 'failed' or closure != outer or closure.get('closure', {}).get('origin') != 'exec_stop_post':
        raise ValueError('An authoritative closed FAILED source task is required')
    if (meta.get('method') != 'cs3.candidates' or meta.get('operation', 'analysis') != 'analysis'
            or meta.get('unit') != 'scp-gui-analysis-'+source.name.lower()
            or meta.get('root') != str(source.parent) or not meta.get('ui_home')):
        raise ValueError('Source task identity mismatch')
    if (refusal.get('reason') != LEGACY_ERROR or refusal.get('type') != 'RuntimeError'
            or outer.get('error') != LEGACY_ERROR or (source/'computation-complete.json').exists()
            or (source/'report-manifest.json').exists()):
        raise ValueError('Only the identified ZIP64 packaging failure can be recovered')
    if (read(source/'analysis/status.json').get('status') != 'succeeded'
            or index.get('schema') != 'scpolya.gui.cs3.v1.result' or index.get('status') != 'succeeded'
            or index.get('method') != meta['method'] or (source/'analysis/REFUSAL.json').exists()):
        raise ValueError('Complete successful science status and index required')
    if (plan.get('schema') != 'scpolya.gui.cs3.v1.plan' or plan.get('status') != 'ready'
            or plan.get('method') != meta['method'] or plan.get('request', {}).get('mode') not in ('historical_export', 'synthetic')
            or hashlib.sha256(canonical({k:v for k,v in plan.items() if k != 'plan_sha256'})).hexdigest() != plan.get('plan_sha256')
            or exports.digest(source/'plan.json') != meta.get('plan_sha256')
            or read(source/'analysis/PLAN.json') != plan or preview.get('backend_plan') != plan
            or preview.get('task_root') != str(source) or preview.get('gui_sources') != meta.get('code_sources')):
        raise ValueError('Source science plan/input identity mismatch')
    for key in ('method_sources','input_sources','parameters','scope','limitations'):
        if index.get(key) != plan.get(key):raise ValueError('Science index/plan binding mismatch: '+key)
    pins = {}
    def pin(p, expected=None):
        desc = file_pin(p, expected)
        pins[str(p)] = desc
        return desc
    # Bind original receipts and damaged archive without modifying any old bytes.
    for name in ('task.json','preview.json','plan.json','status.json','closure.json','REFUSAL.json','worker.log',
                 'analysis/index.json','analysis/status.json','analysis/PLAN.json','report.zip'):
        pin(source/name)
    for p in sorted(source.glob('*binding.json')):pin(p)
    codes = meta.get('code_sources', [])
    if not codes or len(codes) > 1024 or sum(c['bytes'] for c in codes) > 32*2**20:
        raise ValueError('Missing or oversized original code graph')
    worker = next((c for c in codes if c['relative_path'] == 'scpolya_ui/services/analysis_worker.py'), None)
    if not worker or worker['sha256'] != meta.get('worker_sha256'):
        raise ValueError('Original worker/code graph binding mismatch')
    checkout = Path(worker['path']).parents[2]
    seen = set()
    for code in codes:
        relative = code['relative_path']
        if relative in seen or Path(code['path']) != checkout/relative:
            raise ValueError('Original code graph path mismatch')
        seen.add(relative)
        saved = exports.member_path(source/'code-snapshot', relative)
        pin(saved, code); pin(code['path'], code)
    if {str(p.relative_to(source/'code-snapshot')) for p in (source/'code-snapshot').rglob('*') if p.is_file()} != seen:
        raise ValueError('Original code snapshot inventory mismatch')
    for code in plan['method_sources']:
        relative = str(Path(code['path']).relative_to(checkout))
        if relative not in seen:raise ValueError('Method outside original code snapshot')
        pin(code['path'], code)
    executable = plan['runtime']['executable']
    if executable['sha256'] != meta['analysis_python']['sha256'] or executable['path'] != meta['analysis_python']['path']:
        raise ValueError('Original interpreter binding mismatch')
    pin(executable['path'], executable)
    inputs = plan['input_sources']
    requested = plan['request']['inputs']
    if {p['role'] for p in inputs} != set(requested) or len(inputs) != len(requested):
        raise ValueError('Incomplete original input graph')
    for item in inputs:
        wanted = requested[item['role']]
        if any(item[k] != wanted[k] for k in ('path','bytes','sha256')):
            raise ValueError('Input request/plan binding mismatch')
        pin(item['path'], item)
    indexed = index.get('tables', []) + index.get('artifacts', [])
    names = [p['path'] for p in indexed]
    if not indexed or len(set(names)) != len(names):raise ValueError('Missing or duplicate science index records')
    records = exports.inventory(source/'analysis')
    if exports.upper_bound(records) > exports.MAX_EXPORT_BYTES:raise ValueError('Complete export exceeds 8GiB upper bound')
    actual_data = {str(p.relative_to(source/'analysis')) for p in (source/'analysis/data').rglob('*') if p.is_file()}
    if actual_data != set(names):raise ValueError('Science index does not account for all exported data')
    # Excluded SQLite intermediates are still verified against the science index.
    for item in indexed:
        if exports.excluded(item['path']):pin(exports.member_path(source/'analysis',item['path']),item)
    expected = {}
    by_name = {p['path']: p for p in indexed}
    for record in records:
        p = exports.member_path(source/'analysis', record['path'])
        desc = pin(p, by_name.get(record['path']))
        expected[record['path']] = {k:desc[k] for k in ('bytes','sha256')}
    if shutil.disk_usage(source.parent).free < exports.upper_bound(records) + 256*2**20:
        raise ValueError('Insufficient disk for a complete conservative export')
    return dict(schema=SCHEMA+'.source_verification', source_task=str(source),
                original_status='failed', science_status='succeeded', scientific_execution=False,
                science_commit=code_revision(checkout),
                method=meta['method'], pins=pins, files=expected,
                source_bytes=sum(p['bytes'] for p in records), zip_upper_bound=exports.upper_bound(records),
                workspace_id=preview.get('workspace_id'), selected_runs=preview.get('selected_runs', []),
                scope=index['scope'], parameters=index['parameters'], limitations=index['limitations'])


def preview(path):
    from . import analysis_service as service
    binding = verify_source(path)
    service.root().mkdir(parents=True, exist_ok=True)
    if service.root().resolve().is_relative_to(source_root()) or source_root().is_relative_to(service.root().resolve()):
        raise ValueError('Recovery requires an independent output root')
    if shutil.disk_usage(service.root()).free < binding['zip_upper_bound'] + 256*2**20:
        raise ValueError('Insufficient recovery output disk')
    target = service.root()/('A-'+uuid.uuid4().hex[:16]); target.mkdir(); (target/'scratch').mkdir()
    backend = dict(schema=SCHEMA+'.plan', method=binding['method'], operation='export_only', binding=binding, export_contract=exports.RECOVERY_CONTRACT,
                   export_commit=code_revision(service.WORKER.parents[2]))
    value = dict(schema='scpolya.ui.analysis.preview.v1', operation='export_only', method=binding['method'],
                 status='ready', task_root=str(target), gui_sources=service.code_sources(), backend_plan=backend,
                 resources=dict(RESOURCES), workspace_id=binding['workspace_id'], selected_runs=binding['selected_runs'],
                 scope=binding['scope'], parameters=dict(source_task=binding['source_task'],
                 scientific_execution=False, source_bytes=binding['source_bytes'], zip_upper_bound=binding['zip_upper_bound']),
                 limitations=binding['limitations'])
    service.emit(target/'preview.json', value); service.emit(target/'status.json', dict(status='ready', operation='export_only'))
    return value


def verify_binding(binding):
    # Cheap stat gate before full hashing, including original failure and damaged ZIP.
    source_task(binding['source_task'])
    for item in binding['pins'].values():
        if exports.stamp(item['path']) != item['stat']:
            raise ValueError('Source changed after recovery preview: '+item['path'])
    current = verify_source(binding['source_task'])
    if current != binding:raise ValueError('Recovery source changed after preview')
    return Path(binding['source_task'])


def execute(target, plan):
    from . import analysis_service as service
    if (plan.get('schema') != SCHEMA+'.plan' or plan.get('operation') != 'export_only'
            or plan.get('export_contract') != exports.RECOVERY_CONTRACT):
        raise ValueError('Explicit export-only plan required')
    if code_revision(service.WORKER.parents[2]) != plan['export_commit']:
        raise ValueError('Export code revision changed after preview')
    source = verify_binding(plan['binding'])
    service.emit(target/'recovery-verification.json', dict(schema=SCHEMA+'.execution',
                 time=service.utc_now(), source_task=str(source), original_status='failed',
                 scientific_execution=False, science_commit=plan['binding']['science_commit'],
                 export_commit=plan['export_commit'], source_verification=plan['binding']))
    index = read(source/'analysis/index.json')
    exports.package(target, index, source=source, expected=plan['binding']['files'])
    # Detect mutations to any receipt, input, original code or damaged package during export.
    for pin in plan['binding']['pins'].values():
        if exports.stamp(pin['path']) != pin['stat']:
            raise ValueError('Source changed during export-only recovery')
    return index


def result_base(target):
    plan = read(target/'plan.json')
    if (plan.get('schema') != SCHEMA+'.plan' or plan.get('operation') != 'export_only'
            or plan.get('export_contract') != exports.RECOVERY_CONTRACT):
        raise ValueError('Saved recovery plan required')
    verification = read(target/'recovery-verification.json')
    if verification.get('source_verification') != plan['binding'] or verification.get('scientific_execution') is not False:
        raise ValueError('Missing bound recovery execution receipt')
    source = source_task(plan['binding']['source_task'])
    if exports.digest(source/'analysis/index.json') != plan['binding']['pins'][str(source/'analysis/index.json')]['sha256']:
        raise ValueError('Original science index changed')
    return source/'analysis'
