from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scpolya_ui.registry import WorkspaceRecord


@dataclass(frozen=True)
class ValidationMessage:
    level: str
    message_key: str
    params: dict[str, str]


def ok_message(message_key: str, **params: str) -> ValidationMessage:
    return ValidationMessage(level="ok", message_key=message_key, params=dict(params))


def warning_message(message_key: str, **params: str) -> ValidationMessage:
    return ValidationMessage(level="warning", message_key=message_key, params=dict(params))


def error_message(message_key: str, **params: str) -> ValidationMessage:
    return ValidationMessage(level="error", message_key=message_key, params=dict(params))


def validate_workspace_paths(workspace: WorkspaceRecord) -> tuple[ValidationMessage, ...]:
    messages: list[ValidationMessage] = []
    root = Path(workspace.root_path).expanduser()
    db = Path(workspace.db_path).expanduser()
    if root.exists():
        messages.append(ok_message("validation.workspace_root.ready", path=str(root)))
    else:
        messages.append(error_message("validation.workspace_root.missing", path=str(root)))
    if db.exists():
        messages.append(ok_message("validation.state_db.ready", path=str(db)))
    else:
        messages.append(error_message("validation.state_db.missing", path=str(db)))
    return tuple(messages)
