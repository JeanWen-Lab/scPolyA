from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scpolya_ui.reference_cache import cached_fasta_path, cached_reference_dir, find_reference_fasta, genome_id_from_config


@dataclass(frozen=True)
class ReferenceStatus:
    genome: str
    status: str
    fasta_path: str
    index_path: str
    source: str
    cache_dir: str
    expected_cache_path: str


def reference_status(
    config: Mapping[str, Any],
    workspace_root: str,
    *,
    home: str | Path | None = None,
) -> ReferenceStatus:
    home_path = Path(home).expanduser() if home is not None else None
    genome = genome_id_from_config(config)
    reference = find_reference_fasta(config, workspace_root, home=home_path)
    if reference is None:
        return ReferenceStatus(
            genome=genome,
            status="missing",
            fasta_path="",
            index_path="",
            source="",
            cache_dir=str(cached_reference_dir(home_path)),
            expected_cache_path=str(cached_fasta_path(genome, home_path)),
        )
    return ReferenceStatus(
        genome=reference.genome,
        status="ready",
        fasta_path=str(reference.fasta_path),
        index_path=str(reference.index_path),
        source=reference.source,
        cache_dir=str(cached_reference_dir(home_path)),
        expected_cache_path=str(cached_fasta_path(reference.genome, home_path)),
    )
