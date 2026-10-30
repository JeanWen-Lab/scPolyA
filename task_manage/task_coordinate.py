from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl

from .cache_epoch import require_current_cache_epoch
from .task_variants import encode_params


TASK_COORDINATE_SCHEMA_V1 = "scpolya.task_coordinate.v1"
TASK_COORDINATE_HASH_ALGORITHM = "sha256"


class TaskCoordinateError(ValueError):
    pass


class TaskCoordinateDecodeError(TaskCoordinateError):
    pass


@dataclass(frozen=True)
class TaskCoordinateFieldV1:
    key: str
    legacy_value: str

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "legacy_value": self.legacy_value,
        }


@dataclass(frozen=True)
class TaskCoordinateV1:
    stage: str
    task_base: str
    identity: tuple[TaskCoordinateFieldV1, ...]
    params: tuple[TaskCoordinateFieldV1, ...]
    schema_version: str = TASK_COORDINATE_SCHEMA_V1

    def __post_init__(self) -> None:
        if self.schema_version != TASK_COORDINATE_SCHEMA_V1:
            raise TaskCoordinateError(
                f"Unsupported task coordinate schema: {self.schema_version!r}"
            )
        if not self.stage or not self.task_base:
            raise TaskCoordinateError(
                "Task coordinate requires non-empty stage and task_base"
            )
        _validate_canonical_fields(self.identity, label="identity")
        _validate_canonical_fields(self.params, label="params")

    @property
    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "task_base": self.task_base,
            "identity": [field.as_dict() for field in self.identity],
            "params": [field.as_dict() for field in self.params],
        }

    @property
    def canonical_json(self) -> str:
        return canonical_task_coordinate_json(self.payload)

    @property
    def task_coordinate_id(self) -> str:
        return hashlib.sha256(
            self.canonical_json.encode("utf-8")
        ).hexdigest()

    @property
    def identity_hash(self) -> str:
        return _coordinate_component_hash(
            self,
            component="identity",
            fields=self.identity,
        )

    @property
    def params_hash(self) -> str:
        return _coordinate_component_hash(
            self,
            component="params",
            fields=self.params,
        )

    @property
    def legacy_variant_str(self) -> str:
        return encode_params(
            {field.key: field.legacy_value for field in self.params}
        )

    @property
    def identity_dict(self) -> dict[str, str]:
        return {
            field.key: field.legacy_value
            for field in self.identity
        }

    @property
    def params_dict(self) -> dict[str, str]:
        return {
            field.key: field.legacy_value
            for field in self.params
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.payload,
            "task_coordinate_id": self.task_coordinate_id,
            "identity_hash": self.identity_hash,
            "params_hash": self.params_hash,
            "legacy_variant_str": self.legacy_variant_str,
        }


def canonical_task_coordinate_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def decode_task_coordinate_json_v1(
    coordinate_json: str,
) -> TaskCoordinateV1:
    if not isinstance(coordinate_json, str):
        raise TaskCoordinateDecodeError(
            "Task coordinate JSON must be text"
        )
    try:
        payload = json.loads(
            coordinate_json,
            object_pairs_hook=_strict_json_object,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskCoordinateDecodeError(
            "Task coordinate JSON is malformed"
        ) from exc
    if not isinstance(payload, dict):
        raise TaskCoordinateDecodeError(
            "Task coordinate JSON must contain an object"
        )
    expected_keys = {
        "schema_version",
        "stage",
        "task_base",
        "identity",
        "params",
    }
    if set(payload) != expected_keys:
        raise TaskCoordinateDecodeError(
            "Task coordinate JSON fields differ from schema v1"
        )
    if payload["schema_version"] != TASK_COORDINATE_SCHEMA_V1:
        raise TaskCoordinateDecodeError(
            "Task coordinate JSON has an unsupported schema"
        )
    if (
        not isinstance(payload["stage"], str)
        or not isinstance(payload["task_base"], str)
    ):
        raise TaskCoordinateDecodeError(
            "Task coordinate stage and task_base must be text"
        )
    try:
        coordinate = TaskCoordinateV1(
            stage=payload["stage"],
            task_base=payload["task_base"],
            identity=_decode_coordinate_fields_v1(
                payload["identity"],
                label="identity",
            ),
            params=_decode_coordinate_fields_v1(
                payload["params"],
                label="params",
            ),
        )
    except TaskCoordinateError as exc:
        raise TaskCoordinateDecodeError(str(exc)) from exc
    if coordinate.canonical_json != coordinate_json:
        raise TaskCoordinateDecodeError(
            "Task coordinate JSON is not in canonical encoded form"
        )
    return coordinate


def build_task_coordinate_v1(
    *,
    stage: str,
    task_base: str,
    identity: Mapping[str, Any] | Iterable[tuple[str, Any]] = (),
    params: Mapping[str, Any] | Iterable[tuple[str, Any]] = (),
) -> TaskCoordinateV1:
    return TaskCoordinateV1(
        stage=str(stage),
        task_base=str(task_base),
        identity=_canonical_fields(
            identity,
            none_as_empty=True,
            label="identity",
        ),
        params=_canonical_fields(
            params,
            none_as_empty=False,
            label="params",
        ),
    )


def build_task_coordinate_v1_from_contract(
    contract: Any,
) -> TaskCoordinateV1:
    """Coordinate of a *current* contract; fails closed without the current epoch.

    Historical coordinates are rebuilt with ``build_task_coordinate_v1`` /
    ``decode_task_coordinate_json_v1`` / legacy-DONE migration, never here.
    """
    require_current_cache_epoch(
        contract.task_params,
        label=f"Current task coordinate {contract.stage}:{contract.task_base}",
    )
    return build_task_coordinate_v1(
        stage=contract.stage,
        task_base=contract.task_base,
        identity=(
            (field.key, field.value)
            for field in contract.identity
        ),
        params=(
            (field.key, field.value)
            for field in contract.task_params
        ),
    )


def decode_legacy_variant_str_v1(
    variant_str: str | None,
) -> tuple[TaskCoordinateFieldV1, ...]:
    raw = str(variant_str or "")
    _validate_percent_escapes(raw)
    try:
        pairs = parse_qsl(
            raw,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError as exc:
        raise TaskCoordinateDecodeError(
            f"Malformed legacy variant string: {raw!r}"
        ) from exc
    try:
        fields = _canonical_fields(
            pairs,
            none_as_empty=False,
            label="legacy variant parameters",
        )
    except TaskCoordinateError as exc:
        raise TaskCoordinateDecodeError(str(exc)) from exc
    round_trip = encode_params(
        {field.key: field.legacy_value for field in fields}
    )
    if round_trip != raw:
        raise TaskCoordinateDecodeError(
            "Legacy variant string is not in canonical encoded form: "
            f"{raw!r} != {round_trip!r}"
        )
    return fields


def build_task_coordinate_v1_from_legacy_done(
    *,
    stage: str,
    task_base: str,
    variant_str: str | None,
    identity_keys: Iterable[Any],
    identity_values: Iterable[Any],
) -> TaskCoordinateV1:
    keys = tuple(identity_keys)
    values = tuple(identity_values)
    if len(keys) != len(values):
        raise TaskCoordinateDecodeError(
            "Legacy identity key/value slot counts differ"
        )
    identity: list[tuple[str, Any]] = []
    saw_empty_slot = False
    for index, (raw_key, raw_value) in enumerate(zip(keys, values), start=1):
        key = "" if raw_key is None else str(raw_key)
        value = "" if raw_value is None else str(raw_value)
        if not key:
            if value:
                raise TaskCoordinateDecodeError(
                    "Legacy identity value has no key at slot "
                    f"{index}: {value!r}"
                )
            saw_empty_slot = True
            continue
        if saw_empty_slot:
            raise TaskCoordinateDecodeError(
                "Legacy identity keys are not contiguous at slot "
                f"{index}: {key!r}"
            )
        identity.append((key, value))

    params = decode_legacy_variant_str_v1(variant_str)
    return build_task_coordinate_v1(
        stage=stage,
        task_base=task_base,
        identity=identity,
        params=(
            (field.key, field.legacy_value)
            for field in params
        ),
    )


def _canonical_fields(
    values: Mapping[str, Any] | Iterable[tuple[str, Any]],
    *,
    none_as_empty: bool,
    label: str,
) -> tuple[TaskCoordinateFieldV1, ...]:
    items = values.items() if isinstance(values, Mapping) else values
    fields: list[TaskCoordinateFieldV1] = []
    seen: set[str] = set()
    for raw_key, raw_value in items:
        key = str(raw_key)
        if not key:
            raise TaskCoordinateError(
                f"Task coordinate {label} contains an empty key"
            )
        if key in seen:
            raise TaskCoordinateError(
                f"Task coordinate {label} contains duplicate key {key!r}"
            )
        seen.add(key)
        legacy_value = (
            ""
            if none_as_empty and raw_value is None
            else str(raw_value)
        )
        fields.append(
            TaskCoordinateFieldV1(
                key=key,
                legacy_value=legacy_value,
            )
        )
    return tuple(sorted(fields, key=lambda field: field.key))


def _validate_canonical_fields(
    fields: tuple[TaskCoordinateFieldV1, ...],
    *,
    label: str,
) -> None:
    keys = tuple(field.key for field in fields)
    if not all(keys):
        raise TaskCoordinateError(
            f"Task coordinate {label} contains an empty key"
        )
    if len(keys) != len(set(keys)):
        raise TaskCoordinateError(
            f"Task coordinate {label} contains duplicate keys"
        )
    if keys != tuple(sorted(keys)):
        raise TaskCoordinateError(
            f"Task coordinate {label} fields are not canonical"
        )


def _coordinate_component_hash(
    coordinate: TaskCoordinateV1,
    *,
    component: str,
    fields: tuple[TaskCoordinateFieldV1, ...],
) -> str:
    payload = {
        "schema_version": coordinate.schema_version,
        "stage": coordinate.stage,
        "task_base": coordinate.task_base,
        "component": component,
        "fields": [field.as_dict() for field in fields],
    }
    return hashlib.sha256(
        canonical_task_coordinate_json(payload).encode("utf-8")
    ).hexdigest()


def _decode_coordinate_fields_v1(
    value: Any,
    *,
    label: str,
) -> tuple[TaskCoordinateFieldV1, ...]:
    if not isinstance(value, list):
        raise TaskCoordinateDecodeError(
            f"Task coordinate {label} must be an array"
        )
    fields: list[TaskCoordinateFieldV1] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"key", "legacy_value"}
            or not isinstance(item["key"], str)
            or not isinstance(item["legacy_value"], str)
        ):
            raise TaskCoordinateDecodeError(
                f"Task coordinate {label} field is malformed"
            )
        fields.append(
            TaskCoordinateFieldV1(
                key=item["key"],
                legacy_value=item["legacy_value"],
            )
        )
    return tuple(fields)


def _strict_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TaskCoordinateDecodeError(
                f"Task coordinate JSON contains duplicate key {key!r}"
            )
        result[key] = value
    return result


def _validate_percent_escapes(value: str) -> None:
    index = 0
    while index < len(value):
        if value[index] != "%":
            index += 1
            continue
        if (
            index + 2 >= len(value)
            or value[index + 1] not in "0123456789abcdefABCDEF"
            or value[index + 2] not in "0123456789abcdefABCDEF"
        ):
            raise TaskCoordinateDecodeError(
                "Legacy variant string contains an invalid percent escape: "
                f"{value!r}"
            )
        index += 3
