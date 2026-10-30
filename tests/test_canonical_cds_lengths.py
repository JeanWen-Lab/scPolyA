from __future__ import annotations

import gffutils
import pandas as pd
import pytest

from utils.annotation_utils import (
    get_canonical_transcripts, get_genomic_features, get_stop_codon_positions,
    merge_stop_codon_info,
)


def _database(tmp_path, *, strand, lengths, short_length):
    rows = []

    def add(feature, start, end, transcript=None):
        attrs = 'gene_id "G1"; gene_name "GENE";'
        if transcript:
            attrs += f' transcript_id "{transcript}";'
        rows.append(f"chr1\tfixture\t{feature}\t{start}\t{end}\t.\t{strand}\t0\t{attrs}\n")

    add("gene", 1, 1000)
    add("transcript", 1, short_length, "SHORT")
    add("exon", 1, short_length, "SHORT")
    add("CDS", 1, short_length, "SHORT")
    segments = [(201 + i * 100, length) for i, length in enumerate(lengths)]
    add("transcript", segments[0][0], segments[-1][0] + segments[-1][1] - 1, "LONG")
    for start, length in segments:
        add("exon", start, start + length - 1, "LONG")
        add("CDS", start, start + length - 1, "LONG")
    add("transcript", 900, 950, "NO_CDS")
    add("exon", 900, 950, "NO_CDS")
    path = tmp_path / "canonical.gtf"
    path.write_text("".join(rows))
    return gffutils.create_db(str(path), dbfn=":memory:", force=True,
                             disable_infer_genes=True, disable_infer_transcripts=True)


@pytest.mark.parametrize("strand", ["+", "-"])
def test_longest_total_cds_uses_inclusive_gtf_lengths_and_propagates(tmp_path, strand):
    db = _database(tmp_path, strand=strand, lengths=[30] * 5, short_length=147)
    canonical = get_canonical_transcripts(db)
    assert canonical["G1"]["transcript_id"] == "LONG"
    stops = get_stop_codon_positions(db, canonical)
    assert stops["LONG"]["pos"] == (631 if strand == "+" else 200)
    assert stops["LONG"]["strand"] == strand
    features = get_genomic_features(db, canonical)
    assert features["G1"]["transcript_id"] == "LONG"
    assert len(features["G1"]["exons"]) == 5
    assert len(features["G1"]["introns"]) == 4
    assert features["G1"]["strand"] == strand
    peak = pd.DataFrame([{"chrom": "chr1", "start": 700, "end": 701,
                          "gene_id": "G1", "gene_name": "GENE", "strand": strand}])
    merged = merge_stop_codon_info(peak, canonical, stops)
    assert merged["canonical_transcript"].tolist() == ["LONG"]
    assert merged["stop_codon_pos"].tolist() == [631 if strand == "+" else 200]
    assert merged["distance_to_stop_codon"].tolist() == [70 if strand == "+" else -500]


@pytest.mark.parametrize("strand", ["+", "-"])
def test_single_base_cds_segments_are_not_zero_length(tmp_path, strand):
    db = _database(tmp_path, strand=strand, lengths=[1, 1, 1], short_length=2)
    assert get_canonical_transcripts(db)["G1"]["transcript_id"] == "LONG"


@pytest.mark.parametrize("strand", ["+", "-"])
def test_equal_inclusive_lengths_preserve_existing_start_order_tie(tmp_path, strand):
    db = _database(tmp_path, strand=strand, lengths=[30] * 5, short_length=150)
    assert get_canonical_transcripts(db)["G1"]["transcript_id"] == "SHORT"


def test_gene_without_cds_remains_without_canonical_transcript(tmp_path):
    path = tmp_path / "noncoding.gtf"
    path.write_text(
        'chr1\tfixture\tgene\t1\t100\t.\t+\t.\tgene_id "G1";\n'
        'chr1\tfixture\ttranscript\t1\t100\t.\t+\t.\tgene_id "G1"; transcript_id "T1";\n'
        'chr1\tfixture\texon\t1\t100\t.\t+\t.\tgene_id "G1"; transcript_id "T1";\n'
    )
    db = gffutils.create_db(str(path), dbfn=":memory:", force=True,
                           disable_infer_genes=True, disable_infer_transcripts=True)
    assert get_canonical_transcripts(db) == {}
