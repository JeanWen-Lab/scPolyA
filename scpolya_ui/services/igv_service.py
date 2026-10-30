from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import hashlib
import json
from typing import Any, Callable, Mapping, Iterable
from types import SimpleNamespace

from nicegui import app
from task_manage.effective_config import peak_threshold_label, polyA_coverage_scaled

from scpolya_ui.errors import UserError
from scpolya_ui.igv_manifest import (
    GeneLocus,
    IgvManifest,
    artifact_route,
    build_igv_manifest,
    build_single_run_igv_manifest,
    manifest_as_igv_options,
    transcript_model_tracks,
    with_transcript_models,
    with_view_window,
)
from scpolya_ui.reference_cache import cached_reference_dir, find_reference_fasta
from scpolya_ui.run_records import RunRecord, resolve_execution_config
from scpolya_ui.registry import (
    PathMapping, WorkspaceRecord, apply_path_mappings_to_config, apply_path_mappings_to_value,
)
from scpolya_ui.studies import StudyRecord
from scpolya_ui.view_models.igv_vm import IgvManifestSummary, igv_manifest_summary


def ready_track_count(manifest: IgvManifest) -> int:
    return len(manifest.tracks)


def igv_options(manifest: IgvManifest) -> dict:
    return manifest_as_igv_options(manifest)


def manifest_summary(manifest: IgvManifest) -> IgvManifestSummary:
    return igv_manifest_summary(manifest)


def register_artifact_url(path: str) -> str:
    route, directory, url = artifact_route(path)
    if Path(path).expanduser().exists():
        app.add_static_files(route, str(directory))
    return url


def transcript_cache_dir() -> Path:
    return cached_reference_dir() / "transcripts"


def study_manifest(
    study: StudyRecord,
    *,
    gene: str,
    base_config: Mapping[str, Any],
    url_for_file: Callable[[str], str] = register_artifact_url,
    view_window: GeneLocus | None = None,
) -> IgvManifest:
    manifest = build_igv_manifest(
        study,
        gene=gene,
        base_config=base_config,
        url_for_file=url_for_file,
    )
    manifest = replace(manifest, locus=with_view_window(manifest.locus, view_window))
    return with_transcript_models(manifest, url_for_file, transcript_cache_dir())


def single_run_manifest(
    record: RunRecord,
    *,
    gene: str,
    base_config: Mapping[str, Any],
    workspace_root: str,
    url_for_file: Callable[[str], str] = register_artifact_url,
    path_mappings: Iterable[PathMapping] = (),
    transcript_models: bool = True,
    view_window: GeneLocus | None = None,
) -> IgvManifest:
    mappings = tuple(path_mappings)
    execution_config, view_root = run_view_paths(record, workspace_root=workspace_root, path_mappings=mappings)
    artifacts = apply_path_mappings_to_config(dict(record.expected_artifacts), mappings)
    manifest = build_single_run_igv_manifest(
        record_id=record.id,
        label=record.label,
        gene=gene,
        base_config=execution_config,
        workspace_root=view_root,
        expected_artifacts=artifacts,
        url_for_file=url_for_file,
    )
    original = resolve_execution_config(record)
    if execution_config != original.config or view_root != workspace_root:
        manifest = replace(manifest, view_paths={
            "original_root_path": record.root_path or workspace_root,
            "root_path": view_root,
            "original_execution_config_hash": original.config_hash,
            "config": execution_config,
        })
    if not any(t.role == "annotated_peak" for t in manifest.tracks):
        from .historical_annotation import historical_v2_overlay
        historical_tracks, evidence = historical_v2_overlay(
            record, view_config=execution_config, view_root=view_root,
            path_mappings=mappings, url_for_file=url_for_file,
        )
        if evidence:
            manifest = replace(manifest, tracks=(*manifest.tracks, *historical_tracks), historical_evidence=evidence)
    manifest = replace(manifest, locus=with_view_window(manifest.locus, view_window))
    if transcript_models:
        manifest = with_transcript_models(manifest, url_for_file, transcript_cache_dir())
    return manifest


def run_view_paths(
    record: RunRecord, *, workspace_root: str, path_mappings: Iterable[PathMapping] = (),
) -> tuple[dict[str, Any], str]:
    """Resolve an explicit UI view without changing the execution snapshot."""
    resolution = resolve_execution_config(record)
    if not resolution.available:
        raise ValueError(resolution.error)
    mappings = tuple(path_mappings)
    config = apply_path_mappings_to_config(resolution.config, mappings)
    root = apply_path_mappings_to_value(workspace_root, mappings)
    if root != workspace_root:
        original_config_root = Path(str(resolution.config.get("output_dir") or "")) / str(resolution.config.get("run_id") or "")
        if original_config_root.expanduser().resolve() != Path(workspace_root).expanduser().resolve():
            raise ValueError("Mapped run root disagrees with the original execution configuration")
        # Relocation can change the directory basename. These are view-only
        # layout fields; the native run_id/config/hash remain on the record.
        config["output_dir"] = str(Path(root).parent)
        config["run_id"] = Path(root).name
    return config, root


def _comparison_threshold_label(config: Mapping[str, Any]) -> str:
    args = SimpleNamespace(**config)
    try:
        threshold = peak_threshold_label(args, is_polyA=True)
        units = "scaled" if polyA_coverage_scaled(args) else "unscaled"
    except AttributeError:
        return "polyA threshold=unavailable"
    return f"polyA threshold={threshold} {config.get('polyA_peak_threshold_mode', '?')}/{units}"


def comparison_run_label(record: RunRecord) -> str:
    resolution = resolve_execution_config(record)
    config = resolution.config
    return (f"{record.label or record.run_id or record.id}: "
            f"{_comparison_threshold_label(config)}, "
            f"minpolyA={config.get('minpolyA', '?')}, "
            f"scaling={config.get('coverage_scaling', '?')} [{record.id}]")


def comparison_manifest(
    first: RunRecord,
    second: RunRecord,
    *,
    gene: str,
    url_for_file: Callable[[str], str] = register_artifact_url,
    workspaces: Mapping[str, WorkspaceRecord] | None = None,
    view_window: GeneLocus | None = None,
) -> IgvManifest:
    """Read two completed invocation records without creating a study or index.

    Conservatively require the same resolved FASTA and GTF paths. Different
    files need external reference validation; matching species labels alone
    cannot establish that their coordinate systems agree.
    """
    if first.id == second.id or (first.invocation_id and first.invocation_id == second.invocation_id):
        raise UserError("error.igv.pick_two", "Comparison unavailable: select two distinct completed invocations")
    if not gene.strip():
        raise UserError("error.igv.enter_gene", "Comparison unavailable: enter a gene")
    manifests, sources, references = [], [], []
    for record in (first, second):
        if record.status != "succeeded" or not record.invocation_id or record.history_source != "invocation":
            raise UserError("error.igv.not_completed", f"Comparison unavailable: {record.id} is not a completed native invocation", run=record.label or record.id)
        resolution = resolve_execution_config(record)
        if not resolution.available:
            raise ValueError(f"Comparison unavailable: {resolution.error}")
        root = record.root_path or record.workspace_root
        workspace = (workspaces or {}).get(record.workspace_id)
        mappings = tuple(workspace.path_mappings) if workspace else ()
        view_config, view_root = run_view_paths(record, workspace_root=root, path_mappings=mappings)
        for key in ("fasta", "reference_fasta", "genome_fasta", "ref_fasta"):
            if view_config.get(key) and not Path(str(view_config[key])).expanduser().is_file():
                raise UserError("error.igv.fasta_missing", f"Comparison unavailable: {record.id} configured FASTA is missing: {resolution.config[key]}", run=record.label or record.id, path=str(resolution.config[key]))
        manifest = single_run_manifest(record, gene=gene.strip(), base_config=resolution.config,
                                       workspace_root=root, url_for_file=url_for_file, path_mappings=mappings,
                                       transcript_models=False)
        reference = find_reference_fasta(view_config, view_root, create_index=False)
        if reference is None:
            raise UserError("error.igv.fai_missing", f"Comparison unavailable: {record.id} requires a local FASTA with an existing FAI", run=record.label or record.id)
        gtf = Path(manifest.reference_gtf).expanduser()
        if not manifest.reference_gtf or not gtf.is_file():
            raise UserError("error.igv.gtf_missing", f"Comparison unavailable: {record.id} has no readable reference GTF", run=record.label or record.id)
        if manifest.locus is None:
            raise UserError("error.igv.gene_not_found", f"Comparison unavailable: gene {gene} has no locus in {record.id}", gene=gene, run=record.label or record.id)
        required_roles = {"coverage_polya", "coverage_scrna", "raw_peak_scrna", "raw_peak_polya", "annotated_peak"}
        missing_roles = sorted(required_roles - {t.role for t in manifest.tracks})
        missing_roles += [f"{role} {strand}" for role in sorted(required_roles - {"annotated_peak"})
                          for strand in ("+", "-")
                          if not any(t.role == role and t.name.endswith(f" {strand}") for t in manifest.tracks)]
        if manifest.missing_tracks or missing_roles:
            reason = "; ".join((*manifest.missing_tracks, *(f"missing {role} for this configuration" for role in missing_roles)))
            raise ValueError(f"Comparison unavailable: {record.id}: {reason}")
        references.append((reference.genome, str(reference.fasta_path.resolve()),
                           str(reference.index_path.resolve()), str(gtf.resolve())))
        sources.append({"run_record_id": record.id, "invocation_id": record.invocation_id,
                        "label": comparison_run_label(record), "root_path": root,
                        "execution_config_hash": resolution.config_hash,
                        "invocation_config_hash": record.invocation_config_hash,
                        "config_source": resolution.source,
                        "execution_config": resolution.config,
                        "reference_fasta": str(reference.fasta_path.resolve()),
                        "reference_index": str(reference.index_path.resolve()),
                        "viewer_reference_path": str(reference.fasta_path),
                        "viewer_reference_source": reference.source,
                        "reference_gtf": manifest.reference_gtf})
        if manifest.view_paths:
            sources[-1]["view_paths"] = manifest.view_paths
        manifests.append(manifest)
    if references[0] != references[1]:
        raise UserError("error.igv.reference_mismatch", "Comparison unavailable: reference mismatch; both runs must use the same resolved FASTA, FAI and GTF files")
    left, right = (m.locus for m in manifests)
    if (left.chrom, left.start, left.end, left.strand) != (right.chrom, right.start, right.end, right.strand):
        raise UserError("error.igv.locus_mismatch", "Comparison unavailable: gene coordinates or strand differ between runs")
    # Focus includes both actual peak sets; input order and path enumeration
    # never select one run's coordinates as the shared viewport.
    locus = replace(left, focus_start=min(left.focus_start or left.start, right.focus_start or right.start),
                    focus_end=max(left.focus_end or left.end, right.focus_end or right.end))
    locus = with_view_window(locus, view_window)
    tracks = []
    for slot, record, source, manifest in zip(("A", "B"), (first, second), sources, manifests):
        source["comparison_slot"] = slot
        config = source["execution_config"]
        prefix = f"{slot} {_comparison_threshold_label(config)} minpolyA={config.get('minpolyA', '?')}"
        tracks.extend(replace(track, name=f"{prefix} | {track.name}", study_id="", variant_id="",
                              run_record_id=record.id, invocation_id=record.invocation_id)
                      for track in manifest.tracks)
    # Place matching track kinds next to one another for visual comparison.
    order = {key: i for i, key in enumerate(dict.fromkeys((t.role, t.name) for m in manifests for t in m.tracks))}
    tracks.sort(key=lambda t: order[(t.role, t.name.split(" | ", 1)[1])])
    # Both runs resolved the same GTF (checked above), so one shared track.
    shared, transcript_notes = transcript_model_tracks(locus, manifests[0].reference_gtf, url_for_file, transcript_cache_dir())
    tracks[0:0] = shared
    identity = json.dumps({"gene": gene.strip(), "runs": sources}, sort_keys=True, separators=(",", ":"))
    return IgvManifest(study_id="", gene=gene.strip(), locus=locus, tracks=tuple(tracks),
                       missing_tracks=tuple(transcript_notes),
                       genome=manifests[0].genome, reference_gtf=manifests[0].reference_gtf,
                       reference_fasta_url=url_for_file(references[0][1]),
                       reference_index_url=url_for_file(references[0][2]),
                       comparison_id="C-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
                       run_sources=tuple(sources),
                       reference_compatibility="Same resolved viewer FASTA, FAI and run GTF; matching gene coordinates and strand. File compatibility only; no scientific equivalence claim.")


def manifest_payload_with_reference(
    manifest: IgvManifest,
    *,
    base_config: Mapping[str, Any],
    workspace_root: str,
) -> dict[str, Any]:
    payload = manifest.as_dict()
    if manifest.comparison_id:
        payload["reference_status"] = "ready"
        payload["reference_message"] = manifest.reference_compatibility
        return payload
    reference = find_reference_fasta(dict(base_config), workspace_root)
    if reference is not None:
        payload["reference_fasta_url"] = register_artifact_url(str(reference.fasta_path))
        payload["reference_index_url"] = register_artifact_url(str(reference.index_path))
        payload["reference_status"] = "ready"
        payload["reference_message"] = str(reference.fasta_path)
    else:
        payload["reference_status"] = "missing"
        payload["reference_message"] = ""
    return payload
