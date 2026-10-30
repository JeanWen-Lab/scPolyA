from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scpolya_ui.config_schema import required_missing
from scpolya_ui.jobs import JobRecord
from scpolya_ui.registry import WorkspaceRecord, ui_home
from scpolya_ui.run_data import summarize_run_state
from scpolya_ui.services.reference_service import reference_status
from scpolya_ui.services.validation_service import validate_workspace_paths
from scpolya_ui.state.persistence import ProductRegistries


@dataclass(frozen=True)
class ProductDiagnostic:
    level: str
    message_key: str
    params: dict[str, str]
    detail: str = ""
    scope: str = "runtime"


def diagnostic(
    level: str,
    message_key: str,
    *,
    scope: str = "runtime",
    detail: str = "",
    **params: str,
) -> ProductDiagnostic:
    return ProductDiagnostic(
        level=level,
        message_key=message_key,
        params=dict(params),
        detail=detail,
        scope=scope,
    )


def run_state_diagnostics(
    summary,
    *,
    scope: str = "workspace",
) -> tuple[ProductDiagnostic, ...]:
    """Translate low-level run-state read failures into actionable checks.

    The summary keeps its aggregate ``error`` for compatibility, while these
    rows give product surfaces a stable way to distinguish configuration,
    completion, and ledger availability without weakening fail-closed reads.
    """

    checks: list[ProductDiagnostic] = []
    if getattr(summary, "config_error", ""):
        checks.append(
            diagnostic(
                "warning",
                "diagnostics.state.config_unavailable",
                scope=scope,
                detail=summary.config_error,
            )
        )

    completion_state = str(getattr(summary, "completion_state", "unavailable"))
    completion_key = {
        "legacy_only": "diagnostics.state.completion_legacy_only",
        "malformed": "diagnostics.state.completion_malformed",
        "unavailable": "diagnostics.state.completion_unavailable",
    }.get(completion_state)
    if completion_key:
        checks.append(
            diagnostic(
                "warning",
                completion_key,
                scope=scope,
                detail=getattr(summary, "completion_error", ""),
            )
        )

    ledger_state = str(getattr(summary, "ledger_state", "unavailable"))
    ledger_key = {
        "malformed": "diagnostics.state.ledger_malformed",
        "unavailable": "diagnostics.state.ledger_unavailable",
    }.get(ledger_state)
    if ledger_key:
        checks.append(
            diagnostic(
                "warning",
                ledger_key,
                scope=scope,
                detail=getattr(summary, "ledger_error", ""),
            )
        )
    # ``summary.error`` is an aggregate compatibility field and may contain
    # implementation-level exception text. Keep that text in ``detail`` and
    # expose a stable product message whenever no typed state failure was
    # available to explain it.
    if getattr(summary, "error", "") and not checks:
        checks.append(
            diagnostic(
                "warning",
                "diagnostics.state.summary_warning",
                scope=scope,
                detail=str(summary.error),
            )
        )
    return tuple(checks)


def product_preflight(
    registries: ProductRegistries,
    *,
    workspace: WorkspaceRecord | None = None,
    repo_root: str | Path | None = None,
    home: str | Path | None = None,
) -> tuple[ProductDiagnostic, ...]:
    checks: list[ProductDiagnostic] = []
    root = Path(repo_root).expanduser() if repo_root is not None else Path(__file__).resolve().parents[2]
    home_path = Path(home).expanduser() if home is not None else ui_home()
    checks.append(_path_writable_check(home_path))

    igv_path = root / "scpolya_ui" / "vendor" / "igv.min.js"
    if igv_path.exists() and igv_path.stat().st_size > 100_000:
        checks.append(diagnostic("ok", "diagnostics.igv.ready", path=str(igv_path)))
    else:
        checks.append(diagnostic("error", "diagnostics.igv.missing", path=str(igv_path)))

    if not registries.workspaces.workspaces:
        checks.append(diagnostic("warning", "diagnostics.workspace.none"))
        return tuple(checks)

    selected = workspace or registries.workspaces.workspaces[0]
    for message in validate_workspace_paths(selected):
        checks.append(
            ProductDiagnostic(
                level=message.level,
                message_key=message.message_key,
                params=message.params,
                scope="workspace",
            )
        )

    summary = summarize_run_state(selected.db_path)
    if summary.exists:
        checks.append(
            diagnostic(
                "ok",
                "diagnostics.state.summary_ready",
                scope="workspace",
                path=summary.db_path,
            )
        )
        checks.extend(run_state_diagnostics(summary, scope="workspace"))
    else:
        checks.append(
            diagnostic(
                "error",
                "validation.state_db.missing",
                scope="workspace",
                path=summary.db_path,
            )
        )
    if summary.error and not summary.exists:
        checks.append(
            diagnostic(
                "warning",
                "diagnostics.state.summary_warning",
                scope="workspace",
                detail=summary.error,
            )
        )

    missing = required_missing(summary.config or {})
    if missing:
        checks.append(
            diagnostic(
                "warning",
                "diagnostics.config.missing_required",
                scope="workspace",
                keys=", ".join(missing),
            )
        )
    elif summary.config:
        checks.append(diagnostic("ok", "diagnostics.config.ready", scope="workspace"))
    else:
        checks.append(diagnostic("warning", "diagnostics.config.empty", scope="workspace"))

    ref = reference_status(summary.config or {}, selected.root_path, home=home_path)
    if ref.status == "ready":
        checks.append(
            diagnostic(
                "ok",
                "diagnostics.reference.ready",
                scope="workspace",
                genome=ref.genome,
                path=ref.fasta_path,
            )
        )
    else:
        checks.append(
            diagnostic(
                "warning",
                "diagnostics.reference.missing",
                scope="workspace",
                genome=ref.genome,
                path=ref.expected_cache_path,
            )
        )

    failures = job_failure_diagnostics(registries.jobs.jobs, workspace_id=selected.id, limit=5)
    checks.extend(failures)
    return tuple(checks)


def job_failure_diagnostics(
    jobs: list[JobRecord],
    *,
    workspace_id: str | None = None,
    limit: int = 10,
) -> tuple[ProductDiagnostic, ...]:
    out: list[ProductDiagnostic] = []
    terminal_failures = {"failed", "exited", "stopped"}
    for job in jobs:
        if workspace_id is not None and job.workspace_id != workspace_id:
            continue
        if job.status not in terminal_failures:
            continue
        out.append(
            diagnostic(
                "warning",
                "diagnostics.job.failure",
                job_id=job.id,
                status=job.status,
                exit_code="" if job.exit_code is None else str(job.exit_code),
                log_path=job.log_path or "",
                scope="workspace",
            )
        )
        if len(out) >= limit:
            break
    return tuple(out)


def _path_writable_check(path: Path) -> ProductDiagnostic:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return ProductDiagnostic("error", "diagnostics.ui_home.not_writable", {"path": str(path)}, detail=str(exc))
    return diagnostic("ok", "diagnostics.ui_home.writable", path=str(path))
