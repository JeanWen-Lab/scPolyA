"""Run Setup destination for moved or copied runs: the notice, the preview and the launch use one target."""
import json
import shlex
from pathlib import Path

from scpolya_ui.registry import PathMapping, WorkspaceRecord
from scpolya_ui.services.config_service import build_command_for_config
from scpolya_ui.services.workspace_service import moved_run_roots, run_destination
from test_ui_relocated_paths import _invocation

REPO = Path(__file__).resolve().parents[1]


def _mirrored(tmp_path):
    """A copy that keeps the recorded absolute path under a new prefix (as an rsync of /data/... would)."""
    recorded = Path("/data/disk04/readiness/runs/full/pbmc-run")
    local = tmp_path / "otherhost" / recorded.relative_to("/")
    _invocation(local, recorded)
    workspace = WorkspaceRecord("W", "copy", str(local), str(local / "state"), str(local / "state/state.sqlite"))
    return recorded, local, workspace


def test_mirrored_copy_suggests_the_run_root_not_a_root_mapping(tmp_path):
    recorded, local, workspace = _mirrored(tmp_path)
    (suggestion,) = moved_run_roots(workspace)
    assert suggestion.old_prefix == str(recorded) and suggestion.new_prefix == str(local.resolve())
    assert suggestion.old_prefix != "/"


def test_moved_folder_still_maps_at_the_deepest_differing_folders(tmp_path):
    recorded = Path("/historical/project/runs/pbmc-run")
    local = tmp_path / "moved" / "runs" / "pbmc-run"
    _invocation(local, recorded)
    workspace = WorkspaceRecord("W", "moved", str(local), str(local / "state"), str(local / "state/state.sqlite"))
    (suggestion,) = moved_run_roots(workspace)
    assert suggestion.old_prefix == "/historical/project" and suggestion.new_prefix == str((tmp_path / "moved").resolve())


def _config(output_dir, run_id="pbmc-run"):
    return {"output_dir": str(output_dir), "run_id": run_id, "species": "human", "inputs": "/data/inputs.csv",
            "gene_gtf": "/data/genes.gtf", "chrom_sizes": "/data/chrom.sizes", "merge_distance": 10}


def test_recorded_location_of_a_mapped_copy_is_blocked_with_an_explicit_local_choice(tmp_path):
    recorded, local, workspace = _mirrored(tmp_path)
    workspace.path_mappings = [PathMapping(str(recorded.parent.parent.parent), str(local.parent.parent.parent))]
    destination = run_destination(_config(recorded.parent), workspace)
    assert destination.target == str(recorded)
    assert destination.blocked and destination.mapped_view == str(local)
    assert destination.suggested_output_dir == str(local.parent)

    chosen = run_destination(_config(destination.suggested_output_dir), workspace)
    assert not chosen.blocked and chosen.exists and chosen.target == str(local)
    # The local copy's history was recorded at the old root, so the engine's moved-run checks apply.
    assert chosen.moved_history


def test_destination_matches_the_command_the_launch_would_run(tmp_path):
    recorded, local, workspace = _mirrored(tmp_path)
    workspace.path_mappings = [PathMapping(str(recorded), str(local))]
    for output_dir in (recorded.parent, local.parent, tmp_path / "fresh"):
        config = _config(output_dir)
        destination = run_destination(config, workspace)
        argv = build_command_for_config(config, main_path=REPO / "main.py").argv
        launched = Path(argv[argv.index("--output-dir") + 1]) / argv[argv.index("--run-id") + 1]
        assert str(launched) == destination.target


def test_unmapped_new_folder_is_not_blocked(tmp_path):
    destination = run_destination(_config(tmp_path / "new"), None)
    assert destination is not None and not destination.blocked and not destination.exists
    assert run_destination({"run_id": "x"}, None) is None


def test_comparison_base_follows_an_explicit_local_choice_only(tmp_path):
    from scpolya_ui.services.workspace_service import comparison_base_config

    recorded, local, workspace = _mirrored(tmp_path)
    workspace.path_mappings = [PathMapping(str(recorded.parent.parent.parent), str(local.parent.parent.parent))]
    base = _config(recorded.parent)
    # Proposal still at the recorded folder: compared as recorded (and the launch is blocked elsewhere).
    assert comparison_base_config(base, dict(base), workspace) == base
    # Explicit local choice: the base run is compared at its local location; other paths stay as recorded.
    proposed = {**base, "output_dir": str(local.parent), "unscaled_depth_threshold_polyA": 1.0}
    compared = comparison_base_config(base, proposed, workspace)
    assert compared["output_dir"] == str(local.parent) and compared["inputs"] == base["inputs"]
    assert base["output_dir"] == str(recorded.parent)  # stored base left untouched
    # Any other folder is a genuinely new location.
    other = {**base, "output_dir": str(tmp_path / "elsewhere")}
    assert comparison_base_config(base, other, workspace)["output_dir"] == str(recorded.parent)
