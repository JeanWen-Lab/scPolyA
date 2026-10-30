# stages/init_references.py
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from logging_config import get_logger, LOG_MAIN, LOG_STAGE_init_references, LoggersWrapper, stage_scope, task_scope
from task_manage.run_layout import RunLayout, UNFILTERED_STD_CHROM_SIZES, FILTERED_STD_CHROM_SIZES, \
    UNFILTERED_ADJUSTED_GTF, \
    FILTERED_ADJUSTED_GTF, GENE_BED, SORTED_GENE_BED, GFFDB, CANONICAL_TRANSCRIPTS, STOP_CODON_POSITIONS, \
    STOP_CODON_POSITIONS_TSV, SORTED_STOP_CODON_BED, SORTED_STOP_CODON_BIGBED, CDS_GTF, CDS_BED, SORTED_CDS_BED, \
    UNFILTERED_ADJUSTED_BULK_WINDOW_GTF, FILTERED_ADJUSTED_BULK_WINDOW_GTF, BULK_WINDOW_BED, SORTED_BULK_WINDOW_BED, \
    UNFILTERED_ADJUSTED_BULK_GENE_GTF, FILTERED_ADJUSTED_BULK_GENE_GTF, BULK_GENE_BED, SORTED_BULK_GENE_BED
from task_manage import run_flex_batch  # <--- 使用 Flex
from task_manage import ScItem  # <--- 引入 ScItem
from utils.annotation_utils import get_canonical_transcripts, get_stop_codon_positions, write_stop_codon_positions, \
    extract_and_prepare_cds, bulk_three_prime_gtf_to_bed6, bulk_gene_gtf_to_bed6
from utils.chromosome_utils import prefilter_gtf_python, validate_filtered_gtf, get_standard_chromosomes, \
    filter_chromosome_sizes, check_and_adjust_gtf
from utils.io_utils import convert_gtf_to_bed, bedtools_sort, create_or_load_gffdb, load_dict_from_json, \
    convert_bed_to_bigbed

if TYPE_CHECKING:
    from task_manage import TaskManager, Resources

from task_manage.define import (META_BAM_FILES_HAS_CHR_PREFIX, META_STANDARD_CHROMOSOMES,
                                CACHE_CANONICAL_TRANSCRIPTS, CACHE_STOP_CODON_POSITIONS)

STAGE = LOG_STAGE_init_references
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)


def resolve_standard_chromosomes(
    *,
    species: str,
    remove_mt: bool,
    bam_has_chr_prefix: bool,
    requested_contigs: Iterable[str] | None = None,
) -> list[str]:
    """Return the standard contigs for this run, optionally narrowed explicitly."""
    standard_chroms = get_standard_chromosomes(species=species)
    if remove_mt:
        standard_chroms = [chrom for chrom in standard_chroms if chrom not in {"M", "MT"}]

    if bam_has_chr_prefix:
        standard_chroms = [f"chr{chrom}" if not str(chrom).startswith("chr") else chrom for chrom in standard_chroms]
        standard_chroms = ["chrM" if chrom == "chrMT" else chrom for chrom in standard_chroms]

    if isinstance(requested_contigs, str):
        requested_values = (requested_contigs,)
    else:
        requested_values = requested_contigs or ()
    requested_raw = tuple(str(contig).strip() for contig in requested_values)
    if not requested_raw:
        return standard_chroms
    if any("," in contig for contig in requested_raw):
        raise ValueError("--contigs values must be passed separately, not as comma-separated text.")

    requested = tuple(
        _normalize_requested_contig(contig, bam_has_chr_prefix=bam_has_chr_prefix)
        for contig in requested_raw
    )
    if len(set(requested)) != len(requested):
        raise ValueError("--contigs resolves to duplicate contig names after normalization.")

    unsupported = sorted(set(requested) - set(standard_chroms))
    if unsupported:
        available = ", ".join(standard_chroms)
        raise ValueError(
            "--contigs must use standard contig names matching the input BAMs; "
            f"unsupported={unsupported}; available={available}"
        )
    return [chrom for chrom in standard_chroms if chrom in set(requested)]


def _normalize_requested_contig(contig: str, *, bam_has_chr_prefix: bool) -> str:
    value = str(contig).strip()
    if bam_has_chr_prefix:
        value = value if value.startswith("chr") else f"chr{value}"
        return "chrM" if value == "chrMT" else value

    value = value[3:] if value.startswith("chr") else value
    return "M" if value == "MT" else value


# region: APIs

def init_references(tm: TaskManager, layout: RunLayout, args, cache: Resources):
    # --- 定义具体的处理函数 (逻辑保持不变) ---
    def backup_gtf():
        gtf_original = Path(args.gene_gtf)
        gtf_backup = layout.Backup / gtf_original.name
        try:
            shutil.copy(gtf_original, gtf_backup)
            loggers.info(f"Backup created: '{gtf_backup}'")
        except Exception as e:
            loggers.raise_error(f"Failed to create backup for '{gtf_original}': {e}", e=Exception)

        bulk_window_gtf = Path(args.bulk_window_gtf) if args.bulk_window_gtf else None
        if bulk_window_gtf is not None:
            bulk_window_gtf_backup = layout.Backup / bulk_window_gtf.name
            try:
                shutil.copy(bulk_window_gtf, bulk_window_gtf_backup)
                loggers.info(f"Backup for bulk window gtf created: '{bulk_window_gtf_backup}'")
            except Exception as e:
                loggers.raise_error(f"Failed to create backup for '{bulk_window_gtf_backup}': {e}", e=Exception)
        else:
            loggers.info(f"Backup for bulk window gtf not created for '{bulk_window_gtf}'")
        bulk_gene_gtf = Path(args.bulk_gene_gtf) if args.bulk_gene_gtf else None
        if bulk_gene_gtf is not None:
            bulk_gene_gtf_backup = layout.Backup / bulk_gene_gtf.name
            try:
                shutil.copy(bulk_gene_gtf, bulk_gene_gtf_backup)
                loggers.info(f"Backup for bulk gene gtf created: '{bulk_gene_gtf_backup}'")
            except Exception as e:
                loggers.raise_error(f"Failed to create backup for '{bulk_gene_gtf_backup}': {e}", e=Exception)
        else:
            loggers.info(f"Backup for bulk gene gtf not created for '{bulk_gene_gtf}'")



    def adjust_chrom_sizes():
        bam_has_chr_prefix = tm.task_meta_get(META_BAM_FILES_HAS_CHR_PREFIX)
        standard_chroms = resolve_standard_chromosomes(
            species=args.species,
            remove_mt=args.remove_mt,
            bam_has_chr_prefix=bool(bam_has_chr_prefix),
            requested_contigs=getattr(args, "contigs", None),
        )
        if args.remove_mt:
            logger_stage.info("Configured mitochondrial contig exclusion.")
        if getattr(args, "contigs", None):
            logger_stage.info(f"Restricted standard chromosomes to: {', '.join(standard_chroms)}")

        tm.task_meta_set(key=META_STANDARD_CHROMOSOMES, value=standard_chroms)

        chrom_sizes_output = layout.ReferenceFile(file_key=UNFILTERED_STD_CHROM_SIZES, args=args)
        chrom_sizes_output.unlink(missing_ok=True)

        if bam_has_chr_prefix:
            from utils.chromosome_utils import add_chr_prefix_to_chrom_sizes
            add_chr_prefix_to_chrom_sizes(args.chrom_sizes, str(chrom_sizes_output))
        else:
            from utils.chromosome_utils import remove_chr_prefix_from_chrom_sizes
            remove_chr_prefix_from_chrom_sizes(args.chrom_sizes, str(chrom_sizes_output))

    def adjust_chrom_sizes_2():
        standard_chroms = tm.task_meta_get(key=META_STANDARD_CHROMOSOMES)
        cache.set(META_STANDARD_CHROMOSOMES, value=standard_chroms)

    def filter_chrom_sizes():
        # input
        standard_chroms = tm.task_meta_get(key=META_STANDARD_CHROMOSOMES)
        standardized_sizes = layout.ReferenceFile(file_key=UNFILTERED_STD_CHROM_SIZES, args=args)
        # output
        filtered_sizes = layout.ReferenceFile(file_key=FILTERED_STD_CHROM_SIZES, args=args)
        filtered_sizes.unlink(missing_ok=True)
        filter_chromosome_sizes(input_chrom_sizes=str(standardized_sizes),
                                output_chrom_sizes=str(filtered_sizes),
                                standard_chroms=standard_chroms)

    def check_adjust_gtf():
        # input
        bam_has_chr_prefix = tm.task_meta_get(key=META_BAM_FILES_HAS_CHR_PREFIX)
        bam_paths = list(layout.Inputs.glob("*.bam"))
        if not bam_paths:
            loggers.raise_error("No BAM files found under layout.Inputs; cannot adjust GTF to BAM contig naming.")
        standard_chroms = tm.task_meta_get(key=META_STANDARD_CHROMOSOMES)
        # output
        adjusted_gtf = layout.ReferenceFile(file_key=UNFILTERED_ADJUSTED_GTF, args=args)
        adjusted_gtf.unlink(missing_ok=True)
        # exec.
        check_and_adjust_gtf(args.gene_gtf,
                             bam_has_chr_prefix=bam_has_chr_prefix,
                             bam_paths=bam_paths,
                             standard_chroms=standard_chroms,
                             gtf_output=str(adjusted_gtf))


        if args.bulk_window_gtf:
            # output
            adjusted_bulk_gtf = layout.ReferenceFile(file_key=UNFILTERED_ADJUSTED_BULK_WINDOW_GTF, args=args)
            adjusted_bulk_gtf.unlink(missing_ok=True)
            # exec.
            check_and_adjust_gtf(args.bulk_window_gtf,
                                 bam_has_chr_prefix=bam_has_chr_prefix,
                                 bam_paths=bam_paths,
                                 standard_chroms=standard_chroms,
                                 gtf_output=str(adjusted_bulk_gtf))

        if args.bulk_gene_gtf:
            # output
            adjusted_bulk_gene_gtf = layout.ReferenceFile(file_key=UNFILTERED_ADJUSTED_BULK_GENE_GTF, args=args)
            adjusted_bulk_gene_gtf.unlink(missing_ok=True)
            # exec.
            check_and_adjust_gtf(args.bulk_gene_gtf,
                                 bam_has_chr_prefix=bam_has_chr_prefix,
                                 bam_paths=bam_paths,
                                 standard_chroms=standard_chroms,
                                 gtf_output=str(adjusted_bulk_gene_gtf))


    def prefilter_validate_gtf():
        # input
        adjusted_gtf = layout.ReferenceFile(file_key=UNFILTERED_ADJUSTED_GTF, args=args)
        # output
        filtered_gtf = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_GTF, args=args)
        filtered_gtf.unlink(missing_ok=True)
        # exec.
        prefilter_gtf_python(original_gtf=str(adjusted_gtf), filtered_gtf=str(filtered_gtf))
        validate_filtered_gtf(filtered_gtf=str(filtered_gtf))

        # bulk window gtf
        if args.bulk_window_gtf:
            adjusted_bulk_gtf = layout.ReferenceFile(file_key=UNFILTERED_ADJUSTED_BULK_WINDOW_GTF, args=args)
            filtered_bulk_gtf = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_BULK_WINDOW_GTF, args=args)
            filtered_bulk_gtf.unlink(missing_ok=True)
            # exec.
            prefilter_gtf_python(original_gtf=str(adjusted_bulk_gtf), filtered_gtf=str(filtered_bulk_gtf))
            validate_filtered_gtf(filtered_gtf=str(filtered_bulk_gtf))

        if args.bulk_gene_gtf:
            adjusted_bulk_gene_gtf = layout.ReferenceFile(file_key=UNFILTERED_ADJUSTED_BULK_GENE_GTF, args=args)
            filtered_bulk_gene_gtf = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_BULK_GENE_GTF, args=args)
            filtered_bulk_gene_gtf.unlink(missing_ok=True)
            # exec.
            prefilter_gtf_python(original_gtf=str(adjusted_bulk_gene_gtf), filtered_gtf=str(filtered_bulk_gene_gtf))
            validate_filtered_gtf(filtered_gtf=str(filtered_bulk_gene_gtf))


    def gtf_to_bed():
        # input
        input_gtf = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_GTF, args=args)
        # output
        bed_output = layout.ReferenceFile(file_key=GENE_BED, args=args)
        bed_output.unlink(missing_ok=True)
        # exec.
        convert_gtf_to_bed(input_gtf, str(bed_output))

        if args.bulk_window_gtf:
            # input
            input_bulk_gtf = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_BULK_WINDOW_GTF, args=args)
            # output
            bed_bulk_output = layout.ReferenceFile(file_key=BULK_WINDOW_BED, args=args)
            bed_bulk_output.unlink(missing_ok=True)
            # exec.
            bulk_three_prime_gtf_to_bed6(input_bulk_gtf, bed_bulk_output)

        if args.bulk_gene_gtf:
            # input
            input_bulk_gene_gtf = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_BULK_GENE_GTF, args=args)
            # output
            bed_bulk_gene_output = layout.ReferenceFile(file_key=BULK_GENE_BED, args=args)
            bed_bulk_gene_output.unlink(missing_ok=True)
            # exec.
            bulk_gene_gtf_to_bed6(input_bulk_gene_gtf, bed_bulk_gene_output)

    def sort_gene_bed():
        # input
        input_bed = layout.ReferenceFile(file_key=GENE_BED, args=args)
        filtered_chrom_sizes = layout.ReferenceFile(file_key=FILTERED_STD_CHROM_SIZES, args=args)
        # output
        sorted_gene_bed = layout.ReferenceFile(file_key=SORTED_GENE_BED, args=args)
        sorted_gene_bed.unlink(missing_ok=True)
        # exec.
        bedtools_sort(input_bed=input_bed, sorted_bed=sorted_gene_bed, chrom_sizes_sorted=filtered_chrom_sizes)

        if args.bulk_window_gtf:
            input_bulk_bed = layout.ReferenceFile(file_key=BULK_WINDOW_BED, args=args)
            sorted_bulk_bed = layout.ReferenceFile(file_key=SORTED_BULK_WINDOW_BED, args=args)
            sorted_bulk_bed.unlink(missing_ok=True)
            # exec.
            bedtools_sort(input_bed=input_bulk_bed, sorted_bed=sorted_bulk_bed, chrom_sizes_sorted=filtered_chrom_sizes)

        if args.bulk_gene_gtf:
            input_bulk_gene_bed = layout.ReferenceFile(file_key=BULK_GENE_BED, args=args)
            sorted_bulk_gene_bed = layout.ReferenceFile(file_key=SORTED_BULK_GENE_BED, args=args)
            sorted_bulk_gene_bed.unlink(missing_ok=True)
            # exec.
            bedtools_sort(input_bed=input_bulk_gene_bed,
                          sorted_bed=sorted_bulk_gene_bed,
                          chrom_sizes_sorted=filtered_chrom_sizes)

    # def build_gffdb():
    #     # input
    #     filtered_gtf_path = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_GTF, args=args)
    #     # output
    #     db_path = layout.Reference / layout.ReferenceFile(GFFDB, args=args)
    #     db_path.unlink(missing_ok=True)
    #     # exec.
    #     gffdb = create_or_load_gffdb(filtered_gtf_path=filtered_gtf_path, db_path=db_path, mode="create")
    #     cache.set(CACHE_GFFDB, gffdb)

    def build_gffdb():
        # input
        filtered_gtf_path = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_GTF, args=args)
        # output
        db_path = layout.ReferenceFile(file_key=GFFDB, args=args)
        db_path.unlink(missing_ok=True)
        # exec.
        create_or_load_gffdb(filtered_gtf_path=filtered_gtf_path, db_path=db_path, mode="create")

    # def build_gffdb_2():
    #     if not cache.has(CACHE_GFFDB):
    #         db_path = layout.Reference / layout.ReferenceFile(GFFDB, args=args)
    #         gffdb = create_or_load_gffdb(db_path=db_path)
    #         cache.set(CACHE_GFFDB, gffdb)

    def cache_canonical_transcripts():
        # input
        gffdb = create_or_load_gffdb(layout.ReferenceFile(file_key=GFFDB, args=args))
        # output
        canonical_transcripts_path = layout.ReferenceFile(file_key=CANONICAL_TRANSCRIPTS, args=args)
        canonical_transcripts_path.unlink(missing_ok=True)
        # exec.
        get_canonical_transcripts(gffdb=gffdb, save_path=str(canonical_transcripts_path))

    def cache_canonical_transcripts_2():
        canonical_transcripts_path = layout.ReferenceFile(file_key=CANONICAL_TRANSCRIPTS, args=args)
        canonical_transcripts = load_dict_from_json(str(canonical_transcripts_path))
        cache.set(CACHE_CANONICAL_TRANSCRIPTS, value=canonical_transcripts)

    def cache_stop_codon():
        # input
        gffdb = create_or_load_gffdb(layout.ReferenceFile(file_key=GFFDB, args=args))
        canonical_transcripts = cache.get(CACHE_CANONICAL_TRANSCRIPTS)
        # output
        stop_codon_positions_json = layout.ReferenceFile(STOP_CODON_POSITIONS, args=args)
        stop_codon_positions_json.unlink(missing_ok=True)
        # exec. 1
        stop_codon_positions = get_stop_codon_positions(db=gffdb,
                                                        canonical_transcripts=canonical_transcripts,
                                                        save_path=str(stop_codon_positions_json))
        # output
        stop_codon_positions_tsv = layout.ReferenceFile(STOP_CODON_POSITIONS_TSV, args=args)
        stop_codon_positions_tsv.unlink(missing_ok=True)
        # exec. 2
        write_stop_codon_positions(stop_codon_positions=stop_codon_positions,
                                   stop_codon_output_file=str(stop_codon_positions_tsv))

    def cache_stop_codon_2():
        stop_codon_path = layout.ReferenceFile(STOP_CODON_POSITIONS, args=args)
        stop_codon_positions = load_dict_from_json(str(stop_codon_path))
        cache.set(CACHE_STOP_CODON_POSITIONS, value=stop_codon_positions)

    def generate_stop_codon_bigbed():
        # input
        stop_codon_positions_tsv = layout.ReferenceFile(STOP_CODON_POSITIONS_TSV, args=args)
        filtered_chrom_sizes = layout.ReferenceFile(FILTERED_STD_CHROM_SIZES, args=args)
        # output
        sorted_stop_codon_bed = layout.ReferenceFile(SORTED_STOP_CODON_BED, args=args)
        sorted_stop_codon_bigbed = layout.ReferenceFile(SORTED_STOP_CODON_BIGBED, args=args)
        # exec.
        bedtools_sort(input_bed=str(stop_codon_positions_tsv),
                      sorted_bed=str(sorted_stop_codon_bed),
                      chrom_sizes_sorted=str(filtered_chrom_sizes))

        convert_bed_to_bigbed(input_bed=str(sorted_stop_codon_bed),
                              output_bigbed=str(sorted_stop_codon_bigbed),
                              chrom_sizes=str(filtered_chrom_sizes),
                              bedtobigbed_path='bedToBigBed')
        # tm.task_meta_set(META_sorted_cds_bed_path, str(sorted_stop_codon_bed))

    def prepare_cds():
        # cds_gtf = layout.Reference / REF_cds_gft(args.gene_gtf)
        # cds_bed = layout.Reference / REF_cds_bed(args.gene_gtf)
        # sorted_cds_bed = layout.Reference / REF_sorted_cds_bed(args.gene_gtf)
        # filtered_gtf = layout.Reference / REF_filtered_gtf(args.gene_gtf)

        # input
        filtered_gtf = layout.ReferenceFile(file_key=FILTERED_ADJUSTED_GTF, args=args)
        filtered_chrom_sizes = layout.ReferenceFile(FILTERED_STD_CHROM_SIZES, args=args)
        # output
        cds_gtf = layout.ReferenceFile(file_key=CDS_GTF, args=args)
        cds_bed = layout.ReferenceFile(file_key=CDS_BED, args=args)
        sorted_cds_bed = layout.ReferenceFile(file_key=SORTED_CDS_BED, args=args)
        cds_gtf.unlink(missing_ok=True)
        cds_bed.unlink(missing_ok=True)
        sorted_cds_bed.unlink(missing_ok=True)
        # exec.
        extract_and_prepare_cds(
            filtered_gtf=str(filtered_gtf),
            output_dir=layout.Reference,
            cds_gtf=str(cds_gtf),
            cds_bed=str(cds_bed),
            sorted_cds_bed=str(sorted_cds_bed),
            chrom_sizes_sorted=str(filtered_chrom_sizes)
        )

    # 任务字典：区分“执行任务”和“加载/别名任务”
    TASKS = {
        "backup_gtf": backup_gtf,
        "adjust_chrom_sizes": adjust_chrom_sizes,
        "adjust_chrom_sizes_2": (adjust_chrom_sizes_2, "adjust_chrom_sizes"),
        "filter_chrom_sizes": filter_chrom_sizes,
        # "filter_chrom_sizes_2": (filter_chrom_sizes_2, "filter_chrom_sizes"),  # Loader
        "check_adjust_gtf": check_adjust_gtf,
        "prefilter_validate_gtf": prefilter_validate_gtf,
        "gtf_to_bed": gtf_to_bed,
        "sort_gene_bed": sort_gene_bed,
        "build_gffdb": build_gffdb,
        # "build_gffdb_2": (build_gffdb_2, "build_gffdb"),  # Loader
        "cache_canonical_transcripts": cache_canonical_transcripts,
        "cache_canonical_transcripts_2": (cache_canonical_transcripts_2, "cache_canonical_transcripts"),  # Loader
        "cache_stop_codon": cache_stop_codon,
        "cache_stop_codon_2": (cache_stop_codon_2, "cache_stop_codon"),  # Loader
        "generate_stop_codon_bigbed": generate_stop_codon_bigbed,
        "prepare_cds": prepare_cds
    }

    # 创建一个全局 ScItem 载体
    global_item = ScItem(uid="global")

    with stage_scope(STAGE):
        for task_base in TASKS:
            with task_scope(task_base):
                entry = TASKS[task_base]
                if isinstance(entry, Callable):
                    run_flex_batch(
                        tm=tm,
                        stage=STAGE,
                        task_base=task_base,
                        items=[global_item],  # 列表只包含一个 "global" item
                        identity_keys=["uid"],  # 身份是 uid='global'
                        param_keys=[],  # 全局任务通常没有变体参数 (除非你想加上 args)
                        fn=lambda x: entry(),
                        desc=task_base,
                        max_workers=1,
                    )

                else:
                    # 这是一个 Tuple (Loader/Alias)：保持原样直接运行
                    # Loader 通常是轻量级的内存加载，不需要走数据库 Task Check
                    fn, task_name = entry
                    with task_scope(task_name):
                        fn()
