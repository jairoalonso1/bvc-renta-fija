#!/usr/bin/env python3
"""Reconstruye bvc_renta_fija/report.py desde partes zlib+base64."""
import base64, zlib
from pathlib import Path
root = Path(__file__).resolve().parent
b64 = (root / "rp1.b64").read_text().strip() + (root / "rp2.b64").read_text().strip()
data = zlib.decompress(base64.b64decode(b64))
out = root / "bvc_renta_fija" / "report.py"
out.write_bytes(data)
print(f"OK: {out} ({len(data)} bytes)")
