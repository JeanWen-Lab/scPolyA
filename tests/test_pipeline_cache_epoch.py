"""Pipeline cache semantic epoch: current coordinates carry it, history never does.

Earlier-generation run roots are inspected read-only and rejected before any
write. Current-epoch roots resume normally.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as pipeline_main
from task_manage import Plan, TaskManager, preview_rerun_from_state, run_flex_batch
from task_manage.cache_epoch import (
    PIPELINE_CACHE_EPOCH,
    PIPELINE_CACHE_EPOCH_KEY,
    CacheEpochCompatibilityError,
    classify_cache_epoch,
)
from task_manage.cache_epoch_status import (
    PIPELINE_CACHE_EPOCH_COMPLETION_KEY,
    PIPELINE_CACHE_EPOCH_ROOT_KEY,
    CacheEpochStateUnreadableError,
    build_cache_epoch_completion_marker,
    build_cache_epoch_root_marker,
    inspect_run_root_cache_epoch,
    require_compatible_run_root,
    require_safe_state_paths,
)
from task_manage.contract_generation_candidate import build_active_contract_generation_candidate_graph
from task_manage.task_completion import TaskCompletionLoadError
from task_manage.instance_graph import build_dynamic_instance_graph
from task_manage.reuse_report import load_current_reuse_decision_summaries_v2
from task_manage.contract_generation_candidate import cache_epoch_superseded_decision_ids
from task_manage.decision_input_report import build_candidate_decision_input_status_report
from task_manage.pipeline_contracts import (
    CurrentTaskInstanceContractV2,
    TaskFieldValue,
    build_current_task_instance_contract_from_keys_v2,
)
from task_manage.task_coordinate import (
    build_task_coordinate_v1,
    build_task_coordinate_v1_from_contract,
    build_task_coordinate_v1_from_legacy_done,
    decode_task_coordinate_json_v1,
)
from task_manage.task_coordinate_store import write_task_coordinate_done_v2
from task_manage.run_layout import RunLayout
from task_manage.task_specs import task_specs_by_key

STAGE, TASK = "extract_subsets", "extract_polyA"
ITEM = {"uid": "S.chr1", "dedup_method": "directional", "polyA_status": "polyA-10-1-0",
        "minpolyA": 10, "minMAPQ": 0}

MANAGED_TOP_LEVELS = (
    "inputs",
    "input_tables",
    "unit_outputs",
    "merged_cohort",
    "cohort_output",
    "final_output",
    "annotated_bams_temp",
    "annotated_bams",
    "peak_sidecars",
    "count_matrix",
    "count_matrix_temp",
    "matrix_summary",
    "rna_gene_matrix",
    "rna_gene_matrix_temp",
    "reference",
    "samples",
    "logs",
    "backup",
)

UNTRACKED_RUN_ROOT_FILES = tuple(f"{name}/payload.dat" for name in MANAGED_TOP_LEVELS)


def _current_contract(item=ITEM):
    spec = task_specs_by_key()[(STAGE, TASK)]
    return build_current_task_instance_contract_from_keys_v2(
        stage=STAGE, task_base=TASK, item=item,
        identity_keys=spec.identity_keys, param_keys=spec.param_keys, level=spec.level,
    )


def _current_coordinate(item=ITEM):
    return build_task_coordinate_v1_from_contract(_current_contract(item))


def _old_epoch_coordinate(item=ITEM, extra_params=()):
    spec = task_specs_by_key()[(STAGE, TASK)]
    return build_task_coordinate_v1(
        stage=STAGE, task_base=TASK,
        identity={k: item[k] for k in spec.identity_keys},
        params=[*((k, item[k]) for k in spec.param_keys), *extra_params],
    )


def _write_historical_completion(tm, coordinate):
    with tm.store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        write_task_coordinate_done_v2(
            con,
            coordinate,
            done_at="2026-10-14T00:00:00+00:00",
            meta_json=None,
            source="migration",
        )
        con.execute("COMMIT")


# --- current contracts: exactly one current epoch (type invariant) -----------------

def test_current_coordinate_carries_epoch_exactly_once():
    coordinate = _current_coordinate()
    assert coordinate.params_dict[PIPELINE_CACHE_EPOCH_KEY] == PIPELINE_CACHE_EPOCH
    assert [f.key for f in coordinate.params].count(PIPELINE_CACHE_EPOCH_KEY) == 1
    old = _old_epoch_coordinate()
    assert coordinate.identity_dict == old.identity_dict
    assert {k: v for k, v in coordinate.params_dict.items() if k != PIPELINE_CACHE_EPOCH_KEY} == old.params_dict
    assert coordinate.task_coordinate_id != old.task_coordinate_id


@pytest.mark.parametrize(
    "stage,task,item",
    [
        (
            "preprocess_units",
            "dedup_units",
            {
                "sid": "S",
                "ctg": "chr1",
                "dedup_method": "directional",
                "extra_key": "source=raw_region;cell_split=none",
            },
        ),
        (
            "preprocess_units",
            "rewrite_barcode_suffix",
            {
                "sid": "S",
                "ctg": "chr1",
                "dedup_status": "directional",
                "extra_key": "source=dedup;barcode_suffix=-1",
            },
        ),
        (
            "summarize_matrix",
            "summarize_matrix",
            {
                "sid": "S",
                "dedup_method": "directional",
                "extra_key": "summary-contract",
            },
        ),
    ],
)
def test_epoch_changes_representative_dedup_and_downstream_coordinates(stage, task, item):
    spec = task_specs_by_key()[(stage, task)]
    current = build_task_coordinate_v1_from_contract(
        build_current_task_instance_contract_from_keys_v2(
            stage=stage,
            task_base=task,
            item=item,
            identity_keys=spec.identity_keys,
            param_keys=spec.param_keys,
            level=spec.level,
        )
    )
    historical = build_task_coordinate_v1(
        stage=stage,
        task_base=task,
        identity={key: item[key] for key in spec.identity_keys},
        params={key: item[key] for key in spec.param_keys},
    )
    assert current.params_dict[PIPELINE_CACHE_EPOCH_KEY] == "5"
    assert PIPELINE_CACHE_EPOCH_KEY not in historical.params_dict
    assert current.task_coordinate_id != historical.task_coordinate_id


def test_epoch_cannot_be_declared_as_task_parameter():
    with pytest.raises(ValueError, match=PIPELINE_CACHE_EPOCH_KEY):
        build_current_task_instance_contract_from_keys_v2(
            stage=STAGE, task_base=TASK, item={**ITEM, PIPELINE_CACHE_EPOCH_KEY: "1"},
            identity_keys=("uid",), param_keys=("minpolyA", PIPELINE_CACHE_EPOCH_KEY),
        )


def _raw_contract(params, identity=(TaskFieldValue("uid", "S.chr1"),)):
    return CurrentTaskInstanceContractV2(
        stage=STAGE, task_base=TASK, level=None, identity=tuple(identity), task_params=tuple(params),
    )


@pytest.mark.parametrize(
    "params,match",
    [
        ((TaskFieldValue("minpolyA", 10),), "exactly one"),                                   # missing
        ((TaskFieldValue("minpolyA", 10), TaskFieldValue(PIPELINE_CACHE_EPOCH_KEY, "2")), "contracts require"),  # old
        ((TaskFieldValue("minpolyA", 10), TaskFieldValue(PIPELINE_CACHE_EPOCH_KEY, "6")), "contracts require"),  # future
        ((TaskFieldValue("minpolyA", 10), TaskFieldValue(PIPELINE_CACHE_EPOCH_KEY, 2)), "contracts require"),    # wrong type
        ((TaskFieldValue(PIPELINE_CACHE_EPOCH_KEY, PIPELINE_CACHE_EPOCH),
          TaskFieldValue(PIPELINE_CACHE_EPOCH_KEY, PIPELINE_CACHE_EPOCH)), "unique|exactly one"),       # duplicate
    ],
)
def test_direct_current_contract_without_exactly_one_current_epoch_fails_closed(params, match):
    with pytest.raises(ValueError, match=match):
        _raw_contract(params)


def test_epoch_as_identity_field_is_rejected():
    with pytest.raises(ValueError, match="identity"):
        _raw_contract(
            (TaskFieldValue(PIPELINE_CACHE_EPOCH_KEY, PIPELINE_CACHE_EPOCH),),
            identity=(TaskFieldValue("uid", "S.chr1"), TaskFieldValue(PIPELINE_CACHE_EPOCH_KEY, "2")),
        )


def test_direct_current_contract_with_current_epoch_is_accepted():
    contract = _raw_contract((TaskFieldValue(PIPELINE_CACHE_EPOCH_KEY, PIPELINE_CACHE_EPOCH),))
    assert build_task_coordinate_v1_from_contract(contract).params_dict == {
        PIPELINE_CACHE_EPOCH_KEY: PIPELINE_CACHE_EPOCH
    }


def test_current_coordinate_conversion_rejects_epochless_lookalikes():
    lookalike = SimpleNamespace(stage=STAGE, task_base=TASK, identity=(TaskFieldValue("uid", "S.chr1"),),
                                task_params=(TaskFieldValue("minpolyA", 10),))
    with pytest.raises(ValueError, match="exactly one"):
        build_task_coordinate_v1_from_contract(lookalike)


# --- history stays frozen -----------------------------------------------------------

def test_historical_decode_never_injects_epoch():
    old = _old_epoch_coordinate()
    decoded = decode_task_coordinate_json_v1(old.canonical_json)
    assert decoded == old and PIPELINE_CACHE_EPOCH_KEY not in decoded.params_dict
    legacy = build_task_coordinate_v1_from_legacy_done(
        stage=STAGE, task_base=TASK, variant_str=old.legacy_variant_str,
        identity_keys=list(old.identity_dict), identity_values=list(old.identity_dict.values()),
    )
    assert legacy == old and PIPELINE_CACHE_EPOCH_KEY not in legacy.params_dict


@pytest.mark.parametrize("legacy_epoch", [None, "2", "3", "4"])
def test_old_epoch_done_cannot_match_current_and_root_is_blocked_without_write(
    tmp_path, legacy_epoch
):
    tm = TaskManager(state_dir=tmp_path / "state", config={"run_id": "epoch"})
    tm.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    extra = () if legacy_epoch is None else ((PIPELINE_CACHE_EPOCH_KEY, legacy_epoch),)
    old = _old_epoch_coordinate(extra_params=extra)
    _write_historical_completion(tm, old)
    assert tm.check_task_done(old)
    assert not tm.check_task_done(_current_coordinate())
    before = _logical_and_meta(tm.db_path)
    with pytest.raises(CacheEpochCompatibilityError, match="new run root"):
        TaskManager(state_dir=tm.state_dir, config={"run_id": "epoch"}).load_or_init(
            Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]})
        )
    assert _logical_and_meta(tm.db_path) == before


def test_epoch_three_done_and_nonempty_products_block_before_entry_writes(tmp_path, monkeypatch):
    layout = RunLayout(out=tmp_path, run_id="epoch-three")
    old = _old_epoch_coordinate(extra_params=((PIPELINE_CACHE_EPOCH_KEY, "3"),))
    tm = _seeded_root(layout.Root, [old])
    tm.store.meta_set(PIPELINE_CACHE_EPOCH_ROOT_KEY, json.dumps(
        {"schema": "scpolya.pipeline_cache_epoch_root.v1", "epoch": "3"}))
    output = layout.Root / "count_matrix" / "sample.counts.tsv.gz"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"nonempty historical scientific product")
    before_db = tm.db_path.read_bytes()
    before_output = output.read_bytes()
    status = inspect_run_root_cache_epoch(layout.State)
    assert status.blocks_run and status.done_counts["predecessor"] == 1
    assert not tm.check_task_done(_current_coordinate())
    monkeypatch.setattr(pipeline_main, "setup_run_logging", lambda **kw: pytest.fail("logging dispatched"))
    args = SimpleNamespace(verbose=False, output_dir=layout.out, run_id=layout.run_id)
    with pytest.raises(SystemExit, match="new run root"):
        pipeline_main.run_mode_initialization(args)
    assert tm.db_path.read_bytes() == before_db
    assert output.read_bytes() == before_output
    assert not layout.Logs.exists()


def test_runtime_completion_write_rejects_epochless_coordinate(tmp_path):
    tm = TaskManager(state_dir=tmp_path / "state", config={"run_id": "epoch"})
    tm.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    with pytest.raises(ValueError, match="exactly one"):
        tm.mark_task_done(_old_epoch_coordinate())
    assert not tm.check_task_done(_old_epoch_coordinate())


# --- epoch classification and candidate-graph exclusion -----------------------------

def test_epoch_classification_accepts_only_known_generations():
    assert classify_cache_epoch([("minpolyA", 10)]) == "predecessor"           # absent = implicit 1
    assert classify_cache_epoch([(PIPELINE_CACHE_EPOCH_KEY, PIPELINE_CACHE_EPOCH)]) == "current"
    assert classify_cache_epoch([{"key": PIPELINE_CACHE_EPOCH_KEY, "legacy_value": "2"}]) == "predecessor"
    assert classify_cache_epoch([(PIPELINE_CACHE_EPOCH_KEY, "3")]) == "predecessor"
    assert classify_cache_epoch([(PIPELINE_CACHE_EPOCH_KEY, "4")]) == "predecessor"
    for bad in ("6", "1", "", 2):
        assert classify_cache_epoch([(PIPELINE_CACHE_EPOCH_KEY, bad)]) == "unknown"
    assert classify_cache_epoch([(PIPELINE_CACHE_EPOCH_KEY, "3"), (PIPELINE_CACHE_EPOCH_KEY, "3")]) == "unknown"


def _decision(decision_id, epoch, minpolyA=10, uid="S.chr1", extra=()):
    params = [("minMAPQ", 0), ("minpolyA", minpolyA), *extra]
    if epoch is not None:
        params.append((PIPELINE_CACHE_EPOCH_KEY, epoch))
    return SimpleNamespace(decision_id=decision_id, stage=STAGE, task_base=TASK,
                           identity=(("uid", uid), ("dedup_method", "directional")),
                           params=tuple(params))


def test_upgraded_history_sets_aside_every_known_predecessor_decision():
    decisions = (
        _decision(1, None),                                   # predecessor twin
        _decision(2, PIPELINE_CACHE_EPOCH),                   # current
        _decision(3, "2", extra=(("cell_split", "absent"),)),  # predecessor, params also changed
        _decision(4, None, uid="T.chr1"),                     # predecessor, unit not rerun yet
        _decision(5, PIPELINE_CACHE_EPOCH, minpolyA=12),      # second current producer: kept
        _decision(6, "3"),                                # previous integrated generation
    )
    assert cache_epoch_superseded_decision_ids(decisions) == (1, 3, 4, 6)


def test_history_without_current_epoch_is_unchanged():
    assert cache_epoch_superseded_decision_ids((_decision(1, None), _decision(2, None))) == ()


@pytest.mark.parametrize("foreign", ["6", "1", "x"])
def test_unknown_or_future_epoch_is_a_hard_compatibility_error(foreign):
    decisions = (_decision(1, None), _decision(2, PIPELINE_CACHE_EPOCH), _decision(3, foreign))
    with pytest.raises(CacheEpochCompatibilityError, match="new run root"):
        cache_epoch_superseded_decision_ids(decisions)


def test_candidate_input_report_requires_reason_for_every_excluded_decision(tmp_path):
    rows = (SimpleNamespace(decision_id=1, instance_id="a", consumes=()),
            SimpleNamespace(decision_id=2, instance_id="b", consumes=()))
    with pytest.raises(ValueError, match="without a reason"):
        build_candidate_decision_input_status_report(
            tmp_path, source_decisions=rows, retained_decisions=(), excluded_decision_reasons={1: "x"},
        )
    report = build_candidate_decision_input_status_report(
        tmp_path, source_decisions=rows, retained_decisions=(),
        excluded_decision_reasons={1: "cache_epoch_predecessor", 2: "cache_epoch_predecessor"},
    )
    assert report.entries == () and report.source_decision_count == 2
    assert report.excluded_decision_count == 2
    assert dict(report.excluded_reason_counts) == {"cache_epoch_predecessor": 2}


# --- run-root status: preview notice, completion marker and startup guard -----------

def _item(uid):
    return {**ITEM, "uid": uid}


def _seeded_root(tmp_path, coordinates):
    tm = TaskManager(state_dir=tmp_path / "state", config={"run_id": "epoch"})
    tm.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    coordinate_kinds = [classify_cache_epoch(coordinate.params) for coordinate in coordinates]
    for coordinate, kind in zip(coordinates, coordinate_kinds):
        if kind == "current":
            tm.mark_task_done(coordinate)
        else:
            _write_historical_completion(tm, coordinate)
    if coordinate_kinds and "current" not in coordinate_kinds:
        con = sqlite3.connect(tm.db_path)
        with con:
            con.execute("DELETE FROM meta WHERE key=?", (PIPELINE_CACHE_EPOCH_ROOT_KEY,))
        con.close()
    return tm


def _run_current(tm, uids, fn=lambda _item: None):
    spec = task_specs_by_key()[(STAGE, TASK)]
    run_flex_batch(tm=tm, stage=STAGE, task_base=TASK, items=[_item(u) for u in uids],
                   identity_keys=list(spec.identity_keys), param_keys=list(spec.param_keys),
                   fn=fn, max_workers=1)


def _fail(_item):
    raise RuntimeError("stopped part-way")


def _old_abc(tmp_path):
    return _seeded_root(tmp_path, [_old_epoch_coordinate(_item(u)) for u in ("A.chr1", "B.chr1", "C.chr1")])


def test_preview_identifies_predecessor_root_as_blocked(tmp_path):
    tm = _seeded_root(tmp_path, [_old_epoch_coordinate()])
    preview = preview_rerun_from_state(tm.db_path, {"run_id": "epoch"})
    assert preview.changed_effective_keys == () and not preview.rerun_preview.impacted_tasks
    assert preview.full_cache_rebuild                     # config-only "0 impact" is overridden
    payload = preview.as_dict()
    assert payload["full_cache_rebuild"] is True
    assert payload["cache_epoch"]["status"] == "full_rebuild"
    assert payload["cache_epoch"]["blocks_run"] is True
    assert "new run root" in payload["cache_epoch"]["notice"]
    assert payload["cache_epoch"]["rebuild_task_count"] == 1


def test_mixed_generation_root_is_blocked(tmp_path):
    tm = _old_abc(tmp_path)
    _run_current(tm, ["A.chr1"])
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.done_counts["current"] == 1 and status.done_counts["current_incomplete"] == 0
    assert status.status == "upgrade_in_progress" and status.rebuild_required and status.blocks_run
    assert "new run root" in status.notice and status.rebuild_task_count == 3
    assert preview_rerun_from_state(tm.db_path, {"run_id": "epoch"}).full_cache_rebuild
    with pytest.raises(CacheEpochCompatibilityError, match="new run root"):
        require_compatible_run_root(tm.db_path)


def test_completion_marker_is_written_only_by_explicit_plan_completion(tmp_path):
    tm = _seeded_root(tmp_path, [])
    _run_current(tm, ["A.chr1", "B.chr1", "C.chr1"])
    assert inspect_run_root_cache_epoch(tm.db_path).status == "current"
    first = json.loads(tm.record_cache_epoch_plan_completion())
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "current" and not status.rebuild_required and status.notice == ""
    assert status.done_counts["predecessor"] == 0
    second = json.loads(tm.record_cache_epoch_plan_completion())
    assert second["epoch"] == PIPELINE_CACHE_EPOCH and second["completed_invocation_count"] == 2
    assert second["first_completed_at"] == first["first_completed_at"]
    assert inspect_run_root_cache_epoch(tm.db_path).completion_marker["completed_invocation_count"] == 2


def test_current_done_then_failure_without_marker_remains_current(tmp_path):
    tm = _seeded_root(tmp_path, [])
    _run_current(tm, ["A.chr1"])
    with pytest.raises(Exception):
        _run_current(tm, ["B.chr1"], fn=_fail)
    outputs = (
        tmp_path / "unit_outputs" / "A.chr1" / "partial.bam",
        tmp_path / "reference" / "shared" / "adjusted.gtf",
        tmp_path / "backup" / "source.gtf",
    )
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"current-output")
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "current" and not status.blocks_run
    assert set(status.untracked_path_examples) == {
        "backup/source.gtf",
        "reference/shared/adjusted.gtf",
        "unit_outputs/A.chr1/partial.bam",
    }
    assert status.done_counts["current_incomplete"] == 1
    assert status.incomplete_task_keys == (f"{STAGE}:{TASK}",)


def test_failure_after_current_completion_marker_is_an_ordinary_failure(tmp_path):
    tm = _seeded_root(tmp_path, [])
    _run_current(tm, ["A.chr1", "B.chr1", "C.chr1"])
    tm.record_cache_epoch_plan_completion()
    with pytest.raises(Exception):
        _run_current(tm, ["D.chr1"], fn=_fail)
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "current" and not status.rebuild_required
    assert status.done_counts["current_incomplete"] == 1  # reported, not an upgrade state


def test_current_marker_cannot_make_mixed_history_resumable(tmp_path):
    tm = _seeded_root(tmp_path, [_old_epoch_coordinate(_item("OLD-VARIANT.chr1"))])
    _run_current(tm, ["A.chr1"])
    tm.store.meta_set(
        PIPELINE_CACHE_EPOCH_COMPLETION_KEY,
        build_cache_epoch_completion_marker(None, invocation_id="seed"),
    )
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "upgrade_in_progress" and status.blocks_run
    with pytest.raises(CacheEpochCompatibilityError, match="new run root"):
        tm.record_cache_epoch_plan_completion()


def test_current_root_resumes_and_reuses_current_completion(tmp_path):
    tm = _seeded_root(tmp_path, [])
    calls = []
    _run_current(tm, ["A.chr1"], fn=lambda _item: calls.append("ran"))
    assert inspect_run_root_cache_epoch(tm.db_path).status == "current"
    resumed = TaskManager(state_dir=tm.state_dir, config={"run_id": "epoch"})
    resumed.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    _run_current(resumed, ["A.chr1"], fn=lambda _item: calls.append("ran"))
    assert calls == ["ran"]


def test_future_epoch_root_stops_before_any_write(tmp_path):
    tm = _seeded_root(tmp_path, [_old_epoch_coordinate(extra_params=((PIPELINE_CACHE_EPOCH_KEY, "6"),))])
    before = tm.db_path.read_bytes()
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "incompatible" and status.blocks_run and "new run root" in status.notice
    tm2 = TaskManager(state_dir=tm.state_dir, config={"run_id": "epoch"})
    with pytest.raises(CacheEpochCompatibilityError, match="new run root"):
        tm2.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    assert tm.db_path.read_bytes() == before              # nothing scheduled or written


def test_predecessor_decision_without_completion_still_blocks_root(tmp_path):
    tm = _seeded_root(tmp_path, [])
    with pytest.raises(Exception):
        _run_current(tm, ["A.chr1"], fn=_fail)
    con = sqlite3.connect(tm.db_path)
    with con:
        row = con.execute(
            "SELECT decision_id, instance_json FROM task_reuse_decisions_v3 ORDER BY decision_id LIMIT 1"
        ).fetchone()
        payload = json.loads(row[1])
        payload["params"] = [
            field
            for field in payload["params"]
            if field["key"] != PIPELINE_CACHE_EPOCH_KEY
        ]
        con.execute(
            "UPDATE task_reuse_decisions_v3 SET instance_json=? WHERE decision_id=?",
            (json.dumps(payload, sort_keys=True), row[0]),
        )
    con.close()
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "upgrade_in_progress" and status.blocks_run
    assert status.done_counts["predecessor"] == 0
    assert status.decision_counts["predecessor"] == 1
    with pytest.raises(CacheEpochCompatibilityError, match="new run root"):
        require_compatible_run_root(tm.db_path)


def test_foreign_completion_marker_is_incompatible_and_never_overwritten(tmp_path):
    tm = _seeded_root(tmp_path, [_current_coordinate()])
    tm.store.meta_set(PIPELINE_CACHE_EPOCH_COMPLETION_KEY, json.dumps(
        {"schema": "scpolya.pipeline_cache_epoch_completion.v1", "epoch": "6"}))
    assert inspect_run_root_cache_epoch(tm.db_path).status == "incompatible"
    with pytest.raises(CacheEpochCompatibilityError):
        build_cache_epoch_completion_marker(json.dumps({"epoch": "6"}), invocation_id="x")
    with pytest.raises(CacheEpochCompatibilityError):
        tm.record_cache_epoch_plan_completion()


@pytest.mark.parametrize("epoch", ["2", "3", "4"])
def test_known_predecessor_completion_marker_is_not_unknown(tmp_path, epoch):
    tm = _seeded_root(tmp_path, [])
    tm.store.meta_set(
        PIPELINE_CACHE_EPOCH_COMPLETION_KEY,
        json.dumps({"schema": "scpolya.pipeline_cache_epoch_completion.v1", "epoch": epoch}),
    )
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "upgrade_in_progress" and status.blocks_run
    assert "unknown cache generation" not in status.notice
    assert "new run root" in status.notice


def test_foreign_root_marker_is_incompatible_and_never_overwritten(tmp_path):
    tm = _seeded_root(tmp_path, [])
    foreign = json.dumps(
        {"schema": "scpolya.pipeline_cache_epoch_root.v1", "epoch": "6"},
        sort_keys=True,
    )
    tm.store.meta_set(PIPELINE_CACHE_EPOCH_ROOT_KEY, foreign)
    before = _logical_and_meta(tm.db_path)
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "incompatible" and status.blocks_run
    with pytest.raises(CacheEpochCompatibilityError, match="new run root"):
        TaskManager(state_dir=tm.state_dir, config={"run_id": "epoch"}).load_or_init(
            Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]})
        )
    with pytest.raises(CacheEpochCompatibilityError, match="new run root"):
        build_cache_epoch_root_marker(foreign)
    assert _logical_and_meta(tm.db_path) == before


def _db_logical_content(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return list(con.iterdump())
    finally:
        con.close()


def _corrupt(db_path, mode):
    if mode == "not_sqlite":
        for sidecar in (db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
            sidecar.unlink(missing_ok=True)
        db_path.write_bytes(b"this is not an sqlite database" * 100)
        return
    con = sqlite3.connect(db_path)
    with con:
        if mode == "coordinate_json":
            con.execute("UPDATE task_done_coordinate_v2 SET coordinate_json='{' WHERE rowid="
                        "(SELECT MIN(rowid) FROM task_done_coordinate_v2)")
        elif mode == "coordinate_params":
            con.execute("""UPDATE task_done_coordinate_v2 SET coordinate_json='{"params": 5}' WHERE rowid="""
                        "(SELECT MIN(rowid) FROM task_done_coordinate_v2)")
        elif mode == "instance_json":
            con.execute("UPDATE task_reuse_decisions_v3 SET instance_json='{' WHERE decision_id="
                        "(SELECT MIN(decision_id) FROM task_reuse_decisions_v3)")
        elif mode == "marker":
            con.execute("UPDATE meta SET value='{' WHERE key=?", (PIPELINE_CACHE_EPOCH_COMPLETION_KEY,))
        elif mode == "root_marker":
            con.execute("UPDATE meta SET value='{' WHERE key=?", (PIPELINE_CACHE_EPOCH_ROOT_KEY,))
    con.close()


@pytest.mark.parametrize(
    "mode",
    ["coordinate_json", "coordinate_params", "instance_json", "marker", "root_marker", "not_sqlite"],
)
def test_unreadable_state_blocks_before_any_state_write(tmp_path, mode):
    tm = _seeded_root(tmp_path, [])
    _run_current(tm, ["A.chr1"])
    tm.record_cache_epoch_plan_completion()
    _corrupt(tm.db_path, mode)
    before_bytes = tm.db_path.read_bytes()
    before = None if mode == "not_sqlite" else _db_logical_content(tm.db_path)
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "unreadable" and status.blocks_run and not status.rebuild_required
    assert "Restore state/state.sqlite" in status.notice and "new run root" in status.notice
    tm2 = TaskManager(state_dir=tm.state_dir, config={"run_id": "epoch"})
    # load_or_init runs the pre-existing read-only completion-structure check first; for a
    # file that is not SQLite at all that check already fails closed (TaskCompletionLoadError)
    # (main.py runs the cache-generation guard earlier and reports the unreadable notice).
    expected = (CacheEpochStateUnreadableError, TaskCompletionLoadError) if mode == "not_sqlite" \
        else CacheEpochStateUnreadableError
    with pytest.raises(expected):
        tm2.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    with pytest.raises(CacheEpochStateUnreadableError):
        tm2.record_cache_epoch_plan_completion()
    assert tm.db_path.read_bytes() == before_bytes        # no schema/meta/task write
    if before is not None:
        assert _db_logical_content(tm.db_path) == before


def test_missing_state_is_a_new_root_and_is_not_blocked(tmp_path):
    assert inspect_run_root_cache_epoch(tmp_path / "missing").status == "no_state"
    assert not (tmp_path / "missing").exists()
    tm = TaskManager(state_dir=tmp_path / "new" / "state", config={"run_id": "epoch"})
    tm.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))   # not blocked
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "current"
    assert status.root_marker == {
        "schema": "scpolya.pipeline_cache_epoch_root.v1",
        "epoch": PIPELINE_CACHE_EPOCH,
    }
    log = tmp_path / "new" / "logs" / "early-failure.log"
    log.parent.mkdir(parents=True)
    log.write_text("failed before first task")
    resumed = TaskManager(state_dir=tm.state_dir, config={"run_id": "epoch"})
    resumed.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    assert inspect_run_root_cache_epoch(tm.db_path).status == "current"


def test_empty_managed_directories_do_not_block_new_root(tmp_path):
    run_root = tmp_path / "empty-root"
    for name in MANAGED_TOP_LEVELS:
        (run_root / name).mkdir(parents=True)
    status = inspect_run_root_cache_epoch(run_root / "state")
    assert status.status == "no_state" and not status.blocks_run
    assert status.untracked_path_examples == ()
    assert not (run_root / "state").exists()


@pytest.mark.parametrize("state_kind", ["missing", "empty_db"])
@pytest.mark.parametrize("relative_path", UNTRACKED_RUN_ROOT_FILES)
def test_untracked_run_root_files_block_without_state_mutation(
    tmp_path, state_kind, relative_path
):
    run_root = tmp_path / state_kind
    output = run_root / relative_path
    output.parent.mkdir(parents=True)
    output.write_bytes(b"historical")
    db_path = run_root / "state" / "state.sqlite"
    if state_kind == "empty_db":
        db_path.parent.mkdir(parents=True)
        sqlite3.connect(db_path).close()
    before_db = db_path.read_bytes() if db_path.exists() else None
    status = inspect_run_root_cache_epoch(run_root / "state")
    assert status.status == "untracked_outputs" and status.blocks_run
    assert status.untracked_path_examples == (relative_path,)
    assert "new run root" in status.notice
    with pytest.raises(CacheEpochCompatibilityError, match="new run root"):
        require_compatible_run_root(run_root / "state")
    assert output.read_bytes() == b"historical"
    assert (db_path.read_bytes() if db_path.exists() else None) == before_db


@pytest.mark.parametrize("state_kind", ["missing", "empty_db"])
@pytest.mark.parametrize("target_kind", ["dangling", "directory"])
def test_symlink_is_untracked_without_following_target(tmp_path, state_kind, target_kind):
    run_root = tmp_path / state_kind
    link = run_root / "reference" / "external-reference"
    link.parent.mkdir(parents=True)
    target = tmp_path / "external-target"
    if target_kind == "directory":
        target.mkdir()
        (target / "large-external-data.bin").write_bytes(b"external")
    link.symlink_to(target, target_is_directory=True)
    db_path = run_root / "state" / "state.sqlite"
    if state_kind == "empty_db":
        db_path.parent.mkdir(parents=True)
        sqlite3.connect(db_path).close()
    status = inspect_run_root_cache_epoch(run_root / "state")
    assert status.status == "untracked_outputs" and status.blocks_run
    assert status.untracked_path_examples == ("reference/external-reference",)
    assert link.is_symlink()
    assert link.exists() is (target_kind == "directory")


def test_main_entry_guard_runs_before_directory_or_log_initialization(tmp_path, monkeypatch):
    writes = []
    layout = SimpleNamespace(
        State=tmp_path / "state",
        ensure_base_dirs=lambda: writes.append("ensure_base_dirs"),
    )
    monkeypatch.setattr(pipeline_main, "RunLayout", lambda **_kwargs: layout)
    monkeypatch.setattr(
        pipeline_main,
        "require_current_task_completion_state_v1",
        lambda _state: None,
    )

    def reject(_state):
        raise CacheEpochCompatibilityError("use a new run root")

    monkeypatch.setattr(pipeline_main, "require_compatible_run_root", reject)
    monkeypatch.setattr(
        pipeline_main,
        "setup_run_logging",
        lambda **_kwargs: writes.append("setup_run_logging"),
    )
    args = SimpleNamespace(verbose=False, output_dir=tmp_path, run_id="blocked")
    with pytest.raises(SystemExit, match="new run root"):
        pipeline_main.run_mode_initialization(args)
    assert writes == []


def test_main_entry_guard_rejects_legacy_only_root_before_logs_or_dirs(tmp_path):
    layout = RunLayout(out=tmp_path / "out", run_id="legacy")
    layout.State.mkdir(parents=True)
    db_path = layout.State / "state.sqlite"
    con = sqlite3.connect(db_path)
    with con:
        con.execute("CREATE TABLE task_done_flex (legacy_key TEXT)")
        con.execute("INSERT INTO task_done_flex VALUES ('done')")
    con.close()
    before = db_path.read_bytes()
    assert not layout.Logs.exists()
    args = SimpleNamespace(verbose=False, output_dir=layout.out, run_id=layout.run_id)
    with pytest.raises(SystemExit, match="new run root"):
        pipeline_main.run_mode_initialization(args)
    assert db_path.read_bytes() == before
    assert not layout.Logs.exists()


@pytest.mark.parametrize(
    "state_name",
    ("state.sqlite", "state.sqlite-wal", "state.sqlite-shm", "state.sqlite-journal"),
)
@pytest.mark.parametrize("target_kind", ["existing", "dangling"])
def test_main_rejects_state_file_symlinks_before_sqlite_or_writes(
    tmp_path, state_name, target_kind
):
    layout = RunLayout(out=tmp_path / "out", run_id=f"unsafe-{state_name}-{target_kind}")
    layout.State.mkdir(parents=True)
    link = layout.State / state_name
    external = tmp_path / "external" / f"{state_name}-{target_kind}"
    external.parent.mkdir(parents=True)
    before_hash = None
    if target_kind == "existing":
        external.write_bytes(b"external-state-target")
        before_hash = hashlib.sha256(external.read_bytes()).hexdigest()
    link.symlink_to(external)
    args = SimpleNamespace(verbose=False, output_dir=layout.out, run_id=layout.run_id)
    with pytest.raises(CacheEpochStateUnreadableError, match="Unsafe run state path"):
        require_safe_state_paths(layout.State)
    with pytest.raises(SystemExit, match="Unsafe run state path"):
        pipeline_main.run_mode_initialization(args)
    assert link.is_symlink() and not layout.Logs.exists()
    if before_hash is not None:
        assert hashlib.sha256(external.read_bytes()).hexdigest() == before_hash
    else:
        assert not external.exists()
    if state_name != "state.sqlite":
        assert not (layout.State / "state.sqlite").exists()


@pytest.mark.parametrize(
    "unsafe_kind",
    (
        "db-existing",
        "db-dangling",
        "state-dir-existing",
        "state-dir-dangling",
        "wal-existing",
        "wal-dangling",
        "shm-existing",
        "shm-dangling",
        "journal-existing",
        "journal-dangling",
    ),
)
def test_public_runtime_write_boundaries_reject_unsafe_state_without_load_or_init(
    tmp_path, unsafe_kind
):
    layout = RunLayout(out=tmp_path / "out", run_id=f"runtime-{unsafe_kind}")
    path_kind, target_kind = unsafe_kind.rsplit("-", 1)
    external = tmp_path / "external-runtime" / unsafe_kind
    external.parent.mkdir(parents=True)
    sentinel = None
    before_hash = None
    before_mode = None
    if path_kind == "state-dir":
        layout.Root.mkdir(parents=True)
        if target_kind == "existing":
            external.mkdir()
            sentinel = external / "sentinel.bin"
            sentinel.write_bytes(b"external-state-directory")
            before_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            before_mode = stat.S_IMODE(external.stat().st_mode)
        layout.State.symlink_to(external, target_is_directory=True)
    else:
        layout.State.mkdir(parents=True)
        suffix = {
            "db": "",
            "wal": "-wal",
            "shm": "-shm",
            "journal": "-journal",
        }[path_kind]
        unsafe_path = Path(f"{layout.State / 'state.sqlite'}{suffix}")
        if target_kind == "existing":
            external.write_bytes(b"external-state-file")
            before_hash = hashlib.sha256(external.read_bytes()).hexdigest()
            before_mode = stat.S_IMODE(external.stat().st_mode)
        unsafe_path.symlink_to(external)

    tm = TaskManager(state_dir=layout.State, config={"run_id": "unsafe"})
    with pytest.raises(CacheEpochStateUnreadableError, match="Unsafe run state path"):
        tm.store.connect()
    spec = task_specs_by_key()[(STAGE, TASK)]
    with pytest.raises(CacheEpochStateUnreadableError, match="Unsafe run state path"):
        run_flex_batch(
            tm=tm,
            stage=STAGE,
            task_base=TASK,
            items=[],
            identity_keys=list(spec.identity_keys),
            param_keys=list(spec.param_keys),
            fn=lambda _item: None,
            max_workers=1,
        )

    assert not layout.Logs.exists()
    if target_kind == "existing":
        if sentinel is not None:
            assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == before_hash
            assert stat.S_IMODE(external.stat().st_mode) == before_mode
            assert not (external / "state.sqlite").exists()
        else:
            assert hashlib.sha256(external.read_bytes()).hexdigest() == before_hash
            assert stat.S_IMODE(external.stat().st_mode) == before_mode
    else:
        assert not external.exists()


@pytest.mark.parametrize("target_kind", ["existing", "dangling"])
def test_main_rejects_state_directory_symlink_before_sqlite_or_writes(tmp_path, target_kind):
    layout = RunLayout(out=tmp_path / "out", run_id=f"unsafe-state-dir-{target_kind}")
    layout.Root.mkdir(parents=True)
    external = tmp_path / "external-state-dir"
    sentinel = external / "sentinel.bin"
    before_hash = None
    if target_kind == "existing":
        external.mkdir()
        sentinel.write_bytes(b"external-directory-target")
        before_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    layout.State.symlink_to(external, target_is_directory=True)
    args = SimpleNamespace(verbose=False, output_dir=layout.out, run_id=layout.run_id)
    with pytest.raises(CacheEpochStateUnreadableError, match="Unsafe run state path"):
        require_safe_state_paths(layout.State)
    with pytest.raises(SystemExit, match="Unsafe run state path"):
        pipeline_main.run_mode_initialization(args)
    assert layout.State.is_symlink() and not layout.Logs.exists()
    if before_hash is not None:
        assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == before_hash
        assert not (external / "state.sqlite").exists()
    else:
        assert not external.exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_main_rejects_regular_orphan_sidecar_before_state_creation(tmp_path, suffix):
    layout = RunLayout(out=tmp_path / "out", run_id=f"orphan{suffix}")
    layout.State.mkdir(parents=True)
    sidecar = Path(f"{layout.State / 'state.sqlite'}{suffix}")
    sidecar.write_bytes(b"orphan-sidecar")
    before_hash = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    args = SimpleNamespace(verbose=False, output_dir=layout.out, run_id=layout.run_id)
    with pytest.raises(CacheEpochStateUnreadableError, match="sidecar exists without state.sqlite"):
        require_safe_state_paths(layout.State)
    with pytest.raises(SystemExit, match="sidecar exists without state.sqlite"):
        pipeline_main.run_mode_initialization(args)
    assert hashlib.sha256(sidecar.read_bytes()).hexdigest() == before_hash
    assert not (layout.State / "state.sqlite").exists()
    assert not layout.Logs.exists()


@pytest.mark.parametrize("locked_relative", ["reference/locked", "state"])
def test_unreadable_run_root_directory_blocks_without_mode_or_byte_changes(
    tmp_path, locked_relative
):
    layout = RunLayout(out=tmp_path / "out", run_id="permission-denied")
    locked = layout.Root / locked_relative
    locked.mkdir(parents=True)
    sentinel = locked / "sentinel.bin"
    sentinel.write_bytes(b"protected")
    before_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    original_mode = stat.S_IMODE(locked.stat().st_mode)
    locked.chmod(0)
    locked_mode = stat.S_IMODE(locked.stat().st_mode)
    try:
        status = inspect_run_root_cache_epoch(layout.State)
        assert status.status == "unreadable" and status.blocks_run
        assert "PermissionError" in status.notice
        with pytest.raises(CacheEpochStateUnreadableError, match="PermissionError"):
            require_compatible_run_root(layout.State)
        args = SimpleNamespace(verbose=False, output_dir=layout.out, run_id=layout.run_id)
        with pytest.raises(SystemExit, match="PermissionError"):
            pipeline_main.run_mode_initialization(args)
        assert stat.S_IMODE(locked.stat().st_mode) == locked_mode
        assert not layout.Logs.exists()
    finally:
        locked.chmod(original_mode)
    assert stat.S_IMODE(locked.stat().st_mode) == original_mode
    assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == before_hash
    assert not (layout.State / "state.sqlite").exists()


@pytest.mark.parametrize(
    "relative_path",
    (
        "inputs/source.csv",
        "reference/shared/chrom.sizes",
        "backup/source.gtf",
        "annotated_bams/sample.bam",
        "count_matrix/counts.mtx",
        "final_output/historical.tsv",
    ),
)
def test_main_entry_guard_preserves_untracked_file_hash_before_state_or_logs(
    tmp_path, relative_path
):
    layout = RunLayout(out=tmp_path / "out", run_id="orphaned")
    output = layout.Root / relative_path
    output.parent.mkdir(parents=True)
    output.write_bytes(b"historical")
    before_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    assert not layout.State.exists() and not layout.Logs.exists()
    args = SimpleNamespace(verbose=False, output_dir=layout.out, run_id=layout.run_id)
    with pytest.raises(SystemExit, match="new run root"):
        pipeline_main.run_mode_initialization(args)
    assert hashlib.sha256(output.read_bytes()).hexdigest() == before_hash
    assert not layout.State.exists() and not layout.Logs.exists()


def test_status_check_is_read_only_and_never_creates_state(tmp_path):
    db = tmp_path / "state.sqlite"
    sqlite3.connect(db).close()
    assert inspect_run_root_cache_epoch(db).status == "fresh"


def test_source_mismatch_disables_epoch_exclusion(tmp_path):
    tm = _seeded_root(tmp_path, [])
    _run_current(tm, ["A.chr1", "B.chr1"])
    decisions = load_current_reuse_decision_summaries_v2(tm.db_path, limit=None, latest_first=False)
    assert len(decisions) >= 2
    mismatched = build_dynamic_instance_graph(tm.db_path, decisions=decisions[:1])
    result = build_active_contract_generation_candidate_graph(tm.db_path, mismatched)
    assert not result.source_matches
    assert result.cache_epoch_superseded_decision_ids == ()
    assert result.retained_decisions == tuple(decisions) and result.excluded_decision_reasons == {}


# --- strict completion-marker shape (valid JSON, bad shape) --------------------------

_GOOD_MARKER = {
    "schema": "scpolya.pipeline_cache_epoch_completion.v1", "epoch": PIPELINE_CACHE_EPOCH,
    "first_completed_at": "2026-10-09T08:00:00+00:00", "first_invocation_id": "inv-first",
    "last_completed_at": "2026-10-09T09:00:00+00:00", "last_invocation_id": "inv-last",
    "completed_invocation_count": 2,
}


def _without(key):
    return {k: v for k, v in _GOOD_MARKER.items() if k != key}


_BAD_MARKERS = {
    "only_schema_and_epoch": {"schema": _GOOD_MARKER["schema"], "epoch": PIPELINE_CACHE_EPOCH},
    **{f"missing_{k}": _without(k) for k in (
        "first_completed_at", "first_invocation_id", "last_completed_at", "last_invocation_id",
        "completed_invocation_count")},
    "first_completed_at_int": {**_GOOD_MARKER, "first_completed_at": 123},
    "first_invocation_id_empty": {**_GOOD_MARKER, "first_invocation_id": ""},
    "last_completed_at_blank": {**_GOOD_MARKER, "last_completed_at": "   "},
    "last_invocation_id_null": {**_GOOD_MARKER, "last_invocation_id": None},
    "last_invocation_id_list": {**_GOOD_MARKER, "last_invocation_id": ["x"]},
    "count_not_an_int": {**_GOOD_MARKER, "completed_invocation_count": "not-an-int"},
    "count_numeric_string": {**_GOOD_MARKER, "completed_invocation_count": "3"},
    "count_zero": {**_GOOD_MARKER, "completed_invocation_count": 0},
    "count_negative": {**_GOOD_MARKER, "completed_invocation_count": -1},
    "count_bool_true": {**_GOOD_MARKER, "completed_invocation_count": True},
    "count_bool_false": {**_GOOD_MARKER, "completed_invocation_count": False},
    "count_float_whole": {**_GOOD_MARKER, "completed_invocation_count": 1.0},
    "count_float": {**_GOOD_MARKER, "completed_invocation_count": 2.5},
    "count_null": {**_GOOD_MARKER, "completed_invocation_count": None},
    "extra_field": {**_GOOD_MARKER, "note": "unexpected"},
    "wrong_schema": {**_GOOD_MARKER, "schema": "scpolya.something_else.v1"},
    "epoch_empty": {**_GOOD_MARKER, "epoch": ""},
    "epoch_int": {**_GOOD_MARKER, "epoch": 2},
}


def _logical_and_meta(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return list(con.iterdump()), sorted(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()


def _set_marker_raw(db_path, value):
    con = sqlite3.connect(db_path)
    with con:
        con.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (PIPELINE_CACHE_EPOCH_COMPLETION_KEY, json.dumps(value)))
    con.close()


@pytest.mark.parametrize("root_kind", ["predecessor_only", "mixed"])
@pytest.mark.parametrize("case", sorted(_BAD_MARKERS))
def test_bad_shape_marker_is_unreadable_and_blocks_before_any_write(tmp_path, case, root_kind):
    tm = _old_abc(tmp_path)                         # predecessor-only: full_rebuild without a marker
    if root_kind == "mixed":
        _run_current(tm, ["A.chr1"])                # mixed: upgrade_in_progress without a marker
    _set_marker_raw(tm.db_path, _BAD_MARKERS[case])
    before = _logical_and_meta(tm.db_path)

    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "unreadable" and status.blocks_run and not status.rebuild_required
    with pytest.raises(CacheEpochStateUnreadableError):
        require_compatible_run_root(tm.db_path)
    tm2 = TaskManager(state_dir=tm.state_dir, config={"run_id": "epoch"})
    with pytest.raises(CacheEpochStateUnreadableError):
        tm2.load_or_init(Plan(samples=["S"], contigs_by_sample={"S": ["chr1"]}))
    with pytest.raises(CacheEpochStateUnreadableError):
        tm2.record_cache_epoch_plan_completion()
    with pytest.raises(CacheEpochStateUnreadableError):
        build_cache_epoch_completion_marker(json.dumps(_BAD_MARKERS[case]), invocation_id="inv-new")
    assert _logical_and_meta(tm.db_path) == before  # no schema/meta (incl. updated_at)/task/marker write


def test_good_marker_lifecycle_is_strict_and_preserves_first_completion():
    first = json.loads(build_cache_epoch_completion_marker(None, invocation_id="inv-1", now="t1"))
    assert first == {**_GOOD_MARKER, "first_completed_at": "t1", "first_invocation_id": "inv-1",
                     "last_completed_at": "t1", "last_invocation_id": "inv-1", "completed_invocation_count": 1}
    second = json.loads(build_cache_epoch_completion_marker(json.dumps(first), invocation_id="inv-2", now="t2"))
    assert second["first_completed_at"] == "t1" and second["first_invocation_id"] == "inv-1"
    assert second["last_completed_at"] == "t2" and second["last_invocation_id"] == "inv-2"
    assert type(second["completed_invocation_count"]) is int and second["completed_invocation_count"] == 2
    with pytest.raises(ValueError):
        build_cache_epoch_completion_marker(None, invocation_id="")


def test_good_marker_records_current_root_completion(tmp_path):
    tm = _seeded_root(tmp_path, [])
    _run_current(tm, ["A.chr1"])
    _set_marker_raw(tm.db_path, _GOOD_MARKER)
    status = inspect_run_root_cache_epoch(tm.db_path)
    assert status.status == "current" and status.completion_marker["completed_invocation_count"] == 2
    tm.record_cache_epoch_plan_completion()
    assert inspect_run_root_cache_epoch(tm.db_path).completion_marker["completed_invocation_count"] == 3


@pytest.mark.parametrize("foreign", [{"schema": _GOOD_MARKER["schema"], "epoch": "6"},
                                     {**_GOOD_MARKER, "epoch": "6"}])
def test_foreign_marker_stays_incompatible_and_is_never_overwritten(tmp_path, foreign):
    tm = _old_abc(tmp_path)
    _set_marker_raw(tm.db_path, foreign)
    before = _logical_and_meta(tm.db_path)
    assert inspect_run_root_cache_epoch(tm.db_path).status == "incompatible"
    with pytest.raises(CacheEpochCompatibilityError):
        TaskManager(state_dir=tm.state_dir, config={"run_id": "epoch"}).record_cache_epoch_plan_completion()
    with pytest.raises(CacheEpochCompatibilityError):
        build_cache_epoch_completion_marker(json.dumps(foreign), invocation_id="inv-new")
    assert _logical_and_meta(tm.db_path) == before
