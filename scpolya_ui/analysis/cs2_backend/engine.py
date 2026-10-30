"""New-input binding and orchestration over frozen scientific modules."""
from collections import Counter
import gc
import json
from pathlib import Path

from . import api
from .method import common, descriptive, masking, paired, qc_rows
from .method import full_contig_adapter as scope


def qc_manifest(request, arm, output, pins, guard):
    a = request['arms'][arm]
    if request.get('qc_input', 'compute') == 'compute':
        root = output/'qc'/arm
        root.mkdir(parents=True)
        scope_root = root/'.scope-inputs'; scope_root.mkdir()
        calculator = scope.load_calculator(Path(scope.__file__).parent/'q1_site_quality.py')
        original = dict(calculator.PARAMETERS)
        entries = []
        for chrom in request['contigs']:
            guard.check()
            path = scope_root/(chrom+'.catalogue.tsv.gz')
            n, h = scope.make_scope_catalogue(a['files']['peak_to_gene'], chrom, path)
            calculator.PARAMETERS.clear(); calculator.PARAMETERS.update(original)
            calculator.PARAMETERS['chromosomes'] = [chrom]
            calculator.PARAMETERS['atlas_alias'] = ({scope.ALIASES[chrom]: chrom} if scope.ALIASES[chrom] else {})
            target = root/chrom
            sources = {k:{'path':v['path'],'sha256':v['sha256']} for k,v in request['references'].items()}
            sources['catalogue'] = {'path':str(path),'sha256':h}
            q = {'schema':calculator.REQUEST, 'arm':arm+'--'+chrom,
                 'output_dir':str(target), 'sources':sources, 'limits':{}}
            calculator.execute(q)
            entries.append({'contig':chrom, 'output_dir':str(target),
                'scope_catalogue':{'path':str(path),'sha256':h,'rows':n,
                                   'source_sha256':a['files']['peak_to_gene']['sha256']},
                'output_files':{p.name:{'bytes':p.stat().st_size,'sha256':api.digest(p)}
                                for p in target.iterdir() if p.is_file()}})
        manifest = {'status':'complete','calculator_sha256':scope.CALCULATOR_SHA,
                    'method_sha256':scope.METHOD_SHA,'contig_results':entries}
        api.emit(root/'adapter-manifest.json', manifest)
    else:
        manifest = pins.small(a['qc_manifest'])
    api.need(manifest['status']=='complete' and manifest['calculator_sha256']==scope.CALCULATOR_SHA and
             manifest['method_sha256']==scope.METHOD_SHA, 'QC method/complete binding')
    api.need([x['contig'] for x in manifest['contig_results']]==request['contigs'], 'QC contig order')
    paths = []
    for entry in manifest['contig_results']:
        api.need(entry['scope_catalogue']['source_sha256']==a['files']['peak_to_gene']['sha256'],
                 'QC source catalogue mismatch')
        root = Path(entry['output_dir'])
        source = pins.small({'path':str(root/'source-manifest.json'), **entry['output_files']['source-manifest.json']})
        api.need(source['method_sha256']==scope.METHOD_SHA and
                 source['implementation_sha256']==scope.CALCULATOR_SHA, 'QC source method binding')
        api.need(source['sources']['catalogue']['expected_sha256']==entry['scope_catalogue']['sha256'],
                 'QC scoped source mismatch')
        for role, desc in request['references'].items():
            api.need(source['sources'][role]['expected_sha256']==desc['sha256'], 'QC reference mismatch: '+role)
        path = pins.add({'path':str(root/'sites.tsv'), **entry['output_files']['sites.tsv']})
        paths.append((entry['contig'], path))
    return paths


def paired_summary(request, output, cohorts, guard):
    """Independent original load_arm/summarize path; no old answer is read.

    The original paired execute's summary loops are retained. Only publication
    of large observation TSVs and crosswalk is omitted; PSI validation remains.
    """
    output.mkdir()
    data = {}
    try:
        for arm in api.ARMS:
            guard.check()
            data[arm] = paired.load_arm(request['arms'][arm], output/(arm+'.sqlite'), cohorts)
        for k in data['coverage3']['eligible']['native_terminal'] & data['coverage1']['eligible']['native_terminal']:
            tx=lambda arm:{r['canonical_transcript'] for r in data[arm]['native'][k] if not paired.missing(r['canonical_transcript'])}
            paired.need(tx('coverage3')==tx('coverage1'),'cross-arm canonical transcript mismatch')
        summary={'arms':{},'comparisons':{}}
        for arm,d in data.items():
            summary['arms'][arm]={'catalogue':d['catalogue'],'measurements':[]}
            for definition,obs in d['observations'].items():
                shared=d['eligible'][definition]&data['coverage1' if arm=='coverage3' else 'coverage3']['eligible'][definition]
                for cohort,cells in cohorts.items():
                    for subset,eligible in [('all_eligible',d['eligible'][definition]),('shared_eligible_genes',shared),('arm_only_eligible_genes',d['eligible'][definition]-shared)]:
                        for rule in (['native_peak_record','exact_endpoint'] if definition=='native_terminal' else ['exact_endpoint']):
                            for cutoff in common.CUTOFFS:
                                summary['arms'][arm]['measurements'].append({'definition':definition,'site_identity_rule':rule,'cohort':cohort,'gene_subset':subset,'umi_cutoff':cutoff,**paired.summarize(obs,eligible,cells,cutoff,rule)})
        for definition in data['coverage3']['observations']:
            a,b=(data[arm]['observations'][definition] for arm in api.ARMS);shared=set(a)&set(b);pairs=[]
            for cutoff in common.CUTOFFS:
                for cohort,cells in cohorts.items():
                    states=Counter((a[k]['positive_endpoints']>=2 and a[k]['terminal_umis']>=cutoff,b[k]['positive_endpoints']>=2 and b[k]['terminal_umis']>=cutoff) for k in shared if k[2] in cells)
                    # Original paired serializer omits unobserved transition cells;
                    # masking emits all four. Expand absent cells to the defined 0.
                    pairs.append({'cohort':cohort,'cutoff':cutoff,'site_identity_rule':'exact_endpoint','direction':'coverage3_to_coverage1','transitions':[{'coverage3_qualifies':x,'coverage1_qualifies':y,'n':states[x,y]} for x in (False,True) for y in (False,True)],'coverage3_only_observed_records':sum(k[2] in cells for k in set(a)-set(b)),'coverage1_only_observed_records':sum(k[2] in cells for k in set(b)-set(a))})
            summary['comparisons'][definition]=pairs
        api.emit(output/'unmasked.json', summary)
        return summary
    finally:
        for d in data.values(): d['db'].close()
        data.clear();gc.collect()


def compute(plan, output):
    request = plan['request']; pins = common.Pins()
    guard = common.Guard(output, request['resources']['wall_seconds'], request['resources']['disk_bytes'],
                         scratch=output.parent/'scratch', budget_root=output.parent)
    try:
        for record in plan['inputs'] + plan['methods']: pins.add(record)
        api.emit(output/'INPUT-PINS-BEFORE.json', list(pins.records.values()))
        cohorts = paired.cohort_registry(request)
        # Separate original paired algorithm includes exact native PSI and gene-cell validation.
        expected = paired_summary(request, output/'paired', cohorts, guard)
        data = {}; bridge = dict(request); bridge['arms'] = {}
        for arm in api.ARMS:
            a = request['arms'][arm]; path = a['files']['peak_to_gene']['path']
            meta,*_ = paired.metadata(path, a['config']['misprimed_ratio_threshold'])
            api.need({r['chrom'] for r in meta.values()}<=set(request['contigs']), 'catalogue contig scope')
            raw = {r['peak_name']:r for r in common.tsv(path, ['peak_name'], request['bounds']['catalogue_rows'])}
            bridge['arms'][arm] = {'counts':a['files']['counts'], 'expected_catalogue_rows':len(meta)}
            paths = qc_manifest(request, arm, output, pins, guard)
            # Same frozen source-row/QC validation on fresh and explicitly selected saved QC.
            data[arm] = qc_rows.load_rows(bridge, arm, meta, raw, paths, guard)
        a = descriptive.analyze(data, output/'descriptive', request['bounds']['overlap_edges'], guard, paired.missing)
        b = masking.analyze(data, bridge, output/'masking', cohorts, paired, expected, guard)
        api.emit(output/'INPUT-PINS-PREPOST.json', pins.post());guard.check()
        for record in plan['inputs'] + plan['methods']:
            api.need(api.pin(record)==record, 'input changed during task')
        files = [{'path':str(p.relative_to(output)), 'bytes':p.stat().st_size, 'sha256':api.digest(p)}
                 for p in sorted(output.rglob('*')) if p.is_file() and p.suffix!='.sqlite'
                 and p.name not in {'stdout.log','stderr.log','STATUS.json'}]
        result = {'schema':api.SCHEMA+'.result', 'status':'computed', 'ended_utc':api.now(),
                  'plan_sha256':plan['plan_sha256'], 'parameters':plan['parameters'],
                  'families':{'descriptive':a, 'masking':b}, 'files':files,
                  'review_status':'PENDING_INDEPENDENT_REVIEW', 'limits':plan['limitations'],
                  'roles':{arm:{'run_id':request['arms'][arm]['run']['record_id'],
                               'record_id':request['arms'][arm]['run']['record_id'],
                               'invocation_id':request['arms'][arm]['run']['invocation_id'],
                               'root':request['arms'][arm]['run']['root'],
                               'label':request['arms'][arm].get('label',arm)} for arm in api.ARMS}}
        api.emit(output/'RESULT.json', result)
        tables = []; artifacts = []
        for item in files:
            entry = {'name':item['path'], **item}
            path = output/item['path']
            if path.suffix == '.tsv':
                with path.open() as f:
                    entry.update(format='tsv', columns=f.readline().rstrip('\n').split('\t'), row_count=sum(1 for _ in f))
                tables.append(entry)
            elif path.suffix == '.json': artifacts.append(entry)
        api.emit(output/'index.json', {'schema':api.SCHEMA+'.index','method':'cs2.qc_compare',
            'status':'succeeded','summary':result['families'],'tables':tables,'artifacts':artifacts,
            'method_sources':plan['method_sources'],'input_sources':plan['input_sources'],
            'parameters':plan['parameters'],'limitations':plan['limitations'],
            'review_status':'PENDING_INDEPENDENT_REVIEW','roles':result['roles']})
        api.emit(output/'STATUS.json', {'status':'succeeded','ended_utc':api.now()}, replace=True)
    except BaseException as error:
        try: post={'status':'UNCHANGED','records':pins.post()}
        except Exception as failure: post={'status':'POSTCHECK_FAILED','error':str(failure)}
        api.emit(output/'INPUT-PINS-FAILURE-POST.json', post)
        api.emit(output/'REFUSAL.json', {'status':'REFUSED_PARTIAL_OUTPUT_PRESERVED',
            'error_type':type(error).__name__, 'error':str(error), 'guard_failure':guard.failure,
            'callback_error':guard.callback_error, 'sqlite_error':guard.sqlite_error, 'no_automatic_retry':True})
        raise
