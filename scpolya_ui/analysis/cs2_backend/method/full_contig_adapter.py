#!/usr/bin/env python3
"""Minimal full-primary-contig scope adapter for the accepted Q1 calculator.

The accepted calculator and all formulas remain byte-for-byte unchanged.  For
each requested contig this adapter loads one validated private in-memory copy,
changes only the explicit chromosome/alias scope, and invokes that calculator.
It performs no metric calculation itself, never changes windows or thresholds,
and never writes a derived scientific input.
"""
import argparse
import csv
import gzip
import io
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

SCHEMA = 'scpolya.q1_full_contig_scope_adapter.request.v1'
CALCULATOR_SHA = '39c0bd58e1d9658bdd72616b2f965ee09f7f45dd10a3902602f3b908da2381eb'
METHOD_SHA = 'f6f4db8364e7792abb7d8de5e8cbf1727b7df99a698a195b0e50b91c703156c3'
PRIMARY25 = [f'chr{x}' for x in list(range(1, 23)) + ['X', 'Y', 'M']]
ALIASES = {**{f'chr{i}': str(i) for i in range(1, 23)}, 'chrX': 'X', 'chrY': 'Y', 'chrM': None}
SOURCE_ROLES = {'calculator', 'method', 'catalogue', 'fasta', 'fai', 'atlas',
                'genome_receipt', 'atlas_receipt'}
CALCULATOR_SOURCE = 'calculator'


def fail(message):
    raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def regular(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        fail('absolute lexical path required: ' + str(path))
    for part in reversed([path, *path.parents]):
        if part.is_symlink():
            fail('symlink component: ' + str(path))
    if not path.is_file():
        fail('regular file required: ' + str(path))
    return path


def load_json(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except Exception as exc:
        fail('invalid JSON {}: {}'.format(path, type(exc).__name__))


def check_request(req, execute):
    if not isinstance(req, dict) or set(req) != {'schema', 'arm', 'contigs', 'output_root',
                                                 'sources', 'adapter_limits'}:
        fail('request fields differ from schema ' + SCHEMA)
    if req['schema'] != SCHEMA:
        fail('unsupported schema')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', req['arm']):
        fail('invalid arm')
    if not isinstance(req['contigs'], list) or not req['contigs']:
        fail('contigs must be a nonempty list')
    if len(req['contigs']) != len(set(req['contigs'])) or any(x not in PRIMARY25 for x in req['contigs']):
        fail('contigs must be unique exact members of the predeclared primary25 list')
    root = Path(req['output_root'])
    if not root.is_absolute() or '..' in root.parts:
        fail('output_root must be absolute lexical')
    if root.exists() or root.is_symlink():
        if execute or not root.is_dir() or any(root.iterdir()):
            fail('output_root must be absent before execution (an empty directory is allowed only for plan)')
    if not isinstance(req['sources'], dict) or set(req['sources']) != SOURCE_ROLES:
        fail('sources must contain exactly ' + ','.join(sorted(SOURCE_ROLES)))
    for role, desc in sorted(req['sources'].items()):
        if not isinstance(desc, dict) or set(desc) != {'path', 'sha256'}:
            fail('invalid source descriptor ' + role)
        path = regular(desc['path'])
        observed = sha(path)
        if observed != desc['sha256']:
            fail('source hash mismatch {}: expected {} observed {}'.format(role, desc['sha256'], observed))
        if role == 'calculator' and observed != CALCULATOR_SHA:
            fail('calculator implementation mismatch')
        if role == 'method' and observed != METHOD_SHA:
            fail('frozen method mismatch')
    limits = req['adapter_limits']
    if not isinstance(limits, dict) or set(limits) != {'per_contig_seconds'}:
        fail('adapter_limits fields')
    seconds = limits['per_contig_seconds']
    if type(seconds) is not int or not 1 <= seconds <= 120:
        fail('per_contig_seconds must be 1..120 and only tightens the calculator internal limit')
    return [Path(req['sources'][r]['path']) for r in sorted(SOURCE_ROLES)], root


def load_calculator(path):
    spec = importlib.util.spec_from_file_location('accepted_q1_site_quality_adapter_copy', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request_for(req, module, chrom, output_dir):
    sources = {role: copy.deepcopy(req['sources'][role]) for role in req['sources'] if role != CALCULATOR_SOURCE}
    return {'schema': module.REQUEST, 'arm': '{}--{}'.format(req['arm'], chrom), 'output_dir': str(output_dir),
            'sources': sources, 'limits': {}}


def make_scope_catalogue(source_desc, chrom, path):
    """Write a row-filtered catalogue view; coordinates and columns are unchanged."""
    with gzip.open(source_desc['path'], 'rt', newline='', encoding='ascii') as source, \
         open(path, 'wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as zipped, \
         io.TextIOWrapper(zipped, encoding='ascii', newline='') as target:
        reader = csv.reader(source, delimiter='\t')
        writer = csv.writer(target, delimiter='\t', lineterminator='\n')
        try:
            header = next(reader)
        except StopIteration:
            fail('empty catalogue')
        writer.writerow(header)
        try:
            chrom_i = header.index('chrom')
        except ValueError:
            fail('catalogue has no chrom column')
        count = 0
        for fields in reader:
            if len(fields) != len(header):
                fail('catalogue row width differs from generated header')
            if fields[chrom_i] == chrom:
                writer.writerow(fields); count += 1
    observed = sha(path)
    return count, observed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--plan', action='store_true')
    action.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        request_path = regular(args.request)
        req = load_json(request_path)
        _, output_root = check_request(req, args.execute)
        calculator_path = regular(req['sources'][CALCULATOR_SOURCE]['path'])
        module = load_calculator(calculator_path)
        original = copy.deepcopy(module.PARAMETERS)
        plans = []
        if args.plan:
            for chrom in req['contigs']:
                out = output_root / chrom
                module.PARAMETERS.clear(); module.PARAMETERS.update(original)
                module.PARAMETERS['chromosomes'] = [chrom]
                module.PARAMETERS['atlas_alias'] = ({ALIASES[chrom]: chrom} if ALIASES[chrom] else {})
                result = module.response(request_for(req, module, chrom, out), False)
                if result.get('status') != 'planned':
                    fail('plan failed {}: {}'.format(chrom, result))
                plans.append({'contig': chrom, 'atlas_alias': module.PARAMETERS['atlas_alias'],
                              'result': result, 'quality_analysis_executed': False})
            module.PARAMETERS.clear(); module.PARAMETERS.update(original)
            manifest = {'schema': SCHEMA + '.plan', 'status': 'planned', 'arm': req['arm'],
                        'calculator_sha256': CALCULATOR_SHA, 'method_sha256': METHOD_SHA,
                        'scope_adapter': 'explicit_exact_alias_per_contig; formulas unchanged',
                        'contigs': req['contigs'], 'plans': plans}
            sys.stdout.write(json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')
            return 0
        output_root.mkdir(parents=False, exist_ok=False)
        scope_dir = output_root / '.scope-inputs'
        scope_dir.mkdir()
        scope_catalogues = {}
        for chrom in req['contigs']:
            scope_path = scope_dir / (chrom + '.catalogue.tsv.gz')
            row_count, scope_hash = make_scope_catalogue(req['sources']['catalogue'], chrom, scope_path)
            scope_catalogues[chrom] = {'path': str(scope_path), 'sha256': scope_hash,
                                       'rows': row_count, 'source_sha256': req['sources']['catalogue']['sha256']}
        for chrom in req['contigs']:
            out = output_root / chrom
            module.PARAMETERS.clear(); module.PARAMETERS.update(original)
            module.PARAMETERS['chromosomes'] = [chrom]
            module.PARAMETERS['atlas_alias'] = ({ALIASES[chrom]: chrom} if ALIASES[chrom] else {})
            request = request_for(req, module, chrom, out)
            request['sources']['catalogue'] = {'path': scope_catalogues[chrom]['path'],
                                               'sha256': scope_catalogues[chrom]['sha256']}
            result = module.response(request, True)
            if result.get('status') != 'ok':
                module.PARAMETERS.clear(); module.PARAMETERS.update(original)
                fail('calculator refused {}: {}'.format(chrom, result))
        module.PARAMETERS.clear(); module.PARAMETERS.update(original)
        records = []
        for chrom in req['contigs']:
            out = output_root / chrom
            output_files = {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
                            for p in out.iterdir() if p.is_file()}
            records.append({'contig': chrom, 'atlas_alias': ALIASES[chrom], 'output_dir': str(out),
                            'result_status': 'ok', 'output_files': output_files,
                            'scope_catalogue': scope_catalogues[chrom]})
        manifest = {'schema': SCHEMA + '.execution', 'status': 'complete', 'arm': req['arm'],
                    'calculator_sha256': CALCULATOR_SHA, 'method_sha256': METHOD_SHA,
                    'scope_adapter': 'explicit_exact_alias_per_contig; formulas unchanged',
                    'request_path': str(request_path), 'request_sha256': sha(request_path),
                    'contig_results': records}
        with (output_root / 'adapter-manifest.json').open('x', encoding='utf-8') as handle:
            handle.write(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + '\n')
        sys.stdout.write(json.dumps({'schema': manifest['schema'], 'status': 'complete', 'arm': req['arm'],
                                     'manifest': str(output_root / 'adapter-manifest.json')},
                                    sort_keys=True, separators=(',', ':')) + '\n')
        return 0
    except (ValueError, OSError) as exc:
        sys.stdout.write(json.dumps({'schema': SCHEMA, 'status': 'refused', 'detail': str(exc)[:500],
                                     'quality_analysis_executed': False}, sort_keys=True,
                                    separators=(',', ':')) + '\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
