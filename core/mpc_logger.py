"""
core/mpc_logger.py — Datalogger per il futuro controllo predittivo (MPC).

Il rule engine attuale e' REATTIVO: guarda T/umidita' adesso e decide. Per fare
un passo avanti (anticipare: pre-raffrescare prima che torni il caldo, spegnere
sfruttando l'inerzia termica, minimizzare i kWh) serve un MODELLO che impari come
si comporta ogni stanza. E un modello si allena sui dati.

Oggi nel DB c'e' solo la T/umidita' interna (sensor_readings). Mancano gli INPUT
che spiegano perche' la temperatura cambia: cosa stava facendo l'AC, che T c'era
fuori, se la casa era abitata, quanta energia si consumava. Questo modulo li
registra: ogni `interval_seconds` scrive in `mpc_samples` un vettore di stato
COMPLETO e allineato nel tempo per ogni stanza con un condizionatore.

Principi:
  - Solo SCRITTURA: non comanda nulla, non tocca la logica di automazione. E'
    un'aggiunta a rischio zero per il sistema in produzione.
  - Robusto ai guasti parziali: ogni campo si raccoglie in modo indipendente. Se
    il cloud Panasonic e' giu' ma la LAN regge, registra comunque la T interna
    IKEA (e viceversa). Un blackout non crea un buco totale nei dati.
  - Usa la cache dello stato AC (use_cache=True): il warm-cache di main.py la
    tiene fresca, quindi il logger non aggiunge carico al cloud.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("climate.mpc")

# Ogni quanto scattare uno snapshot. 5 min e' un buon compromesso: abbastanza
# fitto per la dinamica termica di una stanza (costante di tempo ~decine di min),
# abbastanza rado da pesare nulla su SQLite/SD (~288 righe/giorno per stanza).
_DEFAULT_INTERVAL_SECONDS = 300


class MpcLogger:
    """Campiona periodicamente lo stato completo di ogni stanza nel DB."""

    def __init__(self, config, ac_controller, database,
                 presence_manager=None, weather_provider=None,
                 interval_seconds: int = _DEFAULT_INTERVAL_SECONDS) -> None:
        self._cfg = config
        self._ac = ac_controller
        self._db = database
        self._presence = presence_manager
        self._weather = weather_provider
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # -- ciclo di vita ------------------------------------------------------
    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop_run(), name="mpc-logger")
        logger.info("MPC datalogger avviato (snapshot ogni %ds).", self._interval)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop_run(self) -> None:
        # Primo snapshot subito dopo l'avvio, poi a intervalli regolari.
        while not self._stop.is_set():
            try:
                await self._snapshot_once()
            except Exception as exc:  # noqa: BLE001 - il logger non deve mai morire
                logger.warning("Snapshot MPC fallito (ignoro): %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    # -- raccolta dati ------------------------------------------------------
    async def _snapshot_once(self) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        # Presenza globale: una sola lettura per tutte le stanze.
        presence_home = None
        if self._presence is not None:
            try:
                presence_home = self._presence.is_home()
            except Exception:  # noqa: BLE001
                presence_home = None

        # T esterna da Open-Meteo: una sola lettura (cache 15 min) per tutte le stanze.
        meteo_temp = None
        if self._weather is not None:
            try:
                meteo_temp = await self._weather.get_current_temp()
            except Exception:  # noqa: BLE001
                meteo_temp = None

        n = 0
        for room in self._cfg.rooms:
            device_id = getattr(room, "panasonic_device_id", None)
            if not device_id:
                continue  # senza AC non c'e' nulla da modellare

            sample = {
                "room_name": room.name,
                "timestamp": ts,
                "presence_home": presence_home,
                "outside_temp_meteo": meteo_temp,
            }

            # T/umidita' interna IKEA (la "verita'"): ultima lettura del poller.
            try:
                latest = await self._db.get_latest_reading(room.name)
                if latest:
                    sample["temperature"] = latest.get("temperature")
                    sample["humidity"] = latest.get("humidity")
            except Exception:  # noqa: BLE001
                pass

            # Stato AC + T esterna + sonda interna AC (use_cache: niente carico cloud).
            try:
                st = await self._ac.get_device_state(device_id, use_cache=True)
                sample["ac_power"] = st.get("power")
                sample["ac_mode"] = st.get("mode")
                sample["ac_setpoint"] = st.get("target_temperature")
                sample["ac_fan"] = st.get("fan_speed")
                sample["ac_inside_temp"] = st.get("inside_temperature")
                sample["outside_temp"] = st.get("outside_temperature")
            except Exception:  # noqa: BLE001 - cloud giu': resta cio' che c'e'
                pass

            # Energia cumulativa giornaliera (kWh). In analisi: differenza tra
            # campioni consecutivi = consumo nell'intervallo (gestire reset 00:00).
            try:
                energy = await self._ac.get_today_energy(device_id)
                if energy:
                    sample["energy_kwh"] = energy.get("consumption")
                    sample["energy_cooling"] = energy.get("cooling")
            except Exception:  # noqa: BLE001
                pass

            # Presenza per-persona (se la stanza e' legata a uno specifico telefono).
            ip = getattr(room, "presence_device_ip", None)
            if self._presence is not None and ip:
                try:
                    sample["person_home"] = self._presence.is_person_home(ip)
                except Exception:  # noqa: BLE001
                    pass

            try:
                await self._db.insert_mpc_sample(sample)
                n += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scrittura snapshot '%s' fallita: %s", room.name, exc)

        logger.debug("Snapshot MPC scritto per %d stanze.", n)
