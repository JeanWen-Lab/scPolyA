"""Finite, receipt-gated transform-output projection; no liftOver or candidate query."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys

SCHEMA = "scpolya.cs3.reference-projection.v1"
CODE_PATH = str(Path(__file__).resolve())
CODE_SHA256 = hashlib.sha256(Path(CODE_PATH).read_bytes()).hexdigest()
SOURCE = ["ordinal", "source_pas_id", "source_chromosome", "source_position_1based",
          "source_position_0based", "source_strand", "source_bed_start", "source_bed_end"]
STATUS = SOURCE + ["status", "target_chromosome", "target_bed_start", "target_bed_end",
                   "target_position_1based", "target_strand", "audit_row_count"]
REF = ["source_pas_id", "source_ordinal", "source_chrom", "source_position", "source_strand",
       "chrom", "position", "strand", "mapping_status", "normalized_row_ordinal", "normalized_key"]
ROLES = {"status", "source_map", "audit_mapped", "chain", "source_sizes", "target_sizes",
         "source_coverage", "sequence_contract", "fasta", "fai", "catalogue", "transform_manifest", "transform_contract", "adapter_template"}
ALLOWED = {"ACCEPTED", "UNMAPPED", "MULTIPLE", "NONSTANDARD_TARGET", "OFFBOUNDS",
           "CROSSWALK_MISMATCH", "STRAND_MISMATCH"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def dump(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    os.replace(temporary, path)


def emit(handle, value):
    handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def state(path):
    value = Path(path).stat()
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def number(value, name, minimum=0):
    text = str(value)
    require(not isinstance(value, bool) and text.isascii() and text.isdigit(), f"invalid integer {name}")
    parsed = int(text)
    require(parsed >= minimum, f"invalid integer range {name}")
    return parsed


def table(path, required, limit):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="") as handle:
        def physical():
            for line in handle:
                require('"' not in line and line.rstrip("\r\n"), "quote/blank physical TSV line")
                yield line
        reader = csv.DictReader(physical(), delimiter="\t", quoting=csv.QUOTE_NONE)
        require(reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames)
                and set(required) <= set(reader.fieldnames), f"table schema mismatch: {path}")
        for ordinal, row in enumerate(reader):
            require(ordinal < limit and None not in row and None not in row.values(), "table limit/ragged row")
            yield ordinal, row


def sizes(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        fields = line.split("\t")
        require(len(fields) == 2 and fields[0] not in result, "sizes schema/duplicate")
        result[fields[0]] = number(fields[1], "contig length", 1)
    return result


def merge(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def complement(intervals, length):
    previous, result = 0, []
    for start, end in merge(intervals):
        require(0 <= start < end <= length, "covered target block outside official bounds")
        if previous < start:
            result.append([previous, start])
        previous = end
    if previous < length:
        result.append([previous, length])
    return result


class Chain:
    """t=source plus; q=target genomic. No reverse-direction assumption by filename."""

    def __init__(self, path, source_sizes, target_sizes, assessed_source, limit):
        self.by_source, self.target_blocks = defaultdict(list), defaultdict(list)
        ids, current, terminal, count = set(), None, False, 0
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                fields = raw.split()
                if fields[0] == "chain":
                    require(current is None or terminal, "unterminated chain")
                    require(len(fields) == 13 and fields[4] == "+" and fields[9] in {"+", "-"}, "unsupported chain header/strands")
                    _, score, t, ts, _, ta, tb, q, qs, orient, qa, qb, cid = fields
                    ts, ta, tb, qs, qa, qb, cid = (number(x, "chain") for x in (ts, ta, tb, qs, qa, qb, cid))
                    require(cid not in ids and source_sizes.get(t) == ts and target_sizes.get(q) == qs,
                            "duplicate chain ID or source/target side size mismatch")
                    require(0 <= ta < tb <= ts and 0 <= qa < qb <= qs, "chain header bounds")
                    ids.add(cid)
                    current, terminal = [t, ta, tb, q, qs, qa, qb, orient, cid, 0], False
                    continue
                require(current is not None and not terminal and len(fields) in {1, 3}, "chain block schema")
                length = number(fields[0], "block size", 1)
                dt, dq = (number(x, "gap") for x in fields[1:]) if len(fields) == 3 else (0, 0)
                t, tp, te, q, qsize, qp, qe, orient, cid, block_id = current
                require(tp + length <= te and qp + length <= qe, "chain block extends beyond header")
                qstart, qend = (qp, qp + length) if orient == "+" else (qsize - qp - length, qsize - qp)
                self.by_source[t].append((tp, tp + length, q, qstart, qend, orient, cid, block_id))
                if t in assessed_source:
                    self.target_blocks[q].append((qstart, qend))
                count += 1
                require(count <= limit, "chain block limit")
                current = [t, tp + length + dt, te, q, qsize, qp + length + dq, qe, orient, cid, block_id + 1]
                if len(fields) == 1:
                    require(current[1] == te and current[5] == qe, "chain final cursor/header mismatch")
                    terminal = True
        require(current is not None and terminal, "empty or unfinished chain")
        self.block_count, self.index = count, {}
        for chrom, blocks in self.by_source.items():
            blocks.sort(key=lambda x: (x[0], x[1], x[6], x[7]))
            maximum, prefix = -1, []
            for block in blocks:
                maximum = max(maximum, block[1])
                prefix.append(maximum)
            self.index[chrom] = (blocks, [x[0] for x in blocks], prefix)

    def mapped_points(self, chrom, pos0, strand):
        blocks, starts, prefix = self.index.get(chrom, ([], [], []))
        result, i = [], bisect_right(starts, pos0) - 1
        while i >= 0 and prefix[i] > pos0:
            start, end, q, qa, qb, orientation, cid, block_id = blocks[i]
            if start <= pos0 < end:
                target = qa + pos0 - start if orientation == "+" else qb - 1 - (pos0 - start)
                target_strand = strand if orientation == "+" else ("-" if strand == "+" else "+")
                result.append((q, target, target + 1, target_strand))
            i -= 1
        return result




def project(request, output, snapshots, metadata):
    pins, limits = request["inputs"], request["limits"]
    path = lambda key: pins[key]["path"]
    source_sizes, target_sizes = sizes(path("source_sizes")), sizes(path("target_sizes"))
    fai = {}
    for line in Path(path("fai")).read_text().splitlines():
        fields = line.split("\t")
        require(len(fields) == 5 and fields[0] not in fai, "FAI schema/duplicate")
        fai[fields[0]] = number(fields[1], "FAI length", 1)
    sequence = metadata["sequence_contract"]
    for role in ("fasta", "fai"):
        require(sequence[role + "_path"] == path(role) and sequence[role + "_sha256"] == pins[role]["sha256"], "exact accepted sequence path/hash mismatch")
    require(sequence["assembly"] == request["target_build"], "sequence build mismatch")
    catalogue_contigs = {row["chrom"] for _, row in table(path("catalogue"), {"chrom"}, limits["catalogue_rows"])}
    require(catalogue_contigs <= fai.keys(), "actual catalogue contig absent from FAI")
    manifest = metadata["transform_manifest"]
    require(set(STATUS) <= set(metadata["transform_contract"]["status_contract"]["columns"]), "frozen transform status contract incompatible")
    require(manifest.get("all_checks_pass") is True, "transform accounting not accepted/passing")
    require(manifest["chain"]["sha256"] == pins["chain"]["sha256"] and manifest.get("candidate_queries_run") == 0,
            "transform manifest identity/scope mismatch")
    source_cov = metadata["source_coverage"]
    require(source_cov["source_sha256"] == manifest["source_table"]["sha256"] and
            source_cov.get("assessment") == "complete_named_source_payload", "source atlas coverage/provenance pending")
    assessed = set(source_cov["assessed_source_contigs"])
    require(assessed <= source_sizes.keys(), "source coverage contig missing official size")
    targets = set(request["assessed_target_contigs"])
    require(len(targets) == len(request["assessed_target_contigs"]) and targets <= target_sizes.keys() and targets <= fai.keys(), "target assessment contigs/FAI mismatch")
    require(all(target_sizes[c] == fai[c] for c in targets), "official target vs FAI lengths mismatch")
    chain = Chain(path("chain"), source_sizes, target_sizes, assessed, limits["chain_blocks"])
    sources = {}
    for physical, row in table(path("source_map"), SOURCE, limits["source_rows"]):
        ordinal = number(row["ordinal"], "source ordinal", 1)
        require(ordinal == physical + 1 and row["source_pas_id"] and row["source_strand"] in {"+", "-"}, "source physical ordinal/identity/strand")
        s, e = number(row["source_bed_start"], "source start"), number(row["source_bed_end"], "source end", 1)
        require(e == s + 1 == number(row["source_position_1based"], "source1", 1)
                and s == number(row["source_position_0based"], "source0")
                and row["source_chromosome"] in assessed and e <= source_sizes[row["source_chromosome"]], "source coordinates/coverage mismatch")
        sources[ordinal] = row
    statuses = {}
    for physical, row in table(path("status"), STATUS, limits["source_rows"]):
        ordinal = number(row["ordinal"], "status ordinal", 1)
        require(ordinal == physical + 1 and ordinal in sources and all(row[k] == sources[ordinal][k] for k in SOURCE), "status/source identity disagreement")
        require(row["status"] in ALLOWED, "unresolved/fatal/unknown transform disposition")
        statuses[ordinal] = row
    require(set(statuses) == set(sources) and len(sources) == manifest["input_ordinal_count"], "full source accounting mismatch")
    require(dict(Counter(x["status"] for x in statuses.values())) == manifest["status_counts"], "manifest status counts mismatch")
    audit, audit_rows = defaultdict(list), []
    with open(path("audit_mapped")) as handle:
        for physical, raw in enumerate(handle):
            require(physical < limits["audit_rows"] and '"' not in raw, "audit limit/quote")
            fields = raw.rstrip("\r\n").split("\t")
            require(len(fields) == 6, "raw audit BED6 required")
            chrom, s, e, ordinal, score, strand = fields
            ordinal, s, e = number(ordinal, "audit source ordinal", 1), number(s, "audit start"), number(e, "audit end")
            require(ordinal in sources and chrom in fai and chrom in target_sizes and target_sizes[chrom] == fai[chrom], "audit source ordinal/actual target contig absent from FAI/official sizes")
            require(strand in {"+", "-"} and number(score, "audit score") == 1000 and 0 <= s < e <= target_sizes[chrom], "invalid/offbounds audit geometry; projection requires reviewed handling")
            item = {"audit_row_ordinal": physical, "source_ordinal": ordinal, "raw_line": raw.rstrip("\r\n"),
                    "chrom": chrom, "start0": s, "end0": e, "strand": strand, "status": statuses[ordinal]["status"]}
            audit[ordinal].append(item)
            audit_rows.append(item)
    require(len(audit_rows) == manifest["audit_metrics"]["audit_mapped_rows"], "manifest raw audit multiplicity mismatch")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "REQUEST.json", request)
    exclusions, loss_reasons, unknown, accepted = defaultdict(list), Counter(), set(), []
    counts = Counter()
    with (output / "dispositions.jsonl").open("w") as dispositions, (output / "audit_mappings.jsonl").open("w") as raw_audit:
        for item in audit_rows:
            emit(raw_audit, item)
        for ordinal, source in sources.items():
            status, rows = statuses[ordinal], audit[ordinal]
            require(number(status["audit_row_count"], "audit cardinality") == len(rows), "status vs raw audit cardinality mismatch")
            expected = chain.mapped_points(source["source_chromosome"], int(source["source_bed_start"]), source["source_strand"])
            observed = [(r["chrom"], r["start0"], r["end0"], r["strand"]) for r in rows]
            if status["status"] == "ACCEPTED":
                require(len(rows) == len(expected) == 1 and observed == expected, "accepted mapping not uniquely chain/audit reconciled")
                target = (status["target_chromosome"], number(status["target_bed_start"], "target start"),
                          number(status["target_bed_end"], "target end"), status["target_strand"])
                require(target == observed[0] and int(status["target_position_1based"]) == target[1] + 1 and target[2] == target[1] + 1
                        and target[0] in targets and target[0] != "chrM", "accepted target/status mismatch or outside declared assessment")
                accepted.append([source["source_pas_id"], ordinal, source["source_chromosome"], source["source_position_1based"], source["source_strand"],
                                 target[0], target[1] + 1, target[3], "accepted_unique", len(accepted), f"{target[0]}:{target[1]+1}:{target[3]}"])
            elif status["status"] == "UNMAPPED":
                require(not rows and not expected, "unmapped source has raw/chain target candidates")
                unknown.add(ordinal)
                loss_reasons["unmapped_without_observed_target"] += 1
            else:
                require(rows, "excluded disposition has no observed target; unresolved source loss")
                if status["status"] == "MULTIPLE":
                    require(len(rows) > 1 and Counter(observed) == Counter(expected), "multiple raw/chain multiplicity mismatch")
                for item in rows:
                    exclusions[item["chrom"]].append({"start": item["start0"], "end": item["end0"], "coordinate_base": "0based",
                        "interval_convention": "half_open", "strand": "both" if status["status"] in {"STRAND_MISMATCH", "CROSSWALK_MISMATCH"} else item["strand"],
                        "target_localization": "known", "reason": "excluded_" + status["status"],
                        "provenance": {"source_ordinal": ordinal, "audit_row_ordinal": item["audit_row_ordinal"], "audit_sha256": pins["audit_mapped"]["sha256"],
                                       "strand_scope": "both conservatively when chain/strand evidence conflicts"}})
            counts[status["status"]] += 1
            emit(dispositions, {"source_ordinal": ordinal, "source": source, "transform_status": status,
                "queryable": status["status"] == "ACCEPTED", "observed_audit_rows": [x["audit_row_ordinal"] for x in rows],
                "unlocalizable_valid_target": ordinal in unknown})
    coverage, unions = {}, {}
    for chrom in sorted(targets | catalogue_contigs | set(exclusions)):
        require(chrom in fai, "actual projected coverage contig absent from FAI")
        known = exclusions[chrom]
        union = merge(chain.target_blocks.get(chrom, [])) if chrom in targets and chrom != "chrM" else []
        unions[chrom] = union
        if chrom in targets and chrom != "chrM":
            for start, end in complement(union, target_sizes[chrom]):
                known.append({"start": start, "end": end, "coordinate_base": "0based", "interval_convention": "half_open", "strand": "both",
                    "target_localization": "known", "reason": "target_chain_block_complement",
                    "provenance": {"chain_sha256": pins["chain"]["sha256"], "source_side": "t", "target_side": "q_genomic", "source_coverage_sha256": pins["source_coverage"]["sha256"]}})
        coverage[chrom] = {"status": "unloaded" if not union else "complete_with_exclusions" if known else "complete", "exclusions": known,
                          "scope": "accepted chain-mapped subset assessment only; no original atlas completeness"}
    loss = {"source_records": len(unknown), "target_location": "unknown" if unknown else "none", "reason_counts": dict(loss_reasons),
            "scope": "excluded source rows without any observed target; no fabricated target interval"}
    require(type(loss["source_records"]) is int and (loss["target_location"] == "none") == (loss["source_records"] == 0), "unlocalizable loss consistency")
    population = request["atlas"]["reference_population"]
    with (output / "reference.tsv").open("w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_NONE, lineterminator="\n")
        writer.writerow(REF)
        writer.writerows(accepted)
    dump(output / "coverage.json", {"build": request["target_build"], "reference_population": population,
         "unlocalizable_loss": loss, "contigs": coverage})
    dump(output / "covered-target-blocks.json", {"coordinate_frame": "target q genomic 0based half-open", "unions": unions,
         "warning": "source-only dt gaps do not imply target dq gaps; target complement does not locate unmapped source records"})
    dump(output / "reference-contract.json", {"source_name": request["atlas"]["name"],
         "release": request["atlas"]["release"],
         "build": request["target_build"], "coordinate_base": "1based", "source_coordinate_base": request["normalized_source_coordinate_base"],
         "source_sha256": manifest["source_table"]["sha256"], "verified": True,
         "point_semantics": request["point_semantics"],
         "candidate_proxy_distance_accepted": True, "queryable_mapping_policy": "accepted_unique_source_rows_only", "reference_population": population,
         "source_provenance": {"atlas_declaration": request["atlas"], "input_pins": pins, "transform_receipt": request["transform_receipt"], "projection_code_sha256": CODE_SHA256,
             "review_scope": "synthetic only" if request["mode"] == "synthetic" else "new task-bound transform/projection; independent review status is external to this computation"}})
    for role, pin in pins.items():
        require(state(pin["path"]) == snapshots[role], f"input changed during projection: {role}")
    dump(output / "SUMMARY.json", {"schema": SCHEMA, "status": "COMPLETED_SYNTHETIC" if request["mode"] == "synthetic" else "COMPLETED_REFERENCE_PREPARATION",
        "projection_code_sha256": CODE_SHA256, "python": sys.version, "source_rows": len(sources), "status_counts": dict(counts),
        "accepted_reference_rows": len(accepted), "raw_audit_rows": len(audit_rows), "chain_blocks": chain.block_count,
        "unlocalizable_loss": loss, "input_pins": pins, "input_states": snapshots,
        "claim_scope": f"absence FROM {request['atlas']['name']} release {request['atlas']['release']} accepted chain-mapped subset only; no full original/native-target atlas or biological absence claim",
        "production_readiness_created": False, "candidate_query_run": False})
