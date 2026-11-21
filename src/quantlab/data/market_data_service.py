"""Servicio de datos de mercado (T009).

Descarga velas paginadas desde ``DerivClient``, las normaliza con la limpieza
OHLC migrada del prototipo y produce un ``DataQualityReport``. También
descarga historial de ticks y precarga ``RAMCache``.

CERO DISCO: este módulo solo construye ``DataFrame`` y registros en RAM; no
importa ``pathlib``/``os`` ni abre archivos.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Final

import pandas as pd

from quantlab.config import TIMEFRAMES
from quantlab.data.cache import RAMCache
from quantlab.data.deriv_calls import build_request
from quantlab.data.deriv_client import DerivClient
from quantlab.data.quality import GRANULARITY_SECONDS, DataQualityReport, calculate_quality
from quantlab.data.rate_limiter import TokenBucket
from quantlab.errors import DerivAPIError
from quantlab.state import CandleRecord

__all__ = ["MarketDataService"]

#: Días de histórico por temporalidad (data-model §6.1, BOOM1000/CRASH500).
_DEFAULT_HISTORY_DAYS: Final[dict[str, int]] = {
    "1min": 30,
    "5min": 90,
    "15min": 180,
    "1h": 365,
    "4h": 730,
    "1d": 1825,
}

_CANDLE_COLUMNS: Final[list[str]] = ["datetime", "epoch", "Open", "High", "Low", "Close"]
_TICK_COLUMNS: Final[list[str]] = ["epoch", "price", "datetime", "sequence"]
_CANDLE_VALUE_COLUMNS: Final[list[str]] = ["epoch", "open", "high", "low", "close"]
_MAX_PAGE_SIZE: Final[int] = 1000
_MAX_TICK_COUNT: Final[int] = 5000
#: Tope de seguridad de páginas por llamada (rango absurdo o servidor bucle).
_MAX_PAGES: Final[int] = 2000


def _is_int(value: object) -> bool:
    """``bool`` es subclase de ``int``; no debe aceptarse como epoch/count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _empty_ticks_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "epoch": pd.Series(dtype="int64"),
            "price": pd.Series(dtype="float64"),
            "datetime": pd.Series(dtype="datetime64[ns, UTC]"),
            "sequence": pd.Series(dtype="int64"),
        }
    )


class MarketDataService:
    """Velas paginadas, normalización OHLC, calidad y precarga de caché."""

    def __init__(
        self,
        deriv_client: DerivClient,
        cache: RAMCache,
        rate_limiter: TokenBucket,
    ) -> None:
        self._client = deriv_client
        self._cache = cache
        self._rate_limiter = rate_limiter

    # ------------------------------------------------------------------ velas

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        start_epoch: int,
        end_epoch: int,
        limit: int = 1000,
    ) -> tuple[pd.DataFrame, DataQualityReport]:
        """Descarga velas de ``[start_epoch, end_epoch]`` paginando de atrás.

        Cada página pide como máximo ``limit`` velas (tope 1000) y termina en la
        vela más antigua recibida menos un segundo. Solo se detiene al alcanzar
        ``start_epoch``, al recibir una página incompleta o si no hay progreso
        temporal. No escribe en disco ni muta ``RAMCache``: la precarga es
        responsabilidad de :meth:`warm_cache`.

        Raises:
            ValueError: símbolo, temporalidad, rango o ``limit`` inválidos.
            DerivAPIError: respuesta sin lista de velas o payload rechazado.
            DerivTransportError: fallo de transporte tras reintentos.
        """
        symbol = self._validate_symbol(symbol)
        timeframe = self._validate_timeframe(timeframe)
        start_epoch, end_epoch = self._validate_range(start_epoch, end_epoch)
        page_size = self._validate_limit(limit)
        granularity = GRANULARITY_SECONDS[timeframe]

        raw_all: list[dict[str, Any]] = []
        notes: list[str] = []
        page_end = end_epoch
        previous_oldest: int | None = None
        pages = 0
        expected_pages = math.ceil((end_epoch - start_epoch + 1) / granularity / page_size) + 1
        max_pages = min(_MAX_PAGES, max(1, expected_pages))

        while pages < max_pages:
            pages += 1
            await self._rate_limiter.acquire()
            payload = build_request(
                "ticks_history_candles",
                ticks_history=symbol,
                granularity=granularity,
                count=page_size,
                start=start_epoch,
                end=page_end,
            )
            response = await self._client.request(
                payload,
                expected_types=("candles", "history"),
                expected_key="candles",
            )
            raw = response.get("candles")
            if not isinstance(raw, list):
                raise DerivAPIError(
                    "La respuesta de velas no contiene una lista 'candles'",
                    context={"symbol": symbol, "timeframe": timeframe},
                )
            if not raw:
                break

            raw_all.extend(raw)
            epochs: list[int] = []
            for candle in raw:
                try:
                    epochs.append(int(candle["epoch"]))
                except (KeyError, TypeError, ValueError):
                    continue
            if not epochs:
                notes.append("Página sin marcas epoch válidas; paginación detenida.")
                break

            oldest = min(epochs)
            if oldest <= start_epoch:
                break
            if previous_oldest is not None and oldest >= previous_oldest:
                notes.append("Paginación detenida por falta de progreso temporal.")
                break

            previous_oldest = oldest
            page_end = oldest - 1
            if len(raw) < page_size:
                break
        else:
            notes.append(f"Paginación truncada en el tope de {max_pages} páginas.")

        df = self._clean_candles(raw_all, start_epoch, end_epoch)
        duplicates, invalid = self._audit_raw(raw_all, start_epoch, end_epoch)
        report = self._build_quality_report(
            df,
            symbol=symbol,
            timeframe=timeframe,
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            duplicates_removed=duplicates,
            invalid_ohlc=invalid,
            notes=notes,
        )
        return df, report

    @staticmethod
    def _clean_candles(raw: list[dict[str, Any]], start_epoch: int, end_epoch: int) -> pd.DataFrame:
        """Limpieza OHLC migrada 1:1 del prototipo (L516-L538).

        Convierte a numérico, descarta nulos, recorta al rango, deduplica por
        ``epoch`` conservando la última vela, elimina OHLC inconsistente y
        devuelve las columnas ``datetime`` (UTC), ``epoch`` y OHLC capitalizado.
        """
        if not raw:
            return pd.DataFrame(columns=_CANDLE_COLUMNS)
        df = pd.DataFrame(raw)
        required = {"epoch", "open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(
                f"La respuesta de velas no contiene campos obligatorios: {sorted(missing)}"
            )
        for col in _CANDLE_VALUE_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=_CANDLE_VALUE_COLUMNS)
        df["epoch"] = df["epoch"].astype("int64")
        df = df[(df["epoch"] >= start_epoch) & (df["epoch"] <= end_epoch)]
        df = df.drop_duplicates(subset=["epoch"], keep="last").sort_values("epoch")
        valid = (
            (df["high"] >= df["low"])
            & (df["high"] >= df[["open", "close"]].max(axis=1))
            & (df["low"] <= df[["open", "close"]].min(axis=1))
        )
        df = df.loc[valid].copy()
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        return df[_CANDLE_COLUMNS].reset_index(drop=True)

    @staticmethod
    def _audit_raw(raw: list[dict[str, Any]], start_epoch: int, end_epoch: int) -> tuple[int, int]:
        """Cuenta duplicados e OHLC inválidos con el mismo orden que la limpieza.

        Devuelve ``(duplicados_eliminados, velas_invalidas)``. Los duplicados se
        cuentan antes de validar OHLC, igual que ``_clean_candles``.
        """
        if not raw:
            return 0, 0
        audit = pd.DataFrame(raw)
        required = {"epoch", "open", "high", "low", "close"}
        if required - set(audit.columns):
            return 0, 0
        for col in _CANDLE_VALUE_COLUMNS:
            audit[col] = pd.to_numeric(audit[col], errors="coerce")
        audit = audit.dropna(subset=_CANDLE_VALUE_COLUMNS)
        audit["epoch"] = audit["epoch"].astype("int64")
        audit = audit[(audit["epoch"] >= start_epoch) & (audit["epoch"] <= end_epoch)]
        deduped = audit.drop_duplicates(subset=["epoch"], keep="last")
        duplicates = len(audit) - len(deduped)
        valid = (
            (deduped["high"] >= deduped["low"])
            & (deduped["high"] >= deduped[["open", "close"]].max(axis=1))
            & (deduped["low"] <= deduped[["open", "close"]].min(axis=1))
        )
        invalid = int((~valid).sum())
        return duplicates, invalid

    def _build_quality_report(
        self,
        df: pd.DataFrame,
        *,
        symbol: str,
        timeframe: str,
        start_epoch: int,
        end_epoch: int,
        duplicates_removed: int,
        invalid_ohlc: int,
        notes: list[str],
    ) -> DataQualityReport:
        """Delega cobertura, huecos y estado en ``calculate_quality`` (T010)."""
        granularity = GRANULARITY_SECONDS[timeframe]
        expected_bars = max(1, (end_epoch - start_epoch) // granularity + 1)
        return calculate_quality(
            df,
            timeframe,
            expected_bars,
            duplicates_removed=duplicates_removed,
            invalid_ohlc=invalid_ohlc,
            dropped_ticks=self._cache.get_stats(symbol).dropped_ticks,
            notes=notes,
            now=self._now(),
            symbol=symbol,
        )

    # ------------------------------------------------------------------- ticks

    async def get_tick_history(
        self,
        symbol: str,
        count: int = 5000,
        start_epoch: int | None = None,
    ) -> pd.DataFrame:
        """Descarga los últimos ``count`` ticks (Deriv permite máx. 5000).

        Devuelve columnas ``epoch, price, datetime, sequence`` ordenadas por
        epoch, sin duplicados y con ``datetime`` en UTC.

        Nota de contrato: la allowlist (``forbidden-calls.md §8``) fija
        ``end: latest`` y no admite ``start`` en la petición, así que
        ``start_epoch`` se aplica como filtro posterior sobre los ticks
        recibidos, nunca modificando el payload enviado a Deriv.

        Raises:
            ValueError: símbolo, ``count`` o ``start_epoch`` inválidos.
            DerivAPIError: historial ausente o con formato inesperado.
        """
        symbol = self._validate_symbol(symbol)
        if not _is_int(count) or not 1 <= count <= _MAX_TICK_COUNT:
            raise ValueError(f"count debe ser un entero entre 1 y {_MAX_TICK_COUNT}")
        if start_epoch is not None and (not _is_int(start_epoch) or start_epoch < 0):
            raise ValueError("start_epoch debe ser un entero >= 0 o None")

        await self._rate_limiter.acquire()
        payload = build_request("ticks_history_ticks", ticks_history=symbol, count=count)
        response = await self._client.request(
            payload,
            expected_types=("history",),
            expected_key="history",
        )
        return self._ticks_frame(response.get("history"), start_epoch)

    @staticmethod
    def _ticks_frame(history: object, start_epoch: int | None) -> pd.DataFrame:
        """Normaliza ``history`` (dict prices/times o lista de ticks) a DataFrame."""
        rows: list[dict[str, Any]] = []
        if isinstance(history, dict):
            times = history.get("times")
            prices = history.get("prices")
            if not isinstance(times, list) or not isinstance(prices, list):
                raise DerivAPIError(
                    "La respuesta de historial de ticks no contiene 'times'/'prices'"
                )
            rows = [{"epoch": t, "price": p, "sequence": None} for t, p in zip(times, prices)]
        elif isinstance(history, list):
            for item in history:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "epoch": item.get("epoch", item.get("time")),
                        "price": item.get("price", item.get("quote")),
                        "sequence": item.get("sequence"),
                    }
                )
        else:
            raise DerivAPIError("La respuesta de historial de ticks no es válida")

        if not rows:
            return _empty_ticks_frame()

        df = pd.DataFrame(rows, columns=["epoch", "price", "sequence"])
        df["epoch"] = pd.to_numeric(df["epoch"], errors="coerce")
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df = df.dropna(subset=["epoch", "price"])
        if df.empty:
            return _empty_ticks_frame()

        df["epoch"] = df["epoch"].astype("int64")
        df["price"] = df["price"].astype("float64")
        if start_epoch is not None:
            df = df[df["epoch"] >= start_epoch]
            if df.empty:
                return _empty_ticks_frame()

        df = df.sort_values("epoch").drop_duplicates(subset=["epoch"], keep="last")
        df = df.reset_index(drop=True)
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
        sequences = pd.to_numeric(df["sequence"], errors="coerce")
        df["sequence"] = (
            sequences.astype("int64") if sequences.notna().all() else pd.Series(range(len(df)))
        )
        return df[_TICK_COLUMNS]

    # ------------------------------------------------------------------- caché

    async def warm_cache(
        self,
        symbol: str,
        timeframes: list[str],
        force: bool = False,
    ) -> dict[str, int]:
        """Precarga ``RAMCache`` con el histórico por defecto de cada TF.

        Si ``force=False`` y el buffer ya tiene velas, no vuelve a descargar.
        El histórico se toma de data-model §6.1 (30/90/180/365/730/1825 días).
        Devuelve ``{timeframe: velas_guardadas}`` (tras aplicar el ``maxlen``
        del buffer de ``RAMCache``).

        Con ``force=True`` se re-descarga y se **reemplaza** el buffer de velas
        (extensión ``RAMCache.replace_candles``), sin duplicar el solapamiento.
        """
        symbol = self._validate_symbol(symbol)
        if not isinstance(timeframes, list) or not timeframes:
            raise ValueError("timeframes debe ser una lista no vacía")
        for timeframe in timeframes:
            self._validate_timeframe(timeframe)

        result: dict[str, int] = {}
        for timeframe in dict.fromkeys(timeframes):
            existing = self._cache.get_candles(symbol, timeframe)
            if existing and not force:
                result[timeframe] = len(existing)
                continue

            days = _DEFAULT_HISTORY_DAYS[timeframe]
            end_epoch = self._now()
            start_epoch = end_epoch - days * 86_400
            df, _ = await self.get_candles(symbol, timeframe, start_epoch, end_epoch)
            candles = [
                CandleRecord(
                    epoch=int(row.epoch),
                    open=float(row.Open),
                    high=float(row.High),
                    low=float(row.Low),
                    close=float(row.Close),
                    timeframe=timeframe,
                )
                for row in df.itertuples(index=False)
            ]
            if force:
                self._cache.replace_candles(symbol, timeframe, candles)
            else:
                self._cache.append_candles(symbol, timeframe, candles)
            result[timeframe] = len(self._cache.get_candles(symbol, timeframe))
        return result

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _validate_symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol no puede estar vacío")
        return symbol.strip()

    @staticmethod
    def _validate_timeframe(timeframe: str) -> str:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"timeframe inválido: {timeframe!r}; válidos: {list(TIMEFRAMES)}")
        return timeframe

    @staticmethod
    def _validate_range(start_epoch: int, end_epoch: int) -> tuple[int, int]:
        if not _is_int(start_epoch) or start_epoch < 0:
            raise ValueError("start_epoch debe ser un entero >= 0")
        if not _is_int(end_epoch) or end_epoch < 0:
            raise ValueError("end_epoch debe ser un entero >= 0")
        if start_epoch > end_epoch:
            raise ValueError("start_epoch no puede ser mayor que end_epoch")
        return start_epoch, end_epoch

    @staticmethod
    def _validate_limit(limit: int) -> int:
        if not _is_int(limit) or not 1 <= limit <= _MAX_PAGE_SIZE:
            raise ValueError(f"limit debe ser un entero entre 1 y {_MAX_PAGE_SIZE}")
        return limit

    @staticmethod
    def _now() -> int:
        """Epoch actual; método sustituible en tests para determinismo."""
        return int(datetime.now(UTC).timestamp())
