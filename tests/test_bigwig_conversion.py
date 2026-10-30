from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pyBigWig
import pytest

from utils import io_utils


def test_real_unsorted_conversion_keeps_all_chromosomes_and_input(tmp_path):
    if not shutil.which('bedGraphToBigWig'):
        pytest.skip('Real UCSC converter is required for conversion/readback evidence')
    bedgraph = tmp_path / 'coverage.bedgraph'
    original = 'chr22\t5\t8\t2\nchr2\t3\t5\t4\nchr10\t0\t2\t9\nchr2\t0\t3\t1\n'
    bedgraph.write_text(original)
    sizes = tmp_path / 'sizes'
    sizes.write_text('chr2\t10\nchr10\t10\nchr22\t10\n')
    out = tmp_path / 'coverage.bw'
    io_utils.convert_bedgraph_to_bigwig(bedgraph, out, sizes, tmp_path / 'convert.log')
    assert bedgraph.read_text() == original
    with pyBigWig.open(str(out)) as bw:
        assert bw.chroms() == {'chr2': 10, 'chr10': 10, 'chr22': 10}
        np.testing.assert_array_equal(bw.values('chr2', 0, 5), [1, 1, 1, 4, 4])
        np.testing.assert_array_equal(bw.values('chr10', 0, 2), [9, 9])
        np.testing.assert_array_equal(bw.values('chr22', 5, 8), [2, 2, 2])
    assert not list(tmp_path.glob('.bigwig-*'))


@pytest.mark.parametrize('text', [
    'chrUnknown\t0\t1\t2\n', 'chr2\t-1\t1\t2\n', 'chr2\t0\t11\t2\n',
    'chr2\t1\t1\t2\n', 'chr2\t0\t1\tnan\n', 'chr2\t0\t1\tinf\n',
    'chr2\t1\t5\t2\nchr2\t2\t3\t2\n', '', 'chr2\t0\t1\t2\textra\n',
])
def test_invalid_coverage_cleans_previous_output(tmp_path, text):
    bedgraph, sizes, out = tmp_path / 'in.bg', tmp_path / 'sizes', tmp_path / 'out.bw'
    bedgraph.write_text(text)
    sizes.write_text('chr2\t10\n')
    out.write_bytes(b'old-output-must-not-look-current')
    with pytest.raises(ValueError):
        io_utils.convert_bedgraph_to_bigwig(bedgraph, out, sizes, tmp_path / 'log')
    assert not out.exists() and not list(tmp_path.glob('.bigwig-*'))
    assert bedgraph.read_text() == text


def test_converter_failure_cleans_partial_output(tmp_path, monkeypatch):
    bedgraph, sizes, out = tmp_path / 'in.bg', tmp_path / 'sizes', tmp_path / 'out.bw'
    bedgraph.write_text('chr2\t0\t1\t-2\n')  # signed display tracks remain valid input
    sizes.write_text('chr2\t10\n')
    original = io_utils.run_tool_command
    def fail(command, **kwargs):
        if command[0] == 'bedGraphToBigWig':
            Path(command[-1]).write_bytes(b'partial')
            raise subprocess.CalledProcessError(1, command, stderr='test converter failure')
        return original(command, **kwargs)
    monkeypatch.setattr(io_utils, 'run_tool_command', fail)
    with pytest.raises(subprocess.CalledProcessError):
        io_utils.convert_bedgraph_to_bigwig(bedgraph, out, sizes, tmp_path / 'log')
    assert not out.exists() and not list(tmp_path.glob('.bigwig-*'))
