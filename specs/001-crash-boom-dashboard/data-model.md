# Data Model: Crash/Boom Quant Lab Dashboard

**Feature**: `001-crash-boom-dashboard` | **Fecha**: 2026-11-16 | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md)

Este documento define las entidades, sus campos, invariantes y ciclos de vida.
El esquema JSON normativo está en
[`contracts/snapshot.schema.json`](./contracts/snapshot.schema.json). Si hay
conflicto entre este documento y el esquema, el esquema es la fuente de verdad
para la serialización; las reglas de negocio de este documento se validan
además en Python.

## 1. Visión general

```text
Instrument 1 ─── * Candle
     │ 1
     ├── * Tick (solo memoria)
     ├── * EventCandidate
     ├── * ReactionZone
     └── 1 InstrumentSnapshot
                 │ 1
                 └── * TimeframeAnalysis
                         ├── 1 DataQualityReport
                         ├── 1 EventSummary
                         ├── * ReactionZone
                         ├── 0..1 EVTResult
                         ├── 0..1 GARCHResult
                         ├── 0..1 RegimeResult
                         ├── 0..1 SurvivalResult
                         ├── 0..1 ACFResult
                         ├── 0..1 DiagnosticsResult
                         ├── 0..1 ConditionalResult
                         ├── 0..1 WalkForwardResult
                         └── * FeatureStatus
```

`EventSummary` contiene dos `EventSeries`: `candle` y `tick`.

- Un `InstrumentSnapshot` es inmutable y agrupa todas las temporalidades de un
  instrumento.
- `Candle` y `Tick` son datos de entrada; `EventCandidate` y `ReactionZone` son
  resultados analíticos.
- Los objetos operativos (`ConnectionState`, `RateLimitState`, `StreamState`) no se serializan dentro del snapshot; se exponen en
  `/system`.
- Los ticks crudos nunca se persisten.

## 2. Convenciones

### 2.1 Enumeraciones

| Enum | Valores | Uso |
|---|---|---|
| `Direction` | `up`, `down` | Dirección del índice y de los eventos |
| `Timeframe` | `1min`, `5min`, `15min`, `1h`, `4h`, `1d` | Temporalidad de velas |
| `ZoneType` | `support`, `resistance` | Tipo de zona |
| `EventKind` | `candle`, `tick` | Origen del evento candidato |
| `AnalysisStatus` | `pending`, `computing`, `ready`, `insufficient`, `not_applicable`, `error`, `stale` | Estado de un análisis |
| `Stage` | `in_sample`, `oos` | Etapa de validación |
| `OverallStatus` | `ready`, `partial`, `stale`, `error` | Estado del snapshot |
| `Severity` | `info`, `warning`, `error`, `critical` | Severidad de errores |
| `Source` | `deriv`, `fixture` | Origen de los datos |
| `QualityStatus` | `ok`, `warn`, `fail` | Estado de calidad |
| `RegimeMethod` | `markov_switching`, `cusum` | Método de regímenes |
| `SurvivalMode` | `event_index`, `time` | Modo de supervivencia |

### 2.2 Unidades y formatos

| Concepto | Convención |
|---|---|
| Tiempo | Epoch Unix en segundos (int, UTC) en datos; ISO 8601 `YYYY-MM-DDTHH:MM:SSZ` en JSON de presentación |
| Precio | `float` sin redondear en el núcleo; la UI formatea a la precisión del símbolo |
| Retorno | `float` logarítmico salvo que el campo diga `_pct` |
| Tasa | `float` en `[0, 1]`; los campos `*_pct` van en `[0, 100]` |
| ATR | `float` en puntos de precio; los multiplicadores `*_atr` son adimensionales |
| Distancia | Barras (int) para `*_bars`; segundos (int) para `*_seconds` |
| IDs | UUID5 determinista o cadena estable; nunca aleatoria |
| Nulos | `null` con `reason` cuando el valor no es calculable; nunca `0` ni `NaN` |
| Infinitos | Se convierten a `null`; nunca se serializan |

### 2.3 Identificadores

- `snapshot_id`: UUID5 de `symbol|generated_at|config_hash`.
- `zone_id`: UUID5 de `symbol|timeframe|zone_type|center_rounded|available_from_epoch`.
- `event_id`: UUID5 de `symbol|timeframe|kind|event_epoch|event_price`.
- `fold_id`: `"{symbol}-{timeframe}-wf{index:02d}"`.
- `analysis_id`: cadena estable, por ejemplo `evt.gpd`, `volatility.garch`.

## 3. Entidades de dominio

### 3.1 Instrument

Representa un índice operable analíticamente. Es configuración, no datos de
mercado.

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `symbol` | `str` | Sí | `^[A-Z0-9]{3,20}$`; único |
| `display_name` | `str` | Sí | Texto para la UI, en español |
| `direction` | `Direction` | Sí | `CRASH500` → `down`; `BOOM1000` → `up` |
| `enabled` | `bool` | Sí | Si es `false`, no se muestra en el menú |
| `history_days` | `map<Timeframe, int>` | Sí | Debe cubrir las 6 temporalidades; > 0 |
| `tick_history_count` | `int` | Sí | 2–5.000; por defecto 5.000 |
| `color` | `str` | No | Color hex para la UI; no afecta cálculos |
| `notes` | `str` | No | Texto libre; no se usa en cálculos |

**Invariantes**

- `INV-001`: la dirección es coherente con el símbolo; un instrumento nuevo
  debe declararla explícitamente.
- `INV-002`: `history_days` contiene exactamente las 6 temporalidades soportadas.
- `INV-003`: `symbol` no se modifica en caliente; requiere reconstruir el
  snapshot.

**Registro por defecto**

| Símbolo | Nombre | Dirección | Ventanas (días) |
|---|---|---|---|
| `CRASH500` | Crash 500 | `down` | 1min 30, 5min 90, 15min 180, 1h 365, 4h 730, 1d 1825 |
| `BOOM1000` | Boom 1000 | `up` | 1min 30, 5min 90, 15min 180, 1h 365, 4h 730, 1d 1825 |

### 3.2 Candle

Una barra OHLC normalizada.

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `symbol` | `str` | Sí | FK a `Instrument.symbol` |
| `timeframe` | `Timeframe` | Sí | Coherente con `epoch % granularity == 0` |
| `epoch` | `int` | Sí | UTC, segundos; > 0; no futuro |
| `open` | `float` | Sí | > 0 |
| `high` | `float` | Sí | ≥ max(open, close, low) |
| `low` | `float` | Sí | ≤ min(open, close, high); > 0 |
| `close` | `float` | Sí | > 0 |
| `source` | `Source` | Sí | `deriv` o `fixture` |
| `quality_flags` | `array<str>` | No | `gap_before`, `duplicate_removed`, `invalid_ohlc`, `out_of_window` |

**Invariantes**

- `INV-010`: `low <= open <= high` y `low <= close <= high`.
- `INV-011`: `(symbol, timeframe, epoch)` es único.
- `INV-012`: las barras están ordenadas por `epoch` ascendente y no hay epoch
  futuro.
- `INV-013`: una barra con `quality_flags` que incluya `invalid_ohlc` no entra
  al análisis.
- `INV-014`: los precios son finitos y positivos; `NaN`/`Inf` se descartan y se
  cuantifican en calidad.

### 3.3 Tick

Observación de precio en un instante. Solo existe en memoria.

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `symbol` | `str` | Sí | FK a `Instrument.symbol` |
| `epoch` | `int` | Sí | UTC, segundos |
| `price` | `float` | Sí | > 0, finito |
| `received_at` | `datetime` | Sí | UTC; puede ser posterior a `epoch` |
| `sequence` | `int` | Sí | Monotónico por sesión; rompe empates de epoch |

**Invariantes**

- `INV-020`: no se deduplica por `(epoch, price)`; dos ticks válidos pueden
  compartir segundo y precio. El orden se preserva con `sequence`.
- `INV-021`: el buffer vivo está acotado (50.000 ticks por símbolo); al llenarse
  se descarta el más antiguo y se incrementa `dropped_ticks`.
- `INV-022`: un tick nunca se escribe en disco.

### 3.4 EventCandidate

Excursión extrema detectada por velas o ticks. Es un **candidato**, no un spike
oficial ni una señal.

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `event_id` | `str` | Sí | UUID5 determinista |
| `symbol` | `str` | Sí | FK |
| `timeframe` | `Timeframe` | Sí | Temporalidad de cálculo |
| `kind` | `EventKind` | Sí | `candle` o `tick` |
| `direction` | `Direction` | Sí | Coherente con el instrumento |
| `event_epoch` | `int` | Sí | Barra o tick del extremo |
| `event_price` | `float` | Sí | `High`/`Low` o precio del tick |
| `magnitude_points` | `float` | Sí | ≥ 0 |
| `magnitude_pct` | `float` | Sí | ≥ 0; en porcentaje |
| `threshold_points` | `float` | Sí | Umbral vigente, calculado solo con pasado |
| `threshold_basis` | `str` | Sí | `rolling_quantile` |
| `lookback` | `int` | Sí | Barras o ticks usados |
| `warmup_ok` | `bool` | Sí | `false` si no había muestra suficiente |
| `reversal_pct` | `float|null` | No | Reversión cierre→extremo; solo velas |
| `label` | `str` | Sí | `"candidato de spike"` |
| `computed_at` | `datetime` | Sí | UTC |

**Invariantes**

- `INV-030`: el umbral usa `shift(1)`; la barra/tick evaluado nunca contribuye
  a su propio umbral.
- `INV-031`: `magnitude_points >= threshold_points` para un evento incluido.
- `INV-032`: `warmup_ok = false` implica que el evento no se usa en tasas.
- `INV-033`: la etiqueta visible siempre dice "candidato".

#### EventSeries

Una serie de eventos candidatos, calculada por velas o por ticks.

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `status` | `AnalysisStatus` | Sí | `ready`/`insufficient` |
| `n_events` | `int` | Sí | Total de eventos, antes de truncar la lista |
| `threshold_quantile` | `float` | Sí | (0, 1) |
| `lookback` | `int` | Sí | Barras o ticks |
| `warmup` | `int` | Sí | Mínimo de muestra usado |
| `events` | `array<EventCandidate>` | Sí | Hasta 5.000; prioriza los más recientes |
| `threshold_summary` | `object` | Sí | `min`, `median`, `max`; `null` si no hay eventos |
| `note` | `str` | Sí | Texto para la UI |

**Invariantes**

- `INV-034`: `n_events` cuenta todos los eventos; si supera 5.000, `note`
  indica que la lista está truncada.
- `INV-035`: si `n_events = 0`, `threshold_summary` tiene los tres valores
  `null` y `status = insufficient` cuando faltó warm-up.

#### EventSummary

Contenedor de las dos series de una temporalidad:

```text
EventSummary = { candle: EventSeries, tick: EventSeries }
```

Es el tipo del campo `TimeframeAnalysis.events`; el esquema normativo lo define
en `contracts/snapshot.schema.json` como `eventSummary`.

### 3.5 ReactionZone

Nivel de soporte o resistencia construido por pivotes y evaluado por retesteos.

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `zone_id` | `str` | Sí | UUID5 determinista |
| `symbol` | `str` | Sí | FK |
| `timeframe` | `Timeframe` | Sí | Temporalidad |
| `zone_type` | `ZoneType` | Sí | `support`/`resistance` |
| `center` | `float` | Sí | Precio mediano del clúster |
| `lower` | `float` | Sí | `center - half_width` |
| `upper` | `float` | Sí | `center + half_width` |
| `half_width` | `float` | Sí | > 0; tolerancia en puntos |
| `touch_bars` | `int` | Sí | Barras que intersectan la zona |
| `episodes` | `int` | Sí | Retesteos separados por `touch_separation_bars` |
| `evaluated_touches` | `int` | Sí | `reactions + failures` |
| `reactions` | `int` | Sí | Target antes que stop |
| `failures` | `int` | Sí | Stop antes que target |
| `ambiguous_touches` | `int` | Sí | Target y stop en la misma barra/tick |
| `pending_touches` | `int` | Sí | Ni target ni stop en el horizonte |
| `reaction_rate` | `float|null` | No | `reactions / evaluated_touches`; `null` si 0 |
| `ci_lower` | `float|null` | No | Wilson 95 %; `null` si 0 |
| `ci_upper` | `float|null` | No | Wilson 95 %; `null` si 0 |
| `p_null` | `float|null` | No | `b / (a + b)`; `null` si no calculable |
| `p_value_raw` | `float|null` | No | Binomial one-sided `greater` |
| `p_value_adj` | `float|null` | No | BH dentro de la familia |
| `reject` | `bool` | Sí | `p_value_adj <= q` |
| `median_favourable_atr` | `float|null` | No | Mediana de excursión favorable |
| `favourable_ci` | `array<float>|null` | No | Bootstrap 95 % |
| `median_adverse_atr` | `float|null` | No | Mediana de excursión adversa |
| `adverse_ci` | `array<float>|null` | No | Bootstrap 95 % |
| `event_count_in_zone` | `int` | Sí | Eventos candidatos dentro de la zona |
| `last_touch_epoch` | `int|null` | No | Último toque |
| `available_from_epoch` | `int` | Sí | Confirmación del último pivote |
| `stage` | `Stage` | Sí | `in_sample` u `oos` |
| `status` | `AnalysisStatus` | Sí | `ready`/`insufficient` |
| `sensitivity` | `object` | Sí | Tasas alternativas contando ambiguos/pendientes como fallo |
| `reason` | `str|null` | No | Explicación si `status != ready` |

**Invariantes**

- `INV-040`: `lower <= center <= upper` y `upper - lower = 2 * half_width`.
- `INV-041`: `evaluated_touches = reactions + failures`.
- `INV-042`: `reactions + failures + ambiguous_touches + pending_touches = episodes`.
- `INV-043`: si `evaluated_touches = 0`, entonces `reaction_rate`, IC y p-valores
  son `null`, no 0.
- `INV-044`: por defecto `p_null = 0.5` (réplica exacta del prototipo). La
  corrección geométrica `p0 = b/(a+b)` (≈0,4286) es **optativa** y solo se aplica
  con autorización explícita.
- `INV-045`: por defecto BH se aplica solo al walk-forward (como el prototipo).
  Aplicar BH a las zonas in-sample es **optativa** y requiere autorización.
- `INV-046`: `stage = oos` solo puede provenir de un fold de walk-forward; una
  zona in-sample nunca se etiqueta OOS.
- `INV-047`: `available_from_epoch` es la confirmación del último pivote; no se
  evalúan toques anteriores.

**Sensibilidad**

```json
{
  "rate_ambiguous_as_failure_pct": 41.2,
  "rate_pending_as_failure_pct": 38.7,
  "note": "Excluidos del denominador principal"
}
```

### 3.6 DataQualityReport

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `status` | `QualityStatus` | Sí | `ok`/`warn`/`fail` |
| `expected_bars` | `int` | Sí | ≥ 0; calculado del rango y granularidad |
| `received_bars` | `int` | Sí | ≥ 0; barras válidas tras deduplicar y validar OHLC |
| `coverage_pct` | `float` | Sí | `100 * received / expected`; 0 si expected=0 |
| `missing_bars` | `int` | Sí | `max(0, expected - received)` |
| `gaps_count` | `int` | Sí | Huecos > 1,5 × granularidad |
| `duplicates_removed` | `int` | Sí | ≥ 0 |
| `invalid_ohlc` | `int` | Sí | ≥ 0 |
| `first_epoch` | `int|null` | No | Primera barra válida |
| `last_epoch` | `int|null` | No | Última barra válida |
| `stale_seconds` | `int|null` | No | `now - last_epoch`; `null` si no hay datos |
| `dropped_ticks` | `int` | Sí | Solo para streams |
| `errors` | `array<ErrorRecord>` | Sí | Puede estar vacío |
| `notes` | `array<str>` | Sí | Explicaciones legibles |

**Invariantes**

- `INV-050`: `coverage_pct` se acota a `[0, 100]`; si `expected_bars=0`, es 0.
- `INV-051`: `status = ok` solo si cobertura ≥ 98 %, sin huecos críticos y sin
  OHLC inválidos.
- `INV-052`: `status = warn` si cobertura ∈ [90, 98) o hay huecos no críticos.
- `INV-053`: `status = fail` si cobertura < 90 %, no hay barras o el último dato
  excede la antigüedad máxima.

### 3.7 TimeframeAnalysis

Resultado completo de una temporalidad.

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `timeframe` | `Timeframe` | Sí | Clave del mapa |
| `status` | `AnalysisStatus` | Sí | Estado global de la temporalidad |
| `generated_at` | `datetime` | Sí | UTC |
| `data_range` | `object` | Sí | `requested_start_epoch`, `requested_end_epoch`, `start_epoch`, `end_epoch` |
| `bars` | `int` | Sí | ≥ 0 |
| `candles_ref` | `str` | Sí | Clave en memoria o `memory://` |
| `quality` | `DataQualityReport` | Sí | Calidad de la serie |
| `events` | `EventSummary` | Sí | Contenedor `{candle, tick}` de dos `EventSeries` |
| `zones` | `array<ReactionZone>` | Sí | Puede estar vacío |
| `evt` | `EVTResult|null` | No | `null` si no aplica |
| `volatility` | `GARCHResult|null` | No | `null` si no aplica |
| `regimes` | `RegimeResult|null` | No | `null` si no aplica |
| `survival` | `SurvivalResult|null` | No | `null` si no aplica |
| `autocorrelation` | `ACFResult|null` | No | `null` si no aplica |
| `diagnostics` | `DiagnosticsResult|null` | No | `null` si no aplica |
| `conditional` | `ConditionalResult|null` | No | `null` si no aplica |
| `walkforward` | `WalkForwardResult|null` | No | `null` si no aplica |
| `features` | `map<str, FeatureStatus>` | Sí | Estado por análisis |
| `errors` | `array<ErrorRecord>` | Sí | Errores de la temporalidad |

**Invariantes**

- `INV-060`: si `status = insufficient`, todos los resultados inferenciales son
  `null` y cada `FeatureStatus` explica el mínimo requerido.
- `INV-061`: `bars = quality.received_bars` para la serie usada; `received_bars`
  cuenta barras válidas y analizables, no las descartadas por OHLC inválido o
  duplicado.
- `INV-062`: `generated_at >= data_range.end_epoch`.
- `INV-063`: los análisis se calculan sobre la misma serie; no se mezclan
  temporalidades.

### 3.8 InstrumentSnapshot

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `schema_version` | `str` | Sí | `"1.0.0"` |
| `snapshot_id` | `str` | Sí | UUID5 |
| `generated_at` | `datetime` | Sí | UTC |
| `app_version` | `str` | Sí | Semver |
| `config_version` | `str` | Sí | Semver de configuración |
| `config_hash` | `str` | Sí | SHA-256 hex |
| `data_version` | `str` | Sí | Hash de la serie usada |
| `instrument` | `Instrument` | Sí | FK lógica |
| `source` | `Source` | Sí | Fuente dominante |
| `config` | `Config` | Sí | Configuración efectiva: gates, análisis y runtime |
| `timeframes` | `map<Timeframe, TimeframeAnalysis>` | Sí | Al menos una |
| `features` | `array<FeatureStatus>` | Sí | Registro agregado |
| `errors` | `array<ErrorRecord>` | Sí | Errores del instrumento |
| `overall_status` | `OverallStatus` | Sí | Agregado |
| `cache_bytes` | `int` | Sí | ≥ 0; RAM usada por el instrumento (nombre histórico) |
| `notes` | `array<str>` | Sí | Advertencias globales |

**Invariantes**

- `INV-070`: el snapshot es inmutable una vez publicado; cualquier cambio crea
  un `snapshot_id` nuevo.
- `INV-071`: `overall_status` sigue esta precedencia: `stale` si hay datos y
  superan la antigüedad máxima (`stale_seconds`); en caso contrario, `ready` solo
  si todas las temporalidades solicitadas están `ready`; `partial` si al menos una
  tiene un resultado usable (`ready` o `insufficient` con calidad y gates) y no
  todas están `ready`; `error` si ninguna tiene resultado usable o el snapshot no
  contiene temporalidades.
- `INV-072`: `config_hash` cubre todos los parámetros analíticos y gates; dos
  configuraciones distintas no comparten hash.
- `INV-073`: un snapshot con `schema_version` desconocida no se sirve a la UI.
- `INV-074`: el reemplazo en `SnapshotStore` es atómico; la UI nunca ve un
  objeto a medio construir.

### 3.9 Resultados analíticos

#### EVTResult

| Campo | Tipo | Reglas |
|---|---|---|
| `status` | `AnalysisStatus` | `ready`/`insufficient`/`error` |
| `threshold` | `float` | Umbral `u` en puntos |
| `threshold_quantile` | `float` | 0,90 o 0,95 |
| `n_excess` | `int` | ≥ 0 |
| `xi` | `float|null` | Parámetro de forma |
| `sigma` | `float|null` | Escala > 0 |
| `se_xi` | `float|null` | Error estándar |
| `se_sigma` | `float|null` | Error estándar |
| `ci_xi` | `array<float>|null` | IC 95 % |
| `ci_sigma` | `array<float>|null` | IC 95 % |
| `ks_p` | `float|null` | Bondad de ajuste |
| `converged` | `bool` | Convergencia |
| `return_levels` | `map<str, float>` | `"10"`, `"50"`, `"100"` barras |
| `mean_excess` | `array<object>` | `{threshold, mean_excess, n}` |
| `message` | `str|null` | Razón si no `ready` |

**Invariantes**

- `INV-080`: si `n_excess < 30`, `status = insufficient` y `xi/sigma = null`.
- `INV-081`: `sigma > 0`; `xi` puede ser negativo, pero si el ajuste no es
  finito se descarta.
- `INV-082`: los niveles de retorno usan la fórmula de `research.md` D5.4.

#### GARCHResult

| Campo | Tipo | Reglas |
|---|---|---|
| `status` | `AnalysisStatus` | `ready`/`insufficient`/`error` |
| `model` | `str` | `"GARCH(1,1)"` |
| `distribution` | `str` | `normal` o `t` |
| `n_obs` | `int` | ≥ 0 |
| `params` | `map<str, float>` | `omega`, `alpha1`, `beta1`, `nu` opcional |
| `p_values` | `map<str, float>` | Mismas claves |
| `persistence` | `float|null` | `alpha1 + beta1` |
| `aic` | `float|null` | |
| `bic` | `float|null` | |
| `log_likelihood` | `float|null` | |
| `forecast` | `array<object>` | 10 pasos con `mean`, `variance`, `sigma`, IC |
| `diagnostics` | `object` | Ljung-Box y ARCH-LM de residuos |
| `converged` | `bool` | |
| `message` | `str|null` | |

**Invariantes**

- `INV-083`: si `n_obs < 200`, `status = insufficient` y no hay parámetros.
- `INV-084`: `persistence < 1` para un modelo estacionario; si ≥ 1, se advierte
  y no se usa el pronóstico a largo plazo.
- `INV-085`: GARCH se ajusta sobre retornos de barras, nunca sobre ticks.

#### RegimeResult

| Campo | Tipo | Reglas |
|---|---|---|
| `status` | `AnalysisStatus` | |
| `method` | `RegimeMethod` | |
| `n_regimes` | `int` | 2 |
| `n_obs` | `int` | |
| `high_vol_prob_current` | `float|null` | [0,1] |
| `series` | `array<object>` | `{epoch, prob_high}` downsampled ≤ 5.000 |
| `segments` | `array<object>` | `{start_epoch, end_epoch, regime, mean_variance}` |
| `last_switch_epoch` | `int|null` | |
| `converged` | `bool` | |
| `message` | `str|null` | |

**Invariantes**

- `INV-086`: los estados se ordenan por varianza; el estado 1 es alta
  volatilidad.
- `INV-087`: si Markov no converge, `method = cusum` y se reporta.
- `INV-088`: `n_obs < 500` → `insufficient`.

#### SurvivalResult

| Campo | Tipo | Reglas |
|---|---|---|
| `status` | `AnalysisStatus` | |
| `mode` | `SurvivalMode` | |
| `n_events` | `int` | |
| `n_censored` | `int` | |
| `median` | `float|null` | |
| `median_ci` | `array<float>|null` | |
| `curve` | `array<object>` | `{time, survival, ci_lower, ci_upper, at_risk}` |
| `message` | `str|null` | |

**Invariantes**

- `INV-089`: la última observación se censura si el periodo termina antes del
  siguiente evento.
- `INV-090`: `n_events < 20` → `insufficient`.

#### ACFResult y DiagnosticsResult

| Campo | Tipo | Reglas |
|---|---|---|
| `ACFResult.n_obs` | `int` | ≥ 100 |
| `ACFResult.max_lag` | `int` | 50 por defecto |
| `ACFResult.acf_returns` | `array<float>` | Longitud `max_lag + 1` |
| `ACFResult.acf_squared` | `array<float>` | Longitud `max_lag + 1` |
| `ACFResult.conf_band` | `float` | `1.96 / sqrt(n)` |
| `ACFResult.ljung_box` | `object` | p en lags 10 y 20 |
| `ACFResult.message` | `str|null` | Razón si no `ready` |
| `DiagnosticsResult.jarque_bera` | `object` | `{stat, p}` |
| `DiagnosticsResult.arch_lm` | `object` | `{stat, p, lags}` |
| `DiagnosticsResult.adf` | `object` | `{stat, p, lags}` |
| `DiagnosticsResult.message` | `str|null` | Razón si no `ready` |

#### ConditionalResult

| Campo | Tipo | Reglas |
|---|---|---|
| `status` | `AnalysisStatus` | |
| `mode` | `str` | `bars` o `ticks` |
| `horizon` | `int` | Pasos hacia adelante |
| `bin_size` | `int` | Pasos por bin |
| `bins` | `array<object>` | `{bin_start, bin_end, n_events, n_trials, probability, ci_lower, ci_upper}` |
| `note` | `str` | "descriptivo, in-sample" |

**Invariantes**

- `INV-091`: cada bin con `n_events < 30` se agrupa o se marca `insufficient`.
- `INV-092`: la probabilidad es descriptiva; no se usa como señal.

### 3.10 Walk-forward

#### WalkForwardFold

| Campo | Tipo | Reglas |
|---|---|---|
| `fold_id` | `str` | Único |
| `train_start_epoch` | `int` | |
| `train_end_epoch` | `int` | `< test_start_epoch` |
| `test_start_epoch` | `int` | |
| `test_end_epoch` | `int` | `> test_start_epoch` |
| `bars_train` | `int` | |
| `bars_test` | `int` | |
| `zones_evaluated` | `int` | |
| `reactions` | `int` | |
| `evaluated_touches` | `int` | |
| `reaction_rate` | `float|null` | |
| `ci_lower` | `float|null` | |
| `ci_upper` | `float|null` | |
| `p_value_raw` | `float|null` | |
| `p_value_adj` | `float|null` | |
| `stage` | `Stage` | `oos` |

#### WalkForwardResult

| Campo | Tipo | Reglas |
|---|---|---|
| `status` | `AnalysisStatus` | |
| `method` | `str` | `anchored` |
| `config` | `object` | `train_bars`, `test_bars`, `step_bars`, `min_folds` |
| `folds` | `array<WalkForwardFold>` | |
| `aggregate` | `object` | `folds`, `evaluated`, `reactions`, `rate`, `ci`, `sign_test_p`, `independent: false` |
| `warning` | `str` | Folds no independientes |
| `recommendation` | `str|null` | Temporalidad sugerida si insuficiente |

**Invariantes**

- `INV-100`: `train_end_epoch < test_start_epoch` en todos los folds.
- `INV-101`: las zonas del test se detectan solo con train; nunca se filtran
  desde el futuro.
- `INV-102`: `folds.length >= 3` para `status = ready`.
- `INV-103`: `aggregate.independent = false` siempre; el sign test es
  descriptivo.
- `INV-104`: 1min se marca `not_applicable`; la recomendación es 5min o 15min.

### 3.11 FeatureStatus

| Campo | Tipo | Reglas |
|---|---|---|
| `id` | `str` | `evt.gpd`, `volatility.garch`, etc. |
| `name_es` | `str` | Nombre visible |
| `category` | `str` | `events`, `zones`, `evt`, `volatility`, `survival`, `diagnostics`, `walkforward`, `data` |
| `status` | `AnalysisStatus` | |
| `min_sample` | `int` | Gate aplicado |
| `sample_size` | `int` | Muestra disponible |
| `reason` | `str|null` | Explicación |
| `ui_path` | `str` | Ruta al panel |
| `stage` | `Stage` | |

**Invariantes**

- `INV-110`: cada análisis del producto tiene exactamente un `FeatureStatus` por
  temporalidad.
- `INV-111`: `status = insufficient` incluye `min_sample` y `sample_size` para
  que la UI explique el faltante.
- `INV-112`: el registro es la fuente de la página Sistema; no se mantiene una
  lista duplicada en la UI.

### 3.12 ExportBundle (en memoria)

`ExportBundle` se define en `specs/002-migration-foundation/data-model.md` y se
genera en memoria (`BytesIO`; JSON/CSV/ZIP). Nunca se escribe en disco. La
serialización del snapshot sigue las reglas de §9.3.

### 3.13 Config

Configuración efectiva con la que se construyó el snapshot. Es la base de la
reproducibilidad y del `config_hash`.

| Campo | Tipo | Obligatorio | Reglas |
|---|---|---:|---|
| `version` | `str` | Sí | Semver de configuración |
| `gates` | `GateConfig` | Sí | Ver sección 6; todos los valores > 0 |
| `analysis` | `object` | Sí | Cuantiles, lookbacks, ATR, FDR, EVT, GARCH y método de regímenes |
| `runtime` | `object` | Sí | Rate limit, RAM, refresco y staleness |

**Invariantes**

- `INV-123`: `config_hash` es SHA-256 de `gates + analysis + runtime +
  version`; cambiar cualquier valor produce un hash distinto.
- `INV-124`: la configuración se valida antes de construir el snapshot; un valor
  fuera de rango lanza `ConfigError`.
- `INV-125`: la UI de Metodología y Sistema muestra la misma configuración
  serializada en el snapshot, no una copia separada.

## 4. Entidades operativas

### 4.1 ConnectionState

| Campo | Tipo | Notas |
|---|---|---|
| `active_endpoint` | `str` | Endpoint en uso |
| `connected` | `bool` | |
| `last_message_at` | `datetime|null` | |
| `reconnect_count` | `int` | |
| `last_error` | `ErrorRecord|null` | |
| `subscriptions` | `array<str>` | `ticks:BOOM1000`, etc. |
| `latency_ms_p50` | `float|null` | Últimas 100 peticiones |

### 4.2 RateLimitState

| Campo | Tipo | Notas |
|---|---|---|
| `tokens` | `float` | 0–30 |
| `capacity` | `int` | 30 |
| `refill_per_second` | `float` | 2,5 |
| `calls_last_minute` | `int` | |
| `waited_ms_last_request` | `int` | |
| `budget_per_minute` | `int` | 150 |

### 4.3 StreamState

| Campo | Tipo | Notas |
|---|---|---|
| `symbol` | `str` | |
| `running` | `bool` | |
| `received` | `int` | |
| `processed` | `int` | |
| `dropped_ticks` | `int` | |
| `queue_depth` | `int` | |
| `last_tick_epoch` | `int|null` | |
| `warmup_complete` | `bool` | ≥ 300 ticks |
| `live_events` | `int` | Desde el inicio de la sesión |

### 4.4 ErrorRecord

| Campo | Tipo | Notas |
|---|---|---|
| `code` | `str` | Ver taxonomía |
| `severity` | `Severity` | |
| `message_es` | `str` | Accionable |
| `detail` | `str|null` | Técnico, sin secretos |
| `occurred_at` | `datetime` | UTC |
| `retryable` | `bool` | |
| `source` | `str` | `deriv`, `analytics`, `ui` |
| `context` | `map<str, str>` | Símbolo, TF, `req_id` |

### 4.5 CacheManifest (eliminado)

No hay caché en disco. El manifiesto de caché (Parquet) del prototipo se elimina;
los datos viven solo en RAM. El uso de RAM se registra en `telemetry` (sin
archivo).

## 5. Ciclos de vida

### 5.1 Snapshot

```text
BUILDING ──► READY
    │          │
    ├──► PARTIAL ──► STALE ──► BUILDING
    └──► ERROR ────► STALE
```

- `BUILDING`: se está construyendo; no visible.
- `READY`: todas las temporalidades solicitadas están listas.
- `PARTIAL`: al menos una con resultado usable y otra no `ready`; incluye
  insuficiencia explícita con calidad y gates.
- `STALE`: los datos superan la antigüedad máxima; se sigue mostrando con badge.
- `ERROR`: ninguna temporalidad con resultado usable; la UI muestra diagnóstico.

### 5.2 TimeframeAnalysis

```text
PENDING ──► COMPUTING ──► READY
                │           │
                ├──► INSUFFICIENT
                ├──► NOT_APPLICABLE
                └──► ERROR
READY ──► STALE ──► COMPUTING
```

### 5.3 Caché (eliminada)

No hay caché en disco; los datos viven solo en RAM.

### 5.4 Stream

```text
STOPPED ──► CONNECTING ──► SUBSCRIBED ──► RUNNING
                │              │            │
                └──► ERROR ◄───┴────────────┘
RUNNING ──► RECONNECTING ──► SUBSCRIBED
```

## 6. Puertas de muestra (`GateConfig`)

| Clave | Mínimo | Preferido | Análisis |
|---|---:|---:|---|
| `candle_events_min_bars` | 50 | 200 | Eventos de vela |
| `tick_events_min_ticks` | 300 | 2.000 | Eventos de tick |
| `zone_min_evaluated` | 10 | 30 | Zona individual |
| `zone_min_count` | 5 | 20 | BH global |
| `evt_min_excess` | 30 | 100 | EVT/GPD |
| `garch_min_obs` | 200 | 500 | GARCH |
| `regime_min_obs` | 500 | 1.000 | Regímenes |
| `survival_min_events` | 20 | 50 | Supervivencia |
| `acf_min_obs` | 100 | 300 | ACF/diagnósticos |
| `conditional_min_events` | 30 | 100 | Condicional por bin |
| `walkforward_min_folds` | 3 | 5 | Walk-forward |
| `walkforward_min_days_5min` | 60 | 90 | Historia 5min |
| `walkforward_min_days_15min` | 90 | 180 | Historia 15min |
| `walkforward_min_days_1h` | 180 | 365 | Historia 1h |
| `walkforward_min_days_4h` | 365 | 730 | Historia 4h |
| `walkforward_min_days_1d` | 730 | 1825 | Historia 1d |

`GateConfig` se serializa en el snapshot (`config.gates`) y se muestra en
Metodología. Cambiar una puerta cambia `config_hash`.

## 7. Forma del snapshot (ejemplo abreviado)

```json
{
  "schema_version": "1.0.0",
  "snapshot_id": "6f1c2b7e-6e0e-5f0a-9d0e-2a4b8c1d3e5f",
  "generated_at": "2026-11-15T12:00:00Z",
  "app_version": "0.1.0",
  "config_version": "1.0.0",
  "config_hash": "sha256:9f2c...",
  "data_version": "sha256:1a2b...",
  "instrument": {
    "symbol": "BOOM1000",
    "display_name": "Boom 1000",
    "direction": "up",
    "enabled": true,
    "history_days": {
      "1min": 30, "5min": 90, "15min": 180,
      "1h": 365, "4h": 730, "1d": 1825
    },
    "tick_history_count": 5000
  },
  "source": "deriv",
  "config": {
    "version": "1.0.0",
    "gates": {
      "candle_events_min_bars": 50,
      "tick_events_min_ticks": 300,
      "zone_min_evaluated": 10,
      "zone_min_count": 5,
      "evt_min_excess": 30,
      "garch_min_obs": 200,
      "regime_min_obs": 500,
      "survival_min_events": 20,
      "acf_min_obs": 100,
      "conditional_min_events": 30,
      "walkforward_min_folds": 3,
      "walkforward_min_days_5min": 60,
      "walkforward_min_days_15min": 90,
      "walkforward_min_days_1h": 180,
      "walkforward_min_days_4h": 365,
      "walkforward_min_days_1d": 730
    },
    "analysis": {
      "candle_event_quantile": 0.99,
      "event_lookback_bars": 200,
      "tick_event_quantile": 0.995,
      "tick_event_lookback": 2000,
      "zone_tolerance_atr": 0.5,
      "pivot_window": 3,
      "min_zone_touches": 3,
      "touch_separation_bars": 3,
      "response_horizon_bars": 10,
      "favourable_atr": 1.0,
      "adverse_atr": 0.75,
      "fdr_q": 0.1,
      "evt_threshold_quantile": 0.95,
      "garch_max_p": 1,
      "garch_max_q": 1,
      "regime_method": "markov_switching"
    },
    "runtime": {
      "rate_limit_per_minute": 150,
      "cache_limit_mb": 400,
      "refresh_seconds": 60,
      "stale_seconds": 900
    }
  },
  "timeframes": {
    "5min": {
      "timeframe": "5min",
      "status": "ready",
      "generated_at": "2026-11-15T12:00:00Z",
      "data_range": {
        "requested_start_epoch": 1752710400,
        "requested_end_epoch": 1763208000,
        "start_epoch": 1752710400,
        "end_epoch": 1763207700
      },
      "bars": 25920,
      "candles_ref": "candles/BOOM1000/5min/2026",
      "quality": {
        "status": "ok",
        "expected_bars": 25920,
        "received_bars": 25920,
        "coverage_pct": 100.0,
        "missing_bars": 0,
        "gaps_count": 0,
        "duplicates_removed": 0,
        "invalid_ohlc": 0,
        "first_epoch": 1752710400,
        "last_epoch": 1763207700,
        "stale_seconds": 300,
        "dropped_ticks": 0,
        "errors": [],
        "notes": []
      },
      "events": {
        "candle": {
          "status": "ready",
          "n_events": 248,
          "threshold_quantile": 0.99,
          "lookback": 200,
          "warmup": 50,
          "events": [],
          "threshold_summary": {"min": 12.1, "median": 24.7, "max": 61.3},
          "note": "Candidatos de spike por velas"
        },
        "tick": {
          "status": "ready",
          "n_events": 37,
          "threshold_quantile": 0.995,
          "lookback": 2000,
          "warmup": 300,
          "events": [],
          "threshold_summary": {"min": 3.2, "median": 7.8, "max": 19.4},
          "note": "Candidatos de spike por ticks; sesión actual"
        }
      },
      "zones": [],
      "evt": null,
      "volatility": null,
      "regimes": null,
      "survival": null,
      "autocorrelation": null,
      "diagnostics": null,
      "conditional": null,
      "walkforward": null,
      "features": {},
      "errors": []
    }
  },
  "features": [],
  "errors": [],
  "overall_status": "partial",
  "cache_bytes": 7340032,
  "notes": ["5min listo; 1h pendiente"]
}
```

El esquema completo y normativo está en
[`contracts/snapshot.schema.json`](./contracts/snapshot.schema.json). Un ejemplo
validado contra el esquema está en
[`contracts/snapshot.example.json`](./contracts/snapshot.example.json).

## 8. Disposición en memoria (cero disco)

```text
RAM (proceso):
├── self.data → DataFrames (velas por temporalidad + ticks)
├── self.results → eventos y zonas por temporalidad
├── self._directional_returns → deque(maxlen=10.000)
├── live_ticks_records → deque(maxlen=50.000)
├── live_events → deque(maxlen=5.000)
└── InstrumentSnapshot → inmutable, en RAM
```

- El snapshot vive solo en RAM; no hay `latest.json` ni archivos.
- No hay `manifest.json`, Parquet, CSV ni base de datos.
- `export_csv` fue eliminado; no existe exportación a disco.
- No existe `data/cache/`; el proceso no escribe ningún archivo.

## 9. Reglas de validación

### 9.1 Validación de esquema

- `validate_snapshot(data)` usa `jsonschema` con
  `contracts/snapshot.schema.json`.
- `schema_version` debe ser exactamente `1.0.0` para esta versión.
- Los objetos tienen `additionalProperties: false`; un campo nuevo requiere
  subir `schema_version`.

### 9.2 Validación de negocio (Python)

| Regla | Dónde se valida |
|---|---|
| OHLC coherente | `market_data_service.validate_candles` |
| Epoch único y ordenado | `market_data_service.validate_candles` |
| `evaluated = reactions + failures` | `analytics/zones.py` |
| `p_null = b/(a+b)` | `analytics/zones.py` |
| BH por familia | `analytics/stats.py` |
| Gate por análisis | `services/snapshot_service.py` |
| `n_obs` mínimo para GARCH/EVT | `analytics/volatility.py`, `analytics/evt.py` |
| Folds sin fuga temporal | `analytics/walkforward.py` |
| Snapshot inmutable | `services/snapshot_store.py` |
| RAM < 400 MB | `tests/performance/test_ram_budget.py` |
| Cero disco | test de contrato + inspección del directorio de trabajo |

### 9.3 Reglas de serialización

- `NaN` e `Inf` se convierten a `null` antes de serializar; el objeto que los
  contenía incluye `reason`.
- Los `datetime` se serializan como ISO 8601 UTC con `Z`.
- Los `float` no se redondean en el núcleo; la UI formatea.
- Los `bool` no se serializan como 0/1.
- Las claves de mapas son strings; las temporalidades usan su etiqueta
  (`"5min"`).
- `cache_limit_mb` y `cache_bytes` son nombres históricos del contrato; en v1
  representan el presupuesto y el uso de RAM (no hay caché en disco).

## 10. Taxonomía de errores

| Código | Severidad | Retryable | Origen | Mensaje UI |
|---|---|---|---|---|
| `DERIV_TRANSPORT` | error | Sí | deriv | "No se pudo conectar con Deriv; reintentando" |
| `DERIV_TIMEOUT` | error | Sí | deriv | "Deriv no respondió a tiempo" |
| `DERIV_RATE_LIMIT` | warning | Sí | deriv | "Límite de Deriv alcanzado; esperando" |
| `DERIV_API_ERROR` | error | No | deriv | "Deriv rechazó la solicitud" |
| `SYMBOL_NOT_ACTIVE` | error | No | deriv | "El símbolo no está activo en tu jurisdicción" |
| `SYMBOL_NOT_FOUND` | error | No | deriv | "Símbolo no encontrado; revisa la configuración" |
| `DATA_EMPTY` | warning | No | data | "No se recibieron velas para esta temporalidad" |
| `DATA_INVALID` | warning | No | data | "Se descartaron barras inválidas" |
| `DATA_GAP` | warning | No | data | "Hay huecos en la serie" |
| `DATA_STALE` | warning | No | data | "Los datos están desactualizados" |
| `COMPUTE_INSUFFICIENT` | info | No | analytics | "Datos insuficientes para este análisis" |
| `COMPUTE_FAILED` | error | No | analytics | "El análisis falló; revisa el detalle" |
| `SNAPSHOT_STALE` | warning | No | services | "El snapshot está desactualizado" |
| `STREAM_DROPPED` | warning | No | live | "Se descartaron ticks por saturación" |
| `EXPORT_FAILED` | error | No | services | "No se pudo generar el archivo" |
| `CONFIG_INVALID` | critical | No | config | "Configuración inválida" |

## 11. Relaciones y claves

| Relación | Cardinalidad | Clave |
|---|---|---|
| `Instrument` → `Candle` | 1:N | `(symbol, timeframe, epoch)` |
| `Instrument` → `Tick` | 1:N (memoria) | `(symbol, sequence)` |
| `Instrument` → `InstrumentSnapshot` | 1:N (histórico) | `snapshot_id` |
| `InstrumentSnapshot` → `TimeframeAnalysis` | 1:N | `timeframe` |
| `TimeframeAnalysis` → `ReactionZone` | 1:N | `zone_id` |
| `TimeframeAnalysis` → `EventSummary` | 1:1 | `timeframe`; contiene 2 `EventSeries` |
| `WalkForwardResult` → `WalkForwardFold` | 1:N | `fold_id` |
| `InstrumentSnapshot` → `FeatureStatus` | 1:N | `id` |

## 12. Resumen de invariantes críticas

| ID | Invariante | Test |
|---|---|---|
| INV-001 | Dirección coherente con el símbolo | `test_instruments.py` |
| INV-010 | OHLC coherente | `test_quality.py` |
| INV-030 | Umbral sin look-ahead | `test_events.py` |
| INV-041 | `evaluated = reactions + failures` | `test_zones.py` |
| INV-044 | `p_null = 0,5` por defecto; geometría optativa | `test_zones.py` |
| INV-045 | BH solo walk-forward por defecto; in-sample optativo | `test_stats.py` |
| INV-060 | Insuficiente ⇒ resultados `null` | `test_snapshot.py` |
| INV-070 | Snapshot inmutable | `test_snapshot_store.py` |
| INV-080 | EVT con < 30 excesos no ajusta | `test_evt.py` |
| INV-083 | GARCH con < 200 obs no ajusta | `test_volatility.py` |
| INV-088 | Regímenes con < 500 obs no ajustan | `test_volatility.py` |
| INV-090 | Supervivencia con < 20 eventos no ajusta | `test_survival.py` |
| INV-100 | Folds sin fuga temporal | `test_walkforward.py` |
| INV-103 | Folds no independientes | `test_walkforward.py` |
| INV-110 | Un `FeatureStatus` por análisis y TF | `test_snapshot.py` |
| INV-022 | Cero disco | `test_forbidden_calls.py` + `test_zero_disk.py` |
