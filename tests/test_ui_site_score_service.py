from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scpolya_ui.services.site_score_service import (
    DEEPCPS_PROVENANCE_SCHEMA,
    discover_deepcps_results,
    read_site_scores,
)

HEADER = [
    "chrom", "start", "end", "name", "max_cov", "strand", "gene", "feature",
    "site_key", "bed_row_1based", "site", "deepcps_s1", "deepcps_s2", "deepcps_s3",
    "deepcps_raw", "deepcps_cal", "locate_offset", "locate_site", "locate_conf",
    "locate_reason", "score_reason",
]


def _row(name, gene, row, cal, reason="scored"):
    values = {
        "chrom": "chr1", "start": "100", "end": "200", "name": name, "max_cov": "1",
        "strand": "+", "gene": gene, "feature": "Terminal_exon", "site_key": name,
        "bed_row_1based": str(row), "site": "200", "deepcps_s1": cal, "deepcps_s2": cal,
        "deepcps_s3": cal, "deepcps_raw": cal, "deepcps_cal": cal,
        "locate_offset": "NA" if cal == "NA" else "1",
        "locate_site": "NA" if cal == "NA" else "201",
        "locate_conf": "NA" if cal == "NA" else "0.5",
        "locate_reason": "located" if cal != "NA" else reason,
        "score_reason": reason,
    }
    return "\t".join(values[key] for key in HEADER)


def _write_result(root: Path, rows: list[str], *, tamper: bool = False) -> Path:
    directory = root / ("a" * 64)
    directory.mkdir(parents=True)
    sites = directory / "sites.deepcps.tsv"
    sites.write_text("\t".join(HEADER) + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    scored = directory / "scored.tsv"
    scored.write_text("key\n", encoding="utf-8")
    outputs = {
        name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
        for name, path in (("sites.deepcps.tsv", sites), ("scored.tsv", scored))
    }
    provenance = {
        "schema": DEEPCPS_PROVENANCE_SCHEMA,
        "fingerprint": "a" * 64,
        "n_sites": len(rows),
        "n_scored": sum(1 for row in rows if "\tscored" in row),
        "reasons": {"scored": 2, "coverage_file_missing": 1},
        "request": {
            "threads": 2,
            "batch_size": 128,
            "model": {"manifest": {"model_id": "candidate", "status": "supplied-handoff-candidate-unconfirmed"}},
        },
        "outputs": outputs,
    }
    (directory / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    if tamper:
        with sites.open("a", encoding="utf-8") as handle:
            handle.write(_row("extra", "X", 9, "0.1") + "\n")
    return directory


def test_discover_and_read_sorted_page(tmp_path):
    rows = [_row("A_peak_1", "A", 1, "0.2"), _row("B_peak_2", "B", 2, "0.9"),
            _row("C_peak_3", "C", 3, "NA", reason="coverage_file_missing")]
    _write_result(tmp_path / "deepcps", rows)
    (result,) = discover_deepcps_results({}, root=tmp_path / "deepcps")
    assert result.readable and result.n_sites == 3 and result.model_id == "candidate"
    page = read_site_scores(result, limit=10)
    assert [row.name for row in page.rows] == ["B_peak_2", "A_peak_1", "C_peak_3"]
    assert page.unscored == 1 and sum(page.histogram) == 2
    assert page.rows[2].calibrated is None and page.rows[2].score_reason == "coverage_file_missing"
    assert read_site_scores(result, query="b").total == 1
    assert read_site_scores(result, scored_only=True).total == 2


def test_tampered_table_is_not_read(tmp_path):
    _write_result(tmp_path / "deepcps", [_row("A_peak_1", "A", 1, "0.2")], tamper=True)
    (result,) = discover_deepcps_results({}, root=tmp_path / "deepcps")
    assert result.integrity == "mismatch"
    with pytest.raises(ValueError):
        read_site_scores(result)


def test_missing_root_returns_nothing(tmp_path):
    assert discover_deepcps_results({}, root=tmp_path / "absent") == ()
