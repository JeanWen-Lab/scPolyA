"""Pinned stdlib IO and immutable accepted-definition snapshot. No metric calculation."""

import csv,gzip,hashlib,importlib.util,json,math,os,stat,time,traceback

from pathlib import Path

ARMS=('coverage3','coverage1')

CONTIGS=['chr'+str(i) for i in range(1,23)]+['chrX','chrY','chrM']

MODES=('unmasked','a_risk_false','a_risk_false_or_unknown')

DEFS=('native_terminal','representative_terminal_exon')

COHORTS=('full_registry','qc_assigned_labels')

CUTOFFS=(3,5,10)

METRICS={'motif_present':'motif_reason','downstream_a_risk':'downstream_reason','atlas_concordant':'atlas_reason','native_read_end_pass':'native_read_end_reason'}

DOWN_REASONS={'boundary_truncation','unsupported_alphabet_N','unsupported_alphabet_other','outside_frozen_chr1_scope','contig_absent_from_fai'}

def need(ok,reason):
 if not ok:raise ValueError(reason)

def digest(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def ratio(n,d):return n/d if d else None

def emit(path,obj):
 with open(path,'x',encoding='utf8') as f:json.dump(obj,f,sort_keys=True,ensure_ascii=False,allow_nan=False,indent=2);f.write('\n')

def write_tsv(path,fields,rows):
 with open(path,'x',newline='',encoding='utf8') as f:
  w=csv.DictWriter(f,fieldnames=fields,delimiter='\t',lineterminator='\n',extrasaction='raise');w.writeheader();w.writerows(rows)

def tsv(path,required,limit):
 op=gzip.open if str(path).endswith('.gz') else open
 with op(path,'rt',newline='',encoding='utf8') as f:
  r=csv.DictReader(f,delimiter='\t');need(r.fieldnames and len(r.fieldnames)==len(set(r.fieldnames)) and set(required)<=set(r.fieldnames),'missing/duplicate TSV columns: '+str(path))
  for n,row in enumerate(r,1):
   need(n<=limit and None not in row and None not in row.values(),'TSV row bound/width: '+str(path));yield row

class Guard:
 def __init__(self,out=None,wall=7200,disk=20*1024**3,scratch=None,budget_root=None):
  self.started=time.monotonic();self.analysis_out=Path(out).resolve() if out else None;self.out=Path(budget_root).resolve() if budget_root else self.analysis_out
  self.scratch=Path(scratch).resolve() if scratch else (self.out/'scratch' if self.out else None);self.wall=wall;self.disk=disk
  self.failure=None;self.callback_error=None;self.callback_exception=None;self.sqlite_error=None;self.sqlite_phase=None;self.last_disk_bytes=0;self.last_spill_files=[]
  if self.out:need(self.scratch==self.out/'scratch','scratch must be the exact budget-root scratch directory')
 def reject(self,condition,**details):
  if self.failure is None:self.failure={'condition':condition,'details':details,'sqlite_phase':self.sqlite_phase,'pid':os.getpid()}
  raise ValueError(condition)
 def check(self):
  if time.monotonic()-self.started>=self.wall:self.reject('wall budget exceeded',wall_seconds=self.wall)
  if self.out and self.out.exists():
   seen={};spills=[];root=self.out
   for p in root.rglob('*'):
    try:st=p.stat()
    except FileNotFoundError:continue # A normal SQLite/publication unlink after directory enumeration.
    if stat.S_ISREG(st.st_mode):seen[st.st_dev,st.st_ino]=st.st_size
   fdroot=Path('/proc/self/fd')
   if fdroot.exists():
    for fd in fdroot.iterdir():
     try:
      link=os.readlink(fd);path=Path(link.removesuffix(' (deleted)'))
      if 'etilqs_' in link and not path.is_relative_to(self.scratch):self.reject('SQLite spill escaped configured scratch',fd=str(fd),target=link,configured_scratch=str(self.scratch),budget_root=str(root))
      if path.is_absolute() and path.is_relative_to(root):
       st=fd.stat();seen[st.st_dev,st.st_ino]=st.st_size
       if 'etilqs_' in link:spills.append({'target':link,'bytes':st.st_size})
     except (FileNotFoundError,ProcessLookupError):pass # Only normal disappearing /proc fd entries.
   self.last_disk_bytes=sum(seen.values());self.last_spill_files=spills
   if sum(seen.values())>self.disk:self.reject('disk polling budget exceeded',bytes=sum(seen.values()),budget=self.disk)
 def progress(self):
  try:self.check();return 0
  except BaseException as e:
   self.callback_exception=e
   self.callback_error={'exception_type':type(e).__name__,'message':str(e),'traceback':traceback.format_exc(),'guard_failure':self.failure,'sqlite_phase':self.sqlite_phase}
   return 1 # SQLite reports interrupted; preserve the actual callback cause separately.

class Pins:
 def __init__(self):self.records={}
 def add(self,d):
  need(isinstance(d,dict) and isinstance(d.get('path'),str) and len(d.get('sha256',''))==64,'unfilled file binding')
  p=Path(d['path']).resolve();need(p.is_file(),'missing bound file: '+str(p));st=p.stat()
  need(d.get('bytes',st.st_size)==st.st_size and digest(p)==d['sha256'],'input size/hash: '+str(p))
  self.records[str(p)]={'path':str(p),'sha256':d['sha256'],'bytes':st.st_size,'stat':(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)};return p
 def small(self,d):
  p=self.add(d);need(p.stat().st_size<=4*1024**2,'JSON metadata bound');return json.loads(p.read_text())
 def post(self):
  for p,d in self.records.items():
   st=Path(p).stat();need((st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)==d['stat'] and digest(p)==d['sha256'],'input changed during analysis: '+p)
  return list(self.records.values())

def state(row,metric):
 value=row[metric];reason=row[METRICS[metric]]
 if value in ('true','false'):
  need(reason=='','nonempty reason on evaluable '+metric);return value=='true'
 need(value=='' and reason!='','invalid nullable boolean/reason '+metric)
 if metric=='downstream_a_risk':need(reason in DOWN_REASONS,'invalid downstream NA reason')
 return None

def definition_snapshot_missing_ratio(m):return str(m['misprimed_ratio']).strip().lower() in {'','na','nan','none','null','<na>'}
