from __future__ import annotations

import collections
import gzip
import logging
import os
import shutil
import subprocess
from itertools import zip_longest
from pathlib import Path

import pysam

from logging_config import run_tool_command
from utils.assignment_diagnostics import AssignmentDiagnostics, read_featurecounts_summary
from utils.read_eligibility import ReadRule

SIDECAR_MAGIC = "#scpolya_feature_sidecar\tv2\tfields=query_name,flag,feature"
SIDECAR_GZIP_COMPRESSLEVEL = 1
SIDECAR_WRITE_BUFFER_BYTES = 8 * 1024 * 1024
DEFAULT_FEATURECOUNTS_ANNOTATED_BAM_THREADS = 1
FEATURECOUNTS_ANNOTATED_BAM_THREADS_ENV = "SCPOLYA_FEATURECOUNTS_ANNOTATED_BAM_THREADS"
DEFAULT_MAX_REORDER_BUFFER_RECORDS = 1_000_000
MAX_REORDER_BUFFER_ENV = "SCPOLYA_SIDECAR_MAX_REORDER_BUFFER"
TRUST_FEATURECOUNTS_T1_ORDER_ENV = "SCPOLYA_SIDECAR_TRUST_FEATURECOUNTS_T1"

logger = logging.getLogger(__name__)


class FeatureSidecarOrderError(ValueError):
    """Raised when featureCounts preserves records but not source BAM order."""


def build_feature_assignment_sidecar(
    *,
    input_bam: Path,
    annotation_gtf: Path,
    output_sidecar: Path,
    cell_barcode_tag: str,
    thread_count: int,
    feature_type: str,
    group_attr: str,
    feature_counts_exec: str = "featureCounts",
    count_features: bool = False,
    count_fragments: bool | None = None,
    read_rule: ReadRule | None = None,
    diagnostics: AssignmentDiagnostics | None = None,
    diagnostics_paths: tuple[Path, Path] | None = None,
) -> None:
    """Annotate a BAM with featureCounts and persist record-order feature assignments as a sidecar.

    A unified ``read_rule`` adds its featureCounts options and then blanks the
    assignment of every record the shared rule rejects, so featureCounts never
    decides read eligibility on its own. Without it the historical command
    and sidecar are unchanged.

    With ``diagnostics`` every annotated record is also attributed to the
    PAS windows it overlaps; the per-PAS table and the record summary
    (including the featureCounts summary) are written to ``diagnostics_paths``.
    """
    if count_fragments is not None:
        count_features = count_fragments
    if read_rule is not None and not read_rule.unified:
        read_rule = None

    featurecounts_threads = _featurecounts_annotated_bam_threads()
    if featurecounts_threads != thread_count:
        logger.info(
            "Using featureCounts -T %s for annotated BAM sidecar generation "
            "(requested thread_count=%s). Set %s to override.",
            featurecounts_threads,
            thread_count,
            FEATURECOUNTS_ANNOTATED_BAM_THREADS_ENV,
        )

    output_sidecar.parent.mkdir(parents=True, exist_ok=True)
    output_sidecar.unlink(missing_ok=True)

    temp_root = output_sidecar.parent / f"{input_bam.stem}.{feature_type}.featureCounts_tmp"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)

    temp_counts = temp_root / "counts.txt"
    temp_fc_bam = temp_root / f"{input_bam.name}.featureCounts.bam"
    final_fc_bam = temp_root / f"{input_bam.stem}.featureCounts.bam"

    fc_cmd = [
        feature_counts_exec,
        "-a", str(annotation_gtf),
        "-o", str(temp_counts),
        "-t", feature_type,
        "-g", group_attr,
        "-F", "GTF",
        "-s", "1",
        "-R", "BAM",
        "-T", str(featurecounts_threads),
        str(input_bam),
    ]
    if count_features:
        fc_cmd.insert(-1, "-f")
    if read_rule is not None:
        fc_cmd[-1:-1] = read_rule.featurecounts_args()

    try:
        run_tool_command(
            fc_cmd,
            tool="featureCounts",
            operation=f"sidecar.{feature_type}.{input_bam.name}",
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if not temp_fc_bam.exists():
            raise FileNotFoundError(f"featureCounts did not produce annotated BAM: {temp_fc_bam}")
        shutil.move(str(temp_fc_bam), str(final_fc_bam))
        if featurecounts_threads == 1 and _trust_featurecounts_t1_order():
            logger.info(
                "Writing feature sidecar directly from single-thread featureCounts annotated BAM; "
                "downstream UMI counting will still validate sidecar/BAM row identity."
            )
            _write_ordered_sidecar_from_annotated_bam(
                annotated_bam=final_fc_bam,
                output_sidecar=output_sidecar,
                read_rule=read_rule,
                diagnostics=diagnostics,
            )
        else:
            _write_sidecar_from_featurecounts_bam(
                input_bam=input_bam,
                annotated_bam=final_fc_bam,
                output_sidecar=output_sidecar,
                read_rule=read_rule,
                diagnostics=diagnostics,
            )
        if diagnostics is not None:
            summary_path = temp_counts.with_name(f"{temp_counts.name}.summary")
            diagnostics.write(
                *diagnostics_paths,
                featurecounts_summary=read_featurecounts_summary(summary_path),
            )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def _write_sidecar_from_featurecounts_bam(
    *,
    input_bam: Path,
    annotated_bam: Path,
    output_sidecar: Path,
    read_rule: ReadRule | None = None,
    diagnostics: AssignmentDiagnostics | None = None,
) -> None:
    temp_sidecar = output_sidecar.with_name(f"{output_sidecar.name}.tmp")
    temp_sidecar.unlink(missing_ok=True)
    try:
        try:
            _write_record_order_sidecar_fast(
                input_bam=input_bam,
                annotated_bam=annotated_bam,
                output_sidecar=temp_sidecar,
                read_rule=read_rule,
                diagnostics=diagnostics,
            )
        except FeatureSidecarOrderError as exc:
            if diagnostics is not None:
                diagnostics.reset()
            temp_sidecar.unlink(missing_ok=True)
            logger.warning(
                "featureCounts annotated BAM order differs from source BAM; "
                "falling back to streaming reorder for sidecar generation: %s",
                exc,
            )
            _write_record_order_sidecar_reordered(
                input_bam=input_bam,
                annotated_bam=annotated_bam,
                output_sidecar=temp_sidecar,
                read_rule=read_rule,
                diagnostics=diagnostics,
            )
        temp_sidecar.replace(output_sidecar)
    except Exception:
        temp_sidecar.unlink(missing_ok=True)
        raise


def _write_ordered_sidecar_from_annotated_bam(
    *,
    annotated_bam: Path,
    output_sidecar: Path,
    read_rule: ReadRule | None = None,
    diagnostics: AssignmentDiagnostics | None = None,
) -> None:
    temp_sidecar = output_sidecar.with_name(f"{output_sidecar.name}.tmp")
    temp_sidecar.unlink(missing_ok=True)
    try:
        with (
            pysam.AlignmentFile(str(annotated_bam), "rb") as annotated_bam_in,
            _SidecarWriter(temp_sidecar) as out,
        ):
            out.write_header()
            for annotated_read in annotated_bam_in:
                out.write_record(
                    annotated_read.query_name,
                    annotated_read.flag,
                    _feature_from_annotated_read(annotated_read, read_rule, diagnostics),
                )
        temp_sidecar.replace(output_sidecar)
    except Exception:
        temp_sidecar.unlink(missing_ok=True)
        raise


def _write_record_order_sidecar_fast(
    *,
    input_bam: Path,
    annotated_bam: Path,
    output_sidecar: Path,
    read_rule: ReadRule | None = None,
    diagnostics: AssignmentDiagnostics | None = None,
) -> None:
    with (
        pysam.AlignmentFile(str(input_bam), "rb") as source_bam,
        pysam.AlignmentFile(str(annotated_bam), "rb") as annotated_bam_in,
        _SidecarWriter(output_sidecar) as out,
    ):
        out.write_header()
        for row_number, (source_read, annotated_read) in enumerate(
            zip_longest(source_bam, annotated_bam_in), start=1
        ):
            if source_read is None:
                raise ValueError(
                    f"featureCounts produced extra annotated records after source BAM ended at sidecar row {row_number}"
                )
            if annotated_read is None:
                raise ValueError(
                    f"featureCounts annotated BAM ended before source BAM at sidecar row {row_number}"
                )
            if source_read.query_name != annotated_read.query_name or source_read.flag != annotated_read.flag:
                raise FeatureSidecarOrderError(
                    "featureCounts annotated BAM order differs from source BAM at sidecar row "
                    f"{row_number}: source={source_read.query_name}/{source_read.flag}, "
                    f"annotated={annotated_read.query_name}/{annotated_read.flag}"
                )
            feature = _feature_from_annotated_read(annotated_read, read_rule, diagnostics)
            out.write_record(source_read.query_name, source_read.flag, feature)


def _write_record_order_sidecar_reordered(
    *,
    input_bam: Path,
    annotated_bam: Path,
    output_sidecar: Path,
    read_rule: ReadRule | None = None,
    diagnostics: AssignmentDiagnostics | None = None,
) -> None:
    max_buffer_records = _max_reorder_buffer_records()
    buffered: dict[tuple, collections.deque[str]] = {}
    buffered_count = 0
    max_observed_buffer = 0

    def pop_buffered(key: tuple) -> str | None:
        nonlocal buffered_count
        queue = buffered.get(key)
        if not queue:
            return None
        feature = queue.popleft()
        buffered_count -= 1
        if not queue:
            del buffered[key]
        return feature

    def add_buffered(read: pysam.AlignedSegment) -> None:
        nonlocal buffered_count, max_observed_buffer
        key = _record_identity(read)
        feature = _feature_from_annotated_read(read, read_rule, diagnostics)
        buffered.setdefault(key, collections.deque()).append(feature)
        buffered_count += 1
        max_observed_buffer = max(max_observed_buffer, buffered_count)
        if buffered_count > max_buffer_records:
            raise ValueError(
                "featureCounts annotated BAM order differs too far from source BAM for streaming reorder: "
                f"buffered {buffered_count:,} annotated records; limit={max_buffer_records:,}. "
                f"Set {MAX_REORDER_BUFFER_ENV} to a larger integer only after confirming memory headroom."
            )

    with (
        pysam.AlignmentFile(str(input_bam), "rb") as source_bam,
        pysam.AlignmentFile(str(annotated_bam), "rb") as annotated_bam_in,
        _SidecarWriter(output_sidecar) as out,
    ):
        annotated_iter = iter(annotated_bam_in)
        out.write_header()
        for row_number, source_read in enumerate(source_bam, start=1):
            source_key = _record_identity(source_read)
            feature = pop_buffered(source_key)
            if feature is None:
                for annotated_read in annotated_iter:
                    annotated_key = _record_identity(annotated_read)
                    if annotated_key == source_key:
                        feature = _feature_from_annotated_read(annotated_read, read_rule, diagnostics)
                        break
                    add_buffered(annotated_read)
                else:
                    raise ValueError(
                        "featureCounts annotated BAM ended before source BAM could be matched at sidecar row "
                        f"{row_number}: source={source_read.query_name}/{source_read.flag}; "
                        f"buffered_records={buffered_count}"
                    )
            out.write_record(source_read.query_name, source_read.flag, feature)

        try:
            extra_read = next(annotated_iter)
        except StopIteration:
            extra_read = None
        if extra_read is not None:
            raise ValueError(
                "featureCounts produced extra annotated records after source BAM ended: "
                f"{extra_read.query_name}/{extra_read.flag}"
            )
        if buffered:
            example_key = next(iter(buffered))
            raise ValueError(
                "featureCounts annotated BAM contained records that were never matched to source BAM; "
                f"unmatched_buffered_records={buffered_count}; example_key={example_key!r}"
            )

    logger.info(
        "Wrote feature sidecar through streaming reorder fallback: input=%s annotated=%s sidecar=%s "
        "max_buffered_records=%s",
        input_bam,
        annotated_bam,
        output_sidecar,
        max_observed_buffer,
    )


def _record_identity(read: pysam.AlignedSegment) -> tuple:
    return (
        read.query_name,
        read.flag,
        read.reference_id,
        read.reference_start,
        read.mapping_quality,
        tuple(read.cigartuples or ()),
        read.next_reference_id,
        read.next_reference_start,
        read.template_length,
        read.query_sequence or "",
    )


def _feature_from_annotated_read(
    read: pysam.AlignedSegment,
    read_rule: ReadRule | None = None,
    diagnostics: AssignmentDiagnostics | None = None,
) -> str:
    feature = read.get_tag("XT") if read.has_tag("XT") else ""
    if feature and read_rule is not None and read_rule.rejection_reason(read) is not None:
        feature = ""
    if diagnostics is not None:
        diagnostics.observe(read, feature)
    return feature


class _SidecarWriter:
    def __init__(self, output_sidecar: Path) -> None:
        self.output_sidecar = output_sidecar
        self._handle = None
        self._chunks: list[bytes] = []
        self._buffered_bytes = 0

    def __enter__(self) -> "_SidecarWriter":
        self._handle = gzip.open(
            self.output_sidecar,
            "wb",
            compresslevel=SIDECAR_GZIP_COMPRESSLEVEL,
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.flush()
        finally:
            if self._handle is not None:
                self._handle.close()

    def write_header(self) -> None:
        self._write_text(f"{SIDECAR_MAGIC}\n")

    def write_record(self, query_name: str, flag: int, feature: str) -> None:
        self._write_text(f"{query_name}\t{flag}\t{feature}\n")

    def flush(self) -> None:
        if self._chunks and self._handle is not None:
            self._handle.write(b"".join(self._chunks))
            self._chunks.clear()
            self._buffered_bytes = 0

    def _write_text(self, text: str) -> None:
        data = text.encode("ascii")
        self._chunks.append(data)
        self._buffered_bytes += len(data)
        if self._buffered_bytes >= SIDECAR_WRITE_BUFFER_BYTES:
            self.flush()


def _max_reorder_buffer_records() -> int:
    raw = os.environ.get(MAX_REORDER_BUFFER_ENV)
    if not raw:
        return DEFAULT_MAX_REORDER_BUFFER_RECORDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{MAX_REORDER_BUFFER_ENV} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{MAX_REORDER_BUFFER_ENV} must be >= 1, got {value}")
    return value


def _trust_featurecounts_t1_order() -> bool:
    raw = os.environ.get(TRUST_FEATURECOUNTS_T1_ORDER_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _featurecounts_annotated_bam_threads() -> int:
    raw = os.environ.get(FEATURECOUNTS_ANNOTATED_BAM_THREADS_ENV)
    if not raw:
        return DEFAULT_FEATURECOUNTS_ANNOTATED_BAM_THREADS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{FEATURECOUNTS_ANNOTATED_BAM_THREADS_ENV} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{FEATURECOUNTS_ANNOTATED_BAM_THREADS_ENV} must be >= 1, got {value}")
    return value
