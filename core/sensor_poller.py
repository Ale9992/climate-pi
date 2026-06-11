"""
core/sensor_poller.py — Lettura dei sensori ambiente IKEA Dirigera.

Strategia ibrida (robusta):
  - PRIMARIO: WebSocket event listener (hub.create_event_listener) per ricevere
    gli aggiornamenti in tempo reale. La libreria dirigera e' sincrona e
    run_forever() BLOCCA, quindi gira in un thread (run_in_executor); i callback
    arrivano dal thread del WebSocket e vengono inoltrati al loop asyncio con
    asyncio.run_coroutine_threadsafe. La riconnessione automatica e' gestita dal
    parametro reconnect di websocket-client.
  - FALLBACK / BASELINE: un loop di polling async ogni poll_interval_seconds che
    rilegge i sensori (get_environment_sensor_by_id). Garantisce storico e
    valutazione anche se il WebSocket cade o non recapita eventi. Se l'hub non e'
    raggiungibile, logga e ritenta (ogni 30s).

Per ogni lettura:
  - salva su SQLite (sensor_readings) con timestamp;
  - se il valore e' cambiato oltre la soglia di hysteresis (hysteresis_temp /
    hysteresis_humidity dal config) rispetto all'ultima lettura "emessa",
    inoltra l'evento al rule engine (rule_engine.process).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from core.config import Config
from core.rule_engine import RuleEngine

logger = logging.getLogger("climate.poller")

# Ogni quanto ritentare la connessione all'hub se non raggiungibile (secondi).
_HUB_RETRY_SECONDS = 30
# Secondi di auto-reconnect del WebSocket (parametro di websocket-client).
_WS_RECONNECT_SECONDS = 5


@dataclass
class _LastEmitted:
    """Ultimo valore inoltrato al rule engine, per il calcolo dell'hysteresis."""
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    # Ultima lettura GREZZA (anche se non inoltrata) e quando il motore e' stato
    # rivalutato l'ultima volta: serve per la rivalutazione periodica forzata,
    # che evita che l'AC resti fermo se la temperatura non "salta".
    last_raw_temp: Optional[float] = None
    last_raw_humidity: Optional[float] = None
    last_eval_monotonic: float = 0.0


# Ogni quanti secondi rivalutare comunque il rule engine, anche senza un salto
# di temperatura (l'isteresi sui comandi e' garantita a valle dall'ACController).
_FORCE_REEVAL_SECONDS = 300


class SensorPoller:
    """Poller dei sensori ambiente con WebSocket + polling di fallback."""

    def __init__(self, hub, config: Config, database, rule_engine: RuleEngine) -> None:
        self._hub = hub
        self._cfg = config
        self._db = database
        self._engine = rule_engine
        # sensor_id -> room_name (solo stanze con sensore configurato).
        self._sensor_to_room: dict[str, str] = {
            r.ikea_sensor_id: r.name for r in config.rooms if r.ikea_sensor_id
        }
        self._last: dict[str, _LastEmitted] = {
            r.name: _LastEmitted() for r in config.rooms
        }
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # -- avvio / arresto ----------------------------------------------------
    async def start(self) -> None:
        """Avvia il task di polling e il task WebSocket."""
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        if not self._sensor_to_room:
            logger.warning("Nessun sensore IKEA configurato: poller in sola "
                           "modalita' idle (le stanze senza sensore non vengono lette).")
        self._tasks = [
            asyncio.create_task(self._polling_loop(), name="sensor-polling"),
            asyncio.create_task(self._websocket_loop(), name="sensor-websocket"),
        ]
        logger.info("SensorPoller avviato (%d sensori).", len(self._sensor_to_room))

    async def stop(self) -> None:
        """Ferma poller e WebSocket in modo pulito."""
        self._stop.set()
        try:
            self._hub.stop_event_listener()
        except Exception:  # noqa: BLE001
            pass
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        logger.info("SensorPoller fermato.")

    # -- gestione di una lettura -------------------------------------------
    async def _handle_reading(self, room_name: str,
                              temperature: Optional[float],
                              humidity: Optional[float]) -> None:
        """Salva la lettura e, se supera l'hysteresis, avvisa il rule engine."""
        # 1) Storico: salva sempre.
        await self._db.insert_sensor_reading(room_name, temperature, humidity)

        # 2) Hysteresis: inoltra al rule engine se cambia abbastanza, OPPURE se
        #    e' passato troppo tempo dall'ultima valutazione (rivalutazione
        #    periodica forzata: evita che l'AC resti fermo a temperatura stabile).
        last = self._last.setdefault(room_name, _LastEmitted())
        last.last_raw_temp = temperature
        last.last_raw_humidity = humidity
        ht = self._cfg.engine.hysteresis_temp
        hh = self._cfg.engine.hysteresis_humidity

        first_time = last.temperature is None and last.humidity is None
        temp_jump = (temperature is not None and last.temperature is not None
                     and abs(temperature - last.temperature) >= ht)
        hum_jump = (humidity is not None and last.humidity is not None
                    and abs(humidity - last.humidity) >= hh)
        now = time.monotonic()
        stale = (last.last_eval_monotonic > 0
                 and now - last.last_eval_monotonic >= _FORCE_REEVAL_SECONDS)

        if first_time or temp_jump or hum_jump or stale:
            last.temperature = temperature
            last.humidity = humidity
            last.last_eval_monotonic = now
            reason = ("primo" if first_time else "salto" if (temp_jump or hum_jump)
                      else "periodico")
            logger.debug("Evento '%s' (%s): T=%s RH=%s -> rule engine",
                         room_name, reason, temperature, humidity)
            await self._engine.process(room_name, temperature, humidity)

    # -- polling (fallback / baseline) -------------------------------------
    async def _polling_loop(self) -> None:
        """Rilegge periodicamente i sensori dall'hub."""
        interval = self._cfg.engine.poll_interval_seconds
        while not self._stop.is_set():
            for sensor_id, room_name in self._sensor_to_room.items():
                try:
                    sensor = await self._loop.run_in_executor(
                        None, self._hub.get_environment_sensor_by_id, sensor_id)
                    a = sensor.attributes
                    temp = getattr(a, "current_temperature", None)
                    rh = getattr(a, "current_r_h", None)
                    await self._handle_reading(room_name, temp, rh)
                except Exception as exc:  # noqa: BLE001 - hub down: ritenta dopo
                    logger.error("Polling sensore '%s' fallito (hub raggiungibile?): %s",
                                 room_name, exc)
            # Attende l'intervallo, ma si sveglia subito se stop richiesto.
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    # -- websocket (tempo reale) -------------------------------------------
    def _on_ws_message(self, _wsapp, message: str) -> None:
        """
        Callback del thread WebSocket: parse del messaggio e inoltro al loop.
        Eseguito FUORI dal loop asyncio -> usa run_coroutine_threadsafe.
        """
        try:
            payload = json.loads(message)
        except (ValueError, TypeError):
            return
        data = payload.get("data") or {}
        sensor_id = data.get("id")
        if sensor_id not in self._sensor_to_room:
            return
        attrs = data.get("attributes") or {}
        # Il WebSocket usa camelCase: currentTemperature / currentRH.
        temp = attrs.get("currentTemperature")
        rh = attrs.get("currentRH")
        if temp is None and rh is None:
            return
        room_name = self._sensor_to_room[sensor_id]

        # Se il WS non riporta uno dei due valori, usa l'ultimo noto.
        last = self._last.get(room_name, _LastEmitted())
        temp = temp if temp is not None else last.temperature
        rh = rh if rh is not None else last.humidity

        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(
                self._handle_reading(room_name, temp, rh), self._loop)

    def _on_ws_error(self, _wsapp, error) -> None:
        logger.warning("WebSocket Dirigera errore: %s", error)

    def _on_ws_open(self, _wsapp) -> None:
        logger.info("WebSocket Dirigera connesso (aggiornamenti in tempo reale).")

    def _on_ws_close(self, _wsapp, *_args) -> None:
        logger.warning("WebSocket Dirigera chiuso (fallback sul polling).")

    def _run_ws_blocking(self) -> None:
        """Avvia il listener bloccante (gira in un thread executor)."""
        self._hub.create_event_listener(
            on_open=self._on_ws_open,
            on_message=self._on_ws_message,
            on_error=self._on_ws_error,
            on_close=self._on_ws_close,
            reconnect=_WS_RECONNECT_SECONDS,
        )

    async def _websocket_loop(self) -> None:
        """Supervisore: (ri)avvia il listener bloccante in un thread."""
        while not self._stop.is_set():
            try:
                # run_forever() ritorna solo a connessione persa/chiusa.
                await self._loop.run_in_executor(None, self._run_ws_blocking)
            except Exception as exc:  # noqa: BLE001
                logger.error("WebSocket loop terminato con errore: %s", exc)
            if self._stop.is_set():
                break
            logger.info("Riprovo connessione WebSocket tra %ds...", _HUB_RETRY_SECONDS)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_HUB_RETRY_SECONDS)
            except asyncio.TimeoutError:
                pass
