from __future__ import annotations

import gffutils
import pandas as pd
import pytest

from utils.annotation_utils import (
    CANONICAL_MODEL_EDGE_FEATURE,
    NONCANONICAL_EXON_FEATURE,
    NONCANONICAL_GENE_BODY_FEATURE,
    NONCODING_EXON_FEATURE,
    NONCODING_GENE_BODY_FEATURE,
    annotate_peaks_with_features,
    classify_outside_canonical_model,
    get_canonical_transcripts,
    get_gene_models,
    get_genomic_features,
    get_stop_codon_positions,
    merge_stop_codon_info,
)
from utils.terminal_quant_utils import (
    INTRON_FEATURE,
    TERMINAL_FEATURES,
    prepare_terminal_peak_metadata,
)

NEW_FEATURES = {
    NONCODING_EXON_FEATURE,
    NONCODING_GENE_BODY_FEATURE,
    NONCANONICAL_EXON_FEATURE,
    NONCANONICAL_GENE_BODY_FEATURE,
    CANONICAL_MODEL_EDGE_FEATURE,
}


def _database(tmp_path):
    rows = []

    def add(feature, start, end, strand, gene, transcript=None):
        attrs = f'gene_id "{gene}"; gene_name "{gene}_NAME";'
        if transcript:
            attrs += f' transcript_id "{transcript}";'
        rows.append(f"chr1\tfixture\t{feature}\t{start}\t{end}\t.\t{strand}\t0\t{attrs}\n")

    # Coding gene: canonical CDS transcript 1001-1600 plus an upstream
    # non-coding isoform whose first exon lies outside the canonical model.
    add("gene", 501, 1600, "+", "CODING")
    add("transcript", 1001, 1600, "+", "CODING", "CODING_CANON")
    for start, end in ((1001, 1100), (1201, 1300), (1401, 1600)):
        add("exon", start, end, "+", "CODING", "CODING_CANON")
    add("CDS", 1050, 1100, "+", "CODING", "CODING_CANON")
    add("CDS", 1201, 1300, "+", "CODING", "CODING_CANON")
    add("CDS", 1401, 1450, "+", "CODING", "CODING_CANON")
    add("transcript", 501, 1300, "+", "CODING", "CODING_ALT")
    add("exon", 501, 600, "+", "CODING", "CODING_ALT")
    add("exon", 1201, 1300, "+", "CODING", "CODING_ALT")

    # Plus-strand lncRNA with two exons (no CDS anywhere).
    add("gene", 3001, 4000, "+", "LNC_PLUS")
    add("transcript", 3001, 4000, "+", "LNC_PLUS", "LNC_PLUS_T1")
    add("exon", 3001, 3200, "+", "LNC_PLUS", "LNC_PLUS_T1")
    add("exon", 3801, 4000, "+", "LNC_PLUS", "LNC_PLUS_T1")

    # Minus-strand single-exon lncRNA.
    add("gene", 6001, 6500, "-", "LNC_MINUS")
    add("transcript", 6001, 6500, "-", "LNC_MINUS", "LNC_MINUS_T1")
    add("exon", 6001, 6500, "-", "LNC_MINUS", "LNC_MINUS_T1")

    # lncRNA nested inside the coding gene's canonical terminal exon.
    add("gene", 1451, 1550, "+", "LNC_NESTED")
    add("transcript", 1451, 1550, "+", "LNC_NESTED", "LNC_NESTED_T1")
    add("exon", 1451, 1550, "+", "LNC_NESTED", "LNC_NESTED_T1")

    path = tmp_path / "noncoding.gtf"
    path.write_text("".join(rows))
    return gffutils.create_db(str(path), dbfn=":memory:", force=True,
                              disable_infer_genes=True, disable_infer_transcripts=True)


def _peak(name, start, end, gene, strand="+", chrom="chr1"):
    return {
        "chrom": chrom, "start": start, "end": end, "peak_name": name,
        "max_cov": 5.0, "strand": strand, "total_polyA_reads": 4,
        "misprimed_ratio": 0.1, "total_reads": 9,
        "gene_name": "" if gene is None else f"{gene}_NAME",
        "gene_id": pd.NA if gene is None else gene, "distance_to_gene": 0,
    }


PEAKS = [
    # (name, BED start, BED end, assigned gene, strand, expected feature)
    ("coding_terminal", 1500, 1510, "CODING", "+", "Terminal_exon"),
    ("coding_other_exon", 1250, 1260, "CODING", "+", "Other_exon"),
    ("coding_intron", 1150, 1160, "CODING", "+", "Intron"),
    ("coding_downstream", 1700, 1710, "CODING", "+", "Downstream_of_terminal_exon"),
    ("coding_alt_exon", 550, 560, "CODING", "+", NONCANONICAL_EXON_FEATURE),
    ("coding_alt_body", 800, 810, "CODING", "+", NONCANONICAL_GENE_BODY_FEATURE),
    # Shares only base 1001 with the canonical first exon; the historical
    # canonical rule misses it, and it must not be called noncanonical.
    ("coding_canonical_edge", 990, 1001, "CODING", "+", CANONICAL_MODEL_EDGE_FEATURE),
    # First base of the canonical intron 1101-1200 (BED 1100-1101).
    ("coding_intron_edge", 1100, 1101, "CODING", "+", CANONICAL_MODEL_EDGE_FEATURE),
    ("lnc_exon", 3850, 3860, "LNC_PLUS", "+", NONCODING_EXON_FEATURE),
    ("lnc_intron", 3500, 3510, "LNC_PLUS", "+", NONCODING_GENE_BODY_FEATURE),
    ("lnc_outside", 4500, 4510, "LNC_PLUS", "+", "Intergenic"),
    # BED end == GTF exon start: exactly one shared base.
    ("lnc_exon_left_touch", 3790, 3801, "LNC_PLUS", "+", NONCODING_EXON_FEATURE),
    ("lnc_exon_left_miss", 3790, 3800, "LNC_PLUS", "+", NONCODING_GENE_BODY_FEATURE),
    # BED start == GTF exon end: no shared base (covers 3201..3210).
    ("lnc_exon_right_miss", 3200, 3210, "LNC_PLUS", "+", NONCODING_GENE_BODY_FEATURE),
    ("lnc_exon_right_touch", 3199, 3210, "LNC_PLUS", "+", NONCODING_EXON_FEATURE),
    ("lnc_minus_exon", 6100, 6110, "LNC_MINUS", "-", NONCODING_EXON_FEATURE),
    ("lnc_minus_wrong_strand", 6100, 6110, "LNC_MINUS", "+", "Intergenic"),
    ("nested_lnc", 1500, 1510, "LNC_NESTED", "+", NONCODING_EXON_FEATURE),
    ("unassigned", 9000, 9010, None, "+", "Intergenic"),
    ("unknown_gene", 9000, 9010, "NOT_IN_GTF", "+", "Intergenic"),
    ("lnc_other_chrom", 3850, 3860, "LNC_PLUS", "+", "Intergenic", "chr2"),
]


@pytest.fixture()
def annotated(tmp_path):
    db = _database(tmp_path)
    canonical = get_canonical_transcripts(db)
    assert set(canonical) == {"CODING"}
    stops = get_stop_codon_positions(db, canonical)
    features = get_genomic_features(db, canonical)
    peaks = pd.DataFrame([_peak(n, s, e, g, strand, *extra) for n, s, e, g, strand, _, *extra in PEAKS])
    merged = merge_stop_codon_info(peaks, canonical, stops)
    old = annotate_peaks_with_features(merged, features)
    new = annotate_peaks_with_features(merged, features, gene_models=get_gene_models(db))
    return old, new


def test_sites_outside_the_coding_model_get_explicit_labels(annotated):
    old, new = annotated
    observed = dict(zip(new["peak_name"], new["feature"]))
    assert observed == {peak[0]: peak[5] for peak in PEAKS}
    # Before the fix every non-canonical-model site was "Intergenic".
    old_observed = dict(zip(old["peak_name"], old["feature"]))
    for name, _, _, _, _, expected, *_ in PEAKS:
        if expected in NEW_FEATURES:
            assert old_observed[name] == "Intergenic"
        else:
            assert old_observed[name] == expected


def test_relabelling_never_invents_coding_metrics(annotated):
    old, new = annotated
    assert not NEW_FEATURES & set(TERMINAL_FEATURES)
    assert INTRON_FEATURE not in NEW_FEATURES
    other = [c for c in old.columns if c != "feature"]
    pd.testing.assert_frame_equal(old[other], new[other])
    relabelled = new[new["feature"].isin(NEW_FEATURES)]
    noncoding = relabelled[relabelled["gene_id"].str.startswith("LNC")]
    assert noncoding["canonical_transcript"].isna().all()
    assert noncoding["stop_codon_pos"].isna().all()
    assert noncoding["distance_to_stop_codon"].isna().all()
    assert set(new.loc[new["peak_name"] == "nested_lnc", "gene_id"]) == {"LNC_NESTED"}


def test_terminal_and_intronic_metadata_are_unchanged(annotated):
    old, new = annotated

    def prepared(frame):
        frame = frame.copy()
        frame["three_prime_utr_length"] = frame["distance_to_stop_codon"]
        return prepare_terminal_peak_metadata(
            frame, general_threshold=0.9, intronic_threshold=0.7
        )

    before, after = prepared(old), prepared(new)
    for key in ("terminal_all", "terminal_for_psi", "intronic"):
        pd.testing.assert_frame_equal(before[key], after[key])
    assert before["stats"] == after["stats"]
    other = [c for c in before["audit"].columns if c != "feature"]
    pd.testing.assert_frame_equal(before["audit"][other], after["audit"][other])


def test_exon_overlap_requires_the_exon_chromosome():
    # Gene span and an exon recorded on another contig (PAR-like record).
    model = {"chrom": "chrX", "strand": "+", "start": 101, "end": 500,
             "has_cds": False, "exons": [("chrY", 101, 200, "T1")]}
    assert classify_outside_canonical_model("chrX", 120, 130, "+", model) == NONCODING_GENE_BODY_FEATURE
    model["exons"].append(("chrX", 101, 200, "T2"))
    assert classify_outside_canonical_model("chrX", 120, 130, "+", model) == NONCODING_EXON_FEATURE


@pytest.mark.parametrize("strand, stop, edge_bed, far_bed", [
    ("+", 1600, (1600, 1601), (3700, 3701)),   # base stop+1 vs. beyond the window
    ("-", 3000, (998, 1000), (500, 501)),      # bases 999-1000 touch window start 1000
])
def test_downstream_window_contact_is_a_canonical_model_edge(strand, stop, edge_bed, far_bed):
    # Unit-level check of the helper: in the pipeline the canonical classifier
    # already claims the minus-strand window-start bases as downstream.
    model = {"chrom": "chr1", "strand": strand, "start": 1, "end": 5000,
             "has_cds": True, "exons": []}
    canonical = {"chrom": "chr1", "strand": strand, "exons": [], "introns": [],
                 "stop_codon_pos": stop, "transcript_id": "T"}
    assert classify_outside_canonical_model("chr1", *edge_bed, strand, model, canonical) == CANONICAL_MODEL_EDGE_FEATURE
    assert classify_outside_canonical_model("chr1", *far_bed, strand, model, canonical) == NONCANONICAL_GENE_BODY_FEATURE


def test_canonical_model_contact_requires_the_canonical_chromosome():
    model = {"chrom": "chrX", "strand": "+", "start": 101, "end": 500,
             "has_cds": True, "exons": []}
    canonical = {"chrom": "chrY", "strand": "+", "exons": [], "introns": [{"start": 101, "end": 300}],
                 "stop_codon_pos": None, "transcript_id": "T"}
    assert classify_outside_canonical_model("chrX", 120, 130, "+", model, canonical) == NONCANONICAL_GENE_BODY_FEATURE
    canonical["chrom"] = "chrX"
    assert classify_outside_canonical_model("chrX", 120, 130, "+", model, canonical) == CANONICAL_MODEL_EDGE_FEATURE
