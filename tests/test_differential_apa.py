"""Native APA contracts and actual R endpoints; synthetic evidence, not FDR calibration.

Set SCPOLYA_DIFF_R_LIBRARY to run R integration tests in a bounded systemd scope.
"""
import csv
import gzip
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from scpolya_diff.cli import main
from scpolya_diff.common import table

ANN_FIELDS = ['peak_name','gene_id','gene_name','chrom','start','end','strand','feature',
              'three_prime_utr_length','canonical_transcript','stop_codon_position',
              'mispriming_pass','distance_to_stop_codon','general_mispriming_pass','is_distal_peak']
META_FIELDS = ['sample_id','cell_barcode','pseudobulk_id','donor_id','condition','cell_type','batch']
MAN_FIELDS = ['sample_id','counts_path','annotation_path']


def write(path, fields, rows):
    op = gzip.open if str(path).endswith('.gz') else open
    with op(path,'wt',newline='') as f:
        w = csv.writer(f,delimiter='\t',lineterminator='\n')
        if fields: w.writerow(fields)
        w.writerows(rows)


def read(path):
    return list(table(path))


@pytest.fixture
def case(tmp_path):
    rng = random.Random(20261021)
    ann=[]
    for g in range(122):
        for site,length in [('p',100),('d',1000)]:
            ann.append([f'G{g:03}_{site}',f'G{g:03}',f'G{g:03}','chr1',1000+g*2000+length,
                        1001+g*2000+length,'+','Terminal_exon',length,f'TX{g:03}',1000+g*2000,'True',length])
    for unit, lengths in [('ZERO',(0,0)),('UNKNOWN',('NA',100)),('TIE',(100,100))]:
        for i,length in enumerate(lengths):
            ann.append([f'{unit}_{i}',unit,unit,'chr2',500+i*100,501+i*100,'+','Terminal_exon',length,'TX'+unit,100,'True',i*100 if unit!='TIE' else 100])
    ann.extend([
        ['downstream','G000','G000','chr1',2500,2501,'+','Downstream_of_terminal_exon',1500,'TX000',1000,'True',1500],
        ['intron','INTRON','I','chr1',50,51,'+','Intron',0,'TXI',1000,'True',-950],
        ['misprime','MIS','M','chr1',1100,1101,'+','Terminal_exon',100,'TXM',1000,'False',100],
        ['allzero','NEVER','N','chr1',1200,1201,'+','Terminal_exon',200,'TXN',1000,'True',200],
        ['dual_plus','DUAL','D','chr3',1100,1101,'+','Terminal_exon',100,'TXD+',1000,'True',100],
        ['dual_minus','DUAL','D','chr3',900,901,'-','Terminal_exon',100,'TXD-',1000,'True',100],
    ])
    # Zero lengths are native clipped negative distances; unknown lengths remain unavailable.
    for row in ann:
        if row[0].startswith('ZERO_'):row[-1]=-100+int(row[0][-1])*20
    native=[x for x in ann if x[7] in ('Terminal_exon','Downstream_of_terminal_exon') and x[11]=='True']
    for row in ann:
        eligible=[x for x in native if (x[1],x[6])==(row[1],row[6])]
        distal=bool(eligible) and row in eligible and row[12]==max(x[12] for x in eligible)
        row.extend([row[11],str(distal)])
    write(tmp_path/'annotation.tsv',ANN_FIELDS,ann)
    meta=[]; manifest=[]; truth={}
    for i in range(8):
        sid=f'S{i+1}'; group='CTRL' if i<4 else 'CASE'
        for barcode in ('BC1','BC2'): meta.append([sid,barcode,sid,f'D{i+1}',group,'CD8T',f'B{i%2}'])
        rows=[]
        for g in range(122):
            total=max(80,int(rng.gauss(400,60)))
            prop=(.8 if group=='CASE' else .2) if g<20 else .5
            prop=min(.95,max(.05,rng.gauss(prop,.035)))
            if 20<=g<40 and group=='CASE': total*=4
            distal=round(total*prop); proximal=total-distal
            if g==120: proximal,distal=1,100
            if g==121: proximal,distal=5,5
            if g==0 and i==0: proximal,distal=80,20
            for site,count in [('p',proximal),('d',distal)]:
                first=round(count*.9)
                rows.extend([[f'G{g:03}_{site}','BC1',first],[f'G{g:03}_{site}','BC2',count-first]])
                truth[(f'G{g:03}_{site}',sid)]=count
        for j,p in enumerate(['ZERO_0','ZERO_1','UNKNOWN_0','UNKNOWN_1','TIE_0','TIE_1','downstream','intron','misprime','dual_plus','dual_minus']):
            rows.extend([[p,'BC1',30+i+j],[p,'BC2',20+i]])
            truth[(p,sid)]=50+2*i+j
        path=tmp_path/(sid+('.tsv.gz' if i==7 else '.tsv'))
        fields=['peak_name','cell_barcode','umi_count'] if i%2 else None
        if i==7:
            fields=['peak_name','cell_barcode','peak_umi_count','psi']; rows=[r+[.123] for r in rows]
        write(path,fields,rows);manifest.append([sid,path.name,'annotation.tsv'])
    write(tmp_path/'metadata.tsv',META_FIELDS,meta)
    write(tmp_path/'manifest.tsv',MAN_FIELDS,manifest)
    return dict(root=tmp_path,ann=ann,meta=meta,manifest=manifest,truth=truth)


def prep(c, name='pb', extra=()):
    r=c['root']
    return main(['prepare','--manifest',str(r/'manifest.tsv'),'--metadata',str(r/'metadata.tsv'),
                 '--annotation',str(r/'annotation.tsv'),'--cell-type','CD8T',
                 '--analysis-set','terminal-only','--min-cells','2','--outdir',str(r/name),*extra])


def test_exact_counts_context_and_native_denominator(case):
    c=case; r=c['root']; assert prep(c)==0
    counts={x['feature_id']:x for x in read(r/'pb/pas_counts.tsv')}
    for (peak,sid),value in c['truth'].items():
        if peak in ('downstream','intron','misprime'): assert peak not in counts
        else: assert int(counts[peak][sid])==value
    assert counts['ZERO_0'] and counts['UNKNOWN_0']
    assert all(int(counts['allzero'][f'S{i}'])==0 for i in range(1,9))
    assert counts['dual_plus']['unit_id']=='DUAL|%2B'
    assert counts['dual_minus']['unit_id']=='DUAL|-'
    assert prep(c,'native',['--analysis-set','native-terminal'])==0
    native={x['feature_id'] for x in read(r/'native/pas_counts.tsv')}
    assert native-set(counts)=={'downstream'}
    assert all(x['n_cells_matched']=='2' for x in read(r/'pb/samples.tsv'))
    assert prep(c)==2  # preserves original output


@pytest.mark.parametrize('column,value',[('start',99999),('end',99999),('strand','-'),('gene_id','OTHER'),
                                       ('canonical_transcript','OTHER'),('stop_codon_position',123),
                                       ('three_prime_utr_length',123),('distance_to_stop_codon',123),('mispriming_pass','False'),('general_mispriming_pass','False'),('is_distal_peak','True')])
def test_catalogue_mismatch_even_same_peak_name(case,column,value):
    c=case;r=c['root'];ann=[x[:] for x in c['ann']]
    ann[1][ANN_FIELDS.index(column)]=value
    if column=='start':ann[1][ANN_FIELDS.index('end')]=100000
    write(r/'sample_annotation.tsv',ANN_FIELDS,ann)
    c['manifest'][0][2]='sample_annotation.tsv';write(r/'manifest.tsv',MAN_FIELDS,c['manifest'])
    assert prep(c)==2
    assert 'fingerprint mismatch' in json.loads((r/'pb/preparation.json').read_text())['error']


@pytest.mark.parametrize('value',['-1','.5','1.00000000000000001','NaN','Inf','2147483648'])
def test_lossless_integer_count_rejection(case,value):
    c=case;r=c['root'];p=r/c['manifest'][0][1]
    with p.open('a') as f: f.write(f'G000_p\tOTHER\t{value}\n')
    assert prep(c)==2
    assert json.loads((r/'pb/preparation.json').read_text())['status']=='FAILED'


def test_duplicate_metadata_and_biological_splits(case):
    c=case;r=c['root'];write(r/'metadata.tsv',META_FIELDS,c['meta']+[c['meta'][0]])
    assert prep(c)==2
    c['meta'][1][2]='technical_split';write(r/'metadata.tsv',META_FIELDS,c['meta'])
    assert prep(c,'split')==2


def test_duplicate_raw_rows_and_inputs(case):
    c=case;r=c['root'];p=r/c['manifest'][0][1]
    with p.open('a') as f:f.write('G000_p\tBC1\t1\n')
    assert prep(c)==2
    assert 'duplicate selected PAS/cell' in json.loads((r/'pb/preparation.json').read_text())['error']
    c['manifest'][1][1]=c['manifest'][0][1];write(r/'manifest.tsv',MAN_FIELDS,c['manifest'])
    assert prep(c,'duplicate_input')==2


def test_anchor_and_unmatched_fail_closed(case):
    c=case;r=c['root'];c['ann'][1][9]='DIFFERENT';write(r/'annotation.tsv',ANN_FIELDS,c['ann'])
    assert prep(c)==2
    c['ann'][1][9]='TX000';write(r/'annotation.tsv',ANN_FIELDS,c['ann'])
    c['meta'].append(['S1','WRONG_SUFFIX-1','S1','D1','CTRL','CD8T','B0']);write(r/'metadata.tsv',META_FIELDS,c['meta'])
    assert prep(c,'unmatched')==2
    assert read(r/'unmatched/unmatched_cells.tsv')[0]['cell_barcode']=='WRONG_SUFFIX-1'
    assert prep(c,'excluded',['--allow-unmatched-cells'])==0
    assert read(r/'excluded/samples.tsv')[0]['n_cells_matched']=='2'


def run_r(c, pb='pb', name='result', extra=()):
    return main(['run','--input',str(c['root']/pb),'--outdir',str(c['root']/name),
                 '--control','CTRL','--case','CASE','--rscript','/usr/bin/Rscript',
                 '--r-library',os.environ['SCPOLYA_DIFF_R_LIBRARY'],*extra])


rtest=pytest.mark.skipif(not os.environ.get('SCPOLYA_DIFF_R_LIBRARY'),reason='Set task-specific R library for actual integration')


@rtest
def test_actual_edger_limma_filtering_and_direction(case):
    c=case;r=c['root'];assert prep(c)==0
    assert run_r(c,extra=['--methods','edger,limma','--batch'])==0
    summary={x['unit_id']:x for x in read(r/'result/differential_apa_summary.tsv')}
    assert len(summary)==123 and 'G120|%2B' not in summary and 'G121|%2B' not in summary
    for i in range(20):
        row=summary[f'G{i:03}|%2B'];assert float(row['edger_FDR'])<.05
        assert float(row['limma_length_log2_ratio'])>0
        assert (row['analysis_delta_distal_usage_pp']=='NA' if i==0 else float(row['analysis_delta_distal_usage_pp'])>0)
    lengths={x['unit_id']:x for x in read(r/'result/weighted_length.tsv')}
    assert float(lengths['G000|%2B']['S1'])==280.0
    assert summary['ZERO|%2B']['limma_length_status']=='nonpositive_weighted_length'
    assert summary['UNKNOWN|%2B']['limma_length_status']=='missing_or_invalid_pas_length'
    assert summary['TIE|%2B']['limma_length_status']=='constant_length'
    assert float(summary['TIE|%2B']['analysis_mean_distal_usage_control'])==1
    assert summary['ZERO|%2B']['edger_pvalue']!='NA'
    filt=read(r/'result/filtering.tsv');assert int(filt[-1]['units'])==123
    assert not (r/'result/SUCCESS').exists()
    assert json.loads((r/'result/run.json').read_text())['status']=='COMPLETE_WITH_UNTESTABLE_LENGTH_UNITS'
    # Recompute BH independently using Python sorted ranks.
    rows=read(r/'result/edger_units.tsv');order=sorted(rows,key=lambda x:float(x['P.Value']))
    bound=1.
    for i in range(len(order)-1,-1,-1):
        row=order[i];bound=min(bound,float(row['P.Value'])*len(order)/(i+1))
        assert math.isclose(bound,float(row['FDR']),rel_tol=1e-12,abs_tol=1e-15)
    # Exact re-derivation from delivered used counts, independent of R usage export.
    counts=read(r/'result/pas_counts_used.tsv');totals={x['unit_id']:x for x in read(r/'result/retained_unit_counts.tsv')}
    usage={x['feature_id']:x for x in read(r/'result/pas_usage.tsv')}
    for row in counts:
        for i in range(1,9):
            sid=f'S{i}';assert math.isclose(int(row[sid])/float(totals[row['unit_id']][sid]),float(usage[row['feature_id']][sid]),rel_tol=1e-14)
    null_calls=sum(float(summary[f'G{i:03}|%2B']['edger_FDR'])<.05 for i in range(20,120))
    print(f'Synthetic expression-only/null edgeR calls {null_calls}/100; not FDR calibration')


@rtest
def test_actual_pairing_confounding_and_single_donor(case):
    c=case;r=c['root']
    for row in c['meta']:row[3]=f'D{(int(row[0][1:])-1)%4+1}'
    write(r/'metadata.tsv',META_FIELDS,c['meta']);assert prep(c)==0
    assert run_r(c,name='unmodeled')==2
    assert run_r(c,name='paired',extra=['--paired'])==0
    assert run_r(c,name='confounded',extra=['--paired','--batch'])==2
    # Only one donor per condition is genuinely allowed to prepare, not infer.
    c['meta']=[x for x in c['meta'] if x[0] in ('S1','S5')]
    c['manifest']=[x for x in c['manifest'] if x[0] in ('S1','S5')]
    write(r/'metadata.tsv',META_FIELDS,c['meta']);write(r/'manifest.tsv',MAN_FIELDS,c['manifest'])
    assert prep(c,'single')==0
    assert run_r(c,pb='single',name='single_inference',extra=['--paired'])==0
    assert json.loads((r/'single_inference/run.json').read_text())['status']=='DESCRIPTIVE_ONLY'
    assert run_r(c,pb='single',name='single_descriptive',extra=['--descriptive-only','--paired'])==0
    assert json.loads((r/'single_descriptive/run.json').read_text())['status']=='DESCRIPTIVE_ONLY'
    assert all('pvalue' not in k and 'FDR' not in k for k in read(r/'single_descriptive/differential_apa_summary.tsv')[0])


@rtest
def test_actual_drimseq_sensitivity_warning_status(case):
    c=case;r=c['root'];assert prep(c)==0
    assert run_r(c,extra=['--methods','drimseq'])==0
    rows=read(r/'result/drimseq_units.tsv')
    assert len(rows)==123 and all(math.isfinite(float(x['pvalue'])) for x in rows)
    warnings=read(r/'result/warnings.tsv')
    status=json.loads((r/'result/run.json').read_text())['status']
    assert status==('COMPLETE_WITH_WARNINGS' if warnings else 'COMPLETE')
    assert not (r/'result/edger_units.tsv').exists()


@rtest
def test_prepared_tampering_and_unknown_group(case):
    c=case;r=c['root'];assert prep(c)==0
    assert run_r(c,name='unknown',extra=['--case','TYPO'])==2
    p=r/'pb/pas_counts.tsv';p.write_text(p.read_text().replace('G000_p','unbound_id',1))
    assert run_r(c,name='tampered')==2
    assert not (r/'tampered').exists()


@rtest
def test_recorded_batch_confounding_cannot_be_hidden(case):
    c=case;r=c['root']
    for row in c['meta']:row[-1]=row[4]
    write(r/'metadata.tsv',META_FIELDS,c['meta']);assert prep(c)==0
    assert run_r(c,name='batch_omitted')==2
    assert 'confounded' in json.loads((r/'batch_omitted/run.json').read_text())['error']


@rtest
def test_method_defaults_and_length_only_endpoint(case):
    c=case;r=c['root'];assert prep(c)==0
    assert run_r(c,name='default',extra=['--batch'])==0
    assert (r/'default/edger_units.tsv').is_file()
    assert (r/'default/limma_length_units.tsv').is_file()
    assert not (r/'default/drimseq_units.tsv').exists()
    assert run_r(c,name='composition_only',extra=['--methods','edger','--batch'])==0
    assert not (r/'composition_only/limma_length_units.tsv').exists()
    assert run_r(c,name='length_only',extra=['--methods','limma','--batch'])==0
    assert not (r/'length_only/edger_units.tsv').exists()
    assert json.loads((r/'length_only/run.json').read_text())['primary_composition'] is None


@rtest
def test_symmetric_null_and_expression_only_control(case):
    from scpolya_diff.prepare import raw_counts
    c=case;r=c['root']
    for i in range(4):
        rows=[list(x) for x in raw_counts(r/c['manifest'][i][1])]
        # Distinct libraries may share selected measurements in this exact null;
        # make their excluded-context counts distinct to retain input identity.
        for row in rows:
            if row[0]=='intron':row[2]+=10000+i
        write(r/c['manifest'][i+4][1],['peak_name','cell_barcode','umi_count'],rows)
    assert prep(c)==0
    assert run_r(c,extra=['--batch'])==0
    rows=read(r/'result/edger_units.tsv')
    assert all(float(row['P.Value'])>.1 for row in rows)
    assert all(float(row['FDR'])>.1 for row in rows)


@rtest
def test_two_donors_warning_and_empty_filter_diagnostics(case):
    c=case;r=c['root']
    c['meta']=[x for x in c['meta'] if x[0] in ('S1','S2','S5','S6')]
    c['manifest']=[x for x in c['manifest'] if x[0] in ('S1','S2','S5','S6')]
    write(r/'metadata.tsv',META_FIELDS,c['meta']);write(r/'manifest.tsv',MAN_FIELDS,c['manifest'])
    assert prep(c)==0
    assert run_r(c,name='two',extra=['--batch'])==0
    report=json.loads((r/'two/run.json').read_text());assert report['status']=='COMPLETE_WITH_WARNINGS'
    assert any('Fewer than 3' in x['warning'] for x in read(r/'two/warnings.tsv'))
    assert run_r(c,name='all_filtered',extra=['--min-pas-count','2147483647'])==2
    assert read(r/'all_filtered/filtering.tsv')[-1]['units']=='0'


@rtest
def test_actual_drimseq_paired_and_batch_diagnostics(case):
    c=case;r=c['root'];assert prep(c)==0
    assert run_r(c,name='batch_drimseq',extra=['--methods','drimseq','--batch'])==0
    assert read(r/'batch_drimseq/drimseq_likelihoods.tsv')
    for row in c['meta']:row[3]=f'D{(int(row[0][1:])-1)%4+1}'
    write(r/'metadata.tsv',META_FIELDS,c['meta']);assert prep(c,'paired_pb')==0
    assert run_r(c,pb='paired_pb',name='paired_drimseq',extra=['--methods','drimseq','--paired'])==0
    assert len(read(r/'paired_drimseq/drimseq_units.tsv'))==123


@rtest
def test_low_cell_pseudobulk_and_incomplete_pairs_are_excluded(case):
    c=case;r=c['root']
    # Donor4 control falls below the frozen cell threshold; case D4 then lacks a pair.
    c['meta']=[x for x in c['meta'] if not (x[0]=='S4' and x[1]=='BC2')]
    for row in c['meta']:row[3]=f'D{(int(row[0][1:])-1)%4+1}'
    write(r/'metadata.tsv',META_FIELDS,c['meta']);assert prep(c)==0
    excluded=read(r/'pb/excluded_pseudobulks.tsv')
    assert len(excluded)==1 and excluded[0]['pseudobulk_id']=='S4' and excluded[0]['reason']=='below_min_matched_cells'
    assert run_r(c,extra=['--paired'])==0
    donors=read(r/'result/excluded_donors.tsv');assert len(donors)==1 and donors[0]['donor_id']=='D4'
    assert len(read(r/'result/samples_used.tsv'))==6
    assert json.loads((r/'result/run.json').read_text())['mode']=='inference'


@rtest
def test_automatic_single_pair_and_no_complete_pairs_descriptive(case):
    c=case;r=c['root']
    c['meta']=[x for x in c['meta'] if x[0] in ('S1','S5','S6')]
    c['manifest']=[x for x in c['manifest'] if x[0] in ('S1','S5','S6')]
    for row in c['meta']:row[3]='D1' if row[0] in ('S1','S5') else 'D2'
    write(r/'metadata.tsv',META_FIELDS,c['meta']);write(r/'manifest.tsv',MAN_FIELDS,c['manifest'])
    assert prep(c)==0
    assert run_r(c,name='one_pair',extra=['--paired'])==0
    report=json.loads((r/'one_pair/run.json').read_text());assert report['status']=='DESCRIPTIVE_ONLY'
    assert report['inference_reason']=='insufficient_replicates'
    assert not (r/'one_pair/edger_units.tsv').exists()
    assert read(r/'one_pair/excluded_donors.tsv')[0]['donor_id']=='D2'
    # No surviving complete pair produces explicit empty descriptive output, not inference.
    for row in c['meta']:row[3]=row[0]
    write(r/'metadata.tsv',META_FIELDS,c['meta']);assert prep(c,'no_pair_pb')==0
    assert run_r(c,pb='no_pair_pb',name='no_pairs',extra=['--paired'])==0
    assert json.loads((r/'no_pairs/run.json').read_text())['status']=='DESCRIPTIVE_ONLY'
    assert not read(r/'no_pairs/differential_apa_summary.tsv')


@rtest
def test_native_distal_denominator_and_filtered_tie(case):
    from scpolya_diff.prepare import raw_counts
    c=case;r=c['root']
    # A third proximal PAS keeps the unit testable after one of two tied distal
    # members is removed by count filtering. The remaining tie cannot replace it.
    c['ann'].append(['TIE_prox','TIE','TIE','chr2',300,301,'+','Terminal_exon',10,'TXTIE',100,'True',10,'True','False'])
    write(r/'annotation.tsv',ANN_FIELDS,c['ann'])
    for row in c['manifest']:
        path=r/row[1];raw=[list(x) for x in raw_counts(path)]
        for x in raw:
            if x[0]=='TIE_1':x[2]=1
        raw.append(['TIE_prox','BC1',20]);write(path,['peak_name','cell_barcode','umi_count'],raw)
    assert prep(c)==0
    assert run_r(c,extra=['--methods','edger','--batch'])==0
    summary={x['unit_id']:x for x in read(r/'result/differential_apa_summary.tsv')}
    assert summary['TIE|%2B']['analysis_distal_status']=='distal_filtered'
    assert summary['TIE|%2B']['analysis_delta_distal_usage_pp']=='NA'
    assert summary['G000|%2B']['analysis_distal_status']=='distal_filtered'
    assert summary['G000|%2B']['analysis_delta_distal_usage_pp']=='NA'
    # Native distal numerator is downstream, denominator is ALL native terminal PAS.
    control=[];case_values=[]
    for i in range(1,9):
        sid=f'S{i}';t=c['truth'];p=t[('G000_p',sid)];d=t[('G000_d',sid)];down=t[('downstream',sid)]
        (control if i<=4 else case_values).append(down/(p+d+down))
    expected=100*(sum(case_values)/4-sum(control)/4)
    assert math.isclose(float(summary['G000|%2B']['native_delta_distal_usage_pp']),expected,abs_tol=1e-12)


@rtest
def test_native_default_and_missing_anchor_composition(case):
    c=case;r=c['root']
    c['ann'][1][9]='';write(r/'annotation.tsv',ANN_FIELDS,c['ann'])
    args=['prepare','--manifest',str(r/'manifest.tsv'),'--metadata',str(r/'metadata.tsv'),'--annotation',str(r/'annotation.tsv'),
          '--cell-type','CD8T','--min-cells','2','--outdir',str(r/'pb')]
    assert main(args)==0
    prep_report=json.loads((r/'pb/preparation.json').read_text());assert prep_report['analysis_set']=='native-terminal'
    assert 'downstream' in {x['feature_id'] for x in read(r/'pb/pas_counts.tsv')}
    assert run_r(c,extra=['--batch'])==0
    summary={x['unit_id']:x for x in read(r/'result/differential_apa_summary.tsv')}
    assert summary['G000|%2B']['limma_length_status']=='missing_or_invalid_pas_length'
    assert summary['G000|%2B']['edger_pvalue']!='NA'


def test_general_quality_and_negative_length_clip_match_native(case):
    c=case;r=c['root']
    for row in c['ann']:
        if row[0]=='G001_p':row[11]='False'  # general flag remains True
        if row[0].startswith('ZERO_'):row[8]=-999
    write(r/'annotation.tsv',ANN_FIELDS,c['ann']);assert prep(c)==0
    ann={x['feature_id']:x for x in read(r/'pb/pas_annotation.tsv')}
    assert 'G001_p' in ann
    assert ann['ZERO_0']['length_bp']=='0' and ann['ZERO_1']['length_bp']=='0'


@rtest
def test_automatic_unpaired_singletons_and_all_quality_excluded(case):
    c=case;r=c['root']
    c['meta']=[x for x in c['meta'] if x[0] in ('S1','S5')]
    c['manifest']=[x for x in c['manifest'] if x[0] in ('S1','S5')]
    write(r/'metadata.tsv',META_FIELDS,c['meta']);write(r/'manifest.tsv',MAN_FIELDS,c['manifest'])
    assert prep(c)==0
    assert run_r(c,name='auto')==0
    report=json.loads((r/'auto/run.json').read_text());assert report['status']=='DESCRIPTIVE_ONLY'
    assert read(r/'auto/versions.tsv')==[{'package':'R','version':'4.5.2'}]
    assert all('FDR' not in x and 'pvalue' not in x for x in read(r/'auto/differential_apa_summary.tsv'))
    assert prep(c,'excluded_all',['--min-cells','3'])==0
    assert len(read(r/'excluded_all/excluded_pseudobulks.tsv'))==2
    assert run_r(c,pb='excluded_all',name='empty')==0
    assert json.loads((r/'empty/run.json').read_text())['status']=='DESCRIPTIVE_ONLY'
    assert not read(r/'empty/differential_apa_summary.tsv')


@rtest
def test_saturated_unused_batch_warns_but_explicit_and_real_confounding_fail(case):
    c=case;r=c['root'];assert prep(c,'reference_pb')==0
    assert run_r(c,pb='reference_pb',name='reference_unadjusted')==0
    for row in c['meta']:row[-1]='unique_'+row[0]
    write(r/'metadata.tsv',META_FIELDS,c['meta']);assert prep(c)==0
    assert run_r(c,name='saturated_omitted')==0
    report=json.loads((r/'saturated_omitted/run.json').read_text())
    assert report['status']=='COMPLETE_WITH_WARNINGS' and report['mode']=='inference'
    assert any('Saturated per-pseudobulk' in x['warning'] and 'unadjusted for batch' in x['warning']
               for x in read(r/'saturated_omitted/warnings.tsv'))
    # The selected design and all scientific outputs equal the original unadjusted fit.
    for name in ('design.tsv','edger_units.tsv','limma_length_units.tsv','pas_counts_used.tsv',
                 'native_unit_counts.tsv','native_distal_usage.tsv','differential_apa_summary.tsv'):
        assert (r/'saturated_omitted'/name).read_bytes()==(r/'reference_unadjusted'/name).read_bytes(),name
    assert run_r(c,name='saturated_explicit',extra=['--batch'])==2
    assert 'confounded or has no residual degrees' in json.loads((r/'saturated_explicit/run.json').read_text())['error']
    # Replacing saturation with genuine two-level condition confounding stays a hard error.
    for row in c['meta']:row[-1]='batch_'+row[4]
    write(r/'metadata.tsv',META_FIELDS,c['meta']);assert prep(c,'confounded_pb')==0
    assert run_r(c,pb='confounded_pb',name='condition_confounded')==2
    assert 'confounded with recorded batch/donor' in json.loads((r/'condition_confounded/run.json').read_text())['error']


@rtest
def test_partial_zero_native_denominator_status_preserves_defined_usage(case):
    from scpolya_diff.prepare import raw_counts
    c=case;r=c['root']
    # Simultaneously exercise zero-denominator coverage and the existing higher
    # priority of unknown distal identity; singleton all-zero NEVER is unchanged.
    for row in c['ann']:
        if row[1]=='UNKNOWN':
            row[8]='NA';row[12]='NA';row[14]='False'
    write(r/'annotation.tsv',ANN_FIELDS,c['ann'])
    p=r/c['manifest'][0][1];rows=[list(x) for x in raw_counts(p)]
    for row in rows:
        if row[0] in ('G119_p','G119_d','UNKNOWN_0','UNKNOWN_1'):row[2]=0
    write(p,['peak_name','cell_barcode','umi_count'],rows)
    assert prep(c)==0
    assert run_r(c,extra=['--methods','edger','--batch'])==0
    effects={x['unit_id']:x for x in read(r/'result/native_distal_effects.tsv')}
    usage={x['unit_id']:x for x in read(r/'result/native_distal_usage.tsv')}
    totals={x['unit_id']:x for x in read(r/'result/native_unit_counts.tsv')}
    unit='G119|%2B'
    assert effects[unit]['native_distal_status']=='zero_native_denominator'
    assert effects[unit]['native_delta_distal_usage_pp']=='NA'
    assert effects[unit]['native_mean_distal_usage_control']=='NA'
    assert usage[unit]['S1']=='NA' and totals[unit]['S1']=='0'
    for i in range(2,9):
        sid=f'S{i}';prox=c['truth'][('G119_p',sid)];dist=c['truth'][('G119_d',sid)]
        assert int(totals[unit][sid])==prox+dist
        assert math.isclose(float(usage[unit][sid]),dist/(prox+dist),rel_tol=1e-15)
    expected_case=sum(float(usage[unit][f'S{i}']) for i in range(5,9))/4
    assert math.isclose(float(effects[unit]['native_mean_distal_usage_case']),expected_case,rel_tol=1e-15)
    assert effects['G118|%2B']['native_distal_status']=='native_catalogue_all_ties'
    assert effects['NEVER|%2B']['native_distal_status']=='fewer_than_two_native_pas'
    assert effects['UNKNOWN|%2B']['native_distal_status']=='unavailable_native_distal_identity'
