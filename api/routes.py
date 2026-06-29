"""
api/routes.py — REST API FastAPI (porta 8000).

Espone lo stato delle stanze, lo storico, i log, la configurazione (regole e
orario di spegnimento) e l'override manuale per stanza.

I componenti runtime (config, db, ac_controller, rule_engine, scheduler) sono
iniettati da main.py tramite AppContext, popolato all'avvio con init_context().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.models import (
    ACState,
    ConnectionStatus,
    ControlRequest,
    LogEntry,
    OverrideRequest,
    ReadingPoint,
    RoomState,
    RulesUpdate,
    ScheduleUpdate,
    WeatherPoint,
    WeatherState,
)
from core import config as config_module
from core.config import Action
from core.weather import weather_description

logger = logging.getLogger("climate.api")
router = APIRouter(prefix="/api")


@dataclass
class AppContext:
    """Riferimenti ai componenti runtime, condivisi con gli endpoint."""
    config: object = None
    database: object = None
    ac_controller: object = None
    rule_engine: object = None
    scheduler: object = None
    season_manager: object = None
    presence_manager: object = None
    light_controller: object = None
    weather_provider: object = None
    boiler_controller: object = None
    # Flag di stato connessioni, aggiornati dai componenti.
    dirigera_connected: bool = True
    panasonic_connected: bool = True


# Singleton popolato da main.py.
ctx = AppContext()


def init_context(**kwargs) -> None:
    """Popola il contesto applicativo con le istanze runtime."""
    for key, value in kwargs.items():
        setattr(ctx, key, value)


def _require(component, name: str):
    if component is None:
        raise HTTPException(status_code=503, detail=f"{name} non inizializzato")
    return component


# ===========================================================================
# Helper per i dati 'derivati' della home (comfort, WiFi, energia, Home Engine)
# ===========================================================================
_SENSOR_FRESH_S = 1800.0  # sensore considerato 'fresco' entro 30 min


def _wifi_signal() -> Optional[float]:
    """Livello segnale WiFi (dBm) da /proc/net/wireless (gira sul Pi). None se assente."""
    try:
        with open("/proc/net/wireless") as f:
            for line in f:
                if ":" in line and "Inter" not in line and "face" not in line:
                    return float(line.split()[3].rstrip("."))
    except Exception:  # noqa: BLE001 - non-Linux o niente WiFi
        return None
    return None


def _wifi_quality(dbm: Optional[float]) -> Optional[int]:
    """dBm -> qualita' % (mappa lineare -90 dBm=0% .. -40 dBm=100%)."""
    if dbm is None:
        return None
    return max(0, min(100, int(round((dbm + 90) * 2))))


def _wifi_label(dbm: Optional[float]) -> str:
    if dbm is None:
        return "—"
    if dbm >= -55:
        return "Ottima"
    if dbm >= -67:
        return "Buona"
    if dbm >= -75:
        return "Discreta"
    return "Debole"


def _comfort_score(temp, hum, band, season: Optional[str]) -> Optional[int]:
    """Punteggio comfort 0-100 di una stanza data la T/RH e la banda di comfort
    stagionale. 100 dentro la banda; cala fuori (T pesa piu' dell'umidita')."""
    if temp is None or band is None:
        return None
    target = band.target_temp
    deadband = band.deadband or 1.0
    if season == "riscaldamento":
        lo, hi = target - deadband, target + 2.0
    else:  # raffrescamento / mezza stagione
        lo, hi = target - 2.0, target + deadband
    if temp < lo:
        tpen = (lo - temp) * 10.0
    elif temp > hi:
        tpen = (temp - hi) * 12.0
    else:
        tpen = 0.0
    hpen = 0.0
    rh_max = band.humidity_dry_threshold or 65.0
    if hum is not None and hum > rh_max:
        hpen = (hum - rh_max) * 1.2
    return max(0, min(100, int(round(100 - tpen - hpen))))


def _energy_derived(data: dict, rate: float) -> dict:
    """Campi derivati per le card energia: ieri, media 7gg, proiezione, delta %."""
    days = data.get("days", [])
    today = datetime.now().strftime("%Y%m%d")
    today_kwh = data.get("today_kwh", 0.0)
    past = [d["kwh"] for d in days if d["day"] != today]
    yest = past[-1] if past else None
    last7 = past[-7:]
    avg7 = round(sum(last7) / len(last7), 1) if last7 else None
    projected = round(max(today_kwh, avg7), 1) if avg7 is not None else round(today_kwh, 1)
    delta_pct = round((today_kwh - yest) / yest * 100) if yest else None
    return {
        "yesterday_kwh": yest,
        "avg7_kwh": avg7,
        "projected_today_kwh": projected,
        "delta_pct": delta_pct,
        "today_cost": round(today_kwh * rate, 2),
    }


# ===========================================================================
# Stato stanze
# ===========================================================================
async def _room_state(room, ac, db, engine) -> RoomState:
    """Costruisce lo stato di UNA stanza (usato in parallelo da get_rooms)."""
    latest = await db.get_latest_reading(room.name)
    ac_state: Optional[ACState] = None
    if ac is not None and room.panasonic_device_id:
        try:
            # use_cache=True: risponde dalla cache (istantaneo) se fresca.
            s = await ac.get_device_state(room.panasonic_device_id, use_cache=True)
            energy = await ac.get_today_energy(room.panasonic_device_id)
            ac_state = ACState(
                **s, reachable=True,
                energy_today_kwh=energy["consumption"] if energy else None,
                energy_cooling_kwh=energy["cooling"] if energy else None,
                energy_heating_kwh=energy["heating"] if energy else None,
            )  # s include gia' nanoe e swing_vertical
            ctx.panasonic_connected = True
        except Exception as exc:  # noqa: BLE001 - cloud down: degrada
            ac_state = ACState(reachable=False, error=str(exc))
            ctx.panasonic_connected = False

    return RoomState(
        name=room.name,
        has_sensor=bool(
            room.ikea_sensor_id
            or getattr(room, "remote_sensor_url", None)
            or getattr(room, "switchbot_mac", None)
        ),
        temperature=latest["temperature"] if latest else None,
        humidity=latest["humidity"] if latest else None,
        pressure=latest["pressure"] if latest else None,
        lux=latest["lux"] if latest else None,
        last_reading=latest["timestamp"] if latest else None,
        ac=ac_state,
        override_active=engine.is_overridden(room.name) if engine else False,
        override_remaining_seconds=(
            engine.override_remaining_seconds(room.name) if engine else 0),
    )


@router.get("/rooms", response_model=list[RoomState])
async def get_rooms() -> list[RoomState]:
    """Stato attuale di tutte le stanze: temp, umidita', stato AC, override.

    Le stanze sono lette IN PARALLELO e lo stato AC viene servito dalla cache:
    la dashboard riceve la risposta in millisecondi invece di attendere il cloud.
    """
    import asyncio
    cfg = _require(ctx.config, "config")
    db = _require(ctx.database, "database")
    ac = ctx.ac_controller
    engine = ctx.rule_engine
    return await asyncio.gather(*[_room_state(r, ac, db, engine) for r in cfg.rooms])


@router.get("/rooms/{room_name}/history", response_model=list[ReadingPoint])
async def get_room_history(room_name: str, hours: int = Query(24, ge=1, le=720)):
    """Storico letture sensore nelle ultime N ore."""
    db = _require(ctx.database, "database")
    cfg = _require(ctx.config, "config")
    if cfg.get_room(room_name) is None:
        raise HTTPException(status_code=404, detail=f"Stanza '{room_name}' non trovata")
    rows = await db.get_recent_readings(room_name, hours=hours)
    return [ReadingPoint(**r) for r in rows]


# ===========================================================================
# Configurazione
# ===========================================================================
@router.get("/config")
async def get_config() -> dict:
    """Configurazione corrente SENZA credenziali in chiaro."""
    cfg = _require(ctx.config, "config")
    return config_module.redacted_config_dict(cfg)


@router.put("/config/rooms/{room_name}/rules")
async def put_room_rules(room_name: str, body: RulesUpdate) -> dict:
    """Aggiorna le regole di una stanza (riscrive config.yaml e ricarica)."""
    cfg = _require(ctx.config, "config")
    if cfg.get_room(room_name) is None:
        raise HTTPException(status_code=404, detail=f"Stanza '{room_name}' non trovata")

    rules_raw = [
        {
            "condition": {k: v for k, v in r.condition.model_dump().items() if v is not None},
            "action": r.action.model_dump(),
        }
        for r in body.rules
    ]
    try:
        config_module.update_room_rules(rules_raw, room_name, cfg.path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    _reload_config()
    return {"status": "ok", "room": room_name, "rules": len(rules_raw)}


@router.put("/config/schedule")
async def put_schedule(body: ScheduleUpdate) -> dict:
    """Aggiorna l'orario di spegnimento forzato e riprogramma lo scheduler."""
    cfg = _require(ctx.config, "config")
    config_module.update_schedule(body.force_off_time, cfg.path)
    cfg.force_off_time = body.force_off_time
    if ctx.scheduler is not None:
        ctx.scheduler.reschedule(body.force_off_time)
    return {"status": "ok", "force_off_time": body.force_off_time}


def _reload_config() -> None:
    """Ricarica il config dal file e aggiorna i componenti che ne dipendono."""
    new_cfg = config_module.load_config(ctx.config.path)
    ctx.config = new_cfg
    # Propaga la nuova config ai componenti che la usano a runtime.
    if ctx.rule_engine is not None:
        ctx.rule_engine._cfg = new_cfg
    if ctx.scheduler is not None:
        ctx.scheduler._cfg = new_cfg


# ===========================================================================
# Log automazioni
# ===========================================================================
@router.get("/logs", response_model=list[LogEntry])
async def get_logs(limit: int = Query(100, ge=1, le=1000)):
    """Ultimi N eventi di automazione."""
    db = _require(ctx.database, "database")
    rows = await db.get_recent_logs(limit=limit)
    return [LogEntry(**r) for r in rows]


# ===========================================================================
# Override manuale
# ===========================================================================
@router.post("/rooms/{room_name}/ac/override")
async def post_override(room_name: str, body: OverrideRequest) -> dict:
    """
    Override manuale: applica subito lo stato richiesto e sospende il rule
    engine per `minutes` minuti (default 60), poi torna automatico.
    """
    cfg = _require(ctx.config, "config")
    engine = _require(ctx.rule_engine, "rule_engine")
    room = cfg.get_room(room_name)
    if room is None:
        raise HTTPException(status_code=404, detail=f"Stanza '{room_name}' non trovata")
    if not room.panasonic_device_id:
        raise HTTPException(status_code=400, detail="Stanza senza condizionatore associato")

    action = Action(power=body.power, mode=body.mode,
                    temperature=body.temperature, fan_speed=body.fan_speed)
    try:
        await engine.apply_override_now(room_name, action, minutes=body.minutes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Comando AC fallito: {exc}")
    return {"status": "ok", "room": room_name,
            "override_minutes": body.minutes}


@router.post("/rooms/{room_name}/ac/control")
async def post_control(room_name: str, body: ControlRequest) -> dict:
    """
    Controllo diretto stile termostato: applica SOLO i campi inviati, fondendoli
    con lo stato attuale dell'AC (così un tocco sul +/- non resetta modo/ventola).
    Imposta un override automatico per `minutes` minuti per non farsi sovrascrivere
    subito dal rule engine.
    """
    cfg = _require(ctx.config, "config")
    engine = _require(ctx.rule_engine, "rule_engine")
    ac = _require(ctx.ac_controller, "ac_controller")
    room = cfg.get_room(room_name)
    if room is None:
        raise HTTPException(status_code=404, detail=f"Stanza '{room_name}' non trovata")
    if not room.panasonic_device_id:
        raise HTTPException(status_code=400, detail="Stanza senza condizionatore associato")

    # Stato attuale come base; i campi inviati lo sovrascrivono (merge).
    try:
        cur = await ac.get_device_state(room.panasonic_device_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Stato AC non leggibile: {exc}")

    merged = Action(
        power=body.power if body.power is not None else (cur.get("power") == "On"),
        mode=body.mode if body.mode is not None else cur.get("mode"),
        temperature=(body.temperature if body.temperature is not None
                     else cur.get("target_temperature")),
        fan_speed=body.fan_speed if body.fan_speed is not None else cur.get("fan_speed"),
    )
    # nanoe/swing/eco non sono parte di Action: li passo a parte al controller.
    nanoe = body.nanoe if body.nanoe is not None else None
    swing = body.swing_vertical if body.swing_vertical is not None else None
    eco = body.eco_mode if body.eco_mode is not None else None

    try:
        engine.set_override(room_name, merged, minutes=body.minutes)
        await ac.set_device_state(
            room.panasonic_device_id,
            power=merged.power, mode=merged.mode,
            temperature=merged.temperature, fan_speed=merged.fan_speed,
            nanoe=nanoe, swing_vertical=swing, eco_mode=eco,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Comando AC fallito: {exc}")
    return {"status": "ok", "room": room_name}


@router.delete("/rooms/{room_name}/ac/override")
async def delete_override(room_name: str) -> dict:
    """Rimuove l'override manuale e riattiva il rule engine."""
    engine = _require(ctx.rule_engine, "rule_engine")
    cfg = _require(ctx.config, "config")
    if cfg.get_room(room_name) is None:
        raise HTTPException(status_code=404, detail=f"Stanza '{room_name}' non trovata")
    engine.clear_override(room_name)
    return {"status": "ok", "room": room_name, "override": "cleared"}


# ===========================================================================
# Scene multi-stanza (comandi vocali Alexa: "protocollo afa", "spegni tutto")
# ===========================================================================
@router.get("/scenes")
async def get_scenes() -> dict:
    """Elenco delle scene disponibili (per il bridge vocale / debug)."""
    from core import scenes
    return {"scenes": scenes.scene_names()}


@router.post("/scene/{scene_name}")
async def post_scene(scene_name: str) -> dict:
    """
    Esegue una scena su TUTTI i condizionatori (es. 'afa' = massimo freddo,
    'off' = spegni tutto). Applica un override che sospende l'automazione per
    un po', cosi' la scena non viene disfatta al ciclo successivo.

    E' il punto chiamato dal bridge Matter quando Alexa accende l'interruttore
    virtuale corrispondente.
    """
    from core import scenes
    cfg = _require(ctx.config, "config")
    engine = _require(ctx.rule_engine, "rule_engine")
    ac = _require(ctx.ac_controller, "ac_controller")
    try:
        return await scenes.run_scene(scene_name, cfg, engine, ac)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Scena '{scene_name}' sconosciuta (disponibili: "
                   f"{', '.join(scenes.scene_names())})")


# ===========================================================================
# Caldaia (relè Sonoff in LAN) — stanza a sé, toggle ON/OFF
# ===========================================================================
@router.get("/boiler")
async def get_boiler() -> dict:
    """Stato del relè caldaia (letto in locale via mDNS). enabled=False se assente."""
    b = ctx.boiler_controller
    if b is None:
        return {"enabled": False}
    st = b.get_state()
    cfg = ctx.config
    st["room"] = cfg.boiler.room if (cfg and getattr(cfg, "boiler", None)) else "Cucina"
    return st


@router.post("/boiler")
async def post_boiler(body: dict) -> dict:
    """Accende/spegne la caldaia: {"on": true|false}. Comando locale cifrato."""
    b = _require(ctx.boiler_controller, "boiler")
    on = bool(body.get("on"))
    try:
        ok = await b.set(on)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Comando caldaia fallito: {exc}")
    if not ok:
        raise HTTPException(status_code=502, detail="Il device ha rifiutato il comando")
    return {"status": "ok", "on": on}


# ===========================================================================
# Luci IKEA
# ===========================================================================
@router.get("/lights")
async def get_lights() -> dict:
    """Luci IKEA raggruppate per stanza."""
    lc = _require(ctx.light_controller, "light_controller")
    return await lc.get_lights_by_room()


@router.post("/lights/{light_id}")
async def set_light(light_id: str, body: dict) -> dict:
    """Comanda una luce: {on?: bool, level?: 1-100, color_temp?: int}."""
    lc = _require(ctx.light_controller, "light_controller")
    try:
        await lc.set_light(
            light_id,
            on=body.get("on"),
            level=body.get("level"),
            color_temp=body.get("color_temp"),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Comando luce fallito: {exc}")
    return {"status": "ok", "light": light_id}


@router.post("/lights/room/{room_name}")
async def set_room_lights(room_name: str, body: dict) -> dict:
    """Comanda tutte le luci di una stanza: {on?: bool, level?: 1-100}."""
    lc = _require(ctx.light_controller, "light_controller")
    try:
        n = await lc.set_room(room_name, on=body.get("on"), level=body.get("level"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Comando luci fallito: {exc}")
    return {"status": "ok", "room": room_name, "lights": n}


# ===========================================================================
# Stato connessioni (header dashboard)
# ===========================================================================
@router.get("/status", response_model=ConnectionStatus)
async def get_status() -> ConnectionStatus:
    """Stato connessioni (Dirigera/Panasonic) e stagione corrente."""
    season = None
    outdoor = None
    if ctx.season_manager is not None:
        try:
            season = ctx.season_manager.season.value
            outdoor = ctx.season_manager.rolling_average()
            if outdoor is not None:
                outdoor = round(outdoor, 1)
        except Exception:  # noqa: BLE001
            pass
    presence = None
    presence_home = None
    presence_people = []
    pm = ctx.presence_manager
    if pm is not None and getattr(pm, "_cfg", None) is not None and pm._cfg.enabled:
        try:
            presence = pm.state.value
            presence_home = pm.is_home()
            presence_people = pm.people_home()
        except Exception:  # noqa: BLE001
            pass
    return ConnectionStatus(
        dirigera=ctx.dirigera_connected,
        panasonic=ctx.panasonic_connected,
        season=season,
        outdoor_avg_temperature=outdoor,
        presence=presence,
        presence_home=presence_home,
        presence_people=presence_people,
    )


@router.get("/weather", response_model=WeatherState)
async def get_weather() -> WeatherState:
    """Meteo esterno corrente e previsione breve da Open-Meteo."""
    weather = ctx.weather_provider
    if weather is None:
        return WeatherState()
    try:
        payload = await weather._fetch()  # cache interna del provider, niente doppie chiamate
    except Exception:  # noqa: BLE001 - degrada senza rompere la dashboard
        logger.exception("Meteo non disponibile")
        return WeatherState()
    if not payload:
        return WeatherState()
    now_hour = datetime.now().strftime("%Y-%m-%dT%H:00")
    forecast = [
        WeatherPoint(time=t, temperature=round(v, 1) if v is not None else None)
        for t, v, _h in (payload.get("hourly") or [])
        if t >= now_hour
    ][:12]
    temp = payload.get("current")
    hum = payload.get("humidity")
    app_t = payload.get("apparent")
    wind = payload.get("wind_speed")
    uv = payload.get("uv_index")
    pop = payload.get("precipitation_probability")
    loc = getattr(ctx.config, "location_name", None) if ctx.config else None
    return WeatherState(
        temperature=round(temp, 1) if temp is not None else None,
        humidity=round(hum, 0) if hum is not None else None,
        apparent_temperature=round(app_t, 1) if app_t is not None else None,
        description=weather_description(payload.get("weather_code")),
        wind_speed=round(wind, 0) if wind is not None else None,
        precipitation_probability=round(pop, 0) if pop is not None else None,
        uv_index=round(uv, 1) if uv is not None else None,
        location=loc,
        forecast=forecast,
    )


@router.get("/rooms/{room_name}/detail")
async def get_room_detail(room_name: str) -> dict:
    """Dati REALI aggiuntivi per la pagina di una stanza: comfort, tempo AC oggi
    (da mpc_samples), energia/costo AC oggi, prossima azione programmata, presenza.
    Niente dati inventati: cio' che manca (CO2, qualita' aria, ecc.) non e' qui."""
    db = _require(ctx.database, "database")
    cfg = ctx.config
    room = None
    if cfg:
        for r in cfg.rooms:
            if r.name == room_name:
                room = r
                break
    season = None
    if ctx.season_manager is not None:
        try:
            season = ctx.season_manager.season.value
        except Exception:  # noqa: BLE001
            pass

    # comfort della stanza
    comfort = None
    latest = await db.get_latest_reading(room_name)
    if room is not None and latest and latest.get("temperature") is not None:
        band = None
        if getattr(room, "comfort", None):
            band = room.comfort.winter if season == "riscaldamento" else room.comfort.summer
        comfort = _comfort_score(latest["temperature"], latest.get("humidity"), band, season)

    # tempo AC oggi (stima da campioni periodici)
    runtime_min = await db.get_ac_runtime_today(room_name)

    # energia/costo AC oggi (dal device Panasonic della stanza)
    rate = 0.0
    if cfg and getattr(cfg, "tariff", None):
        rate = round(cfg.tariff.variable_eur_kwh * (1 + cfg.tariff.vat_rate), 4)
    energy_kwh = None
    if ctx.ac_controller is not None and room is not None and room.panasonic_device_id:
        try:
            e = await ctx.ac_controller.get_today_energy(room.panasonic_device_id)
            energy_kwh = e["consumption"] if e else None
        except Exception:  # noqa: BLE001
            energy_kwh = None
    cost = round(energy_kwh * rate, 2) if energy_kwh is not None else None

    # prossima azione programmata (spegnimento notturno dallo scheduler)
    next_action = None
    if cfg and getattr(cfg, "force_off_time", None):
        next_action = {"label": "Spegnimento programmato", "time": cfg.force_off_time}

    # presenza (globale: per-stanza serve il sensore mmWave, non ancora installato)
    presence_home = None
    people = None
    pm = ctx.presence_manager
    if pm is not None and getattr(pm, "_cfg", None) is not None and pm._cfg.enabled:
        try:
            presence_home = pm.is_home()
            people = len(pm.people_home())
        except Exception:  # noqa: BLE001
            pass

    return {
        "comfort": comfort,
        "ac_runtime_today_min": runtime_min,
        "ac_energy_today_kwh": energy_kwh,
        "ac_cost_today": cost,
        "next_action": next_action,
        "presence_home": presence_home,
        "people": people,
    }


@router.get("/energy/month")
async def get_energy_month() -> dict:
    """Consumo GIORNALIERO del mese corrente (totale d'IMPIANTO, non per-AC: il
    consumo Panasonic e' unico) + costo reale dalla tariffa di config."""
    db = _require(ctx.database, "database")
    data = await db.get_month_energy()
    cfg = ctx.config
    rate = 0.0
    if cfg and getattr(cfg, "tariff", None):
        rate = round(cfg.tariff.variable_eur_kwh * (1 + cfg.tariff.vat_rate), 4)
    for d in data["days"]:
        d["cost"] = round(d["kwh"] * rate, 2)
    data["today_cost"] = round(data["today_kwh"] * rate, 2)
    data["month_cost"] = round(data["month_kwh"] * rate, 2)
    data["rate"] = rate
    data.update(_energy_derived(data, rate))
    return data


@router.get("/overview")
async def get_overview() -> dict:
    """Dati 'derivati' per la home: comfort, salute impianti, WiFi, Home Engine.

    Tutto da fonti reali gia' in funzione: letture sensori (comfort), flag
    connessione, /proc/net/wireless (WiFi), aggregazione energia e consigli del
    controllo predittivo (mpc_advisory) per la card Home Engine."""
    import re

    cfg = ctx.config
    db = _require(ctx.database, "database")
    season = None
    if ctx.season_manager is not None:
        try:
            season = ctx.season_manager.season.value
        except Exception:  # noqa: BLE001
            pass

    # --- comfort per stanza + casa, e freschezza sensori (un solo giro) -----
    rooms_comfort: dict[str, int] = {}
    scores: list[int] = []
    sensor_total = sensor_ok = 0
    if cfg:
        for room in cfg.rooms:
            has_sensor = bool(
                room.ikea_sensor_id
                or getattr(room, "remote_sensor_url", None)
                or getattr(room, "switchbot_mac", None))
            latest = await db.get_latest_reading(room.name)
            if has_sensor:
                sensor_total += 1
                if latest and latest.get("timestamp"):
                    try:
                        age = (datetime.now() - datetime.fromisoformat(latest["timestamp"])).total_seconds()
                        if age <= _SENSOR_FRESH_S:
                            sensor_ok += 1
                    except Exception:  # noqa: BLE001
                        pass
            if not latest or latest.get("temperature") is None:
                continue
            band = None
            if getattr(room, "comfort", None):
                band = (room.comfort.winter if season == "riscaldamento"
                        else room.comfort.summer)
            sc = _comfort_score(latest["temperature"], latest.get("humidity"), band, season)
            if sc is not None:
                rooms_comfort[room.name] = sc
                scores.append(sc)
    comfort_home = int(round(sum(scores) / len(scores))) if scores else None

    # --- WiFi ---------------------------------------------------------------
    dbm = _wifi_signal()
    wifi = {"dbm": dbm, "quality": _wifi_quality(dbm), "label": _wifi_label(dbm)}

    # --- salute impianti ----------------------------------------------------
    systems = [
        {"key": "home_engine", "name": "Home Engine", "online": True, "detail": "attivo"},
        {"key": "panasonic", "name": "Panasonic", "online": bool(ctx.panasonic_connected),
         "detail": "online" if ctx.panasonic_connected else "offline"},
        {"key": "dirigera", "name": "Dirigera", "online": bool(ctx.dirigera_connected),
         "detail": "online" if ctx.dirigera_connected else "offline"},
        {"key": "sensori", "name": "Sensori", "online": sensor_ok == sensor_total and sensor_total > 0,
         "detail": f"{sensor_ok}/{sensor_total} online"},
        {"key": "wifi", "name": "Rete Wi-Fi", "online": dbm is not None,
         "detail": (f"{int(dbm)} dBm" if dbm is not None else "—")},
    ]

    # --- energia oggi + proiezione ------------------------------------------
    rate = 0.0
    if cfg and getattr(cfg, "tariff", None):
        rate = round(cfg.tariff.variable_eur_kwh * (1 + cfg.tariff.vat_rate), 4)
    em = await db.get_month_energy()
    ed = _energy_derived(em, rate)

    # --- Home Engine: prossima decisione + suggerimento dal MPC -------------
    act_map = {"Cool": "Raffredda", "Dry": "Deumidifica", "Pre-raffr.": "Pre-raffredda"}
    next_decision = "Nessuna"
    suggestion = "Nessun suggerimento disponibile."
    try:
        advisories = await db.get_latest_advisories()
    except Exception:  # noqa: BLE001
        advisories = []
    decisions: list[str] = []
    worst = None
    for a in advisories:
        msg = a.get("message") or ""
        m = re.search(r"consiglio:\s*([A-Za-z\-\.]+)", msg)
        act = m.group(1) if m else None
        if act in act_map:
            decisions.append(f"{act_map[act]} {a['room_name']}")
        tn, tp = a.get("temp_now"), a.get("temp_pred_end")
        if tn is not None and tp is not None:
            rise = tp - tn
            if worst is None or rise > worst[0]:
                worst = (rise, a["room_name"], tn, tp)
    if decisions:
        next_decision = decisions[0]
    if worst and worst[0] > 0.3:
        suggestion = (f"Tra ~6h {worst[1]} salira' a {worst[3]:.0f}° "
                      f"(ora {worst[2]:.0f}°).")
    elif worst:
        suggestion = (f"{worst[1]} stabile (~{worst[3]:.0f}° tra 6h). "
                      f"Nessuna azione richiesta.")

    home_engine = {
        "stable": comfort_home is not None and comfort_home >= 80,
        "comfort": comfort_home,
        "projected_kwh_today": ed["projected_today_kwh"],
        "next_decision": next_decision,
        "suggestion": suggestion,
    }

    return {
        "comfort_home": comfort_home,
        "rooms_comfort": rooms_comfort,
        "wifi": wifi,
        "systems": systems,
        "energy": {**em, **ed},
        "home_engine": home_engine,
    }
