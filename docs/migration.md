# Migración legacy v2.2.0 → QuantLab Fase 1

`legacy/boom_crash_quant_analyzer.py` es la referencia inmutable (SHA-256 en
`tests/fixtures/legacy_sha256.json`). La aplicación nueva **sólo** implementa las
versiones canónicas v2; no se porta ninguna variante v1 ni `export_csv`.

## Equivalencias

| Prototipo (v2.2.0) | Código nuevo | Notas |
|--------------------|--------------|-------|
| `wilson_score_interval` | `analytics.stats.wilson_score_interval` | Paridad 1e-12 vía SciPy |
| `binomial_test_pvalue` | `analytics.stats.binomial_test_pvalue` | Misma cola (`greater` por defecto) |
| `benjamini_hochberg_fdr` | `analytics.stats.benjamini_hochberg_fdr` | Mismo orden de entrada |
| `bootstrap_ci` | `analytics.stats.bootstrap_ci` | Semilla explícita; no toca el RNG global |
| `fit_gpd_mle`, `gpd_return_level` | `analytics.evt.*` | Mismo MLE y hessiano numérico |
| `fit_evt_gpd` (llamada sin definir en v2) | `analytics.evt.fit_evt_gpd` | Implementación completa T022 |
| `detect_candle_extremes` | `analytics.events.detect_candle_extremes` | Umbral `shift(1)`, sin look-ahead |
| `detect_tick_extremes` | `analytics.events.detect_tick_extremes` | Mismo cuantil rolling |
| `conditional_event_table` | `analytics.events.conditional_event_table` | Misma tabla descriptiva |
| `_find_pivots`, `_cluster_levels`, `_evaluate_zone` | `analytics.zones.find_pivots`, `cluster_levels`, `evaluate_zone` | `p_null = 0.5` por defecto |
| `detect_reaction_zones` | `analytics.zones.detect_reaction_zones` | BH opcional (walk-forward) |
| `fit_garch` | `analytics.volatility.fit_garch` | Nombres de contrato `alpha1`/`beta1` |
| `plot_interevent_survival` (cálculo) | `analytics.survival.*` | KM + IC Greenwood + KS |
| `residual_diagnostics` | `analytics.diagnostics.residual_diagnostics` | `het_arch(nlags=...)` |
| `plot_autocorrelation` (cálculo) | `analytics.diagnostics.autocorrelation_result` | `ljung_box` en retardos 10/20 |
| `walk_forward_validation`, `aggregate_walk_forward_results` | `analytics.walkforward.*` | Epochs en vez de `datetime`; aviso de no independencia |
| `_process_live_tick` | `live.stream_manager.StreamManager._process_live_tick` | Algoritmo exacto |
| `fetch_historical_data`, `fetch_tick_history` | `data.market_data_service.MarketDataService` | Sobrevive en RAM |
| `export_csv` (archivo) | **No se porta** | `services.export_service.ExportService` usa `BytesIO` |

## Reglas de la migración

1. **CERO DISCO** en `src/quantlab/`: ninguna escritura de archivos. La única
   excepción es el generador de fixtures (`scripts/generate_fixtures.py`), que
   escribe artefactos de test bajo `tests/fixtures/`.
2. **Sin red en tests**: las suites unitarias y de contrato usan datos
   sintéticos o fixtures; las pruebas `live` son opt-in.
3. **Determinismo**: mismos datos + misma configuración producen el mismo
   resultado. El bootstrap usa semilla fija (42) y `computed_at` de los eventos
   se deriva del `epoch` del evento, no de `now`.
4. **Tipos de contrato**: los nombres públicos que exige
   `contracts/snapshot.schema.json` (`alpha1`, `beta1`, `lag_10_p`, ...) se
   aplican en la fachada `AnalyticsEngine`, no en los cálculos puros.
5. **Sin duplicados**: los helpers compartidos viven en `utils.py`
   (`hash_payload`, `_safe_name`) y los DTOs de API en `dashboard/schemas.py`;
   `tests/unit/test_no_duplicates.py` lo verifica con AST.
