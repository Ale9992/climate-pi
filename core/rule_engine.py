"""
core/rule_engine.py — Valutazione delle regole e applicazione sui condizionatori.

Riceve dal sensor_poller i valori correnti (temperatura, umidita') di una stanza,
valuta le regole del config IN ORDINE e applica la PRIMA la cui condizione e'
soddisfatta. Gestisce:

  - AND logico: piu' condizioni nella stessa regola devono valere tutte
    (implementato in Condition.matches() in core/config.py).
  - Cooldown: non cambia lo stato di un AC piu' di una volta ogni
    cooldown_minutes (dal config).
  - Override manuale: l'API puo' sospendere il rule engine per una stanza per N
    minuti; durante l'override le letture non producono comandi automatici.
  - Ridondanza: l'effettiva soppressione del comando "stato gia' corretto" e'
    delegata all'ACController (ChangeRequestBuilder.has_changes), che confronta
    con lo stato reale del device sul cloud.

Logga ogni decisione su SQLite (automation_logs): regola scattata e valori al
momento dello scatto.

Niente blocking call: tutto async.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from core.ac_controller import ACController
from core.config import Action, Config, Room, SeasonComfort

logger = logging.getLogger("climate.rules")


@dataclass
class _RoomRuntime:
    """Stato runtime per stanza (cooldown, override, stato on/off corrente)."""
    last_command_monotonic: float = 0.0   # time.monotonic() dell'ultimo comando
    override_until_epoch: float = 0.0      # time.time() di fine override (0 = nessuno)
    override_action: Optional[Action] = None
    is_on: Optional[bool] = None           # stato AC noto (per l'isteresi comfort)
    # Ultimo stato (power, mode, target) impostato DAL SISTEMA. Serve a rilevare
    # un intervento esterno (telecomando / app Panasonic): se lo stato reale non
    # coincide con questo, qualcuno ha comandato l'AC da fuori.
    expected_power: Optional[bool] = None
    expected_mode: Optional[str] = None
    expected_temp: Optional[float] = None


def comfort_decision(
    band: SeasonComfort,
    heating: bool,
    indoor_temp: Optional[float],
    humidity: Optional[float],
    currently_on: bool,
) -> Optional[Action]:
    """
    Decisione comfort-band con isteresi. Ritorna l'Action desiderata
    (accensione, Dry, o spegnimento) oppure None se non serve cambiare nulla.

    `heating=False` -> banda estiva (Cool/Dry); `heating=True` -> banda invernale.
    L'isteresi usa `currently_on`: dentro la banda morta non cambia stato.
    """
    if indoor_temp is None:
        return None
    on_db = band.target_temp + band.deadband        # soglia "fa caldo" (estate)
    off_db = band.target_temp - band.deadband       # soglia "abbastanza fresco"

    if not heating:
        # --- ESTATE (raffrescamento) ---
        # Priorita' deumidificazione per comfort (consuma poco).
        if (band.humidity_dry_threshold is not None and humidity is not None
                and humidity > band.humidity_dry_threshold):
            return Action(power=True, mode="Dry", temperature=None,
                          fan_speed=band.dry_fan)
        if indoor_temp >= on_db:
            sp = band.setpoint
            if band.boost_temp is not None and indoor_temp >= band.boost_temp \
                    and band.boost_setpoint is not None:
                sp = band.boost_setpoint
            return Action(power=True, mode="Cool", temperature=sp,
                          fan_speed=band.fan_speed)
        if indoor_temp <= off_db:
            return Action(power=False) if currently_on else None
        return None  # dentro la banda: mantieni
    else:
        # --- INVERNO (riscaldamento): soglie speculari ---
        on_db_h = band.target_temp - band.deadband   # "fa freddo"
        off_db_h = band.target_temp + band.deadband  # "abbastanza caldo"
        if indoor_temp <= on_db_h:
            sp = band.setpoint
            if band.boost_temp is not None and indoor_temp <= band.boost_temp \
                    and band.boost_setpoint is not None:
                sp = band.boost_setpoint
            return Action(power=True, mode="Heat", temperature=sp,
                          fan_speed=band.fan_speed)
        if indoor_temp >= off_db_h:
            return Action(power=False) if currently_on else None
        return None


class RuleEngine:
    """Motore di valutazione regole, una istanza per tutto il sistema."""

    def __init__(self, config: Config, ac_controller: ACController, database,
                 season_manager=None, presence_manager=None) -> None:
        self._cfg = config
        self._ac = ac_controller
        self._db = database
        # Opzionale: se presente, filtra le modalità in base alla stagione.
        self._season = season_manager
        # Opzionale: blocca le accensioni quando la casa e' vuota.
        self._presence = presence_manager
        self._cooldown_seconds = config.engine.cooldown_minutes * 60
        # Durata override quando si rileva un comando manuale esterno
        # (telecomando / app Panasonic): poi l'automazione riprende.
        self._external_override_minutes = 120
        self._runtime: dict[str, _RoomRuntime] = {
            room.name: _RoomRuntime() for room in config.rooms
        }

    # -- valutazione pura (testabile) --------------------------------------
    @staticmethod
    def evaluate(room: Room, temperature: Optional[float],
                 humidity: Optional[float]) -> Optional[tuple[int, Action]]:
        """
        Ritorna (indice_regola, Action) della PRIMA regola la cui condizione e'
        soddisfatta, oppure None se nessuna regola scatta.
        """
        for idx, rule in enumerate(room.rules):
            if rule.condition.matches(temperature, humidity):
                return idx, rule.action
        return None

    # -- override manuale ---------------------------------------------------
    def set_override(self, room_name: str, action: Action, minutes: int = 60) -> None:
        """Attiva un override manuale per `minutes` minuti su una stanza."""
        rt = self._runtime.setdefault(room_name, _RoomRuntime())
        rt.override_until_epoch = time.time() + minutes * 60
        rt.override_action = action
        logger.info("Override manuale su '%s' per %d minuti", room_name, minutes)

    def clear_override(self, room_name: str) -> None:
        """Rimuove l'override manuale e riattiva il rule engine sulla stanza."""
        rt = self._runtime.get(room_name)
        if rt:
            rt.override_until_epoch = 0.0
            rt.override_action = None
        logger.info("Override rimosso su '%s'", room_name)

    def override_remaining_seconds(self, room_name: str) -> int:
        """Secondi residui di override (0 se non attivo)."""
        rt = self._runtime.get(room_name)
        if not rt or rt.override_until_epoch == 0.0:
            return 0
        remaining = rt.override_until_epoch - time.time()
        return int(remaining) if remaining > 0 else 0

    def is_overridden(self, room_name: str) -> bool:
        return self.override_remaining_seconds(room_name) > 0

    def _in_night_window(self) -> bool:
        """
        True se l'ora attuale e' nella fascia notturna [force_off_time, night_off_end):
        in quella fascia gli AC restano spenti (niente accensioni automatiche).
        Gestisce anche fasce che attraversano la mezzanotte (es. 23:00->07:00).
        """
        end = (self._cfg.night_off_end or "").strip()
        start = (self._cfg.force_off_time or "").strip()
        if not end or not start:
            return False
        try:
            sh, sm = (int(x) for x in start.split(":"))
            eh, em = (int(x) for x in end.split(":"))
        except ValueError:
            return False
        from datetime import datetime
        now = datetime.now()
        cur = now.hour * 60 + now.minute
        s = sh * 60 + sm
        e = eh * 60 + em
        if s == e:
            return False
        if s < e:                 # fascia nello stesso giorno (es. 03:00-08:00)
            return s <= cur < e
        return cur >= s or cur < e   # attraversa mezzanotte (es. 23:00-07:00)

    # -- processo principale ------------------------------------------------
    async def process(
        self,
        room_name: str,
        temperature: Optional[float],
        humidity: Optional[float],
    ) -> None:
        """
        Punto d'ingresso chiamato dal sensor_poller ad ogni evento di lettura.
        Valuta le regole e, se necessario, comanda l'AC rispettando il cooldown.
        """
        room = self._cfg.get_room(room_name)
        if room is None:
            logger.warning("Stanza sconosciuta dal poller: %s", room_name)
            return
        if not room.panasonic_device_id:
            # Nessun AC associato: niente da comandare.
            return

        rt = self._runtime.setdefault(room_name, _RoomRuntime())

        # 0) Fascia notturna: dalle force_off_time alle night_off_end gli AC
        #    DEVONO restare spenti (regola dura, vince su tutto). Se acceso, spegne.
        if self._in_night_window():
            if rt.is_on:
                logger.info("'%s': fascia notturna -> spengo.", room_name)
                try:
                    await self._ac.turn_off(room.panasonic_device_id)
                    rt.is_on = False
                    rt.expected_power = False
                except Exception as exc:  # noqa: BLE001
                    logger.error("Spegnimento notturno '%s' fallito: %s", room_name, exc)
            else:
                logger.debug("'%s': fascia notturna, salto (no accensioni).", room_name)
            return

        # 1) Override attivo? Il rule engine resta sospeso per questa stanza.
        if self.is_overridden(room_name):
            logger.debug("'%s' in override (%ds rimasti): salto valutazione",
                         room_name, self.override_remaining_seconds(room_name))
            return

        # 1b) Intervento esterno? Se lo stato reale dell'AC non coincide con
        #     quello che il sistema aveva impostato, qualcuno ha comandato da
        #     telecomando / app Panasonic: rispetta la scelta con un override.
        if await self._detect_external_change(room_name, room, rt):
            self.set_override(room_name, Action(power=False),
                              minutes=self._external_override_minutes)
            logger.info("'%s': intervento manuale esterno rilevato -> override %d min",
                        room_name, self._external_override_minutes)
            return

        # Presenza: due livelli.
        # - Se la stanza e' legata a una PERSONA (presence_device_ip), segue solo
        #   quel telefono: se non c'e', spegne l'AC e non lo riaccende.
        # - Altrimenti usa la presenza GLOBALE casa/vuota.
        if self._presence is not None:
            if room.presence_device_ip:
                if not self._presence.is_person_home(room.presence_device_ip):
                    if rt.is_on:
                        logger.info("'%s': la persona di riferimento e' fuori -> spengo.",
                                    room_name)
                        try:
                            await self._ac.turn_off(room.panasonic_device_id)
                            rt.is_on = False
                            rt.expected_power = False
                        except Exception as exc:  # noqa: BLE001
                            logger.error("Spegnimento '%s' (persona fuori) fallito: %s",
                                         room_name, exc)
                    else:
                        logger.debug("'%s': persona fuori, salto (no accensioni).",
                                     room_name)
                    return
            elif not self._presence.is_home():
                logger.debug("'%s': casa vuota, salto valutazione (no accensioni).",
                             room_name)
                return

        # 2) Determina l'azione: modello comfort-band (preferito) o regole legacy.
        if room.comfort is not None:
            action, label = await self._comfort_action(room, temperature, humidity, rt)
        else:
            match = self.evaluate(room, temperature, humidity)
            action, label = (match[1], f"#{match[0]}") if match else (None, "")

        if action is None:
            logger.debug("'%s': nessun cambio necessario (T=%s RH=%s)",
                         room_name, temperature, humidity)
            return

        # 2b) Filtro stagionale (solo per accensioni): blocca la modalità non
        #     ammessa nella stagione, salvo sblocco di sicurezza.
        if self._season is not None and action.power:
            if not self._season.mode_allowed(action.mode, temperature):
                logger.info("'%s': %s (mode=%s) bloccata dalla stagione %s",
                            room_name, label, action.mode, self._season.season.value)
                await self._db.insert_automation_log(
                    room_name=room_name, rule_matched=label,
                    action_taken=f"bloccata (mode {action.mode} fuori stagione "
                                 f"{self._season.season.value})",
                    temp_at_trigger=temperature, humidity_at_trigger=humidity)
                return

        # 3) Cooldown: non agire troppo spesso.
        elapsed = time.monotonic() - rt.last_command_monotonic
        if rt.last_command_monotonic > 0 and elapsed < self._cooldown_seconds:
            logger.info("'%s': cooldown attivo (%.0fs/%ds), '%s' rimandata",
                        room_name, elapsed, self._cooldown_seconds, label)
            return

        # 4) Applica l'azione (spegnimento o impostazione stato).
        if action.power:
            action_desc = (f"ON mode={action.mode} temp={action.temperature} "
                           f"fan={action.fan_speed}")
        else:
            action_desc = "OFF (comfort raggiunto)"
        try:
            if action.power:
                sent = await self._ac.set_device_state(
                    room.panasonic_device_id, power=True, mode=action.mode,
                    temperature=action.temperature, fan_speed=action.fan_speed)
                rt.is_on = True
            else:
                sent = await self._ac.turn_off(room.panasonic_device_id)
                rt.is_on = False
            if sent:
                rt.last_command_monotonic = time.monotonic()
            # Memorizza lo stato che IL SISTEMA si aspetta ora (per rilevare poi
            # un intervento esterno da telecomando / app Panasonic).
            rt.expected_power = bool(action.power)
            rt.expected_mode = action.mode if action.power else None
            rt.expected_temp = action.temperature if action.power else None
            await self._db.insert_automation_log(
                room_name=room_name, rule_matched=label,
                action_taken=action_desc if sent else f"{action_desc} (gia' attivo)",
                temp_at_trigger=temperature, humidity_at_trigger=humidity)
        except Exception as exc:  # noqa: BLE001 - cloud down: non crashare
            logger.error("Applicazione '%s' su '%s' fallita: %s", label, room_name, exc)
            await self._db.insert_automation_log(
                room_name=room_name, rule_matched=label,
                action_taken=f"ERRORE: {exc}",
                temp_at_trigger=temperature, humidity_at_trigger=humidity)

    # -- bootstrap stato all'avvio -----------------------------------------
    async def bootstrap_state(self) -> None:
        """
        All'avvio (anche dopo un black-out) legge lo stato REALE di ogni AC e
        popola il runtime: is_on + expected_*. Cosi' il sistema "sa" subito cosa
        stanno facendo i condizionatori e NON li tratta come spenti o come un
        intervento esterno fittizio al primo ciclo. Non invia nessun comando:
        si limita ad allinearsi alla realta'.
        """
        for room in self._cfg.rooms:
            if not room.panasonic_device_id:
                continue
            rt = self._runtime.setdefault(room.name, _RoomRuntime())
            try:
                state = await self._ac.get_device_state(room.panasonic_device_id)
            except Exception as exc:  # noqa: BLE001 - cloud down: riprovera' al ciclo
                logger.warning("Bootstrap stato '%s' fallito: %s", room.name, exc)
                continue
            on = (state.get("power") == "On")
            rt.is_on = on
            rt.expected_power = on
            rt.expected_mode = state.get("mode") if on else None
            rt.expected_temp = state.get("target_temperature") if on else None
            logger.info("Stato AC ripreso '%s': power=%s mode=%s temp=%s",
                        room.name, state.get("power"), state.get("mode"),
                        state.get("target_temperature"))

    # -- rilevamento intervento manuale esterno ----------------------------
    async def _detect_external_change(self, room_name: str, room: Room,
                                      rt: _RoomRuntime) -> bool:
        """
        True se lo stato reale dell'AC differisce da quello impostato dal sistema
        (-> qualcuno ha usato telecomando/app Panasonic). Aggiorna comunque
        expected_* allo stato reale, cosi' lo stesso intervento non scatta due volte.

        Prima volta (expected_power is None): nessun riferimento, non rileva nulla
        ma allinea l'atteso allo stato corrente.
        """
        try:
            state = await self._ac.get_device_state(room.panasonic_device_id)
        except Exception:  # noqa: BLE001 - cloud down: non posso confrontare
            return False

        real_power = (state.get("power") == "On")
        real_mode = state.get("mode")
        real_temp = state.get("target_temperature")

        # Bootstrap: nessun riferimento ancora -> allinea e basta.
        if rt.expected_power is None:
            rt.expected_power, rt.expected_mode, rt.expected_temp = (
                real_power, real_mode if real_power else None,
                real_temp if real_power else None)
            rt.is_on = real_power
            return False

        # Confronto: power sempre; mode/temp solo se acceso (a OFF non contano).
        changed = real_power != rt.expected_power
        if real_power and rt.expected_power:
            if rt.expected_mode is not None and real_mode != rt.expected_mode:
                changed = True
            if (rt.expected_temp is not None and real_temp is not None
                    and abs(float(real_temp) - float(rt.expected_temp)) >= 0.5):
                changed = True

        # Allinea sempre l'atteso e l'is_on allo stato reale.
        rt.expected_power, rt.expected_mode, rt.expected_temp = (
            real_power, real_mode if real_power else None,
            real_temp if real_power else None)
        rt.is_on = real_power
        return changed

    # -- valutazione comfort-band ------------------------------------------
    async def _comfort_action(self, room: Room, temperature, humidity,
                              rt: _RoomRuntime) -> tuple[Optional[Action], str]:
        """Sceglie la banda di comfort secondo la stagione e decide l'azione."""
        from core.season import Season

        comfort = room.comfort
        season = self._season.season if self._season is not None else None

        # Stato on/off corrente: seed pigro dallo stato reale dell'AC.
        if rt.is_on is None:
            try:
                state = await self._ac.get_device_state(room.panasonic_device_id)
                rt.is_on = (state.get("power") == "On")
            except Exception:  # noqa: BLE001
                rt.is_on = False

        # Selezione della banda in base alla stagione.
        if season == Season.HEATING and comfort.winter:
            band, heating, lbl = comfort.winter, True, "comfort:inverno"
        elif season == Season.COOLING and comfort.summer:
            band, heating, lbl = comfort.summer, False, "comfort:estate"
        else:
            # Mezza stagione (o stagione assente): pivot sul punto medio dei target.
            s, w = comfort.summer, comfort.winter
            if s and w:
                pivot = (s.target_temp + w.target_temp) / 2
                if temperature is not None and temperature > pivot:
                    band, heating, lbl = s, False, "comfort:mezza(estate)"
                else:
                    band, heating, lbl = w, True, "comfort:mezza(inverno)"
            elif s:
                band, heating, lbl = s, False, "comfort:estate"
            elif w:
                band, heating, lbl = w, True, "comfort:inverno"
            else:
                return None, ""

        action = comfort_decision(band, heating, temperature, humidity, rt.is_on)
        return action, lbl

    # -- supporto override: applica subito l'azione manuale -----------------
    async def apply_override_now(self, room_name: str, action: Action,
                                 minutes: int = 60) -> None:
        """Imposta l'override e applica immediatamente l'azione richiesta."""
        self.set_override(room_name, action, minutes)
        room = self._cfg.get_room(room_name)
        if room and room.panasonic_device_id:
            await self._ac.set_device_state(
                room.panasonic_device_id,
                power=action.power, mode=action.mode,
                temperature=action.temperature, fan_speed=action.fan_speed,
            )
