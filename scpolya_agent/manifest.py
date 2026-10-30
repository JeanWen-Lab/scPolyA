"""Explicit operator configuration. Loading never creates data or issues authority."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

SCHEMA = "scpolya.operator_manifest.v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
MAX_METADATA = 1 << 20


class ManifestError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def need(value, code, detail):
    if not value:
        raise ManifestError(code, detail)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate_json_key", key)
            result[key] = value
        return result
    def finite_float(text):
        value = float(text)
        need(math.isfinite(value), "nonfinite_json", text[:80])
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_float=finite_float,
                      parse_constant=lambda v: (_ for _ in ()).throw(
                          ManifestError("nonfinite_json", v)))


def path_at(base, value):
    need(isinstance(value, str) and 0 < len(value) <= 4096 and "\0" not in value,
         "invalid_path", "bounded nonempty path required")
    path = Path(value)
    need(".." not in path.parts, "parent_path_refused", value)
    path = path if path.is_absolute() else base / path
    # Do not resolve symlinks before checking their ancestry.
    for part in [*reversed(path.parents), path]:
        need(not part.is_symlink(), "symlink_path_refused", str(part))
    return path


def read_regular(path, cap):
    path = path_at(Path.cwd(), str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_size <= cap,
             "file_bound", f"regular file <= {cap} bytes required: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(cap + 1)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        need(len(raw) <= cap and all(getattr(before, f) == getattr(after, f) ==
             getattr(path.lstat(), f) for f in fields), "source_changed", str(path))
        return raw
    finally:
        os.close(fd)


class Manifest:
    def __init__(self, path):
        self.filepath = path_at(Path.cwd(), str(path))
        raw = read_regular(self.filepath, MAX_METADATA)
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.doc = parse(raw)
        need(isinstance(self.doc, dict) and self.doc.get("schema") == SCHEMA,
             "manifest_schema", SCHEMA)
        need(set(self.doc) <= {"schema", "native", "sources", "grants", "saved", "control"},
             "unknown_manifest_field", "unknown top-level field")
        self.base = self.filepath.parent
        for section in ("native", "sources", "grants", "saved", "control"):
            need(isinstance(self.doc.get(section, {}), dict), "manifest_section", section)
        for key, record in self.doc.get("sources", {}).items():
            need(isinstance(record, dict) and set(record) == {"path", "sha256", "format", "max_bytes"},
                 "source_record", key)
            need(bool(HEX.fullmatch(str(record["sha256"]))) and record["format"] in ("json", "text")
                 and type(record["max_bytes"]) is int and 0 < record["max_bytes"] <= 16 << 20,
                 "source_record", key)
            self.path(record["path"])
        for key, record in self.doc.get("grants", {}).items():
            need(isinstance(record, dict) and set(record) == {"path", "sha256"}
                 and bool(HEX.fullmatch(str(record["sha256"]))), "grant_record", key)
            self.path(record["path"])

    def path(self, value):
        return path_at(self.base, value)

    def executable(self, value):
        # Python installations commonly use a final python -> python3.x link.
        # Preserve the operator's spelling for existing native identity contracts;
        # only this executable slot permits that final link (data paths do not).
        need(isinstance(value, str) and value and "\0" not in value,
             "invalid_executable", "Python path required")
        p = Path(value)
        need(".." not in p.parts, "parent_path_refused", value)
        p = p if p.is_absolute() else self.base / p
        path_at(self.base, str(p.parent))
        need(p.is_file() and os.access(p, os.X_OK), "native_python_missing", str(p))
        return p

    def pinned(self, record, cap=MAX_METADATA):
        path = self.path(record["path"])
        raw = read_regular(path, cap)
        need(hashlib.sha256(raw).hexdigest() == record["sha256"], "source_pin_mismatch", str(path))
        return path, raw

    def source(self, source_id):
        record = self.doc.get("sources", {}).get(source_id)
        need(record is not None, "source_unregistered", source_id)
        need(record["format"] == "json", "source_format", "source-query/citations require a JSON source")
        path, raw = self.pinned(record, record["max_bytes"])
        return record, path, parse(raw)


def load_manifest(path=None):
    selected = path or os.environ.get("SCPOLYA_MANIFEST")
    need(selected, "manifest_required", "pass --manifest PATH or set SCPOLYA_MANIFEST")
    return Manifest(selected)
