from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from scpolya_ui.config_schema import CONFIG_FIELDS
from scpolya_ui import igv_manifest as discovery
from scpolya_ui.run_records import RunRecord, config_hash
from scpolya_ui.services.igv_service import comparison_manifest, comparison_run_label, single_run_manifest, study_manifest
from scpolya_ui.studies import StudyRecord


@pytest.fixture
def runs(tmp_path, monkeypatch):
    fasta = tmp_path / "reference.fa"
    fasta.write_text(">chr1\n" + "A" * 100 + "\n")
    Path(str(fasta) + ".fai").write_text("chr1\t100\t6\t100\t101\n")
    gtf = tmp_path / "genes.gtf"
    gtf.write_text('chr1\ttest\tgene\t10\t40\t.\t+\t.\tgene_id "g"; gene_name "TNFRSF1B";\n')
    records = []
    for i, depth in enumerate((3.0, 1.0)):
        root = tmp_path / f"run-{i}"
        cohort = root / "cohort.directional.polyA-5-1-10"
        cohort.mkdir(parents=True)
        # Both settings coexist in each directory: lexical first is wrong for A.
        for variant in (1.0, 3.0):
            for strand in ("plus", "minus"):
                (cohort / f"cohort.directional.polyA-5-1-10.{strand}.coverage.peaks-merged-{variant}-10.bed").write_text("chr1\t15\t20\n")
        expression = root / "cohort.directional"
        expression.mkdir()
        for strand in ("plus", "minus"):
            (expression / f"cohort.directional.{strand}.coverage.scaled.removeCDS.peaks-merged-dyn-max_norm_fixed-n0.05-10.bed").write_text("chr1\t15\t20\n")
            (expression / f"cohort.directional.{strand}.coverage.scaled.bw").write_bytes(b"fixture")
            (cohort / f"cohort.directional.polyA-5-1-10.{strand}.coverage.bw").write_bytes(b"fixture")
        final = root / "final.bed"
        final.write_text("chr1\t15\t20\n")
        config = {f.key: f.default for f in CONFIG_FIELDS}
        config.update(gene_gtf=str(gtf), fasta=str(fasta), species="human", dedup_method="directional",
                      minpolyA=5, nonA_allowed=1, minMAPQ=10, coverage_scaling="expr",
                      polyA_peak_threshold_mode="fixed", unscaled_depth_threshold_polyA=depth,
                      exp_peak_threshold_mode="dynamic", exp_dynamic_threshold_stat="max_norm_fixed",
                      exp_dynamic_threshold_normalized=0.05,
                      merge_distance_polyA=10, merge_distance=10, output_dir=str(tmp_path), run_id=root.name)
        records.append(RunRecord(id=f"R-{i}", kind="cli_invocation", status="succeeded", workspace_id=f"W-{i}",
                                 workspace_root=str(root), label=root.name, config=config, config_hash=config_hash(config),
                                 created_at="", updated_at="", root_path=str(root), execution_config=config,
                                 execution_config_hash=config_hash(config), invocation_id=f"invocation-{i}",
                                 history_source="invocation", expected_artifacts={"annotated_bed": str(final)}))

    def project(config, url_for_file):
        root = tmp_path / config["run_id"]
        tracks = discovery._projected_cohort_tracks(str(root / "**" / "*peaks-merged*.bed"),
                                                   config=config, kind="peaks", url_for_file=url_for_file)
        tracks += discovery._projected_cohort_tracks(str(root / "**" / "*.bw"), config=config,
                                                    kind="coverage", url_for_file=url_for_file)
        tracks += (discovery._peak_track("Final", root / "final.bed", "annotated_peak", url_for_file),)
        return discovery._ProjectedTrackDiscovery(tracks=tracks, fallback_allowed=False)

    monkeypatch.setattr(discovery, "_projected_stage_tracks", project)
    return records


def test_comparison_uses_each_execution_and_preserves_identity(runs):
    before = deepcopy(runs)
    manifest = comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)
    assert manifest == comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)
    payload = manifest.as_dict()
    assert payload["study_id"] == ""
    assert payload["source"] == "independent_runs"
    assert [s["invocation_id"] for s in payload["run_sources"]] == [r.invocation_id for r in runs]
    for record, depth in zip(runs, (3.0, 1.0)):
        tracks = [t for t in manifest.tracks if t.run_record_id == record.id and t.role == "raw_peak_polya"]
        assert len(tracks) == 2
        assert all(f"peaks-merged-{depth}-10.bed" in t.path for t in tracks)
        assert all(t.invocation_id == record.invocation_id and not t.study_id for t in tracks)
        assert all(f"polyA threshold={depth} fixed/unscaled" in t.name for t in tracks)
        assert "minpolyA=5" in comparison_run_label(record)
        assert all("minpolyA=5" in t.name and "tail=" not in t.name for t in tracks)
    assert runs == before


@pytest.mark.parametrize("scaling,mode,stat,expected,units", [
    ("both", "fixed", "max_norm_fixed", "2.5", "scaled"),
    ("expr", "dynamic", "max_norm_fixed", "dyn-max_norm_fixed-n0.17", "unscaled"),
    ("both", "dynamic", "median", "dyn-median-a1.5-f0.2", "scaled"),
])
def test_labels_and_paths_follow_active_threshold_mode(runs, scaling, mode, stat, expected, units):
    for record in runs:
        config = dict(record.execution_config, coverage_scaling=scaling,
                      polyA_peak_threshold_mode=mode, scaled_depth_threshold_polyA=2.5,
                      unscaled_depth_threshold_polyA=19.0, polyA_dynamic_threshold_stat=stat,
                      polyA_dynamic_threshold_normalized=0.17,
                      polyA_dynamic_threshold_alpha=1.5, polyA_dynamic_threshold_floor=0.2)
        record.execution_config = config
        record.execution_config_hash = config_hash(config)
        cohort = Path(record.root_path) / "cohort.directional.polyA-5-1-10"
        scaled_suffix = ".scaled" if units == "scaled" else ""
        # Literal producer paths coexist with inactive unscaled 1.0/3.0 files.
        for strand in ("plus", "minus"):
            stem = f"cohort.directional.polyA-5-1-10.{strand}.coverage{scaled_suffix}"
            (cohort / f"{stem}.bw").write_bytes(b"fixture")
            (cohort / f"{stem}.peaks-merged-{expected}-10.bed").write_text("chr1\t15\t20\n")
    before = deepcopy(runs)
    manifest = comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)
    for record in runs:
        assert f"polyA threshold={expected} {mode}/{units}" in comparison_run_label(record)
        assert "minpolyA=5" in comparison_run_label(record)
        own = [t for t in manifest.tracks if t.run_record_id == record.id]
        assert all(f"polyA threshold={expected} {mode}/{units}" in t.name for t in own)
        assert all("minpolyA=5" in t.name and "tail=" not in t.name for t in own)
        peaks = [t for t in own if t.role == "raw_peak_polya"]
        assert len(peaks) == 2
        assert all(f"peaks-merged-{expected}-10.bed" in t.path for t in peaks)
        assert all((".coverage.scaled." in t.path) == (units == "scaled") for t in peaks)
    assert runs == before


@pytest.mark.parametrize("field,value,reason", [("species", "mouse", "reference mismatch"),
                                                ("gene_gtf", "other", "reference mismatch"),
                                                ("fasta", "other", "reference mismatch")])
def test_different_reference_refuses_even_if_gene_coordinates_match(runs, tmp_path, field, value, reason):
    second = runs[1]
    if value == "other":
        original = Path(second.execution_config[field])
        other = tmp_path / ("other" + original.suffix)
        other.write_bytes(original.read_bytes())
        if field == "fasta":
            Path(str(other) + ".fai").write_bytes(Path(str(original) + ".fai").read_bytes())
        value = str(other)
    second.execution_config = dict(second.execution_config, **{field: value})
    second.execution_config_hash = config_hash(second.execution_config)
    with pytest.raises(ValueError, match=reason):
        comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)


def test_missing_index_stays_missing_and_single_mode_still_works(runs):
    index = Path(runs[0].execution_config["fasta"] + ".fai")
    index.unlink()
    with pytest.raises(ValueError, match="existing FAI"):
        comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)
    assert not index.exists()
    record = runs[0]
    manifest = single_run_manifest(record, gene="TNFRSF1B", base_config={},
                                   workspace_root=record.root_path, url_for_file=str)
    assert manifest.study_id == record.id
    assert "comparison_id" not in manifest.as_dict()


def test_duplicate_unfinished_and_missing_gene_refuse(runs):
    for pair, reason in (((runs[0], runs[0]), "distinct"),
                         ((runs[0], replace(runs[1], status="running")), "completed native")):
        with pytest.raises(ValueError, match=reason):
            comparison_manifest(*pair, gene="TNFRSF1B", url_for_file=str)
    with pytest.raises(ValueError, match="no locus"):
        comparison_manifest(*runs, gene="ABSENT", url_for_file=str)


def test_selected_peak_missing_does_not_use_other_threshold(runs):
    for path in Path(runs[0].root_path).rglob("*peaks-merged-3.0-10.bed"):
        path.unlink()
    with pytest.raises(ValueError, match="missing raw_peak_polya"):
        comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)


def test_missing_one_strand_is_explicitly_unavailable(runs):
    path = next(Path(runs[0].root_path).rglob("*.minus.coverage.peaks-merged-3.0-10.bed"))
    path.unlink()
    with pytest.raises(ValueError, match="missing raw_peak_polya -"):
        comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)


def test_missing_configured_fasta_is_not_replaced_by_cache(runs):
    runs[0].execution_config = dict(runs[0].execution_config, fasta="/missing/configured.fa")
    runs[0].execution_config_hash = config_hash(runs[0].execution_config)
    with pytest.raises(ValueError, match="configured FASTA is missing"):
        comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)


def test_reference_urls_and_registration_use_same_resolved_files(runs, tmp_path):
    original = Path(runs[0].execution_config["fasta"])
    alias = tmp_path / "cached.fa"
    alias.symlink_to(original)
    Path(str(alias) + ".fai").symlink_to(str(original) + ".fai")
    runs[0].execution_config = dict(runs[0].execution_config, fasta=str(alias))
    runs[0].execution_config_hash = config_hash(runs[0].execution_config)
    manifest = comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)
    assert manifest.reference_fasta_url == manifest.run_sources[0]["reference_fasta"] == str(original.resolve())
    assert manifest.reference_index_url == str(Path(str(original) + ".fai").resolve())
    assert manifest.run_sources[0]["viewer_reference_path"] == str(alias)


def test_coordinate_mismatch_refuses(runs, monkeypatch):
    from scpolya_ui.services import igv_service
    original = igv_service.single_run_manifest

    def changed(record, **kwargs):
        manifest = original(record, **kwargs)
        return replace(manifest, locus=replace(manifest.locus, strand="-")) if record.id == runs[1].id else manifest

    monkeypatch.setattr(igv_service, "single_run_manifest", changed)
    with pytest.raises(ValueError, match="coordinates or strand differ"):
        comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)


def test_saved_study_manifest_keeps_study_identity(runs):
    study = StudyRecord(id="S-existing", name="Existing", workspace_id="W-0", workspace_root=runs[0].root_path,
                        status="draft", genes=["TNFRSF1B"], grid={}, variants=[], created_at="", updated_at="")
    manifest = study_manifest(study, gene="TNFRSF1B", base_config=runs[0].execution_config, url_for_file=str)
    assert manifest.study_id == study.id
    assert "comparison_id" not in manifest.as_dict()


def _rewrite_gtf(runs, text):
    Path(runs[0].config["gene_gtf"]).write_text(text)


def test_comparison_reports_missing_transcript_models(runs):
    # The fixture GTF has a gene row only: no transcript models to draw.
    manifest = comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)
    assert not any(t.role == "transcript_model" for t in manifest.tracks)
    assert len(manifest.missing_tracks) == 1
    assert manifest.missing_tracks[0].startswith("transcript models: no genes.gtf transcript overlaps chr1:")


def test_comparison_reports_incomplete_transcript_models(runs):
    gene = 'gene_id "g"; gene_name "TNFRSF1B"'
    _rewrite_gtf(runs, (
        f'chr1\ttest\tgene\t10\t40\t.\t+\t.\t{gene};\n'
        f'chr1\ttest\texon\t30\t40\t.\t+\t.\t{gene}; transcript_id "t2"; transcript_name "T-202";\n'
        f'chr1\ttest\ttranscript\t10\t40\t.\t+\t.\t{gene}; transcript_id "t1"; transcript_name "T-201";\n'
        f'chr1\ttest\texon\t10\t40\t.\t+\t.\t{gene}; transcript_id "t1"; transcript_name "T-201";\n'
        f'chr1\ttest\ttranscript\t30\t40\t.\t+\t.\t{gene}; transcript_id "t2"; transcript_name "T-202";\n'))
    manifest = comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)
    shared = [t for t in manifest.tracks if t.role == "transcript_model"]
    assert len(shared) == 1 and shared[0] is manifest.tracks[0]
    assert len(manifest.missing_tracks) == 1 and "1 genes.gtf rows" in manifest.missing_tracks[0]
    assert "are not drawn" in manifest.missing_tracks[0]


def test_comparison_shares_one_scoped_transcript_track(runs):
    gene = 'gene_id "g"; gene_name "TNFRSF1B"'
    _rewrite_gtf(runs, (
        f'chr1\ttest\tgene\t10\t40\t.\t+\t.\t{gene};\n'
        f'chr1\ttest\ttranscript\t10\t40\t.\t+\t.\t{gene}; transcript_id "t1"; transcript_name "T-201";\n'
        f'chr1\ttest\texon\t10\t40\t.\t+\t.\t{gene}; transcript_id "t1"; transcript_name "T-201";\n'))
    manifest = comparison_manifest(*runs, gene="TNFRSF1B", url_for_file=str)
    assert manifest.missing_tracks == ()
    shared = [t for t in manifest.tracks if t.role == "transcript_model"]
    assert len(shared) == 1 and " | " not in shared[0].name
    options = discovery.manifest_as_igv_options(manifest)
    track = next(t for t in options["tracks"] if t["name"] == shared[0].name)
    window = discovery.coordinate_locus(manifest.locus.igv_locus)
    assert track["scpolyaScope"] == {"chr": "chr1", "start": window.start, "end": window.end}
    assert shared[0].name.endswith(f" · chr1:{window.start:,}-{window.end:,}")
