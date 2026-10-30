"""Export-only recovery controls use isolated synthetic closed failures, never science."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile
import pytest
from scpolya_ui.services import analysis_service as service, export_recovery as recovery, report_export as exports


@pytest.fixture
def failed(tmp_path,monkeypatch):
    source_root=tmp_path/'original';source=source_root/'A-1111111111111111';(source/'analysis/data').mkdir(parents=True)
    monkeypatch.setenv('SCPOLYA_UI_EXPORT_SOURCE_ROOT',str(source_root))
    monkeypatch.setenv('SCPOLYA_UI_ANALYSIS_ROOT',str(tmp_path/'recovery'))
    monkeypatch.setenv('SCPOLYA_UI_HOME',str(tmp_path/'ui-home'))
    code=service.code_sources()
    for c in code:
        dest=source/'code-snapshot'/c['relative_path'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(c['path'],dest)
    inp=tmp_path/'input.tsv';inp.write_text('synthetic input\n')
    input_pin=recovery.file_pin(inp);input_pin.pop('stat')
    plan=dict(schema='scpolya.gui.cs3.v1.plan',method='cs3.candidates',status='ready',
              request=dict(mode='synthetic',inputs=dict(counts=input_pin)),
              method_sources=[recovery.file_pin(Path(c['path'])) for c in code if c['relative_path']=='scpolya_ui/analysis/cs3_backend/api.py'],
              input_sources=[dict(role='counts',**input_pin)],scope=dict(population='synthetic export fixture'),parameters={},limitations=['Synthetic fixture only'],
              runtime=dict(executable=recovery.file_pin(Path(sys.executable).resolve())))
    plan['plan_sha256']=hashlib.sha256(recovery.canonical(plan)).hexdigest()
    for name in ('plan.json','analysis/PLAN.json'):service.emit(source/name,plan)
    table=source/'analysis/data/candidates.tsv';table.write_text('site\tvalue\nsynthetic-site\tNA\n')
    desc=dict(path='data/candidates.tsv',name='candidates.tsv',format='tsv',bytes=table.stat().st_size,sha256=exports.digest(table),row_count=1)
    index=dict(schema='scpolya.gui.cs3.v1.result',method=plan['method'],status='succeeded',tables=[desc],artifacts=[],summary={'fixture':True},
               **{k:plan[k] for k in ('method_sources','input_sources','parameters','scope','limitations')})
    service.emit(source/'analysis/index.json',index);service.emit(source/'analysis/status.json',{'status':'succeeded'})
    service.emit(source/'preview.json',dict(task_root=str(source),backend_plan=plan,gui_sources=code,workspace_id='W-fixture',selected_runs=[],method='cs3.candidates'))
    service.emit(source/'task.json',dict(method='cs3.candidates',root=str(source_root),ui_home=str(tmp_path/'old-home'),unit='scp-gui-analysis-'+source.name.lower(),
                 plan_sha256=exports.digest(source/'plan.json'),code_sources=code,worker_sha256=exports.digest(service.WORKER),
                 analysis_python=dict(path=str(Path(sys.executable).resolve()),sha256=exports.digest(Path(sys.executable).resolve()))))
    closed=dict(status='failed',error=recovery.LEGACY_ERROR,closure=dict(origin='exec_stop_post',service_result='exit-code',exit_status='1'))
    for name in ('status.json','closure.json'):service.emit(source/name,closed)
    service.emit(source/'REFUSAL.json',dict(type='RuntimeError',reason=recovery.LEGACY_ERROR))
    (source/'worker.log').write_text('Synthetic fixture: packaging force_zip64 failure\n')
    (source/'report.zip').write_bytes(b'not a zip: retained damaged fixture')
    return source,index


def setup_attempt(preview):
    target=Path(preview['task_root']);service.emit(target/'plan.json',preview['backend_plan'])
    service.emit(target/'task.json',dict(method=preview['method'],operation='export_only'))
    return target


def test_export_recovery_preserves_failed_bytes_and_reads_result(failed,monkeypatch):
    source,index=failed
    before={str(p.relative_to(source)):exports.digest(p) for p in source.rglob('*') if p.is_file()}
    preview=recovery.preview(source);target=setup_attempt(preview)
    # A backend execution call is a test failure, including accidental import routes.
    from scpolya_ui.analysis import dispatcher
    monkeypatch.setattr(dispatcher,'execute',lambda *a,**k:pytest.fail('science execution forbidden'))
    recovery.execute(target,preview['backend_plan'])
    service.emit(target/'status.json',dict(status='succeeded'))
    assert not (target/'analysis').exists()
    assert before=={str(p.relative_to(source)):exports.digest(p) for p in source.rglob('*') if p.is_file()}
    service.emit(target/'status.json',dict(status='succeeded'))
    with zipfile.ZipFile(service.download_path(target)) as z:
        assert z.testzip() is None
        assert z.read('data/candidates.tsv')==(source/'analysis/data/candidates.tsv').read_bytes()
        assert json.loads(z.read('GUI-TASK.json'))['root']==str(source.parent)
        assert json.loads(z.read('GUI-RECOVERY/task.json'))['operation']=='export_only'
        manifest=json.loads(z.read('GUI-MANIFEST.json'))
        for name,desc in manifest['files'].items():
            assert hashlib.sha256(z.read(name)).hexdigest()==desc['sha256']
    assert service.table_page(target,index['tables'][0])[0]['site']=='synthetic-site'
    assert service.read_result(target)['summary']==index['summary']
    assert recovery.read(source/'status.json')['status']=='failed'


@pytest.mark.parametrize('change',['missing_index','incomplete_science','wrong_index_hash','wrong_input','wrong_code','wrong_plan','wrong_failure','extra_data','escape','symlink_directory'])
def test_fail_closed_source_controls(failed,change,tmp_path):
    source,index=failed
    if change=='missing_index':(source/'analysis/index.json').unlink()
    elif change=='incomplete_science':service.emit(source/'analysis/status.json',dict(status='running'))
    elif change=='wrong_index_hash':
        index['tables'][0]['sha256']='0'*64;service.emit(source/'analysis/index.json',index)
    elif change=='wrong_input':Path(recovery.read(source/'plan.json')['input_sources'][0]['path']).write_text('changed')
    elif change=='wrong_code':(source/'code-snapshot/scpolya_ui/services/analysis_worker.py').write_text('changed')
    elif change=='wrong_plan':service.emit(source/'analysis/PLAN.json',{})
    elif change=='wrong_failure':service.emit(source/'REFUSAL.json',dict(type='RuntimeError',reason='science failure'))
    elif change=='extra_data':(source/'analysis/data/unindexed.tsv').write_text('extra')
    elif change=='escape':
        index['tables'][0]['path']='../../outside';service.emit(source/'analysis/index.json',index)
    elif change=='symlink_directory':
        outside=tmp_path/'elsewhere';outside.mkdir();(outside/'file').write_text('x');(source/'analysis/escape').symlink_to(outside,target_is_directory=True)
    with pytest.raises((ValueError,OSError,KeyError)):recovery.preview(source)
    assert not service.recent()


def test_preview_consumes_no_science_and_source_mutation_refuses(failed):
    source,_=failed;preview=recovery.preview(source);target=setup_attempt(preview)
    (source/'worker.log').write_text('modified after preview')
    with pytest.raises(ValueError,match='changed after'):recovery.execute(target,preview['backend_plan'])
    assert not (target/'report.zip').exists()


def test_independent_root_and_source_allowlist(failed,monkeypatch,tmp_path):
    source,_=failed
    monkeypatch.setenv('SCPOLYA_UI_ANALYSIS_ROOT',str(source.parent))
    with pytest.raises(ValueError,match='independent'):recovery.preview(source)
    with pytest.raises(ValueError,match='outside'):recovery.source_task(tmp_path/source.name)


def test_small_export_failure_retains_diagnostic_without_publishing(failed,monkeypatch):
    source,index=failed;preview=recovery.preview(source);target=setup_attempt(preview)
    index['tables'][0]['sha256']='0'*64
    with pytest.raises(ValueError,match='scientific file changed'):exports.package(target,index,source=source)
    assert not (target/'report.zip').exists() and not (target/'report-manifest.json').exists()
    assert list(target.glob('EXPORT-FAILURE-*.json')) and list(target.glob('*.partial.zip'))


def test_conservative_budget_refuses_before_partial_file(failed,monkeypatch):
    source,index=failed;preview=recovery.preview(source);target=setup_attempt(preview)
    monkeypatch.setattr(exports,'MAX_EXPORT_BYTES',1)
    with pytest.raises(ValueError,match='upper bound'):exports.package(target,index,source=source)
    assert not list(target.glob('*.zip'))


def test_streaming_download_exceeds_legacy_memory_cap_without_read_bytes(failed,monkeypatch):
    source,_=failed;preview=recovery.preview(source);target=setup_attempt(preview)
    recovery.execute(target,preview['backend_plan']);service.emit(target/'status.json',dict(status='succeeded'))
    path=target/'report.zip'
    with path.open('wb') as f:f.truncate(513*2**20)
    manifest=recovery.read(target/'report-manifest.json')
    manifest.update(report_sha256=exports.digest(path),report_bytes=path.stat().st_size)
    service.emit(target/'report-manifest.json',manifest)
    original=Path.read_bytes
    def bounded(self):
        if self==path:pytest.fail('streaming path must not use read_bytes')
        return original(self)
    monkeypatch.setattr(Path,'read_bytes',bounded)
    assert service.download_path(target)==path
    with pytest.raises(ValueError,match='In-memory'):service.download_bytes(target)
    manifest['schema']='scpolya.ui.analysis.export.v1';service.emit(target/'report-manifest.json',manifest)
    with pytest.raises(ValueError,match='declared budget'):service.download_path(target)


@pytest.mark.skipif(__import__('os').environ.get('SCPOLYA_TEST_LARGE_EXPORT')!='1',reason='Explicit bounded >4GiB sparse export test')
def test_real_zip64_member_above_four_gib(tmp_path,monkeypatch):
    monkeypatch.setenv('SCPOLYA_UI_ANALYSIS_ROOT',str(tmp_path))
    target=tmp_path/'A-3333333333333333';(target/'analysis').mkdir(parents=True)
    path=target/'analysis/large.bin'
    with path.open('wb') as handle:handle.truncate(4*2**30+31)
    expected=exports.digest(path)
    desc=dict(path='large.bin',bytes=path.stat().st_size,sha256=expected)
    service.emit(target/'task.json',dict(fixture=True));service.emit(target/'plan.json',dict(fixture=True))
    exports.package(target,dict(method='cs3.candidates',tables=[],artifacts=[desc]))
    with zipfile.ZipFile(service.download_path(target)) as archive:
        info=archive.getinfo(path.name)
        assert info.file_size==4*2**30+31 and info.extract_version>=45
        h=hashlib.sha256();n=0
        with archive.open(path.name) as member:
            for block in iter(lambda:member.read(2**20),b''):h.update(block);n+=len(block)
        assert h.hexdigest()==expected and n==desc['bytes']
        assert archive.testzip() is None
    print(json.dumps(dict(zip64_member_bytes=n,member_sha256=expected,zip_bytes=(target/'report.zip').stat().st_size,readback='PASS')))


def test_existing_report_never_overwritten(failed):
    source,index=failed;before=(source/'report.zip').read_bytes()
    # A separate root is mandatory for recovery, and package itself also refuses overwrite.
    from unittest.mock import patch
    with patch.object(service,'task',lambda p:p):
        with pytest.raises(ValueError,match='Existing report'):exports.package(source,index)
    assert (source/'report.zip').read_bytes()==before


def test_source_sqlite_verified_but_excluded_from_report(failed):
    source,index=failed;path=source/'analysis/data/intermediate.sqlite';path.write_bytes(b'synthetic sqlite payload')
    index['artifacts'].append(dict(path='data/intermediate.sqlite',bytes=path.stat().st_size,sha256=exports.digest(path)))
    service.emit(source/'analysis/index.json',index)
    value=recovery.preview(source);target=setup_attempt(value)
    recovery.execute(target,value['backend_plan'])
    with zipfile.ZipFile(target/'report.zip') as archive:
        assert 'data/intermediate.sqlite' not in archive.namelist()
    assert str(path) in value['backend_plan']['binding']['pins']
    path.write_bytes(b'bad sqlite hash')
    with pytest.raises(ValueError,match='hash/size'):recovery.preview(source)
