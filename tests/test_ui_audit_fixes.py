import pytest

from scpolya_ui.errors import UserError
from scpolya_ui.registry import WorkspaceRecord, WorkspaceRegistry
from scpolya_ui.services.config_service import parse_config_json
from scpolya_ui.services.workspace_service import add_workspace_mapping, remove_workspace_mapping
from scpolya_ui.sweep_builder import SweepRow, grid_from_sweep_rows


def test_sweep_refuses_the_same_parameter_twice():
    rows = [SweepRow("merge_distance", "0, 50"), SweepRow("merge_distance", "100")]
    with pytest.raises(UserError) as caught:
        grid_from_sweep_rows(rows)
    assert caught.value.key == "study.error.duplicate_key"


def test_sweep_checks_choices_ranges_and_finite_numbers():
    with pytest.raises(UserError) as caught:
        grid_from_sweep_rows([SweepRow("polyA_dynamic_threshold_stat", "median, bogus_stat")])
    assert caught.value.key == "study.error.bad_choice"
    with pytest.raises(UserError) as caught:
        grid_from_sweep_rows([SweepRow("polyA_dynamic_threshold_alpha", "1e999")])
    assert caught.value.key == "study.error.not_finite"
    with pytest.raises(UserError) as caught:
        grid_from_sweep_rows([SweepRow("misprimed_ratio_threshold", "0.5, 1.5")])
    assert caught.value.key == "study.error.above_maximum"
    assert grid_from_sweep_rows([SweepRow("polyA_dynamic_threshold_stat", "median, mean")]) == {
        "polyA_dynamic_threshold_stat": ["median", "mean"]
    }


def test_config_json_must_be_an_object():
    with pytest.raises(UserError) as caught:
        parse_config_json("[1, 2]")
    assert caught.value.key == "run_setup.json_not_object"


def _registry(tmp_path):
    record = WorkspaceRecord(
        id="W-1", alias="w", root_path=str(tmp_path), state_dir=str(tmp_path), db_path=str(tmp_path / "state.sqlite")
    )
    return WorkspaceRegistry(workspaces=[record]), record


def test_path_mapping_validation_and_removal(tmp_path):
    registry, record = _registry(tmp_path)
    home = tmp_path / "home"
    new = tmp_path / "moved"
    new.mkdir()
    for old, target, key in (
        ("relative/old", str(new), "workspace.mapping_error.relative"),
        (str(new), str(new), "workspace.mapping_error.same"),
        ("/old/run", str(tmp_path / "missing"), "workspace.mapping_error.missing"),
    ):
        with pytest.raises(UserError) as caught:
            add_workspace_mapping(registry, "W-1", old_path=old, new_path=target, home=home)
        assert caught.value.key == key
    add_workspace_mapping(registry, "W-1", old_path="/old/run", new_path=str(new), home=home)
    with pytest.raises(UserError) as caught:
        add_workspace_mapping(registry, "W-1", old_path="/old/run", new_path=str(new), home=home)
    assert caught.value.key == "workspace.mapping_error.duplicate"
    remove_workspace_mapping(registry, "W-1", old_path="/old/run", home=home)
    assert record.path_mappings == []


def test_coordinate_locus_parses_igv_style_ranges():
    from scpolya_ui.igv_manifest import coordinate_locus

    locus = coordinate_locus("chr5:142,898,115-142,911,754")
    assert (locus.chrom, locus.start, locus.end) == ("chr5", 142898115, 142911754)
    assert coordinate_locus("Actb") is None
    assert coordinate_locus("5:10-5") is None
