from __future__ import annotations

from types import SimpleNamespace
import shutil
import subprocess
from pathlib import Path

import gffutils
import pandas as pd
import pytest

from utils.annotation_utils import (
    _map_bulk_bed_to_reference_gene_ids,
    assign_peaks_to_genes,
    annotate_peaks_with_features,
    convert_peaks_bed_to_gtf,
    get_canonical_transcripts,
    get_genomic_features,
    get_stop_codon_positions,
    load_peak_assignments,
    merge_stop_codon_info,
    resolve_peak_issues,
)
from utils.io_utils import bedtools_sort, convert_gtf_to_bed
from utils.terminal_quant_utils import compute_terminal_aware_tables


def _run_gated_assignment_case(
    tmp_path,
    monkeypatch,
    *,
    include_gene_id: bool,
    closest_gene: str,
    gate_gene: str | None,
    preferred_gene: str | None,
):
    peak = ["chr1", 249, 250, "peak_1", 10, "+", 2, 0.0, 2]
    peaks_bed = tmp_path / "peaks.bed"
    peaks_bed.write_text("\t".join(map(str, peak)) + "\n")
    chrom_sizes = tmp_path / "chrom.sizes"
    chrom_sizes.write_text("chr1\t1000\n")

    genes = {
        "G1": ["chr1", "99", "400", "GENE1", "0", "+"],
        "G2": ["chr1", "99", "450", "GENE2", "0", "+"],
    }
    if include_gene_id:
        genes = {gene_id: row + [gene_id] for gene_id, row in genes.items()}
    gene_bed = tmp_path / "genes.bed"
    gene_bed.write_text(
        "".join("\t".join(row) + "\n" for row in genes.values())
    )

    def _bulk_row(gene_id: str) -> str:
        row = genes[gene_id]
        return "\t".join(row) + "\n"

    bulk_gene_bed = tmp_path / "bulk-gene.bed"
    bulk_gene_bed.write_text(_bulk_row(gate_gene or "G1"))
    bulk_window_bed = tmp_path / "bulk-window.bed"
    bulk_window_bed.write_text(_bulk_row(preferred_gene or "G1"))

    def fake_run_tool_command(command, **kwargs):
        kwargs["stdout"].write(
            "\t".join(list(map(str, peak)) + genes[closest_gene] + ["0"]) + "\n"
        )

    def fake_check_tool_output(command, **kwargs):
        operation = kwargs["operation"]
        if "intersect-gene-gate" in operation:
            gene_id = gate_gene
        else:
            gene_id = preferred_gene
        if gene_id is None:
            return ""
        return "\t".join(map(str, peak)) + "\t" + "\t".join(genes[gene_id]) + "\n"

    monkeypatch.setattr("utils.annotation_utils.run_tool_command", fake_run_tool_command)
    monkeypatch.setattr("utils.annotation_utils.check_tool_output", fake_check_tool_output)
    assigned_bed = tmp_path / "assigned.bed"
    assign_peaks_to_genes(
        peaks_bed=peaks_bed,
        gene_bed=gene_bed,
        chrom_sizes=chrom_sizes,
        assigned_peaks_bed=assigned_bed,
        bulk_window_bed=bulk_window_bed,
        bulk_gene_bed=bulk_gene_bed,
        include_gene_id=include_gene_id,
    )
    return assigned_bed


def _unfeatured_peak(*, gene_id):
    return {
        "chrom": "chr1",
        "start": 100,
        "end": 101,
        "peak_name": "noncoding_peak",
        "max_cov": 10,
        "strand": "+",
        "total_polyA_reads": 4,
        "misprimed_ratio": 0.1,
        "total_reads": 5,
        "gene_name": "Pantr1",
        "gene_id": gene_id,
        "distance_to_gene": 0,
        "canonical_transcript": pd.NA,
        "stop_codon_pos": pd.NA,
        "distance_to_stop_codon": pd.NA,
    }


def test_unfeatured_noncoding_gene_preserves_assigned_identity(
    tmp_path, monkeypatch
):
    annotated = annotate_peaks_with_features(
        pd.DataFrame([_unfeatured_peak(gene_id="ENSMUSG_NONCODING")]),
        features_dict={},
    )

    assert annotated.loc[0, "gene_id"] == "ENSMUSG_NONCODING"
    assert annotated.loc[0, "feature"] == "Intergenic"

    output_bed = tmp_path / "terminal.bed"
    chrom_sizes = tmp_path / "chrom.sizes"
    chrom_sizes.write_text("chr1\t1000\n")
    monkeypatch.setattr(
        "utils.annotation_utils.bedtools_sort",
        lambda input_bed, sorted_bed, chrom_sizes_sorted: shutil.copyfile(
            input_bed, sorted_bed
        ),
    )
    resolve_peak_issues(
        annotated,
        output_bed=output_bed,
        chrom_sizes_sorted=chrom_sizes,
        include_gene_id=True,
    )

    fields = output_bed.read_text().rstrip("\n").split("\t")
    assert len(fields) == 16
    assert fields[-1] == "ENSMUSG_NONCODING"


@pytest.mark.parametrize("missing_gene_id", [pd.NA, ""])
def test_terminal_bed_serializes_missing_trailing_gene_id_as_na(
    tmp_path, monkeypatch, missing_gene_id
):
    annotated = annotate_peaks_with_features(
        pd.DataFrame([_unfeatured_peak(gene_id=missing_gene_id)]),
        features_dict={},
    )
    output_bed = tmp_path / "terminal.bed"
    chrom_sizes = tmp_path / "chrom.sizes"
    chrom_sizes.write_text("chr1\t1000\n")
    monkeypatch.setattr(
        "utils.annotation_utils.bedtools_sort",
        lambda input_bed, sorted_bed, chrom_sizes_sorted: shutil.copyfile(
            input_bed, sorted_bed
        ),
    )

    resolve_peak_issues(
        annotated,
        output_bed=output_bed,
        chrom_sizes_sorted=chrom_sizes,
        include_gene_id=True,
    )

    fields = output_bed.read_text().rstrip("\n").split("\t")
    assert len(fields) == 16
    assert fields[-1] == "NA"
    assert pd.isna(pd.read_csv(output_bed, sep="\t", header=None).iloc[0, 15])


def test_bedtools_sort_preserves_called_process_error_and_stderr(
    tmp_path, monkeypatch, caplog
):
    input_bed = tmp_path / "input.bed"
    output_bed = tmp_path / "output.bed"
    chrom_sizes = tmp_path / "chrom.sizes"
    input_bed.write_text("chr1\t0\t1\n")
    chrom_sizes.write_text("chr1\t1000\n")
    original = subprocess.CalledProcessError(
        returncode=1,
        cmd=["bedtools", "sort"],
        stderr="Differing number of BED fields encountered at line: 42",
    )

    def fail(*args, **kwargs):
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["text"] is True
        raise original

    monkeypatch.setattr("utils.io_utils.run_tool_command", fail)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        bedtools_sort(input_bed, output_bed, chrom_sizes)

    assert exc_info.value is original
    assert "Differing number of BED fields" in exc_info.value.stderr
    assert "Differing number of BED fields" in caplog.text


def test_terminal_annotation_propagates_gene_id_and_filters_counting_gtf(tmp_path):
    peak = {
        "chrom": "chr1",
        "start": 100,
        "end": 110,
        "peak_name": "annotated_terminal",
        "max_cov": 10.0,
        "strand": "+",
        "total_polyA_reads": 4,
        "misprimed_ratio": 0.10,
        "total_reads": 5,
        "gene_name": "GENE",
        "distance_to_gene": 0,
    }
    terminal_exon = SimpleNamespace(chrom="chr1", start=90, end=120)
    features = {
        "GENE": {
            "gene_id": "ENSG_TRUE",
            "strand": "+",
            "chrom": "chr1",
            "exons": [terminal_exon],
            "introns": [],
            "stop_codon_pos": 120,
        }
    }
    annotated = annotate_peaks_with_features(pd.DataFrame([peak]), features)
    assert annotated.loc[0, "feature"] == "Terminal_exon"
    assert annotated.loc[0, "gene_id"] == "ENSG_TRUE"

    columns = [
        "chrom",
        "start",
        "end",
        "peak_name",
        "max_cov",
        "strand",
        "total_polyA_reads",
        "misprimed_ratio",
        "total_reads",
        "gene_name",
        "distance_to_gene",
        "canonical_transcript",
        "stop_codon_position",
        "distance_to_stop_codon",
        "feature",
        "gene_id",
    ]
    bed_rows = [
        ["chr1", 100, 110, "terminal_pass", 10, "+", 4, 0.20, 5, "GENE", 0, "TX", 90, 20, "Terminal_exon", "ENSG_TRUE"],
        ["chr1", 120, 130, "terminal_fail", 10, "+", 4, 0.21, 5, "GENE", 0, "TX", 90, 40, "Terminal_exon", "ENSG_TRUE"],
        ["chr1", 50, 60, "intron_audit", 10, "+", 4, 0.90, 5, "GENE", 0, "TX", 90, -30, "Intron", "ENSG_TRUE"],
    ]
    bed_path = tmp_path / "terminal-aware.bed"
    pd.DataFrame(bed_rows, columns=columns).to_csv(
        bed_path, sep="\t", header=False, index=False
    )
    gtf_path = tmp_path / "terminal-aware.gtf"
    convert_peaks_bed_to_gtf(
        bed_path,
        gtf_path,
        non_intronic_misprimed_ratio_threshold=0.20,
    )
    gtf_text = gtf_path.read_text()
    assert "peak_name terminal_pass;" in gtf_text
    assert "peak_name terminal_fail;" not in gtf_text
    assert "peak_name intron_audit;" in gtf_text
    assert "gene_id ENSG_TRUE;" in gtf_text


def test_legacy_fifteen_column_gtf_conversion_remains_supported(tmp_path):
    bed_row = [
        "chr1",
        10,
        11,
        "legacy_peak",
        5,
        "+",
        2,
        0.9,
        3,
        "GENE",
        0,
        "TX",
        9,
        2,
        "Terminal_exon",
    ]
    bed_path = tmp_path / "legacy.bed"
    bed_path.write_text("\t".join(map(str, bed_row)) + "\n")
    gtf_path = tmp_path / "legacy.gtf"
    convert_peaks_bed_to_gtf(bed_path, gtf_path)
    gtf_text = gtf_path.read_text()
    assert "peak_name legacy_peak;" in gtf_text
    assert "gene_id " not in gtf_text


def test_duplicate_display_names_retain_true_identity_through_terminal_bed_and_quantification(
    tmp_path, monkeypatch
):
    gtf_path = tmp_path / "duplicate-names.gtf"
    rows = []
    gene_specs = (
        ("G1", "T1", "+", 100, 300),
        ("G2", "T2", "+", 500, 700),
        ("G3", "T3", "-", 900, 1100),
    )
    for gene_id, transcript_id, strand, start, end in gene_specs:
        gene_attrs = f'gene_id "{gene_id}"; gene_name "DUP";'
        tx_attrs = (
            f'gene_id "{gene_id}"; transcript_id "{transcript_id}"; '
            f'gene_name "DUP";'
        )
        rows.extend(
            [
                ["chr1", "test", "gene", start, end, ".", strand, ".", gene_attrs],
                ["chr1", "test", "transcript", start, end, ".", strand, ".", tx_attrs],
                ["chr1", "test", "exon", start, end, ".", strand, ".", tx_attrs],
                ["chr1", "test", "CDS", start + 20, end - 20, ".", strand, "0", tx_attrs],
            ]
        )
    gtf_path.write_text(
        "".join("\t".join(map(str, row)) + "\n" for row in rows)
    )
    db_path = tmp_path / "annotation.db"
    gffutils.create_db(
        str(gtf_path),
        dbfn=str(db_path),
        force=True,
        keep_order=True,
        disable_infer_genes=True,
        disable_infer_transcripts=True,
    )
    db = gffutils.FeatureDB(str(db_path), keep_order=True)

    canonical = get_canonical_transcripts(db)
    assert set(canonical) == {"G1", "G2", "G3"}
    assert {value["gene_name"] for value in canonical.values()} == {"DUP"}
    stop_codons = get_stop_codon_positions(db, canonical)
    features = get_genomic_features(db, canonical)
    assert set(features) == {"G1", "G2", "G3"}

    gene_bed = tmp_path / "genes.bed"
    convert_gtf_to_bed(gtf_path, gene_bed)
    gene_rows = [line.split("\t") for line in gene_bed.read_text().splitlines()]
    assert {row[6] for row in gene_rows} == {"G1", "G2", "G3"}
    assert {row[3] for row in gene_rows} == {"DUP"}

    assigned_rows = []
    for gene_id, transcript_id, strand, start, end in gene_specs:
        pas_positions = (end - 20, end - 10) if strand == "+" else (start + 10, start + 20)
        for index, pas in enumerate(pas_positions, 1):
            peak_start = pas - 1
            peak_end = pas
            assigned_rows.append(
                [
                    "chr1", peak_start, peak_end, f"{gene_id}_p{index}", 10, strand,
                    3, 0.0, 3,
                    "chr1", start - 1, end, "DUP", 0, strand, gene_id, 0,
                ]
            )
    assigned_bed = tmp_path / "assigned.bed"
    assigned_bed.write_text(
        "".join("\t".join(map(str, row)) + "\n" for row in assigned_rows)
    )
    assignments = load_peak_assignments(assigned_bed)
    assert set(assignments["gene_id"]) == {"G1", "G2", "G3"}
    merged = merge_stop_codon_info(assignments, canonical, stop_codons)
    annotated = annotate_peaks_with_features(merged, features)
    assert set(annotated["gene_id"]) == {"G1", "G2", "G3"}

    chrom_sizes = tmp_path / "chrom.sizes"
    chrom_sizes.write_text("chr1\t2000\n")
    terminal_bed = tmp_path / "terminal.bed"
    monkeypatch.setattr(
        "utils.annotation_utils.bedtools_sort",
        lambda input_bed, sorted_bed, chrom_sizes_sorted: shutil.copyfile(
            input_bed, sorted_bed
        ),
    )
    resolve_peak_issues(
        annotated,
        output_bed=terminal_bed,
        chrom_sizes_sorted=chrom_sizes,
        include_gene_id=True,
    )
    terminal_rows = [line.split("\t") for line in terminal_bed.read_text().splitlines()]
    assert all(len(row) == 16 for row in terminal_rows)
    assert {row[15] for row in terminal_rows} == {"G1", "G2", "G3"}
    assert {row[9] for row in terminal_rows} == {"DUP"}

    metadata = pd.read_csv(terminal_bed, sep="\t", header=None)
    metadata.columns = [
        "chrom", "start", "end", "peak_name", "max_cov", "strand",
        "total_polyA_reads", "misprimed_ratio", "total_reads", "gene_name",
        "distance_to_gene", "canonical_transcript", "stop_codon_pos",
        "distance_to_stop_codon", "feature", "gene_id",
    ]
    metadata["three_prime_utr_length"] = metadata["distance_to_stop_codon"].clip(lower=0)
    counts = pd.DataFrame(
        [(peak_name, "cell", index + 1) for index, peak_name in enumerate(metadata["peak_name"])],
        columns=["peak_name", "cell_barcode", "umi_count"],
    )
    quantified = compute_terminal_aware_tables(
        counts,
        metadata,
        general_threshold=1.0,
        intronic_threshold=1.0,
        psi_min_gene_umis=1,
    )
    assert set(quantified["gene_cell"]["gene_id"]) == {"G1", "G2", "G3"}
    assert quantified["gene_cell"]["terminal_gene_unit_id"].nunique() == 3
    assert set(quantified["psi"]["gene_id"]) == {"G1", "G2", "G3"}


def test_bulk_gate_assignment_preserves_true_identity_end_to_end(tmp_path, monkeypatch):
    gtf_path = tmp_path / "overlapping.gtf"
    rows = []
    specs = (
        ("G1", "T1", "+", 100, 400, ((100, 180), (300, 400))),
        ("G2", "T2", "+", 100, 450, ((100, 200), (300, 450))),
        ("G3", "T3", "-", 100, 450, ((100, 200), (300, 450))),
    )
    for gene_id, transcript_id, strand, start, end, exons in specs:
        gene_attrs = f'gene_id "{gene_id}"; gene_name "DUP";'
        tx_attrs = (
            f'gene_id "{gene_id}"; transcript_id "{transcript_id}"; '
            f'gene_name "DUP";'
        )
        rows.extend(
            [
                ["chr1", "test", "gene", start, end, ".", strand, ".", gene_attrs],
                ["chr1", "test", "transcript", start, end, ".", strand, ".", tx_attrs],
            ]
        )
        for exon_start, exon_end in exons:
            rows.extend(
                [
                    ["chr1", "test", "exon", exon_start, exon_end, ".", strand, ".", tx_attrs],
                    ["chr1", "test", "CDS", exon_start + 20, exon_end - 20, ".", strand, "0", tx_attrs],
                ]
            )
    gtf_path.write_text("".join("\t".join(map(str, row)) + "\n" for row in rows))
    db_path = tmp_path / "overlapping.db"
    gffutils.create_db(
        str(gtf_path),
        dbfn=str(db_path),
        force=True,
        keep_order=True,
        disable_infer_genes=True,
        disable_infer_transcripts=True,
    )
    db = gffutils.FeatureDB(str(db_path), keep_order=True)
    canonical = get_canonical_transcripts(db)
    stop_codons = get_stop_codon_positions(db, canonical)
    features = get_genomic_features(db, canonical)

    gene_bed = tmp_path / "genes.bed"
    convert_gtf_to_bed(gtf_path, gene_bed)
    expected_gene_bed = (
        "chr1\t99\t400\tDUP\t0\t+\tG1\n"
        "chr1\t99\t450\tDUP\t0\t+\tG2\n"
        "chr1\t99\t450\tDUP\t0\t-\tG3\n"
    )
    assert gene_bed.read_text() == expected_gene_bed

    peak_rows = [
        ["chr1", 349, 350, "g1_terminal_1", 10, "+", 3, 0.0, 3],
        ["chr1", 389, 390, "g1_terminal_2", 10, "+", 1, 0.0, 1],
        ["chr1", 249, 250, "g1_intron", 10, "+", 2, 0.0, 2],
        ["chr1", 799, 800, "outside_bulk_gate", 10, "+", 1, 0.0, 1],
    ]
    peaks_bed = tmp_path / "peaks.bed"
    expected_peaks = "".join(
        "\t".join(map(str, row)) + "\n" for row in peak_rows
    )
    peaks_bed.write_text(expected_peaks)
    chrom_sizes = tmp_path / "chrom.sizes"
    chrom_sizes.write_text("chr1\t1000\n")
    bulk_gtf = tmp_path / "bulk.gtf"
    bulk_gtf.write_text(
        'chr1\tbulk\tgene_locus\t90\t410\t.\t+\t.\tgene_id "G1"; gene_name "DUP";\n'
        'chr1\tbulk\tthree_prime_utr\t90\t410\t.\t+\t.\tgene_id "G1"; gene_name "DUP";\n'
    )
    ambiguous_bulk = tmp_path / "ambiguous-bulk.bed"
    ambiguous_bulk.write_text("chr1\t89\t410\tDUP\t0\t+\t.\n")
    ambiguous_mapped = tmp_path / "ambiguous-mapped.bed"
    assert _map_bulk_bed_to_reference_gene_ids(
        ambiguous_bulk, gene_bed, ambiguous_mapped
    ) == (0, 1)
    assert ambiguous_mapped.read_text() == ""

    expected_closest_lines = []
    for peak in peak_rows:
        a = list(map(str, peak))
        expected_closest_lines.extend(
            [
                "\t".join(a + ["chr1", "99", "400", "DUP", "0", "+", "G1", "0"]),
                "\t".join(a + ["chr1", "99", "450", "DUP", "0", "+", "G2", "0"]),
            ]
        )
    expected_closest = "\n".join(expected_closest_lines) + "\n"
    expected_mapped_bulk = "chr1\t89\t410\tDUP\t0\t+\tG1\n"
    expected_intersect = "".join(
        "\t".join(map(str, peak)) + "\t" + expected_mapped_bulk
        for peak in peak_rows[:3]
    )
    observed_commands = []

    def fake_run_tool_command(command, **kwargs):
        observed_commands.append(tuple(map(str, command)))
        assert command[:7] == ["bedtools", "closest", "-s", "-D", "a", "-t", "all"]
        assert Path(command[command.index("-a") + 1]).read_text() == expected_peaks
        assert Path(command[command.index("-b") + 1]).read_text() == expected_gene_bed
        assert "\tG3\t" not in expected_closest
        kwargs["stdout"].write(expected_closest)

    def fake_check_tool_output(command, **kwargs):
        observed_commands.append(tuple(map(str, command)))
        assert command[:5] == ["bedtools", "intersect", "-s", "-wa", "-wb"]
        assert Path(command[command.index("-a") + 1]).read_text() == expected_peaks
        assert Path(command[command.index("-b") + 1]).read_text() == expected_mapped_bulk
        return expected_intersect

    monkeypatch.setattr("utils.annotation_utils.run_tool_command", fake_run_tool_command)
    monkeypatch.setattr("utils.annotation_utils.check_tool_output", fake_check_tool_output)
    assigned_bed = tmp_path / "assigned.bed"
    assign_peaks_to_genes(
        peaks_bed=peaks_bed,
        gene_bed=gene_bed,
        chrom_sizes=chrom_sizes,
        assigned_peaks_bed=assigned_bed,
        bulk_window_gtf=bulk_gtf,
        bulk_gene_gtf=bulk_gtf,
        include_gene_id=True,
        identity_gtf=gtf_path,
    )
    assert len(observed_commands) == 3

    assigned_rows = [row.split("\t") for row in assigned_bed.read_text().splitlines()]
    assert all(len(row) == 20 for row in assigned_rows)
    assignments = load_peak_assignments(assigned_bed)
    assert len(assignments) == 4
    assert assignments["gate"].value_counts().to_dict() == {"PASS": 3, "NO_WINDOW": 1}
    assignments = assignments[assignments["gate"] == "PASS"].copy()
    assert set(assignments["gene_id"]) == {"G1"}
    merged = merge_stop_codon_info(assignments, canonical, stop_codons)
    annotated = annotate_peaks_with_features(merged, features)
    assert set(annotated["gene_id"]) == {"G1"}
    assert set(annotated["feature"]) == {"Terminal_exon", "Intron"}

    monkeypatch.setattr(
        "utils.annotation_utils.bedtools_sort",
        lambda input_bed, sorted_bed, chrom_sizes_sorted: shutil.copyfile(
            input_bed, sorted_bed
        ),
    )
    terminal_bed = tmp_path / "terminal.bed"
    resolve_peak_issues(
        annotated,
        output_bed=terminal_bed,
        chrom_sizes_sorted=chrom_sizes,
        include_gene_id=True,
    )
    assert {row.split("\t")[15] for row in terminal_bed.read_text().splitlines()} == {"G1"}
    terminal_gtf = tmp_path / "terminal.gtf"
    convert_peaks_bed_to_gtf(
        terminal_bed,
        terminal_gtf,
        non_intronic_misprimed_ratio_threshold=1.0,
    )
    assert "gene_id G1;" in terminal_gtf.read_text()
    assert "gene_id G2;" not in terminal_gtf.read_text()

    metadata = pd.read_csv(terminal_bed, sep="\t", header=None)
    metadata.columns = [
        "chrom", "start", "end", "peak_name", "max_cov", "strand",
        "total_polyA_reads", "misprimed_ratio", "total_reads", "gene_name",
        "distance_to_gene", "canonical_transcript", "stop_codon_pos",
        "distance_to_stop_codon", "feature", "gene_id",
    ]
    metadata["three_prime_utr_length"] = metadata["distance_to_stop_codon"].clip(lower=0)
    counts = pd.DataFrame(
        [
            ("DUP_peak_1", "cell", 3),
            ("DUP_peak_2", "cell", 1),
            ("DUP_peak_3", "cell", 2),
        ],
        columns=["peak_name", "cell_barcode", "umi_count"],
    )
    quantified = compute_terminal_aware_tables(
        counts,
        metadata,
        general_threshold=1.0,
        intronic_threshold=1.0,
        psi_min_gene_umis=1,
    )
    assert set(quantified["psi"]["gene_id"]) == {"G1"}
    assert set(quantified["weighted_utr"]["gene_id"]) == {"G1"}
    assert set(quantified["per_site"]["gene_id"]) == {"G1"}
    assert set(quantified["gene_total"]["gene_id"]) == {"G1"}


@pytest.mark.parametrize(
    ("include_gene_id", "expected_width"),
    ((False, 19), (True, 20)),
)
def test_gated_assignment_all_placeholders_preserves_schema(
    tmp_path,
    monkeypatch,
    include_gene_id,
    expected_width,
):
    assigned_bed = _run_gated_assignment_case(
        tmp_path,
        monkeypatch,
        include_gene_id=include_gene_id,
        closest_gene="G1",
        gate_gene=None,
        preferred_gene=None,
    )
    rows = [line.split("\t") for line in assigned_bed.read_text().splitlines()]
    assert len(rows) == 1
    assert len(rows[0]) == expected_width
    assignments = load_peak_assignments(assigned_bed)
    assert assignments.loc[0, "gate"] == "NO_WINDOW"
    assert ("gene_id" in assignments.columns) is include_gene_id


def test_gated_assignment_emits_fail_placeholder_with_identity_schema(
    tmp_path,
    monkeypatch,
):
    assigned_bed = _run_gated_assignment_case(
        tmp_path,
        monkeypatch,
        include_gene_id=True,
        closest_gene="G2",
        gate_gene="G2",
        preferred_gene="G1",
    )
    rows = [line.split("\t") for line in assigned_bed.read_text().splitlines()]
    assert len(rows) == 1
    assert len(rows[0]) == 20
    assignments = load_peak_assignments(assigned_bed)
    assert assignments.loc[0, "gate"] == "FAIL"
    assert assignments.loc[0, "tentative_gene"] == "GENE2"


@pytest.mark.parametrize(
    ("gene_columns", "gate_columns", "expected_columns"),
    (
        (["chr1", "10", "20", "GENE", "0", "+"], [], 16),
        (["chr1", "10", "20", "GENE", "0", "+", "G1"], [], 17),
        (
            ["chr1", "10", "20", "GENE", "0", "+"],
            ["PASS", "GENE", "0"],
            19,
        ),
        (
            ["chr1", "10", "20", "GENE", "0", "+", "G1"],
            ["PASS", "GENE", "0"],
            20,
        ),
    ),
)
def test_load_peak_assignments_accepts_supported_schemas(
    tmp_path,
    gene_columns,
    gate_columns,
    expected_columns,
):
    peak_columns = ["chr1", "1", "2", "peak", "3", "+", "1", "0.0", "1"]
    row = peak_columns + gene_columns + ["0"] + gate_columns
    assigned_bed = tmp_path / f"assigned-{expected_columns}.bed"
    assigned_bed.write_text("\t".join(row) + "\n")
    assignments = load_peak_assignments(assigned_bed)
    assert len(row) == expected_columns
    assert len(assignments) == 1
    assert ("gene_id" in assignments.columns) is (expected_columns in {17, 20})
    assert ("gate" in assignments.columns) is (expected_columns in {19, 20})


def test_load_peak_assignments_rejects_unsupported_width_before_conversion(tmp_path):
    assigned_bed = tmp_path / "assigned-23.bed"
    assigned_bed.write_text("\t".join(["not-a-number"] * 23) + "\n")
    with pytest.raises(Exception, match=r"expected 16, 17, 19, or 20.*observed 23"):
        load_peak_assignments(assigned_bed)


def test_load_peak_assignments_rejects_inconsistent_row_widths(tmp_path):
    assigned_bed = tmp_path / "assigned-mixed.bed"
    assigned_bed.write_text(
        "\t".join(["."] * 20) + "\n" + "\t".join(["."] * 19) + "\n"
    )
    with pytest.raises(Exception, match=r"expected 20.*observed 19 on line 2"):
        load_peak_assignments(assigned_bed)
