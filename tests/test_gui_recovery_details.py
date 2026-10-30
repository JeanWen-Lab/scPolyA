"""Synthetic sealed read fixtures only: no Recover/Start/package/science call."""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import zipfile

import pytest
from scpolya_ui.services import analysis_detail_service as details
from scpolya_ui.services import analysis_json_service as nodes
from scpolya_ui.services import analysis_service as service
from scpolya_ui.services import export_recovery as recovery
from scpolya_ui.services import report_export as exports


@pytest.fixture
def sealed(tmp_path,monkeypatch):
    failed=runpy.run_path(str(Path(__file__).with_name('test_gui_export_recovery.py')))['failed']
    source,index=failed.__wrapped__(tmp_path,monkeypatch)
    rows=[dict(peak_name='site'+str(i),support=dict(n_cells=10+i,pass_true=9+i,pass_false=1,assigned_umis=None),
               reference=dict(query=dict(distance=103,nearest=[dict(position=1234)])),empty={},array=[],literal='NA') for i in range(26)]
    def add(name,value,jsonl=False):
        path=source/'analysis'/name
        raw=(''.join(json.dumps(row)+'\n' for row in value) if jsonl else json.dumps(value)).encode();path.write_bytes(raw)
        desc=dict(path=name,name=Path(name).name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        if jsonl:desc.update(format='jsonl',row_count=len(value));index['tables'].append(desc)
        else:index['artifacts'].append(desc)
        return desc
    table=add('data/primary-all-sorted.jsonl',rows,True)
    shortlist=dict(selection='synthetic only',rows=[dict(stratum='primary' if i<3 else 'risk',source=rows[i]) for i in range(6)])
    add(details.SHORTLIST,shortlist)
    service.emit(source/'analysis/index.json',index)
    binding=recovery.verify_source(source)
    target=service.root()/'A-2222222222222222';target.mkdir(parents=True)
    plan=dict(schema=recovery.SCHEMA+'.plan',method='cs3.candidates',operation='export_only',export_contract=exports.RECOVERY_CONTRACT,binding=binding)
    service.emit(target/'plan.json',plan)
    service.emit(target/'task.json',dict(operation='export_only',method='cs3.candidates',unit='scp-gui-analysis-'+target.name.lower(),plan_sha256=exports.digest(target/'plan.json')))
    service.emit(target/'preview.json',dict(operation='export_only',method='cs3.candidates',backend_plan=plan))
    service.emit(target/'recovery-verification.json',dict(scientific_execution=False,source_verification=binding))
    with zipfile.ZipFile(target/'report.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in binding['files']:archive.write(source/'analysis'/name,name)
        for name in ('task.json','plan.json','preview.json','recovery-verification.json'):archive.write(target/name,'GUI-RECOVERY/'+name)
    manifest=dict(schema='scpolya.ui.analysis.export.v2',method='cs3.candidates',export_contract=exports.RECOVERY_CONTRACT,
                  files=binding['files'],report_sha256=exports.digest(target/'report.zip'),report_bytes=(target/'report.zip').stat().st_size)
    service.emit(target/'report-manifest.json',manifest)
    complete=dict(plan_sha256=exports.digest(target/'plan.json'),files={name:dict(bytes=(target/name).stat().st_size,sha256=exports.digest(target/name)) for name in ('recovery-verification.json','report.zip','report-manifest.json')})
    service.emit(target/'computation-complete.json',complete)
    closed=dict(status='succeeded',closure=dict(origin='exec_stop_post',service_result='success',exit_status='0'))
    service.emit(target/'status.json',closed);service.emit(target/'closure.json',closed)
    return target,source,index,table,rows,shortlist


def test_sealed_jsonl_page_context_qc_distance_and_six_shortlist_values(sealed):
    target,source,index,table,rows,shortlist=sealed
    assert details.read_result(target)==index
    artifacts=details.artifacts(target,index);desc=next(a for a in artifacts if a['path']==table['path'])
    first=details.table_page(target,table);last=details.table_page(target,table,2)
    assert first==rows[:25] and last==rows[25:]
    root=details.node_page(target,desc,page=2);assert root['rows'][0]['pointer']=='/25' and root['total']==26
    support=details.node_page(target,desc,'/0/support')
    assert support['record_ordinal']==0 and support['rows'][3]['kind']=='null'
    assert support['ancestors'][0]['fields']['peak_name']['literal']=='"site0"'
    nearest=details.node_page(target,desc,'/0/reference/query/nearest/0/position')
    assert nearest['rows'][0]['preview']['literal']=='1234'
    short=next(a for a in artifacts if a['path']==details.SHORTLIST)
    assert details.node_page(target,short,'/rows')['total']==6
    assert json.loads(details.download_shortlist(target))==shortlist
    assert recovery.read(source/'status.json')['status']=='failed'


@pytest.mark.parametrize('change',['failed_target','wrong_operation','bad_receipt','bad_manifest','zip_tamper','source_tamper'])
def test_changed_or_unsealed_recovery_refuses(sealed,change):
    target,source,*_=sealed
    details.read_result(target)  # Prime bounded caches; mutation must still refuse.
    if change=='failed_target':service.emit(target/'status.json',dict(status='failed'))
    elif change=='wrong_operation':
        meta=recovery.read(target/'task.json');meta['operation']='analysis';service.emit(target/'task.json',meta)
    elif change=='bad_receipt':service.emit(target/'recovery-verification.json',dict(scientific_execution=True))
    elif change=='bad_manifest':
        value=recovery.read(target/'report-manifest.json');value['files']={};service.emit(target/'report-manifest.json',value)
    elif change=='zip_tamper':
        p=target/'report.zip';raw=p.read_bytes();p.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    elif change=='source_tamper':(source/'worker.log').write_text('changed source')
    with pytest.raises((ValueError,OSError,KeyError)):details.read_result(target)


def test_direct_failed_source_bad_descriptor_pointer_and_page_refuse(sealed):
    target,source,index,table,*_=sealed
    with pytest.raises(ValueError):details.read_result(source)
    desc=next(a for a in details.artifacts(target,index) if a['path']==table['path'])
    for pointer in ('/26','/01','/-1','bad','/0/support/missing','/0/~2'):
        with pytest.raises(ValueError):details.node_page(target,desc,pointer)
    for page in (0,True,1.0,99):
        with pytest.raises(ValueError):details.node_page(target,desc,page=page)
    with pytest.raises(ValueError):details.node_page(target,dict(desc,path='../../outside'))
    with pytest.raises(ValueError):details.node_page(target,dict(desc,sha256='0'*64))


def test_record_types_empty_values_and_table_descriptor_are_exact(sealed):
    target,_,index,table,*_=sealed;desc=next(a for a in details.artifacts(target,index) if a['path']==table['path'])
    record=details.node_page(target,desc,'/0')
    by_key={r['key']:r for r in record['rows']}
    assert by_key['empty']['preview']==dict(kind='object',count=0)
    assert by_key['array']['preview']['literal']=='[]'
    assert by_key['literal']['preview']['literal']=='"NA"'
    assert details.node_page(target,desc,'/0/support/assigned_umis')['rows'][0]['preview']['literal']=='null'
    with pytest.raises(ValueError):details.table_page(target,dict(table,row_count=27))


def test_coherent_truncated_binding_does_not_bypass_full_source_gate(sealed):
    target,source,*_=sealed
    plan=recovery.read(target/'plan.json');only=str(source/'analysis/index.json')
    plan['binding']['pins']={only:plan['binding']['pins'][only]}
    service.emit(target/'plan.json',plan)
    meta=recovery.read(target/'task.json');meta['plan_sha256']=exports.digest(target/'plan.json');service.emit(target/'task.json',meta)
    service.emit(target/'recovery-verification.json',dict(scientific_execution=False,source_verification=plan['binding']))
    complete=recovery.read(target/'computation-complete.json');complete['plan_sha256']=meta['plan_sha256']
    complete['files']['recovery-verification.json']=dict(bytes=(target/'recovery-verification.json').stat().st_size,sha256=exports.digest(target/'recovery-verification.json'))
    service.emit(target/'computation-complete.json',complete)
    with pytest.raises(ValueError,match='full binding'):details.read_result(target)


def test_concurrent_readers_validate_full_binding_once(sealed,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    target,_,index,*_=sealed
    original=recovery.verify_source;calls=[]
    def counted(source):
        calls.append(source)
        return original(source)
    monkeypatch.setattr(recovery,'verify_source',counted)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:details.read_result(target),range(2)))
    assert results==[index,index] and len(calls)==1
