from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from .task_completion import (
    LEGACY_SCHEDULER_AUTHORITY_V1,
    TASK_COORDINATE_SCHEDULER_AUTHORITY_V2,
    TaskCompletionLoadError,
    load_legacy_task_completion_snapshot_v1,
    load_task_completion_snapshot_v2,
)


TASK_COMPLETION_RELEASE_OBSERVATION_SCHEMA_V1 = (
    "scpolya.task_completion.release_observation.v1"
)

TaskCompletionObservationRoleV1 = Literal[
    "representative",
    "legacy_pinned",
]


@dataclass(frozen=True)
class TaskCompletionObservationTargetV1:
    label: str
    state_or_db_path: str | Path
    role: TaskCompletionObservationRoleV1


@dataclass(frozen=True)
class TaskCompletionDatabaseStatV1:
    device: int
    inode: int
    size: int
    mtime_ns: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class TaskCompletionObservationEntryV1:
    label: str
    role: TaskCompletionObservationRoleV1
    requested_path: str
    state_db_path: str
    authority_mode: str
    completion_source: str
    scheduler_authority: str
    record_count: int
    legacy_fallback_count: int
    database_before: TaskCompletionDatabaseStatV1 | None
    database_after: TaskCompletionDatabaseStatV1 | None
    database_unchanged: bool
    error: str | None
    reasons: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.reasons

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "role": self.role,
            "requested_path": self.requested_path,
            "state_db_path": self.state_db_path,
            "authority_mode": self.authority_mode,
            "completion_source": self.completion_source,
            "scheduler_authority": self.scheduler_authority,
            "record_count": self.record_count,
            "legacy_fallback_count": self.legacy_fallback_count,
            "database_before": (
                None
                if self.database_before is None
                else self.database_before.as_dict()
            ),
            "database_after": (
                None
                if self.database_after is None
                else self.database_after.as_dict()
            ),
            "database_unchanged": self.database_unchanged,
            "error": self.error,
            "passed": self.passed,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class TaskCompletionReleaseObservationV1:
    release_id: str
    entries: tuple[TaskCompletionObservationEntryV1, ...]
    global_reasons: tuple[str, ...] = ()
    schema_version: str = TASK_COMPLETION_RELEASE_OBSERVATION_SCHEMA_V1

    @property
    def representative_entries(
        self,
    ) -> tuple[TaskCompletionObservationEntryV1, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.role == "representative"
        )

    @property
    def legacy_pinned_entries(
        self,
    ) -> tuple[TaskCompletionObservationEntryV1, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.role == "legacy_pinned"
        )

    @property
    def passed(self) -> bool:
        return not self.global_reasons and all(
            entry.passed for entry in self.entries
        )

    def as_dict(self) -> dict[str, Any]:
        representatives = self.representative_entries
        pinned = self.legacy_pinned_entries
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "observation_passed": self.passed,
            "global_reasons": list(self.global_reasons),
            "run_count": len(self.entries),
            "representative_run_count": len(representatives),
            "representative_pass_count": sum(
                entry.passed for entry in representatives
            ),
            "representative_record_count": sum(
                entry.record_count for entry in representatives
            ),
            "representative_legacy_fallback_count": sum(
                entry.legacy_fallback_count for entry in representatives
            ),
            "legacy_pinned_run_count": len(pinned),
            "legacy_pinned_pass_count": sum(
                entry.passed for entry in pinned
            ),
            "legacy_pinned_record_count": sum(
                entry.record_count for entry in pinned
            ),
            "legacy_pinned_fallback_count": sum(
                entry.legacy_fallback_count for entry in pinned
            ),
            "database_immutability_passed": all(
                entry.database_unchanged for entry in self.entries
            ),
            "error_count": sum(
                entry.error is not None for entry in self.entries
            ),
            "entries": [entry.as_dict() for entry in self.entries],
        }


def build_task_completion_release_observation_v1(
    release_id: str,
    targets: Iterable[TaskCompletionObservationTargetV1],
) -> TaskCompletionReleaseObservationV1:
    normalized_release_id = str(release_id).strip()
    if not normalized_release_id:
        raise ValueError("release_id must not be empty")

    normalized_targets = _normalize_targets(targets)
    entries = tuple(
        _observe_target(target)
        for target in sorted(
            normalized_targets,
            key=lambda target: (
                0 if target.role == "representative" else 1,
                target.label,
                str(target.state_or_db_path),
            ),
        )
    )
    global_reasons: tuple[str, ...] = ()
    if not any(entry.role == "representative" for entry in entries):
        global_reasons = ("no_representative_runs",)
    return TaskCompletionReleaseObservationV1(
        release_id=normalized_release_id,
        entries=entries,
        global_reasons=global_reasons,
    )


def format_task_completion_release_observation_v1(
    report: TaskCompletionReleaseObservationV1,
) -> str:
    representatives = report.representative_entries
    pinned = report.legacy_pinned_entries
    lines = [
        f"Task completion release observation: {report.release_id}",
        f"status: {'PASS' if report.passed else 'FAIL'}",
        (
            "representative runs: "
            f"{sum(entry.passed for entry in representatives)}"
            f"/{len(representatives)} passed, "
            f"records={sum(entry.record_count for entry in representatives)}, "
            "legacy_fallback="
            f"{sum(entry.legacy_fallback_count for entry in representatives)}"
        ),
        (
            "legacy-pinned runs: "
            f"{sum(entry.passed for entry in pinned)}/{len(pinned)} passed, "
            f"records={sum(entry.record_count for entry in pinned)}, "
            "legacy_fallback="
            f"{sum(entry.legacy_fallback_count for entry in pinned)}"
        ),
    ]
    if report.global_reasons:
        lines.append("global reasons: " + ", ".join(report.global_reasons))
    for entry in report.entries:
        lines.append(
            f"  [{'PASS' if entry.passed else 'FAIL'}] "
            f"{entry.role} {entry.label}: "
            f"mode={entry.authority_mode}, "
            f"source={entry.completion_source}, "
            f"records={entry.record_count}, "
            f"legacy_fallback={entry.legacy_fallback_count}, "
            f"database_unchanged={str(entry.database_unchanged).lower()}"
        )
        lines.append(f"    database: {entry.state_db_path}")
        if entry.error:
            lines.append(f"    error: {entry.error}")
        if entry.reasons:
            lines.append("    reasons: " + ", ".join(entry.reasons))
    return "\n".join(lines)


def _normalize_targets(
    targets: Iterable[TaskCompletionObservationTargetV1],
) -> tuple[TaskCompletionObservationTargetV1, ...]:
    normalized: list[TaskCompletionObservationTargetV1] = []
    labels: set[str] = set()
    database_paths: dict[Path, str] = {}
    for target in targets:
        label = str(target.label).strip()
        if not label:
            raise ValueError("observation target label must not be empty")
        if label in labels:
            raise ValueError(f"duplicate observation target label: {label}")
        if target.role not in {"representative", "legacy_pinned"}:
            raise ValueError(
                f"unsupported observation target role: {target.role!r}"
            )
        requested_path = Path(target.state_or_db_path).expanduser()
        db_path = _resolve_db_path(requested_path)
        previous_label = database_paths.get(db_path)
        if previous_label is not None:
            raise ValueError(
                "duplicate observation database path for labels "
                f"{previous_label!r} and {label!r}: {db_path}"
            )
        labels.add(label)
        database_paths[db_path] = label
        normalized.append(
            TaskCompletionObservationTargetV1(
                label=label,
                state_or_db_path=requested_path,
                role=target.role,
            )
        )
    return tuple(normalized)


def _observe_target(
    target: TaskCompletionObservationTargetV1,
) -> TaskCompletionObservationEntryV1:
    requested_path = Path(target.state_or_db_path).expanduser()
    db_path = _resolve_db_path(requested_path)
    errors: list[str] = []
    before = _read_database_stat(db_path, errors, phase="before")

    authority_mode = "unavailable"
    completion_source = "unavailable"
    scheduler_authority = "unavailable"
    record_count = 0
    legacy_fallback_count = 0
    try:
        loader = (
            load_task_completion_snapshot_v2
            if target.role == "representative"
            else load_legacy_task_completion_snapshot_v1
        )
        snapshot = loader(db_path)
    except TaskCompletionLoadError as exc:
        errors.append(f"TaskCompletionLoadError: {exc}")
    else:
        authority_mode = snapshot.authority_mode
        completion_source = snapshot.completion_source
        scheduler_authority = snapshot.scheduler_authority
        record_count = len(snapshot.records)
        legacy_fallback_count = snapshot.legacy_fallback_count

    after = _read_database_stat(db_path, errors, phase="after")
    database_unchanged = (
        before is not None
        and after is not None
        and before == after
    )
    error = "; ".join(errors) if errors else None
    reasons = _entry_reasons(
        role=target.role,
        authority_mode=authority_mode,
        completion_source=completion_source,
        scheduler_authority=scheduler_authority,
        record_count=record_count,
        legacy_fallback_count=legacy_fallback_count,
        database_unchanged=database_unchanged,
        error=error,
    )
    return TaskCompletionObservationEntryV1(
        label=target.label,
        role=target.role,
        requested_path=str(requested_path),
        state_db_path=str(db_path),
        authority_mode=authority_mode,
        completion_source=completion_source,
        scheduler_authority=scheduler_authority,
        record_count=record_count,
        legacy_fallback_count=legacy_fallback_count,
        database_before=before,
        database_after=after,
        database_unchanged=database_unchanged,
        error=error,
        reasons=reasons,
    )


def _entry_reasons(
    *,
    role: TaskCompletionObservationRoleV1,
    authority_mode: str,
    completion_source: str,
    scheduler_authority: str,
    record_count: int,
    legacy_fallback_count: int,
    database_unchanged: bool,
    error: str | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if error:
        reasons.append("completion_load_or_stat_error")
    if not database_unchanged:
        reasons.append("database_not_proven_unchanged")
    if record_count <= 0:
        reasons.append("no_completion_records")

    if role == "representative":
        if authority_mode != "coordinate_v1":
            reasons.append("representative_not_coordinate_authority")
        if completion_source != "structured":
            reasons.append("representative_not_structured")
        if scheduler_authority != TASK_COORDINATE_SCHEDULER_AUTHORITY_V2:
            reasons.append("representative_scheduler_not_coordinate")
        if legacy_fallback_count != 0:
            reasons.append("representative_legacy_fallback_nonzero")
    else:
        if authority_mode not in {
            "uninitialized",
            "legacy_compatibility",
        }:
            reasons.append("pinned_run_not_legacy_capable")
        if completion_source not in {"legacy", "mixed"}:
            reasons.append("pinned_run_not_using_legacy_completion")
        if scheduler_authority != LEGACY_SCHEDULER_AUTHORITY_V1:
            reasons.append("pinned_run_scheduler_not_legacy")
        if legacy_fallback_count <= 0:
            reasons.append("pinned_run_has_no_legacy_fallback")
    return tuple(reasons)


def _read_database_stat(
    db_path: Path,
    errors: list[str],
    *,
    phase: str,
) -> TaskCompletionDatabaseStatV1 | None:
    try:
        stat = db_path.stat()
    except OSError as exc:
        errors.append(
            f"{phase} database stat failed: {type(exc).__name__}: {exc}"
        )
        return None
    return TaskCompletionDatabaseStatV1(
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        size=int(stat.st_size),
        mtime_ns=int(stat.st_mtime_ns),
    )


def _resolve_db_path(state_or_db_path: str | Path) -> Path:
    path = Path(state_or_db_path).expanduser()
    if path.is_dir():
        if path.name == "state":
            path = path / "state.sqlite"
        else:
            nested = path / "state" / "state.sqlite"
            path = nested if nested.exists() else path / "state.sqlite"
    return path.resolve(strict=False)
