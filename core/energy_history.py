"""
core/energy_history.py — mantiene aggiornato lo storico energia Panasonic.

`panasonic_history` (consumo orario per device) e' la base del grafico consumi
giornaliero del mese in dashboard. Il consumo Panasonic e' UNICO d'impianto
(replicato sui 3 device), quindi per il grafico basta una serie: qui aggiorniamo
i giorni recenti (ieri+oggi, oggi cambia durante il giorno) per UN device, con
upsert, cosi' la dashboard resta fresca senza dover lanciare tool a mano.

Refresh all'avvio (dopo un ritardo) e poi ogni `interval_hours`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("climate.energy")

_MISS = -255  # marker "dato mancante" del cloud Panasonic


def _val(rec: dict, key: str) -> Optional[float]:
    v = rec.get(key)
    return None if (v is None or v == _MISS) else float(v)


class EnergyHistoryLogger:
    """Aggiorna periodicamente panasonic_history coi giorni recenti."""

    def __init__(self, ac_controller, database, interval_hours: int = 6) -> None:
        self._ac = ac_controller
        self._db = database
        self._interval = interval_hours * 3600
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="energy-history")
        logger.info("Energy history logger avviato (refresh ogni %dh).",
                    self._interval // 3600)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(25)  # lascia connettere il cloud all'avvio
        while not self._stop.is_set():
            try:
                await self._refresh()
            except Exception as exc:  # noqa: BLE001 - non deve fermare il servizio
                logger.warning("Refresh storico energia fallito: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def _refresh(self) -> None:
        # Aggregazione MENSILE Panasonic (una serie d'impianto basta): da' il
        # consumo per-giorno che combacia con l'app. Lo storico orario sotto-conta.
        devs = self._ac.device_ids
        if not devs:
            return
        did = devs[0]
        date = datetime.now().strftime("%Y%m%d")
        recs = await self._ac.fetch_month_history(did, date)
        rows = []
        for r in recs:
            day = str(r.get("dataTime") or "")
            cons = r.get("consumption")
            if not day or cons in (None, _MISS):   # giorni futuri/mancanti = -255
                continue
            cost = r.get("cost")
            rows.append((did, day, float(cons),
                         None if cost in (None, _MISS) else float(cost)))
        n = await self._db.upsert_panasonic_daily(rows)
        if n:
            logger.info("Consumo giornaliero aggiornato (%d giorni).", n)
