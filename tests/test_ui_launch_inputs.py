"""Run Setup refuses launches the engine would stop before running any task, and says why."""
from pathlib import Path

import pytest

from scpolya_ui.registry import WorkspaceRecord
from scpolya_ui.services.workspace_service import RunDestination, launch_input_problems, run_destination
from test_ui_relocated_paths import _invocation


def _inputs(tmp_path, source):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "genes.gtf").write_text("")
    (data / "chrom.sizes").write_text("chr1\t100\n")
    table = data / "inputs.csv"
    table.write_text(f"sample_id,source_path,is_deduped,requires_split\nS1,{source},0,1\n")
    return data


def _config(tmp_path, data, **values):
    config = {"output_dir": str(tmp_path / "runs"), "run_id": "r1", "species": "human", "umi_tag": "UB",
              "inputs": str(data / "inputs.csv"), "gene_gtf": str(data / "genes.gtf"),
              "chrom_sizes": str(data / "chrom.sizes"), "contigs": ["chr1"]}
    config.update(values)
    return config


def _kinds(problems):
    return [(problem.kind, problem.key) for problem in problems]


def test_inputs_that_exist_on_this_computer_raise_nothing(tmp_path):
    bam = tmp_path / "local.bam"
    bam.write_bytes(b"x")
    link = tmp_path / "alias.bam"
    link.symlink_to(bam)
    data = _inputs(tmp_path, link)
    assert launch_input_problems(_config(tmp_path, data), None) == ()


def test_missing_required_inputs_and_unresolvable_source_bams_are_listed(tmp_path):
    data = _inputs(tmp_path, "/data/elsewhere/sample.bam")
    config = _config(tmp_path, data, chrom_sizes="/data/elsewhere/chrom.sizes", gene_gtf="/data/elsewhere/genes.gtf")
    assert _kinds(launch_input_problems(config, None)) == [
        ("missing_input", "gene_gtf"),
        ("missing_input", "chrom_sizes"),
        ("missing_source", "S1"),
    ]
    # A dangling alias does not resolve either, as register_inputs requires.
    dangling = tmp_path / "dangling.bam"
    dangling.symlink_to(tmp_path / "gone.bam")
    data = _inputs(tmp_path, dangling)
    assert _kinds(launch_input_problems(_config(tmp_path, data), None)) == [("missing_source", "S1")]


def test_optional_paths_are_not_judged(tmp_path):
    bam = tmp_path / "local.bam"
    bam.write_bytes(b"x")
    data = _inputs(tmp_path, bam)
    config = _config(tmp_path, data, bulk_window_gtf="/data/elsewhere/bulk.gtf", reference_fasta="/data/x.fa")
    assert launch_input_problems(config, None) == ()


def _engine_state(root, config):
    from task_manage import Plan, TaskManager

    manager = TaskManager(state_dir=root / "state", config=config)
    manager.load_or_init(Plan(samples=[], contigs_by_sample={}))
    return manager


def test_immutable_change_is_refused_as_the_engine_refuses_it(tmp_path):
    from task_manage import Plan, TaskManager

    bam = tmp_path / "local.bam"
    bam.write_bytes(b"x")
    data = _inputs(tmp_path, bam)
    stored = _config(tmp_path, data)
    root = Path(stored["output_dir"]) / stored["run_id"]
    _engine_state(root, stored)
    destination = RunDestination(target=str(root), exists=True)

    # Same structural settings (only a parameter changes): neither the GUI nor the engine objects.
    same = dict(stored, unscaled_depth_threshold_polyA=1)
    assert launch_input_problems(same, destination) == ()
    TaskManager(state_dir=root / "state", config=same).load_or_init(Plan(samples=[], contigs_by_sample={}))

    # An identical chrom.sizes file at another path still changes the structural identity.
    other = tmp_path / "copy" / "chrom.sizes"
    other.parent.mkdir()
    other.write_text((data / "chrom.sizes").read_text())
    changed = dict(stored, chrom_sizes=str(other))
    problems = launch_input_problems(changed, destination)
    assert _kinds(problems) == [("immutable_changed", "chrom_sizes")]
    assert problems[0].stored == str(data / "chrom.sizes") and problems[0].value == str(other)
    with pytest.raises(RuntimeError, match="Immutable"):
        TaskManager(state_dir=root / "state", config=changed).load_or_init(Plan(samples=[], contigs_by_sample={}))


def test_moved_run_needs_repair_or_its_dry_run(tmp_path):
    bam = tmp_path / "local.bam"
    bam.write_bytes(b"x")
    data = _inputs(tmp_path, bam)
    recorded = Path("/data/disk04/readiness/runs/r1")
    config = _config(tmp_path, data)
    local = Path(config["output_dir"]) / "r1"
    _invocation(local, recorded)
    workspace = WorkspaceRecord("W", "copy", str(local), str(local / "state"), str(local / "state/state.sqlite"))
    destination = run_destination(config, workspace)
    assert destination.moved_history and not destination.blocked
    assert _kinds(launch_input_problems(config, destination)) == [("moved_without_repair", "repair_moved_run")]
    assert launch_input_problems(dict(config, repair_moved_run=True), destination) == ()
    assert launch_input_problems(dict(config, repair_moved_run_dry_run=True), destination) == ()


def test_a_new_run_folder_is_only_checked_for_inputs(tmp_path):
    bam = tmp_path / "local.bam"
    bam.write_bytes(b"x")
    data = _inputs(tmp_path, bam)
    config = _config(tmp_path, data)
    destination = run_destination(config, None)
    assert not destination.exists
    assert launch_input_problems(config, destination) == ()


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_every_problem_message_formats_with_its_fields(locale):
    from scpolya_ui.i18n.catalog import translate
    from scpolya_ui.state.app_state import ProductAppState

    for kind in ("missing_input", "missing_source", "immutable_changed", "moved_without_repair"):
        key = f"run_setup.launch_problem.{kind}"
        text = translate(locale, key, setting="chrom_sizes", value="/new/chrom.sizes", stored="/old/chrom.sizes")
        assert text != key and "{" not in text
    # The page passes the fields by keyword to the app state's t(); none of them may collide with its parameters.
    import inspect
    assert not {"setting", "value", "stored"} & set(inspect.signature(ProductAppState.t).parameters)
