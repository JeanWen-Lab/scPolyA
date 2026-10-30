# stages/register_inputs.py
from __future__ import annotations

import csv
import hashlib
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from logging_config import get_logger, LOG_MAIN, LoggersWrapper, stage_scope, task_scope, LOG_STAGE_register_inputs
from task_manage import run_flex_batch  # <--- 使用 Flex
from task_manage import ScItem  # <--- 引入 ScItem
from task_manage.define import (
    META_BAM_FILES_HAS_CHR_PREFIX,
    INPUTS_CSV_NAME,
    PREPROCESS_INPUTS_CSV_NAME,
    SOURCE_TYPE_FILE,
    SOURCE_TYPE_DIR,
    FLAG_EXTERNAL, FLAG_UNDEDUPED
)

if TYPE_CHECKING:
    from task_manage import TaskManager, Resources
    from task_manage.run_layout import RunLayout

STAGE = LOG_STAGE_register_inputs
logger_main = get_logger(LOG_MAIN)
logger_stage = get_logger(STAGE)
loggers = LoggersWrapper(main=logger_main, sub=logger_stage)

PREPROCESS_FIELDS = (
    "pp_id",
    "pp_path",
    "sample_id",
    "source_type",
    "contig_name",
    "region_label",
    "dedup_status",
    "is_deduped",
    "requires_split",
    "barcode_suffix",
)


@dataclass(frozen=True)
class PlannedBam:
    source_bam: Path
    source_bai: Path | None
    contig_name: str
    region_label: str
    link_bam: Path


@dataclass(frozen=True)
class ValidatedInputRow:
    sample_id: str
    source_path: Path
    source_type: str
    is_deduped: int
    requires_split: int
    dedup_status: str
    barcode_suffix: str
    bams: tuple[PlannedBam, ...]


@dataclass(frozen=True)
class RegistrationPlan:
    rows: tuple[ValidatedInputRow, ...]
    bam_has_chr_prefix: bool


def register_inputs(tm: TaskManager,
                    layout: RunLayout,
                    args,
                    cache: Resources):
    """
    Stage: register_inputs (Flex Version)
    """
    with stage_scope("register_inputs"):
        # shared state across sub-tasks
        in_csv: Path = Path(args.inputs).expanduser().resolve()
        if not in_csv.exists():
            loggers.raise_error(f"inputs.csv not found: {in_csv}")

        plan = _validate_registration_plan(in_csv, layout)

    def copy_inputs_csv():
        dst_inputs = layout.InputTables / INPUTS_CSV_NAME
        if dst_inputs.is_symlink():
            raise ValueError(
                f"[{STAGE}] Existing inputs.csv copy is a symlink: {dst_inputs}. "
                "Use a new run root or repair it manually."
            )
        if dst_inputs.exists():
            if _sha256(dst_inputs) != _sha256(in_csv):
                raise ValueError(
                    f"[{STAGE}] inputs.csv changed within an existing run root: {dst_inputs}. "
                    "Use a new run root or repair it manually."
                )
            return
        dst_inputs.write_bytes(in_csv.read_bytes())
        loggers.info(f"Copied inputs.csv -> {dst_inputs}")

    def parse_and_link():
        """
        Parse input rows and create symlinks under {Root}/inputs.
        """
        for row in plan.rows:
            for bam in row.bams:
                _safe_symlink(bam.link_bam, bam.source_bam)
                _ensure_bai_for_link(
                    bam.link_bam, bam.source_bam, bam.source_bai,
                    prepared_bai=prepared_indexes.get(bam.link_bam),
                )

        tm.task_meta_set(META_BAM_FILES_HAS_CHR_PREFIX, int(plan.bam_has_chr_prefix))
        loggers.info(f"Set {META_BAM_FILES_HAS_CHR_PREFIX}={int(plan.bam_has_chr_prefix)}")
        loggers.info(f"Parsed {len(_planned_preprocess_rows(plan))} preprocess rows")

    def write_preprocess_csv():
        out_csv = layout.InputTables / PREPROCESS_INPUTS_CSV_NAME
        preprocess_rows = _planned_preprocess_rows(plan)
        with open(out_csv, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=PREPROCESS_FIELDS,
            )
            writer.writeheader()
            writer.writerows(preprocess_rows)
        loggers.info(f"Wrote preprocess_inputs.csv -> {out_csv}")

    TASK_IMPL = {
        "copy_inputs_csv": copy_inputs_csv,
        "parse_and_link": parse_and_link,
        "write_preprocess_csv": write_preprocess_csv,
    }

    # 创建 Global Item
    global_item = ScItem(uid="global")

    # Finish missing-index generation before publishing input tables/links or
    # recording any registration task completion.
    with _prepared_registration_indexes(plan, layout.Root) as prepared_indexes, stage_scope(STAGE):
        for task_base, fn in TASK_IMPL.items():
            with task_scope(task_base):
                run_flex_batch(
                    tm=tm,
                    stage=STAGE,
                    task_base=task_base,
                    items=[global_item],
                    identity_keys=["uid"],  # uid='global'
                    param_keys=[],
                    fn=lambda x: fn(),
                    desc=task_base,
                    max_workers=1,
                )

# region helpers

@contextmanager
def _prepared_registration_indexes(plan: RegistrationPlan, scratch_parent: Path):
    missing = [
        bam for row in plan.rows for bam in row.bams
        if bam.source_bai is None and not Path(f"{bam.link_bam}.bai").exists()
    ]
    if not missing:
        yield {}
        return
    import pysam
    with tempfile.TemporaryDirectory(prefix=".scpolya-register-index-", dir=scratch_parent) as tmpdir:
        prepared = {}
        for number, bam in enumerate(missing):
            index = Path(tmpdir) / f"{number}.bai"
            try:
                pysam.index("-o", str(index), str(bam.source_bam))
                _validate_existing_bai(bam.source_bam, index)
            except Exception as exc:
                raise ValueError(
                    f"[{STAGE}] Failed to prepare missing BAM index before publication: {bam.source_bam}"
                ) from exc
            prepared[bam.link_bam] = index
        yield prepared


def _validate_registration_plan(in_csv: Path, layout: RunLayout) -> RegistrationPlan:
    if layout.Inputs.is_symlink():
        raise ValueError(
            f"[{STAGE}] Inputs directory must not be a symlink: {layout.Inputs}. "
            "Use a new run root or repair it manually."
        )
    if layout.InputTables.is_symlink():
        raise ValueError(
            f"[{STAGE}] InputTables directory must not be a symlink: {layout.InputTables}. "
            "Use a new run root or repair it manually."
        )
    required = {"sample_id", "source_path"}
    with in_csv.open("r", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        if len(fieldnames) != len(set(fieldnames)):
            raise ValueError(f"[{STAGE}] inputs.csv contains duplicate column names")
        if not required.issubset(fieldnames):
            raise ValueError(f"[{STAGE}] inputs.csv must contain columns: {sorted(required)}")
        raw_rows = list(reader)
    if not raw_rows:
        raise ValueError(f"[{STAGE}] inputs.csv must contain at least one data row")

    rows: list[ValidatedInputRow] = []
    seen_sample_ids: set[str] = set()
    prefix_values: list[bool] = []
    for row_number, raw_row in enumerate(raw_rows, start=2):
        if None in raw_row:
            raise ValueError(f"[{STAGE}] row {row_number} contains extra unnamed fields")
        sample_id = _validate_sample_id(raw_row.get("sample_id"), row_number=row_number)
        if sample_id in seen_sample_ids:
            raise ValueError(f"[{STAGE}] duplicate sample_id at row {row_number}: {sample_id!r}")
        seen_sample_ids.add(sample_id)

        source_text = raw_row.get("source_path")
        if source_text is None or not str(source_text).strip():
            raise ValueError(f"[{STAGE}] source_path is required at row {row_number}")
        try:
            source_path = Path(str(source_text).strip()).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(
                f"[{STAGE}] source_path is missing or cannot be resolved at row {row_number}: "
                f"{source_text!r}"
            ) from exc

        is_deduped = _as_int01(raw_row.get("is_deduped"), default=0)
        requires_split = _as_int01(raw_row.get("requires_split"), default=0)
        dedup_status = FLAG_EXTERNAL if is_deduped else FLAG_UNDEDUPED
        barcode_suffix = str(raw_row.get("barcode_suffix") or "").strip()
        planned_bams: list[PlannedBam] = []

        if source_path.is_file():
            source_type = SOURCE_TYPE_FILE
            source_entries = ((source_path, "all", "raw"),)
        elif source_path.is_dir():
            source_type = SOURCE_TYPE_DIR
            prefix = f"{sample_id}."
            matching = sorted(
                entry
                for entry in source_path.iterdir()
                if entry.name.startswith(prefix) and entry.name.endswith(".bam")
            )
            if not matching:
                raise ValueError(
                    f"[{STAGE}] No BAMs found matching {sample_id}.*.bam in {source_path}"
                )
            non_files = [entry for entry in matching if not entry.is_file()]
            if non_files:
                raise ValueError(
                    f"[{STAGE}] Directory source contains non-file BAM matches: "
                    + ", ".join(str(entry) for entry in non_files)
                )
            source_entries = tuple(
                (entry, entry.name[len(prefix):-4], entry.name[len(prefix):-4])
                for entry in matching
            )
            if any(not contig for _, contig, _ in source_entries):
                raise ValueError(f"[{STAGE}] Cannot parse an empty contig in {source_path}")
            requires_split = 0
        else:
            raise ValueError(
                f"[{STAGE}] source_path is neither a regular file nor directory: {source_path}"
            )

        for source_bam, contig_name, region_label in source_entries:
            prefix_values.append(_bam_has_chr_prefix(source_bam))
            source_bai = _resolve_source_bai(source_bam)
            link_bam = layout.Inputs / f"{sample_id}.{region_label}.{dedup_status}.bam"
            planned_bams.append(
                PlannedBam(
                    source_bam=source_bam,
                    source_bai=source_bai,
                    contig_name=contig_name,
                    region_label=region_label,
                    link_bam=link_bam,
                )
            )

        rows.append(
            ValidatedInputRow(
                sample_id=sample_id,
                source_path=source_path,
                source_type=source_type,
                is_deduped=is_deduped,
                requires_split=requires_split,
                dedup_status=dedup_status,
                barcode_suffix=barcode_suffix,
                bams=tuple(planned_bams),
            )
        )

    _validate_plan_destinations(rows)
    _validate_existing_csv_copy(in_csv, layout.InputTables / INPUTS_CSV_NAME)
    for row in rows:
        for bam in row.bams:
            _validate_link_and_index(bam.link_bam, bam.source_bam, bam.source_bai)

    plan = RegistrationPlan(
        rows=tuple(rows),
        bam_has_chr_prefix=prefix_values[0] if prefix_values else False,
    )
    _validate_existing_preprocess_csv(
        plan,
        layout.InputTables / PREPROCESS_INPUTS_CSV_NAME,
    )
    return plan


def _validate_sample_id(value, *, row_number: int) -> str:
    if value is None:
        raise ValueError(f"[{STAGE}] sample_id is required at row {row_number}")
    sample_id = str(value)
    if not sample_id or sample_id != sample_id.strip() or any(ch.isspace() for ch in sample_id):
        raise ValueError(f"[{STAGE}] sample_id must be non-blank with no whitespace: {value!r}")
    if not sample_id.isprintable():
        raise ValueError(f"[{STAGE}] sample_id contains control characters: {value!r}")
    if sample_id in {".", ".."} or Path(sample_id).is_absolute():
        raise ValueError(f"[{STAGE}] sample_id must be one relative path component: {value!r}")
    if "/" in sample_id or "\\" in sample_id or len(Path(sample_id).parts) != 1:
        raise ValueError(f"[{STAGE}] sample_id must be one relative path component: {value!r}")
    return sample_id


def _validate_existing_csv_copy(in_csv: Path, copied_csv: Path) -> None:
    if copied_csv.is_symlink():
        raise ValueError(
            f"[{STAGE}] Existing inputs.csv copy is a symlink: {copied_csv}. "
            "Use a new run root or repair it manually."
        )
    if copied_csv.exists() and _sha256(copied_csv) != _sha256(in_csv):
        raise ValueError(
            f"[{STAGE}] inputs.csv changed within an existing run root: {copied_csv}. "
            "Use a new run root or repair it manually."
        )


def _validate_plan_destinations(rows: list[ValidatedInputRow]) -> None:
    link_owners: dict[Path, tuple[str, Path]] = {}
    pp_id_owners: dict[str, tuple[str, Path]] = {}
    for row in rows:
        for bam in row.bams:
            owner = (row.sample_id, bam.source_bam)
            previous_link_owner = link_owners.setdefault(bam.link_bam, owner)
            if previous_link_owner != owner:
                raise ValueError(
                    f"[{STAGE}] Multiple inputs map to BAM destination {bam.link_bam}: "
                    f"{previous_link_owner} and {owner}"
                )
            pp_id = f"{row.sample_id}.{bam.contig_name}"
            previous_pp_id_owner = pp_id_owners.setdefault(pp_id, owner)
            if previous_pp_id_owner != owner:
                raise ValueError(
                    f"[{STAGE}] Multiple inputs map to preprocess ID {pp_id!r}: "
                    f"{previous_pp_id_owner} and {owner}"
                )


def _planned_preprocess_rows(plan: RegistrationPlan) -> list[dict[str, str]]:
    return [
        {
            "pp_id": f"{row.sample_id}.{bam.contig_name}",
            "pp_path": str(bam.link_bam),
            "sample_id": row.sample_id,
            "source_type": row.source_type,
            "contig_name": bam.contig_name,
            "region_label": bam.region_label,
            "dedup_status": row.dedup_status,
            "is_deduped": str(row.is_deduped),
            "requires_split": str(row.requires_split),
            "barcode_suffix": row.barcode_suffix,
        }
        for row in plan.rows
        for bam in row.bams
    ]


def _validate_existing_preprocess_csv(plan: RegistrationPlan, preprocess_csv: Path) -> None:
    if preprocess_csv.is_symlink():
        raise ValueError(
            f"[{STAGE}] Existing preprocess_inputs.csv is a symlink: {preprocess_csv}. "
            "Use a new run root or repair it manually."
        )
    if not preprocess_csv.exists():
        return
    if not preprocess_csv.is_file():
        raise ValueError(f"[{STAGE}] Existing preprocess_inputs.csv is not a regular file")
    try:
        with preprocess_csv.open("r", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            existing_rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise ValueError(f"[{STAGE}] Cannot read existing preprocess_inputs.csv") from exc
    expected_rows = _planned_preprocess_rows(plan)
    if fieldnames != PREPROCESS_FIELDS or existing_rows != expected_rows:
        raise ValueError(
            f"[{STAGE}] Existing preprocess_inputs.csv conflicts with validated inputs. "
            "Use a new run root or repair it manually."
        )


def _validate_link_and_index(link_bam: Path, source_bam: Path, source_bai: Path | None) -> None:
    if os.path.lexists(link_bam):
        if not link_bam.is_symlink():
            raise ValueError(
                f"[{STAGE}] Existing BAM destination is not a symlink: {link_bam}. "
                "Use a new run root or repair it manually."
            )
        if link_bam.resolve(strict=False) != source_bam.resolve(strict=True):
            raise ValueError(
                f"[{STAGE}] Existing BAM link points to {link_bam.resolve(strict=False)}, "
                f"expected {source_bam}. Use a new run root or repair it manually."
            )

    link_bai = Path(f"{link_bam}.bai")
    if link_bai.is_symlink():
        raise ValueError(
            f"[{STAGE}] Existing BAI destination is a symlink: {link_bai}. "
            "Use a new run root or repair it manually."
        )
    if os.path.lexists(link_bai) and not link_bai.is_file():
        raise ValueError(f"[{STAGE}] Existing BAI destination is not a regular file: {link_bai}")
    if link_bai.is_file():
        if source_bai is not None:
            if _sha256(link_bai) != _sha256(source_bai):
                raise ValueError(
                    f"[{STAGE}] Existing BAI differs from the declared source index: {link_bai}. "
                    "Use a new run root or repair it manually."
                )
        else:
            _validate_existing_bai(source_bam, link_bai)


def _resolve_source_bai(source_bam: Path) -> Path | None:
    candidates = tuple(
        path
        for path in (Path(f"{source_bam}.bai"), source_bam.with_suffix(".bai"))
        if os.path.lexists(path)
    )
    for candidate in candidates:
        if not candidate.is_file():
            raise ValueError(f"[{STAGE}] Source BAI is not a regular file: {candidate}")
    if len(candidates) == 2 and _sha256(candidates[0]) != _sha256(candidates[1]):
        raise ValueError(
            f"[{STAGE}] Ambiguous source indexes differ: {candidates[0]} and {candidates[1]}"
        )
    source_bai = candidates[0] if candidates else None
    if source_bai is not None:
        _validate_existing_bai(source_bam, source_bai)
    return source_bai


def _validate_existing_bai(source_bam: Path, link_bai: Path) -> None:
    try:
        import pysam
        with pysam.AlignmentFile(str(source_bam), "rb", index_filename=str(link_bai)) as bam:
            bam.check_index()
            bam.get_index_statistics()
    except Exception as exc:
        raise ValueError(
            f"[{STAGE}] Existing BAI is not structurally readable with {source_bam}: {link_bai}"
        ) from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _as_int01(v, default: int = 0) -> int:
    if v is None or str(v).strip() == "":
        return int(default)
    s = str(v).strip()
    if s in {"0", "1"}:
        return int(s)
    s2 = s.lower()
    if s2 in {"true", "t", "yes", "y"}:
        return 1
    if s2 in {"false", "f", "no", "n"}:
        return 0
    raise ValueError(f"[{STAGE}] Expected 0/1-like value, got: {v!r}")


def _safe_symlink(link_path: Path, target_path: Path) -> None:
    if os.path.lexists(link_path):
        if not link_path.is_symlink() or link_path.resolve(strict=False) != target_path.resolve(strict=True):
            raise ValueError(
                f"[{STAGE}] Existing BAM destination conflicts with declared source: {link_path}. "
                "Use a new run root or repair it manually."
            )
        return
    link_path.symlink_to(target_path)


def _ensure_bai_for_link(
    link_bam: Path, src_bam: Path, source_bai: Path | None = None,
    *, prepared_bai: Path | None = None,
) -> None:
    with stage_scope("register_inputs"):
        link_bai = Path(str(link_bam) + ".bai")
        source_bai = source_bai if source_bai is not None else _resolve_source_bai(src_bam)
        _validate_link_and_index(link_bam, src_bam, source_bai)
        if link_bai.exists():
            return
        copy_bai = source_bai if source_bai is not None else prepared_bai
        if copy_bai is not None:
            temporary = link_bai.with_name(f".{link_bai.name}.{uuid.uuid4().hex}.tmp")
            try:
                with copy_bai.open("rb") as source, temporary.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                if _sha256(temporary) != _sha256(copy_bai):
                    raise RuntimeError(f"[{STAGE}] Copied BAI verification failed: {link_bai}")
                if os.path.lexists(link_bai):
                    raise ValueError(f"[{STAGE}] BAI destination appeared during registration: {link_bai}")
                os.link(temporary, link_bai, follow_symlinks=False)
            finally:
                temporary.unlink(missing_ok=True)
            return

        try:
            logger_stage.info(f"Creating .bai for {link_bam}.")
            import pysam
            temporary = link_bai.with_name(f".{link_bai.name}.{uuid.uuid4().hex}.tmp")
            pysam.index("-o", str(temporary), str(link_bam))
            if os.path.lexists(link_bai):
                raise ValueError(f"[{STAGE}] BAI destination appeared during registration: {link_bai}")
            os.link(temporary, link_bai, follow_symlinks=False)
            temporary.unlink()
            logger_stage.info(f"Created .bai for {link_bam}.")
        except Exception as e:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
            logger_stage.raise_error(f"Missing index and failed to generate .bai for {link_bam}: {e}")


def _bam_has_chr_prefix(bam_path: Path) -> bool:
    with stage_scope("register_inputs"):
        try:
            import pysam
            with pysam.AlignmentFile(str(bam_path), "rb") as bam:
                return any(ref.startswith("chr") for ref in bam.references)
        except Exception as e:
            loggers.raise_error(f"Error reading BAM file {bam_path}: {e}")

# endregion
