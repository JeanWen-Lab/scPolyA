from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence, Tuple

from .artifact_health import ArtifactHealthEntry, ArtifactHealthReport
from .task_instance_v2 import stable_task_json_hash


ArtifactRole = Literal["input", "output"]
ArtifactPathStatus = Literal["ready", "missing", "empty", "unresolved"]
FingerprintRequestMode = Literal["auto", "content", "metadata"]
FingerprintMode = Literal[
    "content",
    "metadata",
    "config",
    "missing",
    "empty",
    "directory_metadata",
    "unresolved",
]

DEFAULT_CONTENT_FINGERPRINT_MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ArtifactPathFingerprint:
    path: str
    exists: bool
    is_file: bool
    is_dir: bool
    size_bytes: int | None
    mtime_ns: int | None
    mode: FingerprintMode
    fingerprint: str
    content_hash: str | None = None
    detail: str = ""

    @property
    def status(self) -> ArtifactPathStatus:
        if self.mode == "missing":
            return "missing"
        if self.mode == "empty":
            return "empty"
        if self.mode == "unresolved":
            return "unresolved"
        return "ready"

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "exists": self.exists,
            "is_file": self.is_file,
            "is_dir": self.is_dir,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "mode": self.mode,
            "fingerprint": self.fingerprint,
            "content_hash": self.content_hash,
            "detail": self.detail,
            "status": self.status,
        }


@dataclass(frozen=True)
class ArtifactRecord:
    role: ArtifactRole
    artifact_key: str
    path: str
    stage: str = ""
    task_base: str = ""
    label: str = ""
    expectation_mode: str = ""
    expectation_status: str = ""
    path_status: ArtifactPathStatus = "unresolved"
    required: bool = True
    source: str = ""
    fingerprint: ArtifactPathFingerprint | None = None

    @property
    def task_key(self) -> tuple[str, str] | None:
        if not self.stage or not self.task_base:
            return None
        return self.stage, self.task_base

    @property
    def record_hash(self) -> str:
        return stable_task_json_hash(self.as_dict(include_record_hash=False))

    def as_dict(self, *, include_record_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "role": self.role,
            "artifact_key": self.artifact_key,
            "path": self.path,
            "stage": self.stage,
            "task_base": self.task_base,
            "label": self.label,
            "expectation_mode": self.expectation_mode,
            "expectation_status": self.expectation_status,
            "path_status": self.path_status,
            "required": self.required,
            "source": self.source,
            "fingerprint": self.fingerprint.as_dict() if self.fingerprint else None,
        }
        if include_record_hash:
            payload["record_hash"] = self.record_hash
        return payload


@dataclass(frozen=True)
class ArtifactRecordSet:
    records: Tuple[ArtifactRecord, ...]

    @property
    def ready_count(self) -> int:
        return sum(1 for record in self.records if record.path_status == "ready")

    @property
    def missing_count(self) -> int:
        return sum(1 for record in self.records if record.path_status == "missing")

    @property
    def empty_count(self) -> int:
        return sum(1 for record in self.records if record.path_status == "empty")

    @property
    def unresolved_count(self) -> int:
        return sum(1 for record in self.records if record.path_status == "unresolved")

    @property
    def record_set_hash(self) -> str:
        return stable_task_json_hash(sorted(record.record_hash for record in self.records))

    def as_dict(self) -> dict[str, object]:
        return {
            "record_count": len(self.records),
            "ready_count": self.ready_count,
            "missing_count": self.missing_count,
            "empty_count": self.empty_count,
            "unresolved_count": self.unresolved_count,
            "record_set_hash": self.record_set_hash,
            "records": [record.as_dict() for record in self.records],
        }


def fingerprint_path(
    path: str | Path,
    *,
    mode: FingerprintRequestMode = "auto",
    content_max_bytes: int = DEFAULT_CONTENT_FINGERPRINT_MAX_BYTES,
) -> ArtifactPathFingerprint:
    path_obj = Path(path).expanduser()
    path_text = str(path_obj)
    try:
        stat = path_obj.stat()
    except FileNotFoundError:
        return _path_fingerprint(
            path_text,
            exists=False,
            is_file=False,
            is_dir=False,
            size_bytes=None,
            mtime_ns=None,
            mode="missing",
            detail="path does not exist",
        )
    except OSError as exc:
        return _path_fingerprint(
            path_text,
            exists=False,
            is_file=False,
            is_dir=False,
            size_bytes=None,
            mtime_ns=None,
            mode="unresolved",
            detail=str(exc),
        )

    is_file = path_obj.is_file()
    is_dir = path_obj.is_dir()
    size_bytes = stat.st_size
    mtime_ns = stat.st_mtime_ns

    if is_dir:
        return _path_fingerprint(
            path_text,
            exists=True,
            is_file=False,
            is_dir=True,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            mode="directory_metadata",
        )
    if not is_file:
        return _path_fingerprint(
            path_text,
            exists=True,
            is_file=False,
            is_dir=False,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            mode="unresolved",
            detail="path is neither a regular file nor a directory",
        )
    if size_bytes == 0:
        return _path_fingerprint(
            path_text,
            exists=True,
            is_file=True,
            is_dir=False,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            mode="empty",
        )

    actual_mode: FingerprintMode
    if mode == "content":
        actual_mode = "content"
    elif mode == "metadata":
        actual_mode = "metadata"
    else:
        actual_mode = "content" if size_bytes <= content_max_bytes else "metadata"

    if actual_mode == "content":
        content_hash = _content_sha256(path_obj)
        return ArtifactPathFingerprint(
            path=path_text,
            exists=True,
            is_file=True,
            is_dir=False,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            mode="content",
            fingerprint=f"sha256:{content_hash}",
            content_hash=content_hash,
        )

    return _path_fingerprint(
        path_text,
        exists=True,
        is_file=True,
        is_dir=False,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        mode="metadata",
    )


def fingerprint_config_value(
    artifact_key: str,
    value: object,
    *,
    detail: str = "",
) -> ArtifactPathFingerprint:
    """Return a deterministic synthetic fingerprint for a persisted config value."""

    path = f"config://{artifact_key}"
    value_hash = stable_task_json_hash(
        {
            "artifact_key": artifact_key,
            "value": value,
        }
    )
    return ArtifactPathFingerprint(
        path=path,
        exists=True,
        is_file=False,
        is_dir=False,
        size_bytes=None,
        mtime_ns=None,
        mode="config",
        fingerprint=f"sha256:{value_hash}",
        detail=detail,
    )


def unresolved_artifact_fingerprint(path: str | Path, detail: str) -> ArtifactPathFingerprint:
    """Return a stable unresolved fingerprint without probing a filesystem path."""

    path_text = str(path)
    return ArtifactPathFingerprint(
        path=path_text,
        exists=False,
        is_file=False,
        is_dir=False,
        size_bytes=None,
        mtime_ns=None,
        mode="unresolved",
        fingerprint=stable_task_json_hash(
            {
                "path": path_text,
                "mode": "unresolved",
                "detail": detail,
            }
        ),
        detail=detail,
    )


def artifact_records_from_health_report(
    report: ArtifactHealthReport,
    *,
    fingerprint_mode: FingerprintRequestMode = "metadata",
    content_max_bytes: int = DEFAULT_CONTENT_FINGERPRINT_MAX_BYTES,
) -> ArtifactRecordSet:
    return ArtifactRecordSet(
        records=tuple(
            record
            for entry in report.entries
            for record in artifact_records_from_health_entry(
                entry,
                fingerprint_mode=fingerprint_mode,
                content_max_bytes=content_max_bytes,
            )
        )
    )


def artifact_records_from_health_entry(
    entry: ArtifactHealthEntry,
    *,
    fingerprint_mode: FingerprintRequestMode = "metadata",
    content_max_bytes: int = DEFAULT_CONTENT_FINGERPRINT_MAX_BYTES,
) -> Tuple[ArtifactRecord, ...]:
    expectation = entry.expectation
    artifact_key = _health_artifact_key(entry)
    if entry.status == "unresolved" and not expectation.paths:
        path = f"unresolved://{expectation.stage}/{expectation.task_base}/{expectation.label}"
        fingerprint = _unresolved_fingerprint(path, expectation.unresolved_detail)
        return (
            ArtifactRecord(
                role="output",
                artifact_key=artifact_key,
                path=path,
                stage=expectation.stage,
                task_base=expectation.task_base,
                label=expectation.label,
                expectation_mode=expectation.mode,
                expectation_status=entry.status,
                path_status="unresolved",
                required=expectation.mode == "all",
                source="artifact_health",
                fingerprint=fingerprint,
            ),
        )

    records: list[ArtifactRecord] = []
    for path in expectation.paths:
        fingerprint = fingerprint_path(
            path,
            mode=fingerprint_mode,
            content_max_bytes=content_max_bytes,
        )
        records.append(
            ArtifactRecord(
                role="output",
                artifact_key=artifact_key,
                path=str(path),
                stage=expectation.stage,
                task_base=expectation.task_base,
                label=expectation.label,
                expectation_mode=expectation.mode,
                expectation_status=entry.status,
                path_status=fingerprint.status,
                required=expectation.mode == "all",
                source="artifact_health",
                fingerprint=fingerprint,
            )
        )
    return tuple(records)


def artifact_record_set_hash(records: Sequence[ArtifactRecord]) -> str:
    return stable_task_json_hash(sorted(record.record_hash for record in records))


def _health_artifact_key(entry: ArtifactHealthEntry) -> str:
    expectation = entry.expectation
    payload = {
        "source": "artifact_health",
        "stage": expectation.stage,
        "task_base": expectation.task_base,
        "label": expectation.label,
        "paths": expectation.paths,
        "mode": expectation.mode,
    }
    if expectation.unresolved_detail:
        payload["unresolved_detail"] = expectation.unresolved_detail
    return stable_task_json_hash(payload)


def _unresolved_fingerprint(path: str, detail: str) -> ArtifactPathFingerprint:
    return unresolved_artifact_fingerprint(path, detail)


def _content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_fingerprint(
    path: str,
    *,
    exists: bool,
    is_file: bool,
    is_dir: bool,
    size_bytes: int | None,
    mtime_ns: int | None,
    mode: FingerprintMode,
    detail: str = "",
) -> ArtifactPathFingerprint:
    fingerprint = stable_task_json_hash(
        {
            "path": path,
            "exists": exists,
            "is_file": is_file,
            "is_dir": is_dir,
            "size_bytes": size_bytes,
            "mtime_ns": mtime_ns,
            "mode": mode,
            "detail": detail,
        }
    )
    return ArtifactPathFingerprint(
        path=path,
        exists=exists,
        is_file=is_file,
        is_dir=is_dir,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        mode=mode,
        fingerprint=fingerprint,
        detail=detail,
    )
