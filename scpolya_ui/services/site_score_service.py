"""Read-only discovery and paging of per-site score annotations.

Scorers annotate called PAS sites; they never change counts, filtering or the
native annotation. DeepCPS is the only scorer today. Its stage writes
``<annotated BED parent>/deepcps/<request sha256>/`` with ``provenance.json``,
``scored.tsv`` and ``sites.deepcps.tsv``. This module only reads those files.
It verifies their digests against the stage's own provenance but is not the
sealed agent reader (``deepcps-result``) and does not replace it.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from scpolya_ui.registry import PathMapping, apply_path_mappings_to_config

DEEPCPS_PROVENANCE_SCHEMA = "scpolya.deepcps.provenance.v1"
MAX_TABLE_BYTES = 64 * 1024 * 1024
MAX_PROVENANCE_BYTES = 1024 * 1024
MAX_RESULT_DIRS = 64
MAX_PAGE_ROWS = 500
HISTOGRAM_BINS = 20


@dataclass(frozen=True)
class ScoreResult:
    scorer: str
    directory: str
    fingerprint: str
    n_sites: int | None
    n_scored: int | None
    reasons: dict[str, int]
    integrity: str  # "verified" | "mismatch" | "unreadable"
    integrity_detail: str = ""
    bed_matches: bool | None = None
    modified_at: float = 0.0
    threads: int | None = None
    batch_size: int | None = None
    model_id: str = ""
    model_status: str = ""
    reference_fasta: str = ""

    @property
    def sites_path(self) -> str:
        return str(Path(self.directory) / "sites.deepcps.tsv")

    @property
    def readable(self) -> bool:
        return self.integrity == "verified"


@dataclass(frozen=True)
class SiteScoreRow:
    row: int
    name: str
    gene: str
    feature: str
    chrom: str
    site: int | None
    strand: str
    calibrated: float | None
    raw: float | None
    seeds: tuple[float | None, ...]
    locate_offset: int | None
    locate_site: int | None
    locate_confidence: float | None
    locate_reason: str
    score_reason: str
    tail_reads: str = ""
    total_reads: str = ""


@dataclass(frozen=True)
class SiteScorePage:
    rows: tuple[SiteScoreRow, ...]
    total: int
    offset: int
    histogram: tuple[int, ...] = ()
    unscored: int = 0
    genes: tuple[str, ...] = field(default_factory=tuple)


def scored_config(config: Mapping[str, Any], mappings: Iterable[PathMapping] = ()) -> dict[str, Any]:
    mappings = tuple(mappings)
    return apply_path_mappings_to_config(dict(config), mappings) if mappings else dict(config)


def deepcps_requested(config: Mapping[str, Any]) -> bool:
    return bool(config.get("deepcps"))


def deepcps_result_root(config: Mapping[str, Any]) -> Path | None:
    """Directory that holds one subdirectory per DeepCPS request identity."""
    from task_manage.output_projection import project_run_outputs_v1

    try:
        projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    except Exception:
        return None
    by_id = {scope.scope_id: scope for scope in projection.scopes}
    deepcps = by_id.get("final.deepcps")
    if deepcps is not None:
        # The projected glob is ``.../deepcps/*/*``.
        return Path(deepcps.path).parent.parent
    annotated = by_id.get("final.annotated_peaks_bed")
    if annotated is not None:
        return Path(annotated.path).parent / "deepcps"
    legacy = _legacy_annotated_bed(config, projection)
    return legacy.parent / "deepcps" if legacy is not None else None


def empty_state(config: Mapping[str, Any]) -> str:
    """Which empty state a source without score results shows.

    ``"unsupported"``: its annotation format cannot be opened here;
    ``"missing"``: DeepCPS was on but nothing was found; ``"off"``: not scored.
    """
    if annotation_format_refusal(config):
        return "unsupported"
    return "missing" if deepcps_requested(config) else "off"


def annotation_format_refusal(config: Mapping[str, Any]) -> str:
    """Why this run's annotation outputs cannot be opened here, or ``""``.

    Non-empty only when the recorded annotation format is refused and the
    site-score reader cannot locate its annotated BED either (unknown, mixed or
    unreadable records).
    """
    from task_manage.output_projection import (
        ANNOTATION_SCHEMA_NOT_OPENED,
        project_run_outputs_v1,
        recorded_annotated_peaks_bed_v1,
    )

    try:
        projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    except Exception:
        return ""
    refused = [w for w in projection.warnings if w.startswith(ANNOTATION_SCHEMA_NOT_OPENED)]
    if not refused:
        return ""
    try:
        located = recorded_annotated_peaks_bed_v1(dict(config))
    except Exception:
        return refused[0]
    return "" if located.path is not None else (located.refusal or refused[0])


def discover_deepcps_results(
    config: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> tuple[ScoreResult, ...]:
    result_root = root if root is not None else deepcps_result_root(config)
    if result_root is None or not result_root.is_dir():
        return ()
    annotated_bed = _annotated_bed_path(config)
    results = []
    for index, child in enumerate(sorted(result_root.iterdir())):
        if index >= MAX_RESULT_DIRS:
            break
        if child.is_dir() and (child / "provenance.json").is_file():
            results.append(_read_result(child, annotated_bed))
    return tuple(sorted(results, key=lambda item: item.modified_at, reverse=True))


def read_site_scores(
    result: ScoreResult,
    *,
    offset: int = 0,
    limit: int = 50,
    query: str = "",
    scored_only: bool = False,
) -> SiteScorePage:
    if not result.readable:
        raise ValueError("Score result failed its provenance check and is not read")
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), MAX_PAGE_ROWS))
    rows = _load_rows(Path(result.sites_path))
    histogram = [0] * HISTOGRAM_BINS
    unscored = 0
    for row in rows:
        if row.calibrated is None:
            unscored += 1
            continue
        index = min(int(row.calibrated * HISTOGRAM_BINS), HISTOGRAM_BINS - 1)
        histogram[max(index, 0)] += 1
    needle = query.strip().casefold()
    selected = [
        row
        for row in rows
        if (not needle or needle in row.gene.casefold() or needle in row.name.casefold())
        and (not scored_only or row.calibrated is not None)
    ]
    # Isotonic calibration saturates; the raw seed mean breaks its ties.
    selected.sort(
        key=lambda row: (row.calibrated is None, -(row.calibrated or 0.0), -(row.raw or 0.0), row.row)
    )
    genes = tuple(sorted({row.gene for row in rows if row.gene}))
    return SiteScorePage(
        rows=tuple(selected[offset : offset + limit]),
        total=len(selected),
        offset=offset,
        histogram=tuple(histogram),
        unscored=unscored,
        genes=genes,
    )


# ---------- internals ----------
def _annotated_bed_path(config: Mapping[str, Any]) -> Path | None:
    from task_manage.output_projection import project_run_outputs_v1

    try:
        projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    except Exception:
        return None
    for scope in projection.scopes:
        if scope.scope_id == "final.annotated_peaks_bed":
            return Path(scope.path)
    return _legacy_annotated_bed(config, projection)


def _legacy_annotated_bed(config: Mapping[str, Any], projection: Any) -> Path | None:
    """Annotated BED of a refused-but-locatable recorded schema (e.g. v2).

    Only consulted when the shared projection omitted annotation outputs; the
    projection itself and every other reader keep refusing that schema.
    """
    from task_manage.output_projection import (
        ANNOTATION_SCHEMA_NOT_OPENED,
        SITE_SCORE_LOCATABLE_ANNOTATION_SCHEMAS,
        recorded_annotated_peaks_bed_v1,
    )

    if not any(w.startswith(ANNOTATION_SCHEMA_NOT_OPENED) for w in projection.warnings):
        return None
    try:
        located = recorded_annotated_peaks_bed_v1(dict(config))
    except Exception:
        return None
    if located.path is None or located.schema not in SITE_SCORE_LOCATABLE_ANNOTATION_SCHEMAS:
        return None
    return located.path


def _read_result(directory: Path, annotated_bed: Path | None) -> ScoreResult:
    provenance_path = directory / "provenance.json"
    modified = provenance_path.stat().st_mtime
    base = dict(scorer="deepcps", directory=str(directory), modified_at=modified)
    if provenance_path.stat().st_size > MAX_PROVENANCE_BYTES:
        return ScoreResult(
            fingerprint=directory.name, n_sites=None, n_scored=None, reasons={},
            integrity="unreadable", integrity_detail="provenance.json is larger than 1 MiB", **base,
        )
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ScoreResult(
            fingerprint=directory.name, n_sites=None, n_scored=None, reasons={},
            integrity="unreadable", integrity_detail=f"provenance.json: {exc}", **base,
        )
    request = provenance.get("request") if isinstance(provenance.get("request"), Mapping) else {}
    reference = request.get("reference") if isinstance(request.get("reference"), Mapping) else {}
    fasta = reference.get("fasta") if isinstance(reference.get("fasta"), Mapping) else {}
    model = request.get("model") if isinstance(request.get("model"), Mapping) else {}
    manifest = model.get("manifest") if isinstance(model.get("manifest"), Mapping) else {}
    details = dict(
        fingerprint=str(provenance.get("fingerprint") or directory.name),
        n_sites=_int_or_none(provenance.get("n_sites")),
        n_scored=_int_or_none(provenance.get("n_scored")),
        reasons={str(key): int(value) for key, value in (provenance.get("reasons") or {}).items()},
        threads=_int_or_none(request.get("threads")),
        batch_size=_int_or_none(request.get("batch_size")),
        model_id=str(manifest.get("model_id") or ""),
        model_status=str(manifest.get("status") or ""),
        reference_fasta=str(fasta.get("path") or ""),
        bed_matches=_bed_matches(request, annotated_bed),
    )
    if provenance.get("schema") != DEEPCPS_PROVENANCE_SCHEMA:
        return ScoreResult(
            integrity="unreadable",
            integrity_detail=f"unexpected provenance schema {provenance.get('schema')!r}",
            **details, **base,
        )
    problem = _verify_outputs(directory, provenance.get("outputs"))
    return ScoreResult(
        integrity="mismatch" if problem else "verified",
        integrity_detail=problem,
        **details, **base,
    )


def _verify_outputs(directory: Path, outputs: Any) -> str:
    if not isinstance(outputs, Mapping):
        return "provenance lists no outputs"
    for name in ("scored.tsv", "sites.deepcps.tsv"):
        expected = outputs.get(name)
        path = directory / name
        if not isinstance(expected, Mapping) or not expected.get("sha256"):
            return f"provenance has no digest for {name}"
        if not path.is_file():
            return f"{name} is missing"
        size = path.stat().st_size
        if size > MAX_TABLE_BYTES:
            return f"{name} is larger than 64 MiB"
        if expected.get("size") is not None and int(expected["size"]) != size:
            return f"{name} size differs from provenance"
        if _sha256(path) != expected["sha256"]:
            return f"{name} digest differs from provenance"
    return ""


def _bed_matches(request: Mapping[str, Any], annotated_bed: Path | None) -> bool | None:
    bed = request.get("bed") if isinstance(request.get("bed"), Mapping) else {}
    expected = bed.get("sha256")
    if not expected or annotated_bed is None or not annotated_bed.is_file():
        return None
    if annotated_bed.stat().st_size > MAX_TABLE_BYTES:
        return None
    return _sha256(annotated_bed) == expected


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_rows(path: Path) -> list[SiteScoreRow]:
    if path.stat().st_size > MAX_TABLE_BYTES:
        raise ValueError("sites.deepcps.tsv is larger than 64 MiB")
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for index, item in enumerate(csv.DictReader(handle, delimiter="\t"), start=1):
            rows.append(
                SiteScoreRow(
                    row=_int_or_none(item.get("bed_row_1based")) or index,
                    name=item.get("name", ""),
                    gene=item.get("gene", ""),
                    feature=item.get("feature", ""),
                    chrom=item.get("chrom", ""),
                    site=_int_or_none(item.get("site")),
                    strand=item.get("strand", ""),
                    calibrated=_float_or_none(item.get("deepcps_cal")),
                    raw=_float_or_none(item.get("deepcps_raw")),
                    seeds=tuple(_float_or_none(item.get(f"deepcps_s{seed}")) for seed in (1, 2, 3)),
                    locate_offset=_int_or_none(item.get("locate_offset")),
                    locate_site=_int_or_none(item.get("locate_site")),
                    locate_confidence=_float_or_none(item.get("locate_conf")),
                    locate_reason=item.get("locate_reason", ""),
                    score_reason=item.get("score_reason", ""),
                    tail_reads=item.get("tail_reads", ""),
                    total_reads=item.get("total_reads", ""),
                )
            )
    return rows


def _float_or_none(value: Any) -> float | None:
    if value is None or str(value).strip() in {"", "NA", "nan", "NaN"}:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _int_or_none(value: Any) -> int | None:
    number = _float_or_none(value)
    return int(number) if number is not None and float(number).is_integer() else None
