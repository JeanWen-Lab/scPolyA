"""Read-only transcript models for the genome browser.

The run's configured GTF can be a gigabyte; the browser should not download or
parse all of it. For one view window we copy, verbatim, every transcript-level
row (transcript, exon, CDS, UTR, start/stop codon) of transcripts that overlap
the window into a small cached GTF, and let igv.js draw exons with CDS versus
UTR from those rows. Nothing is inferred or synthesised: rows that cannot be
attributed to a transcript_id are skipped and counted. A transcript is selected
from its own 'transcript' row, which GENCODE/Ensembl GTFs place before its exons;
in-window child rows met before any such row are not drawn and are counted as
unordered_rows so the view can say the annotation was incomplete.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

TRANSCRIPT_FEATURES = frozenset({
    "transcript", "exon", "CDS", "UTR", "five_prime_utr", "three_prime_utr", "start_codon", "stop_codon",
})
SCOPE_VERSION = 1
_TRANSCRIPT_ID = re.compile(r'transcript_id "([^"]+)"')


@dataclass(frozen=True)
class ScopedTranscriptModels:
    path: Path
    source_gtf: str
    source_label: str
    chrom: str
    start: int
    end: int
    transcripts: int
    rows: int
    skipped_rows: int
    unordered_rows: int = 0


def gtf_source_label(gtf_path: str | Path) -> str:
    """'GENCODE M23' from the GTF's own header lines; otherwise the file name."""
    provider = version = ""
    with Path(gtf_path).expanduser().open("rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            lowered = line.lower()
            if lowered.startswith("##provider:"):
                provider = line.split(":", 1)[1].strip()
            elif lowered.startswith("##description:"):
                match = re.search(r"\bversion\s+([^\s,;()]+)", line)
                version = match.group(1) if match else version
    if provider and version:
        return f"{provider} {version}"
    return Path(gtf_path).name


def scoped_transcript_models(
    gtf_path: str | Path, chrom: str, start: int, end: int, cache_dir: Path,
) -> ScopedTranscriptModels | None:
    """Transcripts overlapping chrom:start-end (1-based, inclusive), full structure.

    The cache key covers the source file's resolved path, size and mtime, so an
    edited or replaced GTF is never served from a stale subset.
    """
    source = Path(gtf_path).expanduser()
    if not source.is_file() or start < 1 or end < start or not chrom:
        return None
    resolved = source.resolve()
    stat = resolved.stat()
    key = hashlib.sha256(json.dumps(
        [SCOPE_VERSION, str(resolved), stat.st_size, stat.st_mtime_ns, chrom, start, end]
    ).encode()).hexdigest()[:24]
    target = cache_dir / f"transcripts-{key}.gtf"
    meta_path = target.with_suffix(".json")
    if target.is_file() and meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        return ScopedTranscriptModels(path=target, **meta)

    prefix = chrom + "\t"
    selected: set[str] = set()
    seen: set[str] = set()
    unordered = 0
    rows: list[str] = []
    skipped = 0
    with resolved.open("rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith(prefix):
                continue
            parts = line.split("\t", 8)
            if len(parts) < 9 or parts[2] not in TRANSCRIPT_FEATURES:
                continue
            match = _TRANSCRIPT_ID.search(parts[8])
            if match is None:
                skipped += 1
                continue
            transcript_id = match.group(1)
            if parts[2] == "transcript":
                seen.add(transcript_id)
                if int(parts[3]) <= end and int(parts[4]) >= start:
                    selected.add(transcript_id)
                    rows.append(line if line.endswith("\n") else line + "\n")
            elif transcript_id in selected:
                rows.append(line if line.endswith("\n") else line + "\n")
            elif transcript_id not in seen and int(parts[3]) <= end and int(parts[4]) >= start:
                unordered += 1
    label = gtf_source_label(resolved)
    cache_dir.mkdir(parents=True, exist_ok=True)
    header = (f"##description: read-only subset of {resolved} for {chrom}:{start}-{end}; "
              f"rows copied verbatim from transcripts overlapping the window\n")
    _publish(target, header + "".join(rows))
    meta = {"source_gtf": str(resolved), "source_label": label, "chrom": chrom, "start": start, "end": end,
            "transcripts": len(selected), "rows": len(rows), "skipped_rows": skipped, "unordered_rows": unordered}
    # The meta file is the "complete" marker: it is published after the GTF, so
    # a reader that finds it also finds the GTF. Concurrent builders of the same
    # key write identical bytes, each through its own temporary file.
    _publish(meta_path, json.dumps(meta, indent=1) + "\n")
    return ScopedTranscriptModels(path=target, **meta)


def _publish(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
