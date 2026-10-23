"""Ejemplo de uso de bvc-renta-fija con rangos e historial."""
from bvc_renta_fija import BVCRentaFija

def main():
    with BVCRentaFija(headless=True, cache_dir="./cache") as client:
        print("=== Deuda publica segmento publico (hoy) ===")
        df = client.get_deuda_publica_segmento_publico()
        print(df.head(8))
        print(f"Filas: {len(df)}\n")

        print("=== Busqueda TFIT08031127 ===")
        df_tes = client.buscar(nemotecnico="TFIT08031127")
        print(df_tes)
        print()

        # Descomenta para probar rango (tarda mas):
        # print("=== Historial últimos 5 días hábiles ===")
        # from datetime import date, timedelta
        # fin = date.today()
        # ini = fin - timedelta(days=10)
        # hist = client.get_historial_nemotecnico(
        #     nemotecnico="TFIT08031127",
        #     fecha_inicio=ini,
        #     fecha_fin=fin,
        # )
        # print(hist)

if __name__ == "__main__":
    main()
