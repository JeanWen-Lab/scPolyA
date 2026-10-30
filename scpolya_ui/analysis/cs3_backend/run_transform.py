#!/usr/bin/env python3
"""Bounded PolyA_DB 3.2 mouse liftOver runner and accounting implementation.

Synthetic self-tests are permitted.  A real run is refused unless the exact
review authorization token and all pinned inputs are supplied.  Run with:
    /usr/bin/python3 -I -B run_transform.py ...
This program uses Python's standard library only.
"""

import argparse
import bisect
import collections
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

STANDARD = tuple([f"chr{i}" for i in range(1, 20)] + ["chrX", "chrY"])
STANDARD_SET = set(STANDARD)



def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_argv(input_bed, chain, mapped, unmapped, binary):
    return [
        str(binary), "-minMatch=1.0",
        str(input_bed), str(chain), str(mapped), str(unmapped),
    ]


def audit_argv(input_bed, chain, mapped, unmapped, binary):
    # minSizeT is intentionally absent: it is a deprecated synonym for
    # minChainT and supplying both makes the pinned v504 binary exit 255.
    return [
        str(binary), "-minMatch=1.0", "-multiple", "-noSerial",
        "-minChainT=0", "-minChainQ=0", "-minSizeQ=0",
        str(input_bed), str(chain), str(mapped), str(unmapped),
    ]


def run_exact_argv(argv, stdout_path, stderr_path, argv_path, exit_path):
    completed = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    Path(stdout_path).write_text(completed.stdout)
    Path(stderr_path).write_text(completed.stderr)
    Path(argv_path).write_text(json.dumps(argv, indent=2) + "\n")
    Path(exit_path).write_text(f"{completed.returncode}\n")
    if completed.returncode != 0:
        raise RuntimeError(
            f"liftOver exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed


def load_sizes(path):
    path = Path(path)
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        return {str(k): int(v) for k, v in data.items()}
    out = {}
    with path.open() as fh:
        for line_number, raw in enumerate(fh, 1):
            fields = raw.rstrip("\n").split("\t")
            if len(fields) != 2:
                raise ValueError(f"{path}:{line_number}: expected two fields")
            out[fields[0]] = int(fields[1])
    return out


def validate_bed_file(path, expected_names=None, require_unique_names=True):
    rows = []
    seen = set()
    with Path(path).open() as fh:
        for line_number, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            fields = raw.rstrip("\n").split("\t")
            if len(fields) != 6:
                raise ValueError(f"{path}:{line_number}: expected BED6")
            try:
                start, end, score = int(fields[1]), int(fields[2]), int(fields[4])
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: noninteger BED field") from exc
            if require_unique_names and fields[3] in seen:
                raise ValueError(f"{path}:{line_number}: duplicate BED name {fields[3]}")
            seen.add(fields[3])
            rows.append((fields[0], start, end, fields[3], score, fields[5]))
    if expected_names is not None and seen != set(expected_names):
        missing = sorted(set(expected_names) - seen)
        extra = sorted(seen - set(expected_names))
        raise ValueError(f"{path}: name mismatch; missing={missing}, extra={extra}")
    return rows


def parse_unmapped(path):
    reasons = []
    reasons_by_name = {}
    pending = []
    row_count = 0
    with Path(path).open() as fh:
        for raw in fh:
            if raw.startswith("#"):
                text = raw.strip()
                if text not in reasons:
                    reasons.append(text)
                normalized_text = ("DELETED_IN_NEW" if text == "#Deleted in new"
                                   else "DUPLICATED_IN_NEW" if text == "#Duplicated in new"
                                   else "UNEXPECTED:" + text)
                pending.append(normalized_text)
                continue
            if not raw.strip():
                continue
            fields = raw.rstrip("\n").split("\t")
            if len(fields) != 6:
                raise ValueError(f"{path}: malformed unmapped BED6 row")
            row_count += 1
            reasons_by_name.setdefault(fields[3], []).extend(pending or ["UNEXPECTED:NO_REASON_COMMENT"])
            pending = []
    normalized = []
    for text in reasons:
        if text == "#Deleted in new":
            normalized.append("DELETED_IN_NEW")
        elif text == "#Duplicated in new":
            normalized.append("DUPLICATED_IN_NEW")
        else:
            normalized.append("UNEXPECTED:" + text)
    return row_count, normalized, reasons_by_name


class MappedOutputIndex:
    """Bounded per-ordinal index over a raw liftOver mapped BED.

    Accounting needs multiplicity and the first raw row; it does not need to
    retain duplicate mappings in memory.  The complete raw file remains the
    machine-readable excluded-mapping export.
    """

    def __init__(self):
        self.by_name = {}

    def add(self, row):
        name = row[3]
        current = self.by_name.get(name)
        if current is None:
            self.by_name[name] = {"count": 1, "first": row, "all_same": True}
        else:
            if not current["all_same"]:
                current["mapping_counts"][row] += 1
            elif not tuple_equal(current["first"], row):
                current["mapping_counts"] = collections.Counter({current["first"]: current["count"]})
                current["mapping_counts"][row] += 1
            current["count"] += 1
            current["all_same"] = current["all_same"] and tuple_equal(current["first"], row)

    def rows(self, name):
        return [] if name not in self.by_name else [self.by_name[name]["first"]]

    def count(self, name):
        return self.by_name.get(name, {}).get("count", 0)

    def mapping_multiset(self, name):
        """Complete BED6 multiset; identical duplicates use only a count."""
        current = self.by_name.get(name)
        if current is None:
            return collections.Counter()
        if current["all_same"]:
            return collections.Counter({current["first"]: current["count"]})
        return current["mapping_counts"]


def parse_bed_outputs(path):
    """Validate and index raw liftOver output by physical BED name."""
    output = MappedOutputIndex()
    with Path(path).open() as fh:
        for line_number, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            fields = raw.rstrip("\n").split("\t")
            if len(fields) != 6:
                raise ValueError(f"{path}:{line_number}: expected BED6")
            try:
                row = (fields[0], int(fields[1]), int(fields[2]), fields[3],
                       int(fields[4]), fields[5])
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: noninteger BED field") from exc
            output.add(row)
    return output


class ChainIndex:
    """Target-side interval index with exact reverse-query conversion."""

    def __init__(self):
        self.by_target = collections.defaultdict(list)
        self._keys = collections.defaultdict(list)
        self._prefix_max_end = collections.defaultdict(list)
        self.chain_ids = set()
        self.record_count = 0
        self.block_count = 0
        self.target_strands = collections.Counter()
        self.query_strands = collections.Counter()

    def finalize(self):
        for chrom in self.by_target:
            self.by_target[chrom].sort(key=lambda x: (x[0], x[1], x[2]["chain_id"], x[2]["block_index"]))
            self._keys[chrom] = [block[0] for block in self.by_target[chrom]]
            maximum = -1
            self._prefix_max_end[chrom] = []
            for entry in self.by_target[chrom]:
                maximum = max(maximum, entry[1])
                self._prefix_max_end[chrom].append(maximum)

    def candidates(self, chrom, start):
        blocks = self.by_target.get(chrom, ())
        keys = self._keys[chrom]
        right = bisect.bisect_right(keys, start)
        out = []
        # Prefix maximum end permits a reverse scan while retaining every
        # overlapping chain, including a long block that starts far earlier.
        for index in range(right - 1, -1, -1):
            if self._prefix_max_end[chrom][index] <= start:
                break
            entry = blocks[index]
            if entry[0] <= start < entry[1]:
                block = entry[2]
                out.append(block)
        return out


def build_chain_index(chain_path, source_sizes=None, target_sizes=None):
    index = ChainIndex()
    current = None

    def add_header(fields):
        if len(fields) != 13:
            raise ValueError(f"chain header does not have 13 fields: {fields}")
        if fields[4] != "+":
            raise ValueError("negative target chains are not accepted")
        chain_id = int(fields[12])
        if chain_id in index.chain_ids:
            raise ValueError(f"duplicate chain ID {chain_id}")
        index.chain_ids.add(chain_id)
        return {
            "chain_id": chain_id,
            "target": fields[2], "target_size": int(fields[3]), "target_strand": fields[4],
            "target_start": int(fields[5]), "target_end": int(fields[6]),
            "query": fields[7], "query_size": int(fields[8]), "query_strand": fields[9],
            "query_start": int(fields[10]), "query_end": int(fields[11]),
            "target_pos": int(fields[5]), "query_pos": None,
            "block_index": 0,
        }

    opener = gzip.open if str(chain_path).endswith(".gz") else open
    with opener(chain_path, "rt") as fh:
        for line_number, raw in enumerate(fh, 1):
            if raw.startswith("chain "):
                if current is not None:
                    index.record_count += 1
                current = add_header(raw.split())
                index.target_strands[current["target_strand"]] += 1
                index.query_strands[current["query_strand"]] += 1
                continue
            if not raw.strip() or current is None:
                continue
            fields = raw.split()
            if len(fields) not in (1, 3):
                raise ValueError(f"{chain_path}:{line_number}: expected one or three fields")
            size = int(fields[0])
            dt = int(fields[1]) if len(fields) == 3 else 0
            dq = int(fields[2]) if len(fields) == 3 else 0
            current["block_index"] += 1
            index.block_count += 1
            # qStart/qEnd are coordinates on the strand as represented by
            # qStrand.  Convert an entire block to genomic BED bounds below;
            # this is essential for negative-query chains.
            query_start = current["query_start"] if current["query_pos"] is None else current["query_pos"]
            block = {
                "chain_id": current["chain_id"],
                "block_index": current["block_index"],
                "query_strand": current["query_strand"],
                "target_block_start": current["target_pos"],
                "target_block_end": current["target_pos"] + size,
                "query_block_start": query_start,
                "query_block_end": query_start + size,
                "query_chromosome": current["query"],
                "query_size": current["query_size"],
                "query_strand_relative_start": query_start,
                "query_strand_relative_end": query_start + size,
                "query_genomic_block_start": current["query_size"] - (query_start + size),
                "query_genomic_block_end": current["query_size"] - query_start,
            }
            index.by_target[current["target"]].append(
                (current["target_pos"], current["target_pos"] + size, block)
            )
            if source_sizes is not None and current["target"] in source_sizes:
                if current["target_size"] != source_sizes[current["target"]]:
                    raise ValueError(f"chain source size mismatch for {current['target']}")
            if target_sizes is not None and current["query"] in target_sizes:
                if current["query_size"] != target_sizes[current["query"]]:
                    raise ValueError(f"chain target size mismatch for {current['query']}")
            current["target_pos"] += size + dt
            if current["query_strand"] == "-":
                current["query_pos"] = query_start + size + dq
            else:
                current["query_pos"] = query_start + size + dq
    if current is not None:
        index.record_count += 1
    index.finalize()
    return index


SOURCE_HEADER = [
    "ordinal", "source_pas_id", "source_chromosome", "source_position_1based",
    "source_position_0based", "source_strand", "source_bed_start", "source_bed_end",
]


def write_source_map(path, metadata_rows):
    with Path(path).open("w") as fh:
        fh.write("\t".join(SOURCE_HEADER) + "\n")
        for row in metadata_rows:
            fh.write("\t".join(str(row[k]) for k in SOURCE_HEADER) + "\n")


def read_source_map(path, expected_count=None):
    rows = []
    with Path(path).open() as fh:
        header = fh.readline().rstrip("\n").split("\t")
        if header != SOURCE_HEADER:
            raise ValueError(f"{path}: source-map header mismatch")
        for line_number, raw in enumerate(fh, 2):
            fields = raw.rstrip("\n").split("\t")
            if len(fields) != len(SOURCE_HEADER):
                raise ValueError(f"{path}:{line_number}: wrong source-map width")
            row = dict(zip(SOURCE_HEADER, fields))
            row["ordinal"] = int(row["ordinal"])
            row["source_position_1based"] = int(row["source_position_1based"])
            row["source_position_0based"] = int(row["source_position_0based"])
            row["source_bed_start"] = int(row["source_bed_start"])
            row["source_bed_end"] = int(row["source_bed_end"])
            rows.append(row)
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(f"{path}: expected {expected_count} rows, got {len(rows)}")
    return rows


def iter_source_map(path):
    """Stream source metadata with sequential physical-ordinal enforcement."""
    with Path(path).open() as fh:
        header = fh.readline().rstrip("\n").split("\t")
        if header != SOURCE_HEADER:
            raise ValueError(f"{path}: source-map header mismatch")
        ordinal = 0
        for line_number, raw in enumerate(fh, 2):
            fields = raw.rstrip("\n").split("\t")
            if len(fields) != len(SOURCE_HEADER):
                raise ValueError(f"{path}:{line_number}: wrong source-map width")
            row = dict(zip(SOURCE_HEADER, fields))
            try:
                row["ordinal"] = int(row["ordinal"])
                row["source_position_1based"] = int(row["source_position_1based"])
                row["source_position_0based"] = int(row["source_position_0based"])
                row["source_bed_start"] = int(row["source_bed_start"])
                row["source_bed_end"] = int(row["source_bed_end"])
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: noninteger source field") from exc
            ordinal += 1
            if row["ordinal"] != ordinal:
                raise ValueError(f"{path}:{line_number}: physical ordinal is not sequential")
            if (row["source_position_1based"] != row["source_bed_end"] or
                    row["source_position_0based"] != row["source_bed_start"] or
                    row["source_bed_end"] != row["source_bed_start"] + 1):
                raise ValueError(f"{path}:{line_number}: source coordinate representations disagree")
            if row["source_strand"] not in ("+", "-"):
                raise ValueError(f"{path}:{line_number}: invalid source strand")
            yield row


def validate_input_bed(source_map, input_bed):
    """Validate the one-base BED sidecar against streamed source metadata."""
    count = 0
    with Path(source_map).open() as map_fh, Path(input_bed).open() as bed_fh:
        map_header = map_fh.readline().rstrip("\n").split("\t")
        if map_header != SOURCE_HEADER:
            raise ValueError(f"{source_map}: source-map header mismatch")
        for line_number, (map_raw, bed_raw) in enumerate(zip(map_fh, bed_fh), 2):
            map_fields = map_raw.rstrip("\n").split("\t")
            bed_fields = bed_raw.rstrip("\n").split("\t")
            if len(map_fields) != len(SOURCE_HEADER):
                raise ValueError(f"{source_map}:{line_number}: wrong source-map width")
            if len(bed_fields) != 6:
                raise ValueError(f"{input_bed}:{line_number}: expected BED6")
            try:
                metadata = dict(zip(SOURCE_HEADER, map_fields))
                ordinal = int(metadata["ordinal"])
                start = int(bed_fields[1])
                end = int(bed_fields[2])
                score = int(bed_fields[4])
            except ValueError as exc:
                raise ValueError(f"{input_bed}:{line_number}: noninteger field") from exc
            if ordinal != line_number - 1:
                raise ValueError(f"{source_map}:{line_number}: physical ordinal is not sequential")
            if bed_fields[3] != str(ordinal):
                raise ValueError(f"{input_bed}:{line_number}: BED name/source ordinal mismatch")
            if (bed_fields[0] != metadata["source_chromosome"] or
                    int(metadata["source_position_1based"]) != end or
                    int(metadata["source_position_0based"]) != start or
                    end != start + 1 or
                    bed_fields[5] != metadata["source_strand"] or
                    score != 1000):
                raise ValueError(f"{input_bed}:{line_number}: BED/source metadata mismatch")
            count += 1
        extra_map = map_fh.readline()
        extra_bed = bed_fh.readline()
        if extra_map or extra_bed:
            raise ValueError("source-map/input-bed row-count mismatch")
    return count


def parse_original_source(path):
    expected_header = [
        "PAS_ID", "Chromosome", "Position", "Strand", "Mean RPM",
        "Intron/exon location", "Ensemble ID", "RefSeq Gene ID", "Gene Symbol",
        "Gene Name", "Extension", "PAS type", "PSE", "PAS Signal", "Conservation",
        "intergenic_TE",
    ]
    with Path(path).open() as fh:
        header = fh.readline().rstrip("\n").split("\t")
        if header != expected_header:
            raise ValueError("original source header mismatch")
        ordinal = 0
        while True:
            raw = fh.readline()
            if not raw:
                break
            fields = raw.rstrip("\n").split("\t")
            ordinal += 1
            if len(fields) != 16:
                raise ValueError(f"original source ordinal {ordinal}: wrong width")
            pas_id, chrom, position_text, strand = fields[:4]
            if position_text.isdigit() and strand in ("+", "-"):
                position = int(position_text)
                if pas_id == f"{chrom}:{position}:{strand}":
                    yield ordinal, pas_id, chrom, position, strand
                    continue
            raise ValueError(f"original source ordinal {ordinal}: invalid key")


def tuple_equal(a, b):
    return len(a) == len(b) and all(x == y for x, y in zip(a, b))


def classify_row(metadata, source_sizes, target_sizes, canonical_index,
                 canonical_reasons, canonical_reasons_by_name, audit_index,
                 audit_unmapped_by_name, index,
                 accepted_targets=STANDARD_SET):
    ordinal = str(metadata["ordinal"])
    canonical_named = canonical_index.rows(ordinal)
    audit_named = audit_index.rows(ordinal)
    canonical_count = canonical_index.count(ordinal)
    audit_count = audit_index.count(ordinal)
    audit_same = audit_index.by_name.get(ordinal, {}).get("all_same", True)
    reasons = []

    candidates = index.candidates(metadata["source_chromosome"], metadata["source_bed_start"])
    crosswalk_rows = []
    for block in candidates:
        offset = metadata["source_bed_start"] - block["target_block_start"]
        if block["query_strand"] == "-":
            target_start = block["query_size"] - (block["query_strand_relative_start"] + offset + 1)
        else:
            target_start = block["query_block_start"] + offset
        expected_strand = metadata["source_strand"]
        if block["query_strand"] == "-":
            expected_strand = "+" if metadata["source_strand"] == "-" else "-"
        crosswalk_rows.append({
            "ordinal": ordinal,
            "chain_id": block["chain_id"],
            "block_index": block["block_index"],
            "target_chromosome": block["query_chromosome"],
            "target_bed_start": target_start,
            "target_bed_end": target_start + 1,
            "target_position_1based": target_start + 1,
            "chain_target_strand": "+",
            "chain_query_strand": block["query_strand"],
            "expected_target_strand": expected_strand,
            "source_strand": metadata["source_strand"],
            "source_bed_start": metadata["source_bed_start"],
            "source_bed_end": metadata["source_bed_end"],
            "source_block_start": block["target_block_start"],
            "source_block_end": block["target_block_end"],
            "query_block_start": block["query_block_start"],
            "query_block_end": block["query_block_end"],
            "query_genomic_block_start": block["query_genomic_block_start"],
            "query_genomic_block_end": block["query_genomic_block_end"],
            "offset_in_block": offset,
        })

    # Reconcile every chain-derived mapping with every raw audit row before
    # classifying the ordinal. Counter equality retains same-target duplicates.
    crosswalk_multiset = collections.Counter(
        (r["target_chromosome"], r["target_bed_start"], r["target_bed_end"],
         ordinal, 1000, r["expected_target_strand"])
        for r in crosswalk_rows
    )
    if len(crosswalk_rows) != audit_count:
        reasons.append("CROSSWALK_AUDIT_CARDINALITY_MISMATCH")
    if crosswalk_multiset != audit_index.mapping_multiset(ordinal):
        reasons.append("CROSSWALK_AUDIT_MULTISET_MISMATCH")
    if reasons:
        return "AUDIT_INCONSISTENT", None, crosswalk_rows, reasons, audit_count

    # Total-status precedence is: MULTIPLE, UNMAPPED, AUDIT_INCONSISTENT,
    # NONSTANDARD_TARGET, OFFBOUNDS, STRAND_MISMATCH, CROSSWALK_MISMATCH,
    # ACCEPTED.  MULTIPLE is the expected canonical "#Duplicated in new"
    # accounting for every raw audit row, including same-target duplicates.
    duplicated_expected = (
        canonical_count == 0 and audit_count > 1
        and canonical_reasons_by_name.get(ordinal) == ["DUPLICATED_IN_NEW"]
    )
    if audit_count > 1:
        if not duplicated_expected:
            reasons.append("CANONICAL_AUDIT_UNEXPECTED_COMBINATION")
            return "AUDIT_INCONSISTENT", audit_named[0], crosswalk_rows, reasons, audit_count
        target = audit_named[0]
        return "MULTIPLE", target, crosswalk_rows, reasons, audit_count

    deleted_expected = (
        canonical_count == 0 and audit_count == 0
        and canonical_reasons_by_name.get(ordinal) == ["DELETED_IN_NEW"]
    )
    if deleted_expected:
        return "UNMAPPED", None, crosswalk_rows, reasons, audit_count

    full_tuple_equal = (
        canonical_count == 1 and audit_count == 1 and audit_same
        and tuple_equal(canonical_named[0], audit_named[0])
    )
    if not deleted_expected and not duplicated_expected and not full_tuple_equal:
        reasons.append("CANONICAL_AUDIT_UNEXPECTED_COMBINATION")
        return "AUDIT_INCONSISTENT", (canonical_named or audit_named or [None])[0], \
            crosswalk_rows, reasons, audit_count

    mapped = canonical_named[0]
    target_chrom, start, end, name, score, target_strand = mapped
    if score != 1000 or target_strand not in ("+", "-"):
        reasons.append("MALFORMED_CANONICAL_ROW")
        return "AUDIT_INCONSISTENT", mapped, crosswalk_rows, reasons, audit_count
    if target_chrom not in accepted_targets:
        return "NONSTANDARD_TARGET", mapped, crosswalk_rows, ["TARGET_NOT_ACCEPTED_STANDARD"] + reasons, audit_count
    if target_sizes.get(target_chrom) is None or start < 0 or end > target_sizes[target_chrom]:
        return "OFFBOUNDS", mapped, crosswalk_rows, ["TARGET_BOUNDS_FAIL"] + reasons, audit_count
    if end != start + 1:
        return "OFFBOUNDS", mapped, crosswalk_rows, ["TARGET_NOT_ONE_BASE"] + reasons, audit_count

    coordinate_matches = [row for row in crosswalk_rows if (
        row["target_chromosome"] == target_chrom and row["target_bed_start"] == start
        and row["target_bed_end"] == end
    )]
    strand_matches = [row for row in coordinate_matches if row["expected_target_strand"] == target_strand]
    if len(coordinate_matches) == 1 and len(strand_matches) == 0:
        return "STRAND_MISMATCH", mapped, crosswalk_rows, ["TARGET_STRAND_NOT_CHAIN_EXPECTED"] + reasons, audit_count
    if len(crosswalk_rows) != 1 or len(strand_matches) != 1:
        return "CROSSWALK_MISMATCH", mapped, crosswalk_rows, ["CHAIN_CROSSWALK_FAIL"] + reasons, audit_count
    return "ACCEPTED", mapped, crosswalk_rows, reasons, audit_count


CROSSWALK_HEADER = [
    "ordinal", "chain_id", "block_index", "target_chromosome", "target_bed_start",
    "target_bed_end", "target_position_1based", "chain_target_strand",
    "chain_query_strand", "expected_target_strand", "source_strand",
    "source_bed_start", "source_bed_end", "source_block_start", "source_block_end",
    "query_genomic_block_start", "query_genomic_block_end", "offset_in_block",
]


STATUS_HEADER = [
    "ordinal", "status", "source_pas_id", "source_chromosome",
    "source_position_1based", "source_position_0based", "source_strand",
    "source_bed_start", "source_bed_end", "target_chromosome", "target_bed_start",
    "target_bed_end", "target_position_1based", "target_strand", "audit_row_count",
    "canonical_reason_codes", "chain_id", "chain_query_strand", "block_index",
    "reason_codes",
]


def write_crosswalk(path, all_rows):
    with Path(path).open("w") as fh:
        fh.write("\t".join(CROSSWALK_HEADER) + "\n")
        for row in all_rows:
            fh.write("\t".join(str(row.get(key, "")) for key in CROSSWALK_HEADER) + "\n")


def write_status(path, rows):
    with Path(path).open("w") as fh:
        fh.write("\t".join(STATUS_HEADER) + "\n")
        for row in rows:
            fh.write("\t".join(str(row.get(key, "")) for key in STATUS_HEADER) + "\n")


def run_accounted(case_dir, source_sizes, target_sizes, *, input_bed, chain_path, index=None,
                  accepted_targets=STANDARD_SET, binary):
    case_dir = Path(case_dir)
    input_bed = Path(input_bed)
    chain_path = Path(chain_path)
    source_map = case_dir / "source-map.tsv"
    input_ordinal_count = validate_input_bed(source_map, input_bed)
    if index is None:
        index = build_chain_index(chain_path, source_sizes, target_sizes)
    if index.target_strands.get("+", 0) != index.record_count or index.target_strands.get("-", 0):
        raise ValueError("chain index contains a target strand other than +")

    canonical_mapped = case_dir / "canonical.mapped.bed"
    canonical_unmapped = case_dir / "canonical.unmapped.bed"
    audit_mapped = case_dir / "audit.mapped.bed"
    audit_unmapped = case_dir / "audit.unmapped.bed"
    run_exact_argv(
        canonical_argv(input_bed, chain_path, canonical_mapped, canonical_unmapped, binary),
        case_dir / "canonical.stdout", case_dir / "canonical.stderr",
        case_dir / "canonical.argv.json", case_dir / "canonical.exitcode",
    )
    run_exact_argv(
        audit_argv(input_bed, chain_path, audit_mapped, audit_unmapped, binary),
        case_dir / "audit.stdout", case_dir / "audit.stderr",
        case_dir / "audit.argv.json", case_dir / "audit.exitcode",
    )
    canonical_index = parse_bed_outputs(canonical_mapped)
    canonical_unmapped_rows, canonical_reasons, canonical_reasons_by_name = parse_unmapped(canonical_unmapped)
    audit_index = parse_bed_outputs(audit_mapped)
    audit_unmapped_rows, audit_reasons, audit_unmapped_by_name = parse_unmapped(audit_unmapped)

    expected = None  # Golden answers are test inputs only, never worker inputs.
    expected_status_matches = True
    assertion_values = {}
    if expected:
        assertion_values = {item["name"]: True for item in expected.get("assertions", [])}
    counts = collections.Counter()
    crosswalk_row_count = 0
    cardinality_mismatch_ordinals = 0
    multiset_mismatch_ordinals = 0
    seen_count = 0
    with (case_dir / "final-status.tsv").open("w") as status_fh, \
            (case_dir / "chain-crosswalk.tsv").open("w") as crosswalk_fh:
        status_fh.write("\t".join(STATUS_HEADER) + "\n")
        crosswalk_fh.write("\t".join(CROSSWALK_HEADER) + "\n")
        for metadata in iter_source_map(source_map):
            seen_count += 1
            if metadata["ordinal"] != seen_count:
                raise ValueError(f"source ordinal {metadata['ordinal']} is not sequential")
            status, mapped, row_crosswalk, reasons, audit_count = classify_row(
                metadata, source_sizes, target_sizes, canonical_index, canonical_reasons,
                canonical_reasons_by_name, audit_index, audit_unmapped_by_name,
                index, accepted_targets,
            )
            for row in row_crosswalk:
                row["source_pas_id"] = metadata["source_pas_id"]
                crosswalk_fh.write("\t".join(str(row.get(key, "")) for key in CROSSWALK_HEADER) + "\n")
            crosswalk_row_count += len(row_crosswalk)
            cardinality_mismatch_ordinals += "CROSSWALK_AUDIT_CARDINALITY_MISMATCH" in reasons
            multiset_mismatch_ordinals += "CROSSWALK_AUDIT_MULTISET_MISMATCH" in reasons
            # Ambiguous audit alternatives are retained in the raw audit file,
            # never presented as a selected target in the one-row status table.
            if status in {"MULTIPLE", "AUDIT_INCONSISTENT"}:
                mapped = None
            status_row = dict(metadata)
            status_row.update({
                "status": status,
                "target_chromosome": mapped[0] if mapped else "",
                "target_bed_start": mapped[1] if mapped else "",
                "target_bed_end": mapped[2] if mapped else "",
                "target_position_1based": (mapped[1] + 1) if mapped else "",
                "target_strand": mapped[5] if mapped else "",
                "audit_row_count": audit_count,
                "canonical_reason_codes": ";".join(canonical_reasons_by_name.get(str(metadata["ordinal"]), ["MAPPED"])),
                "chain_id": row_crosswalk[0]["chain_id"] if mapped and len(row_crosswalk) == 1 else "",
                "chain_query_strand": row_crosswalk[0]["chain_query_strand"] if mapped and len(row_crosswalk) == 1 else "",
                "block_index": row_crosswalk[0]["block_index"] if mapped and len(row_crosswalk) == 1 else "",
                "reason_codes": ";".join(reasons),
            })
            status_fh.write("\t".join(str(status_row.get(key, "")) for key in STATUS_HEADER) + "\n")
            counts[status] += 1
            if expected:
                expected_status = expected["statuses"].get(str(metadata["ordinal"]))
                expected_status_matches &= expected_status == status
                for assertion in expected.get("assertions", []):
                    if str(assertion["ordinal"]) == str(metadata["ordinal"]):
                        assertion_values[assertion["name"]] &= status_row.get(assertion["field"]) == assertion["value"]

    if seen_count != input_ordinal_count:
        raise ValueError("streamed source ordinal count disagrees with preflight")

    def partition_covers(mapped_index, reason_map):
        if len(mapped_index.by_name) + len(reason_map) != input_ordinal_count:
            return False
        return all(
            (str(name) in mapped_index.by_name) != (str(name) in reason_map)
            for name in range(1, input_ordinal_count + 1)
        )

    checks = {
        "input_ordinal_count": input_ordinal_count,
        "status_counts": dict(sorted(counts.items())),
        "canonical_mapped_rows": sum(item["count"] for item in canonical_index.by_name.values()),
        "canonical_unmapped_rows": canonical_unmapped_rows,
        "canonical_reason_codes": canonical_reasons,
        "audit_mapped_rows": sum(item["count"] for item in audit_index.by_name.values()),
        "audit_unmapped_rows": audit_unmapped_rows,
        "audit_reason_codes": audit_reasons,
        "raw_audit_rows_preserved": sum(item["count"] for item in audit_index.by_name.values()),
        "crosswalk_rows": crosswalk_row_count,
        "crosswalk_audit_cardinality_mismatch_ordinals": cardinality_mismatch_ordinals,
        "crosswalk_audit_multiset_mismatch_ordinals": multiset_mismatch_ordinals,
        "crosswalk_audit_consistent": cardinality_mismatch_ordinals == 0 and multiset_mismatch_ordinals == 0,
        "audit_inconsistent_ordinals": counts["AUDIT_INCONSISTENT"],
        "no_audit_inconsistencies": counts["AUDIT_INCONSISTENT"] == 0,
        "exact_canonical_argv": canonical_argv(input_bed, chain_path, canonical_mapped, canonical_unmapped, binary),
        "exact_audit_argv": audit_argv(input_bed, chain_path, audit_mapped, audit_unmapped, binary),
        "source_ordinal_stream_valid": seen_count == input_ordinal_count,
        "canonical_output_coverage": partition_covers(canonical_index, canonical_reasons_by_name),
        "audit_output_coverage": partition_covers(audit_index, audit_unmapped_by_name),
    }
    if expected:
        expected_keys_cover = (
            set(expected["statuses"]) == {str(name) for name in range(1, input_ordinal_count + 1)}
        )
        checks["expected_status_matches"] = bool(expected_status_matches)
        checks["expected_status_counts_match"] = counts == collections.Counter(expected["statuses"].values())
        checks["expected_status_key_coverage"] = expected_keys_cover
        checks.update(assertion_values)
        boolean_checks = [
            checks["expected_status_matches"], checks["expected_status_counts_match"],
            checks["expected_status_key_coverage"], checks["source_ordinal_stream_valid"],
            checks["canonical_output_coverage"], checks["audit_output_coverage"],
            *assertion_values.values(),
        ]
    else:
        boolean_checks = [
            checks["source_ordinal_stream_valid"], checks["canonical_output_coverage"],
            checks["audit_output_coverage"],
        ]
    boolean_checks.extend([checks["crosswalk_audit_consistent"], checks["no_audit_inconsistencies"]])
    checks["all_checks_pass"] = all(value is True for value in boolean_checks)
    (case_dir / "ACCOUNTING-METRICS.json").write_text(json.dumps(checks, indent=2) + "\n")
    return checks, None
