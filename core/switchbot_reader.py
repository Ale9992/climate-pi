"""
core/switchbot_reader.py — Lettura BLE passiva di sensori SwitchBot Meter/THS1.

Alcune stanze (es. "Camera da letto") non hanno un sensore IKEA Dirigera: la
loro unica "verita'" indoor finora era la sonda interna dell'AC, affidabile solo
ad AC spento. Questo modulo colma il buco leggendo le advertisement BLE di un
SwitchBot Meter/THS1: il sensore trasmette T/umidita' in chiaro nei dati di
advertising, quindi NON serve pairing ne' connessione GATT (lettura passiva, a
costo zero per la batteria del sensore).

Strategia (volutamente identica nello spirito a sensor_poller):
  - un loop async che ogni `interval_seconds` fa una scansione BLE breve,
    decodifica le advertisement dei MAC noti (uno per stanza, da config) e
    scrive la lettura in `sensor_readings` ESATTAMENTE come farebbe un sensore
    IKEA. Cosi' mpc_logger, il rule engine e le query storiche vedono la stanza
    come "sensorizzata" senza alcuna modifica a valle.
  - Solo SCRITTURA su sensor_readings: nessun comando, nessun pairing. Rischio
    zero per il sistema in produzione.
  - Robusto: se il Bluetooth non c'e' / il sensore e' fuori portata, logga e
    ritenta al giro dopo; non solleva mai verso il chiamante.

Decodifica: per i Meter recenti T/umidita' stanno sia nel service-data fd3d sia
negli ultimi byte del manufacturer-data (company id 0x0969). Si usano entrambe
le fonti come ridondanza.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("climate.switchbot")

SWITCHBOT_MFR = 0x0969
FD3D = "0000fd3d-0000-1000-8000-00805f9b34fb"

# Default: quanto spesso campionare. 5 min e' allineato a mpc_logger e basta e
# avanza per una dinamica termica con costante di tempo di ore.
_DEFAULT_INTERVAL_SECONDS = 300
# Se non arriva NESSUNA advertisement da piu' di questo tempo, lo scanner BLE
# e' probabilmente morto silenziosamente (bug noto BlueZ/bleak a lunga durata):
# il watchdog lo riavvia. Il Meter trasmette ogni pochi secondi, quindi 150s
# senza nulla = scanner bloccato, non semplice segnale debole.
_RESTART_AFTER = 150.0


def _decode_service(b: bytes) -> Optional[tuple[float, int]]:
    """Decodifica il service-data fd3d di un SwitchBot Meter -> (temp, hum)."""
    if len(b) < 6:
        return None
    temp = (b[4] & 0x7F) + (b[3] & 0x0F) / 10.0
    if not (b[4] & 0x80):
        temp = -temp
    hum = b[5] & 0x7F
    return round(temp, 1), hum


def _decode_mfr(md: bytes) -> Optional[tuple[float, int]]:
    """Decodifica il manufacturer-data 0x0969 (temp/umidita' negli ultimi byte)."""
    if len(md) < 6:
        return None
    tail = md[-3:]
    temp = (tail[1] & 0x7F) + (tail[0] & 0x0F) / 10.0
    if not (tail[1] & 0x80):
        temp = -temp
    return round(temp, 1), tail[2] & 0x7F


class SwitchBotReader:
    """Campiona via BLE i SwitchBot configurati e li scrive in sensor_readings."""

    def __init__(self, config, database, rule_engine=None,
                 interval_seconds: int = _DEFAULT_INTERVAL_SECONDS) -> None:
        self._cfg = config
        self._db = database
        # Se passato, dopo ogni lettura inoltra T/umidita' al rule engine: cosi'
        # una stanza senza sensore IKEA (Camera da letto) viene comandata sulla
        # temperatura REALE dello SwitchBot, non lasciata alle sole regole cieche.
        self._rule_engine = rule_engine
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._scanner = None
        self._scanner_started = 0.0   # monotonic dell'ultimo avvio scanner
        self._last_any_adv = 0.0      # monotonic dell'ultima advertisement BLE vista
        # MAC -> (temp, hum, rssi, monotonic) ultima advertisement vista.
        self._latest: dict[str, tuple[float, int, int, float]] = {}
        # Oltre questo tempo senza advertisement, la lettura e' considerata
        # vecchia e non si comanda (evita di agire su un dato stantio).
        self._stale_after = max(900.0, interval_seconds * 3)
        # MAC (upper) -> nome stanza, dai soli room con switchbot_mac impostato.
        self._mac_to_room: dict[str, str] = {}
        for room in config.rooms:
            mac = getattr(room, "switchbot_mac", None)
            if mac:
                self._mac_to_room[mac.upper()] = room.name

    # -- ciclo di vita ------------------------------------------------------
    async def start(self) -> None:
        if not self._mac_to_room:
            logger.info("Nessun switchbot_mac in config: reader non avviato.")
            return
        self._stop.clear()
        # Scanner BLE SEMPRE acceso: a segnale debole (sensore in posizione
        # schermata) avviare/fermare la scansione ogni ciclo perde troppe
        # advertisement. Tenendolo attivo si cattura OGNI adv che arriva e si
        # aggiorna una cache; il loop di controllo legge la cache. Il watchdog
        # nel loop lo riavvia se si pianta. Avvio con ritentativi (gara BlueZ).
        await self._restart_scanner()
        self._task = asyncio.create_task(self._loop_run(), name="switchbot-reader")
        logger.info("SwitchBot reader avviato (scanner continuo, ciclo %ds) "
                    "per %d sensore/i.", self._interval, len(self._mac_to_room))

    async def stop(self) -> None:
        self._stop.set()
        if self._scanner is not None:
            try:
                await self._scanner.stop()
            except Exception:  # noqa: BLE001
                pass
            self._scanner = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    # -- callback advertisement (aggiorna la cache) ------------------------
    def _on_adv(self, dev, adv) -> None:
        import time as _t
        self._last_any_adv = _t.monotonic()
        mac = dev.address.upper()
        if mac not in self._mac_to_room:
            return
        decoded = None
        if FD3D in adv.service_data:
            decoded = _decode_service(adv.service_data[FD3D])
        if decoded is None and SWITCHBOT_MFR in adv.manufacturer_data:
            decoded = _decode_mfr(adv.manufacturer_data[SWITCHBOT_MFR])
        if decoded is not None:
            self._latest[mac] = (decoded[0], decoded[1], adv.rssi, _t.monotonic())

    async def _sleep_or_stop(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def _adapter_reset(self) -> None:
        """Reset dell'adattatore HCI (sudo NOPASSWD). Sblocca BlueZ quando tiene
        una sessione di discovery di un processo morto ('Operation in progress')."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "hciconfig", "hci0", "reset",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            logger.info("Adattatore BLE resettato (hciconfig reset).")
            await self._sleep_or_stop(4)  # lascia risalire l'adattatore
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reset adattatore BLE fallito: %s", exc)

    async def _start_scanner_once(self) -> bool:
        from bleak import BleakScanner
        try:
            sc = BleakScanner(detection_callback=self._on_adv)
            await sc.start()
            self._scanner = sc
            import time as _t
            self._scanner_started = _t.monotonic()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Avvio scanner BLE fallito: %s", exc)
            self._scanner = None
            return False

    async def _restart_scanner(self) -> None:
        """(Ri)avvia lo scanner BLE. Su fallimento ripetuto (tipico 'Operation
        already in progress' di BlueZ quando un processo precedente non ha
        rilasciato la discovery) resetta l'adattatore HCI e riprova."""
        if self._scanner is not None:
            try:
                await self._scanner.stop()
            except Exception:  # noqa: BLE001
                pass
            self._scanner = None
            await self._sleep_or_stop(3)
        for attempt in range(2):
            if await self._start_scanner_once():
                logger.info("Scanner BLE (ri)avviato%s.",
                            "" if attempt == 0 else " (dopo reset)")
                return
            # fallito: resetta l'adattatore e riprova
            await self._adapter_reset()
        logger.error("Scanner BLE non avviabile: riprovo al prossimo watchdog.")

    # -- loop di controllo: legge la cache e comanda -----------------------
    async def _loop_run(self) -> None:
        import time as _t
        # Riscaldamento: lascia allo scanner ~25s per catturare la prima adv.
        await self._sleep_or_stop(25)
        while not self._stop.is_set():
            # WATCHDOG: lo scanner e' "fermo" solo se non arriva NESSUNA
            # advertisement BLE da troppo tempo. Non usare solo il MAC target:
            # con segnale debole il sensore puo' comparire raramente, ma se il
            # controller vede altri device BLE lo scanner e BlueZ sono vivi.
            now = _t.monotonic()
            ref = max(self._scanner_started, self._last_any_adv)
            if self._scanner is None or (now - ref) > _RESTART_AFTER:
                logger.warning("Scanner BLE fermo da %.0fs: riavvio.",
                               (now - ref) if self._scanner is not None else -1)
                await self._restart_scanner()
                await self._sleep_or_stop(20)
            for mac, room in self._mac_to_room.items():
                last = self._latest.get(mac)
                if last is None:
                    logger.warning("SwitchBot %s [%s]: nessuna lettura ancora.",
                                   mac, room)
                    continue
                temp, hum, rssi, seen = last
                age = _t.monotonic() - seen
                if age > self._stale_after:
                    logger.warning("SwitchBot %s [%s]: lettura vecchia di %.0fs "
                                   "(fuori portata?), salto.", mac, room, age)
                    continue
                try:
                    await self._db.insert_sensor_reading(room, temp, float(hum))
                    logger.debug("SwitchBot %s [%s]: %.1f°C %d%% (rssi %ddBm, "
                                 "vista %.0fs fa)", mac, room, temp, hum, rssi, age)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Scrittura SwitchBot '%s' fallita: %s", room, exc)
                if self._rule_engine is not None:
                    try:
                        await self._rule_engine.process(room, temp, float(hum))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("rule_engine.process('%s') fallita: %s",
                                       room, exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass
