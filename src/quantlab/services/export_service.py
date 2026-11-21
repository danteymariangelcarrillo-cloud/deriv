"""Exportación en memoria de snapshots (T034/T043).

Todo se serializa en ``io.BytesIO``/``io.StringIO``: no se crea ningún archivo
temporal ni se toca disco. El CSV usa BOM UTF-8 y separador ``;`` para que Excel
en español lo abra correctamente.

El snapshot no embebe velas (sólo referencias ``candles_ref``), así que la
exportación tabular contiene lo que sí está en el contrato: estado de features,
zonas de reacción y eventos candidatos por temporalidad.
"""

from __future__ import annotations

import io
import zipfile
from typing import Final

import pandas as pd

from quantlab.domain.snapshot import InstrumentSnapshot, TimeframeAnalysis
from quantlab.services.snapshot_store import SnapshotStore

__all__ = ["ExportService"]

_MEDIA_TYPES: Final[dict[str, str]] = {
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
    "zip": "application/zip",
}
_EXTENSIONS: Final[dict[str, str]] = {"json": "json", "csv": "csv", "zip": "zip"}
#: Fecha fija en las entradas ZIP para que el binario sea reproducible.
_ZIP_DATE: Final[tuple[int, int, int, int, int, int]] = (1980, 1, 1, 0, 0, 0)


class ExportService:
    """Genera JSON/CSV/ZIP de un snapshot publicado, sin escribir en disco."""

    def __init__(self, snapshot_store: SnapshotStore) -> None:
        self._store = snapshot_store

    # ------------------------------- formatos -------------------------------

    @staticmethod
    def get_media_type(format: str) -> str:
        try:
            return _MEDIA_TYPES[format]
        except KeyError:
            raise ValueError(f"formato de export no válido: {format!r}") from None

    @staticmethod
    def get_filename(symbol: str, format: str) -> str:
        try:
            extension = _EXTENSIONS[format]
        except KeyError:
            raise ValueError(f"formato de export no válido: {format!r}") from None
        return f"{symbol}_snapshot.{extension}"

    def export(self, symbol: str, format: str) -> bytes:
        """Despacha al exportador correspondiente y devuelve los bytes."""
        if format == "json":
            return self.export_json(symbol)
        if format == "csv":
            return self.export_csv_bytes(symbol)
        if format == "zip":
            return self.export_zip(symbol)
        raise ValueError(f"formato de export no válido: {format!r}")

    # -------------------------------- JSON ---------------------------------

    def export_json(self, symbol: str) -> bytes:
        snapshot = self._require(symbol)
        return snapshot.model_dump_json().encode("utf-8")

    # --------------------------------- CSV ---------------------------------

    def export_csv_bytes(self, symbol: str) -> bytes:
        """CSV único con una fila por feature y temporalidad (BOM + ``;``)."""
        snapshot = self._require(symbol)
        frames = [self._features_frame(analysis) for analysis in snapshot.timeframes.values()]
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        buffer = io.StringIO()
        frame.to_csv(buffer, index=False, sep=";", lineterminator="\r\n")
        return buffer.getvalue().encode("utf-8-sig")

    # --------------------------------- ZIP ---------------------------------

    def export_zip(self, symbol: str) -> bytes:
        """ZIP con ``snapshot.json`` + un CSV de features/zonas por TF."""
        snapshot = self._require(symbol)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            self._add(
                archive,
                "snapshot.json",
                snapshot.model_dump_json(indent=2).encode("utf-8"),
            )
            for timeframe, analysis in snapshot.timeframes.items():
                self._add(
                    archive,
                    f"{timeframe}/features.csv",
                    self._csv_bytes(self._features_frame(analysis)),
                )
                self._add(
                    archive,
                    f"{timeframe}/zones.csv",
                    self._csv_bytes(self._zones_frame(analysis)),
                )
        return buffer.getvalue()

    # ------------------------------- helpers -------------------------------

    def _require(self, symbol: str) -> InstrumentSnapshot:
        snapshot = self._store.get(symbol)
        if snapshot is None:
            raise KeyError(f"no hay snapshot para {symbol!r}")
        return snapshot

    @staticmethod
    def _csv_bytes(frame: pd.DataFrame) -> bytes:
        buffer = io.StringIO()
        frame.to_csv(buffer, index=False, sep=";", lineterminator="\r\n")
        return buffer.getvalue().encode("utf-8-sig")

    @staticmethod
    def _add(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
        info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, payload)

    @staticmethod
    def _features_frame(analysis: TimeframeAnalysis) -> pd.DataFrame:
        rows = [
            {
                "timeframe": analysis.timeframe,
                "analysis_id": feature.id,
                "name_es": feature.name_es,
                "category": feature.category,
                "status": feature.status,
                "sample_size": feature.sample_size,
                "min_sample": feature.min_sample,
                "reason": feature.reason or "",
            }
            for feature in (analysis.features[key] for key in sorted(analysis.features))
        ]
        columns = [
            "timeframe",
            "analysis_id",
            "name_es",
            "category",
            "status",
            "sample_size",
            "min_sample",
            "reason",
        ]
        return pd.DataFrame(rows, columns=columns)

    @staticmethod
    def _zones_frame(analysis: TimeframeAnalysis) -> pd.DataFrame:
        rows = [
            {
                "timeframe": analysis.timeframe,
                "zone_id": zone.zone_id,
                "zone_type": zone.zone_type,
                "center": zone.center,
                "lower": zone.lower,
                "upper": zone.upper,
                "evaluated_touches": zone.evaluated_touches,
                "reactions": zone.reactions,
                "reaction_rate": zone.reaction_rate,
                "p_value_raw": zone.p_value_raw,
                "p_value_adj": zone.p_value_adj,
                "status": zone.status,
            }
            for zone in analysis.zones
        ]
        columns = [
            "timeframe",
            "zone_id",
            "zone_type",
            "center",
            "lower",
            "upper",
            "evaluated_touches",
            "reactions",
            "reaction_rate",
            "p_value_raw",
            "p_value_adj",
            "status",
        ]
        return pd.DataFrame(rows, columns=columns)
