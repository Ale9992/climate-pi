#!/usr/bin/env python3
"""
FASE 1 — Mapping Tool per Climate Automation.

Script standalone interattivo da eseguire UNA VOLTA prima di avviare il sistema.

Cosa fa:
  1. Chiede l'IP dell'hub IKEA Dirigera.
  2. Avvia l'autenticazione (generazione token) chiedendo di premere il tasto
     fisico sul fondo dell'hub.
  3. Si connette all'hub e dumpa tutti i device disponibili.
  4. Guida l'utente nella creazione delle stanze, associando un sensore ambiente
     IKEA e (opzionalmente) un condizionatore Panasonic a ciascuna stanza.
  5. Genera config/config.yaml completo, con valori noti popolati e placeholder
     commentati per ciò che va completato a mano.

Eseguibile in modo totalmente indipendente dal sistema principale:
    python tools/mapping_tool.py

Dipendenze: dirigera, aio-panasonic-comfort-cloud, PyYAML.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Import opzionali: il tool deve fallire con un messaggio chiaro, non con un
# traceback criptico, se le librerie non sono installate.
# ---------------------------------------------------------------------------
try:
    import dirigera
except ImportError:  # pragma: no cover
    print("ERRORE: la libreria 'dirigera' non e' installata.\n"
          "        Esegui:  pip install dirigera", file=sys.stderr)
    sys.exit(1)


# Percorso del config: <repo>/config/config.yaml (un livello sopra tools/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "config.yaml"
# Cache del token Dirigera: evita di ripremere il tasto fisico ad ogni rilancio.
TOKEN_CACHE_PATH = CONFIG_DIR / ".dirigera_token"


# ===========================================================================
# Utility di I/O interattivo
# ===========================================================================
def banner(text: str) -> None:
    """Stampa un'intestazione di sezione ben visibile."""
    line = "=" * 70
    print(f"\n{line}\n  {text}\n{line}")


def ask(prompt: str, default: Optional[str] = None) -> str:
    """Chiede una stringa, con valore di default opzionale."""
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("  -> Inserisci un valore.")


def ask_int(prompt: str, lo: int, hi: int) -> int:
    """Chiede un intero in un range [lo, hi] inclusivi."""
    while True:
        raw = input(f"{prompt} ({lo}-{hi}): ").strip()
        if raw.isdigit() and lo <= int(raw) <= hi:
            return int(raw)
        print(f"  -> Inserisci un numero tra {lo} e {hi}.")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Domanda si/no."""
    hint = "S/n" if default else "s/N"
    while True:
        raw = input(f"{prompt} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("s", "si", "sì", "y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  -> Rispondi 's' o 'n'.")


# ===========================================================================
# STEP 1 + 2 — Autenticazione e generazione token Dirigera
# ===========================================================================
def generate_dirigera_token(ip_address: str) -> str:
    """
    Genera un token di accesso permanente per l'hub Dirigera.

    Il flusso PKCE della libreria dirigera richiede di premere il tasto fisico
    sul fondo dell'hub entro pochi secondi dalla richiesta del token.

    Tenta prima l'API interna della libreria (hub.auth); se la struttura del
    modulo dovesse cambiare tra versioni, ricade su un inserimento manuale del
    token generato col comando CLI `generate-token`.
    """
    banner("STEP 1 — Autenticazione hub Dirigera")

    # Riuso del token cacheato da un run precedente: niente tasto fisico.
    if TOKEN_CACHE_PATH.exists():
        cached = TOKEN_CACHE_PATH.read_text(encoding="utf-8").strip()
        if cached and ask_yes_no(
            "Trovato un token salvato da un run precedente. Riutilizzarlo?",
            default=True,
        ):
            print("Token riutilizzato dalla cache.")
            return cached

    print("Sto per richiedere un token di accesso all'hub.")
    print("Quando comparira' la richiesta, premi il TASTO FISICO sul fondo")
    print("dell'hub Dirigera (il pulsante di accoppiamento) e poi premi ENTER.\n")

    try:
        # API interna della libreria dirigera (Leggin/dirigera).
        # NB: send_challenge() calcola internamente il code_challenge a partire
        # dal code_verifier — NON va passato code_challenge(verifier).
        from dirigera.hub.auth import (
            ALPHABET,
            CODE_LENGTH,
            random_code,
            send_challenge,
            get_token,
        )

        code_verifier = random_code(ALPHABET, CODE_LENGTH)
        code = send_challenge(ip_address, code_verifier)

        input(">>> Premi il tasto sul fondo dell'hub, poi premi ENTER per continuare...")
        print("Recupero del token in corso...")
        token = get_token(ip_address, code, code_verifier)

        if not token:
            raise RuntimeError("Token vuoto restituito dall'hub.")
        print("Token generato con successo.")
        return token

    except Exception as exc:  # noqa: BLE001 - vogliamo un fallback robusto
        print(f"\nGenerazione automatica del token fallita: {exc}")
        print("Fallback manuale:")
        print("  1. In un altro terminale esegui:  generate-token " + ip_address)
        print("  2. Premi il tasto sul fondo dell'hub quando richiesto.")
        print("  3. Copia il token stampato e incollalo qui sotto.\n")
        token = ask("Incolla il token Dirigera")
        return token


# ===========================================================================
# STEP 3 + 4 — Connessione e dump dei device
# ===========================================================================
def connect_hub(token: str, ip_address: str):
    """Crea l'istanza Hub e verifica la connessione."""
    banner("STEP 2 — Connessione all'hub")
    hub = dirigera.Hub(token=token, ip_address=ip_address)
    # get_all_devices() valida implicitamente la connessione.
    devices = hub.get_all_devices()
    print(f"Connesso. Trovati {len(devices)} device totali.")
    return hub, devices


def _attr(device: Any, name: str, default: Any = None) -> Any:
    """
    Accesso difensivo a un attributo del device che puo' vivere sia sul device
    sia dentro device.attributes (a seconda della versione della libreria).
    """
    if hasattr(device, name):
        return getattr(device, name)
    attrs = getattr(device, "attributes", None)
    if attrs is not None and hasattr(attrs, name):
        return getattr(attrs, name)
    return default


def _room_info(device: Any) -> tuple[Optional[str], Optional[str]]:
    """Ritorna (room_name, room_id) gestendo l'assenza della stanza."""
    room = getattr(device, "room", None)
    if room is None:
        return None, None
    return getattr(room, "name", None), getattr(room, "id", None)


def dump_devices(devices: list[Any]) -> dict[str, list[Any]]:
    """
    Stampa a console tutti i device e ritorna un dizionario raggruppato per
    categoria. Identifica i sensori ambiente (temperatura/umidita').
    """
    banner("STEP 3 — Device trovati sull'hub")

    grouped: dict[str, list[Any]] = {}
    for dev in devices:
        dtype = _attr(dev, "type", "unknown") or "unknown"
        grouped.setdefault(dtype, []).append(dev)

    for dtype in sorted(grouped):
        print(f"\n--- Tipo: {dtype}  ({len(grouped[dtype])}) ---")
        for dev in grouped[dtype]:
            name = _attr(dev, "custom_name", "(senza nome)")
            room_name, room_id = _room_info(dev)
            reachable = _attr(dev, "is_reachable", "?")
            print(f"  id          : {dev.id}")
            print(f"  nome        : {name}")
            print(f"  tipo        : {dtype}")
            print(f"  stanza      : {room_name or '-'} (id: {room_id or '-'})")
            print(f"  raggiungibile: {reachable}")

            # Per i sensori ambiente, mostra le letture correnti.
            temp = _attr(dev, "current_temperature")
            rh = _attr(dev, "current_r_h")
            if rh is None:
                rh = _attr(dev, "current_rh")  # nome alternativo
            if temp is not None or rh is not None:
                print(f"  temperatura : {temp}")
                print(f"  umidita'    : {rh}")
            print()

    return grouped


def extract_environment_sensors(grouped: dict[str, list[Any]], hub: Any = None) -> list[Any]:
    """
    Estrae la lista dei sensori ambiente. Metodo primario: hub.get_environment_sensors()
    (preciso e indipendente dalla stringa di tipo). Fallback: tipo 'environmentSensor'
    nel dump, poi scansione generica per letture di temperatura/umidita'.
    """
    if hub is not None:
        try:
            sensors = hub.get_environment_sensors()
            if sensors:
                return sensors
        except Exception:  # noqa: BLE001 - ricadiamo sul dump gia' ottenuto
            pass

    sensors: list[Any] = grouped.get("environmentSensor", [])
    if sensors:
        return sensors

    # Fallback: scansione generica.
    for devs in grouped.values():
        for dev in devs:
            temp = _attr(dev, "current_temperature")
            rh = _attr(dev, "current_r_h") or _attr(dev, "current_rh")
            if temp is not None or rh is not None:
                sensors.append(dev)
    return sensors


# ===========================================================================
# STEP 5 + 6 — Definizione stanze e associazione device
# ===========================================================================
def select_sensor(sensors: list[Any]) -> Any:
    """Mostra l'elenco numerato dei sensori e ne fa selezionare uno."""
    print("\n  Sensori ambiente disponibili:")
    for idx, sensor in enumerate(sensors, start=1):
        name = _attr(sensor, "custom_name", "(senza nome)")
        room_name, _ = _room_info(sensor)
        temp = _attr(sensor, "current_temperature")
        rh = _attr(sensor, "current_r_h") or _attr(sensor, "current_rh")
        print(f"    [{idx}] {name}  | stanza: {room_name or '-'} "
              f"| T={temp} RH={rh} | id={sensor.id}")
    choice = ask_int("  Seleziona il sensore per questa stanza", 1, len(sensors))
    return sensors[choice - 1]


def _room_entry(name: str, sensor: Optional[Any]) -> dict[str, Any]:
    """Costruisce un dict-stanza, con sensore opzionale gia' associato."""
    return {
        "name": name,
        "ikea_sensor_id": sensor.id if sensor is not None else None,
        "ikea_sensor_name": _attr(sensor, "custom_name", name) if sensor is not None else None,
        "panasonic_device_id": None,
        "panasonic_device_name": None,
    }


def auto_build_rooms(sensors: list[Any]) -> Optional[list[dict[str, Any]]]:
    """
    Importa automaticamente le stanze dal Dirigera: raggruppa i sensori per la
    stanza gia' assegnata nell'app IKEA (sensor.room.name). Ritorna la lista
    delle stanze proposte, oppure None se l'utente preferisce la modalita'
    manuale o se i sensori non hanno una stanza assegnata.
    """
    # Raggruppa i sensori per nome stanza (saltando quelli senza stanza).
    by_room: dict[str, list[Any]] = {}
    orphans: list[Any] = []
    for s in sensors:
        room_name, _ = _room_info(s)
        if room_name:
            by_room.setdefault(room_name, []).append(s)
        else:
            orphans.append(s)

    if not by_room:
        print("I sensori non hanno una stanza assegnata nell'app IKEA.")
        return None

    print("\nStanze rilevate automaticamente dall'hub Dirigera:")
    for room_name, sens in by_room.items():
        names = ", ".join(_attr(s, "custom_name", "(sensore)") for s in sens)
        print(f"  - {room_name}  ({len(sens)} sensore/i: {names})")
    if orphans:
        print(f"  [!] {len(orphans)} sensore/i senza stanza assegnata (li ignoro "
              "in automatico; usa la modalita' manuale per gestirli).")

    if not ask_yes_no("\nUsare questa mappatura automatica?", default=True):
        return None

    rooms: list[dict[str, Any]] = []
    for room_name, sens in by_room.items():
        if len(sens) == 1:
            chosen = sens[0]
        else:
            # Piu' sensori nella stessa stanza: fai scegliere quale usare.
            print(f"\nLa stanza '{room_name}' ha piu' sensori:")
            chosen = select_sensor(sens)
        rooms.append(_room_entry(room_name, chosen))

    print(f"\nImportate automaticamente {len(rooms)} stanze.")
    return rooms


def build_rooms(sensors: list[Any]) -> list[dict[str, Any]]:
    """
    Definizione delle stanze. Prova prima l'import automatico dalle stanze gia'
    configurate nell'app IKEA; se l'utente preferisce, ricade sulla modalita'
    manuale. Il device Panasonic viene associato dopo (configure_panasonic),
    perche' vive su un cloud separato.
    """
    banner("STEP 4 — Definizione delle stanze/zone")

    if not sensors:
        print("ATTENZIONE: nessun sensore ambiente trovato sull'hub.")
        print("Puoi comunque definire le stanze, ma il sensor_id andra'")
        print("inserito manualmente nel config.yaml.")
    else:
        # Tentativo di import automatico dalle stanze IKEA.
        auto = auto_build_rooms(sensors)
        if auto is not None:
            return auto
        print("\nPasso alla definizione MANUALE delle stanze.")

    rooms: list[dict[str, Any]] = []
    used_sensor_ids: set[str] = set()

    while True:
        n = len(rooms) + 1
        print(f"\n--- Stanza #{n} ---")
        room_name = ask("Nome della stanza (es. Camera, Salotto)")

        sensor_id: Optional[str] = None
        sensor_name: Optional[str] = None
        if sensors:
            available = [s for s in sensors if s.id not in used_sensor_ids]
            if not available:
                print("  Tutti i sensori sono gia' stati assegnati.")
            else:
                sensor = select_sensor(available)
                sensor_id = sensor.id
                sensor_name = _attr(sensor, "custom_name", room_name)
                used_sensor_ids.add(sensor_id)

        rooms.append({
            "name": room_name,
            "ikea_sensor_id": sensor_id,
            "ikea_sensor_name": sensor_name,
            "panasonic_device_id": None,
            "panasonic_device_name": None,
        })

        if not ask_yes_no("Aggiungere un'altra stanza?", default=True):
            break

    print(f"\nDefinite {len(rooms)} stanze.")
    return rooms


# ===========================================================================
# STEP opzionale — Discovery device Panasonic Comfort Cloud
# ===========================================================================
async def _discover_panasonic_devices(username: str, password: str) -> list[dict[str, str]]:
    """
    Si autentica su Panasonic Comfort Cloud e ritorna la lista dei device come
    dict {id, name, model}. Isolato in funzione async dedicata.

    Nota: aio-panasonic-comfort-cloud e' una libreria non ufficiale e la sua API
    e' cambiata nel tempo. Questo codice gestisce le varianti piu' comuni e, in
    caso di incompatibilita', solleva un'eccezione gestita dal chiamante.
    """
    import aiohttp
    from aio_panasonic_comfort_cloud import ApiClient

    async with aiohttp.ClientSession() as session:
        client = ApiClient(username, password, session)
        await client.start_session()

        # get_devices() ritorna gli oggetti device cacheati dopo start_session.
        raw_devices = client.get_devices()

        result: list[dict[str, str]] = []
        for dev in raw_devices:
            dev_id = getattr(dev, "id", None) or getattr(dev, "guid", None) or str(dev)
            dev_name = getattr(dev, "name", None) or str(dev_id)
            dev_model = getattr(dev, "model", None) or "-"
            result.append({"id": str(dev_id), "name": str(dev_name), "model": str(dev_model)})
        return result


def configure_panasonic(rooms: list[dict[str, Any]]) -> Optional[dict[str, str]]:
    """
    Step opzionale: chiede le credenziali Panasonic, lista i device e li associa
    alle stanze. Ritorna le credenziali (per scriverle nel config) oppure None
    se l'utente salta lo step.

    Mutua `rooms` in place impostando panasonic_device_id / _name.
    """
    banner("STEP 5 — Device Panasonic Comfort Cloud (opzionale)")
    print("I condizionatori Panasonic sono su cloud, non sulla LAN.")
    print("Puoi associarli ora (servono le credenziali Comfort Cloud) oppure")
    print("saltare e inserire gli ID manualmente nel config.yaml.\n")

    if not ask_yes_no("Configurare ora i device Panasonic?", default=True):
        print("Step Panasonic saltato. Inserirai gli ID manualmente nel config.")
        return None

    username = ask("Username/email Panasonic Comfort Cloud")
    password = ask("Password Panasonic Comfort Cloud")

    try:
        print("\nConnessione a Panasonic Comfort Cloud...")
        pana_devices = asyncio.run(_discover_panasonic_devices(username, password))
    except ImportError:
        print("ERRORE: 'aio-panasonic-comfort-cloud' non installata.")
        print("        Esegui:  pip install aio-panasonic-comfort-cloud")
        print("        Salvo comunque le credenziali; associa gli ID a mano.")
        return {"username": username, "password": password}
    except Exception as exc:  # noqa: BLE001
        print(f"Impossibile recuperare i device Panasonic: {exc}")
        print("Salvo comunque le credenziali; associa gli ID a mano nel config.")
        return {"username": username, "password": password}

    if not pana_devices:
        print("Nessun device Panasonic trovato sull'account.")
        return {"username": username, "password": password}

    print(f"\nTrovati {len(pana_devices)} device Panasonic:")
    for idx, dev in enumerate(pana_devices, start=1):
        print(f"  [{idx}] {dev['name']}  | modello: {dev['model']} | id: {dev['id']}")

    # Associazione device -> stanza.
    print("\nAssocia ogni stanza al suo condizionatore Panasonic.")
    for room in rooms:
        print(f"\n  Stanza: {room['name']}")
        if not ask_yes_no("  Associare un condizionatore Panasonic a questa stanza?",
                           default=True):
            continue
        choice = ask_int("  Seleziona il device Panasonic", 1, len(pana_devices))
        chosen = pana_devices[choice - 1]
        room["panasonic_device_id"] = chosen["id"]
        room["panasonic_device_name"] = chosen["name"]
        print(f"  -> {room['name']} associata a {chosen['name']}")

    return {"username": username, "password": password}


# ===========================================================================
# STEP 7 — Generazione del config.yaml
# ===========================================================================
DEFAULT_RULES_YAML = """\
    rules:
      # Regole valutate in ordine: scatta la PRIMA il cui 'condition' e' vero.
      # Piu' condizioni nella stessa regola = AND logico.
      - condition:
          temp_gt: 26
          humidity_gt: 60
        action:
          power: on
          mode: Cool
          temperature: 24
          fan_speed: Auto
      - condition:
          temp_gt: 28
        action:
          power: on
          mode: Cool
          temperature: 22
          fan_speed: Mid
      - condition:
          humidity_gt: 75
        action:
          power: on
          mode: Dry
          fan_speed: Low
      - condition:
          temp_lt: 18
        action:
          power: on
          mode: Heat
          temperature: 20
          fan_speed: Auto"""


def _yaml_str(value: Optional[str]) -> str:
    """Formatta una stringa per YAML, o un placeholder commentato se None."""
    if value:
        return f'"{value}"'
    return ""


def generate_config(
    ip_address: str,
    token: str,
    rooms: list[dict[str, Any]],
    panasonic_creds: Optional[dict[str, str]],
) -> str:
    """Costruisce il contenuto del config.yaml come stringa, con commenti."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = []
    lines.append(f"# config.yaml — generato dal mapping_tool il {now}")
    lines.append("# Completa i campi commentati (#) dove necessario, poi avvia il sistema.")
    lines.append("")

    # --- Dirigera ---
    lines.append("dirigera:")
    lines.append(f'  ip_address: "{ip_address}"')
    lines.append(f'  token: "{token}"')
    lines.append("")

    # --- Panasonic ---
    lines.append("panasonic:")
    if panasonic_creds:
        lines.append(f'  username: "{panasonic_creds["username"]}"')
        lines.append(f'  password: "{panasonic_creds["password"]}"')
    else:
        lines.append("  # Credenziali Panasonic Comfort Cloud — da completare:")
        lines.append('  username: ""  # email@example.com')
        lines.append('  password: ""')
    lines.append("")

    # --- Rooms ---
    lines.append("rooms:")
    for room in rooms:
        lines.append(f'  - name: "{room["name"]}"')

        # Sensore IKEA
        if room["ikea_sensor_id"]:
            comment = f'  # {room["ikea_sensor_name"]}' if room.get("ikea_sensor_name") else ""
            lines.append(f'    ikea_sensor_id: "{room["ikea_sensor_id"]}"{comment}')
        else:
            lines.append('    ikea_sensor_id: ""  # TODO: UUID del sensore IKEA')

        # Device Panasonic
        if room["panasonic_device_id"]:
            comment = f'  # {room["panasonic_device_name"]}' if room.get("panasonic_device_name") else ""
            lines.append(f'    panasonic_device_id: "{room["panasonic_device_id"]}"{comment}')
        else:
            lines.append('    panasonic_device_id: ""  # TODO: ID condizionatore Panasonic')

        lines.append(DEFAULT_RULES_YAML)
        lines.append("")

    # --- Schedule ---
    lines.append("schedule:")
    lines.append('  force_off_time: "03:00"  # spegnimento forzato notturno (HH:MM)')
    lines.append("")

    # --- Engine ---
    lines.append("engine:")
    lines.append("  poll_interval_seconds: 60   # fallback polling se il WebSocket cade")
    lines.append("  cooldown_minutes: 5         # tempo minimo tra due cambi di stato AC")
    lines.append("  hysteresis_temp: 0.5        # banda morta temperatura (°C)")
    lines.append("  hysteresis_humidity: 2      # banda morta umidita' (%)")
    lines.append("")

    return "\n".join(lines)


def write_config(content: str) -> None:
    """Scrive il config.yaml, facendo backup se ne esiste gia' uno."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_PATH.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = CONFIG_PATH.with_suffix(f".yaml.bak_{ts}")
        os.rename(CONFIG_PATH, backup)
        print(f"Config esistente salvato come backup: {backup.name}")

    CONFIG_PATH.write_text(content, encoding="utf-8")


# ===========================================================================
# Riepilogo finale
# ===========================================================================
def print_summary(rooms: list[dict[str, Any]], panasonic_creds: Optional[dict[str, str]]) -> None:
    banner("RIEPILOGO CONFIGURAZIONE")
    print(f"File generato: {CONFIG_PATH}\n")
    print(f"Stanze configurate: {len(rooms)}")
    for room in rooms:
        sensor = room["ikea_sensor_id"] or "DA COMPLETARE"
        pana = room["panasonic_device_id"] or "DA COMPLETARE"
        print(f"  - {room['name']}")
        print(f"      sensore IKEA   : {sensor}")
        print(f"      device Panasonic: {pana}")

    print("\nPanasonic Comfort Cloud:",
          "credenziali salvate" if panasonic_creds else "DA COMPLETARE manualmente")

    print("\nProssimi passi:")
    todo = []
    if not panasonic_creds:
        todo.append("  - Inserisci username/password Panasonic in config.yaml")
    if any(not r["panasonic_device_id"] for r in rooms):
        todo.append("  - Completa i panasonic_device_id mancanti")
    if any(not r["ikea_sensor_id"] for r in rooms):
        todo.append("  - Completa gli ikea_sensor_id mancanti")
    todo.append(f"  - Rivedi le SOGLIE in {CONFIG_PATH.name} (sezione rules di ogni stanza)")
    todo.append("  - Avvia il sistema con setup.sh / systemd")
    print("\n".join(todo))
    print("\nLe soglie di default (temp 26/28/18°C, umidita' 60/75%) sono modificabili")
    print("a mano nel config.yaml oppure dalla dashboard una volta avviato il sistema.")


# ===========================================================================
# Main
# ===========================================================================
def main() -> int:
    banner("CLIMATE AUTOMATION — Mapping Tool (FASE 1)")
    print("Questo tool genera config/config.yaml a partire dai device del tuo hub.")

    ip_address = ask("\nIP dell'hub Dirigera", default="192.168.1.x")
    if ip_address == "192.168.1.x":
        print("Devi inserire l'IP reale dell'hub. Riavvia il tool.")
        return 1

    token = generate_dirigera_token(ip_address)

    try:
        hub, devices = connect_hub(token, ip_address)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERRORE di connessione all'hub: {exc}")
        print("Verifica IP e token e riprova.")
        return 1

    # Connessione riuscita: salva il token in cache per i prossimi run.
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE_PATH.write_text(token, encoding="utf-8")
    try:
        os.chmod(TOKEN_CACHE_PATH, 0o600)  # leggibile solo dall'utente
    except OSError:
        pass

    grouped = dump_devices(devices)
    sensors = extract_environment_sensors(grouped, hub)
    print(f"\nRilevati {len(sensors)} sensori ambiente utilizzabili.")

    rooms = build_rooms(sensors)
    panasonic_creds = configure_panasonic(rooms)

    content = generate_config(ip_address, token, rooms, panasonic_creds)
    write_config(content)
    print_summary(rooms, panasonic_creds)

    banner("FATTO")
    print(f"Config pronto in: {CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrotto dall'utente.")
        sys.exit(130)
