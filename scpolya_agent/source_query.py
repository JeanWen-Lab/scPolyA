"""Read exact JSON selectors under a task-specific registered grant."""
from __future__ import annotations

import re
import time

from .manifest import ManifestError, canonical, digest, need, parse

SCHEMA = "scpolya.source_query.v1"
GRANT_SCHEMA = "scpolya.source_grant.v1"


def tokens(pointer):
    need(isinstance(pointer, str) and len(pointer) <= 2048 and
         (pointer == "" or pointer.startswith("/")), "selector_invalid", "JSON pointer required")
    need(not re.search(r"~(?![01])", pointer), "selector_invalid", "invalid pointer escape")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")] if pointer else []


def resolve(value, pointer):
    for token in tokens(pointer):
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(value):
            value = value[int(token)]
        else:
            raise ManifestError("selector_absent", pointer)
    return value


def query(manifest, grant_id, source_id, pointer):
    request = dict(grant_id=grant_id, source_id=source_id, pointer=pointer)
    registration = manifest.doc.get("grants", {}).get(grant_id)
    need(registration is not None, "grant_unregistered", grant_id)
    grant_path, grant_raw = manifest.pinned(registration)
    grant = parse(grant_raw)
    need(isinstance(grant, dict) and set(grant) == {"schema", "task_id", "issued_by", "not_before", "expires", "sources", "max_response_bytes"}
         and grant["schema"] == GRANT_SCHEMA and isinstance(grant["task_id"], str) and grant["task_id"]
         and isinstance(grant["issued_by"], str) and grant["issued_by"], "grant_schema", GRANT_SCHEMA)
    now = time.time()
    need(type(grant["not_before"]) in (int, float) and type(grant["expires"]) in (int, float)
         and grant["not_before"] <= now < grant["expires"], "grant_expired_or_not_active", grant_id)
    need(type(grant["max_response_bytes"]) is int and 0 < grant["max_response_bytes"] <= 256 << 10,
         "grant_response_bound", grant_id)
    need(isinstance(grant["sources"], dict) and source_id in grant["sources"], "source_not_granted", source_id)
    access = grant["sources"][source_id]
    need(isinstance(access, dict) and set(access) == {"sha256", "pointer_prefixes"}, "grant_source_record", source_id)
    record = manifest.doc.get("sources", {}).get(source_id)
    need(record is not None and record["sha256"] == access["sha256"], "grant_source_pin", source_id)
    selected = tokens(pointer)
    prefixes = access["pointer_prefixes"]
    need(isinstance(prefixes, list) and prefixes, "grant_selector_scope", source_id)
    allowed = [tokens(prefix) for prefix in prefixes]
    need(any(selected[:len(prefix)] == prefix for prefix in allowed), "selector_not_granted", pointer)
    _, path, data = manifest.source(source_id)
    value = resolve(data, pointer)
    source_ref = dict(schema="scpolya.source_ref.v1", source_id=source_id,
                      path=str(path), sha256=record["sha256"], json_pointer=pointer,
                      value_sha256=digest(value))
    result = dict(schema=SCHEMA, status="observed", task_id=grant["task_id"], value=value,
                  value_state="NA" if value is None else "observed",
                  source_ref=source_ref, delivered_sources=[source_ref],
                  grant_ref=dict(path=str(grant_path), sha256=registration["sha256"]),
                  authority_scope="operator-registered hash grant; issuer label is not cryptographic authentication",
                  request_sha256=digest(request))
    result["envelope_sha256"] = digest(result)
    need(len(canonical(result)) + 1 <= grant["max_response_bytes"], "response_bound", "select a smaller pointer")
    return result
