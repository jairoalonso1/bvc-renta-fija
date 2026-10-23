"""
Cliente Playwright para extraer datos de renta fija de la BVC.
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime
from typing import Literal, Optional

import pandas as pd
from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PlaywrightTimeout

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


class BVCRentaFija:
    """Cliente de alto nivel para consultar renta fija BVC."""

    def __init__(self, headless: bool = True, timeout: int = 30_000):
        self.headless = headless
        self.timeout = timeout
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
    # API pública
    # ------------------------------------------------------------------

    def get_deuda_publica_segmento_publico(
        self, fecha: Optional[str | date] = None
    ) -> pd.DataFrame:
        return self._get_segmento("deuda_publica_segmento_publico", fecha)

    def get_deuda_publica_segmento_privado(
        self, fecha: Optional[str | date] = None
    ) -> pd.DataFrame:
        return self._get_segmento("deuda_publica_segmento_privado", fecha)

    def get_deuda_corporativa(
        self, fecha: Optional[str | date] = None
    ) -> pd.DataFrame:
        return self._get_segmento("deuda_corporativa", fecha)

    def buscar(
        self,
        nemotecnico: str,
        fecha: Optional[str | date] = None,
        segmento: Optional[Segmento] = None,
    ) -> pd.DataFrame:
        """Busca un nemotécnico. Si no se indica segmento, busca en los tres."""
        segmentos: list[Segmento] = (
            [segmento]
            if segmento
            else [
                "deuda_publica_segmento_publico",
                "deuda_publica_segmento_privado",
                "deuda_corporativa",
            ]
        )
        frames = []
        for seg in segmentos:
            df = self._get_segmento(seg, fecha)
            if df.empty:
                continue
            mask = df["Nemotécnico"].astype(str).str.upper() == nemotecnico.upper()
            filtered = df.loc[mask].copy()
            if not filtered.empty:
                filtered["Segmento"] = SEGMENTO_LABELS[seg]
                frames.append(filtered)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _get_segmento(
        self, segmento: Segmento, fecha: Optional[str | date] = None
    ) -> pd.DataFrame:
        self._start()
        assert self._browser is not None

        page = self._browser.new_page()
        try:
            page.set_default_timeout(self.timeout)
            page.goto(BASE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            # Clic en la sub-pestaña del segmento
            label = SEGMENTO_LABELS[segmento]
            try:
                page.get_by_text(label, exact=False).first.click(timeout=8000)
                page.wait_for_timeout(1500)
            except PlaywrightTimeout:
                logger.warning("No se encontró la pestaña %s; se usa la vista actual", label)

            # Navegar a fecha si se solicita
            if fecha is not None:
                target = self._parse_fecha(fecha)
                self._seleccionar_fecha(page, target)

            # Esperar tabla
            page.wait_for_selector("table", timeout=10_000)
            page.wait_for_timeout(1000)

            html = page.content()
            dfs = pd.read_html(io.StringIO(html))
            if not dfs:
                return pd.DataFrame()

            # Tomar la tabla más grande que tenga columna Nemotécnico
            df = None
            for candidate in dfs:
                cols = [str(c).lower() for c in candidate.columns]
                if any("nemot" in c for c in cols):
                    df = candidate
                    break
            if df is None:
                df = max(dfs, key=lambda d: d.shape[0])

            df = self._limpiar(df)
            return df

        finally:
            page.close()

    def _seleccionar_fecha(self, page: Page, target: date):
        """Navega el date-picker React de la BVC (misma estructura que De Cardona)."""
        try:
            # Abrir calendario
            icon = page.locator(".OCIcon__Svg-sc-hhr8al-0, .react-date-picker__calendar-button").first
            if icon.count() == 0:
                # fallback genérico
                icon = page.locator("[class*=\'date-picker\'], [class*=\'DatePicker\']").first
            icon.click()
            page.wait_for_timeout(800)

            # Ir a vista de año
            nav = page.locator(".DatePickerstyled__StyledNavigationLabel-sc-nz7bkq-2, .react-calendar__navigation__label").first
            nav.click()
            page.wait_for_timeout(500)

            # Ajustar año
            for _ in range(15):
                label_text = page.locator(".react-calendar__navigation__label__labelText, .react-calendar__navigation__label").first.inner_text()
                try:
                    cur_year = int("".join(filter(str.isdigit, label_text))[:4])
                except ValueError:
                    break
                if cur_year == target.year:
                    break
                if cur_year > target.year:
                    page.locator("button.react-calendar__navigation__arrow:nth-child(1), .react-calendar__navigation__prev-button").first.click()
                else:
                    page.locator("button.react-calendar__navigation__arrow:nth-child(3), .react-calendar__navigation__next-button").first.click()
                page.wait_for_timeout(400)

            # Seleccionar mes (tiles 1-12)
            month_tile = page.locator(f"button.react-calendar__tile:nth-child({target.month})").first
            month_tile.click()
            page.wait_for_timeout(600)

            # Seleccionar día
            day_buttons = page.locator("button.react-calendar__tile")
            count = day_buttons.count()
            for i in range(count):
                btn = day_buttons.nth(i)
                name = btn.get_attribute("aria-label") or btn.inner_text()
                if str(target.day) in name and str(target.year) in name:
                    btn.click()
                    break
            page.wait_for_timeout(2000)

        except Exception as exc:
            logger.warning("No se pudo cambiar la fecha (%s). Se usa la fecha actual de la página.", exc)

    @staticmethod
    def _parse_fecha(fecha: str | date) -> date:
        if isinstance(fecha, date):
            return fecha
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(fecha, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Formato de fecha no reconocido: {fecha}")

    @staticmethod
    def _limpiar(df: pd.DataFrame) -> pd.DataFrame:
        # Normalizar nombres de columnas
        df.columns = [str(c).strip() for c in df.columns]
        # Eliminar filas completamente vacías
        df = df.dropna(how="all").reset_index(drop=True)
        # Limpiar números con comas/puntos
        for col in df.columns:
            if df[col].dtype == object:
                sample = df[col].dropna().astype(str).head(5)
                if sample.str.contains(r"[0-9],[0-9]").any():
                    df[col] = (
                        df[col]
                        .astype(str)
                        .str.replace(".", "", regex=False)
                        .str.replace(",", ".", regex=False)
                    )
                    df[col] = pd.to_numeric(df[col], errors="ignore")
        return df
