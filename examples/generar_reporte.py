"""Genera reportes HTML y Excel a partir de consultas de la BVC."""
from bvc_renta_fija import generar_reporte_excel, generar_reporte_html


def main() -> None:
    html_path = generar_reporte_html(fecha="2026-10-22", output_path="reporte_renta_fija.html", cache_dir="./cache")
    print(f"Reporte HTML generado: {html_path}")
    excel_path = generar_reporte_excel(fecha="2026-10-22", output_path="reporte_renta_fija.xlsx", cache_dir="./cache")
    print(f"Reporte Excel generado: {excel_path}")


if __name__ == "__main__":
    main()
