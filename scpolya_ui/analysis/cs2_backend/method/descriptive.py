"""Frozen all-catalogue QC comparison; rows and endpoints remain separate units."""
from collections import Counter,defaultdict
import heapq,json,math
from .common import ARMS,METRICS,need,ratio,emit,write_tsv

def code(v):return 'NA' if v is None else str(v).lower()
def summary(rows):
 out={'row_n':len(rows),'metrics':{}}
 for m in METRICS:
  c=Counter(code(r['values'][m]) for r in rows);n=c['true']+c['false']
  out['metrics'][m]={'true_n':c['true'],'false_n':c['false'],'na_n':c['NA'],'evaluable_n':n,'true_fraction_evaluable':ratio(c['true'],n),'na_fraction_all':ratio(c['NA'],len(rows)),'evaluable_fraction_all':ratio(n,len(rows)),'na_reasons':dict(sorted(Counter(r['reasons'][m] for r in rows if r['values'][m] is None).items()))}
 out['four_metric_cross_tabs']=[{'states':list(k),'n':n} for k,n in sorted(Counter(tuple(code(r['values'][m]) for m in METRICS) for r in rows).items())]
 return out

def endpoint_summary(rows):
 groups=defaultdict(list)
 for r in rows:groups[r['endpoint']].append(r)
 out={'endpoint_n':len(groups),'metrics':{}}
 for metric in METRICS:
  bad=[list(k) for k,rs in sorted(groups.items()) if len({code(r['values'][metric]) for r in rs})>1]
  if bad:out['metrics'][metric]={'status':'NOT_DISPLAYED_INCONSISTENT_ENDPOINT_METRIC','inconsistent_groups':bad,'summary':None};continue
  representatives=[]
  for rs in groups.values():
   r=dict(rs[0]);r['reasons']=dict(r['reasons']);r['reasons'][metric]='|'.join(sorted({x['reasons'][metric] for x in rs}));representatives.append(r)
  out['metrics'][metric]={'status':'CONSISTENT','summary':summary(representatives)['metrics'][metric]}
 return out

def endpoint_sets(data):
 groups={arm:defaultdict(list) for arm in ARMS}
 for arm in ARMS:
  for r in data[arm].values():groups[arm][r['endpoint']].append(r)
 return groups,set(groups['coverage3'])&set(groups['coverage1'])

def composition(data,missing):
 out={}
 for metric in METRICS:
  strata={a:defaultdict(lambda:[0,0]) for a in ARMS};excluded={};all_eval={}
  for arm in ARMS:
   excluded[arm]=sum(missing(r['meta']['gene_id']) for r in data[arm].values());all_eval[arm]=sum(r['values'][metric] is not None for r in data[arm].values())
   for r in data[arm].values():
    if missing(r['meta']['gene_id']):continue
    k=(r['meta']['gene_id'],r['context'],r['meta']['strand']);v=r['values'][metric];strata[arm][k][0]+=v is not None;strata[arm][k][1]+=v is True
  support=sorted(k for k in set(strata['coverage3'])&set(strata['coverage1']) if strata['coverage3'][k][0]>0 and strata['coverage1'][k][0]>0)
  total={a:sum(strata[a][k][0] for k in support) for a in ARMS};rows=[]
  for k in support:
   e3,t3=strata['coverage3'][k];e1,t1=strata['coverage1'][k];rows.append({'gene_id':k[0],'context':k[1],'strand':k[2],'coverage3_evaluable_n':e3,'coverage3_true_n':t3,'coverage1_evaluable_n':e1,'coverage1_true_n':t1,'weight_numerator':e3,'weight_denominator':total['coverage3']})
  rates={a:math.fsum((strata['coverage3'][k][0]/total['coverage3'])*(strata[a][k][1]/strata[a][k][0]) for k in support) if support else None for a in ARMS}
  out[metric]={'support_strata_n':len(support),'excluded_missing_gene_rows':excluded,'excluded_strata_n':{a:len(strata[a])-len(support) for a in ARMS},'total_evaluable_n_all_rows':all_eval,'support_evaluable_n':total,'support_coverage_fraction':{a:ratio(total[a],all_eval[a]) for a in ARMS},'standardized_true_fraction':rates,'coverage1_minus_coverage3':rates['coverage1']-rates['coverage3'] if support else None,'strata':rows,'interpretation':'Fixed common evaluable strata only; original pooled composition not a paired-site improvement.'}
 return out

def interval_components(data,shared,bound,guard):
 """Same-strand half-open overlap graph over all records, no nearest/tolerance pairing."""
 nodes={(a,p):(a,p) for a in ARMS for p in data[a]}
 def find(x):
  while nodes[x]!=x:nodes[x]=nodes[nodes[x]];x=nodes[x]
  return x
 def union(x,y):
  x,y=find(x),find(y)
  if x!=y:nodes[max(x,y)]=min(x,y)
 groups=defaultdict(list)
 for arm in ARMS:
  for p,r in data[arm].items():groups[(r['meta']['chrom'],r['meta']['strand'])].append((r['meta']['start'],arm,p))
 edges=[]
 for key,events in sorted(groups.items()):
  active={a:{} for a in ARMS};heaps={a:[] for a in ARMS}
  for lo,arm,p in sorted(events):
   for a in ARMS:
    while heaps[a] and heaps[a][0][0]<=lo:
     _,old=heapq.heappop(heaps[a]);active[a].pop(old,None)
   other=ARMS[1] if arm==ARMS[0] else ARMS[0]
   for q in sorted(active[other]):
    need(len(edges)<bound,'overlap edge budget exceeded');a,b=((arm,p),(other,q)) if arm=='coverage3' else ((other,q),(arm,p));edges.append((a,b));union(a,b)
   hi=data[arm][p]['meta']['end'];active[arm][p]=hi;heapq.heappush(heaps[arm],(hi,p))
  guard.check()
 components=defaultdict(lambda:{'coverage3':[],'coverage1':[],'overlap_edges_n':0})
 for x in sorted(nodes):components[find(x)][x[0]].append(x[1])
 for a,b in edges:components[find(a)]['overlap_edges_n']+=1
 out=[]
 for _,c in sorted(components.items()):
  n3,n1=len(c['coverage3']),len(c['coverage1']);c['type']='unmatched_coverage3' if n1==0 else ('unmatched_coverage1' if n3==0 else ('one_to_many' if min(n3,n1)==1 and max(n3,n1)>1 else 'many_to_many'))
  if n3==n1==1:
   a,b=(data[arm][c[arm][0]] for arm in ARMS);c['type']='one_to_one_with_exact_shared_endpoint' if a['endpoint'] in shared or b['endpoint'] in shared else 'unique_overlap_different_endpoint_candidate'
   c.update({'signed_b0_shift_genomic':b['endpoint'][2]-a['endpoint'][2],'signed_b0_shift_transcript':(b['endpoint'][2]-a['endpoint'][2])*(1 if a['meta']['strand']=='+' else -1),'width_change':(b['meta']['end']-b['meta']['start'])-(a['meta']['end']-a['meta']['start']),'gene_ids':[a['meta']['gene_id'],b['meta']['gene_id']],'contexts':[a['context'],b['context']],'qc_states':{m:[code(a['values'][m]),code(b['values'][m])] for m in METRICS}})
  out.append(c)
 return out

def analyze(data,out,bound,guard,missing):
 out.mkdir();groups,shared=endpoint_sets(data);summaries=[];endpoint_rows=[];transitions=[];invariants=[]
 for arm in ARMS:
  strata=defaultdict(list)
  for r in data[arm].values():
   rel='shared' if r['endpoint'] in shared else arm+'_only';r['relation']=rel;m=r['meta'];native=r['raw_qc']['native_read_end_state']
   keys=[('all',),('relation',rel),('context',r['context']),('feature',m['feature']),('strand',m['strand']),('contig',m['chrom']),('native_pass',native),('relation_context_strand_contig',rel,r['context'],m['strand'],m['chrom']),('relation_context_native_pass',rel,r['context'],native)]
   for k in keys:strata[k].append(r)
  for key,rs in sorted(strata.items()):summaries.append({'arm':arm,'stratum':list(key),'record_summary':summary(rs),'unique_endpoint_summary':endpoint_summary(rs)})
  coord=Counter((r['meta']['chrom'],r['meta']['strand'],r['meta']['start'],r['meta']['end']) for r in data[arm].values())
  for ep,rs in sorted(groups[arm].items()):endpoint_rows.append({'arm':arm,'chrom':ep[0],'strand':ep[1],'b0':ep[2],'relation':'shared' if ep in shared else arm+'_only','native_rows':len(rs),'peak_names':sorted(r['peak'] for r in rs),'gene_ids':sorted({r['meta']['gene_id'] for r in rs}),'features':sorted({r['meta']['feature'] for r in rs}),'contexts':sorted({r['context'] for r in rs}),'native_pass_states':sorted({r['raw_qc']['native_read_end_state'] for r in rs}),'coordinate_group_multiplicities':sorted({coord[r['meta']['chrom'],r['meta']['strand'],r['meta']['start'],r['meta']['end']] for r in rs})})
 for ep in sorted(shared):
  a,b=groups['coverage3'][ep],groups['coverage1'][ep];fixed=len(a)==len(b)==1 and not missing(a[0]['meta']['gene_id']) and a[0]['meta']['gene_id']==b[0]['meta']['gene_id'] and a[0]['context']==b[0]['context']
  transitions.append({'endpoint':list(ep),'coverage3_peak_names':sorted(r['peak'] for r in a),'coverage1_peak_names':sorted(r['peak'] for r in b),'coverage3_gene_ids':sorted({r['meta']['gene_id'] for r in a}),'coverage1_gene_ids':sorted({r['meta']['gene_id'] for r in b}),'coverage3_contexts':sorted({r['context'] for r in a}),'coverage1_contexts':sorted({r['context'] for r in b}),'fixed_one_to_one_gene_context':fixed,'qc_states_fixed_pair':{m:[code(a[0]['values'][m]),code(b[0]['values'][m])] for m in METRICS} if fixed else None})
  for name,fun in [('motif_present',lambda r:r['values']['motif_present']),('downstream_a_risk',lambda r:r['values']['downstream_a_risk']),('atlas_distance',lambda r:r['atlas_distance']),('atlas_tied_cluster_ids',lambda r:r['atlas_ties'])]:
   vals={json.dumps(fun(r),sort_keys=True) for r in a+b}
   if len(vals)!=1:invariants.append({'endpoint':list(ep),'field':name,'states':sorted(vals)})
 for arm in ARMS:
  for ep,rs in sorted(groups[arm].items()):
   for name,fun in [('motif_present',lambda r:r['values']['motif_present']),('downstream_a_risk',lambda r:r['values']['downstream_a_risk']),('atlas_distance',lambda r:r['atlas_distance']),('atlas_tied_cluster_ids',lambda r:r['atlas_ties'])]:
    states={json.dumps(fun(r),sort_keys=True) for r in rs}
    if len(states)>1:invariants.append({'arm':arm,'endpoint':list(ep),'field':'within_arm_'+name,'states':sorted(states)})
 emit(out/'summaries.json',summaries);emit(out/'endpoint-groups.json',endpoint_rows);emit(out/'shared-endpoint-transitions.json',transitions);emit(out/'reference-invariance.json',invariants);emit(out/'composition-standardization.json',composition(data,missing));emit(out/'interval-components.json',interval_components(data,shared,bound,guard))
 # Preserve every original QC/source column; a union header is explicit, empty values remain producer NA.
 fields=['arm','endpoint_relation']+sorted({k for a in ARMS for r in data[a].values() for k in r['raw_qc']})
 write_tsv(out/'all-catalogue-qc.tsv',fields,({'arm':arm,'endpoint_relation':r['relation'],**{k:r['raw_qc'].get(k,'') for k in fields[2:]}} for arm in ARMS for _,r in sorted(data[arm].items())))
 need(not invariants,'QC reference/endpoint inconsistency: see descriptive/reference-invariance.json; no mask computed')
 duplicates={}
 for arm in ARMS:
  coords=Counter((r['meta']['chrom'],r['meta']['strand'],r['meta']['start'],r['meta']['end']) for r in data[arm].values());eps=Counter(r['endpoint'] for r in data[arm].values());duplicates[arm]={name:{'unique_groups':len(c),'repeated_groups':sum(v>1 for v in c.values()),'rows_in_repeated_groups':sum(v for v in c.values() if v>1),'excess_records':sum(v-1 for v in c.values())} for name,c in [('coordinate',coords),('endpoint',eps)]}
 emit(out/'duplicate-accounting.json',duplicates)
 return {'site_rows':{a:len(data[a]) for a in ARMS},'unique_endpoints':{a:len(groups[a]) for a in ARMS},'exact_shared_endpoint_n':len(shared),'reference_invariance_issues':len(invariants),'quality_truth_claim':False}
