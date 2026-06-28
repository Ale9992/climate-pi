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
    "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
    "weather_code,wind_speed_10m,precipitation"
    "&hourly=temperature_2m,relative_humidity_2m,precipitation_probability,uv_index"
    "&forecast_days=2&timezone=Europe%2FRome"
)

_CACHE_TTL = 900.0  # 15 min: il meteo cambia lentamente, niente martellamento

# Codici meteo WMO -> descrizione breve in italiano (per la card meteo).
_WMO = {
    0: "Sereno", 1: "Prevalentemente sereno", 2: "Parz. nuvoloso", 3: "Coperto",
    45: "Nebbia", 48: "Nebbia con brina", 51: "Pioggerella", 53: "Pioggerella",
    55: "Pioggerella", 56: "Pioggia gelata", 57: "Pioggia gelata",
    61: "Pioggia debole", 63: "Pioggia", 65: "Pioggia forte",
    66: "Pioggia gelata", 67: "Pioggia gelata", 71: "Neve debole", 73: "Neve",
    75: "Neve forte", 77: "Nevischio", 80: "Rovesci", 81: "Rovesci",
    82: "Rovesci forti", 85: "Rovesci di neve", 86: "Rovesci di neve",
    95: "Temporale", 96: "Temporale con grandine", 99: "Temporale con grandine",
}


def weather_description(code) -> Optional[str]:
    """Descrizione testuale dal codice WMO Open-Meteo (None se ignoto)."""
    if code is None:
        return None
    return _WMO.get(int(code), "—")


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
        times = hourly.get("time", [])
        # UV e probabilita' di pioggia "correnti" = valore orario dell'ora attuale.
        now_hour = time.strftime("%Y-%m-%dT%H:00")
        uv_h = hourly.get("uv_index", [])
        pop_h = hourly.get("precipitation_probability", [])
        cur_uv = cur_pop = None
        for i, t in enumerate(times):
            if t == now_hour:
                cur_uv = uv_h[i] if i < len(uv_h) else None
                cur_pop = pop_h[i] if i < len(pop_h) else None
                break
        payload = {
            "current": cur.get("temperature_2m"),
            "humidity": cur.get("relative_humidity_2m"),
            "apparent": cur.get("apparent_temperature"),
            "weather_code": cur.get("weather_code"),
            "wind_speed": cur.get("wind_speed_10m"),
            "precipitation": cur.get("precipitation"),
            "uv_index": cur_uv,
            "precipitation_probability": cur_pop,
            "hourly": list(zip(times,
                               hourly.get("temperature_2m", []),
                               hourly.get("relative_humidity_2m", []))),
        }
        self._cache = (time.monotonic(), payload)
        return payload

    async def get_current_temp(self) -> Optional[float]:
        """T esterna attuale (°C, 0.1° di risoluzione) o None."""
        p = await self._fetch()
        return p.get("current") if p else None

    async def get_current_humidity(self) -> Optional[float]:
        """Umidita' relativa esterna attuale (%) o None."""
        p = await self._fetch()
        return p.get("humidity") if p else None

    async def get_forecast(self, hours: int = 12) -> list[tuple[str, float]]:
        """
        Previsione oraria a partire da adesso: lista (iso_time, °C).
        Vuota se non disponibile. Pronta per l'MPC (pianificazione prossime ore).
        """
        p = await self._fetch()
        if not p:
            return []
        now = time.strftime("%Y-%m-%dT%H:00")
        fut = [(t, temp) for t, temp, _rh in p["hourly"] if t >= now and temp is not None]
        return fut[:hours]

    async def get_humidity_forecast(self, hours: int = 12) -> list[tuple[str, float]]:
        """Previsione oraria dell'umidita' relativa esterna: lista (iso_time, %)."""
        p = await self._fetch()
        if not p:
            return []
        now = time.strftime("%Y-%m-%dT%H:00")
        fut = [(t, rh) for t, _temp, rh in p["hourly"] if t >= now and rh is not None]
        return fut[:hours]
