import os

from scpolya_ui.igv_manifest import (
    GeneLocus, IgvManifest, IgvTrack, _igv_track_options, transcript_model_tracks, with_transcript_models,
)
from scpolya_ui.transcript_annotation import gtf_source_label, scoped_transcript_models

HEADER = ("##description: evidence-based annotation of the mouse genome (GRCm38), version M23 (Ensembl 98)\n"
          "##provider: GENCODE\n")


def _row(chrom, feature, start, end, strand, tid, name="", gene="G"):
    attrs = f'gene_id "{gene}.1"; gene_name "{gene}";'
    if tid:
        attrs += f' transcript_id "{tid}"; transcript_name "{name or tid}";'
    return f"{chrom}\tTEST\t{feature}\t{start}\t{end}\t.\t{strand}\t.\t{attrs}\n"


def _gtf(tmp_path, rows, header=HEADER):
    path = tmp_path / "ann.gtf"
    path.write_text(header + "".join(rows))
    return path


# Plus-strand transcript with 5'UTR, CDS, stop codon and a long terminal 3'UTR exon,
# a minus-strand transcript in the same window, and one transcript far away.
ROWS = [
    _row("chr4", "gene", 100, 2000, "+", "", gene="Gp"),
    _row("chr4", "transcript", 100, 2000, "+", "TP.1", "Gp-201", gene="Gp"),
    _row("chr4", "exon", 100, 300, "+", "TP.1", "Gp-201", gene="Gp"),
    _row("chr4", "UTR", 100, 149, "+", "TP.1", "Gp-201", gene="Gp"),
    _row("chr4", "CDS", 150, 300, "+", "TP.1", "Gp-201", gene="Gp"),
    _row("chr4", "exon", 500, 2000, "+", "TP.1", "Gp-201", gene="Gp"),
    _row("chr4", "CDS", 500, 600, "+", "TP.1", "Gp-201", gene="Gp"),
    _row("chr4", "stop_codon", 601, 603, "+", "TP.1", "Gp-201", gene="Gp"),
    _row("chr4", "UTR", 601, 2000, "+", "TP.1", "Gp-201", gene="Gp"),
    _row("chr4", "transcript", 1500, 2600, "-", "TM.1", "Gm-201", gene="Gm"),
    _row("chr4", "exon", 2400, 2600, "-", "TM.1", "Gm-201", gene="Gm"),
    _row("chr4", "CDS", 2400, 2500, "-", "TM.1", "Gm-201", gene="Gm"),
    _row("chr4", "UTR", 2501, 2600, "-", "TM.1", "Gm-201", gene="Gm"),
    _row("chr4", "exon", 1500, 1700, "-", "TM.1", "Gm-201", gene="Gm"),
    _row("chr4", "UTR", 1500, 1700, "-", "TM.1", "Gm-201", gene="Gm"),
    _row("chr4", "transcript", 90000, 91000, "+", "TF.1", "Far-201", gene="Far"),
    _row("chr4", "exon", 90000, 91000, "+", "TF.1", "Far-201", gene="Far"),
    _row("chr5", "transcript", 100, 2000, "+", "OTHER.1", "Other-201", gene="Other"),
    _row("chr5", "exon", 100, 2000, "+", "OTHER.1", "Other-201", gene="Other"),
]


def test_window_keeps_full_structure_of_overlapping_transcripts_on_both_strands(tmp_path):
    gtf = _gtf(tmp_path, ROWS)
    models = scoped_transcript_models(gtf, "chr4", 1800, 1900, tmp_path / "cache")
    text = models.path.read_text().splitlines()
    body = [line for line in text if not line.startswith("#")]
    # Rows outside the window are kept when their transcript overlaps it: the
    # 5' exon and CDS at 100-300 belong to TP.1, which spans 1800-1900.
    assert body == [row.rstrip("\n") for row in ROWS[1:15]]
    assert (models.transcripts, models.rows, models.skipped_rows, models.unordered_rows) == (2, 14, 0, 0)
    assert models.source_label == "GENCODE M23"
    assert all('"TF.1"' not in line and "chr5" not in line for line in body)
    # Gene rows are not transcript models.
    assert not any("\tgene\t" in line for line in body)


def test_window_boundaries_are_inclusive_one_based(tmp_path):
    gtf = _gtf(tmp_path, ROWS)
    only_minus = scoped_transcript_models(gtf, "chr4", 2001, 2001, tmp_path / "c1")
    assert only_minus.transcripts == 1 and '"TM.1"' in only_minus.path.read_text()
    touching_end = scoped_transcript_models(gtf, "chr4", 2000, 2000, tmp_path / "c2")
    assert touching_end.transcripts == 2
    none = scoped_transcript_models(gtf, "chr4", 3000, 4000, tmp_path / "c3")
    assert none.transcripts == 0 and none.rows == 0


def test_rows_without_transcript_id_or_before_their_transcript_row_are_counted_not_drawn(tmp_path):
    rows = [
        _row("chr4", "exon", 1800, 1900, "+", "LATE.1"),  # child before its transcript row
        _row("chr4", "transcript", 1700, 1950, "+", "LATE.1"),
        _row("chr4", "exon", 1700, 1950, "+", "LATE.1"),
        _row("chr4", "exon", 1800, 1900, "+", ""),  # no transcript_id
    ]
    models = scoped_transcript_models(_gtf(tmp_path, rows), "chr4", 1800, 1900, tmp_path / "cache")
    assert (models.transcripts, models.rows, models.skipped_rows, models.unordered_rows) == (1, 2, 1, 1)


def test_cache_is_reused_and_invalidated_when_the_source_changes(tmp_path):
    gtf = _gtf(tmp_path, ROWS)
    first = scoped_transcript_models(gtf, "chr4", 1800, 1900, tmp_path / "cache")
    marker = first.path.stat().st_mtime_ns
    again = scoped_transcript_models(gtf, "chr4", 1800, 1900, tmp_path / "cache")
    assert again == first and again.path.stat().st_mtime_ns == marker
    gtf.write_text(HEADER + "".join(ROWS[:9]))
    os.utime(gtf, ns=(marker + 10**9, marker + 10**9))
    changed = scoped_transcript_models(gtf, "chr4", 1800, 1900, tmp_path / "cache")
    assert changed.path != first.path and changed.transcripts == 1


def test_missing_or_invalid_inputs_return_none(tmp_path):
    assert scoped_transcript_models(tmp_path / "absent.gtf", "chr4", 1, 10, tmp_path) is None
    gtf = _gtf(tmp_path, ROWS)
    assert scoped_transcript_models(gtf, "chr4", 10, 1, tmp_path) is None
    assert scoped_transcript_models(gtf, "", 1, 10, tmp_path) is None


def test_source_label_falls_back_to_file_name(tmp_path):
    assert gtf_source_label(_gtf(tmp_path, ROWS, header="")) == "ann.gtf"
    assert gtf_source_label(_gtf(tmp_path, ROWS)) == "GENCODE M23"


def test_manifest_track_is_inserted_after_gene_span_and_labelled_with_source(tmp_path):
    gtf = _gtf(tmp_path, ROWS)
    gene_span = IgvTrack("Gene model", "g.bed", "/g.bed", "bed", "annotation", "reference_context")
    peaks = IgvTrack("Final annotated peaks BED", "p.bed", "/p.bed", "bed", "annotation", "annotated_peak")
    manifest = IgvManifest(study_id="R", gene="Gp", locus=GeneLocus("Gp", "chr4", 1800, 1900),
                           tracks=(gene_span, peaks), missing_tracks=(), reference_gtf=str(gtf))
    out = with_transcript_models(manifest, lambda p: "/u/" + p.rsplit("/", 1)[-1], tmp_path / "cache")
    assert [t.role for t in out.tracks] == ["reference_context", "transcript_model", "annotated_peak"]
    track = out.tracks[1]
    # The label names the range the subset covers (the padded initial window).
    assert track.name == "Transcripts · GENCODE M23 · chr4:1,300-2,400" and track.format == "gtf"
    assert out.missing_tracks == ()
    options = _igv_track_options(track.as_dict(), 1)
    assert options["nameField"] == "transcript_name" and options["displayMode"] == "EXPANDED"
    assert options["autoHeight"] is True


def test_transcript_track_does_not_shift_other_track_colours(tmp_path):
    from scpolya_ui.igv_manifest import manifest_as_igv_options
    gtf = _gtf(tmp_path, ROWS)
    tracks = (IgvTrack("Gene model", "g.bed", "/g.bed", "bed", "annotation", "reference_context"),
              IgvTrack("polyA coverage +", "p.bw", "/p.bw", "bigwig", "wig", "coverage_polya"))
    base = IgvManifest(study_id="R", gene="Gp", locus=GeneLocus("Gp", "chr4", 1800, 1900),
                       tracks=tracks, missing_tracks=(), reference_gtf=str(gtf))
    with_tx = with_transcript_models(base, str, tmp_path / "cache")
    colours = {t["name"]: t["color"] for t in manifest_as_igv_options(base)["tracks"]}
    colours_tx = {t["name"]: t["color"] for t in manifest_as_igv_options(with_tx)["tracks"]}
    assert {k: colours_tx[k] for k in colours} == colours


def test_missing_gtf_or_empty_window_is_reported_not_faked(tmp_path):
    locus = GeneLocus("Gp", "chr4", 1800, 1900)
    tracks, notes = transcript_model_tracks(locus, str(tmp_path / "absent.gtf"), str, tmp_path)
    assert tracks == () and notes == ("transcript models: the run's reference GTF is not readable",)
    gtf = _gtf(tmp_path, ROWS)
    tracks, notes = transcript_model_tracks(GeneLocus("X", "chr9", 10, 20), str(gtf), str, tmp_path)
    assert tracks == () and notes and "no GENCODE M23 transcript overlaps chr9:" in notes[0]
    assert transcript_model_tracks(None, str(gtf), str, tmp_path) == ((), ())


def test_concurrent_cold_cache_builds_of_one_key_both_succeed(tmp_path, monkeypatch):
    import threading
    from scpolya_ui import transcript_annotation as ta
    gtf = _gtf(tmp_path, ROWS)
    cache = tmp_path / "cache"
    # Hold both builders after their cache-miss check and scan, so both publish.
    gate = threading.Barrier(2, timeout=10)
    label = ta.gtf_source_label
    monkeypatch.setattr(ta, "gtf_source_label", lambda path: (gate.wait(), label(path))[1])
    results, errors = [], []

    def build():
        try:
            results.append(ta.scoped_transcript_models(gtf, "chr4", 1800, 1900, cache))
        except Exception as error:  # noqa: BLE001 - the failure itself is the assertion
            errors.append(error)

    threads = [threading.Thread(target=build) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and len(results) == 2 and results[0] == results[1]
    assert sorted(p.name for p in cache.iterdir()) == sorted([results[0].path.name, results[0].path.with_suffix(".json").name])
    assert results[0].transcripts == 2


def test_transcript_scope_option_follows_the_track_label():
    from scpolya_ui.igv_manifest import _transcript_scope
    assert _transcript_scope({"name": "Transcripts · GENCODE M23 · chr4:155,554,280-155,562,264"}) == {
        "chr": "chr4", "start": 155554280, "end": 155562264}
    assert _transcript_scope({"name": "Gene model"}) is None
