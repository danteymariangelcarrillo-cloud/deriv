"""Punto de entrada de ``python -m quantlab``.

Delega en el dashboard FastAPI cuando ``quantlab.dashboard.app`` está
implementado. Mientras ese módulo no exista, informa que aún no hay dashboard.
"""

from __future__ import annotations


def main() -> None:
    """Arranca el dashboard si existe; si no, lo informa."""
    try:
        from quantlab.dashboard.app import main as dashboard_main
    except ImportError:
        print("dashboard no implementado todavía")
        return

    dashboard_main()


if __name__ == "__main__":
    main()
