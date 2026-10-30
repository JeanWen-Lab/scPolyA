from pathlib import Path

from scpolya_ui.registry import WorkspaceRecord
from scpolya_ui.services.workspace_service import untracked_outputs


def _record(root: Path) -> WorkspaceRecord:
    return WorkspaceRecord(
        id="W-1",
        alias="w",
        root_path=str(root),
        state_dir=str(root / "state"),
        db_path=str(root / "state" / "state.sqlite"),
    )


def test_empty_folder_reports_nothing(tmp_path):
    assert untracked_outputs(_record(tmp_path)) is None


def test_outputs_without_invocations_are_described(tmp_path):
    (tmp_path / "logs" / "sub").mkdir(parents=True)
    (tmp_path / "logs" / "main.log").write_text("x")
    (tmp_path / "logs" / "sub" / "a.log").write_text("x")
    (tmp_path / "final_output").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "state.sqlite").write_bytes(b"")
    found = untracked_outputs(_record(tmp_path))
    assert found is not None
    assert found.log_files == 2
    assert found.output_dirs == ("final_output",)
    assert found.has_state_db


def test_invocation_history_means_runs_are_discoverable(tmp_path):
    (tmp_path / "logs" / "invocations").mkdir(parents=True)
    (tmp_path / "final_output").mkdir()
    assert untracked_outputs(_record(tmp_path)) is None


def _write_invocation(root: Path, run_root: str, name: str = "inv-1") -> None:
    import json

    folder = root / "logs" / "invocations" / name
    folder.mkdir(parents=True)
    (folder / "invocation.json").write_text(json.dumps({"run_root": run_root}))


def test_moved_run_root_suggests_mapping_at_deepest_differing_folder(tmp_path):
    from scpolya_ui.services.workspace_service import moved_run_roots

    root = tmp_path / "new" / "outputs" / "run-a"
    root.mkdir(parents=True)
    _write_invocation(root, "/old/place/outputs/run-a")
    moved = moved_run_roots(_record(root))
    assert len(moved) == 1
    assert moved[0].invocation_count == 1
    assert moved[0].old_prefix == "/old/place"
    assert moved[0].new_prefix == str(tmp_path / "new")


def test_matching_or_mapped_run_root_is_not_reported(tmp_path):
    from scpolya_ui.registry import PathMapping
    from scpolya_ui.services.workspace_service import moved_run_roots

    root = tmp_path / "run-b"
    root.mkdir()
    _write_invocation(root, str(root), "inv-1")
    assert moved_run_roots(_record(root)) == []
    _write_invocation(root, "/old/run-b", "inv-2")
    record = _record(root)
    record.path_mappings.append(PathMapping(old_path="/old", new_path=str(tmp_path), note="", created_at=""))
    assert moved_run_roots(record) == []
