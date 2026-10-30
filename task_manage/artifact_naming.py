from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Literal, Mapping, Tuple

from quantification_config import mode_scoped_quantification_payload


ARTIFACT_NAME_SCHEMA_V1 = "scpolya.artifact_name.v1"
ARTIFACT_NAME_MANIFEST_SCHEMA_V1 = "scpolya.artifact_name_manifest.v1"
ARTIFACT_NAME_MANIFEST_FILE_V1 = "artifact_name_manifest.v1.json"
ARTIFACT_NAMING_ENV = "SCPOLYA_ARTIFACT_NAMING"
ARTIFACT_NAME_HASH_ALGORITHM = "sha256"
ARTIFACT_NAME_SHORT_HEX_LENGTH = 16

ArtifactNameMode = Literal["auto", "short", "legacy"]
ArtifactNameScope = Literal["peak", "annotation", "matrix", "rna_gene"]

_SCOPE_PREFIXES: Mapping[ArtifactNameScope, str] = {
    "peak": "pk",
    "annotation": "an",
    "matrix": "mx",
    "rna_gene": "rn",
}
_ABSOLUTE_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/])")
_MANIFEST_LOCK = RLock()


class ArtifactNameError(ValueError):
    pass


class ArtifactNameCollisionError(ArtifactNameError):
    pass


class ArtifactNameManifestError(ArtifactNameError):
    pass


@dataclass(frozen=True)
class ScopedArtifactNameV1:
    scope: ArtifactNameScope
    code: str
    full_digest: str
    legacy_name: str
    payload: Mapping[str, Any]

    @property
    def canonical_payload_json(self) -> str:
        return canonical_artifact_name_json(self.scope, self.payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "code": self.code,
            "full_digest": self.full_digest,
            "legacy_name": self.legacy_name,
            "payload": _normalize_json_value(self.payload),
        }


@dataclass(frozen=True)
class ArtifactNameManifestV1:
    mode: Literal["short", "legacy"]
    records: Tuple[ScopedArtifactNameV1, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ARTIFACT_NAME_MANIFEST_SCHEMA_V1,
            "mode": self.mode,
            "hash_algorithm": ARTIFACT_NAME_HASH_ALGORITHM,
            "short_hex_length": ARTIFACT_NAME_SHORT_HEX_LENGTH,
            "records": [
                record.as_dict()
                for record in sorted(
                    self.records,
                    key=lambda record: (
                        record.scope,
                        record.code,
                        record.full_digest,
                    ),
                )
            ],
        }


def normalize_artifact_naming_mode(
    value: str | None,
    *,
    default: ArtifactNameMode = "auto",
) -> ArtifactNameMode:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    if normalized not in {"auto", "short", "legacy"}:
        raise ArtifactNameError(
            f"Unsupported artifact naming mode {value!r}; "
            "expected auto, short, or legacy"
        )
    return normalized  # type: ignore[return-value]


def configured_artifact_naming_mode() -> ArtifactNameMode:
    return normalize_artifact_naming_mode(os.environ.get(ARTIFACT_NAMING_ENV))


def canonical_artifact_name_json(
    scope: ArtifactNameScope,
    payload: Mapping[str, Any],
) -> str:
    if scope not in _SCOPE_PREFIXES:
        raise ArtifactNameError(f"Unsupported artifact name scope: {scope!r}")
    normalized = _normalize_json_value(payload)
    if not isinstance(normalized, Mapping):
        raise ArtifactNameError("Artifact name payload must be a mapping")
    _assert_no_absolute_paths(normalized)
    return json.dumps(
        {
            "schema_version": ARTIFACT_NAME_SCHEMA_V1,
            "scope": scope,
            "payload": normalized,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def build_scoped_artifact_name_v1(
    scope: ArtifactNameScope,
    payload: Mapping[str, Any],
    *,
    legacy_name: str,
) -> ScopedArtifactNameV1:
    normalized_legacy_name = str(legacy_name).strip()
    if not normalized_legacy_name:
        raise ArtifactNameError("Artifact legacy name cannot be blank")
    if "/" in normalized_legacy_name or "\\" in normalized_legacy_name:
        raise ArtifactNameError(
            "Artifact legacy name must be one path segment"
        )
    canonical = canonical_artifact_name_json(scope, payload)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    prefix = _SCOPE_PREFIXES[scope]
    code = f"{prefix}-{digest[:ARTIFACT_NAME_SHORT_HEX_LENGTH]}"
    return ScopedArtifactNameV1(
        scope=scope,
        code=code,
        full_digest=digest,
        legacy_name=normalized_legacy_name,
        payload=_normalize_json_value(payload),
    )


def peak_name_payload(config: Mapping[str, Any]) -> dict[str, Any]:
    payload = _select_required_keys(
        config,
        (
            "dedup_method",
            "polyA_status",
            "coverage_scaling",
            "depth_threshold",
            "depth_threshold_polyA",
            "merge_distance",
            "merge_distance_polyA",
            "misprime_A_count",
            "misprime_in",
            "misprimed_ratio_threshold",
        ),
    )
    payload.update(mode_scoped_quantification_payload(config))
    return payload


def annotation_name_payload(
    peak_payload: Mapping[str, Any],
    *,
    annotate_peak_source: Any,
    terminal_annotation_schema: Any = None,
) -> dict[str, Any]:
    payload = {
        "peak": _normalize_json_value(peak_payload),
        "annotate_peak_source": _normalize_json_value(
            annotate_peak_source
        ),
    }
    if terminal_annotation_schema not in (None, ""):
        payload["terminal_annotation_schema"] = _normalize_json_value(
            terminal_annotation_schema
        )
    return payload


def matrix_name_payload(
    annotation_payload: Mapping[str, Any],
    *,
    psi_min_gene_umis: Any,
    quantification_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "annotation": _normalize_json_value(annotation_payload),
        "psi_min_gene_umis": _normalize_json_value(psi_min_gene_umis),
    }
    if quantification_config:
        payload["quantification"] = _normalize_json_value(
            quantification_config
        )
    return payload


def rna_gene_name_payload(config: Mapping[str, Any]) -> dict[str, Any]:
    return _select_required_keys(
        config,
        (
            "dedup_method",
            "cb_tag",
            "umi_tag",
        ),
    )


def validate_artifact_name_records(
    records: Iterable[ScopedArtifactNameV1],
) -> Tuple[ScopedArtifactNameV1, ...]:
    ordered = tuple(
        sorted(
            records,
            key=lambda record: (
                record.scope,
                record.code,
                record.full_digest,
            ),
        )
    )
    by_code: dict[str, ScopedArtifactNameV1] = {}
    by_digest: dict[tuple[str, str], ScopedArtifactNameV1] = {}
    by_legacy: dict[tuple[str, str], ScopedArtifactNameV1] = {}
    validated: list[ScopedArtifactNameV1] = []
    for record in ordered:
        expected = build_scoped_artifact_name_v1(
            record.scope,
            record.payload,
            legacy_name=record.legacy_name,
        )
        if (
            record.code != expected.code
            or record.full_digest != expected.full_digest
        ):
            raise ArtifactNameManifestError(
                f"Artifact name record does not match its payload: {record.code}"
            )
        code_previous = by_code.get(record.code)
        if code_previous is not None:
            if _same_artifact_name_record(code_previous, record):
                continue
            raise ArtifactNameCollisionError(
                f"Short artifact code collision: {record.code}"
            )
        digest_key = (record.scope, record.full_digest)
        digest_previous = by_digest.get(digest_key)
        if digest_previous is not None and (
            digest_previous.code != record.code
            or digest_previous.legacy_name != record.legacy_name
            or dict(digest_previous.payload) != dict(record.payload)
        ):
            raise ArtifactNameManifestError(
                "One artifact payload maps to inconsistent names: "
                f"{record.scope}:{record.full_digest}"
            )
        legacy_key = (record.scope, record.legacy_name)
        legacy_previous = by_legacy.get(legacy_key)
        if legacy_previous is not None and (
            legacy_previous.full_digest != record.full_digest
            or dict(legacy_previous.payload) != dict(record.payload)
        ):
            raise ArtifactNameCollisionError(
                "Legacy artifact name maps to multiple payloads: "
                f"{record.scope}:{record.legacy_name}"
            )
        by_code[record.code] = record
        by_digest[digest_key] = record
        by_legacy[legacy_key] = record
        validated.append(record)
    return tuple(validated)


def load_artifact_name_manifest_v1(
    path: str | Path,
) -> ArtifactNameManifestV1 | None:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactNameManifestError(
            f"Could not read artifact name manifest {manifest_path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ArtifactNameManifestError(
            "Artifact name manifest root must be an object"
        )
    if payload.get("schema_version") != ARTIFACT_NAME_MANIFEST_SCHEMA_V1:
        raise ArtifactNameManifestError(
            "Unsupported artifact name manifest schema: "
            f"{payload.get('schema_version')!r}"
        )
    if payload.get("hash_algorithm") != ARTIFACT_NAME_HASH_ALGORITHM:
        raise ArtifactNameManifestError(
            "Unsupported artifact name manifest hash algorithm"
        )
    if payload.get("short_hex_length") != ARTIFACT_NAME_SHORT_HEX_LENGTH:
        raise ArtifactNameManifestError(
            "Artifact name manifest short digest length changed"
        )
    mode = payload.get("mode")
    if mode not in {"short", "legacy"}:
        raise ArtifactNameManifestError(
            f"Invalid artifact name manifest mode: {mode!r}"
        )
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ArtifactNameManifestError(
            "Artifact name manifest records must be a list"
        )
    records = tuple(_record_from_dict(record) for record in raw_records)
    return ArtifactNameManifestV1(
        mode=mode,
        records=validate_artifact_name_records(records),
    )


def register_artifact_name_v1(
    manifest_path: str | Path,
    *,
    mode: Literal["short", "legacy"],
    record: ScopedArtifactNameV1,
) -> ArtifactNameManifestV1:
    if mode not in {"short", "legacy"}:
        raise ArtifactNameManifestError(
            f"Invalid artifact name manifest mode: {mode!r}"
        )
    path = Path(manifest_path)
    with _MANIFEST_LOCK:
        current = load_artifact_name_manifest_v1(path)
        if current is not None and current.mode != mode:
            raise ArtifactNameManifestError(
                "Artifact naming mode cannot change within one run: "
                f"{current.mode!r} -> {mode!r}"
            )
        records = validate_artifact_name_records(
            (*(current.records if current is not None else ()), record)
        )
        if current is not None and records == current.records:
            return current
        manifest = ArtifactNameManifestV1(mode=mode, records=records)
        _write_manifest_atomic(path, manifest)
        return manifest


def _same_artifact_name_record(
    left: ScopedArtifactNameV1,
    right: ScopedArtifactNameV1,
) -> bool:
    return (
        left.scope == right.scope
        and left.code == right.code
        and left.full_digest == right.full_digest
        and left.legacy_name == right.legacy_name
        and dict(left.payload) == dict(right.payload)
    )


def _record_from_dict(value: Any) -> ScopedArtifactNameV1:
    if not isinstance(value, Mapping):
        raise ArtifactNameManifestError(
            "Artifact name manifest record must be an object"
        )
    scope = value.get("scope")
    if scope not in _SCOPE_PREFIXES:
        raise ArtifactNameManifestError(
            f"Invalid artifact name record scope: {scope!r}"
        )
    code = value.get("code")
    full_digest = value.get("full_digest")
    legacy_name = value.get("legacy_name")
    payload = value.get("payload")
    if not isinstance(code, str) or not code:
        raise ArtifactNameManifestError("Artifact name record lacks a code")
    if (
        not isinstance(full_digest, str)
        or len(full_digest) != 64
        or any(char not in "0123456789abcdef" for char in full_digest)
    ):
        raise ArtifactNameManifestError(
            f"Invalid artifact name record digest for {code!r}"
        )
    if not isinstance(legacy_name, str) or not legacy_name:
        raise ArtifactNameManifestError(
            f"Invalid artifact legacy name for {code!r}"
        )
    if not isinstance(payload, Mapping):
        raise ArtifactNameManifestError(
            f"Invalid artifact payload for {code!r}"
        )
    return ScopedArtifactNameV1(
        scope=scope,
        code=code,
        full_digest=full_digest,
        legacy_name=legacy_name,
        payload=_normalize_json_value(payload),
    )


def _write_manifest_atomic(
    path: Path,
    manifest: ArtifactNameManifestV1,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(
            manifest.as_dict(),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="ascii",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _select_required_keys(
    value: Mapping[str, Any],
    keys: Iterable[str],
) -> dict[str, Any]:
    missing = tuple(key for key in keys if key not in value)
    if missing:
        raise ArtifactNameError(
            "Artifact name payload is missing required keys: "
            + ",".join(missing)
        )
    return {
        key: _normalize_json_value(value[key])
        for key in keys
    }


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_json_value(item)
            for key, item in sorted(
                value.items(),
                key=lambda item: str(item[0]),
            )
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_normalize_json_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ),
        )
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "item") and callable(value.item):
        return _normalize_json_value(value.item())
    return str(value)


def _assert_no_absolute_paths(value: Any) -> None:
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_no_absolute_paths(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_no_absolute_paths(item)
        return
    if isinstance(value, str) and _ABSOLUTE_PATH_RE.match(value):
        raise ArtifactNameError(
            "Artifact name manifest payload cannot contain absolute paths"
        )
