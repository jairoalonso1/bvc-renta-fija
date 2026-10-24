from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

logger = logging.getLogger(__name__)


class Segmento(str, Enum):
    DEUDA_PUBLICA_PUBLICA = "deuda_publica_publica"
    DEUDA_PUBLICA_PRIVADA = "deuda_publica_privada"
    DEUDA_CORPORATIVA = "deuda_corporativa"


SEGMENTO_LABELS = {
    Segmento.DEUDA_PUBLICA_PUBLICA: "Deuda pública - pública",
    Segmento.DEUDA_PUBLICA_PRIVADA: "Deuda pública - privada",
    Segmento.DEUDA_CORPORATIVA: "Deuda corporativa",
}

SEGMENTO_URLS = {
    Segmento.DEUDA_PUBLICA_PUBLICA: "https://www.bvc.com.co/mercados/market-information/debt/public-debt",
    Segmento.DEUDA_PUBLICA_PRIVADA: "https://www.bvc.com.co/mercados/market-information/debt/private-debt",
    Segmento.DEUDA_CORPORATIVA: "https://www.bvc.com.co/mercados/market-information/debt/corporate-debt",
}


def parse_bvc_number(val: Any) -> float:
    """Convierte números mostrados por la BVC a float sin confundir decimales."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return float("nan")
    if isinstance(val, (int, float)):
        return float(val)
    raw = str(val).strip().replace("\xa0", "").replace(" ", "")
    if not raw or raw in {"-", "—", "–", "N/A", "n/a"}:
        return float("nan")
    raw = re.sub(r"[^0-9,\.\-]", "", raw)
    if not raw or raw in {"-", ".", ","}:
        return float("nan")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        tail = raw.rsplit(",", 1)[1]
        if len(tail) == 3 and raw.count(",") == 1:
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
    elif raw.count(".") > 1:
        parts = raw.split(".")
        raw = "".join(parts) if len(parts[-1]) == 3 else "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(raw)
    except ValueError:
        return float("nan")


def _parse_fecha(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(text, dayfirst=True, errors="raise").date()
    except (ValueError, TypeError, OverflowError):
        return None


def _rango_dias_habiles(fecha: date, dias: int) -> list[date]:
    if dias < 1:
        raise ValueError("dias debe ser >= 1")
    result: list[date] = []
    current = fecha
    while len(result) < dias:
        if current.weekday() < 5:
            result.append(current)
        current -= timedelta(days=1)
    return result


@dataclass
class BVCClient:
    headless: bool = True
    timeout_ms: int = 20000
    cache_dir: str | Path | None = None

    def __post_init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def __enter__(self) -> "BVCClient":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Use BVCClient dentro de un bloque 'with' o llame __enter__ primero")
        return self._page

    @staticmethod
    def _coerce_segmento(segmento: Segmento | str) -> Segmento:
        if isinstance(segmento, Segmento):
            return segmento
        normalized = str(segmento).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "publica_publica": Segmento.DEUDA_PUBLICA_PUBLICA,
            "deuda_publica_publica": Segmento.DEUDA_PUBLICA_PUBLICA,
            "publica_privada": Segmento.DEUDA_PUBLICA_PRIVADA,
            "deuda_publica_privada": Segmento.DEUDA_PUBLICA_PRIVADA,
            "corporativa": Segmento.DEUDA_CORPORATIVA,
            "deuda_corporativa": Segmento.DEUDA_CORPORATIVA,
        }
        if normalized not in aliases:
            raise ValueError(f"Segmento no soportado: {segmento!r}")
        return aliases[normalized]

    def get_deuda(self, segmento: Segmento | str, fecha: str | date | None = None) -> pd.DataFrame:
        seg = self._coerce_segmento(segmento)
        url = SEGMENTO_URLS[seg]
        page = self._require_page()
        logger.info("Consultando BVC: %s", url)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        if fecha is not None:
            fecha_d = _parse_fecha(fecha)
            if fecha_d is None:
                raise ValueError(f"Fecha inválida: {fecha!r}")
            inputs = page.locator("input")
            for i in range(inputs.count()):
                item = inputs.nth(i)
                placeholder = (item.get_attribute("placeholder") or "").lower()
                name = (item.get_attribute("name") or "").lower()
                if "fecha" in placeholder or "date" in placeholder or "fecha" in name or "date" in name:
                    item.fill(fecha_d.strftime("%d/%m/%Y"))
            page.wait_for_timeout(700)
        tables = page.locator("table")
        rows: list[list[str]] = []
        headers: list[str] = []
        for ti in range(tables.count()):
            table = tables.nth(ti)
            ths = table.locator("thead th")
            local_headers = [ths.nth(i).inner_text().strip() for i in range(ths.count())]
            if not local_headers or not any(k in " ".join(local_headers).lower() for k in ("nemot", "título", "titulo", "vencimiento", "tasa", "precio")):
                continue
            trs = table.locator("tbody tr")
            local_rows = []
            for ri in range(trs.count()):
                tds = trs.nth(ri).locator("td")
                values = [tds.nth(i).inner_text().strip() for i in range(tds.count())]
                if values:
                    local_rows.append(values)
            if local_rows:
                headers, rows = local_headers, local_rows
                break
        if not rows:
            logger.warning("BVC no devolvió filas de deuda para segmento=%s fecha=%s", seg.value, fecha)
            return pd.DataFrame()
        width = min(len(headers), min(len(row) for row in rows))
        frame = pd.DataFrame([r[:width] for r in rows], columns=headers[:width])
        frame["Segmento"] = seg.value
        frame["Fecha_consulta"] = (_parse_fecha(fecha) or date.today()).isoformat()
        return frame

    def get_deuda_publica(self, fecha: str | date | None = None) -> pd.DataFrame:
        return self.get_deuda(Segmento.DEUDA_PUBLICA_PUBLICA, fecha)

    def get_deuda_privada(self, fecha: str | date | None = None) -> pd.DataFrame:
        return self.get_deuda(Segmento.DEUDA_PUBLICA_PRIVADA, fecha)

    def get_deuda_corporativa(self, fecha: str | date | None = None) -> pd.DataFrame:
        return self.get_deuda(Segmento.DEUDA_CORPORATIVA, fecha)

    def get_deuda_publica_segmento_publico(self, fecha: str | date | None = None) -> pd.DataFrame:
        return self.get_deuda_publica(fecha)

    def get_detalle_titulo(self, nemotecnico: str, segmento: Segmento | str = Segmento.DEUDA_CORPORATIVA) -> dict[str, Any]:
        """Consulta el detalle público de un título; no inventa una ficha si la consulta falla."""
        seg = self._coerce_segmento(segmento)
        page = self._require_page()
        page.goto(SEGMENTO_URLS[seg], wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        row = page.locator("tr").filter(has_text=nemotecnico).first
        if row.count():
            text = row.inner_text().strip()
            if nemotecnico.lower() in text.lower():
                cells = row.locator("th,td")
                values = [cells.nth(i).inner_text().strip() for i in range(cells.count())]
                return {"Nemotecnico": nemotecnico, "Segmento": seg.value, "Datos_fila": values, "Fuente_datos": "BVC", "Estado": "Fila visible; no equivale a ficha validada"}
        return {"Nemotecnico": nemotecnico, "Segmento": seg.value, "Fuente_datos": None, "Estado": "No verificado: consulta a BVC sin resultado"}

    def get_operaciones_titulo(self, nemotecnico: str, fecha: str | date | None = None, segmento: Segmento | str = Segmento.DEUDA_CORPORATIVA) -> pd.DataFrame:
        """Busca operaciones de un título si existe una tabla visible accesible en la página."""
        seg = self._coerce_segmento(segmento)
        page = self._require_page()
        page.goto(SEGMENTO_URLS[seg], wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        candidates = page.locator("table")
        for ti in range(candidates.count()):
            table = candidates.nth(ti)
            if nemotecnico.lower() not in table.inner_text().lower():
                continue
            header_nodes = table.locator("thead th")
            headers = [header_nodes.nth(i).inner_text().strip() for i in range(header_nodes.count())]
            rows = []
            trs = table.locator("tbody tr")
            for ri in range(trs.count()):
                row = trs.nth(ri)
                td_nodes = row.locator("td")
                values = [td_nodes.nth(i).inner_text().strip() for i in range(td_nodes.count())]
                if values:
                    rows.append(values)
            width = min(len(headers), min((len(r) for r in rows), default=0)) if headers else 0
            if width:
                df = pd.DataFrame([r[:width] for r in rows], columns=headers[:width])
                df["Nemotecnico"] = nemotecnico
                df["Segmento"] = seg.value
                df["Fuente_datos"] = "BVC"
                return df
        return pd.DataFrame()

    def get_operaciones_dia(self, fecha: str | date | None = None) -> pd.DataFrame:
        """No hay extracción consolidada intradía validada: no se simulan operaciones."""
        logger.warning("No hay extracción consolidada de operaciones intradía BVC validada; se devuelve DataFrame vacío")
        return pd.DataFrame(columns=["Fecha", "Nemotecnico", "Hora", "Tasa", "Precio", "Nominal", "Giro", "Fuente_datos"])


BVCRentaFija = BVCClient
