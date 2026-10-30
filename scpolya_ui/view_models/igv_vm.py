from __future__ import annotations

from dataclasses import dataclass

from scpolya_ui.igv_manifest import IgvManifest, IgvTrack


@dataclass(frozen=True)
class IgvTrackSummary:
    name: str
    role: str
    ready: bool
    format: str = ""
    path: str = ""


@dataclass(frozen=True)
class IgvManifestSummary:
    gene: str
    locus: str
    track_count: int
    missing_count: int
    tracks: tuple[IgvTrackSummary, ...]


def igv_track_summary(track: IgvTrack) -> IgvTrackSummary:
    return IgvTrackSummary(
        name=track.name,
        role=track.role,
        ready=bool(track.url),
        format=track.format,
        path=track.path,
    )


def igv_manifest_summary(manifest: IgvManifest) -> IgvManifestSummary:
    locus = manifest.locus.igv_locus if manifest.locus else ""
    tracks = tuple(igv_track_summary(track) for track in manifest.tracks)
    return IgvManifestSummary(
        gene=manifest.gene,
        locus=locus,
        track_count=len(tracks),
        missing_count=len(manifest.missing_tracks),
        tracks=tracks,
    )
