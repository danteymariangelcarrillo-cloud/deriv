# QuantLab — Crash/Boom Quant Lab

Dashboard **de solo lectura** para analizar índices Crash/Boom de Deriv. El sistema
consume únicamente datos públicos de mercado (`active_symbols`, `ticks_history`,
`ticks`, `time`, `ping`): no se conecta a ninguna cuenta, no guarda credenciales,
no ejecuta operaciones y **no persiste nada en disco** (todo vive en RAM).

> **Estado: fundación en progreso.** Implementadas las utilidades del núcleo y la
> configuración tipada (`utils.py`, `config.py`, `settings.py`, `errors.py`) con
> sus tests. No existe todavía servidor FastAPI, rutas, cliente Deriv, analítica
> ni frontend. El diseño completo está en
> [`specs/002-migration-foundation/`](specs/002-migration-foundation/spec.md);
> su [`quickstart.md`](specs/002-migration-foundation/quickstart.md) describe el
> **objetivo**, no lo ya construido.

## Requisitos

- Windows 10/11 (desarrollo de referencia), Python 3.13.x y [uv](https://docs.astral.sh/uv/) ≥ 0.5
- Sin base de datos, sin servicios externos de pago y sin claves API

## Instalación y uso

```bash
uv sync --extra dev
cp .env.example .env   # opcional: todavía ningún módulo lee estas variables
uv run pytest
```

`uv run python -m quantlab` avisa de que el dashboard aún no está implementado.

## Estructura

```text
src/quantlab/       paquete (hoy: __init__, __main__, utils, config, settings, errors)
tests/unit/         142 tests rápidos (utilidades, config, errores, legacy)
tests/fixtures/     hashes SHA-256 de legacy/
legacy/             prototipo original, referencia inmutable (verificado por test)
specs/              especificación, plan, contratos y tareas
```

## Calidad

```bash
uv run ruff check .            # lint (excluye legacy/ y tooling)
uv run ruff format --check .
uv run pytest
```

El SHA-256 de `legacy/` se verifica en `tests/unit/test_legacy_integrity.py`:
cualquier edición del prototipo rompe la paridad numérica y el test falla.

## Documentación

- [VISION.md](VISION.md) — alcance, principios y decisiones
- [.specify/memory/constitution.md](.specify/memory/constitution.md) — principios no negociables
- [specs/002-migration-foundation/tasks.md](specs/002-migration-foundation/tasks.md) — plan de 44 tareas y estado real
