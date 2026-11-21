"""Contrato del verificador de snapshots (T037)."""

from __future__ import annotations

import copy

import pytest

from scripts.verify_contracts import (
    EXAMPLE_PATH,
    FIXTURE_PATH,
    SCHEMA_PATH,
    load_json,
    main,
    validate,
    verify_all,
)


def test_committed_snapshots_are_valid() -> None:
    assert verify_all() == []


def test_example_and_fixture_validate_against_schema() -> None:
    schema = load_json(SCHEMA_PATH)

    assert validate(load_json(EXAMPLE_PATH), schema) == []
    assert validate(load_json(FIXTURE_PATH), schema) == []


def test_validator_reports_missing_required_and_type_errors() -> None:
    schema = load_json(SCHEMA_PATH)
    payload = copy.deepcopy(load_json(FIXTURE_PATH))

    del payload["config_hash"]
    payload["cache_bytes"] = -1

    errors = validate(payload, schema)

    assert any("config_hash" in error for error in errors)
    assert any("cache_bytes" in error for error in errors)


def test_validator_honours_anyof_and_refs() -> None:
    schema = load_json(SCHEMA_PATH)
    payload = copy.deepcopy(load_json(FIXTURE_PATH))
    payload["timeframes"]["5min"]["evt"] = {"status": "ready"}

    errors = validate(payload, schema)

    assert any("evt" in error for error in errors)
    assert any("required" in error or "requerido" in error for error in errors)


def test_main_exit_codes() -> None:
    assert main([]) == 0

    assert main([str(EXAMPLE_PATH)]) == 0


def test_main_fails_with_missing_extra_file(tmp_path) -> None:
    assert main([str(tmp_path / "missing.json")]) == 1


@pytest.mark.parametrize("filename", ["snapshot.example.json", "snapshot_boom1000.json"])
def test_snapshots_use_full_sha256(filename: str) -> None:
    import re

    payload = load_json(EXAMPLE_PATH if filename == "snapshot.example.json" else FIXTURE_PATH)

    assert re.fullmatch(r"sha256:[a-f0-9]{64}", payload["config_hash"])
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", payload["data_version"])
