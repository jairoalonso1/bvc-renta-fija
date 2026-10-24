"""
Cliente Playwright para extraer datos de renta fija de la BVC.

Soporta:
- Los tres segmentos (público, privado, corporativa)
- Fecha única o rango de fechas
- Búsqueda por nemotécnico
- Historial de un nemotécnico en un rango
- Cache local opcional en Parquet
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional, Union

import pandas as pd
from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
    TimeoutError as PlaywrightTimeout,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bvc.com.co/mercado-local-en-linea?tab=renta-fija"

Segmento = Literal[
    "deuda_publica_segmento_publico",
    "deuda_publica_segmento_privado",
    "deuda_corporativa",
]

SEGMENTO_LABELS = {
    "deuda_publica_segmento_publico": "Deuda pública segmento público",
    "deuda_publica_segmento_privado": "Deuda pública segmento privado",
    "deuda_corporativa": "Deuda corporativa",
}

FechaLike = Union[str, date]


def _parse_fecha(fecha: FechaLike) -> date:
    if isinstance(fecha, date):
        return fecha
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(fecha, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Formato de fecha no reconocido: {fecha}")


def _daterange(inicio: date, fin: date):
    """Genera días hábiles aproximados (lunes-viernes)."""
    cur = inicio
    while cur <= fin:
        if cur.weekday() < 5:
            yield cur
        cur += timedelta(days=1)


class BVCRentaFija:
    """Cliente de alto nivel para consultar renta fija BVC."""

    def __init__(self, headless: bool = True, timeout: int = 30_000, cache_dir: Optional[str | Path] = None):
        self.headless = headless
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._browser: Optional[Browser] = None
        self._playwright = None

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, *args):
        self.close()

    def _start(self):
        if self._browser is not None:
            return
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)

    def close(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    # ------------------------------------------------------------------
    # API pública — segmentos
    # ------------------------------------------------------------------
    def get_deuda_publica_segmento_publico(self, fecha: Optional[FechaLike] = None, fecha_inicio: Optional[FechaLike] = None, fecha_fin: Optional[FechaLike] = None) -> pd.DataFrame:
        return self._get("deuda_publica_segmento_publico", fecha=fecha, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)

    def get_deuda_publica_segmento_privado(self, fecha: Optional[FechaLike] = None, fecha_inicio: Optional[FechaLike] = None, fecha_fin: Optional[FechaLike] = None) -> pd.DataFrame:
        return self._get("deuda_publica_segmento_privado", fecha=fecha, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)

    def get_deuda_corporativa(self, fecha: Optional[FechaLike] = None, fecha_inicio: Optional[FechaLike] = None, fecha_fin: Optional[FechaLike] = None) -> pd.DataFrame:
        return self._get("deuda_corporativa", fecha=fecha, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)

    def get_todo(self, fecha: Optional[FechaLike] = None, fecha_inicio: Optional[FechaLike] = None, fecha_fin: Optional[FechaLike] = None) -> pd.DataFrame:
        """Concatena los tres segmentos."""
        frames = []
        for seg in SEGMENTO_LABELS:
            df = self._get(seg, fecha=fecha, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
            if not df.empty:
                df = df.copy()
                df["Segmento"] = SEGMENTO_LABELS[seg]
                frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # ------------------------------------------------------------------
    # API pública — búsqueda
    # ------------------------------------------------------------------
    def buscar(self, nemotecnico: str, fecha: Optional[FechaLike] = None, fecha_inicio: Optional[FechaLike] = None, fecha_fin: Optional[FechaLike] = None, segmento: Optional[Segmento] = None) -> pd.DataFrame:
        """Filtra por nemotécnico en uno o todos los segmentos."""
        segmentos = [segmento] if segmento else list(SEGMENTO_LABELS.keys())
        frames = []
        for seg in segmentos:
            df = self._get(seg, fecha=fecha, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
            if df.empty:
                continue
            col = self._col_nemotecnico(df)
            if col is None:
                continue
            filtered = df.loc[df[col].astype(str).str.upper() == nemotecnico.upper()].copy()
            if not filtered.empty:
                filtered["Segmento"] = SEGMENTO_LABELS[seg]
                frames.append(filtered)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def get_historial_nemotecnico(self, nemotecnico: str, fecha_inicio: FechaLike, fecha_fin: FechaLike, segmento: Optional[Segmento] = None) -> pd.DataFrame:
        """Recorre un rango de días hábiles y devuelve las filas del nemotécnico."""
        return self.buscar(nemotecnico=nemotecnico, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, segmento=segmento)

    # ------------------------------------------------------------------
    # API pública — ficha facial y operaciones intradía
    # ------------------------------------------------------------------
    def get_detalle_titulo(self, nemotecnico: str, segmento: Optional[Segmento] = "deuda_corporativa", slug_url: Optional[str] = None) -> dict:
        """Lee los pares etiqueta/valor de la ficha visible de una emisión BVC.

        Si un nemotécnico aparece en varias emisiones, pase `slug_url` con la URL
        del vencimiento concreto para evitar seleccionar una emisión arbitraria.
        """
        self._start()
        assert self._browser is not None
        symbol = str(nemotecnico).strip().upper()
        page = self._browser.new_page()
        try:
            page.set_default_timeout(self.timeout)
            page.goto(slug_url or self._detalle_url(segmento, symbol, "resumen"), wait_until="domcontentloaded")
            page.wait_for_timeout(900)
            h1 = page.locator("h1").first
            title = h1.inner_text().strip() if h1.count() else ""
            pairs = {}
            rows = page.locator("tr")
            for i in range(rows.count()):
                cells = rows.nth(i).locator("th, td")
                if cells.count() >= 2:
                    key, value = cells.nth(0).inner_text().strip().rstrip(":"), cells.nth(1).inner_text().strip()
                    if key and value:
                        pairs[key] = value
            if not pairs:
                labels = page.locator("[class*='Detail'], [class*='detail'], [class*='Row']")
                for i in range(min(labels.count(), 100)):
                    text = labels.nth(i).inner_text().strip()
                    if ":" in text:
                        key, value = text.split(":", 1)
                        if key.strip() and value.strip():
                            pairs[key.strip()] = value.strip()
            return {"Nemotecnico": title.upper() or symbol, "Segmento": segmento, "URL": page.url, "Fuente_datos": "BVC" if pairs else None, "Estado": "OK" if pairs else "No verificado: BVC no devolvió una ficha legible", "Detalle": pairs}
        finally:
            page.close()

    @staticmethod
    def _detalle_url(segmento: Optional[Segmento], nemotecnico: str, tab: str) -> str:
        roots = {
            "deuda_corporativa": "https://www.bvc.com.co/renta-fija-deuda-corporativa",
            "deuda_publica_segmento_publico": "https://www.bvc.com.co/renta-fija-deuda-publica-segmento-publico",
            "deuda_publica_segmento_privado": "https://www.bvc.com.co/renta-fija-deuda-publica-segmento-privado",
        }
        root = roots.get(segmento or "deuda_corporativa", roots["deuda_corporativa"])
        return f"{root}/{nemotecnico.lower()}?tab={tab}"

    def get_operaciones_titulo(self, nemotecnico: str, fecha: Optional[FechaLike] = None, segmento: Optional[Segmento] = "deuda_corporativa", slug_url: Optional[str] = None) -> pd.DataFrame:
        """Extrae la tabla **Contado** de la pestaña Operaciones de una emisión BVC."""
        target = _parse_fecha(fecha) if fecha is not None else None
        symbol = str(nemotecnico).strip().upper()
        cols = ["Hora", "Rueda", "Precio", "Tasa", "Cantidad", "Volúmenes", "Procedencia"]
        outcols = cols + ["Nemotecnico", "Fecha", "Segmento", "Fuente_datos"]
        self._start()
        assert self._browser is not None
        page = self._browser.new_page()
        try:
            page.set_default_timeout(self.timeout)
            url = slug_url or self._detalle_url(segmento, symbol, "operaciones")
            if "?" in url:
                url = re.sub(r"([?&])tab=[^&]*", r"\1tab=operaciones", url)
                if "tab=" not in url:
                    url += "&tab=operaciones"
            else:
                url += "?tab=operaciones"
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            if target is not None:
                self._seleccionar_fecha(page, target)
                page.wait_for_timeout(1200)
            contado = page.locator("#accordion__panel-Contado").first
            if not contado.count():
                heading = page.locator("#accordion__heading-Contado").first
                if heading.count():
                    if (heading.get_attribute("aria-expanded") or "").lower() != "true":
                        heading.click()
                        page.wait_for_timeout(250)
                    panel_id = heading.get_attribute("aria-controls")
                    if panel_id:
                        contado = page.locator(f"#{panel_id}").first
            if not contado.count():
                logger.warning("La BVC no renderizó la sección Contado para %s", symbol)
                return pd.DataFrame(columns=outcols)
            tables = pd.read_html(io.StringIO(contado.inner_html()))
            table = None
            expected = {"hora", "rueda", "precio", "cantidad", "volúmenes", "procedencia"}
            for candidate in tables:
                normalized = {str(c).strip().casefold() for c in candidate.columns}
                if len(expected.intersection(normalized)) >= 5:
                    table = candidate.copy()
                    break
            if table is None or table.empty:
                logger.info("La BVC no mostró operaciones Contado para %s en %s", symbol, target)
                return pd.DataFrame(columns=outcols)
            rename = {}
            for col in table.columns:
                key = str(col).strip().casefold()
                if key in {"tasa de interés", "tasa de interes"}:
                    rename[col] = "Tasa"
                elif key == "volumenes":
                    rename[col] = "Volúmenes"
                else:
                    for canonical in cols:
                        if key == canonical.casefold():
                            rename[col] = canonical
            table = table.rename(columns=rename)
            for col in cols:
                if col not in table:
                    table[col] = pd.NA
            table = table[cols].copy()
            if target is not None:
                table["Fecha"] = target.isoformat()
            else:
                selected = page.locator('input[name="date"][type="date"]').first
                table["Fecha"] = selected.input_value() if selected.count() else date.today().isoformat()
            table["Nemotecnico"] = symbol
            table["Segmento"] = segmento
            table["Fuente_datos"] = "BVC"
            return table[outcols]
        finally:
            page.close()

    def get_operaciones_dia(self, fecha: Optional[FechaLike] = None, segmento: Optional[Segmento] = "deuda_corporativa", max_titulos: int = 20) -> pd.DataFrame:
        """Consolida Contado para los primeros títulos únicos del listado BVC."""
        listing = self._get(segmento or "deuda_corporativa", fecha=fecha)
        col = self._col_nemotecnico(listing)
        outcols = ["Hora", "Rueda", "Precio", "Tasa", "Cantidad", "Volúmenes", "Procedencia", "Nemotecnico", "Fecha", "Segmento", "Fuente_datos"]
        if listing.empty or col is None:
            return pd.DataFrame(columns=outcols)
        tickers = listing[col].dropna().astype(str).str.strip().str.upper().drop_duplicates().head(max(1, int(max_titulos)))
        frames = []
        for ticker in tickers:
            try:
                df = self.get_operaciones_titulo(ticker, fecha=fecha, segmento=segmento)
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                logger.warning("No se pudo extraer Contado de %s: %s", ticker, exc)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=outcols)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _get(self, segmento: Segmento, fecha: Optional[FechaLike] = None, fecha_inicio: Optional[FechaLike] = None, fecha_fin: Optional[FechaLike] = None) -> pd.DataFrame:
        if fecha is not None:
            fechas = [_parse_fecha(fecha)]
        elif fecha_inicio is not None and fecha_fin is not None:
            ini = _parse_fecha(fecha_inicio)
            fin = _parse_fecha(fecha_fin)
            if ini > fin:
                ini, fin = fin, ini
            fechas = list(_daterange(ini, fin))
        else:
            fechas = [None]
        frames = []
        for f in fechas:
            df = self._fetch_one(segmento, f)
            if df is not None and not df.empty:
                if f is not None:
                    df = df.copy()
                    df["Fecha_consulta"] = f.isoformat()
                frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _cache_path(self, segmento: Segmento, fecha: Optional[date]) -> Optional[Path]:
        if not self.cache_dir:
            return None
        key = fecha.isoformat() if fecha else "hoy"
        return self.cache_dir / f"{segmento}_{key}.parquet"

    def _fetch_one(self, segmento: Segmento, fecha: Optional[date]) -> pd.DataFrame:
        path = self._cache_path(segmento, fecha)
        if path and path.exists():
            logger.info("Cache hit: %s", path.name)
            return pd.read_parquet(path)
        self._start()
        assert self._browser is not None
        page = self._browser.new_page()
        try:
            page.set_default_timeout(self.timeout)
            page.goto(BASE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            label = SEGMENTO_LABELS[segmento]
            try:
                page.get_by_text(label, exact=False).first.click(timeout=8000)
                page.wait_for_timeout(1500)
            except PlaywrightTimeout:
                logger.warning("No se encontró la pestaña %s; se usa la vista actual", label)
            if fecha is not None:
                self._seleccionar_fecha(page, fecha)
            page.wait_for_selector("table", timeout=12_000)
            page.wait_for_timeout(1200)
            html = page.content()
            dfs = pd.read_html(io.StringIO(html))
            if not dfs:
                return pd.DataFrame()
            df = None
            for candidate in dfs:
                cols = [str(c).lower() for c in candidate.columns]
                if any("nemot" in c for c in cols):
                    df = candidate
                    break
            if df is None:
                df = max(dfs, key=lambda d: d.shape[0])
            df = self._limpiar(df)
            if path and not df.empty:
                df.to_parquet(path, index=False)
                logger.info("Cache saved: %s", path.name)
            return df
        except Exception as exc:
            logger.error("Error scraping %s fecha=%s: %s", segmento, fecha, exc)
            return pd.DataFrame()
        finally:
            page.close()

    def _seleccionar_fecha(self, page: Page, target: date) -> None:
        """Selecciona una fecha en el date-picker de BVC y valida el valor final."""
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        date_input = page.locator('input[name="date"][type="date"]').first
        if date_input.count() and date_input.input_value() == target.isoformat():
            return
        calendar_button = page.locator("button.react-date-picker__calendar-button").first
        if not calendar_button.count():
            calendar_button = page.locator("[class*='date-picker'] button").first
        if not calendar_button.count():
            raise RuntimeError("No se encontró el botón del calendario de BVC")
        calendar_button.click()
        page.wait_for_timeout(300)
        nav_label = page.locator("button.react-calendar__navigation__label").first
        if not nav_label.count():
            raise RuntimeError("BVC abrió el date-picker pero no mostró el selector del mes")
        for _ in range(180):
            label = nav_label.inner_text().strip().lower()
            month_found = next((i + 1 for i, name in enumerate(meses) if name in label), None)
            year_match = re.search(r"\\b(20\\d{2})\\b", label)
            if month_found is None or year_match is None:
                raise RuntimeError(f"No se pudo interpretar el mes/año del calendario: {label!r}")
            current_year = int(year_match.group(1))
            current_month = month_found
            if (current_year, current_month) == (target.year, target.month):
                break
            prev_month = page.locator("button.react-calendar__navigation__prev-button").first
            next_month = page.locator("button.react-calendar__navigation__next-button").first
            if (current_year, current_month) > (target.year, target.month):
                if not prev_month.count() or prev_month.is_disabled():
                    raise RuntimeError(f"La BVC no permite navegar hasta la fecha {target.isoformat()}")
                prev_month.click()
            else:
                if not next_month.count() or next_month.is_disabled():
                    raise RuntimeError(f"La BVC no permite navegar hasta la fecha {target.isoformat()}")
                next_month.click()
            page.wait_for_timeout(100)
        else:
            raise RuntimeError(f"No se alcanzó el mes de la fecha solicitada {target.isoformat()}")
        aria = f"{target.day} de {meses[target.month - 1]} de {target.year}"
        tile = page.locator(f'button.react-calendar__tile:has(abbr[aria-label="{aria}"])').first
        if not tile.count() or tile.is_disabled():
            raise RuntimeError(f"La BVC no ofrece un día seleccionable para {target.isoformat()}")
        tile.click()
        page.wait_for_timeout(500)
        actual = page.locator('input[name="date"][type="date"]').first
        if not actual.count():
            raise RuntimeError("No se pudo validar la fecha seleccionada: falta input[name=date]")
        selected = actual.input_value()
        if selected != target.isoformat():
            raise RuntimeError(f"BVC no confirmó la fecha {target.isoformat()}; la interfaz quedó en {selected!r}")

    @staticmethod
    def _col_nemotecnico(df: pd.DataFrame) -> Optional[str]:
        for c in df.columns:
            if "nemot" in str(c).lower():
                return c
        return None

    @staticmethod
    def _limpiar(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how="all").reset_index(drop=True)
        for col in df.columns:
            if df[col].dtype == object:
                sample = df[col].dropna().astype(str).head(8)
                if sample.str.contains(r"\\d[\\.,]\\d").any():
                    cleaned = df[col].astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False).str.strip()
                    numeric = pd.to_numeric(cleaned, errors="coerce")
                    if numeric.notna().sum() > len(df) * 0.5:
                        df[col] = numeric
        return df
