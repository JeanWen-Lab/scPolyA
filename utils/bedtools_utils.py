import subprocess
import os
import tempfile
from pathlib import Path

from logging_config import (
    LOG_MAIN,
    LOG_UTILS,
    LoggersWrapper,
    ToolExecution,
    get_logger,
    run_tool_command,
)

suffix = lambda x: f" (bedtools_utils:{x})"
logger_main = get_logger(LOG_MAIN)
logger_utils = get_logger(LOG_UTILS)
loggers = LoggersWrapper(main=logger_main, sub=logger_utils)


def bedtools_generate_coverage(in_bam, out_bedgraph, local_log, *, strand, read_rule=None):
    """Strand-filtered coverage; a unified read rule filters reads first.

    ``read_rule=None`` (or legacy) is the historical command over every read.
    """
    strand_token = {"plus": "+", "minus": "-"}.get(strand)
    if strand_token is None:
        raise ValueError(
            f"Strand-filtered coverage requires strand='plus' or 'minus', got {strand!r}"
        )
    if read_rule is not None and read_rule.unified:
        _filtered_genomecov(in_bam, out_bedgraph, local_log, strand_token, read_rule)
        return
    command_args = [
        'bedtools', 'genomecov', '-bg', '-split', '-strand', strand_token,
        '-ibam', str(in_bam)
    ]

    # TODO: bedtools path
    logger_utils.info(f"Raw coverage generation started.")
    with open(str(out_bedgraph), 'w') as outfile:
        with open(local_log, 'a') as f:
            try:
                run_tool_command(
                    command_args,
                    tool="bedtools",
                    operation=f"genomecov.{Path(out_bedgraph).name}",
                    compatibility_log=local_log,
                    stdout=outfile,
                    stderr=f,
                    text=True,
                    check=True
                )
                logger_utils.info(f"Coverage BedGraph saved to '{out_bedgraph}'.")
            except Exception as e:
                loggers.raise_error(f"Failed to calculate coverage: {e}")



def _filtered_genomecov(in_bam, out_bedgraph, local_log, strand_token, read_rule):
    """samtools view <read rule> | bedtools genomecov, published atomically."""
    out_bedgraph = Path(out_bedgraph)
    filter_args = [
        'samtools', 'view', '-u', *read_rule.samtools_view_filter_args(), str(in_bam)
    ]
    coverage_args = [
        'bedtools', 'genomecov', '-bg', '-split', '-strand', strand_token,
        '-ibam', 'stdin'
    ]
    temporary = out_bedgraph.with_name(f".{out_bedgraph.name}.tmp")
    logger_utils.info(
        f"Raw coverage generation started with read rule "
        f"{read_rule.mode}/{read_rule.multimapper_policy} (MAPQ>={read_rule.min_mapq})."
    )
    try:
        with open(local_log, 'a') as log, open(temporary, 'w') as outfile, ToolExecution(
            tool="samtools",
            operation=f"read-rule-filter.{out_bedgraph.name}",
            command=filter_args,
            compatibility_log=local_log,
        ) as filter_execution:
            producer = subprocess.Popen(filter_args, stdout=subprocess.PIPE, stderr=log)
            try:
                run_tool_command(
                    coverage_args,
                    tool="bedtools",
                    operation=f"genomecov.{out_bedgraph.name}",
                    compatibility_log=local_log,
                    stdin=producer.stdout,
                    stdout=outfile,
                    stderr=log,
                    text=True,
                    check=True,
                )
            finally:
                producer.stdout.close()
                producer.wait()
            filter_execution.record_result(
                subprocess.CompletedProcess(filter_args, producer.returncode)
            )
            if producer.returncode != 0:
                raise subprocess.CalledProcessError(producer.returncode, filter_args)
        temporary.replace(out_bedgraph)
        logger_utils.info(f"Coverage BedGraph saved to '{out_bedgraph}'.")
    except Exception as e:
        temporary.unlink(missing_ok=True)
        loggers.raise_error(f"Failed to calculate coverage: {e}")

CDS_EXCLUSION_SEMANTICS = "basewise-unstranded-cds-union-v1"


def bedtools_subtract_coverage(in_bedgraph, cds_bed, out_bedgraph):
    """Remove CDS bases, retaining the depth of every non-CDS fragment.

    The reference set and strand policy are unchanged: all supplied CDS
    intervals mask each coverage strand. This fixes whole-row exclusion, not
    transcript selection or antisense-CDS policy. Publish only a complete result.
    """
    in_bedgraph, cds_bed, out_bedgraph = map(Path, (in_bedgraph, cds_bed, out_bedgraph))
    if out_bedgraph.resolve() in {in_bedgraph.resolve(), cds_bed.resolve()}:
        raise ValueError("CDS subtraction output must not overwrite an input")
    out_bedgraph.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        command_args = [
            "bedtools", "subtract", "-a", str(in_bedgraph), "-b", str(cds_bed)
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", dir=out_bedgraph.parent,
            prefix=f".{out_bedgraph.name}.", suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            run_tool_command(
                command_args, tool="bedtools",
                operation=f"subtract-cds.{out_bedgraph.name}",
                stdout=output, check=True,
            )
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(out_bedgraph)
        logger_utils.info(f"CDS bases subtracted from coverage into '{out_bedgraph}'.")
    except Exception:
        logger_utils.error(f"CDS subtraction failed for '{in_bedgraph}'; no result published.")
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def bedtools_intersect(in_bedgraph, in_bedgraph_2, out_bedgraph):
    """Compatibility entry point for the corrected CDS coverage operation."""
    return bedtools_subtract_coverage(in_bedgraph, in_bedgraph_2, out_bedgraph)
