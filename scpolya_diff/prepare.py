"""Stream raw molecule counts; never treat cells or technical splits as replicates."""
import collections
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

from . import __version__
from .common import (MAX_COUNT, file_record, integer, new_output, now, open_text,
                     table, write_json, write_table, sha256)

CAT_FIELDS = ("peak_name", "gene_id", "chrom", "start", "end", "strand", "feature",
              "canonical_transcript", "stop_codon_position", "three_prime_utr_length",
              "distance_to_stop_codon", "mispriming_pass", "general_mispriming_pass", "is_distal_peak")
MISSING = {"", "NA", "NaN", "nan", "None"}


def length_value(value):
    if value in MISSING:
        return "NA"
    try:
        x = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"Invalid three_prime_utr_length: {value}") from None
    if not x.is_finite():
        return "NA"
    return str(max(x, Decimal(0)).normalize())


def catalogue(path):
    rows = {}
    for r in table(path, CAT_FIELDS):
        peak = r["peak_name"]
        if not peak or peak in rows:
            raise ValueError(f"{path}: empty/duplicate annotation peak_name {peak!r}")
        if r["strand"] not in ("+", "-") or not r["chrom"]:
            raise ValueError(f"{path}: invalid chrom/strand for {peak}")
        r["start"] = str(integer(r["start"], f"{peak} start", 2**53 - 1))
        r["end"] = str(integer(r["end"], f"{peak} end", 2**53 - 1))
        if int(r["end"]) <= int(r["start"]):
            raise ValueError(f"{path}: invalid half-open interval for {peak}")
        if r["stop_codon_position"] not in MISSING:
            r["stop_codon_position"] = str(integer(r["stop_codon_position"], f"{peak} stop", 2**53 - 1))
        d = r["distance_to_stop_codon"]
        if d in MISSING:
            r["distance_to_stop_codon"] = "NA"
        else:
            try:
                z = Decimal(d)
            except InvalidOperation:
                raise ValueError(f"{peak}: invalid distance_to_stop_codon") from None
            if not z.is_finite():
                raise ValueError(f"{peak}: nonfinite distance_to_stop_codon")
            r["distance_to_stop_codon"] = str(z.normalize())
        r["three_prime_utr_length"] = length_value(r["three_prime_utr_length"])
        for flag_field in ("mispriming_pass", "general_mispriming_pass", "is_distal_peak"):
            flag = r[flag_field].lower()
            if flag_field == "is_distal_peak" and r[flag_field] in MISSING:
                r[flag_field] = "NA"
                continue
            if flag not in ("true", "false", "1", "0"):
                raise ValueError(f"{peak}: {flag_field} must be an explicit boolean")
            r[flag_field] = "true" if flag in ("true", "1") else "false"
        rows[peak] = r
    if not rows:
        raise ValueError(f"Empty catalogue: {path}")
    payload = [[rows[p][k] for k in CAT_FIELDS] for p in sorted(rows)]
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return rows, digest


def raw_counts(path):
    with open_text(path) as f:
        reader = csv.reader(f, delimiter="\t")
        first = next(reader, None)
        if first is None:
            raise ValueError(f"Empty count file: {path}")
        if "peak_name" in first and "cell_barcode" in first:
            if len(first) != len(set(first)):
                raise ValueError(f"{path}: duplicate count header")
            value = "peak_umi_count" if "peak_umi_count" in first else "umi_count"
            if value not in first:
                raise ValueError(f"{path}: raw peak_umi_count/umi_count required")
            indices = [first.index(k) for k in ("peak_name", "cell_barcode", value)]
            width = len(first)
        else:
            if len(first) != 3:
                raise ValueError(f"{path}: expected headerless peak_name,cell_barcode,umi_count")
            indices, width = [0, 1, 2], 3
            reader = itertools.chain([first], reader)
        for n, row in enumerate(reader, 1):
            if len(row) != width:
                raise ValueError(f"{path}:{n}: malformed count row")
            peak, barcode, value = [row[i] for i in indices]
            if not peak or not barcode or any(v != v.strip() or any(c in v for c in '\r\n\x00') for v in (peak, barcode, value)):
                raise ValueError(f"{path}:{n}: invalid count identifier/value")
            yield peak, barcode, integer(value, f"{path}:{n} count")


def prepare(a):
    out = new_output(a.outdir)
    report = {"schema": "scpolya.diff_apa.preparation.v2", "status": "RUNNING", "started_utc": now(),
              "module_version": __version__, "python": sys.version, "arguments": vars(a),
              "source": {p.name: file_record(p) for p in Path(__file__).parent.iterdir() if p.suffix in (".py", ".R")},
              "unit": "gene_id+strand (not resolved alternative-terminal-exon isoforms)",
              "zero_policy": "Omitted PAS rows within matched, catalogue-qualified cells are zero; unmatched cells are excluded, never certified zero."}
    try:
        _prepare(a, out, report)
        report["outputs"] = {p.name: file_record(p) for p in sorted(out.glob("*.tsv"))}
        report["status"] = "PREPARED"
    except Exception as exc:
        report.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["finished_utc"] = now()
        write_json(out / "preparation.json", report)
    return report


def _prepare(a, out, report):
    if a.min_cells < 1:
        raise ValueError("--min-cells must be positive")
    fields = ["sample_id", "cell_barcode", "pseudobulk_id", "donor_id", "condition", "cell_type"]
    all_meta = list(table(a.metadata, fields))
    if not all_meta:
        raise ValueError("Empty metadata")
    seen = set()
    for r in all_meta:
        if any(not r[k] for k in fields):
            raise ValueError("Required metadata labels must not be empty")
        key = r["sample_id"], r["cell_barcode"]
        if key in seen:
            raise ValueError(f"Duplicate metadata cell key: {key}")
        seen.add(key)
    meta = [r for r in all_meta if r["cell_type"] == a.cell_type]
    if not meta:
        raise ValueError("No cells for requested --cell-type")
    cell_map = {(r["sample_id"], r["cell_barcode"]): r["pseudobulk_id"] for r in meta}
    pb_meta, cell_totals, biological = {}, collections.Counter(), {}
    for r in meta:
        pb = r["pseudobulk_id"]
        labels = tuple(r.get(k, "") for k in ("donor_id", "condition", "cell_type", "batch"))
        if pb in pb_meta and pb_meta[pb] != labels:
            raise ValueError(f"Inconsistent donor/condition/cell_type/batch for {pb}")
        bio = labels[:3]
        if bio in biological and biological[bio] != pb:
            raise ValueError("One donor×condition×celltype must map to one pseudobulk; aggregate technical libraries")
        biological[bio], pb_meta[pb] = pb, labels
        cell_totals[pb] += 1
    if set(pb_meta) & {"gene_id", "feature_id", "unit_id", "strand"}:
        raise ValueError("pseudobulk_id collides with a reserved count-matrix column")
    annotated, fingerprint = catalogue(a.annotation)
    report["inputs"] = {"annotation": file_record(a.annotation), "metadata": file_record(a.metadata),
                        "manifest": file_record(a.manifest)}
    report["catalogue_fingerprint"] = fingerprint
    report["catalogue_fingerprint_fields"] = CAT_FIELDS
    report["analysis_set"] = a.analysis_set
    native_contexts = {"Terminal_exon", "Downstream_of_terminal_exon"}
    allowed = native_contexts if a.analysis_set == "native-terminal" else {"Terminal_exon"}
    native, retained, exclusions, anchors, diagnostics = {}, {}, collections.Counter(), {}, []
    for peak, source_row in annotated.items():
        r = dict(source_row)
        native_reason = "native_terminal"
        if r["feature"] not in native_contexts:
            native_reason = "nonterminal_context"
        elif r["general_mispriming_pass"] != "true":
            native_reason = "general_mispriming_failed"
        elif r["gene_id"] in MISSING:
            native_reason = "missing_gene_id"
        else:
            unit = quote(r["gene_id"], safe="") + "|" + quote(r["strand"], safe="")
            r["unit_id"] = unit
            # Unknown anchors do not remove composition PAS. Contradictory known
            # anchors fail; any required missing anchor masks the length endpoint.
            anchor = anchors.setdefault(unit, {})
            for k in ("chrom", "canonical_transcript", "stop_codon_position"):
                if r[k] not in MISSING:
                    if k in anchor and anchor[k] != r[k]:
                        raise ValueError(f"{unit}: incompatible transcript/stop-codon anchors")
                    anchor[k] = r[k]
            missing_anchor = any(r[k] in MISSING for k in ("canonical_transcript", "stop_codon_position"))
            distance = r["distance_to_stop_codon"]
            if missing_anchor or distance == "NA" or r["three_prime_utr_length"] == "NA":
                r["three_prime_utr_length"] = "NA"
                r["length_status"] = "missing_anchor_or_length"
            else:
                expected = max(Decimal(distance), Decimal(0))
                if Decimal(r["three_prime_utr_length"]) != expected:
                    raise ValueError(f"{peak}: length differs from native clip(distance_to_stop_codon,0)")
                r["three_prime_utr_length"] = str(expected.normalize())
                r["length_status"] = "available"
            native[peak] = r
            if r["feature"] in allowed:
                retained[peak] = r
        reason = "retained" if peak in retained else ("outside_explicit_analysis_set" if peak in native else native_reason)
        exclusions[reason] += 1
        diagnostics.append((peak, native_reason, reason, r.get("length_status", "not_applicable")))
    if not native:
        raise ValueError("No eligible native terminal PAS")
    # Freeze the upstream native distal identities; validate all ties against
    # native signed distances whenever that comparison is fully measurable.
    for unit in anchors:
        sites = [r for r in native.values() if r["unit_id"] == unit]
        if all(r["distance_to_stop_codon"] != "NA" for r in sites):
            maximum = max(Decimal(r["distance_to_stop_codon"]) for r in sites)
            if any((r["is_distal_peak"] == "true") != (Decimal(r["distance_to_stop_codon"]) == maximum) for r in sites):
                raise ValueError(f"{unit}: native is_distal_peak disagrees with maximum distance/all ties")
    report["catalogue_selection"] = dict(exclusions)
    report["native_to_analysis"] = {"catalogue_pas": len(annotated), "native_terminal_pas": len(native),
                                    "native_terminal_units": len(anchors), "analysis_set_pas": len(retained),
                                    "analysis_set_units": len({r["unit_id"] for r in retained.values()})}
    manifest_path = Path(a.manifest).resolve()
    manifest = list(table(manifest_path, ["sample_id", "counts_path", "annotation_path"]))
    ids = [r["sample_id"] for r in manifest]
    if not ids or any(not s for s in ids) or len(set(ids)) != len(ids):
        raise ValueError("Manifest requires unique nonempty sample_id")
    if set(ids) != {r["sample_id"] for r in meta}:
        raise ValueError("Manifest samples must equal selected metadata samples exactly")
    paths, hashes = set(), set()
    count_files = []
    for r in manifest:
        def resolve(v):
            if not v:
                raise ValueError("Manifest paths cannot be empty")
            p = Path(v)
            return (manifest_path.parent / p).resolve() if not p.is_absolute() else p.resolve()
        path, annpath = resolve(r["counts_path"]), resolve(r["annotation_path"])
        if path in paths:
            raise ValueError("The same count file appears more than once")
        rec = file_record(path)
        if rec["sha256"] in hashes:
            raise ValueError("Byte-identical count inputs under different sample labels; resolve duplicate input")
        paths.add(path)
        hashes.add(rec["sha256"])
        cat, fp = catalogue(annpath)
        if fp != fingerprint:
            changed = sorted(p for p in set(cat) | set(annotated)
                             if p not in cat or p not in annotated or
                             any(cat[p][k] != annotated[p][k] for k in CAT_FIELDS))
            raise ValueError(f"{r['sample_id']}: common catalogue fingerprint mismatch; first changed PAS: {changed[:5]}")
        rec.update(sample_id=r["sample_id"], annotation=file_record(annpath), catalogue_fingerprint=fp)
        count_files.append(rec)
    report["count_files"] = count_files
    counts = {p: collections.Counter() for p in native}
    matched, positive = set(), set()
    dbpath = out / ".duplicate-check.sqlite"
    connection = sqlite3.connect(dbpath)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA cache_size=-16384")
        connection.execute("CREATE TABLE seen (peak TEXT, cell TEXT, PRIMARY KEY(peak,cell)) WITHOUT ROWID")
        for rec in count_files:
            sid = rec["sample_id"]
            connection.execute("DELETE FROM seen")
            nrows = selected_umis = retained_umis = 0
            for peak, barcode, count in raw_counts(rec["path"]):
                nrows += 1
                if peak not in annotated:
                    raise ValueError(f"{sid}: count peak absent from common catalogue: {peak}")
                key = sid, barcode
                if key not in cell_map:
                    continue
                try:
                    connection.execute("INSERT INTO seen VALUES (?,?)", (peak, barcode))
                except sqlite3.IntegrityError:
                    raise ValueError(f"{sid}: duplicate selected PAS/cell count row {peak}/{barcode}; refuse double counting") from None
                matched.add(key)
                selected_umis += count
                if peak in native:
                    pb = cell_map[key]
                    counts[peak][pb] += count
                    if counts[peak][pb] > MAX_COUNT:
                        raise ValueError(f"{peak}/{pb}: pseudobulk count exceeds R integer max")
                    if peak in retained:
                        retained_umis += count
                        if count:
                            positive.add(key)
            connection.commit()
            rec.update(rows_scanned=nrows, selected_umis_all_catalogue=selected_umis, analysis_set_umis=retained_umis)
    finally:
        connection.close()
        dbpath.unlink(missing_ok=True)
    for rec in count_files:
        if sha256(rec["path"]) != rec["sha256"] or sha256(rec["annotation"]["path"]) != rec["annotation"]["sha256"]:
            raise ValueError("Count/catalogue input changed during preparation")
    for rec in report["inputs"].values():
        if sha256(rec["path"]) != rec["sha256"]:
            raise ValueError("Manifest/metadata/common catalogue changed during preparation")
    unmatched = set(cell_map) - matched
    matched_n = collections.Counter(cell_map[k] for k in matched)
    positive_n = collections.Counter(cell_map[k] for k in positive)
    excluded = {}
    for pb in pb_meta:
        if matched_n[pb] < a.min_cells:
            excluded[pb] = "below_min_matched_cells"
        elif positive_n[pb] == 0:
            excluded[pb] = "no_analysis_set_positive_cells"
    write_table(out / "unmatched_cells.tsv", ["sample_id", "cell_barcode", "pseudobulk_id", "pseudobulk_excluded"],
                ([*k, cell_map[k], cell_map[k] in excluded] for k in sorted(unmatched)))
    write_table(out / "excluded_pseudobulks.tsv", ["pseudobulk_id", "donor_id", "condition", "cell_type", "batch", "reason",
                                                 "n_cells_metadata", "n_cells_matched", "n_cells_target_positive"],
                ([pb, *pb_meta[pb], reason, cell_totals[pb], matched_n[pb], positive_n[pb]] for pb, reason in sorted(excluded.items())))
    unresolved = [k for k in unmatched if cell_map[k] not in excluded]
    if unresolved and not a.allow_unmatched_cells:
        raise ValueError(f"{len(unresolved)} unmatched selected metadata cells in eligible pseudobulks: label mismatch/zero coverage unresolved; inspect unmatched_cells.tsv or explicitly --allow-unmatched-cells")
    pbs = sorted(set(pb_meta) - set(excluded))
    annfields = ["unit_id", "feature_id", "gene_id", "strand", "gene_name", "chrom", "start", "end",
                 "canonical_transcript", "stop_codon_position", "length_bp", "length_status", "feature", "distance_to_stop_codon", "is_distal_peak"]
    for prefix, subset in (("", retained), ("native_", native)):
        peaks = sorted(subset, key=lambda p: (subset[p]["unit_id"], p))
        write_table(out / (prefix + "pas_counts.tsv"), ["unit_id", "feature_id"] + pbs,
                    ([subset[p]["unit_id"], p] + [counts[p][s] for s in pbs] for p in peaks))
        write_table(out / (prefix + "pas_annotation.tsv"), annfields,
                    ([r["unit_id"], p, r["gene_id"], r["strand"], r.get("gene_name", r["gene_id"]), r["chrom"], r["start"], r["end"],
                      r["canonical_transcript"], r["stop_codon_position"], r["three_prime_utr_length"], r["length_status"], r["feature"],
                      r["distance_to_stop_codon"], r["is_distal_peak"]] for p in peaks for r in [subset[p]]))
    write_table(out / "catalogue_selection.tsv", ["feature_id", "native_status", "analysis_status", "length_status"], diagnostics)
    write_table(out / "samples.tsv", ["sample_id", "donor_id", "condition", "cell_type", "batch", "n_cells_metadata", "n_cells_matched", "n_cells_target_positive",
                                      "matched_fraction", "target_positive_fraction"],
                ([pb, *pb_meta[pb], cell_totals[pb], matched_n[pb], positive_n[pb], matched_n[pb]/cell_totals[pb], positive_n[pb]/cell_totals[pb]] for pb in pbs))
    report.update(n_catalogue_pas=len(annotated), n_native_terminal_pas=len(native), n_analysis_set_pas=len(retained),
                  n_observed_analysis_set_pas=sum(any(counts[p][s]>0 for s in pbs) for p in retained),
                  n_analysis_set_units=len({r["unit_id"] for r in retained.values()}), n_pseudobulks=len(pbs),
                  n_excluded_pseudobulks=len(excluded), n_unmatched_cells=len(unmatched),
                  assumptions=["Per-sample annotation_path describes the catalogue actually used to quantify that count file; input tables cannot prove this upstream provenance.",
                               "Technical libraries aggregated into one biological unit contain disjoint molecules; duplicate selected PAS/cell rows are rejected."])
