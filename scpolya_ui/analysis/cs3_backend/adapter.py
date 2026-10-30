"""Finite CS3 historical-export adapter. No native job, transform or locus selection."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import types
from bisect import bisect_left
from collections import Counter
from pathlib import Path

VERSION = "scpolya.cs3.adapter.v2"
ROLES = {"helper", "catalogue", "counts", "reference", "reference_contract",
         "coverage", "fasta", "fai", "sequence_contract"}
AXES = {"helper", "reference", "sequence", "stored_flag", "count_scope"}
ADAPTER_PATH = str(Path(__file__).resolve())
ADAPTER_SHA256 = hashlib.sha256(Path(ADAPTER_PATH).read_bytes()).hexdigest()


def execution_binding():
    return {"adapter": {"path": ADAPTER_PATH, "sha256": ADAPTER_SHA256},
            "python": sys.version, "python_executable": sys.executable, "sqlite": sqlite3.sqlite_version}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_state(path):
    stat = Path(path).stat()
    return {"dev": stat.st_dev, "ino": stat.st_ino, "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns}


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def rows(path, required):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        def physical_lines():
            for line_number, line in enumerate(handle, 1):
                require('"' not in line, f"quote character forbidden in TSV {path}:{line_number}")
                require(line.rstrip("\r\n"), f"blank physical TSV row {path}:{line_number}")
                yield line
        reader = csv.DictReader(physical_lines(), delimiter="\t", quoting=csv.QUOTE_NONE, strict=True)
        header = reader.fieldnames or []
        require(len(header) == len(set(header)) and set(required) <= set(header),
                f"invalid/missing columns in {path}: need {sorted(required)}")
        for ordinal, row in enumerate(reader):
            require(None not in row and None not in row.values(), f"ragged row {ordinal} in {path}")
            yield ordinal, row


def integer(token, field):
    require(isinstance(token, str) and re.fullmatch(r"[0-9]+", token) is not None,
            f"{field} must be nonnegative integer text")
    return int(token)


def load_labels(label_rows, helper, limit=100000):
    """One pass supports csv/generator inputs; conflicting duplicates are errors."""
    labels = {}
    duplicate_rows = 0
    for ordinal, row in label_rows:
        require(ordinal < limit, "label row limit exceeded")
        barcode = row["barcode"]
        require(bool(barcode), "empty label barcode")
        value = {"raw": row, "pass_qc": helper.parse_pass_qc(row.get("pass_qc"))}
        if barcode in labels:
            require(labels[barcode] == value, f"conflicting duplicate label: {barcode}")
            duplicate_rows += 1
        labels[barcode] = value
    return labels, duplicate_rows






class IndexedFasta:
    """Read-only standard uncompressed FASTA/FAI; no aliasing or index creation."""

    def __init__(self, fasta, fai):
        self.index = {}
        size = Path(fasta).stat().st_size
        with open(fai, encoding="utf-8") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                require(len(parts) == 5, "FAI needs five columns")
                name = parts[0]
                length, offset, bases, width = (integer(x, "FAI value") for x in parts[1:])
                require(name and name not in self.index and length > 0 and bases > 0 and
                        width >= bases and width - bases <= 2 and offset < size, "invalid FAI entry")
                require(offset + (length - 1) // bases * width + (length - 1) % bases < size,
                        "FAI extends beyond FASTA")
                self.index[name] = (length, offset, bases, width)
        self.handle = open(fasta, "rb")

    def close(self):
        self.handle.close()

    def fetch(self, chrom, start0, end0):
        length, offset, bases, width = self.index[chrom]
        require(0 <= start0 <= end0 <= length, "FASTA fetch bounds")
        if start0 == end0:
            return ""
        first = offset + start0 // bases * width + start0 % bases
        last = offset + (end0 - 1) // bases * width + (end0 - 1) % bases
        self.handle.seek(first)
        sequence = self.handle.read(last - first + 1).replace(b"\n", b"").replace(b"\r", b"").decode("ascii")
        require(len(sequence) == end0 - start0, "FAI/FASTA fetched length mismatch")
        return sequence


def sequence_axes(helper, genome, chrom, site0, strand):
    """Read at most101 bases; export all context coordinate keys as genomic1."""
    if chrom not in genome.index:
        context = helper.sequence_context(None, chrom, set(), site0 + 1, strand)
        return {"coordinate_frame": "genomic_closed_1based; sequence unavailable, channel coordinates null", "slice_start0": None,
                "genomic_site1": site0 + 1, "context": context}
    length = genome.index[chrom][0]
    require(site0 < length, "candidate proxy outside sequence contig")
    start0, end0 = max(0, site0 - 50), min(length, site0 + 51)
    piece = genome.fetch(chrom, start0, end0)
    context = helper.sequence_context(piece, chrom, {chrom}, site0 - start0 + 1, strand)
    windows = {}
    for name in ("motif", "arich"):
        windows[name] = {}
        for kind in ("requested", "observed"):
            keys = [f"{kind}_chrom_{bound}_1based" for bound in ("start", "end")]
            require(all(key in context[name] for key in keys), "helper r3 window coordinate interface required")
            windows[name][kind + "_closed_interval1"] = [None if context[name][key] is None else context[name][key] + start0 for key in keys]
    for name in ("motif", "arich"):
        for prefix in ("", "requested_", "observed_"):
            for bound in ("start", "end"):
                key = f"{prefix}chrom_{bound}_1based"
                if context[name][key] is not None:
                    context[name][key] += start0
    return {"coordinate_frame": "genomic_closed_1based; requested coordinates may be outside observable contig",
            "slice_start0": start0, "slice_end0": end0,
            "genomic_site1": site0 + 1, "genomic_windows": windows, "context": context}


def load_reference(path, definition, coverage, helper, output, limit):
    require(definition["verified"] is True and definition["coordinate_base"] in {"0based", "1based"}, "unresolved reference")
    require(definition.get("point_semantics") and definition.get("candidate_proxy_distance_accepted") is True,
            "reference point/proxy distance not accepted")
    require(definition.get("source_provenance"), "actual atlas provenance required")
    require(definition.get("queryable_mapping_policy") == "accepted_unique_source_rows_only",
            "queryable reference must explicitly exclude ambiguous mappings")
    require(definition.get("source_coordinate_base") in {"0based", "1based"}, "original source base required")
    contract = helper.ReferenceContract(**{key: definition[key] for key in
        ("source_name", "build", "coordinate_base", "source_sha256", "verified", "release")})
    sidecar = {}
    seen_source_ordinals = set()

    def projected():
        for ordinal, row in rows(path, {"source_pas_id", "source_ordinal", "source_chrom", "source_position", "source_strand", "mapping_status", "chrom", "position", "strand"}):
            require(ordinal < limit, "reference row limit exceeded")
            source_ordinal = integer(row["source_ordinal"], "original source ordinal")
            require(row["mapping_status"] == "accepted_unique", "raw/ambiguous mapping cannot enter queryable reference")
            require(source_ordinal not in seen_source_ordinals, "multiple mappings for one source ordinal cannot enter queryable reference")
            seen_source_ordinals.add(source_ordinal)
            source_position = integer(row["source_position"], "original source position")
            require(definition["source_coordinate_base"] != "1based" or source_position >= 1, "invalid original 1based position")
            require(row["source_chrom"] and row["source_strand"] in {"+", "-"}, "invalid original source chromosome/strand")
            position = integer(row["position"], "reference position")
            require(row["source_pas_id"], "empty original PAS ID")
            sidecar[source_ordinal] = {"normalized_row_ordinal": ordinal, "original": row,
                "source_coordinate_base": definition["source_coordinate_base"]}
            output.write(json.dumps(sidecar[source_ordinal], ensure_ascii=False, allow_nan=False) + "\n")
            yield {"pas_id": row["source_pas_id"], "source_pas_id": row["source_pas_id"],
                   "normalized_key": f"{row['chrom']}:{position}:{row['strand']}", "chrom": row["chrom"],
                   "position": position, "strand": row["strand"], "source_ordinal": source_ordinal}
    return helper.ReferenceIndex.build(projected(), contract), sidecar


class CoverageIndex:
    """Stranded known target exclusions; unknown target losses remain separate."""

    def __init__(self, coverage):
        require(coverage.get("reference_population"), "coverage reference population required")
        loss = coverage.get("unlocalizable_loss", {})
        require(loss.get("target_location") in {"unknown", "none"} and
                (loss.get("source_records") is None or type(loss.get("source_records")) is int and loss["source_records"] >= 0),
                "unlocalizable source loss must be disclosed separately")
        self.coverage, self.index = coverage, {}
        for chrom, state in coverage["contigs"].items():
            require(state.get("status") in {"complete", "complete_with_exclusions", "incomplete", "unloaded"}, "invalid coverage state")
            require("excluded_intervals0" not in state, "legacy exclusion missing explicit base/strand/provenance")
            exclusions = state.get("exclusions", [])
            require(state["status"] != "complete" or not exclusions, "use complete_with_exclusions for local losses")
            for ordinal, item in enumerate(exclusions):
                start, end = item["start"], item["end"]
                require(type(start) is int and type(end) is int, "coverage interval integers required")
                if item.get("coordinate_base") == "0based" and item.get("interval_convention") == "half_open":
                    require(0 <= start < end, "invalid 0based exclusion")
                elif item.get("coordinate_base") == "1based" and item.get("interval_convention") == "closed":
                    require(1 <= start <= end, "invalid 1based exclusion")
                    start -= 1
                else:
                    raise ValueError("exclusion needs explicit 0based half_open or 1based closed coordinates")
                require(item.get("strand") in {"+", "-", "both"}, "exclusion strand required")
                require(item.get("target_localization") == "known" and item.get("reason") and item.get("provenance"),
                        "only known target exclusions with reason/provenance are localizable")
                for strand in ("+", "-") if item["strand"] == "both" else (item["strand"],):
                    self.index.setdefault((chrom, strand), []).append((start, end, ordinal, item))
        for key, values in self.index.items():
            values.sort(key=lambda x: (x[0], x[1], x[2]))
            maximum, prefix = -1, []
            for item in values:
                maximum = max(maximum, item[1])
                prefix.append(maximum)
            self.index[key] = (values, [x[0] for x in values], prefix)

    def assess(self, chrom, strand, site0, query):
        left, right = max(0, site0 - 100), site0 + 101
        values, starts, prefix = self.index.get((chrom, strand), ([], [], []))
        i, hits = bisect_left(starts, right) - 1, []
        while i >= 0 and prefix[i] > left:
            start, end, ordinal, item = values[i]
            if end > left:
                hits.append({"exclusion_ordinal": ordinal, "normalized_interval0": [start, end], **item})
            i -= 1
        hits.sort(key=lambda x: x["exclusion_ordinal"])
        broader = self.coverage["contigs"].get(chrom, {"status": "unloaded"})["status"]
        if query["distance"] is not None and query["distance"] <= 100:
            status, reason = "proximal", "accepted_same_strand_hit_within100"
        elif broader in {"incomplete", "unloaded"}:
            status, reason = "unassessable", "broader_coverage_" + broader
        elif hits:
            status, reason = "unassessable", "known_target_exclusion_overlaps_search"
        else:
            status, reason = "absent", "no_accepted_same_strand_hit_within100_assessed_subset"
        return {"assessment": status, "reason": reason, "broader_status": broader,
                "search_interval0": [left, right], "exclusion_hits": hits}


def emit(handle, value):
    handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def native_evidence(row, helper):
    result = {}
    for key in ("misprimed_ratio", "general_mispriming_pass", "intronic_mispriming_pass",
                "max_cov", "total_polyA_reads", "total_reads", "feature"):
        raw = row.get(key)
        missing = raw is None or raw.strip().upper() in {"", "NA", "NULL", "NAN"}
        value = None if missing else raw
        if not missing and key.endswith("mispriming_pass"):
            value = helper.parse_stored_mispriming(raw)
        if not missing and key == "misprimed_ratio":
            value = float(raw)
            require(math.isfinite(value) and 0 <= value <= 1, "invalid stored native fraction")
        result[key] = {"value": value, "status": "column_not_supplied" if key not in row else "missing" if missing else "stored_observation"}
    return result


def run(request, out, observations, cached):
    from . import mouse_candidates as helper
    # A failed preflight creates no output directory and performs no scientific reads.
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "REQUEST.json", request)
    write_json(out / "INPUT-BINDING.json", request["native"])
    write_json(out / "INPUT-OBSERVATIONS.json", observations)
    inputs = request["inputs"]
    path = lambda role: inputs[role]["path"]
    definition, coverage, sequence_def = (json.loads(cached[role]) for role in
        ("reference_contract", "coverage", "sequence_contract"))
    require(definition["build"] == coverage["build"] == sequence_def["assembly"] == request["assembly"], "build mismatch")
    require(definition.get("reference_population") == coverage.get("reference_population"), "reference population mismatch")
    coverage_index = CoverageIndex(coverage)
    require(sequence_def["fasta_sha256"] == inputs["fasta"]["sha256"] and
            sequence_def["fai_sha256"] == inputs["fai"]["sha256"], "sequence contract binding mismatch")
    require(sequence_def.get("source_provenance") and sequence_def.get("identity_scope"), "sequence provenance/scope missing")
    for role in ("reference_contract", "coverage", "sequence_contract"):
        (out / (role.upper() + ".json")).write_bytes(cached[role])
    labels_available = "labels" in inputs
    labels, label_duplicates = load_labels(rows(path("labels"), {"barcode", "pass_qc"}), helper,
                                          request["limits"]["label_rows"]) if labels_available else ({}, 0)
    with (out / "labels.jsonl").open("w") as label_file:
        for barcode, value in sorted(labels.items()):
            emit(label_file, {"barcode": barcode, **value})
    db = sqlite3.connect(out / "support.sqlite")
    db.execute("PRAGMA cache_size=-16384")
    db.execute("CREATE TABLE sites (peak TEXT PRIMARY KEY, ordinal INTEGER UNIQUE, raw TEXT)")
    db.execute("CREATE TABLE positives (peak TEXT, barcode TEXT, nrows INTEGER, source_rows INTEGER, umis INTEGER, PRIMARY KEY(peak,barcode)) WITHOUT ROWID")
    audit = Counter({key: 0 for key in ("catalogue_rows", "count_rows", "positive_count_rows", "zero_count_rows",
        "candidate_universe_rows", "orphan_positive_pairs", "retained_export_stored_pass",
        "excluded_export_stored_false", "excluded_export_stored_unknown", "reference_absent",
        "reference_proximal", "reference_unassessable")})
    try:
        for ordinal, row in rows(path("catalogue"), {"peak_name", "chrom", "start", "end", "strand", "mispriming_pass"}):
            require(ordinal < request["limits"]["catalogue_rows"], "catalogue row limit exceeded")
            core_row = dict(row, transcript_id=row.get("canonical_transcript"), source_ordinal=ordinal)
            for key in ("gene_id", "gene_name", "transcript_id"):
                value = core_row.get(key)
                if value is None or value.strip().upper() in {"", "NA", "NULL", "NAN"}:
                    core_row[key] = None
            helper.candidate_core(core_row)
            helper.export_scope_status(row["mispriming_pass"])
            native_evidence(row, helper)
            try:
                db.execute("INSERT INTO sites VALUES (?,?,?)", (row["peak_name"], ordinal, json.dumps({"source": row, "core": core_row})))
            except sqlite3.IntegrityError as error:
                raise ValueError("duplicate catalogue peak_name: count-to-site join ambiguous") from error
            audit["catalogue_rows"] += 1
        for ordinal, row in rows(path("counts"), {"peak_name", "cell_barcode", "umi_count"}):
            require(ordinal < request["limits"]["count_rows"], "count row limit exceeded")
            require(row["peak_name"] and row["cell_barcode"], f"empty count key at row {ordinal}")
            count = integer(row["umi_count"], "umi_count")
            require(count <= 2**63 - 1, "UMI exceeds SQLite signed integer range")
            audit["count_rows"] += 1
            if count == 0:
                audit["zero_count_rows"] += 1
            else:
                audit["positive_count_rows"] += 1
            db.execute("INSERT INTO positives VALUES (?,?,?,1,?) ON CONFLICT(peak,barcode) DO UPDATE SET nrows=nrows+excluded.nrows, source_rows=source_rows+1, umis=NULL",
                       (row["peak_name"], row["cell_barcode"], int(count > 0), count))
            if ordinal % 50000 == 0:
                db.commit()
        db.commit()
        audit["duplicate_positive_pair_rows"] = db.execute("SELECT coalesce(sum(nrows-1),0) FROM positives WHERE nrows>0").fetchone()[0]
        audit["duplicate_count_pair_rows"] = db.execute("SELECT coalesce(sum(source_rows-1),0) FROM positives").fetchone()[0]
        audit["orphan_zero_only_pairs"] = db.execute("SELECT count(*) FROM positives p LEFT JOIN sites s USING(peak) WHERE s.peak IS NULL AND p.nrows=0").fetchone()[0]
        require(request["pair_semantics"] != "unique_pairs_required" or audit["duplicate_count_pair_rows"] == 0,
                "duplicate count pairs violate accepted unique-pair semantics")
        with (out / "orphan_positive_support.jsonl").open("w") as orphan:
            for peak, barcode, nrows in db.execute("SELECT p.peak,p.barcode,p.nrows FROM positives p LEFT JOIN sites s USING(peak) WHERE s.peak IS NULL AND p.nrows>0 ORDER BY p.peak,p.barcode"):
                emit(orphan, {"peak_name": peak, "cell_barcode": barcode, "positive_source_rows": nrows})
                audit["orphan_positive_pairs"] += 1
        require(audit["orphan_positive_pairs"] == 0, "count peaks outside catalogue; explicit orphan audit written")
        genome = IndexedFasta(path("fasta"), path("fai"))
        try:
            with (out / "reference_rows.jsonl").open("w") as refs:
                index, sidecar = load_reference(path("reference"), definition, coverage, helper, refs, request["limits"]["reference_rows"])
            audit["reference_rows"] = len(sidecar)
            with (out / "assessment.jsonl").open("w") as assessed, (out / "candidate_universe.jsonl").open("w") as universe, (out / "positive_support.jsonl").open("w") as positives:
                for peak, ordinal, raw in db.execute("SELECT peak,ordinal,raw FROM sites ORDER BY ordinal"):
                    stored = json.loads(raw)
                    row = stored["core"]
                    core = helper.candidate_core(row)
                    if core.chrom in genome.index:
                        require(core.end <= genome.index[core.chrom][0], "candidate interval beyond sequence contig")
                    flag = helper.export_scope_status(row["mispriming_pass"])
                    site0 = core.end - 1 if core.strand == "+" else core.start
                    query = helper.candidate_reference_query(index, request["assembly"], definition["coordinate_base"],
                        core.chrom, core.strand, site0 + (definition["coordinate_base"] == "1based"))
                    for hit in query["nearest"]:
                        hit["source_identity"] = sidecar[hit["source_ordinal"]]
                        hit["normalized_row_ordinal"] = sidecar[hit["source_ordinal"]]["normalized_row_ordinal"]
                    assessment = coverage_index.assess(core.chrom, core.strand, site0, query)
                    status = assessment["assessment"]
                    support = {"n_cells": 0, "positive_source_rows": 0, "assigned_umis": 0 if request["pair_semantics"] == "unique_pairs_required" else None,
                        "n_cells_labelled_intersection": 0 if labels_available else None,
                        "n_cells_unlabelled": 0 if labels_available else None,
                        "n_cells_pass_qc_true": 0 if labels_available else None,
                        "n_cells_pass_qc_false": 0 if labels_available else None,
                        "n_cells_pass_qc_unknown": 0 if labels_available else None,
                        "labels_status": "supplied" if labels_available else "not_supplied",
                        "umi_status": request["pair_semantics"], "detection_status": "no_recorded_positive_detection_not_biological_zero"}
                    for barcode, nrows, umis in db.execute("SELECT barcode,nrows,umis FROM positives WHERE peak=? AND nrows>0 ORDER BY barcode", (peak,)):
                        label = labels.get(barcode)
                        support["n_cells"] += 1
                        support["positive_source_rows"] += nrows
                        if support["assigned_umis"] is not None:
                            support["assigned_umis"] += umis
                        if labels_available:
                            support["n_cells_labelled_intersection"] += label is not None
                            support["n_cells_unlabelled"] += label is None
                            if label is not None:
                                suffix = "true" if label["pass_qc"] is True else "false" if label["pass_qc"] is False else "unknown"
                                support["n_cells_pass_qc_" + suffix] += 1
                        emit(positives, {"catalogue_row_ordinal": ordinal, "peak_name": peak, "cell_barcode": barcode,
                            "positive_source_rows": nrows, "assigned_umis": umis if request["pair_semantics"] == "unique_pairs_required" else None,
                            "label_present": (label is not None) if labels_available else None,
                            "label_barcode": barcode if label else None, "pass_qc": label["pass_qc"] if label else None})
                    if support["n_cells"]:
                        support["detection_status"] = "observed_positive"
                    result = {"catalogue_row_ordinal": ordinal, "peak_name": peak, "raw_catalogue": stored["source"],
                        "native_evidence": native_evidence(row, helper),
                        "gene_annotation": {"gene_id": core.gene_id, "gene_name": core.gene_name, "transcript_id": core.transcript_id},
                        "coordinates": {"interval0": [core.start, core.end], "chrom": core.chrom, "strand": core.strand,
                            "last_templated_base_proxy0": site0, "display_base1": site0 + 1,
                            "interbase_boundary0": core.end if core.strand == "+" else core.start},
                        "stored_native_flag": flag, "reference": {**assessment, "query": query,
                            "reference_population": definition["reference_population"],
                            "unlocalizable_loss": coverage["unlocalizable_loss"],
                            "distance_scope": "accepted_chain_mapped_rows_only; no original/native-mm10 atlas completeness claim"},
                        "support": support, "sequence": sequence_axes(helper, genome, core.chrom, site0, core.strand),
                        "unavailable_channels": {"secondary_reference": "not_acquired", "deepcps": "not_available",
                            "tail_end_distribution": "not_available", "tracks": "not_available", "utr_semantics": "unresolved"}}
                    audit[flag["status"]] += 1
                    audit["reference_" + status] += 1
                    audit["coverage_reason_" + assessment["reason"]] += 1
                    for exclusion in assessment["exclusion_hits"]:
                        audit["exclusion_overlap_reason_" + exclusion["reason"]] += 1
                    if core.chrom not in genome.index:
                        audit["contig_missing_sequence_" + core.chrom] += 1
                    if core.chrom not in coverage["contigs"]:
                        audit["coverage_contig_not_declared_" + core.chrom] += 1
                    audit[flag["status"] + "_reference_" + status] += 1
                    for channel in ("motif", "arich"):
                        audit[channel + "_" + result["sequence"]["context"][channel]["status"]] += 1
                    for channel, value in result["native_evidence"].items():
                        audit["native_" + channel + "_" + value["status"]] += 1
                    emit(assessed, result)
                    if flag["stored_mispriming_pass"] is True and status == "absent":
                        emit(universe, result)
                        audit["candidate_universe_rows"] += 1
        finally:
            genome.close()
        audit["identical_duplicate_label_rows"] = label_duplicates
        audit["label_barcodes"] = len(labels)
        audit["all_positive_barcodes"] = db.execute("SELECT count(DISTINCT barcode) FROM positives WHERE nrows>0").fetchone()[0]
        audit["unmatched_positive_barcodes"] = (sum(1 for (barcode,) in db.execute("SELECT DISTINCT barcode FROM positives WHERE nrows>0") if barcode not in labels)
                                                 if labels_available else None)
        with (out / "cell_cohort.jsonl").open("w") as cohort:
            for (barcode,) in db.execute("SELECT DISTINCT barcode FROM positives WHERE nrows>0 ORDER BY barcode"):
                label = labels.get(barcode)
                emit(cohort, {"cell_barcode": barcode, "label_present": (label is not None) if labels_available else None,
                              "label_barcode": barcode if label else None, "pass_qc": label["pass_qc"] if label else None})
        for role, pin in inputs.items():
            require(file_state(pin["path"]) == observations["inputs"][role], f"input changed during run: {role}")
        write_json(out / "SUMMARY.json", {"schema": VERSION, "status": "COMPLETED_SYNTHETIC" if request["mode"] == "synthetic" else "COMPLETED_CURRENT_SNAPSHOT" if request["mode"] == "native_snapshot" else "COMPLETED_HISTORICAL_EXPORT",
            "scope": request["scope"], "execution": execution_binding(), "labels_available": labels_available, "audit": dict(audit),
            "definition": "stored true AND no accepted hit within100 AND assessed chain-mapped subset search window without known target exclusion",
            "reference_population": definition["reference_population"], "unlocalizable_loss": coverage["unlocalizable_loss"],
            "depth_filter": "none; total_polyA_reads/max_cov never substituted for native per-base depth",
            "claims": "absence FROM ACCEPTED CHAIN-MAPPED PolyA_DB3.2 subset only; no full original/native-mm10 atlas completeness or biological absence/discovery/neuron-specific/validation claim"})
    finally:
        db.close()
