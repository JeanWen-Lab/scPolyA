"""Direct source-checkout entry: python -I -B /.../cs3_backend/worker.py ..."""
import hashlib
import json
from pathlib import Path
import sys


def verify_execute_sources(argv):
    """Check recorded project entry bytes before importing any project package."""
    if not argv or argv[0] != 'execute':
        return
    if '--plan' not in argv or argv.index('--plan') + 1 == len(argv):
        raise SystemExit('execute requires --plan before package import')
    value = json.loads(Path(argv[argv.index('--plan') + 1]).read_text())
    here = Path(__file__).resolve().parent
    required = {p.resolve() for p in here.iterdir() if p.suffix == '.py' or p.name == 'METHOD-SOURCES.json'}
    required |= {here.parents[1] / name for name in
                 ('__init__.py', 'registry.py', 'run_data.py', 'run_records.py', 'services/run_evidence_service.py')}
    entries = {Path(p['path']): p for p in value['method_sources']}
    if not required <= entries.keys():
        raise SystemExit('plan does not pin this checkout import entry graph')
    for path in required:
        expected = entries[path]
        before = path.stat()
        if path.is_symlink() or not path.is_file():
            raise SystemExit('source entry is not a regular non-symlink file')
        with path.open('rb') as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1048576), b''):
                digest.update(chunk)
        after = path.stat()
        observed = dict(dev=after.st_dev, ino=after.st_ino, bytes=after.st_size,
                        mtime_ns=after.st_mtime_ns, ctime_ns=after.st_ctime_ns)
        if digest.hexdigest() != expected['sha256'] or observed != expected['stat'] or before != after:
            raise SystemExit('source entry changed before package import: ' + str(path))


if __name__ == '__main__':
    verify_execute_sources(sys.argv[1:])
    # Isolated Python ignores PYTHONPATH; bind only this script's own checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from scpolya_ui.analysis.cs3_backend.api import main
    main()
