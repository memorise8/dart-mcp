from __future__ import annotations

from collections.abc import Mapping

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | dict[str, JsonValue] | list[JsonValue]
type JsonObject = dict[str, object]
type JsonArray = list[JsonValue]
type DartRecord = dict[str, str]
type ObjectRecord = dict[str, object]
type QueryParams = dict[str, str]


def records_from(value: object) -> list[DartRecord]:
    if isinstance(value, Mapping):
        return [_string_record(value)]
    if isinstance(value, list):
        return [_string_record(item) for item in value if isinstance(item, Mapping)]
    return []


def object_records_from(value: object) -> list[ObjectRecord]:
    if isinstance(value, Mapping):
        return [_object_record(value)]
    if isinstance(value, list):
        return [_object_record(item) for item in value if isinstance(item, Mapping)]
    return []


def _string_record(value: Mapping[object, object]) -> DartRecord:
    record: DartRecord = {}
    for key, raw_value in value.items():
        if isinstance(key, str):
            record[key] = "" if raw_value is None else str(raw_value)
    return record


def _object_record(value: Mapping[object, object]) -> ObjectRecord:
    record: ObjectRecord = {}
    for key, raw_value in value.items():
        if isinstance(key, str):
            record[key] = raw_value
    return record
