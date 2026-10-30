"""Read-only bounded adapter. Direct-script CLI avoids package inspection imports."""
from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import zlib
from collections import Counter, defaultdict
from decimal import Decimal, DecimalException, InvalidOperation
from types import SimpleNamespace

SCHEMA = 'scpolya.native_gene_diagnostics.v1'
ENC = dict(ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)
LIMITS = dict(request_bytes=32768, source_files=9, source_bytes_each=33554432,
              source_bytes_total=67108864, io_bytes=134217728,
              expanded_bytes_each=134217728, expanded_bytes_total=268435456,
              count_rows=1000000, catalogue_rows=20000, registry_rows=10000,
              optional_rows=100000, columns=32, row_bytes=65536, field_bytes=4096,
              json_bytes=1048576, json_nodes=20000, selected_genes=3,
              units=256, engine_count_rows=50000, engine_catalogue_rows=2000,
              engine_text_bytes=4194304, engine_nodes=500000,
              output_records=4096, output_bytes=1048576, seconds=120)
TERMINAL = {'Terminal_exon', 'Downstream_of_terminal_exon'}


class Refusal(ValueError):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(f'{code}: {detail}')


def fail(code, detail):
    raise Refusal(code, detail)


def text(value):
    if not isinstance(value, str) or not 0 < len(value) <= 512 or not value.strip():
        fail('invalid_request', 'expected bounded nonblank text')
    return value


def encoded_size(obj, maximum):
    size = 0
    for part in json.JSONEncoder(**ENC).iterencode(obj):
        size += len(part)
        if size > maximum:
            fail('limit_exceeded', 'JSON bytes')
    return size


def tree_budget(obj, max_nodes=20000, depth_limit=12):
    nodes = chars = 0
    def visit(v, depth):
        nonlocal nodes, chars
        nodes += 1
        if depth > depth_limit or nodes > max_nodes:
            fail('limit_exceeded', 'JSON depth/nodes')
        if isinstance(v, str):
            if len(v) > 4194304:
                fail('limit_exceeded', 'input text')
            for i in range(0, len(v), 4096):
                chars += len(json.dumps(v[i:i+4096], ensure_ascii=True)) - 2
                if chars > 4194304:
                    fail('limit_exceeded', 'input text')
        elif type(v) is dict:
            for k, item in v.items():
                if not isinstance(k, str): fail('invalid_request', 'non-text key')
                visit(k, depth+1); visit(item, depth+1)
        elif type(v) is list:
            for item in v: visit(item, depth+1)
        elif v is not None and type(v) not in (bool, int, float):
            fail('invalid_request', 'not JSON data')
        elif type(v) is float and not math.isfinite(v):
            fail('invalid_request', 'nonfinite JSON number')
    visit(obj, 0)
    return nodes, chars


def parse_json(data, limits):
    if len(data) > limits['json_bytes']: fail('limit_exceeded', 'JSON source bytes')
    # Bound nesting before json.loads allocates recursive structures.
    depth = 0; quoted = escaped = False
    for c in data.decode('utf-8'):
        if quoted:
            if escaped: escaped = False
            elif c == '\\': escaped = True
            elif c == '"': quoted = False
        elif c == '"': quoted = True
        elif c in '[{':
            depth += 1
            if depth > 12: fail('limit_exceeded', 'JSON depth')
        elif c in ']}': depth -= 1
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result: fail('unsupported_schema', 'duplicate JSON key')
            result[k] = v
        return result
    value = json.loads(data, object_pairs_hook=pairs,
                       parse_constant=lambda _: fail('unsupported_schema', 'nonfinite JSON'))
    tree_budget(value, limits['json_nodes'])
    return value


def pure_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses in artifact_naming need this binding
    spec.loader.exec_module(module)
    return module


def helpers():
    base = Path(__file__).resolve().parents[1]
    # Native artifact helper imports only quantification_config; avoid task_manage.__init__.
    sys.path.insert(0, str(base))
    try:
        naming = pure_module('_native_diagnostic_naming', base/'task_manage/artifact_naming.py')
        effective = pure_module('_native_diagnostic_effective', base/'task_manage/effective_config.py')
        engine = pure_module('_native_diagnostic_engine', base/'scpolya_agent/gene_diagnostics.py')
    finally:
        sys.path.pop(0)
    return naming, effective, engine


def config_digest(config):
    # Snapshot v1 is strict JSON: native _jsonable is identity on this domain.
    # Do not import logging_config: its package initialization mutates host logging.
    payload = json.dumps(config, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class Meter(io.RawIOBase):
    def __init__(self, raw, session):
        self.raw, self.session = raw, session
        self.digest = hashlib.sha256()
        self.bytes = 0
    def readable(self): return True
    def readinto(self, buffer):
        self.session.tick()
        n = self.raw.readinto(buffer)
        self.bytes += n; self.session.io_bytes += n
        if self.bytes > self.session.limits['source_bytes_each'] or self.session.io_bytes > self.session.limits['io_bytes']:
            fail('limit_exceeded', 'source IO bytes')
        self.digest.update(memoryview(buffer)[:n])
        return n


class Sources:
    def __init__(self, root, descriptions, limits):
        self.root, self.limits = root, limits
        self.deadline = time.monotonic() + limits['seconds']
        self.io_bytes = self.expanded = 0
        self.records = {}
        if not 6 <= len(descriptions) <= limits['source_files']:
            fail('limit_exceeded', 'source file count')
        paths = set(); total = 0
        for role, desc in descriptions.items():
            if not isinstance(desc, dict) or set(desc) != {'path', 'sha256'}:
                fail('invalid_request', 'source requires path and sha256')
            value = text(desc['path']); p = Path(value)
            if role == 'registry':
                if not p.is_absolute(): fail('invalid_request', 'registry must be absolute')
                if not p.name.endswith(('.tsv','.txt','.tsv.gz','.txt.gz')):
                    fail('unsupported_schema','registry must be a text whitelist path')
            elif p.is_absolute() or '..' in p.parts:
                fail('path_refused', 'native source must be relative')
            else: p = root/p
            self.safe_path(p)
            if role != 'registry' and not p.resolve().is_relative_to(root):
                fail('path_refused', 'source escapes run')
            if str(p) in paths: fail('invalid_request', 'duplicate source path')
            paths.add(str(p))
            if not re.fullmatch('[0-9a-f]{64}', desc['sha256']): fail('invalid_request', 'SHA256 required')
            st = p.stat(); total += st.st_size
            if st.st_size > limits['source_bytes_each'] or total > limits['source_bytes_total']:
                fail('limit_exceeded', 'source file bytes')
            self.records[role] = dict(path=p, expected=desc['sha256'], stat=self.signature(st))
    def tick(self):
        if time.monotonic() > self.deadline: fail('timeout', 'cooperative deadline')
    @staticmethod
    def signature(st): return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    @staticmethod
    def safe_path(p):
        for part in [p, *p.parents]:
            if part.is_symlink(): fail('path_refused', 'symlink source/path component')
        if not p.is_file(): fail('path_refused', 'regular file required')
    def stream(self, role, decode=True):
        rec = self.records[role]; p = rec['path']; self.safe_path(p)
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb', buffering=0) as raw:
            if not stat.S_ISREG(os.fstat(raw.fileno()).st_mode): fail('path_refused', 'not regular')
            if self.signature(os.fstat(raw.fileno())) != rec['stat']: fail('changed_input', role)
            meter = Meter(raw, self)
            with io.BufferedReader(meter, buffer_size=32768) as buf:
                reader = gzip.GzipFile(fileobj=buf) if decode and p.suffix == '.gz' else buf
                expanded = 0
                try:
                    while True:
                        line = reader.readline(self.limits['row_bytes']+1) if decode else reader.read(32768)
                        if not line: break
                        self.tick()
                        if decode:
                            expanded += len(line); self.expanded += len(line)
                            if len(line) > self.limits['row_bytes'] or expanded > self.limits['expanded_bytes_each'] or self.expanded > self.limits['expanded_bytes_total']:
                                fail('limit_exceeded', 'expanded/row bytes')
                        yield line
                finally:
                    if reader is not buf: reader.close()
                if meter.digest.hexdigest() != rec['expected']: fail('source_hash_mismatch', role)
            if self.signature(os.fstat(raw.fileno())) != rec['stat']: fail('changed_input', role)
        self.safe_path(p)
        if self.signature(p.stat()) != rec['stat']: fail('changed_input', role)
    def json(self, role):
        data = bytearray()
        for line in self.stream(role):
            data.extend(line)
            if len(data) > self.limits['json_bytes']: fail('limit_exceeded', 'JSON bytes')
        return parse_json(bytes(data), self.limits)
    def table(self, role, required, max_rows, delimiter='\t', exact=False):
        header = None; n = 0
        for line in self.stream(role):
            row = next(csv.reader([line.decode('utf-8')], delimiter=delimiter, strict=True))
            if len(row) > self.limits['columns'] or any(len(v.encode('utf-8')) > self.limits['field_bytes'] for v in row):
                fail('limit_exceeded', 'columns/field bytes')
            if header is None:
                header = row
                if len(set(header)) != len(header) or not set(required) <= set(header) or (exact and header != required):
                    fail('unsupported_schema', role+' header')
                continue
            n += 1
            if n > max_rows: fail('limit_exceeded', role+' rows')
            if len(row) != len(header): fail('unsupported_schema', role+' row width')
            yield dict(zip(header, row))
        if header is None: fail('unsupported_schema', role+' empty file')
    def verify_again(self):
        for role in self.records:
            try:
                for _ in self.stream(role, decode=False): pass
            except Refusal as exc:
                if exc.code == 'source_hash_mismatch': fail('changed_input', role)
                raise


def real_number(value, nullable=False):
    if nullable and value == '': return None
    result = float(value)
    if not math.isfinite(result): fail('unsupported_schema', 'nonfinite number')
    return result


def whole(value, nullable=False):
    if nullable and value == '': return None
    try:
        d = Decimal(value)
        if not d.is_finite() or d != d.to_integral_value() or abs(d) > 2**53-1:
            fail('invalid_counts', 'nonintegral/out of range')
        return int(d)
    except DecimalException:
        fail('invalid_counts', 'invalid decimal count')


def boolean(value, nullable=False):
    if nullable and value == '': return None
    if value not in ('True', 'False'): fail('unsupported_schema', 'boolean text')
    return value == 'True'


def bind(request, source, naming, effective):
    ns = request['namespace']; root = source.root; sid = text(request['sample_id'])
    if set(ns) != {'peak', 'annotation', 'matrix'}: fail('namespace_mismatch', 'three explicit scopes')
    for scope, prefix in [('peak','pk'),('annotation','an'),('matrix','mx')]:
        if not re.fullmatch(prefix+'-[0-9a-f]{16}', ns[scope]): fail('namespace_mismatch', scope)
    for role, relative in [('manifest','state/artifact_name_manifest.v1.json'),('inputs','input_tables/inputs.csv')]:
        if source.records[role]['path'] != root/relative: fail('namespace_mismatch', role)
    cp = source.records['config']['path'].relative_to(root).parts
    if len(cp) != 4 or cp[:2] != ('logs','invocations') or cp[3] != 'invocation.json':
        fail('unsupported_schema', 'explicit invocation path required')
    manifest = source.json('manifest')
    if not isinstance(manifest,dict):fail('unsupported_schema','manifest object')
    if (manifest.get('schema_version') != 'scpolya.artifact_name_manifest.v1' or manifest.get('mode') != 'short'
        or manifest.get('hash_algorithm') != 'sha256' or manifest.get('short_hex_length') != 16):
        fail('unsupported_schema', 'artifact manifest')
    records = [naming.ScopedArtifactNameV1(**r) for r in manifest['records']]
    if len({r.code for r in records}) != len(records): fail('namespace_mismatch', 'duplicate code')
    try:
        validated = naming.validate_artifact_name_records(records)
    except naming.ArtifactNameError as exc:
        fail('manifest_invalid',type(exc).__name__)
    chosen = {}
    for scope, code in ns.items():
        matches = [r for r in validated if r.scope == scope and r.code == code]
        if len(matches) != 1: fail('namespace_mismatch', scope)
        chosen[scope] = matches[0]
    pk, an, mx = [chosen[s].payload for s in ('peak','annotation','matrix')]
    if an.get('peak') != pk or mx.get('annotation') != an: fail('namespace_mismatch', 'payload nesting')
    invocation = source.json('config')
    if not isinstance(invocation,dict):fail('unsupported_schema','invocation object')
    if invocation.get('schema_version') != 'scpolya.logging.invocation.v2': fail('unsupported_schema', 'invocation')
    if type(invocation.get('config_schema_version')) is not int or invocation['config_schema_version'] != 1:
        fail('unsupported_schema', 'config_schema_version must be integer 1')
    cfg = invocation['resolved_config']
    if not isinstance(cfg,dict):fail('unsupported_schema','resolved_config object')
    digest = invocation.get('config_hash')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or digest != config_digest(cfg):
        fail('config_mismatch', 'invocation canonical config_hash')
    if invocation.get('output_dir') != cfg.get('output_dir') or invocation.get('run_id') != cfg.get('run_id'):
        fail('sample_binding_mismatch', 'invocation top-level output_dir/run_id')
    if cfg.get('quantification_mode') != 'terminal-aware': fail('unsupported_schema', 'mode')
    for name in ('misprimed_ratio_threshold','intronic_misprimed_ratio_threshold'):
        value=cfg[name]
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
            fail('config_mismatch','invalid threshold')
    if type(cfg['psi_min_gene_umis']) is not int or cfg['psi_min_gene_umis']<0:
        fail('config_mismatch','invalid PSI mask')
    if invocation.get('run_root') != str(root) or Path(cfg['output_dir'])/cfg['run_id'] != root:
        fail('sample_binding_mismatch', 'invocation run path')
    if cfg['inputs'] != request['invocation_inputs_path']: fail('sample_binding_mismatch', 'recorded inputs path')
    expected = effective.build_annotated_peak_config(SimpleNamespace(**cfg),dedup_method=cfg['dedup_method'],
                         polyA_status=f"polyA-{cfg['minpolyA']}-{cfg['nonA_allowed']}-{cfg['minMAPQ']}")
    ep = naming.peak_name_payload(expected)
    # The schema comes from the caller-bound manifest record, not current code;
    # only explicitly readable schemas (v3, v4) are accepted.
    quant = sys.modules[effective.terminal_annotation_schema_payload.__module__]
    schema = an.get('terminal_annotation_schema')
    if schema not in quant.READABLE_TERMINAL_ANNOTATION_SCHEMAS:
        fail('unsupported_schema', f'annotation schema {schema!r}')
    ea = naming.annotation_name_payload(ep, annotate_peak_source=cfg['annotate_peak_source'],
                                       terminal_annotation_schema=schema)
    em = naming.matrix_name_payload(ea, psi_min_gene_umis=cfg['psi_min_gene_umis'],quantification_config={
        'quantification_mode':'terminal-aware','intronic_misprimed_ratio_threshold':cfg['intronic_misprimed_ratio_threshold']})
    if pk != ep or an != ea or mx != em: fail('config_mismatch', 'scientific config vs namespace payload')
    inp = list(source.table('inputs',['sample_id','source_path','is_deduped','requires_split'],1,delimiter=','))
    if len(inp)!=1 or inp[0]['sample_id'] != sid or inp[0]['source_path'] != request['sample_source_path']:
        fail('sample_binding_mismatch', 'single sample/path')
    if inp[0]['is_deduped'] not in ('0','1') or inp[0]['requires_split'] not in ('0','1'):
        fail('unsupported_schema', 'input flags')
    # Native name base is an explicit assertion checked against sample+dedup token.
    base = text(request['artifact_base'])
    if inp[0]['is_deduped']!='0': fail('unsupported_schema','v1 native dedup-output naming only')
    allowed = {sid+'.'+cfg['dedup_method']+'.annotated'}
    if base not in allowed: fail('sample_binding_mismatch', 'artifact base')
    summary = Path('matrix_summary')/ns['peak']/ns['annotation']/ns['matrix']
    paths = {'counts':Path('count_matrix')/ns['peak']/ns['annotation']/(base+'.counts.terminal-aware.v2.tsv.gz'),
             'catalogue':summary/(ns['annotation']+'.terminal_aware_peak_to_gene.v2.tsv.gz'),
             'psi':summary/(base+'.terminal_aware_psi.v2.tsv.gz'),
             'ipui_site':summary/(base+'.intronic_ipui_per_site.v2.tsv.gz'),
             'ipui_total':summary/(base+'.intronic_ipui_gene_total.v2.tsv.gz')}
    for role, relative in paths.items():
        if role in source.records and source.records[role]['path'] != root/relative: fail('namespace_mismatch', role)
    return cfg, {s:chosen[s].as_dict() for s in chosen}

CAT_FIELDS = ['peak_name','gene_id','strand','feature','misprimed_ratio','general_mispriming_pass',
              'intronic_mispriming_pass','mispriming_pass','is_distal_peak','distance_to_stop_codon',
              'n_peaks_in_gene','peak_order_in_gene']


def _diagnose(request):
    if not isinstance(request,dict):fail('invalid_request','request object required')
    tree_budget(request, 6000, 8)
    encoded_size(request, LIMITS['request_bytes'])
    if request.get('schema') != 'scpolya.native_gene_diagnostics.request.v1': fail('unsupported_schema', 'request')
    allowed_request={'schema','run_root','namespace','sample_id','sources','selection','assertions','registry_sample_id','artifact_base','invocation_inputs_path','sample_source_path','historical_run_commit','limits','metrics'}
    if set(request)-allowed_request: fail('invalid_request','unknown request field')
    if 'metrics' in request:
        fail('unsupported_metric_selection','v1 always returns the four named engine metrics')
    limits = dict(LIMITS)
    overrides = request.get('limits', {})
    if not isinstance(overrides, dict) or set(overrides)-set(limits): fail('invalid_request', 'limits')
    for k,v in overrides.items():
        if type(v) is not int or not 1 <= v <= limits[k]: fail('invalid_request', 'limits only decrease')
        limits[k] = v
    encoded_size(request, limits['request_bytes'])
    assertions = request.get('assertions', {})
    if not isinstance(assertions,dict):fail('invalid_request','assertions object required')
    for name in ['quiescent','complete_count_catalogue_scope','registry_sample_binding']:
        if assertions.get(name) is not True: fail('scope_not_declared', name)
    root = Path(text(request['run_root']))
    if not root.is_absolute() or not root.is_dir(): fail('path_refused','absolute existing run_root')
    for p in [root,*root.parents]:
        if p.is_symlink(): fail('path_refused','symlink run_root')
    root = root.resolve()
    sid = text(request['sample_id'])
    if request.get('registry_sample_id') != sid: fail('sample_binding_mismatch','registry sample')
    selections = request['selection']
    if not isinstance(selections,list) or not 1 <= len(selections) <= limits['selected_genes']:
        fail('limit_exceeded','selected_genes')
    selected = {}; total_units = 0
    for item in selections:
        if 'gene_id' not in item: fail('symbol_requires_gene_id','gene_id and strand required')
        if set(item) != {'gene_id','strand','cell_barcodes'}: fail('invalid_request','selection fields')
        key=(text(item['gene_id']),item['strand'])
        if key[1] not in ('+','-'): fail('invalid_request','strand')
        cells=item['cell_barcodes']
        if not isinstance(cells,list) or not cells: fail('invalid_request','explicit cells required')
        cells=[text(c) for c in cells]
        if len(set(cells))!=len(cells) or key in selected: fail('invalid_request','duplicate selection')
        selected[key]=set(cells); total_units+=len(cells)
    if total_units > limits['units']: fail('limit_exceeded','total requested gene-cell units')
    allowed={'inputs','manifest','config','registry','counts','catalogue','psi','ipui_site','ipui_total'}
    descriptions=request['sources']
    if not isinstance(descriptions,dict) or set(descriptions)-allowed or not {'inputs','manifest','config','registry','counts','catalogue'} <= set(descriptions):
        fail('invalid_request','source roles')
    source=Sources(root,descriptions,limits)
    naming,effective,engine=helpers()
    cfg,binding=bind(request,source,naming,effective)
    registry=set()
    for line in source.stream('registry'):
        cell=line.decode('utf-8').rstrip('\r\n');text(cell)
        if '\t' in cell or cell in registry: fail('unsupported_schema','registry duplicate/column')
        registry.add(cell)
        if len(registry)>limits['registry_rows']: fail('limit_exceeded','registry_rows')
    for cells in selected.values():
        if not cells<=registry: fail('unknown_cell','requested cell not in registry')
    catalogue={}; genes=defaultdict(list); terminal=Counter(); intronic=Counter(); contexts=Counter()
    general=cfg['misprimed_ratio_threshold']; intronic_threshold=cfg['intronic_misprimed_ratio_threshold']
    for row in source.table('catalogue',CAT_FIELDS,limits['catalogue_rows']):
        peak=text(row['peak_name']);gene=row['gene_id'] or None;strand=row['strand']
        if peak in catalogue: fail('ambiguous_mapping','duplicate catalogue peak')
        if gene is not None: text(gene)
        if strand not in ('+','-') and not (gene is None and strand in ('','.')): fail('unsupported_schema','catalogue strand')
        text(row['feature'])
        m={k:row[k] for k in CAT_FIELDS};m['gene_id']=gene
        for k in ['misprimed_ratio','distance_to_stop_codon']:m[k]=real_number(m[k],True)
        for k in ['n_peaks_in_gene','peak_order_in_gene']:m[k]=whole(m[k],True)
        for k in ['general_mispriming_pass','intronic_mispriming_pass','mispriming_pass']:m[k]=boolean(m[k])
        m['is_distal_peak']=boolean(m['is_distal_peak'],True)
        ratio=m['misprimed_ratio']
        if ratio is not None and not 0<=ratio<=1:fail('unsupported_schema','ratio range')
        if (m['general_mispriming_pass']!=(ratio is not None and ratio<=general)
            or m['intronic_mispriming_pass']!=(ratio is not None and ratio<=intronic_threshold)
            or m['mispriming_pass']!=(m['intronic_mispriming_pass'] if m['feature']=='Intron' else m['general_mispriming_pass'])):
            fail('config_mismatch','native pass flags')
        catalogue[peak]=m;contexts[m['feature']]+=1
        if gene is not None:
            key=(gene,strand);genes[key].append(m)
            if m['feature'] in TERMINAL and m['general_mispriming_pass']:terminal[key]+=1
            if m['feature']=='Intron' and m['intronic_mispriming_pass']:intronic[key]+=1
    picked={key:[] for key in selected};seen=set();observed=set();rows=umis=unassigned_umis=0
    for row in source.table('counts',['peak_name','cell_barcode','umi_count'],limits['count_rows'],exact=True):
        peak=text(row['peak_name']);cell=text(row['cell_barcode']);count=whole(row['umi_count'])
        if count<0: fail('invalid_counts','negative')
        if (peak,cell) in seen:fail('duplicate_count_pair','counts')
        seen.add((peak,cell))
        if peak not in catalogue:fail('unknown_peak_mapping','counts')
        if cell not in registry:fail('registry_mismatch','count cell absent from registry')
        m=catalogue[peak];key=m['gene_id'],m['strand']
        if count and (m['misprimed_ratio'] is None or (m['feature']!='Intron' and not m['mispriming_pass'])):
            fail('invalid_counts','native excluded site has positive count')
        rows+=1;umis+=count;observed.add(cell)
        if m['gene_id'] is None:unassigned_umis+=count
        if key in selected and cell in selected[key]:
            picked[key].append(dict(sample_id=sid,peak_name=peak,cell_barcode=cell,umi_count=count))
            if sum(map(len,picked.values()))>limits['engine_count_rows']:fail('limit_exceeded','engine count rows')
    # Optional native rows are observations only, not an alternative formula engine.
    native_psi={}; native_stats={}; optional_rows=0
    for role in ['psi','ipui_site','ipui_total']:
        if role not in source.records:continue
        required={'psi':['peak_name','gene_id','strand','cell_barcode','peak_umi_count','gene_umi_count','psi'],
                  'ipui_site':['intronic_pas_id','gene_id','strand','quantification_unit_id','I_i','U','iPUI_i'],
                  'ipui_total':['gene_id','strand','quantification_unit_id','I_total','U','total_iPUI']}[role]
        total=matched=numeric=0;unique=set()
        for row in source.table(role,required,limits['optional_rows']):
            total+=1;optional_rows+=1
            if optional_rows>limits['optional_rows']:fail('limit_exceeded','combined optional summary rows')
            gene=text(row['gene_id']),row['strand']
            if gene[1] not in ('+','-'):fail('unsupported_schema','native summary strand')
            cell=text(row['cell_barcode'] if role=='psi' else row['quantification_unit_id'])
            peak=text(row['peak_name']) if role=='psi' else (text(row['intronic_pas_id']) if role=='ipui_site' else None)
            key=gene,cell,peak
            if key in unique:fail('unsupported_schema','duplicate native summary row')
            unique.add(key)
            if peak is not None and (peak not in catalogue or (catalogue[peak]['gene_id'],catalogue[peak]['strand'])!=gene):
                fail('ambiguous_mapping','native summary PAS identity')
            if gene not in genes:fail('ambiguous_mapping','native summary gene')
            if cell and cell not in registry:fail('registry_mismatch','native summary cell')
            numeric_fields={'psi':['peak_umi_count','gene_umi_count'],'ipui_site':['I_i','U'],'ipui_total':['I_total','U']}[role]
            for field in numeric_fields:
                value=whole(row[field],nullable=(field=='U'))
                if value is not None and value<0:fail('invalid_counts','native summary count')
            value=real_number(row[{'psi':'psi','ipui_site':'iPUI_i','ipui_total':'total_iPUI'}[role]],True)
            if gene in selected and cell in selected[gene]:
                matched+=1;numeric+=value is not None
                if role=='psi':
                    pc,gc=whole(row['peak_umi_count']),whole(row['gene_umi_count'])
                    if pc<0 or gc<0:fail('invalid_counts','native PSI counts')
                    native_psi[gene,cell,peak]={'value':value,'D':pc,'T':gc}
        native_stats[role]=dict(rows=total,selected_rows=matched,selected_numeric_rows=numeric)
    evidence=[dict(role=role,path=str(r['path']),sha256=r['expected'],bytes=r['stat'][2]) for role,r in sorted(source.records.items())]
    declared_commit=request.get('historical_run_commit')
    if declared_commit is not None:text(declared_commit)
    result={'schema':SCHEMA,'status':'ok','scope':{'sample_id':sid,'namespace':request['namespace'],
        'inference':'descriptive_only','whole_run_health':'not_assessed','clearance':'none'},
        'source_evidence':evidence,'namespace_binding':binding,
        'provenance':{'historical_run_commit':declared_commit,'trust':'caller_declaration',
        'not_authenticated':['historical_commit','upstream_completeness','quiescence','external_registry_binding',
                             'invocation.inputs to copied input_tables/inputs.csv origin'],
        'observed':'expected source SHA, schemas, single-sample CSV/path, namespace payload/config consistency and before/after stability',
        'invocation_inputs_path':cfg['inputs'],'sample_source_path':request['sample_source_path'],
        'tool_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'engine_sha256':hashlib.sha256((Path(__file__).parent/'gene_diagnostics.py').read_bytes()).hexdigest(),
        'selection_sha256':hashlib.sha256(json.dumps(selections,**ENC).encode()).hexdigest()},
        'catalogue_accounting':{'pas':len(catalogue),'contexts':dict(sorted(contexts.items())),
            'retained_intronic_pas':sum(intronic.values()),
            'intronic_gene_strands_with_terminal_partner':sum(k in terminal for k in intronic),
            'unassigned_pas':sum(m['gene_id'] is None for m in catalogue.values()),
            'empty_native_ipui_explanation':'missing_terminal_partner' if intronic and not any(k in terminal for k in intronic) else 'not_globally_missing_terminal'},
        'count_accounting':{'rows':rows,'observed_cells':len(observed),'site_assigned_umis':umis,'unassigned_umis':unassigned_umis},
        'native_summary_accounting':native_stats,'gene_results':[], 'budget_usage':{}}
    # Reserve bounded final usage counters. No giant whole-result serialization.
    remaining=limits['output_bytes']-encoded_size(result,limits['output_bytes'])-2048
    input_nodes=input_text=engine_counts=engine_catalogue=0
    output_records=len(evidence)+len(contexts)+1
    for gene in sorted(selected):
        source.tick()
        if gene not in genes:
            item={'gene_id':gene[0],'strand':gene[1],'status':'unavailable','reason':'gene_absent_from_catalogue'}
        else:
            catrows=[dict(sample_id=sid,**m) for m in genes[gene]]
            reg=[dict(sample_id=sid,gene_id=gene[0],strand=gene[1],cell_barcode=c) for c in sorted(selected[gene])]
            engine_counts+=len(picked[gene]);engine_catalogue+=len(catrows)
            if engine_counts>limits['engine_count_rows'] or engine_catalogue>limits['engine_catalogue_rows']:
                fail('limit_exceeded','aggregate engine rows')
            if remaining<=0:fail('limit_exceeded','output_bytes')
            arguments=dict(count_rows=picked[gene],catalogue_rows=catrows,unit_registry=reg,
                policy=dict(quantification_mode='terminal-aware',sample_ids=[sid],sample_binding='explicit_sample_id',
                    complete_locus_counts=True,complete_locus_catalogue=True,sparse_omission_is_zero=True,
                    general_mispriming_threshold=general,intronic_mispriming_threshold=intronic_threshold,
                    psi_min_gene_umis=cfg['psi_min_gene_umis'],limits={'output_json_bytes':min(remaining,engine.LIMITS['output_json_bytes'])}),
                provenance={'counts_sha256':source.records['counts']['expected'],'catalogue_sha256':source.records['catalogue']['expected'],
                            'run_commit':declared_commit,'completeness':'caller assertion; full supplied files scanned to EOF'})
            nodes,chars=tree_budget(arguments,limits['engine_nodes'],8)
            input_nodes+=nodes;input_text+=chars
            if input_nodes>limits['engine_nodes'] or input_text>limits['engine_text_bytes']:
                fail('limit_exceeded','aggregate engine text/nodes')
            try:
                calculated=engine.diagnose_gene_units(**arguments)
            except engine.DiagnosticValidationError as exc:
                fail(exc.code,exc.detail)
            comparisons=[]
            denominator={r['cell_barcode']:r for r in calculated['denominator_rows']}
            for metric in calculated['metric_rows']:
                if metric['metric_name']!='distal_fraction_terminal_conditional':continue
                cell=metric['cell_barcode'];dpeak=denominator[cell]['unique_distal_peak_name']
                key=gene,cell,dpeak;value=metric['recipe_native_comparable_distal_value_or_null']
                present=key in native_psi
                row=native_psi.get(key)
                native=row['value'] if row else None
                if row and (row['T']!=denominator[cell]['T'] or row['D']!=denominator[cell]['D']):
                    fail('native_summary_mismatch','distal denominator or count')
                comparison='native_summary_unavailable' if 'psi' not in source.records else 'native_row_absent' if not present else 'recipe_value_unavailable' if value is None else 'native_value_unavailable' if native is None else 'matched' if abs(value-native)<=1e-15 else 'mismatch'
                comparisons.append(dict(cell_barcode=cell,cell_status='matched_observed' if cell in observed else 'registered_unobserved',
                                        native_row_present=present,native_value=native,recipe_value=value,comparison=comparison))
            item={'gene_id':gene[0],'strand':gene[1],'status':'ok','engine_result':calculated,'native_distal_comparisons':comparisons}
            output_records+=sum(len(calculated[k]) for k in ['denominator_rows','metric_rows','site_audit','unassigned_count_audit'])+len(comparisons)
        output_records+=1
        if output_records>limits['output_records']:fail('limit_exceeded','combined output records')
        cost=encoded_size(item,max(0,remaining))+int(bool(result['gene_results']))
        if cost>remaining:fail('limit_exceeded','output_bytes')
        remaining-=cost;result['gene_results'].append(item)
    source.verify_again()
    result['budget_usage']={'requested_gene_cell_units':total_units,'engine_input_nodes':input_nodes,
        'engine_input_text_bytes':input_text,'engine_count_rows':engine_counts,'engine_catalogue_rows':engine_catalogue,
        'output_records':output_records,'source_read_bytes':source.io_bytes,'expanded_bytes':source.expanded,
        'limits':limits,'output_encoding':'ensure_ascii sorted compact UTF-8 JSON; no newline',
        'deadline':'loop checks only; blocking OS reads are not preempted; external supervisor required'}
    encoded_size(result,limits['output_bytes'])
    return result


def diagnose_native_gene_selection(request):
    """Structured refusal, never a partial numeric response on integrity/budget failure."""
    try:
        return _diagnose(request)
    except Refusal as exc:
        return {'schema':SCHEMA,'status':'refused','code':exc.code,'detail':exc.detail[:200]}
    except (KeyError, TypeError, ValueError, AttributeError, OSError, EOFError, RecursionError, OverflowError, InvalidOperation, zlib.error) as exc:
        return {'schema':SCHEMA,'status':'refused','code':'invalid_source_or_request','detail':type(exc).__name__}


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',required=True)
    args=parser.parse_args(argv)
    try:
        path=Path(args.request);Sources.safe_path(path)
        with path.open('rb') as f:data=f.read(LIMITS['request_bytes']+1)
        if len(data)>LIMITS['request_bytes']:fail('limit_exceeded','request bytes')
        result=diagnose_native_gene_selection(parse_json(data,LIMITS))
    except (Refusal,OSError,ValueError) as exc:
        result={'schema':SCHEMA,'status':'refused','code':getattr(exc,'code','invalid_request'),'detail':type(exc).__name__}
    sys.stdout.write(json.dumps(result,**ENC))
    return 0 if result['status']=='ok' else 2


if __name__=='__main__':
    raise SystemExit(main())
