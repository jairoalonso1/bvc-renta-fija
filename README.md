# bvc-renta-fija

Librería Python para extraer datos de **renta fija** de la Bolsa de Valores de Colombia (BVC).

Soporta los tres segmentos del Mercado Electrónico Colombiano (MEC):

- Deuda pública – segmento público
- Deuda pública – segmento privado
- Deuda corporativa

Permite consultar por fecha y filtrar por nemotécnico.

## Instalación

```bash
pip install -e .
playwright install chromium
```

## Uso rápido

```python
from bvc_renta_fija import BVCRentaFija

client = BVCRentaFija(headless=True)

# Segmento público (TES, TIDIS, etc.)
df = client.get_deuda_publica_segmento_publico(fecha="2025-10-15")

# Segmento privado
df = client.get_deuda_publica_segmento_privado(fecha="2025-10-15")

# Deuda corporativa
df = client.get_deuda_corporativa(fecha="2025-10-15")

# Búsqueda por nemotécnico
df = client.buscar(nemotecnico="TFIT08031127", fecha="2025-10-15")
print(df.head())
```

## Columnas típicas del DataFrame

| Nemotécnico | Emisor | Clase de título | Fecha de vencimiento | Cantidad | Volúmenes |

## Créditos

Inspirado en la lógica de [dcardonav/bvc_scrapper](https://github.com/dcardonav/bvc_scrapper) (renta variable), adaptado y modernizado para renta fija con Playwright.
