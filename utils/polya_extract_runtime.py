"""Parent-only process launch and publication for Poly(A) extraction."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from logging_config import run_tool_command

from .read_eligibility import ReadRule
from .polya_extract import (
    PolyAExtractionStats,
    bam_index_path,
    polya_publish_marker_path,
    validate_polya_bam,
)


LOCAL_REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_MARKER_SCHEMA_V1 = "scpolya.polya_extraction.publish.v1"


def _valid_transaction_id(transaction_id: object) -> bool:
    return isinstance(transaction_id, str) and bool(
        re.fullmatch(r"[A-Za-z0-9_-]{1,128}", transaction_id)
    )


def _backup_path(final_path: Path, transaction_id: str) -> Path:
    if not _valid_transaction_id(transaction_id):
        raise ValueError(f"Invalid Poly(A) publication transaction ID: {transaction_id!r}")
    return final_path.with_name(
        f".{final_path.name}.polya-backup.{transaction_id}"
    )


def _cleanup_transaction_backups(backup_bam: Path, backup_index: Path) -> None:
    """Remove only one transaction's private backups after commit/recovery."""

    backup_bam.unlink(missing_ok=True)
    backup_index.unlink(missing_ok=True)
    _fsync_directory(backup_bam.parent)


def _cleanup_obsolete_polya_backups(
    final_bam: Path,
    *,
    current_transaction_id: str,
) -> tuple[Path, ...]:
    """Reclaim abandoned private backups while holding the publish marker."""

    final_index = bam_index_path(final_bam)
    current_paths = {
        _backup_path(final_bam, current_transaction_id),
        _backup_path(final_index, current_transaction_id),
    }
    prefixes = (
        f".{final_bam.name}.polya-backup.",
        f".{final_index.name}.polya-backup.",
    )
    removed: list[Path] = []
    for path in final_bam.parent.iterdir():
        if path in current_paths:
            continue
        matching_prefix = next(
            (prefix for prefix in prefixes if path.name.startswith(prefix)),
            None,
        )
        if matching_prefix is None:
            continue
        if not _valid_transaction_id(path.name[len(matching_prefix):]):
            continue
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
            removed.append(path)
    if removed:
        _fsync_directory(final_bam.parent)
    return tuple(removed)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        state = stat_path.read_text().rsplit(")", 1)[1].split()[0]
        if state == "Z":
            return False
    except (FileNotFoundError, IndexError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_stale_polya_temps(final_bam: str | Path) -> tuple[Path, ...]:
    """Remove unique temp files whose owner parent process no longer exists."""

    final_bam = Path(final_bam).resolve()
    prefix = f".{final_bam.name}.polya-tmp."
    removed: list[Path] = []
    if not final_bam.parent.is_dir():
        return ()
    for path in final_bam.parent.iterdir():
        if not path.name.startswith(prefix):
            continue
        owner_text = path.name[len(prefix):].split(".", 1)[0]
        try:
            owner_pid = int(owner_text)
        except ValueError:
            # An unrecognized hidden file is not ours to remove.
            continue
        if _process_is_alive(owner_pid):
            continue
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
            removed.append(path)
    if removed:
        _fsync_directory(final_bam.parent)
    return tuple(removed)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_backup(source: Path, backup: Path) -> None:
    backup.unlink(missing_ok=True)
    try:
        os.link(source, backup)
    except OSError:
        shutil.copy2(source, backup)


def _restore_from_backup(backup: Path, final_path: Path) -> None:
    """Atomically restore without consuming the recovery authority backup."""

    restore_path = backup.with_name(
        f".{backup.name}.restore.{os.getpid()}.{uuid.uuid4().hex}"
    )
    try:
        _copy_backup(backup, restore_path)
        os.replace(restore_path, final_path)
    finally:
        restore_path.unlink(missing_ok=True)


def _write_publish_marker(
    marker: Path,
    *,
    transaction_id: str,
    phase: str,
    final_bam: Path,
    final_index: Path,
    bam_existed: bool | None = None,
    index_existed: bool | None = None,
) -> None:
    if not _valid_transaction_id(transaction_id):
        raise ValueError(f"Invalid Poly(A) publication transaction ID: {transaction_id!r}")
    payload = {
        "schema": PUBLISH_MARKER_SCHEMA_V1,
        "pid": os.getpid(),
        "transaction_id": transaction_id,
        "phase": phase,
        "final_bam": str(final_bam),
        "final_index": str(final_index),
        "bam_existed": bam_existed,
        "index_existed": index_existed,
    }
    created = False
    try:
        with marker.open("x", encoding="utf-8") as handle:
            created = True
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(marker.parent)
    except BaseException:
        # Preparing-state creation happens before backups or final mutations,
        # so this transaction may safely relinquish an incomplete lock.
        if created:
            marker.unlink(missing_ok=True)
        raise


def _mark_publish_backups_ready(
    marker: Path,
    transaction_id: str,
    *,
    bam_existed: bool,
    index_existed: bool,
) -> None:
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if (
        payload.get("schema") != PUBLISH_MARKER_SCHEMA_V1
        or payload.get("transaction_id") != transaction_id
        or payload.get("phase") != "preparing"
    ):
        raise RuntimeError(f"Poly(A) publish transaction marker changed: {marker}")
    payload["phase"] = "ready"
    payload["bam_existed"] = bam_existed
    payload["index_existed"] = index_existed
    update_path = marker.with_name(
        f".{marker.name}.update.{os.getpid()}.{transaction_id}"
    )
    try:
        with update_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(update_path, marker)
        _fsync_directory(marker.parent)
    finally:
        update_path.unlink(missing_ok=True)


def recover_interrupted_polya_publish(
    final_bam: str | Path,
    *,
    transaction_id: str | None = None,
) -> bool:
    """Restore the pre-publication pair recorded by a stale marker."""

    final_bam = Path(final_bam).resolve()
    final_index = bam_index_path(final_bam)
    marker = polya_publish_marker_path(final_bam)
    if not marker.exists():
        return False

    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid Poly(A) publish recovery marker: {marker}") from exc
    if (
        payload.get("schema") != PUBLISH_MARKER_SCHEMA_V1
        or payload.get("final_bam") != str(final_bam)
        or payload.get("final_index") != str(final_index)
    ):
        raise RuntimeError(f"Invalid Poly(A) publish recovery marker: {marker}")
    try:
        owner_pid = int(payload.get("pid"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid Poly(A) publish owner in marker: {marker}") from exc
    marker_transaction_id = payload.get("transaction_id")
    phase = payload.get("phase")
    if not _valid_transaction_id(marker_transaction_id) or phase not in {
        "preparing",
        "ready",
    }:
        raise RuntimeError(f"Invalid Poly(A) publish transaction marker: {marker}")
    if phase == "ready" and not all(
        isinstance(payload.get(key), bool)
        for key in ("bam_existed", "index_existed")
    ):
        raise RuntimeError(f"Invalid Poly(A) publish backup state marker: {marker}")
    rollback_authorized = (
        transaction_id is not None
        and owner_pid == os.getpid()
        and marker_transaction_id == transaction_id
    )
    if transaction_id is not None and not rollback_authorized:
        raise RuntimeError(
            f"Poly(A) rollback token does not own publication marker: {marker}"
        )
    if not rollback_authorized and _process_is_alive(owner_pid):
        raise RuntimeError(
            "Poly(A) publication is still owned by live transaction "
            f"{owner_pid}/{marker_transaction_id}: {marker}"
        )

    backup_bam = _backup_path(final_bam, marker_transaction_id)
    backup_index = _backup_path(final_index, marker_transaction_id)
    if phase == "preparing":
        # The protocol never mutates a final path until the atomically updated
        # marker says backups are ready. Preparing-state cleanup is therefore
        # safe and idempotent even if backup creation was interrupted.
        marker.unlink()
        _cleanup_transaction_backups(backup_bam, backup_index)
        return True
    if bool(payload.get("bam_existed")) and not backup_bam.is_file():
        raise RuntimeError(f"Poly(A) BAM recovery backup is missing: {backup_bam}")
    if bool(payload.get("index_existed")) and not backup_index.is_file():
        raise RuntimeError(f"Poly(A) index recovery backup is missing: {backup_index}")
    try:
        if bool(payload.get("index_existed")):
            _restore_from_backup(backup_index, final_index)
        else:
            final_index.unlink(missing_ok=True)

        if bool(payload.get("bam_existed")):
            _restore_from_backup(backup_bam, final_bam)
        else:
            final_bam.unlink(missing_ok=True)

        marker.unlink()
        _cleanup_transaction_backups(backup_bam, backup_index)
        return True
    except BaseException as exc:
        raise RuntimeError(
            f"Could not recover interrupted Poly(A) publication for {final_bam}: {exc}"
        ) from exc


def _publish_polya_pair(temp_bam: Path, final_bam: Path) -> None:
    temp_index = bam_index_path(temp_bam)
    final_index = bam_index_path(final_bam)
    marker = polya_publish_marker_path(final_bam)
    if marker.exists():
        recover_interrupted_polya_publish(final_bam)
    transaction_id = uuid.uuid4().hex
    backup_bam = _backup_path(final_bam, transaction_id)
    backup_index = _backup_path(final_index, transaction_id)
    marker_acquired = False
    try:
        try:
            _write_publish_marker(
                marker,
                transaction_id=transaction_id,
                phase="preparing",
                final_bam=final_bam,
                final_index=final_index,
            )
            marker_acquired = True
        except FileExistsError as exc:
            raise RuntimeError(
                f"Another Poly(A) publication acquired the transaction marker: {marker}"
            ) from exc

        _cleanup_obsolete_polya_backups(
            final_bam,
            current_transaction_id=transaction_id,
        )
        bam_existed = final_bam.is_file()
        index_existed = final_index.is_file()
        if bam_existed:
            _copy_backup(final_bam, backup_bam)
        if index_existed:
            _copy_backup(final_index, backup_index)
        _mark_publish_backups_ready(
            marker,
            transaction_id,
            bam_existed=bam_existed,
            index_existed=index_existed,
        )

        # Publish the BAM last: its rename is the artifact commit point.
        os.replace(temp_index, final_index)
        os.replace(temp_bam, final_bam)
        _fsync_directory(final_bam.parent)
        validate_polya_bam(final_bam)
        marker.unlink()
        _fsync_directory(final_bam.parent)
    except BaseException:
        if marker_acquired and marker.exists():
            recover_interrupted_polya_publish(
                final_bam,
                transaction_id=transaction_id,
            )
        raise
    else:
        # Marker removal committed the validated pair. Backups are disposable
        # even if this best-effort cleanup is interrupted.
        try:
            _cleanup_transaction_backups(backup_bam, backup_index)
        except OSError:
            pass


def _stderr_tail(value: Any, *, lines: int = 30) -> str:
    text = "" if value is None else str(value)
    return "\n".join(text.splitlines()[-lines:])


def run_isolated_polya_extraction(
    *,
    input_bam: str | Path,
    output_bam: str | Path,
    nonA_allowed: int,
    minpolyA: int,
    minMAPQ: int,
    read_rule: ReadRule | None = None,
) -> PolyAExtractionStats:
    """Run one pure conversion child, then publish its validated BAM pair."""

    input_bam = Path(input_bam).resolve()
    output_bam = Path(output_bam).resolve()
    if not input_bam.is_file():
        raise FileNotFoundError(f"Input BAM not found: {input_bam}")
    output_bam.parent.mkdir(parents=True, exist_ok=True)
    recover_interrupted_polya_publish(output_bam)
    cleanup_stale_polya_temps(output_bam)

    token = f"{os.getpid()}.{uuid.uuid4().hex}"
    temp_bam = output_bam.with_name(
        f".{output_bam.name}.polya-tmp.{token}.bam"
    )
    temp_index = bam_index_path(temp_bam)
    command = [
        sys.executable,
        "-m",
        "utils.polya_extract_worker",
        "--input-bam",
        str(input_bam),
        "--output-bam",
        str(temp_bam),
        "--nonA-allowed",
        str(nonA_allowed),
        "--minpolyA",
        str(minpolyA),
        "--minMAPQ",
        str(minMAPQ),
    ]
    if read_rule is not None and read_rule.unified:
        command += [
            "--count-read-rule", read_rule.mode,
            "--multimapper-policy", read_rule.multimapper_policy,
        ]
    try:
        try:
            result = run_tool_command(
                command,
                tool="scpolya-polya-extract",
                # Unit-specific so concurrent units never share a flat log.
                operation=f"extract_polyA.{output_bam.name}",
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(LOCAL_REPO_ROOT),
            )
        except subprocess.CalledProcessError as exc:
            tail = _stderr_tail(exc.stderr)
            raise RuntimeError(
                f"Poly(A) extraction child failed with exit code {exc.returncode}"
                + (f":\n{tail}" if tail else "")
            ) from exc

        try:
            payload = json.loads((result.stdout or "").strip())
            stats = PolyAExtractionStats.from_dict(payload)
        except Exception as exc:
            raise RuntimeError(
                "Poly(A) extraction child returned an invalid result payload"
            ) from exc
        validate_polya_bam(temp_bam)
        _publish_polya_pair(temp_bam, output_bam)
        return stats
    finally:
        temp_bam.unlink(missing_ok=True)
        temp_index.unlink(missing_ok=True)
