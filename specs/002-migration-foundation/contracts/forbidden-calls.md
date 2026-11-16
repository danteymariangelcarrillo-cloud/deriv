# Forbidden Calls — Migration Foundation

**Feature**: `002-migration-foundation` | **Fecha**: 2026-11-16

Lista de llamadas **PROHIBIDAS** en el código de producción. El test `tests/contract/test_forbidden_calls.py` escanea el código y falla si encuentra cualquiera de estos patrones.

---

## 1. Endpoints de Trading / Ejecución (Deriv API)

| Patrón Prohibido | Descripción | Excepción |
|------------------|-------------|-----------|
| `buy` | Compra de contratos | Ninguna |
| `sell` | Venta de contratos | Ninguna |
| `cancel` | Cancelación de órdenes | Ninguna |
| `proposal` | Solicitud de propuesta | Ninguna |
| `proposal_open_contract` | Propuesta contrato abierto | Ninguna |
| `buy_multi` | Compra múltiple | Ninguna |
| `sell_multi` | Venta múltiple | Ninguna |

---

## 2. Endpoints de Cuenta / Balance / Portfolio

| Patrón Prohibido | Descripción | Excepción |
|------------------|-------------|-----------|
| `balance` | Consulta de balance | Ninguna |
| `portfolio` | Consulta de portafolio | Ninguna |
| `statement` | Extracto de cuenta | Ninguna |
| `get_limits` | Límites de cuenta | Ninguna |
| `get_financial_assessment` | Evaluación financiera | Ninguna |
| `account_status` | Estado de cuenta | Ninguna |
| `verify_email` | Verificación email | Ninguna |
| `set_account_currency` | Cambiar moneda cuenta | Ninguna |
| `withdrawal` | Retiro de fondos | Ninguna |
| `deposit` | Depósito de fondos | Ninguna |
| `transfer_between_accounts` | Transferencia entre cuentas | Ninguna |

---

## 3. Autenticación / Tokens / Secretos

| Patrón Prohibido | Descripción | Excepción |
|------------------|-------------|-----------|
| `authorize` | Autorización con token | Solo en tests live marcados `@pytest.mark.live` |
| `oauth_token` | Token OAuth | Ninguna |
| `logout` | Cerrar sesión | Ninguna |
| `api_token` | Gestión de API tokens | Ninguna |
| `app_id` en código | App ID hardcodeado | Solo en `.env` / config |
| `app_id=` en URL | App ID en query string | Solo endpoint legacy fallback |

---

## 4. Persistencia en Disco (CERO DISCO)

| Patrón Prohibido | Descripción | Excepción |
|------------------|-------------|-----------|
| `to_csv(` | Escritura CSV | Solo en `ExportService` → `BytesIO` |
| `to_parquet(` | Escritura Parquet | Ninguna |
| `to_json(` con `open(` | Escritura JSON a archivo | Solo en `ExportService` → `BytesIO` |
| `open(..., "w")` | Apertura archivo escritura | Ninguna |
| `open(..., "a")` | Apertura archivo append | Ninguna |
| `pickle.dump(` | Serialización pickle | Ninguna |
| `joblib.dump(` | Serialización joblib | Ninguna |
| `sqlite3.connect(` | Base de datos SQLite | Ninguna |
| `psycopg2.connect(` | PostgreSQL | Ninguna |
| `redis.Redis(` | Redis | Ninguna |
| `mkdir(` / `makedirs(` | Crear directorios | Ninguna |
| `Path.write_` | Escritura pathlib | Ninguna |

**Nota:** `ExportService` usa `io.BytesIO` exclusivamente — **nunca toca disco**.

---

## 5. WebSocket Deriv — Solo Lectura Permitida

| Permitido (Allowlist) | Prohibido |
|----------------------|-----------|
| `active_symbols` | Cualquier otro `msg_type` no en allowlist |
| `ticks_history` (style=candles/ticks) | |
| `ticks` (subscribe=1) | |
| `time` | |
| `ping` | |

**Regla:** El código solo envía payloads con `msg_type` en allowlist. Cualquier otro `msg_type` en request → test falla.

---

## 6. Patrones de Código Prohibidos

| Patrón | Descripción |
|--------|-------------|
| `asyncio.sleep(0)` en bucle | Busy wait — usar `asyncio.Queue.get()` |
| `threading.Thread` sin `daemon=True` | Hilos que bloquean salida |
| `subprocess.run(` | Ejecución de comandos shell |
| `eval(` / `exec(` | Evaluación dinámica |
| `pickle.loads(` datos no confiables | Deserialización insegura |
| `random.random()` sin seed | No determinístico — usar `np.random.Generator(seed)` |
| `time.time()` para medir latencia WS | Usar `time.perf_counter()` |
| `print(` en código producción | Usar `structlog` |

---

## 7. Test de Verificación (`tests/contract/test_forbidden_calls.py`)

```python
import ast
import re
from pathlib import Path

FORBIDDEN_PATTERNS = [
    # Trading
    (r"\bbuy\b", "buy"),
    (r"\bsell\b", "sell"),
    (r"\bcancel\b", "cancel"),
    (r"\bproposal\b", "proposal"),
    # Cuenta
    (r"\bbalance\b", "balance"),
    (r"\bportfolio\b", "portfolio"),
    (r"\bstatement\b", "statement"),
    # Auth
    (r"\bauthorize\b", "authorize"),
    (r"\boauth_token\b", "oauth_token"),
    # Disco
    (r"\.to_csv\(", "to_csv"),
    (r"\.to_parquet\(", "to_parquet"),
    (r"open\([^)]*[\"']w[\"']", "open_write"),
    (r"open\([^)]*[\"']a[\"']", "open_append"),
    # WebSocket no allowlist
    (r"msg_type\s*[:=]\s*[\"'](?!active_symbols|ticks_history|ticks|time|ping)[\"']", "ws_msg_type_not_allowlisted"),
]

ALLOWLIST_FILES = [
    "tests/integration/test_live_deriv.py",  # Tests live marcados @pytest.mark.live
    "scripts/spike_deriv.py",                 # Spike técnico
]

def test_no_forbidden_calls():
    src_root = Path("src/quantlab")
    violations = []
    
    for py_file in src_root.rglob("*.py"):
        if str(py_file) in ALLOWLIST_FILES:
            continue
        content = py_file.read_text(encoding="utf-8")
        for pattern, name in FORBIDDEN_PATTERNS:
            matches = list(re.finditer(pattern, content))
            if matches:
                for m in matches:
                    line_no = content[:m.start()].count("\n") + 1
                    violations.append(f"{py_file}:{line_no}: {name} -> {m.group()[:80]}")
    
    assert not violations, f"Llamadas prohibidas encontradas:\n" + "\n".join(violations)
```

---

## 8. Allowlist Explícita (Deriv Calls)

Ver `data/deriv_calls.py` — única fuente de truth para payloads permitidos.

```python
# data/deriv_calls.py
ALLOWED_REQUESTS = {
    "active_symbols": {"active_symbols": "brief"},
    "ticks_history_candles": {"ticks_history": "{symbol}", "style": "candles", "granularity": "{int}", "count": "{int}", "start": "{int}", "end": "{int}"},
    "ticks_history_ticks": {"ticks_history": "{symbol}", "style": "ticks", "count": "{int}", "end": "latest"},
    "subscribe_ticks": {"ticks": "{symbol}", "subscribe": 1},
    "ping": {"ping": 1},
    "time": {"time": 1},
}
```

Cualquier request no construido desde `ALLOWED_REQUESTS` → test falla.