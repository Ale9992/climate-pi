"""
core/season.py — Determinazione della stagione e filtro di modalità.

L'analisi dello storico Panasonic (docs/analisi_stagionale.md) ha mostrato che
la stagione (raffrescamento vs riscaldamento) è determinata dalla temperatura
ESTERNA, non da quella interna. Questo modulo:

  - mantiene una media mobile della temperatura esterna (campionata dai
    condizionatori Panasonic), inizializzata all'avvio dallo storico orario;
  - classifica la stagione corrente con isteresi sui confini:
        media > cooling_outdoor_threshold -> RAFFRESCAMENTO (solo Cool/Dry)
        media < heating_outdoor_threshold -> RISCALDAMENTO  (solo Heat)
        in mezzo                          -> MEZZA STAGIONE (entrambe ammesse)
  - decide se una certa modalità è ammessa nella stagione corrente, con
    sblocco di sicurezza per temperature interne estreme.

Scelte utente: mezza stagione = automatico in base alle regole interne;
rilevamento stagione = automatico dalla temperatura esterna.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from enum import Enum
from typing import Optional

from core.config import SeasonSettings

logger = logging.getLogger("climate.season")


class Season(str, Enum):
    COOLING = "raffrescamento"
    HEATING = "riscaldamento"
    SHOULDER = "mezza_stagione"


class SeasonManager:
    """Calcola la stagione dalla media mobile della temperatura esterna."""

    def __init__(self, settings: SeasonSettings, ac_controller) -> None:
        self._cfg = settings
        self._ac = ac_controller
        # Campioni (monotonic_seconds, temperatura). Finestra = window_hours.
        self._samples: deque[tuple[float, float]] = deque()
        self._season: Season = Season.SHOULDER
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # -- campionamento ------------------------------------------------------
    def add_sample(self, temperature: float) -> None:
        """Aggiunge un campione e potatura della finestra mobile."""
        now = time.monotonic()
        self._samples.append((now, temperature))
        horizon = now - self._cfg.outdoor_avg_window_hours * 3600
        while self._samples and self._samples[0][0] < horizon:
            self._samples.popleft()
        self._recompute_season()

    def rolling_average(self) -> Optional[float]:
        if not self._samples:
            return None
        return sum(t for _, t in self._samples) / len(self._samples)

    async def bootstrap(self) -> None:
        """Inizializza la media mobile dallo storico esterno (no avvio a freddo)."""
        try:
            avg = await self._ac.get_recent_outdoor_avg(self._cfg.outdoor_avg_window_hours)
        except Exception as exc:  # noqa: BLE001
            avg = None
            logger.warning("Bootstrap stagione fallito: %s", exc)
        if avg is not None:
            self.add_sample(avg)
            logger.info("Stagione inizializzata: T.esterna media %.1f°C -> %s",
                        avg, self._season.value)
        else:
            # Fallback: prova un campione live.
            await self.sample_once()

    async def sample_once(self) -> None:
        """Legge la temperatura esterna live e aggiorna la media."""
        try:
            t = await self._ac.get_outside_temperature()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Campionamento T.esterna fallito: %s", exc)
            return
        if t is not None:
            self.add_sample(t)
            logger.debug("Campione T.esterna %.1f°C -> media %.1f°C (%s)",
                         t, self.rolling_average() or 0, self._season.value)

    # -- classificazione ----------------------------------------------------
    def _recompute_season(self) -> None:
        """Aggiorna la stagione con isteresi per evitare oscillazioni al confine."""
        avg = self.rolling_average()
        if avg is None:
            return
        ch = self._cfg.cooling_outdoor_threshold
        hh = self._cfg.heating_outdoor_threshold
        hy = self._cfg.hysteresis
        s = self._season

        if s == Season.COOLING:
            # Resta in cooling finché non scende sotto la soglia - isteresi.
            if avg < ch - hy:
                s = Season.HEATING if avg < hh - hy else Season.SHOULDER
        elif s == Season.HEATING:
            if avg > hh + hy:
                s = Season.COOLING if avg > ch + hy else Season.SHOULDER
        else:  # SHOULDER
            if avg > ch + hy:
                s = Season.COOLING
            elif avg < hh - hy:
                s = Season.HEATING

        if s != self._season:
            logger.info("Cambio stagione: %s -> %s (T.esterna media %.1f°C)",
                        self._season.value, s.value, avg)
            self._season = s

    @property
    def season(self) -> Season:
        return self._season

    # -- decisione di modalità ---------------------------------------------
    def mode_allowed(self, mode: Optional[str], indoor_temp: Optional[float]) -> bool:
        """
        True se la modalità richiesta è ammessa nella stagione corrente.

        - MEZZA STAGIONE: tutto ammesso (le regole interne decidono).
        - RAFFRESCAMENTO: Cool/Dry/Fan/Auto; Heat bloccato.
        - RISCALDAMENTO : Heat/Fan/Auto; Cool/Dry bloccati.
        - Sblocco di SICUREZZA: in qualunque stagione, una T. interna estrema
          consente la modalità opposta (es. freddo anomalo a luglio -> Heat).
        """
        if not self._cfg.enabled:
            return True
        m = (mode or "").strip().lower()

        # Sblocco di sicurezza per comfort estremo.
        if indoor_temp is not None:
            if indoor_temp <= self._cfg.safety_temp_low and m == "heat":
                return True
            if indoor_temp >= self._cfg.safety_temp_high and m in ("cool", "dry"):
                return True

        if self._season == Season.SHOULDER:
            return True
        if self._season == Season.COOLING:
            return m in ("cool", "dry", "fan", "auto")
        if self._season == Season.HEATING:
            return m in ("heat", "fan", "auto")
        return True

    # -- task periodico -----------------------------------------------------
    async def start(self) -> None:
        """Bootstrap + task di campionamento periodico."""
        if not self._cfg.enabled:
            logger.info("Algoritmo stagionale DISABILITATO da config.")
            return
        await self.bootstrap()
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="season-sampler")

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
        interval = self._cfg.sample_interval_minutes * 60
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                await self.sample_once()
