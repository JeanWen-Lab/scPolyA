"""Bounded, lossless navigation of JSON artifacts from a sealed analysis export."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path

MAX_SOURCE = 32 * 2**20
PAGE_SIZE = 25
STRING_PAGE = 4096


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


def _decode(raw):
    return json.loads(raw, object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))


def _read(path, expected, maximum):
    before = path.stat()
    if path.is_symlink() or before.st_size > maximum or before.st_size != expected['bytes']:
        raise ValueError('Saved JSON size or file kind changed')
    raw = path.read_bytes()
    frame = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if frame(before) != frame(path.stat()) or hashlib.sha256(raw).hexdigest() != expected['sha256']:
        raise ValueError('Saved JSON changed')
    return _decode(raw)


def _index(target):
    from . import analysis_service as service
    target = service.task(target)
    if service.status(target)['status'] != 'succeeded':
        raise ValueError('No successful analysis result')
    manifest_path = target / 'report-manifest.json'
    if manifest_path.is_symlink() or manifest_path.stat().st_size > service.MAX_INDEX:
        raise ValueError('Invalid saved export manifest')
    manifest = _decode(manifest_path.read_bytes())
    expected = manifest['files']['index.json']
    index = _read(service.output_path(target, 'index.json'), expected, service.MAX_INDEX)
    return index, manifest


def artifacts(target: Path, index: dict) -> list[dict]:
    """Index membership and export pins are checked before offering any artifact."""
    saved, manifest = _index(target)
    if index != saved:
        raise ValueError('Result index changed')
    choices = []
    seen = set()
    for item in saved.get('artifacts', []):
        if not item['path'].endswith('.json'):
            continue
        if item['path'] in seen:
            raise ValueError('Duplicate JSON artifact path')
        seen.add(item['path'])
        seal = manifest['files'].get(item['path'])
        if not seal or any(item[key] != seal[key] for key in ('bytes', 'sha256')):
            raise ValueError('JSON artifact differs from saved export')
        choices.append(dict(path=item['path'], bytes=item['bytes'], sha256=item['sha256']))
    return choices


def document(target: Path, artifact: dict):
    from . import analysis_service as service
    index, _ = _index(target)
    allowed = artifacts(target, index)
    if artifact not in allowed:
        raise ValueError('Select a JSON artifact from this saved result')
    return _read(service.output_path(target, artifact['path']), artifact, MAX_SOURCE)


def token(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def locate(document, pointer):
    if not isinstance(pointer, str) or len(pointer) > 16384 or (pointer and not pointer.startswith('/')):
        raise ValueError('Invalid JSON source pointer')
    parts = pointer.split('/')[1:] if pointer else []
    if len(parts) > 64:
        raise ValueError('JSON path exceeds navigation budget')
    value = document
    ancestors = []
    current = ''
    for part in parts:
        if any(part[i:i+2] not in ('~0', '~1') for i, char in enumerate(part) if char == '~'):
            raise ValueError('Invalid JSON pointer escape')
        key = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict):
            context = {};count=0
            for k,v in value.items():
                item=preview(v)
                if 'literal' in item:
                    count+=1
                    if len(context)<20:context[k]=item
            if context:
                ancestors.append(dict(pointer=current, fields=context,context_fields=count))
            if key not in value:raise ValueError('JSON source pointer field does not exist')
            value = value[key]
        elif isinstance(value, list):
            if not key.isascii() or not key.isdecimal() or len(key) > 10 or str(int(key)) != key:
                raise ValueError('Invalid JSON array index')
            if int(key)>=len(value):raise ValueError('JSON source pointer index is out of range')
            value = value[int(key)]
        else:
            raise ValueError('Cannot descend into a JSON scalar')
        current += '/' + part
    return value, ancestors


def kind(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, str):
        return 'string'
    return 'number'


def preview(value):
    """Collections/long scalars advertise navigation, never truncated fake values."""
    if isinstance(value,list) and len(value)<=16 and all(not isinstance(v,(dict,list)) for v in value):
        literal=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if len(literal)<=384:return dict(literal=literal,kind='array')
    if isinstance(value, (dict, list)):
        return dict(kind=kind(value), count=len(value))
    if isinstance(value,str) and len(value)>384:
        return dict(kind='string',count=len(value))
    literal = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(literal) > 384:
        return dict(kind=kind(value), count=len(value) if isinstance(value, str) else None)
    return dict(literal=literal, kind=kind(value))


def page_value(value, pointer='', page=1):
    """Pure projection; all children have exact source pointers and remain reachable."""
    if type(page) is not int or page < 1:
        raise ValueError('Invalid JSON page')
    node_kind = kind(value)
    start = (page - 1) * PAGE_SIZE
    rows = []
    fields = []
    if isinstance(value, list):
        count = len(value)
        selected = value[start:start + PAGE_SIZE]
        if selected and all(isinstance(row, dict) for row in selected):
            fields = list(dict.fromkeys(key for row in selected for key in row))
            if len(fields) > 40:
                fields = []
        for ordinal, item in enumerate(selected, start):
            rows.append(dict(key=str(ordinal), pointer=pointer+'/'+str(ordinal), kind=kind(item),
                             preview=preview(item), values={key: preview(item[key]) for key in fields if key in item}))
    elif isinstance(value, dict):
        count = len(value)
        for key in list(value)[start:start + PAGE_SIZE]:
            item = value[key]
            rows.append(dict(key=key, pointer=pointer+'/'+token(key), kind=kind(item), preview=preview(item), values={}))
    elif isinstance(value, str) and len(value) > STRING_PAGE:
        # Long strings have character offsets rather than being silently clipped.
        count = math.ceil(len(value) / STRING_PAGE)
        if page>count:raise ValueError('JSON page is out of range')
        offset = (page - 1) * STRING_PAGE
        rows = [dict(key=str(offset)+':'+str(min(len(value), offset+STRING_PAGE)), pointer=pointer,
                     kind='string', preview=dict(literal=json.dumps(value[offset:offset+STRING_PAGE],ensure_ascii=False),kind='string'), values={})] if offset < len(value) else []
        return dict(kind=node_kind, pointer=pointer, page=page, total=len(value), pages=count,
                    fields=[], rows=rows, unit='characters', page_size=STRING_PAGE)
    else:
        count = 1
        if page == 1:
            rows = [dict(key='', pointer=pointer, kind=node_kind,
                         preview=dict(literal=json.dumps(value,ensure_ascii=False,allow_nan=False),kind=node_kind),values={})]
    pages=max(1,math.ceil(count/PAGE_SIZE))
    if page>pages:raise ValueError('JSON page is out of range')
    return dict(kind=node_kind, pointer=pointer, page=page, total=count, pages=pages,
                fields=fields, rows=rows, unit='entries', page_size=PAGE_SIZE)


def node_page(target: Path, artifact: dict, pointer='', page=1):
    value, ancestors = locate(document(target, artifact), pointer)
    result = page_value(value, pointer, page)
    # Ancestral scalar and short-array keys preserve cohort/stratum/metric context.
    # Full ancestors remain available using parent/root navigation.
    result['ancestors'] = ancestors
    result['source'] = artifact
    if len(json.dumps(result,ensure_ascii=False).encode()) > 2*2**20:
        raise ValueError('JSON page exceeds display budget; download the original artifact')
    return result
