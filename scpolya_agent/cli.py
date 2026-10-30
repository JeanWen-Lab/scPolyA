"""Installed public CLI. Existing scientific contracts retain their own schemas."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .manifest import ManifestError, load_manifest

TOOLS = {
    "coverage": "coverage_diagnostics.py",
    "native-coverage": "native_coverage.py",
    "native-gene-diagnostics": "native_gene_diagnostics.py",
    "tail-profile": "tail_profile.py",
}
SOURCE_TOOLS = {"native-gene-diagnostics", "tail-profile"}


def emit(value, code=0):
    print(json.dumps(value, sort_keys=True, allow_nan=False))
    return code


def failure(exc):
    return emit(dict(schema="scpolya.cli.v1", status="refused", error={
        "code": getattr(exc, "code", "invalid_invocation"), "detail": str(exc)[:800]}), 2)


def take_manifest(argv):
    argv = list(argv)
    path = None
    if argv and argv[0] == "--manifest":
        if len(argv) < 2:
            raise ValueError("--manifest requires PATH")
        path, argv = argv[1], argv[2:]
    elif argv and argv[0].startswith("--manifest="):
        path, argv = argv[0].split("=", 1)[1], argv[1:]
    manifest = load_manifest(path) if path or os.environ.get("SCPOLYA_MANIFEST") else None
    return manifest, argv


def tool(argv, manifest):
    if not argv or argv[0] not in TOOLS:
        return emit(dict(schema="scpolya.tools.v1", status="available", tools=TOOLS,
                         usage="scpolya-agent [--manifest PATH] tool NAME -- ORIGINAL_ARGS",
                         runtime="Some accepted tools require the exact scientific Python and OS envelope; see doctor."), 0 if not argv else 2)
    name, rest = argv[0], argv[1:]
    if rest[:1] == ["--"]:
        rest = rest[1:]
    python = sys.executable
    script = Path(__file__).parent / TOOLS[name]
    if manifest and manifest.doc.get("native"):
        from .operator_cli import native_binding
        root, python = native_binding(manifest)
        selected = manifest.path(str(root / "scpolya_agent" / TOOLS[name]))
        from .manifest import read_regular, need
        need(hashlib.sha256(read_regular(selected, 1 << 20)).digest() ==
             hashlib.sha256(read_regular(script, 1 << 20)).digest(),
             "tool_source_module_mismatch", "selected checkout tool must match installed accepted module: " + name)
        script = selected
    elif name in SOURCE_TOOLS:
        from .manifest import need
        need(False, "tool_native_source_required", name + " requires manifest.native with a complete pinned checkout for its source-relative helpers")
    # Use original direct script entry: imports, caps, OS envelope and pins stay intact.
    return subprocess.run([str(python), "-I", "-B", str(script), *rest]).returncode


def main(argv=None):
    prior_manifest = os.environ.get("SCPOLYA_MANIFEST")
    try:
        manifest, args = take_manifest(sys.argv[1:] if argv is None else argv)
        if manifest:
            os.environ["SCPOLYA_MANIFEST"] = str(manifest.filepath)
        if not args or args[0] in ("--help", "help", "capabilities"):
            return emit(dict(schema="scpolya.cli.v1", version="2.1.0", status="available",
                             commands=["source-query", "citation-check", "skills", "tool", "inspect", "operator"],
                             operator_extensions={"native": "DeepCPS settings in native request.run; driver deepcps-result reads own completed output",
                                                  "differential-apa": ["plan", "execute", "status", "result"]},
                             manifest_schema="scpolya.operator_manifest.v1",
                             installation="pip install .; scientific runtime/data are configured separately"))
        if args[0] == "--version":
            return emit(dict(schema="scpolya.cli.v1", version="2.1.0"))
        if args[0] == "operator":
            from .operator_cli import run
            return run(args[1:], manifest)
        if args[0] == "source-query":
            from .source_query import query
            p = argparse.ArgumentParser(allow_abbrev=False)
            p.add_argument("--grant", required=True)
            p.add_argument("--source", required=True)
            p.add_argument("--pointer", required=True)
            selected = p.parse_args(args[1:])
            return emit(query(manifest or load_manifest(), selected.grant, selected.source, selected.pointer))
        if args[0] == "tool":
            return tool(args[1:], manifest)
        if args[0] == "skills":
            p = argparse.ArgumentParser(allow_abbrev=False)
            p.add_argument("--id")
            a = p.parse_args(args[1:])
            base = Path(__file__).parent
            paths = {f"scpolya-{name}": base / "assets/skills" / f"scpolya-{name}/SKILL.md"
                     for name in ("prepare-run", "compare-settings", "resume-diagnose", "explain-export")}
            from .manifest import need
            need(a.id is None or a.id in paths, "skill_unknown", str(a.id))
            selected = paths if a.id is None else {a.id: paths[a.id]}
            rows = []
            for name, path in selected.items():
                raw = path.read_bytes()
                row = dict(id=name, path=str(path), sha256=hashlib.sha256(raw).hexdigest())
                if a.id:
                    row["text"] = raw.decode()
                rows.append(row)
            return emit(dict(schema="scpolya.skill_inventory.v1", status="available", skills=rows))
        if args[0] == "citation-check":
            from .citations import check
            p = argparse.ArgumentParser(allow_abbrev=False)
            p.add_argument("--delivery", required=True)
            p.add_argument("--source", required=True)
            p.add_argument("--pointer", required=True)
            p.add_argument("--value-sha256", required=True)
            a = p.parse_args(args[1:])
            return emit(check(manifest or load_manifest(), a.delivery, a.source, a.pointer, a.value_sha256))
        from .__main__ import main as legacy_main
        return legacy_main(args)
    except (ManifestError, ValueError, OSError, RecursionError) as exc:
        return failure(exc)
    finally:
        if prior_manifest is None:
            os.environ.pop("SCPOLYA_MANIFEST", None)
        else:
            os.environ["SCPOLYA_MANIFEST"] = prior_manifest


if __name__ == "__main__":
    raise SystemExit(main())
