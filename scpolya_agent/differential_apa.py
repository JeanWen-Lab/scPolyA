"""Structured, supervised adapter to the reviewed scpolya_diff implementation.

Planning materializes raw-count aggregation in an exclusively owned directory.
It does not run inference. ``execute`` runs exactly the plan file given
(its sha256 is recorded in attempt.json); no approval file is needed. The
researcher confirms the shown plan in the chat or GUI. A legacy ``--approval``
argument is accepted and ignored.
"""
from __future__ import annotations

import argparse
import collections
import csv
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__ in (None, ""):
    # The installed operator launches this pinned checkout script with -I -B.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scpolya_diff.cli import build_parser, _run
from scpolya_diff.common import file_record, now, sha256, table, write_json
from scpolya_diff.prepare import prepare

PREFIX = "scpolya.differential_apa."
MAX_JSON = 2 * 1024 * 1024
MAX_INPUT_BYTES = 2 * 1024**3
MAX_METADATA_ROWS = 200000
MAX_SAMPLES = 128
DEFAULTS = dict(analysis_set="native-terminal", min_cells=20, allow_unmatched_cells=False,
                methods="edger,limma", descriptive_only=False, paired=False, batch=False,
                min_pas_count=5, min_pas_samples="auto", min_unit_count=20,
                seed=20261021, timeout_seconds=1200)
DONE = {"DESCRIPTIVE_ONLY", "COMPLETE", "COMPLETE_WITH_WARNINGS", "COMPLETE_WITH_UNTESTABLE_LENGTH_UNITS"}
LIMITS = ["Supplied donor, condition and upstream catalogue provenance cannot be independently authenticated by this adapter.",
          "Execution is bound to the exact plan file bytes (plan sha256 recorded in attempt.json).",
          "One donor or one observed condition does not establish population differential APA.",
          "Eligibility diagnostics are preflight checks, not statistical results or calibrated FDR evidence.",
          "Existing native denominators, distal ties, unavailable values and separate endpoint families remain authoritative."]


def need(ok, message):
    if not ok:
        raise ValueError(message)


def load(path):
    p = Path(path)
    need(p.stat().st_size <= MAX_JSON, "JSON exceeds 2 MiB")
    def unique(pairs):
        d = {}
        for k, v in pairs:
            need(k not in d, "Duplicate JSON field: " + k)
            d[k] = v
        return d
    return json.loads(p.read_text(), object_pairs_hook=unique,
                      parse_constant=lambda x: need(False, "Nonfinite JSON value"))


def exact(d, required, optional=()):
    need(isinstance(d, dict), "Expected JSON object")
    need(set(required) <= set(d) and not set(d) - set(required) - set(optional),
         "Missing or unknown fields: expected " + ",".join(required))


def text(value, label):
    need(isinstance(value, str) and bool(value.strip()) and value == value.strip()
         and not any(c in value for c in "\r\n\t\x00"), "Invalid " + label)
    return value


def absolute(value):
    text(value, "path")
    need(Path(value).is_absolute(), "Absolute paths required: " + value)
    return str(Path(value).resolve())


def normalize(doc):
    exact(doc, ("schema", "inputs", "cell_type", "provenance"), ("contrast", "options", "runtime"))
    need(doc["schema"] == PREFIX + "request.v1", "Invalid request schema")
    exact(doc["inputs"], ("manifest", "metadata", "annotation"))
    inputs = {k: absolute(v) for k, v in doc["inputs"].items()}
    exact(doc["provenance"], ("kind", "description", "metadata_origin"))
    need(doc["provenance"]["kind"] in ("real", "synthetic"), "Provenance kind must be real or synthetic")
    for k, v in doc["provenance"].items(): text(v, "provenance." + k)
    opts = doc.get("options", {})
    exact(opts, (), DEFAULTS)
    opts = {**DEFAULTS, **opts}
    for k in ("allow_unmatched_cells", "descriptive_only", "paired", "batch"):
        need(type(opts[k]) is bool, k + " must be boolean")
    for k in ("min_cells", "min_pas_count", "min_unit_count", "seed", "timeout_seconds"):
        need(type(opts[k]) is int and 1 <= opts[k] <= 2**31-1, k + " must be a positive integer")
    need(opts["timeout_seconds"] <= 1200, "timeout_seconds exceeds 1200")
    need(opts["analysis_set"] in ("native-terminal", "terminal-only"), "Invalid analysis_set")
    need(isinstance(opts["methods"], str), "methods must be comma-separated text")
    methods = opts["methods"].split(",")
    need(len(methods) == len(set(methods)) and bool(methods) and not set(methods) - {"edger", "limma", "drimseq"}, "Invalid methods")
    need(not opts["descriptive_only"] or opts["methods"] == "edger,limma", "Descriptive-only requires default methods")
    mps = opts["min_pas_samples"]
    need(mps == "auto" or type(mps) is int and 1 <= mps <= 2**31-1, "min_pas_samples must be auto or positive integer")
    contrast = doc.get("contrast")
    if contrast is not None:
        exact(contrast, ("control", "case"))
        for k, v in contrast.items(): text(v, "contrast." + k)
        need(contrast["case"] != contrast["control"], "Contrast labels must differ")
    runtime = doc.get("runtime")
    if runtime is not None:
        exact(runtime, ("rscript", "r_library"))
        runtime = {k: absolute(v) for k, v in runtime.items()}
    need(contrast is None or runtime is not None, "Explicit R runtime required for a contrast")
    return dict(schema=doc["schema"], inputs=inputs, cell_type=text(doc["cell_type"], "cell_type"),
                provenance=doc["provenance"], contrast=contrast, options=opts, runtime=runtime)


def records(request):
    paths = set(request["inputs"].values())
    manifest = Path(request["inputs"]["manifest"])
    rows = list(table(manifest, ("sample_id", "counts_path", "annotation_path")))
    need(0 < len(rows) <= MAX_SAMPLES, "Manifest must contain 1..128 libraries")
    for row in rows:
        for k in ("counts_path", "annotation_path"):
            text(row[k], k)
            paths.add(str((manifest.parent / row[k]).resolve()))
    total = 0
    for p in paths:
        need(Path(p).is_file(), "Input file missing: " + p)
        total += Path(p).stat().st_size
    need(total <= MAX_INPUT_BYTES, "Raw input bytes exceed 2 GiB")
    return {p: file_record(p) for p in sorted(paths)}


def sources():
    import scpolya_diff
    root = Path(scpolya_diff.__file__).parent
    files = [Path(__file__), *sorted(root.glob("*.py")), *sorted(root.glob("*.R"))]
    # Names, rather than absolute source roots, allow a byte-identical installed adapter.
    return {p.parent.name + "/" + p.name: dict(sha256=sha256(p), bytes=p.stat().st_size) for p in files}


def verify(recs):
    for p, r in recs.items():
        need(file_record(p) == r, "Hash/size/path mismatch: " + p)


def rank(rows):
    """Exact rank of the small categorical design; no fitted statistical model."""
    basis = {}
    for row in rows:
        v = list(map(Fraction, row))
        for pivot, b in sorted(basis.items()):
            if v[pivot]:
                factor = v[pivot]
                v = [a - factor*c for a, c in zip(v, b)]
        pivot = next((i for i, a in enumerate(v) if a), None)
        if pivot is not None:
            factor = v[pivot]
            basis[pivot] = [a/factor for a in v]
    return len(basis)


def design(rows, terms, case):
    levels = {t: sorted({r[t] for r in rows})[1:] for t in terms}
    return [[1, *[int(r[t] == level) for t in terms for level in levels[t]], int(r["condition"] == case)] for r in rows]


def eligibility(request, prepared):
    meta = []
    for n, row in enumerate(table(request["inputs"]["metadata"]), 1):
        need(n <= MAX_METADATA_ROWS, "Metadata exceeds 200000 rows")
        if row.get("cell_type") == request["cell_type"]: meta.append(row)
    observed = sorted({r["condition"] for r in meta})
    samples = list(table(prepared / "samples.tsv"))
    result = dict(observed_conditions=observed, metadata_cells=len(meta), eligible_pseudobulks=len(samples),
                  independent_donors=len({r["donor_id"] for r in samples}),
                  inference_eligible=False, mode="eligibility_only", reason="no_contrast_requested",
                  excluded_pseudobulks=sum(1 for _ in table(prepared / "excluded_pseudobulks.tsv")))
    contrast = request["contrast"]
    if contrast is None: return result
    need(set(contrast.values()) <= set(observed), "Requested condition absent from actual metadata")
    rows = [r for r in samples if r["condition"] in contrast.values()]
    opts = request["options"]
    if opts["paired"]:
        complete = {r["donor_id"] for r in rows if {x["condition"] for x in rows if x["donor_id"] == r["donor_id"]} == set(contrast.values())}
        result["incomplete_donors_excluded"] = len({r["donor_id"] for r in rows} - complete)
        rows = [r for r in rows if r["donor_id"] in complete]
    else:
        need(len({r["donor_id"] for r in rows}) == len(rows), "Donor occurs in both groups; paired=true required")
    groups = {label: len({r["donor_id"] for r in rows if r["condition"] == label}) for label in contrast.values()}
    result.update(donors_per_condition=groups, selected_pseudobulks=len(rows))
    descriptive = opts["descriptive_only"] or min(groups.values()) < 2
    reason = "insufficient_replicates" if min(groups.values()) < 2 else "explicit_descriptive_only"
    if not descriptive:
        terms = ["donor_id"] if opts["paired"] else []
        if opts["batch"]:
            need(all(r["batch"] for r in rows) and len({r["batch"] for r in rows}) >= 2, "Batch requires >=2 nonempty levels")
            terms.append("batch")
        matrix = design(rows, terms, contrast["case"])
        need(rank(matrix) == len(matrix[0]) and len(matrix) > len(matrix[0]), "Design confounded or no residual degrees of freedom")
        batches = {r["batch"] for r in rows}
        if all(batches) and len(batches) >= 2 and (opts["batch"] or len(batches) != len(rows)):
            audit = design(rows, (["donor_id"] if opts["paired"] else []) + ["batch"], contrast["case"])
            need(rank(audit) > rank([r[:-1] for r in audit]), "Condition confounded with recorded donor/batch")
        result.update(design_rank=rank(matrix), design_columns=len(matrix[0]), residual_df=len(matrix)-len(matrix[0]))
    # Same declared coverage gates; report counts only, never p-values/effects.
    selected = [r["sample_id"] for r in rows]
    supported = collections.Counter()
    totals = collections.defaultdict(lambda: [0]*len(selected))
    threshold = max(1, min(groups.values())) if opts["min_pas_samples"] == "auto" else opts["min_pas_samples"]
    need(not selected or threshold <= len(selected), "min_pas_samples exceeds selected pseudobulks")
    for row in table(prepared / "pas_counts.tsv"):
        values = [int(row[s]) for s in selected]
        if selected and sum(v >= opts["min_pas_count"] for v in values) >= threshold:
            supported[row["unit_id"]] += 1
            totals[row["unit_id"]] = [a+b for a, b in zip(totals[row["unit_id"]], values)]
    testable = sum(n >= 2 and all(v >= opts["min_unit_count"] for v in totals[u]) for u, n in supported.items())
    result.update(mode="descriptive" if descriptive else "inference_candidate", reason=reason if descriptive else "sufficient_replicates",
                  inference_eligible=not descriptive and testable > 0, units_after_declared_coverage_gates=testable,
                  endpoint_testability="Length availability/moderation and actual inference remain backend decisions.")
    if not descriptive and not testable: result.update(mode="blocked", reason="no_units_after_filtering")
    return result


def runtime_binding(runtime, methods):
    need(Path(runtime["rscript"]).is_file() and os.access(runtime["rscript"], os.X_OK), "Rscript executable missing")
    need(Path(runtime["r_library"]).is_dir(), "R library directory missing")
    packages = sorted(set(((["edgeR", "limma", "statmod"] if "edger" in methods else []) +
                           (["limma", "statmod"] if "limma" in methods else []) +
                           (["DRIMSeq", "BiocParallel"] if "drimseq" in methods else []))))
    code = '''a<-commandArgs(TRUE); .libPaths(c(a[1],.libPaths()));
cat("R\\t",as.character(getRversion()),"\\t",R.home(),"\\n",sep="");
for(p in a[-1]) if(!requireNamespace(p,quietly=TRUE)) stop(paste("Missing R package",p));
if("edgeR" %in% a && (packageVersion("edgeR") < "4.6.0" || !exists("diffSplice.DGEGLM",asNamespace("edgeR"),inherits=FALSE))) stop("Modern edgeR >=4.6 required");
for(p in sort(loadedNamespaces())) cat(p,"\\t",as.character(packageVersion(p)),"\\t",find.package(p),"\\n",sep="")'''
    env = dict(os.environ)
    for k in ("R_PROFILE", "R_PROFILE_USER", "R_ENVIRON", "R_ENVIRON_USER", "R_LIBS"): env.pop(k, None)
    env.update(R_LIBS_USER=runtime["r_library"], OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    probe = subprocess.run([runtime["rscript"], "--vanilla", "-e", code, runtime["r_library"], *packages],
                           env=env, capture_output=True, text=True, timeout=45)
    need(probe.returncode == 0, "R runtime preflight failed: " + probe.stderr[-1500:])
    rows = list(csv.reader(io.StringIO(probe.stdout), delimiter="\t"))
    need(len(rows) > 1 and rows[0][0] == "R" and all(len(r) == 3 for r in rows), "Invalid R runtime probe")
    trees = {}
    for package, version, root in rows[1:]:
        h = hashlib.sha256(); n = 0; size = 0
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                n += 1; size += p.stat().st_size
                need(n <= 50000 and size <= 1024**3, "R package tree exceeds bound")
                h.update(str(p.relative_to(root)).encode() + b"\0" + sha256(p).encode())
        trees[package] = dict(version=version, path=root, sha256=h.hexdigest(), files=n, bytes=size)
    rhome = Path(rows[0][2])
    binaries = {str(p.resolve()): file_record(p) for p in (rhome/"bin/exec/R", rhome/"lib/libR.so") if p.is_file()}
    return dict(rscript=file_record(runtime["rscript"]), r_version=rows[0][1], r_home=str(rhome),
                r_binaries=binaries, packages=trees)


def owned(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def atomic(path, doc):
    temporary = path.with_name(path.name + ".tmp")
    write_json(temporary, doc)
    temporary.replace(path)


def plan(request_path, outdir):
    out = owned(outdir)
    doc = dict(schema=PREFIX+"plan.v1", status="FAILED", started_utc=now(), plan_dir=str(out), limits=LIMITS,
               planning_writes="Materializes existing raw-count preparation in this owned directory; no inference or approval issued.",
               bounds=dict(input_bytes=MAX_INPUT_BYTES, libraries=MAX_SAMPLES, metadata_rows=MAX_METADATA_ROWS, timeout_seconds=1200,
                           resource_contract="Caller supplies <=2 CPU, <=4 GiB, swap=0 envelope; R/BLAS threads fixed to one."))
    try:
        request = normalize(load(request_path))
        doc.update(request=request, request_file=file_record(request_path), source=sources(),
                   python=file_record(sys.executable), python_version=sys.version)
        doc["inputs"] = records(request)
        # Bound metadata before preparation, which materializes selected cells.
        for n, _ in enumerate(table(request["inputs"]["metadata"]), 1):
            need(n <= MAX_METADATA_ROWS, "Metadata exceeds 200000 rows")
        args = ["prepare"]
        for k, v in request["inputs"].items(): args += ["--"+k, v]
        args += ["--cell-type", request["cell_type"], "--outdir", str(out/"prepared")]
        for k in ("analysis_set", "min_cells"): args += ["--"+k.replace("_", "-"), str(request["options"][k])]
        if request["options"]["allow_unmatched_cells"]: args += ["--allow-unmatched-cells"]
        prep = prepare(build_parser().parse_args(args))
        doc["prepared"] = {str(out/"prepared"/name): record for name, record in prep["outputs"].items()}
        doc["prepared"][str(out/"prepared/preparation.json")] = file_record(out/"prepared/preparation.json")
        doc["eligibility"] = eligibility(request, out/"prepared")
        verify(doc["inputs"])
        need(records(request) == doc["inputs"], "Manifest path bindings changed while planning")
        if request["contrast"] is None:
            doc["status"] = "PREPARED_ELIGIBILITY_ONLY"
        elif doc["eligibility"]["mode"] == "blocked":
            doc["status"] = "BLOCKED_ELIGIBILITY"
        else:
            methods = request["options"]["methods"].split(",") if doc["eligibility"]["inference_eligible"] else []
            try:
                doc["runtime"] = runtime_binding(request["runtime"], methods)
                doc["runtime_methods"] = methods
                doc["status"] = "READY_FOR_EXTERNAL_APPROVAL"
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                doc.update(status="BLOCKED_RUNTIME", error=str(exc))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        doc.update(status="FAILED", error=str(exc))
    doc["finished_utc"] = now()
    atomic(out/"plan.json", doc)
    return dict(schema=PREFIX+"plan_result.v1", status=doc["status"], plan=file_record(out/"plan.json"),
                eligibility=doc.get("eligibility"), error=doc.get("error"), limits=LIMITS)


def execute(plan_path, approval_path=None, outdir=None, expected_plan_sha256=None):
    # approval_path is a deprecated, ignored argument kept for old callers.
    # expected_plan_sha256, when given (e.g. the hash shown to the researcher), must equal the plan file's sha256.
    # The status token READY_FOR_EXTERNAL_APPROVAL is kept for plan compatibility; it means "ready to execute".
    need(outdir is not None, "outdir is required")
    plan_path = Path(plan_path).resolve()
    need(expected_plan_sha256 is None or sha256(plan_path) == expected_plan_sha256,
         "plan.json differs from the expected plan sha256")
    doc = load(plan_path)
    need(doc.get("schema") == PREFIX+"plan.v1" and doc.get("status") == "READY_FOR_EXTERNAL_APPROVAL", "Plan is not executable")
    request = normalize(doc["request"])
    need(sources() == doc["source"] and file_record(sys.executable) == doc["python"] and sys.version == doc["python_version"], "Stale source/Python binding")
    verify(doc["inputs"]); verify(doc["prepared"])
    need(records(request) == doc["inputs"], "Stale manifest path bindings")
    need(runtime_binding(request["runtime"], doc["runtime_methods"]) == doc["runtime"], "Stale R runtime binding")
    need(str(plan_path.parent) == doc["plan_dir"], "Moved plan directory")
    out = owned(outdir)
    attempt = dict(schema=PREFIX+"attempt.v1", status="RUNNING", started_utc=now(), output_dir=str(out),
                   plan=file_record(plan_path), approval=None, issued_by=None,
                   provenance=request["provenance"], eligibility=doc["eligibility"], limits=LIMITS)
    atomic(out/"attempt.json", attempt)
    try:
        args = ["run", "--input", str(plan_path.parent/"prepared"), "--outdir", str(out/"analysis"),
                "--control", request["contrast"]["control"], "--case", request["contrast"]["case"]]
        for k in ("methods", "min_pas_count", "min_pas_samples", "min_unit_count", "seed", "timeout_seconds"):
            args += ["--"+k.replace("_", "-"), str(request["options"][k])]
        for k in ("descriptive_only", "paired", "batch"):
            if request["options"][k]: args += ["--"+k.replace("_", "-")]
        for k, v in request["runtime"].items(): args += ["--"+k.replace("_", "-"), v]
        report = _run(build_parser().parse_args(args))
        verify(doc["inputs"]); verify(doc["prepared"])
        need(sources() == doc["source"], "Source changed during execution")
        need(runtime_binding(request["runtime"], doc["runtime_methods"]) == doc["runtime"], "Runtime changed during execution")
        attempt.update(status=report["status"], backend_mode=report["mode"], inference_reason=report["inference_reason"])
    except Exception as exc:
        attempt.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
    finally:
        attempt.update(finished_utc=now(), outputs={str(p.relative_to(out)): file_record(p) for p in sorted(out.rglob("*"))
                                                   if p.is_file() and p.name != "attempt.json"})
        atomic(out/"attempt.json", attempt)
    return status(out)


def status(run_dir):
    out = Path(run_dir).resolve()
    doc = load(out/"attempt.json")
    need(doc.get("schema") == PREFIX+"attempt.v1" and doc["output_dir"] == str(out), "Invalid attempt ownership")
    need(doc["status"] in DONE | {"FAILED", "RUNNING"}, "Invalid attempt state")
    if doc["status"] != "RUNNING":
        expected = {str(out/k) for k in doc["outputs"]}
        actual = {str(p) for p in out.rglob("*") if p.is_file() and p.name != "attempt.json"}
        need(actual == expected, "Attempt output inventory changed")
        for rel, rec in doc["outputs"].items():
            need(not Path(rel).is_absolute() and ".." not in Path(rel).parts and str((out/rel).resolve()) == rec["path"], "Invalid output path")
            verify({str(out/rel): rec})
        if doc["status"] in DONE:
            report = load(out/"analysis/run.json")
            need(report.get("schema") == "scpolya.diff_apa.run.v2" and report.get("status") == doc["status"], "Backend/attempt completion mismatch")
            need("analysis/differential_apa_summary.tsv" in doc["outputs"], "Completed result lacks summary")
            for name, rec in report["outputs"].items():
                need(doc["outputs"].get("analysis/"+name) == rec, "Backend output manifest mismatch")
    return dict(schema=PREFIX+"status.v1", status=doc["status"], output_dir=str(out), verified=doc["status"] != "RUNNING",
                attempt=file_record(out/"attempt.json"), plan=doc["plan"], provenance=doc["provenance"],
                eligibility=doc["eligibility"], backend_mode=doc.get("backend_mode"), inference_reason=doc.get("inference_reason"),
                error=doc.get("error"), tables=sorted(k[9:] for k in doc.get("outputs", {}) if k.startswith("analysis/") and k.endswith(".tsv")), limits=LIMITS)


def result(run_dir, name="differential_apa_summary.tsv", offset=0, limit=20, max_bytes=65536):
    need(type(offset) is int and 0 <= offset <= 1000000 and type(limit) is int and 1 <= limit <= 1000
         and type(max_bytes) is int and 1024 <= max_bytes <= 262144, "Invalid result bounds")
    summary = status(run_dir)
    need(summary["status"] in DONE and summary["verified"], "Result requires verified backend completion")
    need(name in summary["tables"] and Path(name).name == name, "Unknown result table")
    path = Path(run_dir).resolve()/"analysis"/name
    page = dict(schema=PREFIX+"result.v1", status=summary["status"], provenance=summary["provenance"],
                backend_mode=summary["backend_mode"], inference_reason=summary["inference_reason"], table=file_record(path),
                offset=offset, rows=[], next_offset=None, limits=LIMITS)
    with open(path, newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        page["columns"] = reader.fieldnames
        need(len(json.dumps(page).encode())+100 <= max_bytes, "Result metadata exceeds byte budget")
        for i, row in enumerate(reader):
            if i < offset: continue
            need(None not in row and all(v is not None for v in row.values()), "Malformed result row")
            page["rows"].append(row)
            if len(page["rows"]) > limit or len(json.dumps(page).encode())+100 > max_bytes:
                page["rows"].pop()
                need(bool(page["rows"]), "Single row exceeds byte budget; increase max_bytes")
                page["next_offset"] = offset+len(page["rows"])
                break
    need(file_record(path) == page["table"], "Result table changed during reading")
    need(len(json.dumps(page, ensure_ascii=True).encode()) <= max_bytes, "Result exceeds byte budget")
    return page


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan"); p.add_argument("--request", required=True); p.add_argument("--outdir", required=True)
    p = sub.add_parser("execute"); p.add_argument("--plan", required=True); p.add_argument("--approval", required=False, default=None, help="deprecated and ignored"); p.add_argument("--outdir", required=True)
    p.add_argument("--plan-sha256", default=None, help="optional: refuse unless plan.json has exactly this sha256")
    for verb in ("status", "result"):
        p = sub.add_parser(verb); p.add_argument("--run", required=True)
        if verb == "result":
            p.add_argument("--table", default="differential_apa_summary.tsv"); p.add_argument("--offset", type=int, default=0)
            p.add_argument("--limit", type=int, default=20); p.add_argument("--max-bytes", type=int, default=65536)
    a = parser.parse_args(argv)
    try:
        if a.command == "plan": doc = plan(a.request, a.outdir)
        elif a.command == "execute": doc = execute(a.plan, a.approval, a.outdir, a.plan_sha256)
        elif a.command == "status": doc = status(a.run)
        else: doc = result(a.run, a.table, a.offset, a.limit, a.max_bytes)
        print(json.dumps(doc, sort_keys=True, allow_nan=False))
        return 0 if doc["status"] in DONE | {"PREPARED_ELIGIBILITY_ONLY", "READY_FOR_EXTERNAL_APPROVAL", "RUNNING"} else 2
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(schema=PREFIX+"error.v1", status="REFUSED", error=str(exc)[:2000])))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
