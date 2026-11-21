"""Almacén en memoria de snapshots con swap atómico (T014).

CERO DISCO: no hay persistencia ni archivos temporales. Cada símbolo tiene un
``InstrumentSnapshot`` inmutable y un número de versión. El swap sustituye la
referencia del snapshot (asignación atómica en CPython) e incrementa la versión,
de modo que un lector concurrente siempre obtiene un snapshot completo ya
construido, nunca uno a medio armar (INV-M04).

``get`` y ``get_version`` son llamadas independientes: entre ambas puede ocurrir
un swap, pero cada una por separado observa un valor consistente. Quien necesite
la pareja coherente debe leer el snapshot y, si es preciso, comparar
``get_version`` antes y después.
"""

from __future__ import annotations

from quantlab.domain.snapshot import InstrumentSnapshot

__all__ = ["SnapshotStore"]


class SnapshotStore:
    """Snapshots por símbolo con versionado incremental."""

    def __init__(self) -> None:
        self._snapshots: dict[str, InstrumentSnapshot] = {}
        self._versions: dict[str, int] = {}

    def get(self, symbol: str) -> InstrumentSnapshot | None:
        """Devuelve el snapshot vigente o ``None`` si el símbolo no tiene."""
        return self._snapshots.get(symbol)

    def swap(self, symbol: str, snapshot: InstrumentSnapshot) -> int:
        """Reemplaza el snapshot y devuelve la nueva versión.

        La primera escritura de un símbolo crea la versión 1.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol no puede estar vacío")
        if not isinstance(snapshot, InstrumentSnapshot):
            raise TypeError(
                f"snapshot debe ser un InstrumentSnapshot, recibido {type(snapshot).__name__}"
            )
        version = self._versions.get(symbol, 0) + 1
        # Referencia inmutable primero; la versión se publica después. Un lector
        # que consulte get_version en medio del swap ve la versión anterior,
        # nunca un snapshot incompleto.
        self._snapshots[symbol] = snapshot
        self._versions[symbol] = version
        return version

    def get_version(self, symbol: str) -> int:
        """Versión vigente del símbolo; 0 si nunca se ha escrito."""
        return self._versions.get(symbol, 0)

    def clear(self, symbol: str) -> None:
        """Elimina el snapshot y la versión del símbolo (no falla si no existe)."""
        self._snapshots.pop(symbol, None)
        self._versions.pop(symbol, None)

    def symbols(self) -> tuple[str, ...]:
        """Símbolos con snapshot vigente, en orden alfabético."""
        return tuple(sorted(self._snapshots))

    def __len__(self) -> int:
        return len(self._snapshots)

    def __contains__(self, symbol: object) -> bool:
        return symbol in self._snapshots
