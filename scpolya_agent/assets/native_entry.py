#!/usr/bin/env python3
"""Bounded native product entry: discovery, settings, and explicit routing.

This adapter adds no scheduler or science.  It only binds a complete native
source checkout, exposes the existing native_task_driver operations, and names
four instruction skills.  Runs and recovers execute exactly the existing
native plan file given (verified by its plan_sha256); no approval file is
required.  The researcher confirms the shown plan parameters in the chat.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

SCHEMA = "scpolya.native_product_entry.v1"
ENTRY_VERSION = "20261020-r2"
ANALYSIS_SKILL_SHA256 = "eb9dcbf1bbb9516ea26861146ba78740a3e9aced79d3728ce80ad544a3c2cb1c"
SKILLS = (
    {"id": "scpolya-prepare-run", "path": "skills/scpolya-prepare-run/SKILL.md",
     "covers": "inputs, settings, native plan, researcher confirmation in chat, driver run"},
    {"id": "scpolya-compare-settings", "path": "skills/scpolya-compare-settings/SKILL.md",
     "covers": "small matched native studies and honest yield/support comparison"},
    {"id": "scpolya-resume-diagnose", "path": "skills/scpolya-resume-diagnose/SKILL.md",
     "covers": "native status/diagnostics and researcher-confirmed recover"},
    {"id": "scpolya-explain-export", "path": "skills/scpolya-explain-export/SKILL.md",
     "covers": "verified-output explanation and GUI handoff"},
)
REQUIRED_SOURCE_FILES = (
    "scpolya_agent/native_task_driver.py",
    "arguments.py",
    "main.py",
    "scpolya_ui/command_builder.py",
    "scpolya_ui/job_supervisor.py",
    "task_manage/run_config_preview.py",
    "task_manage/cache_epoch_status.py",
)
DRIVER_VERBS = ("plan", "run", "recover", "status", "stop", "harvest", "snapshot", "identity", "deepcps-result")


class EntryError(Exception):
    def __init__(self, code: str, detail: str, next_operation: str = "bridge"):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.next_operation = next_operation


def emit(doc: dict, code: int = 0) -> int:
    print(json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return code


def error(code: str, detail: str, next_operation: str = "bridge") -> int:
    return emit({"schema": SCHEMA, "entry_version": ENTRY_VERSION, "status": "error",
                 "error": {"code": code, "detail": detail[:1000],
                           "next_supported_operation": next_operation}}, 2)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise EntryError("module_load_failed", f"cannot load {path}", "bridge")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def validate_source(root: Path) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise EntryError("native_source_root_missing", f"not a directory: {root}", "bridge --source-root ABS_DIR")
    missing = [rel for rel in REQUIRED_SOURCE_FILES if not (root / rel).is_file()]
    if missing:
        # This is the explicit copied-package failure: importing the driver here
        # would make dependence on a hidden ambient path look like success.
        raise EntryError(
            "incomplete_native_source_root",
            "complete native checkout required; missing " + ", ".join(missing),
            "set --source-root to a complete checkout (not a copied scpolya_agent package)")
    pins = {rel: sha256(root / rel) for rel in REQUIRED_SOURCE_FILES}
    source_root = str(root)
    return {"source_root": source_root, "complete": True,
            "completeness_scope": "required layout present, not dependency or runtime certification",
            "hash_trust": "current observations, not external trusted source pins",
            "required_files": pins,
            "required_files_sha256": hashlib.sha256(
                json.dumps(pins, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def driver_module(root: Path):
    bind_import_root(root)
    return load_module("scpolya_native_task_driver_bound", root / "scpolya_agent/native_task_driver.py")


def arguments_module(root: Path):
    bind_import_root(root)
    return load_module("scpolya_arguments_bound", root / "arguments.py")


def product_catalog(root: Path) -> dict:
    # Case-study evidence operations are not distributed with this release.
    return {
        "binding": "not_included_in_release",
        "configured": False,
        "operations": [],
        "tri_state": [],
        "unsupported_claims": [],
        "status_contract": "source_absent and unsupported are distinct; never impute",
    }


def bind_import_root(root: Path) -> None:
    """Private inspection helpers fail closed on previously imported roots."""
    for name, module in tuple(sys.modules.items()):
        if name == "arguments" or name.split(".")[0] in ("scpolya_ui", "task_manage", "scpolya_agent"):
            location = getattr(module, "__file__", None)
            if location and not Path(location).resolve().is_relative_to(root.resolve()):
                raise EntryError("native_import_root_conflict", f"{name} already loaded outside {root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def metadata(path: str, label: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise EntryError("native_metadata_missing", f"{label} file absent: {p}")
    if p.stat().st_size > 16 << 20:
        raise EntryError("native_metadata_invalid", f"{label} exceeds 16MiB metadata bound")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out
    try:
        value = json.loads(p.read_text(), object_pairs_hook=pairs,
                           parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (ValueError, UnicodeError) as exc:
        raise EntryError("native_metadata_invalid", f"{label}: {exc}") from exc
    if not isinstance(value, dict):
        raise EntryError("native_metadata_invalid", f"{label} must be an object")
    return value


def observed_source_identity(root: Path) -> tuple[dict, dict]:
    """Reproduce native runtime_identity.source using stdlib/Git, before imports.

    Only checkout source files are hashed; no plan input/BAM is opened here.
    This comparison inherits the native untracked-content limitation.
    """
    def git(*args):
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           text=True, timeout=30)
        if r.returncode:
            raise EntryError("native_source_identity_unavailable", r.stderr[:500])
        return r.stdout
    if Path(git("rev-parse", "--show-toplevel").strip()).resolve() != root:
        raise EntryError("native_source_root_mismatch", "selected root is not the checkout root")
    head = git("rev-parse", "HEAD").strip()
    index = git("ls-files", "-s")
    paths = sorted(line.split("\t", 1)[1] for line in index.splitlines())
    driver = root / "scpolya_agent/native_task_driver.py"
    if "scpolya_agent/native_task_driver.py" not in paths or driver.resolve() != driver:
        raise EntryError("native_driver_not_bound", "driver must be a regular tracked file at the selected root")
    digest = hashlib.sha256()
    for rel in paths:
        p = root / rel
        digest.update(rel.encode() + b"\0" + (sha256(p).encode() if p.is_file() and not p.is_symlink() else b"-") + b"\n")
    source = {"head": head, "tracked_index_sha256": hashlib.sha256(index.encode()).hexdigest(),
              "tracked_bytes_sha256": digest.hexdigest(),
              "worktree_status": git("status", "--porcelain=v1", "--untracked-files=all", "--ignored=no").splitlines()}
    product_git = {"head": head, "dirty_tracked": bool(git("status", "--porcelain", "--untracked-files=no").strip())}
    return source, product_git


def admit_plan(root: Path, path: str, approval_path: str | None, action: str) -> dict:
    plan = metadata(path, "plan")
    body = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if plan.get("schema") != "scpolya.native_task_plan.v1" or plan.get("plan_sha256") != hashlib.sha256(canonical(body)).hexdigest():
        raise EntryError("plan_hash_mismatch", str(path))
    # approval_path is accepted for backward compatibility and ignored: no
    # approval file is required or validated.
    product = plan.get("product") or {}
    if product.get("repo_root") != str(root):
        raise EntryError("plan_source_root_mismatch", "selected checkout must equal plan.product.repo_root; no implicit rebind")
    identity = plan.get("runtime_identity")
    required = {"python", "conda_packages_sha256", "selected_packages", "tools", "source", "identity_sha256"}
    if not isinstance(identity, dict) or not required <= identity.keys():
        raise EntryError("plan_source_identity_invalid", "native runtime_identity fields missing")
    identity_body = {k: v for k, v in identity.items() if k != "identity_sha256"}
    if identity["identity_sha256"] != hashlib.sha256(canonical(identity_body)).hexdigest():
        raise EntryError("plan_source_identity_invalid", "native runtime identity digest mismatch")
    observed, product_git = observed_source_identity(root)
    if identity["source"] != observed or product.get("git") != product_git:
        raise EntryError("plan_source_identity_mismatch", "selected checkout differs from plan source identity")
    python = plan.get("python")
    argv = plan.get("argv")
    if not isinstance(python, str) or not Path(python).is_absolute() or Path(python).resolve() != Path(sys.executable).resolve():
        raise EntryError("plan_python_mismatch", "invoke this entry with the plan's Python interpreter")
    if not isinstance(identity["python"], dict) or identity["python"].get("path") != python:
        raise EntryError("plan_source_identity_invalid", "plan Python differs from native runtime identity")
    if not isinstance(argv, list) or argv[:3] != [python, str(root / "main.py"), "run"]:
        raise EntryError("plan_entrypoint_mismatch", "plan argv must target selected checkout main.py run")
    return plan


def child_entry(command: list[str], *, help_requested=False) -> int:
    """Fresh -I -B child; native driver retains all plan/supervisor ownership."""
    result = subprocess.run(command, capture_output=True, text=True)
    if help_requested and result.returncode == 0:
        print(result.stdout, end="")
        return 0
    try:
        doc = json.loads(result.stdout)
        if not isinstance(doc, dict):
            raise ValueError("expected JSON object")
    except ValueError:
        lines = result.stderr.strip().splitlines()
        raise EntryError("native_child_failed", lines[-1][:700] if lines else "child returned no single JSON object")
    return emit(doc, result.returncode)


def analysis_skill(path: str | None) -> dict:
    expected = ANALYSIS_SKILL_SHA256
    if not path:
        return {"name": "scpolya-analysis", "status": "unavailable",
                "reason": "explicit path required; no private default",
                "expected_sha256": expected,
                "next_supported_operation": "pass --analysis-skill ABS_SKILL.md"}
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return {"name": "scpolya-analysis", "status": "unavailable", "path": str(p),
                "reason": "file absent", "expected_sha256": expected}
    actual = sha256(p)
    if actual != expected:
        return {"name": "scpolya-analysis", "status": "unavailable", "path": str(p),
                "reason": "sha256 mismatch; do not modify or substitute",
                "expected_sha256": expected, "actual_sha256": actual}
    return {"name": "scpolya-analysis", "status": "available", "path": str(p),
            "sha256": actual, "modified": False,
            "route": "use it for analysis interpretation alongside actual product operations"}


def skill_catalog(root: Path) -> list[dict]:
    out = []
    for skill in SKILLS:
        p = root / skill["path"]
        out.append({**skill, "status": "available" if p.is_file() else "unavailable",
                    "sha256": sha256(p) if p.is_file() else None})
    return out


def bridge(args) -> dict:
    binding = validate_source(Path(args.source_root))
    root = Path(binding["source_root"])
    drv = driver_module(root)
    identity = drv.runtime_identity(args.python, root)
    parser = arguments_module(root).build_parser()
    run_sub = next(a for a in parser._actions if getattr(a, "dest", "") == "command").choices["run"]
    parser_defaults = {a.dest: a.default for a in run_sub._actions
                       if a.dest != "help" and a.default is not argparse.SUPPRESS}
    catalog = product_catalog(root)
    analysis = analysis_skill(args.analysis_skill)
    skills = skill_catalog(root)
    runtime_ready = bool(identity["source"].get("tracked_bytes_sha256")) and all(
        value is not None for value in (identity.get("tools") or {}).values())
    status = "available" if runtime_ready else "degraded"
    return {
        "schema": SCHEMA, "entry_version": ENTRY_VERSION, "op": "bridge", "status": status,
        "native_bridge": {
            **binding,
            "driver": {
                "module_sha256": sha256(root / "scpolya_agent/native_task_driver.py"),
                "verbs": list(DRIVER_VERBS),
                "request_keys": sorted(drv.REQUEST_KEYS),
                "resource_keys": sorted(drv.RESOURCE_KEYS),
                "presets": sorted(drv.PRESETS),
                "runtime_ready": runtime_ready,
                "runtime_ready_scope": "observed tracked-source digest and tool presence; not dependency/science certification",
                "missing_tools": sorted(name for name, value in (identity.get("tools") or {}).items() if value is None),
                "plan_contract": "driver builds plan; entry never invents one",
                "runtime_identity": identity,
            },
            "deployment_warning": None if identity["source"].get("tracked_bytes_sha256") else "source identity incomplete",
            "copied_package_only": False,
        },
        "product_operations": catalog,
        "shared_analysis_skill": analysis,
        "skills": skills,
        "authorization": {
            "plan": "existing native plan path required",
            "run_recover": "existing native plan only; runs exactly that plan (plan_sha256); researcher confirms in chat",
            "approval_file_required": False,
            "os_security_claim": False,
        },
    }


def settings_op(args) -> dict:
    binding = validate_source(Path(args.source_root))
    root = Path(binding["source_root"])
    drv = driver_module(root)
    parser = arguments_module(root).build_parser()
    run_sub = next(a for a in parser._actions if getattr(a, "dest", "") == "command").choices["run"]
    defaults = {a.dest: a.default for a in run_sub._actions
                if a.dest != "help" and a.default is not argparse.SUPPRESS}
    preset = drv.PRESETS[args.preset]
    return {
        "schema": SCHEMA, "entry_version": ENTRY_VERSION, "op": "settings", "status": "observed",
        "source_root": binding["source_root"], "requested_preset": args.preset,
        "parser_defaults": defaults, "native_preset": preset,
        "meaning": {
            "minpolyA": "minimum total terminal A/T length in the called tail context, not homopolymer count",
            "unscaled_depth_threshold_polyA": "per-base poly(A)-tail coverage threshold, not number of tail reads in a cluster",
            "multi_site": "at least two native sites may include intronic sites; terminal-site eligibility is separate",
        },
        "warnings": [
            "published benchmark settings are not necessarily package defaults",
            "report the selected preset explicitly; never describe preset values as hidden defaults",
        ],
    }


def skills_op(args) -> dict:
    root = Path(args.source_root).resolve()
    return {"schema": SCHEMA, "entry_version": ENTRY_VERSION, "op": "skills", "status": "observed",
            "skills": skill_catalog(root)}


def driver_op(args) -> int:
    forwarded = list(args.driver_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    if not forwarded or forwarded[0] not in DRIVER_VERBS:
        return error("driver_verb_required", f"forward exactly one of: {', '.join(DRIVER_VERBS)}", "driver -- <verb>")
    verb = forwarded[0]
    help_requested = any(x in ("--help", "-h") for x in forwarded[1:])
    if verb in ("run", "recover") and not help_requested:
        present = {x.split("=", 1)[0] for x in forwarded[1:]}
        if "--plan" not in present:
            return error("existing_plan_required",
                         "existing --plan required; entry does not mint plans",
                         "prepare a native plan first")
    binding = validate_source(Path(args.source_root))
    root = Path(binding["source_root"])
    if not help_requested:
        p = DriverArgumentParser(add_help=False, allow_abbrev=False)
        options = {"plan": ("request",), "identity": ("python", "repo-root"),
                   "run": ("plan",), "recover": ("plan",),
                   "snapshot": ("plan", "label")}.get(verb, ("plan",))
        for option in options:
            if sum(x == "--" + option or x.startswith("--" + option + "=") for x in forwarded[1:]) > 1:
                raise EntryError("driver_arguments_invalid", f"duplicate --{option}")
            p.add_argument("--" + option, required=True)
        if verb in ("run", "recover"):
            # Deprecated and ignored; accepted so older commands keep working.
            if sum(x == "--approval" or x.startswith("--approval=") for x in forwarded[1:]) > 1:
                raise EntryError("driver_arguments_invalid", "duplicate --approval")
            p.add_argument("--approval", required=False, default=None)
        if verb == "deepcps-result":
            for option in ("offset", "limit"):
                if sum(x == "--" + option or x.startswith("--" + option + "=") for x in forwarded[1:]) > 1:
                    raise EntryError("driver_arguments_invalid", f"duplicate --{option}")
                p.add_argument("--" + option, type=int)
        parsed = p.parse_args(forwarded[1:])
        if verb in ("run", "recover") and not Path(parsed.plan).is_file():
            raise EntryError("existing_plan_required", "plan absent; entry does not mint plans")
        if hasattr(parsed, "plan"):
            admit_plan(root, parsed.plan, getattr(parsed, "approval", None), verb)
        elif verb == "plan":
            metadata(parsed.request, "request")
        elif Path(parsed.repo_root).resolve() != root:
            raise EntryError("native_source_root_mismatch", "identity --repo-root must equal selected --source-root")
    return child_entry([sys.executable, "-I", "-B", str(root / "scpolya_agent/native_task_driver.py"), *forwarded],
                       help_requested=help_requested)


class DriverArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise EntryError("driver_arguments_invalid", message, "driver -- <verb> --help")


def inspect_child(args) -> int:
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
               "--source-root", args.source_root, "_inspect", args.op]
    if args.op == "bridge":
        command += ["--python", args.python]
        if args.analysis_skill:
            command += ["--analysis-skill", args.analysis_skill]
    else:
        command += ["--preset", args.preset]
    return child_entry(command)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", default=str(Path(__file__).resolve().parent),
                   help="complete native checkout containing arguments.py and native_task_driver.py")
    sub = p.add_subparsers(dest="op", required=True)
    b = sub.add_parser("bridge", help="discover actual native/product capabilities")
    b.add_argument("--python", default=sys.executable, help="Python executable used for native runtime identity")
    b.add_argument("--analysis-skill", help="explicit existing scpolya-analysis SKILL.md; no default")
    b.set_defaults(func=bridge)
    s = sub.add_parser("settings", help="actual parser defaults and native preset")
    s.add_argument("--preset", choices=("manuscript-v4",), default="manuscript-v4")
    s.set_defaults(func=settings_op)
    k = sub.add_parser("skills", help="list the four instruction skills")
    k.set_defaults(func=skills_op)
    d = sub.add_parser("driver", help="forward one existing native_task_driver verb")
    d.add_argument("driver_args", nargs=argparse.REMAINDER,
                   help="existing driver arguments after --")
    d.set_defaults(func=driver_op)
    internal = sub.add_parser("_inspect", help=argparse.SUPPRESS)
    internal.add_argument("inspection", choices=("bridge", "settings"))
    internal.add_argument("--python", default=sys.executable)
    internal.add_argument("--analysis-skill")
    internal.add_argument("--preset", choices=("manuscript-v4",), default="manuscript-v4")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.op == "driver":
            return driver_op(args)
        if args.op in ("bridge", "settings"):
            return inspect_child(args)
        if args.op == "_inspect":
            return emit(bridge(args) if args.inspection == "bridge" else settings_op(args))
        return emit(args.func(args))
    except EntryError as exc:
        return error(exc.code, exc.detail, exc.next_operation)
    except Exception as exc:
        return error("entry_failed", f"{type(exc).__name__}: {exc}", "bridge")


if __name__ == "__main__":
    raise SystemExit(main())
