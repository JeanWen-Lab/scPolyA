"""Inert source/plan fixtures; never launch native/systemd work or read scientific inputs."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'native_entry.py'
spec = importlib.util.spec_from_file_location('entry_binding_tests', ENTRY)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def call(root, *args):
    r = subprocess.run([sys.executable, '-I', '-B', str(ENTRY), '--source-root', str(root), *args],
                       capture_output=True, text=True, timeout=30)
    assert 'Traceback' not in r.stderr
    return r, json.loads(r.stdout) if r.stdout.lstrip().startswith('{') else None


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True)


def fixture_root(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    marker = tmp_path / (name + '-IMPORTED')
    for rel in entry.REQUIRED_SOURCE_FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('# inert source fixture\n')
    (root / 'scpolya_ui/command_builder.py').write_text(f'ORIGIN = {str(root)!r}\n')
    (root / 'arguments.py').write_text('import argparse\ndef build_parser():\n p=argparse.ArgumentParser();s=p.add_subparsers(dest="command");r=s.add_parser("run");r.add_argument("--marker",default='+repr(name)+');return p\n')
    (root / 'scpolya_agent/native_task_driver.py').write_text(
        'import json,sys\nfrom pathlib import Path\n'
        'REPO_ROOT=Path(__file__).resolve().parents[1]\nsys.path.insert(0,str(REPO_ROOT))\n'
        'from scpolya_ui.command_builder import ORIGIN\n'
        f'Path({str(marker)!r}).write_text("INERT TEST IMPORT ONLY")\n'
        'PRESETS={"manuscript-v4":{"minpolyA":5}}\n'
        'if __name__=="__main__":print(json.dumps({"schema":"inert.fixture","status":"observed","driver_root":str(REPO_ROOT),"dependency_root":ORIGIN,"args":sys.argv[1:]}))\n')
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Inert Fixture', '-c', 'user.email=inert@example.invalid', 'commit', '-qm', 'fixture')
    return root, marker


def seal(plan):
    identity = plan['runtime_identity']
    identity['identity_sha256'] = hashlib.sha256(entry.canonical({k:v for k,v in identity.items() if k!='identity_sha256'})).hexdigest()
    plan['plan_sha256'] = hashlib.sha256(entry.canonical({k:v for k,v in plan.items() if k!='plan_sha256'})).hexdigest()
    return plan


def fixture_plan(tmp_path, root):
    source, product_git = entry.observed_source_identity(root)
    identity = dict(python={'path':sys.executable},conda_packages_sha256='inert',selected_packages={},tools={},source=source)
    return seal(dict(schema='scpolya.native_task_plan.v1',product={'repo_root':str(root),'git':product_git},
                     python=sys.executable,argv=[sys.executable,str(root/'main.py'),'run'],runtime_identity=identity,
                     status='ready_for_external_approval',job_dir=str(tmp_path/'never-job'),run_root=str(tmp_path/'never-run')))


def files(tmp_path, plan, **approval_changes):
    pp=tmp_path/'plan.json';ap=tmp_path/'approval.json'
    pp.write_text(json.dumps(plan))
    ap.write_text(json.dumps(dict(schema='scpolya.native_task_approval.v1',plan_sha256=plan['plan_sha256'],
                                  allowed_actions=['run','recover'],issued_by='INERT FIXTURE ONLY; NO NATIVE AUTHORITY',**approval_changes)))
    return pp,ap


@pytest.mark.parametrize('verb',['run','recover','status','stop','harvest','snapshot'])
def test_cross_root_refused_before_selected_or_foreign_driver_import(tmp_path,verb):
    a,am=fixture_root(tmp_path,'a');b,bm=fixture_root(tmp_path,'b')
    pp,ap=files(tmp_path,fixture_plan(tmp_path,b))
    args=['driver','--',verb,'--plan',str(pp)]
    if verb=='snapshot':args+=['--label','inert']
    r,d=call(a,*args)
    assert r.returncode==2 and d['error']['code']=='plan_source_root_mismatch'
    assert not am.exists() and not bm.exists() and not (tmp_path/'never-job').exists()


@pytest.mark.parametrize('mutation',['tracked_driver','source_schema','runtime_digest','entrypoint'])
def test_bound_metadata_and_source_negatives_before_import(tmp_path,mutation):
    root,marker=fixture_root(tmp_path,'root');plan=fixture_plan(tmp_path,root)
    if mutation=='source_schema':plan['runtime_identity']['source']={'invented_driver_sha':'bad'};seal(plan)
    if mutation=='runtime_digest':plan['runtime_identity']['identity_sha256']='bad';plan['plan_sha256']=hashlib.sha256(entry.canonical({k:v for k,v in plan.items() if k!='plan_sha256'})).hexdigest()
    if mutation=='entrypoint':plan['argv'][1]=str(tmp_path/'foreign.py');seal(plan)
    pp,ap=files(tmp_path,plan)
    if mutation=='tracked_driver':
        p=root/'scpolya_agent/native_task_driver.py';p.write_text(p.read_text()+'\n# source drift\n')
    r,d=call(root,'driver','--','run','--plan',str(pp))
    expected={'tracked_driver':'plan_source_identity_mismatch','source_schema':'plan_source_identity_mismatch',
              'runtime_digest':'plan_source_identity_invalid','entrypoint':'plan_entrypoint_mismatch'}
    assert r.returncode==2 and d['error']['code']==expected[mutation]
    assert not marker.exists() and not (tmp_path/'never-job').exists()


@pytest.mark.parametrize('approval',['none','legacy','legacy_mismatched'])
@pytest.mark.parametrize('equals',[False,True])
def test_matching_inert_run_routes_once_without_native_attempt(tmp_path,equals,approval):
    # No approval file is required; a legacy --approval is accepted and ignored, even if stale.
    root,marker=fixture_root(tmp_path,'root');pp,ap=files(tmp_path,fixture_plan(tmp_path,root))
    if approval=='legacy_mismatched':
        d=json.loads(ap.read_text());d['plan_sha256']='wrong';ap.write_text(json.dumps(d))
    options=[f'--plan={pp}'] if equals else ['--plan',str(pp)]
    if approval!='none':options+=[f'--approval={ap}'] if equals else ['--approval',str(ap)]
    r,d=call(root,'driver','--','run',*options)
    assert r.returncode==0 and d['driver_root']==d['dependency_root']==str(root) and marker.exists()
    assert not (tmp_path/'never-job').exists() and not (tmp_path/'never-run').exists()


@pytest.mark.parametrize('verb',['run','recover'])
def test_readonly_help_has_no_execution_prerequisites(verb):
    r,d=call(ROOT,'driver','--',verb,'--help')
    assert r.returncode==0 and 'usage:' in r.stdout and '--approval' in r.stdout


@pytest.mark.parametrize('case',['source_missing','plan_missing','malformed','directory','duplicate','unknown'])
def test_structured_driver_path_and_argument_failures(tmp_path,case):
    root=ROOT;plan=tmp_path/'absent.json';args=['driver','--','status','--plan',str(plan)]
    if case=='source_missing':root=tmp_path/'missing-root'
    if case=='malformed':plan.write_text('{broken')
    if case=='directory':plan.mkdir()
    if case=='duplicate':args+=['--plan',str(plan)]
    if case=='unknown':args+=['--surprise']
    r,d=call(root,*args)
    assert r.returncode==2 and d['status']=='error' and 'code'in d['error']
    assert r.stderr==''


def test_actual_native_source_schema_matches_independent_admission():
    r,d=call(ROOT,'driver','--','identity','--python',sys.executable,'--repo-root',str(ROOT))
    assert r.returncode==0
    observed,_=entry.observed_source_identity(ROOT)
    assert observed==d['source']


def test_public_inprocess_calls_use_fresh_root_dependencies(tmp_path):
    a,am=fixture_root(tmp_path,'a');b,bm=fixture_root(tmp_path,'b')
    values=[]
    for root in (a,b):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert entry.main(['--source-root',str(root),'settings'])==0
        values.append(json.loads(out.getvalue())['parser_defaults']['marker'])
    assert values==['a','b'] and am.exists() and bm.exists()


def test_literal_skill_commands_with_inert_missing_metadata(tmp_path):
    commands=[]
    for skill in (ROOT/'skills').glob('*/SKILL.md'):
        text=skill.read_text()
        found=re.findall(r'"\$PY" -I -B "\$SRC/native_entry.py"[^`\n]+',text)
        assert found,skill
        for command in found:
            args=shlex.split(command.replace('$PY',sys.executable).replace('$SRC',str(ROOT)))
            args=[str(tmp_path/x) if x in ('ABS_REQUEST','ABS_PLAN','ABS_APPROVAL') else x for x in args]
            r=subprocess.run(args,capture_output=True,text=True,timeout=30)
            d=json.loads(r.stdout)
            assert 'unrecognized arguments' not in r.stderr and 'Traceback' not in r.stderr
            if 'ABS_' not in command:assert r.returncode==0
            else:assert r.returncode==2 and d['status']=='error'
            commands.append(command)
    assert len(commands)>=8


