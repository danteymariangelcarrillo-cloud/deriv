# Requirements Checklist — Migration Foundation

**Feature**: `002-migration-foundation` | **Fecha**: 2026-11-16

Lista de verificación de calidad de requisitos (equivalente a `/speckit.checklist`).

---

## 1. Completitud Funcional

| Requisito | Especificado | Testable | Trazable a Código | Notas |
|-----------|--------------|----------|-------------------|-------|
| FR-M1: Config Pydantic | ✅ spec.md §Requirements | ✅ test_config.py | ✅ config.py | AnalyzerConfig, ModelConfig, GateConfig, RAMConfig |
| FR-M2: Config Hash | ✅ spec.md §Requirements | ✅ test_config_hash.py | ✅ utils.py | SHA-256 canónico |
| FR-M3: DerivClient Async | ✅ spec.md §Requirements | ✅ test_deriv_client.py | ✅ data/deriv_client.py | WS persistente, rate limiter, fallback |
| FR-M4: MarketDataService | ✅ spec.md §Requirements | ✅ test_market_data.py | ✅ data/market_data_service.py | Sin persistencia |
| FR-M5: RAM Cache | ✅ spec.md §Requirements | ✅ test_ram_cache.py | ✅ data/cache.py | deque + LRU + monitor |
| FR-M6: SnapshotService | ✅ spec.md §Requirements | ✅ test_snapshot.py | ✅ services/snapshot_service.py | Gates → analytics → atomic swap |
| FR-M7: StreamManager | ✅ spec.md §Requirements | ✅ test_live_stream.py | ✅ live/stream_manager.py | Algoritmo exacto original |
| FR-M8: FastAPI App | ✅ spec.md §Requirements | ✅ test_api.py | ✅ dashboard/app.py + routes.py | REST + WS |
| FR-M9: fit_evt_gpd | ✅ spec.md §Requirements | ✅ test_evt.py | ✅ analytics/evt.py | **Completa: MLE+KS+MeanExcess+RL** |
| FR-M10: detect_volatility_regimes | ✅ spec.md §Requirements | ✅ test_volatility.py | ✅ analytics/volatility.py | **Markov + CUSUM fallback** |
| FR-M11: Eliminar duplicados | ✅ spec.md §Requirements | ✅ grep test | ✅ boom_crash_quant_analyzer.py | 4 métodos v1 eliminados |
| FR-M12: ExportService RAM | ✅ spec.md §Requirements | ✅ test_export.py | ✅ services/export_service.py | BytesIO → StreamingResponse |

---

## 2. Requisitos No Funcionales

| Requisito | Especificado | Medible | Target | Verificación |
|-----------|--------------|---------|--------|--------------|
| NFR-M1: Arranque | ✅ spec.md | ✅ | <3s cold / <30s warm | tests/performance/test_startup_time.py + test_warm_start.py |
| NFR-M2: RAM | ✅ spec.md | ✅ | < 400 MB | tests/performance/test_ram_usage.py |
| NFR-M3: Rate Limit | ✅ spec.md | ✅ | ≤ 150/min | tests/performance/test_rate_limit.py |
| NFR-M4: Cero Disco | ✅ spec.md | ✅ | 0 archivos | tests/contract/test_forbidden_calls.py + T043 grep disco |
| NFR-M5: Recalculo Config | ✅ spec.md | ✅ | < 500ms/TF | tests/performance/test_recompute_time.py |
| NFR-M6: Latencia Tick | ✅ spec.md | ✅ | < 5ms | tests/performance/test_tick_latency.py |
| NFR-M7: Reconexión | ✅ spec.md | ✅ | Auto + backoff | tests/integration/ (2.10 reconnect 30 s) |
| NFR-M8: Paridad Numérica | ✅ spec.md | ✅ | 1e-10 | tests/unit/test_*_parity.py |

---

## 3. Trazabilidad Spec → Código → Tests

| Spec Section | Código | Tests |
|--------------|--------|-------|
| spec.md §FR-M1 | config.py | test_config.py |
| spec.md §FR-M2 | utils.py:config_hash | test_config_hash.py |
| spec.md §FR-M3 | data/deriv_client.py | test_deriv_client.py |
| spec.md §FR-M4 | data/market_data_service.py | test_market_data.py |
| spec.md §FR-M5 | data/cache.py | test_ram_cache.py |
| spec.md §FR-M6 | services/snapshot_service.py | test_snapshot.py |
| spec.md §FR-M7 | live/stream_manager.py | test_live_stream.py |
| spec.md §FR-M8 | dashboard/app.py, routes.py | test_api.py, test_ws.py |
| spec.md §FR-M9 | analytics/evt.py | test_evt.py |
| spec.md §FR-M10 | analytics/volatility.py | test_volatility.py |
| spec.md §FR-M11 | (eliminación en legacy) | test_no_duplicates.py |
| spec.md §FR-M12 | services/export_service.py | test_export.py |

---

## 4. Validación de Esquemas

| Esquema | Archivo | Validado Contra |
|---------|---------|-----------------|
| Snapshot JSON | contracts/snapshot.schema.json | snapshot.example.json + snapshots generados |
| API REST | OpenAPI auto (Pydantic) | tests/contract/test_api_schema.py |
| WebSocket | contracts/service-interfaces.md | tests/contract/test_ws_protocol.py |
| Forbidden Calls | contracts/forbidden-calls.md | tests/contract/test_forbidden_calls.py |

---

## 5. Paridad Numérica (Obligatoria)

| Función | Original | Migrada | Test Paridad | Tolerancia |
|---------|----------|---------|--------------|------------|
| wilson_score_interval | boom_crash_quant_analyzer.py L72 | analytics/stats.py | test_stats_parity.py | 1e-10 |
| binomial_test_pvalue | L86 | analytics/stats.py | test_stats_parity.py | 1e-10 |
| benjamini_hochberg_fdr | L101 | analytics/stats.py | test_stats_parity.py | 1e-10 |
| fit_gpd_mle | L119 | analytics/evt.py | test_evt_parity.py | 1e-10 |
| gpd_return_level | L164 | analytics/evt.py | test_evt_parity.py | 1e-10 |
| bootstrap_ci | L173 | analytics/stats.py | test_stats_parity.py | estadística (RNG distinto) |
| detect_candle_extremes | L626 | analytics/events.py | test_events_parity.py | 1e-10 |
| detect_tick_extremes | L680 | analytics/events.py | test_events_parity.py | 1e-10 |
| _evaluate_zone | L753 | analytics/zones.py | test_zones_parity.py | 1e-10 |
| fit_garch | L1598 | analytics/volatility.py | test_volatility_parity.py | 1e-10 |
| _process_live_tick | L1666 | live/stream_manager.py | test_live_parity.py | 1e-10 |

---

## 6. Fixtures Determinísticas

| Fixture | Generado Con | Seed | Usado En |
|---------|--------------|------|----------|
| boom1000_5min.parquet | Script generador | 42 | test_market_data.py, test_snapshot.py |
| crash500_ticks.csv | Script generador | 42 | test_live_stream.py, test_events_parity.py |
| snapshot_boom1000.json | build_snapshot() | 42 | test_snapshot.py, verify_contracts.py |
| synthetic_evt.csv | GPD(xi=0.2, σ=1.5) | 42 | test_evt.py |
| synthetic_garch.csv | GARCH(ω=1e-7, α=0.08, β=0.91) | 42 | test_volatility.py |
| synthetic_regimes.csv | 2 regímenes var=0.0004/0.0012 | 42 | test_volatility.py |

---

## 7. Criterios de Aceptación (Definition of Done)

| Criterio | Verificación | Estado |
|----------|--------------|--------|
| SC-M1: Arranque < 3s | pytest tests/performance/test_startup_time.py | ⬜ |
| SC-M2: Config hot-reload | POST /api/config → WS push + recalculated | ⬜ |
| SC-M3: Spike Deriv | scripts/spike_deriv.py conecta < 5s | ⬜ |
| SC-M4: RAM < 400 MB | pytest tests/performance/test_ram_usage.py | ⬜ |
| SC-M5: Cero Disco | test_forbidden_calls.py + T043 grep disco | ⬜ |
| SC-M6: Paridad 100% | pytest tests/unit/test_*_parity.py | ⬜ |
| SC-M7: Funciones faltantes | fit_evt_gpd + detect_volatility_regimes implementadas | ⬜ |
| SC-M8: Duplicados eliminados | grep v1 methods → 0 resultados | ⬜ |
| SC-M9: Rate Limit ≤ 150/min | pytest tests/performance/test_rate_limit.py | ⬜ |
| SC-M10: Reconexión Auto | tests/integration/ (2.10 reconnect 30 s) | ⬜ |

---

## 8. Checklist de Calidad de Código

- [ ] **Type Hints**: 100% funciones públicas tipadas
- [ ] **Docstrings**: Google style en todas las funciones públicas
- [ ] **Immutability**: Pydantic `frozen=True` en snapshots
- [ ] **Async Safety**: `asyncio.Lock` para estado compartido
- [ ] **Error Handling**: Taxonomía completa (errors.py) + logging estructurado
- [ ] **No Hardcoded Secrets**: Solo `.env` / `settings.py`
- [ ] **Deterministic Tests**: Seed fija (42) en todos los fixtures
- [ ] **Coverage**: ≥ 95% en analytics/, ≥ 90% en services/, ≥ 80% en data/
- [ ] **Lint**: `ruff check .` pasa sin warnings
- [ ] **Format**: `ruff format --check .` pasa

---

## 9. Firmas de Validación

| Rol | Nombre | Fecha | Firma |
|-----|--------|-------|-------|
| Arquitecto | — | — | — |
| Desarrollador | — | — | — |
| QA | — | — | — |

*Este checklist se marca como completado cuando todos los ítems están ✅ y los criterios de aceptación pasan.*