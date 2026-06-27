"""
core/config.py — Caricamento e accesso al config.yaml.

Centralizza la lettura del config generato dal mapping tool, espone oggetti
tipizzati (dataclass) al resto del sistema ed evita che ogni modulo riparsifichi
il YAML a mano. Supporta anche la riscrittura (usata dagli endpoint PUT della
API per aggiornare regole e orario di spegnimento) preservando la struttura.

Niente valori hardcodati: tutte le soglie, gli intervalli e gli orari vengono
da qui.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# Percorso di default: <repo>/config/config.yaml
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


# ---------------------------------------------------------------------------
# Dataclass tipizzate che rispecchiano la struttura del config.yaml.
# ---------------------------------------------------------------------------
@dataclass
class Condition:
    """Condizione di una regola. Campi None = non vincolante."""
    temp_gt: Optional[float] = None
    temp_lt: Optional[float] = None
    humidity_gt: Optional[float] = None
    humidity_lt: Optional[float] = None

    def matches(self, temperature: Optional[float], humidity: Optional[float]) -> bool:
        """
        Valuta la condizione con AND logico tra tutti i vincoli presenti.
        Un vincolo su temperatura con temperatura assente => non soddisfatto.
        """
        if self.temp_gt is not None:
            if temperature is None or not temperature > self.temp_gt:
                return False
        if self.temp_lt is not None:
            if temperature is None or not temperature < self.temp_lt:
                return False
        if self.humidity_gt is not None:
            if humidity is None or not humidity > self.humidity_gt:
                return False
        if self.humidity_lt is not None:
            if humidity is None or not humidity < self.humidity_lt:
                return False
        # Una condizione completamente vuota non matcha mai (evita azioni cieche).
        return any(v is not None for v in (self.temp_gt, self.temp_lt,
                                           self.humidity_gt, self.humidity_lt))


@dataclass
class Action:
    """Azione da applicare al condizionatore quando una regola scatta."""
    power: bool = True
    mode: Optional[str] = None          # Cool / Heat / Dry / Fan / Auto
    temperature: Optional[float] = None
    fan_speed: Optional[str] = None      # Auto / Low / Mid / High
    eco_mode: Optional[str] = None       # Auto / Powerful / Quiet (boost vs silenzioso)

    def as_dict(self) -> dict[str, Any]:
        return {
            "power": self.power,
            "mode": self.mode,
            "temperature": self.temperature,
            "fan_speed": self.fan_speed,
            "eco_mode": self.eco_mode,
        }


@dataclass
class Rule:
    condition: Condition
    action: Action


@dataclass
class SeasonComfort:
    """
    Banda di comfort energy-aware per una stagione (estate o inverno).

    Logica con isteresi (evita over-cooling/heating e accensioni continue):
      - ESTATE: accende Cool se T >= target + deadband; spegne se T <= target - deadband;
                setpoint efficiente = `setpoint`; se T >= boost_temp usa boost_setpoint.
                Se umidita' > humidity_dry_threshold -> Dry (comfort, consuma poco).
      - INVERNO: accende Heat se T <= target - deadband; spegne se T >= target + deadband;
                 setpoint = `setpoint`; se T <= boost_temp usa boost_setpoint.
    """
    target_temp: float
    deadband: float = 1.0
    setpoint: float = 24.0
    fan_speed: str = "Auto"
    boost_temp: Optional[float] = None       # soglia di "boost" (estremo)
    boost_setpoint: Optional[float] = None
    humidity_dry_threshold: Optional[float] = None   # solo estate
    dry_fan: str = "Low"


@dataclass
class RoomComfort:
    """Comfort-band per stagione di una stanza."""
    summer: Optional[SeasonComfort] = None
    winter: Optional[SeasonComfort] = None


@dataclass
class Room:
    name: str
    ikea_sensor_id: Optional[str]
    panasonic_device_id: Optional[str]
    # Alcuni sensori IKEA (es. TIMMERFLOTTE) espongono temperatura e umidita' come
    # due sub-device separati (..._1 = temperatura, ..._2 = umidita'). Se impostato,
    # l'umidita' della stanza viene letta da questo secondo id (il VINDSTYRKA invece
    # ha tutto su ..._1 e questo resta vuoto).
    ikea_humidity_sensor_id: Optional[str] = None
    rules: list[Rule] = field(default_factory=list)
    comfort: Optional[RoomComfort] = None
    # IP di un device-presenza (telefono): se impostato, questa stanza segue la
    # presenza di QUELLA persona (non solo la presenza globale casa/vuota).
    presence_device_ip: Optional[str] = None
    # MAC di un sensore SwitchBot Meter/THS1 (BLE): se impostato, una stanza
    # senza sensore IKEA riceve T/umidita' indoor reali leggendo le advertisement
    # BLE del sensore (lettura passiva, nessun pairing). Vedi switchbot_reader.
    switchbot_mac: Optional[str] = None
    # Endpoint HTTP di un nodo sensore remoto (es. Pi Zero con BME280/BH1750).
    # Se presente, questa lettura ha precedenza sul sensore IKEA della stanza.
    remote_sensor_url: Optional[str] = None
    # Correzione lineare in punti percentuali per allineare l'RH del sensore
    # remoto a un riferimento noto. Esempio: +11.6 se BME280 legge 53.4 e il
    # riferimento della stanza legge 65.0 nello stesso punto.
    remote_sensor_humidity_offset: float = 0.0
    # Se True: alla PRIMA comparsa della persona di riferimento nella giornata,
    # se la stanza e' calda, avvia l'AC in modalita' Powerful (raffreddamento
    # rapido). Pensato per chi ha esigenze particolari (es. termoregolazione
    # alterata): trova la stanza gia' fresca al rientro.
    powerful_on_first_arrival: bool = False
    # Se True: stanza SOLO MONITORATA. Il sensore continua a registrare T/umidita',
    # ma il sistema NON controlla l'AC in automatico (no comfort, no spegnimento per
    # presenza). Lo spegnimento forzato notturno delle 03:00 si applica COMUNQUE.
    monitor_only: bool = False


@dataclass
class EngineSettings:
    poll_interval_seconds: int = 60
    cooldown_minutes: int = 5
    hysteresis_temp: float = 0.5
    hysteresis_humidity: float = 2.0


@dataclass
class LightSettings:
    """
    Configurazione luci. `ceiling_rooms`: stanze in cui le lampadine sono una
    UNICA plafoniera -> vanno comandate insieme (un solo controllo in dashboard).
    """
    ceiling_rooms: list = field(default_factory=list)


@dataclass
class SeasonSettings:
    """
    Parametri dell'algoritmo stagionale. La stagione e' decisa dalla media
    mobile della temperatura ESTERNA (riportata dai condizionatori Panasonic):
      - media > cooling_outdoor_threshold -> stagione RAFFRESCAMENTO (solo Cool/Dry)
      - media < heating_outdoor_threshold -> stagione RISCALDAMENTO  (solo Heat)
      - in mezzo                          -> MEZZA STAGIONE (entrambe ammesse)
    """
    enabled: bool = True
    cooling_outdoor_threshold: float = 21.0
    heating_outdoor_threshold: float = 16.0
    outdoor_avg_window_hours: int = 24
    sample_interval_minutes: int = 30
    hysteresis: float = 0.5         # banda di isteresi sui confini di stagione
    # Limiti di sicurezza: in stagione, una T. interna estrema sblocca la
    # modalita' opposta (es. ondata di freddo a luglio -> consenti Heat).
    safety_temp_low: float = 16.0
    safety_temp_high: float = 29.0


@dataclass
class TrackedDevice:
    """Un dispositivo (telefono) usato come indicatore di presenza."""
    name: str
    mac: Optional[str] = None
    ip: Optional[str] = None


@dataclass
class PresenceSettings:
    """
    Parametri del rilevamento presenza casa/vuota via FRITZ!Box (TR-064).
    Almeno un device tracciato attivo sulla rete => casa abitata. Dopo
    `away_grace_minutes` minuti senza alcun device => casa vuota (spegne gli AC).
    """
    enabled: bool = False
    address: str = "192.168.178.1"
    user: str = ""
    password: str = ""
    poll_interval_seconds: int = 60
    away_grace_minutes: int = 30
    devices: list = field(default_factory=list)


@dataclass
class Tariff:
    """Tariffa elettrica REALE (dalla bolletta), per calcolare i costi veri.
    Fonte unica: niente piu' stime gonfiate dal cloud Panasonic."""
    variable_eur_kwh: float = 0.21    # quota consumi all-in (energia+rete+oneri), pre-IVA
    vat_rate: float = 0.10            # IVA elettricita' domestica
    fixed_monthly_eur: float = 10.92  # quota fissa (commercializzazione + rete)
    power_eur_kw_month: float = 1.98  # quota potenza (per kW impegnato)
    contracted_power_kw: float = 3.0
    provider: str = ""
    valid_until: str = ""

    @property
    def marginal_eur_kwh(self) -> float:
        """Costo reale di 1 kWh IN PIU' (variabile + IVA): la cifra giusta per
        stimare quanto costa accendere/spegnere l'AC."""
        return round(self.variable_eur_kwh * (1.0 + self.vat_rate), 4)

    def cost(self, kwh: float) -> float:
        """Costo (€) dei kWh indicati, IVA inclusa (quote fisse escluse)."""
        return kwh * self.marginal_eur_kwh


@dataclass
class Boiler:
    """Relè caldaia Sonoff (firmware eWeLink, modalità LAN). Controllo locale
    cifrato: appare come stanza a sé (es. Cucina) con un semplice toggle ON/OFF."""
    enabled: bool
    room: str
    deviceid: str
    devicekey: str
    ip: Optional[str] = None
    port: int = 8081


@dataclass
class Config:
    dirigera_ip: str
    dirigera_token: str
    panasonic_username: str
    panasonic_password: str
    rooms: list[Room]
    force_off_time: str        # orario (HH:MM) dello spegnimento forzato iniziale
    night_off_end: str         # fine fascia notturna (HH:MM): fino a qui AC bloccati
    engine: EngineSettings
    season: SeasonSettings
    presence: PresenceSettings
    lights: LightSettings
    tariff: Tariff
    path: Path
    # Coordinate per il meteo (Open-Meteo). Default generici (centro Italia); le
    # proprie si mettono in config.yaml -> location: {latitude, longitude}.
    latitude: float = 41.9
    longitude: float = 12.5
    boiler: Optional[Boiler] = None

    # -- helper di lookup ---------------------------------------------------
    def get_room(self, name: str) -> Optional[Room]:
        for room in self.rooms:
            if room.name == name:
                return room
        return None

    def room_by_sensor_id(self, sensor_id: str) -> Optional[Room]:
        for room in self.rooms:
            if room.ikea_sensor_id == sensor_id:
                return room
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _parse_condition(raw: dict[str, Any]) -> Condition:
    return Condition(
        temp_gt=raw.get("temp_gt"),
        temp_lt=raw.get("temp_lt"),
        humidity_gt=raw.get("humidity_gt"),
        humidity_lt=raw.get("humidity_lt"),
    )


def _parse_action(raw: dict[str, Any]) -> Action:
    return Action(
        power=bool(raw.get("power", True)),
        mode=raw.get("mode"),
        temperature=raw.get("temperature"),
        fan_speed=raw.get("fan_speed"),
        eco_mode=raw.get("eco_mode"),
    )


def _parse_room(raw: dict[str, Any]) -> Room:
    rules = [
        Rule(condition=_parse_condition(r.get("condition", {}) or {}),
             action=_parse_action(r.get("action", {}) or {}))
        for r in (raw.get("rules") or [])
    ]
    return Room(
        name=raw["name"],
        ikea_sensor_id=raw.get("ikea_sensor_id") or None,
        ikea_humidity_sensor_id=raw.get("ikea_humidity_sensor_id") or None,
        panasonic_device_id=raw.get("panasonic_device_id") or None,
        monitor_only=bool(raw.get("monitor_only", False)),
        rules=rules,
        comfort=_parse_comfort(raw.get("comfort")),
        presence_device_ip=raw.get("presence_device_ip") or None,
        switchbot_mac=raw.get("switchbot_mac") or None,
        remote_sensor_url=raw.get("remote_sensor_url") or None,
        remote_sensor_humidity_offset=float(
            raw.get("remote_sensor_humidity_offset", 0.0) or 0.0),
        powerful_on_first_arrival=bool(raw.get("powerful_on_first_arrival", False)),
    )


def _parse_season_comfort(raw: Optional[dict[str, Any]]) -> Optional[SeasonComfort]:
    if not raw:
        return None
    return SeasonComfort(
        target_temp=float(raw["target_temp"]),
        deadband=float(raw.get("deadband", 1.0)),
        setpoint=float(raw.get("setpoint", 24.0)),
        fan_speed=raw.get("fan_speed", "Auto"),
        boost_temp=raw.get("boost_temp"),
        boost_setpoint=raw.get("boost_setpoint"),
        humidity_dry_threshold=raw.get("humidity_dry_threshold"),
        dry_fan=raw.get("dry_fan", "Low"),
    )


def _parse_comfort(raw: Optional[dict[str, Any]]) -> Optional[RoomComfort]:
    if not raw:
        return None
    return RoomComfort(
        summer=_parse_season_comfort(raw.get("summer")),
        winter=_parse_season_comfort(raw.get("winter")),
    )


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    """Carica e valida il config.yaml in un oggetto Config tipizzato."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"config.yaml non trovato in {path}. "
            "Esegui prima il mapping tool: python tools/mapping_tool.py"
        )

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    dirigera = raw.get("dirigera", {}) or {}
    panasonic = raw.get("panasonic", {}) or {}
    schedule = raw.get("schedule", {}) or {}
    engine_raw = raw.get("engine", {}) or {}

    engine = EngineSettings(
        poll_interval_seconds=int(engine_raw.get("poll_interval_seconds", 60)),
        cooldown_minutes=int(engine_raw.get("cooldown_minutes", 5)),
        hysteresis_temp=float(engine_raw.get("hysteresis_temp", 0.5)),
        hysteresis_humidity=float(engine_raw.get("hysteresis_humidity", 2.0)),
    )

    season_raw = raw.get("season", {}) or {}
    season = SeasonSettings(
        enabled=bool(season_raw.get("enabled", True)),
        cooling_outdoor_threshold=float(season_raw.get("cooling_outdoor_threshold", 21.0)),
        heating_outdoor_threshold=float(season_raw.get("heating_outdoor_threshold", 16.0)),
        outdoor_avg_window_hours=int(season_raw.get("outdoor_avg_window_hours", 24)),
        sample_interval_minutes=int(season_raw.get("sample_interval_minutes", 30)),
        hysteresis=float(season_raw.get("hysteresis", 0.5)),
        safety_temp_low=float(season_raw.get("safety_temp_low", 16.0)),
        safety_temp_high=float(season_raw.get("safety_temp_high", 29.0)),
    )

    rooms = [_parse_room(r) for r in (raw.get("rooms") or [])]

    presence_raw = raw.get("presence", {}) or {}
    fritz_raw = presence_raw.get("fritzbox", {}) or {}
    presence = PresenceSettings(
        enabled=bool(presence_raw.get("enabled", False)),
        address=fritz_raw.get("address", "192.168.178.1"),
        user=fritz_raw.get("user", ""),
        password=fritz_raw.get("password", ""),
        poll_interval_seconds=int(presence_raw.get("poll_interval_seconds", 60)),
        away_grace_minutes=int(presence_raw.get("away_grace_minutes", 30)),
        devices=[
            TrackedDevice(
                name=d.get("name", ""),
                mac=(d.get("mac") or None),
                ip=(d.get("ip") or None),
            )
            for d in (presence_raw.get("devices") or [])
        ],
    )

    lights_raw = raw.get("lights", {}) or {}
    lights = LightSettings(
        ceiling_rooms=list(lights_raw.get("ceiling_rooms") or []),
    )

    t = raw.get("tariff", {}) or {}
    tariff = Tariff(
        variable_eur_kwh=float(t.get("variable_eur_kwh", 0.21)),
        vat_rate=float(t.get("vat_rate", 0.10)),
        fixed_monthly_eur=float(t.get("fixed_monthly_eur", 10.92)),
        power_eur_kw_month=float(t.get("power_eur_kw_month", 1.98)),
        contracted_power_kw=float(t.get("contracted_power_kw", 3.0)),
        provider=str(t.get("provider", "")),
        valid_until=str(t.get("valid_until", "")),
    )

    location = raw.get("location", {}) or {}

    b = raw.get("boiler", {}) or {}
    boiler = None
    if b.get("deviceid") and b.get("devicekey"):
        boiler = Boiler(
            enabled=bool(b.get("enabled", True)),
            room=str(b.get("room", "Cucina")),
            deviceid=str(b["deviceid"]),
            devicekey=str(b["devicekey"]),
            ip=b.get("ip"),
            port=int(b.get("port", 8081)),
        )

    return Config(
        boiler=boiler,
        latitude=float(location.get("latitude", 41.9)),
        longitude=float(location.get("longitude", 12.5)),
        dirigera_ip=dirigera.get("ip_address", ""),
        dirigera_token=dirigera.get("token", ""),
        panasonic_username=panasonic.get("username", ""),
        panasonic_password=panasonic.get("password", ""),
        rooms=rooms,
        force_off_time=schedule.get("force_off_time", "03:00"),
        night_off_end=schedule.get("night_off_end", ""),
        engine=engine,
        season=season,
        presence=presence,
        lights=lights,
        tariff=tariff,
        path=path,
    )


# ---------------------------------------------------------------------------
# Scrittura (usata dagli endpoint PUT della API)
# ---------------------------------------------------------------------------
def _raw_load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _raw_dump(path: Path, data: dict[str, Any]) -> None:
    # default_flow_style=False -> stile a blocchi leggibile; sort_keys=False ->
    # mantiene l'ordine delle chiavi che impostiamo.
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False,
                       allow_unicode=True)


def update_room_rules(
    rules: list[dict[str, Any]],
    room_name: str,
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> None:
    """
    Riscrive le regole di una stanza nel config.yaml.
    `rules` e' una lista di dict {condition: {...}, action: {...}}.
    """
    path = Path(path)
    data = _raw_load(path)
    for room in data.get("rooms", []):
        if room.get("name") == room_name:
            room["rules"] = rules
            _raw_dump(path, data)
            return
    raise KeyError(f"Stanza '{room_name}' non trovata nel config.")


def update_schedule(
    force_off_time: str,
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> None:
    """Aggiorna l'orario di spegnimento forzato (formato HH:MM)."""
    path = Path(path)
    data = _raw_load(path)
    data.setdefault("schedule", {})["force_off_time"] = force_off_time
    _raw_dump(path, data)


def _season_comfort_to_dict(sc: Optional["SeasonComfort"]) -> Optional[dict[str, Any]]:
    if sc is None:
        return None
    return {k: v for k, v in {
        "target_temp": sc.target_temp, "deadband": sc.deadband,
        "setpoint": sc.setpoint, "fan_speed": sc.fan_speed,
        "boost_temp": sc.boost_temp, "boost_setpoint": sc.boost_setpoint,
        "humidity_dry_threshold": sc.humidity_dry_threshold, "dry_fan": sc.dry_fan,
    }.items() if v is not None}


def _comfort_to_dict(c: Optional["RoomComfort"]) -> Optional[dict[str, Any]]:
    if c is None:
        return None
    return {"summer": _season_comfort_to_dict(c.summer),
            "winter": _season_comfort_to_dict(c.winter)}


def redacted_config_dict(config: Config) -> dict[str, Any]:
    """
    Rappresentazione del config SENZA credenziali in chiaro, per l'endpoint
    GET /api/config. Token e password vengono mascherati.
    """
    return {
        "dirigera": {
            "ip_address": config.dirigera_ip,
            "token": "***" if config.dirigera_token else "",
        },
        "panasonic": {
            "username": config.panasonic_username,
            "password": "***" if config.panasonic_password else "",
        },
        "rooms": [
            {
                "name": r.name,
                "ikea_sensor_id": r.ikea_sensor_id,
                "panasonic_device_id": r.panasonic_device_id,
                "rules": [
                    {
                        "condition": {k: v for k, v in {
                            "temp_gt": rule.condition.temp_gt,
                            "temp_lt": rule.condition.temp_lt,
                            "humidity_gt": rule.condition.humidity_gt,
                            "humidity_lt": rule.condition.humidity_lt,
                        }.items() if v is not None},
                        "action": rule.action.as_dict(),
                    }
                    for rule in r.rules
                ],
                "comfort": _comfort_to_dict(r.comfort),
            }
            for r in config.rooms
        ],
        "schedule": {"force_off_time": config.force_off_time,
                     "night_off_end": config.night_off_end},
        "engine": {
            "poll_interval_seconds": config.engine.poll_interval_seconds,
            "cooldown_minutes": config.engine.cooldown_minutes,
            "hysteresis_temp": config.engine.hysteresis_temp,
            "hysteresis_humidity": config.engine.hysteresis_humidity,
        },
        "season": {
            "enabled": config.season.enabled,
            "cooling_outdoor_threshold": config.season.cooling_outdoor_threshold,
            "heating_outdoor_threshold": config.season.heating_outdoor_threshold,
            "outdoor_avg_window_hours": config.season.outdoor_avg_window_hours,
            "sample_interval_minutes": config.season.sample_interval_minutes,
            "hysteresis": config.season.hysteresis,
            "safety_temp_low": config.season.safety_temp_low,
            "safety_temp_high": config.season.safety_temp_high,
        },
        "presence": {
            "enabled": config.presence.enabled,
            "fritzbox": {
                "address": config.presence.address,
                "user": config.presence.user,
                "password": "***" if config.presence.password else "",
            },
            "poll_interval_seconds": config.presence.poll_interval_seconds,
            "away_grace_minutes": config.presence.away_grace_minutes,
            "devices": [
                {"name": d.name, "mac": d.mac, "ip": d.ip}
                for d in config.presence.devices
            ],
        },
        "tariff": {
            "variable_eur_kwh": config.tariff.variable_eur_kwh,
            "vat_rate": config.tariff.vat_rate,
            "fixed_monthly_eur": config.tariff.fixed_monthly_eur,
            "power_eur_kw_month": config.tariff.power_eur_kw_month,
            "marginal_eur_kwh": config.tariff.marginal_eur_kwh,
        },
    }
