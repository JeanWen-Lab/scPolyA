import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scpolya_ui.command_builder import build_run_command
from scpolya_ui.jobs import JobRegistry
from scpolya_ui.registry import WorkspaceRecord
from scpolya_ui.run_records import RunRecord, RunRecordRegistry, config_hash, load_run_records, create_run_record
from scpolya_ui.services import job_service


def source(tmp_path, record_id="R-correct"):
    config = {"output_dir": str(tmp_path), "run_id": "source", "species": "human", "inputs": str(tmp_path / "inputs.csv"),
              "gene_gtf": str(tmp_path / "genes.gtf"), "chrom_sizes": str(tmp_path / "chrom.sizes"), "minpolyA": 5, "merge_distance": 10,
              "coverage_scaling": "none", "unscaled_depth_threshold": 1, "unscaled_depth_threshold_polyA": 3}
    record = RunRecord(id=record_id, kind="manual_rerun", status="succeeded", workspace_id="W", workspace_root=str(tmp_path),
                       label="Actual selected run", config=config, config_hash=config_hash(config), created_at="", updated_at="",
                       root_path=str(tmp_path / "source"), invocation_id="correct-invocation", execution_config=config,
                       execution_config_hash=config_hash(config))
    return record


def test_selected_source_persisted_before_launch_and_not_workspace_default(tmp_path, monkeypatch):
    selected = source(tmp_path)
    wrong = replace(selected, id="R-url-dryrun", invocation_id="wrong-invocation")
    records = RunRecordRegistry(records=[wrong, selected])
    workspace = WorkspaceRecord("W", "W", str(tmp_path), str(tmp_path / "state"), str(tmp_path / "state/state.sqlite"))
    command = build_run_command(dict(selected.execution_config, run_id="target", minpolyA=5), python_executable=sys.executable,
                                main_path=Path(__file__).resolve().parents[1] / "main.py")
    home = tmp_path / "ui-home"
    def mock_launch(job, **kwargs):
        saved = load_run_records(home)
        target = next(r for r in saved.records if r.job_id == job.id)
        assert target.parent_run_id == selected.id
        assert target.source_run_binding["invocation_id"] == "correct-invocation"
        assert target.source_run_binding["root_path"] == selected.root_path
        assert target.source_run_binding["config"] == selected.execution_config
        job.status = "starting"
        return job
    monkeypatch.setattr(job_service, "launch_job", mock_launch)
    result = job_service.create_run_setup_job(JobRegistry(), records, workspace=workspace, command=command,
                                              base_config=selected.execution_config, base_run_record_id=selected.id,
                                              launch=True, home=home)
    assert result.status == "starting"


def test_base_source_config_change_or_wrong_workspace_refuses(tmp_path):
    selected = source(tmp_path)
    records = RunRecordRegistry(records=[selected])
    workspace = WorkspaceRecord("W", "W", str(tmp_path), str(tmp_path / "state"), str(tmp_path / "state/state.sqlite"))
    command = build_run_command(selected.execution_config, python_executable=sys.executable,
                                main_path=Path(__file__).resolve().parents[1] / "main.py")
    with pytest.raises(ValueError, match="config changed"):
        job_service.create_run_setup_job(JobRegistry(), records, workspace=workspace, command=command,
                                         base_config=dict(selected.execution_config, minpolyA=10), base_run_record_id=selected.id,
                                         home=tmp_path / "ui-home")
    with pytest.raises(ValueError, match="does not belong"):
        job_service.create_run_setup_job(JobRegistry(), records, workspace=replace(workspace, id="OTHER"), command=command,
                                         base_config=selected.execution_config, base_run_record_id=selected.id, home=tmp_path / "ui-home")


def test_gui_created_sparse_source_is_compared_after_real_cli_defaults(tmp_path):
    workspace = WorkspaceRecord("W", "W", str(tmp_path), str(tmp_path / "state"), str(tmp_path / "state/state.sqlite"))
    records = RunRecordRegistry()
    command = build_run_command(source(tmp_path).config, python_executable=sys.executable,
                                main_path=Path(__file__).resolve().parents[1] / "main.py")
    selected = create_run_record(records, workspace=workspace, kind="manual_rerun", command=command,
                                 label="GUI-created source", status="succeeded")
    assert selected.config != selected.execution_config
    proposed = build_run_command(dict(selected.config, run_id="new-run"), python_executable=sys.executable,
                                 main_path=Path(__file__).resolve().parents[1] / "main.py")
    result = job_service.create_run_setup_job(JobRegistry(), records, workspace=workspace, command=proposed,
                                              base_config=dict(selected.config), base_run_record_id=selected.id,
                                              home=tmp_path / "ui-home")
    saved = next(r for r in load_run_records(tmp_path / "ui-home").records if r.id == result.run_record_id)
    assert saved.source_run_binding["config"] == selected.execution_config
