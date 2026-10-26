"""
bvc_renta_fija — Scraper y reportes de renta fija de la Bolsa de Valores de Colombia.
"""

from .client import BVCRentaFija, BVCOperationsExtractionError
from .report import generar_reporte_html, enriquecer, generar_reporte_corporativa

__version__ = "0.3.6"
__all__ = ["BVCRentaFija", "BVCOperationsExtractionError", "generar_reporte_html", "enriquecer", "generar_reporte_corporativa"]
