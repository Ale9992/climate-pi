"""
main.py — Entry point del sistema Climate Automation.

Avvia in parallelo con asyncio:
  - connessione all'hub Dirigera (con retry) e all'ACController Panasonic;
  - SensorPoller (WebSocket + polling di fallback);
  - RuleEngine collegato agli eventi del poller;
  - ForceOffScheduler (spegnimento forzato 03:00);
  - server FastAPI/uvicorn sulla porta 8000 (API + dashboard statica);
  - gestione graceful shutdown su SIGINT/SIGTERM.

Logging strutturato su file con RotatingFileHandler (10MB, 3 backup) + console.
Uso:
    python main.py            # produzione
    DEV=1 python main.py      # log verbose in console
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import dirigera

from api import routes
from core.ac_controller import ACController
from core.boiler import BoilerController
from core.config import load_config
from core.energy_history import EnergyHistoryLogger
from core.light_controller import LightController
from core.mpc_advisor import MpcAdvisor
from core.mpc_logger import MpcLogger
from core.presence import PresenceManager
from core.remote_sensor_reader import RemoteSensorReader
from core.rule_engine import RuleEngine
from core.scheduler import ForceOffScheduler
from core.season import SeasonManager
from core.sensor_poller import SensorPoller
from core.switchbot_reader import SwitchBotReader
from core.weather import WeatherProvider
from db.database import Database

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"
DB_PATH = PROJECT_ROOT / "db" / "climate.db"
DASHBOARD_DIST = PROJECT_ROOT / "dashboard" / "dist"

logger = logging.getLogger("climate")


# ===========================================================================
# Logging
# ===========================================================================
def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if os.getenv("DEV") else logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        LOG_DIR / "climate.log", maxBytes=10 * 1024 * 1024, backupCount=3,
        encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.INFO)
    root.addHandler(file_handler)

    if os.getenv("DEV"):
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        console.setLevel(logging.DEBUG)
        root.addHandler(console)
    else:
        # In produzione un livello INFO anche su console (systemd journal).
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        console.setLevel(logging.INFO)
        root.addHandler(console)


# ===========================================================================
# Connessione hub Dirigera con retry
# ===========================================================================
async def connect_dirigera(ip: str, token: str, retry_seconds: int = 30):
    """Crea l'Hub Dirigera; ritenta ogni retry_seconds se non raggiungibile."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            hub = dirigera.Hub(token=token, ip_address=ip)
            # get_all_devices valida la connessione (chiamata sincrona).
            await loop.run_in_executor(None, hub.get_all_devices)
            logger.info("Hub Dirigera connesso (%s).", ip)
            return hub
        except Exception as exc:  # noqa: BLE001
            logger.error("Hub Dirigera non raggiungibile (%s): %s — ritento tra %ds",
                         ip, exc, retry_seconds)
            await asyncio.sleep(retry_seconds)


# ===========================================================================
# Applicazione FastAPI
# ===========================================================================
def build_app() -> FastAPI:
    app = FastAPI(title="Climate Automation", version="1.0")
    app.include_router(routes.router)

    # No-cache su index.html: gli asset hanno l'hash nel nome (cache-busting),
    # ma l'index va riletto sempre o il browser resta su un bundle vecchio.
    @app.middleware("http")
    async def _no_cache_index(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith(".html"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    # Serve la dashboard React buildata, se presente.
    if DASHBOARD_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(DASHBOARD_DIST), html=True),
                  name="dashboard")
        logger.info("Dashboard montata da %s", DASHBOARD_DIST)
    else:
        logger.warning("Dashboard non buildata (%s assente): solo API attive.",
                       DASHBOARD_DIST)
    return app


# ===========================================================================
# Orchestrazione
# ===========================================================================
async def run() -> None:
    setup_logging()
    logger.info("=== Climate Automation: avvio ===")

    cfg = load_config()

    # --- DB ---
    db = Database(DB_PATH)
    await db.connect()

    # --- Dirigera (con retry) ---
    hub = await connect_dirigera(cfg.dirigera_ip, cfg.dirigera_token)

    # --- Panasonic AC ---
    ac = ACController(cfg.panasonic_username, cfg.panasonic_password, db)
    try:
        await ac.connect()
        panasonic_ok = True
    except Exception as exc:  # noqa: BLE001 - cloud down all'avvio: continua
        logger.error("Panasonic Cloud non raggiungibile all'avvio: %s "
                     "(ritentera' ai prossimi comandi)", exc)
        panasonic_ok = False

    # --- Algoritmo stagionale ---
    season_manager = SeasonManager(cfg.season, ac)

    # --- Rilevamento presenza (FRITZ!Box) ---
    # Le stanze monitor_only NON vengono spente alla transizione casa-vuota.
    ac_device_ids = [r.panasonic_device_id for r in cfg.rooms
                     if r.panasonic_device_id and not r.monitor_only]
    presence_manager = PresenceManager(cfg.presence, ac, ac_device_ids, database=db)

    # --- Luci IKEA (stesso hub Dirigera) ---
    light_controller = LightController(hub, ceiling_rooms=cfg.lights.ceiling_rooms)

    # --- Rule engine, poller, scheduler ---
    engine = RuleEngine(cfg, ac, db, season_manager=season_manager,
                        presence_manager=presence_manager)
    poller = SensorPoller(hub, cfg, db, engine)
    scheduler = ForceOffScheduler(cfg, ac, engine, db)

    # --- Meteo esterno (Open-Meteo): T esterna 0.1°C + previsione per l'MPC ---
    #     Coordinate dal config (location:); default generici se assenti.
    weather = WeatherProvider(cfg.latitude, cfg.longitude)

    # --- Datalogger MPC: raccoglie il dataset per il futuro modello predittivo
    #     (solo scrittura, non influenza l'automazione). ---
    mpc_logger = MpcLogger(cfg, ac, db, presence_manager=presence_manager,
                           weather_provider=weather)

    # --- Logger storico energia: tiene fresca panasonic_history (grafico consumi). ---
    energy_history = EnergyHistoryLogger(ac, db)

    # --- Reader SwitchBot (BLE): dà a una stanza senza sensore IKEA (Camera da
    #     letto) T/umidita' indoor reali, scritte in sensor_readings come un
    #     sensore qualsiasi. Solo scrittura, lettura BLE passiva. ---
    switchbot_reader = SwitchBotReader(cfg, db, rule_engine=engine)

    # --- Reader sensori remoti HTTP: nodi Pi Zero/BME280 in LAN. Se una stanza
    #     lo configura, la lettura remota sostituisce l'IKEA locale come sorgente
    #     ad alta precisione per storico, rule engine e MPC. ---
    remote_sensor_reader = RemoteSensorReader(cfg, db, rule_engine=engine)

    # --- MPC advisory: controllo predittivo in sola lettura (consiglia, non
    #     comanda). Gira il modello validato in avanti + meteo, logga i consigli. ---
    mpc_advisor = MpcAdvisor(cfg, db, weather, presence_manager=presence_manager)

    # --- Relè caldaia Sonoff (eWeLink LAN), se configurato: stanza a sé (Cucina),
    #     controllo locale cifrato + lettura stato passiva via mDNS. ---
    boiler = None
    if cfg.boiler and cfg.boiler.enabled:
        boiler = BoilerController(
            cfg.boiler.deviceid, cfg.boiler.devicekey,
            ip=cfg.boiler.ip, port=cfg.boiler.port, enabled=True)

    # --- Contesto API ---
    routes.init_context(
        config=cfg, database=db, ac_controller=ac, rule_engine=engine,
        scheduler=scheduler, season_manager=season_manager,
        presence_manager=presence_manager, light_controller=light_controller,
        weather_provider=weather, boiler_controller=boiler,
        dirigera_connected=True, panasonic_connected=panasonic_ok,
    )

    # --- Avvio componenti ---
    await season_manager.start()   # bootstrap media esterna + campionamento
    await presence_manager.start()  # connette FRITZ!Box e avvia il polling presenza
    # Riprende lo stato reale degli AC (es. dopo un black-out): il rule engine
    # sa subito cosa stanno facendo i condizionatori, prima del primo ciclo.
    if panasonic_ok:
        await engine.bootstrap_state()
    await poller.start()
    scheduler.start()
    await mpc_logger.start()
    await energy_history.start()
    await switchbot_reader.start()
    await remote_sensor_reader.start()
    await mpc_advisor.start()
    if boiler is not None:
        await boiler.start()

    # --- Warm-up cache AC: pre-carica stato + energia in background, cosi' la
    #     prima apertura della dashboard e' gia' istantanea (niente attesa cloud).
    async def _warm_cache() -> None:
        for r in cfg.rooms:
            if r.panasonic_device_id:
                try:
                    await ac.get_device_state(r.panasonic_device_id)
                    await ac.get_today_energy(r.panasonic_device_id)
                except Exception:  # noqa: BLE001
                    pass
        # Mantiene la cache calda: refresh periodico in background.
        while True:
            await asyncio.sleep(20)
            for r in cfg.rooms:
                if r.panasonic_device_id:
                    try:
                        await ac.get_device_state(r.panasonic_device_id)
                    except Exception:  # noqa: BLE001
                        pass

    warm_task = asyncio.create_task(_warm_cache(), name="ac-cache-warm")

    # --- Server uvicorn come task ---
    app = build_app()
    server = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=8000, log_level="info", access_log=False))
    # Gestiamo NOI i segnali (sotto): impedisci a uvicorn di installare i suoi
    # handler, altrimenti sovrascriverebbero quelli del nostro graceful shutdown.
    server.install_signal_handlers = lambda: None
    server_task = asyncio.create_task(server.serve(), name="uvicorn")

    # --- Graceful shutdown su segnali ---
    # Il segnale chiede a uvicorn di uscire (should_exit): quando serve()
    # ritorna, eseguiamo il teardown nel blocco finally. Niente Event separato:
    # legare lo shutdown al ritorno di uvicorn evita contese di lock col thread
    # del WebSocket che impedivano il risveglio del teardown.
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        logger.info("Segnale di arresto ricevuto.")
        server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover (Windows)
            pass

    logger.info("=== Sistema operativo. API su http://0.0.0.0:8000 ===")
    try:
        await server_task
    finally:
        # --- Teardown ordinato, con timeout su ogni step (mai bloccante) ---
        logger.info("=== Arresto in corso ===")

        async def _safe(coro, name: str, timeout: float = 10) -> None:
            try:
                await asyncio.wait_for(coro, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Teardown '%s' non pulito: %s", name, exc)

        warm_task.cancel()
        if boiler is not None:
            await _safe(boiler.stop(), "boiler")
        await _safe(mpc_advisor.stop(), "mpc_advisor")
        await _safe(remote_sensor_reader.stop(), "remote_sensor_reader")
        await _safe(switchbot_reader.stop(), "switchbot_reader")
        await _safe(energy_history.stop(), "energy_history")
        await _safe(mpc_logger.stop(), "mpc_logger")
        await _safe(poller.stop(), "sensor_poller")
        await _safe(season_manager.stop(), "season_manager")
        await _safe(presence_manager.stop(), "presence_manager")
        scheduler.shutdown()
        await _safe(ac.close(), "ac_controller")
        await _safe(weather.close(), "weather")
        await _safe(db.close(), "database")
        logger.info("=== Climate Automation: arresto completato ===")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
