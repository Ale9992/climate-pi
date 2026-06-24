"""
core/remote_sensor_reader.py — Lettura sensori remoti via HTTP.

Usato per nodi sensore dedicati (es. Pi Zero con BME280/BH1750) che espongono
un JSON locale con temperatura/umidita'. La lettura viene normalizzata nello
stesso flusso degli altri sensori: sensor_readings + rule_engine.process.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger("climate.remote_sensor")

_DEFAULT_TIMEOUT_SECONDS = 5


class RemoteSensorReader:
    """Campiona endpoint HTTP configurati nelle stanze."""

    def __init__(self, config, database, rule_engine=None,
                 interval_seconds: Optional[int] = None) -> None:
        self._cfg = config
        self._db = database
        self._rule_engine = rule_engine
        self._interval = interval_seconds or config.engine.poll_interval_seconds
        self._stale_after = max(180.0, self._interval * 3)
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._room_urls: dict[str, str] = {
            r.name: r.remote_sensor_url
            for r in config.rooms
            if getattr(r, "remote_sensor_url", None)
        }
        self._humidity_offsets: dict[str, float] = {
            r.name: float(getattr(r, "remote_sensor_humidity_offset", 0.0) or 0.0)
            for r in config.rooms
            if getattr(r, "remote_sensor_url", None)
        }

    async def start(self) -> None:
        if not self._room_urls:
            logger.info("Nessun remote_sensor_url in config: reader non avviato.")
            return
        self._stop.clear()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_DEFAULT_TIMEOUT_SECONDS))
        self._task = asyncio.create_task(self._loop_run(), name="remote-sensor-reader")
        logger.info("Remote sensor reader avviato (%d endpoint, ciclo %ds).",
                    len(self._room_urls), self._interval)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _loop_run(self) -> None:
        while not self._stop.is_set():
            for room_name, url in self._room_urls.items():
                try:
                    payload = await self._fetch_json(url)
                    await self._handle_payload(room_name, payload)
                except Exception as exc:  # noqa: BLE001 - il sensore non deve fermare il servizio
                    logger.warning("Sensore remoto '%s' non leggibile: %s", room_name, exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def _fetch_json(self, url: str) -> dict:
        if self._session is None:
            raise RuntimeError("RemoteSensorReader non avviato")
        async with self._session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _handle_payload(self, room_name: str, payload: dict,
                              now_iso: Optional[str] = None) -> None:
        """Valida e salva una lettura gia' scaricata."""
        if payload.get("ok") is not True:
            logger.warning("Sensore remoto '%s': payload non ok: %s", room_name, payload)
            return

        payload_room = payload.get("room")
        if payload_room and payload_room != room_name:
            logger.warning("Sensore remoto '%s': stanza payload diversa (%r)",
                           room_name, payload_room)
            return

        ts = payload.get("timestamp")
        if ts:
            try:
                now = datetime.fromisoformat(now_iso) if now_iso else datetime.now()
                age = (now - datetime.fromisoformat(ts)).total_seconds()
                if age < -60 or age > self._stale_after:
                    logger.warning("Sensore remoto '%s': lettura vecchia/non valida "
                                   "(age %.0fs), salto.", room_name, age)
                    return
            except ValueError:
                logger.warning("Sensore remoto '%s': timestamp non valido %r",
                               room_name, ts)
                return

        try:
            temp = float(payload["temperature"])
            humidity = payload.get("humidity")
            humidity = None if humidity is None else float(humidity)
            pressure = payload.get("pressure")
            pressure = None if pressure is None else float(pressure)
            lux = payload.get("lux")
            lux = None if lux is None else float(lux)
        except (KeyError, TypeError, ValueError):
            logger.warning("Sensore remoto '%s': T/RH non valide: %s", room_name, payload)
            return

        offset = self._humidity_offsets.get(room_name, 0.0)
        if humidity is not None and offset:
            humidity = round(max(0.0, min(100.0, humidity + offset)), 2)

        await self._db.insert_sensor_reading(
            room_name, temp, humidity, pressure=pressure, lux=lux)
        if self._rule_engine is not None:
            await self._rule_engine.process(room_name, temp, humidity)
        logger.debug("Sensore remoto '%s': %.2f°C RH=%s P=%s lux=%s",
                     room_name, temp, humidity, payload.get("pressure"),
                     payload.get("lux"))
