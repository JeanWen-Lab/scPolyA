"""New synthetic source inputs. No case result/expected bytes used."""
from collections import Counter
import csv
import gzip
import json
from pathlib import Path
from .. import api


def table(path, rows, fields=None):
    op = gzip.open if str(path).endswith('.gz') else open
    with op(path, 'wt', newline='') as f:
        w=csv.DictWriter(f, fieldnames=fields or list(rows[0]), delimiter='\t', lineterminator='\n')
        w.writeheader(); w.writerows(rows)
    return {'path':str(path), 'sha256':api.digest(path)}


def desc(path):
    return {'path':str(path), 'sha256':api.digest(path)}


def make(root):
    root=Path(root);root.mkdir(exist_ok=True)
    labels=table(root/'labels.tsv', [dict(barcode='cellA',pass_qc='True',cell_type='T'),
        dict(barcode='cellB',pass_qc='True',cell_type='T'),
        dict(barcode='cellExcluded',pass_qc='False',cell_type='QC_fail')])
    sequence=list('C'*1000);sequence[60:66]='AATAAA';sequence[200:208]='AAAAAAAA'
    fasta=root/'synthetic.fa';fasta.write_text('>chr1\n'+''.join(sequence)+'\n')
    fai=root/'synthetic.fa.fai';fai.write_text('chr1\t1000\t6\t1000\t1001\n')
    atlas=root/'atlas.bed.gz'
    with gzip.open(atlas,'wt') as f:
        f.write('1\t90\t110\t1:100:+\t0\t+\t0\t0\t0\t0\t0\n')
        f.write('1\t490\t510\t1:501:-\t0\t-\t0\t0\t0\t0\t0\n')
    genome=root/'genome.json';genome.write_text(json.dumps({'schema':'scpolya.genome_reference_intake.v1',
        'files':[{'name':p.name,'local_sha256':api.digest(p)} for p in [fasta,fai]]}))
    intake=root/'atlas.json';intake.write_text(json.dumps({'schema':'scpolya.polyasite_v2_reference_intake.v1',
        'files':[{'name':atlas.name,'sha256_observed':api.digest(atlas)}]}))
    refs={k:desc(p) for k,p in {'fasta':fasta,'fai':fai,'atlas':atlas,'genome_receipt':genome,
        'atlas_receipt':intake,'method':api.ROOT/'method/method.frozen.json'}.items()}
    identity={k:'a'*64 for k in api.IDENTITY_KEYS-{'assembly'}}
    identity.update(assembly='GRCh38', registry_sha256=labels['sha256'])
    request={'schema':api.SCHEMA,'arms':{},'registry':labels,'references':refs,
        'contigs':['chr1'],'compared_key':'min_3prime_coverage','output_dir':str(root/'task/analysis')}
    (root/'task').mkdir()
    for arm,coverage in [('coverage3',3),('coverage1',1)]:
        native=root/arm;native.mkdir()
        sites=[]
        for gene,strand,endpoints in [('syntheticG','+',[100,200]+([300] if coverage==1 else [])),
                                      ('minusG','-',[500,600]),('boundaryG','+',[995])]:
            for i,endpoint in enumerate(endpoints):
                sites.append(dict(peak_name=f'{gene}{i}', gene_id=gene,chrom='chr1',
                    start=endpoint-1 if strand=='+' else endpoint,
                    end=endpoint if strand=='+' else endpoint+1,strand=strand,feature='Terminal_exon',
                    canonical_transcript=gene+'.tx', misprimed_ratio='0.1',general_mispriming_pass='True',
                    intronic_mispriming_pass='True',mispriming_pass='True',n_peaks_in_gene=len(endpoints),
                    is_distal_peak=str(i==len(endpoints)-1),distance_to_stop_codon=(i+1)*10))
        catalogue=table(native/'catalogue.tsv.gz',sites)
        counts=[];psis=[];gcs=[]
        for bc in ['cellA','cellB']:
            amounts={s['peak_name']:(2+int(s['peak_name'][-1]) if bc=='cellA' else 0) for s in sites}
            totals=Counter()
            for s in sites:
                if s['n_peaks_in_gene']>=2: totals[s['gene_id'],s['strand']]+=amounts[s['peak_name']]
            for s in sites:
                n=amounts[s['peak_name']];counts.append(dict(peak_name=s['peak_name'],cell_barcode=bc,umi_count=n))
                if s['n_peaks_in_gene']>=2:
                    g,strand=s['gene_id'],s['strand'];t=totals[g,strand]
                    psis.append(dict(peak_name=s['peak_name'],cell_barcode=bc,peak_umi_count=n,gene_umi_count=t,
                        gene_id=g,strand=strand,terminal_gene_unit_id=g+'|'+ ('%2B' if strand=='+' else '-'),psi=n/t if t else ''))
            for (g,strand),total in totals.items():
                gcs.append(dict(gene_id=g,strand=strand,cell_barcode=bc,
                    terminal_gene_unit_id=g+'|'+('%2B' if strand=='+' else '-'),gene_umi_count=total))
        files={'peak_to_gene':catalogue,'counts':table(native/'counts.tsv.gz',counts),
               'psi':table(native/'psi.tsv.gz',psis),'gene_cell':table(native/'gene-cell.tsv.gz',gcs)}
        config={'quantification_mode':'terminal-aware','psi_min_gene_umis':1,'intronic_misprimed_ratio_threshold':0.7,'min_3prime_coverage':coverage,'misprimed_ratio_threshold':0.9,'output_dir':str(native)}
        receipt=native/'receipt.json';receipt.write_text(json.dumps({'run_id':arm,'config':config,'files':files}))
        request['arms'][arm]={'label':arm,'run':{'record_id':arm,'invocation_id':'fixture-'+arm,
            'root':str(native),'config':config,'metadata_pin':desc(receipt)},'config':config,
            'identity':dict(identity),'files':files}
    return request
