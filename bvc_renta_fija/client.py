"""
Cliente Playwright para extraer datos de renta fija de la BVC.

Soporta:
- Los tres segmentos (pÃºblico, privado, corporativa)
- Fecha Ãºnica o rango de fechas
- BÃºsqueda por nemotÃ©cnico
- Historial de un nemotÃ©cnico en un rango
- Cache local opcional en Parquet
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata
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


class BVCSegmentSelectionError(RuntimeError):
    """Raised when BVC cannot confirm selection of the requested segment."""


class BVCOperationsExtractionError(RuntimeError):
    """Raised when all BVC operation queries fail, rather than implying no trades."""


BASE_URL = "https://www.bvc.com.co/mercado-local-en-linea?tab=renta-fija"

Segmento = Literal[
    "deuda_publica_segmento_publico",
    "deuda_publica_segmento_privado",
    "deuda_corporativa",
]

SEGMENTO_LABELS = {
    "deuda_publica_segmento_publico": "Deuda pÃºblica segmento pÃºblico",
    "deuda_publica_segmento_privado": "Deuda pÃºblica segmento privado",
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
    """Genera dÃ­as hÃ¡biles aproximados (lunes-viernes)."""
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
        self.last_operations_diagnostics: dict = {}

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

    @staticmethod
    def _limpiar(df: pd.DataFrame) -> pd.DataFrame:
        """Normaliza encabezados y celdas de una tabla HTML de BVC."""
        if df is None:
            return pd.DataFrame()

        cleaned = df.copy()
        if isinstance(cleaned.columns, pd.MultiIndex):
            flattened = []
            for parts in cleaned.columns:
                labels = []
                for part in parts:
                    label = re.sub(r"\s+", " ", str(part).replace("\xa0", " ")).strip()
                    if not label or label.casefold().startswith("unnamed:"):
                        continue
                    if label not in labels:
                        labels.append(label)
                flattened.append(" ".join(labels))
            cleaned.columns = flattened

        # Pandas puede devolver columnas duplicadas despuÃ©s de aplanar niveles.
        unique_labels = []
        counts = {}
        for index, raw_label in enumerate(cleaned.columns):
            label = re.sub(r"\s+", " ", str(raw_label).replace("\xa0", " ")).strip()
            base = label or f"columna_{index + 1}"
            occurrence = counts.get(base, 0)
            counts[base] = occurrence + 1
            unique_labels.append(base if occurrence == 0 else f"{base}_{occurrence}")
        cleaned.columns = unique_labels

        for column in cleaned.columns:
            if pd.api.types.is_object_dtype(cleaned[column]) or pd.api.types.is_string_dtype(cleaned[column]):
                def clean_value(value):
                    if not isinstance(value, str):
                        return value
                    value = value.replace("\xa0", " ").replace("\u200b", "")
                    value = re.sub(r"\s+", " ", value).strip()
                    return value if value else pd.NA
                cleaned[column] = cleaned[column].map(clean_value)

        cleaned = cleaned.dropna(how="all")
        nemo_col = BVCRentaFija._col_nemotecnico(cleaned)
        if nemo_col is not None:
            repeated_header = (
                cleaned[nemo_col].astype("string").str.strip().str.casefold()
                .isin({"nemotecnico", "nemotÃ©cnico"})
            )
            cleaned = cleaned.loc[~repeated_header.fillna(False)]
        return cleaned.reset_index(drop=True)

    @staticmethod
    def _col_nemotecnico(df: pd.DataFrame) -> Optional[str]:
        """Encuentra la columna de nemotÃ©cnico aunque cambie la tilde o el espaciado."""
        if df is None or len(df.columns) == 0:
            return None

        def normalize(value) -> str:
            value = unicodedata.normalize("NFKD", str(value))
            value = "".join(char for char in value if not unicodedata.combining(char))
            return re.sub(r"[^a-z0-9]+", "", value.casefold())

        for column in df.columns:
            if "nemotecnico" in normalize(column):
                return column
        return None

    @staticmethod
    def _base_nemotecnico(value: str) -> str:
        """Devuelve el nemotÃ©cnico corto a partir del identificador de emisiÃ³n."""
        return str(value).strip().upper().split("-", 1)[0]

    # ------------------------------------------------------------------
    # API pÃºblica â€” segmentos
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
    # API pÃºblica â€” bÃºsqueda
    # ------------------------------------------------------------------
    def buscar(self, nemotecnico: str, fecha: Optional[FechaLike] = None, fecha_inicio: Optional[FechaLike] = None, fecha_fin: Optional[FechaLike] = None, segmento: Optional[Segmento] = None) -> pd.DataFrame:
        """Filtra por nemotÃ©cnico en uno o todos los segmentos."""
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
        """Recorre un rango de dÃ­as hÃ¡biles y devuelve las filas del nemotÃ©cnico."""
        return self.buscar(nemotecnico=nemotecnico, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, segmento=segmento)

    # ------------------------------------------------------------------
    # API pÃºblica â€” ficha facial y operaciones intradÃ­a
    # ------------------------------------------------------------------
    def get_detalle_titulo(self, nemotecnico: str, segmento: Optional[Segmento] = "deuda_corporativa", slug_url: Optional[str] = None) -> dict:
        """Lee los pares etiqueta/valor de la ficha visible de una emisiÃ³n BVC.

        Si un nemotÃ©cnico aparece en varias emisiones, pase `slug_url` con la URL
        del vencimiento concreto para evitar seleccionar una emisiÃ³n arbitraria.
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
            return {"Nemotecnico": title.upper() or symbol, "Segmento": segmento, "URL": page.url, "Fuente_datos": "BVC" if pairs else None, "Estado": "OK" if pairs else "No verificado: BVC no devolviÃ³ una ficha legible", "Detalle": pairs}
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
        """Normaliza las categorÃ­as de operaciones que devuelve la API oficial de BVC.

        El frontend actual usa contado, repo y ttv; se mantiene compatibilidad
        con la clave histÃ³rica simultaneo.
        """
        cols = ["Hora", "Rueda", "Precio", "Tasa", "Cantidad", "VolÃºmenes", "Procedencia", "Tipo_operacion"]
        outcols = cols + ["Nemotecnico", "Fecha", "Segmento", "Fuente_datos"]
        data = payload.get("data") if isinstance(payload, dict) else None
        tab = data.get("tab", {}) if isinstance(data, dict) else {}
        normalized = []
        if not isinstance(tab, dict):
            tab = {}

        operation_types = (
            ("contado", "Contado"),
            ("simultaneo", "Simultaneo"),
            ("simultaneas", "Simultaneas"),
            ("repo", "Repo"),
            ("ttv", "TTV"),
        )
        labels = dict(operation_types)
        display_symbol = str(nemotecnico).strip().upper().split("-", 1)[0]
        # Mantiene las categorÃ­as conocidas y acepta nuevas pestaÃ±as que BVC
        # publique en data.tab como listas de operaciones.
        ordered_keys = [key for key, _ in operation_types]
        ordered_keys.extend(
            key for key, rows in tab.items()
            if key not in labels and isinstance(rows, list)
        )
        for operation_key in ordered_keys:
            rows = tab.get(operation_key, [])
            if not isinstance(rows, list):
                continue
            operation_label = labels.get(
                operation_key,
                re.sub(r"[_-]+", " ", str(operation_key)).strip().capitalize(),
            )
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
                    "VolÃºmenes": item.get("volume"),
                    "Procedencia": origin,
                    "Tipo_operacion": operation_label,
                    "Nemotecnico": display_symbol,
                    "Fecha": fecha.isoformat(),
                    "Segmento": segmento,
                    "Fuente_datos": "BVC",
                })
        return pd.DataFrame(normalized, columns=outcols)

    def get_operaciones_titulo(self, nemotecnico: str, fecha: Optional[FechaLike] = None, segmento: Optional[Segmento] = "deuda_corporativa", slug_url: Optional[str] = None) -> pd.DataFrame:
        """Consulta la API de operaciones BVC usando el mismo handshake del sitio oficial.

        La interfaz pÃºblica obtiene un token temporal en /api/handshake y envÃ­a
        los encabezados token y k a rest.bvc.com.co. Se captura k de la peticiÃ³n
        que genera el propio frontend para evitar codificar credenciales.
        """
        target = _parse_fecha(fecha) if fecha is not None else date.today()
        symbol = str(nemotecnico).strip().upper()
        outcols = ["Hora", "Rueda", "Precio", "Tasa", "Cantidad", "VolÃºmenes", "Procedencia", "Tipo_operacion",
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
                    "BVC no generÃ³ la peticiÃ³n inicial de operaciones; no fue posible "
                    f"capturar el encabezado k. URL={page.url!r}; tÃ­tulo={page.title()!r}"
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

    def _get_identificadores_emision(self, fecha: FechaLike, segmento: Segmento) -> list[str]:
        """Obtiene los identificadores completos de emisiÃ³n desde la respuesta JSON de BVC.

        El listado HTML muestra solo el nemotÃ©cnico corto y puede mezclar varias
        emisiones del mismo instrumento. La respuesta lvl-2 contiene `mnemonic`
        con el identificador exacto que requiere la ficha intradÃ­a.
        """
        target = _parse_fecha(fecha)
        self._start()
        assert self._browser is not None
        page = self._context.new_page()
        responses: list[list[dict]] = []

        def capture(response):
            if "market-information/rf/lvl-2?" not in response.url or target.isoformat() not in response.url:
                return
            try:
                payload = response.json()
                rows = payload.get("data", {}).get("tab", [])
                if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "mnemonic" in rows[0]:
                    responses.append(rows)
            except Exception:
                return

        page.on("response", capture)
        try:
            page.set_default_timeout(self.timeout)
            page.goto("https://www.bvc.com.co/mercado-local-en-linea?tab=renta-fija_deuda-corporativa", wait_until="domcontentloaded")
            page.wait_for_timeout(1800)
            self._seleccionar_fecha(page, target)
            page.wait_for_timeout(1800)
            if not responses:
                raise RuntimeError(f"BVC no devolviÃ³ el listado JSON de emisiones para {target.isoformat()}")
            # La Ãºltima respuesta corresponde al estado final tras aplicar la fecha.
            rows = responses[-1]
            ids = []
            seen = set()
            for row in rows:
                value = row.get("mnemonic")
                if not isinstance(value, str) or not value.strip():
                    continue
                value = value.strip()
                if value.upper() not in seen:
                    ids.append(value)
                    seen.add(value.upper())
            return ids
        finally:
            page.close()

    def get_operaciones_dia(
        self,
        fecha: Optional[FechaLike] = None,
        segmento: Optional[Segmento] = "deuda_corporativa",
        max_titulos: Optional[int] = None,
        nemotecnico: Optional[str] = None,
    ) -> pd.DataFrame:
        """Consolida las operaciones de todas las emisiones de una fecha.

        Por defecto consulta todas las emisiones identificadas por BVC. Use
        max_titulos solo cuando quiera limitar explÃ­citamente las consultas.
        Si se pasa nemotecnico, consulta las emisiones que coinciden con ese
        nemotÃ©cnico corto.

        Si fallan todas las consultas tÃ©cnicas, lanza
        BVCOperationsExtractionError. Una respuesta vÃ¡lida sin filas se
        conserva como ausencia de operaciones y no como fallo de extracciÃ³n.
        """
        outcols = ["Hora", "Rueda", "Precio", "Tasa", "Cantidad", "VolÃºmenes",
                   "Procedencia", "Tipo_operacion", "Nemotecnico", "Fecha",
                   "Segmento", "Fuente_datos"]
        seg = segmento or "deuda_corporativa"
        target = date.today() if fecha is None else _parse_fecha(fecha)
        requested_nemo = None
        if nemotecnico is not None:
            requested_nemo = str(nemotecnico).strip().upper()
            if not requested_nemo:
                raise ValueError("nemotecnico no puede estar vacÃ­o")

        diagnostics = {
            "fecha": target.isoformat(),
            "segmento": seg,
            "nemotecnico": requested_nemo,
            "emisiones_encontradas": 0,
            "emisiones_consultadas": 0,
            "emisiones_con_datos": 0,
            "emisiones_sin_datos": 0,
            "consultas_fallidas": [],
            "filas": 0,
            "estado": "iniciando",
        }
        self.last_operations_diagnostics = diagnostics

        try:
            if seg == "deuda_corporativa":
                identifiers = self._get_identificadores_emision(target, seg)
            else:
                listing = self._get(seg, fecha=target)
                col = self._col_nemotecnico(listing)
                if listing.empty or col is None:
                    diagnostics["estado"] = "sin_listado"
                    return pd.DataFrame(columns=outcols)
                identifiers = (
                    listing[col].dropna().astype(str).str.strip()
                    .str.upper().drop_duplicates().tolist()
                )
        except Exception as exc:
            diagnostics["estado"] = "error_listado"
            diagnostics["error"] = str(exc)[:1000]
            raise

        if requested_nemo is not None:
            identifiers = [
                identifier for identifier in identifiers
                if (
                    str(identifier).strip().upper() == requested_nemo
                    or self._base_nemotecnico(identifier) == requested_nemo
                )
            ]

        diagnostics["emisiones_encontradas"] = len(identifiers)
        if not identifiers:
            diagnostics["estado"] = "sin_coincidencias" if requested_nemo else "sin_emisiones"
            return pd.DataFrame(columns=outcols)

        if max_titulos is not None:
            max_titulos = int(max_titulos)
            if max_titulos < 1:
                raise ValueError("max_titulos debe ser mayor que cero; use None para consultar todas las emisiones")
            identifiers = identifiers[:max_titulos]

        frames = []
        root = "https://www.bvc.com.co/renta-fija-deuda-corporativa"
        for identifier in identifiers:
            diagnostics["emisiones_consultadas"] += 1
            try:
                slug_url = (
                    f"{root}/{identifier.lower()}?tab=operaciones"
                    if seg == "deuda_corporativa" else None
                )
                df = self.get_operaciones_titulo(
                    identifier, fecha=target, segmento=seg, slug_url=slug_url
                )
                if not df.empty:
                    frames.append(df)
                    diagnostics["emisiones_con_datos"] += 1
                else:
                    diagnostics["emisiones_sin_datos"] += 1
            except Exception as exc:
                diagnostics["consultas_fallidas"].append({
                    "identificador": identifier,
                    "error": str(exc)[:500],
                })
                logger.warning(
                    "No se pudo extraer operaciones de emisiÃ³n %s (fecha=%s): %s",
                    identifier, target, exc
                )

        if not frames:
            if diagnostics["consultas_fallidas"]:
                diagnostics["estado"] = "error"
                examples = "; ".join(
                    f"{x['identificador']}: {x['error']}"
                    for x in diagnostics["consultas_fallidas"][:3]
                )
                raise BVCOperationsExtractionError(
                    f"Fallaron las consultas de las {len(diagnostics['consultas_fallidas'])} "
                    f"emisiones intentadas para {target.isoformat()}; no se pudo verificar "
                    f"si hubo operaciones. Detalles: {examples}"
                )
            diagnostics["estado"] = "sin_operaciones"
            return pd.DataFrame(columns=outcols)

        result = pd.concat(frames, ignore_index=True)
        if "Hora" in result.columns:
            result = result.sort_values("Hora", kind="stable", na_position="last")
        result = result.reindex(columns=outcols).reset_index(drop=True)
        diagnostics["filas"] = len(result)
        diagnostics["estado"] = "parcial" if diagnostics["consultas_fallidas"] else "ok"
        result.attrs["diagnostics"] = dict(diagnostics)
        self.last_operations_diagnostics = diagnostics
        return result

    def get_operaciones_nemotecnico(
        self,
        nemotecnico: str,
        fecha: Optional[FechaLike] = None,
        segmento: Optional[Segmento] = "deuda_corporativa",
        max_titulos: Optional[int] = None,
    ) -> pd.DataFrame:
        """Consulta todas las emisiones asociadas a un nemotÃ©cnico en una fecha.

        A diferencia de una consulta directa a una ficha, este mÃ©todo localiza
        identificadores completos de emisiÃ³n y consolida los movimientos de
        todos los vencimientos que comparten el nemotÃ©cnico.
        """
        return self.get_operaciones_dia(
            fecha=fecha,
            segmento=segmento,
            max_titulos=max_titulos,
            nemotecnico=nemotecnico,
        )

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

    def _cache_path(self, segmento: Segmento, fecha: ¢ëiºÛkºwµç_ºYhºÚn¶Æ¯yÛhşiíıø¥zÏÜ¢jh²*?¢ëiºßÛjÈfyëlµÚ.¶ÜmFéÜjßæßßŠW¬ıÊ&¦‹"£ú.¶›­ı¶¬†g¶Ë]¢ëmÆÛh¾'°¶ŸºYhºÚn¶Šî±è^iÙõÓOæßßŠW¬ıÊ&¦‹"£ú.¶›­ı¶¬†g¶Ë]¢ëmÆßíj)g×M?š{~)^³÷(šš,ŠèºÚn·öÚ²zÛ-v‹­·m¢øÂ–«¶ÊŠ