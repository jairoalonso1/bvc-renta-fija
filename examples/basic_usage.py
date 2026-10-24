"""Ejemplo básico para consultar renta fija de la BVC."""
from bvc_renta_fija import BVCRentaFija


def main() -> None:
    with BVCRentaFija(headless=True, cache_dir="./cache") as client:
        df = client.get_deuda_corporativa(fecha="2026-10-22")
        print(df.head(8))
        print(f"Filas devueltas por la fuente: {len(df)}")
        operaciones = client.get_operaciones_titulo("CDTBBO80", fecha="2026-10-22", segmento="deuda_corporativa")
        if operaciones.empty:
            print("La BVC no devolvió operaciones visibles para los parámetros indicados.")
        else:
            print(operaciones.head())


if __name__ == "__main__":
    main()
