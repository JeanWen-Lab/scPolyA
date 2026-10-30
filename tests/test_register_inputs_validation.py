from __future__ import annotations

import csv
import hashlib
import importlib
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pysam
import pytest

from stages.register_inputs import register_inputs
from task_manage import Plan, Resources, TaskManager
from task_manage.run_layout import RunLayout

register_inputs_stage = importlib.import_module("stages.register_inputs")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _make_bam(path: Path, *, start: int, read_count: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "chr1", "LN": 1000}]}
    alignment_header = pysam.AlignmentHeader.from_dict(header)
    with pysam.AlignmentFile(str(path), "wb", header=header) as out:
        for index in range(read_count):
            read = pysam.AlignedSegment(alignment_header)
            read.query_name = f"read-{start}-{index}"
            read.query_sequence = "A" * 20
            read.flag = 0
            read.reference_id = 0
            read.reference_start = start + index * 100
            read.mapping_quality = 60
            read.cigarstring = "20M"
            out.write(read)
    pysam.index(str(path))


def _write_csv(path: Path, rows: list[dict[str, object]], fieldnames=None) -> None:
    fieldnames = fieldnames or (
        "sample_id",
        "source_path",
        "is_deduped",
        "requires_split",
        "barcode_suffix",
    )
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _runtime(tmp_path: Path, inputs_csv: Path):
    layout = RunLayout(out=tmp_path / "out", run_id="run")
    layout.ensure_base_dirs()
    args = SimpleNamespace(inputs=str(inputs_csv))
    tm = TaskManager(state_dir=layout.State, config={"run_id": "run", "inputs": str(inputs_csv)})
    tm.load_or_init(Plan(samples=[], contigs_by_sample={}))
    cache = Resources(tm=tm, layout=layout, args=args)
    tm.cache = cache
    cache.set("context", {})
    return layout, args, tm, cache


def _register_done_rows(tm: TaskManager):
    con = sqlite3.connect(tm.db_path)
    try:
        return tuple(
            con.execute(
                "SELECT task_base, task_coordinate_id, coordinate_json "
                "FROM task_done_coordinate_v2 WHERE stage='register_inputs' ORDER BY task_base"
            ).fetchall()
        )
    finally:
        con.close()


def _stage_files(layout: RunLayout) -> dict[str, str]:
    roots = (layout.Inputs, layout.InputTables)
    return {
        path.relative_to(layout.Root).as_posix(): _sha256(path)
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


@pytest.mark.parametrize("valid_first", [False, True])
def test_unindexable_source_fails_before_any_registration_publication(tmp_path, valid_first):
    valid = tmp_path / "valid.bam"
    _make_bam(valid, start=100)
    Path(f"{valid}.bai").unlink()
    invalid = tmp_path / "unsorted.bam"
    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "chr1", "LN": 1000}]}
    with pysam.AlignmentFile(str(invalid), "wb", header=header) as out:
        for i, start in enumerate([500, 100]):
            read = pysam.AlignedSegment(out.header)
            read.query_name = f"unsorted-{i}"
            read.query_sequence = "A" * 20
            read.flag = 0
            read.reference_id = 0
            read.reference_start = start
            read.mapping_quality = 60
            read.cigarstring = "20M"
            out.write(read)
    rows = [{"sample_id": "valid", "source_path": valid},
            {"sample_id": "invalid", "source_path": invalid}]
    if not valid_first:
        rows.reverse()
    inputs = tmp_path / "inputs.csv"
    _write_csv(inputs, rows)
    layout, args, tm, cache = _runtime(tmp_path, inputs)
    unrelated = layout.Root / ".scpolya-register-index-unrelated"
    unrelated.mkdir()
    sentinel = unrelated / "keep.txt"
    sentinel.write_text("unrelated attempt")
    source_hashes = {source: _sha256(source) for source in (valid, invalid)}
    before = _stage_files(layout)
    done = _register_done_rows(tm)
    with pytest.raises(Exception):
        register_inputs(tm, layout, args, cache)
    assert _stage_files(layout) == before
    assert _register_done_rows(tm) == done
    assert list(layout.Inputs.iterdir()) == []
    assert [path.name for path in layout.Root.glob(".scpolya-register-index-*")] == [unrelated.name]
    assert sentinel.read_text() == "unrelated attempt"
    for source, digest in source_hashes.items():
        assert _sha256(source) == digest
        assert not Path(f"{source}.bai").exists()


@pytest.mark.parametrize("fail_second", [False, True])
def test_missing_indexes_are_prepared_privately_before_publish_and_reused(tmp_path, monkeypatch, fail_second):
    sources = [tmp_path / "a.bam", tmp_path / "b.bam"]
    for i, source in enumerate(sources):
        _make_bam(source, start=100 + i * 100)
        Path(f"{source}.bai").unlink()
    inputs = tmp_path / "inputs.csv"
    _write_csv(inputs, [{"sample_id": f"sample{i}", "source_path": source}
                        for i, source in enumerate(sources)])
    layout, args, tm, cache = _runtime(tmp_path, inputs)
    hashes = {source: _sha256(source) for source in sources}
    index = pysam.index
    attempts = []

    def prepare_index(*command):
        destination = Path(command[1])
        assert destination.parent.name.startswith(".scpolya-register-index-")
        assert not list(layout.Inputs.iterdir())
        assert not _stage_files(layout)
        assert not _register_done_rows(tm)
        attempts.append(destination)
        if fail_second and len(attempts) == 2:
            destination.write_bytes(b"incomplete index")
            raise OSError("injected index preparation failure")
        return index(*command)

    monkeypatch.setattr(pysam, "index", prepare_index)
    if fail_second:
        with pytest.raises(ValueError, match="Failed to prepare missing BAM index"):
            register_inputs(tm, layout, args, cache)
        assert not _stage_files(layout)
        assert not _register_done_rows(tm)
        assert not list(layout.Inputs.iterdir())
    else:
        register_inputs(tm, layout, args, cache)
        before = _stage_files(layout)
        done = _register_done_rows(tm)
        register_inputs(tm, layout, args, cache)
        assert _stage_files(layout) == before
        assert _register_done_rows(tm) == done
        assert len(done) == 3
    assert len(attempts) == 2
    assert not list(layout.Root.glob(".scpolya-register-index-*"))
    for source, digest in hashes.items():
        assert _sha256(source) == digest
        assert not Path(f"{source}.bai").exists()


def _assert_invalid_stage_has_no_new_outputs(layout: RunLayout, tm: TaskManager) -> None:
    assert not any(layout.Inputs.iterdir())
    assert not (layout.InputTables / "inputs.csv").exists()
    assert not (layout.InputTables / "preprocess_inputs.csv").exists()
    assert _register_done_rows(tm) == ()


@pytest.mark.parametrize(
    "case",
    (
        "duplicate",
        "blank",
        "whitespace",
        "leading-whitespace",
        "relative-parent",
        "dot",
        "absolute",
        "slash",
        "backslash",
        "control",
        "missing-source",
        "missing-path",
        "directory-no-bams",
        "directory-bam-is-directory",
    ),
)
def test_invalid_second_row_is_rejected_before_first_row_side_effects(tmp_path, case):
    sources = tmp_path / "sources"
    first = sources / "first.bam"
    second = sources / "second.bam"
    _make_bam(first, start=20)
    _make_bam(second, start=40)
    valid = {
        "sample_id": "sampleA",
        "source_path": str(first),
        "is_deduped": "0",
        "requires_split": "0",
        "barcode_suffix": "",
    }
    invalid = {
        "sample_id": "sampleB",
        "source_path": str(second),
        "is_deduped": "0",
        "requires_split": "0",
        "barcode_suffix": "",
    }
    if case == "duplicate":
        invalid["sample_id"] = "sampleA"
    elif case == "blank":
        invalid["sample_id"] = ""
    elif case == "whitespace":
        invalid["sample_id"] = "   "
    elif case == "leading-whitespace":
        invalid["sample_id"] = " sampleB"
    elif case == "relative-parent":
        invalid["sample_id"] = "../escape"
    elif case == "dot":
        invalid["sample_id"] = ".."
    elif case == "absolute":
        invalid["sample_id"] = str((tmp_path / "absolute" / "sampleB").resolve())
    elif case == "slash":
        invalid["sample_id"] = "group/sampleB"
    elif case == "backslash":
        invalid["sample_id"] = "group\\sampleB"
    elif case == "control":
        invalid["sample_id"] = "sample\x01B"
    elif case == "missing-source":
        invalid["source_path"] = str(tmp_path / "missing.bam")
    elif case == "missing-path":
        invalid["source_path"] = ""
    elif case == "directory-no-bams":
        directory = tmp_path / "empty-source"
        directory.mkdir()
        invalid["source_path"] = str(directory)
    elif case == "directory-bam-is-directory":
        directory = tmp_path / "ambiguous-source"
        (directory / "sampleB.chr1.bam").mkdir(parents=True)
        invalid["source_path"] = str(directory)

    inputs_csv = tmp_path / "inputs.csv"
    _write_csv(inputs_csv, [valid, invalid])
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)
    source_hashes = {
        path.name: _sha256(path)
        for path in sources.iterdir()
        if path.is_file()
    }

    with pytest.raises(ValueError):
        register_inputs(tm=tm, layout=layout, args=args, cache=cache)

    _assert_invalid_stage_has_no_new_outputs(layout, tm)
    assert {
        path.name: _sha256(path)
        for path in sources.iterdir()
        if path.is_file()
    } == source_hashes


@pytest.mark.parametrize("missing_header", ["sample_id", "source_path"])
def test_missing_required_header_is_rejected_before_dispatch(tmp_path, missing_header):
    source = tmp_path / "source.bam"
    _make_bam(source, start=20)
    headers = [header for header in ("sample_id", "source_path") if header != missing_header]
    inputs_csv = tmp_path / "inputs.csv"
    _write_csv(
        inputs_csv,
        [{header: "sampleA" if header == "sample_id" else str(source) for header in headers}],
        fieldnames=headers,
    )
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)
    with pytest.raises(ValueError):
        register_inputs(tm=tm, layout=layout, args=args, cache=cache)
    _assert_invalid_stage_has_no_new_outputs(layout, tm)


@pytest.mark.parametrize("link_kind", ["wrong", "dangling"])
def test_existing_bam_link_must_match_declared_source(tmp_path, link_kind):
    source_a = tmp_path / "sourceA.bam"
    source_b = tmp_path / "sourceB.bam"
    _make_bam(source_a, start=20)
    _make_bam(source_b, start=40, read_count=2)
    inputs_csv = tmp_path / "inputs.csv"
    _write_csv(inputs_csv, [{"sample_id": "sample", "source_path": str(source_b)}])
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)
    link = layout.Inputs / "sample.raw.undeduped.bam"
    target = source_a if link_kind == "wrong" else tmp_path / "missing.bam"
    link.symlink_to(target)
    raw_target = os.readlink(link)

    with pytest.raises(ValueError):
        register_inputs(tm=tm, layout=layout, args=args, cache=cache)

    assert link.is_symlink() and os.readlink(link) == raw_target
    assert not (layout.InputTables / "inputs.csv").exists()
    assert not (layout.InputTables / "preprocess_inputs.csv").exists()
    assert _register_done_rows(tm) == ()


@pytest.mark.parametrize("target_exists", [True, False])
def test_existing_bai_symlink_is_rejected_without_touching_external_target(
    tmp_path, target_exists
):
    source = tmp_path / "source.bam"
    _make_bam(source, start=20)
    inputs_csv = tmp_path / "inputs.csv"
    _write_csv(inputs_csv, [{"sample_id": "sample", "source_path": str(source)}])
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)
    link = layout.Inputs / "sample.raw.undeduped.bam"
    link.symlink_to(source)
    external = tmp_path / "external.bai"
    external_hash = None
    if target_exists:
        external.write_bytes(b"external-index")
        external_hash = _sha256(external)
    link_bai = Path(f"{link}.bai")
    link_bai.symlink_to(external)

    with pytest.raises(ValueError):
        register_inputs(tm=tm, layout=layout, args=args, cache=cache)

    assert link_bai.is_symlink()
    if external_hash is None:
        assert not external.exists()
    else:
        assert _sha256(external) == external_hash
    assert _register_done_rows(tm) == ()


def test_existing_wrong_regular_bai_is_rejected_without_replacement(tmp_path):
    source_a = tmp_path / "sourceA.bam"
    source_b = tmp_path / "sourceB.bam"
    _make_bam(source_a, start=20)
    _make_bam(source_b, start=40, read_count=2)
    inputs_csv = tmp_path / "inputs.csv"
    _write_csv(inputs_csv, [{"sample_id": "sample", "source_path": str(source_b)}])
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)
    link = layout.Inputs / "sample.raw.undeduped.bam"
    link.symlink_to(source_b)
    link_bai = Path(f"{link}.bai")
    link_bai.write_bytes(Path(f"{source_a}.bai").read_bytes())
    before_hash = _sha256(link_bai)

    with pytest.raises(ValueError):
        register_inputs(tm=tm, layout=layout, args=args, cache=cache)

    assert _sha256(link_bai) == before_hash
    assert _register_done_rows(tm) == ()


def test_valid_same_source_rerun_is_idempotent_and_cached_validation_cannot_be_bypassed(tmp_path):
    source = tmp_path / "source.bam"
    _make_bam(source, start=20)
    source_hashes = (_sha256(source), _sha256(Path(f"{source}.bai")))
    inputs_csv = tmp_path / "inputs.csv"
    sample_id = "sample-1_part.v2"
    valid_row = {"sample_id": sample_id, "source_path": str(source)}
    _write_csv(inputs_csv, [valid_row])
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)

    register_inputs(tm=tm, layout=layout, args=args, cache=cache)
    link = layout.Inputs / f"{sample_id}.raw.undeduped.bam"
    link_bai = Path(f"{link}.bai")
    before_target = os.readlink(link)
    before_files = _stage_files(layout)
    before_done = _register_done_rows(tm)
    before_bai_hash = _sha256(link_bai)

    register_inputs(tm=tm, layout=layout, args=args, cache=cache)
    assert os.readlink(link) == before_target
    assert _sha256(link_bai) == before_bai_hash
    assert _stage_files(layout) == before_files
    assert _register_done_rows(tm) == before_done

    assert (_sha256(source), _sha256(Path(f"{source}.bai"))) == source_hashes

    _write_csv(inputs_csv, [valid_row, valid_row])
    with pytest.raises(ValueError):
        register_inputs(tm=tm, layout=layout, args=args, cache=cache)
    assert os.readlink(link) == before_target
    assert _sha256(link_bai) == before_bai_hash
    assert _stage_files(layout) == before_files
    assert _register_done_rows(tm) == before_done

    _write_csv(inputs_csv, [valid_row])
    preprocess_csv = layout.InputTables / "preprocess_inputs.csv"
    preprocess_csv.write_text("pp_id,pp_path\nwrong,wrong\n")
    tampered_hash = _sha256(preprocess_csv)
    with pytest.raises(ValueError, match="preprocess_inputs.csv conflicts"):
        register_inputs(tm=tm, layout=layout, args=args, cache=cache)
    assert _sha256(preprocess_csv) == tampered_hash
    assert os.readlink(link) == before_target
    assert _sha256(link_bai) == before_bai_hash
    assert _register_done_rows(tm) == before_done


def test_rerun_after_parse_task_completion_writes_the_validated_rows(tmp_path, monkeypatch):
    source = tmp_path / "source.bam"
    _make_bam(source, start=20)
    inputs_csv = tmp_path / "inputs.csv"
    _write_csv(inputs_csv, [{"sample_id": "sample", "source_path": str(source)}])
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)
    original_run_flex_batch = register_inputs_stage.run_flex_batch

    def interrupt_before_write(*call_args, **call_kwargs):
        if call_kwargs["task_base"] == "write_preprocess_csv":
            raise RuntimeError("simulated interruption before preprocess CSV write")
        return original_run_flex_batch(*call_args, **call_kwargs)

    monkeypatch.setattr(register_inputs_stage, "run_flex_batch", interrupt_before_write)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        register_inputs(tm=tm, layout=layout, args=args, cache=cache)
    assert not (layout.InputTables / "preprocess_inputs.csv").exists()
    assert [row[0] for row in _register_done_rows(tm)] == ["copy_inputs_csv", "parse_and_link"]

    monkeypatch.setattr(register_inputs_stage, "run_flex_batch", original_run_flex_batch)
    register_inputs(tm=tm, layout=layout, args=args, cache=cache)

    with (layout.InputTables / "preprocess_inputs.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["pp_id"] for row in rows] == ["sample.all"]
    assert [row[0] for row in _register_done_rows(tm)] == [
        "copy_inputs_csv",
        "parse_and_link",
        "write_preprocess_csv",
    ]


def test_overlapping_sample_prefixes_cannot_share_link_or_preprocess_id(tmp_path):
    source_dir = tmp_path / "source-dir"
    _make_bam(source_dir / "A.B.chr1.bam", start=20)
    inputs_csv = tmp_path / "inputs.csv"
    _write_csv(
        inputs_csv,
        [
            {"sample_id": "A", "source_path": str(source_dir)},
            {"sample_id": "A.B", "source_path": str(source_dir)},
        ],
    )
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)

    with pytest.raises(ValueError, match="Multiple inputs map to BAM destination"):
        register_inputs(tm=tm, layout=layout, args=args, cache=cache)

    _assert_invalid_stage_has_no_new_outputs(layout, tm)


def test_missing_source_index_is_generated_only_under_run_root_and_reruns_idempotently(tmp_path):
    source = tmp_path / "source.bam"
    _make_bam(source, start=20)
    source_bai = Path(f"{source}.bai")
    source_bai.unlink()
    source_hash = _sha256(source)
    inputs_csv = tmp_path / "inputs.csv"
    _write_csv(inputs_csv, [{"sample_id": "sample", "source_path": str(source)}])
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)

    register_inputs(tm=tm, layout=layout, args=args, cache=cache)
    link = layout.Inputs / "sample.raw.undeduped.bam"
    link_bai = Path(f"{link}.bai")
    generated_hash = _sha256(link_bai)
    assert not source_bai.exists()
    assert _sha256(source) == source_hash

    register_inputs(tm=tm, layout=layout, args=args, cache=cache)
    assert _sha256(link_bai) == generated_hash
    assert not source_bai.exists()
    assert _sha256(source) == source_hash


def test_valid_directory_source_registers_each_immediate_bam(tmp_path):
    source_dir = tmp_path / "source-dir"
    bam1 = source_dir / "sample.chr1.bam"
    bam2 = source_dir / "sample.chr2.bam"
    _make_bam(bam1, start=20)
    _make_bam(bam2, start=40)
    source_hashes = {
        path.name: _sha256(path)
        for path in source_dir.iterdir()
        if path.is_file()
    }
    inputs_csv = tmp_path / "inputs.csv"
    _write_csv(inputs_csv, [{"sample_id": "sample", "source_path": str(source_dir)}])
    layout, args, tm, cache = _runtime(tmp_path, inputs_csv)

    register_inputs(tm=tm, layout=layout, args=args, cache=cache)

    for contig, source in (("chr1", bam1), ("chr2", bam2)):
        link = layout.Inputs / f"sample.{contig}.undeduped.bam"
        assert link.is_symlink() and link.resolve() == source.resolve()
        assert _sha256(Path(f"{link}.bai")) == _sha256(Path(f"{source}.bai"))
    with (layout.InputTables / "preprocess_inputs.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["pp_id"] for row in rows] == ["sample.chr1", "sample.chr2"]
    assert {
        path.name: _sha256(path)
        for path in source_dir.iterdir()
        if path.is_file()
    } == source_hashes
