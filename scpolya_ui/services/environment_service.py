"""Read-only checks of the runtime a launched pipeline job will inherit."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from scpolya_ui.registry import PathMapping, apply_path_mappings_to_value

# Must match REQUIRED_TOOLS in main.py; tests/test_ui_environment_service.py
# keeps the two lists in step.
PIPELINE_TOOLS = ("featureCounts", "samtools", "umi_tools", "bedtools", "bedToBigBed", "bedGraphToBigWig")

REFERENCE_INPUT_KEYS = ("inputs", "gene_gtf", "chrom_sizes", "bulk_window_gtf", "bulk_gene_gtf")


@dataclass(frozen=True)
class ToolCheck:
    name: str
    path: str

    @property
    def found(self) -> bool:
        return bool(self.path)


@dataclass(frozen=True)
class InputFileCheck:
    key: str
    configured: str
    resolved: str
    exists: bool
    mapped: bool


def job_search_path(python_executable: str | None = None, base_path: str | None = None) -> str:
    """PATH a UI-launched run sees.

    Mirrors ``job_supervisor.child_environment``: the run inherits this
    process's PATH with the run interpreter's directory prepended.
    """
    current = os.environ.get("PATH", "") if base_path is None else base_path
    parts = current.split(os.pathsep) if current else []
    bin_dir = str(Path(python_executable or sys.executable).expanduser().parent)
    if bin_dir not in parts:
        parts.insert(0, bin_dir)
    return os.pathsep.join(parts)


def pipeline_tool_checks(
    tools: Iterable[str] = PIPELINE_TOOLS,
    *,
    search_path: str | None = None,
) -> tuple[ToolCheck, ...]:
    path = job_search_path() if search_path is None else search_path
    return tuple(ToolCheck(name=tool, path=shutil.which(tool, path=path) or "") for tool in tools)


def job_python_executable() -> str:
    return sys.executable


def reference_input_checks(
    config: Mapping[str, Any],
    mappings: Iterable[PathMapping] = (),
    keys: Iterable[str] = REFERENCE_INPUT_KEYS,
) -> tuple[InputFileCheck, ...]:
    mappings = tuple(mappings)
    checks = []
    for key in keys:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        resolved = apply_path_mappings_to_value(value, mappings) if mappings else value
        path = Path(resolved).expanduser()
        checks.append(
            InputFileCheck(
                key=key,
                configured=value,
                resolved=str(path),
                exists=path.is_file(),
                mapped=resolved != value,
            )
        )
    return tuple(checks)
