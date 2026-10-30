"""Stdlib-only closure hook, executed from each task's immutable code snapshot."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(2**20),b''):h.update(block)
    return h.hexdigest()


def main():
    # This file is run only from TASK/code-snapshot/scpolya_ui/services/.
    target=Path(__file__).resolve().parents[3]
    if not re.fullmatch(r'A-[0-9a-f]{16}',target.name):raise ValueError('Invalid closure task')
    meta=json.loads((target/'task.json').read_text())
    if Path(meta['root']).resolve()!=target.parent or meta['unit']!='scp-gui-analysis-'+target.name.lower():raise ValueError('Closure task/unit binding mismatch')
    status=json.loads((target/'status.json').read_text())
    if (target/'closure.json').is_file() and status.get('status') in ('succeeded','failed'):return
    origin='exec_stop_post' if len(sys.argv)==1 else 'monitor'
    observation=dict(origin=origin,service_result=os.environ.get('SERVICE_RESULT') if origin=='exec_stop_post' else sys.argv[1],
                     exit_code=os.environ.get('EXIT_CODE'),exit_status=os.environ.get('EXIT_STATUS') if origin=='exec_stop_post' else sys.argv[2])
    good=origin=='exec_stop_post' and observation['service_result']=='success' and observation['exit_status']=='0'
    error=status.get('error') or 'Resource unit terminated before an authoritative successful closure'
    try:
        if not good or status.get('status')!='computation_complete':raise ValueError(error)
        complete=json.loads((target/'computation-complete.json').read_text())
        if complete['plan_sha256']!=meta['plan_sha256']:raise ValueError('Completion plan mismatch')
        for name,desc in complete['files'].items():
            path=target/name
            if not path.resolve().is_relative_to(target) or path.stat().st_size!=desc['bytes'] or digest(path)!=desc['sha256']:
                raise ValueError('Completed output bytes changed')
    except Exception as exc:
        good=False;error=str(exc)
    now=datetime.now(timezone.utc).isoformat()
    final=dict(status='succeeded' if good else 'failed',finished_at=now,closure=observation)
    if not good:final['error']=error
    if origin=='exec_stop_post':
        cg=Path('/sys/fs/cgroup')/Path('/proc/self/cgroup').read_text().strip().split(':',2)[2].lstrip('/')
        final['cgroup']=str(cg)
        final['resources']={key:(cg/key).read_text().strip() for key in ('memory.peak','memory.events','memory.max','memory.swap.max','cpu.max','cpu.stat') if (cg/key).is_file()}
    else:
        final['resource_observation']='Original workload cgroup counters unavailable to this fallback; no reconstructed peak/events claim'
    def emit(name,value):
        temporary=target/(name+'.'+str(os.getpid())+'.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temporary.replace(target/name)
    if not good and not (target/'REFUSAL.json').is_file():
        emit('REFUSAL.json',dict(schema='scpolya.ui.analysis.refusal.v1',status='failed',time=now,reason=error,origin='resource_unit_closure',observation=observation,partial_outputs='Retained; no automatic retry'))
    emit('closure.json',final);emit('status.json',final)


if __name__=='__main__':main()
