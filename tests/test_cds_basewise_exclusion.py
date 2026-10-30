from pathlib import Path
import shutil
import subprocess

import pytest

from utils.bedtools_utils import bedtools_subtract_coverage, bedtools_intersect
from utils.annotation_utils import exclude_cds_from_coverage


@pytest.fixture
def bedtools_available():
    if shutil.which("bedtools") is None:
        pytest.skip("bedtools executable required for real interval regression")


@pytest.mark.parametrize("mask,expected", [
    ("chr1\t100\t103\n", "chr1\t103\t200\t5\n"),
    ("chr1\t90\t210\n", ""),
    ("chr1\t20\t50\n", "chr1\t100\t200\t5\n"),
    ("chr1\t120\t130\nchr1\t150\t160\n",
     "chr1\t100\t120\t5\nchr1\t130\t150\t5\nchr1\t160\t200\t5\n"),
    ("chr1\t120\t150\nchr1\t140\t160\n",
     "chr1\t100\t120\t5\nchr1\t160\t200\t5\n"),
    ("chr1\t200\t210\n", "chr1\t100\t200\t5\n"),
])
def test_real_subtraction(tmp_path, bedtools_available, mask, expected):
    coverage, cds, output = (tmp_path / name for name in ("coverage", "cds", "out"))
    coverage.write_text("chr1\t100\t200\t5\n")
    cds.write_text(mask)
    bedtools_subtract_coverage(coverage, cds, output)
    assert output.read_text() == expected


@pytest.mark.parametrize("strand", ["+", "-"])
def test_representation_invariance_and_unchanged_unstranded_policy(tmp_path, bedtools_available, strand):
    cds = tmp_path / "cds"
    cds.write_text(f"chr1\t100\t103\tgene\t0\t{strand}\n")
    outputs = []
    for index, data in enumerate(("chr1\t100\t200\t5\n", "chr1\t100\t103\t5\nchr1\t103\t200\t5\n")):
        source, target = tmp_path / f"in{index}", tmp_path / f"out{index}"
        source.write_text(data)
        bedtools_subtract_coverage(source, cds, target)
        outputs.append(target.read_text())
    assert outputs == ["chr1\t103\t200\t5\n"] * 2


@pytest.mark.parametrize("wrapper", [bedtools_intersect, exclude_cds_from_coverage])
def test_compatibility_helpers_use_same_semantics(tmp_path, bedtools_available, wrapper):
    source, cds, out = (tmp_path / name for name in ("in", "cds", "out"))
    source.write_text("022\t100\t200\t2.25\n")
    cds.write_text("022\t100\t103\n")
    wrapper(str(source), str(cds), str(out))
    assert out.read_text() == "022\t103\t200\t2.25\n"


def test_failure_keeps_previous_complete_output_not_partial(tmp_path, monkeypatch):
    source, cds, out = (tmp_path / name for name in ("in", "cds", "out"))
    source.write_text("chr1\t100\t200\t5\n")
    cds.write_text("chr1\t100\t103\n")
    out.write_text("previous-complete-result\n")
    failure = subprocess.CalledProcessError(2, ["bedtools", "subtract"], stderr="bad input")

    def failed_command(*args, **kwargs):
        kwargs["stdout"].write("partial\n")
        raise failure

    monkeypatch.setattr("utils.bedtools_utils.run_tool_command", failed_command)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        bedtools_subtract_coverage(source, cds, out)
    assert exc.value is failure
    assert out.read_text() == "previous-complete-result\n"
    assert not list(tmp_path.glob(".out.*.tmp"))
    out.unlink()
    with pytest.raises(subprocess.CalledProcessError):
        bedtools_subtract_coverage(source, cds, out)
    assert not out.exists()


@pytest.mark.parametrize("target", ["in", "cds"])
def test_no_input_overwrite(tmp_path, target):
    source, cds = tmp_path / "in", tmp_path / "cds"
    source.write_text("source\n")
    cds.write_text("mask\n")
    with pytest.raises(ValueError, match="overwrite an input"):
        bedtools_subtract_coverage(source, cds, tmp_path / target)
    assert source.read_text() == "source\n"
    assert cds.read_text() == "mask\n"
