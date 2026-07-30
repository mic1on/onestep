from __future__ import annotations

import base64
import json
import math
import sys
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

TAG = "$onestep"


class CaptureEncodingError(ValueError):
    def __init__(self, *, path: str, type_name: str, reason: str) -> None:
        self.path = path or "/"
        self.type_name = type_name
        self.reason = reason
        super().__init__(f"cannot encode {type_name} at {self.path}: {reason}")


def _type_name(value: Any) -> str:
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _pointer(path: str, token: str) -> str:
    escaped = token.replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}"


def _tag(type_name: str, **fields: Any) -> dict[str, Any]:
    return {TAG: {"type": type_name, **fields}}


def encode_value(value: Any, *, _path: str = "") -> Any:
    if isinstance(value, Enum):
        cls = type(value)
        return _tag(
            "enum",
            module=cls.__module__,
            qualname=cls.__qualname__,
            name=value.name,
            value=encode_value(value.value, _path=_pointer(_path, "value")),
        )
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CaptureEncodingError(
                path=_path,
                type_name="builtins.float",
                reason="non-finite float",
            )
        return value
    if isinstance(value, datetime):
        return _tag("datetime", value=value.isoformat(), fold=value.fold)
    if isinstance(value, UUID):
        return _tag("uuid", value=str(value))
    if isinstance(value, bytes):
        return _tag("bytes", value=base64.b64encode(value).decode("ascii"))
    if isinstance(value, Decimal):
        return _tag("decimal", value=str(value))
    if isinstance(value, tuple) and hasattr(type(value), "_fields"):
        cls = type(value)
        fields = tuple(getattr(cls, "_fields"))
        return _tag(
            "namedtuple",
            module=cls.__module__,
            qualname=cls.__qualname__,
            fields=list(fields),
            values=[
                encode_value(item, _path=_pointer(_path, field))
                for field, item in zip(fields, value)
            ],
        )
    if isinstance(value, tuple):
        return _tag(
            "tuple",
            values=[
                encode_value(item, _path=_pointer(_path, str(index)))
                for index, item in enumerate(value)
            ],
        )
    if isinstance(value, (set, frozenset)):
        encoded = [
            encode_value(item, _path=_pointer(_path, "set")) for item in value
        ]
        encoded.sort(
            key=lambda item: json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )
        return _tag(
            "frozenset" if isinstance(value, frozenset) else "set",
            values=encoded,
        )
    if isinstance(value, list):
        return [
            encode_value(item, _path=_pointer(_path, str(index)))
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CaptureEncodingError(
                    path=_path,
                    type_name=_type_name(key),
                    reason="mapping keys must be strings",
                )
        if TAG in value:
            return _tag(
                "mapping",
                items=[
                    [key, encode_value(item, _path=_pointer(_path, key))]
                    for key, item in value.items()
                ],
            )
        return {
            key: encode_value(item, _path=_pointer(_path, key))
            for key, item in value.items()
        }
    raise CaptureEncodingError(
        path=_path,
        type_name=_type_name(value),
        reason="unsupported value type",
    )


def _require_fields(
    payload: dict[str, Any],
    *,
    type_name: str,
    fields: set[str],
    path: str,
) -> None:
    expected = {"type", *fields}
    if set(payload) != expected:
        raise ValueError(f"invalid {type_name} tag fields at {path or '/'}")


def _resolve_loaded_type(module_name: Any, qualname: Any, *, path: str) -> type[Any]:
    if not isinstance(module_name, str) or not isinstance(qualname, str):
        raise ValueError(f"invalid recorded type at {path or '/'}")
    if "<locals>" in qualname.split("."):
        raise ValueError(f"local recorded type is not replayable at {path or '/'}")
    module = sys.modules.get(module_name)
    if module is None:
        raise ValueError(f"recorded module {module_name!r} is not loaded at {path or '/'}")
    resolved: Any = module
    for part in qualname.split("."):
        try:
            resolved = getattr(resolved, part)
        except AttributeError as exc:
            raise ValueError(
                f"recorded type {module_name}.{qualname} is unavailable at {path or '/'}"
            ) from exc
    if not isinstance(resolved, type):
        raise ValueError(f"recorded object is not a type at {path or '/'}")
    return resolved


def decode_value(value: Any, *, _path: str = "") -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float at {_path or '/'}")
        return value
    if isinstance(value, list):
        return [
            decode_value(item, _path=_pointer(_path, str(index)))
            for index, item in enumerate(value)
        ]
    if not isinstance(value, dict):
        raise ValueError(f"invalid encoded value at {_path or '/'}")
    if TAG not in value:
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"mapping keys must be strings at {_path or '/'}")
        return {
            key: decode_value(item, _path=_pointer(_path, key))
            for key, item in value.items()
        }
    if set(value) != {TAG} or not isinstance(value[TAG], dict):
        raise ValueError(f"invalid extension tag at {_path or '/'}")

    payload = value[TAG]
    type_name = payload.get("type")
    if not isinstance(type_name, str):
        raise ValueError(f"extension type must be a string at {_path or '/'}")
    if type_name == "datetime":
        _require_fields(payload, type_name=type_name, fields={"value", "fold"}, path=_path)
        if not isinstance(payload["value"], str) or payload["fold"] not in {0, 1}:
            raise ValueError(f"invalid datetime tag at {_path or '/'}")
        return datetime.fromisoformat(payload["value"]).replace(fold=payload["fold"])
    if type_name == "uuid":
        _require_fields(payload, type_name=type_name, fields={"value"}, path=_path)
        if not isinstance(payload["value"], str):
            raise ValueError(f"invalid uuid tag at {_path or '/'}")
        return UUID(payload["value"])
    if type_name == "bytes":
        _require_fields(payload, type_name=type_name, fields={"value"}, path=_path)
        if not isinstance(payload["value"], str):
            raise ValueError(f"invalid bytes tag at {_path or '/'}")
        try:
            return base64.b64decode(payload["value"], validate=True)
        except ValueError as exc:
            raise ValueError(f"invalid bytes tag at {_path or '/'}") from exc
    if type_name == "decimal":
        _require_fields(payload, type_name=type_name, fields={"value"}, path=_path)
        if not isinstance(payload["value"], str):
            raise ValueError(f"invalid decimal tag at {_path or '/'}")
        return Decimal(payload["value"])
    if type_name in {"tuple", "set", "frozenset"}:
        _require_fields(payload, type_name=type_name, fields={"values"}, path=_path)
        if not isinstance(payload["values"], list):
            raise ValueError(f"invalid {type_name} tag at {_path or '/'}")
        items = [
            decode_value(item, _path=_pointer(_path, str(index)))
            for index, item in enumerate(payload["values"])
        ]
        if type_name == "tuple":
            return tuple(items)
        try:
            return frozenset(items) if type_name == "frozenset" else set(items)
        except TypeError as exc:
            raise ValueError(f"unhashable {type_name} member at {_path or '/'}") from exc
    if type_name == "mapping":
        _require_fields(payload, type_name=type_name, fields={"items"}, path=_path)
        if not isinstance(payload["items"], list):
            raise ValueError(f"invalid mapping tag at {_path or '/'}")
        result: dict[str, Any] = {}
        for index, pair in enumerate(payload["items"]):
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or pair[0] in result
            ):
                raise ValueError(f"invalid mapping item at {_pointer(_path, str(index))}")
            result[pair[0]] = decode_value(pair[1], _path=_pointer(_path, pair[0]))
        return result
    if type_name == "enum":
        _require_fields(
            payload,
            type_name=type_name,
            fields={"module", "qualname", "name", "value"},
            path=_path,
        )
        enum_type = _resolve_loaded_type(payload["module"], payload["qualname"], path=_path)
        if not issubclass(enum_type, Enum) or not isinstance(payload["name"], str):
            raise ValueError(f"recorded type is not an enum at {_path or '/'}")
        try:
            member = enum_type[payload["name"]]
        except KeyError as exc:
            raise ValueError(f"enum member changed at {_path or '/'}") from exc
        recorded_value = decode_value(payload["value"], _path=_pointer(_path, "value"))
        if member.value != recorded_value:
            raise ValueError(f"enum value changed at {_path or '/'}")
        return member
    if type_name == "namedtuple":
        _require_fields(
            payload,
            type_name=type_name,
            fields={"module", "qualname", "fields", "values"},
            path=_path,
        )
        namedtuple_type = _resolve_loaded_type(
            payload["module"], payload["qualname"], path=_path
        )
        fields = payload["fields"]
        values = payload["values"]
        if (
            not isinstance(fields, list)
            or not all(isinstance(field, str) for field in fields)
            or not isinstance(values, list)
            or len(fields) != len(values)
            or tuple(getattr(namedtuple_type, "_fields", ())) != tuple(fields)
        ):
            raise ValueError(f"namedtuple definition changed at {_path or '/'}")
        decoded = [
            decode_value(item, _path=_pointer(_path, field))
            for field, item in zip(fields, values)
        ]
        return namedtuple_type(*decoded)
    raise ValueError(f"unknown extension type {type_name!r} at {_path or '/'}")


__all__ = ["CaptureEncodingError", "decode_value", "encode_value"]
