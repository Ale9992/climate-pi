"""
core/weather.py — Temperatura esterna e previsione da Open-Meteo.

Perche': la sonda esterna degli AC Panasonic da' la T attuale a passi di 1°C e
NON da' la previsione. Per un controllo predittivo (MPC) serve sapere che tempo
FARA' nelle prossime ore, non solo adesso. Open-Meteo fornisce, gratis e senza
API key, la T per le coordinate esatte di casa con risoluzione 0.1°C, sia
attuale sia oraria in avanti.

Una sola chiamata HTTP restituisce current + hourly forecast: la mettiamo in
cache (il meteo non cambia ogni 5 min) e la riusiamo. Fallback robusto: se
Open-Meteo non risponde, i metodi tornano None e il chiamante degrada (resta la
sonda Panasonic come fonte alternativa).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger("climate.weather")

# Coordinate di DEFAULT (centro Italia, generiche). Le coordinate reali si
# impostano in config.yaml -> location: {latitude, longitude} e arrivano qui dal
# costruttore via main.py; questi default servono solo se manca la config.
_LAT = 41.9
_LON = 12.5

_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,relative_humidity_2m"
    "&hourly=temperature_2m"
    "&forecast_days=2&timezone=Europe%2FRome"
)

_CACHE_TTL = 900.0  # 15 min: il meteo cambia lentamente, niente martellamento


class WeatherProvider:
    """Fornisce T esterna attuale + previsione oraria da Open-Meteo, con cache."""

    def __init__(self, latitude: float = _LAT, longitude: float = _LON) -> None:
        self._lat = latitude
        self._lon = longitude
        self._session: Optional[aiohttp.ClientSession] = None
        # cache: (monotonic, payload) dove payload = {"current": float,
        # "humidity": float, "hourly": [(iso_time, temp), ...]}
        self._cache: Optional[tuple[float, dict]] = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _fetch(self) -> Optional[dict]:
        """Scarica e mette in cache current+hourly. None se non raggiungibile."""
        cached = self._cache
        if cached and (time.monotonic() - cached[0]) < _CACHE_TTL:
            return cached[1]
        if self._session is None:
            self._session = aiohttp.ClientSession()
        url = _URL.format(lat=self._lat, lon=self._lon)
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    logger.warning("Open-Meteo HTTP %s", r.status)
                    return self._cache[1] if self._cache else None
                data = await r.json()
        except Exception as exc:  # noqa: BLE001 - rete giu': degrada
            logger.warning("Open-Meteo non raggiungibile: %s", exc)
            return self._cache[1] if self._cache else None

        cur = data.get("current", {})
        hourly = data.get("hourly", {})
        payload = {
            "current": cur.get("temperature_2m"),
            "humidity": cur.get("relative_humidity_2m"),
            "hourly": list(zip(hourly.get("time", []),
                               hourly.get("temperature_2m", []))),
        }
        self._cache = (time.monotonic(), payload)
        return payload

    async def get_current_temp(self) -> Optional[float]:
        """T esterna attuale (°C, 0.1° di risoluzione) o None."""
        p = await self._fetch()
        return p.get("current") if p else None

    async def get_forecast(self, hours: int = 12) -> list[tuple[str, float]]:
        """
        Previsione oraria a partire da adesso: lista (iso_time, °C).
        Vuota se non disponibile. Pronta per l'MPC (pianificazione prossime ore).
        """
        p = await self._fetch()
        if not p:
            return []
        now = time.strftime("%Y-%m-%dT%H:00")
        fut = [(t, v) for t, v in p["hourly"] if t >= now and v is not None]
        return fut[:hours]
