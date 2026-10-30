"""Bounded read-only pre-dedup tail profile of one aligned BAM.

The per-record tail rule replicates ``utils.polya_extract.extract_polya_bam``
exactly (see ``SEMANTICS``); the native module is not imported or modified.
One pass over one fixed record set evaluates every requested minimum
tail length with the same nonA/MAPQ/tag settings. Counts are alignment
records before UMI deduplication, never molecules, and are not expected to
equal the native extractor's post-dedup counters. No pipeline execution,
parameter recommendation or biological interpretation is provided.

    python -B -m scpolya_agent.tail_profile --request REQ.json --output OUT.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "scpolya.agent.tail_profile.v1"
REQUEST_SCHEMA = "scpolya.agent.tail_profile_request.v1"
MODULE = Path(__file__).absolute()
REPO = MODULE.parents[1]
NATIVE_EXTRACTOR = REPO / "utils" / "polya_extract.py"
HEX = re.compile(r"[0-9a-f]{64}\Z")
ENC = dict(sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)

MAX_RECORDS_LIMIT = 50_000_000
MAX_THRESHOLDS = 8
MAX_CONTIGS = 256
MAX_WHITELIST_BYTES = 64 * 1024 * 1024
MAX_JOINT_ROWS = 65536  # Exact (clip, A count) pairs; beyond this the table is withheld.
GATES = ("read2_flag", "unmapped", "missing_cell_tag", "missing_umi_tag", "mapq_below_min")
TAIL_CLASSES = ("retained", "short_clip", "low_A", "too_many_nonA")
OVERLAP_FLAGS = ("secondary", "supplementary", "duplicate_flag", "qcfail_flag",
                 "tail_end_hard_clip", "sequence_unavailable")

SEMANTICS = {
    "unit": "alignment record (BAM line); pre-UMI-dedup; not molecules or cells",
    "tail_clip": ("strand-appropriate soft clip: reverse-flag records use the LEADING "
                  "soft clip in stored (reference-forward) orientation and count 'T'; "
                  "others use the TRAILING soft clip and count 'A'. Hard clips are not "
                  "sequence and are invisible to the rule."),
    "minpolyA_m": ("used twice: tail-clip length >= m (else short_clip) AND the A/T "
                   "count over the WHOLE clip >= m (else low_A). Not a contiguous-run "
                   "length and not a biological poly(A) length."),
    "nonA_allowed_k": ("maximum number of non-A (non-T for reverse) bases anywhere in the "
                       "whole clip, N included, case-insensitive; adapter or other "
                       "sequence in the clip counts against k (too_many_nonA)."),
    "native_class_map": ("extractor rule applied to this record set: excluded_reads_polyA = short_clip + low_A; "
                         "native excluded_reads_nonA = too_many_nonA; low_A takes "
                         "precedence over too_many_nonA"),
    "minMAPQ_q": "records with MAPQ < q are dropped silently by native; 255 passes",
    "native_stage_order": ("split/filter by contig -> umi_tools 1.1.6 dedup without --paired "
                           "(drops read2-flag records, then unmapped, then records missing "
                           "cell or UMI tag; no MAPQ gate; then groups per cell/UMI/position "
                           "and keeps one representative) -> extract_polya_bam (MAPQ then "
                           "tail rule)."),
    "mirrored_gates": ("this profile mirrors only the explicit upstream gates read2, "
                       "unmapped, cell tag, UMI tag (umi_tools order) and MAPQ (extractor). "
                       "It does not mirror the dedup grouping/representative selection, so "
                       "upstream_gated_pre_dedup is a profiler-defined record set, not the "
                       "native extractor input and not a molecule count."),
    "secondary_supplementary": ("no explicit secondary/supplementary flag filter upstream of "
                                "grouping; such records can still be collapsed or selected "
                                "away by dedup. Kept in upstream_gated_pre_dedup, removed in "
                                "primary_only."),
    "joint_clip_A": ("exact (tail_clip_length, A_or_T_count) pairs, no binning: for any m>=1, "
                     "k>=0, retained = clip>=m and a>=m and clip-a<=k. Withheld (null) "
                     "if more than MAX_JOINT_ROWS distinct pairs occur."),
    "sequence_unavailable": ("SEQ '*': excluded from tail classes; native would count a "
                             "forward record as excluded_reads_polyA and raise TypeError "
                             "on a reverse record whose leading clip >= m"),
}


class Refusal(ValueError):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, str(detail)[:240]
        super().__init__(self.detail)


def need(condition, detail, code="invalid_request"):
    if not condition:
        raise Refusal(code, detail)


# ---------------------------------------------------------------- pure rule

def tail_clip_features(is_reverse, query_sequence, query_alignment_start,
                       query_alignment_end, query_length):
    """Return (clip_length, a_count) with native slicing, or None without SEQ."""
    if query_sequence is None:
        return None
    if is_reverse:
        size = query_alignment_start
        clip = query_sequence[:size]
        base = "T"
    else:
        size = query_length - query_alignment_end
        # Native only slices when size >= m >= 1; seq[-0:] would be the whole read.
        clip = query_sequence[-size:] if size > 0 else ""
        base = "A"
    return size, clip.upper().count(base)


def classify_tail(clip_length, a_count, minpolyA, nonA_allowed):
    """Exact branch order of extract_polya_bam for one record past MAPQ."""
    if clip_length < minpolyA:
        return "short_clip"
    non_a = clip_length - a_count
    if a_count >= minpolyA and non_a <= nonA_allowed:
        return "retained"
    if a_count < minpolyA:
        return "low_A"
    return "too_many_nonA"


def native_outcome_without_sequence(is_reverse, query_alignment_start,
                                    query_alignment_end, query_length, minpolyA):
    """What extract_polya_bam does with SEQ '*' (query_length is 0)."""
    size = query_alignment_start if is_reverse else query_length - query_alignment_end
    return "excluded_reads_polyA" if size < minpolyA else "raises"


def gate(read, cb_tag, umi_tag, min_mapq):
    """First failing ordered gate, or None for the tail stage."""
    if read.is_read2:
        return "read2_flag"
    if read.is_unmapped:
        return "unmapped"
    if not read.has_tag(cb_tag):
        return "missing_cell_tag"
    if not read.has_tag(umi_tag):
        return "missing_umi_tag"
    if read.mapping_quality < min_mapq:
        return "mapq_below_min"
    return None


def qname_fraction(seed, qname):
    digest = hashlib.blake2b(f"{seed}\0{qname}".encode("utf-8", "surrogateescape"),
                             digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2.0 ** 64


# ---------------------------------------------------------------- request

def _int(value, low, high, where):
    need(type(value) is int and low <= value <= high, f"{where} must be int in [{low},{high}]")
    return value


def validate_request(request):
    need(type(request) is dict, "request must be an object")
    allowed = {"schema", "bam", "contigs", "minpolyA_values", "nonA_allowed", "minMAPQ",
               "cb_tag", "umi_tag", "max_records", "sampling", "cell_whitelist",
               "hash_source", "expected_sha256"}
    need(set(request) <= allowed, f"unknown fields {sorted(set(request) - allowed)}")
    need(request.get("schema") == REQUEST_SCHEMA, "request schema mismatch")
    need(type(request.get("bam")) is str and request["bam"], "bam path required")
    thresholds = request.get("minpolyA_values")
    need(type(thresholds) is list and 1 <= len(thresholds) <= MAX_THRESHOLDS,
         f"minpolyA_values: 1..{MAX_THRESHOLDS} ints")
    for value in thresholds:
        _int(value, 1, 1000, "minpolyA value")
    need(len(set(thresholds)) == len(thresholds), "duplicate minpolyA value")
    _int(request.get("nonA_allowed"), 0, 1000, "nonA_allowed")
    _int(request.get("minMAPQ"), 0, 255, "minMAPQ")
    _int(request.get("max_records"), 1, MAX_RECORDS_LIMIT, "max_records")
    for key in ("cb_tag", "umi_tag"):
        value = request.get(key, "CB" if key == "cb_tag" else "UB")
        need(type(value) is str and len(value) == 2 and value.isalnum(), f"{key} must be a 2-char tag")
    contigs = request.get("contigs")
    need(contigs is None or (type(contigs) is list and 1 <= len(contigs) <= MAX_CONTIGS
                             and all(type(c) is str and c for c in contigs)
                             and len(set(contigs)) == len(contigs)),
         "contigs must be null or a list of distinct names")
    sampling = request.get("sampling")
    need(type(sampling) is dict, "sampling required")
    if sampling.get("policy") == "head":
        need(set(sampling) == {"policy"}, "head sampling has no other fields")
    else:
        need(sampling.get("policy") == "qname_hash" and set(sampling) == {"policy", "fraction", "seed"},
             "sampling policy must be head or qname_hash{fraction,seed}")
        fraction = sampling["fraction"]
        need(type(fraction) in (int, float) and 0 < fraction <= 1, "fraction in (0,1]")
        _int(sampling["seed"], 0, 2 ** 63 - 1, "seed")
    whitelist = request.get("cell_whitelist")
    need(whitelist is None or (type(whitelist) is str and whitelist), "cell_whitelist path or null")
    need(type(request.get("hash_source", False)) is bool, "hash_source must be bool")
    expected = request.get("expected_sha256")
    need(expected is None or (type(expected) is dict and expected
                              and set(expected) <= {"bam", "index", "cell_whitelist"}
                              and all(type(v) is str and HEX.match(v) for v in expected.values())),
         "expected_sha256: object of bam/index/cell_whitelist -> lowercase sha256")
    need(not (expected or {}).get("cell_whitelist") or whitelist, "expected whitelist hash without whitelist")
    return request


# ---------------------------------------------------------------- provenance

def _sha256_file(path, limit=None):
    digest, total = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            total += len(chunk)
            need(limit is None or total <= limit, f"{path} exceeds {limit} bytes", "input_too_large")
            digest.update(chunk)
    return digest.hexdigest()


def _stat(path):
    st = os.stat(path)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "inode": st.st_ino, "device": st.st_dev}


def _git(*argv):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        out = subprocess.run(["git", "-C", str(REPO), *argv], capture_output=True, text=True,
                             timeout=5, env=env, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def code_pins():
    status = _git("status", "--porcelain", "--", str(MODULE.relative_to(REPO)),
                  str(NATIVE_EXTRACTOR.relative_to(REPO)))
    return {
        "module": str(MODULE), "module_sha256": _sha256_file(MODULE),
        "native_extractor": str(NATIVE_EXTRACTOR),
        "native_extractor_sha256": _sha256_file(NATIVE_EXTRACTOR) if NATIVE_EXTRACTOR.is_file() else None,
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty_pinned_files": None if status is None else bool(status),
    }


def env_pins(pysam):
    return {
        "python": sys.version.split()[0], "executable": sys.executable,
        "pysam": pysam.__version__, "htslib": getattr(pysam, "__htslib_version__", None),
        "thread_env": {k: os.environ.get(k) for k in (
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")},
    }


def _identity(path, with_hash, limit=None):
    """Static identity (stat, optionally sha256) used before and after the scan."""
    st = _stat(path)
    if with_hash:
        st["sha256"] = _sha256_file(path, limit)
        need(_stat(path) == {k: v for k, v in st.items() if k != "sha256"},
             f"{path} changed while hashing", "source_changed")
    return st


def _index_path(bam):
    for candidate in (Path(f"{bam}.bai"), Path(bam).with_suffix(".bai"), Path(f"{bam}.csi")):
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------- profile

def _counter():
    return {"tail_stage_records": 0, "sequence_unavailable": 0,
            "native_missing_sequence": {},  # minpolyA -> {excluded_reads_polyA, raises}
            "classes": {}, "joint": {}, "whitelisted_retained": {}}


def _classes_for(universe, thresholds):
    for m in thresholds:
        universe["classes"][str(m)] = dict.fromkeys(TAIL_CLASSES, 0)
        universe["native_missing_sequence"][str(m)] = {"excluded_reads_polyA": 0, "raises": 0}
        universe["whitelisted_retained"][str(m)] = 0


def _record_tail(universe, read, thresholds, k, in_whitelist):
    universe["tail_stage_records"] += 1
    features = tail_clip_features(read.is_reverse, read.query_sequence, read.query_alignment_start,
                                  read.query_alignment_end, read.query_length)
    if features is None:
        universe["sequence_unavailable"] += 1
        for m in thresholds:
            outcome = native_outcome_without_sequence(
                read.is_reverse, read.query_alignment_start, read.query_alignment_end,
                read.query_length, m)
            universe["native_missing_sequence"][str(m)][outcome] += 1
        return
    clip, a_count = features
    joint = universe["joint"]
    if joint is not None:
        joint[(clip, a_count)] = joint.get((clip, a_count), 0) + 1
        if len(joint) > MAX_JOINT_ROWS:
            universe["joint"] = None
    for m in thresholds:
        cls = classify_tail(clip, a_count, m, k)
        universe["classes"][str(m)][cls] += 1
        if cls == "retained" and in_whitelist:
            universe["whitelisted_retained"][str(m)] += 1


def _tail_end_hard_clip(read):
    cigar = read.cigartuples or []
    if not cigar:
        return False
    return (cigar[0][0] if read.is_reverse else cigar[-1][0]) == 5


def _finish_universe(universe, thresholds, has_whitelist):
    denominator = universe["tail_stage_records"] - universe["sequence_unavailable"]
    per_threshold = {}
    for m in thresholds:
        classes = universe["classes"][str(m)]
        missing = universe["native_missing_sequence"][str(m)]
        assert sum(classes.values()) == denominator
        per_threshold[str(m)] = {
            "classes": classes,
            "retained_fraction": {"numerator": classes["retained"], "denominator": denominator,
                                  "value": classes["retained"] / denominator if denominator else None},
            "native_counter_mapping": {
                "retained_reads": classes["retained"],
                "excluded_reads_polyA": classes["short_clip"] + classes["low_A"]
                + missing["excluded_reads_polyA"],
                "excluded_reads_nonA": classes["too_many_nonA"],
                "native_would_raise_records": missing["raises"],
            },
            "retained_cell_tag_in_whitelist": universe["whitelisted_retained"][str(m)] if has_whitelist else None,
        }
    return {
        "tail_stage_records": universe["tail_stage_records"],
        "sequence_unavailable": universe["sequence_unavailable"],
        "tail_classifiable_records": denominator,
        "per_minpolyA": per_threshold,
        "joint_clip_A": None if universe["joint"] is None else {
            "fields": ["tail_clip_length", "A_or_T_count", "records"], "exact": True,
            "rows": [[c, a, v] for (c, a), v in sorted(universe["joint"].items())],
        },
        "joint_clip_A_withheld_reason": None if universe["joint"] is not None
        else f"more than {MAX_JOINT_ROWS} distinct pairs",
    }


def _iter_records(bam, contigs):
    if contigs is None:
        yield from bam.fetch(until_eof=True)
        return
    for contig in contigs:
        yield from bam.fetch(contig)


def profile_bam(request):
    import pysam

    started, t0 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), time.monotonic()
    request = validate_request(request)
    thresholds = sorted(request["minpolyA_values"])
    k, q = request["nonA_allowed"], request["minMAPQ"]
    cb_tag, umi_tag = request.get("cb_tag", "CB"), request.get("umi_tag", "UB")
    cap, sampling, contigs = request["max_records"], request["sampling"], request.get("contigs")
    bam_path = Path(request["bam"])
    need(bam_path.is_file(), f"BAM not found: {bam_path}", "source_unavailable")
    index = _index_path(bam_path)
    need(contigs is None or index is not None, "contig restriction requires a BAM index",
         "source_unavailable")

    expected = request.get("expected_sha256") or {}
    hash_bam = bool(request.get("hash_source")) or "bam" in expected
    need("index" not in expected or index is not None, "expected index hash but no index found",
         "source_unavailable")
    pinned = {"bam": (bam_path, hash_bam, None)}
    if index is not None:
        pinned["index"] = (index, True, None)

    whitelist = None
    if request.get("cell_whitelist"):
        wl_path = Path(request["cell_whitelist"])
        need(wl_path.is_file(), f"whitelist not found: {wl_path}", "source_unavailable")
        pinned["cell_whitelist"] = (wl_path, True, MAX_WHITELIST_BYTES)
        with open(wl_path, encoding="ascii") as handle:
            whitelist = {line.strip() for line in handle if line.strip()}

    before = {name: _identity(*args) for name, args in pinned.items()}
    for name, digest in expected.items():
        need(before[name]["sha256"] == digest, f"{name} sha256 differs from expected_sha256",
             "source_mismatch")

    waterfall = {"examined_records": 0, "not_sampled": 0, **{g: 0 for g in GATES}}
    overlaps = {"sampled": dict.fromkeys(OVERLAP_FLAGS, 0), "tail_stage": dict.fromkeys(OVERLAP_FLAGS, 0),
                "tail_stage_cell_tag_in_whitelist": 0}
    gated, primary = _counter(), _counter()
    _classes_for(gated, thresholds)
    _classes_for(primary, thresholds)
    truncated = False
    fraction = sampling.get("fraction")

    # The pinned index is the one opened, so contig selection is bound to it.
    opened = dict(index_filename=str(index)) if index is not None else {}
    with pysam.AlignmentFile(str(bam_path), "rb", **opened) as bam:
        header_sha256 = hashlib.sha256(str(bam.header).encode()).hexdigest()
        if contigs is not None:
            missing = [c for c in contigs if c not in bam.references]
            need(not missing, f"contigs absent from header: {missing[:5]}", "invalid_request")
        for read in _iter_records(bam, contigs):
            if waterfall["examined_records"] >= cap:
                truncated = True
                break
            waterfall["examined_records"] += 1
            if fraction is not None and qname_fraction(sampling["seed"], read.query_name) >= fraction:
                waterfall["not_sampled"] += 1
                continue
            flags = {
                "secondary": read.is_secondary, "supplementary": read.is_supplementary,
                "duplicate_flag": read.is_duplicate, "qcfail_flag": read.is_qcfail,
                "tail_end_hard_clip": _tail_end_hard_clip(read),
                "sequence_unavailable": read.query_sequence is None,
            }
            for name, value in flags.items():
                overlaps["sampled"][name] += value
            failed = gate(read, cb_tag, umi_tag, q)
            if failed is not None:
                waterfall[failed] += 1
                continue
            for name, value in flags.items():
                overlaps["tail_stage"][name] += value
            in_whitelist = whitelist is not None and read.get_tag(cb_tag) in whitelist
            overlaps["tail_stage_cell_tag_in_whitelist"] += in_whitelist
            _record_tail(gated, read, thresholds, k, in_whitelist)
            if not (read.is_secondary or read.is_supplementary):
                _record_tail(primary, read, thresholds, k, in_whitelist)

    if whitelist is None:
        overlaps["tail_stage_cell_tag_in_whitelist"] = None
    after = {name: _identity(*args) for name, args in pinned.items()}
    changed = sorted(name for name in pinned if after[name] != before[name])
    need(not changed, f"pinned input changed during the profile: {changed}", "source_changed")
    source = {
        "path": str(bam_path), "realpath": os.path.realpath(bam_path), **before["bam"],
        "sha256": before["bam"].get("sha256"), "header_sha256": header_sha256,
        "index": None if index is None else {"path": str(index), **before["index"]},
        "identity_rechecked_after_scan": sorted(pinned),
        "expected_sha256_matched": sorted(expected),
    }
    whitelist_pin = None if whitelist is None else {
        "path": str(pinned["cell_whitelist"][0]), **before["cell_whitelist"], "barcodes": len(whitelist)}
    sampled = waterfall["examined_records"] - waterfall["not_sampled"]
    waterfall["tail_stage_records"] = gated["tail_stage_records"]
    totals = {"examined_records": waterfall.pop("examined_records"), "sampled_records": sampled}
    assert totals["examined_records"] == sum(waterfall.values())
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "schema": SCHEMA,
        "status": "ok",
        "semantics": SEMANTICS,
        "parameters": {"minpolyA_values": thresholds, "nonA_allowed": k, "minMAPQ": q,
                       "cb_tag": cb_tag, "umi_tag": umi_tag},
        "scope": {
            "contigs": contigs, "record_order": "file order" if contigs is None else "index fetch per listed contig",
            "max_records": cap, "truncated_at_cap": truncated,
            "sampling": sampling,
            "sampling_note": ("head = first records in scope order, not a random sample"
                              if fraction is None else
                              "qname_hash decides per query name (no read-group/sample "
                              "namespace) among records examined before the cap; a cap can "
                              "split a query name's records"),
            "pre_dedup": True, "molecule_level": False,
        },
        "waterfall": {
            "totals": totals,
            "partition_of": "examined_records",
            "partition_order": ["not_sampled", *GATES, "tail_stage_records"],
            "partition": waterfall,
        },
        "flag_overlaps": {
            "disjoint": False,
            "note": "labels overlap each other; counted among sampled records and among tail-stage records",
            **overlaps,
        },
        "universes": {
            "upstream_gated_pre_dedup": _finish_universe(gated, thresholds, whitelist is not None),
            "primary_only": _finish_universe(primary, thresholds, whitelist is not None),
        },
        "provenance": {
            "source": source, "cell_whitelist": whitelist_pin,
            "code": code_pins(), "environment": env_pins(pysam),
            "request_sha256": hashlib.sha256(json.dumps(request, **ENC).encode()).hexdigest(),
        },
        "attempt": {"started_utc": started, "elapsed_seconds": round(time.monotonic() - t0, 3),
                    "max_rss_kib": usage.ru_maxrss, "cpu_user_seconds": round(usage.ru_utime, 3),
                    "pid": os.getpid()},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded pre-dedup tail profile (read-only).")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="new file; refused if it exists")
    args = parser.parse_args(argv)
    try:
        need(args.output is None or not args.output.exists(), "output exists", "output_exists")
        raw = args.request.read_bytes()
        need(len(raw) <= 64 * 1024, "request too large")
        result = profile_bam(json.loads(raw))
        result["attempt"]["argv"] = sys.argv if argv is None else ["tail_profile", *argv]
    except Refusal as exc:
        result = {"schema": SCHEMA, "status": "refused", "code": exc.code, "detail": exc.detail}
    encoded = json.dumps(result, **ENC) + "\n"
    if args.output is not None and result["status"] == "ok":
        with open(args.output, "x", encoding="ascii") as handle:
            handle.write(encoded)
    print(encoded if args.output is None or result["status"] != "ok" else
          json.dumps({"schema": SCHEMA, "status": "ok", "output": str(args.output),
                      "sha256": hashlib.sha256(encoded.encode()).hexdigest()}, **ENC))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
