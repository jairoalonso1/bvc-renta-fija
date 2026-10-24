"""Reportes de datos BVC sin convertir datos no validados en operaciones reales."""
from __future__ import annotations

import html as html_lib
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from tabulate import tabulate

from .client import BVCRentaFija, Segmento, _parse_fecha, parse_bvc_number

logger = logging.getLogger(__name__)


def _find_col(df: pd.DataFrame, *keywords: str):
    for col in df.columns:
        name = str(col).lower()
        if all(k in name for k in keywords):
            return col
    for col in df.columns:
        if any(k in str(col).lower() for k in keywords):
            return col
    return None


def enriquecer(df: pd.DataFrame, fecha_ref: date | None = None) -> pd.DataFrame:
    """Añade categorías, plazo de vencimiento y versiones numéricas de columnas."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    col_n = _find_col(out, "nemot")
    col_c = _find_col(out, "clase")
    col_v = _find_col(out, "venc")
    col_cant = _find_col(out, "cant")
    col_vol = _find_col(out, "vol")
    if col_n:
        out["Prefijo"] = out[col_n].astype(str).str.upper().str[:6]
        out["Nemotecnico_norm"] = out[col_n].astype(str).str.upper().str.strip()
    if "Familia" not in out.columns:
        def _familia(row) -> str:
            clase = str(row.get(col_c, "") or "").upper()
            pref = str(row.get("Prefijo", "") or "").upper()
            if "UVR" in clase or pref.startswith(("TUVT", "TEUV")): return "TES UVR"
            if "TES" in clase or pref.startswith(("TFIT", "TCO")): return "TES Tasa Fija"
            if "TIDIS" in clase or pref.startswith("TIDI"): return "TIDIS"
            if "CDT" in clase or pref.startswith("CDT"): return "CDT"
            if "BONO" in clase or "DEUDA PUB" in clase: return "Bonos"
            if "TDA" in clase or pref.startswith("TDA"): return "TDA (Finagro)"
            if clase and clase not in ("-", "NAN", "NONE"): return clase.title()
            return "Otros"
        out["Familia"] = out.apply(_familia, axis=1)
    ref = fecha_ref or date.today()
    def _bucket(val) -> str:
        if pd.isna(val): return "Sin fecha"
        try:
            if isinstance(val, datetime): val = val.date()
            if isinstance(val, date): venc = val
            else:
                value = str(val).strip()
                venc = None
                for fmt in ("%Y/%m/%d", "%d/%m/%Y", "%Y-%m-%d"):
                    try: venc = datetime.strptime(value, fmt).date(); break
                    except ValueError: pass
                if venc is None: return "Sin fecha"
            days = (venc - ref).days
            if days < 0: return "Vencido"
            years = days / 365.25
            if years < 1: return "< 1 año"
            if years < 3: return "1-3 años"
            if years < 5: return "3-5 años"
            if years < 10: return "5-10 años"
            return "> 10 años"
        except Exception: return "Sin fecha"
    out["Bucket_vencimiento"] = out[col_v].apply(_bucket) if col_v else "Sin fecha"
    out["Cantidad_num"] = out[col_cant].apply(parse_bvc_number) if col_cant else 0.0
    out["Volumen_num"] = out[col_vol].apply(parse_bvc_number) if col_vol else 0.0
    return out


def _fmt_cop(x: float) -> str:
    if pd.isna(x): return "-"
    if abs(x) >= 1e12: return f"${x/1e12:,.2f} B"
    if abs(x) >= 1e9: return f"${x/1e9:,.2f} MM"
    if abs(x) >= 1e6: return f"${x/1e6:,.1f} M"
    return f"${x:,.0f}"


def generar_reporte_html(fecha=None, output_path="reporte_renta_fija.html", cache_dir="./cache"):
    """Genera un reporte HTML de consulta. La ausencia de operaciones se muestra explícitamente."""
    output_path = Path(output_path)
    with BVCRentaFija(cache_dir=cache_dir) as client:
        df_ops = client.get_operaciones_dia(fecha=fecha)
        deuda = client.get_deuda_publica(fecha=fecha)
    if df_ops.empty:
        html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Reporte Renta Fija BVC</title></head><body><main><h1>Reporte de Renta Fija BVC</h1><p>Fecha consultada: {html_lib.escape(str(fecha or date.today().isoformat()))}</p><h2>Operaciones intradía</h2><p>No hay operaciones consolidadas verificables disponibles mediante la consulta actual. El reporte no sustituye datos faltantes por ejemplos ni registros codificados manualmente.</p><h2>Consulta de títulos</h2><p>Filas de deuda pública obtenidas: {len(deuda)}</p></main></body></html>"""
        output_path.write_text(html, encoding="utf-8")
        return output_path
    # Any populated operation data must come from the live BVC method.
    safe = df_ops.copy()
    safe.columns = [str(c) for c in safe.columns]
    table_html = safe.to_html(index=False, escape=True)
    html = f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Reporte Renta Fija BVC</title></head><body><h1>Reporte de Renta Fija BVC</h1><p>Fecha: {html_lib.escape(str(fecha or date.today().isoformat()))}</p>{table_html}</body></html>"
    output_path.write_text(html, encoding="utf-8")
    return output_path


def generar_reporte_excel(fecha=None, segmento=None, output_path="Reporte_Oficial_BVC.xlsx", cache_dir="./cache"):
    """Exporta resumen, operaciones y detalle; no inventa datos faltantes."""
    output_path = Path(output_path)
    with BVCRentaFija(cache_dir=cache_dir) as client:
        operaciones = client.get_operaciones_dia(fecha=fecha)
        deuda = client.get_deuda_publica(fecha=fecha)
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen_Ejecutivo"
    ws.append(["Reporte de Renta Fija BVC", "Fecha", str(fecha or date.today().isoformat())])
    ws.append(["Operaciones consolidadas verificables", len(operaciones)])
    ws.append(["Títulos de deuda pública consultados", len(deuda)])
    ws_ops = wb.create_sheet("Operaciones_Por_Hora")
    ws_ops.append(list(operaciones.columns))
    for row in operaciones.itertuples(index=False, name=None): ws_ops.append(list(row))
    ws_detail = wb.create_sheet("Detalle_Operaciones_Hora")
    ws_detail.append(list(deuda.columns))
    for row in deuda.itertuples(index=False, name=None): ws_detail.append(list(row))
    wb.save(output_path)
    return output_path


def generar_reporte_comportamiento_html(fecha=None, output_path="reporte_comportamiento_deuda_privada.html", cache_dir="./cache"):
    return generar_reporte_html(fecha=fecha, output_path=output_path, cache_dir=cache_dir)


def imprimir_reporte_horario_consola(fecha=None, segmento=None, cache_dir="./cache") -> str:
    with BVCRentaFija(cache_dir=cache_dir) as client:
        df = client.get_operaciones_dia(fecha=fecha)
    if df.empty:
        return "No hay operaciones consolidadas verificables disponibles para la fecha consultada."
    text = tabulate(df, headers="keys", tablefmt="rounded_grid", showindex=False)
    print(text)
    return text

