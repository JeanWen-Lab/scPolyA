"""Fixed absolute entrypoint for GUI analysis execution and independent closure."""
from pathlib import Path
import sys
# Explicit package bootstrap before any project imports (-I does not include cwd).
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import hashlib
import json
import os
import subprocess
import time

# Verify the captured source graph before importing any project package.
if __name__=='__main__' and sys.argv[1]=='execute':
    task_path=Path(sys.argv[2]);metadata_path=task_path/'task.json'
    if metadata_path.stat().st_size>2*2**20:raise ValueError('Task metadata exceeds budget')
    metadata=json.loads(metadata_path.read_text())
    for item in metadata['code_sources']:
        source=Path(item['path'])
        if not source.resolve().is_relative_to(ROOT) or item['bytes']>2*2**20 or source.stat().st_size!=item['bytes'] or hashlib.sha256(source.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Code graph changed before project import')

from scpolya_ui.services import analysis_service as service
from scpolya_ui.registry import utc_now


def identity(target):
    target=service.task(target)
    meta=json.loads((target/'task.json').read_text())
    if meta['unit']!='scp-gui-analysis-'+target.name.lower():raise ValueError('Task unit binding changed')
    if meta['root']!=str(service.root()) or meta['ui_home']!=str(service.ui_home()):raise ValueError('Task home/root binding changed')
    if hashlib.sha256((target/'plan.json').read_bytes()).hexdigest()!=meta['plan_sha256']:raise ValueError('Plan bytes changed')
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=meta['worker_sha256']:raise ValueError('Worker bytes changed')
    service.verify_code(meta.get('code_sources',[]))
    return meta


def execute(target):
    try:
        meta=identity(target)
        if os.environ.get('TMPDIR')!=str(target/'scratch') or os.environ.get('SQLITE_TMPDIR')!=str(target/'scratch'):
            raise ValueError('Scratch must be bound before interpreter startup')
        service.emit(target/'status.json',dict(status='running',started_at=utc_now(),unit=meta['unit']))
        plan=json.loads((target/'plan.json').read_text())
        if meta.get('operation') == 'export_only':
            from scpolya_ui.services import export_recovery
            index=export_recovery.execute(target,plan)
            completion_names=('recovery-verification.json','report.zip','report-manifest.json')
        else:
            from scpolya_ui.analysis import dispatcher
            index=dispatcher.execute(plan,target/'analysis')
            service.emit(target/'science-complete.json',dict(plan_sha256=meta['plan_sha256'],
                         index_sha256=service.hash_file(target/'analysis/index.json'),time=utc_now()))
            service.package_report(target,index)
            completion_names=('analysis/index.json','report.zip','report-manifest.json')
        completed_files={}
        for name in completion_names:
            path=target/name;completed_files[name]=dict(bytes=path.stat().st_size,sha256=service.hash_file(path))
        service.emit(target/'computation-complete.json',dict(plan_sha256=meta['plan_sha256'],files=completed_files))
        service.emit(target/'status.json',dict(status='computation_complete',phase='Closing resource unit'))
    except BaseException as exc:
        service.emit(target/'REFUSAL.json',dict(status='failed',time=utc_now(),type=type(exc).__name__,reason=str(exc),partial_outputs='Retained; no automatic retry'))
        service.emit(target/'status.json',dict(status='failed',error=str(exc),finished_at=utc_now()))
        raise


def finalize(target, observation=None):
    # Fallback invokes the frozen stdlib hook and never imports a backend to close it.
    observation=observation or dict(service_result='closure_missing',exit_status='1')
    script=target/'code-snapshot/scpolya_ui/services/analysis_finalizer.py'
    subprocess.run([sys.executable,'-I','-B',str(script),str(observation.get('service_result','unknown')),str(observation.get('exit_status','unknown'))],env=os.environ,capture_output=True,text=True,timeout=30,check=True)


def monitor(target):
    try:
        meta=identity(target)
    except Exception:
        unit='scp-gui-analysis-'+target.name.lower()
        subprocess.run(['systemctl','--user','stop',unit],env=service._system_env(),capture_output=True,text=True,timeout=20)
        finalize(target,dict(origin='monitor',service_result='identity_changed',exit_status='1'));return
    deadline=time.monotonic()+meta['resources']['wall_seconds']+30
    while True:
        current=service.status(target)
        if current['status'] in ('succeeded','failed') and (target/'closure.json').is_file():return
        probe=subprocess.run(['systemctl','--user','show',meta['unit'],'--property=ActiveState,Result,ExecMainStatus','--no-pager'],env=service._system_env(),capture_output=True,text=True,timeout=15)
        fields=dict(line.split('=',1) for line in probe.stdout.splitlines() if '=' in line)
        if probe.returncode==0 and fields.get('ActiveState') in ('inactive','failed'):
            # ExecStopPost may still be closing. The terminal marker wins.
            if not (target/'closure.json').is_file():
                finalize(target,dict(origin='monitor',service_result=fields.get('Result'),exit_status=fields.get('ExecMainStatus')))
            return
        total=sum(p.stat().st_size for p in target.rglob('*') if p.is_file() and not p.is_symlink())
        if total>meta['resources']['disk_bytes'] or time.monotonic()>deadline:
            service.emit(target/'REFUSAL.json',dict(status='failed',time=utc_now(),reason='Task disk or hard-wall budget exceeded',partial_outputs='Retained'))
            subprocess.run(['systemctl','--user','stop',meta['unit']],env=service._system_env(),capture_output=True,text=True,timeout=20)
            if not (target/'closure.json').is_file():finalize(target,dict(origin='monitor',service_result='budget_exceeded',exit_status='1'))
            return
        time.sleep(1)


if __name__=='__main__':
    action=sys.argv[1];target=service.task(Path(sys.argv[2]))
    {'execute':execute,'finalize':finalize,'monitor':monitor}[action](target)
