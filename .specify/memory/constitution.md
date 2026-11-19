# Constitución del Proyecto Crash/Boom Quant Lab

## Principios fundamentales

### I. Solo lectura (NO NEGOCIABLE)

El sistema es exclusivamente de análisis. **Nunca** llama a `buy`, `sell`, `cancel`,
`proposal`, `proposal_open_contract`, `portfolio`, `balance`, `statement` ni a
cualquier endpoint de cuenta o de ejecución. Solo utiliza datos públicos de
mercado: `active_symbols`, `ticks_history`, `ticks`, `time` y `ping`.

No se almacenan tokens, credenciales ni identificadores de cuenta. La aplicación
no muestra botones de compra/venta ni sugiere entradas al mercado. Un test
automatizado debe fallar si aparece una llamada prohibida en el código de
producción.

### II. Honestidad estadística

Toda tasa, probabilidad o métrica inferencial se presenta con:

- tamaño de muestra (`n`),
- intervalo de confianza cuando corresponda,
- etiqueta explícita **in-sample** u **out-of-sample**,
- definición del denominador y de los casos excluidos (ambiguos, pendientes,
  censurados).

Un **candidato de spike** no es un spike oficial de Deriv. No se presentan
probabilidades in-sample como predicciones ni se ocultan resultados con muestra
insuficiente: se muestra un estado "datos insuficientes" con el mínimo requerido.

**Política estadística de la Fase 1 (paridad con el prototipo):** las zonas de
reacción y el walk-forward se contrastan con `p_null = 0.5`, el mismo valor que
usa `legacy/boom_crash_quant_analyzer.py`. Benjamini-Hochberg se aplica en el
agregado walk-forward (out-of-sample), como en el prototipo; no se aplica BH
in-sample. La validación walk-forward se reporta como descriptiva cuando los
folds no son independientes.

Las correcciones pendientes —nulo geométrico `b/(a+b)` derivado de target/stop,
muestra placebo, BH in-sample, semilla de bootstrap explícita, sin backfill y
KM con censura— están **autorizadas como modo optativo**, nunca por defecto:
requieren activación explícita y tests de valores esperados (no de paridad
1e-10 con `legacy/`). Cualquier cambio de la política por defecto exige enmendar
esta constitución.

### III. Cero persistencia en disco

Los ticks crudos y los datos de mercado viven solo en memoria. No se escribe
nada en disco en tiempo de ejecución: sin caché Parquet/JSON, sin CSV, sin base
de datos y sin exportación. Los búferes en vivo son `deque` circulares acotados;
objetivo de RAM < 400 MB. La app se recalcula desde Deriv al arrancar.

### IV. Reproducibilidad

Versiones fijadas con `uv.lock`, semillas explícitas, timestamps en UTC,
fixtures versionados y resultados deterministas para los tests. Toda cifra que
aparezca en la UI debe poder recalcularse a partir de la configuración y del
snapshot correspondiente.

### V. Núcleo testeable y separado de la UI

La ingesta, la estadística y la construcción de snapshots viven en un paquete
Python independiente del servidor web y del frontend. La UI solo presenta datos
y no contiene fórmulas estadísticas. Las funciones del núcleo se prueban con
datos sintéticos y fixtures; las rutas y el WebSocket se prueban con tests de
contrato.

### VI. Transparencia y trazabilidad

La aplicación muestra fuente de datos, endpoint activo, hora UTC de última
actualización, cobertura, huecos, duplicados, ticks descartados, versión del
módulo, configuración analítica y estado de cada análisis. La página de
Metodología explica definiciones, fórmulas, requisitos de muestra y limitaciones.

### VII. Rendimiento y respeto de los límites

Se usa una conexión WebSocket persistente con multiplexación por `req_id`, token
bucket por debajo del límite de Deriv (220 llamadas/minuto) y backoff
exponencial. La UI no bloquea: los cálculos pesados se ejecutan en background y
se leen desde un snapshot inmutable. Los gráficos se limitan a unos pocos miles
de puntos mediante downsampling.

### VIII. Alcance acotado y extensible

La primera versión cubre **Crash 500** (`CRASH500`, dirección down) y
**Boom 1000** (`BOOM1000`, dirección up). El registro de instrumentos permite
añadir más índices por configuración, sin duplicar páginas ni lógica. No se
incorporan funciones de trading, multiusuario ni predicciones garantizadas.

## Restricciones adicionales

- **Stack:** Python 3.13, `uv`, FastAPI + Uvicorn + WebSockets, Plotly.js
  (frontend estático), pandas/numpy/scipy/statsmodels/arch, `websocket-client`,
  `pydantic`, pytest. Sin `plotly` de Python ni `pyarrow`.
- **Plataforma:** Windows 10/11 local; navegador moderno. La app debe arrancar
  con un comando y funcionar sin servicios externos obligatorios.
- **Almacenamiento:** solo RAM (búferes circulares `deque`); cero disco; sin base
  de datos; sin ticks crudos persistidos.
- **Idioma:** UI, mensajes y documentación en español; identificadores y nombres
  de archivos en inglés.
- **Seguridad:** sin secretos en el repositorio; allowlist de símbolos y de
  llamadas; validación de entradas; sin endpoints de trading.
- **Datos:** se respetan los términos de uso de Deriv; la app muestra atribución
  y no redistribuye datos crudos.

## Flujo de trabajo y puertas de calidad

1. Constitución una vez por proyecto.
2. Por feature: `specify → clarify → plan → checklist → tasks → analyze →
   implement → converge`.
3. No se implementa una historia sin spec y plan revisados.
4. Cada historia debe ser independientemente testeable y demostrable.
5. Los tests del núcleo estadístico, la integración con fixtures y el test de
   llamadas prohibidas son obligatorios antes de `/speckit.converge`.
6. Toda desviación de esta constitución debe justificarse en la tabla de
   Complexity Tracking del plan.

## Gobernanza

Esta constitución prevalece sobre prácticas locales y decisiones ad hoc. Las
enmiendas requieren: motivación escrita, impacto en specs/planes existentes,
actualización de la versión y aprobación del responsable del proyecto. La
versión sigue SemVer: MAJOR por cambios incompatibles de principios, MINOR por
nuevos principios o secciones, PATCH por aclaraciones.

## Registro de enmiendas

### 2.0.0 — 2026-11-18: política estadística de la Fase 1

- **Motivación**: el principio II exigía nulo geométrico y BH in-sample, en
  contradicción con `VISION.md` §13 y con `specs/002-migration-foundation/`
  (`p_null = 0.5`, BH solo walk-forward), que preservan el cálculo exacto del
  prototipo. Dos fuentes de verdad incompatibles bloqueaban T012/T024/T028.
- **Cambio**: se fija `p_null = 0.5` y BH en walk-forward como política por
  defecto; las correcciones (nulo geométrico, placebo, BH in-sample, semilla
  explícita, sin backfill, KM con censura) quedan como modo optativo con
  autorización explícita.
- **Impacto**: `specs/002-migration-foundation/{spec,plan,tasks}.md` ya cumplían
  esta política en lo esencial; se corrigen en el plan dos comentarios que aún
  decían "null geo/FDR" y "librería downsample". `specs/001-crash-boom-dashboard/`
  sigue siendo el spec padre que 002 extiende. Se aclara además que el núcleo es
  independiente del servidor web (FastAPI), no de Dash.

**Version**: 2.0.0 | **Ratified**: 2026-11-15 | **Last Amended**: 2026-11-18
