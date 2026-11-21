"""Valida snapshots contra ``contracts/snapshot.schema.json`` (T037).

El proyecto no declara ``jsonschema`` como dependencia, así que este script
incluye un validador del subconjunto de JSON Schema que usa el contrato:
``$ref`` locales, ``type``, ``anyOf``, ``enum``/``const``, ``required``,
``properties``, ``additionalProperties``, ``items``, límites numéricos, longitud
de cadenas/arrays y ``pattern``/``format: date-time``.

Uso::

    uv run python scripts/verify_contracts.py
    uv run python scripts/verify_contracts.py ruta/a/snapshot.json ...

Sale con código 0 si todo es válido y 1 si hay errores.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "specs" / "002-migration-foundation" / "contracts"
SCHEMA_PATH = CONTRACTS / "snapshot.schema.json"
EXAMPLE_PATH = CONTRACTS / "snapshot.example.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "snapshot_boom1000.json"

__all__ = [
    "CONTRACTS",
    "EXAMPLE_PATH",
    "FIXTURE_PATH",
    "SCHEMA_PATH",
    "load_json",
    "main",
    "validate",
    "verify_all",
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(root: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ValueError(f"$ref no local no soportado: {ref}")
    target: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        target = target[part]
    return target


def _is_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    raise ValueError(f"tipo JSON Schema no soportado: {expected}")


def validate(
    instance: Any, schema: Any, root: dict[str, Any] | None = None, path: str = "$"
) -> list[str]:
    """Devuelve la lista de errores (vacía si ``instance`` cumple ``schema``)."""
    if root is None:
        root = schema
    if schema is True or schema == {}:
        return []
    if schema is False:
        return [f"{path}: el schema prohíbe cualquier valor"]
    if not isinstance(schema, dict):
        raise TypeError(f"schema inválido en {path}")

    if "$ref" in schema:
        return validate(instance, _resolve(root or schema, schema["$ref"]), root, path)

    errors: list[str] = []

    if "anyOf" in schema:
        branch_errors = [validate(instance, option, root, path) for option in schema["anyOf"]]
        if any(not branch for branch in branch_errors):
            return []
        errors.append(f"{path}: no cumple ninguna alternativa de anyOf")
        errors.extend(branch_errors[0])

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: se esperaba {schema['const']!r}, recibido {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} no está en {schema['enum']}")

    expected = schema.get("type")
    if expected is not None:
        options = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(instance, option) for option in options):
            errors.append(f"{path}: tipo {type(instance).__name__} no es {expected}")
            return errors

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        _check_number(instance, schema, path, errors)
    elif isinstance(instance, str):
        _check_string(instance, schema, path, errors)
    elif isinstance(instance, list):
        _check_array(instance, schema, root, path, errors)
    elif isinstance(instance, dict):
        _check_object(instance, schema, root, path, errors)
    return errors


def _check_number(value: float, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: {value} < minimum {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        errors.append(f"{path}: {value} > maximum {schema['maximum']}")
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        errors.append(f"{path}: {value} <= exclusiveMinimum {schema['exclusiveMinimum']}")
    if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
        errors.append(f"{path}: {value} >= exclusiveMaximum {schema['exclusiveMaximum']}")


def _check_string(value: str, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(f"{path}: longitud {len(value)} < {schema['minLength']}")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        errors.append(f"{path}: longitud {len(value)} > {schema['maxLength']}")
    if "pattern" in schema and re.search(schema["pattern"], value) is None:
        errors.append(f"{path}: {value!r} no cumple el patrón {schema['pattern']!r}")
    if schema.get("format") == "date-time":
        try:
            datetime.fromisoformat(value)
        except ValueError:
            errors.append(f"{path}: {value!r} no es date-time ISO-8601")


def _check_array(
    value: list[Any],
    schema: dict[str, Any],
    root: dict[str, Any] | None,
    path: str,
    errors: list[str],
) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        errors.append(f"{path}: {len(value)} items < minItems {schema['minItems']}")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        errors.append(f"{path}: {len(value)} items > maxItems {schema['maxItems']}")
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for index, item in enumerate(value):
            errors.extend(validate(item, item_schema, root, f"{path}[{index}]"))


def _check_object(
    value: dict[str, Any],
    schema: dict[str, Any],
    root: dict[str, Any] | None,
    path: str,
    errors: list[str],
) -> None:
    for required in schema.get("required", []):
        if required not in value:
            errors.append(f"{path}: falta el campo requerido {required!r}")
    if "minProperties" in schema and len(value) < schema["minProperties"]:
        errors.append(f"{path}: {len(value)} propiedades < minProperties {schema['minProperties']}")

    properties = schema.get("properties", {})
    for key, item in value.items():
        if key in properties:
            errors.extend(validate(item, properties[key], root, f"{path}.{key}"))
        else:
            additional = schema.get("additionalProperties", True)
            if additional is False:
                errors.append(f"{path}: propiedad no permitida {key!r}")
            elif isinstance(additional, dict):
                errors.extend(validate(item, additional, root, f"{path}.{key}"))


def verify_all(extra_paths: list[Path] | None = None) -> list[str]:
    """Valida el ejemplo, la fixture y las rutas extra contra el schema."""
    schema = load_json(SCHEMA_PATH)
    errors: list[str] = []

    targets: list[Path] = [EXAMPLE_PATH]
    if FIXTURE_PATH.exists():
        targets.append(FIXTURE_PATH)
    targets.extend(extra_paths or [])

    for target in targets:
        if not target.exists():
            errors.append(f"{target}: no existe")
            continue
        for error in validate(load_json(target), schema):
            errors.append(f"{target.name}: {error}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    extra = [Path(arg) for arg in args]
    errors = verify_all(extra)
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1
    print("OK: snapshots válidos contra snapshot.schema.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
