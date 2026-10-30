import copy
import json
from pathlib import Path
import pytest
from scpolya_ui.analysis.cs2_backend import api
from scpolya_ui.analysis.cs2_backend.tests.fixtures import make


def test_fresh_fixture_and_masks(tmp_path):
    r=make(tmp_path)
    p=api.plan(r)
    assert p['status']=='ready' and not Path(r['output_dir']).exists()
    result=api.execute(p,r['output_dir'])
    assert result['status']=='succeeded'
    out=Path(r['output_dir'])
    parity=json.loads((out/'masking/unmasked-parity.json').read_text())
    assert parity['status']=='PASS'
    measurements=json.loads((out/'masking/measurements.json').read_text())
    assert len(measurements)==324
    assert result['summary']['masking']['transition_cells']==144
    assert result['summary']['descriptive']['site_rows']=={'coverage3':5,'coverage1':6}
    assert result['tables'] and all(x['sha256'] for x in result['tables'])
    # New-data numerators: original cellA has 2+3=5 UMIs; A-rich deletion
    # removes the second plus site and cannot create terminal eligibility.
    unmasked=next(x for x in measurements if x['arm']=='coverage3' and x['mode']=='unmasked'
        and x['definition']=='native_terminal' and x['cohort']=='full_registry'
        and x['gene_subset']=='all_eligible' and x['site_identity_rule']=='native_peak_record' and x['umi_cutoff']==5)
    assert unmasked['qualifying_gene_strand_cell_units']==2
    assert (out/'qc/coverage3/chr1/sites.tsv').is_file()


def test_multi_axis_and_missing_registry_refuse(tmp_path):
    r=make(tmp_path)
    r['arms']['coverage1']['config']['misprimed_ratio_threshold']=0.8
    with pytest.raises(ValueError,match='exactly the selected'): api.plan(r)
    r=make(tmp_path/'other')
    Path(r['registry']['path']).write_text('barcode\tcell_type\nx\tT\n')
    r['registry']['sha256']=api.digest(r['registry']['path'])
    for arm in api.ARMS:r['arms'][arm]['identity']['registry_sha256']=r['registry']['sha256']
    with pytest.raises(ValueError,match='registry schema'):api.plan(r)


def test_stale_preview_and_no_overwrite(tmp_path):
    r=make(tmp_path);p=api.plan(r)
    path=Path(r['arms']['coverage1']['files']['counts']['path'])
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='pin mismatch'):api.execute(p,r['output_dir'])
    assert not Path(r['output_dir']).exists()


def test_bad_native_psi_failure_preserved(tmp_path):
    r=make(tmp_path)
    import gzip
    path=Path(r['arms']['coverage1']['files']['psi']['path'])
    text=gzip.decompress(path.read_bytes()).decode().replace('0.2222222222222222','0.5')
    path.write_bytes(gzip.compress(text.encode()))
    r['arms']['coverage1']['files']['psi']['sha256']=api.digest(path)
    p=api.plan(r)
    with pytest.raises(ValueError,match='worker failed'):api.execute(p,r['output_dir'])
    out=Path(r['output_dir'])
    assert api.status(out)['status']=='failed' and (out/'REFUSAL.json').exists()
    with pytest.raises(ValueError,match='fresh absolute'):api.execute(p,r['output_dir'])


def test_qc_coordinate_na_and_zero_semantics(tmp_path):
    r=make(tmp_path);api.execute(api.plan(r),r['output_dir'])
    import csv
    out=Path(r['output_dir'])
    with (out/'qc/coverage3/chr1/sites.tsv').open() as f:
        qc={x['source_peak_name']:x for x in csv.DictReader(f,delimiter='\t')}
    assert qc['syntheticG0']['b0']=='100' and qc['syntheticG0']['p0']=='99'
    assert qc['minusG0']['b0']=='500' and qc['minusG0']['p0']=='500'
    assert qc['boundaryG0']['downstream_a_risk']==''
    assert qc['boundaryG0']['downstream_reason']=='boundary_truncation'
    assert qc['syntheticG1']['downstream_a_risk']=='true'
    measurements=json.loads((out/'masking/measurements.json').read_text())
    empty=[x for x in measurements if x['arm']=='coverage3' and x['gene_subset']=='arm_only_eligible_genes']
    assert empty and all(x['fraction_of_catalogue_units'] is None for x in empty)


def test_saved_qc_reuse_and_changed_source_row_refusal(tmp_path):
    r=make(tmp_path);api.execute(api.plan(r),r['output_dir'])
    original=Path(r['output_dir']);r['qc_input']='saved'
    for arm in api.ARMS:
        manifest=original/'qc'/arm/'adapter-manifest.json'
        r['arms'][arm]['qc_manifest']={'path':str(manifest),'sha256':api.digest(manifest)}
    (tmp_path/'reuse').mkdir();r['output_dir']=str(tmp_path/'reuse/analysis')
    assert api.execute(api.plan(r),r['output_dir'])['status']=='succeeded'
    # Corrupt a QC source column while updating every byte binding: source-row
    # semantics must still reject independently from hash validation.
    manifest=original/'qc/coverage1/adapter-manifest.json'
    m=json.loads(manifest.read_text());entry=m['contig_results'][0]
    sites=Path(entry['output_dir'])/'sites.tsv'
    text=sites.read_text();lines=text.splitlines();headers=lines[0].split('\t')
    fields=lines[1].split('\t');fields[headers.index('source_gene_id')]='changedGene'
    lines[1]='\t'.join(fields);sites.write_text('\n'.join(lines)+'\n')
    entry['output_files']['sites.tsv']={'bytes':sites.stat().st_size,'sha256':api.digest(sites)}
    manifest.write_text(json.dumps(m))
    r['arms']['coverage1']['qc_manifest']['sha256']=api.digest(manifest)
    (tmp_path/'badqc').mkdir();r['output_dir']=str(tmp_path/'badqc/analysis')
    with pytest.raises(ValueError,match='worker failed'):api.execute(api.plan(r),r['output_dir'])
    refusal=json.loads((Path(r['output_dir'])/'REFUSAL.json').read_text())
    assert 'QC source column mismatch' in refusal['error']


def test_missing_ratio_never_passed_and_na_reason_closed(tmp_path):
    from scpolya_ui.analysis.cs2_backend.method import paired, common
    from scpolya_ui.analysis.cs2_backend.tests.fixtures import table
    r=make(tmp_path)
    rows=list(paired.rows(r['arms']['coverage3']['files']['peak_to_gene']['path'],['peak_name']))
    row=rows[-1];row.update(misprimed_ratio='',general_mispriming_pass='False',mispriming_pass='False')
    desc=table(tmp_path/'missing.tsv.gz',rows)
    meta,*_=paired.metadata(desc['path'],0.9)
    assert meta[row['peak_name']]['passed'] is False and meta[row['peak_name']]['native_passed'] is False
    with pytest.raises(ValueError,match='invalid downstream NA reason'):
        common.state({'downstream_a_risk':'','downstream_reason':'invented'},'downstream_a_risk')


def test_cross_arm_transcript_mismatch_refuses(tmp_path):
    import gzip
    r=make(tmp_path);path=Path(r['arms']['coverage1']['files']['peak_to_gene']['path'])
    text=gzip.decompress(path.read_bytes()).decode().replace('syntheticG.tx','different.tx')
    path.write_bytes(gzip.compress(text.encode()))
    r['arms']['coverage1']['files']['peak_to_gene']['sha256']=api.digest(path)
    with pytest.raises(ValueError,match='worker failed'):api.execute(api.plan(r),r['output_dir'])
    assert 'canonical transcript mismatch' in (Path(r['output_dir'])/'REFUSAL.json').read_text()


def test_mask_monotonicity_and_record_endpoint_distinction(tmp_path):
    from scpolya_ui.analysis.cs2_backend.method import masking, paired, common
    r=make(tmp_path);data={}
    for arm in api.ARMS:
        meta,*_=paired.metadata(r['arms'][arm]['files']['peak_to_gene']['path'],0.9)
        # Make plus records coincident endpoints without changing record IDs.
        for value in meta.values():
            if value['gene_id']=='syntheticG':value['endpoint']=100
        data[arm]={p:{'meta':m,'endpoint':(m['chrom'],m['strand'],m['endpoint']),
            'values':{'downstream_a_risk':p=='syntheticG1'}} for p,m in meta.items()}
    pools,eligible,ledger=masking.catalogue(data,paired,common.Guard())
    assert ('syntheticG','+') in eligible['coverage3','unmasked','native_terminal']
    assert ('syntheticG','+') not in eligible['coverage3','unmasked','representative_terminal_exon']
    for (arm,mode,definition),units in eligible.items():
        assert units<=eligible[arm,'unmasked',definition]


def test_sqlite_spill_escape_guard(tmp_path):
    from scpolya_ui.analysis.cs2_backend.method.common import Guard
    budget=tmp_path/'budget';budget.mkdir();scratch=budget/'scratch';scratch.mkdir()
    outside=tmp_path/'etilqs_simulated'
    with outside.open('wb') as handle:
        handle.write(b'held open');handle.flush()
        with pytest.raises(ValueError,match='SQLite spill escaped'):
            Guard(budget/'analysis',scratch=scratch,budget_root=budget).check()


def test_cohort_minclass_and_identity_refusal(tmp_path):
    from scpolya_ui.analysis.cs2_backend.tests.fixtures import table
    r=make(tmp_path)
    registry=table(tmp_path/'large-labels.tsv',[{'barcode':f'b{i}','pass_qc':'True',
        'cell_type':'Small' if i<19 else 'Large'} for i in range(501)])
    assert api.registry_counts(registry)==(501,482)
    r['arms']['coverage1']['identity']['bam_sha256']='b'*64
    with pytest.raises(ValueError,match='share scientific'):api.plan(r)


def test_read_atime_does_not_invalidate_input_pin(tmp_path):
    import os
    path=tmp_path/'atime-input';path.write_text('reference')
    before=path.stat();os.utime(path,ns=(0,before.st_mtime_ns))
    record=api.pin({'path':str(path),'sha256':api.digest(path)})
    assert record['sha256']==api.digest(path)


# Independent review probes (new synthetic inputs only).
def test_execution_key_cannot_be_contrast(tmp_path):
    r=make(tmp_path)
    for arm in api.ARMS:
        r['arms'][arm]['config']['threads']=4 if arm=='coverage3' else 8
    api.plan(copy.deepcopy(r))  # Execution fields may differ alongside one scientific key.
    r['compared_key']='threads'
    with pytest.raises(ValueError,match='scientific compared_key'):api.plan(r)


def test_mask_regain_refusal(tmp_path,monkeypatch):
    from scpolya_ui.analysis.cs2_backend.method import masking,paired,common
    r=make(tmp_path);data={}
    for arm in api.ARMS:
        meta,*_=paired.metadata(r['arms'][arm]['files']['peak_to_gene']['path'],0.9)
        data[arm]={p:{'meta':m,'endpoint':(m['chrom'],m['strand'],m['endpoint']),
            'values':{'downstream_a_risk':False}} for p,m in meta.items()}
    # Inject a pathological retention predicate: masked modes regain a record.
    monkeypatch.setattr(masking,'retained',lambda row,mode:
        not(mode=='unmasked' and row['meta']['peak_name']=='minusG0'))
    with pytest.raises(ValueError,match='eligibility regained'):
        masking.catalogue(data,paired,common.Guard())


def test_saved_qc_dropped_row_closure(tmp_path):
    r=make(tmp_path);api.execute(api.plan(r),r['output_dir'])
    original=Path(r['output_dir']);r['qc_input']='saved'
    for arm in api.ARMS:
        manifest=original/'qc'/arm/'adapter-manifest.json'
        r['arms'][arm]['qc_manifest']={'path':str(manifest),'sha256':api.digest(manifest)}
    manifest=original/'qc/coverage3/adapter-manifest.json';m=json.loads(manifest.read_text())
    entry=m['contig_results'][0];sites=Path(entry['output_dir'])/'sites.tsv'
    sites.write_text('\n'.join(sites.read_text().splitlines()[:-1])+'\n')
    entry['output_files']['sites.tsv']={'bytes':sites.stat().st_size,'sha256':api.digest(sites)}
    manifest.write_text(json.dumps(m));r['arms']['coverage3']['qc_manifest']['sha256']=api.digest(manifest)
    (tmp_path/'missing-qc-row').mkdir();r['output_dir']=str(tmp_path/'missing-qc-row/analysis')
    with pytest.raises(ValueError,match='worker failed'):api.execute(api.plan(r),r['output_dir'])
    assert 'row-set closure' in (Path(r['output_dir'])/'REFUSAL.json').read_text()


@pytest.mark.parametrize('historical',[False,True])
def test_result_roles_preserve_declared_run_binding(tmp_path,historical):
    r=make(tmp_path)
    if historical:
        for arm in api.ARMS:
            r['arms'][arm]['run']['record_id']='saved-export:'+arm
            r['arms'][arm]['run']['invocation_id']='source-pair-request:declared'
    result=api.execute(api.plan(r),r['output_dir'])
    raw=json.loads((Path(r['output_dir'])/'RESULT.json').read_text())
    for arm in api.ARMS:
        run=r['arms'][arm]['run'];role=result['roles'][arm]
        assert role==raw['roles'][arm]
        assert role['run_id']==role['record_id']==run['record_id']
        assert role['invocation_id']==run['invocation_id'] and role['root']==run['root']


def test_optimized_worker_refuses_bad_scratch_with_receipt(tmp_path):
    import os,subprocess,sys
    r=make(tmp_path);p=api.plan(r)
    out=Path(r['output_dir']);out.mkdir();(out.parent/'scratch').mkdir()
    api.emit(out/'PLAN.json',p)
    env=dict(os.environ,TMPDIR=str(tmp_path),SQLITE_TMPDIR=str(tmp_path))
    child=subprocess.run([sys.executable,'-O','-I','-B',str(api.ROOT/'worker.py'),
        str(out/'PLAN.json'),p['plan_sha256']],env=env,capture_output=True,text=True)
    assert child.returncode==2
    assert 'startup scratch environment mismatch' in json.loads((out/'REFUSAL.json').read_text())['error']
    assert api.status(out)['status']=='failed' and not (out/'paired').exists()
