"""Ejemplo basico de uso de bvc-renta-fija."""
from bvc_renta_fija import BVCRentaFija

def main():
    with BVCRentaFija(headless=True) as client:
        # Hoy (o ultima sesion disponible)
        df_pub = client.get_deuda_publica_segmento_publico()
        print("=== Deuda publica segmento publico ===")
        print(df_pub.head(10))
        print(f"Filas: {len(df_pub)}")

        # Busqueda de un TES concreto
        df_tes = client.buscar(nemotecnico="TFIT08031127")
        print("\n=== Busqueda TFIT08031127 ===")
        print(df_tes)

if __name__ == "__main__":
    main()
