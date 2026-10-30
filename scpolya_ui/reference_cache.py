from __future__ import annotations

import gzip
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .registry import ui_home


UCSC_MM10_FASTA_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/mm10/bigZips/mm10.fa.gz"


@dataclass(frozen=True)
class ReferenceFasta:
    genome: str
    fasta_path: Path
    index_path: Path
    source: str


def genome_id_from_config(config: Mapping[str, Any]) -> str:
    species = str(config.get("species") or "").strip().lower()
    if species == "mouse":
        return "mm10"
    if species == "human":
        return "hg38"
    return species or "hg38"


def cached_reference_dir(home: Path | None = None) -> Path:
    return (home or ui_home()) / "igv_refs"


def cached_fasta_path(genome: str, home: Path | None = None) -> Path:
    return cached_reference_dir(home) / genome / f"{genome}.fa"


def find_reference_fasta(
    config: Mapping[str, Any],
    workspace_root: str,
    *,
    home: Path | None = None,
    create_index: bool = True,
) -> ReferenceFasta | None:
    genome = genome_id_from_config(config)
    candidates: list[tuple[str, Path]] = []
    for key in ("fasta", "reference_fasta", "genome_fasta", "ref_fasta"):
        value = config.get(key)
        if value:
            candidates.append((key, Path(str(value)).expanduser()))

    root = Path(workspace_root).expanduser()
    candidates.extend(("workspace", path) for path in sorted((root / "reference").glob("*.fa")))
    candidates.extend(("workspace", path) for path in sorted((root / "reference").glob("*.fasta")))
    candidates.append(("ui-cache", cached_fasta_path(genome, home)))

    for source, fasta_path in candidates:
        if not fasta_path.exists() or not fasta_path.is_file() or fasta_path.stat().st_size == 0:
            continue
        index_path = fasta_path.with_suffix(fasta_path.suffix + ".fai")
        if create_index:
            index_path = ensure_fai(fasta_path)
        elif not index_path.is_file() or index_path.stat().st_size == 0:
            return None
        return ReferenceFasta(genome=genome, fasta_path=fasta_path, index_path=index_path, source=source)
    return None


def prepare_cached_reference_fasta(
    genome: str,
    *,
    home: Path | None = None,
    url: str | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> ReferenceFasta:
    if genome != "mm10":
        raise ValueError(f"No built-in FASTA download is configured for {genome!r}")
    target_url = url or UCSC_MM10_FASTA_URL
    cache_dir = cached_reference_dir(home) / genome
    cache_dir.mkdir(parents=True, exist_ok=True)
    fasta_path = cache_dir / f"{genome}.fa"
    gz_path = cache_dir / f"{genome}.fa.gz"

    if not fasta_path.exists() or fasta_path.stat().st_size == 0:
        _download_once(target_url, gz_path, progress=progress)
        _gunzip_once(gz_path, fasta_path, progress=progress)
    index_path = ensure_fai(fasta_path, progress=progress)
    if progress:
        progress({"status": "ready", "fasta": str(fasta_path), "index": str(index_path)})
    return ReferenceFasta(genome=genome, fasta_path=fasta_path, index_path=index_path, source="ui-cache")


def ensure_fai(
    fasta_path: str | Path,
    *,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    path = Path(fasta_path).expanduser()
    index_path = path.with_suffix(path.suffix + ".fai")
    if index_path.exists() and index_path.stat().st_mtime >= path.stat().st_mtime:
        return index_path

    rows: list[str] = []
    with path.open("rb") as handle:
        name = ""
        length = 0
        seq_offset = 0
        line_bases = 0
        line_width = 0
        while True:
            line_start = handle.tell()
            line = handle.readline()
            if not line:
                break
            if line.startswith(b">"):
                if name:
                    rows.append(f"{name}\t{length}\t{seq_offset}\t{line_bases}\t{line_width}")
                name = line[1:].strip().split()[0].decode("utf-8", errors="replace")
                length = 0
                seq_offset = handle.tell()
                line_bases = 0
                line_width = 0
                continue
            stripped = line.rstrip(b"\r\n")
            if not stripped:
                continue
            if line_bases == 0:
                line_bases = len(stripped)
                line_width = len(line)
            length += len(stripped)
            if progress and length and length % 100_000_000 < max(1, line_bases):
                progress({"status": "indexing", "sequence": name, "bytes_read": line_start})
        if name:
            rows.append(f"{name}\t{length}\t{seq_offset}\t{line_bases}\t{line_width}")
    index_path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return index_path


def _download_once(
    url: str,
    target: Path,
    *,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    if target.exists() and target.stat().st_size > 0:
        return
    tmp = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "scpolya-ui/1.0"})
    downloaded = 0
    with urllib.request.urlopen(request) as response, tmp.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            downloaded += len(chunk)
            if progress:
                progress({"status": "downloading", "downloaded": downloaded, "total": total, "url": url})
    tmp.replace(target)


def _gunzip_once(
    source: Path,
    target: Path,
    *,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    if target.exists() and target.stat().st_size > 0:
        return
    tmp = target.with_suffix(target.suffix + ".part")
    with gzip.open(source, "rb") as src, tmp.open("wb") as dst:
        copied = 0
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            copied += len(chunk)
            if progress:
                progress({"status": "decompressing", "bytes_written": copied})
    tmp.replace(target)
