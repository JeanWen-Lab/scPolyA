"""The CLI writes exactly one versioned JSON document to stdout, including errors."""
from __future__ import annotations

import argparse
import contextlib
import json
import hashlib
import sys
import subprocess
from pathlib import Path

from . import INSPECTION_SCHEMA, encode_inspection, inspect_workspace


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "native-entry":
        entry = Path(__file__).resolve().parent.parent / "native_entry.py"
        if not entry.is_file():
            print(json.dumps({"schema": "scpolya.native_product_entry.v1", "status": "error",
                              "error": {"code": "native_entry_missing", "detail": str(entry)}}))
            return 2
        # Preserve isolated admission/driver processes; no cached cross-checkout imports.
        return subprocess.run([sys.executable, "-I", "-B", str(entry), *argv[1:]]).returncode
    if argv and argv[0] in ("saved-case-query", "evidence-query", "product"):
        # Case-study evidence packages are not distributed with this release.
        print(json.dumps({"schema": "scpolya.cli.v1", "status": "unavailable",
                          "error": {"code": "operation_not_in_release",
                                    "detail": argv[0] + " requires case-study data that is not included in this release"}},
                         sort_keys=True))
        return 2
    try:
        parser = _Parser(add_help=False)
        parser.add_argument("--help", action="store_true")
        commands = parser.add_subparsers(dest="command")
        inspect = commands.add_parser("inspect", add_help=False)
        inspect.add_argument("--run-root", required=True)
        inspect.add_argument("--repo-root")
        args = parser.parse_args(argv)
        if args.help:
            result = {"schema_version": INSPECTION_SCHEMA, "status": "help",
                      "usage": ("installed wrapper: scpolya-agent inspect --run-root PATH "
                                "[--repo-root PATH]; from a source checkout use "
                                "python -B -m scpolya_agent inspect (never -I without the wrapper)")}
        elif args.command != "inspect":
            raise ValueError("inspect command is required")
        else:
            # Third-party diagnostics must not corrupt the JSON output stream.
            with contextlib.redirect_stdout(sys.stderr):
                result = inspect_workspace(args.run_root, repo_root=args.repo_root)
        encoded = encode_inspection(result)
    except Exception as exc:
        result = {"schema_version": INSPECTION_SCHEMA, "status": "error",
                  "issues": [{"section": "cli", "code": "inspection_failed",
                              "detail": f"{type(exc).__name__}: {exc}"[:160]}]}
        encoded = encode_inspection(result)
    print(encoded)
    return 2 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
