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
        for t, v in (payload.get("hourly") or [])
        if t >= now_hour
    ][:12]
    temp = payload.get("current")
    hum = payload.get("humidity")
    return WeatherState(
        temperature=round(temp, 1) if temp is not None else None,
        humidity=round(hum, 0) if hum is not None else None,
        forecast=forecast,
    )
