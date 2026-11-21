"""Escaneo estático de llamadas prohibidas (T040/T043).

Se analiza ``src/quantlab`` (código de producción). ``legacy/`` es referencia
inmutable y no se escanea. Los falsos positivos documentados en
``contracts/forbidden-calls.md`` se evitan comparando nombres de payload
exactos (no ``asyncio.Task.cancel()``) y permitiendo ``to_csv`` sólo en
``ExportService``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from quantlab.data.deriv_calls import ALLOWED_REQUESTS

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "quantlab"

TRADING_ACCOUNT_STRINGS = {
    "buy",
    "sell",
    "cancel",
    "proposal",
    "proposal_open_contract",
    "buy_multi",
    "sell_multi",
    "balance",
    "portfolio",
    "statement",
    "get_limits",
    "get_financial_assessment",
    "account_status",
    "verify_email",
    "set_account_currency",
    "withdrawal",
    "deposit",
    "transfer_between_accounts",
    "authorize",
    "oauth_token",
    "logout",
    "api_token",
}

DISK_PATTERNS = [
    (re.compile(r"\bto_parquet\s*\("), "to_parquet"),
    (re.compile(r"\bto_pickle\s*\("), "to_pickle"),
    (re.compile(r"\bpickle\.dump\s*\("), "pickle.dump"),
    (re.compile(r"\bjoblib\.dump\s*\("), "joblib.dump"),
    (re.compile(r"\bsqlite3\.connect\s*\("), "sqlite3.connect"),
    (re.compile(r"\bos\.system\s*\("), "os.system"),
    (re.compile(r"\bsubprocess\.(run|call|Popen)\s*\("), "subprocess"),
    (re.compile(r"\.mkdir\s*\("), "mkdir"),
    (re.compile(r"\bmakedirs\s*\("), "makedirs"),
    (re.compile(r"\bPath\b[^\n]*\.write_(text|bytes)\s*\("), "Path.write_"),
    (re.compile(r"\bprint\s*\("), "print"),
    (re.compile(r"\beval\s*\("), "eval"),
    (re.compile(r"\bexec\s*\("), "exec"),
]

WRITE_OPEN = re.compile(r"\bopen\s*\([^\n]*,\s*[\"'](?:w|a|wb|ab|w\+|a\+)[\"']")


def _production_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_only_allowlisted_files_use_disk_writers() -> None:
    violations: list[str] = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(SRC).as_posix()
        for pattern, label in DISK_PATTERNS:
            if pattern.search(text):
                violations.append(f"{relative}: {label}")
        if WRITE_OPEN.search(text):
            violations.append(f"{relative}: open(..., 'w'/'a')")
        if re.search(r"\bto_csv\s*\(", text) and relative != "services/export_service.py":
            violations.append(f"{relative}: to_csv fuera de ExportService")
        if "threading.Thread(" in text and "daemon=True" not in text:
            violations.append(f"{relative}: threading.Thread sin daemon=True")
        if re.search(r"asyncio\.sleep\s*\(\s*0\s*\)", text):
            violations.append(f"{relative}: asyncio.sleep(0) en bucle")
    assert violations == []


def test_no_export_csv_definition_is_portado() -> None:
    matches = [
        str(path.relative_to(ROOT))
        for path in _production_files()
        if re.search(r"def\s+export_csv\s*\(", path.read_text(encoding="utf-8"))
    ]
    assert matches == []


def test_no_trading_or_account_msg_types_in_code() -> None:
    violations: list[str] = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.strip().lower() in TRADING_ACCOUNT_STRINGS
            ):
                violations.append(f"{path.relative_to(SRC)}:{node.lineno}: {node.value!r}")
    assert violations == []


def test_deriv_payloads_are_allowlisted() -> None:
    from quantlab.data.deriv_calls import build_request

    params: dict[str, dict[str, object]] = {
        "ticks_history_candles": {
            "ticks_history": "BOOM1000",
            "granularity": 300,
            "count": 10,
            "start": 0,
            "end": 1,
        },
        "ticks_history_ticks": {"ticks_history": "BOOM1000", "count": 10},
        "subscribe_ticks": {"ticks": "BOOM1000"},
    }
    allowed_types = {"active_symbols", "ticks_history", "ticks", "ping", "time"}

    for name in ALLOWED_REQUESTS:
        payload = build_request(name, **params.get(name, {}))
        declared = set(payload) & allowed_types
        assert declared, f"{name} no declara un msg_type permitido"
        assert declared <= allowed_types


def test_build_request_calls_use_known_template_names() -> None:
    violations: list[str] = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_request"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value not in ALLOWED_REQUESTS
            ):
                violations.append(f"{path.relative_to(SRC)}:{node.lineno}: {node.args[0].value}")
    assert violations == []


def test_export_service_uses_only_memory_buffers() -> None:
    text = (SRC / "services" / "export_service.py").read_text(encoding="utf-8")

    assert "BytesIO" in text
    assert "StringIO" in text
    assert "to_csv(" in text  # permitido: escribe en StringIO, no en disco
    assert "zipfile.ZipFile(buffer" in text
