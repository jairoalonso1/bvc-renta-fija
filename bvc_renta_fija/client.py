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
        if cur.weekday() < 5:  # 0=lun ... 4=vie
            yield cur
        cur += timedelta(days=1)


class BVCRentaFija:
    """Cliente de alto nivel para consultar renta fija BVC."""

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30_000,
        cache_dir: Optional[str | Path] = None,
    ):
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

    def get_deuda_publica_segmento_publico(
        self,
        fecha: Optional[FechaLike] = None,
        fecha_inicio: Optional[FechaLike] = None,
        fecha_fin: Optional[FechaLike] = None,
    ) -> pd.DataFrame:
        return self._get(
            "deuda_publica_segmento_publico",
            fecha=fecha,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
        )

    def get_deuda_publica_segmento_privado(
        self,
        fecha: Optional[FechaLike] = None,
        fecha_inicio: Optional[FechaLike] = None,
        fecha_fin: Optional[FechaLike] = None,
    ) -> pd.DataFrame:
        return self._get(
            "deuda_publica_segmento_privado",
            fecha=fecha,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
        )

    def get_deuda_corporativa(
        self,
        fecha: Optional[FechaLike] = None,
        fecha_inicio: Optional[FechaLike] = None,
        fecha_fin: Optional[FechaLike] = None,
    ) -> pd.DataFrame:
        return self._get(
            "deuda_corporativa",
            fecha=fecha,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
        )

    def get_todo(
        self,
        fecha: Optional[FechaLike] = None,
        fecha_inicio: Optional[FechaLike] = None,
        fecha_fin: Optional[FechaLike] = None,
    ) -> pd.DataFrame:
        """Concatena los tres segmentos."""
        frames = []
        for seg in SEGMENTO_LABELS:
            df = self._get(seg, fecha=fecha, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
            if not df.empty:
                df = df.copy()
                df["Segmento"] = SEGMENTO_LABELS[seg]
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    # ------------------------------------------------------------------
    # API pública — búsqueda
    # ------------------------------------------------------------------

    def buscar(
        self,
        nemotecnico: str,
        fecha: Optional[FechaLike] = None,
        fecha_inicio: Optional[FechaLike] = None,
        fecha_fin: Optional[FechaLike] = None,
        segmento: Optional[Segmento] = None,
    ) -> pd.DataFrame:
        """Filtra por nemotécnico en uno o todos los segmentos."""
        segmentos: list[Segmento] = (
            [segmento]
            if segmento
            else list(SEGMENTO_LABELS.keys())  # type: ignore
        )
        frames = []
        for seg in segmentos:
            df = self._get(
                seg,
                fecha=fecha,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
            )
            if df.empty:
                continue
            col = self._col_nemotecnico(df)
            if col is None:
                continue
            mask = df[col].astype(str).str.upper() == nemotecnico.upper()
            filtered = df.loc[mask].copy()
            if not filtered.empty:
                filtered["Segmento"] = SEGMENTO_LABELS[seg]
                frames.append(filtered)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def get_historial_nemotecnico(
        self,
        nemotecnico: str,
        fecha_inicio: FechaLike,
        fecha_fin: FechaLike,
        segmento: Optional[Segmento] = None,
    ) -> pd.DataFrame:
        """
        Recorre día a día un rango y devuelve solo las filas del nemotécnico.
        Ideal para reconstruir la trayectoria de un CDT / TES concreto.
        """
        return self.buscar(
            nemotecnico=nemotecnico,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            segmento=segmento,
        )

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _get(
        self,
        segmento: Segmento,
        fecha: Optional[FechaLike] = None,
        fecha_inicio: Optional[FechaLike] = None,
        fecha_fin: Optional[FechaLike] = None,
    ) -> pd.DataFrame:
        # Normalizar rango
        if fecha is not None:
            fechas = [_parse_fecha(fecha)]
        elif fecha_inicio is not None and fecha_fin is not None:
            ini = _parse_fecha(fecha_inicio)
            fin = _parse_fecha(fecha_fin)
            if ini > fin:
                ini, fin = fin, ini
            fechas = list(_daterange(ini, fin))
        else:
            fechas = [None]  # día actual de la página

        frames = []
        for f in fechas:
            df = self._fetch_one(segmento, f)
            if df is not None and not df.empty:
                if f is not None:
                    df = df.copy()
                    df["Fecha_consulta"] = f.isoformat()
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def _cache_path(self, segmento: Segmento, fecha: Optional[date]) -> Optional[Path]:
        if not self.cache_dir:
            return None
        key = fecha.isoformat() if fecha else "hoy"
        return self.cache_dir / f"{segmento}_{key}.parquet"

    def _fetch_one(self, segmento: Segmento, fecha: Optional[date]) -> pd.DataFrame:
        # Cache
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

            # Sub-pestaña
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

    def _seleccionar_fecha(self, page: Page, target: date):
        """Navega el date-picker React de la BVC."""
        try:
            # Abrir calendario
            selectors = [
                ".OCIcon__Svg-sc-hhr8al-0",
                ".react-date-picker__calendar-button",
                "[class*='date-picker'] button",
                "[class*='DatePicker'] button",
            ]
            opened = False
            for sel in selectors:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click()
                    opened = True
                    break
            if not opened:
                logger.warning("No se encontró botón de calendario")
                return
            page.wait_for_timeout(700)

            # Vista de año
            for nav_sel in (
                ".DatePickerstyled__StyledNavigationLabel-sc-nz7bkq-2",
                ".react-calendar__navigation__label",
            ):
                nav = page.locator(nav_sel).first
                if nav.count() > 0:
                    nav.click()
                    break
            page.wait_for_timeout(400)

            # Ajustar año
            for _ in range(20):
                label_loc = page.locator(
                    ".react-calendar__navigation__label__labelText, .react-calendar__navigation__label"
                ).first
                label_text = label_loc.inner_text()
                digits = "".join(filter(str.isdigit, label_text))
                if len(digits) < 4:
                    break
                cur_year = int(digits[:4])
                if cur_year == target.year:
                    break
                if cur_year > target.year:
                    page.locator(
                        "button.react-calendar__navigation__prev-button, "
                        "button.react-calendar__navigation__arrow:nth-child(1)"
                    ).first.click()
                else:
                    page.locator(
                        "button.react-calendar__navigation__next-button, "
                        "button.react-calendar__navigation__arrow:nth-child(3)"
                    ).first.click()
                page.wait_for_timeout(350)

            # Mes
            month_btn = page.locator(
                f"button.react-calendar__tile:nth-child({target.month})"
            ).first
            if month_btn.count() > 0:
                month_btn.click()
            page.wait_for_timeout(500)

            # Día
            day_buttons = page.locator("button.react-calendar__tile")
            count = day_buttons.count()
            for i in range(count):
                btn = day_buttons.nth(i)
                aria = btn.get_attribute("aria-label") or ""
                text = btn.inner_text()
                if (
                    str(target.day) in aria
                    and str(target.year) in aria
                ) or (
                    text.strip() == str(target.day)
                    and "neighboring" not in (btn.get_attribute("class") or "")
                ):
                    btn.click()
                    break
            page.wait_for_timeout(2000)

        except Exception as exc:
            logger.warning(
                "No se pudo cambiar la fecha a %s (%s). Se usa la fecha actual.",
                target,
                exc,
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
                # Detectar números con separador de miles colombiano
                if sample.str.contains(r"\d[\.,]\d").any():
                    cleaned = (
                        df[col]
                        .astype(str)
                        .str.replace(".", "", regex=False)
                        .str.replace(",", ".", regex=False)
                        .str.replace("%", "", regex=False)
                        .str.strip()
                    )
                    numeric = pd.to_numeric(cleaned, errors="coerce")
                    if numeric.notna().sum() > len(df) * 0.5:
                        df[col] = numeric
        return df
