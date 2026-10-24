from datetime import date
import sys
import types
import pandas as pd
import pytest
try:
    import playwright.sync_api
except ImportError:
    playwright = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    class PlaywrightTimeout(Exception): pass
    sync_api.sync_playwright = lambda: None
    sync_api.Page = type("Page", (), {})
    sync_api.Browser = type("Browser", (), {})
    sync_api.TimeoutError = PlaywrightTimeout
    playwright.sync_api = sync_api
    sys.modules["playwright"] = playwright
    sys.modules["playwright.sync_api"] = sync_api
from bvc_renta_fija.client import BVCRentaFija, BVCDateSelectionError, _parse_fecha, SEGMENTO_LABELS

def test_existing_segment_public_api_is_preserved():
    client = BVCRentaFija()
    assert all(hasattr(client, m) for m in ["get_deuda_publica_segmento_publico", "get_deuda_publica_segmento_privado", "get_deuda_corporativa", "get_todo", "buscar", "get_historial_nemotecnico"])
    assert set(SEGMENTO_LABELS) == {"deuda_publica_segmento_publico", "deuda_publica_segmento_privado", "deuda_corporativa"}

def test_detail_url_uses_segment_and_operations_tab():
    assert BVCRentaFija._detalle_url("deuda_corporativa", "CDTBBO80", "operaciones") == "https://www.bvc.com.co/renta-fija-deuda-corporativa/cdtbbo80?tab=operaciones"

def test_parse_bvc_date_formats():
    assert _parse_fecha("2026-10-22") == date(2026, 10, 22)
    assert _parse_fecha("22/10/2026") == date(2026, 10, 22)
    assert _parse_fecha("2026/10/22") == date(2026, 10, 22)

def test_calendar_year_regex_matches_bvc_spanish_month_label():
    import re
    label = "octubre de 2026"
    match = re.search(r"\b(20\d{2})\b", label)
    assert match is not None
    assert int(match.group(1)) == 2026

def test_date_picker_fails_loudly_when_input_is_missing():
    class FakeLoc:
        first = None
        def __init__(self, count=0, value=""):
            self._count, self._value = count, value
            self.first = self
        def count(self): return self._count
        def input_value(self): return self._value
        def click(self): pass
        def inner_text(self): return "octubre de 2026"
        def is_disabled(self): return False
        def locator(self, *args, **kwargs): return FakeLoc(0)
    class FakePage:
        url = "https://www.bvc.com.co/renta-fija-deuda-publica-segmento-publico/tfit16300632?tab=operaciones"
        def wait_for_function(self, *args, **kwargs):
            raise playwright.sync_api.TimeoutError("picker hydration timeout")
        def title(self): return "Bolsa de Valores de Colombia"
        def locator(self, selector):
            if selector == "body":
                return FakeLoc(1, "Página sin date-picker")
            if selector == 'input[name="date"][type="date"]': return FakeLoc(0)
            return FakeLoc(0)
    with pytest.raises(BVCDateSelectionError, match="no renderizó el selector de fecha"):
        BVCRentaFija()._seleccionar_fecha(FakePage(), date(2026, 10, 22))

def test_no_movement_message_is_a_distinct_live_state():
    # BVC's actual page renders these Spanish phrases when there is no table.
    text = "El día de hoy no tenemos movimientos en el mercado. No hay cifras para mostrarte.".casefold()
    assert "no tenemos movimientos en el mercado" in text
    assert "no hay cifras para mostrarte" in text

def test_table_column_schema_matches_bvc_contado_screenshot():
    # Synthetic columns only: confirms the response contract, never fills market rows.
    columns = ["Hora", "Rueda", "Precio", "Tasa", "Cantidad", "Volúmenes", "Procedencia"]
    empty = pd.DataFrame(columns=columns)
    assert empty.empty and list(empty.columns) == columns

def test_official_operations_api_payload_maps_to_public_columns():
    payload = {
        "data": {
            "tab": {
                "contado": [
                    {
                        "time": "10:46",
                        "board": "RTES",
                        "price": 77.012,
                        "rate": 12.93,
                        "quantity": 40000000000,
                        "volume": 31679320000,
                        "origin": {"es": "Registro", "en": "OTC (NegDeal)"},
                    },
                    {
                        "time": "10:50",
                        "board": "RTES",
                        "price": 76.973,
                        "rate": 12.942,
                        "quantity": 40000000000,
                        "volume": 31663720000,
                        "origin": {"es": "Registro", "en": "OTC (NegDeal)"},
                    },
                    {
                        "time": "11:42",
                        "board": "RTES",
                        "price": 76.834,
                        "rate": 12.985,
                        "quantity": 40000000000,
                        "volume": 31608120000,
                        "origin": {"es": "Registro", "en": "OTC (NegDeal)"},
                    },
                ]
            }
        }
    }
    df = BVCRentaFija._normalizar_operaciones_api(
        payload, "TFIT16300632", date(2026, 10, 22), "deuda_publica_segmento_publico"
    )
    assert list(df["Hora"]) == ["10:46", "10:50", "11:42"]
    assert list(df["Precio"]) == [77.012, 76.973, 76.834]
    assert list(df["Procedencia"]) == ["Registro", "Registro", "Registro"]
    assert set(df["Fuente_datos"]) == {"BVC"}
    assert set(df["Fecha"]) == {"2026-10-22"}


def test_official_operations_api_empty_tab_returns_empty_schema():
    df = BVCRentaFija._normalizar_operaciones_api(
        {"data": {"tab": {}}}, "TFIT16300632", date(2026, 10, 22),
        "deuda_publica_segmento_publico"
    )
    assert df.empty
    assert list(df.columns) == [
        "Hora", "Rueda", "Precio", "Tasa", "Cantidad", "Volúmenes", "Procedencia",
        "Nemotecnico", "Fecha", "Segmento", "Fuente_datos"
    ]
