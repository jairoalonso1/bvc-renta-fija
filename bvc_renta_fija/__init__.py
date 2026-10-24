"""Cliente y reportes para el mercado de renta fija de la BVC."""

from .client import (
    BVCClient,
    BVCRentaFija,
    SEGMENTO_LABELS,
    SEGMENTO_URLS,
    Segmento,
    parse_bvc_number,
)
from .report import (
    enriquecer,
    generar_reporte_excel,
    generar_reporte_html,
    generar_reporte_comportamiento_html,
    imprimir_reporte_horario_consola,
)

__version__ = "0.3.1"

__all__ = [
    "BVCRentaFija",
    "BVCClient",
    "Segmento",
    "SEGMENTO_LABELS",
    "SEGMENTO_URLS",
    "parse_bvc_number",
    "enriquecer",
    "generar_reporte_html",
    "generar_reporte_comportamiento_html",
    "generar_reporte_excel",
    "imprimir_reporte_horario_consola",
]
