"""Streaming, bounded ZIP64 exports; a partial archive is never a report."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import uuid
import zipfile

MAX_EXPORT_BYTES = 8 * 2**30
LEGACY_DOWNLOAD_BYTES = 512 * 2**20
RECOVERY_CONTRACT = 'scpolya.ui.export_recovery.zip64-streaming-8g.v1'
MAX_FILES = 4096
BLOCK = 2**20


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(BLOCK), b''):
            h.update(block)
    return h.hexdigest()


def stamp(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def member_path(base, name):
    name = str(name)
    relative = Path(name)
    path = base / relative
    if relative.is_absolute() or not relative.parts or any(p in ('.', '..') for p in relative.parts) or '\\' in name:
        raise ValueError('Invalid export member path')
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('Export member escapes output or is not a regular file')
    for parent in path.parents:
        if parent == base.parent:
            break
        if parent.is_symlink():
            raise ValueError('Export symlink directory refused')
    return path


def excluded(path):
    path=Path(path)
    return path.suffix in ('.sqlite','.sqlite3','.db') or path.name in ('report.zip','files.json')


def inventory(base):
    records = []
    if base.is_symlink() or not base.is_dir():
        raise ValueError('Regular analysis directory required')
    for path in sorted(base.rglob('*')):
        if path.is_symlink():
            raise ValueError('Output symlink refused')
        if not path.is_file():
            continue
        if excluded(path):
            continue
        name = str(path.relative_to(base))
        member_path(base, name)
        records.append(dict(path=name, bytes=path.stat().st_size))
        if len(records) > MAX_FILES:
            raise ValueError('Export file count exceeds budget')
    return records


def upper_bound(records, extra_bytes=128*2**20):
    # zlib compressBound plus generous per-member metadata/ZIP64 allowance.
    return extra_bytes + sum(r['bytes'] + (r['bytes'] >> 12) + (r['bytes'] >> 14)
                             + (r['bytes'] >> 25) + 13 + 2**17 for r in records)


def package(target, index, *, source=None, expected=None):
    from . import analysis_service as service
    target = service.task(target)
    if (target/'report.zip').exists():raise ValueError('Existing report is preserved; use a separate export attempt')
    source = source or target
    base = source/'analysis'
    records = inventory(base)
    budget = upper_bound(records)
    if budget > MAX_EXPORT_BYTES:
        raise ValueError('Complete ZIP upper bound exceeds 8GiB export budget')
    indexed = {r['path']: r for r in index.get('tables', []) + index.get('artifacts', []) if not excluded(r['path'])}
    if not set(indexed).issubset({r['path'] for r in records}):
        raise ValueError('Indexed scientific file missing from complete export')
    recovering=source!=target
    download_budget=MAX_EXPORT_BYTES if recovering else LEGACY_DOWNLOAD_BYTES
    manifest = dict(schema='scpolya.ui.analysis.export.v2' if recovering else 'scpolya.ui.analysis.export.v1', method=index['method'],
                    export_contract=RECOVERY_CONTRACT if recovering else 'scpolya.ui.analysis.legacy-512m.v1',
                    download_budget_bytes=download_budget,
                    limitations=index.get('limitations', []), files={},
                    excluded_intermediates='SQLite databases and redundant backend ZIPs; full scientific tables and provenance retained',
                    export_budget_bytes=MAX_EXPORT_BYTES, conservative_zip_upper_bound=budget)
    temporary = target/('report.'+uuid.uuid4().hex+'.partial.zip')
    try:
        with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for record in records:
                path = member_path(base, record['path'])
                before = stamp(path); h = hashlib.sha256(); n = 0
                # ZIP64 must be selected BEFORE streaming an entry with unknown size.
                with path.open('rb') as src, archive.open(record['path'], 'w', force_zip64=True) as dst:
                    for block in iter(lambda: src.read(BLOCK), b''):
                        dst.write(block); h.update(block); n += len(block)
                desc = dict(bytes=n, sha256=h.hexdigest())
                if n != record['bytes'] or before != stamp(path):
                    raise ValueError('Output changed while exporting')
                if record['path'] in indexed and any(desc[k] != indexed[record['path']][k] for k in desc):
                    raise ValueError('Indexed scientific file changed while exporting')
                if expected is not None and desc != expected.get(record['path']):
                    raise ValueError('Pinned recovery output changed while exporting')
                manifest['files'][record['path']] = desc
            if expected is not None and set(expected) != set(manifest['files']):
                raise ValueError('Recovery output inventory changed')
            archive.writestr('GUI-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            for code in sorted((source/'code-snapshot').rglob('*')):
                if code.is_symlink():raise ValueError('Code snapshot symlink refused')
                if code.is_file():archive.write(code, 'GUI-CODE/'+str(code.relative_to(source/'code-snapshot')))
            for receipt in sorted(source.glob('*binding.json')):
                if receipt.is_symlink():raise ValueError('Source binding symlink refused')
                archive.writestr('GUI-SOURCE/'+receipt.name, receipt.read_bytes())
            archive.writestr('GUI-TASK.json', (source/'task.json').read_bytes())
            archive.writestr('GUI-PLAN.json', (source/'plan.json').read_bytes())
            if source != target:
                for name in ('preview.json', 'plan.json', 'task.json', 'recovery-verification.json'):
                    archive.writestr('GUI-RECOVERY/'+name, (target/name).read_bytes())
                for code in sorted((target/'code-snapshot').rglob('*')):
                    if code.is_symlink():raise ValueError('Recovery code symlink refused')
                    if code.is_file():archive.write(code, 'GUI-RECOVERY/CODE/'+str(code.relative_to(target/'code-snapshot')))
        if temporary.stat().st_size > download_budget:
            raise ValueError('Complete export exceeds declared download budget')
        with zipfile.ZipFile(temporary) as check:
            if len(check.namelist()) != len(set(check.namelist())):
                raise ValueError('Duplicate ZIP members')
            # Read every member to EOF: zipfile checks its CRC; science bytes also
            # have to match the pinned SHA before a final report can be published.
            for info in check.infolist():
                h=hashlib.sha256();n=0
                with check.open(info) as member:
                    for block in iter(lambda:member.read(BLOCK),b''):
                        h.update(block);n+=len(block)
                if info.filename in manifest['files']:
                    desc=manifest['files'][info.filename]
                    if n!=desc['bytes'] or h.hexdigest()!=desc['sha256']:
                        raise ValueError('Incomplete or changed ZIP member')
        manifest.update(report_sha256=digest(temporary), report_bytes=temporary.stat().st_size)
        # Hard-link publication is atomic and refuses to overwrite an existing report.
        os.link(temporary, target/'report.zip')
        temporary.unlink()
        service.emit(target/'report-manifest.json', manifest)
    except BaseException as exc:
        service.emit(target/('EXPORT-FAILURE-'+uuid.uuid4().hex+'.json'), dict(
            error=str(exc), partial_archive=str(temporary) if temporary.exists() else None,
            time=service.utc_now(), complete=False))
        raise
