from __future__ import annotations

import csv
import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scpolya_ui.config_schema import CONFIG_FIELDS
from scpolya_ui.run_data import _invocation_config_hash
from scpolya_ui.run_records import RunRecord, config_hash
from scpolya_ui.services import run_evidence_service as service
from scpolya_ui.view_models.artifact_vm import artifact_state, final_primary_file_readiness
from task_manage import project_run_outputs_v1
from task_manage.artifact_naming import build_scoped_artifact_name_v1, register_artifact_name_v1


@pytest.fixture
def native(tmp_path):
    config = {f.key: f.default for f in CONFIG_FIELDS}
    config.update(output_dir=str(tmp_path), run_id="native", species="human", dedup_method="directional",
                  gene_gtf=str(tmp_path / "genes.gtf"), chrom_sizes=str(tmp_path / "chrom.sizes"),
                  quantification_mode="terminal-aware", summarize_matrix=True, psi_min_gene_umis=1,
                  minpolyA=5, nonA_allowed=1, minMAPQ=10, coverage_scaling="expr",
                  polyA_peak_threshold_mode="fixed", unscaled_depth_threshold_polyA=3.0,
                  exp_peak_threshold_mode="dynamic", exp_dynamic_threshold_stat="max_norm_fixed",
                  exp_dynamic_threshold_normalized=0.05, merge_distance_polyA=10, merge_distance=10)
    root = tmp_path / "native"
    projection = project_run_outputs_v1(config)
    assert not projection.warnings
    for scope in projection.scopes:
        for name in scope.artifact_names:
            record = build_scoped_artifact_name_v1(name.scope, name.payload, legacy_name=name.legacy_name)
            register_artifact_name_v1(root / "state/artifact_name_manifest.v1.json", mode="short", record=record)
    directory = Path(projection.by_scope_id["matrix_summary"].path)
    directory.mkdir(parents=True)
    table = directory / "S.terminal_aware_distal_site_usage.v1.tsv.gz"
    with gzip.open(table, "wt") as handle:
        handle.write("gene_id\tstrand\tcell_barcode\tdistal_site_usage\nG\t+\tA\t0\nG\t+\tB\tNA\nG2\t-\tA\t0.25\n")
    inv_id = "12345678123442348234123456789012"
    invocation = root / "logs/invocations/20261026T000000.000000Z-12345678/invocation.json"
    invocation.parent.mkdir(parents=True)
    payload = {"schema_version": "scpolya.logging.invocation.v2", "invocation_id": inv_id,
               "status": "succeeded", "started_at": "2026-10-26T00:00:00+00:00", "finished_at": "2026-10-26T00:00:01+00:00",
               "duration_seconds": 1, "pid": 123, "argv": ["fixture", "run"], "error": "", "paths": {},
               "run_root": str(root), "output_dir": str(tmp_path), "run_id": "native", "config_schema_version": 1,
               "resolved_config": config, "config_hash": _invocation_config_hash(config)}
    invocation.write_text(json.dumps(payload))
    record = RunRecord(id="R-test", kind="cli_invocation", status="succeeded", workspace_id="W", workspace_root=str(root),
                       label="test", config=config, config_hash=config_hash(config), created_at="", updated_at="", run_id="native",
                       root_path=str(root), invocation_id=inv_id, execution_config=config, execution_config_hash=config_hash(config))
    return record, table, invocation


def attempt(tmp_path, plan):
    target = tmp_path / "query"
    target.mkdir()
    service.emit(target / "preview.json", plan)
    return target


def test_real_projection_query_zero_na_and_export(native, tmp_path):
    record, source, _ = native
    before = source.read_bytes()
    tables, binding = service.discover(record)
    assert [(t.key, t.path) for t in tables] == [("distal", str(source))]
    target = attempt(tmp_path, service.preview(record, (), str(source), {"gene_id": "G"}))
    service.execute(target)
    result = service.read_result(target)
    assert result["matched_rows"] == 2 and result["scanned_rows"] == 3
    assert [row["distal_site_usage"] for row in result["rows"]] == ["0", "NA"]
    assert (target / "source.tsv.gz").read_bytes() == source.read_bytes() == before
    import zipfile
    with zipfile.ZipFile(target / "report.zip") as archive:
        assert set(archive.namelist()) == {"REPORT.txt", "preview.json", "result.json", "rows.tsv"}
        assert b"new_snapshot_of_current_retained_files" in archive.read("result.json")
    (target / "rows.tsv").write_text("changed")
    with pytest.raises(ValueError, match="Saved query files changed"):
        service.read_result(target)


def test_source_change_after_preview_refuses(native, tmp_path):
    record, source, _ = native
    plan = service.preview(record, (), str(source), {})
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="Source changed"):
        service.execute(attempt(tmp_path, plan))


def test_older_attempt_and_unavailable_filter_refuse(native):
    record, source, _ = native
    with pytest.raises(ValueError, match="older attempt"):
        service.discover(replace(record, invocation_id="different"))
    with pytest.raises(ValueError, match="lacks a requested filter"):
        service.preview(record, (), str(source), {"peak_name": "P"})


def test_new_invocation_after_preview_refuses(native, tmp_path):
    record, source, invocation = native
    plan = service.preview(record, (), str(source), {})
    payload = json.loads(invocation.read_text())
    payload.update(invocation_id="22345678123442348234123456789012", started_at="2026-10-26T01:00:00+00:00")
    newer = invocation.parent.parent / "20261026T010000.000000Z-22345678/invocation.json"
    newer.parent.mkdir()
    newer.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="newer execution"):
        service.execute(attempt(tmp_path, plan))


def test_truncation_counts_all_matches(native, tmp_path, monkeypatch):
    record, source, _ = native
    monkeypatch.setattr(service, "MAX_RESULT_ROWS", 1)
    target = attempt(tmp_path, service.preview(record, (), str(source), {}))
    service.execute(target)
    result = service.read_result(target)
    assert result["truncated"] and result["matched_rows"] == 3 and result["displayed_rows"] == 1


def test_native_tables_indexed_and_empty_audit_has_specific_semantics(native, tmp_path):
    record, source, _ = native
    artifacts = final_primary_file_readiness(record.execution_config).artifacts
    assert str(source) in {a.path for a in artifacts if a.role.startswith("native_table.")}
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert artifact_state(empty, role="final.not_evaluable_peaks_audit").status == "ready"
    assert artifact_state(empty, role="final.annotated_peaks_bed").status == "missing"


@pytest.mark.parametrize("key,header", [
    ("ipui_gene", "gene_id\tgene_name\tstrand\tquantification_unit_id\tretained_intronic_pas_count\tI_total\tU\ttotal_iPUI"),
    ("ipui_site", "gene_id\tgene_name\tintronic_pas_id\tchromosome\tcoordinate\tstrand\tquantification_unit_id\tI_i\tU\tiPUI_i\tmisprimed_ratio\tmispriming_pass"),
])
def test_actual_native_ipui_headers_have_unit_ids_not_cell_barcodes(native, key, header):
    record, table, _ = native
    suffix, _ = service.TABLES[key]
    path = table.parent / ("S." + suffix)
    with gzip.open(path, "wt") as handle:
        handle.write(header + "\n")
    sources, _ = service.discover(record)
    found = next(s for s in sources if s.key == key)
    assert "quantification_unit_id" in found.columns and "cell_barcode" not in found.columns
    service.preview(record, (), str(path), {"gene_id": "G"})
    with pytest.raises(ValueError, match="lacks a requested filter"):
        service.preview(record, (), str(path), {"cell_barcode": "A"})


def test_quote_characters_round_trip_as_literal_native_tsv(native, tmp_path):
    record, source, _ = native
    with gzip.open(source, "wt") as handle:
        handle.write('gene_id\tstrand\tcell_barcode\tdistal_site_usage\nG"literal"\t+\tA\t0\n')
    target = attempt(tmp_path, service.preview(record, (), str(source), {}))
    service.execute(target)
    assert (target / "rows.tsv").read_text() == 'gene_id\tstrand\tcell_barcode\tdistal_site_usage\nG"literal"\t+\tA\t0\n'
    with (target / "rows.tsv").open() as handle:
        rows = list(csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))
    assert rows == service.read_result(target)["rows"]


@pytest.mark.parametrize("name", ["rows.tsv", "report.zip", "source.tsv.gz"])
def test_each_download_is_verified_against_saved_bytes(native, tmp_path, name):
    record, source, _ = native
    target = attempt(tmp_path, service.preview(record, (), str(source), {}))
    service.execute(target)
    raw = service.download_bytes(target, name)
    assert raw == (target / name).read_bytes()
    (target / name).write_bytes(raw + b"changed")
    with pytest.raises(service.EvidenceError) as error:
        service.download_bytes(target, name)
    assert error.value.code == "result_changed"


def test_job_only_binding_cannot_borrow_a_later_execution(native):
    record, source, invocation = native
    payload = json.loads(invocation.read_text())
    payload["job_id"] = "J-new"
    invocation.write_text(json.dumps(payload))
    old = replace(record, invocation_id="", job_id="J-old")
    with pytest.raises(ValueError, match="not bound to the latest"):
        service.discover(old)
    new = replace(record, invocation_id="", job_id="J-new")
    sources, binding = service.discover(new)
    assert binding["run_record_id"] == record.id and binding["invocation_id"] == payload["invocation_id"]


def test_dense_matrix_requires_a_cell_filter_at_preview(native):
    record, source, _ = native
    path = source.parent / "S.terminal_aware_cell_gene.matrix.v2.tsv.gz"
    with gzip.open(path, "wt") as handle:
        handle.write("cell_barcode\tG|%2B\nA\t1\n")
    with pytest.raises(service.EvidenceError) as error:
        service.preview(record, (), str(path), {})
    assert error.value.code == "matrix_filter"
    assert service.preview(record, (), str(path), {"cell_barcode": "A"})["query"] == {"cell_barcode": "A"}
