"""
core/scheduler.py — Spegnimento forzato notturno.

Usa APScheduler (AsyncIOScheduler) per spegnere TUTTI i condizionatori
configurati ogni giorno all'orario indicato nel config (default 03:00).

Lo spegnimento forzato:
  - bypassa qualsiasi regola del rule engine e qualsiasi cooldown attivo
    (chiama direttamente ac_controller.turn_off);
  - rimuove eventuali override manuali attivi, cosi' la stanza torna automatica;
  - viene loggato su SQLite (automation_logs) per ogni stanza.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.ac_controller import ACController
from core.config import Config
from core.rule_engine import RuleEngine

logger = logging.getLogger("climate.scheduler")


class ForceOffScheduler:
    """Gestisce il job giornaliero di spegnimento forzato."""

    def __init__(self, config: Config, ac_controller: ACController,
                 rule_engine: RuleEngine, database) -> None:
        self._cfg = config
        self._ac = ac_controller
        self._engine = rule_engine
        self._db = database
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        """Registra il job all'orario configurato e avvia lo scheduler."""
        hour, minute = self._parse_time(self._cfg.force_off_time)
        self._scheduler.add_job(
            self._force_off_all,
            trigger=CronTrigger(hour=hour, minute=minute),
            id="force_off_nightly",
            name="Spegnimento forzato AC",
            replace_existing=True,
            misfire_grace_time=300,  # tolleranza se il sistema era occupato
        )
        # Pulizia giornaliera del DB (retention) alle 04:00: limita crescita DB
        # e usura SD. Gira nella fascia notturna, cloud/AC tranquilli.
        self._scheduler.add_job(
            self._prune_db,
            trigger=CronTrigger(hour=4, minute=0),
            id="db_prune_daily",
            name="Pulizia retention DB",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        self._scheduler.start()
        logger.info("Scheduler avviato: spegnimento forzato alle %02d:%02d "
                    "+ pulizia DB 04:00", hour, minute)

    def shutdown(self) -> None:
        """Ferma lo scheduler (senza attendere i job in corso)."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler fermato.")

    def reschedule(self, force_off_time: str) -> None:
        """Riprogramma il job (usato dall'endpoint PUT /api/config/schedule)."""
        hour, minute = self._parse_time(force_off_time)
        self._scheduler.reschedule_job(
            "force_off_nightly", trigger=CronTrigger(hour=hour, minute=minute))
        self._cfg.force_off_time = force_off_time
        logger.info("Spegnimento forzato riprogrammato alle %02d:%02d", hour, minute)

    # -- interno ------------------------------------------------------------
    @staticmethod
    def _parse_time(value: str) -> tuple[int, int]:
        """'03:00' -> (3, 0). Default a (3, 0) se malformato."""
        try:
            hh, mm = value.strip().split(":")
            return int(hh), int(mm)
        except (ValueError, AttributeError):
            logger.warning("Orario spegnimento non valido (%r): uso 03:00", value)
            return 3, 0

    async def _prune_db(self) -> None:
        """Pulizia retention del DB (cancella storico vecchio ad alto volume)."""
        try:
            deleted = await self._db.prune_old()
            tot = sum(deleted.values())
            if tot:
                logger.info("Pulizia DB: %d righe rimosse (%s)", tot,
                            ", ".join(f"{k}={v}" for k, v in deleted.items() if v))
            else:
                logger.info("Pulizia DB: nessuna riga da rimuovere.")
        except Exception as exc:  # noqa: BLE001 - non deve mai crashare il servizio
            logger.error("Pulizia DB fallita: %s", exc)

    async def _force_off_all(self) -> None:
        """Spegne tutti gli AC configurati, bypassando regole e cooldown."""
        logger.info("== SPEGNIMENTO FORZATO: avvio ==")
        for room in self._cfg.rooms:
            if not room.panasonic_device_id:
                continue
            # Rimuove eventuale override per tornare automatici dopo lo spegnimento.
            self._engine.clear_override(room.name)
            try:
                await self._ac.turn_off(room.panasonic_device_id)
                await self._db.insert_automation_log(
                    room_name=room.name,
                    rule_matched="schedule:force_off",
                    action_taken="power=Off (spegnimento forzato 03:00)",
                    temp_at_trigger=None,
                    humidity_at_trigger=None,
                )
            except Exception as exc:  # noqa: BLE001 - non bloccare le altre stanze
                logger.error("Spegnimento forzato '%s' fallito: %s", room.name, exc)
                await self._db.insert_automation_log(
                    room_name=room.name,
                    rule_matched="schedule:force_off",
                    action_taken=f"ERRORE spegnimento: {exc}",
                    temp_at_trigger=None,
                    humidity_at_trigger=None,
                )
        logger.info("== SPEGNIMENTO FORZATO: completato ==")
