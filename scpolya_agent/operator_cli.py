"""Trusted operator CLI: configured native source, accepted controllers, no authority minting."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .cli import emit, failure, take_manifest, TOOLS, SOURCE_TOOLS
from .manifest import load_manifest, need, parse

ASSETS = Path(__file__).parent / "assets"


def native_binding(manifest):
    native = manifest.doc.get("native", {})
    need(set(native) == {"source_root", "python", "expected_commit"}, "native_config_required",
         "native requires source_root, python, expected_commit")
    root, python = manifest.path(native["source_root"]), manifest.executable(native["python"])
    need(isinstance(native["expected_commit"], str) and len(native["expected_commit"]) == 40
         and all(c in "0123456789abcdef" for c in native["expected_commit"]), "native_commit_invalid", "40-hex Git commit required")
    need(root.is_dir(), "native_source_missing", str(root))
    required = ("main.py", "arguments.py", "native_entry.py", "scpolya_agent/native_task_driver.py",
                "scpolya_ui/command_builder.py", "scpolya_ui/job_supervisor.py", "task_manage/run_config_preview.py")
    for rel in required:
        p = manifest.path(str(root / rel))
        need(p.is_file(), "native_source_incomplete", rel)
    need(python.is_file() and os.access(python, os.X_OK), "native_python_missing", str(python))
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        need(result.returncode == 0, "native_git_unavailable", result.stderr[:300])
        return result.stdout.strip()
    need(git("rev-parse", "--show-toplevel") == str(root), "native_source_root", "checkout root required")
    need(git("rev-parse", "HEAD") == native["expected_commit"], "native_source_commit", "configured commit differs")
    need(not git("status", "--porcelain", "--untracked-files=all", "--ignored=no"),
         "native_source_dirty", "native checkout must be clean including untracked source")
    return root, python


def doctor(manifest, check_native=False):
    findings = []
    native_source = None
    native_python = sys.executable
    for source_id in manifest.doc.get("sources", {}):
        try:
            record = manifest.doc["sources"][source_id]
            path, raw = manifest.pinned(record, record["max_bytes"])
            parse(raw) if record["format"] == "json" else raw.decode("utf-8")
            findings.append(dict(id=source_id, kind="source", status="available", path=str(path), sha256=record["sha256"]))
        except Exception as exc:
            findings.append(dict(id=source_id, kind="source", status="unavailable", code=getattr(exc, "code", "source_missing"), detail=str(exc)))
    if check_native or manifest.doc.get("native"):
        try:
            root, python = native_binding(manifest)
            native_source, native_python = root, str(python)
            findings.append(dict(kind="native", status="available", source_root=str(root), python=str(python),
                                 scope="source layout, Git identity and Python presence only; native bridge checks dependencies/tools"))
        except Exception as exc:
            findings.append(dict(kind="native", status="unavailable", code=getattr(exc, "code", "native_missing"), detail=str(exc)))
    tool_runtime = []
    for name, module in TOOLS.items():
        blockers = []
        if name in SOURCE_TOOLS and native_source is None:
            blockers.append("complete pinned native checkout required for source-relative helpers")
        tool_runtime.append(dict(tool=name, entry=str(native_source / "scpolya_agent" / module) if native_source else str(Path(__file__).parent / module),
                                 python=native_python, status="unavailable" if blockers else "configuration_present_runtime_not_certified",
                                 blockers=blockers,
                                 outer_envelope="original tool's cgroup/thread/dependency contract remains authoritative"))
    return dict(schema="scpolya.operator_doctor.v1", status="degraded" if any(f["status"] == "unavailable" for f in findings) else "available",
                manifest=str(manifest.filepath), manifest_sha256=manifest.sha256, findings=findings,
                tool_runtime=tool_runtime,
                differential_apa={
                    "entry": str(native_source / "scpolya_agent/differential_apa.py") if native_source else None,
                    "status": "configuration_present_runtime_not_certified" if native_source and
                              (native_source / "scpolya_agent/differential_apa.py").is_file() else "unavailable",
                    "commands": ["plan", "execute", "status", "result"],
                    "requirements": "Configured pinned clean checkout and scientific Python; inference additionally requires R and requested packages.",
                },
                grants="Doctor only inspects; it starts no execution.",
                portability_limits=["Native execution requires a complete pinned Git checkout and its scientific runtime/tools.",
                                    "Some generic scientific tools retain exact interpreter and OS envelope requirements; tool refusal is authoritative."])


def run(args, manifest=None):
    if not args or args[0] in ("help", "--help"):
        return emit(dict(schema="scpolya.operator.v1", status="available",
                         commands=["validate", "doctor", "native", "differential-apa", "annotate"],
                         differential_apa_commands=["plan", "execute", "status", "result"],
                         annotate_commands=["add", "list", "reply", "wait"],
                         usage="scpolya-operator --manifest PATH COMMAND [ARGS]"))
    if args[0] == "annotate":
        # Notes are a UI overlay stored under the UI home; they need no native binding.
        from .annotations import main as annotate
        return annotate(args[1:])
    manifest = manifest or load_manifest()
    op, rest = args[0], args[1:]
    if op in ("validate", "doctor"):
        need(not rest or rest == ["--native"], "operator_arguments", "only --native is supported")
        if op == "validate":
            return emit(dict(schema="scpolya.operator_validation.v1", status="valid", manifest_sha256=manifest.sha256,
                             scope="manifest structure and path representations; doctor verifies source bytes/runtime"))
        result = doctor(manifest, rest == ["--native"])
        return emit(result, 2 if result["status"] == "degraded" else 0)
    env = {**os.environ, "SCPOLYA_MANIFEST": str(manifest.filepath), "PYTHONDONTWRITEBYTECODE": "1"}
    if op == "native":
        root, python = native_binding(manifest)
        # Run the installed reviewed adapter in a fresh isolated interpreter,
        # bound to the explicit native checkout, never the wheel's ambient imports.
        return subprocess.run([str(python), "-I", "-B", str(ASSETS / "native_entry.py"),
                               "--source-root", str(root), *rest], env=env).returncode
    if op == "differential-apa":
        root, python = native_binding(manifest)
        script = manifest.path(str(root / "scpolya_agent/differential_apa.py"))
        need(script.is_file(), "differential_apa_source_missing", str(script))
        return subprocess.run([str(python), "-I", "-B", str(script), *rest], env=env).returncode
    need(op not in ("action-binding", "action", "action-worker", "rerun", "scoped-export"), "operation_not_in_release",
         op + " controls are not included in this release")
    need(False, "operator_operation", op)


def main(argv=None):
    try:
        manifest, args = take_manifest(sys.argv[1:] if argv is None else argv)
        return run(args, manifest)
    except (ValueError, OSError, RecursionError) as exc:
        return failure(exc)


if __name__ == "__main__":
    raise SystemExit(main())
