"""
core/light_controller.py — Controllo delle luci IKEA via hub Dirigera.

La libreria dirigera e' SINCRONA e fa I/O di rete: ogni chiamata gira in
run_in_executor per non bloccare il loop asyncio (come il sensor_poller).

Espone lettura (stato di tutte le luci, raggruppate per stanza) e controllo
(on/off, dimmer 1-100, temperatura colore). Cache a TTL breve per non
martellare l'hub ad ogni refresh della dashboard; invalidata dopo un comando.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("climate.lights")


class LightController:
    """Controller asincrono per le luci IKEA (hub Dirigera)."""

    def __init__(self, hub, ceiling_rooms: Optional[list[str]] = None) -> None:
        self._hub = hub
        # Stanze in cui le luci sono UNA plafoniera -> comandate insieme.
        self._ceiling_rooms = {(r or "").strip() for r in (ceiling_rooms or [])}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Cache: (epoch, lista-dict). TTL breve, lo stato luci cambia spesso.
        self._cache: tuple[float, list[dict]] | None = None
        self._CACHE_TTL = 10.0

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        return self._loop

    # -- lettura ------------------------------------------------------------
    def _read_blocking(self) -> list[dict]:
        """Legge tutte le luci dall'hub (bloccante)."""
        out: list[dict] = []
        for light in self._hub.get_lights():
            a = light.attributes
            caps = getattr(light, "capabilities", None)
            can = getattr(caps, "can_receive", []) if caps else []
            room = getattr(light, "room", None)
            out.append({
                "id": light.id,
                "name": getattr(a, "custom_name", "") or "Luce",
                "room": room.name if room else None,
                "is_on": bool(getattr(a, "is_on", False)),
                "level": getattr(a, "light_level", None),
                "color_temp": getattr(a, "color_temperature", None),
                "supports_level": "lightLevel" in can,
                "supports_color_temp": "colorTemperature" in can,
            })
        return out

    async def get_lights(self, use_cache: bool = True) -> list[dict]:
        """Stato di tutte le luci (lista di dict). Usa cache se fresca."""
        if use_cache and self._cache and (time.monotonic() - self._cache[0]) < self._CACHE_TTL:
            return self._cache[1]
        loop = self._ensure_loop()
        try:
            lights = await loop.run_in_executor(None, self._read_blocking)
        except Exception as exc:  # noqa: BLE001 - hub down: degrada a cache/vuoto
            logger.warning("Lettura luci fallita: %s", exc)
            return self._cache[1] if self._cache else []
        self._cache = (time.monotonic(), lights)
        return lights

    async def get_lights_by_room(self, use_cache: bool = True) -> dict[str, list[dict]]:
        """
        Luci raggruppate per stanza. Per le stanze 'plafoniera' (ceiling_rooms)
        le luci sono fuse in UN unico controllo virtuale (id = 'ceiling:<stanza>').
        """
        lights = await self.get_lights(use_cache=use_cache)
        grouped: dict[str, list[dict]] = {}
        for lt in lights:
            grouped.setdefault((lt["room"] or "Altro").strip(), []).append(lt)

        for room, lst in list(grouped.items()):
            if room in self._ceiling_rooms and len(lst) > 1:
                on = any(l["is_on"] for l in lst)
                levels = [l["level"] for l in lst if l["level"] is not None]
                grouped[room] = [{
                    "id": f"ceiling:{room}",
                    "name": "Plafoniera",
                    "room": room,
                    "is_on": on,
                    "level": round(sum(levels) / len(levels)) if levels else 100,
                    "color_temp": None,
                    "supports_level": any(l["supports_level"] for l in lst),
                    "supports_color_temp": False,
                    "is_ceiling": True,
                    "member_ids": [l["id"] for l in lst],
                }]
        return grouped

    # -- controllo ----------------------------------------------------------
    def _set_blocking(self, light_id: str, on: Optional[bool],
                      level: Optional[int], color_temp: Optional[int]) -> None:
        light = self._hub.get_light_by_id(light_id)
        if on is not None:
            light.set_light(on)
        if level is not None:
            light.set_light_level(max(1, min(100, int(level))))
        if color_temp is not None:
            try:
                light.set_color_temperature(int(color_temp))
            except Exception as exc:  # noqa: BLE001 - non tutte le luci lo supportano
                logger.debug("color_temp non applicabile a %s: %s", light_id, exc)

    async def set_light(self, light_id: str, on: Optional[bool] = None,
                        level: Optional[int] = None,
                        color_temp: Optional[int] = None) -> None:
        """
        Comanda una luce: on/off, dimmer (1-100), temperatura colore.
        Se light_id e' 'ceiling:<stanza>', comanda l'intera plafoniera insieme.
        """
        if light_id.startswith("ceiling:"):
            room = light_id.split(":", 1)[1]
            await self.set_room(room, on=on, level=level)
            return
        loop = self._ensure_loop()
        await loop.run_in_executor(
            None, self._set_blocking, light_id, on, level, color_temp)
        self._cache = None  # invalida: il prossimo read riflette il nuovo stato
        logger.info("Luce %s -> on=%s level=%s ct=%s", light_id, on, level, color_temp)

    async def set_room(self, room_name: str, on: Optional[bool] = None,
                       level: Optional[int] = None) -> int:
        """Comanda tutte le luci di una stanza. Ritorna quante ne ha toccate."""
        lights = await self.get_lights(use_cache=False)
        n = 0
        for lt in lights:
            if lt["room"] == room_name:
                try:
                    await self.set_light(lt["id"], on=on, level=level)
                    n += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error("Comando luce %s fallito: %s", lt["id"], exc)
        return n
