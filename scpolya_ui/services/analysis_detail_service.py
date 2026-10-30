"""Read-only, sealed CS3 recovery members; no backend or recovery execution."""
from __future__ import annotations
from collections import OrderedDict
import csv
import hashlib
import io
from itertools import islice
import json
import math
from pathlib import Path
from threading import RLock
import zipfile

from . import analysis_json_service as nodes
from . import analysis_service as service
from . import export_recovery as recovery
from . import report_export as exports

JSONL = {'data/'+name+'.jsonl' for name in (
    'assessment','candidate_universe','primary-all-sorted','risk-all-sorted','reference_rows','labels')}
SHORTLIST = 'data/shortlist.json'
MAX_MEMBER = 256*2**20
MAX_ROW = 2**20
_archives = OrderedDict()
_rows = OrderedDict()
_bindings = OrderedDict()
_lock = RLock()


def _pins(binding):
    pins=binding.get('pins',{})
    if not pins or len(pins)>2048:raise ValueError('Missing bounded recovery source pins')
    for path,pin in pins.items():
        if path!=pin['path'] or Path(path).is_symlink() or exports.stamp(path)!=pin['stat']:
            raise ValueError('Recovery source changed: '+path)


def _archive(target,manifest,complete):
    path=target/'report.zip';stamp=exports.stamp(path)
    expected=complete['files']['report.zip']
    if (path.is_symlink() or stamp[2]!=expected['bytes'] or stamp[2]>exports.MAX_EXPORT_BYTES
            or expected['bytes']!=manifest['report_bytes'] or expected['sha256']!=manifest['report_sha256']):
        raise ValueError('Recovery report seal mismatch')
    key=(str(path),tuple(stamp),expected['sha256'])
    with _lock:
        cached=key in _archives
        if cached:_archives.move_to_end(key)
    if not cached:
        if exports.digest(path)!=expected['sha256'] or exports.stamp(path)!=stamp:
            raise ValueError('Recovery report changed')
        with zipfile.ZipFile(path) as archive:
            names=archive.namelist()
            if len(names)>2048 or len(names)!=len(set(names)):
                raise ValueError('Invalid recovery report inventory')
        with _lock:
            _archives[key]=True
            while len(_archives)>4:_archives.popitem(last=False)
    return path,key


def _member(context,name,maximum):
    expected=context['manifest']['files'].get(name)
    if not expected or expected['bytes']>maximum:raise ValueError('Member exceeds detail reader budget')
    result=bytearray();digest=hashlib.sha256()
    with zipfile.ZipFile(context['archive']) as archive:
        info=archive.getinfo(name)
        if info.file_size!=expected['bytes']:raise ValueError('Recovery member size mismatch')
        with archive.open(info) as stream:
            for block in iter(lambda:stream.read(2**20),b''):
                result.extend(block);digest.update(block)
                if len(result)>maximum:raise ValueError('Member exceeds detail reader budget')
    if len(result)!=expected['bytes'] or digest.hexdigest()!=expected['sha256']:
        raise ValueError('Recovery member changed')
    _stable(context)
    return bytes(result)


def _stable(context):
    if exports.stamp(context['archive'])!=list(context['archive_key'][1]):
        raise ValueError('Recovery report changed during viewing')
    _pins(context['binding'])


def authorize(target):
    target=service.task(target)
    closed=recovery.read(target/'status.json')
    if (closed.get('status')!='succeeded' or recovery.read(target/'closure.json')!=closed
            or closed.get('closure',{}).get('origin')!='exec_stop_post'
            or closed['closure'].get('service_result')!='success' or closed['closure'].get('exit_status')!='0'):
        raise ValueError('Successful authoritative recovery required')
    meta=recovery.read(target/'task.json');plan=recovery.read(target/'plan.json')
    complete=recovery.read(target/'computation-complete.json')
    if (meta.get('operation')!='export_only' or meta.get('method')!='cs3.candidates'
            or meta.get('unit')!='scp-gui-analysis-'+target.name.lower()
            or plan.get('schema')!=recovery.SCHEMA+'.plan' or plan.get('operation')!='export_only'
            or plan.get('export_contract')!=exports.RECOVERY_CONTRACT
            or exports.digest(target/'plan.json')!=meta.get('plan_sha256')
            or complete.get('plan_sha256')!=meta.get('plan_sha256')):
        raise ValueError('Bound CS3 recovery plan required')
    for name in ('recovery-verification.json','report-manifest.json'):
        recovery.file_pin(target/name,complete['files'][name])
    verification=recovery.read(target/'recovery-verification.json')
    if (verification.get('source_verification')!=plan.get('binding')
            or verification.get('scientific_execution') is not False):
        raise ValueError('Recovery verification binding mismatch')
    binding=plan['binding'];_pins(binding)
    binding_key=hashlib.sha256(recovery.canonical(binding)).hexdigest()
    with _lock:
        if binding_key not in _bindings:
            # Serialize the initial reviewed gate even across concurrent clients.
            # Later reads still check every recorded source pin's stat.
            if recovery.verify_source(binding['source_task'])!=binding:
                raise ValueError('Recovered source no longer satisfies its full binding')
            _bindings[binding_key]=True
            while len(_bindings)>4:_bindings.popitem(last=False)
    # This applies the existing SOURCE_ROOT allowlist and original index pin.
    base=service.result_base(target)
    manifest=recovery.read(target/'report-manifest.json')
    if (manifest.get('schema')!='scpolya.ui.analysis.export.v2'
            or manifest.get('export_contract')!=exports.RECOVERY_CONTRACT
            or manifest.get('method')!='cs3.candidates'
            or manifest.get('files')!=binding.get('files')):
        raise ValueError('Recovery manifest differs from successful source binding')
    path,key=_archive(target,manifest,complete)
    with zipfile.ZipFile(path) as archive:
        for name in ('task.json','plan.json','preview.json','recovery-verification.json'):
            info=archive.getinfo('GUI-RECOVERY/'+name)
            if info.file_size>service.MAX_INDEX or archive.read(info)!=(target/name).read_bytes():
                raise ValueError('Recovery receipt differs from sealed report: '+name)
    context=dict(target=target,archive=path,archive_key=key,manifest=manifest,binding=binding)
    source_index=binding['pins'][str(base/'index.json')]
    if any(source_index[k]!=manifest['files']['index.json'][k] for k in ('bytes','sha256')):
        raise ValueError('Source index/report binding mismatch')
    index=nodes._decode(_member(context,'index.json',service.MAX_INDEX))
    if index.get('method')!='cs3.candidates' or index.get('status')!='succeeded':
        raise ValueError('Complete CS3 science index required')
    context['index']=index
    return context


def read_result(target):
    return authorize(target)['index']


def _descriptor(context,name):
    if name not in JSONL|{SHORTLIST,'data/candidates.tsv','data/selection-audit.jsonl'}:
        raise ValueError('Not a supported CS3 detail member')
    matches=[x for x in context['index']['tables']+context['index']['artifacts'] if x['path']==name]
    if len(matches)!=1:raise ValueError('Detail member missing from source index')
    item=matches[0];seal=context['manifest']['files'].get(name)
    if not seal or any(item[k]!=seal[k] for k in ('bytes','sha256')):
        raise ValueError('Detail index/export seal mismatch')
    original=Path(context['binding']['source_task'])/'analysis'/name
    pin=context['binding']['pins'].get(str(original))
    if not pin or any(pin[k]!=seal[k] for k in ('bytes','sha256')):
        raise ValueError('Detail not bound by original source pins')
    if item['bytes']>MAX_MEMBER:raise ValueError('Detail member exceeds bounded viewer')
    return item


def artifacts(target,index):
    context=authorize(target)
    if context['index']!=index:raise ValueError('Result index changed')
    choices=[]
    for name in [SHORTLIST,*sorted(JSONL)]:
        if any(x['path']==name for x in index['tables']+index['artifacts']):
            item=_descriptor(context,name)
            choices.append(dict(path=name,bytes=item['bytes'],sha256=item['sha256'],format='jsonl' if name in JSONL else 'json'))
    return choices


def _lines(context,item,start,count):
    if type(start) is not int or start<0 or type(count) is not int or not 1<=count<=25:
        raise ValueError('Invalid bounded JSONL row request')
    total=item['row_count']
    if type(total) is not int or not 0<=total<=1000000 or start>=max(1,total):
        raise ValueError('JSONL row is out of range')
    keys=[(context['archive_key'],item['path'],i) for i in range(start,min(total,start+count))]
    with _lock:
        cached=[_rows[key] for key in keys] if keys and all(key in _rows for key in keys) else None
    if cached is not None:
        result=[nodes._decode(line) for line in cached];_stable(context);return result
    selected=[];digest=hashlib.sha256();seen=0;size=0
    with zipfile.ZipFile(context['archive']) as archive:
        info=archive.getinfo(item['path'])
        if info.file_size!=item['bytes']:raise ValueError('JSONL member size mismatch')
        with archive.open(info) as stream:
            while True:
                line=stream.readline(MAX_ROW+1)
                if not line:break
                if len(line)>MAX_ROW:raise ValueError('JSONL record exceeds detail budget')
                digest.update(line);size+=len(line)
                if start<=seen<start+count:selected.append((seen,line))
                seen+=1
    if size!=item['bytes'] or digest.hexdigest()!=item['sha256'] or seen!=total:
        raise ValueError('JSONL member hash/count mismatch')
    _stable(context)
    result=[nodes._decode(line) for _,line in selected]
    if any(not isinstance(row,dict) for row in result):raise ValueError('JSONL detail records must be objects')
    with _lock:
        for ordinal,line in selected:
            key=(context['archive_key'],item['path'],ordinal);_rows[key]=line;_rows.move_to_end(key)
        while len(_rows)>32:_rows.popitem(last=False)
    return result


def table_page(target,table,page=1):
    if type(page) is not int or page<1:raise ValueError('Invalid detail table page')
    context=authorize(target);item=_descriptor(context,table['path'])
    if item!=table:raise ValueError('Saved table descriptor changed')
    if table.get('format')=='jsonl':return _lines(context,item,(page-1)*25,25)
    raw=_member(context,item['path'],nodes.MAX_SOURCE)
    return list(islice(csv.DictReader(io.StringIO(raw.decode()),delimiter='\t'),(page-1)*25,page*25))


def node_page(target,artifact,pointer='',page=1):
    if type(page) is not int or page<1:raise ValueError('Invalid detail page')
    if (not isinstance(pointer,str) or len(pointer)>16384 or (pointer and not pointer.startswith('/'))
            or len(pointer.split('/'))>65):raise ValueError('Invalid detail source pointer')
    context=authorize(target);item=_descriptor(context,artifact['path'])
    wanted=dict(path=item['path'],bytes=item['bytes'],sha256=item['sha256'],format='jsonl' if item['path'] in JSONL else 'json')
    if artifact!=wanted:raise ValueError('Select a bound recovery detail member')
    record=None
    if item['path']==SHORTLIST:
        document=nodes._decode(_member(context,SHORTLIST,nodes.MAX_SOURCE))
        value,ancestors=nodes.locate(document,pointer);result=nodes.page_value(value,pointer,page)
    elif not pointer:
        records=_lines(context,item,(page-1)*25,25)
        result=nodes.page_value(records)
        first=(page-1)*25
        for i,row in enumerate(result['rows']):row.update(key=str(first+i),pointer='/'+str(first+i))
        result.update(pointer='',page=page,total=item['row_count'],pages=max(1,math.ceil(item['row_count']/25)))
        ancestors=[]
    else:
        if not pointer.startswith('/'):raise ValueError('Invalid JSONL source pointer')
        ordinal,_,tail=pointer[1:].partition('/')
        if not ordinal.isascii() or not ordinal.isdecimal() or len(ordinal)>10 or str(int(ordinal))!=ordinal:
            raise ValueError('Invalid JSONL row ordinal')
        record=int(ordinal);document=_lines(context,item,record,1)[0]
        value,ancestors=nodes.locate(document,('/'+tail) if tail else '')
        for ancestor in ancestors:ancestor['pointer']='/'+ordinal+ancestor['pointer']
        result=nodes.page_value(value,pointer,page)
    result.update(ancestors=ancestors,source=wanted,record_ordinal=record,
                  recovery_task=context['target'].name,source_task=Path(context['binding']['source_task']).name)
    if len(json.dumps(result,ensure_ascii=False).encode())>2*2**20:raise ValueError('Detail page exceeds display budget')
    _stable(context)
    return result


def download_shortlist(target):
    context=authorize(target);_descriptor(context,SHORTLIST)
    return _member(context,SHORTLIST,nodes.MAX_SOURCE)
