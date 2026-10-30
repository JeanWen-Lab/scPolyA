"""Site-score discovery under each recorded terminal annotation schema."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scpolya_ui.config_schema import CONFIG_FIELDS
from scpolya_ui.services.site_score_service import (
    DEEPCPS_PROVENANCE_SCHEMA,
    annotation_format_refusal,
    discover_deepcps_results,
    empty_state,
    read_site_scores,
)
from task_manage.output_projection import project_run_outputs_v1
from task_manage.parameter_studies import final_peak_item_for_config
from task_manage.run_layout import RunLayout
from utils.terminal_quant_utils import quantification_artifact_suffix

V2, V3, V4 = "terminal-annotation-v2", "terminal-annotation-v3", "terminal-annotation-v4"
LOCALES = Path(__file__).resolve().parents[1] / "scpolya_ui" / "i18n" / "locales"
SITES_HEADER = ["chrom", "start", "end", "name", "site_key", "deepcps_cal", "score_reason"]


def _config(tmp_path, **extra):
    config = {f.key: f.default for f in CONFIG_FIELDS}
    config.update(output_dir=str(tmp_path), run_id="native", species="human", dedup_method="directional",
                  gene_gtf=str(tmp_path / "genes.gtf"), chrom_sizes=str(tmp_path / "chrom.sizes"),
                  quantification_mode="terminal-aware", summarize_matrix=True, psi_min_gene_umis=1,
                  minpolyA=5, nonA_allowed=1, minMAPQ=10, coverage_scaling="expr",
                  polyA_peak_threshold_mode="fixed", unscaled_depth_threshold_polyA=3.0,
                  exp_peak_threshold_mode="dynamic", exp_dynamic_threshold_stat="max_norm_fixed",
                  exp_dynamic_threshold_normalized=0.05, merge_distance_polyA=10, merge_distance=10, **extra)
    return config


def _annotated_bed(tmp_path, config, schema) -> Path:
    """Record one schema's annotation outputs and write its annotated BED, as a run would."""
    layout = RunLayout(out=tmp_path, run_id="native", artifact_naming_mode="short")
    item = final_peak_item_for_config(config)
    item["terminal_annotation_schema"] = schema
    layout.MatrixSummaryConfigSub(item, psi_min_gene_umis=1)  # registers peak/annotation records
    bed = layout.AnnotatedFinalOutputFileCandidates(
        suffix=quantification_artifact_suffix("___peaks-annotated.bed", "terminal-aware"), d=item)[0]
    bed.parent.mkdir(parents=True, exist_ok=True)
    bed.write_text("chr1\t100\t200\tA_peak_1\t1\t+\n", encoding="utf-8")
    return bed


def _score_result(bed: Path, *, tamper: bool = False) -> Path:
    directory = bed.parent / "deepcps" / ("b" * 64)
    directory.mkdir(parents=True)
    sites = directory / "sites.deepcps.tsv"
    sites.write_text("\t".join(SITES_HEADER) + "\nchr1\t100\t200\tA_peak_1\tk\t0.9\tscored\n", encoding="utf-8")
    scored = directory / "scored.tsv"
    scored.write_text("site_key\nk\n", encoding="utf-8")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    provenance = {
        "schema": DEEPCPS_PROVENANCE_SCHEMA, "fingerprint": "b" * 64, "n_sites": 1, "n_scored": 1,
        "reasons": {"scored": 1},
        "request": {"bed": {"path": str(bed), "sha256": sha(bed)}, "threads": 2, "batch_size": 128,
                    "model": {"manifest": {"model_id": "m", "status": "supplied-unconfirmed"}}},
        "outputs": {name: {"sha256": sha(path), "size": path.stat().st_size}
                    for name, path in (("sites.deepcps.tsv", sites), ("scored.tsv", scored))},
    }
    (directory / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    if tamper:
        with sites.open("a", encoding="utf-8") as handle:
            handle.write("chr1\t300\t400\tB_peak_2\tk2\t0.1\tscored\n")
    return directory


def test_v2_run_scores_are_found_while_other_readers_still_refuse(tmp_path):
    config = _config(tmp_path)
    bed = _annotated_bed(tmp_path, config, V2)
    directory = _score_result(bed)
    (result,) = discover_deepcps_results(config)
    assert result.directory == str(directory)
    assert result.readable and result.bed_matches is True and result.n_scored == 1
    assert read_site_scores(result).total == 1
    assert annotation_format_refusal(config) == "" and empty_state(config) == "off"
    # The shared gate is unchanged: the projection still omits every annotation-scoped output.
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert any(V2 in w for w in projection.warnings)
    assert "final.annotated_peaks_bed" not in projection.by_scope_id
    assert "matrix_summary" not in projection.by_scope_id


@pytest.mark.parametrize("schema", [V3, V4])
def test_v3_and_v4_discovery_is_unchanged(tmp_path, schema):
    config = _config(tmp_path)
    bed = _annotated_bed(tmp_path, config, schema)
    directory = _score_result(bed)
    projection = project_run_outputs_v1(dict(config), annotation_schema="recorded")
    assert not projection.warnings
    assert Path(projection.by_scope_id["final.annotated_peaks_bed"].path) == bed
    (result,) = discover_deepcps_results(config)
    assert result.directory == str(directory) and result.bed_matches is True
    assert annotation_format_refusal(config) == ""


@pytest.mark.parametrize("schemas", [("terminal-annotation-v9",), (V2, V4)])
def test_unknown_or_mixed_annotation_format_shows_unsupported(tmp_path, schemas):
    config = _config(tmp_path)
    for schema in schemas:
        _score_result(_annotated_bed(tmp_path, config, schema))
    assert discover_deepcps_results(config) == ()
    assert annotation_format_refusal(config)
    assert empty_state(config) == "unsupported"
    assert empty_state(dict(config, deepcps=True)) == "unsupported"


def test_scored_off_and_missing_states_are_unchanged(tmp_path):
    assert empty_state(_config(tmp_path)) == "off"
    assert empty_state(_config(tmp_path, deepcps=True)) == "missing"


def test_v2_digest_mismatch_is_still_refused(tmp_path):
    config = _config(tmp_path)
    _score_result(_annotated_bed(tmp_path, config, V2), tamper=True)
    (result,) = discover_deepcps_results(config)
    assert result.integrity == "mismatch"
    with pytest.raises(ValueError):
        read_site_scores(result)


def test_v2_changed_bed_is_flagged(tmp_path):
    config = _config(tmp_path)
    bed = _annotated_bed(tmp_path, config, V2)
    _score_result(bed)
    bed.write_text("chr1\t100\t201\tA_peak_1\t1\t+\n", encoding="utf-8")
    (result,) = discover_deepcps_results(config)
    assert result.readable and result.bed_matches is False


def test_unsupported_message_exists_in_both_locales():
    for locale in ("en", "zh-CN"):
        messages = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
        assert messages["site_scores.unsupported_title"] and messages["site_scores.unsupported_detail"]
        assert messages["site_scores.unsupported_title"] != messages["site_scores.off_title"]
