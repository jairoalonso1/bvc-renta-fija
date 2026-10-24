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
    BrowserContext,
    TimeoutError as PlaywrightTimeout,
)

logger = logging.getLogger(__name__)


class BVCDateSelectionError(RuntimeError):
    """Raised when BVC cannot confirm the requested date in the UI."""


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
        self._context: Optional[BrowserContext] = None
        self._playwright = None

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, *args):
        self.close()

    def _start(self):
        if self._context is not None:
            return
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CO",
            timezone_id="America/Bogota",
            viewport={"width": 1440, "height": 900},
        )
        # Keep the browser context that successfully loads the official BVC site.
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def close(self):
        if self._context:
            self._context.close()
            self._context = None
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
        page = self._context.new_page()
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

    @staticmethod
    def _normalizar_operaciones_api(payload: dict, nemotecnico: str, fecha: date, segmento: Optional[Segmento]) -> pd.DataFrame:
        """Convierte la respuesta oficial de operaciones BVC al esquema público."""
        cols = ["Hora", "Rueda", "Precio", "Tasa", "Cantidad", "Volúmenes", "Procedencia"]
        outcols = cols + ["Nemotecnico", "Fecha", "Segmento", "Fuente_datos"]
        data = payload.get("data") if isinstance(payload, dict) else None
        tab = data.get("tab", {}) if isinstance(data, dict) else {}
        rows = tab.get("contado", []) if isinstance(tab, dict) else []
        if not isinstance(rows, list):
            rows = []
        normalized = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            origin = item.get("origin")
            if isinstance(origin, dict):
                origin = origin.get("es") or origin.get("en")
            normalized.append({
                "Hora": item.get("time"),
                "Rueda": item.get("board"),
                "Precio": item.get("price"),
                "Tasa": item.get("rate"),
                "Cantidad": item.get("quantity"),
                "Volúmenes": item.get("volume"),
                "Procedencia": origin,
                "Nemotecnico": str(nemotecnico).strip().upper(),
                "Fecha": fecha.isoformat(),
                "Segmento": segmento,
                "Fuente_datos": "BVC",
            })
        return pd.DataFrame(normalized, columns=outcols)

    def get_operaciones_titulo(self, nemotecnico: str, fecha: Optional[FechaLike] = None, segmento: Optional[Segmento] = "deuda_corporativa", slug_url: Optional[str] = None) -> pd.DataFrame:
        """Consulta la API de operaciones BVC usando el mismo handshake del sitio oficial.

        La interfaz pública obtiene un token temporal en /api/handshake y envía
        los encabezados token y k a rest.bvc.com.co. Se captura k de la petición
        que genera el propio frontend para evitar codificar credenciales.
        """
        target = _parse_fecha(fecha) if fecha is not None else date.today()
        symbol = str(nemotecnico).strip().upper()
        outcols = ["Hora", "Rueda", "Precio", "Tasa", "Cantidad", "Volúmenes", "Procedencia",
                   "Nemotecnico", "Fecha", "Segmento", "Fuente_datos"]
        if segmento == "deuda_publica_segmento_publico":
            filter_key, segment_filter = "marketDataRfPub", "&filters[marketDataRfPub][segment]=A2A"
        elif segmento == "deuda_publica_segmento_privado":
            filter_key, segment_filter = "marketDataRfPub", "&filters[marketDataRfPub][segment]=D2D"
        else:
            filter_key, segment_filter = "marketDataRfPriv", ""

        self._start()
        assert self._browser is not None
        page = self._context.new_page()
        captured = {}

        def capture_api_headers(request):
            if "rest.bvc.com.co/market-information/rf/lvl-3/operation" in request.url:
                headers = request.headers
                if headers.get("k"):
                    captured["k"] = headers["k"]

        page.on("request", capture_api_headers)
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
            try:
                page.wait_for_function("() => performance.getEntriesByType('resource').some(e => e.name.includes('/market-information/rf/lvl-3/operation'))",
                                       timeout=min(self.timeout, 12000))
            except PlaywrightTimeout:
                pass
            if not captured.get("k"):
                raise RuntimeError(
                    "BVC no generó la petición inicial de operaciones; no fue posible "
                    f"capturar el encabezado k. URL={page.url!r}; título={page.title()!r}"
                )

            query = (
                f"filters[{filter_key}][tradeDate]={target.isoformat()}"
                f"&filters[{filter_key}][symbol]={symbol}{segment_filter}"
            )
            result = page.evaluate(
                """async ({query, k}) => {
                    const handshakeUrl = '/api/handshake?ts=' + Date.now() + '&r=' + crypto.randomUUID();
                    const handshake = await fetch(handshakeUrl, {credentials: 'include'});
                    if (!handshake.ok) throw new Error('BVC handshake HTTP ' + handshake.status);
                    const auth = await handshake.json();
                    if (!auth || !auth.token) throw new Error('BVC handshake did not return a token');
                    const response = await fetch('https://rest.bvc.com.co/market-information/rf/lvl-3/operation?' + query, {
                        method: 'GET',
                        headers: { token: auth.token, k, origin: location.origin }
                    });
                    const text = await response.text();
                    let body;
                    try { body = JSON.parse(text); } catch { body = {message: text.slice(0, 500)}; }
                    if (!response.ok) {
                        throw new Error('BVC operations API HTTP ' + response.status + ': ' + JSON.stringify(body).slice(0, 500));
                    }
                    return body;
                }""",
                {"query": query, "k": captured["k"]},
            )
            return self._normalizar_operaciones_api(result, symbol, target, segmento)[outcols]
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
        page = self._context.new_page()
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
        except BVCDateSelectionError:
            # Do not return silently when a requested historic date was not selected.
            raise
        except Exception as exc:
            logger.error("Error scraping %s fecha=%s: %s", segmento, fecha, exc)
            return pd.DataFrame()
        finally:
            page.close()

    def _seleccionar_fecha(self, page: Page, target: date) -> None:
        """Selecciona y verifica una fecha en el date-picker de BVC.

        BVC renderiza campos visibles separados para año, mes y día y un input
        ISO oculto. Espera a que React hidrate el selector y solo usa el calendario
        como alternativa si los campos separados no están disponibles.
        """
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        date_selector = 'input[name="date"][type="date"]'
        year_selector = 'input[name="year"]'
        month_selector = 'input[name="month"]'
        day_selector = 'input[name="day"]'

        # La página puede devolver DOMContentLoaded antes de que React monte el picker.
        try:
            page.wait_for_function(
                """() => document.querySelector('input[name="year"]') ||
                         document.querySelector('input[name="date"][type="date"]') ||
                         document.querySelector('[class*="date-picker"]')""",
                timeout=min(self.timeout, 15000),
            )
        except PlaywrightTimeout:
            title = page.title()
            excerpt = re.sub(r"\s+", " ", page.locator("body").inner_text() if page.locator("body").count() else "")[:300]
            raise BVCDateSelectionError(
                f"BVC no renderizó el selector de fecha para {target.isoformat()}. "
                f"URL={page.url!r}; título={title!r}; contenido={excerpt!r}"
            )

        date_input = page.locator(date_selector).first
        if date_input.count() and date_input.input_value() == target.isoformat():
            return

        year_input = page.locator(year_selector).first
        month_input = page.locator(month_selector).first
        day_input = page.locator(day_selector).first
        if year_input.count() and month_input.count() and day_input.count():
            year_input.fill(str(target.year))
            month_input.fill(f"{target.month:02d}")
            day_input.fill(f"{target.day:02d}")
            day_input.press("Tab")
            page.wait_for_timeout(500)
            if date_input.count() and date_input.input_value() == target.isoformat():
                return
            actual_value = date_input.input_value() if date_input.count() else None
            raise BVCDateSelectionError(
                f"BVC no confirmó la fecha {target.isoformat()} tras editar año/mes/día; "
                f"valor actual={actual_value!r}; URL={page.url!r}"
            )

        calendar_selectors = [
            "button.react-date-picker__calendar-button",
            "button[class*='calendar-button']",
            "[class*='date-picker'] button[aria-expanded]",
            "[class*='date-picker'] button",
        ]
        calendar_button = None
        for selector in calendar_selectors:
            candidate = page.locator(selector).first
            if candidate.count():
                calendar_button = candidate
                break
        if calendar_button is None:
            title = page.title()
            excerpt = re.sub(r"\s+", " ", page.locator("body").inner_text() if page.locator("body").count() else "")[:300]
            raise BVCDateSelectionError(
                f"No se encontró el botón del calendario de BVC para {target.isoformat()}. "
                f"URL={page.url!r}; título={title!r}; contenido={excerpt!r}"
            )

        calendar_button.click()
        page.wait_for_timeout(300)
        nav_label = page.locator("button.react-calendar__navigation__label").first
        if not nav_label.count():
            raise BVCDateSelectionError(
                f"BVC abrió el date-picker pero no mostró el selector del mes. URL={page.url!r}"
            )
        for _ in range(180):
            label = nav_label.inner_text().strip().lower()
            month_found = next((i + 1 for i, name in enumerate(meses) if name in label), None)
            year_match = re.search(r"\b(20\d{2})\b", label)
            if month_found is None or year_match is None:
                raise BVCDateSelectionError(f"No se pudo interpretar el mes/año del calendario: {label!r}")
            current_year = int(year_match.group(1))
            current_month = month_found
            if (current_year, current_month) == (target.year, target.month):
                break
            prev_month = page.locator("button.react-calendar__navigation__prev-button").first
            next_month = page.locator("button.react-calendar__navigation__next-button").first
            if (current_year, current_month) > (target.year, target.month):
                if not prev_month.count() or prev_month.is_disabled():
                    raise BVCDateSelectionError(f"La BVC no permite navegar hasta la fecha {target.isoformat()}")
                prev_month.click()
            else:
                if not next_month.count() or next_month.is_disabled():
                    raise BVCDateSelectionError(f"La BVC no permite navegar hasta la fecha {target.isoformat()}")
                next_month.click()
            page.wait_for_timeout(100)
        else:
            raise BVCDateSelectionError(f"No se alcanzó el mes de la fecha solicitada {target.isoformat()}")
        aria = f"{target.day} de {meses[target.month - 1]} de {target.year}"
        tile = page.locator(f'button.react-calendar__tile:has(abbr[aria-label="{aria}"])').first
        if not tile.count() or tile.is_disabled():
            raise BVCDateSelectionError(f"La BVC no ofrece un día seleccionable para {target.isoformat()}")
        tile.click()
        page.wait_for_timeout(500)
        actual = page.locator(date_selector).first
        if not actual.count():
            raise BVCDateSelectionError("No se pudo validar la fecha seleccionada: falta input[name=date]")
        selected = actual.input_value()
        if selected != target.isoformat():
            raise BVCDateSelectionError(
                f"BVC no confirmó la fecha {target.isoformat()}; la interfaz quedó en {selected!r}"
            )

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
