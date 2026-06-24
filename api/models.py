"""
api/models.py — Modelli Pydantic per richieste e risposte della REST API.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Risposte
# ---------------------------------------------------------------------------
class ACState(BaseModel):
    """Stato corrente di un condizionatore."""
    power: Optional[str] = None             # "On" / "Off"
    mode: Optional[str] = None              # Cool / Heat / Dry / Fan / Auto
    target_temperature: Optional[float] = None
    fan_speed: Optional[str] = None
    inside_temperature: Optional[float] = None
    outside_temperature: Optional[float] = None
    nanoe: Optional[bool] = None            # True/False acceso; None = non disponibile
    swing_vertical: Optional[str] = None    # Auto / Swing / posizione
    eco_mode: Optional[str] = None          # Auto / Powerful / Quiet
    energy_today_kwh: Optional[float] = None      # consumo cumulativo di oggi (kWh)
    energy_cooling_kwh: Optional[float] = None     # quota raffrescamento (kWh)
    energy_heating_kwh: Optional[float] = None     # quota riscaldamento (kWh)
    reachable: bool = True
    error: Optional[str] = None             # valorizzato se il cloud non risponde


class RoomState(BaseModel):
    """Stato completo di una stanza per la dashboard."""
    name: str
    has_sensor: bool
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None
    lux: Optional[float] = None
    last_reading: Optional[str] = None      # timestamp ISO ultima lettura sensore
    ac: Optional[ACState] = None
    override_active: bool = False
    override_remaining_seconds: int = 0


class ReadingPoint(BaseModel):
    """Punto dello storico letture."""
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None
    lux: Optional[float] = None
    timestamp: str


class LogEntry(BaseModel):
    """Voce del log automazioni."""
    id: int
    room_name: str
    rule_matched: Optional[str] = None
    action_taken: Optional[str] = None
    temp_at_trigger: Optional[float] = None
    humidity_at_trigger: Optional[float] = None
    timestamp: str


class ConnectionStatus(BaseModel):
    """Stato delle connessioni, della stagione e della presenza."""
    dirigera: bool
    panasonic: bool
    season: Optional[str] = None              # raffrescamento / riscaldamento / mezza_stagione
    outdoor_avg_temperature: Optional[float] = None
    presence: Optional[str] = None            # casa_abitata / casa_vuota / sconosciuto / None(disattivo)
    presence_home: Optional[bool] = None      # True=in casa, False=fuori, None=non disponibile
    presence_people: list[str] = Field(default_factory=list)  # nomi dei device/persone presenti


class WeatherPoint(BaseModel):
    """Punto orario della previsione meteo."""
    time: str
    temperature: Optional[float] = None


class WeatherState(BaseModel):
    """Meteo esterno corrente + previsione breve."""
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    forecast: list[WeatherPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Richieste (PUT / POST)
# ---------------------------------------------------------------------------
class ConditionModel(BaseModel):
    temp_gt: Optional[float] = None
    temp_lt: Optional[float] = None
    humidity_gt: Optional[float] = None
    humidity_lt: Optional[float] = None


class ActionModel(BaseModel):
    power: bool = True
    mode: Optional[str] = None
    temperature: Optional[float] = None
    fan_speed: Optional[str] = None


class RuleModel(BaseModel):
    condition: ConditionModel
    action: ActionModel


class RulesUpdate(BaseModel):
    """Body di PUT /api/config/rooms/{room_name}/rules."""
    rules: list[RuleModel]


class ScheduleUpdate(BaseModel):
    """Body di PUT /api/config/schedule."""
    force_off_time: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
                                description="Orario HH:MM (24h)")


class OverrideRequest(BaseModel):
    """Body di POST /api/rooms/{room_name}/ac/override."""
    power: bool = True
    mode: Optional[str] = None
    temperature: Optional[float] = None
    fan_speed: Optional[str] = None
    nanoe: Optional[bool] = None
    swing_vertical: Optional[str] = None
    minutes: int = 60


class ControlRequest(BaseModel):
    """
    Body di POST /api/rooms/{room_name}/ac/control — controllo diretto stile
    termostato. Tutti i campi opzionali: si invia solo cio' che cambia. Applica
    un override automatico per `minutes` minuti (default 120) cosi' il rule
    engine non sovrascrive subito la scelta manuale.
    """
    power: Optional[bool] = None
    mode: Optional[str] = None
    temperature: Optional[float] = None
    fan_speed: Optional[str] = None
    nanoe: Optional[bool] = None
    swing_vertical: Optional[str] = None
    eco_mode: Optional[str] = None          # Auto / Powerful / Quiet
    minutes: int = 120
