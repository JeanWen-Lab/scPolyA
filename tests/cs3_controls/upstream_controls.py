from pathlib import Path
import json
from scpolya_ui.analysis.cs3_backend.run_transform import write_source_map

def make_case(case_dir, chain, bed_rows, metadata_rows, expected, source_size=1000,
              target_size=1000):
    case_dir.mkdir(parents=True)
    (case_dir / "map.chain").write_text(chain)
    with (case_dir / "input.bed").open("w") as fh:
        for row in bed_rows:
            fh.write("\t".join(map(str, row)) + "\n")
    write_source_map(case_dir / "source-map.tsv", metadata_rows)
    (case_dir / "source-sizes.json").write_text(json.dumps({"chrS": source_size}, indent=2) + "\n")
    target_sizes = {"chrT": target_size}
    if any(row[0] not in target_sizes for row in bed_rows):
        target_sizes["chrUn_synthetic"] = target_size
    (case_dir / "target-sizes.json").write_text(json.dumps(target_sizes, indent=2) + "\n")
    (case_dir / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")

def build_selftest(workdir):
    workdir = Path(workdir)
    if workdir.exists():
        raise FileExistsError(f"refusing to overwrite synthetic-control evidence: {workdir}")
    workdir.mkdir(parents=True)
    cases = {}

    inversion_chain = "chain 1000 chrS 1000 + 0 1000 chrT 1000 - 0 1000 1\n1000\n"
    rows = [
        ("chrS", 0, 1, "1", 1000, "+"),
        ("chrS", 0, 1, "2", 1000, "-"),
        ("chrS", 999, 1000, "3", 1000, "+"),
        ("chrS", 999, 1000, "4", 1000, "-"),
    ]
    metadata = []
    for row in rows:
        metadata.append({
            "ordinal": int(row[3]), "source_pas_id": "synthetic:dup:first" if row[3] in ("1", "2") else f"synthetic:{row[3]}",
            "source_chromosome": row[0], "source_position_1based": row[2],
            "source_position_0based": row[1], "source_strand": row[5],
            "source_bed_start": row[1], "source_bed_end": row[2],
        })
    expected = {
        "statuses": {"1": "ACCEPTED", "2": "ACCEPTED", "3": "ACCEPTED", "4": "ACCEPTED"},
        "assertions": [
            {"name": "inversion_strand_flip", "ordinal": 1, "field": "target_strand", "value": "-"},
            {"name": "inversion_strand_flip", "ordinal": 2, "field": "target_strand", "value": "+"},
            {"name": "inversion_strand_flip", "ordinal": 3, "field": "target_strand", "value": "-"},
            {"name": "inversion_strand_flip", "ordinal": 4, "field": "target_strand", "value": "+"},
        ],
    }
    make_case(workdir / "inversion_first_last", inversion_chain, rows, metadata, expected)
    cases["inversion_first_last"] = ({"chrS": 1000}, {"chrT": 1000})

    forward_chain = "chain 1000 chrS 1000 + 99 110 chrT 1000 + 200 211 1\n11\n"
    rows = [
        ("chrS", 99, 100, "1", 1000, "+"),
        ("chrS", 99, 100, "2", 1000, "-"),
        ("chrS", 98, 99, "3", 1000, "+"),
        ("chrS", 109, 110, "4", 1000, "-"),
        ("chrS", 110, 111, "5", 1000, "+"),
    ]
    metadata = []
    for row in rows:
        metadata.append({
            "ordinal": int(row[3]), "source_pas_id": f"synthetic:{row[3]}",
            "source_chromosome": row[0], "source_position_1based": row[2],
            "source_position_0based": row[1], "source_strand": row[5],
            "source_bed_start": row[1], "source_bed_end": row[2],
        })
    expected = {
        "statuses": {"1": "ACCEPTED", "2": "ACCEPTED", "3": "UNMAPPED", "4": "ACCEPTED", "5": "UNMAPPED"},
        "assertions": [
            {"name": "forward_strand_preserved", "ordinal": 1, "field": "target_strand", "value": "+"},
            {"name": "forward_strand_preserved", "ordinal": 2, "field": "target_strand", "value": "-"},
        ],
    }
    make_case(workdir / "forward_first_last_gaps", forward_chain, rows, metadata, expected)
    cases["forward_first_last_gaps"] = ({"chrS": 1000}, {"chrT": 1000})

    gapped_chain = "chain 1000 chrS 1000 + 100 113 chrT 1000 + 200 214 1\n5 3 4\n5\n"
    bed = [
        ("chrS", 100, 101, "1", 1000, "+"),
        ("chrS", 104, 105, "2", 1000, "-"),
        ("chrS", 105, 106, "3", 1000, "+"),
        ("chrS", 107, 108, "4", 1000, "-"),
        ("chrS", 108, 109, "5", 1000, "+"),
        ("chrS", 112, 113, "6", 1000, "-"),
    ]
    metadata = [{
        "ordinal": int(r[3]), "source_pas_id": f"synthetic:{r[3]}", "source_chromosome": r[0],
        "source_position_1based": r[2], "source_position_0based": r[1], "source_strand": r[5],
        "source_bed_start": r[1], "source_bed_end": r[2],
    } for r in bed]
    expected = {"statuses": {"1": "ACCEPTED", "2": "ACCEPTED", "3": "UNMAPPED", "4": "UNMAPPED", "5": "ACCEPTED", "6": "ACCEPTED"}}
    make_case(workdir / "gapped", gapped_chain, bed, metadata, expected)
    cases["gapped"] = ({"chrS": 1000}, {"chrT": 1000})

    inversion_gapped_chain = (
        "chain 1000 chrS 1000 + 100 113 chrT 1000 - 787 797 1\n"
        "5 3 0\n5\n"
    )
    bed = [
        ("chrS", 100, 101, "1", 1000, "+"),
        ("chrS", 104, 105, "2", 1000, "-"),
        ("chrS", 105, 106, "3", 1000, "+"),
        ("chrS", 107, 108, "4", 1000, "-"),
        ("chrS", 108, 109, "5", 1000, "+"),
        ("chrS", 112, 113, "6", 1000, "-"),
    ]
    metadata = [{
        "ordinal": int(r[3]), "source_pas_id": f"synthetic:{r[3]}", "source_chromosome": r[0],
        "source_position_1based": r[2], "source_position_0based": r[1], "source_strand": r[5],
        "source_bed_start": r[1], "source_bed_end": r[2],
    } for r in bed]
    expected = {
        "statuses": {"1": "ACCEPTED", "2": "ACCEPTED", "3": "UNMAPPED", "4": "UNMAPPED", "5": "ACCEPTED", "6": "ACCEPTED"},
        "assertions": [
            {"name": "inversion_strand_flip", "ordinal": 1, "field": "target_strand", "value": "-"},
            {"name": "inversion_strand_flip", "ordinal": 2, "field": "target_strand", "value": "+"},
            {"name": "inversion_strand_flip", "ordinal": 5, "field": "target_strand", "value": "-"},
            {"name": "inversion_strand_flip", "ordinal": 6, "field": "target_strand", "value": "+"},
        ],
    }
    make_case(workdir / "inversion_gapped", inversion_gapped_chain, bed, metadata, expected)
    cases["inversion_gapped"] = ({"chrS": 1000}, {"chrT": 1000})

    distinct_chain = (
        "chain 1000 chrS 1000 + 100 110 chrT 1000 + 200 210 1\n10\n\n"
        "chain 900 chrS 1000 + 100 110 chrT 1000 + 400 410 2\n10\n"
    )
    bed = [
        ("chrS", 100, 101, "1", 1000, "+"),
        ("chrS", 100, 101, "2", 1000, "-"),
        ("chrS", 150, 151, "3", 1000, "+"),
    ]
    metadata = [{
        "ordinal": int(r[3]), "source_pas_id": "synthetic:duplicate-id" if r[3] in ("1", "2") else f"synthetic:{r[3]}",
        "source_chromosome": r[0], "source_position_1based": r[2], "source_position_0based": r[1],
        "source_strand": r[5], "source_bed_start": r[1], "source_bed_end": r[2],
    } for r in bed]
    expected = {"statuses": {"1": "MULTIPLE", "2": "MULTIPLE", "3": "UNMAPPED"}}
    make_case(workdir / "multiple_distinct_same_duplicate_ids", distinct_chain, bed, metadata, expected)
    cases["multiple_distinct_same_duplicate_ids"] = ({"chrS": 1000}, {"chrT": 1000})

    same_chain = (
        "chain 1000 chrS 1000 + 100 110 chrT 1000 + 200 210 1\n10\n\n"
        "chain 900 chrS 1000 + 100 110 chrT 1000 + 200 210 2\n10\n"
    )
    bed = [("chrS", 100, 101, "1", 1000, "+")]
    metadata = [{"ordinal": 1, "source_pas_id": "synthetic:same-target", "source_chromosome": "chrS",
                 "source_position_1based": 101, "source_position_0based": 100, "source_strand": "+",
                 "source_bed_start": 100, "source_bed_end": 101}]
    expected = {"statuses": {"1": "MULTIPLE"}}
    make_case(workdir / "multiple_same_coordinate", same_chain, bed, metadata, expected)
    cases["multiple_same_coordinate"] = ({"chrS": 1000}, {"chrT": 1000})

    nonstandard_chain = "chain 1000 chrS 1000 + 100 110 chrUn_synthetic 1000 + 200 210 1\n10\n"
    bed = [("chrS", 100, 101, "1", 1000, "+")]
    metadata = [{"ordinal": 1, "source_pas_id": "synthetic:nonstandard", "source_chromosome": "chrS",
                 "source_position_1based": 101, "source_position_0based": 100, "source_strand": "+",
                 "source_bed_start": 100, "source_bed_end": 101}]
    expected = {"statuses": {"1": "NONSTANDARD_TARGET"}}
    make_case(workdir / "nonstandard_target", nonstandard_chain, bed, metadata, expected)
    cases["nonstandard_target"] = ({"chrS": 1000}, {"chrT": 1000})

    results = {"exact_binary": str(LIFTOVER), "cases": {}, "all_pass": False}
    try:
        for name, (source_sizes, target_sizes) in cases.items():
            checks, _ = run_accounted(workdir / name, source_sizes, target_sizes,
                                      input_bed=workdir / name / "input.bed",
                                      chain_path=workdir / name / "map.chain",
                                      accepted_targets={"chrT"})
            results["cases"][name] = checks
        old_argv = [
            str(LIFTOVER), "-minMatch=1.0", "-multiple", "-noSerial",
            "-minChainT=0", "-minSizeT=0", "-minChainQ=0", "-minSizeQ=0",
            str(workdir / "forward_first_last_gaps/input.bed"),
            str(workdir / "forward_first_last_gaps/map.chain"),
            str(workdir / "argv-history.mapped.bed"),
            str(workdir / "argv-history.unmapped.bed"),
        ]
        completed = subprocess.run(old_argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        Path(workdir / "argv-history.old-conflicting.argv.json").write_text(json.dumps(old_argv, indent=2) + "\n")
        Path(workdir / "argv-history.old-conflicting.stderr").write_text(completed.stderr)
        Path(workdir / "argv-history.old-conflicting.exitcode").write_text(f"{completed.returncode}\n")
        results["old_conflicting_argv_preserved"] = {
            "exit": completed.returncode,
            "stderr": completed.stderr.strip(),
            "expected_exit": 255,
            "pass": completed.returncode == 255 and "Can't set both" in completed.stderr,
        }
        results["all_pass"] = all(case.get("all_checks_pass") is True for case in results["cases"].values()) and results["old_conflicting_argv_preserved"]["pass"]
    except Exception as exc:
        results["failure"] = {
            "type": type(exc).__name__, "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    results["real_transform_attempted"] = False
    (workdir / "SELFTEST-RESULTS.json").write_text(json.dumps(results, indent=2) + "\n")
    if not results.get("all_pass"):
        raise RuntimeError("synthetic controls failed; evidence preserved")
    return results
