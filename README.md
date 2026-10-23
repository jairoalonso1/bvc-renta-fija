# bvc-renta-fija

Librería Python para extraer datos de **renta fija** de la Bolsa de Valores de Colombia (BVC) y generar reportes HTML visuales.

## Segmentos

- Deuda pública – segmento público
- Deuda pública – segmento privado
- Deuda corporativa

## Características

- Consulta por fecha única o rango
- Búsqueda e historial por nemotécnico
- Columnas derivadas: **Prefijo** (6 caracteres), **Familia**, **Bucket de vencimiento**
- **Reporte HTML** con gráficos (Chart.js): volumen por familia, vencimiento, prefijos (CDTBBO, CDTBCB…) y emisores
- Cache local en Parquet

## Instalación

```bash
pip install -e ".[cache]"
playwright install chromium
```

## Uso rápido — datos

```python
from bvc_renta_fija import BVCRentaFija

with BVCRentaFija(headless=True, cache_dir="./cache") as client:
    df = client.get_deuda_publica_segmento_publico(fecha="2025-10-15")
    df = client.get_todo(fecha_inicio="2025-10-01", fecha_fin="2025-10-15")
    hist = client.get_historial_nemotecnico(
        nemotecnico="TFIT08031127",
        fecha_inicio="2025-09-01",
        fecha_fin="2025-10-15",
    )
```

## Uso rápido — reporte HTML

```python
from bvc_renta_fija import generar_reporte_html

# Un día, todos los segmentos
path = generar_reporte_html(
    fecha="2025-10-15",
    output_path="reporte_2025-10-15.html",
)
print("Reporte en:", path)

# Solo deuda corporativa (donde suelen estar los CDT)
path = generar_reporte_html(
    fecha="2025-10-15",
    segmento="deuda_corporativa",
    output_path="reporte_corporativa.html",
)

# Rango de fechas
path = generar_reporte_html(
    fecha_inicio="2025-10-01",
    fecha_fin="2025-10-15",
    output_path="reporte_rango.html",
)
```

Abre el HTML en el navegador: verás KPIs, gráficos de dona/barras y tablas por familia, vencimiento, prefijo de nemotécnico (ideal para CDT) y emisores.

## Columnas base (BVC)

| Nemotécnico | Emisor | Clase de título | Fecha de vencimiento | Cantidad | Volúmenes |

## Columnas derivadas (`enriquecer`)

| Prefijo | Familia | Bucket_vencimiento | Volumen_num | Cantidad_num |

- **Prefijo**: primeros 6 caracteres del nemotécnico (`CDTBBO`, `CDTBCB`, `TFIT08`…)
- **Familia**: TES Tasa Fija, TES UVR, TIDIS, CDT, Bonos, TDA, Otros
- **Bucket**: < 1 año | 1–3 | 3–5 | 5–10 | > 10 años

## Créditos

Inspirado en [dcardonav/bvc_scrapper](https://github.com/dcardonav/bvc_scrapper), adaptado a renta fija con Playwright.
