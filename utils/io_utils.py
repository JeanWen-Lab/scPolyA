import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TypeVar, Literal, Optional, Any, Dict

import gffutils
import pandas as pd

from logging_config import (
    LOG_MAIN,
    LOG_UTILS,
    LoggersWrapper,
    get_logger,
    run_tool_command,
    suffix_scope,
)

TExc = TypeVar("TExc", bound=BaseException)
suffix = lambda x: f" (io_utils:{x})"
logger_main = get_logger(LOG_MAIN)
logger_utils = get_logger(LOG_UTILS)
loggers = LoggersWrapper(main=logger_main, sub=logger_utils)


def convert_gtf_to_bed(gene_gtf: str, gene_bed: str) -> None:
    with suffix_scope(suffix("convert_gtf_to_bed")):
        logger_utils.info(f"Converting GTF '{gene_gtf}' to BED '{gene_bed}' (excluding 'Gm' genes)...")

        try:
            with open(gene_gtf, 'r') as gtf, open(gene_bed, 'w') as bed:
                for line_num, line in enumerate(gtf, 1):
                    if line.startswith('#'):
                        continue

                    fields = line.strip().split('\t')
                    if len(fields) < 9:
                        logger_utils.warning(f"Skipping malformed GTF line {line_num}")
                        continue

                    # GTF fields: chrom, source, feature, start, end, score, strand, frame, attributes
                    chrom, _, feature, start, end, _, strand, _, attributes = fields

                    if feature != 'gene':
                        continue

                    # Parse attributes slightly more robustly
                    attrs = {}
                    # Remove trailing newline/semicolons and split
                    attr_list = [x.strip() for x in attributes.strip().strip(';').split(';') if x.strip()]

                    for attr in attr_list:
                        # Split only on the first space
                        parts = attr.split(' ', 1)
                        if len(parts) == 2:
                            key, value = parts
                            attrs[key] = value.strip('"')

                    gene_id = attrs.get('gene_id', 'NA')
                    gene_name = attrs.get('gene_name', 'NA')
                    if gene_name == 'NA':
                        # Fallback to gene_id
                        gene_name = gene_id
                        logger_utils.warning(f"Line {line_num}: 'gene_name' missing, using 'gene_id' ({gene_id}).")

                    # Filter exclusion logic
                    if gene_name.startswith("Gm"):
                        continue

                    # Coordinate conversion: GTF (1-based) -> BED (0-based)
                    try:
                        bed_start = int(start) - 1
                        bed_end = int(end)
                    except ValueError:
                        logger_utils.warning(f"Line {line_num}: Invalid coordinates start={start}, end={end}")
                        continue

                    if gene_id == 'NA':
                        logger_utils.warning(
                            f"Line {line_num}: gene feature lacks a true gene_id; skipping identity-unsafe row."
                        )
                        continue

                    # BED6 plus a seventh identity column. BED consumers that only
                    # need coordinates explicitly read the first six columns; peak
                    # assignment retains column seven for terminal-aware identity.
                    bed.write(
                        f"{chrom}\t{bed_start}\t{bed_end}\t{gene_name}\t0\t{strand}\t{gene_id}\n"
                    )

            logger_utils.info(f"Successfully converted GTF to BED: '{gene_bed}'.")

        except Exception as e:
            loggers.raise_error(f"Failed to convert GTF to BED: {e}")
            raise Exception(f"Error: Failed to convert GTF to BED: {e}")


def bedtools_sort(input_bed: str | Path, sorted_bed: str | Path, chrom_sizes_sorted: str | Path) -> None:
    with suffix_scope(suffix("bedtools_sort")):
        try:
            logger_utils.info(f"Sorting BED file '{input_bed}' using Bedtools sort...")

            cmd = ["bedtools", "sort", "-i", str(input_bed), "-g", str(chrom_sizes_sorted)]

            with open(str(sorted_bed), "w") as out_f:
                run_tool_command(
                    cmd,
                    tool="bedtools",
                    operation=f"sort.{Path(sorted_bed).name}",
                    stdout=out_f,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=True,
                )

            logger_utils.info(f"Sorted BED file saved as '{sorted_bed}'.")
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            detail = f"; stderr={stderr}" if stderr else ""
            message = f"Bedtools sort command failed for '{input_bed}': {e}{detail}"
            logger_utils.error(message)
            logger_main.error(message)
            raise
        except Exception as e:
            loggers.raise_error(f"Failed to sort BED file '{input_bed}': {e}")


def create_or_load_gffdb(
        db_path: Path,
        filtered_gtf_path: Path = None,
        mode: Literal["create", "load"] = "load",
):
    with suffix_scope(suffix("create_or_load_gffdb")):
        if mode == "create":
            if db_path.exists():
                logger_utils.info(f"gffutils database '{db_path}' already exists. Recreating it.")
                db_path.unlink()
            assert filtered_gtf_path is not None
            logger_utils.info(f"Creating gffutils database '{db_path}' from pre-filtered GTF '{filtered_gtf_path}'...")
            db = gffutils.create_db(
                str(filtered_gtf_path),
                dbfn=str(db_path),
                force=True,
                keep_order=True,
                merge_strategy="merge",
                sort_attribute_values=True,
                disable_infer_genes=True,
                disable_infer_transcripts=True,
                id_spec={"gene": "gene_id", "transcript": "transcript_id"},
            )
            loggers.info(f"Successfully created gffutils database at '{db_path}'.")
            return db

        if mode == "load":
            if not db_path.exists():
                loggers.raise_error(f"Requested to load gffutils database but file does not exist: '{db_path}'",
                                    FileNotFoundError, )
            # logger_utils.info(f"Loading existing gffutils database from '{db_path}'.")
            return gffutils.FeatureDB(str(db_path))

        loggers.raise_error(f"Unsolved mode: {mode}", ValueError)
        raise  # unreachable, for IDE inspection only


def load_dict_from_json(filepath: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(filepath):
        logger_utils.warning(f"JSON file '{filepath}' does not exist.")
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger_utils.info(f"Successfully loaded data from '{filepath}'.")
        return data
    except Exception as e:
        logger_utils.error(f"Failed to load data from '{filepath}': {e}")
        raise


def save_dict_to_json(data, filepath):
    """
    Saves a dictionary to a JSON file.

    Parameters:
    - data (dict): The dictionary to save.
    - filepath (str): The path to the JSON file.
    """
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=4)
        logger_utils.info(f"Successfully saved data to JSON file at '{filepath}'.")
    except Exception as e:
        logger_utils.error(f"Failed to save data to JSON file at '{filepath}': {e}")
        raise


def convert_bed_to_bigbed(
        input_bed: str | Path,
        output_bigbed: str | Path,
        chrom_sizes: str | Path,
        bedtobigbed_path: str = 'bedToBigBed'
) -> None:
    # Remove existing bigBed file to avoid append errors or permissions issues
    Path(output_bigbed).unlink(missing_ok=True)

    input_bed, output_bigbed, chrom_sizes = str(input_bed), str(output_bigbed), str(chrom_sizes)


    logger_utils.info(f"Starting conversion from BED to bigBed: '{input_bed}' -> '{output_bigbed}'")

    # 1. Validation Checks
    if not shutil.which(bedtobigbed_path):
        msg = f"bedToBigBed not found at '{bedtobigbed_path}'."
        loggers.raise_error(msg)
        raise FileNotFoundError(f"Error: {msg}")

    if not os.path.isfile(input_bed):
        loggers.raise_error(f"BED file '{input_bed}' does not exist.")
        raise FileNotFoundError(f"Error: BED file '{input_bed}' does not exist.")

    if not os.path.isfile(chrom_sizes):
        loggers.raise_error(f"Chromosome sizes file '{chrom_sizes}' does not exist.")
        raise FileNotFoundError(f"Error: Chromosome sizes file '{chrom_sizes}' does not exist.")

    temp_bed_path: Optional[str] = None

    try:
        # 2. Process BED file with Pandas
        # Read all columns as strings strictly to prevent early type inference issues
        # keep_default_na=False: a contig literally named "NA" must stay text,
        # otherwise it is written as an empty field and bedToBigBed rejects it.
        bed_df = pd.read_csv(input_bed, sep='\t', header=None, dtype=str, keep_default_na=False)

        if bed_df.shape[1] < 6:
            msg = f"BED file '{input_bed}' does not have at least 6 columns."
            loggers.raise_error(msg)
            raise ValueError(f"Error: {msg}")

        # Select standard 6 columns: chrom, start, end, name, score, strand
        bed_df = bed_df.iloc[:, :6].copy()
        bed_df.columns = ['chrom', 'start', 'end', 'name', 'score', 'strand']

        # Type conversion
        bed_df['start'] = bed_df['start'].astype(int)
        bed_df['end'] = bed_df['end'].astype(int)

        # Handle Score: Convert to float first (to handle "100.0"), then int, then cap at 1000
        bed_df['score'] = pd.to_numeric(bed_df['score'], errors='coerce').fillna(0).astype(int)

        over_1000_mask = bed_df['score'] > 1000
        if over_1000_mask.any():
            count = over_1000_mask.sum()
            loggers.warning(f"Capping {count} 'score' values above 1000 to 1000.")
            bed_df.loc[over_1000_mask, 'score'] = 1000

        # Validate Strand
        bed_df['strand'] = bed_df['strand'].astype(str).str.upper()
        valid_strands = {'+', '-'}
        # Check if there are any invalid strands (optimized set logic)
        unique_strands = set(bed_df['strand'].unique())
        invalid_strands = unique_strands - valid_strands

        if invalid_strands:
            # Check if likely dots '.', common in some BED formats, but bigBed often requires strict +/-
            # If logic requires only +/-, raise error
            msg = f"Invalid strand values found: {invalid_strands}. Expected '+' or '-'."
            loggers.raise_error(msg)
            raise ValueError(f"Error: {msg}")

        # 3. Write processed data to temp file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.bed') as temp_bed:
            bed_df.to_csv(temp_bed.name, sep='\t', header=False, index=False)
            temp_bed_path = temp_bed.name
            logger_utils.debug(f"Temporary BED file created at '{temp_bed_path}'.")

        # 4. Execute conversion
        cmd = [bedtobigbed_path, temp_bed_path, chrom_sizes, output_bigbed]
        logger_utils.debug(f"Executing: {' '.join(cmd)}")

        run_tool_command(
            cmd,
            tool="bedToBigBed",
            operation=f"convert.{Path(output_bigbed).name}",
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        logger_utils.info(f"Successfully converted '{input_bed}' to bigBed format at '{output_bigbed}'.")

    except subprocess.CalledProcessError as e:
        loggers.raise_error(f"bedToBigBed failed: {e.stderr}")
        raise RuntimeError(f"Error: bedToBigBed failed. Details: {e.stderr}")
    except ValueError as ve:
        loggers.raise_error(f"Value conversion error: {ve}")
        raise ValueError(f"Error: Value conversion failed: {ve}")
    except Exception as e:
        loggers.raise_error(f"Unexpected error during bedToBigBed conversion: {e}")
        raise Exception(f"Error: Unexpected error: {e}")
    finally:
        # 5. Cleanup
        if temp_bed_path and os.path.exists(temp_bed_path):
            try:
                os.remove(temp_bed_path)
                logger_utils.debug(f"Removed temporary file '{temp_bed_path}'.")
            except OSError as e:
                logger_utils.warning(f"Failed to remove temp file '{temp_bed_path}': {e}")


def convert_bedgraph_to_bigwig(in_bedgraph, out_bigwig, standard_chrom_sizes, local_log):
    """Validate and externally sort coverage, then atomically publish a complete bigWig.

    The bedGraph used by peak calling/counting is never rewritten. GNU sort uses
    a bounded 64 MiB buffer and spills into the output directory. Signed display
    tracks remain supported; non-finite values and overlapping intervals do not.
    """
    import math

    in_bedgraph, out_bigwig = Path(in_bedgraph), Path(out_bigwig)
    standard_chrom_sizes, local_log = Path(standard_chrom_sizes), Path(local_log)
    out_bigwig.parent.mkdir(parents=True, exist_ok=True)
    local_log.parent.mkdir(parents=True, exist_ok=True)
    # A failed replacement must never leave an older bigWig that appears current.
    out_bigwig.unlink(missing_ok=True)
    sizes = {}
    with standard_chrom_sizes.open() as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != 2 or fields[0] in sizes:
                raise ValueError(f"Invalid/duplicate chromosome sizes row {number}")
            length = int(fields[1])
            if length <= 0:
                raise ValueError(f"Invalid chromosome length at row {number}")
            sizes[fields[0]] = length
    if not sizes:
        raise ValueError("Empty chromosome sizes file")

    with tempfile.TemporaryDirectory(prefix=".bigwig-", dir=out_bigwig.parent) as temp:
        temp = Path(temp)
        normalized, ordered, candidate = temp / "input.bedgraph", temp / "sorted.bedgraph", temp / "output.bw"
        n_rows = 0
        with in_bedgraph.open() as source, normalized.open("w") as target:
            for number, line in enumerate(source, 1):
                if not line.strip() or line.startswith(("#", "track ", "browser ")):
                    continue
                fields = line.split()
                if len(fields) != 4:
                    raise ValueError(f"Expected four bedGraph columns at row {number}")
                chrom, start, end, value = fields
                start, end, numeric = int(start), int(end), float(value)
                if chrom not in sizes or not 0 <= start < end <= sizes[chrom] or not math.isfinite(numeric):
                    raise ValueError(f"Invalid bedGraph contig/interval/value at row {number}: {line.strip()}")
                target.write(f"{chrom}\t{start}\t{end}\t{value}\n")
                n_rows += 1
        if not n_rows:
            raise ValueError("Cannot build a bigWig from empty coverage")
        with ordered.open("w") as target:
            run_tool_command(
                ["sort", "--buffer-size=64M", f"--temporary-directory={temp}", "-k1,1", "-k2,2n", "-k3,3n", str(normalized)],
                tool="sort", operation=f"bedgraph.{out_bigwig.name}",
                stdout=target, stderr=subprocess.PIPE, text=True, check=True,
                env={**os.environ, "LC_ALL": "C"},
            )
        previous_chrom, previous_end = None, None
        sorted_rows = 0
        with ordered.open() as handle:
            for line in handle:
                chrom, start, end, _ = line.split()
                start, end = int(start), int(end)
                if chrom == previous_chrom and start < previous_end:
                    raise ValueError(f"Overlapping bedGraph intervals on {chrom} at {start}")
                previous_chrom, previous_end = chrom, end
                sorted_rows += 1
        if sorted_rows != n_rows:
            raise ValueError("bedGraph sorting changed the number of coverage intervals")
        with local_log.open("a") as handle:
            run_tool_command(
                ["bedGraphToBigWig", str(ordered), str(standard_chrom_sizes), str(candidate)],
                tool="bedGraphToBigWig", operation=f"convert.{out_bigwig.name}",
                compatibility_log=local_log, stdout=handle,
                stderr=subprocess.PIPE, text=True, check=True,
            )
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise RuntimeError("bedGraphToBigWig returned without a nonempty output")
        os.replace(candidate, out_bigwig)
        logger_utils.info(f"Validated and sorted {n_rows} intervals; bigWig saved to '{out_bigwig}'.")
