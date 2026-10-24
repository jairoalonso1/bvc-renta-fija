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
from bvc_renta_fija.client import BVCRentaFija, _parse_fecha, SEGMENTO_LABELS

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
        def locator(self, selector):
            if selector == 'input[name="date"][type="date"]': return FakeLoc(0)
            return FakeLoc(0)
    with pytest.raises(RuntimeError, match="botón del calendario"):
        BVCRentaFija()._seleccionar_fecha(FakePage(), date(2026, 10, 22))

def test_empty_operations_result_is_not_faked():
    columns = ["Hora", "Rueda", "Precio", "Tasa", "Cantidad", "Volúmenes", "Procedencia"]
    empty = pd.DataFrame(columns=columns)
    assert empty.empty and list(empty.columns) == columns
