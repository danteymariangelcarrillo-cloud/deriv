"""Punto de entrada de ``python -m quantlab`` (T032/T035)."""

from __future__ import annotations


def main() -> None:
    """Arranca el dashboard FastAPI con la configuración de entorno."""
    from quantlab.dashboard.app import main as dashboard_main

    dashboard_main()


if __name__ == "__main__":
    main()
