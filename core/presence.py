"""
core/presence.py — Rilevamento presenza casa/vuota via FRITZ!Box (TR-064).

Risponde alla domanda che temperatura e umidita' non possono risolvere: "c'e'
qualcuno in casa?". Senza presenza, l'automazione climatizzerebbe stanze vuote.

Strategia (decisa con l'utente):
  - Presenza GLOBALE casa/vuota (non per-stanza): si interroga il FRITZ!Box per
    sapere se i telefoni di casa sono connessi alla rete (campo `Active` del
    servizio Hosts TR-064). Almeno un telefono attivo => casa ABITATA.
  - Grace period: i telefoni staccano il WiFi in standby per risparmio batteria;
    non si dichiara "vuoto" al primo dropout, ma solo dopo `away_grace_minutes`
    minuti di assenza continuativa di TUTTI i device tracciati.
  - Azione alla transizione ABITATA -> VUOTA: spegnimento di TUTTI gli AC (una
    volta sola). Mentre la casa e' vuota, il rule engine blocca le accensioni.
  - Fail-safe: se il FRITZ!Box non e' raggiungibile o lo stato e' incerto, si
    assume CASA ABITATA. Meglio climatizzare a vuoto che spegnere col padrone in
    casa: l'errore non deve mai togliere comfort.

La libreria fritzconnection e' sincrona e fa I/O di rete: gira in executor
(run_in_executor) per non bloccare il loop asyncio, come per Dirigera/Panasonic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Optional

from core.config import PresenceSettings

logger = logging.getLogger("climate.presence")


class Presence(str, Enum):
    HOME = "casa_abitata"
    AWAY = "casa_vuota"
    UNKNOWN = "sconosciuto"


class PresenceManager:
    """Determina lo stato casa/vuota interrogando il FRITZ!Box."""

    def __init__(self, settings: PresenceSettings, ac_controller,
                 ac_device_ids: list[str]) -> None:
        self._cfg = settings
        self._ac = ac_controller
        # Device AC da spegnere alla transizione -> vuoto.
        self._ac_device_ids = list(ac_device_ids)
        # Stato corrente (parte da HOME: fail-safe, non spegne all'avvio).
        self._state: Presence = Presence.HOME
        # Epoch dell'ultima volta in cui ALMENO un device era visto attivo.
        self._last_seen_epoch: float = time.time()
        # Presenza PER-DEVICE (per stanze legate a una persona specifica):
        # ip -> ultimo epoch in cui quel device e' stato visto attivo.
        self._device_last_seen: dict[str, float] = {
            d.ip: time.time() for d in settings.devices if d.ip
        }
        self._fc = None                       # FritzConnection (creata in start)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # -- API pubblica letta dal rule engine / API --------------------------
    @property
    def state(self) -> Presence:
        return self._state

    def is_home(self) -> bool:
        """True se la casa e' considerata abitata (HOME o stato incerto)."""
        return self._state != Presence.AWAY

    def away_for_seconds(self) -> int:
        """Secondi trascorsi dall'ultimo device visto attivo (0 se mai assente)."""
        if self._state != Presence.AWAY:
            return 0
        return int(time.time() - self._last_seen_epoch)

    def is_person_home(self, ip: Optional[str]) -> bool:
        """
        True se lo specifico device (per IP) e' considerato in casa: visto attivo
        entro il grace period. Usato per stanze legate a una persona.
        Fail-safe: se l'IP non e' tracciato o non determinabile -> True (non
        toglie comfort per un dato mancante).
        """
        if not ip or ip not in self._device_last_seen:
            return True
        grace = self._cfg.away_grace_minutes * 60
        return (time.time() - self._device_last_seen[ip]) < grace

    # -- connessione FRITZ!Box (bloccante, in executor) --------------------
    def _connect_blocking(self):
        from fritzconnection import FritzConnection
        return FritzConnection(
            address=self._cfg.address,
            user=self._cfg.user,
            password=self._cfg.password,
        )

    def _device_active_blocking(self, mac: str, ip: str) -> Optional[bool]:
        """
        Ritorna True/False se il device e' attivo, None se non determinabile.
        Prova prima per MAC (stabile per-SSID), poi per IP come fallback.
        """
        if self._fc is None:
            return None
        # 1) per MAC
        if mac:
            try:
                res = self._fc.call_action(
                    "Hosts1", "GetSpecificHostEntry", NewMACAddress=mac)
                return bool(res.get("NewActive"))
            except Exception:  # noqa: BLE001 - MAC non noto (es. rotato): prova IP
                pass
        # 2) per IP
        if ip:
            try:
                res = self._fc.call_action(
                    "Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP",
                    NewIPAddress=ip)
                return bool(res.get("NewActive"))
            except Exception:  # noqa: BLE001
                return None
        return None

    def _read_all_devices_blocking(self) -> dict:
        """{ip: True/False/None} per ogni device tracciato (None=non leggibile)."""
        out = {}
        for dev in self._cfg.devices:
            if dev.ip:
                out[dev.ip] = self._device_active_blocking(dev.mac, dev.ip)
        return out

    def _any_device_active_blocking(self) -> Optional[bool]:
        """
        True se ALMENO un device tracciato e' attivo; False se TUTTI inattivi;
        None se nessun device e' stato determinabile (Fritz/rete giu').
        """
        determinable = False
        for dev in self._cfg.devices:
            active = self._device_active_blocking(dev.mac, dev.ip)
            if active is None:
                continue
            determinable = True
            if active:
                return True
        return False if determinable else None

    # -- ciclo principale ---------------------------------------------------
    async def start(self) -> None:
        """Crea la connessione al FRITZ!Box e avvia il task di polling."""
        if not self._cfg.enabled:
            logger.info("Rilevamento presenza DISABILITATO da config.")
            return
        if not self._cfg.devices:
            logger.warning("Nessun device da tracciare: presenza disattivata.")
            return
        self._loop = asyncio.get_running_loop()
        try:
            self._fc = await self._loop.run_in_executor(None, self._connect_blocking)
            logger.info("FRITZ!Box connesso (%s): traccio %d device, grace %d min.",
                        self._cfg.address, len(self._cfg.devices),
                        self._cfg.away_grace_minutes)
        except Exception as exc:  # noqa: BLE001 - non bloccare l'avvio del sistema
            logger.error("FRITZ!Box non raggiungibile all'avvio (%s): %s — "
                         "presenza in fail-safe (casa abitata), ritento al loop.",
                         self._cfg.address, exc)
            self._fc = None
        self._stop.clear()
        self._task = asyncio.create_task(self._loop_run(), name="presence-poller")

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
        interval = self._cfg.poll_interval_seconds
        while not self._stop.is_set():
            await self._poll_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self) -> None:
        """Una rilevazione: aggiorna lo stato e gestisce la transizione."""
        assert self._loop is not None
        # (ri)connessione pigra se la connessione e' caduta.
        if self._fc is None:
            try:
                self._fc = await self._loop.run_in_executor(None, self._connect_blocking)
                logger.info("FRITZ!Box riconnesso.")
            except Exception as exc:  # noqa: BLE001
                logger.warning("FRITZ!Box ancora non raggiungibile: %s", exc)
                return  # stato invariato (fail-safe)

        try:
            # Legge OGNI device singolarmente: aggiorna sia il globale sia il
            # per-device (per stanze legate a una persona).
            per_device = await self._loop.run_in_executor(
                None, self._read_all_devices_blocking)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Lettura presenza fallita: %s (stato invariato)", exc)
            self._fc = None  # forza riconnessione al prossimo giro
            return

        now = time.time()

        # Aggiorna il "last seen" di ogni device determinabile e attivo.
        determinable = [v for v in per_device.values() if v is not None]
        for ip, active in per_device.items():
            if active:
                self._device_last_seen[ip] = now
        any_active = (True if any(per_device.values())
                      else (False if determinable else None))

        if any_active is None:
            # Stato non determinabile: fail-safe, non cambiare nulla.
            logger.debug("Presenza non determinabile (nessun device leggibile).")
            return

        if any_active:
            self._last_seen_epoch = now
            if self._state != Presence.HOME:
                logger.info("Presenza: rilevato un telefono -> CASA ABITATA.")
            self._state = Presence.HOME
            return

        # Nessun device attivo: valuta il grace period.
        away_for = now - self._last_seen_epoch
        grace = self._cfg.away_grace_minutes * 60
        if away_for >= grace:
            if self._state != Presence.AWAY:
                logger.info("Presenza: nessun telefono da %.0f min (>= %d) -> "
                            "CASA VUOTA. Spengo tutti gli AC.",
                            away_for / 60, self._cfg.away_grace_minutes)
                self._state = Presence.AWAY
                await self._turn_off_all()
        else:
            logger.debug("Nessun telefono da %.0f min (grace %d min): attendo.",
                         away_for / 60, self._cfg.away_grace_minutes)

    async def _turn_off_all(self) -> None:
        """Spegne tutti gli AC configurati (alla transizione -> casa vuota)."""
        for device_id in self._ac_device_ids:
            try:
                await self._ac.turn_off(device_id)
            except Exception as exc:  # noqa: BLE001 - non bloccare gli altri
                logger.error("Spegnimento AC %s (casa vuota) fallito: %s",
                             device_id, exc)
