#!/usr/bin/env python3
"""Reconstruye client.py y report.py desde partes zlib+b64 y escribe en el paquete."""
import zlib, base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "bvc_renta_fija"

def restore(prefix: str, dest: Path):
    p1 = (ROOT / f"{prefix}1.b64").read_text().strip()
    p2 = (ROOT / f"{prefix}2.b64").read_text().strip()
    data = zlib.decompress(base64.b64decode(p1 + p2))
    dest.write_bytes(data)
    print(f"Wrote {dest} ({len(data)} bytes)")

restore("cl", PKG / "client.py")
restore("rp", PKG / "report.py")
print("Done. Verifica imports y version.")
