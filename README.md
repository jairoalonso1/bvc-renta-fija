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

## Ficha y operaciones intradía

La librería consulta las operaciones intradía desde el endpoint utilizado por el frontend oficial de BVC. Playwright inicia la sesión pública del sitio y obtiene los encabezados temporales requeridos; las filas se normalizan desde la respuesta JSON, no se reconstruyen a partir de datos sintéticos.

BVC puede tener varias emisiones y vencimientos asociados a un mismo nemotécnico corto. Use get_operaciones_nemotecnico para consolidar todas las emisiones asociadas al NEMO en una fecha, o get_operaciones_titulo con el identificador completo / slug_url para una emisión específica.

La salida de operaciones incluye Hora, Rueda, Precio, Tasa, Cantidad, Volúmenes, Procedencia, Tipo_operacion, Nemotecnico, Fecha, Segmento y Fuente_datos. Se incluyen las categorías conocidas (Contado, Simultáneo, Repo y TTV) y cualquier pestaña adicional que BVC publique como una lista de operaciones. Si la BVC no devuelve movimientos, no se inventan filas. Los errores técnicos quedan explícitos y no deben confundirse con cero operaciones.

Por defecto, get_operaciones_dia(fecha=...) consulta **todas las emisiones** identificadas por BVC para esa fecha. No existe un límite predeterminado de 12 o 15 emisiones. Puede definir max_titulos solo cuando necesite limitar expresamente la extracción.

Ejemplo de uso:

~~~python
from bvc_renta_fija import BVCRentaFija

with BVCRentaFija(headless=True, cache_dir="./cache") as client:
    # Todas las operaciones de todas las emisiones del día
    todas = client.get_operaciones_dia(
        fecha="2026-10-26",
        segmento="deuda_corporativa",
    )

    # Operaciones de todas las emisiones que corresponden a este nemotécnico
    por_nemotecnico = client.get_operaciones_nemotecnico(
        nemotecnico="CDTBBO80",
        fecha="2026-10-26",
        segmento="deuda_corporativa",
    )

    # Límite opcional y explícito
    muestra = client.get_operaciones_dia(
        fecha="2026-10-26",
        segmento="deuda_corporativa",
        max_titulos=10,
    )
~~~

Los diagnósticos de extracción se guardan en client.last_operations_diagnostics. Si fallan todas las consultas a la API, se lanza BVCOperationsExtractionError en vez de afirmar que no hubo operaciones.

## Reporte corporativo: índice y operaciones

El reporte corporativo incluye una gráfica de volumen por Índice (por ejemplo FS, IPC e IB1), una columna Índice en el listado y un filtro construido a partir de los tipos de operación que devuelve la API oficial. La sección de operaciones deja visible el estado de extracción y el número de filas recuperadas; si falla la consulta, muestra el error en lugar de presentar el fallo silenciosamente como si no hubiera movimientos.

El selector del listado de BVC (por ejemplo Contado secundario) no es lo mismo que las categorías de operaciones de una emisión. La tabla de operaciones filtra por las categorías reales retornadas por el detalle BVC, como Contado, Repo o TTV.
