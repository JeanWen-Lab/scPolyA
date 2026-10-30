"""Raw assigned-count SQLite aggregation; no retained-site UMI reassignment or PSI reuse."""
from collections import Counter,defaultdict
import sqlite3
from .common import ARMS,MODES,DEFS,COHORTS,CUTOFFS,need,ratio,emit,write_tsv,tsv

def retained(r,mode):
 v=r['values']['downstream_a_risk'];return mode=='unmasked' or v is False or mode=='a_risk_false_or_unknown' and v is None

def catalogue(data,definitions,guard):
 pools={};eligible={};ledger=[]
 for mode in MODES:
  for arm in ARMS:
   for definition in DEFS:
    rs=defaultdict(list)
    for p,r in data[arm].items():
     m=r['meta'];base=m['passed'] and not definitions.missing(m['gene_id']) and (m['feature'] in definitions.NATIVE if definition=='native_terminal' else m['feature']=='Terminal_exon' and not definitions.missing(m['canonical_transcript']))
     if base and retained(r,mode):rs[m['unit']].append(p)
    pools[arm,mode,definition]=dict(rs)
    eligible[arm,mode,definition]={g for g,ps in rs.items() if (len(ps) if definition=='native_terminal' else len({data[arm][p]['endpoint'] for p in ps}))>=2}
    if mode!='unmasked':need(eligible[arm,mode,definition]<=eligible[arm,'unmasked',definition],'eligibility regained through deletion')
   guard.check()
  for definition in DEFS:
   shared=eligible['coverage3',mode,definition]&eligible['coverage1',mode,definition]
   if definition=='native_terminal':
    for gene in shared:
     tx=[{data[a][p]['meta']['canonical_transcript'] for p in pools[a,mode,definition][gene] if not definitions.missing(data[a][p]['meta']['canonical_transcript'])} for a in ARMS]
     need(tx[0]==tx[1],'cross-arm retained native transcript mismatch')
   for arm in ARMS:
    baseunits=set(pools[arm,'unmasked',definition])
    for g in sorted(baseunits):
     ps=pools[arm,mode,definition].get(g,[]);ep={data[arm][p]['endpoint'] for p in ps};ok=g in eligible[arm,mode,definition]
     ledger.append({'arm':arm,'mode':mode,'definition':definition,'gene_id':g[0],'strand':g[1],'retained_peak_records':len(ps),'retained_endpoints':len(ep),'eligible':ok,'stratum':'shared_eligible_genes' if ok and g in shared else ('arm_only_eligible_genes' if ok else 'ineligible'),'original_unmasked_eligible':g in eligible[arm,'unmasked',definition]})
 return pools,eligible,ledger

def create_db(path,data,request,cohorts,definitions,guard):
 db=sqlite3.connect(path);db.execute('PRAGMA cache_size=-65536');db.execute('PRAGMA temp_store=FILE');db.execute('PRAGMA journal_mode=DELETE')
 guard.sqlite_phase='load_raw_counts';db.set_progress_handler(guard.progress,100000)
 db.executescript('''CREATE TABLE p(arm TEXT,peak TEXT,g TEXT,s TEXT,chrom TEXT,endpoint INTEGER,native INTEGER,strict INTEGER,risk INTEGER,reason TEXT,valid_gene INTEGER,PRIMARY KEY(arm,peak)) WITHOUT ROWID;
 CREATE TABLE c(arm TEXT,peak TEXT,bc TEXT,n INTEGER,PRIMARY KEY(arm,peak,bc)) WITHOUT ROWID;
 CREATE TABLE b(bc TEXT PRIMARY KEY,selected INTEGER) WITHOUT ROWID;
 CREATE TABLE obs(arm TEXT,mode TEXT,definition TEXT,g TEXT,s TEXT,bc TEXT,total INTEGER,positive_peak_records INTEGER,positive_endpoints INTEGER,PRIMARY KEY(arm,mode,definition,g,s,bc)) WITHOUT ROWID;''')
 db.executemany('INSERT INTO b VALUES(?,?)',((bc,int(bc in cohorts['qc_assigned_labels'])) for bc in sorted(cohorts['full_registry'])))
 rawstats={}
 for arm in ARMS:
  for p,r in data[arm].items():
   m=r['meta'];valid=not definitions.missing(m['gene_id']);native=m['passed'] and valid and m['feature'] in definitions.NATIVE;strict=m['passed'] and valid and m['feature']=='Terminal_exon' and not definitions.missing(m['canonical_transcript']);risk=r['values']['downstream_a_risk']
   db.execute('INSERT INTO p VALUES(?,?,?,?,?,?,?,?,?,?,?)',(arm,p,m['gene_id'],m['strand'],m['chrom'],m['endpoint'],int(native),int(strict),-1 if risk is None else int(risk),r['reasons']['downstream_a_risk'],int(valid)))
  n=zero=0;cells=set()
  for r in tsv(request['arms'][arm]['counts']['path'],['peak_name','cell_barcode','umi_count'],request['bounds']['raw_rows_per_arm']):
   p,bc=r['peak_name'],r['cell_barcode'];need(p in data[arm] and bc in cohorts['full_registry'],'unknown raw count peak/barcode');value=definitions.integer(r['umi_count']);need(value<=2**63-1,'SQLite integer overflow')
   try:db.execute('INSERT INTO c VALUES(?,?,?,?)',(arm,p,bc,value))
   except sqlite3.IntegrityError as e:raise ValueError('duplicate raw arm/peak/barcode count') from e
   cells.add(bc);zero+=value==0;n+=1
   if n%10000==0:db.commit();guard.check()
  db.commit();rawstats[arm]={'raw_rows':n,'raw_explicit_zero_rows':zero,'raw_observed_cells':len(cells)}
 return db,rawstats

def build_obs(db,pools,eligible,request,guard):
 db.executescript('CREATE TABLE e(arm TEXT,mode TEXT,definition TEXT,g TEXT,s TEXT,PRIMARY KEY(arm,mode,definition,g,s)) WITHOUT ROWID;')
 db.executemany('INSERT INTO e VALUES(?,?,?,?,?)',((a,m,d,g,s) for (a,m,d),gs in eligible.items() for g,s in sorted(gs)));db.commit()
 for arm in ARMS:
  for mode in MODES:
   for definition in DEFS:
    flag='native' if definition=='native_terminal' else 'strict';mask='1' if mode=='unmasked' else ('p.risk=0' if mode=='a_risk_false' else 'p.risk!=1')
    sql=f'''INSERT INTO obs SELECT ?,?,?,g,s,bc,SUM(n),SUM(nrecords),SUM(CASE WHEN n>0 THEN 1 ELSE 0 END) FROM
      (SELECT p.g,p.s,c.bc,p.chrom,p.endpoint,SUM(c.n) n,SUM(CASE WHEN c.n>0 THEN 1 ELSE 0 END) nrecords
       FROM c JOIN p ON c.arm=p.arm AND c.peak=p.peak JOIN e ON e.arm=p.arm AND e.g=p.g AND e.s=p.s
       WHERE c.arm=? AND e.mode=? AND e.definition=? AND p.{flag}=1 AND ({mask})
       GROUP BY p.g,p.s,c.bc,p.chrom,p.endpoint) GROUP BY g,s,bc HAVING SUM(n)>0'''
    guard.sqlite_phase=f'build_obs:{arm}:{mode}:{definition}'
    db.execute(sql,(arm,mode,definition,arm,mode,definition));db.commit()
    count=db.execute('SELECT COUNT(*) FROM obs WHERE arm=? AND mode=? AND definition=?',(arm,mode,definition)).fetchone()[0];need(count<=request['bounds']['observations_per_arm_mode_definition'],'observations row bound');guard.check()

def measurement_rows(db,eligible,cohorts):
 rows=[]
 for arm in ARMS:
  for mode in MODES:
   for definition in DEFS:
    own=eligible[arm,mode,definition];other=eligible[ARMS[1] if arm==ARMS[0] else ARMS[0],mode,definition];shared=own&other
    sets={'all_eligible':own,'shared_eligible_genes':shared,'arm_only_eligible_genes':own-shared}
    rules=('native_peak_record','exact_endpoint') if definition=='native_terminal' else ('exact_endpoint',)
    obs=Counter();quals=Counter();genes=defaultdict(set);cells=defaultdict(set)
    for g,s,bc,total,nr,ne in db.execute('SELECT g,s,bc,total,positive_peak_records,positive_endpoints FROM obs WHERE arm=? AND mode=? AND definition=?',(arm,mode,definition)):
     subsets=['all_eligible','shared_eligible_genes' if (g,s) in shared else 'arm_only_eligible_genes']
     for cohort in COHORTS:
      if bc not in cohorts[cohort]:continue
      for subset in subsets:
       obs[cohort,subset]+=1
       for rule in rules:
        positive=nr if rule=='native_peak_record' else ne
        if positive<2:continue
        for cutoff in CUTOFFS:
         if total<cutoff:continue
         key=(cohort,subset,rule,cutoff);quals[key]+=1;genes[key].add((g,s));cells[key].add(bc)
    for cohort in COHORTS:
     for subset,gs in sets.items():
      for rule in rules:
       for cutoff in CUTOFFS:
        k=(cohort,subset,rule,cutoff);n=quals[k];ng=len(genes[k]);nc=len(cells[k]);den=obs[cohort,subset]
        rows.append({'arm':arm,'mode':mode,'definition':definition,'site_identity_rule':rule,'cohort':cohort,'gene_subset':subset,'umi_cutoff':cutoff,'catalogue_eligible_gene_strand_units':len(gs),'selected_cells':len(cohorts[cohort]),'observed_positive_gene_strand_cell_units':den,'qualifying_gene_strand_cell_units':n,'qualifying_gene_strand_units':ng,'qualifying_cells':nc,'fraction_of_observed_units':ratio(n,den),'fraction_of_catalogue_units':ratio(ng,len(gs)),'fraction_of_selected_cells':ratio(nc,len(cohorts[cohort]))})
 return rows

def transitions(db,cohorts):
 out=[]
 for mode in MODES:
  for definition in DEFS:
   # Shared positive keys only; missing observations are deliberately absent from the four cells.
   states=Counter();only=Counter()
   sql='''SELECT a.bc,a.total,a.positive_endpoints,b.total,b.positive_endpoints FROM obs a LEFT JOIN obs b
    ON b.arm='coverage1' AND b.mode=a.mode AND b.definition=a.definition AND b.g=a.g AND b.s=a.s AND b.bc=a.bc
    WHERE a.arm='coverage3' AND a.mode=? AND a.definition=?'''
   for bc,ta,pa,tb,pb in db.execute(sql,(mode,definition)):
    for cohort in COHORTS:
     if bc not in cohorts[cohort]:continue
     if tb is None:only[cohort,'coverage3']+=1;continue
     for cutoff in CUTOFFS:states[cohort,cutoff,pa>=2 and ta>=cutoff,pb>=2 and tb>=cutoff]+=1
   sql='''SELECT b.bc FROM obs b LEFT JOIN obs a ON a.arm='coverage3' AND a.mode=b.mode AND a.definition=b.definition AND a.g=b.g AND a.s=b.s AND a.bc=b.bc WHERE b.arm='coverage1' AND b.mode=? AND b.definition=? AND a.bc IS NULL'''
   for (bc,) in db.execute(sql,(mode,definition)):
    for cohort in COHORTS:
     if bc in cohorts[cohort]:only[cohort,'coverage1']+=1
   for cutoff in CUTOFFS:
    for cohort in COHORTS:out.append({'mode':mode,'definition':definition,'cohort':cohort,'cutoff':cutoff,'site_identity_rule':'exact_endpoint','direction':'coverage3_to_coverage1','transitions':[{'coverage3_qualifies':a,'coverage1_qualifies':b,'n':states[cohort,cutoff,a,b]} for a in (False,True) for b in (False,True)],'coverage3_only_observed_records':only[cohort,'coverage3'],'coverage1_only_observed_records':only[cohort,'coverage1']})
 return out

def source_catalogues(data,rawstats,definitions):
 out={}
 for arm in ARMS:
  meta={p:r['meta'] for p,r in data[arm].items()};native=defaultdict(list);strict=defaultdict(list);allpass=defaultdict(list)
  for r in meta.values():
   if r['native_passed'] and not definitions.missing(r['gene_id']):allpass[r['unit']].append(r)
   if r['passed'] and not definitions.missing(r['gene_id']):
    if r['feature'] in definitions.NATIVE:native[r['unit']].append(r)
    if r['feature']=='Terminal_exon' and not definitions.missing(r['canonical_transcript']):strict[r['unit']].append(r)
  ne={k for k,v in native.items() if len(v)>=2}
  out[arm]={'all_annotated_records':len(meta),'all_annotated_exact_endpoint_groups':len({(r['chrom'],r['strand'],r['endpoint']) for r in meta.values()}),'native_context_pass_assigned_gene_strand_units_ge2_records':sum(len(v)>=2 for v in allpass.values()),'retained_native_terminal_records':sum(map(len,native.values())),'retained_native_terminal_units':len(native),'native_multi_peak_units':len(ne),'representative_terminal_exon_multi_endpoint_units':sum(len({(r['chrom'],r['endpoint']) for r in v})>=2 for v in strict.values()),'native_multi_units_with_one_exact_endpoint':sum(len({r['endpoint'] for r in native[k]})<2 for k in ne),'context_records':dict(Counter(r['feature'] for r in meta.values())),**rawstats[arm]}
 return out

def exact_unmasked_gate(rows,transition,catalogues,expected,out):
 observed={'arms':{},'comparisons':{}}
 for arm in ARMS:observed['arms'][arm]={'catalogue':catalogues[arm],'measurements':[{k:v for k,v in r.items() if k not in {'arm','mode'}} for r in rows if r['arm']==arm and r['mode']=='unmasked']}
 for definition in DEFS:observed['comparisons'][definition]=[{k:v for k,v in t.items() if k not in {'mode','definition'}} for t in transition if t['mode']=='unmasked' and t['definition']==definition]
 diffs=[]
 for arm in ARMS:
  target=dict(expected['arms'][arm]['catalogue']);annotation=target.pop('native_psi_validation',None)
  if target!=catalogues[arm]:diffs.append({'arm':arm,'family':'catalogue','expected':target,'observed':catalogues[arm]})
  if expected['arms'][arm]['measurements']!=observed['arms'][arm]['measurements']:diffs.append({'arm':arm,'family':'measurements','expected':expected['arms'][arm]['measurements'],'observed':observed['arms'][arm]['measurements']})
 if expected['comparisons']!=observed['comparisons']:diffs.append({'family':'transitions','expected':expected['comparisons'],'observed':observed['comparisons']})
 emit(out/'unmasked-parity.json',{'status':'PASS' if not diffs else 'REFUSED','measurement_rows':sum(len(v['measurements']) for v in observed['arms'].values()),'transition_cells':sum(len(t['transitions']) for ts in observed['comparisons'].values() for t in ts),'catalogue_statistics_recomputed':True,'native_psi_validation_annotation':'Native PSI validated by same-run paired path; text excluded from numerical equality.','differences':diffs})
 need(not diffs,'same-run unmasked consistency failed; see masking/unmasked-parity.json')

def attrition(db,eligible,cohorts):
 rows=[]
 for arm in ARMS:
  for definition in DEFS:
   base=eligible[arm,'unmasked',definition];stats=Counter()
   for g,s,bc,n in db.execute("SELECT g,s,bc,total FROM obs WHERE arm=? AND mode='unmasked' AND definition=?",(arm,definition)):
    for mode in MODES[1:]:
     state='retained_eligible' if (g,s) in eligible[arm,mode,definition] else 'lost_eligible'
     for cohort in COHORTS:
      if bc in cohorts[cohort]:stats[mode,cohort,state,'observations']+=1;stats[mode,cohort,state,'umis']+=n
   for mode in MODES[1:]:
    for cohort in COHORTS:
     for state,gs in [('retained_eligible',base&eligible[arm,mode,definition]),('lost_eligible',base-eligible[arm,mode,definition])]:rows.append({'arm':arm,'mode':mode,'definition':definition,'cohort':cohort,'unmasked_eligible_units':len(base),'state':state,'gene_strand_units':len(gs),'original_unmasked_positive_observations':stats[mode,cohort,state,'observations'],'original_unmasked_assigned_umis':stats[mode,cohort,state,'umis'],'basis':'Original unmasked support of the fixed original eligible set; not a masked total or zero-imputed result.'})
 return rows

def unknown_loss(db,data,cohorts):
 rows=[]
 for arm in ARMS:
  for domain,flag in [('all_source_records','1'),('native_pass_terminal_pool','p.native=1'),('strict_pass_terminal_pool','p.strict=1')]:
   sites=list(db.execute(f'SELECT peak,g,s,chrom,endpoint,reason,valid_gene FROM p WHERE arm=? AND risk=-1 AND ({flag})',(arm,)))
   for reason in sorted({x[5] for x in sites}):
    chosen=[x for x in sites if x[5]==reason]
    for cohort in COHORTS:
     select='1' if cohort=='full_registry' else 'b.selected=1'
     total=db.execute(f'SELECT COALESCE(SUM(c.n),0) FROM c JOIN p ON c.arm=p.arm AND c.peak=p.peak JOIN b ON c.bc=b.bc WHERE c.arm=? AND p.risk=-1 AND p.reason=? AND ({flag}) AND ({select})',(arm,reason)).fetchone()[0]
     sql=f'''SELECT COUNT(*) FROM (SELECT p.g,p.s,c.bc FROM c JOIN p ON c.arm=p.arm AND c.peak=p.peak JOIN b ON c.bc=b.bc WHERE c.arm=? AND p.risk=-1 AND p.reason=? AND p.valid_gene=1 AND ({flag}) AND ({select}) GROUP BY p.g,p.s,c.bc HAVING SUM(c.n)>0)'''
     affected=db.execute(sql,(arm,reason)).fetchone()[0]
     rows.append({'arm':arm,'pool':domain,'cohort':cohort,'reason':reason,'catalogue_records':len(chosen),'unique_endpoints':len({(x[3],x[2],x[4]) for x in chosen}),'assigned_gene_strand_units':len({(x[1],x[2]) for x in chosen if x[6]}),'missing_gene_records':sum(not x[6] for x in chosen),'raw_assigned_umis':total,'positive_gene_strand_cell_keys_with_unknown_support':affected,'site_denominator_is_cohort_independent':True,'not_additive_with_true_risk_losses':True})
 return rows

def analyze(data,request,out,cohorts,definitions,expected,guard):
 out.mkdir();pools,eligible,ledger=catalogue(data,definitions,guard)
 write_tsv(out/'gene-eligibility.tsv',list(ledger[0]) if ledger else ['arm','mode','definition','gene_id','strand','retained_peak_records','retained_endpoints','eligible','stratum','original_unmasked_eligible'],ledger)
 site_rows=[];filtered=[]
 for arm in ARMS:
  for mode in MODES:
   for peak,r in sorted(data[arm].items()):
    m=r['meta'];site_rows.append({'arm':arm,'mode':mode,'peak_name':peak,'gene_id':m['gene_id'],'chrom':m['chrom'],'start':m['start'],'end':m['end'],'strand':m['strand'],'b0':m['endpoint'],'feature':m['feature'],'downstream_a_risk':'NA' if r['values']['downstream_a_risk'] is None else str(r['values']['downstream_a_risk']).lower(),'downstream_reason':r['reasons']['downstream_a_risk'],'mask_retained':retained(r,mode),'native_general_pass':m['passed'],'native_context_pass':m['native_passed']})
   filtered.append({'arm':arm,'mode':mode,'source_records':len(data[arm]),'mask_retained_source_records':sum(retained(r,mode) for r in data[arm].values()),'definitions':{d:{'retained_pool_records':sum(map(len,pools[arm,mode,d].values())),'retained_pool_units':len(pools[arm,mode,d]),'eligible_units':len(eligible[arm,mode,d])} for d in DEFS}})
 write_tsv(out/'site-mask-ledger.tsv',list(site_rows[0]) if site_rows else ['arm','mode','peak_name'],site_rows);emit(out/'filtered-catalogues.json',filtered)
 db=None
 try:
  db,rawstats=create_db(out/'counts-and-observations.sqlite',data,request,cohorts,definitions,guard);build_obs(db,pools,eligible,request,guard)
  rows=measurement_rows(db,eligible,cohorts);transition=transitions(db,cohorts);cats=source_catalogues(data,rawstats,definitions)
  emit(out/'measurements.json',rows);write_tsv(out/'measurements.tsv',list(rows[0]),rows);emit(out/'transitions.json',transition);emit(out/'unmasked-catalogues.json',cats)
  exact_unmasked_gate(rows,transition,cats,expected,out)
  emit(out/'attrition.json',attrition(db,eligible,cohorts));emit(out/'unknown-loss.json',unknown_loss(db,data,cohorts))
  # The difference of fixed missingness alternatives is descriptive, never a chosen best mask.
  lookup={(r['arm'],r['mode'],r['definition'],r['cohort'],r['gene_subset'],r['site_identity_rule'],r['umi_cutoff']):r for r in rows};diff=[]
  for key,a in lookup.items():
   if key[1]!='a_risk_false':continue
   b=lookup[(key[0],'a_risk_false_or_unknown',*key[2:])];diff.append({'arm':key[0],'definition':key[2],'cohort':key[3],'gene_subset':key[4],'site_identity_rule':key[5],'umi_cutoff':key[6],'false_only':a,'false_or_unknown':b,'warning':'Strata/denominators are mode-specific; do not interpret unequal shared/only sets as paired causal losses.'})
  emit(out/'predeclared-missingness-sensitivity.json',diff)
  def observed_rows():
   for i,row in enumerate(db.execute('SELECT arm,mode,definition,g,s,bc,total,positive_peak_records,positive_endpoints FROM obs ORDER BY arm,mode,definition,g,s,bc'),1):
    if i%10000==0:guard.check()
    yield dict(zip(['arm','mode','definition','gene_id','strand','cell_barcode','terminal_umis','positive_peak_records','positive_endpoints'],row))
  write_tsv(out/'positive-observations.tsv',['arm','mode','definition','gene_id','strand','cell_barcode','terminal_umis','positive_peak_records','positive_endpoints'],observed_rows());guard.check()
  return {'measurements':len(rows),'transition_cells':sum(len(t['transitions']) for t in transition),'unmasked_parity':'PASS','masked_result_kind':'Predeclared sensitivity with postfreeze schema/reporting clarification, not validated site quality.'}
 except sqlite3.OperationalError as sqlite_error:
  guard.sqlite_error={'exception_type':type(sqlite_error).__name__,'message':str(sqlite_error),'sqlite_phase':guard.sqlite_phase}
  if guard.callback_exception is not None:raise guard.callback_exception from sqlite_error
  raise
 finally:
  if db is not None:db.close()
