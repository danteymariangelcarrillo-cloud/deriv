"""ExportService en memoria (T034/T043)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from quantlab.domain.snapshot import InstrumentSnapshot
from quantlab.services.export_service import ExportService
from quantlab.services.snapshot_store import SnapshotStore
from tests.unit.test_fixtures_deterministic import FIXTURES


@pytest.fixture(scope="module")
def export_service() -> ExportService:
    payload = json.loads((FIXTURES / "snapshot_boom1000.json").read_text(encoding="utf-8"))
    store = SnapshotStore()
    store.swap("BOOM1000", InstrumentSnapshot.model_validate(payload))
    return ExportService(store)


def test_export_json_is_valid_and_complete(export_service: ExportService) -> None:
    payload = json.loads(export_service.export_json("BOOM1000"))

    assert payload["instrument"]["symbol"] == "BOOM1000"
    assert "5min" in payload["timeframes"]


def test_export_csv_has_excel_es_bom_and_semicolon(export_service: ExportService) -> None:
    data = export_service.export_csv_bytes("BOOM1000")

    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0].startswith("timeframe;analysis_id;")
    assert all(";" in line for line in lines[:5])
    assert "BOOM1000" not in lines[0]  # cabecera sin datos


def test_export_zip_contains_json_and_per_timeframe_csv(export_service: ExportService) -> None:
    data = export_service.export_zip("BOOM1000")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert "snapshot.json" in names
        assert "5min/features.csv" in names
        assert "5min/zones.csv" in names
        snapshot = json.loads(archive.read("snapshot.json"))
        assert snapshot["instrument"]["symbol"] == "BOOM1000"
        assert archive.read("5min/zones.csv").startswith(b"\xef\xbb\xbf")


def test_export_dispatch_and_metadata(export_service: ExportService) -> None:
    assert export_service.get_media_type("json") == "application/json"
    assert export_service.get_media_type("csv").startswith("text/csv")
    assert export_service.get_media_type("zip") == "application/zip"
    assert export_service.get_filename("BOOM1000", "zip") == "BOOM1000_snapshot.zip"
    assert export_service.export("BOOM1000", "json") == export_service.export_json("BOOM1000")
    with pytest.raises(ValueError):
        export_service.export("BOOM1000", "pdf")
    with pytest.raises(ValueError):
        export_service.get_media_type("pdf")


def test_export_unknown_symbol(export_service: ExportService) -> None:
    with pytest.raises(KeyError):
        export_service.export_json("CRASH500")


def test_export_zip_is_reproducible(export_service: ExportService) -> None:
    assert export_service.export_zip("BOOM1000") == export_service.export_zip("BOOM1000")
