"""Installable scpolya-diff-apa CLI, independent of BAM/GUI dependencies."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import __version__
from .common import file_record, new_output, now, sha256, table, write_json
from .prepare import prepare


def build_parser():
    p = argparse.ArgumentParser(prog="scpolya-diff-apa", description="Donor-level differential APA: one cell type and explicit case minus control. No differential iPUI.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="Validate per-sample common catalogues and aggregate raw PAS UMIs")
    prep.add_argument("--manifest", required=True, help="TSV: sample_id, counts_path, annotation_path (relative to manifest)")
    prep.add_argument("--metadata", required=True, help="TSV: sample_id,cell_barcode,pseudobulk_id,donor_id,condition,cell_type[,batch]")
    prep.add_argument("--annotation", required=True, help="Common native PAS annotation TSV[.gz]; compare every per-sample annotation")
    prep.add_argument("--cell-type", required=True)
    prep.add_argument("--analysis-set", default="native-terminal", choices=["native-terminal", "terminal-only"], help="Explicit denominator: native Terminal_exon+Downstream_of_terminal_exon, or Terminal_exon only; zero lengths retained for composition")
    prep.add_argument("--min-cells", type=int, default=20, help="Minimum count-record-matched cells per biological pseudobulk (default 20)")
    prep.add_argument("--allow-unmatched-cells", action="store_true", help="Explicitly exclude unmatched selected cells; save their identities, never certify absent cells as zero")
    prep.add_argument("--outdir", required=True, help="New or empty output directory; failures are preserved")
    run = sub.add_parser("run", help="Run selected endpoints or explicit descriptive-only analysis")
    run.add_argument("--input", required=True, help="Successful, unmodified prepare directory")
    run.add_argument("--outdir", required=True, help="New or empty output directory; failures are preserved")
    run.add_argument("--control", required=True); run.add_argument("--case", required=True)
    run.add_argument("--methods", default="edger,limma", help="Default edger,limma: primary composition + separate length endpoint; drimseq is explicit sensitivity; families remain separate")
    run.add_argument("--descriptive-only", action="store_true", help="Force no p-values/FDR; insufficient donors also trigger descriptive-only automatically")
    run.add_argument("--paired", action="store_true", help="Exclude incomplete donors; infer with donor+group only for >=2 complete pairs")
    run.add_argument("--batch", action="store_true", help="Include nonempty batch factor; refuse rank deficiency")
    run.add_argument("--min-pas-count", type=int, default=5)
    run.add_argument("--min-pas-samples", default="auto", help="Samples with PAS count >= min; auto = smaller group size")
    run.add_argument("--min-unit-count", type=int, default=20, help="Retained-PAS total required in EVERY selected pseudobulk")
    run.add_argument("--seed", type=int, default=20261021)
    run.add_argument("--rscript", default="Rscript", help="Rscript executable (called with --vanilla)")
    run.add_argument("--r-library", default="", help="Task-specific R package library, prepended explicitly inside vanilla R")
    run.add_argument("--timeout-seconds", type=int, default=3600)
    return p


def _run(a):
    if a.control == a.case:
        raise ValueError("--control and --case must differ")
    methods = a.methods.split(",")
    if not methods or len(methods)!=len(set(methods)) or set(methods)-{"edger","limma","drimseq"}:
        raise ValueError("--methods must be unique selections from edger,limma,drimseq")
    if a.descriptive_only and a.methods != "edger,limma":
        raise ValueError("--descriptive-only does not accept additional --methods")
    if any(x<1 or x>2**31-1 for x in (a.min_pas_count,a.min_unit_count,a.seed,a.timeout_seconds)):
        raise ValueError("Count/seed/timeout parameters must be positive integers <= R integer max")
    if a.min_pas_samples!="auto" and (not a.min_pas_samples.isdigit() or int(a.min_pas_samples)<1):
        raise ValueError("--min-pas-samples must be auto or a positive integer")
    inp = Path(a.input).resolve()
    prep = json.loads((inp/"preparation.json").read_text())
    if prep.get("schema")!="scpolya.diff_apa.preparation.v2" or prep.get("status")!="PREPARED":
        raise ValueError("Input requires a successful native differential-APA preparation")
    expected = {"pas_counts.tsv","pas_annotation.tsv","native_pas_counts.tsv","native_pas_annotation.tsv","samples.tsv","catalogue_selection.tsv","unmatched_cells.tsv","excluded_pseudobulks.tsv"}
    if set(prep.get("outputs",{}))!=expected:
        raise ValueError("Preparation output manifest has missing/unexpected files")
    for name, rec in prep["outputs"].items():
        if sha256(inp/name)!=rec["sha256"]:
            raise ValueError(f"Prepared file hash mismatch: {name}; re-prepare rather than editing intermediate files")
    exe = shutil.which(a.rscript)
    if not exe:
        raise ValueError(f"Rscript executable not found: {a.rscript}")
    library = str(Path(a.r_library).resolve()) if a.r_library else ""
    if library and not Path(library).is_dir():
        raise ValueError("--r-library directory does not exist")
    out = new_output(a.outdir)
    here = Path(__file__).resolve().parent
    report = {"schema":"scpolya.diff_apa.run.v2", "status":"RUNNING", "started_utc":now(),
              "arguments":vars(a), "module_version":__version__, "python":sys.version,
              "preparation":file_record(inp/"preparation.json"), "catalogue_fingerprint":prep["catalogue_fingerprint"],
              "analysis_set":prep["analysis_set"], "input_files":{n:file_record(inp/n) for n in sorted(expected)},
              "source":{p.name:file_record(p) for p in sorted(here.iterdir()) if p.suffix in (".py",".R")},
              "rscript":file_record(exe), "r_library":library,
              "primary_composition":"edger" if not a.descriptive_only and "edger" in methods else None,
              "hypothesis_family":"Unit-level BH independently within each selected method for one celltype and one contrast; no union; no multi-contrast or multi-celltype correction.",
              "effect_definition":"Mean of donor-pseudobulk proportions/lengths; case minus control; not pooled-across-donor counts or fitted batch-adjusted effect.",
              "limits":["Gene+strand does not resolve alternative terminal exons.","No differential iPUI.","Per-sample catalogue provenance is supplied by the analyst; fingerprints check consistency, not upstream quantification history.","DRIMSeq finite outputs do not prove optimization convergence."]}
    backend = here/"backend.R"
    options = {"input":str(inp),"outdir":str(out),"control":a.control,"case":a.case,"methods":a.methods,
               "paired":str(a.paired).lower(),"batch":str(a.batch).lower(),"descriptive":str(a.descriptive_only).lower(),
               "min_pas_count":str(a.min_pas_count),"min_pas_samples":a.min_pas_samples,"min_unit_count":str(a.min_unit_count),
               "seed":str(a.seed),"r_library":library}
    cmd = [exe,"--vanilla",str(backend)]
    for k,v in options.items(): cmd.extend(["--"+k,v])
    report["argv"] = cmd
    write_json(out/"run.json",report)
    env = dict(os.environ)
    for key in ("R_PROFILE","R_PROFILE_USER","R_ENVIRON","R_ENVIRON_USER"):
        env.pop(key,None)
    env.update(OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1",MKL_NUM_THREADS="1",VECLIB_MAXIMUM_THREADS="1")
    # Avoid ambient user libraries even under --vanilla; task library is explicit.
    env["R_LIBS_USER"] = library or str(out/".nonexistent-user-rlibrary")
    env.pop("R_LIBS",None)
    try:
        with open(out/"statistics.stdout.log","w") as stdout, open(out/"statistics.stderr.log","w") as stderr:
            result = subprocess.run(cmd,env=env,stdout=stdout,stderr=stderr,timeout=a.timeout_seconds,check=False)
        report["returncode"] = result.returncode
        status_rows = list(table(out/"backend_status.tsv")) if (out/"backend_status.tsv").is_file() else []
        if len(status_rows)!=1 or result.returncode!=0:
            raise ValueError(status_rows[0].get("error") if status_rows else "R execution failed without completion status; inspect statistics.stderr.log")
        status = status_rows[0]["status"]
        allowed = {"DESCRIPTIVE_ONLY","COMPLETE","COMPLETE_WITH_WARNINGS","COMPLETE_WITH_UNTESTABLE_LENGTH_UNITS"}
        if status not in allowed:
            raise ValueError(f"Invalid backend completion: {status}")
        if not (out/"differential_apa_summary.tsv").is_file():
            raise ValueError("R completion lacks summary")
        report.update(status=status, backend=status_rows[0], mode=status_rows[0]["mode"], inference_reason=status_rows[0]["reason"])
        if status=="DESCRIPTIVE_ONLY": report["primary_composition"]=None
        # Protect provenance against input modification while R was executing.
        for n, rec in report["input_files"].items():
            if sha256(inp/n)!=rec["sha256"]:
                raise ValueError(f"Prepared input changed during execution: {n}")
    except Exception as exc:
        report.update(status="FAILED",error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["finished_utc"] = now()
        report["outputs"] = {p.name:file_record(p) for p in sorted(out.iterdir()) if p.is_file() and p.name!="run.json"}
        write_json(out/"run.json",report)
    return report


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        result = prepare(a) if a.command=="prepare" else _run(a)
        print(json.dumps({"status":result["status"],"outdir":str(Path(a.outdir).resolve())}))
        return 0
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as exc:
        print(f"scpolya-diff-apa: {exc}",file=sys.stderr)
        return 2
