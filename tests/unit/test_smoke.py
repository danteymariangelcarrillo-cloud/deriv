"""Smoke test del bootstrap: el paquete importa y expone su versión."""

import quantlab


def test_package_version() -> None:
    assert quantlab.__version__ == "0.1.0"
