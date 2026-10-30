"""New-input controls and frozen transform boundary fixtures; no case answers in worker."""
import copy
import csv
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from scpolya_ui.analysis.cs3_backend import api

import shutil
BINARY=Path(os.environ.get('SCPOLYA_LIFTOVER') or shutil.which('liftOver') or '/nonexistent/liftOver')
if not BINARY.is_file():
    raise unittest.SkipTest('UCSC liftOver is not available; set SCPOLYA_LIFTOVER')

def table(path,columns,rows):
    with path.open('w') as handle:
        w=csv.writer(handle,delimiter='\t',lineterminator='\n');w.writerow(columns);w.writerows(rows)

def atlas():
    return dict(name='NEW SYNTHETIC ATLAS',release='fixture-7',source_assembly='fixtureS',target_assembly='fixtureT',source_coordinate_base='1based',coordinate_status='user_declared',strand_semantics='transcribed_RNA_strand',reference_population='fixture-7 accepted mapped subset',source_provenance='new generated control, not old case',mitochondrial_contig='chrM',mitochondrial_policy='ucsc_chrM_conservative_v1')

def make_fixture(root):
    root.mkdir(exist_ok=True)
    (root/'source.sizes').write_text('chrS\t1000\n');(root/'target.sizes').write_text('chrT\t1000\n')
    (root/'map.chain').write_text('chain 1000 chrS 1000 + 0 1000 chrT 1000 + 0 1000 7\n1000\n')
    table(root/'points.tsv',['pas_id','chrom','position','strand'],[['NEWREF1','chrS',100,'+'],['NEWREF2','chrS',200,'-']])
    sequence='C'*510+'N'+'C'*489
    (root/'genome.fa').write_text('>chrT\n'+sequence+'\n');(root/'genome.fa.fai').write_text('chrT\t1000\t6\t1000\t1001\n')
    api.write_json(root/'sequence.json',dict(assembly='fixtureT',fasta_path=str(root/'genome.fa'),fai_path=str(root/'genome.fa.fai'),fasta_sha256=api.sha(root/'genome.fa'),fai_sha256=api.sha(root/'genome.fa.fai'),source_provenance='generated',identity_scope='whole fixtureT'))
    table(root/'catalogue.tsv',['peak_name','chrom','start','end','strand','mispriming_pass','gene_id','canonical_transcript','misprimed_ratio'],[['NEW_near','chrT',99,100,'+','True','G1','T1','0.1'],['NEW_absent','chrT',399,400,'+','True','G2','T2','NA'],['NEW_N','chrT',499,500,'+','True','NA','NA','NA'],['NEW_edge','chrT',0,1,'-','False','G4','T4','0.3']])
    table(root/'counts.tsv',['peak_name','cell_barcode','umi_count'],[['NEW_near','AA',3],['NEW_absent','AA',2],['NEW_absent','BB',1],['NEW_N','CC',1],['NEW_edge','DD',0]])
    table(root/'labels.tsv',['barcode','pass_qc'],[['AA','True'],['BB','False']])
    api.write_json(root/'source_cov.json',dict(source_sha256=api.sha(root/'points.tsv'),assessment='complete_named_source_payload',assessed_source_contigs=['chrS']))
    common=dict(schema=api.SCHEMA,mode='synthetic',assembly='fixtureT',scope='new pooled control',atlas=atlas(),native=dict(classification='synthetic',source_provenance='generated control'))
    transform=dict(common,method='cs3.transform',format='normalized_points',chain_direction={'source_side':'t','target_side':'q'},liftover_version='UCSC pinned v504',accepted_target_contigs=['chrT'],limits={'source_rows':50},inputs={r:api.pin(p) for r,p in dict(source_table=root/'points.tsv',chain=root/'map.chain',source_sizes=root/'source.sizes',target_sizes=root/'target.sizes',liftover=BINARY).items()})
    return common,transform

def run_task(request,parent,name):
    out=parent/name
    return api.execute(api.plan(request),out),out

def full_fixture(root, atlas_override=None):
    common,req=make_fixture(root)
    if atlas_override:common['atlas'].update(atlas_override)
    t,tout=run_task(req,root,'transform')
    source=tout/'data'
    p=dict(common,method='cs3.project',normalized_source_coordinate_base='1based',source_build='fixtureS',target_build='fixtureT',assessed_target_contigs=['chrT'],transform_receipt=api.pin(tout/'index.json'),limits=dict(source_rows=50,audit_rows=50,chain_blocks=50,catalogue_rows=50),inputs={r:api.pin(path) for r,path in dict(status=source/'final-status.tsv',source_map=source/'source-map.tsv',audit_mapped=source/'audit.mapped.bed',chain=root/'map.chain',source_sizes=root/'source.sizes',target_sizes=root/'target.sizes',sequence_sizes=root/'target.sizes',source_coverage=root/'source_cov.json',sequence_contract=root/'sequence.json',fasta=root/'genome.fa',fai=root/'genome.fa.fai',catalogue=root/'catalogue.tsv',transform_manifest=source/'RUN-MANIFEST.json',transform_contract=source/'TRANSFORM-CONTRACT.json').items()})
    pr,pout=run_task(p,root,'project')
    candidate=dict(common,method='cs3.candidates',pair_semantics='unique_pairs_required',limits=dict(catalogue_rows=50,count_rows=50,reference_rows=50,label_rows=50),inputs={r:api.pin(path) for r,path in dict(catalogue=root/'catalogue.tsv',counts=root/'counts.tsv',reference=pout/'data/reference.tsv',reference_contract=pout/'data/reference-contract.json',coverage=pout/'data/coverage.json',projection_receipt=pout/'index.json',fasta=root/'genome.fa',fai=root/'genome.fa.fai',target_sizes=root/'target.sizes',sequence_sizes=root/'target.sizes',sequence_contract=root/'sequence.json',labels=root/'labels.tsv').items()})
    return req,p,candidate

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_new_input_pipeline_and_NA_support(self):
        _,_,req=full_fixture(self.root)
        result,out=run_task(req,self.root,'candidates')
        self.assertEqual(api.status(out)['status'],'succeeded');self.assertEqual(api.result(out)['status'],'succeeded')
        rows={r['peak_name']:r for r in map(json.loads,(out/'data/assessment.jsonl').read_text().splitlines())}
        self.assertEqual(rows['NEW_near']['reference']['assessment'],'proximal')
        absent=rows['NEW_absent'];self.assertEqual(absent['reference']['assessment'],'absent');self.assertEqual(absent['support']['n_cells'],2);self.assertEqual(absent['support']['assigned_umis'],3)
        self.assertEqual(absent['support']['n_cells_pass_qc_true'],1);self.assertEqual(absent['support']['n_cells_pass_qc_false'],1)
        self.assertEqual(rows['NEW_N']['sequence']['context']['arich']['status'],'not_assessable')
        self.assertIsNone(rows['NEW_N']['native_evidence']['misprimed_ratio']['value'])
        self.assertEqual(rows['NEW_edge']['support']['detection_status'],'no_recorded_positive_detection_not_biological_zero')
        self.assertIn('chrT:400-400',(out/'data/candidates.tsv').read_text())
        self.assertEqual(result['summary']['audit']['candidate_universe_rows'],2)
    def test_base_flip_is_effective_and_recorded(self):
        _,req=make_fixture(self.root)
        _,one=run_task(req,self.root,'one')
        req['atlas']['source_coordinate_base']='0based'
        _,zero=run_task(req,self.root,'zero')
        one_rows=list(csv.DictReader((one/'data/final-status.tsv').read_text().splitlines(),delimiter='\t'));zero_rows=list(csv.DictReader((zero/'data/final-status.tsv').read_text().splitlines(),delimiter='\t'))
        self.assertEqual(int(zero_rows[0]['target_bed_start']),int(one_rows[0]['target_bed_start'])+1)
        self.assertEqual(api.result(zero)['parameters']['atlas']['source_coordinate_base'],'0based')
    def test_pin_change_refuses_with_receipt(self):
        _,req=make_fixture(self.root);p=api.plan(req);(self.root/'points.tsv').write_text('changed')
        with self.assertRaises(api.Refusal): api.execute(p,self.root/'refused')
        self.assertTrue((self.root/'refused/REFUSAL.json').is_file());self.assertEqual(api.status(self.root/'refused')['status'],'failed')
    def test_existing_output_untouched(self):
        _,req=make_fixture(self.root);out=self.root/'exists';out.mkdir();(out/'keep').write_text('preserve')
        with self.assertRaises(api.Refusal):api.execute(api.plan(req),out)
        self.assertEqual(list(out.iterdir()),[out/'keep'])
    def test_size_header_mismatch_refuses(self):
        _,req=make_fixture(self.root);(self.root/'source.sizes').write_text('chrS\t1001\n');req['inputs']['source_sizes']=api.pin(self.root/'source.sizes')
        with self.assertRaises(ValueError):run_task(req,self.root,'badsize')
        self.assertTrue((self.root/'badsize/REFUSAL.json').exists())
    def test_negative_target_chain_refuses(self):
        _,req=make_fixture(self.root);(self.root/'map.chain').write_text('chain 1000 chrS 1000 - 0 1000 chrT 1000 + 0 1000 7\n1000\n');req['inputs']['chain']=api.pin(self.root/'map.chain')
        with self.assertRaises(ValueError):run_task(req,self.root,'badstrand')
    def test_fasta_contig_mismatch_refuses(self):
        _,_,req=full_fixture(self.root);(self.root/'target.sizes').write_text('chrOTHER\t1000\n');req['inputs']['target_sizes']=api.pin(self.root/'target.sizes')
        with self.assertRaises(api.Refusal):run_task(req,self.root,'badfasta')
    def test_duplicate_count_pairs_refuse(self):
        _,_,req=full_fixture(self.root)
        with (self.root/'counts.tsv').open('a') as h:h.write('NEW_absent\tAA\t3\n')
        req['inputs']['counts']=api.pin(self.root/'counts.tsv')
        with self.assertRaises(ValueError):run_task(req,self.root,'dup')
    def test_unknown_pair_semantics_preserve_null_UMIs(self):
        _,_,req=full_fixture(self.root);req['pair_semantics']='unknown';_,out=run_task(req,self.root,'unknown')
        for row in map(json.loads,(out/'data/assessment.jsonl').read_text().splitlines()):self.assertIsNone(row['support']['assigned_umis'])
    def test_projection_product_not_receipt_bound_refuses(self):
        _,_,req=full_fixture(self.root);(self.root/'fake.tsv').write_text((Path(req['inputs']['reference']['path'])).read_text()+'FAKE\n');req['inputs']['reference']=api.pin(self.root/'fake.tsv')
        with self.assertRaises(api.Refusal):run_task(req,self.root,'fake')
    def test_old_readiness_is_not_a_new_mode(self):
        _,req=make_fixture(self.root);req['mode']='accepted'
        with self.assertRaises(api.Refusal):api.plan(req)
    def test_source_limits_preserve_partial(self):
        _,req=make_fixture(self.root);req['limits']['source_rows']=1
        with self.assertRaises(api.Refusal):run_task(req,self.root,'limited')
        self.assertTrue((self.root/'limited/data/source-map.tsv').exists())
    def test_frozen_transform_controls(self):
        from scpolya_ui.analysis.cs3_backend import run_transform as t
        import subprocess,traceback
        path=Path(__file__).parent/'cs3_controls/upstream_controls.py'
        spec=importlib.util.spec_from_file_location('controls',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.LIFTOVER=BINARY;mod.subprocess=subprocess;mod.traceback=traceback
        def accounted(case_dir,*args,**kwargs):
            metrics,unused=t.run_accounted(case_dir,*args,**kwargs,binary=BINARY)
            expected=json.loads((Path(case_dir)/'expected.json').read_text())
            actual=list(csv.DictReader((Path(case_dir)/'final-status.tsv').read_text().splitlines(),delimiter='\t'))
            self.assertEqual({r['ordinal']:r['status'] for r in actual},expected['statuses'])
            for assertion in expected.get('assertions',[]):
                row=actual[assertion['ordinal']-1];self.assertEqual(row[assertion['field']],str(assertion['value']))
            return metrics,unused
        mod.run_accounted=accounted
        result=mod.build_selftest(self.root/'oldcontrols');self.assertTrue(result['all_pass'])
    def test_contig_set_differences_are_explicit_and_query_intersection_required(self):
        report=api.contig_compatibility({'chrT':1000,'chrUCSC_only':200},{'chrT':1000,'GL_only':200})
        self.assertEqual(report['shared'],['chrT']);self.assertEqual(report['chain_only'],['chrUCSC_only']);self.assertEqual(report['sequence_only'],['GL_only']);self.assertEqual(report['shared_length_mismatches'],[])
        _,_,request=full_fixture(self.root)
        with self.assertRaises(api.Refusal):api.require_actual_contigs(request,{'GL_only'})
        self.assertEqual(api.require_actual_contigs(request,{'chrT'})['shared_count'],1)
    def test_shared_contig_length_mismatch_reported(self):
        report=api.contig_compatibility({'chrT':1000},{'chrT':999})
        self.assertEqual(report['shared_length_mismatches'],[{'contig':'chrT','chain_length':1000,'sequence_length':999}])
    def test_new_reference_metadata_uses_declaration_and_normalized_column_base(self):
        _,_,request=full_fixture(self.root,{'name':'Alternate declared atlas','release':'other-r2','source_coordinate_base':'0based','coordinate_status':'documented'})
        result=json.loads((self.root/'project/data/reference-contract.json').read_text())
        summary=json.loads((self.root/'project/data/SUMMARY.json').read_text())
        self.assertEqual(result['source_name'],'Alternate declared atlas');self.assertEqual(result['release'],'other-r2')
        self.assertEqual(result['source_coordinate_base'],'1based')
        self.assertEqual(result['source_coordinate_declaration']['source_coordinate_base'],'0based')
        self.assertIn('declared 0based (documented)',result['point_semantics'])
        self.assertNotIn('independently accepted',result['point_semantics'])
        self.assertIn('Alternate declared atlas release other-r2',summary['claim_scope'])
        self.assertNotIn('PolyA_DB',summary['claim_scope'])
    def test_unsupported_mitochondrial_declaration_refuses(self):
        _,request=make_fixture(self.root);request['atlas']['mitochondrial_contig']='MT'
        with self.assertRaises(api.Refusal):api.plan(request)
    def test_unsupported_MT_size_contig_refuses_without_alias(self):
        _,request=make_fixture(self.root)
        with (self.root/'target.sizes').open('a') as handle:handle.write('MT\t1000\n')
        request['inputs']['target_sizes']=api.pin(self.root/'target.sizes')
        with self.assertRaises(api.Refusal):api.plan(request)
    def test_unsupported_projection_normalized_column_base_refuses(self):
        _,request,_=full_fixture(self.root);request['normalized_source_coordinate_base']='0based'
        with self.assertRaises(api.Refusal):api.plan(request)
    def test_other_joint_QC_audit_retains_source_order(self):
        _,_,request=full_fixture(self.root)
        with (self.root/'counts.tsv').open('a') as handle:handle.write('NEW_N\tAA\t1\nNEW_N\tBB\t1\n')
        request['inputs']['counts']=api.pin(self.root/'counts.tsv')
        _,out=run_task(request,self.root,'order')
        original=[json.loads(x)['peak_name'] for x in (out/'data/candidate_universe.jsonl').read_text().splitlines()]
        audit=[json.loads(x)['peak_name'] for x in (out/'data/selection-audit.jsonl').read_text().splitlines()]
        self.assertEqual(original,['NEW_absent','NEW_N']);self.assertEqual(audit,original)
        ranked=list(csv.DictReader((out/'data/candidates.tsv').read_text().splitlines(),delimiter='\t'))
        self.assertEqual(ranked[0]['peak_name'],'NEW_N')
    def test_worker_refuses_changed_initializer_or_api_before_import(self):
        import shutil,subprocess,sys
        for target in ('scpolya_ui/__init__.py','scpolya_ui/analysis/cs3_backend/api.py'):
            with self.subTest(target=target):
                shadow=self.root/('shadow-'+str(len(target)));shadow.mkdir()
                checkout=api.HERE.parents[2]
                sources=[]
                for item in api.method_pins():
                    path=shadow/Path(item['path']).relative_to(checkout);path.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(item['path'],path);sources.append(api.pin(path))
                plan_path=self.root/(str(len(target))+'-plan.json');api.write_json(plan_path,{'method_sources':sources})
                marker=self.root/('IMPORTED-'+str(len(target)))
                changed=shadow/target
                changed.write_text(changed.read_text()+"\nfrom pathlib import Path\nPath("+repr(str(marker))+").write_text('unexpected import')\n")
                completed=subprocess.run([sys.executable,'-I','-B',str(shadow/'scpolya_ui/analysis/cs3_backend/worker.py'),'execute','--plan',str(plan_path),'--output',str(self.root/'unstarted')],capture_output=True,text=True)
                self.assertNotEqual(completed.returncode,0);self.assertIn('changed before package import',completed.stderr)
                self.assertFalse(marker.exists());self.assertFalse((self.root/'unstarted').exists())
    def native_request(self):
        from scpolya_ui.services.run_evidence_service import pin as gui_pin
        _,_,request=full_fixture(self.root)
        config={'output_dir':str(self.root.parent),'run_id':self.root.name,'species':'mouse'}
        invocation_id='abcdef1234564abc8abc123456789012'
        directory=self.root/'logs/invocations/20261026T000000.000000Z-abcdef12';directory.mkdir(parents=True)
        payload={'schema_version':'scpolya.logging.invocation.v2','invocation_id':invocation_id,'status':'succeeded','started_at':'2026-10-26T00:00:00+00:00','finished_at':'2026-10-26T00:00:01+00:00','duration_seconds':1,'pid':123,'argv':['fixture-producer'],'error':'','run_root':str(self.root),'output_dir':config['output_dir'],'run_id':config['run_id'],'config_schema_version':1,'resolved_config':config,'paths':{},'config_hash':api.hashlib.sha256(api.canonical(config)).hexdigest()}
        path=directory/'invocation.json';api.write_json(path,payload)
        request['mode']='native_snapshot';request['native']={'classification':'new_snapshot_of_current_retained_files','run_record_id':'SYNTHETIC-record','run_id':self.root.name,'workspace_id':'SYNTHETIC-workspace','view_root':str(self.root),'original_root':str(self.root),'config':config,'config_hash':payload['config_hash'],'invocation_id':invocation_id,'invocation':gui_pin(path),'path_mappings':[]}
        return request,path,payload
    def test_native_snapshot_real_gui_verifier_without_mock(self):
        request,_,_=self.native_request()
        result,out=run_task(request,self.root,'native')
        self.assertEqual(result['summary']['audit']['candidate_universe_rows'],2)
        self.assertEqual(json.loads((out/'data/INPUT-BINDING.json').read_text())['invocation_id'],request['native']['invocation_id'])
    def test_native_snapshot_changed_invocation_refuses(self):
        request,path,payload=self.native_request();plan=api.plan(request)
        payload['status']='failed';api.write_json(path,payload)
        with self.assertRaises(ValueError):api.execute(plan,self.root/'changed-invocation')
    def test_native_snapshot_other_root_tables_refuse(self):
        request,_,_=self.native_request()
        other=self.root.parent/(self.root.name+'-outside.tsv')
        try:
            other.write_text((self.root/'catalogue.tsv').read_text());request['inputs']['catalogue']=api.pin(other)
            with self.assertRaises(ValueError):api.plan(request)
        finally:
            other.unlink(missing_ok=True)
    def test_lower_bound_sequence_status_is_preserved(self):
        from scpolya_ui.analysis.cs3_backend import mouse_candidates as h
        value=h.sequence_context('C'*50+'A'*12+'N'+'C'*7,'chrT',{'chrT'},50,'+')
        self.assertEqual(value['arich']['status'],'positive_lower_bound')
        self.assertIs(value['arich']['high_arich'],True)
        self.assertFalse(value['arich']['complete_strict_acgt'])
    def test_corrupt_result_refuses_readback(self):
        _,req=make_fixture(self.root);_,out=run_task(req,self.root,'good');(out/'data/final-status.tsv').write_text('corrupt')
        with self.assertRaises(api.Refusal):api.result(out)

if __name__=='__main__':unittest.main()
