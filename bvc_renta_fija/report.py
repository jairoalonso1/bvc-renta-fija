"""
Generación de reportes HTML visuales de renta fija BVC.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from .client import BVCRentaFija, Segmento, SEGMENTO_LABELS, _parse_fecha

FechaLike = Union[str, date]


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

    # Prefijo (6 caracteres del nemotécnico) — útil para CDT (CDTBBO, CDTBCB…)
    if col_n:
        out["Prefijo"] = out[col_n].astype(str).str.upper().str[:6]
        out["Nemotecnico_norm"] = out[col_n].astype(str).str.upper().str.strip()

    # Familia a partir de clase de título + prefijo
    def _familia(row) -> str:
        clase = str(row.get(col_c, "") or "").upper()
        pref = str(row.get("Prefijo", "") or "").upper()
        if "UVR" in clase or pref.startswith("TUVT"):
            return "TES UVR"
        if "TES" in clase or pref.startswith(("TFIT", "TCO", "TFIT")):
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

    # Bucket de vencimiento
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
    else:
        out["Bucket_vencimiento"] = "Sin fecha"

    # Asegurar numéricos
    for c, name in ((col_cant, "Cantidad_num"), (col_vol, "Volumen_num")):
        if c and c in out.columns:
            s = out[c]
            if s.dtype == object:
                s = (
                    s.astype(str)
                    .str.replace(".", "", regex=False)
                    .str.replace(",", ".", regex=False)
                    .str.replace("%", "", regex=False)
                )
                out[name] = pd.to_numeric(s, errors="coerce")
            else:
                out[name] = pd.to_numeric(s, errors="coerce")
        else:
            out[name] = 0.0

    out["Cantidad_num"] = out["Cantidad_num"].fillna(0)
    out["Volumen_num"] = out["Volumen_num"].fillna(0)
    return out


def _fmt_cop(x: float) -> str:
    if pd.isna(x):
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
    g = (
        df.groupby(by, dropna=False)
        .agg(
            Titulos=("Nemotecnico_norm", "nunique") if "Nemotecnico_norm" in df.columns else ("Volumen_num", "count"),
            Volumen=("Volumen_num", "sum"),
            Cantidad=("Cantidad_num", "sum"),
        )
        .reset_index()
        .sort_values("Volumen", ascending=False)
    )
    total = g["Volumen"].sum()
    g["Participacion_%"] = (g["Volumen"] / total * 100).round(2) if total else 0
    return g


def generar_reporte_html(
    fecha: Optional[FechaLike] = None,
    fecha_inicio: Optional[FechaLike] = None,
    fecha_fin: Optional[FechaLike] = None,
    segmento: Optional[Segmento] = None,
    output_path: str | Path = "reporte_renta_fija.html",
    headless: bool = True,
    cache_dir: Optional[str | Path] = "./cache",
    top_n: int = 15,
) -> Path:
    """
    Genera un reporte HTML visual de renta fija BVC.

    Parameters
    ----------
    fecha :
        Día concreto (YYYY-MM-DD). Si se omite y no hay rango, usa el día actual de la BVC.
    fecha_inicio, fecha_fin :
        Rango de fechas (alternativa a `fecha`).
    segmento :
        Uno de: deuda_publica_segmento_publico | deuda_publica_segmento_privado | deuda_corporativa.
        Si es None, consolida los tres.
    output_path :
        Ruta del archivo HTML de salida.
    top_n :
        Cuántos emisores / prefijos mostrar en rankings.

    Returns
    -------
    Path del HTML generado.
    """
    output_path = Path(output_path)

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

    ref = _parse_fecha(fecha) if fecha else (
        _parse_fecha(fecha_fin) if fecha_fin else date.today()
    )
    df = enriquecer(df, fecha_ref=ref)

    if df.empty:
        html = _render_empty(titulo_seg, fecha, fecha_inicio, fecha_fin)
        output_path.write_text(html, encoding="utf-8")
        return output_path

    # Agregaciones
    by_familia = _agg_table(df, "Familia")
    by_bucket = _agg_table(df, "Bucket_vencimiento")
    # Orden lógico de buckets
    order_b = ["Vencido", "< 1 año", "1–3 años", "3–5 años", "5–10 años", "> 10 años", "Sin fecha"]
    by_bucket["_ord"] = by_bucket["Bucket_vencimiento"].apply(
        lambda x: order_b.index(x) if x in order_b else 99
    )
    by_bucket = by_bucket.sort_values("_ord").drop(columns="_ord")

    col_e = _find_col(df, "emisor")
    by_emisor = _agg_table(df, col_e) if col_e else pd.DataFrame()
    if not by_emisor.empty:
        by_emisor = by_emisor.head(top_n)

    by_prefijo = _agg_table(df, "Prefijo").head(top_n)

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
        detalle=df,
        top_n=top_n,
    )
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _periodo_label(fecha, fecha_inicio, fecha_fin) -> str:
    if fecha:
        return str(fecha)
    if fecha_inicio and fecha_fin:
        return f"{fecha_inicio} → {fecha_fin}"
    return "Sesión actual (BVC)"


def _render_empty(titulo_seg, fecha, fecha_inicio, fecha_fin) -> str:
    periodo = _periodo_label(fecha, fecha_inicio, fecha_fin)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Reporte Renta Fija BVC</title>
<style>body{{font-family:system-ui;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#1e293b;padding:2rem 3rem;border-radius:16px;text-align:center}}</style></head>
<body><div class="card"><h1>Sin datos</h1><p>{titulo_seg} · {periodo}</p>
<p>No se encontraron operaciones para los filtros indicados.</p></div></body></html>"""


def _chart_data(df: pd.DataFrame, label_col: str) -> tuple[list, list]:
    labels = df[label_col].astype(str).tolist()
    values = [round(float(v) / 1e9, 2) for v in df["Volumen"].tolist()]  # en miles de millones
    return labels, values


def _table_html(df: pd.DataFrame, label_col: str) -> str:
    rows = []
    for _, r in df.iterrows():
        rows.append(
            f"<tr><td>{r[label_col]}</td>"
            f"<td class=\"num\">{int(r.get('Titulos', 0))}</td>"
            f"<td class=\"num\">{_fmt_cop(r['Volumen'])}</td>"
            f"<td class=\"num\">{r.get('Participacion_%', 0):.1f}%</td></tr>"
        )
    return "\n".join(rows)


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
    detalle,
    top_n,
) -> str:
    fam_l, fam_v = _chart_data(by_familia, "Familia")
    buck_l, buck_v = _chart_data(by_bucket, "Bucket_vencimiento")
    pref_l, pref_v = _chart_data(by_prefijo, "Prefijo") if not by_prefijo.empty else ([], [])
    em_col = by_emisor.columns[0] if not by_emisor.empty else "Emisor"
    em_l, em_v = _chart_data(by_emisor, em_col) if not by_emisor.empty else ([], [])

    # Top detalle por volumen
    det = detalle.sort_values("Volumen_num", ascending=False).head(25)
    col_n = _find_col(det, "nemot") or "Nemotecnico_norm"
    col_e = _find_col(det, "emisor") or "Emisor"
    col_c = _find_col(det, "clase") or "Familia"
    col_v = _find_col(det, "venc") or "Bucket_vencimiento"

    det_rows = []
    for _, r in det.iterrows():
        det_rows.append(
            f"<tr>"
            f"<td><code>{r.get(col_n, '')}</code></td>"
            f"<td>{r.get(col_e, '')}</td>"
            f"<td>{r.get('Familia', r.get(col_c, ''))}</td>"
            f"<td>{r.get(col_v, '')}</td>"
            f"<td>{r.get('Bucket_vencimiento', '')}</td>"
            f"<td class=\"num\">{_fmt_cop(r['Volumen_num'])}</td>"
            f"</tr>"
        )

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
  --bg: #0b1220;
  --card: #111827;
  --border: #1f2937;
  --text: #e5e7eb;
  --muted: #9ca3af;
  --accent: #38bdf8;
  --accent2: #a78bfa;
  --good: #34d399;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  background: radial-gradient(1200px 600px at 10% -10%, #1e3a5f 0%, transparent 50%),
              radial-gradient(900px 500px at 100% 0%, #312e81 0%, transparent 45%),
              var(--bg);
  color: var(--text);
  line-height: 1.45;
  min-height: 100vh;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 2rem 1.25rem 3rem; }}
header {{
  display: flex; flex-wrap: wrap; justify-content: space-between; gap: 1rem;
  align-items: flex-end; margin-bottom: 1.75rem;
}}
h1 {{ margin: 0; font-size: 1.75rem; letter-spacing: -0.02em; }}
.sub {{ color: var(--muted); font-size: 0.95rem; margin-top: 0.25rem; }}
.badge {{
  background: linear-gradient(135deg, #0ea5e9, #6366f1);
  color: white; font-size: 0.75rem; font-weight: 600;
  padding: 0.35rem 0.75rem; border-radius: 999px;
}}
.kpis {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem; margin-bottom: 1.5rem;
}}
.kpi {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 14px; padding: 1.1rem 1.25rem;
}}
.kpi .label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }}
.kpi .value {{ font-size: 1.45rem; font-weight: 700; margin-top: 0.35rem; color: var(--accent); }}
.grid {{
  display: grid; grid-template-columns: 1fr 1fr; gap: 1.25rem; margin-bottom: 1.25rem;
}}
@media (max-width: 860px) {{ .grid {{ grid-template-columns: 1fr; }} }}
.card {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 16px; padding: 1.25rem 1.35rem;
}}
.card h2 {{ margin: 0 0 1rem; font-size: 1.05rem; font-weight: 600; }}
.chart-box {{ position: relative; height: 260px; }}
table {{
  width: 100%; border-collapse: collapse; font-size: 0.88rem;
}}
th, td {{ padding: 0.55rem 0.6rem; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.03em; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
code {{
  background: #1e293b; padding: 0.15rem 0.4rem; border-radius: 4px;
  font-size: 0.82rem; color: #7dd3fc;
}}
.full {{ grid-column: 1 / -1; }}
footer {{ margin-top: 2rem; color: var(--muted); font-size: 0.8rem; text-align: center; }}
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
      <h2>Volumen por familia / tipo de título</h2>
      <div class="chart-box"><canvas id="chartFamilia"></canvas></div>
    </div>
    <div class="card">
      <h2>Volumen por rango de vencimiento</h2>
      <div class="chart-box"><canvas id="chartBucket"></canvas></div>
    </div>
  </section>

  <section class="grid">
    <div class="card">
      <h2>Top prefijos de nemotécnico (6 caracteres)</h2>
      <p class="sub" style="margin-top:-0.5rem;margin-bottom:0.75rem">Útil para CDT: CDTBBO, CDTBCB, etc.</p>
      <div class="chart-box"><canvas id="chartPrefijo"></canvas></div>
      <table style="margin-top:1rem">
        <thead><tr><th>Prefijo</th><th>Títulos</th><th>Volumen</th><th>%</th></tr></thead>
        <tbody>
        {_table_html(by_prefijo, "Prefijo")}
        </tbody>
      </table>
    </div>
    <div class="card">
      <h2>Top emisores</h2>
      <div class="chart-box"><canvas id="chartEmisor"></canvas></div>
      <table style="margin-top:1rem">
        <thead><tr><th>Emisor</th><th>Títulos</th><th>Volumen</th><th>%</th></tr></thead>
        <tbody>
        {_table_html(by_emisor, em_col) if not by_emisor.empty else ""}
        </tbody>
      </table>
    </div>
  </section>

  <section class="grid">
    <div class="card full">
      <h2>Detalle por familia</h2>
      <table>
        <thead><tr><th>Familia</th><th>Títulos</th><th>Volumen</th><th>%</th></tr></thead>
        <tbody>{_table_html(by_familia, "Familia")}</tbody>
      </table>
    </div>
  </section>

  <section class="grid">
    <div class="card full">
      <h2>Top 25 operaciones por volumen</h2>
      <table>
        <thead>
          <tr>
            <th>Nemotécnico</th><th>Emisor</th><th>Familia</th>
            <th>Vencimiento</th><th>Bucket</th><th>Volumen</th>
          </tr>
        </thead>
        <tbody>
        {''.join(det_rows)}
        </tbody>
      </table>
    </div>
  </section>

  <footer>
    Fuente: Bolsa de Valores de Colombia (Mercado en Línea) · Generado con bvc-renta-fija
  </footer>
</div>

<script>
const colors = [
  '#38bdf8','#a78bfa','#34d399','#fbbf24','#f472b6',
  '#60a5fa','#c084fc','#2dd4bf','#fb923c','#e879f9'
];
const commonOpts = {{
  responsive: true,
  maintainAspectRatio: false,
  plugins: {{
    legend: {{ labels: {{ color: '#9ca3af' }} }},
    tooltip: {{
      callbacks: {{
        label: (ctx) => ' $ ' + ctx.parsed.y.toLocaleString('es-CO') + ' MM'
      }}
    }}
  }},
  scales: {{
    x: {{ ticks: {{ color: '#9ca3af' }}, grid: {{ color: '#1f2937' }} }},
    y: {{ ticks: {{ color: '#9ca3af' }}, grid: {{ color: '#1f2937' }},
         title: {{ display: true, text: 'Volumen (miles de millones COP)', color: '#9ca3af' }} }}
  }}
}};

function barChart(id, labels, data) {{
  new Chart(document.getElementById(id), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [{{ data, backgroundColor: colors, borderRadius: 6, borderSkipped: false }}]
    }},
    options: {{ ...commonOpts, plugins: {{ ...commonOpts.plugins, legend: {{ display: false }} }} }}
  }});
}}

function doughnut(id, labels, data) {{
  new Chart(document.getElementById(id), {{
    type: 'doughnut',
    data: {{
      labels,
      datasets: [{{ data, backgroundColor: colors, borderWidth: 0 }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ position: 'right', labels: {{ color: '#9ca3af', boxWidth: 12 }} }},
        tooltip: {{
          callbacks: {{
            label: (ctx) => ' $ ' + ctx.parsed.toLocaleString('es-CO') + ' MM'
          }}
        }}
      }}
    }}
  }});
}}

doughnut('chartFamilia', {json.dumps(fam_l)}, {json.dumps(fam_v)});
barChart('chartBucket', {json.dumps(buck_l)}, {json.dumps(buck_v)});
barChart('chartPrefijo', {json.dumps(pref_l)}, {json.dumps(pref_v)});
barChart('chartEmisor', {json.dumps(em_l)}, {json.dumps(em_v)});
</script>
</body>
</html>"""
