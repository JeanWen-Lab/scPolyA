from logging_config import get_logger, LoggersWrapper, LOG_MAIN, LOG_UTILS

suffix = lambda x: f" (bedtools_utils:{x})"
logger_main = get_logger(LOG_MAIN)
logger_utils = get_logger(LOG_UTILS)
loggers = LoggersWrapper(main=logger_main, sub=logger_utils)


def tt_scale_with_factor(in_bedgraph, out_bedgraph, scaling_factor):
    logger_utils.info(f"Applying UPM scaling with factor {scaling_factor} to '{in_bedgraph}'...")

    with open(in_bedgraph, 'r') as infile, open(str(out_bedgraph), 'w') as outfile:
        for line in infile:
            if line.startswith('#') or line.strip() == '':
                continue  # Skip headers or empty lines
            parts = line.strip().split('\t')
            if len(parts) < 4:
                logger_utils.warning(f"Skipping malformed line: {line.strip()}")
                continue
            chrom, start, end, coverage = parts[:4]
            try:
                scaled_coverage = float(coverage) * scaling_factor
            except ValueError:
                logger_utils.warning(f"Non-numeric coverage value '{coverage}' in line: {line.strip()}")
                continue
            outfile.write(f"{chrom}\t{start}\t{end}\t{scaled_coverage}\n")

    logger_utils.info(f"Applied UPM scaling with factor {scaling_factor} to '{in_bedgraph}'...")
