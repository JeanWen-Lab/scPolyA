"""Bounded historical-v2 display; never upgrades native output readiness."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Iterable

from task_manage import project_run_outputs_v1
from task_manage.artifact_naming import load_artifact_name_manifest_v1, annotation_name_payload
from scpolya_ui.igv_manifest import IgvTrack
from scpolya_ui.registry import PathMapping
from scpolya_ui.run_data import discover_run_log_catalog
from scpolya_ui.run_records import RunRecord, resolve_execution_config


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def historical_v2_overlay(
    record: RunRecord, *, view_config: dict[str, Any], view_root: str,
    path_mappings: Iterable[PathMapping], url_for_file: Callable[[str], str],
) -> tuple[tuple[IgvTrack, ...], dict[str, Any]]:
    """Require one succeeded invocation and one exact stored namespace match.

    This associates the imported files with stored metadata; it does not claim
    an immutable execution-time artifact snapshot or current-engine health.
    """
    if record.history_source != 'invocation' or record.status != 'succeeded' or not record.invocation_id:
        return (), {}
    if view_config.get('quantification_mode') != 'terminal-aware':
        return (), {}
    root = Path(view_root)
    stored_path = root / 'state' / 'artifact_name_manifest.v1.json'
    try:
        stored = load_artifact_name_manifest_v1(stored_path)
        if stored is None or stored.mode != 'short':
            return (), {}
        projection = project_run_outputs_v1(view_config)
        if projection.warnings:
            return (), {'unavailable': 'Native output namespace could not be validated'}
        peak_scope = projection.by_scope_id.get('final.filtered_peaks')
        peaks = [p for p in (peak_scope.artifact_names if peak_scope else ()) if p.scope == 'peak']
        if len(peaks) != 1:
            return (), {'unavailable': 'Unique current peak identity is unavailable'}
        peak = peaks[0]
        peak_records = [r for r in stored.records if r.scope == 'peak' and r.code == peak.short_code and r.payload == peak.payload]
        expected_annotation = annotation_name_payload(
            peak.payload, annotate_peak_source=view_config.get('annotate_peak_source') or 'resolved',
            terminal_annotation_schema='terminal-annotation-v2',
        )
        annotations = [r for r in stored.records if r.scope == 'annotation' and r.payload == expected_annotation]
        if not annotations:
            return (), {}
        if len(peak_records) != 1 or len(annotations) != 1:
            return (), {'unavailable': 'Ambiguous historical annotation namespace binding'}
        completed = [i for i in discover_run_log_catalog(root, path_mappings=tuple(path_mappings)).invocations
                     if i.status == 'succeeded' and i.config_hash == record.invocation_config_hash]
        original_config = resolve_execution_config(record)
        if (len(completed) != 1 or completed[0].invocation_id != record.invocation_id
                or not original_config.available or completed[0].resolved_config != original_config.config):
            return (), {'unavailable': 'Historical overlay needs one exact succeeded invocation/config binding'}
        annotation = annotations[0]
        parent = root / 'final_output' / peak.short_code / annotation.code
        files = [(fmt, parent / f'{peak.short_code}.___peaks-annotated.terminal-aware.v2.{fmt}') for fmt in ('bed', 'gtf')]
        if any(not p.is_file() or p.stat().st_size == 0 or p.stat().st_size > 32 * 1024**2 for _, p in files):
            return (), {'unavailable': 'Historical BED/GTF pair is missing, empty, or exceeds the bounded view size'}
        pins = [{'format': fmt, 'path': str(p), 'canonical_path': str(p.resolve()),
                 'bytes': p.stat().st_size, 'sha256': _sha256(p)} for fmt, p in files]
        tracks = tuple(IgvTrack(
            name=f'Historical terminal-annotation-v2 · {fmt.upper()}', path=str(p), url=url_for_file(str(p)),
            format=fmt, track_type='annotation', role='historical_annotation',
            run_record_id=record.id, invocation_id=record.invocation_id,
        ) for fmt, p in files)
        return tracks, {
            'classification': 'separately labelled imported historical evidence; not current native output',
            'terminal_annotation_schema': 'terminal-annotation-v2',
            'run_record_id': record.id, 'invocation_id': record.invocation_id,
            'original_root_path': record.root_path,
            'invocation_config_hash': record.invocation_config_hash,
            'invocation_metadata_sha256': _sha256(Path(completed[0].metadata_path)),
            'namespace_manifest_sha256': _sha256(stored_path),
            'peak_code': peak.short_code, 'annotation_code': annotation.code,
            'peak_full_digest': peak_records[0].full_digest, 'annotation_full_digest': annotation.full_digest,
            'files': pins,
            'limits': 'Stored file/config association only; no immutable execution-time snapshot, current-engine health or scientific-scale certification.',
        }
    except (ValueError, OSError) as exc:
        return (), {'unavailable': f'Historical overlay binding refused: {type(exc).__name__}: {exc}'}
