import copy
import hashlib
import json

import pytest
from scpolya_ui.services import analysis_json_service as reader
from scpolya_ui.services import analysis_service as service


@pytest.fixture
def saved(tmp_path,monkeypatch):
    monkeypatch.setenv('SCPOLYA_UI_ANALYSIS_ROOT',str(tmp_path/'tasks'))
    target=service.root()/'A-1234567890abcdef';(target/'analysis').mkdir(parents=True)
    value=[dict(cohort='stratum/'+str(i),value=None if i%2 else 0,flag=bool(i%2),literal='NA',
                transitions=[dict(n=i,qualifies=False)],**({'extra':'retained'} if i==24 else {})) for i in range(51)]
    path=target/'analysis/transitions.json';path.write_text(json.dumps(value))
    item=dict(path=path.name,bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    index=dict(method='cs2.qc_compare',tables=[],artifacts=[item],summary={},limitations=[])
    service.emit(target/'analysis/index.json',index)
    service.emit(target/'task.json',dict(root=str(service.root()),method='cs2.qc_compare'))
    service.emit(target/'plan.json',dict(schema='synthetic_json_reader_fixture'))
    service.emit(target/'status.json',dict(status='succeeded',origin='synthetic_fixture'))
    service.package_report(target,index)
    return target,item,index,value


def test_sealed_artifacts_pagination_types_and_nested_context(saved):
    target,item,index,value=saved
    assert reader.artifacts(target,index)==[item]
    first=reader.node_page(target,item);second=reader.node_page(target,item,page=2);last=reader.node_page(target,item,page=3)
    assert [len(x['rows']) for x in (first,second,last)]==[25,25,1]
    assert first['pages']==3 and first['rows'][0]['pointer']=='/0' and last['rows'][0]['pointer']=='/50'
    assert 'extra' in first['fields'] and 'extra' not in first['rows'][0]['values']
    assert first['rows'][0]['values']['value']['literal']=='0'
    assert first['rows'][1]['values']['value']['literal']=='null'
    assert first['rows'][1]['values']['flag']['literal']=='true'
    assert first['rows'][1]['values']['literal']['literal']=='"NA"'
    nested=reader.node_page(target,item,'/24/transitions')
    assert nested['rows'][0]['values']['n']['literal']=='24'
    assert nested['ancestors'][0]['fields']['cohort']['literal']=='"stratum/24"'
    doc=reader.document(target,item)
    for i in range(51):assert reader.locate(doc,'/'+str(i))[0]==value[i]


def test_unlisted_path_or_wrong_pin_refuses(saved):
    target,item,index,_=saved
    for wrong in [dict(item,path='../outside.json'),dict(item,sha256='0'*64)]:
        with pytest.raises(ValueError,match='Select a JSON artifact'):reader.node_page(target,wrong)
    changed=copy.deepcopy(index);changed['artifacts'][0]['bytes']+=1
    with pytest.raises(ValueError,match='index changed'):reader.artifacts(target,changed)


def test_source_index_and_export_pin_tampering_refuse(saved):
    target,item,index,_=saved
    path=target/'analysis'/item['path'];original=path.read_bytes();path.write_bytes(original.replace(b'NA',b'NB'))
    with pytest.raises(ValueError,match='JSON changed'):reader.node_page(target,item)
    with pytest.raises(ValueError,match='export changed'):service.download_bytes(target,item['path'])
    path.write_bytes(original)
    (target/'analysis/index.json').write_text(json.dumps(dict(index,extra='changed')))
    with pytest.raises(ValueError,match='JSON size|JSON changed'):reader.node_page(target,item)


def test_export_membership_required_even_if_index_itself_sealed(saved):
    target,item,index,_=saved
    path=target/'report-manifest.json';manifest=json.loads(path.read_text());manifest['files'][item['path']]['sha256']='0'*64;path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='differs from saved export'):reader.artifacts(target,index)


def test_source_budget_and_failed_task_refuse(saved,monkeypatch):
    target,item,_,_=saved;monkeypatch.setattr(reader,'MAX_SOURCE',10)
    with pytest.raises(ValueError,match='size'):reader.node_page(target,item)
    service.emit(target/'status.json',dict(status='failed'))
    with pytest.raises(ValueError,match='successful'):reader.node_page(target,item)


def test_pointer_escaping_arbitrary_structures_and_long_string_pages():
    value={'a/b':{'~key':[None,False,'NA',0,0.12345678901234568]}}
    root=reader.page_value(value);assert root['rows'][0]['pointer']=='/a~1b'
    node,_=reader.locate(value,'/a~1b/~0key');assert node==value['a/b']['~key']
    assert [r['preview']['literal'] for r in reader.page_value(node)['rows']]==['null','false','"NA"','0','0.12345678901234568']
    for pointer in ['/a~2b','/a~1b/~0key/-1','/a~1b/~0key/01']:
        with pytest.raises((ValueError,KeyError)):reader.locate(value,pointer)
    text='literal"\n'*1200
    parts=[reader.page_value(text,page=i) for i in range(1,4)]
    assert ''.join(json.loads(p['rows'][0]['preview']['literal']) for p in parts)==text
    assert all(p['unit']=='characters' for p in parts)
    with pytest.raises(ValueError):reader.page_value([],page=True)
    with pytest.raises(ValueError,match='Duplicate'):reader._decode(b'{"n":1,"n":2}')
    with pytest.raises(ValueError,match='Non-finite'):reader._decode(b'{"n":NaN}')


def test_empty_containers_null_bad_pages_and_bad_pointer_are_explicit():
    for empty in ([],{}):
        result=reader.page_value(empty);assert result['rows']==[] and result['total']==0 and result['pages']==1
    null=reader.page_value(None);assert null['total']==1 and null['rows'][0]['preview']['literal']=='null'
    for value in ([],{},None,[0],{'x':1},'x'*5000):
        with pytest.raises(ValueError,match='out of range'):reader.page_value(value,page=99)
    for pointer in ('/missing','/x/5','/x/-1','/x/00','/x/~2'):
        with pytest.raises(ValueError):reader.locate({'x':[1]},pointer)


def test_source_symlink_is_refused_even_with_matching_bytes(saved,tmp_path):
    target,item,_,_=saved;path=target/'analysis'/item['path']
    outside=tmp_path/'outside.json';outside.write_bytes(path.read_bytes());path.unlink();path.symlink_to(outside)
    with pytest.raises(ValueError,match='Invalid result file path'):reader.node_page(target,item)


def test_each_rendered_node_is_bounded_without_expanding_large_collections():
    value=[dict(stratum=['gene','+'],records=list(range(100000))) for _ in range(60)]
    view=reader.page_value(value);assert len(view['rows'])==25 and view['pages']==3
    assert view['rows'][0]['values']['records']==dict(kind='array',count=100000)
    assert view['rows'][0]['values']['stratum']['literal']=='["gene", "+"]'
    assert len(json.dumps(view))<20000
    _,ancestors=reader.locate(value,'/0/records')
    assert ancestors[-1]['fields']['stratum']['literal']=='["gene", "+"]'
