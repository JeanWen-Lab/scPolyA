"""Isolated worker entry. Scratch must be configured before importing SQLite."""
import os
from pathlib import Path
import resource
import sys
import traceback


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


root = Path(__file__).resolve().parent
output = Path(sys.argv[1]).parent
bound_output = False
try:
    need('sqlite3' not in sys.modules, 'SQLite loaded before scratch check')
    sys.path.insert(0, str(root.parent))
    from cs2_backend import api
    p = api.small(sys.argv[1])
    need(api.hashlib.sha256(api.canonical({k:v for k,v in p.items() if k!='plan_sha256'})).hexdigest()
         == p['plan_sha256'] == sys.argv[2], 'worker plan digest mismatch')
    need(p['request']['output_dir'] == str(output) and output.name == 'analysis',
         'worker output differs from bound plan')
    api.regular(str(output), directory=True)
    bound_output = True
    scratch = output.parent / 'scratch'
    api.regular(str(scratch), directory=True)
    need(os.environ.get('TMPDIR') == str(scratch) == os.environ.get('SQLITE_TMPDIR'),
         'startup scratch environment mismatch before SQLite import')
    # Source/request pins are rechecked before scientific imports.
    need(api.digest(root / 'api.py') == next(x['sha256'] for x in p['methods'] if x['path'] == str(root / 'api.py')),
         'worker API source pin mismatch')
    for record in p['inputs'] + p['methods']:
        api.need(api.pin(record) == record, 'worker source drift: ' + record['path'])
    budget = p['request']['resources']
    resource.setrlimit(resource.RLIMIT_AS, (budget['memory_bytes'], budget['memory_bytes']))
    resource.setrlimit(resource.RLIMIT_FSIZE, (budget['disk_bytes'], budget['disk_bytes']))
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:budget['cpu_cores']])
    from cs2_backend.engine import compute
    compute(p, output)
except BaseException as error:
    traceback.print_exc()
    # Write only in an existing directory explicitly bound by the validated plan.
    if bound_output:
        if not (output/'REFUSAL.json').exists():
            api.emit(output/'REFUSAL.json', {'status':'REFUSED_PARTIAL_OUTPUT_PRESERVED',
                'error_type':type(error).__name__, 'error':str(error), 'no_automatic_retry':True})
        api.emit(output/'STATUS.json', {'status':'failed','ended_utc':api.now()}, replace=True)
    raise SystemExit(2)
