# Requirements Quality Checklist: Crash/Boom Quant Lab Dashboard

**Purpose**: Revisar que la especificación, el plan y las tareas sean completos,
consistentes, verificables y honestos estadísticamente antes de implementar.
**Created**: 2026-11-15 | **Updated**: 2026-11-16 (FastAPI, cero disco, 8 GB)
**Feature**: [spec.md](../spec.md) | [plan.md](../plan.md) | [tasks.md](../tasks.md)

**Note**: Checklist de calidad de requisitos, no de implementación. Un ítem se
marca `[x]` solo cuando el revisor confirma que el criterio está satisfecho en
los artefactos.

**Estado (2026-11-16)**: revisado contra los artefactos remediados. Los ítems de
verificación de implementación (tests y artefactos aún no creados) se marcan
durante `/speckit.implement`; no se marcan antes para no declarar evidencia
inexistente.

## Alcance y solo lectura

- [ ] CHK001 El alcance excluye explícitamente trading, cuenta, propuestas y ejecución (FR-001).
- [ ] CHK002 La allowlist de Deriv está definida y es coherente con los datos públicos (FR-001, FR-029).
- [ ] CHK003 La denylist y las pruebas de llamadas prohibidas cubren los endpoints sensibles (FR-033).
- [ ] CHK004 La UI no tiene rutas ni botones de compra/venta (FR-019).
- [ ] CHK005 El registro de instrumentos permite añadir índices sin duplicar lógica (FR-002).
- [ ] CHK006 La dirección `up`/`down` de cada índice está declarada y se usa (FR-002).

## Datos y memoria (cero disco)

- [ ] CHK007 Las ventanas por temporalidad cubren 1min–1d (FR-004).
- [ ] CHK008 La paginación, deduplicación, validación OHLC y timestamps UTC están especificados (FR-004).
- [ ] CHK009 La calidad de datos incluye cobertura, huecos, duplicados y antigüedad (FR-006).
- [ ] CHK010 No hay persistencia en disco: sin CSV, DB ni caché; el export en memoria (BytesIO) está permitido (FR-028, SC-006).
- [ ] CHK011 `export_csv` no se porta; `ExportService` en memoria lo sustituye (FR-028).
- [ ] CHK012 Los búferes en vivo usan `deque` circulares acotados (FR-026, NFR-003).
- [ ] CHK013 La fuente y el endpoint activo son visibles y auditables (FR-018, FR-031).
- [ ] CHK014 El sistema tolera fallos por instrumento sin bloquear la app (FR-030).
- [ ] CHK015 La app funciona sin base de datos ni servicios externos además de Deriv (NFR-005).

## Honestidad estadística

- [ ] CHK016 El motor estadístico se preserva 1:1 (mismas fórmulas y resultados) (FR-011, SC-002).
- [ ] CHK017 Las correcciones estadísticas (null geométrico, FDR in-sample, etc.) están documentadas como optativas (FR-009, FR-010).
- [ ] CHK018 `fit_evt_gpd` está implementada con firma documentada (FR-011).
- [ ] CHK019 `detect_volatility_regimes` está implementada con fallback CUSUM (FR-013).
- [ ] CHK020 GARCH(1,1) se calcula sobre barras temporales, no ticks (FR-012).
- [ ] CHK021 La supervivencia maneja censura y reporta `n` y mediana (FR-014).
- [ ] CHK022 ACF y diagnósticos reportan `n`, lags y banda de confianza (FR-015).
- [ ] CHK023 La probabilidad condicional se etiqueta como descriptiva e in-sample (FR-016).
- [ ] CHK024 Walk-forward usa train/test separados y mínimo 3 folds (FR-017).
- [ ] CHK025 El sign test no se vende como independiente (FR-017).
- [ ] CHK026 Toda métrica inferencial muestra `n`, IC o "N/A", y etapa (FR-021).
- [ ] CHK027 Las puertas de muestra están definidas por análisis (FR-022).
- [ ] CHK028 Los eventos se etiquetan como candidatos, no spikes oficiales (FR-023).
- [ ] CHK029 Los umbrales de eventos usan solo información anterior (`shift(1)`) (FR-007).
- [ ] CHK030 Los 4 métodos duplicados de figura están consolidados en la canónica (SC-002).

## UI, accesibilidad y español

- [ ] CHK031 La barra superior tiene botones Boom/Crash + conexión + latencia (FR-018).
- [ ] CHK032 El selector de temporalidad cubre las 6 temporalidades (FR-019).
- [ ] CHK033 El menú interno tiene las 6 secciones definidas (FR-019).
- [ ] CHK034 El estado de cada índice es independiente (FR-020).
- [ ] CHK035 La sección Configuración expone parámetros de `AnalyzerConfig` y de modelo (FR-024).
- [ ] CHK036 Guardar configuración recalcula en RAM al instante; hay botón restaurar (FR-025).
- [ ] CHK037 Hay estados explícitos de carga, vacío, error, insuficiencia y stale (NFR-010).
- [ ] CHK038 La UI y los docs están en español; el código en inglés (FR-034, SC-009).
- [ ] CHK039 La app es responsive y usable en navegador de escritorio (FR-035).
- [ ] CHK040 El render es solo de la vista activa (FR-027).

## Rendimiento y operación

- [ ] CHK041 El objetivo de RAM es < 400 MB con medición (NFR-003, SC-013).
- [ ] CHK042 El baseline de hardware es 8 GB / i3-10100F (NFR-001…NFR-003).
- [ ] CHK043 El inicio con datos en memoria tiene objetivo < 2 s (NFR-001, SC-001).
- [ ] CHK044 El snapshot inicial de ambos índices tiene objetivo < 3 min (NFR-002, SC-007).
- [ ] CHK045 El rate limit respeta 220/min de Deriv con margen y backoff (NFR-007).
- [ ] CHK046 El arranque en Windows es un solo comando y muestra la URL (NFR-004, SC-010).
- [ ] CHK047 No hay secretos, tokens ni credenciales en el repositorio (NFR-006).
- [ ] CHK048 La app usa UTC y lo muestra explícitamente (NFR-011).

## Pruebas, reproducibilidad y completitud

- [ ] CHK049 Los tests cubren estadística, ingesta, contratos y UI (FR-033, SC-011).
- [ ] CHK050 Existe un test de "cero disco" que bloquea la entrega si falla (FR-028, SC-006).
- [ ] CHK051 Existe un test de llamadas prohibidas que bloquea la entrega si falla (FR-033, SC-003).
- [ ] CHK052 Los fixtures son deterministas y no requieren datos reales (NFR-008).
- [ ] CHK053 La misma entrada y semilla producen los mismos valores (NFR-009).
- [ ] CHK054 La matriz de migración cubre el 100 % de las funciones del prototipo (SC-002).
- [ ] CHK055 `fit_evt_gpd` y `detect_volatility_regimes` tienen tarea y test (SC-002).
- [ ] CHK056 El prototipo y el notebook se preservan en `legacy/` sin pérdida (SC-002).

## Notes

- Marcar `[x]` solo tras revisión explícita de los artefactos.
- Los hallazgos deben incluir archivo y línea.
- Este checklist no sustituye `/speckit.analyze` ni la revisión de la
  constitución.
