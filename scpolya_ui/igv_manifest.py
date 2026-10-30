from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, replace
from glob import glob as expand_glob
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

from task_manage import project_run_outputs_v1
from task_manage.output_projection import ANNOTATION_SCHEMA_NOT_OPENED
from task_manage.effective_config import exp_coverage_scaled, polyA_coverage_scaled
from task_manage.sc_item import ScItem

from .studies import StudyRecord, StudyVariantRecord
from .transcript_annotation import scoped_transcript_models


@dataclass(frozen=True)
class GeneLocus:
    gene: str
    chrom: str
    start: int
    end: int
    strand: str = ""
    focus_start: int | None = None
    focus_end: int | None = None
    viewport_locus: str = ""

    @property
    def igv_locus(self) -> str:
        if self.viewport_locus:
            return self.viewport_locus
        view_start = self.focus_start if self.focus_start is not None else self.start
        view_end = self.focus_end if self.focus_end is not None else self.end
        padding = max(500, int((view_end - view_start) * 1.5))
        padding = min(padding, 5000)
        start = max(1, view_start - padding)
        end = view_end + padding
        return f"{self.chrom}:{start}-{end}"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.viewport_locus:
            payload.pop("viewport_locus")
        payload["igv_locus"] = self.igv_locus
        return payload


def with_view_window(locus: GeneLocus | None, window: GeneLocus | None) -> GeneLocus | None:
    """Keep a validated annotation-reload window without changing normal focus.

    Bind it to the resolved coordinate query so it cannot substitute another
    gene/contig. Both viewer and transcript subset use this same exact window.
    """
    if window is None:
        return locus
    if (locus is None or window.start < 1 or window.end < window.start or
            (locus.chrom, locus.start, locus.end) != (window.chrom, window.start, window.end)):
        raise ValueError("Annotation reload window does not match the resolved locus")
    return replace(locus, viewport_locus=f"{window.chrom}:{window.start}-{window.end}")


@dataclass(frozen=True)
class IgvTrack:
    name: str
    path: str
    url: str
    format: str
    track_type: str
    role: str
    variant_id: str = ""
    study_id: str = ""
    index_path: str = ""
    index_url: str = ""
    run_record_id: str = ""
    invocation_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "format": self.format,
            "type": self.track_type,
            "role": self.role,
            "variant_id": self.variant_id,
            "study_id": self.study_id,
            "path": self.path,
            "index_path": self.index_path,
            "index_url": self.index_url,
            **({"run_record_id": self.run_record_id, "invocation_id": self.invocation_id}
               if self.run_record_id else {}),
        }


@dataclass(frozen=True)
class IgvManifest:
    study_id: str
    gene: str
    locus: GeneLocus | None
    tracks: tuple[IgvTrack, ...]
    missing_tracks: tuple[str, ...]
    genome: str = ""
    reference_gtf: str = ""
    reference_fasta_url: str = ""
    reference_index_url: str = ""
    comparison_id: str = ""
    run_sources: tuple[dict[str, Any], ...] = ()
    reference_compatibility: str = ""
    view_paths: dict[str, Any] | None = None
    historical_evidence: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "gene": self.gene,
            "locus": self.locus.as_dict() if self.locus else None,
            "tracks": [track.as_dict() for track in self.tracks],
            "missing_tracks": list(self.missing_tracks),
            "genome": self.genome,
            "reference_gtf": self.reference_gtf,
            "reference_fasta_url": self.reference_fasta_url,
            "reference_index_url": self.reference_index_url,
            **({"view_paths": self.view_paths} if self.view_paths else {}),
            **({"historical_evidence": self.historical_evidence} if self.historical_evidence else {}),
            **({"comparison_id": self.comparison_id, "source": "independent_runs",
                "run_sources": list(self.run_sources),
                "reference_compatibility": self.reference_compatibility}
               if self.comparison_id else {}),
        }


@dataclass(frozen=True)
class _ProjectedTrackDiscovery:
    tracks: tuple[IgvTrack, ...]
    fallback_allowed: bool
    integrity_errors: tuple[str, ...] = ()
    missing_tracks: tuple[str, ...] = ()
    # The run's recorded annotation schema was refused: no annotation-scoped
    # path (projected or globbed) may stand in for it.
    annotation_schema_refused: bool = False


def resolve_gene_locus(gtf_path: str | Path, gene: str) -> GeneLocus | None:
    path = Path(gtf_path).expanduser()
    target = gene.strip()
    if not target or not path.exists():
        return None
    target_lower = target.lower()
    chrom = ""
    strand = ""
    start: int | None = None
    end: int | None = None
    with path.open("rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            feature = parts[2].lower()
            if feature not in {"gene", "gene_locus", "transcript", "exon", "three_prime_utr"}:
                continue
            attrs = parse_gtf_attributes(parts[8])
            names = {
                attrs.get("gene_name", ""),
                attrs.get("gene_id", ""),
                attrs.get("Name", ""),
                attrs.get("gene", ""),
            }
            if target_lower not in {name.lower() for name in names if name}:
                continue
            row_start = int(parts[3])
            row_end = int(parts[4])
            chrom = parts[0]
            strand = parts[6]
            start = row_start if start is None else min(start, row_start)
            end = row_end if end is None else max(end, row_end)
            if feature in {"gene", "gene_locus"}:
                break
    if start is None or end is None or not chrom:
        return None
    return GeneLocus(gene=target, chrom=chrom, start=start, end=end, strand=strand)


def parse_gtf_attributes(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r'([A-Za-z0-9_.:-]+)\s+"([^"]*)"', text):
        attrs[match.group(1)] = match.group(2)
    if attrs:
        return attrs
    for match in re.finditer(r"([A-Za-z0-9_.:-]+)\s+([^;\s]+)", text):
        attrs[match.group(1)] = match.group(2).strip('"')
    if attrs:
        return attrs
    for token in text.split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        key, value = token.split("=", 1)
        attrs[key.strip()] = value.strip().strip('"')
    return attrs


def build_igv_manifest(
    study: StudyRecord,
    *,
    gene: str,
    base_config: Mapping[str, Any],
    url_for_file: Callable[[str], str],
    max_tracks: int = 12,
) -> IgvManifest:
    root = Path(study.workspace_root).expanduser()
    gtf_path = _reference_gtf_path(base_config, study.workspace_root)
    locus = _resolve_locus(root, gtf_path, gene)
    tracks: list[IgvTrack] = []
    missing: list[str] = []
    if locus is not None:
        tracks.extend(_shared_evidence_tracks(root, url_for_file))
    for variant in study.variants[:max_tracks]:
        track = _track_for_variant(study, variant, url_for_file)
        if track is None:
            missing.append(f"{variant.id}: no annotated BED/BigBed artifact")
            continue
        tracks.append(track)
    locus = _locus_focused_on_peak_tracks(locus, tracks) or locus
    return IgvManifest(
        study_id=study.id,
        gene=gene,
        locus=locus,
        tracks=_dedupe_tracks(tracks),
        missing_tracks=tuple(missing),
        genome=str(base_config.get("species") or ""),
        reference_gtf=gtf_path,
    )


def build_single_run_igv_manifest(
    *,
    record_id: str,
    label: str,
    gene: str,
    base_config: Mapping[str, Any],
    workspace_root: str,
    expected_artifacts: Mapping[str, Any],
    url_for_file: Callable[[str], str],
) -> IgvManifest:
    root = Path(workspace_root).expanduser()
    gtf_path = _reference_gtf_path(base_config, workspace_root)
    locus = _resolve_locus(root, gtf_path, gene)
    tracks: list[IgvTrack] = []
    missing: list[str] = []
    projected = _projected_stage_tracks(base_config, url_for_file)
    missing.extend(projected.missing_tracks)
    if locus is not None:
        tracks.extend(_reference_context_tracks(root, url_for_file))
        tracks.extend(projected.tracks)
        if projected.fallback_allowed:
            tracks.extend(_coverage_tracks(root, url_for_file))
            tracks.extend(_stage_audit_peak_tracks(
                root, url_for_file,
                include_annotation=not projected.annotation_schema_refused,
            ))
    if not projected.integrity_errors and not projected.annotation_schema_refused:
        fallback = _existing_peak_artifact(expected_artifacts)
        if fallback and not any(Path(track.path).expanduser() == Path(fallback).expanduser() for track in tracks):
            tracks.append(_peak_track(label or record_id, Path(fallback).expanduser(), "annotated_peak", url_for_file))
    if not any(track.role in {"annotated_peak", "variant_peak"} for track in tracks):
        if projected.integrity_errors:
            missing.extend(
                f"current run: output namespace integrity error: {message}"
                for message in projected.integrity_errors
            )
        else:
            missing.append("current run: no annotated BED/BigBed artifact")
    locus = _locus_focused_on_peak_tracks(locus, tracks) or locus
    return IgvManifest(
        study_id=record_id,
        gene=gene,
        locus=locus,
        tracks=_dedupe_tracks(tracks),
        missing_tracks=tuple(missing),
        genome=str(base_config.get("species") or ""),
        reference_gtf=gtf_path,
    )


def artifact_route(path: str) -> tuple[str, Path, str]:
    # A registered view may deliberately alias immutable external evidence.
    # Use the same canonical file for URL generation and static registration.
    file_path = Path(path).expanduser().resolve()
    digest = hashlib.sha256(str(file_path.parent).encode("utf-8")).hexdigest()[:16]
    route = f"/_scpolya_artifacts/{digest}"
    return route, file_path.parent, f"{route}/{file_path.name}"


def manifest_as_igv_options(manifest: IgvManifest | Mapping[str, Any]) -> dict[str, Any]:
    payload = manifest.as_dict() if isinstance(manifest, IgvManifest) else dict(manifest)
    locus = payload.get("locus") or {}
    genome = _igv_genome_id(str(payload.get("genome") or ""))
    return {
        "loadDefaultGenomes": False,
        "genomeList": "/_scpolya_genomes.json",
        # Navbar with locus box and zoom; showControls hides the whole bar when false.
        # Tracks still come from the manifest only.
        "showNavigation": True,
        "showControls": True,
        "showIdeogram": False,
        "showRuler": True,
        "showGenomeWidget": False,
        "showCursorTrackingGuideButton": False,
        "showCenterGuideButton": False,
        "showTrackLabelButton": False,
        "showSVGButton": False,
        "reference": {
            "id": f"local-{genome}",
            "name": genome,
            "fastaURL": payload.get("reference_fasta_url") or "",
            "indexURL": payload.get("reference_index_url") or "",
        },
        "locus": locus.get("igv_locus") or "",
        "tracks": [
            {
                "name": track["name"],
                "url": track["url"],
                "format": track["format"],
                "type": track["type"],
                "scpolyaKey": f"track-{ordinal}",
                "scpolyaCore": is_core_track(track),
                **_igv_track_options(track, index),
            }
            for ordinal, (track, index) in enumerate(_palette_indices(payload["tracks"]))
        ],
    }


def is_core_track(track: Mapping[str, Any]) -> bool:
    """Both coverage strands, source transcripts and final sites, all run slots.

    Historical-v2 BED stays labelled historical; its companion GTF is optional.
    Study variants are retained together so Core cannot silently pick a variant.
    """
    return track.get("role") in {
        "transcript_model", "coverage_scrna", "coverage_polya", "annotated_peak", "variant_peak",
    } or (track.get("role") == "historical_annotation" and track.get("format") == "bed")


def _transcript_scope(track: Mapping[str, Any]) -> dict[str, Any] | None:
    match = re.search(r" · ([^\s:]+):([\d,]+)-([\d,]+)$", str(track.get("name") or ""))
    if match is None:
        return None
    start, end = (int(group.replace(",", "")) for group in match.group(2, 3))
    return {"chr": match.group(1), "start": start, "end": end}


def _palette_indices(tracks: Iterable[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], int]]:
    # The transcript track takes no palette slot, so adding it leaves the
    # colours of the run's own tracks as they were without it.
    out, index = [], 0
    for track in tracks:
        if track.get("role") == "transcript_model":
            out.append((track, 0))
            continue
        out.append((track, index))
        index += 1
    return out


_COORDINATE_LOCUS = re.compile(r"^\s*([^\s:]+):([\d,]+)-([\d,]+)\s*$")


def coordinate_locus(text: str) -> GeneLocus | None:
    """``chr5:142,898,115-142,911,754`` as a locus (1-based, inclusive), else ``None``."""
    match = _COORDINATE_LOCUS.match(str(text or ""))
    if match is None:
        return None
    chrom = match.group(1)
    start, end = (int(group.replace(",", "")) for group in match.group(2, 3))
    if start < 1 or end < start:
        return None
    return GeneLocus(gene=text.strip(), chrom=chrom, start=start, end=end)


def _resolve_locus(root: Path, gtf_path: str, gene: str) -> GeneLocus | None:
    # Coordinates (for example from a locus note) are used as given; names go through the annotation.
    locus = coordinate_locus(gene)
    if locus is not None:
        return locus
    locus = resolve_gene_locus(gtf_path, gene) if gtf_path else None
    return locus or _reference_bed_locus(root, gene)


def _shared_evidence_tracks(root: Path, url_for_file: Callable[[str], str]) -> tuple[IgvTrack, ...]:
    tracks: list[IgvTrack] = []
    tracks.extend(_reference_context_tracks(root, url_for_file))
    tracks.extend(_coverage_tracks(root, url_for_file))
    return _dedupe_tracks(tracks)


def transcript_model_tracks(
    locus: GeneLocus | None,
    gtf_path: str,
    url_for_file: Callable[[str], str],
    cache_dir: Path,
) -> tuple[tuple[IgvTrack, ...], tuple[str, ...]]:
    """Transcript models from the run's own GTF for the initial view window.

    Returns (tracks, notes). The track is a verbatim, window-scoped subset of
    the configured GTF, labelled with the GTF's own provider/version.
    """
    if locus is None:
        return (), ()
    if not gtf_path or not Path(gtf_path).expanduser().is_file():
        return (), ("transcript models: the run's reference GTF is not readable",)
    window = coordinate_locus(locus.igv_locus)
    if window is None:
        return (), ()
    models = scoped_transcript_models(gtf_path, window.chrom, window.start, window.end, cache_dir)
    if models is None:
        return (), ("transcript models: the run's reference GTF is not readable",)
    notes: list[str] = []
    if models.unordered_rows:
        notes.append(f"transcript models: {models.unordered_rows} {models.source_label} rows in "
                     f"{window.chrom}:{window.start}-{window.end} precede their transcript row and are not drawn")
    if not models.transcripts:
        notes.append(f"transcript models: no {models.source_label} transcript overlaps "
                     f"{window.chrom}:{window.start}-{window.end}")
        return (), tuple(notes)
    track = IgvTrack(
        name=f"Transcripts · {models.source_label} · {window.chrom}:{window.start:,}-{window.end:,}",
        path=str(models.path),
        url=url_for_file(str(models.path)),
        format="gtf",
        track_type="annotation",
        role="transcript_model",
    )
    return (track,), tuple(notes)


def with_transcript_models(manifest: IgvManifest, url_for_file: Callable[[str], str],
                           cache_dir: Path) -> IgvManifest:
    """Insert the transcript track directly after the gene-span track."""
    tracks, notes = transcript_model_tracks(manifest.locus, manifest.reference_gtf, url_for_file, cache_dir)
    if not tracks and not notes:
        return manifest
    ordered = list(manifest.tracks)
    at = next((i + 1 for i, t in enumerate(ordered) if t.role == "reference_context"), 0)
    ordered[at:at] = tracks
    return replace(manifest, tracks=tuple(ordered), missing_tracks=(*manifest.missing_tracks, *notes))


def _reference_context_tracks(root: Path, url_for_file: Callable[[str], str]) -> tuple[IgvTrack, ...]:
    specs = [
        ("Gene model", "reference/*gene.sorted.bed", "reference_context"),
        ("Bulk gene range", "reference/*bulk_gene.sorted.bed", "bulk_gene_range"),
        ("Bulk 3UTR range", "reference/*bulk_window.sorted.bed", "bulk_utr_range"),
    ]
    tracks: list[IgvTrack] = []
    for name, pattern, role in specs:
        for path in sorted(root.glob(pattern)):
            if role == "reference_context" and "bulk_" in path.name:
                continue
            tracks.append(_annotation_track(name, path, role, url_for_file, track_type="annotation"))
            break
    return tuple(tracks)


def _coverage_tracks(root: Path, url_for_file: Callable[[str], str]) -> tuple[IgvTrack, ...]:
    specs = [
        ("scRNA coverage +", "cohort_output/cohort.directional/cohort.directional.plus.coverage.removeCDS.bw", "coverage_scrna"),
        ("scRNA coverage -", "cohort_output/cohort.directional/cohort.directional.minus.coverage.removeCDS.negate.bw", "coverage_scrna"),
        ("polyA coverage +", "cohort_output/cohort.directional.polyA-*/*plus.coverage.bw", "coverage_polya"),
        ("polyA coverage -", "cohort_output/cohort.directional.polyA-*/*minus.coverage.negate.bw", "coverage_polya"),
    ]
    tracks: list[IgvTrack] = []
    for name, pattern, role in specs:
        for path in sorted(root.glob(pattern)):
            tracks.append(
                IgvTrack(
                    name=name,
                    path=str(path),
                    url=url_for_file(str(path)),
                    format="bigwig",
                    track_type="wig",
                    role=role,
                )
            )
            break
    return tuple(tracks)


_ANNOTATION_STAGE_ROLES = frozenset({"assigned_peak", "annotated_peak"})


def _stage_audit_peak_tracks(
    root: Path,
    url_for_file: Callable[[str], str],
    *,
    include_annotation: bool = True,
) -> tuple[IgvTrack, ...]:
    specs = [
        ("scRNA called peaks +", "cohort_output/cohort.directional/cohort.directional.plus.coverage.removeCDS.peaks-merged-*.bed", "raw_peak_scrna"),
        ("scRNA called peaks -", "cohort_output/cohort.directional/cohort.directional.minus.coverage.removeCDS.peaks-merged-*.bed", "raw_peak_scrna"),
        ("polyA called peaks +", "cohort_output/cohort.directional.polyA-*/*plus.coverage.peaks-merged-*.bed", "raw_peak_polya"),
        ("polyA called peaks -", "cohort_output/cohort.directional.polyA-*/*minus.coverage.peaks-merged-*.bed", "raw_peak_polya"),
        ("Merged filtered peaks", "final_output/*/*___peaks-mispriming-filtered.bed", "merged_peak"),
        ("Assigned peaks", "final_output/*/*___peaks-assigned.bed", "assigned_peak"),
        ("Final annotated peaks", "final_output/*/*___peaks-annotated.bb", "annotated_peak"),
        ("Final annotated peaks BED", "final_output/*/*___peaks-annotated.bed", "annotated_peak"),
    ]
    tracks: list[IgvTrack] = []
    for name, pattern, role in specs:
        if not include_annotation and role in _ANNOTATION_STAGE_ROLES:
            continue
        for path in sorted(root.glob(pattern)):
            tracks.append(_peak_track(name, path, role, url_for_file))
            break
    return tuple(tracks)


def _projected_stage_tracks(
    config: Mapping[str, Any],
    url_for_file: Callable[[str], str],
) -> _ProjectedTrackDiscovery:
    projection = project_run_outputs_v1(config, annotation_schema="recorded")
    integrity_errors = tuple(
        warning
        for warning in projection.warnings
        if warning.startswith("Physical output namespace could not be projected:")
    )
    if integrity_errors:
        return _ProjectedTrackDiscovery(
            tracks=(),
            fallback_allowed=False,
            integrity_errors=integrity_errors,
        )

    schema_notes = tuple(
        warning for warning in projection.warnings
        if warning.startswith(ANNOTATION_SCHEMA_NOT_OPENED)
    )
    scopes = projection.by_scope_id
    final_scope_ids = {
        "final.filtered_peaks",
        "final.assigned_peaks",
        "final.annotated_peaks_bigbed",
        "final.annotated_peaks_bed",
    }
    tracks: list[IgvTrack] = []
    missing: list[str] = [f"current run: {note}" for note in schema_notes]

    bigwigs = scopes.get("cohort_output.bigwigs")
    if bigwigs is not None:
        tracks.extend(_projected_cohort_tracks(bigwigs.path, config=config, kind="coverage", url_for_file=url_for_file))

    merged_peaks = scopes.get("cohort_output.merged_peaks")
    if merged_peaks is not None:
        tracks.extend(_projected_cohort_tracks(merged_peaks.path, config=config, kind="peaks", url_for_file=url_for_file))

    for role in ("coverage_scrna", "coverage_polya", "raw_peak_scrna", "raw_peak_polya"):
        for strand in ("+", "-"):
            if not any(t.role == role and t.name.endswith(f" {strand}") for t in tracks):
                missing.append(f"current run: {role} {strand} is unavailable for this configuration")

    for scope_id, name, role in (
        ("final.filtered_peaks", "Merged filtered peaks", "merged_peak"),
        ("final.assigned_peaks", "Assigned peaks", "assigned_peak"),
        ("final.annotated_peaks_bigbed", "Final annotated peaks", "annotated_peak"),
        ("final.annotated_peaks_bed", "Final annotated peaks BED", "annotated_peak"),
    ):
        scope = scopes.get(scope_id)
        if scope is None:
            continue
        path = Path(scope.path).expanduser()
        if path.is_file():
            tracks.append(_peak_track(name, path, role, url_for_file))
        elif role != "annotated_peak":
            missing.append(f"current run: {scope_id} is unavailable")

    return _ProjectedTrackDiscovery(
        tracks=_dedupe_tracks(tracks),
        fallback_allowed=not final_scope_ids.issubset(scopes),
        missing_tracks=tuple(missing),
        annotation_schema_refused=bool(schema_notes),
    )


def _projected_cohort_tracks(
    pattern: str,
    *,
    config: Mapping[str, Any],
    kind: str,
    url_for_file: Callable[[str], str],
) -> tuple[IgvTrack, ...]:
    # Output scopes are broad retention globs and can contain several runs'
    # parameter variants. Match the producer's names for this execution rather
    # than choosing whichever threshold or polyA setting sorts first.
    args = SimpleNamespace(**config)
    expected: set[tuple[str, str]] = set()
    for is_poly_a in (False, True):
        scaled = polyA_coverage_scaled(args) if is_poly_a else exp_coverage_scaled(args)
        for strand in ("plus", "minus"):
            item = ScItem(
                is_cohort=True,
                is_polyA=is_poly_a,
                add_polyA=is_poly_a,
                strand=strand,
                scaling_factor=1.0 if scaled else None,
                args=args,
            )
            filename = item.format_name(
                suffix="bw" if kind == "coverage" else "bed",
                is_coverage=True,
                remove_CDS=kind == "peaks" and not is_poly_a,
                is_merged_peak=kind == "peaks",
                add_threshold=kind == "peaks",
                add_merge_dist=kind == "peaks",
            )
            expected.add((item.cohort_name, filename))
    by_category: dict[tuple[bool, str], Path] = {}
    for raw_path in sorted(expand_glob(str(Path(pattern).expanduser()), recursive=True)):
        path = Path(raw_path)
        if not path.is_file():
            continue
        if (path.parent.name, path.name) not in expected:
            continue
        cohort_name = f"{path.parent.name}/{path.name}".lower()
        is_poly_a = "polya" in cohort_name
        strand = "+" if ".plus." in path.name else "-" if ".minus." in path.name else ""
        by_category.setdefault((is_poly_a, strand), path)

    tracks: list[IgvTrack] = []
    for category in ((False, "+"), (False, "-"), (True, "+"), (True, "-"), (False, ""), (True, "")):
        path = by_category.get(category)
        if path is None:
            continue
        is_poly_a, strand = category
        source = "polyA" if is_poly_a else "scRNA"
        suffix = f" {strand}" if strand else ""
        if kind == "coverage":
            tracks.append(
                IgvTrack(
                    name=f"{source} coverage{suffix}",
                    path=str(path),
                    url=url_for_file(str(path)),
                    format="bigwig",
                    track_type="wig",
                    role="coverage_polya" if is_poly_a else "coverage_scrna",
                )
            )
        else:
            tracks.append(
                _peak_track(
                    f"{source} called peaks{suffix}",
                    path,
                    "raw_peak_polya" if is_poly_a else "raw_peak_scrna",
                    url_for_file,
                )
            )
    return tuple(tracks)


def _track_for_variant(
    study: StudyRecord,
    variant: StudyVariantRecord,
    url_for_file: Callable[[str], str],
) -> IgvTrack | None:
    artifact = _existing_peak_artifact(
        {
            "annotated_bigbed": variant.annotated_bigbed,
            "annotated_bed": variant.annotated_bed,
        }
    )
    if not artifact:
        return None
    path = Path(artifact).expanduser()
    if not path.exists():
        return None
    name = variant.label if variant.label.startswith(variant.id) else f"{variant.id} {variant.label}"
    return _peak_track(name, path, "variant_peak", url_for_file, variant_id=variant.id, study_id=study.id)


def _annotation_track(
    name: str,
    path: Path,
    role: str,
    url_for_file: Callable[[str], str],
    *,
    track_type: str = "annotation",
) -> IgvTrack:
    suffix = path.suffix.lower()
    fmt = "bigbed" if suffix == ".bb" else "bed"
    return IgvTrack(
        name=name,
        path=str(path),
        url=url_for_file(str(path)),
        format=fmt,
        track_type=track_type,
        role=role,
    )


def _peak_track(
    name: str,
    path: Path,
    role: str,
    url_for_file: Callable[[str], str],
    *,
    variant_id: str = "",
    study_id: str = "",
) -> IgvTrack:
    suffix = path.suffix.lower()
    fmt = "bigbed" if suffix == ".bb" else "bed"
    return IgvTrack(
        name=name,
        path=str(path),
        url=url_for_file(str(path)),
        format=fmt,
        track_type="annotation",
        role=role,
        variant_id=variant_id,
        study_id=study_id,
    )


def _dedupe_tracks(tracks: Iterable[IgvTrack]) -> tuple[IgvTrack, ...]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[IgvTrack] = []
    for track in tracks:
        key = (track.role, str(Path(track.path).expanduser()), track.variant_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(track)
    return tuple(deduped)


def _locus_focused_on_peak_tracks(locus: GeneLocus | None, tracks: Iterable[IgvTrack]) -> GeneLocus | None:
    if locus is None:
        return None
    starts: list[int] = []
    ends: list[int] = []
    for track in tracks:
        if track.role not in {
            "raw_peak_scrna",
            "raw_peak_polya",
            "merged_peak",
            "assigned_peak",
            "annotated_peak",
            "variant_peak",
        }:
            continue
        for start, end in _bed_intervals_for_locus(_focus_bed_path(Path(track.path)), locus):
            starts.append(start)
            ends.append(end)
    if not starts or not ends:
        return None
    return GeneLocus(
        gene=locus.gene,
        chrom=locus.chrom,
        start=locus.start,
        end=locus.end,
        strand=locus.strand,
        focus_start=min(starts),
        focus_end=max(ends),
    )


def _bed_intervals_for_locus(path: Path, locus: GeneLocus) -> tuple[tuple[int, int], ...]:
    if not path.exists() or path.suffix.lower() != ".bed":
        return ()
    intervals: list[tuple[int, int]] = []
    with path.open("rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line or line.startswith(("#", "track", "browser")):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or parts[0] != locus.chrom:
                continue
            try:
                start = int(parts[1])
                end = int(parts[2])
            except ValueError:
                continue
            if end < locus.start or start > locus.end:
                continue
            intervals.append((start, end))
    return tuple(intervals)


def _focus_bed_path(path: Path) -> Path:
    if path.suffix.lower() == ".bb":
        bed_path = path.with_suffix(".bed")
        if bed_path.exists():
            return bed_path
    return path


def _reference_bed_locus(root: Path, gene: str) -> GeneLocus | None:
    target = gene.lower()
    for pattern in (
        "reference/*gene.sorted.bed",
        "reference/*bulk_gene.sorted.bed",
        "reference/*bulk_window.sorted.bed",
    ):
        for bed_path in sorted(root.glob(pattern)):
            if "bulk_" in bed_path.name and pattern == "reference/*gene.sorted.bed":
                continue
            with bed_path.open("rt", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < 4 or parts[3].lower() != target:
                        continue
                    try:
                        start = int(parts[1]) + 1
                        end = int(parts[2])
                    except ValueError:
                        continue
                    strand = parts[5] if len(parts) > 5 else ""
                    return GeneLocus(gene=gene, chrom=parts[0], start=start, end=end, strand=strand)
    return None


def _existing_peak_artifact(artifacts: Mapping[str, Any]) -> str:
    candidates = [
        str(artifacts.get("annotated_bigbed") or ""),
        str(artifacts.get("annotated_bed") or ""),
    ]
    final_dir = str(artifacts.get("final_dir") or "")
    if final_dir:
        final_path = Path(final_dir).expanduser()
        candidates.extend(str(path) for path in sorted(final_path.glob("*peaks-annotated.bb")))
        candidates.extend(str(path) for path in sorted(final_path.glob("*peaks-annotated.bed")))
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().exists():
            return str(Path(candidate).expanduser())
    return ""


def _reference_gtf_path(base_config: Mapping[str, Any], workspace_root: str) -> str:
    configured = str(base_config.get("gene_gtf") or "")
    if configured and Path(configured).expanduser().exists():
        return str(Path(configured).expanduser())
    root = Path(workspace_root).expanduser()
    for pattern in (
        "reference/*gene*.adjusted.filtered.gtf",
        "reference/*gene*.adjusted.gtf",
        "reference/*adjusted.filtered.gtf",
        "reference/*adjusted.gtf",
        "backup/*.gtf",
    ):
        matches = sorted(root.glob(pattern))
        if matches:
            return str(matches[0])
    return configured


def _igv_track_options(track: Mapping[str, Any], index: int) -> dict[str, Any]:
    role = str(track.get("role") or "")
    fmt = str(track.get("format") or "")
    if fmt == "bigwig":
        return {
            "height": 80,
            "autoscale": True,
            "color": _track_color(index),
        }
    if role == "transcript_model":
        # igv.js joins exon/CDS/UTR rows per transcript_id: thick CDS, thin UTR.
        return {
            "displayMode": "EXPANDED",
            "height": 120,
            "autoHeight": True,
            # Start the first row below the track-name box so it is not covered.
            "margin": 26,
            "nameField": "transcript_name",
            "color": _track_color(index),
            # Read by the page script: the subset only covers this range.
            **({"scpolyaScope": scope} if (scope := _transcript_scope(track)) else {}),
            "visibilityWindow": 5_000_000,
        }
    if role in {"reference_context", "bulk_gene_range", "bulk_utr_range"}:
        return {
            "displayMode": "EXPANDED",
            "height": 46,
            "color": _track_color(index),
            "visibilityWindow": 5_000_000,
        }
    return {
        "displayMode": "EXPANDED",
        "height": 62,
        "visibilityWindow": 1_000_000,
        "color": _track_color(index),
    }


def _igv_genome_id(species: str) -> str:
    lowered = species.lower()
    if lowered == "human":
        return "hg38"
    if lowered == "mouse":
        return "mm10"
    return lowered or "hg38"

def _track_color(index: int) -> str:
    palette = (
        "rgb(0, 121, 107)",
        "rgb(25, 118, 210)",
        "rgb(194, 65, 12)",
        "rgb(123, 31, 162)",
        "rgb(46, 125, 50)",
        "rgb(198, 40, 40)",
        "rgb(73, 80, 87)",
        "rgb(2, 132, 199)",
        "rgb(190, 24, 93)",
        "rgb(101, 163, 13)",
    )
    return palette[index % len(palette)]
