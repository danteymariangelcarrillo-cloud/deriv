# Contracts: Forbidden Calls and Read-Only Guardrails

**Feature**: `001-crash-boom-dashboard` | **Fecha**: 2026-11-16

La aplicación es de **solo lectura**. Este contrato define la allowlist de
llamadas a Deriv, la denylist de endpoints prohibidos y las pruebas que impiden
que una llamada de trading, cuenta o ejecución entre al código de producción.

## 1. Principio

> El código de producción solo puede enviar los `msg_type` de la allowlist. No
> se aceptan tokens, credenciales, endpoints de cuenta ni de ejecución. Un
> análisis puede ser incorrecto y corregirse; una llamada de trading no debe
> existir nunca.

## 2. Allowlist de Deriv

| `msg_type` | Uso | Datos | Frecuencia |
|---|---|---|---|
| `active_symbols` | Resolver símbolos activos | Público | Al inicio y al reconectar |
| `ticks_history` | Velas y ticks históricos | Público | Paginación de historia |
| `ticks` | Suscripción de ticks en vivo | Público | 1 por símbolo |
| `forget` | Cancelar una suscripción | Público | Al cerrar |
| `forget_all` | Cancelar todas las suscripciones | Público | Al cerrar/reconectar |
| `ping` | Heartbeat | Público | Cada 30 s |
| `time` | Hora del servidor (opcional) | Público | Diagnóstico |

Ninguna de estas llamadas requiere token. `app_id=1089` es un identificador
público de demo, no un secreto.

### 2.1 Payload permitido de `ticks_history`

```json
{
  "ticks_history": "BOOM1000",
  "style": "candles",
  "granularity": 300,
  "start": 1752710400,
  "end": 1763208000,
  "count": 5000,
  "req_id": 42
}
```

```json
{
  "ticks_history": "BOOM1000",
  "style": "ticks",
  "count": 5000,
  "req_id": 43
}
```

- `style` solo puede ser `candles` o `ticks`.
- `granularity` debe ser una de `60, 300, 900, 3600, 14400, 86400`.
- `count` ≤ 5000.
- No se permite `subscribe` en `ticks_history`; el stream usa `ticks`.

### 2.2 Payload permitido de `ticks`

```json
{
  "ticks": "BOOM1000",
  "subscribe": 1,
  "req_id": 44
}
```

- Solo el símbolo del registro de instrumentos.
- No se permite `ticks_history` con `subscribe: 1`.

## 3. Denylist

Las siguientes cadenas no pueden aparecer como `msg_type`, nombre de método,
clave de payload ni llamada en `src/quantlab/` (excepto en tests y
documentación):

```text
authorize
buy
sell
cancel
proposal
proposal_open_contract
portfolio
balance
statement
profit_table
transaction
transfer_between_accounts
new_account_real
new_account_virtual
set_account_currency
get_account_status
logout
copy_start
copy_stop
copytrading
mt5
payout_currencies
```

También se prohíben:

- `api_token`, `access_token`, `bearer`, `DERIV_TOKEN`, `Authorization`.
- Cualquier header HTTP de autenticación.
- Cualquier endpoint REST de trading.
- Cualquier escritura a disco en runtime (CSV, caché, base de datos, exportación).
- Cualquier ruta FastAPI de trading, cuenta o propuesta.

### 3.1 Falsos positivos conocidos

| Cadena | Dónde aparece | Tratamiento |
|---|---|---|
| `token` | Rate limiter ("tokens", "token bucket") | No se escanea genéricamente; solo `api_token`, `access_token`, `bearer` |
| `balance` | Texto de UI "balance de probabilidad" | Evitar la palabra en UI; usar "equilibrio" o "comparación" |
| `statement` | SQL inexistente; no se usa | Prohibido igualmente |
| `buy`/`sell` | No deberían aparecer ni en comentarios de producción | Prohibido; usar "compra/venta" solo en docs de alcance |

## 4. Pruebas obligatorias

Ubicación: `tests/contract/test_forbidden_calls.py`.

### 4.1 `test_no_forbidden_tokens`

- Recorre `src/quantlab/**/*.py`.
- Ignora comentarios y docstrings para reducir falsos positivos, pero **no**
  ignora strings de código.
- Tokeniza identificadores y strings; compara contra la denylist por token
  exacto (`buy`, `sell`, etc.) y por subcadena para `authorize`, `api_token`,
  `access_token`, `bearer`.
- Falla con archivo, línea y token encontrado.

### 4.2 `test_allowed_deriv_calls_only`

- Parsea el AST de `src/quantlab/data/deriv_client.py`.
- Encuentra cada asignación/valor de `msg_type` y cada llamada a
  `client.request(...)`.
- Verifica que el `msg_type` esté en la allowlist.
- Verifica que ningún otro módulo de `src/quantlab/` contenga la cadena
  `msg_type` (salvo `deriv_client.py` y tests).
- Falla si aparece un `msg_type` dinámico no validado.

### 4.3 `test_runtime_payloads`

- Ejecuta una integración con `FakeDerivClient`.
- Registra todos los payloads enviados.
- Verifica que cada `msg_type` esté en la allowlist y que `style` sea válido.
- Verifica que no se envíe `token`, `authorize` ni `subscribe` en
  `ticks_history`.

### 4.4 `test_no_auth_headers`

- Verifica que `websocket-client` se instancie sin headers de autenticación.
- Verifica que no se lea `DERIV_TOKEN` ni `Authorization` del entorno.

### 4.5 `test_zero_disk`

- Ejecuta un snapshot de integración con fixtures.
- Ejecuta el pipeline con fixtures y verifica que no se crea ningún archivo
  (sin CSV, sin `data/cache/`, sin base de datos ni archivos; el export en memoria no escribe archivo).
- Nada se persiste en disco en v1: ni ticks crudos, ni eventos, ni caché, ni
  exportación.

### 4.6 `test_frontend_does_not_import_deriv`

- Verifica que `src/quantlab/static/**` no importe
  `quantlab.data.deriv_client` ni `websocket`.
- El frontend consume `SnapshotService`, los endpoints JSON/WS y `ExportService`
  (solo en memoria; `BytesIO`).

### 4.7 `test_no_trading_routes`

- Recorre las rutas de FastAPI y verifica que no existan `/trade`, `/buy`,
  `/sell`, `/account`, `/portfolio`, `/balance`.
- Verifica que el frontend no contenga `msg_type`.

## 5. Comando de verificación

```powershell
uv run pytest tests/contract/test_forbidden_calls.py -q
```

Debe pasar en CI local y antes de cada release. Un fallo es bloqueante: no se
mergea ni se publica.

## 6. Proceso de excepción

Cualquier nueva llamada a Deriv requiere:

1. Justificación en el issue o PR: qué dato público falta, por qué la allowlist
   actual no lo cubre y qué riesgo tiene.
2. Actualización de este contrato y de la allowlist.
3. Test que demuestre que la llamada es pública y de solo lectura.
4. Revisión de la constitución (Principio I).
5. Aprobación explícita del responsable del proyecto.

No se aceptan excepciones para `buy`, `sell`, `cancel`, `proposal*`,
`portfolio`, `balance`, `statement`, `transaction`, `authorize` ni ninguna
operación de cuenta.

## 7. Revisión manual antes de publicar

- [ ] `grep` de la denylist en `src/`.
- [ ] Inspección de `DerivClient.request` y de todos sus llamadores.
- [ ] Verificar que no hay tokens en `.env.example`, README ni docs.
- [ ] Verificar que no se creó ningún archivo durante la ejecución.
- [ ] Revisar los términos de Deriv si la app deja de ser local.
- [ ] Confirmar que la UI no tiene botones de compra/venta.
- [ ] Confirmar que el export no incluye datos de cuenta.

## 8. Relación con la constitución

| Principio | Cómo lo protege este contrato |
|---|---|
| I. Solo lectura | Allowlist + denylist + tests de payload |
| III. Sin persistencia masiva | `test_zero_disk` |
| V. Núcleo testeable | Tests de contrato sin red |
| VI. Transparencia | Allowlist pública en docs y `/system` |
| VIII. Alcance acotado | Rutas y endpoints limitados a análisis |

Un fallo de estas pruebas es una violación de la constitución y bloquea la
entrega.
