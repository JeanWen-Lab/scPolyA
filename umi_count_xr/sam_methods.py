from __future__ import annotations

import collections
import random
import re
from functools import partial

import umi_tools.Utilities as U
from umi_tools.sam_methods import getMetaContig2contig, get_read_position, metafetcher

SIDECAR_MAGIC_PREFIX = "#scpolya_feature_sidecar\tv2"


def get_barcode_read_id(read, cell_barcode=False, sep="_"):
    try:
        if cell_barcode:
            umi = read.qname.split(sep)[-1].encode("utf-8")
            cell = read.qname.split(sep)[-2].encode("utf-8")
        else:
            umi = read.qname.split(sep)[-1].encode("utf-8")
            cell = None
        return umi, cell
    except Exception as exc:
        raise ValueError("Could not extract UMI +/- cell barcode from the readID") from exc


def get_barcode_tag(read, cell_barcode=False, umi_tag="RX", cell_tag=None,
                    umi_tag_split=None, umi_tag_delim=None, cell_tag_split=None, cell_tag_delim=None):
    try:
        umi = read.get_tag(umi_tag)
        cell = read.get_tag(cell_tag) if cell_barcode else None
        if umi_tag_split:
            umi = umi.split(umi_tag_split)[0]
        if umi_tag_delim:
            umi = "".join(umi.split(umi_tag_delim))
        if cell and cell_tag_split:
            cell = cell.split(cell_tag_split)[0]
        if cell and cell_tag_delim:
            cell = "".join(cell.split(cell_tag_delim))
        if cell:
            cell = cell.encode("utf-8")
        return umi.encode("utf-8"), cell
    except IndexError as exc:
        raise ValueError("Could not extract UMI +/- cell barcode from the read tag") from exc


def get_barcode_umis(read, cell_barcode=False):
    umi, cell = None, None
    try:
        for element in read.qname.split(":"):
            if element.startswith("UMI_"):
                umi = element[4:].encode("utf-8")
            elif element.startswith("CELL_") and cell_barcode:
                cell = element[5:].encode("utf-8")
        if umi is None:
            raise ValueError()
        return umi, cell
    except Exception as exc:
        raise ValueError("Could not extract UMI +/- cell barcode from the read tag") from exc


class get_bundles:
    def __init__(self, options, only_count_reads=False, all_reads=False,
                 return_unmapped=False, return_read2=False, metacontig_contig=None):
        self.options = options
        self.only_count_reads = only_count_reads
        self.all_reads = all_reads
        self.return_unmapped = return_unmapped
        self.return_read2 = return_read2
        self.metacontig_contig = metacontig_contig

        self.contig_metacontig = {}
        if self.metacontig_contig:
            for metacontig in metacontig_contig:
                for contig in metacontig_contig[metacontig]:
                    self.contig_metacontig[contig] = metacontig

        if self.options.get_umi_method == "read_id":
            self.barcode_getter = partial(get_barcode_read_id, cell_barcode=self.options.per_cell, sep=self.options.umi_sep)
        elif self.options.get_umi_method == "tag":
            self.barcode_getter = partial(
                get_barcode_tag,
                umi_tag=self.options.umi_tag,
                cell_barcode=self.options.per_cell,
                cell_tag=self.options.cell_tag,
                umi_tag_split=self.options.umi_tag_split,
                umi_tag_delim=self.options.umi_tag_delim,
                cell_tag_split=self.options.cell_tag_split,
                cell_tag_delim=self.options.cell_tag_delim,
            )
        elif self.options.get_umi_method == "umis":
            self.barcode_getter = partial(get_barcode_umis, cell_barcode=self.options.per_cell)
        else:
            raise ValueError("Unknown UMI extraction method")

        self.read_events = collections.Counter()
        self.observed_contigs = collections.defaultdict(set)
        self.last_pos = 0
        self.last_chr = None
        self.start = 0
        self.current_chr = None
        self.reads_dict = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(dict)))
        self.read_counts = collections.defaultdict(lambda: collections.defaultdict(dict))
        self.feature_assignments = None
        self.feature_assignment_buffer = None
        self.feature_sidecar_format = "legacy"
        self.feature_assignment_row = 0
        if getattr(self.options, "feature_map", None):
            self.feature_assignments = self._open_feature_assignments(self.options.feature_map)
            self._prime_feature_assignment_header()

    @staticmethod
    def _open_feature_assignments(feature_map_path):
        return U.openFile(feature_map_path, mode="r")

    def _prime_feature_assignment_header(self):
        line = self.feature_assignments.readline()
        if line == "":
            return
        if line.rstrip("\n").startswith(SIDECAR_MAGIC_PREFIX):
            self.feature_sidecar_format = "v2"
            return
        self.feature_assignment_buffer = line

    def _next_feature_assignment(self, read):
        if self.feature_assignments is None:
            return None
        if self.feature_assignment_buffer is not None:
            line = self.feature_assignment_buffer
            self.feature_assignment_buffer = None
        else:
            line = self.feature_assignments.readline()
        if line == "":
            raise ValueError("Feature sidecar ended before BAM iteration completed")
        self.feature_assignment_row += 1
        line = line.rstrip("\n")
        if self.feature_sidecar_format == "legacy":
            return line

        fields = line.split("\t", 2)
        if len(fields) != 3:
            raise ValueError(
                f"Malformed v2 feature sidecar row {self.feature_assignment_row}: expected query_name, flag, feature"
            )
        query_name, flag_text, feature = fields
        try:
            sidecar_flag = int(flag_text)
        except ValueError as exc:
            raise ValueError(
                f"Malformed v2 feature sidecar row {self.feature_assignment_row}: non-integer flag {flag_text!r}"
            ) from exc
        if query_name != read.query_name or sidecar_flag != read.flag:
            raise ValueError(
                "Feature sidecar/BAM record mismatch at row "
                f"{self.feature_assignment_row}: sidecar={query_name}/{sidecar_flag}, "
                f"bam={read.query_name}/{read.flag}"
            )
        return feature

    def update_dicts(self, read, pos, key, umi):
        if self.only_count_reads:
            try:
                self.reads_dict[pos][key][umi]["count"] += 1
            except KeyError:
                self.reads_dict[pos][key][umi]["count"] = 1
            return

        if self.all_reads:
            try:
                self.reads_dict[pos][key][umi]["count"] += 1
            except KeyError:
                self.reads_dict[pos][key][umi]["read"] = [read]
                self.reads_dict[pos][key][umi]["count"] = 1
            else:
                self.reads_dict[pos][key][umi]["read"].append(read)
            return

        try:
            self.reads_dict[pos][key][umi]["count"] += 1
        except KeyError:
            self.reads_dict[pos][key][umi]["read"] = read
            self.reads_dict[pos][key][umi]["count"] = 1
            self.read_counts[pos][key][umi] = 0
            return

        if self.reads_dict[pos][key][umi]["read"].mapq > read.mapq:
            return
        if self.reads_dict[pos][key][umi]["read"].mapq < read.mapq:
            self.reads_dict[pos][key][umi]["read"] = read
            self.read_counts[pos][key][umi] = 0
            return

        if self.options.detection_method in ["NH", "X0"]:
            tag = self.options.detection_method
            if self.reads_dict[pos][key][umi]["read"].opt(tag) < read.opt(tag):
                return
            if self.reads_dict[pos][key][umi]["read"].opt(tag) > read.opt(tag):
                self.reads_dict[pos][key][umi]["read"] = read
                self.read_counts[pos][key][umi] = 0
        elif self.options.detection_method == "XT":
            if self.reads_dict[pos][key][umi]["read"].opt("XT") == "U":
                return
            if read.opt("XT") == "U":
                self.reads_dict[pos][key][umi]["read"] = read
                self.read_counts[pos][key][umi] = 0

        self.read_counts[pos][key][umi] += 1
        if random.random() < 1.0 / self.read_counts[pos][key][umi]:
            self.reads_dict[pos][key][umi]["read"] = read

    def check_output(self):
        do_output = False
        out_keys = None
        if self.options.per_gene:
            if self.metacontig_contig:
                if self.current_chr != self.last_chr and self.observed_contigs[self.last_pos] == self.metacontig_contig[self.last_pos]:
                    do_output = True
                    out_keys = [self.last_pos]
            elif self.current_chr != self.last_chr:
                do_output = True
                out_keys = sorted(self.reads_dict.keys())
        elif self.options.whole_contig:
            if self.current_chr != self.last_chr:
                do_output = True
                out_keys = sorted(self.reads_dict.keys())
        elif self.start > (self.last_pos + 1000) or self.current_chr != self.last_chr:
            do_output = True
            out_keys = sorted(self.reads_dict.keys())
            if self.current_chr == self.last_chr:
                out_keys = [x for x in out_keys if x <= self.start - 1000]
        return do_output, out_keys

    def __call__(self, inreads):
        for read in inreads:
            ordered_feature = self._next_feature_assignment(read)
            if read.is_read2:
                if self.return_read2 and (not read.is_unmapped or (read.is_unmapped and self.return_unmapped)):
                    yield read, None, "single_read"
                continue
            self.read_events["Input Reads"] += 1

            if self.options.paired:
                if read.is_paired:
                    self.read_events["Read pairs"] += 1
                else:
                    self.read_events["Unpaired reads"] += 1
                    if self.options.unpaired_reads == "discard":
                        continue
                    if self.options.unpaired_reads == "output":
                        yield read, None, "single_read"

            if read.is_unmapped:
                if self.options.paired:
                    self.read_events["Both unmapped" if read.mate_is_unmapped else "Read 1 unmapped"] += 1
                else:
                    self.read_events["Single end unmapped"] += 1
                if self.return_unmapped:
                    self.read_events["Input Reads"] += 1
                    yield read, None, "single_read"
                continue

            if self.options.paired and read.mate_is_unmapped:
                self.read_events["Read 2 unmapped"] += 1
                if self.options.unmapped_reads != "use":
                    if self.return_unmapped:
                        yield read, None, "single_read"
                    continue

            if read.is_paired and read.reference_name != read.next_reference_name:
                self.read_events["Chimeric read pair"] += 1
                if self.options.chimeric_pairs == "discard":
                    continue
                if self.options.chimeric_pairs == "output":
                    yield read, None, "single_read"
                    continue

            if self.options.subset and random.random() >= self.options.subset:
                self.read_events["Randomly excluded"] += 1
                continue
            if self.options.mapping_quality and read.mapq < self.options.mapping_quality:
                self.read_events["< MAPQ threshold"] += 1
                continue

            if self.options.ignore_umi:
                if self.options.per_cell:
                    umi, cell = self.barcode_getter(read)
                    umi = ""
                else:
                    umi, cell = "", ""
            else:
                try:
                    umi, cell = self.barcode_getter(read)
                except KeyError:
                    error_msg = "Read skipped, missing umi and/or cell tag"
                    if self.read_events[error_msg] == 0:
                        try:
                            formatted_read = read.to_string()
                        except AttributeError:
                            formatted_read = read.query_name
                        U.warn("At least one read is missing UMI and/or cell tag(s): %s" % formatted_read)
                    self.read_events[error_msg] += 1
                    continue

            self.current_chr = read.reference_name

            if self.options.per_gene:
                if self.options.per_contig:
                    if self.metacontig_contig:
                        transcript = read.reference_name
                        gene = self.contig_metacontig[transcript]
                    else:
                        gene = read.reference_name
                elif getattr(self.options, "feature_map", None):
                    gene = ordered_feature
                    if not gene:
                        self.read_events["Read skipped - no external assignment"] += 1
                        continue
                    assigned = gene
                    if gene == "":
                        if self.read_events["Read skipped - gene string is empty"] == 0:
                            U.warn("Assigned gene is empty string. First such read:\n%s" % read.to_string())
                        self.read_events["Read skipped - gene string is empty"] += 1
                        continue
                    if re.search(self.options.skip_regex, assigned):
                        self.read_events["Read skipped - assigned tag matches skip_regex"] += 1
                        continue
                else:
                    try:
                        assigned = read.get_tag(self.options.assigned_tag)
                        gene = read.get_tag(self.options.gene_tag)
                    except KeyError:
                        self.read_events["Read skipped, no tag"] += 1
                        continue
                    if gene == "":
                        if self.read_events["Read skipped - gene string is empty"] == 0:
                            U.warn("Assigned gene is empty string. First such read:\n%s" % read.to_string())
                        self.read_events["Read skipped - gene string is empty"] += 1
                        continue
                    if re.search(self.options.skip_regex, assigned):
                        self.read_events["Read skipped - assigned tag matches skip_regex"] += 1
                        continue

                pos = gene
                key = pos
                do_output, out_keys = self.check_output() if self.last_chr else (False, None)
                if do_output:
                    for p in out_keys:
                        for k in sorted(self.reads_dict[p].keys()):
                            yield self.reads_dict[p][k], k, "bundle"
                        del self.reads_dict[p]
                self.last_chr = self.current_chr
                self.last_pos = pos
            else:
                self.start, pos, is_spliced = get_read_position(read, self.options.soft_clip_threshold)
                do_output, out_keys = self.check_output()
                if do_output:
                    for p in out_keys:
                        for k in sorted(self.reads_dict[p].keys()):
                            yield self.reads_dict[p][k], k, "bundle"
                        del self.reads_dict[p]
                        if p in self.read_counts:
                            del self.read_counts[p]
                self.last_pos = self.start
                self.last_chr = self.current_chr
                r_length = read.query_length if self.options.read_length else 0
                key = (read.is_reverse, self.options.spliced and is_spliced,
                       (not self.options.ignore_tlen) * self.options.paired * read.tlen, r_length)

            key = (key, cell)
            self.update_dicts(read, pos, key, umi)
            if self.metacontig_contig:
                self.observed_contigs[gene].add(transcript)

        for p in sorted(self.reads_dict.keys()):
            for k in sorted(self.reads_dict[p].keys()):
                yield self.reads_dict[p][k], k, "bundle"
        if self.feature_assignments is not None:
            trailing = self.feature_assignments.readline()
            self.feature_assignments.close()
            if trailing != "":
                raise ValueError("Feature sidecar contains more records than the BAM stream")
