"""Server-side directory picker used by the local workspace registry UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from scpolya_ui.components.interaction import make_keyboard_selectable


PathEntryKind = Literal["directory", "state_db"]


@dataclass(frozen=True)
class WorkspacePathEntry:
    name: str
    path: Path
    kind: PathEntryKind


def initial_picker_directory(path: str | Path | None, *, fallback: Path | None = None) -> Path:
    """Return the nearest existing directory for an optional path hint."""

    fallback_path = (fallback or Path.home()).expanduser()
    try:
        fallback_path = fallback_path.resolve()
    except OSError:
        fallback_path = Path.cwd().resolve()

    raw_path = str(path or "").strip()
    candidate = Path(raw_path).expanduser() if raw_path else fallback_path
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        return fallback_path

    if candidate.is_file():
        candidate = candidate.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else fallback_path


def workspace_path_entries(
    directory: str | Path,
    *,
    show_hidden: bool = False,
) -> tuple[WorkspacePathEntry, ...]:
    """List navigable directories and directly selectable state databases."""

    current = Path(directory).expanduser().resolve()
    if not current.is_dir():
        raise NotADirectoryError(str(current))

    entries: list[WorkspacePathEntry] = []
    for item in current.iterdir():
        if not show_hidden and item.name.startswith("."):
            continue
        try:
            if item.is_dir():
                entries.append(WorkspacePathEntry(item.name, item.resolve(), "directory"))
            elif item.is_file() and item.name == "state.sqlite":
                entries.append(WorkspacePathEntry(item.name, item.resolve(), "state_db"))
        except OSError:
            # A disappearing or inaccessible entry should not break the picker.
            continue
    return tuple(sorted(entries, key=lambda item: (item.kind != "directory", item.name.casefold())))


class WorkspacePathPicker:
    """A small server-side filesystem browser for local NiceGUI deployments."""

    def __init__(
        self,
        *,
        translate: Callable[..., str],
        on_select: Callable[[str], None],
    ) -> None:
        from nicegui import ui

        self._translate = translate
        self._on_select = on_select
        self._dialog = None
        self._content = None
        self._location_input = None
        self._directory = initial_picker_directory(None)
        self._show_hidden = False
        self._error = ""

        with ui.dialog() as dialog:
            self._dialog = dialog
            with ui.card().classes("workspace-path-picker"):
                with ui.row().classes("workspace-path-picker-header w-full"):
                    ui.icon("folder_open").classes("workspace-path-picker-icon")
                    with ui.column().classes("gap-1 min-w-0"):
                        ui.label(self._t("workspace.path_picker.title")).classes(
                            "workspace-path-picker-title"
                        )
                        ui.label(self._t("workspace.path_picker.subtitle")).classes(
                            "workspace-path-picker-subtitle"
                        )

                @ui.refreshable
                def content() -> None:
                    self._render_content()

                self._content = content
                content()

    def open(self, path: str | Path | None = None) -> None:
        self._directory = initial_picker_directory(path)
        self._show_hidden = False
        self._error = ""
        self._content.refresh()
        self._dialog.open()

    def _t(self, key: str, **params: object) -> str:
        return self._translate(key, **params)

    def _render_content(self) -> None:
        from nicegui import ui

        with ui.column().classes("workspace-path-picker-body w-full"):
            with ui.row().classes("workspace-path-picker-toolbar w-full"):
                home = ui.button(icon="home", on_click=self._go_home).props(
                    f'flat dense aria-label="{self._t("workspace.path_picker.home")}"'
                )
                home.tooltip(self._t("workspace.path_picker.home"))
                root = ui.button(icon="computer", on_click=self._go_root).props(
                    f'flat dense aria-label="{self._t("workspace.path_picker.root")}"'
                )
                root.tooltip(self._t("workspace.path_picker.root"))
                parent = ui.button(icon="arrow_upward", on_click=self._go_parent).props(
                    f'flat dense aria-label="{self._t("workspace.path_picker.parent")}"'
                )
                parent.tooltip(self._t("workspace.path_picker.parent"))
                if self._directory.parent == self._directory:
                    parent.props("disable")
                refresh = ui.button(icon="refresh", on_click=self._refresh).props(
                    f'flat dense aria-label="{self._t("workspace.path_picker.refresh")}"'
                )
                refresh.tooltip(self._t("workspace.path_picker.refresh"))
                ui.element("div").classes("workbench-toolbar-spacer")
                ui.checkbox(
                    self._t("workspace.path_picker.show_hidden"),
                    value=self._show_hidden,
                    on_change=self._toggle_hidden,
                ).props("dense")

            with ui.row().classes("workspace-path-picker-location-row w-full"):
                location = ui.input(
                    value=str(self._directory),
                    on_change=None,
                ).classes("workspace-path-picker-location w-full").props(
                    f'dense outlined hide-bottom-space aria-label="{self._t("workspace.path_picker.location")}"'
                )
                self._location_input = location
                go = ui.button(icon="arrow_forward", on_click=self._go_location).props(
                    f'unelevated dense color=primary aria-label="{self._t("workspace.path_picker.go")}"'
                )
                go.tooltip(self._t("workspace.path_picker.go"))

            if self._error:
                with ui.row().classes("workspace-path-picker-error w-full"):
                    ui.icon("warning").classes("product-status-missing")
                    ui.label(self._error).classes("product-small-muted product-status-missing")

            with ui.element("div").classes("workspace-path-picker-list w-full"):
                listing_failed = False
                try:
                    entries = workspace_path_entries(
                        self._directory,
                        show_hidden=self._show_hidden,
                    )
                except OSError:
                    entries = ()
                    listing_failed = True
                    ui.label(self._t("workspace.path_picker.unreadable")).classes(
                        "workspace-path-picker-empty"
                    )
                if not entries and not listing_failed:
                    ui.label(self._t("workspace.path_picker.empty")).classes(
                        "workspace-path-picker-empty"
                    )
                for entry in entries:
                    row = ui.element("div").classes(
                        f"workspace-path-picker-row workspace-path-picker-row-{entry.kind}"
                    )
                    make_keyboard_selectable(
                        row,
                        lambda _event, entry=entry: self._activate_entry(entry),
                        label=entry.name,
                    )
                    with row:
                        ui.icon("folder" if entry.kind == "directory" else "storage").classes(
                            "workspace-path-picker-entry-icon"
                        )
                        with ui.column().classes("workspace-path-picker-entry-copy gap-1"):
                            entry_name = ui.label(entry.name).classes("workspace-path-picker-entry-name")
                            entry_name.tooltip(entry.name)
                            ui.label(
                                self._t(
                                    "workspace.path_picker.folder"
                                    if entry.kind == "directory"
                                    else "workspace.path_picker.state_db"
                                )
                            ).classes("workspace-path-picker-entry-kind")
                        ui.icon("chevron_right" if entry.kind == "directory" else "check").classes(
                            "workspace-path-picker-entry-action"
                        )

            with ui.row().classes("workspace-path-picker-actions w-full"):
                ui.label(str(self._directory)).classes(
                    "workspace-path workspace-path-compact workspace-path-picker-current"
                )
                ui.element("div").classes("workbench-toolbar-spacer")
                ui.button(
                    self._t("common.cancel"),
                    icon="close",
                    on_click=self._dialog.close,
                ).props("flat no-caps")
                ui.button(
                    self._t("workspace.path_picker.use_folder"),
                    icon="check",
                    on_click=lambda: self._select(self._directory),
                ).props("unelevated no-caps color=primary")

    def _activate_entry(self, entry: WorkspacePathEntry) -> None:
        if entry.kind == "directory":
            self._directory = entry.path
            self._error = ""
            self._content.refresh()
        else:
            self._select(entry.path)

    def _select(self, path: str | Path) -> None:
        self._on_select(str(Path(path).expanduser().resolve()))
        self._dialog.close()

    def _go_home(self) -> None:
        self._navigate(Path.home())

    def _go_root(self) -> None:
        self._navigate(Path("/"))

    def _go_parent(self) -> None:
        self._navigate(self._directory.parent)

    def _refresh(self) -> None:
        self._error = ""
        self._content.refresh()

    def _toggle_hidden(self, event) -> None:
        self._show_hidden = bool(event.value)
        self._content.refresh()

    def _go_location(self) -> None:
        value = str(getattr(self._location_input, "value", "") or "").strip()
        candidate = Path(value).expanduser()
        try:
            candidate = candidate.resolve(strict=False)
            if candidate.is_dir():
                self._navigate(candidate)
            elif candidate.is_file() and candidate.name == "state.sqlite":
                self._select(candidate)
            else:
                self._error = self._t("workspace.path_picker.invalid_directory")
                self._content.refresh()
        except OSError:
            self._error = self._t("workspace.path_picker.invalid_directory")
            self._content.refresh()

    def _navigate(self, path: str | Path) -> None:
        try:
            directory = Path(path).expanduser().resolve(strict=True)
            if not directory.is_dir():
                raise NotADirectoryError(str(directory))
        except OSError:
            self._error = self._t("workspace.path_picker.invalid_directory")
            self._content.refresh()
            return
        self._directory = directory
        self._error = ""
        self._content.refresh()
