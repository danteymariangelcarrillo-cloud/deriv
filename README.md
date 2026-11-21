# QuantLab — Crash/Boom Quant Lab

Dashboard **de solo lectura** para analizar índices Crash/Boom de Deriv. El sistema
consume únicamente datos públicos de mercado (`active_symbols`, `ticks_history`,
`ticks`, `time`, `ping`): no se conecta a ninguna cuenta, no guarda credenciales,
no ejecuta operaciones y **no persiste nada en disco** (todo vive en RAM).

> **Estado: Fase 1 completa (T000-T043).** Núcleo, datos Deriv, analítica con
> paridad legacy, fachada `AnalyticsEngine`, API FastAPI con WebSocket y export
> en memoria. El frontend sigue fuera de alcance. El diseño y el estado tarea a
> tarea están en [`specs/002-migration-foundation/`](specs/002-migration-foundation/);
> [`quickstart.md`](specs/002-migration-foundation/quickstart.md) describe el uso.

## Requisitos

- Windows 10/11 (desarrollo de referencia), Python 3.13.x y [uv](https://docs.astral.sh/uv/) ≥ 0.5
- Sin base de datos, sin servicios externos de pago y sin claves API

## Instalación y uso

```bash
uv sync --extra dev
cp .env.example .env   # opcional: configuración local
uv run pytest
```

Arranque del dashboard (Windows):

```powershell
.\scripts\run.ps1            # o: uv run python -m quantlab
```

La API queda en `http://127.0.0.1:8050` (`/api/health`, `/api/instruments`,
`/api/snapshot/{symbol}`, `/api/config/{symbol}`, `/api/live/{symbol}/status`,
`/api/export/{symbol}`) y el WebSocket en `/api/ws/{symbol}`. La caché se puede
precargar con `uv run python scripts/warm_cache.py --symbols BOOM1000 CRASH500
--timeframes 5min 1h`.

## Estructura

```text
src/quantlab/       paquete: analytics/, dashboard/, data/, domain/, live/, services/
tests/unit/         paridad numérica con legacy, dominio, servicios, scripts y utilidades
tests/contract/     snapshot, OpenAPI, protocolo WS y llamadas prohibidas
tests/integration/  app FastAPI, rutas y WebSocket
tests/performance/  benchmarks locales (marcados performance, fuera del CI rápido)
tests/fixtures/     datos deterministas y hashes SHA-256 de legacy/
legacy/             prototipo original, referencia inmutable (verificado por test)
docs/migration.md   equivalencias legacy v2.2.0 → QuantLab
specs/              especificación, plan, contratos y tareas
```

## Calidad

```bash
uv run ruff check .            # lint (excluye legacy/ y tooling)
uv run ruff format --check .
uv run pytest                  # 620 tests; 2 live opt-in se omiten
uv run pytest -m performance   # benchmarks locales
```

El SHA-256 de `legacy/` se verifica en `tests/unit/test_legacy_integrity.py`:
cualquier edición del prototipo rompe la paridad numérica y el test falla.

## Documentación

- [VISION.md](VISION.md) — alcance, principios y decisiones
- [.specify/memory/constitution.md](.specify/memory/constitution.md) — principios no negociables
- [specs/002-migration-foundation/tasks.md](specs/002-migration-foundation/tasks.md) — 44 tareas y estado real
- [docs/migration.md](docs/migration.md) — qué se migró, qué no y por qué
