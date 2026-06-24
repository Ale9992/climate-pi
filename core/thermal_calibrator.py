"""
core/thermal_calibrator.py — Auto-calibrazione del modello termico dai DRIFT.

Idea (dell'utente): quando l'AC e' spento la stanza DERIVA liberamente; quei tratti
di salita sono "esperimenti naturali" gratuiti. Questo modulo li trova nei dati
(mpc_samples, 5 min) e stima la costante di tempo.

IMPORTANTE (rivisto 2026-06-17): si e' SCOPERTO sui dati reali che la stanza NON
rilassa verso l'ESTERNO ma verso l'INTERNO della casa (le altre stanze). Quindi
la regressione del drift va fatta contro (T_house - T_int), NON (T_est - T_int)
(quella dava R²~0). T_house = media delle ALTRE stanze allo stesso istante.

Fisica del drift (AC spento), modello a 2 conduttanze:
    dT/dt = ((UA_house+UA)/C)*(T_target - T_int) + ...      con T_target ~ T_house
Regressione di dT/dt contro (T_house - T_int):
    pendenza = (UA_house+UA)/C = 1/tau   ->  tau = 1/pendenza
    C = (UA_house+UA) * tau
Guard su R²: si aggiorna solo se il fit spiega davvero la varianza (>= R2_MIN),
cosi' i dati piatti/rumorosi NON sovrascrivono parametri gia' validati.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import datetime

from core.thermal_model import RoomThermalParams

logger = logging.getLogger("climate.calib")

ROOM = "Stanza da letto"


def _params_path(room_name: str) -> str:
    """File parametri termici della stanza (camerina usa il nome storico)."""
    if room_name == "Stanza da letto":
        return "config/thermal_params.json"
    slug = room_name.lower().replace(" ", "_")
    return f"config/thermal_params_{slug}.json"


_MIN_POINTS = 12
_MAX_GAP_S = 1200.0
_MIN_RISE = 1.0
_TAU_MIN_H, _TAU_MAX_H = 1.0, 60.0
_R2_MIN = 0.3             # guard: sotto questo il fit e' rumore -> non aggiornare


def _house_series(con, room):
    """T_house(t) = media delle ALTRE stanze (mpc_samples) a ogni istante."""
    rows = con.execute(
        "SELECT timestamp, room_name, temperature FROM mpc_samples "
        "WHERE room_name != ? AND temperature IS NOT NULL", (room,)).fetchall()
    byts = defaultdict(list)
    for ts, _r, t in rows:
        byts[ts].append(t)
    return {ts: sum(v) / len(v) for ts, v in byts.items()}


def _load_samples(con, room):
    house = _house_series(con, room)
    rows = con.execute(
        "SELECT timestamp, temperature, ac_power FROM mpc_samples "
        "WHERE room_name=? AND temperature IS NOT NULL AND ac_power IS NOT NULL "
        "ORDER BY timestamp", (room,)).fetchall()
    out = []
    for ts, tin, power in rows:
        th = house.get(ts)
        if th is None:
            continue
        out.append((datetime.fromisoformat(ts), tin, th, power))
    return out


def _drift_points(samples):
    """Punti (x=T_house-T_int, y=dT/dt) dai tratti AC-OFF in salita."""
    pts = []
    seg = []

    def flush(seg):
        if len(seg) < 3 or seg[-1][1] - seg[0][1] < _MIN_RISE:
            return
        for (t0, tin0, th0, _), (t1, tin1, _, _) in zip(seg, seg[1:]):
            dt = (t1 - t0).total_seconds()
            if dt <= 0:
                continue
            pts.append((th0 - tin0, (tin1 - tin0) / dt))

    for s in samples:
        ts, tin, th, power = s
        if str(power).lower() != "off":
            flush(seg); seg = []
            continue
        if seg and (ts - seg[-1][0]).total_seconds() > _MAX_GAP_S:
            flush(seg); seg = []
        seg.append(s)
    flush(seg)
    return pts


def _ols(pts):
    """Regressione y=a*x+b. Ritorna (a, b, n, r2)."""
    n = len(pts)
    sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts)
    sxx = sum(p[0]*p[0] for p in pts); sxy = sum(p[0]*p[1] for p in pts)
    den = n * sxx - sx * sx
    if abs(den) < 1e-12:
        return None
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    my = sy / n
    ss_tot = sum((p[1] - my) ** 2 for p in pts)
    ss_res = sum((p[1] - (a * p[0] + b)) ** 2 for p in pts)
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return a, b, n, r2


def calibrate(db_path: str = "db/climate.db",
              params_path: str = "config/thermal_params.json",
              room: str = ROOM) -> bool:
    """Esegue una calibrazione. Ritorna True se ha aggiornato i parametri."""
    con = sqlite3.connect(db_path)
    samples = _load_samples(con, room)
    pts = _drift_points(samples)
    logger.info("Calibrazione %s: %d campioni, %d punti drift AC-off.",
                room, len(samples), len(pts))
    if len(pts) < _MIN_POINTS:
        logger.info("Drift insufficienti (<%d): nessun aggiornamento.", _MIN_POINTS)
        return False

    fit = _ols(pts)
    if not fit:
        return False
    a, b, n, r2 = fit
    if a <= 0:
        logger.warning("Pendenza non fisica (a=%.2e): scarto.", a)
        return False
    if r2 < _R2_MIN:
        logger.info("Fit rumoroso (R²=%.2f < %.2f): non aggiorno (dati troppo "
                    "piatti/quantizzati).", r2, _R2_MIN)
        return False
    tau_h = 1.0 / a / 3600.0
    if not (_TAU_MIN_H <= tau_h <= _TAU_MAX_H):
        logger.warning("tau fuori range (%.1f h): scarto.", tau_h)
        return False

    p = RoomThermalParams.load(params_path)
    UA_tot = p.UA + p.UA_house     # dalla fisica/geometria
    C_new = UA_tot / a             # C = (UA+UA_house) * tau
    Q_new = b * C_new

    import json
    import os
    C_prev = None
    if os.path.exists(params_path):
        try:
            C_prev = json.load(open(params_path)).get("C")
        except Exception:  # noqa: BLE001
            C_prev = None
    C_final = C_new if C_prev is None else 0.7 * C_new + 0.3 * C_prev
    p.C = C_final
    p.save(params_path, extra={
        "calib_points": n,
        "calib_r2": round(r2, 3),
        "calib_last": datetime.now().isoformat(timespec="seconds"),
        "tau_hours_est": round(C_final / UA_tot / 3600.0, 2),
        "C_this_run": round(C_new),
        "Q_drift_est": round(Q_new),
    })
    logger.info("Calibrato %s: tau=%.1fh C=%.0f kJ/°C R²=%.2f (%d punti).",
                room, C_final / UA_tot / 3600.0, C_final / 1000, r2, n)
    return True


def calibrate_all(db_path: str = "db/climate.db") -> int:
    """Calibra OGNI stanza che ha un file parametri e dati di sensore. Ritorna
    quante stanze sono state aggiornate. Le stanze senza sensore (es. Salotto)
    o senza drift sufficienti vengono saltate in sicurezza (calibrate -> False)."""
    import os
    from core.config import load_config
    try:
        rooms = [r.name for r in load_config("config/config.yaml").rooms]
    except Exception as exc:  # noqa: BLE001 - fallback alla sola camerina
        logger.warning("Config non caricabile (%s): calibro solo %s", exc, ROOM)
        rooms = [ROOM]
    updated = 0
    for name in rooms:
        path = _params_path(name)
        if not os.path.exists(path):
            continue
        try:
            if calibrate(db_path, path, room=name):
                updated += 1
        except Exception as exc:  # noqa: BLE001 - una stanza non blocca le altre
            logger.warning("Calibrazione '%s' fallita: %s", name, exc)
    logger.info("Calibrazione completata: %d stanze aggiornate.", updated)
    return updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    calibrate_all()
