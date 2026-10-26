"""
Generación de reportes HTML visuales de renta fija BVC.

Incluye:
- Reporte general con tabla completa paginada (25 filas)
- Columnas de listado + opcional Contado (Hora, Rueda, Tasa, …)
- Reporte de deuda corporativa con visualización avanzada (Plotly)
"""
from __future__ import annotations

import json
import math
import html as html_lib
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from .client import BVCRentaFija, Segmento, SEGMENTO_LABELS, _parse_fecha

FechaLike = Union[str, date]

PAGE_SIZE = 25


def _find_col(df: pd.DataFrame, *keywords: str) -> Optional[str]:
    for c in df.columns:
        cl = str(c).lower()
        if all(k in cl for k in keywords):
            return c
    for c in df.columns:
        cl = str(c).lower()
        if any(k in cl for k in keywords):
            return c
    return None


def _normalizar_indice(value) -> str:
    """Normaliza índices BVC y agrupa guiones y ausencias."""
    try:
        if pd.isna(value):
            return "Sin índice"
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.upper() in {"", "-", "NAN", "NONE", "NULL", "N/A", "NA"}:
        return "Sin índice"
    return text.upper()


def enriquecer(df: pd.DataFrame, fecha_ref: Optional[date] = None) -> pd.DataFrame:
    """Añade columnas derivadas: Prefijo, Familia, Bucket_vencimiento, etc."""
    if df is None or df.empty:
        return df

    out = df.copy()
    col_n = _find_col(out, "nemot")
    col_e = _find_col(out, "emisor")
    col_c = _find_col(out, "clase")
    col_v = _find_col(out, "venc")
    col_cant = _find_col(out, "cant")
    col_vol = _find_col(out, "vol")

    if col_n:
        out["Prefijo"] = out[col_n].astype(str).str.upper().str[:6]
        out["Nemotecnico_norm"] = out[col_n].astype(str).str.upper().str.strip()

    def _familia(row) -> str:
        clase = str(row.get(col_c, "") or "").upper()
        pref = str(row.get("Prefijo", "") or "").upper()
        if "UVR" in clase or pref.startswith("TUVT"):
            return "TES UVR"
        if "TES" in clase or pref.startswith(("TFIT", "TCO")):
            return "TES Tasa Fija / Descuento"
        if "TIDIS" in clase or pref.startswith("TIDIS"):
            return "TIDIS"
        if "CDT" in clase or pref.startswith("CDT"):
            return "CDT"
        if "BONO" in clase or "DEUDA PUB" in clase:
            return "Bonos deuda pública"
        if "TDA" in clase or pref.startswith("TDA"):
            return "TDA (Finagro)"
        if clase and clase not in ("-", "NAN", "NONE"):
            return clase.title()
        return "Otros"

    out["Familia"] = out.apply(_familia, axis=1)

    ref = fecha_ref or date.today()

    def _bucket(val) -> str:
        if pd.isna(val):
            return "Sin fecha"
        try:
            if isinstance(val, datetime):
                v = val.date()
            elif isinstance(val, date):
                v = val
            else:
                s = str(val).strip().replace("-", "/")
                for fmt in ("%Y/%m/%d", "%d/%m/%Y", "%Y-%m-%d"):
                    try:
                        v = datetime.strptime(s, fmt).date()
                        break
                    except ValueError:
                        continue
                else:
                    return "Sin fecha"
            days = (v - ref).days
            if days < 0:
                return "Vencido"
            years = days / 365.25
            if years < 1:
                return "< 1 año"
            if years < 3:
                return "1–3 años"
            if years < 5:
                return "3–5 años"
            if years < 10:
                return "5–10 años"
            return "> 10 años"
        except Exception:
            return "Sin fecha"

    if col_v:
        out["Bucket_vencimiento"] = out[col_v].apply(_bucket)

        def _years(val):
            try:
                if isinstance(val, datetime):
                    v = val.date()
                elif isinstance(val, date):
                    v = val
                else:
                    s = str(val).strip().replace("-", "/")
                    v = None
                    for fmt in ("%Y/%m/%d", "%d/%m/%Y", "%Y-%m-%d"):
                        try:
                            v = datetime.strptime(s, fmt).date()
                            break
                        except ValueError:
                            continue
                    if v is None:
                        return None
                return round((v - ref).days / 365.25, 2)
            except Exception:
                return None

        out["Anios_vencimiento"] = out[col_v].apply(_years)

    def _num(series):
        return pd.to_numeric(
            series.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.strip(),
            errors="coerce",
        )

    out["Cantidad_num"] = _num(out[col_cant]) if col_cant else 0.0
    out["Volumen_num"] = _num(out[col_vol]) if col_vol else 0.0

    # Tipo de tasa a partir de columnas del listado (si existen)
    col_tasa = _find_col(out, "tasa")
    col_idx = _find_col(out, "indice")
    col_per = _find_col(out, "period")
    out["Indice_norm"] = out[col_idx].apply(_normalizar_indice) if col_idx else "Sin índice"

    def _tipo_tasa(row) -> str:
        tasa = str(row.get(col_tasa, "") or "").strip() if col_tasa else ""
        idx = str(row.get(col_idx, "") or "").strip() if col_idx else ""
        per = str(row.get(col_per, "") or "").strip() if col_per else ""
        fam = str(row.get("Familia", "") or "")
        if idx and idx.upper() not in ("NAN", "NONE", "-", ""):
            return f"Indexada ({idx})"
        if tasa and tasa.upper() not in ("NAN", "NONE", "-", ""):
            try:
                float(tasa.replace(",", "."))
                return "Tasa fija"
            except ValueError:
                return tasa[:40]
        if "UVR" in fam:
            return "UVR"
        if "CDT" in fam:
            return "CDT"
        if per and per.upper() not in ("NAN", "NONE", "-", ""):
            return per[:40]
        return "No clasificado"

    out["Tipo_tasa"] = out.apply(_tipo_tasa, axis=1)
    return out


def _fmt_cop(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    abs_x = abs(x)
    if abs_x >= 1e12:
        return f"$ {x/1e12:,.2f} B"
    if abs_x >= 1e9:
        return f"$ {x/1e9:,.2f} MM"
    if abs_x >= 1e6:
        return f"$ {x/1e6:,.1f} M"
    return f"$ {x:,.0f}"


def _agg_table(df: pd.DataFrame, by: str) -> pd.DataFrame:
    if by not in df.columns or df.empty:
        return pd.DataFrame()
    g = (
        df.groupby(by, dropna=False)
        .agg(
            Titulos=("Nemotecnico_norm", "nunique")
            if "Nemotecnico_norm" in df.columns
            else ("Volumen_num", "count"),
            Volumen=("Volumen_num", "sum"),
            Cantidad=("Cantidad_num", "sum"),
        )
        .reset_index()
        .sort_values("Volumen", ascending=False)
    )
    total = g["Volumen"].sum()
    g["Participacion_%"] = (g["Volumen"] / total * 100).round(2) if total else 0
    return g


def _periodo_label(fecha, fecha_inicio, fecha_fin) -> str:
    if fecha:
        return str(fecha)
    if fecha_inicio and fecha_fin:
        return f"{fecha_inicio} → {fecha_fin}"
    return "sesión actual"


def _chart_data(df: pd.DataFrame, label_col: str):
    if df is None or df.empty or label_col not in df.columns:
        return [], []
    labels = [str(x) for x in df[label_col].tolist()]
    values = [round(float(v) / 1e9, 3) for v in df["Volumen"].tolist()]  # miles de millones
    return labels, values


def _table_html(df: pd.DataFrame, label_col: str) -> str:
    if df is None or df.empty:
        return ""
    rows = []
    for _, r in df.iterrows():
        rows.append(
            f"<tr><td>{r[label_col]}</td>"
            f"<td class=\"num\">{int(r.get('Titulos', 0))}</td>"
            f"<td class=\"num\">{_fmt_cop(r['Volumen'])}</td>"
            f"<td class=\"num\">{r.get('Participacion_%', 0):.1f}%</td></tr>"
        )
    return "\n".join(rows)


def _render_empty(titulo_seg, fecha, fecha_inicio, fecha_fin) -> str:
    periodo = _periodo_label(fecha, fecha_inicio, fecha_fin)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"/><title>Reporte vacío · {periodo}</title>
<style>body{{font-family:system-ui;background:#0b1220;color:#e5e7eb;padding:2rem}}
.card{{background:#111827;border-radius:12px;padding:1.5rem;max-width:640px}}</style></head>
<body><div class="card"><h1>Sin datos</h1>
<p>{titulo_seg} · {periodo}</p>
<p>La BVC no devolvió operaciones para este periodo (día no bursátil o sin movimientos).</p>
</div></body></html>"""


def _detalle_rows_json(df: pd.DataFrame) -> list:
    """Serializa todas las filas del listado para paginación en el cliente."""
    if df is None or df.empty:
        return []
    det = df.sort_values("Volumen_num", ascending=False)
    col_n = _find_col(det, "nemot") or "Nemotecnico_norm"
    col_e = _find_col(det, "emisor") or "Emisor"
    col_v = _find_col(det, "venc") or "Bucket_vencimiento"
    col_tasa = _find_col(det, "tasa")
    rows = []
    for _, r in det.iterrows():
        rows.append({
            "nemotecnico": str(r.get(col_n, "") or ""),
            "emisor": str(r.get(col_e, "") or ""),
            "familia": str(r.get("Familia", "") or ""),
            "vencimiento": str(r.get(col_v, "") or ""),
            "bucket": str(r.get("Bucket_vencimiento", "") or ""),
            "tipo_tasa": str(r.get("Tipo_tasa", "") or ""),
            "indice": _normalizar_indice(r.get("Indice_norm", "")),
            "tasa": str(r.get(col_tasa, "") or "") if col_tasa else "",
            "cantidad": _fmt_cop(r.get("Cantidad_num", 0)),
            "volumen": _fmt_cop(r.get("Volumen_num", 0)),
            "volumen_num": float(r.get("Volumen_num", 0) or 0),
        })
    return rows


def _ops_rows_json(ops: pd.DataFrame) -> list:
    if ops is None or ops.empty:
        return []
    rows = []
    for _, r in ops.iterrows():
        rows.append({
            "hora": str(r.get("Hora", "") or ""),
            "rueda": str(r.get("Rueda", "") or ""),
            "nemotecnico": str(r.get("Nemotecnico", "") or ""),
            "tipo_operacion": str(r.get("Tipo_operacion", "Contado") or "Contado"),
            "precio": str(r.get("Precio", "") or ""),
            "tasa": str(r.get("Tasa", "") or ""),
            "cantidad": str(r.get("Cantidad", "") or ""),
            "volumen": str(r.get("Volúmenes", r.get("Volumen", "")) or ""),
            "procedencia": str(r.get("Procedencia", "") or ""),
            "segmento": str(r.get("Segmento", "") or ""),
        })
    return rows


def generar_reporte_html(
    fecha: Optional[FechaLike] = None,
    fecha_inicio: Optional[FechaLike] = None,
    fecha_fin: Optional[FechaLike] = None,
    segmento: Optional[Segmento] = None,
    output_path: str | Path = "reporte_renta_fija.html",
    headless: bool = True,
    cache_dir: Optional[str | Path] = "./cache",
    top_n: int = 15,
    include_operaciones: bool = False,
    max_titulos_ops: int = 15,
    page_size: int = PAGE_SIZE,
) -> Path:
    """
    Genera un reporte HTML visual de renta fija BVC.

    - Tabla de **todas** las filas del listado, con paginación cada `page_size` (25).
    - Si `include_operaciones=True`, consulta Contado (Hora, Rueda, Tasa, …)
      para hasta `max_titulos_ops` títulos (más lento).
    """
    output_path = Path(output_path)

    ops = pd.DataFrame()
    with BVCRentaFija(headless=headless, cache_dir=cache_dir) as client:
        if segmento:
            df = client._get(
                segmento,
                fecha=fecha,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
            )
            titulo_seg = SEGMENTO_LABELS.get(segmento, segmento)
        else:
            df = client.get_todo(
                fecha=fecha,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
            )
            titulo_seg = "Todos los segmentos"

        if include_operaciones and not df.empty:
            seg_ops = segmento or "deuda_corporativa"
            try:
                ops = client.get_operaciones_dia(
                    fecha=fecha,
                    segmento=seg_ops,
                    max_titulos=max_titulos_ops,
                )
            except Exception:
                ops = pd.DataFrame()

    ref = _parse_fecha(fecha) if fecha else (
        _parse_fecha(fecha_fin) if fecha_fin else date.today()
    )
    df = enriquecer(df, fecha_ref=ref)

    if df.empty:
        html = _render_empty(titulo_seg, fecha, fecha_inicio, fecha_fin)
        output_path.write_text(html, encoding="utf-8")
        return output_path

    by_familia = _agg_table(df, "Familia")
    by_bucket = _agg_table(df, "Bucket_vencimiento")
    order_b = ["Vencido", "< 1 año", "1–3 años", "3–5 años", "5–10 años", "> 10 años", "Sin fecha"]
    if not by_bucket.empty:
        by_bucket["_ord"] = by_bucket["Bucket_vencimiento"].apply(
            lambda x: order_b.index(x) if x in order_b else 99
        )
        by_bucket = by_bucket.sort_values("_ord").drop(columns="_ord")

    col_e = _find_col(df, "emisor")
    by_emisor = _agg_table(df, col_e) if col_e else pd.DataFrame()
    if not by_emisor.empty:
        by_emisor = by_emisor.head(top_n)
    by_prefijo = _agg_table(df, "Prefijo").head(top_n)
    by_tasa = _agg_table(df, "Tipo_tasa")

    vol_total = float(df["Volumen_num"].sum())
    cant_total = float(df["Cantidad_num"].sum())
    n_titulos = int(df["Nemotecnico_norm"].nunique()) if "Nemotecnico_norm" in df.columns else len(df)
    n_ops = len(df)
    periodo = _periodo_label(fecha, fecha_inicio, fecha_fin)

    html = _render_html(
        titulo_seg=titulo_seg,
        periodo=periodo,
        vol_total=vol_total,
        cant_total=cant_total,
        n_titulos=n_titulos,
        n_ops=n_ops,
        by_familia=by_familia,
        by_bucket=by_bucket,
        by_emisor=by_emisor,
        by_prefijo=by_prefijo,
        by_tasa=by_tasa,
        detalle=df,
        ops=ops,
        top_n=top_n,
        page_size=page_size,
    )
    output_path.write_text(html, encoding="utf-8")
    return output_path


def generar_reporte_corporativa(
    fecha: Optional[FechaLike] = None,
    output_path: str | Path = "reporte_corporativa.html",
    headless: bool = True,
    cache_dir: Optional[str | Path] = "./cache",
    max_titulos_detalle: int = 25,
    include_operaciones: bool = True,
    max_titulos_ops: int = 12,
) -> Path:
    """Genera el informe corporativo con análisis por índice y diagnóstico de operaciones."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fecha = fecha or date.today().isoformat()

    fichas = []
    ops = pd.DataFrame()
    ops_status = "No se solicitaron operaciones."
    with BVCRentaFija(headless=headless, cache_dir=cache_dir) as client:
        df = client.get_deuda_corporativa(fecha=fecha)
        df = enriquecer(df, fecha_ref=_parse_fecha(fecha))

        if not df.empty and "Nemotecnico_norm" in df.columns:
            top = (
                df.sort_values("Volumen_num", ascending=False)["Nemotecnico_norm"]
                .dropna()
                .astype(str)
                .unique()
                .tolist()[:max_titulos_detalle]
            )
            for n in top:
                try:
                    ficha = client.get_detalle_titulo(n, segmento="deuda_corporativa")
                    detalle = ficha.get("Detalle") or {}
                    tipo = (
                        detalle.get("Tipo de tasa")
                        or detalle.get("Tasa")
                        or detalle.get("Modalidad")
                        or detalle.get("Periodicidad")
                        or ""
                    )
                    fichas.append({
                        "Nemotecnico": n,
                        "Tipo_tasa_ficha": str(tipo)[:60],
                        **{f"f_{k}": v for k, v in list(detalle.items())[:12]},
                    })
                except Exception:
                    # La ficha es enriquecimiento opcional y no debe tumbar el reporte.
                    continue

        if not include_operaciones:
            ops_status = "Consulta de operaciones desactivada (include_operaciones=False)."
        elif df.empty:
            ops_status = "No se consultaron operaciones porque el listado corporativo está vacío."
        else:
            try:
                ops = client.get_operaciones_dia(
                    fecha=fecha,
                    segmento="deuda_corporativa",
                    max_titulos=max_titulos_ops,
                )
                diagnostics = getattr(client, "last_operations_diagnostics", {}) or {}
                failures = diagnostics.get("consultas_fallidas", [])
                attempted = diagnostics.get("emisiones_consultadas", 0)
                if not ops.empty:
                    ops_status = (
                        f"Extracción completada: {len(ops)} filas; "
                        f"{diagnostics.get('emisiones_con_datos', 0)} emisiones con datos "
                        f"de {attempted} consultadas."
                    )
                    if failures:
                        ops_status += f" Extracción parcial: {len(failures)} consultas fallaron."
                elif diagnostics.get("estado") == "sin_emisiones":
                    ops_status = (
                        "La BVC no devolvió identificadores de emisiones para consultar "
                        f"operaciones en la fecha {fecha}."
                    )
                else:
                    ops_status = (
                        f"La consulta terminó sin filas de operaciones para {fecha}; "
                        f"emisiones consultadas: {attempted}. Revise el diagnóstico si esperaba movimientos."
                    )
            except Exception as exc:
                diagnostics = getattr(client, "last_operations_diagnostics", {}) or {}
                ops_status = "ERROR al extraer operaciones de BVC: " + str(exc)
                if diagnostics:
                    ops_status += (
                        f" Estado={diagnostics.get('estado', 'desconocido')}; "
                        f"emisiones={diagnostics.get('emisiones_encontradas', 0)}; "
                        f"consultas fallidas={len(diagnostics.get('consultas_fallidas', []))}."
                    )
                ops = pd.DataFrame()

    if df.empty:
        html = _render_empty("Deuda corporativa", fecha, None, None)
        output_path.write_text(html, encoding="utf-8")
        return output_path

    if fichas:
        fdf = pd.DataFrame(fichas)
        df = df.merge(
            fdf[["Nemotecnico", "Tipo_tasa_ficha"]],
            left_on="Nemotecnico_norm",
            right_on="Nemotecnico",
            how="left",
        )
        df["Tipo_tasa"] = df["Tipo_tasa_ficha"].fillna(df.get("Tipo_tasa", "No clasificado"))
        df["Tipo_tasa"] = df["Tipo_tasa"].replace("", "No clasificado")

    html = _render_corporativa(df, ops, fecha, ops_status=ops_status)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _render_html(
    *,
    titulo_seg,
    periodo,
    vol_total,
    cant_total,
    n_titulos,
    n_ops,
    by_familia,
    by_bucket,
    by_emisor,
    by_prefijo,
    by_tasa,
    detalle,
    ops,
    top_n,
    page_size,
) -> str:
    fam_l, fam_v = _chart_data(by_familia, "Familia")
    buck_l, buck_v = _chart_data(by_bucket, "Bucket_vencimiento")
    pref_l, pref_v = _chart_data(by_prefijo, "Prefijo") if not by_prefijo.empty else ([], [])
    em_col = by_emisor.columns[0] if not by_emisor.empty else "Emisor"
    em_l, em_v = _chart_data(by_emisor, em_col) if not by_emisor.empty else ([], [])
    tasa_l, tasa_v = _chart_data(by_tasa, "Tipo_tasa") if not by_tasa.empty else ([], [])

    detalle_json = json.dumps(_detalle_rows_json(detalle), ensure_ascii=False)
    ops_json = json.dumps(_ops_rows_json(ops), ensure_ascii=False)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Reporte Renta Fija BVC · {periodo}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root {{
  --bg: #0b1220; --card: #111827; --border: #1f2937;
  --text: #e5e7eb; --muted: #9ca3af; --accent: #38bdf8; --accent2: #a78bfa;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
  background: radial-gradient(1200px 600px at 10% -10%, #1e3a5f 0%, transparent 50%),
              radial-gradient(900px 500px at 100% 0%, #312e81 0%, transparent 45%), var(--bg);
  color: var(--text); line-height: 1.45; min-height: 100vh;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 2rem 1.25rem 3rem; }}
header {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 1rem; align-items: flex-end; margin-bottom: 1.75rem; }}
h1 {{ margin: 0; font-size: 1.75rem; letter-spacing: -0.02em; }}
.sub {{ color: var(--muted); font-size: 0.95rem; margin-top: 0.25rem; }}
.badge {{ background: #1e293b; border: 1px solid var(--border); padding: 0.35rem 0.75rem; border-radius: 999px; font-size: 0.8rem; color: var(--muted); }}
.kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 1.5rem; }}
@media (max-width: 860px) {{ .kpis {{ grid-template-columns: 1fr 1fr; }} }}
.kpi {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 1rem 1.15rem; }}
.kpi .label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }}
.kpi .value {{ font-size: 1.35rem; font-weight: 700; margin-top: 0.35rem; color: var(--accent); }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; margin-bottom: 1.25rem; }}
@media (max-width: 860px) {{ .grid {{ grid-template-columns: 1fr; }} }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 1.25rem 1.35rem; }}
.card h2 {{ margin: 0 0 1rem; font-size: 1.05rem; font-weight: 600; }}
.chart-box {{ position: relative; height: 260px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
th, td {{ padding: 0.5rem 0.55rem; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
code {{ background: #1e293b; padding: 0.12rem 0.35rem; border-radius: 4px; font-size: 0.8rem; color: #7dd3fc; }}
.full {{ grid-column: 1 / -1; }}
.pager {{ display: flex; gap: 0.5rem; align-items: center; justify-content: flex-end; margin-top: 0.85rem; flex-wrap: wrap; }}
.pager button {{
  background: #1e293b; border: 1px solid var(--border); color: var(--text);
  padding: 0.35rem 0.75rem; border-radius: 8px; cursor: pointer;
}}
.pager button:disabled {{ opacity: 0.4; cursor: not-allowed; }}
.pager .info {{ color: var(--muted); font-size: 0.85rem; margin-right: auto; }}
footer {{ margin-top: 2rem; color: var(--muted); font-size: 0.8rem; text-align: center; }}
.tabs {{ display: flex; gap: 0.5rem; margin-bottom: 0.75rem; }}
.tabs button {{ background: transparent; border: 1px solid var(--border); color: var(--muted); padding: 0.4rem 0.9rem; border-radius: 999px; cursor: pointer; }}
.tabs button.active {{ background: #1e3a5f; color: var(--accent); border-color: #334155; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Reporte Renta Fija BVC</h1>
      <div class="sub">{titulo_seg} · {periodo}</div>
    </div>
    <span class="badge">Generado {generated}</span>
  </header>

  <section class="kpis">
    <div class="kpi"><div class="label">Volumen negociado</div><div class="value">{_fmt_cop(vol_total)}</div></div>
    <div class="kpi"><div class="label">Cantidad</div><div class="value">{_fmt_cop(cant_total)}</div></div>
    <div class="kpi"><div class="label">Títulos distintos</div><div class="value">{n_titulos:,}</div></div>
    <div class="kpi"><div class="label">Filas / operaciones</div><div class="value">{n_ops:,}</div></div>
  </section>

  <section class="grid">
    <div class="card">
      <h2>Volumen por familia</h2>
      <div class="chart-box"><canvas id="chartFamilia"></canvas></div>
    </div>
    <div class="card">
      <h2>Volumen por rango de vencimiento</h2>
      <div class="chart-box"><canvas id="chartBucket"></canvas></div>
    </div>
  </section>

  <section class="grid">
    <div class="card">
      <h2>Top prefijos</h2>
      <div class="chart-box"><canvas id="chartPrefijo"></canvas></div>
      <table style="margin-top:1rem"><thead><tr><th>Prefijo</th><th>Títulos</th><th>Volumen</th><th>%</th></tr></thead>
      <tbody>{_table_html(by_prefijo, "Prefijo")}</tbody></table>
    </div>
    <div class="card">
      <h2>Top emisores</h2>
      <div class="chart-box"><canvas id="chartEmisor"></canvas></div>
      <table style="margin-top:1rem"><thead><tr><th>Emisor</th><th>Títulos</th><th>Volumen</th><th>%</th></tr></thead>
      <tbody>{_table_html(by_emisor, em_col) if not by_emisor.empty else ""}</tbody></table>
    </div>
  </section>

  <section class="grid">
    <div class="card full">
      <h2>Volumen por tipo de tasa</h2>
      <div class="chart-box" style="height:220px"><canvas id="chartTasa"></canvas></div>
      <table style="margin-top:1rem"><thead><tr><th>Tipo de tasa</th><th>Títulos</th><th>Volumen</th><th>%</th></tr></thead>
      <tbody>{_table_html(by_tasa, "Tipo_tasa")}</tbody></table>
    </div>
  </section>

  <section class="grid">
    <div class="card full">
      <h2>Detalle del listado (todas las filas)</h2>
      <p class="sub" style="margin-top:-0.5rem">Paginado de {page_size} en {page_size}. Ordenado por volumen descendente.</p>
      <div class="tabs">
        <button type="button" class="active" id="tabListado" onclick="showTab('listado')">Listado BVC</button>
        <button type="button" id="tabOps" onclick="showTab('ops')">Operaciones Contado</button>
      </div>
      <div id="panelListado">
        <table>
          <thead>
            <tr>
              <th>Nemotécnico</th><th>Emisor</th><th>Familia</th><th>Vencimiento</th>
              <th>Bucket</th><th>Tipo tasa</th><th>Tasa</th><th>Cantidad</th><th>Volumen</th>
            </tr>
          </thead>
          <tbody id="tbodyListado"></tbody>
        </table>
        <div class="pager">
          <span class="info" id="infoListado"></span>
          <button type="button" id="prevListado" onclick="pageListado(-1)">Anterior</button>
          <button type="button" id="nextListado" onclick="pageListado(1)">Siguiente</button>
        </div>
      </div>
      <div id="panelOps" style="display:none">
        <p class="sub">Datos de Contado (Hora, Rueda, Tasa…) cuando se generó con <code>include_operaciones=True</code>.</p>
        <table>
          <thead>
            <tr>
              <th>Hora</th><th>Rueda</th><th>Nemotécnico</th><th>Precio</th>
              <th>Tasa</th><th>Cantidad</th><th>Volumen</th><th>Procedencia</th>
            </tr>
          </thead>
          <tbody id="tbodyOps"></tbody>
        </table>
        <div class="pager">
          <span class="info" id="infoOps"></span>
          <button type="button" id="prevOps" onclick="pageOps(-1)">Anterior</button>
          <button type="button" id="nextOps" onclick="pageOps(1)">Siguiente</button>
        </div>
      </div>
    </div>
  </section>

  <footer>
    Fuente: Bolsa de Valores de Colombia · Generado con bvc-renta-fija
  </footer>
</div>

<script>
const PAGE = {page_size};
const DETALLE = {detalle_json};
const OPS = {ops_json};
let pageL = 0, pageO = 0;

function showTab(which) {{
  document.getElementById('panelListado').style.display = which === 'listado' ? '' : 'none';
  document.getElementById('panelOps').style.display = which === 'ops' ? '' : 'none';
  document.getElementById('tabListado').classList.toggle('active', which === 'listado');
  document.getElementById('tabOps').classList.toggle('active', which === 'ops');
}}

const filtroTipoOps = document.getElementById('filtroTipoOps');
[...new Set(OPS.map(r => r.tipo_operacion).filter(Boolean))]
  .sort((a, b) => a.localeCompare(b, 'es'))
  .forEach(tipo => {{
    const option = document.createElement('option');
    option.value = tipo;
    option.textContent = tipo;
    filtroTipoOps.appendChild(option);
  }});
filtroTipoOps.addEventListener('change', () => {{ pageO = 0; renderOps(); }});

function renderListado() {{
  const start = pageL * PAGE;
  const slice = DETALLE.slice(start, start + PAGE);
  document.getElementById('tbodyListado').innerHTML = slice.map(r =>
    '<tr><td><code>' + r.nemotecnico + '</code></td><td>' + r.emisor + '</td><td>' + r.familia +
    '</td><td>' + r.vencimiento + '</td><td>' + r.bucket + '</td><td>' + r.indice + '</td><td>' +
    r.tipo_tasa + '</td><td class="num">' + r.cantidad + '</td><td class="num">' + r.volumen +
    '</td></tr>'
  ).join('') || '<tr><td colspan="9">Sin datos</td></tr>';
  const total = DETALLE.length;
  document.getElementById('infoListado').textContent = total
    ? 'Filas ' + (start + 1) + '–' + Math.min(start + PAGE, total) + ' de ' + total
    : 'Sin datos';
  document.getElementById('prevL').disabled = pageL <= 0;
  document.getElementById('nextL').disabled = start + PAGE >= total;
}}
function renderOps() {{
  const filtro = filtroTipoOps.value;
  const filtradas = filtro ? OPS.filter(r => r.tipo_operacion === filtro) : OPS;
  const pages = Math.max(1, Math.ceil(filtradas.length / PAGE));
  pageO = Math.min(pageO, pages - 1);
  const start = pageO * PAGE;
  const slice = filtradas.slice(start, start + PAGE);
  document.getElementById('tbodyOps').innerHTML = slice.map(r =>
    '<tr><td>' + r.hora + '</td><td>' + r.tipo_operacion + '</td><td>' + r.rueda +
    '</td><td><code>' + r.nemotecnico + '</code></td><td class="num">' + r.precio +
    '</td><td class="num">' + r.tasa + '</td><td class="num">' + r.cantidad +
    '</td><td class="num">' + r.volumen + '</td><td>' + r.procedencia + '</td></tr>'
  ).join('') || '<tr><td colspan="9">No hay operaciones para este filtro en el reporte</td></tr>';
  const total = filtradas.length;
  document.getElementById('infoOps').textContent = total
    ? 'Ops ' + (start + 1) + '–' + Math.min(start + PAGE, total) + ' de ' + total + (filtro ? ' · ' + filtro : '')
    : (OPS.length ? 'No hay operaciones para este tipo' : 'BVC no devolvió operaciones');
  document.getElementById('prevO').disabled = pageO <= 0;
  document.getElementById('nextO').disabled = start + PAGE >= total;
}}
function pageListado(d) {{ pageL = Math.max(0, pageL + d); renderListado(); }}
function pageOps(d) {{ pageO = Math.max(0, pageO + d); renderOps(); }}

renderListado();
renderOps();

const colors = ['#38bdf8','#a78bfa','#34d399','#fbbf24','#f472b6','#60a5fa','#c084fc','#2dd4bf','#fb923c','#e879f9'];
const commonOpts = {{
  responsive: true, maintainAspectRatio: false,
  plugins: {{
    legend: {{ labels: {{ color: '#9ca3af' }} }},
    tooltip: {{ callbacks: {{ label: (ctx) => ' $ ' + (ctx.parsed.y ?? ctx.parsed).toLocaleString('es-CO') + ' MM' }} }}
  }},
  scales: {{
    x: {{ ticks: {{ color: '#9ca3af' }}, grid: {{ color: '#1f2937' }} }},
    y: {{ ticks: {{ color: '#9ca3af' }}, grid: {{ color: '#1f2937' }},
         title: {{ display: true, text: 'Volumen (miles de millones COP)', color: '#9ca3af' }} }}
  }}
}};
function barChart(id, labels, data) {{
  if (!labels.length) return;
  new Chart(document.getElementById(id), {{
    type: 'bar',
    data: {{ labels, datasets: [{{ data, backgroundColor: colors, borderRadius: 6, borderSkipped: false }}] }},
    options: {{ ...commonOpts, plugins: {{ ...commonOpts.plugins, legend: {{ display: false }} }} }}
  }});
}}
function doughnut(id, labels, data) {{
  if (!labels.length) return;
  new Chart(document.getElementById(id), {{
    type: 'doughnut',
    data: {{ labels, datasets: [{{ data, backgroundColor: colors, borderWidth: 0 }}] }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ position: 'right', labels: {{ color: '#9ca3af', boxWidth: 12 }} }},
        tooltip: {{ callbacks: {{ label: (ctx) => ' $ ' + ctx.parsed.toLocaleString('es-CO') + ' MM' }} }}
      }}
    }}
  }});
}}
doughnut('chartFamilia', {json.dumps(fam_l)}, {json.dumps(fam_v)});
barChart('chartBucket', {json.dumps(buck_l)}, {json.dumps(buck_v)});
barChart('chartPrefijo', {json.dumps(pref_l)}, {json.dumps(pref_v)});
barChart('chartEmisor', {json.dumps(em_l)}, {json.dumps(em_v)});
barChart('chartTasa', {json.dumps(tasa_l)}, {json.dumps(tasa_v)});
</script>
</body>
</html>"""


def _render_corporativa(df: pd.DataFrame, ops: pd.DataFrame, fecha, ops_status: str = "") -> str:
    """HTML corporativo con Plotly: plazos × volumen × nemotécnico / tipo de tasa."""
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    if "Indice_norm" not in df.columns:
        idx_col = _find_col(df, "indice")
        df = df.copy()
        df["Indice_norm"] = df[idx_col].apply(_normalizar_indice) if idx_col else "Sin índice"
    vol_total = float(df["Volumen_num"].sum())
    n_titulos = int(df["Nemotecnico_norm"].nunique()) if "Nemotecnico_norm" in df.columns else len(df)

    # Datos para stacked bar: bucket × tipo_tasa
    pivot = (
        df.groupby(["Bucket_vencimiento", "Tipo_tasa"], dropna=False)["Volumen_num"]
        .sum()
        .reset_index()
    )
    order_b = ["Vencido", "< 1 año", "1–3 años", "3–5 años", "5–10 años", "> 10 años", "Sin fecha"]
    buckets = [b for b in order_b if b in set(pivot["Bucket_vencimiento"].astype(str))]
    tipos = sorted(pivot["Tipo_tasa"].astype(str).unique().tolist())
    series = []
    for t in tipos:
        vals = []
        sub = pivot[pivot["Tipo_tasa"].astype(str) == t]
        for b in buckets:
            hit = sub[sub["Bucket_vencimiento"].astype(str) == b]["Volumen_num"]
            vals.append(round(float(hit.sum()) / 1e9, 3) if len(hit) else 0)
        series.append({"name": t, "values": vals})

    # Bubble: x=años, y=volumen, size=cantidad, color=tipo tasa, text=nemo
    bubbles = []
    for _, r in df.iterrows():
        bubbles.append({
            "x": float(r["Anios_vencimiento"]) if pd.notna(r.get("Anios_vencimiento")) else None,
            "y": round(float(r.get("Volumen_num", 0) or 0) / 1e9, 3),
            "size": max(8, min(60, math.sqrt(max(float(r.get("Cantidad_num", 0) or 0), 0) / 1e9) * 4)),
            "nemo": str(r.get("Nemotecnico_norm", "")),
            "tipo": str(r.get("Tipo_tasa", "")),
            "emisor": str(r.get(_find_col(df, "emisor") or "Emisor", r.get("Emisor", ""))),
        })
    bubbles = [b for b in bubbles if b["x"] is not None]

    # Top nemotécnicos por volumen (barras)
    top_n = (
        df.groupby("Nemotecnico_norm", as_index=False)["Volumen_num"]
        .sum()
        .sort_values("Volumen_num", ascending=False)
        .head(20)
    )
    top_labels = top_n["Nemotecnico_norm"].tolist()
    top_vals = [round(float(v) / 1e9, 3) for v in top_n["Volumen_num"].tolist()]

    by_indice = _agg_table(df, "Indice_norm")
    index_labels = by_indice["Indice_norm"].astype(str).tolist() if not by_indice.empty else []
    index_values = [round(float(v) / 1e9, 3) for v in by_indice["Volumen"].tolist()] if not by_indice.empty else []
    index_table = _table_html(by_indice, "Indice_norm") if not by_indice.empty else '<tr><td colspan="4">Sin datos de índice</td></tr>'

    detalle_json = json.dumps(_detalle_rows_json(df), ensure_ascii=False)
    ops_json = json.dumps(_ops_rows_json(ops), ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Deuda corporativa BVC · {fecha}</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
:root {{ --bg:#0b1220; --card:#111827; --border:#1f2937; --text:#e5e7eb; --muted:#9ca3af; --accent:#38bdf8; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font-family:Segoe UI,system-ui,sans-serif; background:var(--bg); color:var(--text); }}
.wrap {{ max-width:1200px; margin:0 auto; padding:2rem 1.25rem 3rem; }}
h1 {{ margin:0 0 0.25rem; font-size:1.7rem; }}
.sub {{ color:var(--muted); margin-bottom:1.5rem; }}
.kpis {{ display:grid; grid-template-columns:repeat(3,1fr); gap:1rem; margin-bottom:1.25rem; }}
.kpi {{ background:var(--card); border:1px solid var(--border); border-radius:14px; padding:1rem 1.15rem; }}
.kpi .label {{ color:var(--muted); font-size:0.78rem; text-transform:uppercase; }}
.kpi .value {{ font-size:1.35rem; font-weight:700; color:var(--accent); margin-top:0.3rem; }}
.card {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:1.2rem; margin-bottom:1.25rem; }}
.card h2 {{ margin:0 0 0.85rem; font-size:1.05rem; }}
table {{ width:100%; border-collapse:collapse; font-size:0.84rem; }}
th,td {{ padding:0.45rem 0.5rem; border-bottom:1px solid var(--border); text-align:left; }}
th {{ color:var(--muted); font-size:0.72rem; text-transform:uppercase; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
code {{ background:#1e293b; padding:0.1rem 0.35rem; border-radius:4px; color:#7dd3fc; font-size:0.8rem; }}
.pager {{ display:flex; gap:0.5rem; align-items:center; margin-top:0.75rem; flex-wrap:wrap; }}
.pager button {{ background:#1e293b; border:1px solid var(--border); color:var(--text); padding:0.35rem 0.75rem; border-radius:8px; cursor:pointer; }}
.pager button:disabled {{ opacity:0.4; }}
.pager .info {{ color:var(--muted); font-size:0.85rem; margin-right:auto; }}
.ops-status {{ color:var(--muted); font-size:0.88rem; line-height:1.5; background:#0b1220; border:1px solid var(--border); padding:0.75rem; border-radius:8px; overflow-wrap:anywhere; }}
.ops-controls {{ display:flex; align-items:center; gap:0.65rem; margin:0.75rem 0 1rem; flex-wrap:wrap; color:var(--muted); font-size:0.85rem; }}
.ops-controls select {{ background:#0b1220; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:0.45rem 0.65rem; }}
footer {{ text-align:center; color:var(--muted); font-size:0.8rem; margin-top:1.5rem; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Deuda corporativa — BVC</h1>
  <div class="sub">Sesión {fecha} · Generado {generated}</div>

  <section class="kpis">
    <div class="kpi"><div class="label">Volumen</div><div class="value">{_fmt_cop(vol_total)}</div></div>
    <div class="kpi"><div class="label">Títulos</div><div class="value">{n_titulos}</div></div>
    <div class="kpi"><div class="label">Filas listado</div><div class="value">{len(df)}</div></div>
    <div class="kpi"><div class="label">Operaciones extraídas</div><div class="value">{len(ops) if ops is not None else 0:,}</div></div>
  </section>

  <div class="card">
    <h2>Volumen por índice</h2>
    <p class="sub">Distribución por el campo Índice (FS, IPC, IB1, etc.). Los guiones y valores ausentes se agrupan como “Sin índice”.</p>
    <div id="chartIndice" style="height:320px"></div>
    <table style="margin-top:1rem"><thead><tr><th>Índice</th><th>Títulos</th><th>Volumen</th><th>Participación</th></tr></thead>
    <tbody>{index_table}</tbody></table>
  </div>

  <div class="card">
    <h2>Volumen por plazo (eje X) y tipo de tasa (series apiladas)</h2>
    <div id="chartStacked" style="height:380px"></div>
  </div>

  <div class="card">
    <h2>Mapa de burbujas — plazo (X) × volumen (Y) · tamaño = cantidad · color = tipo de tasa</h2>
    <p class="sub" style="margin-top:-0.4rem">Cada punto es un nemotécnico. Ideal para ver concentración por vencimiento y tasa.</p>
    <div id="chartBubble" style="height:420px"></div>
  </div>

  <div class="card">
    <h2>Top 20 nemotécnicos por volumen</h2>
    <div id="chartTop" style="height:360px"></div>
  </div>

  <div class="card">
    <h2>Listado completo (paginado cada 25)</h2>
    <table>
      <thead><tr>
        <th>Nemotécnico</th><th>Emisor</th><th>Familia</th><th>Vencimiento</th>
        <th>Bucket</th><th>Índice</th><th>Tipo tasa</th><th>Cantidad</th><th>Volumen</th>
      </tr></thead>
      <tbody id="tbodyListado"></tbody>
    </table>
    <div class="pager">
      <span class="info" id="infoListado"></span>
      <button type="button" onclick="pageListado(-1)" id="prevL">Anterior</button>
      <button type="button" onclick="pageListado(1)" id="nextL">Siguiente</button>
    </div>
  </div>

  <div class="card">
    <h2>Operaciones de la emisión (BVC)</h2>
    <p class="ops-status">{html_lib.escape(ops_status or "No se generó diagnóstico para la extracción.")}</p>
    <div class="ops-controls">
      <label for="filtroTipoOps">Tipo de operación</label>
      <select id="filtroTipoOps"><option value="">Todas las operaciones</option></select>
    </div>
    <table>
      <thead><tr>
        <th>Hora</th><th>Tipo de operación</th><th>Rueda</th><th>Nemotécnico</th><th>Precio</th>
        <th>Tasa</th><th>Cantidad</th><th>Volumen</th><th>Procedencia</th>
      </tr></thead>
      <tbody id="tbodyOps"></tbody>
    </table>
    <div class="pager">
      <span class="info" id="infoOps"></span>
      <button type="button" onclick="pageOps(-1)" id="prevO">Anterior</button>
      <button type="button" onclick="pageOps(1)" id="nextO">Siguiente</button>
    </div>
  </div>

  <footer>Fuente: Bolsa de Valores de Colombia · bvc-renta-fija</footer>
</div>

<script>
const PAGE = 25;
const DETALLE = {detalle_json};
const OPS = {ops_json};
let pageL = 0, pageO = 0;

const buckets = {json.dumps(buckets, ensure_ascii=False)};
const series = {json.dumps(series, ensure_ascii=False)};
const indexLabels = {json.dumps(index_labels, ensure_ascii=False)};
const indexValues = {json.dumps(index_values)};
const bubbles = {json.dumps(bubbles, ensure_ascii=False)};
const topLabels = {json.dumps(top_labels, ensure_ascii=False)};
const topVals = {json.dumps(top_vals)};

const palette = ['#38bdf8','#a78bfa','#34d399','#fbbf24','#f472b6','#60a5fa','#c084fc','#2dd4bf','#fb923c','#e879f9','#94a3b8'];

Plotly.newPlot('chartIndice', [{{
  type: 'bar', orientation: 'h',
  y: indexLabels.slice().reverse(),
  x: indexValues.slice().reverse(),
  marker: {{ color: '#38bdf8' }},
  hovertemplate: '<b>%{{y}}</b><br>Volumen: %{{x}} miles de millones COP<extra></extra>'
}}], {{
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  font: {{ color: '#9ca3af' }},
  margin: {{ t: 10, r: 20, b: 35, l: 100 }},
  xaxis: {{ title: 'Volumen (miles de millones COP)', gridcolor: '#1f2937', zeroline: false }},
  yaxis: {{ automargin: true, gridcolor: '#1f2937' }}
}}, {{responsive: true, displayModeBar: false}});

// Stacked bars: X = plazos, stack = tipo tasa, Y = volumen
Plotly.newPlot('chartStacked', series.map((s, i) => ({{
  type: 'bar', name: s.name, x: buckets, y: s.values,
  marker: {{ color: palette[i % palette.length] }}
}})), {{
  barmode: 'stack',
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  font: {{ color: '#9ca3af' }},
  margin: {{ t: 20, r: 20, b: 60, l: 60 }},
  yaxis: {{ title: 'Volumen (miles de millones COP)', gridcolor: '#1f2937' }},
  xaxis: {{ title: 'Plazo / bucket de vencimiento', gridcolor: '#1f2937' }},
  legend: {{ orientation: 'h', y: -0.25 }}
}}, {{responsive: true, displayModeBar: false}});

// Bubbles
const byTipo = {{}};
bubbles.forEach(b => {{
  if (!byTipo[b.tipo]) byTipo[b.tipo] = {{ x: [], y: [], size: [], text: [], customdata: [] }};
  byTipo[b.tipo].x.push(b.x);
  byTipo[b.tipo].y.push(b.y);
  byTipo[b.tipo].size.push(b.size);
  byTipo[b.tipo].text.push(b.nemo);
  byTipo[b.tipo].customdata.push(b.emisor);
}});
Plotly.newPlot('chartBubble', Object.keys(byTipo).map((t, i) => ({{
  type: 'scatter', mode: 'markers', name: t,
  x: byTipo[t].x, y: byTipo[t].y,
  text: byTipo[t].text, customdata: byTipo[t].customdata,
  marker: {{ size: byTipo[t].size, color: palette[i % palette.length], opacity: 0.75, line: {{ width: 0 }} }},
  hovertemplate: '<b>%{{text}}</b><br>Emisor: %{{customdata}}<br>Plazo: %{{x:.1f}} años<br>Vol: %{{y}} MM<extra>' + t + '</extra>'
}})), {{
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  font: {{ color: '#9ca3af' }},
  margin: {{ t: 20, r: 20, b: 50, l: 60 }},
  xaxis: {{ title: 'Años a vencimiento (plazo)', gridcolor: '#1f2937', zeroline: false }},
  yaxis: {{ title: 'Volumen (miles de millones COP)', gridcolor: '#1f2937', zeroline: false }},
  legend: {{ orientation: 'h', y: -0.2 }}
}}, {{responsive: true, displayModeBar: false}});

Plotly.newPlot('chartTop', [{{
  type: 'bar', orientation: 'h',
  y: topLabels.slice().reverse(),
  x: topVals.slice().reverse(),
  marker: {{ color: '#38bdf8' }}
}}], {{
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  font: {{ color: '#9ca3af' }},
  margin: {{ t: 10, r: 20, b: 40, l: 100 }},
  xaxis: {{ title: 'Volumen (MM COP)', gridcolor: '#1f2937' }},
  yaxis: {{ gridcolor: '#1f2937' }}
}}, {{responsive: true, displayModeBar: false}});

function renderListado() {{
  const start = pageL * PAGE;
  const slice = DETALLE.slice(start, start + PAGE);
  document.getElementById('tbodyListado').innerHTML = slice.map(r => `<tr>
    <td><code>${{r.nemotecnico}}</code></td><td>${{r.emisor}}</td><td>${{r.familia}}</td>
    <td>${{r.vencimiento}}</td><td>${{r.bucket}}</td><td>${{r.tipo_tasa}}</td>
    <td class="num">${{r.cantidad}}</td><td class="num">${{r.volumen}}</td>
  </tr>`).join('') || '<tr><td colspan="8">Sin datos</td></tr>';
  const total = DETALLE.length;
  document.getElementById('infoListado').textContent = total
    ? `Filas ${{start+1}}–${{Math.min(start+PAGE,total)}} de ${{total}}`
    : 'Sin datos';
  document.getElementById('prevL').disabled = pageL <= 0;
  document.getElementById('nextL').disabled = start + PAGE >= total;
}}
function renderOps() {{
  const start = pageO * PAGE;
  const slice = OPS.slice(start, start + PAGE);
  document.getElementById('tbodyOps').innerHTML = slice.map(r => `<tr>
    <td>${{r.hora}}</td><td>${{r.rueda}}</td><td><code>${{r.nemotecnico}}</code></td>
    <td class="num">${{r.precio}}</td><td class="num">${{r.tasa}}</td>
    <td class="num">${{r.cantidad}}</td><td class="num">${{r.volumen}}</td>
    <td>${{r.procedencia}}</td>
  </tr>`).join('') || '<tr><td colspan="8">Sin operaciones Contado en este reporte</td></tr>';
  const total = OPS.length;
  document.getElementById('infoOps').textContent = total
    ? `Ops ${{start+1}}–${{Math.min(start+PAGE,total)}} de ${{total}}`
    : 'Sin operaciones Contado';
  document.getElementById('prevO').disabled = pageO <= 0;
  document.getElementById('nextO').disabled = start + PAGE >= total;
}}
function pageListado(d) {{ pageL = Math.max(0, pageL + d); renderListado(); }}
function pageOps(d) {{ pageO = Math.max(0, pageO + d); renderOps(); }}
renderListado(); renderOps();
</script>
</body>
</html>"""
