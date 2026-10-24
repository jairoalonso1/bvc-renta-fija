# bvc-renta-fija

Librería Python para extraer datos de **renta fija** de la Bolsa de Valores de Colombia (BVC) y generar reportes HTML visuales.

## Segmentos

- Deuda pública — segmento público
- Deuda pública — segmento privado
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

path = generar_reporte_html(fecha="2025-10-15", output_path="reporte.html")
print("Reporte en:", path)
```

## Columnas base (BVC)

| Nemotécnico | Emisor | Clase de título | Fecha de vencimiento | Cantidad | Volúmenes |

## Columnas derivadas (`enriquecer`)

| Prefijo | Familia | Bucket_vencimiento | Volumen_num | Cantidad_num |

- **Prefijo**: primeros 6 caracteres del nemotécnico (`CDTBBO`, `CDTBCB`, `TFIT08`…)
- **Familia**: TES Tasa Fija, TES UVR, TIDIS, CDT, Bonos, TDA, Otros
- **Bucket**: < 1 año | 1–3 | 3–5 | 5–10 | > 10 años

## Ficha facial y operaciones de un título

La librería también puede abrir la ficha de una emisión y consultar las operaciones **Contado** desde el endpoint de mercado que usa el propio frontend oficial de BVC. Playwright se utiliza para iniciar la sesión pública del sitio y obtener los encabezados temporales requeridos; las filas se normalizan desde la respuesta JSON, no se reconstruyen a partir de datos sintéticos. En la BVC, un nemotécnico puede corresponder a varias emisiones y vencimientos; para seleccionar una emisión concreta, pase `slug_url` con la URL exacta del detalle cuando esté disponible.

```python
from bvc_renta_fija import BVCRentaFija

with BVCRentaFija(headless=True) as client:
    # Use el símbolo correcto: CDTBB080 (con cero) no es el ticker CDTBBO80 (con O).
    ficha = client.get_detalle_titulo("CDTBBO80", segmento="deuda_corporativa")
    ops = client.get_operaciones_titulo("CDTBBO80", fecha="2026-10-22", segmento="deuda_corporativa")
    print(ficha)
    print(ops)
```

`get_operaciones_titulo` devuelve las columnas `Hora`, `Rueda`, `Precio`, `Tasa`, `Cantidad`, `Volúmenes` y `Procedencia`, además de `Nemotecnico`, `Fecha`, `Segmento` y `Fuente_datos`. Solo se extraen filas que devuelve la API oficial en `data.tab.contado`; no se inventan operaciones si la BVC no devuelve movimientos. Si el sitio bloquea la sesión, no genera el encabezado temporal requerido o la API rechaza la consulta, se genera un error explícito en vez de devolver una tabla vacía que parezca un día sin operaciones.

`get_operaciones_dia(fecha=..., max_titulos=20)` ofrece una consolidación acotada por los primeros títulos únicos del listado. Para evitar carga innecesaria sobre la BVC, ajuste `max_titulos`; para una extracción exhaustiva, primero hay que confirmar límites de uso y disponibilidad de los datos de cada modalidad.

**Importante sobre la validación:** se comprobó en una sesión del sitio oficial que el endpoint devuelve tres operaciones para `TFIT16300632` el 22 de octubre de 2026 (10:46, 10:50 y 11:42). Los valores de mercado se leen en tiempo de ejecución desde la respuesta de BVC; el caso se usa en pruebas de integración, no como dato hardcodeado de producción. Algunos entornos automatizados pueden recibir un 403 de CloudFront al abrir el sitio, lo que es una restricción de acceso del entorno y no una respuesta de mercado vacía.

## Créditos

Inspirado en [dcardonav/bvc_scrapper](https://github.com/dcardonav/bvc_scrapper), adaptado a renta fija con Playwright.
