"""
core/occupancy_model.py — Modello di OCCUPAZIONE (un layer SOPRA il termico).

Non modella la stanza, modella le PERSONE: ricostruisce dagli storici di
presence_log gli intervalli casa/fuori e stima, per ora del giorno, la
probabilita' che la persona sia in casa -> da cui il prossimo rientro probabile.

Serve all'MPC per sapere il QUANDO ("comfort pronto per le 18"); il modello
termico dice il QUANTO ("per arrivarci parti alle 17:20"). Ruoli distinti: non
collide col termico, gli sta sopra.

Degrada con grazia: con pochi giorni di storico la confidenza e' bassa e
predict_next_home ritorna None (l'MPC allora non azzarda pre-raffrescamenti).
"""

from __future__ import annotations

from datetime import datetime, timedelta

_MIN_DAYS = 3   # sotto questo storico, niente previsioni d'orario (solo stato attuale)


class OccupancyModel:
    def __init__(self, database) -> None:
        self._db = database

    async def _events(self, person: str):
        """Transizioni (datetime, is_home) ordinate per una persona."""
        rows = await self._db.get_presence_events(person)
        out = []
        for ts, is_home in rows:
            try:
                out.append((datetime.fromisoformat(ts), bool(is_home)))
            except Exception:  # noqa: BLE001
                pass
        return out

    async def is_home_now(self, person: str):
        ev = await self._events(person)
        return ev[-1][1] if ev else None

    async def hourly_home_prob(self, person: str):
        """Frazione di tempo in casa per ora del giorno (0..23). {} se pochi dati."""
        ev = await self._events(person)
        if len(ev) < 2:
            return {}, 0.0
        span_days = (ev[-1][0] - ev[0][0]).total_seconds() / 86400.0
        # accumula secondi-in-casa per ora del giorno, campionando gli intervalli
        home = {h: 0.0 for h in range(24)}
        tot = {h: 0.0 for h in range(24)}
        for (t0, s0), (t1, _s1) in zip(ev, ev[1:]):
            cur = t0
            while cur < t1:
                h = cur.hour
                step = min(600.0, (t1 - cur).total_seconds())  # passo 10 min
                tot[h] += step
                if s0:
                    home[h] += step
                cur = cur + timedelta(seconds=step)
        prob = {h: (home[h] / tot[h] if tot[h] > 0 else None) for h in range(24)}
        return prob, span_days

    async def predict_next_home(self, person: str, now: datetime):
        """Prossima ora in cui la persona sara' probabilmente in casa.
        Ritorna (hour:int, confidenza:str) oppure (None, motivo)."""
        if await self.is_home_now(person):
            return now.hour, "gia' in casa"
        prob, span = await self.hourly_home_prob(person)
        if span < _MIN_DAYS or not prob:
            return None, f"storico presenza insufficiente ({span:.1f} giorni, servono {_MIN_DAYS})"
        # cerca, nelle prossime 12 ore, la prima ora con prob casa alta (>0.5)
        for dh in range(1, 13):
            h = (now.hour + dh) % 24
            if prob.get(h) is not None and prob[h] > 0.5:
                return h, f"rientro tipico ~{h:02d}:00 (prob {prob[h]:.0%})"
        return None, "nessun rientro chiaro nelle prossime 12h"
