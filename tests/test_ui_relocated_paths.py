from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import hashlib
import json

import pytest

from scpolya_ui.registry import PathMapping, WorkspaceRecord, apply_path_mappings_to_value
from scpolya_ui.run_data import discover_run_log_catalog
from scpolya_ui.run_records import RunRecordRegistry, config_hash, sync_run_records_from_invocations
from scpolya_ui.jobs import JobRegistry
from scpolya_ui.services.igv_service import comparison_manifest, single_run_manifest, run_view_paths
from scpolya_ui.igv_manifest import artifact_route
from test_ui_igv_run_comparison import runs


def test_component_boundaries_specificity_and_single_application():
    mappings = [PathMapping('/old', '/new'), PathMapping('/old/genes.gtf', '/refs/exact.gtf')]
    assert apply_path_mappings_to_value('/old/run/a.bed', mappings) == '/new/run/a.bed'
    assert apply_path_mappings_to_value('/old/genes.gtf', mappings) == '/refs/exact.gtf'
    assert apply_path_mappings_to_value('/older/run', mappings) == '/older/run'
    assert apply_path_mappings_to_value('text /old/run', mappings) == 'text /old/run'
    assert apply_path_mappings_to_value('/old/x', mappings + [PathMapping('/new', '/again')]) == '/new/x'


@pytest.mark.parametrize('value,mappings', [
    ('/old/../escape', [PathMapping('/old', '/new')]),
    ('/old/x', [PathMapping('/old', '/new/../escape')]),
    ('/old/x', [PathMapping('/old', '/new'), PathMapping('/old', '/different')]),
])
def test_unsafe_or_conflicting_mapping_refuses(value, mappings):
    with pytest.raises(ValueError):
        apply_path_mappings_to_value(value, mappings)


def _invocation(root, historical, status='succeeded'):
    config = {'output_dir': str(historical.parent), 'run_id': historical.name, 'species': 'human'}
    inv_id = 'abcdef1234564abc8abc123456789012'
    directory = root / 'logs/invocations/20261011T224824.000000Z-abcdef12'
    directory.mkdir(parents=True)
    payload = {'schema_version': 'scpolya.logging.invocation.v2', 'invocation_id': inv_id,
               'status': status, 'started_at': '2026-10-11T22:48:24+00:00',
               'finished_at': '2026-10-11T22:48:25+00:00' if status == 'succeeded' else '',
               'duration_seconds': 1 if status == 'succeeded' else None, 'pid': 123,
               'argv': ['python', 'main.py', 'run'], 'error': '', 'run_root': str(historical),
               'output_dir': config['output_dir'], 'run_id': config['run_id'], 'config_schema_version': 1,
               'resolved_config': config, 'paths': {},
               'config_hash': hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    path = directory / 'invocation.json'
    path.write_text(json.dumps(payload))
    return path, payload


@pytest.mark.parametrize('status', ['succeeded', 'running'])
def test_explicit_relocation_preserves_hash_root_and_raw_status(tmp_path, status):
    root = tmp_path / 'local'; historical = Path('/historical/original-run')
    path, payload = _invocation(root, historical, status)
    before = path.read_bytes()
    assert discover_run_log_catalog(root).invocations == ()
    mappings = (PathMapping(str(historical), str(root)),)
    catalog = discover_run_log_catalog(root, path_mappings=mappings)
    assert len(catalog.invocations) == 1
    inv = catalog.invocations[0]
    assert inv.run_root == str(historical)
    assert inv.status == status and inv.config_hash == payload['config_hash']
    assert inv.resolved_config == payload['resolved_config']
    workspace = WorkspaceRecord('W', 'Relocated', str(root), str(root/'state'), str(root/'state/state.sqlite'), path_mappings=list(mappings))
    records = RunRecordRegistry()
    assert sync_run_records_from_invocations(records, JobRegistry(), [workspace])
    record = records.records[0]
    assert record.root_path == str(historical) and record.raw_status == status
    assert record.invocation_config_hash == payload['config_hash']
    assert record.config == payload['resolved_config']
    assert record.status == ('succeeded' if status == 'succeeded' else 'unknown')
    assert path.read_bytes() == before
    payload['resolved_config']['species'] = 'mouse'
    path.write_text(json.dumps(payload))
    assert discover_run_log_catalog(root, path_mappings=mappings).invocations == ()


def test_wrong_mapping_does_not_admit_relocated_invocation(tmp_path):
    root = tmp_path/'local'; historical = Path('/historical/run-extra')
    _invocation(root, historical)
    assert not discover_run_log_catalog(root, path_mappings=(PathMapping('/historical/run', str(root)),)).invocations
    assert not discover_run_log_catalog(root, path_mappings=(PathMapping(str(historical), str(tmp_path/'other')),)).invocations


def test_single_and_comparison_use_each_workspaces_view_without_rewriting_history(runs, tmp_path):
    before = deepcopy(runs)
    workspaces = {}
    for i, record in enumerate(runs):
        old = Path(record.root_path); new = tmp_path / f'relocated-{i}'
        old.rename(new)
        workspaces[record.workspace_id] = WorkspaceRecord(record.workspace_id, 'Relocated', str(new), str(new/'state'), str(new/'state/state.sqlite'), path_mappings=[PathMapping(str(old), str(new))])
    first = runs[0]; mappings = workspaces[first.workspace_id].path_mappings
    one = single_run_manifest(first, gene='TNFRSF1B', base_config={}, workspace_root=first.root_path, path_mappings=iter(mappings), url_for_file=str)
    assert one.view_paths['original_root_path'] == first.root_path
    assert one.view_paths['root_path'] == str(tmp_path/'relocated-0')
    pair = comparison_manifest(*runs, gene='TNFRSF1B', workspaces=workspaces, url_for_file=str)
    for source, record in zip(pair.run_sources, runs):
        local = workspaces[record.workspace_id].root_path
        assert source['root_path'] == record.root_path
        assert source['execution_config'] == record.execution_config
        assert source['view_paths']['root_path'] == local
        own = [t for t in pair.tracks if t.run_record_id == record.id]
        assert own and all(Path(t.path).is_relative_to(local) and t.invocation_id == record.invocation_id for t in own)
    assert runs == before
    with pytest.raises(ValueError, match='Comparison unavailable'):
        comparison_manifest(*runs, gene='TNFRSF1B', url_for_file=str)


def test_mapped_root_requires_config_root_identity(runs, tmp_path):
    record = runs[0]
    record = replace(record, root_path='/different/run')
    with pytest.raises(ValueError, match='original execution configuration'):
        run_view_paths(record, workspace_root=record.root_path, path_mappings=[PathMapping(record.root_path,str(tmp_path/'view'))])


def test_reference_mapping_and_unmapped_behavior_preserve_execution(runs, tmp_path):
    record = runs[0]; before = deepcopy(record)
    oldgtf = record.execution_config['gene_gtf']
    newgtf = str(tmp_path/'mapped-gtf')
    view, root = run_view_paths(record,workspace_root=record.root_path,path_mappings=[PathMapping(oldgtf,newgtf)])
    assert view['gene_gtf'] == newgtf and root == record.root_path
    assert run_view_paths(record,workspace_root=record.root_path) == (record.execution_config,record.root_path)
    assert record == before


def test_explicit_file_alias_urls_use_canonical_target(tmp_path):
    original = tmp_path/'actual'/'genome.fa';original.parent.mkdir();original.write_text('>chr1\nA\n')
    alias = tmp_path/'view'/'hg38.fa';alias.parent.mkdir();alias.symlink_to(original)
    assert artifact_route(str(alias)) == artifact_route(str(original))


def _partial_record(tmp_path, quantification_mode="legacy"):
    from scpolya_ui.config_schema import CONFIG_FIELDS
    from scpolya_ui.run_records import RunRecord
    from task_manage import project_run_outputs_v1
    from task_manage.parameter_studies import final_peak_item_for_config
    from task_manage.run_layout import RunLayout

    gtf = tmp_path/'genes.gtf'
    gtf.write_text('chr1\ttest\tgene\t10\t40\t.\t+\t.\tgene_id "g"; gene_name "TEST";\n')
    chrom = tmp_path/'chrom.sizes';chrom.write_text('chr1\t100\n')
    config = {f.key:f.default for f in CONFIG_FIELDS}
    config.update(output_dir=str(tmp_path),run_id='partial',gene_gtf=str(gtf),chrom_sizes=str(chrom),quantification_mode=quantification_mode,
                  species='human',dedup_method='directional',minpolyA=5,nonA_allowed=1,minMAPQ=10,
                  coverage_scaling='expr',exp_peak_threshold_mode='dynamic',
                  exp_dynamic_threshold_stat='max_norm_fixed',exp_dynamic_threshold_normalized=0.05,
                  polyA_peak_threshold_mode='fixed',unscaled_depth_threshold_polyA=3.0,
                  merge_distance=10,merge_distance_polyA=10)
    layout = RunLayout(out=tmp_path,run_id='partial',artifact_naming_mode='short')
    item = final_peak_item_for_config(config)
    layout.FinalOutputSub(item);layout.AnnotatedFinalOutputSub(item)
    root=tmp_path/'partial'
    for parent,stem in [('cohort.directional','cohort.directional'),
                        ('cohort.directional.polyA-5-1-10','cohort.directional.polyA-5-1-10')]:
        d=root/'cohort_output'/parent;d.mkdir(parents=True)
        for strand in ('plus','minus'):
            scale='.scaled' if parent=='cohort.directional' else ''
            (d/f'{stem}.{strand}.coverage{scale}.bw').write_bytes(b'fixture')
    projection=project_run_outputs_v1(config)
    bed=Path(projection.by_scope_id['final.annotated_peaks_bed'].path)
    bed.parent.mkdir(parents=True,exist_ok=True);bed.write_text('chr1\t15\t20\n')
    record=RunRecord('R-fixture','cli_invocation','succeeded','W-fixture',str(root),'Fixture',config,
                     config_hash(config),'','',root_path=str(root),execution_config=config,
                     execution_config_hash=config_hash(config))
    return record


def test_partial_materialization_shows_available_tracks_and_missing_outputs(tmp_path):
    record = _partial_record(tmp_path)
    manifest=single_run_manifest(record,gene='TEST',base_config={},workspace_root=record.root_path,url_for_file=str)
    assert len(manifest.tracks)==5
    assert {t.role for t in manifest.tracks}=={'coverage_scrna','coverage_polya','annotated_peak'}
    # 6 missing outputs, plus the transcript-model note: the fixture GTF has gene rows only.
    assert len(manifest.missing_tracks)==7
    assert "transcript models: no genes.gtf transcript overlaps chr1:1-520" in manifest.missing_tracks
    assert all(any(f'{role} {strand}' in m for m in manifest.missing_tracks)
               for role in ('raw_peak_scrna','raw_peak_polya') for strand in ('+','-'))
    assert any('final.filtered_peaks' in m for m in manifest.missing_tracks)
    assert any('final.assigned_peaks' in m for m in manifest.missing_tracks)


def _historical_record(tmp_path):
    from task_manage import project_run_outputs_v1
    from task_manage.artifact_naming import annotation_name_payload, build_scoped_artifact_name_v1, register_artifact_name_v1
    record = _partial_record(tmp_path, 'terminal-aware')
    root = Path(record.root_path);config = record.execution_config
    projection = project_run_outputs_v1(config)
    Path(projection.by_scope_id['final.annotated_peaks_bed'].path).unlink()
    peak = next(n for n in projection.by_scope_id['final.filtered_peaks'].artifact_names if n.scope=='peak')
    annotation = build_scoped_artifact_name_v1('annotation', annotation_name_payload(
        peak.payload, annotate_peak_source=config.get('annotate_peak_source') or 'resolved',
        terminal_annotation_schema='terminal-annotation-v2'),legacy_name='fixture-history-v2')
    register_artifact_name_v1(root/'state/artifact_name_manifest.v1.json',mode='short',record=annotation)
    parent=root/'final_output'/peak.short_code/annotation.code;parent.mkdir(parents=True)
    for fmt in ('bed','gtf'):
        (parent/f'{peak.short_code}.___peaks-annotated.terminal-aware.v2.{fmt}').write_text('chr1\t15\t20\n')
    path,payload=_invocation(root,root)
    payload.update(resolved_config=config,output_dir=config['output_dir'],run_id=config['run_id'],
                   config_hash=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    path.write_text(json.dumps(payload))
    record=replace(record,history_source='invocation',invocation_id=payload['invocation_id'],
                   invocation_config_hash=payload['config_hash'],invocation_metadata_path=str(path))
    return record,path,payload,parent


def test_historical_overlay_has_separate_roles_hashes_and_never_fills_current_missing(tmp_path):
    record,path,payload,parent=_historical_record(tmp_path)
    before=deepcopy(record);metadata=path.read_bytes()
    manifest=single_run_manifest(record,gene='TEST',base_config={},workspace_root=record.root_path,url_for_file=str)
    historical=[t for t in manifest.tracks if t.role=='historical_annotation']
    assert {t.format for t in historical}=={'bed','gtf'}
    assert all('terminal-annotation-v2' in t.name and t.invocation_id==record.invocation_id for t in historical)
    # 7 current-run gaps plus the transcript-model note (fixture GTF has gene rows only).
    assert len(manifest.missing_tracks)==8 and not any(t.role=='annotated_peak' for t in manifest.tracks)
    assert any(m.startswith('transcript models: no ') for m in manifest.missing_tracks)
    evidence=manifest.historical_evidence
    assert evidence['invocation_config_hash']==payload['config_hash']
    assert evidence['namespace_manifest_sha256']==hashlib.sha256((Path(record.root_path)/'state/artifact_name_manifest.v1.json').read_bytes()).hexdigest()
    assert all(f['sha256']==hashlib.sha256(Path(f['path']).read_bytes()).hexdigest() for f in evidence['files'])
    assert record==before and path.read_bytes()==metadata
    with pytest.raises(ValueError,match='Comparison unavailable'):
        comparison_manifest(record,replace(record,id='R-other',invocation_id='different-invocation'),gene='TEST',url_for_file=str)


@pytest.mark.parametrize('fault',['duplicate_completed','missing_file','noncompleted','namespace_mismatch','record_config_mismatch'])
def test_historical_overlay_refuses_ambiguous_or_inexact_binding(tmp_path,fault):
    record,path,payload,parent=_historical_record(tmp_path)
    if fault=='duplicate_completed':
        other=path.parent.parent/'20261011T224824.000000Z-12345678';other.mkdir()
        payload=dict(payload,invocation_id='1234567812344abc8abc123456789012')
        (other/'invocation.json').write_text(json.dumps(payload))
    elif fault=='missing_file':
        next(parent.glob('*.gtf')).unlink()
    elif fault=='noncompleted':
        record=replace(record,status='unknown',raw_status='running')
    elif fault=='record_config_mismatch':
        config=dict(record.execution_config,inputs='/different-source.csv')
        record=replace(record,execution_config=config,execution_config_hash=config_hash(config))
    else:
        p=Path(record.root_path)/'state/artifact_name_manifest.v1.json';d=json.loads(p.read_text())
        r=next(r for r in d['records'] if r.get('payload',{}).get('terminal_annotation_schema')=='terminal-annotation-v2')
        r['payload']['annotate_peak_source']='mismatched';p.write_text(json.dumps(d))
    manifest=single_run_manifest(record,gene='TEST',base_config={},workspace_root=record.root_path,url_for_file=str)
    assert not any(t.role=='historical_annotation' for t in manifest.tracks)
    assert manifest.missing_tracks
