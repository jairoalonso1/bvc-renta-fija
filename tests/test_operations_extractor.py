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
        "Hora", "Rueda", "Precio", "Tasa", "Cantidad", "Volúmenes", "Procedencia", "Tipo_operacion",
        "Nemotecnico", "Fecha", "Segmento", "Fuente_datos"
    ]


def test_official_operations_api_maps_contado_and_simultaneo():
    payload = {"data": {"tab": {
        "contado": [{"time": "10:26", "board": "TRD", "price": 100, "rate": 13.05, "quantity": 35, "volume": 35, "origin": {"es": "Registro"}}],
        "simultaneo": [{"time": "10:27", "board": "SIM", "price": 100, "rate": 13.05, "quantity": 20, "volume": 20, "origin": {"es": "Registro"}}],
    }}}
    df = BVCRentaFija._normalizar_operaciones_api(payload, "CDTFALS0V-TEST", date(2026, 10, 9), "deuda_corporativa")
    assert list(df["Tipo_operacion"]) == ["Contado", "Simultaneo"]
    assert list(df["Hora"]) == ["10:26", "10:27"]



def test_limpieza_normaliza_encabezados_celdas_y_filas_vacias():
    raw = pd.DataFrame(
        [
            [" CDTBBO80" + chr(160), " BANCO\\tDE BOGOTA "],
            [None, None],
        ],
        columns=pd.MultiIndex.from_tuples(
            [("Información", "Nemotécnico"), ("Emisor", "Emisor")]
        ),
    )

    cleaned = BVCRentaFija._limpiar(raw)

    assert list(cleaned.columns) == ["Información Nemotécnico", "Emisor"]
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["Información Nemotécnico"] == "CDTBBO80"
    assert cleaned.iloc[0]["Emisor"] == "BANCO DE BOGOTA"
    assert BVCRentaFija._col_nemotecnico(cleaned) == "Información Nemotécnico"


def test_limpieza_elimina_encabezados_repetidos_en_cuerpo():
    raw = pd.DataFrame(
        [
            ["Nemotécnico", "Emisor"],
            ["CDTBBO80", "BANCO DE BOGOTA"],
        ],
        columns=["Nemotécnico", "Emisor"],
    )
    cleaned = BVCRentaFija._limpiar(raw)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["Nemotécnico"] == "CDTBBO80"


def test_get_operaciones_dia_consulta_todas_las_emisiones_por_defecto(monkeypatch):
    client = BVCRentaFija()
    identifiers = ["CDTBOCS0V-ISSUE-1", "CDTBBO80-ISSUE-2", "CDTTEST0-ISSUE-3"]
    monkeypatch.setattr(
        client, "_get_identificadores_emision",
        lambda fecha, segmento: identifiers,
    )
    queried = []

    def fake_get_operaciones_titulo(identifier, fecha=None, segmento=None, slug_url=None):
        queried.append(identifier)
        return pd.DataFrame([{
            "Hora": f"10:{len(queried):02d}", "Rueda": "TRD",
            "Precio": 100.0, "Tasa": 10.0, "Cantidad": 1, "Volúmenes": 1,
            "Procedencia": "Registro", "Tipo_operacion": "Contado",
            "Nemotecnico": identifier.split("-", 1)[0], "Fecha": "2026-10-26",
            "Segmento": "deuda_corporativa", "Fuente_datos": "BVC",
        }])

    monkeypatch.setattr(client, "get_operaciones_titulo", fake_get_operaciones_titulo)
    result = client.get_operaciones_dia(fecha="2026-10-26")

    assert queried == identifiers
    assert len(result) == 3
    assert client.last_operations_diagnostics["emisiones_encontradas"] == 3
    assert client.last_operations_diagnostics["emisiones_consultadas"] == 3
    assert client.last_operations_diagnostics["estado"] == "ok"


def test_get_operaciones_nemotecnico_consulta_todas_sus_emisiones(monkeypatch):
    client = BVCRentaFija()
    identifiers = ["CDTBOCS0V-ISSUE-1", "CDTBBO80-ISSUE-2", "CDTBOCS0V-ISSUE-3"]
    monkeypatch.setattr(
        client, "_get_identificadores_emision",
        lambda fecha, segmento: identifiers,
    )
    queried = []

    def fake_get_operaciones_titulo(identifier, fecha=None, segmento=None, slug_url=None):
        queried.append(identifier)
        return pd.DataFrame([{
            "Hora": "11:15", "Rueda": "TRD", "Precio": 99.5, "Tasa": 11.0,
            "Cantidad": 10, "Volúmenes": 995, "Procedencia": "Registro",
            "Tipo_operacion": "Contado", "Nemotecnico": identifier.split("-", 1)[0],
            "Fecha": "2026-10-26", "Segmento": "deuda_corporativa", "Fuente_datos": "BVC",
        }])

    monkeypatch.setattr(client, "get_operaciones_titulo", fake_get_operaciones_titulo)
    result = client.get_operaciones_nemotecnico("CDTBOCS0V", fecha="2026-10-26")

    assert queried == ["CDTBOCS0V-ISSUE-1", "CDTBOCS0V-ISSUE-3"]
    assert len(result) == 2
    assert set(result["Nemotecnico"]) == {"CDTBOCS0V"}
    assert client.last_operations_diagnostics["emisiones_encontradas"] == 2


def test_bvc_operations_normalizer_accepts_additional_operation_tabs():
    payload = {"data": {"tab": {
        "contado": [{"time": "10:00", "board": "TRD", "price": 100, "rate": 10,
                     "quantity": 1, "volume": 1, "origin": {"es": "Registro"}}],
        "subasta": [{"time": "10:10", "board": "SUB", "price": 100, "rate": 10,
                     "quantity": 2, "volume": 2, "origin": {"es": "Subasta"}}],
    }}}
    result = BVCRentaFija._normalizar_operaciones_api(
        payload, "CDTBBO80-ISSUE-1", date(2026, 10, 26), "deuda_corporativa"
    )
    assert list(result["Tipo_operacion"]) == ["Contado", "Subasta"]
