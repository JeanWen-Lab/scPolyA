"""QC catalogue validation body from frozen common.load_qc; new receipt bindings in engine."""
import json
from .common import *

def load_rows(request,arm,meta,raw,paths,guard):
 a=request['arms'][arm];out={}
 for chrom,path in paths:
  required=['source_'+k for k in next(iter(raw.values()))] if raw else ['source_peak_name','source_chrom']
  required+=['b0','p0','context_group','atlas_distance','atlas_tied_cluster_ids','atlas_tie_count','atlas_nearest_cluster_id','native_read_end_state',*METRICS,*METRICS.values()]
  for q in tsv(path,required,request['bounds']['catalogue_rows']):
   guard.check() if len(out)%10000==0 else None
   peak=q['source_peak_name'];need(peak in meta and peak not in out,'duplicate/extra QC peak: '+arm+':'+peak)
   m=meta[peak];need(m['chrom']==chrom,'QC contig partition mismatch')
   for k,v in raw[peak].items():need(q['source_'+k]==v,'QC source column mismatch: '+arm+':'+peak+':'+k)
   b=m['endpoint'];need(q['b0']==str(b) and q['p0']==str(b-1 if m['strand']=='+' else b),'QC b0/p0 mismatch')
   ctx='terminal' if m['feature'] in {'Terminal_exon','Downstream_of_terminal_exon'} else ('intronic' if m['feature']=='Intron' else 'other');need(q['context_group']==ctx,'QC source context mismatch')
   values={k:state(q,k) for k in METRICS}
   need(values['native_read_end_pass']==m['native_passed'] if not definition_snapshot_missing_ratio(m) else values['native_read_end_pass'] is None,'native QC/read-pass mismatch')
   need(q['native_read_end_state']==('not_evaluable' if values['native_read_end_pass'] is None else ('pass' if values['native_read_end_pass'] else 'fail')),'native read-end state mismatch')
   dist=None if q['atlas_distance']=='' else int(q['atlas_distance']);ties=None if q['atlas_tied_cluster_ids']=='' else json.loads(q['atlas_tied_cluster_ids'])
   if dist is None:need(values['atlas_concordant'] is None and ties is None,'atlas NA field conflict')
   else:
    need(dist>=0 and isinstance(ties,list) and all(isinstance(x,str) for x in ties) and ties==sorted(set(ties)) and len(ties)>0,'invalid atlas distance/ties')
    need(int(q['atlas_tie_count'])==len(ties) and q['atlas_nearest_cluster_id']==ties[0] and values['atlas_concordant']==(dist<=25),'atlas ties/concordance mismatch')
   out[peak]={'peak':peak,'meta':m,'raw_qc':q,'values':values,'context':ctx,'endpoint':(m['chrom'],m['strand'],m['endpoint']),'atlas_distance':dist,'atlas_ties':ties,'reasons':{k:q[v] for k,v in METRICS.items()}}
 need(set(out)==set(meta) and len(out)==a['expected_catalogue_rows'],'QC/catalogue row-set closure: '+arm)
 return out
