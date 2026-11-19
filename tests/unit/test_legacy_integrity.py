"""Verificación de integridad de ``legacy/`` (T042).

Cada artefacto histórico está fijado por SHA-256 en
``tests/fixtures/legacy_sha256.json``. ``legacy/`` es la referencia byte a byte
del prototipo original: cualquier edición rompe la paridad numérica que la
migración debe reproducir y este test falla.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "legacy_sha256.json"
LEGACY_DIR = PROJECT_ROOT / "legacy"


def _expected_hashes() -> dict[str, str]:
    """Carga el fixture de hashes registrados para ``legacy/``."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("filename", sorted(_expected_hashes()))
def test_legacy_file_matches_recorded_sha256(filename: str) -> None:
    path = LEGACY_DIR / filename
    assert path.is_file(), f"falta el artefacto histórico {path}"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == _expected_hashes()[filename], (
        f"{filename} fue modificado; legacy/ es una referencia inmutable"
    )


def test_fixture_covers_every_legacy_artifact() -> None:
    """Un artefacto nuevo sin hash registrado también es una desviación."""
    recorded = set(_expected_hashes())
    present = {
        path.name for path in LEGACY_DIR.iterdir() if path.is_file() and path.name != "README.md"
    }
    assert present == recorded
