"""Durable task launcher, resource supervision, bounded result pages and exports."""
from __future__ import annotations
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import zipfile

from scpolya_ui.registry import ui_home, utc_now

THREADS = ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','VECLIB_MAXIMUM_THREADS')
WORKER = Path(__file__).with_name('analysis_worker.py').resolve()
MAX_INDEX = 2*2**20


def hash_file(path):
    from .report_export import digest
    return digest(path)


def root() -> Path:
    identity = hashlib.sha256(str(ui_home().resolve()).encode()).hexdigest()[:16]
    return Path(os.environ.get('SCPOLYA_UI_ANALYSIS_ROOT', str(ui_home()/'gui-analyses'/identity)))


def task(path: str | Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or not re.fullmatch(r'A-[0-9a-f]{16}',path.name) or path.parent.resolve()!=root().resolve() or path.is_symlink():
        raise ValueError('Invalid analysis task path')
    return path


def emit(path: Path, value):
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def code_sources():
    checkout=WORKER.parents[2]
    paths=[]
    for directory in ('scpolya_ui','task_manage'):
        paths.extend(p for p in (checkout/directory).rglob('*') if p.suffix in ('.py','.json') and p.is_file())
    paths.append(checkout/'quantification_config.py')
    if len(paths)>1024 or sum(p.stat().st_size for p in paths)>32*2**20:raise ValueError('Code snapshot exceeds bounded graph')
    records=[]
    for path in sorted(set(paths)):
        raw=path.read_bytes()
        if len(raw)>2*2**20:raise ValueError('Code file exceeds graph bound')
        records.append(dict(path=str(path),relative_path=str(path.relative_to(checkout)),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    return records


def verify_code(records):
    checkout=WORKER.parents[2]
    for item in records:
        path=Path(item['path'])
        if not path.resolve().is_relative_to(checkout) or path.stat().st_size!=item['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Installed code changed after preview: '+str(path))


def _system_env():
    return dict(os.environ,XDG_RUNTIME_DIR='/run/user/'+str(os.getuid()),
                DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/'+str(os.getuid())+'/bus')


def capabilities():
    from scpolya_ui.analysis import dispatcher
    return dispatcher.capabilities()


def _preview(builder, workspace_id=None, selected_runs=None):
    from scpolya_ui.analysis import dispatcher
    root().mkdir(parents=True,exist_ok=True)
    target=root()/('A-'+uuid.uuid4().hex[:16]);target.mkdir();(target/'scratch').mkdir()
    emit(target/'status.json',dict(status='previewing',created_at=utc_now()))
    try:
        payload=builder(target)
        if payload['method'].startswith('cs2.'):
            payload['output_dir']=str(target/'analysis')
        backend_plan=dispatcher.plan(payload)
        preview=dict(schema='scpolya.ui.analysis.preview.v1',method=backend_plan['method'],status='ready',task_root=str(target),
                     workspace_id=workspace_id,selected_runs=selected_runs or [],gui_sources=code_sources(),backend_plan=backend_plan,resources=backend_plan['resources'],scope=backend_plan['scope'],
                     parameters=backend_plan['parameters'],limitations=backend_plan['limitations'])
        emit(target/'preview.json',preview)
        emit(target/'status.json',dict(status='ready',created_at=utc_now()))
        return preview
    except BaseException as exc:
        emit(target/'PLAN-REFUSAL.json',dict(time=utc_now(),error=str(exc),partial_outputs='Preview only; no automatic retry'))
        emit(target/'status.json',dict(status='failed',error=str(exc)))
        raise


def plan(request):
    return _preview(lambda target:dict(request))


def preview_selection(method, records, mappings, fields):
    from scpolya_ui.services import analysis_source_service as sources
    if method!='cs3.transform' and (not records or len({r.workspace_id for r in records})!=1):raise ValueError('Select executions in one workspace')
    def build(target):
        if method=='cs1.fixed_coverage_full':return sources.cs1_request(records[0],mappings,fields)
        if method=='cs2.qc_compare':return sources.cs2_request(records,mappings,fields,target)
        if method.startswith('cs3.'):return sources.cs3_request(method,records,mappings,fields,target)
        raise ValueError('This method has no integrated source form yet')
    return _preview(build,records[0].workspace_id if records else fields.get('_workspace_id'),[dict(record_id=r.id,label=r.label,root=r.root_path) for r in records])


def start(preview: dict) -> Path:
    """A GUI confirmation calls this once. No retries and no approval CLI."""
    if preview.get('schema')!='scpolya.ui.analysis.preview.v1':raise ValueError('Common preview required')
    target=task(Path(preview['task_root']))
    if status(target)['status']!='ready' or json.loads((target/'preview.json').read_text())!=preview:raise ValueError('Preview changed or already consumed')
    verify_code(preview['gui_sources'])
    if preview.get('operation') == 'export_only':
        from . import export_recovery
        with (target/'EXPORT-START-CLAIM.json').open('x') as claim:
            json.dump(dict(time=utc_now(),operation='export_only'),claim)
        try:
            export_recovery.verify_binding(preview['backend_plan']['binding'])
        except BaseException as exc:
            emit(target/'status.json',dict(status='failed',error=str(exc),finished_at=utc_now()))
            emit(target/'REFUSAL.json',dict(reason=str(exc),partial_outputs='No automatic retry'))
            raise
    resources = preview['resources']
    if resources['cpu_cores']>2 or resources['memory_bytes']>16*2**30 or resources['swap_bytes']!=0 or resources['wall_seconds']>7200 or resources['disk_bytes']>20*2**30:
        raise ValueError('Analysis resource envelope exceeded')
    root().mkdir(parents=True,exist_ok=True)
    if any(status(p)['status'] in ('starting','running','computation_complete') for p in recent()):
        raise ValueError('Another analysis is still running')
    emit(target/'plan.json',preview['backend_plan'])
    unit = 'scp-gui-analysis-'+target.name.lower()
    identity = dict(schema='scpolya.ui.analysis.task.v1',unit=unit,method=preview['method'],created_at=utc_now(),
                    operation=preview.get('operation','analysis'),
                    ui_home=str(ui_home()),root=str(root()),plan_sha256=hashlib.sha256((target/'plan.json').read_bytes()).hexdigest(),
                    worker_sha256=hashlib.sha256(WORKER.read_bytes()).hexdigest(),resources=resources,code_sources=preview['gui_sources'],
                    analysis_python=dict(path=str(Path(sys.executable).resolve()),sha256=hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),version=sys.version))
    emit(target/'task.json',identity)
    for item in preview['gui_sources']:
        destination=target/'code-snapshot'/item['relative_path'];destination.parent.mkdir(parents=True,exist_ok=True)
        raw=Path(item['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Code changed during task snapshot')
        destination.write_bytes(raw)
    emit(target/'status.json',dict(status='starting',created_at=identity['created_at']))
    # Absolute Python and worker path. systemd parses one fixed ExecStopPost command;
    # paths are checked to exclude whitespace/metacharacters before construction.
    fixed = [str(Path(sys.executable).resolve()),str(WORKER),str(target)]
    if any(re.search(r'[^A-Za-z0-9_./-]',v) for v in fixed):
        emit(target/'status.json',dict(status='failed',error='Launcher paths contain unsupported characters'))
        raise ValueError('Launcher requires ordinary absolute executable/task paths')
    argv = ['systemd-run','--user','--unit='+unit,'--service-type=exec',
            '--property=CPUQuota='+str(resources['cpu_cores']*100)+'%',
            '--property=MemoryMax='+str(resources['memory_bytes']),'--property=MemorySwapMax=0',
            '--property=RuntimeMaxSec='+str(resources['wall_seconds']),'--property=TimeoutStopSec=10',
            '--property=KillMode=control-group',
            '--property=ExecStopPost='+fixed[0]+' -I -B '+str(target/'code-snapshot/scpolya_ui/services/analysis_finalizer.py'),
            '--property=StandardOutput=append:'+str(target/'worker.log'),
            '--property=StandardError=append:'+str(target/'worker.log'),
            '--setenv=SCPOLYA_UI_HOME='+str(ui_home()),'--setenv=SCPOLYA_UI_ANALYSIS_ROOT='+str(root()),
            '--setenv=TMPDIR='+str(target/'scratch'),'--setenv=SQLITE_TMPDIR='+str(target/'scratch')]
    if preview.get('operation') == 'export_only':
        argv.append('--setenv=SCPOLYA_UI_EXPORT_SOURCE_ROOT='+str(export_recovery.source_root()))
    argv.extend('--setenv='+name+'=1' for name in THREADS)
    argv.extend([fixed[0],'-I','-B',fixed[1],'execute',fixed[2]])
    emit(target/'launch.json',dict(argv=argv))
    try:
        completed = subprocess.run(argv,env=_system_env(),capture_output=True,text=True,timeout=30)
        emit(target/'submission.json',dict(returncode=completed.returncode,stdout=completed.stdout,stderr=completed.stderr))
        if completed.returncode:
            raise RuntimeError('Task launch failed: '+completed.stderr.strip())
        with (target/'monitor.log').open('xb') as log:
            subprocess.Popen([fixed[0],'-I','-B',fixed[1],'monitor',fixed[2]],env=dict(_system_env(),SCPOLYA_UI_HOME=str(ui_home()),SCPOLYA_UI_ANALYSIS_ROOT=str(root())),
                             stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
    except BaseException as exc:
        emit(target/'status.json',dict(status='failed',error=str(exc),finished_at=utc_now()))
        raise
    return target


def recent():
    return sorted((p for p in root().glob('A-*') if p.is_dir() and not p.is_symlink()),key=lambda p:p.stat().st_mtime,reverse=True)[:100]


def reconcile_lost_task(target: Path, value: dict):
    """A restarted UI closes a terminated unit even when its monitor/hook died."""
    if value.get('status') not in ('starting','running','computation_complete') or (target/'closure.json').is_file():return value
    try:
        submission=json.loads((target/'submission.json').read_text())
        if submission.get('returncode')!=0:return value
        meta=json.loads((target/'task.json').read_text())
        unit='scp-gui-analysis-'+target.name.lower()
        if meta['unit']!=unit:raise ValueError('Unit identity changed')
        probe=subprocess.run(['systemctl','--user','show',unit,'--property=ActiveState,Result,ExecMainStatus','--no-pager'],env=_system_env(),capture_output=True,text=True,timeout=3)
        fields=dict(line.split('=',1) for line in probe.stdout.splitlines() if '=' in line)
        if probe.returncode or fields.get('ActiveState') not in ('inactive','failed'):return value
        frozen=target/'code-snapshot/scpolya_ui/services/analysis_finalizer.py'
        completed=subprocess.run([meta['analysis_python']['path'],'-I','-B',str(frozen),fields.get('Result','unknown'),fields.get('ExecMainStatus','unknown')],capture_output=True,text=True,timeout=3)
        if completed.returncode==0 and (target/'closure.json').is_file():return json.loads((target/'status.json').read_text())
        failure=dict(status='failed',error='Unit terminated and closure tracking was lost',finished_at=utc_now(),observation=fields,resource_observation='Original workload counters unavailable; no reconstructed peak/events claim')
        emit(target/'REFUSAL.json',dict(**failure,partial_outputs='Retained; no automatic retry'))
        emit(target/'closure.json',failure);emit(target/'status.json',failure);return failure
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired):
        return value


def status(target: Path):
    target = task(target)
    try:
        value=json.loads((target/'status.json').read_text())
        return reconcile_lost_task(target,value)
    except (OSError,ValueError):
        return dict(status='failed',error='Task status is unavailable; partial files retained')


def read_result(target: Path):
    target = task(target)
    if status(target)['status']!='succeeded':
        raise ValueError('No successful analysis result')
    from scpolya_ui.analysis import dispatcher
    method = json.loads((target/'task.json').read_text())['method']
    value = dispatcher.result(method,result_base(target))
    if len(json.dumps(value).encode())>MAX_INDEX:
        raise ValueError('Result metadata exceeds the display budget')
    return value


def result_base(target: Path):
    meta=json.loads((target/'task.json').read_text()) if (target/'task.json').is_file() else {}
    if meta.get('operation') == 'export_only':
        if status(target)['status'] != 'succeeded':raise ValueError('Recovery has not succeeded')
        from . import export_recovery
        return export_recovery.result_base(target)
    return target/'analysis'


def output_path(target: Path, name: str):
    target = task(target)
    base = result_base(target);path = base/name
    if Path(name).is_absolute() or not path.resolve().is_relative_to(base.resolve()) or path.is_symlink() or not path.is_file():
        raise ValueError('Invalid result file path')
    return path


def table_page(target: Path, table: dict, page: int = 1, rows_per_page: int = 25):
    if type(page) is not int or page<1 or rows_per_page not in (25,50,100):
        raise ValueError('Invalid table page')
    path = output_path(target,table['path'])
    if path.stat().st_size!=table['bytes']:
        raise ValueError('Table size changed')
    # Verify full bytes before displaying a page; aggregate remains bounded to one page.
    h=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(2**20),b''):h.update(block)
    if h.hexdigest()!=table['sha256']:
        raise ValueError('Saved table changed')
    opener=gzip.open if path.suffix=='.gz' else open
    rows=[];first=(page-1)*rows_per_page
    if first>10_000_000:raise ValueError('Page exceeds the bounded reader')
    with opener(path,'rt',encoding='utf-8',newline='') as handle:
        if table.get('format')=='jsonl':
            def objects():
                for line in handle:
                    if len(line)>2**20:raise ValueError('JSONL row exceeds reader budget')
                    yield json.loads(line)
            reader=objects()
        elif table.get('format')=='bed':
            # BED has no header. Preserve every literal field, including the first
            # interval, instead of letting DictReader consume it as column names.
            bed_columns=('chrom','chromStart','chromEnd','name','score','strand',
                         'thickStart','thickEnd','itemRgb','blockCount','blockSizes','blockStarts')
            def intervals():
                for line in handle:
                    literal=line.removesuffix('\n').removesuffix('\r')
                    if literal.lstrip().startswith('#') or re.match(r'\s*(?:track|browser)(?:\s|$)',literal):
                        yield {'comment':literal}
                        continue
                    fields=next(csv.reader([line],delimiter='\t',quoting=csv.QUOTE_NONE))
                    yield {bed_columns[i] if i<len(bed_columns) else 'column_'+str(i+1):value
                           for i,value in enumerate(fields)}
            reader=intervals()
        else:
            reader=csv.DictReader(handle,delimiter='\t')
        for index,row in enumerate(reader):
            if index>=first+rows_per_page:break
            if index>=first:rows.append(row)
    if table.get('format')=='bed' and rows:
        # The first row may be a liftOver comment or a narrower BED interval.
        # Give every displayed row the same ordered columns for this page.
        width=max(3,max(len(row)-('comment' in row) for row in rows))
        columns=[bed_columns[i] if i<len(bed_columns) else 'column_'+str(i+1) for i in range(width)]
        if any('comment' in row for row in rows):columns.append('comment')
        rows=[{column:row.get(column,'') for column in columns} for row in rows]
    return rows


def package_report(target: Path, index: dict):
    from .report_export import package
    return package(target,index)


def download_path(target: Path, name: str='report.zip'):
    """Verify by streaming, then let the HTTP file response stream bounded blocks."""
    from .report_export import digest, MAX_EXPORT_BYTES, LEGACY_DOWNLOAD_BYTES, RECOVERY_CONTRACT, stamp
    target=task(target)
    manifest=json.loads((target/'report-manifest.json').read_text())
    if name=='report.zip':
        path=target/name;expected=manifest['report_sha256'];size=manifest['report_bytes']
    else:
        path=output_path(target,name);desc=manifest['files'].get(name,{})
        expected=desc.get('sha256');size=desc.get('bytes')
    limit=LEGACY_DOWNLOAD_BYTES
    if manifest.get('schema')=='scpolya.ui.analysis.export.v2':
        meta=json.loads((target/'task.json').read_text());plan=json.loads((target/'plan.json').read_text())
        if (meta.get('operation')!='export_only' or plan.get('export_contract')!=RECOVERY_CONTRACT
                or manifest.get('export_contract')!=RECOVERY_CONTRACT or status(target)['status']!='succeeded'):
            raise ValueError('Versioned export-only streaming contract required')
        limit=MAX_EXPORT_BYTES
    before=stamp(path)
    if path.is_symlink() or path.stat().st_size>limit:
        raise ValueError('Download exceeds declared budget or is a symlink')
    if digest(path)!=expected or path.stat().st_size!=size or stamp(path)!=before:
        raise ValueError('Saved export changed')
    return path


def download_bytes(target: Path, name: str='report.zip'):
    """Compatibility reader for small callers; the product uses download_path."""
    path=download_path(target,name)
    if path.stat().st_size>512*2**20:raise ValueError('In-memory download exceeds 512MiB budget; use streaming download')
    return path.read_bytes()
