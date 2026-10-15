"""Minimal JSON Schema (draft-07) validator.

Only the keywords used by the KiCad PCM and IPC plugin schemas are
implemented: type, required, properties, patternProperties,
additionalProperties, items, enum, pattern, minLength/maxLength,
minItems/maxItems, uniqueItems, $ref and $defs/definitions.

Implemented as a small self-contained module so validation works without any
third-party dependency, keeping the plugin installable through KiCad's
per-plugin virtual environment.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["ValidationError", "validate", "validate_or_raise", "load_schema"]


class ValidationError(Exception):
    """Raised when an instance does not satisfy a schema."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def load_schema(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _check_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    py_type = _TYPE_MAP.get(expected)
    if py_type is None:
        return True
    if py_type is dict or py_type is list or py_type is str:
        return isinstance(value, py_type)
    return isinstance(value, py_type)


def _resolve_ref(schema: dict, root: dict) -> dict:
    ref = schema.get("$ref")
    if not ref or not ref.startswith("#/"):
        return schema
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return {}
        node = node[part]
    return node if isinstance(node, dict) else {}


def _validate(value: Any, schema: dict, root: dict, path: str, errors: list[str]) -> None:
    if "$ref" in schema:
        merged = dict(_resolve_ref(schema, root))
        for key, val in schema.items():
            if key != "$ref":
                merged.setdefault(key, val)
        schema = merged

    if "enum" in schema:
        if value not in schema["enum"]:
            errors.append(f"{path}: Wert {value!r} nicht in erlaubter Liste {schema['enum']}")
        return

    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_check_type(value, t) for t in types):
            errors.append(f"{path}: erwartet Typ {expected}, gefunden {type(value).__name__}")
            return

    if isinstance(value, str):
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, value):
            errors.append(
                f"{path}: Wert {value!r} verletzt das Muster {pattern}"
            )
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: zu kurz (min {schema['minLength']})")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: zu lang (max {schema['maxLength']})")

    if isinstance(value, dict):
        _validate_object(value, schema, root, path, errors)

    if isinstance(value, list):
        _validate_array(value, schema, root, path, errors)


def _validate_object(value: dict, schema: dict, root: dict, path: str, errors: list[str]) -> None:
    for key in schema.get("required", []):
        if key not in value:
            errors.append(f"{path}: Pflichtfeld '{key}' fehlt")

    properties = schema.get("properties", {})
    pattern_properties = schema.get("patternProperties", {})
    additional = schema.get("additionalProperties", True)

    for key, item in value.items():
        child_path = f"{path}.{key}"
        handled = False
        if key in properties:
            _validate(item, properties[key], root, child_path, errors)
            handled = True
        for pattern, sub_schema in pattern_properties.items():
            if re.search(pattern, key):
                _validate(item, sub_schema, root, child_path, errors)
                handled = True
        if not handled and additional is False:
            errors.append(f"{path}: unbekanntes Feld '{key}'")
        elif not handled and isinstance(additional, dict):
            _validate(item, additional, root, child_path, errors)


def _validate_array(value: list, schema: dict, root: dict, path: str, errors: list[str]) -> None:
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            _validate(item, items, root, f"{path}[{index}]", errors)
    if "minItems" in schema and len(value) < schema["minItems"]:
        errors.append(f"{path}: zu wenige Elemente (min {schema['minItems']})")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        errors.append(f"{path}: zu viele Elemente (max {schema['maxItems']})")
    if schema.get("uniqueItems"):
        seen: list[Any] = []
        for item in value:
            if item in seen:
                errors.append(f"{path}: doppelter Eintrag {item!r}")
                break
            seen.append(item)


def validate(instance: Any, schema: dict) -> list[str]:
    """Return a list of human readable validation errors (empty means valid)."""
    errors: list[str] = []
    _validate(instance, schema, schema, "$", errors)
    return errors


def validate_or_raise(instance: Any, schema: dict, label: str = "") -> None:
    """Raise :class:`ValidationError` if *instance* violates *schema*."""
    errors = validate(instance, schema)
    if errors:
        prefix = f"{label}: " if label else ""
        raise ValidationError([f"{prefix}{e}" for e in errors])