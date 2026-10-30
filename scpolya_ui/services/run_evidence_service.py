"""Bounded queries of a new snapshot of retained native tables.

An invocation config is an association, never an execution-time artifact seal.
Every query pins the current file, copies it into a fresh attempt and queries
that copy. No BAM processing, statistical inference or native state mutation.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from scpolya_ui.registry import PathMapping, apply_path_mappings_to_config, ui_home, utc_now
from scpolya_ui.run_data import discover_run_log_catalog
from scpolya_ui.run_records import RunRecord, resolve_execution_config

MAX_SOURCE_BYTES = 128 * 1024**2
MAX_EXPANDED_BYTES = 2 * 1024**3
MAX_SCAN_ROWS = 15_000_000
MAX_RESULT_ROWS = 500
WALL_SECONDS = 120
TABLES = {
    "distal": ("terminal_aware_distal_site_usage.v1.tsv.gz", ("gene_id", "strand", "cell_barcode", "distal_site_usage")),
    "gene_cell": ("terminal_aware_gene_cell.v2.tsv.gz", ("gene_id", "strand", "cell_barcode", "gene_umi_count")),
    "psi": ("terminal_aware_psi.v2.tsv.gz", ("peak_name", "gene_id", "strand", "cell_barcode", "psi")),
    "weighted_length": ("terminal_aware_weighted_three_prime_utr_length.v2.tsv.gz", ("gene_id", "strand", "cell_barcode")),
    "catalogue": ("terminal_aware_peak_to_gene.v2.tsv.gz", ("peak_name", "gene_id", "strand", "chrom", "start", "end")),
    "ipui_gene": ("intronic_ipui_gene_total.v2.tsv.gz", ("gene_id", "strand", "quantification_unit_id", "I_total", "U", "total_iPUI")),
    "ipui_site": ("intronic_ipui_per_site.v2.tsv.gz", ("intronic_pas_id", "gene_id", "strand", "quantification_unit_id", "I_i", "U", "iPUI_i")),
    "cell_gene_matrix": ("terminal_aware_cell_gene.matrix.v2.tsv.gz", ("cell_barcode",)),
    "counts": ("counts.terminal-aware.v2.tsv.gz", ("peak_name", "cell_barcode", "umi_count")),
}
LIMITS = (
    "Rows are an exact filtered extract of native tables; NA and zero are preserved. "
    "Distal usage is D/T; tied distal PAS follow the native producer. "
    "A catalogue or count increase alone does not establish usable terminal APA. "
    "This snapshot was captured now. An associated invocation/config does not prove "
    "that shared files are unchanged since that execution."
)


class EvidenceError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def require(condition: bool, message: str, code: str = "invalid_input") -> None:
    if not condition:
        raise EvidenceError(code, message)


def emit(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024**2), b""):
            h.update(block)
    return h.hexdigest()


def pin(path: Path, *, maximum: int = MAX_SOURCE_BYTES) -> dict[str, Any]:
    require(path.is_file(), f"Source is missing: {path}", "source_missing")
    before = path.stat()
    require(before.st_size <= maximum, f"Source exceeds {maximum} bytes: {path}", "budget")
    result = {"path": str(path), "canonical_path": str(path.resolve()), "bytes": before.st_size,
              "sha256": digest(path)}
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "Source changed while hashing; preview again", "source_changed")
    return result


def verify(item: dict[str, Any]) -> None:
    require(pin(Path(item["path"])) == item, f"Source changed; preview again: {item['path']}", "source_changed")


@dataclass(frozen=True)
class TableSource:
    key: str
    path: str
    size_bytes: int
    columns: tuple[str, ...]


def header(path: Path) -> tuple[str, ...]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        line = handle.readline(262145)
    require(len(line) <= 262144, "Table header exceeds the display budget")
    fields = tuple(line.rstrip("\r\n").split("\t"))
    require(bool(fields[0]) and len(fields) == len(set(fields)), "Missing or duplicate table columns")
    return fields


def discover(record: RunRecord, mappings: Iterable[PathMapping] = ()) -> tuple[list[TableSource], dict[str, Any]]:
    """Find current tables only in the stored config's projected namespaces."""
    from task_manage import project_run_outputs_v1

    mappings = tuple(mappings)
    require(record.status == "succeeded", "Select a completed successful execution")
    resolution = resolve_execution_config(record)
    require(resolution.available, resolution.error)
    original = dict(resolution.config)
    config = apply_path_mappings_to_config(original, tuple(mappings))
    require(config.get("quantification_mode") == "terminal-aware", "This reader requires terminal-aware tables; select a terminal-aware run")
    projection = project_run_outputs_v1(config, annotation_schema="recorded")
    require(not projection.warnings, "; ".join(projection.warnings))
    require(bool(projection.run_root), "Run root is unavailable")
    root = Path(str(projection.run_root)).resolve()
    catalogue = discover_run_log_catalog(root, path_mappings=tuple(mappings))
    require(bool(catalogue.invocations), "A structured native invocation is required; register its output root in Workspaces")
    latest = catalogue.invocations[0]
    require(latest.status == "succeeded", "The latest execution has not succeeded; wait for completion or select another root")
    require(latest.resolved_config == original, "Stored config differs from the latest invocation; refresh Runs and select that attempt")
    require(not record.invocation_id or latest.invocation_id == record.invocation_id, "This is an older attempt sharing current outputs; select the latest attempt")
    require(bool(record.invocation_id) or (bool(record.job_id) and latest.job_id == record.job_id),
            "The selected job is not bound to the latest native invocation; register its output root and select that execution")
    binding = {
        "classification": "new_snapshot_of_current_retained_files",
        "run_record_id": record.id, "run_id": record.run_id,
        "workspace_id": record.workspace_id, "original_root": record.root_path,
        "view_root": str(root), "config": original, "config_hash": resolution.config_hash,
        "invocation_id": latest.invocation_id,
        "invocation": pin(Path(latest.metadata_path), maximum=2 * 1024**2),
        "path_mappings": [asdict(m) for m in mappings],
        "limits": LIMITS,
    }
    manifest = root / "state" / "artifact_name_manifest.v1.json"
    if manifest.is_file():
        binding["namespace_manifest"] = pin(manifest, maximum=8 * 1024**2)
    sources = []
    for scope_id in ("matrix_summary", "count_matrix"):
        scope = projection.by_scope_id.get(scope_id)
        if scope is None:
            continue
        directory = Path(scope.path)
        if not directory.is_dir():
            continue
        require(directory.resolve().is_relative_to(root), "Projected tables are outside the run root")
        for index, path in enumerate(sorted(directory.glob("*.tsv.gz"))):
            require(index < 256, "Too many tables; narrow the run scope")
            require(path.resolve().is_relative_to(root), "Table symlink leaves the run root")
            for key, (suffix, required) in TABLES.items():
                if path.name.endswith(suffix):
                    fields = header(path)
                    require(set(required) <= set(fields), f"Unexpected native schema for {path.name}")
                    sources.append(TableSource(key, str(path), path.stat().st_size, fields))
    return sources, binding


def preview(record: RunRecord, mappings: Iterable[PathMapping], path: str, query: dict[str, str]) -> dict[str, Any]:
    sources, binding = discover(record, mappings)
    source = next((item for item in sources if item.path == path), None)
    require(source is not None, "Select a table from this run")
    require(set(query) <= {"gene_id", "cell_barcode", "strand", "peak_name", "quantification_unit_id", "intronic_pas_id"}, "Unsupported filter")
    query = {key: str(value).strip() for key, value in query.items() if str(value).strip()}
    require(all(key in source.columns for key in query), "This table lacks a requested filter column; clear that filter or select a different table", "filter")
    require(query.get("strand", "+") in {"+", "-"}, "Strand must be + or -", "filter")
    require(source.key != "cell_gene_matrix" or bool(query.get("cell_barcode")), "The dense matrix needs an exact cell barcode filter; select a long table for a gene query", "matrix_filter")
    for value in query.values():
        require(len(value) <= 256 and not any(c in value for c in "\t\r\n"), "Invalid filter value")
    return {"schema": "scpolya.ui.run_evidence.preview.v1", "created_at": utc_now(),
            "binding": binding, "table": asdict(source), "source": pin(Path(path)),
            "reader": pin(Path(__file__).resolve()),
            "query": query, "limits": {"source_bytes": MAX_SOURCE_BYTES, "expanded_bytes": MAX_EXPANDED_BYTES,
                                       "scan_rows": MAX_SCAN_ROWS, "result_rows": MAX_RESULT_ROWS, "wall_seconds": WALL_SECONDS}}


def attempts(home: Path | None = None) -> Path:
    return (home or ui_home()) / "run_evidence"


_PROCESSES: dict[str, subprocess.Popen] = {}


def start(plan: dict[str, Any], home: Path | None = None) -> Path:
    """Called only after the person's preview confirmation; no approval CLI."""
    directory = attempts(home)
    directory.mkdir(parents=True, exist_ok=True)
    require(not any(p.poll() is None for p in _PROCESSES.values()), "An evidence query is already running; wait for it to finish", "busy")
    target = directory / ("Q-" + uuid.uuid4().hex[:12])
    target.mkdir()
    emit(target / "preview.json", plan)
    emit(target / "status.json", {"status": "starting", "created_at": utc_now()})
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    with (target / "worker.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", __name__, str(target)],
                                   cwd=Path(__file__).resolve().parents[2], env=env,
                                   stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
    _PROCESSES[str(target)] = process
    return target


def recent(home: Path | None = None) -> list[Path]:
    root = attempts(home)
    return sorted((p for p in root.glob("Q-*") if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)[:50]


def status(target: Path) -> dict[str, Any]:
    try:
        value = json.loads((target / "status.json").read_text())
    except (OSError, ValueError):
        return {"status": "failed", "error": "Status record is unavailable"}
    if value.get("status") in {"starting", "running"}:
        process = _PROCESSES.get(str(target))
        if process is not None and process.poll() is not None:
            return {"status": "failed", "error": "Worker exited before closing its result; see worker.log"}
        if time.time() - (target / "status.json").stat().st_mtime > WALL_SECONDS + 30:
            return {"status": "failed", "error": "Worker stopped responding; partial files are preserved"}
    return value


def read_result(target: Path) -> dict[str, Any]:
    require(status(target)["status"] == "succeeded", "The query has no complete result")
    require((target / "result.json").stat().st_size < 4 * 1024**2, "Result exceeds the display budget")
    seals = json.loads((target / "files.json").read_text())
    for name in ("result.json", "rows.tsv", "preview.json"):
        require(digest(target / name) == seals[name], "Saved query files changed; inspect the original attempt before using its report", "result_changed")
    return json.loads((target / "result.json").read_text())


def verify_binding(binding: dict[str, Any]) -> None:
    for key in ("invocation", "namespace_manifest"):
        if key in binding:
            verify(binding[key])
    mappings = tuple(PathMapping(**m) for m in binding.get("path_mappings", []))
    latest = discover_run_log_catalog(binding["view_root"], path_mappings=mappings).invocations
    require(bool(latest) and latest[0].invocation_id == binding["invocation_id"] and latest[0].status == "succeeded",
            "A newer execution appeared; refresh Runs and preview again", "source_changed")
    require(latest[0].resolved_config == binding["config"], "Invocation config changed; refresh Runs")


def execute(target: Path) -> None:
    started = time.monotonic()
    def check() -> None:
        require(time.monotonic() - started < WALL_SECONDS, "Query exceeded the time limit; narrow the table scope")
    emit(target / "status.json", {"status": "running", "phase": "Verifying and copying source", "started_at": utc_now()})
    plan = json.loads((target / "preview.json").read_text())
    source = plan["source"]
    binding = plan["binding"]
    verify(plan["reader"])
    verify_binding(binding)
    verify(source)
    snapshot = target / "source.tsv.gz"
    h = hashlib.sha256()
    size = 0
    with Path(source["path"]).open("rb") as src, snapshot.open("xb") as dst:
        for block in iter(lambda: src.read(1024**2), b""):
            check()
            size += len(block)
            require(size <= MAX_SOURCE_BYTES, "Source grew beyond the snapshot budget")
            h.update(block)
            dst.write(block)
    require(size == source["bytes"] and h.hexdigest() == source["sha256"], "Source changed after preview; preview again", "source_changed")
    verify(source)
    fields = header(snapshot)
    require(list(fields) == list(plan["table"]["columns"]), "Table schema changed")
    matched, scanned, expanded, result_bytes = 0, 0, 0, 0
    selected = []
    emit(target / "status.json", {"status": "running", "phase": "Reading native rows", "started_at": utc_now()})
    with gzip.open(snapshot, "rt", encoding="utf-8", newline="") as handle:
        def lines():
            nonlocal expanded
            while True:
                line = handle.readline(1024**2 + 1)
                if not line:
                    break
                expanded += len(line.encode("utf-8"))
                require(len(line) <= 1024**2 and expanded <= MAX_EXPANDED_BYTES, "Expanded table exceeds the read budget")
                yield line
        reader = csv.DictReader(lines(), delimiter="\t", quoting=csv.QUOTE_NONE)
        require(tuple(reader.fieldnames or ()) == fields, "Unexpected TSV header")
        for row in reader:
            scanned += 1
            if scanned % 10000 == 0:
                check()
            require(scanned <= MAX_SCAN_ROWS, "Table exceeds the row budget")
            require(None not in row and None not in row.values(), "Ragged TSV row")
            if all(row.get(key) == value for key, value in plan["query"].items()):
                matched += 1
                if len(selected) < MAX_RESULT_ROWS:
                    result_bytes += len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
                    require(result_bytes < 2 * 1024**2, "Matching rows exceed the report budget; narrow your filters")
                    selected.append(row)
    check()
    verify_binding(binding)
    result = {"schema": "scpolya.ui.run_evidence.result.v1", "status": "succeeded", "finished_at": utc_now(),
              "columns": fields, "rows": selected, "scanned_rows": scanned, "matched_rows": matched,
              "displayed_rows": len(selected), "truncated": matched > len(selected), "limits": LIMITS,
              "query": plan["query"], "source": source, "binding": binding}
    emit(target / "result.json", result)
    with (target / "rows.tsv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, quotechar=None)
        writer.writeheader()
        writer.writerows(selected)
    (target / "REPORT.txt").write_text(
        f"Native run evidence\n\nTable: {plan['table']['key']}\nRun: {binding['run_id']}\n"
        f"Associated invocation: {binding['invocation_id']}\nSource SHA256: {source['sha256']}\n"
        f"Exact filters: {json.dumps(plan['query'], ensure_ascii=False)}\n"
        f"Matched {matched} of {scanned} source rows; exported first {len(selected)} rows.\n\n{LIMITS}\n",
        encoding="utf-8")
    with zipfile.ZipFile(target / "report.zip", "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("REPORT.txt", "preview.json", "result.json", "rows.tsv"):
            archive.write(target / name, name)
    emit(target / "files.json", {name: digest(target / name) for name in ("result.json", "rows.tsv", "preview.json", "report.zip", "source.tsv.gz")})
    emit(target / "status.json", {"status": "succeeded", "finished_at": utc_now(), "matched_rows": matched})


def download_bytes(target: Path, name: str) -> bytes:
    require(name in {"report.zip", "rows.tsv", "source.tsv.gz", "worker.log"}, "Unsupported download")
    path = target / name
    require(path.is_file(), "The saved download is missing; open another query or run a new one", "source_missing")
    require(path.stat().st_size <= MAX_SOURCE_BYTES, "Download exceeds the size budget", "budget")
    raw = path.read_bytes()
    if name != "worker.log":
        seals = json.loads((target / "files.json").read_text())
        require(hashlib.sha256(raw).hexdigest() == seals.get(name), "Saved query files changed; download refused", "result_changed")
    return raw


if __name__ == "__main__":
    target = Path(sys.argv[1])
    try:
        def timeout(_signum, _frame):
            raise TimeoutError("Query exceeded the time limit; partial files are preserved")
        signal.signal(signal.SIGALRM, timeout)
        signal.alarm(WALL_SECONDS)
        execute(target)
    except Exception as exc:
        emit(target / "status.json", {"status": "failed", "finished_at": utc_now(), "error": str(exc), "error_code": getattr(exc, "code", "worker_failed")})
        raise
