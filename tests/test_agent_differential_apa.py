"""Wrapper contracts, adversarial admission and actual R synthetic checks."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scpolya_agent import differential_apa as api
from scpolya_diff.common import sha256, table
from test_differential_apa import case, write, META_FIELDS, MAN_FIELDS

LIBRARY = os.environ.get("SCPOLYA_DIFF_R_LIBRARY", "/nonexistent/scpolya-r-library")


def request(c, contrast=True, **options):
    root = c['root']
    return dict(schema=api.PREFIX+'request.v1',
                inputs={k:str(root/(k+'.tsv')) for k in ('manifest','metadata','annotation')}, cell_type='CD8T',
                provenance=dict(kind='synthetic',description='Fixed-seed synthetic contract fixture, not real donor inference',
                                metadata_origin='tests/test_differential_apa.py synthetic labels'),
                contrast=dict(control='CTRL',case='CASE') if contrast else None,
                options=dict(min_cells=2,analysis_set='terminal-only',**options),
                runtime=dict(rscript='/usr/bin/Rscript',r_library=LIBRARY))


def make_plan(c, doc=None, name='plan'):
    path = c['root']/(name+'-request.json')
    path.write_text(json.dumps(doc or request(c)))
    return api.plan(path,c['root']/name)


def grant(c, output='run', plan='plan', **updates):
    # Legacy approval file: execute no longer requires or reads it.
    doc=dict(schema=api.PREFIX+'approval.v1',plan_sha256=sha256(c['root']/plan/'plan.json'),
             allowed_actions=['execute'],issued_by='external-test-operator',output_dir=str(c['root']/output))
    doc.update(updates)
    path=c['root']/(output+'-approval.json');path.write_text(json.dumps(doc));return path


@pytest.fixture
def no_runtime(monkeypatch):
    monkeypatch.setattr(api,'runtime_binding',lambda runtime,methods:dict(test_double=True,methods=methods))


def test_plan_actual_preparation_and_eligibility(case,no_runtime):
    c=case;p=make_plan(c)
    assert p['status']=='READY_FOR_EXTERNAL_APPROVAL'
    assert p['eligibility']['donors_per_condition']=={'CTRL':4,'CASE':4}
    assert p['eligibility']['inference_eligible']
    assert p['eligibility']['units_after_declared_coverage_gates']==123
    assert not (c['root']/'plan/analysis').exists()
    counts={r['feature_id']:r for r in table(c['root']/'plan/prepared/pas_counts.tsv')}
    for (peak,sample),value in c['truth'].items():
        if peak in counts: assert int(counts[peak][sample])==value
    doc=json.loads((c['root']/'plan/plan.json').read_text())
    assert {'scpolya_diff/backend.R','scpolya_agent/differential_apa.py'}<=doc['source'].keys()
    with pytest.raises(FileExistsError):make_plan(c)


@pytest.mark.parametrize('mutation',[lambda d:d.pop('provenance'),lambda d:d['inputs'].pop('metadata'),
    lambda d:d['options'].update(paired='false'),lambda d:d['options'].update(min_cells=True),
    lambda d:d.update(contrast=dict(case='CTRL',control='CTRL')),lambda d:d['options'].update(unknown=1),
    lambda d:d['options'].update(timeout_seconds=1201),lambda d:d['inputs'].update(metadata='relative.tsv')])
def test_invalid_requests_preserve_failure(case,mutation):
    d=request(case);mutation(d)
    p=make_plan(case,d);assert p['status']=='FAILED'
    assert json.loads((case['root']/'plan/plan.json').read_text())['status']=='FAILED'


@pytest.mark.parametrize('mode',['empty_donor','duplicate','split','unknown_condition','same_donor','confounded'])
def test_metadata_refusals(case,no_runtime,mode):
    c=case;d=request(c)
    if mode=='empty_donor':c['meta'][0][3]=''
    if mode=='duplicate':c['meta'].append(c['meta'][0])
    if mode=='split':c['meta'][1][2]='ARTIFICIAL'
    if mode=='unknown_condition':d['contrast']['case']='INVENTED'
    if mode=='same_donor':
        for r in c['meta']:r[3]='ONE_DONOR'
    if mode=='confounded':
        for r in c['meta']:r[-1]=r[4]
    write(c['root']/'metadata.tsv',META_FIELDS,c['meta'])
    p=make_plan(c,d);assert p['status']=='FAILED'
    assert p['error']


def single(c):
    c['meta']=[r for r in c['meta'] if r[0] in ('S1','S5')]
    c['manifest']=[r for r in c['manifest'] if r[0] in ('S1','S5')]
    write(c['root']/'metadata.tsv',META_FIELDS,c['meta'])
    write(c['root']/'manifest.tsv',MAN_FIELDS,c['manifest'])


def test_insufficient_replicates_and_no_invented_condition(case,no_runtime):
    c=case;single(c);p=make_plan(c)
    assert p['eligibility']['mode']=='descriptive' and not p['eligibility']['inference_eligible']
    assert p['eligibility']['reason']=='insufficient_replicates'
    c['meta']=[r for r in c['meta'] if r[0]=='S1'];c['manifest']=c['manifest'][:1]
    write(c['root']/'metadata.tsv',META_FIELDS,c['meta']);write(c['root']/'manifest.tsv',MAN_FIELDS,c['manifest'])
    p=make_plan(c,request(c,False),name='one-condition')
    assert p['status']=='PREPARED_ELIGIBILITY_ONLY' and p['eligibility']['observed_conditions']==['CTRL']
    a=grant(c,plan='one-condition')
    with pytest.raises(ValueError,match='not executable'):api.execute(c['root']/'one-condition/plan.json',a,c['root']/'run')


def test_missing_r_dependency(case):
    d=request(case);d['runtime']['rscript']='/nonexistent/Rscript'
    p=make_plan(case,d);assert p['status']=='BLOCKED_RUNTIME' and 'Rscript' in p['error']


def test_paired_design_and_coverage_failure(case,no_runtime):
    c=case
    for row in c['meta']:row[3]=f'D{(int(row[0][1:])-1)%4+1}'
    write(c['root']/'metadata.tsv',META_FIELDS,c['meta'])
    p=make_plan(c,request(c,paired=True))
    assert p['eligibility']['inference_eligible'] and p['eligibility']['residual_df']==3
    p=make_plan(c,request(c,paired=True,min_unit_count=2147483647),name='no-testable-units')
    assert p['status']=='BLOCKED_ELIGIBILITY' and p['eligibility']['units_after_declared_coverage_gates']==0


@pytest.mark.parametrize('change',['raw','prepared','request','source','runtime'])
def test_stale_admission(case,no_runtime,monkeypatch,change):
    c=case;assert make_plan(c)['status']=='READY_FOR_EXTERNAL_APPROVAL'
    a=None;shown=sha256(c['root']/'plan/plan.json')  # the hash shown to the researcher
    if change=='raw':
        with (c['root']/'S1.tsv').open('a') as f:f.write('G000_p\tNEW\t1\n')
    if change=='prepared':
        with (c['root']/'plan/prepared/samples.tsv').open('a') as f:f.write('\n')
    if change=='request':
        # Frozen plan bytes, not a mutable request, bind execution.
        path=c['root']/'plan/plan.json';path.write_text(path.read_text()+' ')
    if change=='source':monkeypatch.setattr(api,'sources',lambda:{})
    if change=='runtime':monkeypatch.setattr(api,'runtime_binding',lambda *args:{})
    with pytest.raises(ValueError):api.execute(c['root']/'plan/plan.json',a,c['root']/'run',shown)
    assert not (c['root']/'run').exists()


@pytest.mark.parametrize('legacy',['none','matching','stale'])
def test_execute_without_approval_records_exact_plan(case,no_runtime,monkeypatch,legacy):
    c=case;make_plan(c)
    a={'none':None,'matching':grant(c),'stale':grant(c,plan_sha256='0'*64,output_dir=str(c['root']/'OTHER'))}[legacy]
    seen=[]
    def fake(args):
        seen.append(args);raise ValueError('stop after admission')
    monkeypatch.setattr(api,'_run',fake)
    outcome=api.execute(c['root']/'plan/plan.json',a,c['root']/'run')
    assert seen and outcome['status']=='FAILED'
    attempt=json.loads((c['root']/'run/attempt.json').read_text())
    assert attempt['plan']['sha256']==sha256(c['root']/'plan/plan.json')
    assert attempt['approval'] is None and attempt['issued_by'] is None


def test_cli_execute_accepts_missing_approval(case,no_runtime,monkeypatch,capsys):
    c=case;make_plan(c)
    monkeypatch.setattr(api,'_run',lambda args:(_ for _ in ()).throw(ValueError('stop')))
    code=api.main(['execute','--plan',str(c['root']/'plan/plan.json'),'--outdir',str(c['root']/'run')])
    doc=json.loads(capsys.readouterr().out)
    assert doc['status']=='FAILED' and code==2 and (c['root']/'run/attempt.json').is_file()


def test_backend_failure_preserved_and_no_retry(case,no_runtime,monkeypatch):
    c=case;make_plan(c);a=None
    def fail(args):raise ValueError('controlled backend failure')
    monkeypatch.setattr(api,'_run',fail)
    outcome=api.execute(c['root']/'plan/plan.json',a,c['root']/'run')
    assert outcome['status']=='FAILED' and outcome['verified']
    with pytest.raises(FileExistsError):api.execute(c['root']/'plan/plan.json',a,c['root']/'run')
    with pytest.raises(ValueError):api.result(c['root']/'run')


def test_isolated_script_help():
    run=subprocess.run([sys.executable,'-I','-B',str(Path(api.__file__)),'--help'],capture_output=True,text=True)
    assert run.returncode==0 and 'execute' in run.stdout


actual=pytest.mark.skipif(not os.environ.get('SCPOLYA_AGENT_DIFF_ACTUAL_R'),reason='Opt in actual bounded R execution')


@actual
def test_actual_missing_r_packages(case):
    c=case;empty=c['root']/'empty-library';empty.mkdir()
    wrapper=c['root']/'base-only-Rscript'
    wrapper.write_text('#!/bin/sh\nR_LIBS_SITE=/nonexistent exec /usr/bin/Rscript "$@"\n')
    wrapper.chmod(0o755)
    d=request(c);d['runtime']=dict(rscript=str(wrapper),r_library=str(empty))
    p=make_plan(c,d)
    assert p['status']=='BLOCKED_RUNTIME' and 'Missing R package' in p['error']


@actual
def test_actual_synthetic_r_and_bounded_verified_pages(case):
    c=case;assert make_plan(c)['status']=='READY_FOR_EXTERNAL_APPROVAL'
    outcome=api.execute(c['root']/'plan/plan.json',None,c['root']/'run')
    # This fixture records batch variation and deliberately omits adjustment.
    assert outcome['status']=='COMPLETE_WITH_WARNINGS'
    warnings=list(table(c['root']/'run/analysis/warnings.tsv'))
    assert any('batch' in r['warning'].lower() for r in warnings)
    backend=list(table(c['root']/'run/analysis/backend_status.tsv'))[0]
    assert backend['untestable_length_units']=='TRUE'
    page=api.result(c['root']/'run',limit=2)
    assert len(page['rows'])==2 and page['next_offset']==2
    assert len(json.dumps(page).encode())<=65536
    summary={r['unit_id']:r for r in table(c['root']/'run/analysis/differential_apa_summary.tsv')}
    assert len(summary)==123 and float(summary['G001|%2B']['edger_FDR'])<.05
    assert float(summary['G001|%2B']['analysis_delta_distal_usage_pp'])>0
    assert summary['G000|%2B']['analysis_delta_distal_usage_pp']=='NA'
    # Re-derive unit distal effect from delivered counts, without usage exports.
    counts=list(table(c['root']/'run/analysis/pas_counts_used.tsv'))
    gene=[r for r in counts if r['unit_id']=='G001|%2B']
    usage={s:next(int(r[s]) for r in gene if r['feature_id']=='G001_d')/sum(int(r[s]) for r in gene) for s in [f'S{i}' for i in range(1,9)]}
    expected=100*(sum(usage[f'S{i}'] for i in range(5,9))/4-sum(usage[f'S{i}'] for i in range(1,5))/4)
    assert abs(float(summary['G001|%2B']['analysis_delta_distal_usage_pp'])-expected)<1e-12
    for kw in [dict(name='../plan.json'),dict(limit=1001),dict(offset=-1),dict(max_bytes=1023)]:
        with pytest.raises(ValueError):api.result(c['root']/'run',**kw)
    p=c['root']/'run/analysis/differential_apa_summary.tsv';p.write_text(p.read_text()+'\n')
    with pytest.raises(ValueError,match='mismatch'):api.result(c['root']/'run')


@actual
def test_actual_insufficient_replicates_has_no_pvalues(case):
    c=case;single(c);assert make_plan(c)['status']=='READY_FOR_EXTERNAL_APPROVAL'
    outcome=api.execute(c['root']/'plan/plan.json',None,c['root']/'run')
    assert outcome['status']=='DESCRIPTIVE_ONLY' and outcome['inference_reason']=='insufficient_replicates'
    page=api.result(c['root']/'run')
    assert not any('pvalue' in k.lower() or 'fdr' in k.lower() for k in page['columns'])
    assert not (c['root']/'run/analysis/edger_units.tsv').exists()
