from pathlib import Path
import hashlib,json,sys
import pytest
from scpolya_ui.services.analysis_source_service import recorded_runtime


@pytest.fixture
def records(tmp_path):
    package=tmp_path/'package';evidence=package/'evidence';evidence.mkdir(parents=True)
    source=tmp_path/'source';source.mkdir();entry=source/'main.py';entry.write_text('# explicit retained source\n')
    root=tmp_path/'native';root.mkdir();metadata=root/'invocation.json';metadata.write_text(json.dumps(dict(argv=[str(entry),'run'])))
    binding=dict(view_root=str(root),config=dict(scientific_key=3,inactive_default=None),invocation=dict(path=str(metadata)))
    def emit(name,value):
        p=evidence/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_text(json.dumps(value));return p
    emit('RUN-IDENTITIES.json',dict(ui_native_commit='a'*40,runs=[dict(qualified_run_root=str(root),config=dict(scientific_key=3),job='J-original')]))
    emit('SESSION.json',dict(ui_commit='a'*40,ui_worktree=str(source)))
    emit('ENVIRONMENT.json',dict(python_executable=sys.executable,native_and_ui_git_commit='a'*40,captured_at_utc='2026-10-26T00:10:00Z',scope='after test execution'))
    emit('arbitrary-folder/GUI-JOB-SPEC.submitted.json',dict(job_id='J-original',run_root=str(root),argv=[sys.executable,str(entry),'run'],cwd=str(source)))
    def seal():
        paths=sorted(evidence.rglob('*.json'))
        (package/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(package))+'\n' for p in paths))
    seal();return package,binding,seal


def test_runtime_is_recorded_descriptor_with_native_only_fields_retained(records):
    package,binding,_=records;result=recorded_runtime(str(package),binding)
    assert result['descriptor']['identity_kind']=='recorded_runtime_descriptor_v1'
    assert result['descriptor']['environment']['scope']=='after test execution'
    assert result['recorded_config_comparison']['native_only_fields']==['inactive_default']
    assert len(result['runtime_sha256'])==64 and result['evidence_pins']
    assert 'not a start-time' in result['descriptor']['limits'][0]


def test_runtime_refuses_wrong_selected_config_even_with_valid_manifest(records):
    package,binding,_=records;binding['config']['scientific_key']=1
    with pytest.raises(ValueError,match='does not bind'):recorded_runtime(str(package),binding)


def test_recorded_source_tamper_and_wrong_argv_refuse(records):
    package,binding,seal=records
    path=package/'evidence/arbitrary-folder/GUI-JOB-SPEC.submitted.json'
    value=json.loads(path.read_text());value['argv'].append('--unrecorded');path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='record changed'):recorded_runtime(str(package),binding)
    seal()
    with pytest.raises(ValueError,match='argv/root'):recorded_runtime(str(package),binding)


def test_unknown_runtime_has_no_current_python_fallback(records):
    package,binding,_=records;(package/'evidence/ENVIRONMENT.json').unlink()
    with pytest.raises((ValueError,FileNotFoundError)):recorded_runtime(str(package),binding)
