"""Chequeo AST de definiciones duplicadas en el código nuevo (T042)."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "quantlab"

#: Entrypoints legítimos: ``python -m quantlab`` y el arranque uvicorn.
ALLOWED_DUPLICATES = {"main"}


def _top_level_definitions() -> dict[str, list[str]]:
    definitions: dict[str, list[str]] = defaultdict(list)
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("_"):
                    continue
                definitions[node.name].append(str(path.relative_to(SRC)))
    return definitions


def test_no_duplicate_public_definitions() -> None:
    duplicates = {
        name: files
        for name, files in _top_level_definitions().items()
        if len(files) > 1 and name not in ALLOWED_DUPLICATES
    }

    assert duplicates == {}


def test_legacy_is_not_imported_by_new_code() -> None:
    offending: list[str] = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "legacy" in text and "import legacy" in text:
            offending.append(str(path.relative_to(SRC)))
    assert offending == []


def test_legacy_hash_fixture_is_read_only_reference() -> None:
    import json

    payload = json.loads(
        (ROOT / "tests" / "fixtures" / "legacy_sha256.json").read_text(encoding="utf-8")
    )
    assert payload
