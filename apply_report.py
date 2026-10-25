#!/usr/bin/env python3
"""Reconstruye client.py y report.py desde cl*.b64 y rp*.b64 (zlib+base64)."""
import base64, zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "bvc_renta_fija"

def restore(parts, dest: Path):
    data = "".join((ROOT / p).read_text().strip() for p in parts)
    raw = zlib.decompress(base64.b64decode(data))
    dest.write_bytes(raw)
    print(f"OK {dest} ({len(raw)} bytes)")

restore(["cl1.b64", "cl2.b64"], PKG / "client.py")
restore(["rp1.b64", "rp2.b64"], PKG / "report.py")
print("Listo. Revisa git status y haz commit/push.")
