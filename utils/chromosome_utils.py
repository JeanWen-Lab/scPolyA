import re
from pathlib import Path

import pandas as pd

from logging_config import get_logger, LoggersWrapper, LOG_MAIN, LOG_UTILS, suffix_scope

suffix = lambda x: f" (chromosome_utils:{x})"
logger_main = get_logger(LOG_MAIN)
logger_utils = get_logger(LOG_UTILS)
loggers = LoggersWrapper(main=logger_main, sub=logger_utils)


def get_standard_chromosomes(species) -> list:
    """
    Returns a sorted list of standard chromosome names based on the species.
    Parameters:
        species (str): Species identifier ('human', 'mouse').
    Returns:
        set: A set of standard chromosome names.
    """
    with suffix_scope(suffix("get_standard_chromosomes")):
        species = species.lower()
        standard_chroms_predefined = {
            'human': {
                '1', '2', '3', '4', '5', '6', '7', '8', '9', '10',
                '11', '12', '13', '14', '15', '16', '17', '18', '19', '20',
                '21', '22', 'X', 'Y', 'M'
            },
            'mouse': {
                '1', '2', '3', '4', '5', '6', '7', '8', '9', '10',
                '11', '12', '13', '14', '15', '16', '17', '18', '19', 'X',
                'Y', 'M',  # we don't include mitochondrial DNA for mouse
            },
            'fly': {
                '2L', '2R', '3L', '3R', '4', 'X', 'Y', 'M'
            },
            'worm': {
                'I', 'II', 'III', 'IV', 'V', 'X', 'M'  # No Y chromosome
            }
        }

        if species not in standard_chroms_predefined:
            loggers.raise_error(f"Unsupported species '{species}'. Supported species: 'human', 'mouse', 'fly', 'worm'.")

        return sorted(standard_chroms_predefined[species])


def add_chr_prefix_to_chrom_sizes(input_chrom_sizes, output_chrom_sizes):
    """
    Adds 'chr' prefix to chromosome names in a chromosome sizes file.

    Parameters:
        input_chrom_sizes (str): Path to the input chromosome sizes file.
        output_chrom_sizes (str): Path to save the modified chromosome sizes file.
    """
    with suffix_scope(suffix("add_chr_prefix_to_chrom_sizes")):
        try:
            chrom_df = pd.read_csv(input_chrom_sizes, sep='\t', header=None, names=['chrom', 'size'], dtype=str)
            chrom_df['chrom'] = chrom_df['chrom'].apply(lambda x: f'chr{x}' if not x.startswith('chr') else x)
            chrom_df.to_csv(output_chrom_sizes, sep='\t', header=False, index=False)
        except FileNotFoundError:
            loggers.raise_error(f"Input chromosome sizes file '{input_chrom_sizes}' not found.", FileNotFoundError)
        except Exception as e:
            loggers.raise_error(f"Error adding 'chr' prefix to chromosome sizes: {e}")


def remove_chr_prefix_from_chrom_sizes(input_chrom_sizes, output_chrom_sizes):
    """
    Removes 'chr' prefix from chromosome names in a chromosome sizes file.

    Parameters:
        input_chrom_sizes (str): Path to the input chromosome sizes file.
        output_chrom_sizes (str): Path to save the modified chromosome sizes file.

    Returns:
        None
    """
    with suffix_scope(suffix("remove_chr_prefix_from_chrom_sizes")):
        try:
            chrom_df = pd.read_csv(input_chrom_sizes, sep='\t', header=None, names=['chrom', 'size'], dtype=str)
            chrom_df['chrom'] = chrom_df['chrom'].apply(lambda x: re.sub(r'^chr', '', x) if x.startswith('chr') else x)
            chrom_df.to_csv(output_chrom_sizes, sep='\t', header=False, index=False)
        except FileNotFoundError:
            loggers.raise_error(f"Input chromosome sizes file '{input_chrom_sizes}' not found.", FileNotFoundError)
        except pd.errors.EmptyDataError:
            loggers.raise_error(f"Chromosome sizes file '{input_chrom_sizes}' is empty.", pd.errors.EmptyDataError)
        except Exception as e:
            loggers.raise_error(f"Error processing chromosome sizes file '{input_chrom_sizes}': {e}")


def filter_chromosome_sizes(input_chrom_sizes, output_chrom_sizes, standard_chroms):
    """
    Filters and sorts a chromosome sizes file to include only standard chromosomes in a specific order.

    Parameters:
        input_chrom_sizes (str): Path to the input chromosome sizes file.
        output_chrom_sizes (str): Path to save the filtered and sorted chromosome sizes file.
        standard_chroms (set): A set of standard chromosome names to retain.

    Returns:
        None
    """
    with suffix_scope(suffix("filter_chromsome_sizes")):
        try:
            # Read the chromosome sizes file
            chrom_df = pd.read_csv(input_chrom_sizes, sep='\t', header=None, names=['chrom', 'size'], dtype=str)

            original_chroms = set(chrom_df['chrom'].tolist())
            # Determine chromosomes to keep
            chroms_to_keep = original_chroms.intersection(standard_chroms)
            if not chroms_to_keep:
                logger_main.raise_error("No standard chromosomes found in the chromosome sizes file.")

            # Filter the dataframe
            filtered_chrom_df = chrom_df[chrom_df['chrom'].isin(chroms_to_keep)].copy()
            excluded_chroms = original_chroms - chroms_to_keep

            if excluded_chroms:
                loggers.warning(f"Excluded {len(excluded_chroms)} non-standard chromosomes from chromosome sizes file.")

            # Define the ordered list of chromosomes
            def get_ordered_chroms(standard_chroms):
                """
                Converts a set of standard chromosomes to an ordered list.
                Numeric chromosomes are sorted numerically, followed by sex chromosomes and mitochondrial DNA.

                Parameters:
                    standard_chroms (set): A set of standard chromosome names (e.g., {'chr1', 'chr2', 'chrX', 'chrY', 'chrM'}).

                Returns:
                    ordered_chroms (list): An ordered list of chromosome names.
                """
                def chrom_sort_key(chrom):
                    raw = chrom[3:] if chrom.startswith("chr") else chrom
                    raw = "M" if raw == "MT" else raw
                    if raw.isdigit():
                        return 0, int(raw), ""
                    special_order = {"X": 1, "Y": 2, "M": 3}
                    return 1, special_order.get(raw, 100), raw

                return sorted(standard_chroms, key=chrom_sort_key)

            ordered_chroms = get_ordered_chroms(chroms_to_keep)

            # Create a categorical type with the desired order
            chrom_cat_type = pd.api.types.CategoricalDtype(categories=ordered_chroms, ordered=True)
            filtered_chrom_df['chrom'] = filtered_chrom_df['chrom'].astype(chrom_cat_type)

            # Sort the dataframe based on the ordered chromosome list
            filtered_chrom_df.sort_values('chrom', inplace=True)

            # Save the filtered and sorted chromosome sizes file
            filtered_chrom_df.to_csv(output_chrom_sizes, sep='\t', header=False, index=False)
            loggers.info(f"Filtered and sorted chromosome sizes file saved as '{output_chrom_sizes}'.")

        except FileNotFoundError:
            loggers.raise_error(f"Chromosome sizes file '{input_chrom_sizes}' not found.", FileNotFoundError)
        except pd.errors.EmptyDataError:
            loggers.raise_error(f"Chromosome sizes file '{input_chrom_sizes}' is empty.", pd.errors.EmptyDataError)
        except Exception as e:
            loggers.raise_error(f"Error filtering and sorting chromosome sizes file: {e}")


def check_and_adjust_gtf(gtf_input, bam_has_chr_prefix, bam_paths, standard_chroms, gtf_output):
    """
    Checks if the GTF file's chromosome names have the 'chr' prefix and matches the BAM files' naming convention.
    If there's a mismatch, adjusts the GTF file by adding or removing the 'chr' prefix.
    Also filters the GTF file to include only standard chromosomes.

    Parameters:
        gtf_input (str): Path to the input GTF file.
        bam_paths (list): List of paths to BAM files.
        standard_chroms (set): Set of standard chromosome names (without 'chr' prefix).
        gtf_output (str): Path to save the adjusted and filtered GTF file.

    Returns:
        None
    """

    def detect_chr_prefix_in_gtf(_gtf_path):
        """
        Detects if chromosome names in a GTF file have the 'chr' prefix.

        Parameters:
            _gtf_path (str): Path to the GTF file.

        Returns:
            bool: True if 'chr' prefix is detected, False otherwise.
        """
        try:
            with open(_gtf_path, 'r') as gtf_file:
                for line in gtf_file:
                    if line.startswith('#'):
                        continue
                    fields = line.strip().split('\t')
                    if len(fields) < 1:
                        continue
                    chrom = fields[0]
                    if chrom.startswith('chr'):
                        return True
                    else:
                        return False
            return False
        except FileNotFoundError:
            loggers.raise_error(f"GTF file '{_gtf_path}' not found.", FileNotFoundError)
        except Exception as e:
            loggers.raise_error(f"Error reading GTF file '{_gtf_path}': {e}")

    def remove_chr_prefix_from_gtf(_gtf_input, _gtf_output):
        """
        Removes 'chr' prefix from chromosome names in a GTF file.

        Parameters:
            _gtf_input (str): Path to the input GTF file.
            _gtf_output (str): Path to save the modified GTF file.

        Returns:
            None
        """
        try:
            with open(_gtf_input, 'r') as infile, open(_gtf_output, 'w') as outfile:
                for line in infile:
                    if line.startswith('#'):
                        outfile.write(line)
                        continue
                    fields = line.strip().split('\t')
                    if len(fields) < 1:
                        outfile.write(line)
                        continue
                    chrom = fields[0]
                    if chrom.startswith('chr'):
                        chrom = chrom[3:]
                    fields[0] = chrom
                    outfile.write('\t'.join(fields) + '\n')
            logger_utils.info(f"Removed 'chr' prefix from GTF file and saved as '{_gtf_output}'.")
        except Exception as e:
            loggers.raise_error(f"Failed to remove 'chr' prefix from GTF file '{_gtf_input}': {e}")

    def add_chr_prefix_to_gtf(_gtf_input, _gtf_output):
        """
        Adds 'chr' prefix to chromosome names in a GTF file.

        Parameters:
            _gtf_input (str): Path to the input GTF file.
            _gtf_output (str): Path to save the modified GTF file.

        Returns:
            None
        """
        try:
            with open(_gtf_input, 'r') as infile, open(_gtf_output, 'w') as outfile:
                for line in infile:
                    if line.startswith('#'):
                        outfile.write(line)
                        continue
                    fields = line.strip().split('\t')
                    if len(fields) < 1:
                        outfile.write(line)
                        continue
                    chrom = fields[0]
                    if not chrom.startswith('chr'):
                        chrom = f'chr{chrom}'
                    fields[0] = chrom
                    outfile.write('\t'.join(fields) + '\n')
            loggers.info(f"Added 'chr' prefix to GTF file and saved as '{_gtf_output}'.")
        except Exception as e:
            loggers.raise_error(f"Failed to add 'chr' prefix to GTF file '{_gtf_input}': {e}")

    def filter_gtf_by_standard_chroms(_gtf_input, _standard_chroms, _gtf_output):
        """
        Filters a GTF file to include only entries on standard chromosomes.

        Parameters:
            _gtf_input (str): Path to the input GTF file.
            _standard_chroms (set): Set of standard chromosome names (with or without 'chr').
            _gtf_output (str): Path to save the filtered GTF file.

        Returns:
            None
        """
        try:
            with open(_gtf_input, 'r') as infile, open(_gtf_output, 'w') as outfile:
                count_total = 0
                count_filtered = 0
                for line in infile:
                    if line.startswith('#'):
                        outfile.write(line)
                        continue
                    fields = line.strip().split('\t')
                    if len(fields) < 1:
                        outfile.write(line)
                        continue
                    chrom = fields[0]
                    if chrom in _standard_chroms:
                        outfile.write(line)
                        count_filtered += 1
                    count_total += 1
            loggers.info(
                f"Filtered GTF file saved as '{_gtf_output}', including only standard chromosomes. {count_filtered}/{count_total} entries retained.")

        except FileNotFoundError:
            loggers.raise_error(f"Input GTF file '{_gtf_input}' not found.")

        except Exception as e:
            loggers.raise_error(f"Error: Could not filter GTF file '{_gtf_input}': {e}")

    with suffix_scope(suffix("check_and_adjust_gtf")):
        has_chr_gtf = detect_chr_prefix_in_gtf(gtf_input)

        if not bam_paths:
            loggers.raise_error(f"No BAM files provided for chromosome prefix detection.")
        try:
            if has_chr_gtf and not bam_has_chr_prefix:
                logger_main.info(f"Adjusting GTF file to remove 'chr' prefix to match BAM files.")
                temp_gtf = gtf_output + ".temp"
                remove_chr_prefix_from_gtf(gtf_input, temp_gtf)
                filter_gtf_by_standard_chroms(temp_gtf, standard_chroms, gtf_output)
                Path(temp_gtf).unlink()  # Remove temporary file
            elif not has_chr_gtf and bam_has_chr_prefix:
                loggers.info("Adjusting GTF file to add 'chr' prefix to match BAM files.")
                temp_gtf = gtf_output + ".temp"
                add_chr_prefix_to_gtf(gtf_input, temp_gtf)
                filter_gtf_by_standard_chroms(temp_gtf, standard_chroms, gtf_output)
                Path(temp_gtf).unlink()  # Remove temporary file
            else:
                loggers.info("No adjustment needed for GTF file's 'chr' prefix.")
                filter_gtf_by_standard_chroms(gtf_input, standard_chroms, gtf_output)

            loggers.info(f"GTF file '{gtf_output}' has been checked and adjusted for 'chr' prefix consistency.")

        except Exception as e:
            loggers.raise_error(f"Error: Failed to check and adjust GTF file '{gtf_input}': {e}")


def parse_gtf_attributes(attributes_str):
    with suffix_scope(suffix("parse_gtf_attributes")):
        """
        Parses the attributes column of a GTF file line.
    
        Parameters:
        - attributes_str (str): The attributes string from a GTF line.
    
        Returns:
        - attributes (dict): A dictionary of attribute keys and values.
        """
        attributes = {}
        # Split by semicolon to separate attributes
        for attribute in attributes_str.strip().split(';'):
            if attribute.strip() == '':
                continue
            # Split by space to separate key and value
            key_value = attribute.strip().split(' ', 1)
            if len(key_value) != 2:
                continue
            key, value = key_value
            # Remove quotes from value
            value = value.strip('"')
            attributes[key] = value
        return attributes


def prefilter_gtf_python(original_gtf: str, filtered_gtf: str):
    """
    Pre-filters the GTF file to exclude genes starting with 'Gm' or containing 'Rik'.
    Excludes all features associated with excluded genes.

    Parameters:
    - original_gtf (str): Path to the original GTF file.
    - filtered_gtf (str): Path to save the filtered GTF file.
    """

    with suffix_scope(suffix("prefilter_gtf_python")):
        try:
            logger_utils.info(f"Pre-filtering GTF file '{original_gtf}' to exclude unwanted genes...")
            exclude_genes = set()
            with open(original_gtf, 'r') as in_f, open(filtered_gtf, 'w') as out_f:
                for line in in_f:
                    if line.startswith('#'):
                        out_f.write(line)
                        continue
                    fields = line.strip().split('\t')
                    if len(fields) < 9:
                        continue  # Skip malformed lines
                    feature_type = fields[2]
                    attributes_str = fields[8]
                    attributes = parse_gtf_attributes(attributes_str)
                    gene_name = attributes.get('gene_name', attributes.get('gene_id', ''))
                    gene_id = attributes.get('gene_id', gene_name)
                    if feature_type == 'gene':
                        if gene_name.startswith('Gm') or 'Rik' in gene_name:
                            exclude_genes.add(gene_id)
                            logger_utils.debug(f"Excluding gene '{gene_name}' with gene_id '{gene_id}'")
                            continue  # Skip writing this gene
                        else:
                            # Keep gene
                            out_f.write(line)
                    else:
                        # For other features, check if their gene_id is not in exclude_genes
                        gene_id = attributes.get('gene_id', '')
                        if gene_id not in exclude_genes:
                            out_f.write(line)
            logger_utils.info(f"Filtered GTF file saved to '{filtered_gtf}'.")
        except Exception as e:
            loggers.raise_error(f"Failed to pre-filter GTF file: {e}")


def validate_filtered_gtf(filtered_gtf):
    """
    Validates that the filtered GTF file excludes genes starting with 'Gm' or containing 'Rik'.

    Parameters:
    - filtered_gtf (str): Path to the filtered GTF file.
    """
    with suffix_scope(suffix("validate_filtered_gtf")):
        try:
            logger_utils.info(f"Validating filtered GTF file '{filtered_gtf}'...")
            with open(filtered_gtf, 'r') as f:
                for line in f:
                    if line.startswith('#'):
                        continue  # Skip header lines
                    fields = line.strip().split('\t')
                    if len(fields) < 9:
                        continue  # Skip malformed lines
                    feature_type = fields[2]
                    attributes_str = fields[8]
                    attributes = parse_gtf_attributes(attributes_str)
                    gene_name = attributes.get('gene_name', attributes.get('gene_id', ''))
                    if feature_type == 'gene':
                        if gene_name.startswith('Gm') or 'Rik' in gene_name:
                            loggers.raise_error(f"Filtered GTF still contains excluded gene: {gene_name}")
            logger_utils.info("Filtered GTF validation passed. No excluded genes found.")
        except Exception as e:
            loggers.raise_error(f"Failed to validate filtered GTF: {e}")
