"""Optional annotation after annotate_peaks; importing this module needs no torch."""
from __future__ import annotations


def resolve_deepcps_inputs(layout, args, cache):
    from task_manage import ScItem
    from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_upm_factor
    from task_manage.effective_config import build_annotated_peak_config, exp_coverage_scaled
    from task_manage.run_layout import FILTERED_STD_CHROM_SIZES
    from utils.terminal_quant_utils import quantification_artifact_suffix, quantification_mode

    item = build_annotated_peak_config(args, dedup_method=cache.get(CACHE_dedup_method),
                                      polyA_status=cache.get(CACHE_polyA_status))
    bed = layout.ResolveAnnotatedFinalOutputFile(
        suffix=quantification_artifact_suffix('___peaks-annotated.bed', quantification_mode(args)), d=item)
    scaling = cache.get(CACHE_upm_factor) if exp_coverage_scaled(args) else None
    if exp_coverage_scaled(args) and scaling is None:
        raise ValueError('DeepCPS cannot resolve scaled expression coverage without its UPM factor')
    tracks = []
    for strand in ('plus', 'minus'):
        track = ScItem(strand=strand, scaling_factor=scaling, is_cohort=True, is_polyA=False,
                       coverage_source='unstranded', strand_filter=strand, add_dedup_method=True, args=args)
        tracks.append(layout.CohortOutput(track.cohort_name) / track.format_name(suffix='bw', is_coverage=True))
    cohort = ScItem(is_cohort=True, add_dedup_method=True, args=args)
    bam = layout.CohortOutput(cohort.cohort_name) / cohort.format_name(suffix='bam')
    return dict(bed=bed, fasta=args.reference_fasta, bam=bam,
                chrom_sizes=layout.ReferenceFile(file_key=FILTERED_STD_CHROM_SIZES, args=args),
                cov_plus=tracks[0], cov_minus=tracks[1], model_dir=args.deepcps_model_dir), item


def deepcps(tm, layout, args, cache):
    if not getattr(args, 'deepcps', False):
        return None
    from logging_config import get_logger, stage_scope, task_scope, LOG_MAIN
    from modules.deepcps.pipeline import DEFAULT_MANIFEST, digest, prepare_request, run_annotation

    inputs, item = resolve_deepcps_inputs(layout, args, cache)
    request = prepare_request(**inputs,
                              model_manifest=getattr(args, 'deepcps_model_manifest', None) or DEFAULT_MANIFEST,
                              threads=getattr(args, 'deepcps_threads', 2),
                              batch_size=getattr(args, 'deepcps_batch_size', 128),
                              expected_reference_sha256=getattr(args, 'deepcps_reference_sha256', None))
    item = dict(item, deepcps_fingerprint=digest(request))
    with stage_scope('deepcps'), task_scope('score_and_join'):
        result = tm.run_inline_execution(stage='deepcps', task_base='score_and_join', item=item,
                                         fn=run_annotation,
                                         call_kwargs=dict(request=request, output_root=inputs['bed'].parent / 'deepcps'))
        get_logger(LOG_MAIN).info(f'DeepCPS {result["status"]}: {result["output_dir"]}; candidate identity in provenance.json')
    return result
