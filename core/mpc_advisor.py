"""
core/mpc_advisor.py — MPC ADVISORY: l'ARBITRO unico (consiglia, NON comanda).

Architettura "predittori -> arbitro":
  - PREDITTORI: modello termico (temp), modello umidita' (accoppiato via psicrometria),
    modello occupazione (orari di rientro).
  - ARBITRO (questo modulo): per ogni stanza valuta le azioni candidate
    (Off / Cool / Dry / Pre-raffrescamento) simulandole in avanti, e sceglie la
    PIU' ECONOMICA che mantiene il comfort (temperatura+umidita') quando la stanza
    e' (o sara') occupata. Cosi' le "collisioni" (un solo AC, una sola modalita';
    pre-cool vs persona-fuori; limite 3 kW) si risolvono DENTRO l'ottimizzazione,
    non come conflitti tra controllori separati.

NON invia comandi: logga e scrive in mpc_advisory. Serve a validare la logica dal
vivo prima di promuoverla a controllo.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from core import psychro
from core.humidity_model import HumidityModel, HumidityParams
from core.occupancy_model import OccupancyModel
from core.thermal_model import RoomThermalModel, RoomThermalParams

logger = logging.getLogger("climate.mpc_adv")

_HORIZON_H = 6
_DT_S = 900.0
_N = int(_HORIZON_H * 3600 / _DT_S)
_DEFAULT_INTERVAL = 900
_STALE_S = 1800.0
_RH_MAX = 65.0          # soglia comfort umidita' (oltre -> scomodo)
_OUTDOOR_RH = 65.0      # RH esterna assunta (manca forecast umidita') -> provvisorio
_DRY_POWER_KW = 0.30    # assorbimento stimato in modalita' Dry


def _params_path(room_name: str) -> str:
    if room_name == "Stanza da letto":
        return "config/thermal_params.json"
    slug = room_name.lower().replace(" ", "_")
    return f"config/thermal_params_{slug}.json"


class MpcAdvisor:
    def __init__(self, config, database, weather_provider,
                 presence_manager=None, interval_seconds: int = _DEFAULT_INTERVAL) -> None:
        self._cfg = config
        self._db = database
        self._weather = weather_provider
        self._presence = presence_manager
        self._occ = OccupancyModel(database)
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._task = None
        # ip -> nome persona (per collegare stanza -> presence_log)
        self._ip_name = {d.ip: d.name for d in config.presence.devices if getattr(d, "ip", None)}

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="mpc-advisor")
        logger.info("MPC advisor (arbitro temp+umidita'+occupazione) avviato, ogni %ds.",
                    self._interval)

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
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
        while not self._stop.is_set():
            try:
                await self._advise_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MPC advisor (ignoro): %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def _outdoor_temp_series(self) -> list[float]:
        fc = await self._weather.get_forecast(_HORIZON_H + 1)
        per_hour = int(3600 / _DT_S)
        if fc:
            hourly = [v for _, v in fc]
            s = []
            for h in range(_HORIZON_H):
                s += [hourly[h] if h < len(hourly) else hourly[-1]] * per_hour
            return s[:_N] or [hourly[0]] * _N
        cur = await self._weather.get_current_temp()
        return [cur if cur is not None else 28.0] * _N

    # -- simula UNA azione: ritorna (traj_temp, traj_rh, kWh) ---------------
    def _simulate_action(self, tmodel, hmodel, t0, rh0, out_t, house, w_out,
                         action, target, boost_sp, n_person, sweating):
        p = tmodel.p
        t = t0
        traj_t = [t]
        kwh = 0.0
        on_series = []
        mode = None
        for i in range(_N):
            if action == "Cool":
                q = tmodel.q_ac_cooling(t, target, True); mode = "Cool"; on = True
            elif action == "Pre-raffr.":
                q = -p.Q_cool_max if t > boost_sp else 0.0; mode = "Cool"; on = q < 0
            elif action == "Dry":
                q = 0.0; mode = "Dry"; on = True          # Dry: temp ~ invariata
                kwh += _DRY_POWER_KW * (_DT_S / 3600.0)
            else:  # Off
                q = 0.0; mode = None; on = False
            if q < 0:
                kwh += tmodel.electrical_kwh(q, _DT_S)
            on_series.append(on)
            t = tmodel.step(t, out_t[i], q, _DT_S, q_internal=p.Q_internal, t_house=house[i])
            traj_t.append(t)
        traj_rh = hmodel.simulate(rh0, traj_t, w_out, mode, on_series, n_person,
                                  dt_s=_DT_S, sweating=sweating)
        return traj_t, traj_rh, kwh

    async def _advise_once(self) -> None:
        out_t = await self._outdoor_temp_series()
        # umidita' assoluta esterna (da temp esterna + RH esterna assunta)
        w_out = [psychro.abs_humidity(t, _OUTDOOR_RH) for t in out_t]

        # letture correnti (T, RH) di ogni stanza
        cur: dict[str, tuple] = {}
        for room in self._cfg.rooms:
            r = await self._db.get_latest_reading(room.name)
            if r and r.get("temperature") is not None and r.get("timestamp"):
                age = (datetime.now() - datetime.fromisoformat(r["timestamp"])).total_seconds()
                if age <= _STALE_S:
                    cur[room.name] = (r["temperature"], r.get("humidity") or 55.0)

        chosen: list[tuple] = []  # (room, action, kwh, msg)
        for room in self._cfg.rooms:
            path = _params_path(room.name)
            if room.name not in cur or not os.path.exists(path):
                continue
            t0, rh0 = cur[room.name]
            p = RoomThermalParams.load(path)
            tmodel = RoomThermalModel(p)
            others = [v[0] for k, v in cur.items() if k != room.name]
            house = [(sum(others) / len(others) if others else p.T_house)] * _N

            # comfort
            target, on_thr, rh_max = 22.0, 23.0, _RH_MAX
            boost_sp = 20.0
            if room.comfort and room.comfort.summer:
                b = room.comfort.summer
                target = b.target_temp
                on_thr = b.target_temp + b.deadband
                boost_sp = b.boost_setpoint or (target - 2)
                rh_max = b.humidity_dry_threshold or _RH_MAX

            # occupazione: persona di riferimento (se la stanza la segue)
            person = self._ip_name.get(getattr(room, "presence_device_ip", None))
            home_now, arrive_h, occ_note = True, None, "presenza globale"
            if person:
                hn = await self._occ.is_home_now(person)
                home_now = bool(hn) if hn is not None else False
                ah, note = await self._occ.predict_next_home(person, datetime.now())
                arrive_h = ah
                occ_note = f"{person.split()[-1]}: " + ("in casa" if home_now else note)

            # maschera "occupata" sull'orizzonte (ora se in casa, o dal rientro)
            def occupied(i):
                if home_now:
                    return True
                if arrive_h is None:
                    return False
                hh = (datetime.now().hour + int(i * _DT_S / 3600)) % 24
                return hh >= arrive_h
            occ_mask = [occupied(i) for i in range(_N + 1)]
            any_occ = any(occ_mask)

            # umidita': parametri calibrati dal file della stanza
            hmodel = HumidityModel(HumidityParams.load(path))
            # Stanza con occupante a maggiore sudorazione (esigenze di comfort
            # piu' stringenti): umidita' pesata di piu'. Configurabile per stanza.
            sweating = (room.name == "Camera da letto")
            n_person = 1.0 if home_now else 0.0

            # valuta i candidati
            cands = ["Off", "Cool", "Dry", "Pre-raffr."]
            best = None
            for act in cands:
                tt, rr, kwh = self._simulate_action(
                    tmodel, hmodel, t0, rh0, out_t, house, w_out, act,
                    target, boost_sp, max(n_person, 0.0), sweating)
                # violazione comfort sullo STATO STABILE (ultimo 40% dell'orizzonte,
                # quando occupata): ignora il transitorio di raffreddamento iniziale,
                # cosi' "Cool" non viene penalizzato per il tempo che ci mette a scendere.
                steady = range(int(_N * 0.6), _N + 1)
                tviol = sum(max(0.0, tt[i] - on_thr) for i in steady if occ_mask[i])
                rviol = sum(max(0.0, rr[i] - rh_max) for i in steady if occ_mask[i])
                cost = self._cfg.tariff.cost(kwh)
                best = best or []
                best.append((act, tviol, rviol, kwh, cost, tt[-1], rr[-1]))

            # scelta: se non occupata mai -> Off. Altrimenti la piu' economica che
            # azzera la violazione comfort; se nessuna ci riesce, la minima violazione.
            # PRIORITA': temperatura (vincolo primario, sicurezza per chi non
            # percepisce il caldo), poi umidita', poi costo. Cosi' non si sceglie
            # mai "Dry e resta caldo": se fa caldo si raffredda (Cool dehumidifica
            # comunque un po'); Dry vince solo se la temp e' gia' ok ma c'e' afa.
            if not any_occ:
                pick = next(c for c in best if c[0] == "Off")
            else:
                temp_ok = [c for c in best if c[1] <= 0.3]
                full_ok = [c for c in temp_ok if c[2] <= 0.5]
                if full_ok:
                    pick = min(full_ok, key=lambda c: c[4])         # comfort pieno: piu' economica
                elif temp_ok:
                    pick = min(temp_ok, key=lambda c: (c[2], c[4]))  # tieni la temp, minimizza afa
                else:
                    pick = min(best, key=lambda c: c[1] + c[2])      # impossibile: minima violazione
            act, tviol, rviol, kwh, cost, tend, rhend = pick

            msg = (f"{room.name}: ora {t0:.1f}°/{rh0:.0f}%. {occ_note}. "
                   f"-> consiglio: {act}")
            if act in ("Cool", "Pre-raffr.", "Dry"):
                msg += f" (~{cost:.2f}€/{_HORIZON_H}h)"
            msg += f". Se Off: {_HORIZON_H}h -> {best[0][5]:.1f}°/{best[0][6]:.0f}%."
            chosen.append((room.name, act, kwh, cost, msg, round(t0, 1),
                           round(best[0][5], 1)))

        # -- vincolo 3 kW: somma potenza delle azioni di raffreddamento ------
        limit_kw = self._cfg.tariff.contracted_power_kw
        cooling = [c for c in chosen if c[1] in ("Cool", "Pre-raffr.")]
        # potenza elettrica ~ Q_cool_max/cop (stima per AC che raffredda)
        est_kw = 0.6
        if len(cooling) * est_kw > limit_kw - 0.8:  # 0.8 kW margine carico base casa
            logger.info("[ADVISORY] Nota: %d AC in raffreddamento insieme (~%.1f kW) "
                        "vicino al limite %.1f kW -> scaglionare le accensioni.",
                        len(cooling), len(cooling) * est_kw, limit_kw)

        for room_name, act, kwh, cost, msg, t0, tend in chosen:
            logger.info("[ADVISORY] %s", msg)
            try:
                await self._db.insert_mpc_advisory(room_name, t0, tend, None, msg)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Scrittura advisory fallita: %s", exc)
