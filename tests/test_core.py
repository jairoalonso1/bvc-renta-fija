from datetime import date
from pathlib import Path

import pandas as pd

from bvc_renta_fija import BVCRentaFija, enriquecer, generar_reporte_excel, generar_reporte_html, parse_bvc_number


def test_parse_bvc_number_locale_formats():
    assert parse_bvc_number("20,000,000,000.00") == 20_000_000_000.0
    assert parse_bvc_number("20.000.000.000,00") == 20_000_000_000.0
    assert parse_bvc_number("99.992") == 99.992
    assert parse_bvc_number("99,992") == 99.992
    assert parse_bvc_number("12.290%") == 12.290
    assert pd.isna(parse_bvc_number(None))
    assert pd.isna(parse_bvc_number("—"))


def test_parse_fecha_and_weekdays():
    from bvc_renta_fija.client import _parse_fecha, _rango_dias_habiles
    assert _parse_fecha("23/10/2026") == date(2026, 10, 23)
    assert _rango_dias_habiles(date(2026, 10, 26), 3) == [date(2026, 10, 26), date(2026, 10, 23), date(2026, 10, 22)]
    try:
        _rango_dias_habiles(date(2026, 10, 26), 0)
    except ValueError:
        pass
    else:
        raise AssertionError("dias=0 debe producir ValueError")


def test_enriquecer_categorizes_and_parses_numeric_values():
    df = pd.DataFrame([{"Nemotécnico": "CDTBBO80", "Clase de título": "CERTIFICADO CDT", "Fecha de vencimiento": "2027/10/23", "Cantidad": "1,000,000,000.00", "Volúmenes": "998,750,000.00"}])
    out = enriquecer(df, fecha_ref=date(2026, 10, 23))
    assert out.iloc[0]["Familia"] == "CDT"
    assert out.iloc[0]["Cantidad_num"] == 1_000_000_000.0
    assert out.iloc[0]["Volumen_num"] == 998_750_000.0
    assert out.iloc[0]["Bucket_vencimiento"] == "1-3 años"


def test_html_report_escapes_untrusted_strings_and_has_usable_output(tmp_path: Path, monkeypatch):
    import bvc_renta_fija.report as report
    operations = pd.DataFrame([{"Nemotécnico": "TEST<1", "Emisor": "EMISOR <script>alert(1)</script>"}])
    monkeypatch.setattr(report.BVCRentaFija, "__enter__", lambda self: self)
    monkeypatch.setattr(report.BVCRentaFija, "__exit__", lambda self, *args: None)
    monkeypatch.setattr(report.BVCRentaFija, "get_operaciones_dia", lambda self, **kwargs: operations)
    monkeypatch.setattr(report.BVCRentaFija, "get_deuda_publica", lambda self, **kwargs: pd.DataFrame())
    output = tmp_path / "report.html"
    path = generar_reporte_html(fecha="2026-10-22", output_path=output, cache_dir=None)
    html = path.read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_excel_report_generates_three_sheets(tmp_path: Path, monkeypatch):
    import openpyxl
    import bvc_renta_fija.report as report
    monkeypatch.setattr(report.BVCRentaFija, "__enter__", lambda self: self)
    monkeypatch.setattr(report.BVCRentaFija, "__exit__", lambda self, *args: None)
    monkeypatch.setattr(report.BVCRentaFija, "get_operaciones_dia", lambda self, **kwargs: pd.DataFrame(columns=["Fecha", "Hora", "Nemotecnico"]))
    monkeypatch.setattr(report.BVCRentaFija, "get_deuda_publica", lambda self, **kwargs: pd.DataFrame([{"Nemotécnico": "TEST01"}]))
    path = generar_reporte_excel(fecha="2026-10-22", output_path=tmp_path / "report.xlsx", cache_dir=None)
    wb = openpyxl.load_workbook(path, read_only=True)
    assert wb.sheetnames == ["Resumen_Ejecutivo", "Operaciones_Por_Hora", "Detalle_Operaciones_Hora"]
    wb.close()


def test_empty_live_operations_do_not_fabricate_rows(monkeypatch):
    client = BVCRentaFija(cache_dir=None)
    monkeypatch.setattr(client, "_require_page", lambda: None)
    df = client.get_operaciones_dia(fecha="2026-10-22")
    assert df.empty
    assert list(df.columns) == ["Fecha", "Nemotecnico", "Hora", "Tasa", "Precio", "Nominal", "Giro", "Fuente_datos"]
