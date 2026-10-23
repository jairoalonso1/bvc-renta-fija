"""Genera un reporte HTML de renta fija BVC."""
from bvc_renta_fija import generar_reporte_html

def main():
    # Sesion actual, todos los segmentos
    path = generar_reporte_html(
        output_path="reporte_renta_fija.html",
        cache_dir="./cache",
    )
    print(f"Reporte generado: {path}")
    print("Abrelo en el navegador.")

    # Ejemplo con fecha y solo corporativa (CDTs):
    # path = generar_reporte_html(
    #     fecha="2025-10-15",
    #     segmento="deuda_corporativa",
    #     output_path="reporte_corporativa_2025-10-15.html",
    # )

if __name__ == "__main__":
    main()
