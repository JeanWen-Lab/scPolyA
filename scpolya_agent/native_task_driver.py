"""Bounded native ``main.py run`` driver: plan, run, status, stop and recover.

The driver adds no scientific behaviour. It composes existing product APIs:
``scpolya_ui.command_builder`` (argv and parser materialization),
``task_manage.run_config_preview`` / ``cache_epoch_status`` (run-root state and
rerun preview) and ``scpolya_ui.job_supervisor.run_spec`` (durable job status).

Plan binding: ``run`` and ``recover`` execute exactly the frozen plan file
given, after verifying its ``plan_sha256``, and record that hash in each
attempt. No approval file is required; the researcher confirms the shown plan
parameters in the chat. A legacy ``--approval`` argument is accepted and
ignored. The plan status token ``ready_for_external_approval`` is kept
unchanged for plan-hash compatibility and simply means "ready to run".
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scpolya_ui.command_builder import build_run_command, materialize_execution_config  # noqa: E402
from task_manage.cache_epoch_status import inspect_run_root_cache_epoch  # noqa: E402
from task_manage.run_config_preview import preview_rerun_from_state  # noqa: E402

REQUEST_SCHEMA = "scpolya.native_task_request.v1"
PLAN_SCHEMA = "scpolya.native_task_plan.v1"
APPROVAL_SCHEMA = "scpolya.native_task_approval.v1"  # legacy; no longer checked
RESULT_SCHEMA = "scpolya.native_task_driver_result.v1"

# Manuscript-V4 analysis settings, as mapped to the current parser by the
# accepted 2026-10-18 PBMC chr1 frozen plan (source config ce004fcd). Package
# defaults differ: minpolyA 10, minMAPQ 0.
PRESETS: dict[str, dict[str, Any]] = {
    "manuscript-v4": {
        "cb_tag": "CB",
        "umi_tag": "UB",
        "dedup_method": "directional",
        "minpolyA": 5,
        "nonA_allowed": 1,
        "minMAPQ": 10,
        "coverage_scaling": "expr",
        "exp_peak_threshold_mode": "dynamic",
        "exp_dynamic_threshold_stat": "max_norm_fixed",
        "exp_dynamic_threshold_normalized": 0.05,
        "polyA_peak_threshold_mode": "fixed",
        "unscaled_depth_threshold_polyA": 3,
        "merge_distance": 10,
        "merge_distance_polyA": 10,
        "misprime_A_count": 8,
        "misprime_in": 10,
        "misprimed_ratio_threshold": 0.9,
        "intronic_misprimed_ratio_threshold": 0.7,
        "quantification_mode": "terminal-aware",
        "annotate_peak_source": "merged",
        "summarize_matrix": True,
        "psi_min_gene_umis": 1,
        "tools_seed": 2026,
    }
}

WORKER_KEYS = (
    "n_workers", "n_threads", "n_workers_annotate", "n_threads_merge",
    "n_threads_bam_annotation", "merge_thread", "max_split_workers",
    "max_filter_workers", "max_dedup_workers", "max_extract_workers", "max_merge_workers",
)
REQUEST_KEYS = {"schema", "job_name", "job_dir", "python", "preset", "run", "resources", "env", "preflight"}
RESOURCE_KEYS = {"cpu_quota_percent", "memory_max_bytes", "runtime_max_sec", "workers"}
PATH_KEYS = ("inputs", "gene_gtf", "chrom_sizes")
DEFAULT_ENV = {
    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "NUMEXPR_MAX_THREADS": "1",
    "SCPOLYA_FEATURECOUNTS_ANNOTATED_BAM_THREADS": "1",
}
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
TERMINAL = {"succeeded", "failed", "stopped"}
MAX_PREFLIGHT_RECORDS = 1_000_000
HASH_CHUNK = 1 << 22


class DriverError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: str | Path, *, hash_content: bool = True) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        raise DriverError("relative_path", str(path))
    if not p.is_file():
        raise DriverError("missing_file", str(p))
    st = p.stat()
    out: dict[str, Any] = {"path": str(p), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns}
    if hash_content:
        out["sha256"] = sha256_file(p)
    return out


def _absolute(value: Any, key: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise DriverError("invalid_request", f"{key} must be an absolute path")
    return Path(value)


# ---------------------------------------------------------------- plan

def resolve_config(request: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    preset_name = request.get("preset")
    if preset_name not in PRESETS:
        raise DriverError("unknown_preset", repr(preset_name))
    run = request.get("run")
    if not isinstance(run, dict):
        raise DriverError("invalid_request", "run must be an object")
    for key in WORKER_KEYS:
        if key in run:
            raise DriverError("invalid_request", f"{key} is set by resources.workers")
    workers = request["resources"]["workers"]
    preset = PRESETS[preset_name]
    config = {**preset, **{key: workers for key in WORKER_KEYS}, **run}
    overrides = {
        key: {"preset": preset[key], "request": run[key]}
        for key in sorted(run) if key in preset and run[key] != preset[key]
    }
    return config, {"preset": preset_name, "request_overrides_preset": overrides}


def _parser_defaults() -> dict[str, Any]:
    """Declared defaults of the installed ``run`` subparser (no validation pass)."""
    import argparse as _argparse
    from arguments import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, _argparse._SubParsersAction)).choices["run"]
    return {a.dest: a.default for a in sub._actions
            if a.dest != "help" and a.default is not _argparse.SUPPRESS}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return [str(x) for x in a] == [str(x) for x in b]
    return a == b


def _read_inputs_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise DriverError("invalid_inputs_csv", "no sample rows")
    for row in rows:
        for key in ("sample_id", "source_path", "is_deduped", "requires_split"):
            if not str(row.get(key) or "").strip():
                raise DriverError("invalid_inputs_csv", f"missing {key}")
    return rows


def _chrom_sizes(path: Path) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            name, length = line.split("\t")[:2]
            sizes[name] = int(length)
    return sizes


def preflight_bam(bam: Path, *, contigs: list[str], chrom_sizes: Mapping[str, int],
                  cb_tag: str, umi_tag: str, max_records: int) -> dict[str, Any]:
    """Bounded header/index/tag observations; issues block the plan."""
    import pysam

    issues: list[dict[str, str]] = []
    result: dict[str, Any] = {"bam": str(bam), "contigs": {}, "issues": issues}
    index = [Path(str(bam) + ".bai"), bam.with_suffix(".bai"), Path(str(bam) + ".csi")]
    found = [p for p in index if p.is_file()]
    if not found:
        issues.append({"code": "index_missing", "detail": str(bam)})
        return result
    result["index"] = str(found[0])
    if found[0].stat().st_mtime_ns < bam.stat().st_mtime_ns:
        issues.append({"code": "index_older_than_bam", "detail": str(found[0])})
    with pysam.AlignmentFile(str(bam), "rb") as handle:
        header = dict(zip(handle.references, handle.lengths))
        so = handle.header.to_dict().get("HD", {}).get("SO")
        result["sort_order"] = so
        if so != "coordinate":
            issues.append({"code": "not_coordinate_sorted", "detail": str(so)})
        stats = {row.contig: row.mapped for row in handle.get_index_statistics()}
        for contig in contigs:
            entry: dict[str, Any] = {"bam_length": header.get(contig), "chrom_sizes_length": chrom_sizes.get(contig),
                                     "index_mapped": stats.get(contig)}
            result["contigs"][contig] = entry
            if contig not in header:
                issues.append({"code": "contig_absent_from_bam", "detail": contig})
                continue
            if chrom_sizes.get(contig) != header[contig]:
                issues.append({"code": "chrom_size_mismatch", "detail": contig})
            seen = with_cb = with_umi = 0
            for read in handle.fetch(contig):
                if read.is_unmapped or read.is_secondary or read.is_supplementary:
                    continue
                seen += 1
                with_cb += read.has_tag(cb_tag)
                with_umi += read.has_tag(umi_tag)
                if seen >= max_records:
                    break
            entry.update({"sampled_primary_records": seen, "with_cb_tag": with_cb, "with_umi_tag": with_umi,
                          "sampling": f"first {max_records} primary mapped records in coordinate order"})
            if seen == 0:
                issues.append({"code": "no_primary_records", "detail": contig})
            elif with_cb == 0:
                issues.append({"code": "cb_tag_absent", "detail": f"{contig}:{cb_tag}"})
            elif with_umi == 0:
                issues.append({"code": "umi_tag_absent", "detail": f"{contig}:{umi_tag}"})
    return result


def validate_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("schema") != REQUEST_SCHEMA:
        raise DriverError("invalid_request", f"schema must be {REQUEST_SCHEMA}")
    extra = set(request) - REQUEST_KEYS
    missing = {"schema", "job_name", "job_dir", "python", "preset", "run", "resources"} - set(request)
    if extra or missing:
        raise DriverError("invalid_request", f"unexpected {sorted(extra)} missing {sorted(missing)}")
    if not isinstance(request["job_name"], str) or not SLUG.match(request["job_name"]):
        raise DriverError("invalid_request", "job_name must be a lowercase slug")
    _absolute(request["job_dir"], "job_dir")
    _absolute(request["python"], "python")
    res = request["resources"]
    if not isinstance(res, dict) or set(res) != RESOURCE_KEYS:
        raise DriverError("invalid_request", f"resources must have exactly {sorted(RESOURCE_KEYS)}")
    for key in RESOURCE_KEYS:
        if type(res[key]) is not int or res[key] < 1:
            raise DriverError("invalid_request", f"resources.{key} must be a positive integer")
    env = request.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise DriverError("invalid_request", "env must map strings to strings")
    pre = request.get("preflight", {})
    if not isinstance(pre, dict) or set(pre) - {"max_records_per_contig", "hash_bams"}:
        raise DriverError("invalid_request", "preflight keys: max_records_per_contig, hash_bams")
    return request


def build_plan(request: Mapping[str, Any], *, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    request = validate_request(dict(request))
    config, provenance = resolve_config(request)
    run = request["run"]
    contigs = run.get("contigs")
    if not isinstance(contigs, list) or not contigs:
        raise DriverError("invalid_request", "run.contigs must be an explicit non-empty list")
    for key in ("output_dir", *PATH_KEYS):
        _absolute(run.get(key), f"run.{key}")
    python = request["python"]
    main_path = Path(repo_root) / "main.py"
    try:
        command = build_run_command(config, python_executable=python, main_path=main_path)
        materialized = materialize_execution_config(command.argv)
    except ValueError as exc:
        raise DriverError("invalid_run_config", str(exc)) from exc
    if command.ignored_keys:
        raise DriverError("unknown_run_keys", ",".join(command.ignored_keys))
    mismatched = sorted(key for key, value in config.items() if not _same(materialized.get(key), value))
    if mismatched:
        raise DriverError("parser_mismatch", ",".join(mismatched))
    defaults = _parser_defaults()
    preset = PRESETS[request["preset"]]
    provenance["preset_differs_from_parser_default"] = {
        key: {"parser_default": defaults.get(key), "preset": value}
        for key, value in sorted(preset.items()) if not _same(defaults.get(key), value)
    }

    pre = request.get("preflight", {})
    max_records = int(pre.get("max_records_per_contig", 20000))
    if not 1 <= max_records <= MAX_PREFLIGHT_RECORDS:
        raise DriverError("invalid_request", "max_records_per_contig out of range")
    hash_bams = bool(pre.get("hash_bams", True))
    inputs = {key: file_identity(run[key]) for key in PATH_KEYS}
    rows = _read_inputs_csv(Path(run["inputs"]))
    sizes = _chrom_sizes(Path(run["chrom_sizes"]))
    samples = []
    issues: list[dict[str, str]] = []
    for row in rows:
        bam = _absolute(row["source_path"], "inputs.source_path")
        identity = file_identity(bam, hash_content=hash_bams)
        check = preflight_bam(bam, contigs=[str(c) for c in contigs], chrom_sizes=sizes,
                              cb_tag=config["cb_tag"], umi_tag=config["umi_tag"], max_records=max_records)
        if "index" in check:
            identity["index"] = file_identity(check["index"])
        samples.append({"sample_id": row["sample_id"], "declared_is_deduped": row["is_deduped"],
                        "declared_requires_split": row["requires_split"], "bam": identity, "preflight": check})
        issues.extend({"sample_id": row["sample_id"], **issue} for issue in check["issues"])
        if row["is_deduped"].strip() not in {"0", "1"}:
            issues.append({"sample_id": row["sample_id"], "code": "invalid_is_deduped", "detail": row["is_deduped"]})

    deepcps_identity = _capture_deepcps_identity(materialized, samples, python)

    run_root = Path(run["output_dir"]) / run["run_id"]
    state = run_root_state(run_root)
    if state["blocks_run"]:
        issues.append({"code": "run_root_blocked", "detail": state["status"]})
    mode = "fresh" if state["status"] == "no_state" and not run_root.exists() else "existing_root"
    if mode == "existing_root":
        issues.append({"code": "run_root_exists", "detail": "fresh plans require a new run root; use recover for an own attempt"})

    plan = {
        "schema": PLAN_SCHEMA,
        "created_utc": utc_now(),
        "job_name": request["job_name"],
        "job_dir": request["job_dir"],
        "product": {"repo_root": str(Path(repo_root).resolve()), "git": _git_identity(Path(repo_root))},
        "python": python,
        "config_provenance": provenance,
        "argv": list(command.argv),
        "materialized_config": materialized,
        "run_root": str(run_root),
        "mode": mode,
        "run_root_state": state,
        "inputs": inputs,
        "samples": samples,
        "deepcps": deepcps_identity,
        "resources": dict(request["resources"]),
        "systemd_bus_env": bus_env(),
        "runtime_identity": runtime_identity(python, Path(repo_root)),
        "env": {**DEFAULT_ENV, "PATH": f"{Path(python).parent}:/usr/bin:/bin", **request.get("env", {})},
        "status": "blocked" if issues else "ready_for_external_approval",
        "issues": issues,
        "scope": "One native main.py run of the stated argv; no scientific acceptance, threshold selection or retry policy.",
    }
    plan["plan_sha256"] = plan_hash(plan)
    return plan


def plan_hash(plan: Mapping[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    return hashlib.sha256(canonical(body)).hexdigest()


def _git_identity(repo: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
        return out.stdout.strip() if out.returncode == 0 else ""
    head = git("rev-parse", "HEAD")
    return {"head": head or None, "dirty_tracked": bool(git("status", "--porcelain", "--untracked-files=no")) if head else None}


def run_root_state(run_root: Path) -> dict[str, Any]:
    status = inspect_run_root_cache_epoch(run_root / "state")
    return {"status": status.status, "blocks_run": status.blocks_run,
            "incomplete_task_count": len(status.incomplete_task_keys),
            "completion_marker": None if status.completion_marker is None else dict(status.completion_marker),
            "error": status.error}


# ---------------------------------------------------------------- plan and attempts

def load_verified_plan(plan_path: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema") != PLAN_SCHEMA or plan.get("plan_sha256") != plan_hash(plan):
        raise DriverError("plan_hash_mismatch", str(plan_path))
    return plan


def _attempts_dir(plan: Mapping[str, Any]) -> Path:
    return Path(plan["job_dir"]) / "attempts"


def list_attempts(plan: Mapping[str, Any]) -> list[Path]:
    root = _attempts_dir(plan)
    return sorted(p for p in root.glob("a[0-9][0-9][0-9]") if p.is_dir()) if root.is_dir() else []


def _verify_inputs_unchanged(plan: Mapping[str, Any]) -> None:
    for item in plan["inputs"].values():
        if file_identity(item["path"]) != item:
            raise DriverError("input_drift", item["path"])
    for sample in plan["samples"]:
        bam = sample["bam"]
        now = file_identity(bam["path"], hash_content="sha256" in bam)
        if any(now[k] != bam[k] for k in now):
            raise DriverError("input_drift", bam["path"])
        if "index" in bam and file_identity(bam["index"]["path"]) != bam["index"]:
            raise DriverError("input_drift", bam["index"]["path"])
    if plan["materialized_config"].get("deepcps", False):
        now = _capture_deepcps_identity(plan["materialized_config"], plan["samples"], plan["python"],
                                       error_code="deepcps_input_drift")
        if now != plan.get("deepcps"):
            raise DriverError("deepcps_input_drift", "DeepCPS resource/runtime identity differs from plan")


def _capture_deepcps_identity(config, samples, python, *, error_code="invalid_deepcps"):
    from scpolya_agent.native_deepcps import capture_identity

    try:
        return capture_identity(config, samples, python)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError, subprocess.SubprocessError) as exc:
        raise DriverError(error_code, str(exc)) from exc


def deepcps_result_plan(plan_path: Path, *, offset: int = 0, limit: int = 100,
                        query: UnitQuery | None = None) -> dict[str, Any]:
    """Read one bounded result page only for an own verified native completion."""
    from scpolya_agent.native_deepcps import read_result
    import sqlite3

    plan = load_verified_plan(plan_path)
    if not plan["materialized_config"].get("deepcps", False):
        raise DriverError("deepcps_disabled", "DeepCPS was not enabled in this plan")
    if not plan.get("deepcps", {}).get("enabled"):
        raise DriverError("deepcps_identity_missing", "enabled plan has no DeepCPS admission identity")
    status = status_plan(plan_path, query=query or unit_query)
    if status["state"] != "succeeded_verified" or not status.get("quiescence", {}).get("confirmed"):
        raise DriverError("deepcps_result_unavailable", status["state"])
    try:
        result = read_result(plan, status, offset=offset, limit=limit)
        seal_path = list_attempts(plan)[-1] / "deepcps-result-seal.json"
        with seal_path.open("rb") as handle:
            data = handle.read((1 << 20) + 1)
        if len(data) > 1 << 20 or json.loads(data) != _deepcps_seal(result):
            raise ValueError("DeepCPS result differs from the own attempt completion seal")
        result["completion_seal"] = str(seal_path)
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, csv.Error, sqlite3.Error) as exc:
        raise DriverError("invalid_deepcps_result", str(exc)) from exc


def _deepcps_seal(result):
    return {"schema": "scpolya.native_deepcps_completion_seal.v1",
            **{key: result[key] for key in ("plan_sha256", "fingerprint", "observation_id", "invocation",
                                            "provenance_path", "provenance_sha256")},
            "outputs": result["provenance"]["outputs"]}


def _seal_deepcps_result(plan, status, attempt_dir):
    from scpolya_agent.native_deepcps import read_result

    if status["state"] != "succeeded_verified":
        raise ValueError("native completion is not verified; cannot seal DeepCPS result")
    result = read_result(plan, status, offset=0, limit=1)
    target = attempt_dir / "deepcps-result-seal.json"
    with target.open("x", encoding="utf-8") as handle:
        json.dump(_deepcps_seal(result), handle, sort_keys=True, indent=2)
    return {"status": "sealed", "path": str(target), "sha256": sha256_file(target)}


RUNTIME_TOOLS = ("samtools", "bedtools", "featureCounts", "umi_tools", "bedToBigBed", "bedGraphToBigWig")
UNIT_PROPERTIES = ("LoadState", "ActiveState", "SubState", "Result", "ExecMainCode", "ExecMainStatus",
                   "MainPID", "ControlGroup", "CPUUsageNSec", "MemoryPeak", "MemorySwapPeak",
                   "ExecMainStartTimestamp", "ExecMainExitTimestamp", "CPUQuotaPerSecUSec", "MemoryMax", "MemorySwapMax")


def bus_env(uid: int | None = None) -> dict[str, str]:
    """Explicit user-manager bus; never inherited from the caller, so a missing caller env cannot fake 'lost'."""
    uid = os.getuid() if uid is None else uid
    return {"XDG_RUNTIME_DIR": f"/run/user/{uid}", "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
            "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


def runtime_identity(python: str, repo: Path) -> dict[str, Any]:
    """Interpreter, conda package records, external tools and checkout source bytes."""
    py = Path(python)
    real = py.resolve()
    prefix = real.parent.parent
    probe = subprocess.run([str(py), "-I", "-c", "import json,sys;print(json.dumps([sys.version,sys.prefix,sys.executable]))"],
                           capture_output=True, text=True, timeout=60, env={"PATH": "/usr/bin:/bin"})
    if probe.returncode != 0:
        raise DriverError("python_probe_failed", probe.stderr[-300:])
    packages = []
    for meta in sorted((prefix / "conda-meta").glob("*.json")):
        record = json.loads(meta.read_text(encoding="utf-8"))
        packages.append([record.get("name"), record.get("version"), record.get("build")])
    tools = {}
    for tool in RUNTIME_TOOLS:
        path = prefix / "bin" / tool
        tools[tool] = {"path": str(path), "realpath": str(path.resolve()), "sha256": sha256_file(path)} if path.exists() else None
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=60)
    ls = git("ls-files", "-s")
    st = git("status", "--porcelain=v1", "--untracked-files=all", "--ignored=no")
    source = {"head": git("rev-parse", "HEAD").stdout.strip() or None,
              "tracked_index_sha256": hashlib.sha256(ls.stdout.encode()).hexdigest() if ls.returncode == 0 else None,
              "worktree_status": st.stdout.splitlines() if st.returncode == 0 else None}
    if source["head"]:
        tracked = sorted(line.split("\t", 1)[1] for line in ls.stdout.splitlines())
        digest = hashlib.sha256()
        for rel in tracked:
            p = repo / rel
            digest.update(rel.encode() + b"\0" + (sha256_file(p).encode() if p.is_file() and not p.is_symlink() else b"-") + b"\n")
        source["tracked_bytes_sha256"] = digest.hexdigest()
    out = {"python": {"path": str(py), "realpath": str(real), "sha256": sha256_file(real),
                      "version": json.loads(probe.stdout)[0], "prefix": json.loads(probe.stdout)[1]},
           "conda_packages_sha256": hashlib.sha256(canonical(packages)).hexdigest(),
           "selected_packages": {p[0]: p[1:] for p in packages if p[0] in {
               "python", "pysam", "numpy", "pandas", "scipy", "gffutils", "umi_tools", "samtools", "bedtools",
               "subread", "htslib", "ucsc-bedtobigbed", "ucsc-bedgraphtobigwig"}},
           "tools": tools, "source": source}
    out["identity_sha256"] = hashlib.sha256(canonical(out)).hexdigest()
    return out


def _verify_product(plan: Mapping[str, Any]) -> dict[str, Any]:
    repo = Path(plan["product"]["repo_root"])
    if _git_identity(repo) != plan["product"]["git"]:
        raise DriverError("product_drift", str(repo))
    now = runtime_identity(plan["python"], repo)
    if now["identity_sha256"] != plan["runtime_identity"]["identity_sha256"]:
        changed = sorted(k for k in now if now[k] != plan["runtime_identity"].get(k))
        raise DriverError("runtime_drift", ",".join(changed))
    return now


class UnitQueryError(DriverError):
    pass


def unit_query(unit: str, env: Mapping[str, str]) -> dict[str, str]:
    """Fail closed: any systemctl/bus error raises; never mapped to inactive."""
    try:
        out = subprocess.run(["systemctl", "--user", "show", unit, *[f"--property={p}" for p in UNIT_PROPERTIES]],
                             capture_output=True, text=True, timeout=30, env=dict(env))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UnitQueryError("unit_query_failed", f"{type(exc).__name__}: {exc}") from exc
    props = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    if out.returncode != 0 or not {"LoadState", "ActiveState"} <= set(props):
        raise UnitQueryError("unit_query_failed", f"rc={out.returncode} {out.stderr.strip()[-300:]}")
    return {**props, "queried_utc": utc_now()}


UnitQuery = Callable[[str, Mapping[str, str]], dict[str, str]]


def _read_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _cgroup_populated(path: Path) -> bool | None:
    """True/False from cgroup.events; None when the cgroup directory is absent."""
    events = path / "cgroup.events"
    if not path.exists():
        return None
    for line in events.read_text().splitlines():
        if line.startswith("populated "):
            return line.split()[1] != "0"
    raise DriverError("cgroup_events_unreadable", str(events))


def quiescence(plan: Mapping[str, Any], attempt_dir: Path, *, query: UnitQuery = unit_query) -> dict[str, Any]:
    """Confirmed only if the unit query succeeds, the unit has no live main process and every known cgroup is empty or gone."""
    launch = _read_json(attempt_dir / "launch.json") or {}
    unit = launch.get("unit")
    receipt: dict[str, Any] = {"attempt": attempt_dir.name, "unit": unit, "checked_utc": utc_now(), "confirmed": False}
    if not unit:
        receipt["reason"] = "unit_unknown"
        return receipt
    try:
        props = query(unit, plan["systemd_bus_env"])
    except UnitQueryError as exc:
        receipt["reason"] = exc.code
        receipt["detail"] = exc.detail
        return receipt
    receipt["unit_properties"] = props
    stopped = props["LoadState"] == "not-found" or props["ActiveState"] in {"inactive", "failed"} or (
        props["ActiveState"] == "active" and props.get("SubState") == "exited" and props.get("MainPID") == "0")
    groups = {g for g in (launch.get("control_group"), props.get("ControlGroup"),
                          ((_read_json(attempt_dir / "resources.json") or {}).get("cgroup_rel"))) if g}
    checks = {}
    for rel in sorted(groups):
        try:
            populated = _cgroup_populated(Path("/sys/fs/cgroup") / rel.lstrip("/"))
        except (OSError, DriverError) as exc:
            populated = f"unreadable: {exc}"
        checks[rel] = populated
    receipt["cgroups"] = checks
    if not stopped:
        receipt["reason"] = "unit_still_active"
    elif not groups:
        receipt["reason"] = "cgroup_unknown"
    elif any(v not in (None, False) for v in checks.values()):
        receipt["reason"] = "cgroup_populated_or_unreadable"
    else:
        receipt["confirmed"] = True
    return receipt


Launcher = Callable[[Mapping[str, Any], Path], dict[str, Any]]


def systemd_launcher(plan: Mapping[str, Any], attempt_dir: Path) -> dict[str, Any]:
    """Retained unit (RemainAfterExit, no --collect): systemd Result/ExecMainStatus survive until 'harvest'."""
    res = plan["resources"]
    spec = json.loads((attempt_dir / "spec.json").read_text(encoding="utf-8"))
    unit = f"scpolya-native-{spec['job_id']}.service"
    argv = [
        "systemd-run", "--user", f"--unit={unit}", "-p", "RemainAfterExit=yes",
        "-p", f"CPUQuota={res['cpu_quota_percent']}%", "-p", f"MemoryMax={res['memory_max_bytes']}",
        "-p", "MemorySwapMax=0", "-p", f"RuntimeMaxSec={res['runtime_max_sec']}",
        "-p", "TimeoutStopSec=30", "-p", "KillMode=control-group",
        f"--working-directory={plan['product']['repo_root']}",
        *[f"--setenv={k}={v}" for k, v in sorted(plan["env"].items())],
        plan["python"], "-B", str(Path(__file__).resolve()), "_unit-entry", "--attempt-dir", str(attempt_dir),
    ]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=plan["systemd_bus_env"])
    receipt = {"launcher": "systemd-run", "unit": unit, "argv": argv, "returncode": out.returncode,
               "stdout": out.stdout[-2000:], "stderr": out.stderr[-2000:]}
    if out.returncode == 0:
        try:
            props = unit_query(unit, plan["systemd_bus_env"])
            receipt["launch_query"] = props
            receipt["control_group"] = props.get("ControlGroup") or None
        except UnitQueryError as exc:
            receipt["launch_query_error"] = exc.detail
    return receipt


def _new_attempt(plan: Mapping[str, Any], *, launch_mode: str, continue_from: str, launcher: Launcher,
                 identity: Mapping[str, Any]) -> dict[str, Any]:
    # Callers decide admissibility: run requires no attempts, recover requires a confirmed-quiescent one.
    attempts = list_attempts(plan)
    attempt_dir = _attempts_dir(plan) / f"a{len(attempts) + 1:03d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    if plan["materialized_config"].get("deepcps", False):
        (attempt_dir / "native-plan.json").write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
    (attempt_dir / "runtime-before.json").write_text(json.dumps(identity, indent=2, sort_keys=True), encoding="utf-8")
    job_id = f"{plan['job_name']}-{attempt_dir.name}"
    spec = {
        "job_id": job_id, "argv": plan["argv"], "command": " ".join(plan["argv"]),
        "cwd": plan["product"]["repo_root"], "run_root": plan["run_root"],
        "log_path": str(attempt_dir / "native.log"), "status_path": str(attempt_dir / "status.json"),
        "created_at": utc_now(), "launch_mode": launch_mode, "continue_from_run_record_id": continue_from,
        "plan_sha256": plan["plan_sha256"],
    }
    (attempt_dir / "spec.json").write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    receipt = launcher(plan, attempt_dir)
    receipt.update({"job_id": job_id, "attempt": attempt_dir.name, "launched_utc": utc_now(), "launch_mode": launch_mode})
    (attempt_dir / "launch.json").write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    if receipt.get("returncode") != 0:
        raise DriverError("launch_failed", receipt.get("stderr", "")[-300:])
    return receipt


def run_plan(plan_path: Path, approval_path: Path | None = None, *,
             launcher: Launcher = systemd_launcher) -> dict[str, Any]:
    # approval_path is a deprecated, ignored argument kept for old callers.
    plan = load_verified_plan(plan_path)
    if plan["status"] != "ready_for_external_approval":
        raise DriverError("plan_blocked", json.dumps(plan["issues"])[:300])
    if list_attempts(plan):
        raise DriverError("already_launched", "use status or recover")
    if Path(plan["run_root"]).exists():
        raise DriverError("run_root_exists", plan["run_root"])
    identity = _verify_product(plan)
    _verify_inputs_unchanged(plan)
    return _new_attempt(plan, launch_mode="fresh", continue_from="", launcher=launcher, identity=identity)


def recover_plan(plan_path: Path, approval_path: Path | None = None, *, launcher: Launcher = systemd_launcher,
                 query: UnitQuery = unit_query) -> dict[str, Any]:
    # approval_path is a deprecated, ignored argument kept for old callers.
    plan = load_verified_plan(plan_path)
    attempts = list_attempts(plan)
    if not attempts:
        raise DriverError("nothing_to_recover", "no attempt exists")
    last = status_plan(plan_path, query=query)
    if last["state"] == "succeeded_verified":
        raise DriverError("already_complete", last["attempt"])
    if last["state"] not in {"failed", "stopped", "lost", "succeeded_unverified"}:
        raise DriverError("not_recoverable", last["state"])
    previous = attempts[-1]
    quiet = quiescence(plan, previous, query=query)
    (previous / "quiescence-before-recover.json").write_text(json.dumps(quiet, indent=2, sort_keys=True), encoding="utf-8")
    if not quiet["confirmed"]:
        raise DriverError("not_quiescent", quiet.get("reason", "unconfirmed"))
    identity = _verify_product(plan)
    _verify_inputs_unchanged(plan)
    root = Path(plan["run_root"])
    if (root / "state" / "state.sqlite").is_file():
        done = len(list((previous / "snapshots").glob("before-recover-*"))) if (previous / "snapshots").is_dir() else 0
        snapshot_state(plan_path, label=f"before-recover-{done + 1}")
    state = run_root_state(root)
    preview: dict[str, Any] = {"run_root_state": state, "quiescence": quiet}
    if state["blocks_run"]:
        raise DriverError("run_root_blocked", state["status"])
    if (root / "state" / "state.sqlite").is_file():
        rerun = preview_rerun_from_state(root / "state", plan["materialized_config"])
        preview["changed_effective_keys"] = list(rerun.changed_effective_keys)
        preview["full_cache_rebuild"] = rerun.full_cache_rebuild
        if rerun.changed_effective_keys or rerun.full_cache_rebuild:
            raise DriverError("config_drift", ",".join(rerun.changed_effective_keys) or "full_cache_rebuild")
    receipt = _new_attempt(plan, launch_mode="resume", continue_from=last["job_id"], launcher=launcher, identity=identity)
    receipt["recovery_preview"] = preview
    (_attempts_dir(plan) / receipt["attempt"] / "recovery.json").write_text(
        json.dumps(preview, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return receipt


# ---------------------------------------------------------------- status

def _attempt_status(attempt_dir: Path) -> dict[str, Any]:
    status = _read_json(attempt_dir / "status.json") or {}
    launch = _read_json(attempt_dir / "launch.json") or {}
    return {"attempt": attempt_dir.name, "job_id": launch.get("job_id"), "unit": launch.get("unit"),
            "supervisor_status": status.get("status", "not_started"), "exit_code": status.get("exit_code"),
            "started_at": status.get("started_at"), "finished_at": status.get("finished_at"),
            "launch_mode": launch.get("launch_mode"), "resources": _read_json(attempt_dir / "resources.json"),
            "harvest": _read_json(attempt_dir / "unit-harvest.json")}


def _invocation_for_job(run_root: Path, job_id: str) -> dict[str, Any] | None:
    for path in sorted((run_root / "logs" / "invocations").glob("*/invocation.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if isinstance(payload, dict) and payload.get("job_id") == job_id:
            return {"path": str(path), "invocation_id": payload.get("invocation_id"), "status": payload.get("status")}
    return None


def _native_completion(run_root: Path, job_id: str) -> dict[str, Any]:
    invocation = _invocation_for_job(run_root, job_id)
    state = run_root_state(run_root)
    marker = state["completion_marker"] or {}
    verified = (invocation is not None and invocation.get("status") == "succeeded" and state["status"] == "current"
                and marker.get("last_invocation_id") == invocation.get("invocation_id"))
    return {"invocation": invocation, "run_root_state": state,
            "state": "succeeded_verified" if verified else "succeeded_unverified"}


def status_plan(plan_path: Path, *, query: UnitQuery = unit_query) -> dict[str, Any]:
    plan = load_verified_plan(plan_path)
    attempts = list_attempts(plan)
    if not attempts:
        return {"schema": RESULT_SCHEMA, "state": "not_launched", "plan_sha256": plan["plan_sha256"], "attempts": []}
    history = [_attempt_status(path) for path in attempts]
    last = history[-1]
    root = Path(plan["run_root"])
    result = {"schema": RESULT_SCHEMA, "plan_sha256": plan["plan_sha256"], "run_root": str(root),
              "attempt": last["attempt"], "job_id": last["job_id"], "attempts": history}
    sup = last["supervisor_status"]
    quiet = quiescence(plan, attempts[-1], query=query)
    result["quiescence"] = quiet
    if sup not in TERMINAL:
        if quiet.get("reason") == "unit_still_active":
            result["state"] = "running"
        elif quiet["confirmed"]:
            result["state"] = "lost"
        else:
            result["state"] = "unknown"  # query/bus error or cgroup evidence missing: never 'lost'
        return result
    if sup != "succeeded":
        result["state"] = sup
        return result
    result.update(_native_completion(root, last["job_id"]))
    if result["state"] == "succeeded_verified" and plan["materialized_config"].get("deepcps", False):
        seal_path = attempts[-1] / "deepcps-result-seal.json"
        recorded = (last.get("resources") or {}).get("deepcps_result_seal") or {}
        try:
            with seal_path.open("rb") as handle:
                data = handle.read((1 << 20) + 1)
            seal = json.loads(data) if len(data) <= 1 << 20 else {}
            valid = (recorded.get("status") == "sealed" and recorded.get("sha256") == hashlib.sha256(data).hexdigest()
                     and seal.get("schema") == "scpolya.native_deepcps_completion_seal.v1"
                     and seal.get("plan_sha256") == plan["plan_sha256"] and seal.get("invocation") == result["invocation"])
        except (OSError, ValueError):
            valid = False
        result["deepcps_result_seal"] = recorded
        if not valid:
            result["state"] = "succeeded_unverified"
            result["deepcps_result_seal_reason"] = "DeepCPS completion seal pending, absent or invalid"
    return result


def stop_plan(plan_path: Path, *, query: UnitQuery = unit_query) -> dict[str, Any]:
    plan = load_verified_plan(plan_path)
    attempts = list_attempts(plan)
    if not attempts:
        raise DriverError("not_launched", plan["job_name"])
    last = _attempt_status(attempts[-1])
    if last["supervisor_status"] in TERMINAL:
        raise DriverError("already_terminal", last["supervisor_status"])
    props = query(str(last["unit"]), plan["systemd_bus_env"])  # raises on bus error
    if props["ActiveState"] != "active" or props.get("SubState") != "running":
        raise DriverError("unit_not_running", f"{props['ActiveState']}/{props.get('SubState')}")
    out = subprocess.run(["systemctl", "--user", "kill", "--kill-whom=main", "--signal=SIGTERM", str(last["unit"])],
                         capture_output=True, text=True, timeout=30, env=plan["systemd_bus_env"])
    if out.returncode != 0:
        raise DriverError("stop_failed", out.stderr[-300:])
    receipt = {"schema": RESULT_SCHEMA, "stop_requested": True, "unit": last["unit"], "pre_stop_query": props,
               "requested_utc": utc_now()}
    (attempts[-1] / "stop.json").write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return receipt


def harvest_plan(plan_path: Path, *, query: UnitQuery = unit_query) -> dict[str, Any]:
    """Record retained-unit systemd properties for a quiescent terminal attempt, then stop/reset the unit."""
    plan = load_verified_plan(plan_path)
    attempts = list_attempts(plan)
    if not attempts:
        raise DriverError("not_launched", plan["job_name"])
    attempt = attempts[-1]
    if (attempt / "unit-harvest.json").exists():
        raise DriverError("already_harvested", attempt.name)
    quiet = quiescence(plan, attempt, query=query)
    if not quiet["confirmed"]:
        raise DriverError("not_quiescent", quiet.get("reason", "unconfirmed"))
    unit = quiet["unit"]
    cleanup = []
    if quiet["unit_properties"]["LoadState"] != "not-found":
        for verb in ("stop", "reset-failed"):
            out = subprocess.run(["systemctl", "--user", verb, unit], capture_output=True, text=True, timeout=60,
                                 env=plan["systemd_bus_env"])
            cleanup.append({"verb": verb, "returncode": out.returncode, "stderr": out.stderr[-300:]})
    receipt = {"quiescence": quiet, "cleanup": cleanup, "after_cleanup": query(unit, plan["systemd_bus_env"])}
    (attempt / "unit-harvest.json").write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return receipt


def snapshot_state(plan_path: Path, *, label: str) -> dict[str, Any]:
    """Read-only SQLite backup of the run state plus completion/decision evidence, saved in the latest attempt."""
    import sqlite3

    if not SLUG.match(label):
        raise DriverError("invalid_label", label)
    plan = load_verified_plan(plan_path)
    attempts = list_attempts(plan)
    if not attempts:
        raise DriverError("not_launched", plan["job_name"])
    db = Path(plan["run_root"]) / "state" / "state.sqlite"
    if not db.is_file():
        raise DriverError("no_state", str(db))
    target = attempts[-1] / "snapshots" / label / "state" / "state.sqlite"
    target.parent.mkdir(parents=True, exist_ok=False)
    src = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        src.close()
    tables = {name: dst.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
              for (name,) in dst.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()}
    dst.close()
    status = inspect_run_root_cache_epoch(target)
    summary = {"label": label, "taken_utc": utc_now(), "source": str(db), "snapshot": str(target),
               "snapshot_sha256": sha256_file(target), "table_rows": tables, "epoch_status": status.as_dict(),
               "invocations": sorted((Path(plan["run_root"]) / "logs" / "invocations").glob("*/invocation.json")).__len__()}
    (target.parent.parent / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str),
                                                      encoding="utf-8")
    return summary


# ---------------------------------------------------------------- unit entry

def _own_cgroup_rel() -> str | None:
    try:
        line = Path("/proc/self/cgroup").read_text().strip().splitlines()[0]
    except OSError:
        return None
    return line.split("::", 1)[1] if "::" in line else None


CGROUP_FILES = ("memory.peak", "memory.events", "memory.swap.peak", "memory.swap.events", "memory.swap.max",
                "memory.max", "cpu.stat", "cpu.max", "pids.peak")


def read_cgroup(rel: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    base = Path("/sys/fs/cgroup") / rel.lstrip("/") if rel else None
    for name in CGROUP_FILES:
        try:
            out[name] = (base / name).read_text().strip() if base else None
        except OSError:
            out[name] = None  # reported missing, never substituted
    return out


def unit_entry(attempt_dir: Path) -> int:
    """Inside the transient unit: run the product supervisor, then record whole-cgroup counters."""
    from scpolya_ui.job_supervisor import run_spec

    rel = _own_cgroup_rel()
    before = {"utc": utc_now(), "cgroup_rel": rel, "files": read_cgroup(rel)}
    code = run_spec(attempt_dir / "spec.json")
    seal = None
    if code == 0 and (attempt_dir / "native-plan.json").is_file():
        try:
            plan_path = attempt_dir / "native-plan.json"
            plan = load_verified_plan(plan_path)
            spec = json.loads((attempt_dir / "spec.json").read_text(encoding="utf-8"))
            seal = _seal_deepcps_result(plan, _native_completion(Path(plan["run_root"]), spec["job_id"]), attempt_dir)
        except Exception as exc:
            # Preserve the scientific/supervisor outcome; the result reader
            # fails closed when its independent completion seal is unavailable.
            seal = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500]}
    observed = {"cgroup_rel": rel, "before": before,
                "after": {"utc": utc_now(), "files": read_cgroup(rel)}, "supervisor_return_code": code,
                "note": "aggregate cgroup counters of this unit; no per-process RSS substitution"}
    if seal is not None:
        observed["deepcps_result_seal"] = seal
    temp_resources = attempt_dir / "resources.json.tmp"
    temp_resources.write_text(json.dumps(observed, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp_resources, attempt_dir / "resources.json")
    return code


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="native_task_driver")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--request", required=True, type=Path)
    for name in ("run", "recover"):
        q = sub.add_parser(name)
        q.add_argument("--plan", required=True, type=Path)
        q.add_argument("--approval", required=False, default=None, type=Path,
                       help="deprecated and ignored; no approval file is needed")
    for name in ("status", "stop", "harvest"):
        sub.add_parser(name).add_argument("--plan", required=True, type=Path)
    deep = sub.add_parser("deepcps-result", help="Read a bounded, verified DeepCPS result page")
    deep.add_argument("--plan", required=True, type=Path)
    deep.add_argument("--offset", type=int, default=0)
    deep.add_argument("--limit", type=int, default=100)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--plan", required=True, type=Path)
    snap.add_argument("--label", required=True)
    ident = sub.add_parser("identity")
    ident.add_argument("--python", required=True)
    ident.add_argument("--repo-root", required=True, type=Path)
    u = sub.add_parser("_unit-entry")
    u.add_argument("--attempt-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "_unit-entry":
        return unit_entry(args.attempt_dir)
    try:
        if args.command == "plan":
            request = json.loads(args.request.read_text(encoding="utf-8"))
            plan = build_plan(request)
            job_dir = Path(plan["job_dir"])
            job_dir.mkdir(parents=True, exist_ok=True)
            target = job_dir / "plan.json"
            if target.exists():
                raise DriverError("plan_exists", str(target))
            target.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
            result = {"schema": RESULT_SCHEMA, "plan": str(target), "plan_sha256": plan["plan_sha256"],
                      "status": plan["status"], "issues": plan["issues"]}
        elif args.command == "run":
            result = run_plan(args.plan, args.approval)
        elif args.command == "recover":
            result = recover_plan(args.plan, args.approval)
        elif args.command == "status":
            result = status_plan(args.plan)
        elif args.command == "deepcps-result":
            result = deepcps_result_plan(args.plan, offset=args.offset, limit=args.limit)
        elif args.command == "harvest":
            result = harvest_plan(args.plan)
        elif args.command == "snapshot":
            result = snapshot_state(args.plan, label=args.label)
        elif args.command == "identity":
            result = runtime_identity(args.python, args.repo_root)
        else:
            result = stop_plan(args.plan)
    except DriverError as exc:
        print(json.dumps({"schema": RESULT_SCHEMA, "status": "error", "code": exc.code, "detail": exc.detail[:500]}))
        return 2
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
