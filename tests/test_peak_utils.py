from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from utils.peak_utils import assess_peak_mispriming


@dataclass
class _Read:
    query_alignment_sequence: str = "AAAA"
    is_unmapped: bool = False
    is_reverse: bool = False

    @property
    def query_sequence(self):
        return self.query_alignment_sequence


class _AlignmentFile:
    def __init__(self, reads=(), error=None):
        self.reads = list(reads)
        self.error = error
        self.fetch_calls = []
        self.closed = False

    def fetch(self, chrom, start, end):
        self.fetch_calls.append((chrom, start, end))
        if self.error is not None:
            raise self.error
        return iter(self.reads)

    def close(self):
        self.closed = True


def _write_peak(path: Path, contig: str):
    path.write_text(f"{contig}\t10\t20\tpeak\t5\t+\n")


@pytest.mark.parametrize("contig", ["22", "chr22", "022", "NA"])
def test_assess_peak_mispriming_fetches_string_contigs(
    tmp_path, monkeypatch, contig
):
    combined = tmp_path / "combined.bed"
    resolved = tmp_path / "resolved.bed"
    _write_peak(combined, contig)
    original = _AlignmentFile([_Read(), _Read()])
    polya = _AlignmentFile([_Read()])

    def alignment_file(path, mode):
        assert mode == "rb"
        return polya if Path(path).name == "polya.bam" else original

    monkeypatch.setattr("utils.peak_utils.pysam.AlignmentFile", alignment_file)

    assess_peak_mispriming(
        combined,
        resolved,
        merge_distance=0,
        strand="+",
        misprime_A_count=4,
        misprime_in=4,
        polyA_bam=tmp_path / "polya.bam",
        original_bam=tmp_path / "original.bam",
    )

    assert original.fetch_calls == [(contig, 10, 20)]
    assert polya.fetch_calls == [(contig, 10, 20)]
    assert isinstance(original.fetch_calls[0][0], str)
    output = pd.read_csv(
        resolved,
        sep="\t",
        header=None,
        dtype={0: str},
        keep_default_na=False,
        na_filter=False,
    )
    assert output.iloc[0, 0] == contig
    assert output.iloc[0, 6] == 1
    assert output.iloc[0, 7] == 1.0
    assert output.iloc[0, 8] == 2
    assert original.closed
    assert polya.closed


@pytest.mark.parametrize(
    ("error", "message_prefix"),
    [
        (ValueError("unknown reference"), "Error fetching reads"),
        (
            subprocess.CalledProcessError(2, ["samtools", "view"]),
            "Unexpected error fetching reads",
        ),
    ],
)
def test_assess_peak_mispriming_propagates_original_fetch_errors(
    tmp_path, monkeypatch, error, message_prefix
):
    combined = tmp_path / "combined.bed"
    resolved = tmp_path / "resolved.bed"
    _write_peak(combined, "22")
    original = _AlignmentFile(error=error)
    polya = _AlignmentFile([_Read()])

    def alignment_file(path, mode):
        return polya if Path(path).name == "polya.bam" else original

    monkeypatch.setattr("utils.peak_utils.pysam.AlignmentFile", alignment_file)
    error_messages = []
    monkeypatch.setattr("utils.peak_utils.logger_utils.error", error_messages.append)

    with pytest.raises(type(error)) as exc_info:
        assess_peak_mispriming(
            combined,
            resolved,
            merge_distance=0,
            strand="+",
            misprime_A_count=4,
            misprime_in=4,
            polyA_bam=tmp_path / "polya.bam",
            original_bam=tmp_path / "original.bam",
        )

    assert exc_info.value is error
    assert any(message_prefix in message for message in error_messages)
    assert any("22:10-20" in message for message in error_messages)
    assert all("Setting original_total_reads_in_peak to 0" not in message for message in error_messages)
    assert original.closed
    assert polya.closed
    assert not resolved.exists()


def test_assess_peak_mispriming_closes_polya_bam_when_original_open_fails(
    tmp_path, monkeypatch
):
    combined = tmp_path / "combined.bed"
    resolved = tmp_path / "resolved.bed"
    _write_peak(combined, "22")
    polya = _AlignmentFile([_Read()])
    error = subprocess.CalledProcessError(2, ["samtools", "view"])

    def alignment_file(path, mode):
        if Path(path).name == "polya.bam":
            return polya
        raise error

    monkeypatch.setattr("utils.peak_utils.pysam.AlignmentFile", alignment_file)
    error_messages = []
    monkeypatch.setattr("utils.peak_utils.logger_utils.error", error_messages.append)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        assess_peak_mispriming(
            combined,
            resolved,
            merge_distance=0,
            strand="+",
            misprime_A_count=4,
            misprime_in=4,
            polyA_bam=tmp_path / "polya.bam",
            original_bam=tmp_path / "original.bam",
        )

    assert exc_info.value is error
    assert polya.closed
    assert any("Failed to open original BAM file" in message for message in error_messages)
    assert not resolved.exists()
