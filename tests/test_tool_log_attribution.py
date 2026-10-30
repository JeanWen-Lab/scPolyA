from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pysam
import pytest

from logging_config import (
    ToolExecution,
    identity_scope,
    run_tool_command,
    setup_run_logging,
    stage_scope,
    task_scope,
)
from utils.polya_extract import extract_polya_bam
from utils.polya_extract_runtime import run_isolated_polya_extraction

BLOCK = re.compile(r"\[scpolya-tool\]\n(.*?)\n\[/scpolya-tool\]", re.S)


def _blocks(text):
    blocks = []
    for match in BLOCK.finditer(text):
        fields = {}
        for line in match.group(1).splitlines():
            key, _, value = line.partition("=")
            fields[key] = json.loads(value)
        blocks.append((match.start(), fields))
    return blocks


@pytest.fixture()
def session(tmp_path):
    session = setup_run_logging(
        log_dir=tmp_path / "logs",
        invocation_id="a" * 32,
        verbose=False,
        argv=("pytest",),
        config={},
        run_root=tmp_path,
    )
    yield session
    session.finish("succeeded")
    session.close()


# Each child prints its unit token, then waits until every concurrent child
# has started, so all executions provably overlap on the shared flat log.
CHILD = (
    "import pathlib, sys, time\n"
    "token, gate, n, code = sys.argv[1], pathlib.Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])\n"
    "(gate / token).touch()\n"
    "deadline = time.time() + 30\n"
    "while len(list(gate.iterdir())) < n and time.time() < deadline: time.sleep(0.01)\n"
    "print(f'RESULT {token}')\n"
    "sys.exit(code)\n"
)


def _run_unit(token, gate, n, code):
    with stage_scope("stage_x"), task_scope("task_x"), identity_scope(f"uid={token}"):
        return run_tool_command(
            [sys.executable, "-c", CHILD, token, str(gate), str(n), str(code)],
            tool="python",
            operation="shared",
            check=True,
            capture_output=True,
            text=True,
        )


def test_concurrent_shared_flat_log_stays_attributable(session, tmp_path):
    gate = tmp_path / "gate"
    gate.mkdir()
    tokens = [f"unit{i}" for i in range(4)]
    codes = {"unit3": 5}

    def worker(token):
        try:
            return _run_unit(token, gate, len(tokens), codes.get(token, 0)).returncode
        except subprocess.CalledProcessError as exc:
            return exc.returncode

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(worker, tokens)) == [0, 0, 0, 5]

    # Retry of the failed unit, alone.
    retry_gate = tmp_path / "retry_gate"
    retry_gate.mkdir()
    assert _run_unit("unit3", retry_gate, 1, 0).returncode == 0

    invocation_logs = sorted((session.tool_log_dir / "python").glob("shared*.log"))
    assert len(invocation_logs) == 5
    seen = []
    for path in invocation_logs:
        text = path.read_text()
        blocks = [fields for _, fields in _blocks(text)]
        assert [b["event"] for b in blocks] == ["START", "FINISH"], path.name
        start, finish = blocks
        token = start["identity"].removeprefix("uid=")
        for key in ("execution_id", "attempt", "stage", "task", "identity"):
            assert start[key] == finish[key]
        assert (start["stage"], start["task"]) == ("stage_x", "task_x")
        # Only this unit's own output; never another unit's record.
        assert f"RESULT {token}" in text
        assert not [t for t in tokens if t != token and f"RESULT {t}" in text], path.name
        seen.append((token, start["attempt"], finish["status"], finish["exit_code"],
                     "[omitted:" in text))
    assert sorted(seen) == [
        ("unit0", 1, "succeeded", 0, True),
        ("unit1", 1, "succeeded", 0, True),
        ("unit2", 1, "succeeded", 0, True),
        ("unit3", 1, "failed", 5, True),
        ("unit3", 2, "succeeded", 0, False),
    ]

    # The flat compatibility log holds every record, each self-identifying.
    flat = (session.log_dir / "python" / "shared.log").read_text()
    summaries = _blocks(flat)
    assert len(summaries) == 5
    previous_end = 0
    for offset, fields in summaries:
        assert fields["event"] == "SUMMARY"
        token = fields["identity"].removeprefix("uid=")
        assert f"RESULT {token}" in flat[previous_end:offset]
        previous_end = offset
    assert {(f["identity"], f["attempt"], f["status"]) for _, f in summaries} == {
        ("uid=unit0", 1, "succeeded"), ("uid=unit1", 1, "succeeded"),
        ("uid=unit2", 1, "succeeded"), ("uid=unit3", 1, "failed"),
        ("uid=unit3", 2, "succeeded"),
    }


def _write_bam(path: Path, n_tail: int) -> Path:
    header = {"HD": {"VN": "1.6", "SO": "coordinate"},
              "SQ": [{"SN": "chr1", "LN": 100000}]}
    with pysam.AlignmentFile(str(path), "wb", header=header) as bam:
        index = 0
        for start in range(100, 100 + 40 * (n_tail + 30), 40):
            for kind in ("tail_plus", "tail_minus", "short", "nonA", "lowq"):
                read = pysam.AlignedSegment()
                read.query_name = f"r{index}_{kind}"
                index += 1
                read.reference_id = 0
                read.reference_start = start
                read.mapping_quality = 1 if kind == "lowq" else 255
                if kind == "tail_minus":
                    read.flag = 16
                    read.query_sequence = "TTTTTTT" + "ACGT" * 5
                    read.cigartuples = [(4, 7), (0, 20)]
                else:
                    clip = {"tail_plus": "AAAAAAA", "short": "AAA",
                            "nonA": "AACCAAA", "lowq": "AAAAAAA"}[kind]
                    read.query_sequence = "ACGT" * 5 + clip
                    read.cigartuples = [(0, 20), (4, len(clip))]
                read.query_qualities = pysam.qualitystring_to_array("I" * len(read.query_sequence))
                if kind.startswith("tail") and index > 5 * n_tail:
                    read.mapping_quality = 0
                bam.write(read)
    return path


def _reads(path):
    with pysam.AlignmentFile(str(path), "rb") as bam:
        return [(r.query_name, r.reference_start, r.query_sequence, r.flag)
                for r in bam.fetch(until_eof=True)]


def test_concurrent_polya_units_keep_logs_and_numbers_separate(session, tmp_path):
    units = {f"chr{i}": _write_bam(tmp_path / f"in.chr{i}.bam", n_tail=i) for i in range(1, 5)}
    broken = tmp_path / "in.chrBAD.bam"
    broken.write_bytes(b"not a bam")
    out = {uid: tmp_path / "out" / f"unit.{uid}.polyA.bam" for uid in [*units, "chrBAD"]}

    def worker(uid):
        with stage_scope("extract_subsets"), task_scope("extract_polyA"), \
                identity_scope(f"uid={uid}"):
            try:
                return run_isolated_polya_extraction(
                    input_bam=units.get(uid, broken), output_bam=out[uid],
                    nonA_allowed=1, minpolyA=5, minMAPQ=10)
            except RuntimeError as exc:
                return exc

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = dict(zip(out, pool.map(worker, out)))
    assert isinstance(results["chrBAD"], RuntimeError)

    # Retry the failed unit with a valid input.
    units["chrBAD"] = _write_bam(tmp_path / "in.chrBAD.fixed.bam", n_tail=2)
    results["chrBAD"] = worker("chrBAD")

    # Numeric outputs equal a direct, unlogged extraction of the same input.
    for uid, stats in results.items():
        reference_bam = tmp_path / f"reference.{uid}.bam"
        expected = extract_polya_bam(units[uid], reference_bam, 1, 5, 10)
        assert stats == expected
        assert _reads(out[uid]) == _reads(reference_bam)
        assert stats.retained_reads > 0

    tool_dir = session.tool_log_dir / "scpolya-polya-extract"
    for uid in out:
        name = f"extract_polyA.{out[uid].name}"
        # Allocation order: NAME.log, then NAME.02.log, ...
        logs = sorted(tool_dir.glob(f"{name}*.log"), key=lambda p: (len(p.name), p.name))
        assert len(logs) == (2 if uid == "chrBAD" else 1)
        for attempt, path in enumerate(logs, start=1):
            text = path.read_text()
            blocks = [fields for _, fields in _blocks(text)]
            assert [b["event"] for b in blocks] == ["START", "FINISH"]
            assert {b["identity"] for b in blocks} == {f"uid={uid}"}
            assert {b["attempt"] for b in blocks} == {attempt}
            payloads = [json.loads(line) for line in text.splitlines()
                        if line.startswith('{"excluded_reads_nonA"')]
            if uid == "chrBAD" and attempt == 1:
                assert blocks[1]["status"] == "failed" and payloads == []
            else:
                assert blocks[1]["status"] == "succeeded"
                assert payloads == [results[uid].as_dict()]
        flat = (session.log_dir / "scpolya-polya-extract" / f"{name}.log").read_text()
        assert {f["identity"] for _, f in _blocks(flat)} == {f"uid={uid}"}
    assert not (session.log_dir / "scpolya-polya-extract" / "extract_polyA.log").exists()


def _sequential(log, op="seq"):
    return run_tool_command([sys.executable, "-c", "print('SEQ')"], tool="python",
                            operation=op, compatibility_log=log, check=True,
                            capture_output=True, text=True)


def test_unentered_execution_does_not_mark_later_runs_shared(session, tmp_path):
    log = tmp_path / "logs" / "python" / "leak.log"
    ToolExecution(tool="python", operation="leak", command=["true"], compatibility_log=log)
    _sequential(log)
    text = sorted((session.tool_log_dir / "python").glob("seq*.log"))[-1].read_text()
    assert "[omitted:" not in text


def test_path_aliases_share_one_registry_entry(session, tmp_path):
    real = tmp_path / "logs" / "python"
    real.mkdir(parents=True, exist_ok=True)
    alias = real / ".." / "python" / "alias.log"
    with ToolExecution(tool="python", operation="a", command=["true"],
                       compatibility_log=real / "alias.log") as first:
        with ToolExecution(tool="python", operation="b", command=["true"],
                           compatibility_log=alias) as second:
            pass
    assert first._shared_compatibility and second._shared_compatibility
