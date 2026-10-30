from __future__ import annotations

import os
import re
import sys

import numpy as np
import pysam

import umi_tools.Documentation as Documentation
import umi_tools.Utilities as U
import umi_tools.network as network

from umi_count_xr import sam_methods

__doc__ = """
========================================================================
count - Count reads per gene from BAM using UMIs and ordered sidecars
========================================================================
""" + Documentation.GENERIC_DOCSTRING_GDC

usage = """
count - Count reads per-gene using UMI and mapping coordinates

Usage: python -m umi_count_xr.count [OPTIONS] --stdin=IN_BAM [--stdout=OUT_BAM]
"""


def _validate_count_options(options) -> None:
    if options.per_gene:
        if options.gene_tag and options.per_contig:
            raise ValueError("need to use either --per-contig OR --gene-tag, please do not provide both")
        if options.feature_map and options.per_contig:
            raise ValueError("need to use either --per-contig OR --feature-map, please do not provide both")
        if options.feature_map and options.gene_tag:
            raise ValueError("need to use either --gene-tag OR --feature-map, please do not provide both")
        if not options.per_contig and not options.gene_tag and not options.feature_map:
            raise ValueError("for per-gene applications, must supply --per-contig, --gene-tag or --feature-map")

    if options.per_contig and not options.per_gene:
        raise ValueError("need to use --per-gene with --per-contig")
    if options.gene_tag and not options.per_gene:
        raise ValueError("need to use --per-gene with --gene_tag")
    if options.feature_map and not options.per_gene:
        raise ValueError("need to use --per-gene with --feature-map")
    if options.gene_transcript_map and not options.per_contig:
        raise ValueError("need to use --per-contig and --per-gene with --gene-transcript-map")

    if options.get_umi_method == "tag":
        if options.umi_tag is None:
            raise ValueError("Need to supply the --umi-tag option")
        if options.per_cell and options.cell_tag is None:
            raise ValueError("Need to supply the --cell-tag option")

    if options.assigned_tag is None:
        options.assigned_tag = options.gene_tag

    if options.skip_regex:
        try:
            re.compile(options.skip_regex)
        except re.error as exc:
            raise ValueError(f"skip-regex '{options.skip_regex}' is not a valid regex") from exc

    if options.unmapped_reads == "output":
        raise ValueError(
            "Cannot use --unmapped-reads=output. If you want to retain unmapped without deduplicating them, use the group command"
        )


def main(argv=None):
    if argv is None:
        argv = sys.argv

    parser = U.OptionParser(version="%prog version: $Id$", usage=usage, description=globals()["__doc__"])
    if len(argv) == 1:
        parser.print_usage()
        print("Required options missing, see --help for more details")
        return 1

    group = U.OptionGroup(parser, "count-specific options")
    parser.add_option(
        "--wide-format-cell-counts",
        dest="wide_format_cell_counts",
        action="store_true",
        default=False,
        help="output the cell counts in a wide format (rows=genes, columns=cells)",
    )
    group.add_option(
        "--feature-map",
        dest="feature_map",
        type="string",
        default=None,
        help="Line-aligned external feature sidecar matching BAM record order",
    )
    group.add_option("--xr-tag", dest="xr_tag", type="string", default="XR", help="Deprecated compatibility option")
    parser.add_option_group(group)

    options, _ = U.Start(parser, argv=argv, add_group_dedup_options=False, add_dedup_count_sam_options=True)
    options.per_gene = True
    # Preserve full cell barcodes such as AAAC...-1 unless the caller explicitly requests splitting.
    if getattr(options, "cell_tag_split", None) == "-":
        options.cell_tag_split = None
    _validate_count_options(options)

    if options.random_seed:
        np.random.seed(options.random_seed)

    if options.stdin == sys.stdin:
        raise ValueError("Input on standard in not currently supported")

    in_name = options.stdin.name
    options.stdin.close()
    infile = pysam.Samfile(in_name, "r" if options.in_sam else "rb")

    tmpfilename = U.getTempFilename(dir=options.tmpdir)
    tmpfile = U.openFile(tmpfilename, mode="w")

    n_input, n_output, input_reads = 0, 0, 0
    metacontig2contig = None

    if options.chrom:
        inreads = infile.fetch(reference=options.chrom)
    elif options.gene_transcript_map:
        metacontig2contig = sam_methods.getMetaContig2contig(infile, options.gene_transcript_map)
        inreads = sam_methods.metafetcher(infile, metacontig2contig, "MC")
    else:
        inreads = infile.fetch()

    bundle_iterator = sam_methods.get_bundles(options, only_count_reads=True, metacontig_contig=metacontig2contig)
    processor = network.UMIClusterer(options.method)

    for bundle, key, status in bundle_iterator(inreads):
        if status == "single_read":
            continue

        gene, cell = key
        counts = {umi: bundle[umi]["count"] for umi in bundle.keys()}
        n_input += sum(counts.values())

        while n_input >= input_reads + 1000000:
            input_reads += 1000000
            U.info("Parsed %i input reads" % input_reads)

        gene_count = len(processor(counts, threshold=options.threshold))
        if options.per_cell:
            tmpfile.write("%s\n" % "\t".join((gene, cell.decode(), str(gene_count))))
        else:
            tmpfile.write("%s\n" % "\t".join((gene, str(gene_count))))
        n_output += gene_count

    tmpfile.close()

    if options.per_cell:
        gene_counts_dict = {}
        genes = set()
        cells = set()
        with U.openFile(tmpfilename, mode="r") as inf:
            for line in inf:
                gene, cell, gene_count = line.strip().split("\t")
                genes.add(gene)
                cells.add(cell)
                gene_counts_dict.setdefault(gene, {})[cell] = gene_count

        if options.wide_format_cell_counts:
            options.stdout.write("%s\t%s\n" % ("gene", "\t".join(sorted(cells))))
            for gene in sorted(genes):
                counts = [gene_counts_dict[gene].get(cell, 0) for cell in sorted(cells)]
                options.stdout.write("%s\t%s\n" % (gene, "\t".join(map(str, counts))))
        else:
            options.stdout.write("%s\t%s\t%s\n" % ("gene", "cell", "count"))
            for gene in sorted(genes):
                for cell in sorted(gene_counts_dict[gene].keys()):
                    options.stdout.write("%s\t%s\t%s\n" % (gene, cell, gene_counts_dict[gene][cell]))
    else:
        options.stdout.write("%s\t%s\n" % ("gene", "count"))
        with U.openFile(tmpfilename, mode="r") as inf:
            for line in inf:
                options.stdout.write(line)

    os.unlink(tmpfilename)

    for event in bundle_iterator.read_events.most_common():
        U.info("%s: %s" % (event[0], event[1]))
    U.info("Number of (post deduplication) reads counted: %i" % n_output)
    U.Stop()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
