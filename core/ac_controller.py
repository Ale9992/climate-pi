"""
core/ac_controller.py — Wrapper asincrono su aio-panasonic-comfort-cloud.

Responsabilita':
  - Mantiene la sessione verso Panasonic Comfort Cloud (login + cache device).
  - Legge lo stato attuale di un condizionatore (get_device_state).
  - Applica uno stato desiderato (set_device_state) SOLO se diverso da quello
    attuale, per evitare chiamate ridondanti al cloud.
  - Spegne un device (turn_off).
  - Retry automatico con backoff esponenziale (1s, 2s, 4s) sugli errori di rete.
  - Logga ogni comando inviato e l'esito su SQLite (tabella ac_commands).

Gestisce esplicitamente il caso "cloud non raggiungibile": logga l'errore, non
solleva oltre il necessario, e lascia che il chiamante riprovi al ciclo dopo.

API libreria (aio-panasonic-comfort-cloud 2025.5.x):
  ApiClient(user, pwd, aiohttp_session) -> start_session()
  get_devices() -> [PanasonicDeviceInfo(.id, .guid, .name, .model)]
  get_device(info) -> PanasonicDevice(.parameters: power/mode/fan_speed/
                       target_temperature/inside_temperature)
  ChangeRequestBuilder(device).set_power_mode/set_hvac_mode/
                       set_target_temperature/set_fan_speed -> build() -> dict
  set_device_raw(device, params_dict)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import aiohttp
from aio_panasonic_comfort_cloud import ApiClient, ChangeRequestBuilder
from aio_panasonic_comfort_cloud.constants import FanSpeed, OperationMode, Power

from db.database import Database

logger = logging.getLogger("climate.ac")


# Numero massimo di tentativi e ritardi (backoff esponenziale) sui comandi.
_MAX_RETRIES = 3
_BACKOFF_SECONDS = (1, 2, 4)
# Circuit breaker per il cloud Panasonic: dopo _CB_FAIL_THRESHOLD chiamate
# consecutive fallite (es. cloud in manutenzione/outage), il breaker si APRE e per
# _CB_COOLDOWN_SECONDS le chiamate falliscono subito senza ritentare ne' loggare a
# raffica (evita migliaia di warning e di martellare il cloud). Alla scadenza fa un
# solo probe (half-open); un successo lo richiude.
_CB_FAIL_THRESHOLD = 5
_CB_COOLDOWN_SECONDS = 120.0


class ACUnreachableError(Exception):
    """Sollevata quando il cloud Panasonic non e' raggiungibile dopo i retry."""


def _to_power(value: Any) -> Power:
    """Converte bool/str ('on'/'off'/'On'/'Off') o Power nell'enum Power."""
    if isinstance(value, Power):
        return value
    if isinstance(value, bool):
        return Power.On if value else Power.Off
    text = str(value).strip().lower()
    return Power.On if text in ("on", "true", "1", "yes") else Power.Off


def _to_mode(value: Optional[str]) -> Optional[OperationMode]:
    """Converte 'Cool'/'Heat'/'Dry'/'Fan'/'Auto' nell'enum OperationMode."""
    if value is None:
        return None
    if isinstance(value, OperationMode):
        return value
    try:
        return OperationMode[str(value).strip().capitalize()]
    except KeyError:
        logger.warning("Modalita' AC sconosciuta: %r (ignorata)", value)
        return None


def _to_fan(value: Optional[str]) -> Optional[FanSpeed]:
    """Converte 'Auto'/'Low'/'Mid'/'High'/'LowMid'/'HighMid' nell'enum FanSpeed."""
    if value is None:
        return None
    if isinstance(value, FanSpeed):
        return value
    # Normalizza il casing: 'low' -> 'Low', 'lowmid' -> 'LowMid'.
    key = str(value).strip()
    for member in FanSpeed:
        if member.name.lower() == key.lower():
            return member
    logger.warning("Fan speed AC sconosciuta: %r (ignorata)", value)
    return None


class ACController:
    """Controller asincrono per i condizionatori Panasonic."""

    def __init__(self, username: str, password: str, database: Database) -> None:
        self._username = username
        self._password = password
        self._db = database
        self._session: Optional[aiohttp.ClientSession] = None
        self._client: Optional[ApiClient] = None
        # device_id -> PanasonicDeviceInfo (cache popolata al connect()).
        self._infos: dict[str, Any] = {}
        # Cache a TTL: evita di interrogare il cloud Panasonic ad ogni richiesta
        # della dashboard (era ~2s per device x 3 stanze x 2 chiamate = ~13s).
        # {device_id: (epoch, value)}. Lo stato lo invalida un comando inviato.
        self._state_cache: dict[str, tuple[float, dict]] = {}
        self._energy_cache: dict[str, tuple[float, Optional[dict]]] = {}
        self._STATE_TTL = 25.0     # secondi: stato AC (allineato al polling 30s)
        self._ENERGY_TTL = 300.0   # secondi: consumo kWh (cambia lentamente)
        # Circuit breaker cloud Panasonic (vedi _with_retry).
        self._cb_open = False
        self._cb_fail = 0
        self._cb_open_until = 0.0

    # -- ciclo di vita ------------------------------------------------------
    async def connect(self) -> None:
        """Autentica e cachea la lista dei device Panasonic."""
        self._session = aiohttp.ClientSession()
        self._client = ApiClient(self._username, self._password, self._session)
        await self._client.start_session()
        self._infos = {info.id: info for info in self._client.get_devices()}
        logger.info("Panasonic connesso: %d device in cache", len(self._infos))

    async def close(self) -> None:
        """Chiude la sessione HTTP (idempotente)."""
        if self._client is not None:
            try:
                await self._client.stop_session()
            except Exception:  # noqa: BLE001
                pass
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._client = None

    @property
    def device_ids(self) -> list[str]:
        return list(self._infos.keys())

    def device_name(self, device_id: str) -> str:
        info = self._infos.get(device_id)
        return getattr(info, "name", device_id) if info else device_id

    # -- helper retry -------------------------------------------------------
    async def _with_retry(self, what: str, factory) -> Any:
        """
        Esegue una coroutine con retry e backoff esponenziale.
        `factory` e' una funzione zero-arg che ritorna una nuova coroutine
        ad ogni tentativo (le coroutine non sono riutilizzabili).
        """
        now = time.monotonic()
        half_open = False
        if self._cb_open:
            if now < self._cb_open_until:
                # Breaker aperto: fail-fast, niente retry ne' warning a raffica.
                raise ACUnreachableError(
                    f"{what}: cloud Panasonic non raggiungibile (circuit breaker aperto)")
            half_open = True  # cooldown scaduto: un solo tentativo di prova

        attempts = 1 if half_open else _MAX_RETRIES
        last_exc: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                result = await factory()
                if self._cb_open or self._cb_fail:
                    logger.info("Cloud Panasonic recuperato (circuit breaker chiuso).")
                self._cb_open = False
                self._cb_fail = 0
                return result
            except Exception as exc:  # noqa: BLE001 - rete: vogliamo ritentare
                last_exc = exc
                if not half_open:
                    delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                    logger.warning(
                        "Errore '%s' (tentativo %d/%d): %s — ritento tra %ds",
                        what, attempt + 1, attempts, exc, delay,
                    )
                    if attempt < attempts - 1:
                        await asyncio.sleep(delay)

        # Tutti i tentativi falliti: conta verso il breaker.
        self._cb_fail += 1
        if self._cb_fail >= _CB_FAIL_THRESHOLD and not self._cb_open:
            self._cb_open = True
            logger.warning(
                "Cloud Panasonic non risponde da %d chiamate: circuit breaker APERTO "
                "per %ds (smetto di ritentare).", self._cb_fail, int(_CB_COOLDOWN_SECONDS))
        if self._cb_open:
            self._cb_open_until = time.monotonic() + _CB_COOLDOWN_SECONDS
        raise ACUnreachableError(f"{what}: cloud Panasonic non raggiungibile") from last_exc

    # -- lettura stato ------------------------------------------------------
    async def _fetch_device(self, device_id: str):
        """Recupera l'oggetto PanasonicDevice completo (stato live)."""
        info = self._infos.get(device_id)
        if info is None:
            raise KeyError(f"Device Panasonic sconosciuto: {device_id}")
        assert self._client is not None
        return await self._with_retry(
            f"get_device({self.device_name(device_id)})",
            lambda: self._client.get_device(info),
        )

    async def get_device_state(self, device_id: str,
                               use_cache: bool = False) -> dict[str, Any]:
        """
        Ritorna lo stato attuale come dict serializzabile.
        Se use_cache=True e c'e' un valore fresco (< _STATE_TTL), lo ritorna
        senza interrogare il cloud (la dashboard usa questo per essere istantanea).
        """
        if use_cache:
            import time as _t
            cached = self._state_cache.get(device_id)
            if cached and (_t.monotonic() - cached[0]) < self._STATE_TTL:
                return cached[1]

        device = await self._fetch_device(device_id)
        p = device.parameters
        nanoe = _enum_name(getattr(p, "nanoe_mode", None))
        state = {
            "power": _enum_name(p.power),
            "mode": _enum_name(p.mode),
            "target_temperature": p.target_temperature,
            "fan_speed": _enum_name(p.fan_speed),
            "inside_temperature": getattr(p, "inside_temperature", None),
            "outside_temperature": getattr(p, "outside_temperature", None),
            # nanoe: True/False/None (None = funzione non disponibile sul device)
            "nanoe": (nanoe in ("On", "All", "ModeG")) if nanoe not in (None, "Unavailable") else None,
            # swing verticale: "Auto"/"Swing"/posizione, o None
            "swing_vertical": _enum_name(getattr(p, "vertical_swing_mode", None)),
            # eco_mode: "Auto"/"Powerful"/"Quiet" (boost vs silenzioso, esclusivi)
            "eco_mode": _enum_name(getattr(p, "eco_mode", None)),
        }
        import time as _t
        self._state_cache[device_id] = (_t.monotonic(), state)
        return state

    # -- scrittura stato ----------------------------------------------------
    def _state_differs(self, device, power, mode, temperature, fan_speed,
                       nanoe, swing_vertical, eco_mode) -> bool:
        """True se lo stato desiderato differisce da quello ATTUALE del device.
        Confronta solo i campi specificati (non-None); il power e' confrontato
        sempre. Se vogliamo spegnere e il device e' gia' Off, nient'altro conta."""
        p = device.parameters
        if _enum_name(p.power) != _enum_name(_to_power(power)):
            return True
        if _enum_name(_to_power(power)) != "On":
            return False
        if mode is not None and _enum_name(p.mode) != _enum_name(_to_mode(mode)):
            return True
        if temperature is not None and p.target_temperature != int(round(temperature)):
            return True
        if fan_speed is not None:
            want = _to_fan(fan_speed)
            if want is not None and _enum_name(p.fan_speed) != _enum_name(want):
                return True
        if eco_mode is not None and _enum_name(getattr(p, "eco_mode", None)) != eco_mode:
            return True
        if nanoe is not None:
            cur = _enum_name(getattr(p, "nanoe_mode", None))
            cur_bool = (cur in ("On", "All", "ModeG")) if cur not in (None, "Unavailable") else None
            if cur_bool != nanoe:
                return True
        if swing_vertical is not None and \
                _enum_name(getattr(p, "vertical_swing_mode", None)) != swing_vertical:
            return True
        return False

    async def set_device_state(
        self,
        device_id: str,
        power: Any = True,
        mode: Optional[str] = None,
        temperature: Optional[float] = None,
        fan_speed: Optional[str] = None,
        nanoe: Optional[bool] = None,
        swing_vertical: Optional[str] = None,
        eco_mode: Optional[str] = None,
    ) -> bool:
        """
        Porta il device allo stato desiderato. Non invia nulla se il device e'
        gia' in quello stato (il ChangeRequestBuilder confronta col valore
        corrente). Ritorna True se ha inviato un comando, False se era gia' ok.
        """
        params_log = {
            "power": _enum_name(_to_power(power)),
            "mode": mode, "temperature": temperature, "fan_speed": fan_speed,
            "nanoe": nanoe, "swing_vertical": swing_vertical, "eco_mode": eco_mode,
        }
        try:
            device = await self._fetch_device(device_id)

            # Dedup REALE: ChangeRequestBuilder.has_changes fa solo len(_request)!=0
            # e NON confronta con lo stato corrente -> ogni set partirebbe sempre.
            # Confrontiamo qui col device live (get_device e' fresco): se lo stato
            # desiderato combacia gia', non inviamo nulla. Evita comandi cloud
            # ridondanti (meno carico/429) e righe inutili in ac_commands.
            if not self._state_differs(device, power, mode, temperature,
                                       fan_speed, nanoe, swing_vertical, eco_mode):
                logger.debug("AC %s gia' nello stato desiderato: nessun comando",
                             self.device_name(device_id))
                return False

            builder = ChangeRequestBuilder(device)
            builder.set_power_mode(_to_power(power))

            mode_enum = _to_mode(mode)
            if mode_enum is not None:
                builder.set_hvac_mode(mode_enum)
            if temperature is not None:
                builder.set_target_temperature(int(round(temperature)))
            fan_enum = _to_fan(fan_speed)
            if fan_enum is not None:
                builder.set_fan_speed(fan_enum)
            if nanoe is not None:
                from aio_panasonic_comfort_cloud.constants import NanoeMode
                builder.set_nanoe_mode(NanoeMode.On if nanoe else NanoeMode.Off)
            if swing_vertical is not None:
                from aio_panasonic_comfort_cloud.constants import AirSwingUD
                try:
                    builder.set_vertical_swing(AirSwingUD[swing_vertical])
                except KeyError:
                    logger.warning("Swing verticale sconosciuto: %r", swing_vertical)
            if eco_mode is not None:
                from aio_panasonic_comfort_cloud.constants import EcoMode
                try:
                    builder.set_eco_mode(EcoMode[eco_mode])
                except KeyError:
                    logger.warning("Eco mode sconosciuto: %r", eco_mode)

            payload = builder.build()
            assert self._client is not None
            await self._with_retry(
                f"set_device({self.device_name(device_id)})",
                lambda: self._client.set_device_raw(device, payload),
            )
            await self._db.insert_ac_command(
                device_id, "set_state", params_log, success=True)
            logger.info("AC %s -> %s", self.device_name(device_id), params_log)
            # Invalida la cache: il prossimo read riflette il nuovo stato.
            self._state_cache.pop(device_id, None)
            return True

        except Exception as exc:  # noqa: BLE001
            await self._db.insert_ac_command(
                device_id, "set_state", params_log, success=False,
                error_message=str(exc))
            logger.error("Comando AC %s fallito: %s", self.device_name(device_id), exc)
            raise

    # -- temperatura esterna (per l'algoritmo stagionale) -------------------
    async def get_outside_temperature(self) -> Optional[float]:
        """
        Temperatura esterna live, letta dal primo device Panasonic raggiungibile.
        Tutti i device riportano la stessa T. esterna della zona.
        """
        for device_id in self._infos:
            try:
                state = await self.get_device_state(device_id)
                t = state.get("outside_temperature")
                if t is not None:
                    return float(t)
            except Exception:  # noqa: BLE001 - prova il device successivo
                continue
        return None

    async def get_recent_outdoor_avg(self, hours: int = 24) -> Optional[float]:
        """
        Media della temperatura esterna nelle ultime ~`hours` ore, dallo storico
        orario Panasonic (history mode Day di oggi + ieri). Usata per inizializzare
        la media mobile della stagione all'avvio, senza partire "a freddo".
        """
        if self._client is None or not self._infos:
            return None
        from datetime import datetime, timedelta
        device_id = next(iter(self._infos))
        info = self._infos[device_id]
        today = datetime.now()
        dates = [today.strftime("%Y%m%d"),
                 (today - timedelta(days=1)).strftime("%Y%m%d")]
        values: list[float] = []
        for date in dates:
            try:
                resp = await self._with_retry(
                    f"history({date})",
                    lambda d=date: self._client.history(info.id, "Day", d),
                )
                params = resp.get("parameters") if resp else None
                records = params.get("historyDataList", []) if isinstance(params, dict) else []
                for rec in records:
                    t = rec.get("averageOutsideTemp", -255)
                    if t != -255:
                        values.append(float(t))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Storico esterno %s non disponibile: %s", date, exc)
        if not values:
            return None
        # Tiene gli ultimi `hours` campioni orari disponibili.
        recent = values[-hours:] if len(values) > hours else values
        return sum(recent) / len(recent)

    # -- consumo energetico (per dashboard e ottimizzazione) ----------------
    async def get_today_energy(self, device_id: str) -> Optional[dict[str, Any]]:
        """
        Consumo di OGGI del device, dal Comfort Cloud (endpoint history Month).
        Ritorna {consumption, cooling, heating} in kWh, o None se non disponibile.

        NB: e' il cumulativo giornaliero (kWh dall'inizio del giorno), non la
        potenza istantanea: l'API Panasonic non espone i Watt in tempo reale.
        """
        info = self._infos.get(device_id)
        if info is None or self._client is None:
            return None
        import time as _t
        cached = self._energy_cache.get(device_id)
        if cached and (_t.monotonic() - cached[0]) < self._ENERGY_TTL:
            return cached[1]
        try:
            energy = await self._with_retry(
                f"energy({self.device_name(device_id)})",
                lambda: self._client.async_get_energy(info),
            )
        except Exception as exc:  # noqa: BLE001 - cloud down: degrada a None
            logger.warning("Lettura consumo %s fallita: %s",
                           self.device_name(device_id), exc)
            self._energy_cache[device_id] = (_t.monotonic(), None)
            return None
        if energy is None:
            return None
        result = {
            "consumption": round(float(getattr(energy, "consumption", 0.0)), 3),
            "cooling": round(float(getattr(energy, "cooling_consumption", 0.0)), 3),
            "heating": round(float(getattr(energy, "heating_consumption", 0.0)), 3),
        }
        self._energy_cache[device_id] = (_t.monotonic(), result)
        return result

    async def turn_off(self, device_id: str) -> bool:
        """
        Spegne il device. Usato anche dallo spegnimento forzato delle 03:00.
        Ritorna True se ha inviato il comando, False se era gia' spento.
        """
        try:
            device = await self._fetch_device(device_id)
            if _enum_name(device.parameters.power) == "Off":
                logger.info("AC %s gia' spento", self.device_name(device_id))
                return False

            builder = ChangeRequestBuilder(device)
            builder.set_power_mode(Power.Off)
            payload = builder.build()
            assert self._client is not None
            await self._with_retry(
                f"turn_off({self.device_name(device_id)})",
                lambda: self._client.set_device_raw(device, payload),
            )
            await self._db.insert_ac_command(
                device_id, "turn_off", {"power": "Off"}, success=True)
            logger.info("AC %s spento", self.device_name(device_id))
            return True

        except Exception as exc:  # noqa: BLE001
            await self._db.insert_ac_command(
                device_id, "turn_off", {"power": "Off"}, success=False,
                error_message=str(exc))
            logger.error("Spegnimento AC %s fallito: %s",
                         self.device_name(device_id), exc)
            raise


def _enum_name(value: Any) -> Any:
    """Ritorna il .name di un enum, o il valore stesso se non e' un enum."""
    return value.name if hasattr(value, "name") else value
