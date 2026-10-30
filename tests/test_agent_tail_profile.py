"""Synthetic-BAM tests for the pre-dedup tail profile and native-rule agreement."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pysam
import pytest

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("tail_profile_under_test", ROOT / "scpolya_agent/tail_profile.py")
tp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tp)
from utils.polya_extract import extract_polya_bam  # noqa: E402

HEADER = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "c1", "LN": 100000}, {"SN": "c2", "LN": 100000}]}
GENOME = "ACGTTGCA" * 4  # 32 aligned bases with no tail meaning
TAGS = (("CB", "AAAC-1"), ("UB", "GGGG"))


def rec(name, cigar, seq, flag=0, mapq=60, tags=TAGS, ref=0):
    return dict(name=name, cigar=cigar, seq=seq, flag=flag, mapq=mapq, tags=tags, ref=ref)


def fwd(name, clip, **kw):
    return rec(name, f"32M{len(clip)}S" if clip else "32M", GENOME + clip, **kw)


def rev(name, clip, **kw):
    return rec(name, f"{len(clip)}S32M" if clip else "32M", clip + GENOME, flag=16 | kw.pop("flag", 0), **kw)


def write_bam(path, records):
    with pysam.AlignmentFile(str(path), "wb", header=HEADER) as out:
        for i, r in enumerate(sorted(records, key=lambda r: r["ref"])):
            a = pysam.AlignedSegment()
            a.query_name, a.flag = r["name"], r["flag"]
            a.reference_id, a.reference_start = r["ref"], 100 + i
            a.mapping_quality = r["mapq"]
            if not r["flag"] & 4:
                a.cigarstring = r["cigar"]
            if r["seq"] is not None:
                a.query_sequence = r["seq"]
            for tag, value in r["tags"]:
                a.set_tag(tag, value)
            out.write(a)
    pysam.index(str(path))
    return path


def request(bam, values=(5, 10), k=1, q=10, **extra):
    return dict(schema=tp.REQUEST_SCHEMA, bam=str(bam), minpolyA_values=list(values), nonA_allowed=k,
                minMAPQ=q, max_records=1000, sampling={"policy": "head"}, **extra)


def classes(result, m, universe="upstream_gated_pre_dedup"):
    return result["universes"][universe]["per_minpolyA"][str(m)]["classes"]


# ------------------------------------------------------------- pure rule

@pytest.mark.parametrize("clip,m,k,expected", [
    ("A" * 8, 5, 1, "retained"),
    ("A" * 8, 10, 1, "short_clip"),
    ("AAAAG", 5, 1, "low_A"),            # clip long enough, A count 4 < m
    ("AAAAAGAAA", 5, 1, "retained"),     # one non-A anywhere tolerated
    ("AAAAAGAAA", 5, 0, "too_many_nonA"),
    ("AAAANAAAA", 5, 0, "too_many_nonA"),  # N is non-A
    ("aaaaaaa", 5, 0, "retained"),       # case-insensitive
    ("A" * 12 + "CTGACT", 10, 1, "too_many_nonA"),  # adapter after the run
    ("GAGAGAGAGA", 5, 5, "retained"),    # not a contiguous run: 5 A interleaved
])
def test_classify_forward(clip, m, k, expected):
    seq = GENOME + clip
    size, a = tp.tail_clip_features(False, seq, 0, 32, len(seq))
    assert size == len(clip)
    assert tp.classify_tail(size, a, m, k) == expected


def test_reverse_counts_leading_T_in_stored_orientation():
    seq = "T" * 8 + GENOME
    assert tp.classify_tail(*tp.tail_clip_features(True, seq, 8, 40, 40), 5, 1) == "retained"
    seq = "A" * 8 + GENOME  # A on the reverse-strand leading clip is not a tail
    assert tp.classify_tail(*tp.tail_clip_features(True, seq, 8, 40, 40), 5, 1) == "low_A"
    seq = GENOME + "T" * 8  # trailing clip is the wrong end for a reverse record
    assert tp.classify_tail(*tp.tail_clip_features(True, seq, 0, 32, 40), 5, 1) == "short_clip"


def test_missing_sequence_native_outcomes():
    assert tp.tail_clip_features(False, None, 0, 32, 0) is None
    assert tp.native_outcome_without_sequence(False, 0, 32, 0, 5) == "excluded_reads_polyA"
    assert tp.native_outcome_without_sequence(True, 8, 40, 0, 5) == "raises"
    assert tp.native_outcome_without_sequence(True, 3, 35, 0, 5) == "excluded_reads_polyA"


# ------------------------------------------------------------- agreement with native

def native_fixture():
    """Records native extract_polya_bam could see after umi_tools (all tagged, SEQ present)."""
    return [
        fwd("f8", "A" * 8), fwd("f12", "A" * 12), fwd("f12g", "A" * 6 + "G" + "A" * 6),
        fwd("f12gg", "A" * 6 + "GG" + "A" * 6), fwd("f4", "AAAA"), fwd("f_none", ""),
        fwd("f_adapt", "A" * 12 + "CTGA"), fwd("f_low", "AAAAGGGGGG"),
        rev("r8", "T" * 8), rev("r11", "T" * 11), rev("r_wrong", "A" * 11), rev("r_n", "TTTTTNTTTTT"),
        fwd("q9", "A" * 12, mapq=9), fwd("q10", "A" * 12, mapq=10), fwd("q255", "A" * 12, mapq=255),
        fwd("sec", "A" * 12, flag=256), rev("sup", "T" * 12, flag=2048),
        rec("hard", "32M12H", GENOME), rec("unm", None, "A" * 40, flag=4, mapq=0),
        fwd("c2", "A" * 12, ref=1),
    ]


@pytest.mark.parametrize("m", [5, 10])
@pytest.mark.parametrize("k", [0, 1])
def test_agrees_with_actual_extractor(tmp_path, m, k):
    bam = write_bam(tmp_path / "in.bam", native_fixture())
    stats = extract_polya_bam(bam, tmp_path / "out.bam", k, m, 10)
    with pysam.AlignmentFile(str(tmp_path / "out.bam")) as out:
        native_names = sorted(r.query_name for r in out)
    result = tp.profile_bam(request(bam, values=(m,), k=k))
    eq = result["universes"]["upstream_gated_pre_dedup"]["per_minpolyA"][str(m)]["native_counter_mapping"]
    assert eq == {"retained_reads": stats.retained_reads, "excluded_reads_polyA": stats.excluded_reads_polyA,
                  "excluded_reads_nonA": stats.excluded_reads_nonA, "native_would_raise_records": 0}
    assert result["waterfall"]["totals"]["examined_records"] == stats.total_reads
    profiled = sorted(read.query_name for read in pysam.AlignmentFile(str(bam))
                      if tp.gate(read, "CB", "UB", 10) is None and tp.classify_tail(
                          *tp.tail_clip_features(read.is_reverse, read.query_sequence, read.query_alignment_start,
                                                 read.query_alignment_end, read.query_length), m, k) == "retained")
    assert profiled == native_names
    assert {"sec", "sup", "q10", "q255"} <= set(native_names) and "q9" not in native_names


def test_native_raises_on_reverse_record_without_sequence(tmp_path):
    bam = write_bam(tmp_path / "in.bam", [rev("r", "T" * 8), rec("rs", "8S32M", None, flag=16 | 256)])
    with pytest.raises(TypeError):
        extract_polya_bam(bam, tmp_path / "out.bam", 1, 5, 10)
    result = tp.profile_bam(request(bam, values=(5,)))
    u = result["universes"]["upstream_gated_pre_dedup"]
    assert u["sequence_unavailable"] == 1 and u["tail_classifiable_records"] == 1
    assert u["per_minpolyA"]["5"]["native_counter_mapping"]["native_would_raise_records"] == 1
    assert result["universes"]["primary_only"]["sequence_unavailable"] == 0


def test_forward_record_without_sequence_matches_native_polyA_exclusion(tmp_path):
    bam = write_bam(tmp_path / "in.bam", [fwd("f", "A" * 8), rec("fs", "32M8S", None, flag=256)])
    stats = extract_polya_bam(bam, tmp_path / "out.bam", 1, 5, 10)
    eq = tp.profile_bam(request(bam, values=(5,)))["universes"]["upstream_gated_pre_dedup"]["per_minpolyA"]["5"]
    assert eq["native_counter_mapping"]["excluded_reads_polyA"] == stats.excluded_reads_polyA == 1
    assert eq["classes"]["retained"] == stats.retained_reads == 1


# ------------------------------------------------------------- waterfall, universes, thresholds

def test_waterfall_is_ordered_and_disjoint(tmp_path):
    records = [
        rec("unm", None, "A" * 40, flag=4, mapq=0, tags=()),      # unmapped wins over missing tags
        fwd("nocb", "A" * 12, tags=(("UB", "G"),)),
        fwd("noboth", "A" * 12, tags=(), mapq=0),                  # cell tag wins over MAPQ
        fwd("noub", "A" * 12, tags=(("CB", "X"),)),
        fwd("lowq", "A" * 12, mapq=3),
        fwd("ok", "A" * 12), fwd("emptycb", "A" * 12, tags=(("CB", ""), ("UB", "G"))),
        fwd("r2", "A" * 12, flag=1 | 128, tags=()),             # read2 wins over everything (umi_tools)
    ]
    w = tp.profile_bam(request(write_bam(tmp_path / "in.bam", records)))["waterfall"]
    assert w["totals"] == {"examined_records": 8, "sampled_records": 8}
    assert w["partition"] == {"not_sampled": 0, "read2_flag": 1, "unmapped": 1, "missing_cell_tag": 2,
                              "missing_umi_tag": 1, "mapq_below_min": 1, "tail_stage_records": 2}
    assert list(w["partition"]) == w["partition_order"]


def test_thresholds_share_one_universe_and_joint_table_recomputes(tmp_path):
    bam = write_bam(tmp_path / "in.bam", native_fixture())
    result = tp.profile_bam(request(bam, values=(10, 5, 8)))
    u = result["universes"]["upstream_gated_pre_dedup"]
    assert result["parameters"]["minpolyA_values"] == [5, 8, 10]
    denominators = {v["retained_fraction"]["denominator"] for v in u["per_minpolyA"].values()}
    assert denominators == {u["tail_classifiable_records"]}
    retained = [u["per_minpolyA"][str(m)]["classes"]["retained"] for m in (5, 8, 10)]
    assert retained == sorted(retained, reverse=True)
    rows = u["joint_clip_A"]["rows"]
    assert sum(n for *_, n in rows) == u["tail_classifiable_records"]
    for m in (5, 8, 10):  # independent recomputation from the exported joint table
        assert recompute(rows, m, 1) == u["per_minpolyA"][str(m)]["classes"]["retained"]


def recompute(rows, m, k):
    return sum(n for clip, a, n in rows if clip >= m and a >= m and clip - a <= k)


def test_joint_table_is_exact_for_long_clips(tmp_path):
    """Review counterexample: clip 200 with 100 A at (m=120, k=100) is low_A, not retained."""
    clip = ("AG" * 100)
    bam = write_bam(tmp_path / "in.bam", [rec("long", "32M200S", GENOME + clip)])
    result = tp.profile_bam(request(bam, values=(120,), k=100))
    u = result["universes"]["upstream_gated_pre_dedup"]
    assert u["per_minpolyA"]["120"]["classes"]["low_A"] == 1
    assert u["joint_clip_A"]["rows"] == [[200, 100, 1]]
    assert recompute(u["joint_clip_A"]["rows"], 120, 100) == 0
    assert recompute(u["joint_clip_A"]["rows"], 100, 100) == 1


def test_joint_table_withheld_beyond_row_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "MAX_JOINT_ROWS", 2)
    records = [fwd(f"x{i}", "A" * (5 + i)) for i in range(4)]
    u = tp.profile_bam(request(write_bam(tmp_path / "in.bam", records)))["universes"]["primary_only"]
    assert u["joint_clip_A"] is None and u["joint_clip_A_withheld_reason"]
    assert u["per_minpolyA"]["5"]["classes"]["retained"] == 4  # direct classes unaffected


def test_primary_only_removes_secondary_and_supplementary(tmp_path):
    bam = write_bam(tmp_path / "in.bam", native_fixture())
    result = tp.profile_bam(request(bam, values=(5,)))
    native, primary = (result["universes"][u] for u in ("upstream_gated_pre_dedup", "primary_only"))
    assert native["tail_stage_records"] - primary["tail_stage_records"] == 2
    assert classes(result, 5)["retained"] - classes(result, 5, "primary_only")["retained"] == 2
    tail_flags = result["flag_overlaps"]["tail_stage"]
    assert tail_flags["secondary"] == 1 and tail_flags["supplementary"] == 1
    assert tail_flags["tail_end_hard_clip"] == 1 and "read2_flag" not in tail_flags
    assert classes(result, 5)["short_clip"] >= 1  # hard-clipped tail is not soft-clip evidence


def test_whitelist_overlap_counts(tmp_path):
    wl = tmp_path / "wl.txt"
    wl.write_text("AAAC-1\n")
    records = [fwd("a", "A" * 12), fwd("b", "A" * 12, tags=(("CB", "TTTT-1"), ("UB", "G")))]
    result = tp.profile_bam(request(write_bam(tmp_path / "in.bam", records), values=(5,), cell_whitelist=str(wl)))
    assert result["flag_overlaps"]["tail_stage_cell_tag_in_whitelist"] == 1
    per = result["universes"]["upstream_gated_pre_dedup"]["per_minpolyA"]["5"]
    assert per["classes"]["retained"] == 2 and per["retained_cell_tag_in_whitelist"] == 1
    assert result["provenance"]["cell_whitelist"]["barcodes"] == 1


# ------------------------------------------------------------- caps, sampling, scope

def test_head_cap_truncates_and_contig_scope(tmp_path):
    bam = write_bam(tmp_path / "in.bam", native_fixture())
    req = request(bam)
    req["max_records"] = 3
    result = tp.profile_bam(req)
    assert result["scope"]["truncated_at_cap"] is True
    assert result["waterfall"]["totals"]["examined_records"] == 3
    req = request(bam, contigs=["c2"])
    result = tp.profile_bam(req)
    assert result["waterfall"]["totals"]["examined_records"] == 1
    assert result["scope"]["truncated_at_cap"] is False


def test_qname_hash_keeps_templates_together_and_is_deterministic(tmp_path):
    records = []
    for i in range(60):
        records += [fwd(f"t{i}", "A" * 12), fwd(f"t{i}", "A" * 12, flag=256)]
    bam = write_bam(tmp_path / "in.bam", records)
    req = request(bam, values=(5,))
    req["sampling"] = {"policy": "qname_hash", "fraction": 0.5, "seed": 7}
    a, b = tp.profile_bam(req), tp.profile_bam(req)
    assert a["waterfall"] == b["waterfall"]
    kept = a["waterfall"]["totals"]["sampled_records"]
    assert kept % 2 == 0 and 0 < kept < 120
    assert a["universes"]["primary_only"]["tail_stage_records"] * 2 == kept
    req["sampling"]["fraction"] = 1
    assert tp.profile_bam(req)["waterfall"]["totals"]["sampled_records"] == 120


@pytest.mark.parametrize("change,code", [
    ({"minpolyA_values": [0]}, "invalid_request"),
    ({"minpolyA_values": [5, 5]}, "invalid_request"),
    ({"max_records": 0}, "invalid_request"),
    ({"minMAPQ": 256}, "invalid_request"),
    ({"sampling": {"policy": "random"}}, "invalid_request"),
    ({"extra": 1}, "invalid_request"),
    ({"contigs": ["chrZ"]}, "invalid_request"),
    ({"bam": "/nonexistent.bam"}, "source_unavailable"),
    ({"expected_sha256": {"index": "0" * 64}}, "source_mismatch"),
    ({"expected_sha256": {"bam": "0" * 64}}, "source_mismatch"),
    ({"expected_sha256": {"bam": "A" * 64}}, "invalid_request"),
    ({"expected_sha256": {"cell_whitelist": "0" * 64}}, "invalid_request"),
])
def test_refusals(tmp_path, change, code):
    req = request(write_bam(tmp_path / "in.bam", [fwd("a", "A" * 12)]))
    req.update(change)
    with pytest.raises(tp.Refusal) as info:
        tp.profile_bam(req)
    assert info.value.code == code


def test_contig_scope_requires_index(tmp_path):
    bam = write_bam(tmp_path / "in.bam", [fwd("a", "A" * 12)])
    Path(f"{bam}.bai").unlink()
    with pytest.raises(tp.Refusal):
        tp.profile_bam(request(bam, contigs=["c1"]))
    assert tp.profile_bam(request(bam))["status"] == "ok"  # whole-file scan needs no index


def test_cli_output_receipt_and_no_overwrite(tmp_path, capsys):
    bam = write_bam(tmp_path / "in.bam", native_fixture())
    req_path = tmp_path / "req.json"
    req_path.write_text(json.dumps(request(bam, hash_source=True)))
    out = tmp_path / "out.json"
    assert tp.main(["--request", str(req_path), "--output", str(out)]) == 0
    result = json.loads(out.read_text())
    prov = result["provenance"]
    assert prov["source"]["sha256"] and prov["code"]["module_sha256"] and prov["code"]["native_extractor_sha256"]
    assert prov["source"]["index"]["sha256"] and prov["source"]["identity_rechecked_after_scan"] == ["bam", "index"]
    assert prov["environment"]["pysam"] == pysam.__version__
    assert result["attempt"]["argv"][0] == "tail_profile"
    capsys.readouterr()
    assert tp.main(["--request", str(req_path), "--output", str(out)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "output_exists"


# ------------------------------------------------------------- source/index binding

def _sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_expected_hashes_bind_bam_and_index(tmp_path):
    bam = write_bam(tmp_path / "in.bam", native_fixture())
    req = request(bam, contigs=["c1"], expected_sha256={"bam": _sha(bam), "index": _sha(f"{bam}.bai")})
    result = tp.profile_bam(req)
    src = result["provenance"]["source"]
    assert src["expected_sha256_matched"] == ["bam", "index"]
    assert src["sha256"] == _sha(bam) and src["index"]["sha256"] == _sha(f"{bam}.bai")


@pytest.mark.parametrize("mutation", ["touch", "append"])
def test_index_change_during_scan_fails(tmp_path, monkeypatch, mutation):
    bam = write_bam(tmp_path / "in.bam", native_fixture())
    bai = Path(f"{bam}.bai")
    original = tp._iter_records

    def mutate_after(handle, contigs):
        yield from original(handle, contigs)
        if mutation == "touch":
            st = bai.stat()
            os.utime(bai, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        else:
            with open(bai, "ab") as out:
                out.write(b"\0")

    monkeypatch.setattr(tp, "_iter_records", mutate_after)
    with pytest.raises(tp.Refusal) as info:
        tp.profile_bam(request(bam, contigs=["c1"]))
    assert info.value.code == "source_changed" and "index" in info.value.detail


# ------------------------------------------------------------- upstream read2 gate (actual umi_tools)

@pytest.mark.skipif(shutil.which("umi_tools") is None, reason="umi_tools not on PATH")
def test_actual_umi_tools_drops_read2_like_the_profile_gate(tmp_path):
    from stages.preprocess_units import build_umi_dedup_command
    from types import SimpleNamespace
    records = [fwd("r1", "A" * 12), rec("r2", "32M12S", GENOME + "A" * 12, flag=1 | 128,
                                        tags=(("CB", "TTTT-1"), ("UB", "CCCC")))]
    bam = write_bam(tmp_path / "in.bam", records)
    args = SimpleNamespace(cb_tag="CB", umi_tag="UB", tools_seed=1)
    out = tmp_path / "dedup.bam"
    cmd = build_umi_dedup_command(args=args, in_bam=bam, out_bam=out, local_log=tmp_path / "u.log",
                                  dedup_method="directional", temp_dir=tmp_path)
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    with pysam.AlignmentFile(str(out)) as handle:
        assert [r.query_name for r in handle] == ["r1"]
    w = tp.profile_bam(request(bam))["waterfall"]["partition"]
    assert w["read2_flag"] == 1 and w["tail_stage_records"] == 1
