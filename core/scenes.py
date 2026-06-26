"""
core/scenes.py — Scene multi-stanza attivabili a comando (es. via Alexa).

Una "scena" applica in un colpo solo uno stato a TUTTI i condizionatori e
registra un override che SOSPENDE l'automazione per un po' (cosi' il rule engine,
la presenza e il comfort non la disfano al ciclo successivo). Pensata per i
comandi vocali:

  - "afa" -> tutti gli AC al massimo freddo (Cool 16°C, ventola High, Powerful)
  - "off" -> tutti i condizionatori spenti

I PARAMETRI delle scene si modificano QUI sotto: la frase su Alexa resta identica,
cambia solo cosa fa la scena. Non serve toccare nulla lato assistente vocale.

Note di precedenza (volute):
  - Lo spegnimento forzato notturno (fascia force_off_time -> night_off_end, es.
    03:00) ha COMUNQUE la precedenza: durante quella fascia il rule engine spegne
    al ciclo dopo, anche con la scena "afa" attiva (vedi rule_engine._in_night_window).
  - Le stanze monitor_only vengono comandate lo stesso da una scena: e' un comando
    MANUALE esplicito dell'utente ("accendi tutti"), non automazione.
"""

from __future__ import annotations

import logging
from typing import Any

from core.config import Action

logger = logging.getLogger("climate.scenes")

# Quanto a lungo la scena "tiene" sospendendo l'automazione (minuti).
SCENE_HOLD_MINUTES = 180

# --- Definizione delle scene (PARAMETRI MODIFICABILI) ----------------------
# Ogni scena e' lo stato applicato a OGNI condizionatore. power=False = spegni.
SCENES: dict[str, dict[str, Any]] = {
    # "Protocollo afa": massima potenza di raffrescamento.
    "afa": {
        "power": True,
        "mode": "Cool",
        "temperature": 16,      # minimo Panasonic
        "fan_speed": "High",    # ventola massima
        "eco_mode": "Powerful",  # boost Panasonic (la vera "massima potenza")
    },
    # "Spegni tutto": tutti i condizionatori OFF.
    "off": {
        "power": False,
    },
}


def scene_names() -> list[str]:
    """Nomi delle scene disponibili."""
    return list(SCENES.keys())


async def run_scene(name: str, cfg, engine, ac) -> dict:
    """
    Esegue una scena su tutti i condizionatori configurati.

    Per ogni stanza con un AC:
      1) registra l'override (sospende l'automazione per SCENE_HOLD_MINUTES);
      2) applica lo stato della scena (con eco_mode, che apply_override_now non
         passerebbe — per questo usiamo set_override + set_device_state diretti).

    Solleva KeyError se la scena non esiste. Non solleva sui singoli Az falliti:
    raccoglie l'esito per stanza (un AC irraggiungibile non blocca gli altri).
    """
    spec = SCENES.get(name)
    if spec is None:
        raise KeyError(name)

    power = bool(spec.get("power", True))
    action = Action(
        power=power,
        mode=spec.get("mode"),
        temperature=spec.get("temperature"),
        fan_speed=spec.get("fan_speed"),
        eco_mode=spec.get("eco_mode"),
    )

    results: list[dict] = []
    for room in cfg.rooms:
        if not room.panasonic_device_id:
            continue
        # Hold: sospende il rule engine su questa stanza (senza inviare nulla).
        engine.set_override(room.name, action, minutes=SCENE_HOLD_MINUTES)
        try:
            if power:
                await ac.set_device_state(
                    room.panasonic_device_id,
                    power=True,
                    mode=spec.get("mode"),
                    temperature=spec.get("temperature"),
                    fan_speed=spec.get("fan_speed"),
                    eco_mode=spec.get("eco_mode"),
                )
            else:
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
        "applied": power and "on" or "off",
        "rooms": results,
        "hold_minutes": SCENE_HOLD_MINUTES,
    }
