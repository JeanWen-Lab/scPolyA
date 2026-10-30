from __future__ import annotations

import csv
import importlib
import shutil
import sqlite3

import pandas as pd
import pysam
import pytest

from utils.assignment_diagnostics import AssignmentDiagnostics, WindowIndex, unit_diagnostics_paths

from arguments import parse_arguments
from task_manage.define import CACHE_dedup_method, CACHE_polyA_status, CACHE_unit_inputs
from task_manage.effective_config import build_annotated_peak_config
from task_manage.run_layout import FILTERED_ADJUSTED_GTF, RunLayout


def _fixture(tmp_path, *, multi_contig=True, initialize=None):
    args = parse_arguments([
        "run", "--output-dir", str(tmp_path), "--run-id", "run",
        "--species", "mouse", "--inputs", "inputs.csv",
        "--gene-gtf", "genes.gtf", "--chrom-sizes", "chrom.sizes",
        "--coverage-scaling", "none", "--merge-distance", "0",
        "--unscaled-depth-threshold", "1",
        "--unscaled-depth-threshold-polyA", "1",
    ])
    layout = RunLayout(out=tmp_path, run_id="run")
    layout.ensure_base_dirs()
    if initialize is not None:
        initialize(layout, args)
    units = tmp_path / "units.csv"
    identities = [("S", "chr1"), ("S.chr1", "chr1"), ("S.chr1.chr1", "chr1")]
    if multi_contig:
        identities.extend([("S.chr1", "chr2"), ("S", "chr1.alt"),
                           ("S.chr1", "chr1.alt"), ("S.chr1.chr1", "chr2")])
    rows = [
        {"sample_id": sid, "contig_name": ctg, "unit_id": f"{sid}.{ctg}",
         "dedup_status": "external", "resolved_path": str(tmp_path / f"{sid}.{ctg}.bam")}
        for sid, ctg in identities
    ]
    with units.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    cache = {CACHE_unit_inputs: units, CACHE_dedup_method: "directional",
             CACHE_polyA_status: "polyA-10-1-0"}
    final = build_annotated_peak_config(args, dedup_method="directional",
                                       polyA_status="polyA-10-1-0")
    layout.AnnotatedFinalOutputSub(final)
    for suffix in ["___peaks-annotated.gtf", "___peaks-annotated.bed"]:
        output = layout.ResolveAnnotatedFinalOutputFile(suffix=suffix, d=final)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            'chr1\tfixture\tpeak\t101\t200\t.\t+\t.\tpeak_name "p1";\n'
            if suffix.endswith("gtf") else
            "chr1\t100\t200\tp1\t1\t+\t1\t0\t1\tGENE\t0\ttx\t50\t150\tTerminal_exon\n"
        )
    reference = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_GTF, args=args)
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_text('chr1\tfixture\texon\t101\t200\t.\t+\t.\tgene_name "GENE"; gene_id "G1";\n')
    return args, layout, cache


@pytest.mark.parametrize("stage", ["annotate_bams", "count_matrix", "summarize_matrix", "rna_gene_matrix"])
def test_stage_dispatch_preserves_explicit_sample_id(tmp_path, monkeypatch, stage):
    args, layout, cache = _fixture(tmp_path, multi_contig=False)
    module = importlib.import_module(f"stages.{stage}")
    batches = []

    def capture_batch(**kwargs):
        batches.append((kwargs["task_base"], list(kwargs["items"])))

    monkeypatch.setattr(module, "run_flex_batch", capture_batch)
    getattr(module, stage)(None, layout, args, cache)
    for task, items in batches:
        for item in items:
            if item.uid:
                assert item.uid == f"{item.sid}.{item.ctg}", (task, item.uid, item.sid)
        if items and items[0].uid is None:
            assert {item.sid for item in items} == {"S", "S.chr1", "S.chr1.chr1"}, task


@pytest.mark.parametrize("stage", ["annotate_bams", "count_matrix", "summarize_matrix", "rna_gene_matrix"])
@pytest.mark.parametrize("sample_id", [None, "", "   "])
def test_invalid_sample_identity_is_rejected_before_dispatch(tmp_path, monkeypatch, stage, sample_id):
    args, layout, cache = _fixture(tmp_path)
    units = cache[CACHE_unit_inputs]
    fields = ["unit_id", "contig_name", "dedup_status", "resolved_path"]
    row = ["S.chr1", "chr1", "external", "S.chr1.bam"]
    if sample_id is not None:
        fields.append("sample_id")
        row.append(sample_id)
    with units.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows([fields, row])
    module = importlib.import_module(f"stages.{stage}")
    monkeypatch.setattr(module, "run_flex_batch", lambda **kw: pytest.fail("invalid identity dispatched"))
    with pytest.raises(ValueError, match="missing or empty sample_id"):
        getattr(module, stage)(None, layout, args, cache)


def test_peak_counts_do_not_mix_or_split_samples(tmp_path, monkeypatch):
    args, layout, cache = _fixture(tmp_path)
    module = importlib.import_module("stages.count_matrix")
    counts_by_unit = {"S.chr1": 1, "S.chr1.chr1": 2, "S.chr1.chr1.chr1": 6,
                      "S.chr1.chr2": 3, "S.chr1.alt": 4,
                      "S.chr1.chr1.alt": 5, "S.chr1.chr1.chr2": 7}
    observed = {}

    def execute_batch(**kwargs):
        for item in kwargs["items"]:
            if kwargs["task_base"] == "count_unit_matrix":
                final = build_annotated_peak_config(args, dedup_method="directional",
                                                   polyA_status="polyA-10-1-0")
                root = layout.CountMatrixTempConfigSub(final) / "unit_counts"
                pd.DataFrame([("p1", "cell", counts_by_unit[item.uid])],
                             columns=["gene", "cell", "count"]).to_csv(
                    root / item.format_name(suffix="peak-counts.tsv.gz"),
                    sep="\t", index=False,
                )
                # annotate_bams is not run here: write the (empty) unit
                # assignment diagnostics it would leave beside the sidecar.
                sidecar = layout.ResolvePeakSidecarFile(
                    file_name=item.format_name(suffix="xr_to_peak.tsv.gz"), d=final,
                )
                sidecar.parent.mkdir(parents=True, exist_ok=True)
                AssignmentDiagnostics(WindowIndex({}), read_rule=None, minpolyA=10, nonA_allowed=1).write(
                    *unit_diagnostics_paths(sidecar), {},
                )
            else:
                kwargs["fn"](item)
                observed[item.sid] = item.format_name(suffix="counts.tsv.gz", is_annotated_bam=True)

    monkeypatch.setattr(module, "run_flex_batch", execute_batch)
    module.count_matrix(None, layout, args, cache)
    final = build_annotated_peak_config(args, dedup_method="directional",
                                       polyA_status="polyA-10-1-0")
    totals = {sid: int(pd.read_csv(layout.CountMatrixConfigSub(final) / name,
                                  sep="\t")["umi_count"].sum())
              for sid, name in observed.items()}
    assert totals == {"S": 5, "S.chr1": 10, "S.chr1.chr1": 13}
    assert sum(totals.values()) == sum(counts_by_unit.values())


@pytest.mark.skipif(shutil.which("featureCounts") is None, reason="featureCounts executable required")
def test_real_bam_sidecar_counts_and_both_gene_summaries_preserve_samples(tmp_path, monkeypatch):
    args, layout, cache = _fixture(tmp_path, multi_contig=False)
    expected = {"S": 1, "S.chr1": 2, "S.chr1.chr1": 6}
    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "chr1", "LN": 1000}]}
    for sid, count in expected.items():
        with pysam.AlignmentFile(str(tmp_path / f"{sid}.chr1.bam"), "wb", header=header) as out:
            for i, umi in enumerate(["AAAA", "CCCC", "GGGG", "TTTT", "ACGT", "TGCA"][:count]):
                read = pysam.AlignedSegment(out.header)
                read.query_name = f"{sid}-{i}"
                read.query_sequence = "A" * 20
                read.flag = 0
                read.reference_id = 0
                read.reference_start = 110
                read.mapping_quality = 60
                read.cigarstring = "20M"
                read.set_tags([("CB", "cell"), ("UB", umi), ("NH", 1)])
                out.write(read)
        pysam.index(str(tmp_path / f"{sid}.chr1.bam"))

    def execute_batch(**kwargs):
        for item in kwargs["items"]:
            kwargs["fn"](item)

    for stage in ["annotate_bams", "count_matrix", "summarize_matrix", "rna_gene_matrix"]:
        module = importlib.import_module(f"stages.{stage}")
        monkeypatch.setattr(module, "run_flex_batch", execute_batch)
        getattr(module, stage)(None, layout, args, cache)

    final = build_annotated_peak_config(args, dedup_method="directional", polyA_status="polyA-10-1-0")
    from task_manage import ScItem
    from task_manage.effective_config import build_rna_gene_matrix_config
    rna_config = build_rna_gene_matrix_config(args, dedup_method="directional")
    totals = {}
    for sid, count in expected.items():
        item = ScItem(sid=sid, dedup_status="external", args=args)
        paths = [
            layout.CountMatrixConfigSub(final) / item.format_name(suffix="counts.tsv.gz", is_annotated_bam=True),
            layout.MatrixSummaryConfigSub(final, psi_min_gene_umis=args.psi_min_gene_umis)
            / item.format_name(suffix="gene_cell.tsv.gz", is_annotated_bam=True),
            layout.RnaGeneMatrixSub(rna_config) / item.format_name(suffix="rna_gene_cell.tsv.gz"),
        ]
        for path in paths:
            frame = pd.read_csv(path, sep="\t")
            column = "umi_count" if "umi_count" in frame else "gene_umi_count"
            assert frame[column].tolist() == [count], path
        totals[sid] = count
    assert sum(totals.values()) == 9


@pytest.mark.parametrize("authority", ["legacy", "v2"])
@pytest.mark.parametrize("failure_point", ["before_batch", "second_sample"])
def test_real_taskmanager_recovers_pending_metadata_invalidation(
    tmp_path, monkeypatch, authority, failure_point,
):
    from task_manage import Plan, ScItem, TaskManager
    monkeypatch.setenv("SCPOLYA_REUSE_V2_AUTHORITY", authority)
    samples = ["S", "S.chr1", "S.chr1.chr1"]
    runtime = {}

    def initialize(layout, args):
        args.n_workers = 1
        tm = TaskManager(state_dir=layout.State, config=vars(args))
        tm.load_or_init(Plan(samples=samples, contigs_by_sample={sid: ["chr1"] for sid in samples}))
        runtime["tm"] = tm

    args, layout, cache = _fixture(tmp_path, multi_contig=False, initialize=initialize)
    module = importlib.import_module("stages.summarize_matrix")
    final = build_annotated_peak_config(args, dedup_method="directional", polyA_status="polyA-10-1-0")
    for sid in samples:
        item = ScItem(sid=sid, dedup_status="external", args=args)
        pd.DataFrame([("p1", "cell", 8)], columns=["peak_name", "cell_barcode", "umi_count"]).to_csv(
            layout.CountMatrixConfigSub(final) / item.format_name(suffix="counts.tsv.gz", is_annotated_bam=True),
            sep="\t", index=False,
        )
    root = layout.MatrixSummaryConfigSub(final, psi_min_gene_umis=args.psi_min_gene_umis)
    original_batch = module.run_flex_batch
    forces = []

    def observed_batch(**kwargs):
        forces.append(kwargs["force"])
        return original_batch(**kwargs)

    monkeypatch.setattr(module, "run_flex_batch", observed_batch)
    module.summarize_matrix(runtime["tm"], layout, args, cache)
    metadata = next(root.glob("*peak_to_gene*.tsv.gz"))
    mtime = metadata.stat().st_mtime_ns
    module.summarize_matrix(runtime["tm"], layout, args, cache)
    assert forces == [True, False]
    assert metadata.stat().st_mtime_ns == mtime
    bed = layout.ResolveAnnotatedFinalOutputFile(suffix="___peaks-annotated.bed", d=final)
    bed.write_text(bed.read_text().replace("GENE", "UPDATED"))

    def interrupted_batch(**kwargs):
        assert kwargs["force"] is True
        assert pd.read_csv(metadata, sep="\t")["gene_name"].tolist() == ["UPDATED"]
        if failure_point == "before_batch":
            raise RuntimeError("injected interruption before batch")
        worker = kwargs["fn"]

        def interrupted_worker(item):
            if item.sid == "S.chr1":
                raise RuntimeError("injected second sample failure")
            return worker(item)

        kwargs["fn"] = interrupted_worker
        return original_batch(**kwargs)

    monkeypatch.setattr(module, "run_flex_batch", interrupted_batch)
    # TaskManager wraps worker failures in its existing generic exception type.
    with pytest.raises(Exception, match="injected"):
        module.summarize_matrix(runtime["tm"], layout, args, cache)
    pending = root / ".peak-metadata.pending.json"
    assert pending.exists()
    if failure_point == "second_sample":
        item = ScItem(sid="S", dedup_status="external", args=args)
        completed = root / item.format_name(suffix="gene_cell.tsv.gz", is_annotated_bam=True)
        assert pd.read_csv(completed, sep="\t")["gene_name"].tolist() == ["UPDATED"]
    with sqlite3.connect(runtime["tm"].db_path) as con:
        assert con.execute("SELECT count(*) FROM task_done_coordinate_v2 WHERE stage='summarize_matrix'").fetchone()[0] == 3
        before_decisions = con.execute("SELECT max(decision_id) FROM task_reuse_decisions_v3").fetchone()[0]

    initialize(layout, args)  # A new real TaskManager; retain all old DONE rows.
    monkeypatch.setattr(module, "run_flex_batch", observed_batch)
    module.summarize_matrix(runtime["tm"], layout, args, cache)
    assert forces[-1] is True
    assert not pending.exists()
    for sid in samples:
        item = ScItem(sid=sid, dedup_status="external", args=args)
        path = root / item.format_name(suffix="gene_cell.tsv.gz", is_annotated_bam=True)
        assert pd.read_csv(path, sep="\t")["gene_name"].tolist() == ["UPDATED"]
    with sqlite3.connect(runtime["tm"].db_path) as con:
        resumed = con.execute(
            "SELECT force, queued FROM task_reuse_decisions_v3 WHERE stage='summarize_matrix' AND decision_id>?",
            (before_decisions,),
        ).fetchall()
    assert resumed == [(1, 1)] * 3
