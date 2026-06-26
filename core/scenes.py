"""
core/scenes.py — Scene multi-stanza attivabili a comando (es. via Alexa).

Una "scena" applica in un colpo solo uno stato a TUTTI i condizionatori e
registra un override BREVE che sospende l'automazione giusto il tempo del "colpo"
(poi la programmazione normale riprende: la scena si INTEGRA col sistema, non lo
scavalca per ore). Pensata per i comandi vocali:

  - "afa" -> accende TUTTI gli AC in Powerful, alla temperatura di boost di OGNI
             stanza (i nostri parametri di comfort), ventola High. NON forza 16°
             fisso: rispetta i setpoint del sistema e dopo l'hold ridà il
             controllo all'automazione.
  - "off" -> spegne tutti i condizionatori.

PARAMETRI MODIFICABILI: vedi le costanti qui sotto. La frase su Alexa resta
identica; cambia solo cosa fa la scena.

Note di precedenza (volute):
  - Lo spegnimento forzato notturno (fascia force_off_time -> night_off_end) ha
    COMUNQUE la precedenza (rule_engine._in_night_window).
  - Le stanze monitor_only vengono comandate lo stesso da una scena: e' un comando
    MANUALE esplicito dell'utente ("accendi tutti"), non automazione.
"""

from __future__ import annotations

import logging

from core.config import Action

logger = logging.getLogger("climate.scenes")

# Quanto a lungo la scena "tiene" sospendendo l'automazione (minuti). Breve:
# e' un "colpo" iniziale, poi l'automazione normale riprende. Il Powerful sui
# Panasonic si auto-esaurisce in ~20 min, quindi 30 copre il boost e poco oltre.
SCENE_HOLD_MINUTES = 30

# Temperatura di ripiego per stanze SENZA banda di comfort estiva (es. Salotto).
AFA_FALLBACK_SETPOINT = 22

SCENE_NAMES = ["afa", "off"]


def scene_names() -> list[str]:
    """Nomi delle scene disponibili."""
    return list(SCENE_NAMES)


def _afa_setpoint(room) -> float:
    """Temperatura dell'afa per la stanza: il boost_setpoint estivo (i NOSTRI
    parametri), o il setpoint normale, o il fallback se la stanza non ha comfort."""
    comfort = getattr(room, "comfort", None)
    summer = getattr(comfort, "summer", None) if comfort else None
    if summer is not None:
        return summer.boost_setpoint or summer.setpoint or AFA_FALLBACK_SETPOINT
    return AFA_FALLBACK_SETPOINT


def _afa_action(room) -> Action:
    """Azione afa per UNA stanza: Cool + Powerful + High al setpoint di boost."""
    return Action(
        power=True,
        mode="Cool",
        temperature=_afa_setpoint(room),
        fan_speed="High",
        eco_mode="Powerful",
    )


async def run_scene(name: str, cfg, engine, ac) -> dict:
    """
    Esegue una scena su tutti i condizionatori configurati.

    Per ogni stanza con un AC: registra un override breve (sospende l'automazione
    per SCENE_HOLD_MINUTES) e applica lo stato. L'afa usa il setpoint di boost
    PER STANZA (non un valore fisso). Solleva KeyError se la scena non esiste; non
    solleva sui singoli AC falliti (un AC giu' non blocca gli altri).
    """
    if name not in SCENE_NAMES:
        raise KeyError(name)

    results: list[dict] = []
    for room in cfg.rooms:
        if not room.panasonic_device_id:
            continue
        try:
            if name == "afa":
                action = _afa_action(room)
                engine.set_override(room.name, action, minutes=SCENE_HOLD_MINUTES)
                await ac.set_device_state(
                    room.panasonic_device_id,
                    power=True, mode="Cool",
                    temperature=action.temperature,
                    fan_speed="High", eco_mode="Powerful",
                )
                results.append({"room": room.name, "ok": True,
                                "setpoint": action.temperature})
            else:  # "off"
                engine.set_override(room.name, Action(power=False),
                                    minutes=SCENE_HOLD_MINUTES)
                await ac.turn_off(room.panasonic_device_id)
                results.append({"room": room.name, "ok": True})
        except Exception as exc:  # noqa: BLE001 - un AC giu' non ferma gli altri
            logger.error("Scena '%s' su '%s' fallita: %s", name, room.name, exc)
            results.append({"room": room.name, "ok": False, "error": str(exc)})

    ok = sum(1 for r in results if r["ok"])
    logger.info("Scena '%s' applicata: %d/%d AC ok (hold %d min)",
                name, ok, len(results), SCENE_HOLD_MINUTES)
    return {
        "scene": name,
        "applied": "on" if name == "afa" else "off",
        "rooms": results,
        "hold_minutes": SCENE_HOLD_MINUTES,
    }
