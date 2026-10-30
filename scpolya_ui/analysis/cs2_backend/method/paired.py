"""Mechanically extracted accepted CS2 measurement definitions; no production entry or old-result gate.
Caller must provide source pins, fixed cohort and interpreter-start scratch.
"""
import argparse, csv, gzip, hashlib, json, math, sqlite3

from collections import Counter, defaultdict

from decimal import Decimal

from pathlib import Path

from urllib.parse import quote

NA={'','na','nan','none','null','<na>'}

NATIVE={'Terminal_exon','Downstream_of_terminal_exon'}

ROLES=('peak_to_gene','counts','psi','gene_cell')

def need(ok,message):
 if not ok: raise ValueError(message)

def digest(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def missing(v):return str(v).strip().lower() in NA

def integer(v):
 d=Decimal(str(v)); need(d.is_finite() and d>=0 and d==d.to_integral_value(),'noninteger/nonfinite/negative UMI: '+str(v));return int(d)

def boolean(v):
 value=str(v).strip().lower()
 need(value in {'true','false'},'invalid bool '+str(v));return value=='true'

def ratio(a,b):return a/b if b else None

def uid(g,s):return quote(g,safe='')+'|'+quote(s,safe='')

def rows(path,required,limit=25000000):
 op=gzip.open if str(path).endswith('.gz') else open
 with op(path,'rt',newline='') as f:
  reader=csv.DictReader(f,delimiter='\t');need(reader.fieldnames and len(reader.fieldnames)==len(set(reader.fieldnames)) and set(required)<=set(reader.fieldnames),'missing/duplicate columns '+str(path))
  for i,r in enumerate(reader,1):
   need(i<=limit and None not in r and None not in r.values(),'row bound or malformed TSV '+str(path));yield r

def cohort_registry(request):
 rs=list(rows(request['registry']['path'],['barcode','pass_qc','cell_type'],100000))
 allcells={r['barcode'] for r in rs};need(len(allcells)==len(rs)==request['registry']['expected_rows'],'registry duplicates/scope')
 assigned=[r for r in rs if boolean(r['pass_qc']) and not r['cell_type'].startswith(('Unassigned','QC_fail'))]
 frequencies=Counter(r['cell_type'] for r in assigned);minclass=20 if len(assigned)>=500 else 1
 selected={r['barcode'] for r in assigned if frequencies[r['cell_type']]>=minclass}
 need(len(selected)==request['registry']['expected_selected'],'selected-label cohort mismatch')
 return {'full_registry':allcells,'qc_assigned_labels':selected}

def metadata(path,threshold):
 required=['peak_name','gene_id','chrom','start','end','strand','feature','canonical_transcript','misprimed_ratio','general_mispriming_pass','mispriming_pass','n_peaks_in_gene','is_distal_peak','distance_to_stop_codon']
 meta={};native=defaultdict(list);strict=defaultdict(list);allpass=defaultdict(list)
 for r in rows(path,required,250000):
  p=r['peak_name'];need(p not in meta,'duplicate peak ID');r=dict(r);r['start']=integer(r['start']);r['end']=integer(r['end'])
  need(r['strand'] in {'+','-'} and r['end']>r['start'],'invalid coordinates/strand')
  r['endpoint']=r['end'] if r['strand']=='+' else r['start'] # BED interbase boundary
  d=None if missing(r['misprimed_ratio']) else Decimal(r['misprimed_ratio']);need(d is None or d.is_finite() and 0<=d<=1,'invalid mispriming ratio')
  r['passed']=d is not None and d<=Decimal(str(threshold));need(boolean(r['general_mispriming_pass'])==r['passed'],'native eligibility flag mismatch')
  r['native_passed']=d is not None and d<=Decimal('0.7' if r['feature']=='Intron' else str(threshold));need(boolean(r['mispriming_pass'])==r['native_passed'],'native context mispriming flag mismatch')
  r['unit']=(r['gene_id'],r['strand']);meta[p]=r
  if r['native_passed'] and not missing(r['gene_id']):allpass[r['unit']].append(r)
  if r['passed'] and not missing(r['gene_id']):
   if r['feature'] in NATIVE:native[r['unit']].append(r)
   if r['feature']=='Terminal_exon' and not missing(r['canonical_transcript']):strict[r['unit']].append(r)
 for key,rs in native.items():
  need(len({r['chrom'] for r in rs})==1,'native gene-strand across chromosomes')
  tx={r['canonical_transcript'] for r in rs if not missing(r['canonical_transcript'])};need(len(tx)<=1,'within-arm canonical transcript conflict')
  for r in rs:need(integer(r['n_peaks_in_gene'])==len(rs),'native n_peaks mismatch')
  distances=[Decimal(r['distance_to_stop_codon']) for r in rs if not missing(r['distance_to_stop_codon'])]
  for r in rs:
   expected=bool(distances) and not missing(r['distance_to_stop_codon']) and Decimal(r['distance_to_stop_codon'])==max(distances)
   need(boolean(r['is_distal_peak'])==expected,'native distal metadata mismatch')
 neligible={k for k,rs in native.items() if len(rs)>=2}
 seligible={k for k,rs in strict.items() if len({(r['chrom'],r['endpoint']) for r in rs})>=2}
 return meta,native,strict,allpass,neligible,seligible

def load_arm(arm,dbpath,cohorts):
 meta,native,strict,allpass,ne,se=metadata(arm['files']['peak_to_gene']['path'],arm['config']['misprimed_ratio_threshold'])
 db=sqlite3.connect(dbpath);db.execute('PRAGMA cache_size=-32768');db.execute('PRAGMA temp_store=FILE')
 db.executescript('CREATE TABLE p(peak TEXT PRIMARY KEY,g TEXT,s TEXT,endpoint INTEGER,native INTEGER,strict INTEGER,distal INTEGER); CREATE TABLE c(peak TEXT,bc TEXT,n INTEGER,PRIMARY KEY(peak,bc)) WITHOUT ROWID;')
 db.executemany('INSERT INTO p VALUES(?,?,?,?,?,?,?)',[(p,r['gene_id'],r['strand'],r['endpoint'],int(r['unit'] in ne and r['feature'] in NATIVE and r['passed']),int(r['unit'] in se and r['feature']=='Terminal_exon' and r['passed'] and not missing(r['canonical_transcript'])),int(boolean(r['is_distal_peak'])) if not missing(r['is_distal_peak']) else 0) for p,r in meta.items()])
 nraw=nzero=0;rawcells=set()
 for r in rows(arm['files']['counts']['path'],['peak_name','cell_barcode','umi_count']):
  p,bc=r['peak_name'],r['cell_barcode'];need(p in meta,'unknown count peak');need(bc in cohorts['full_registry'],'count barcode outside registry');n=integer(r['umi_count']);rawcells.add(bc)
  try:db.execute('INSERT INTO c VALUES(?,?,?)',(p,bc,n))
  except sqlite3.IntegrityError as e:raise ValueError('duplicate raw peak-cell') from e
  nraw+=1;nzero+=n==0
  if nraw%10000==0:db.commit()
 db.commit()
 db.executescript('CREATE INDEX cb_index ON c(bc); CREATE TABLE t AS SELECT g,s,bc,SUM(n) total,SUM(CASE WHEN distal=1 THEN n ELSE 0 END) distal FROM c JOIN p USING(peak) WHERE native=1 GROUP BY g,s,bc; CREATE UNIQUE INDEX t_key ON t(g,s,bc); CREATE TABLE psi_seen(peak TEXT,bc TEXT,PRIMARY KEY(peak,bc)) WITHOUT ROWID; CREATE TABLE gc_seen(g TEXT,s TEXT,bc TEXT,PRIMARY KEY(g,s,bc)) WITHOUT ROWID;')
 # Check every native PSI numerator/denominator/key against the raw native matrix.
 for r in rows(arm['files']['psi']['path'],['peak_name','cell_barcode','peak_umi_count','gene_umi_count','gene_id','strand','terminal_gene_unit_id','psi'],12000000):
  p,bc=r['peak_name'],r['cell_barcode'];expect=db.execute('SELECT p.g,p.s,c.n,t.total FROM c JOIN p USING(peak) JOIN t ON p.g=t.g AND p.s=t.s AND c.bc=t.bc WHERE c.peak=? AND c.bc=? AND p.native=1',(p,bc)).fetchone()
  need(expect is not None,'PSI key not raw eligible');g,s,n,total=expect
  need((r['gene_id'],r['strand'],r['terminal_gene_unit_id'])==(g,s,uid(g,s)) and integer(r['peak_umi_count'])==n and integer(r['gene_umi_count'])==total,'PSI identity/count mismatch')
  need(missing(r['psi']) if total<1 else not missing(r['psi']) and abs(Decimal(r['psi'])-Decimal(n)/Decimal(total))<=Decimal('1e-12'),'PSI formula/mask mismatch')
  try:db.execute('INSERT INTO psi_seen VALUES(?,?)',(p,bc))
  except sqlite3.IntegrityError as e:raise ValueError(f"duplicate PSI row: peak_name={p!r}, cell_barcode={bc!r}, source={arm['files']['psi']['path']!r}") from e
 expected=db.execute('SELECT COUNT(*) FROM c JOIN p USING(peak) WHERE native=1').fetchone()[0]
 need(db.execute('SELECT COUNT(*) FROM psi_seen').fetchone()[0]==expected,'missing PSI rows')
 for r in rows(arm['files']['gene_cell']['path'],['gene_id','strand','cell_barcode','terminal_gene_unit_id','gene_umi_count'],12000000):
  g,s,bc=r['gene_id'],r['strand'],r['cell_barcode'];x=db.execute('SELECT total FROM t WHERE g=? AND s=? AND bc=?',(g,s,bc)).fetchone();need(x is not None and x[0]==integer(r['gene_umi_count']) and r['terminal_gene_unit_id']==uid(g,s),'gene-cell mismatch')
  try:db.execute('INSERT INTO gc_seen VALUES(?,?,?)',(g,s,bc))
  except sqlite3.IntegrityError as e:raise ValueError(f"duplicate gene-cell row: gene_id={g!r}, strand={s!r}, cell_barcode={bc!r}, source={arm['files']['gene_cell']['path']!r}") from e
 need(db.execute('SELECT COUNT(*) FROM gc_seen').fetchone()[0]==db.execute('SELECT COUNT(*) FROM t').fetchone()[0],'missing native gene-cell rows');db.commit()
 observations={}
 for definition,flag in [('native_terminal','native'),('representative_terminal_exon','strict')]:
  # Aggregate endpoint bookkeeping while preserving positive peak-record counts for the primary native rule.
  sql=f'''SELECT g,s,bc,SUM(n),SUM(CASE WHEN n>0 THEN 1 ELSE 0 END),SUM(positive_records) FROM (SELECT g,s,bc,endpoint,SUM(n) n,SUM(CASE WHEN n>0 THEN 1 ELSE 0 END) positive_records FROM c JOIN p USING(peak) WHERE {flag}=1 GROUP BY g,s,bc,endpoint) GROUP BY g,s,bc HAVING SUM(n)>0'''
  observations[definition]={}
  for g,s,bc,n,np,nr in db.execute(sql):
   need(len(observations[definition])<2500000,'observation memory bound exceeded; preserve partial DB, revise resource plan before retry')
   observations[definition][(g,s,bc)]={'terminal_umis':n,'positive_endpoints':np,'positive_peak_records':nr}
 catalogue={'all_annotated_records':len(meta),'all_annotated_exact_endpoint_groups':len({(r['chrom'],r['strand'],r['endpoint']) for r in meta.values()}),'native_context_pass_assigned_gene_strand_units_ge2_records':sum(len(rs)>=2 for rs in allpass.values()),'retained_native_terminal_records':sum(map(len,native.values())),'retained_native_terminal_units':len(native),'native_multi_peak_units':len(ne),'representative_terminal_exon_multi_endpoint_units':len(se),'native_multi_units_with_one_exact_endpoint':sum(len({r['endpoint'] for r in native[k]})<2 for k in ne),'context_records':dict(Counter(r['feature'] for r in meta.values())),'raw_rows':nraw,'raw_explicit_zero_rows':nzero,'raw_observed_cells':len(rawcells),'native_psi_validation':'PASS; exact keys/counts and ratios tolerance1e-12'}
 return {'db':db,'meta':meta,'native':native,'strict':strict,'eligible':{'native_terminal':ne,'representative_terminal_exon':se},'observations':observations,'catalogue':catalogue}

def summarize(obs,eligible,cells,cutoff,site_identity="exact_endpoint"):
 obs={k:v for k,v in obs.items() if k[:2] in eligible and k[2] in cells}
 field='positive_peak_records' if site_identity=='native_peak_record' else 'positive_endpoints'
 qualified={k:v for k,v in obs.items() if v[field]>=2 and v['terminal_umis']>=cutoff}
 return {'catalogue_eligible_gene_strand_units':len(eligible),'selected_cells':len(cells),'observed_positive_gene_strand_cell_units':len(obs),'qualifying_gene_strand_cell_units':len(qualified),'qualifying_gene_strand_units':len({k[:2] for k in qualified}),'qualifying_cells':len({k[2] for k in qualified}),'fraction_of_observed_units':ratio(len(qualified),len(obs)),'fraction_of_catalogue_units':ratio(len({k[:2] for k in qualified}),len(eligible)),'fraction_of_selected_cells':ratio(len({k[2] for k in qualified}),len(cells))}

def write_tsv(path,fields,records):
 with open(path,'x',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,delimiter='\t',lineterminator='\n');w.writeheader();w.writerows(records)

def crosswalk(a,b):
 out=[];groups=defaultdict(list)
 for p,r in b.items():groups[(r['chrom'],r['strand'])].append((r['endpoint'],p,r))
 for rs in groups.values():rs.sort(key=lambda x:(x[0],x[1]))
 for pa,ra in sorted(a.items()):
  partners=[]
  for endpoint,pb,rb in groups[(ra['chrom'],ra['strand'])]:
   exact=endpoint==ra['endpoint'];near=abs(endpoint-ra['endpoint'])<=25;overlap=max(ra['start'],rb['start'])<min(ra['end'],rb['end'])
   if exact or near or overlap:partners.append({'coverage3_peak':pa,'coverage1_peak':pb,'exact_endpoint':exact,'within25nt':near,'interval_overlap':overlap,'signed_shift_in_transcript_direction':(endpoint-ra['endpoint'])*(1 if ra['strand']=='+' else -1),'gene_assignment_same':None if missing(ra['gene_id']) or missing(rb['gene_id']) else ra['gene_id']==rb['gene_id']})
  out.extend(partners or [{'coverage3_peak':pa,'coverage1_peak':'','exact_endpoint':False,'within25nt':False,'interval_overlap':False,'signed_shift_in_transcript_direction':'','gene_assignment_same':None}])
 matched={r['coverage1_peak'] for r in out if r['coverage1_peak']}
 for pb in sorted(set(b)-matched):out.append({'coverage3_peak':'','coverage1_peak':pb,'exact_endpoint':False,'within25nt':False,'interval_overlap':False,'signed_shift_in_transcript_direction':'','gene_assignment_same':None})
 # Degrees are per relation, preserving rather than greedily resolving all ties.
 for field in ['exact_endpoint','within25nt','interval_overlap']:
  da=Counter(r['coverage3_peak'] for r in out if r[field]);db=Counter(r['coverage1_peak'] for r in out if r[field])
  for r in out:r[field+'_degree3']=da[r['coverage3_peak']];r[field+'_degree1']=db[r['coverage1_peak']]
 return out
