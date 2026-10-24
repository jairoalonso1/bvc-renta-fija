"""Consulta la tabla Contado de la pestaña Operaciones de una emisión BVC."""
from bvc_renta_fija import BVCRentaFija


def main() -> None:
    # La BVC distingue la letra O del cero: CDT BBO80 -> CDTBBO80.
    with BVCRentaFija(headless=True) as client:
        ficha = client.get_detalle_titulo("CDTBBO80", segmento="deuda_corporativa")
        print("Ficha facial:", ficha)
        operaciones = client.get_operaciones_titulo("CDTBBO80", fecha="2026-10-22", segmento="deuda_corporativa")
        if operaciones.empty:
            print("La BVC no devolvió filas Contado para la emisión y fecha solicitadas.")
        else:
            print(operaciones.to_string(index=False))


if __name__ == "__main__":
    main()
