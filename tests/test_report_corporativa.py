from datetime import date

import pandas as pd
import pytest

from bvc_renta_fija.client import BVCOperationsExtractionError, BVCRentaFija
from bvc_renta_fija.report import _normalizar_indice, _render_corporativa, enriquecer, generar_reporte_corporativa, generar_reporte_html


def test_normaliza_indices_ausentes_y_codigos():
    assert _normalizar_indice("FS") == "FS"
    assert _normalizar_indice("ipc") == "IPC"
    assert _normalizar_indice(" IB1 ") == "IB1"
    assert _normalizar_indice("-") == "Sin índice"
    assert _normalizar_indice(None) == "Sin índice"


def test_reporte_corporativo_incluye_grafica_columna_indice_y_diagnostico():
    raw = pd.DataFrame({
        "Nemotécnico": ["CDTBOCS0V", "BBOC1199B120", "CDTBBO80", "CDTTEST0"],
        "Emisor": ["BANCO DE OCCIDENTE", "BANCO DE OCCIDENTE", "BANCO DE BOGOTA", "EMISOR TEST"],
        "Clase de título": [
            "CERT DEPOSITO TERMINO BANCARIO", "BONOS ORDINARIOS",
            "CERT DEPOSITO TERMINO BANCARIO", "CERTIFICADO",
        ],
        "Fecha de vencimiento": ["2027/03/12", "2029/09/18", "2026/11/11", "2027/01/01"],
        "Periodicidad": ["Trimestral", "Trimestral", "Mensual", "Trimestral"],
        "Tasa facial": [10.4346, 2.63, 0.8, 9.0],
        "Indice": ["FS", "IPC", "IB1", "-"],
        "Cantidad": ["58000000000.00", "30000000000.00", "10000000000.00", "1000000.00"],
        "Volúmenes": ["58405420000.00", "27603150000.00", "10050800000.00", "1000000.00"],
    })
    df = enriquecer(raw, fecha_ref=date(2026, 10, 26))
    ops = pd.DataFrame([{
        "Hora": "12:07", "Tipo_operacion": "Contado", "Rueda": "TRD",
        "Nemotecnico": "CDTBOCS0V", "Precio": 99.415, "Tasa": 12.65,
        "Cantidad": 20_000_000_000, "Volúmenes": 20_138_200_000,
        "Procedencia": "Registro", "Fecha": "2026-10-26",
        "Segmento": "deuda_corporativa", "Fuente_datos": "BVC",
    }])
    html = _render_corporativa(
        df, ops, "2026-10-26",
        ops_status="Extracción completada: 1 fila de operaciones",
    )
    assert 'id="chartIndice"' in html
    assert "Volumen por índice" in html
    assert "Sin índice" in html
    assert "<th>Índice</th>" in html
    assert 'id="filtroTipoOps"' in html
    assert "Extracción completada: 1 fila de operaciones" in html
    assert "tipo_operacion" in html


def test_all_emission_query_failures_do_not_look_like_zero_trades(monkeypatch):
    client = BVCRentaFija()
    monkeypatch.setattr(
        client, "_get_identificadores_emision",
        lambda fecha, segmento: ["CDTBOCS0V-ISSUE1", "CDTBOCS0V-ISSUE2"],
    )

    def fail_query(*args, **kwargs):
        raise RuntimeError("HTTP 403 from BVC")

    monkeypatch.setattr(client, "get_operaciones_titulo", fail_query)
    with pytest.raises(BVCOperationsExtractionError, match="no se pudo verificar si hubo operaciones"):
        client.get_operaciones_dia(
            fecha="2026-10-26",
            segmento="deuda_corporativa",
            max_titulos=2,
        )
    assert client.last_operations_diagnostics["estado"] == "error"
    assert len(client.last_operations_diagnostics["consultas_fallidas"]) == 2


def test_normaliza_pestanas_actuales_de_bvc_y_mantiene_simultaneo_legacy():
    payload = {"data": {"tab": {
        "contado": [{"time": "12:07", "board": "TRD", "price": 99.415, "rate": 12.65,
                     "quantity": 20, "volume": 21, "origin": {"es": "Registro"}}],
        "repo": [{"time": "12:10", "board": "SIM", "price": 100, "rate": 10,
                  "quantity": 5, "volume": 5, "origin": {"es": "Registro"}}],
        "ttv": [{"time": "12:12", "board": "TTV", "price": 100, "rate": 1,
                 "quantity": 4, "volume": 4, "origin": {"es": "Registro"}}],
        "simultaneo": [{"time": "12:15", "board": "SIM", "price": 101, "rate": 9,
                        "quantity": 3, "volume": 3, "origin": {"es": "Registro"}}],
    }}}
    df = BVCRentaFija._normalizar_operaciones_api(
        payload,
        "CDTBOCS0V-2025121220270312QR10.4346",
        date(2026, 10, 26),
        "deuda_corporativa",
    )
    assert list(df["Tipo_operacion"]) == ["Contado", "Simultaneo", "Repo", "TTV"]
    assert set(df["Nemotecnico"]) == {"CDTBOCS0V"}



def test_reporte_corporativo_consulta_todas_las_emisiones_por_defecto():
    import inspect

    assert inspect.signature(generar_reporte_corporativa).parameters["max_titulos_ops"].default is None
    assert inspect.signature(generar_reporte_html).parameters["max_titulos_ops"].default is None
