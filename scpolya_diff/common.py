"""Strict, lossless TSV and provenance helpers (Python standard library only)."""
import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_COUNT = 2**31 - 1


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def open_text(path):
    return gzip.open(path, "rt", newline="") if str(path).endswith(".gz") else open(path, newline="")


def table(path, required=()):
    with open_text(path) as f:
        reader = csv.DictReader(f, delimiter="\t")
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or any(not k for k in fields):
            raise ValueError(f"{path}: empty or duplicate header")
        if set(required) - set(fields):
            raise ValueError(f"{path}: missing columns {sorted(set(required) - set(fields))}")
        for n, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"{path}:{n}: row width differs from header")
            # Preserve exact identifiers; whitespace in them is invalid rather than rewritten.
            if any(v != v.strip() or any(c in v for c in '\r\n\x00') for v in row.values()):
                raise ValueError(f"{path}:{n}: whitespace/control characters in fields")
            yield row


def integer(value, label, maximum=MAX_COUNT):
    try:
        z = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{label}: not a nonnegative integer: {value}") from None
    if not z.is_finite() or z < 0 or z != z.to_integral_value() or z > maximum:
        raise ValueError(f"{label}: not a nonnegative integer <= {maximum}: {value}")
    return int(z)


def write_table(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(fields)
        w.writerows(rows)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def new_output(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise ValueError(f"Output directory must be empty: {path}")
    return path


def file_record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}
