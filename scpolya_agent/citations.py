"""Validate a source citation against an operator-registered delivered response."""
from __future__ import annotations

import hashlib

from .manifest import canonical, digest, need, parse
from .source_query import resolve, tokens


def verify_delivery(response, source_id, source_sha256, pointer, value_sha256):
    """The caller supplies a trusted response, never actor-provided response metadata.

    The returned subtree itself proves delivery. A full source read grant does
    not prove that any sibling was returned by this particular response.
    """
    need(isinstance(response, dict) and response.get("schema") == "scpolya.source_query.v1"
         and response.get("status") == "observed", "delivery_schema", "observed source-query response required")
    need(response.get("envelope_sha256") == digest({k: v for k, v in response.items() if k != "envelope_sha256"}),
         "delivery_envelope_hash", "delivered envelope changed")
    ref = response.get("source_ref")
    need(isinstance(ref, dict) and response.get("delivered_sources") == [ref]
         and ref.get("schema") == "scpolya.source_ref.v1", "delivery_reference", "exact delivered reference required")
    need(ref.get("source_id") == source_id and ref.get("sha256") == source_sha256,
         "citation_source_mismatch", source_id)
    need(ref.get("value_sha256") == digest(response.get("value")), "delivery_value_hash", "delivered value changed")
    origin = tokens(ref.get("json_pointer"))
    selected = tokens(pointer)
    need(selected[:len(origin)] == origin, "citation_not_delivered", pointer)
    remainder = selected[len(origin):]
    relative = "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in remainder)
    value = resolve(response["value"], relative)
    need(digest(value) == value_sha256, "citation_value_hash", "value or JSON type differs")
    return value


def check(manifest, delivery_id, source_id, pointer, value_sha256):
    deliveries = manifest.doc.get("control", {}).get("deliveries", {})
    need(isinstance(deliveries, dict) and delivery_id in deliveries, "delivery_unregistered", delivery_id)
    registration = deliveries[delivery_id]
    need(isinstance(registration, dict) and set(registration) == {"path", "sha256"}, "delivery_record", delivery_id)
    delivery_path, raw = manifest.pinned(registration, 256 << 10)
    response = parse(raw)
    record, source_path, original = manifest.source(source_id)
    value = verify_delivery(response, source_id, record["sha256"], pointer, value_sha256)
    need(canonical(resolve(original, pointer)) == canonical(value), "citation_source_value", "source/delivery mismatch")
    return dict(schema="scpolya.citation_check.v1", status="verified", value=value,
                source_ref=dict(schema="scpolya.source_ref.v1", source_id=source_id,
                                path=str(source_path), sha256=record["sha256"],
                                json_pointer=pointer, value_sha256=value_sha256),
                delivery_ref=dict(path=str(delivery_path), sha256=registration["sha256"]),
                scope="Exact original-source selector and delivered-field coverage; scientific interpretation not evaluated")
