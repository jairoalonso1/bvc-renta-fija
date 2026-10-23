# bvc-renta-fija

Librería Python para extraer datos de **renta fija** de la Bolsa de Valores de Colombia (BVC).

Soporta los tres segmentos del Mercado Electrónico Colombiano (MEC):

- Deuda pública – segmento público
- Deuda pública – segmento privado
- Deuda corporativa

## Características

- Consulta por **fecha única** o **rango de fechas**
- Búsqueda por **nemotécnico**
- Historial de un título a lo largo del tiempo
- Cache local en Parquet (opcional)
- API limpia y tipada

## Instalación

```bash
pip install -e .
playwright install chromium
# Opcional para cache:
pip install pyarrow
```

## Uso rápido

```python
from bvc_renta_fija import BVCRentaFija

with BVCRentaFija(headless=True, cache_dir="./cache") as client:
    # Un día
    df = client.get_deuda_publica_segmento_publico(fecha="2025-10-15")

    # Rango de fechas (solo días hábiles)
    df = client.get_deuda_corporativa(
        fecha_inicio="2025-10-01",
        fecha_fin="2025-10-15",
    )

    # Los tres segmentos juntos
    df = client.get_todo(fecha="2025-10-15")

    # Buscar un nemotécnico concreto
    df = client.buscar(nemotecnico="TFIT08031127", fecha="2025-10-15")

    # Historial de un CDT / TES en un rango
    df = client.get_historial_nemotecnico(
        nemotecnico="TFIT08031127",
        fecha_inicio="2025-09-01",
        fecha_fin="2025-10-15",
    )
    print(df)
```

## Columnas típicas

| Nemotécnico | Emisor | Clase de título | Fecha de vencimiento | Cantidad | Volúmenes |

Cuando usas rango de fechas se agrega la columna `Fecha_consulta`.

## Créditos

Inspirado en la lógica de [dcardonav/bvc_scrapper](https://github.com/dcardonav/bvc_scrapper) (renta variable), adaptado y modernizado para renta fija con Playwright.
