# 🌡️ Climate Automation

**🇮🇹 Italiano** · [🇬🇧 English](README.en.md)

Sistema di **domotica climatica self-hosted** che governa automaticamente i
condizionatori **Panasonic** in base a temperatura, umidità, stagione e
**presenza** delle persone in casa — con dashboard web in stile iOS e controllo
delle **luci IKEA**.

Pensato per girare 24/7 su un **Raspberry Pi**, senza cloud di terze parti oltre
a quelli dei produttori, senza app proprietarie.

```
Sensori IKEA Dirigera ──┐
Stato AC Panasonic ─────┼──► Rule Engine ──► comanda gli AC (Cool/Heat/Dry…)
Presenza FRITZ!Box ─────┘                └──► Dashboard web (React)
Luci IKEA Dirigera ─────────────────────────► controllo on/off + dimmer
```

> ⚠️ Progetto personale, pubblicato a scopo didattico. Dipende da hardware
> specifico (vedi *Requisiti*). Non è un prodotto commerciale.

---

## ✨ Funzionalità

### Automazione climatica intelligente
- **Modello comfort-band**: mantiene la temperatura entro una banda attorno a un
  target, accendendo/spegnendo l'AC con **isteresi** (niente cicli continui).
  Soglie calibrate sullo storico consumi reale.
- **Consapevolezza stagionale**: la stagione è decisa dalla **media mobile della
  temperatura esterna** (letta dagli AC), così d'estate non parte mai il
  riscaldamento e viceversa — con sblocco di sicurezza per condizioni estreme.
- **Deumidificazione automatica**: passa in modalità Dry quando l'umidità supera
  una soglia (consuma poco, migliora il comfort).
- **Spegnimento forzato + fascia notturna**: gli AC restano spenti in una fascia
  oraria configurabile (es. 03:00–08:00), anche se fa caldo.

### Presenza (FRITZ!Box, senza app sul telefono)
- **Casa vuota → spegne tutto**: rileva se gli smartphone sono connessi al WiFi
  (via TR-064); dopo un *grace period* senza nessuno, spegne i condizionatori.
- **Presenza per-persona**: una stanza può seguire **uno specifico telefono**
  (es. la camera segue solo il tuo iPhone).
- **Fail-safe**: se il FRITZ!Box non risponde, assume "casa abitata" — un errore
  di rete non toglie mai il comfort.

### Convivenza col mondo reale
- **Rileva il telecomando**: se accendi/spegni l'AC dal telecomando o dall'app
  Panasonic, il sistema se ne accorge e **rispetta la scelta** (non "combatte").
- **Ripresa dopo black-out**: al riavvio legge lo stato reale degli AC e riprende
  coerentemente; il Pi si riaccende da solo al ritorno della corrente.

### Luci IKEA
- Controllo **on/off + dimmer** delle luci Dirigera, raggruppate per stanza.
- **Plafoniere**: più lampadine che formano un'unica plafoniera vengono comandate
  insieme come un solo controllo (configurabile per stanza).

### Dashboard web
- Interfaccia **React** responsive in stile *glassmorphism / iOS*, navigazione
  per stanza, controllo termostato (modalità, ventola, swing, nanoe™X, Powerful/
  Quiet), luci, grafici storici di temperatura/umidità, consumi.
- Servita dallo stesso processo backend, raggiungibile da tutta la rete locale.

---

## 🧰 Stack tecnico

| Livello | Tecnologia |
|---|---|
| Backend | Python 3.11+ (asyncio), FastAPI + Uvicorn |
| Persistenza | SQLite (async, `aiosqlite`) |
| Scheduling | APScheduler |
| Integrazioni | `dirigera` (IKEA), `aio-panasonic-comfort-cloud`, `fritzconnection` |
| Frontend | React 18 + Vite + Tailwind CSS v4, Material Design Icons |
| Deploy | systemd su Raspberry Pi OS / Debian |

---

## 🔌 Requisiti hardware

- **Hub IKEA DIRIGERA** (sensori ambiente VINDSTYRKA, luci) — API locale
- **Condizionatori Panasonic** compatibili **Comfort Cloud** (es. serie CS-TZ)
- **FRITZ!Box** (per il rilevamento presenza via TR-064) — opzionale
- Un host che gira 24/7: **Raspberry Pi 3** o superiore (testato), o qualsiasi
  Linux/macOS per lo sviluppo

> Senza tutti i componenti il sistema funziona comunque in modo ridotto (es.
> senza FRITZ!Box la presenza è disattivata, senza luci IKEA la relativa card
> non compare).

---

## 🚀 Installazione

### 1. Configurazione

Le credenziali **non** sono nel repository. Copia il template e inserisci i tuoi dati:

```bash
cp config/config.example.yaml config/config.yaml
```

Poi modifica `config/config.yaml`:
- **token Dirigera** — generato automaticamente dal mapping tool (passo 2)
- **email/password Panasonic Comfort Cloud**
- **credenziali FRITZ!Box** — crea un utente dedicato in *Sistema → Utenti*
- **ID dei device** (AC, sensori IKEA) — popolati dal mapping tool

`config/config.yaml` è in `.gitignore`: i segreti non finiscono mai su Git.

### 2. Mappatura hardware

Lo strumento interattivo scopre i tuoi device e popola il config:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tools/mapping_tool.py
```

(Per la prima autenticazione all'hub Dirigera dovrai premere il pulsante fisico.)

### 3. Build dashboard + avvio

```bash
# build della dashboard (richiede Node.js)
cd dashboard && npm install && npm run build && cd ..

# avvio
.venv/bin/python main.py          # produzione
DEV=1 .venv/bin/python main.py    # log verbosi
```

Dashboard e API su **http://localhost:8000**.

### 4. Deploy su Raspberry Pi (24/7)

Lo script `setup.sh` crea il venv, builda la dashboard (se Node è presente) e
installa il servizio systemd:

```bash
./setup.sh
```

Comandi utili:
```bash
sudo systemctl status climate-automation     # stato
journalctl -u climate-automation -f          # log live
sudo systemctl restart climate-automation    # riavvio
```

Il servizio è `enabled`: riparte da solo a ogni boot, crash o black-out.

> **Nota Pi headless**: la build React può esaurire la RAM su un Pi 3. Conviene
> buildare la `dashboard/dist/` su un'altra macchina e copiarla, evitando `npm`
> sul Pi.

---

## ⚙️ Configurazione principale (`config.yaml`)

```yaml
rooms:
  - name: "Camera"
    ikea_sensor_id: "<SENSOR_ID>"        # sensore ambiente IKEA (opzionale)
    panasonic_device_id: "<DEVICE_ID>"   # condizionatore
    presence_device_ip: "192.168.1.50"   # opz: questa stanza segue questo telefono
    comfort:
      summer: { target_temp: 25, deadband: 1.5, setpoint: 25 }
      winter: { target_temp: 21.5, deadband: 1.0, setpoint: 21 }

schedule:
  force_off_time: "03:00"   # inizio fascia notturna
  night_off_end: "08:00"    # fine fascia: AC spenti 03:00–08:00

presence:
  enabled: true
  fritzbox: { address, user, password }
  away_grace_minutes: 30
  devices: [ { name, ip, mac } ]

lights:
  ceiling_rooms: ["Salotto", "Camera"]  # luci comandate come unica plafoniera
```

Il template completo e commentato è in [`config/config.example.yaml`](config/config.example.yaml).

---

## 🗂️ Struttura

```
climate-automation/
├── main.py                 # entry point: orchestrazione asyncio + uvicorn
├── core/
│   ├── config.py           # caricamento config tipizzato
│   ├── rule_engine.py      # cuore: decide e comanda gli AC
│   ├── ac_controller.py    # wrapper async Panasonic Comfort Cloud
│   ├── sensor_poller.py    # lettura sensori IKEA (WebSocket + polling)
│   ├── season.py           # algoritmo stagionale (media mobile T esterna)
│   ├── presence.py         # presenza casa/persona via FRITZ!Box
│   ├── light_controller.py # luci IKEA (+ plafoniere)
│   └── scheduler.py        # spegnimento forzato notturno
├── api/                    # FastAPI: routes + modelli
├── db/                     # SQLite async (storico, log, comandi)
├── dashboard/              # frontend React + Vite + Tailwind
├── tools/mapping_tool.py   # discovery hardware + generazione config
├── setup.sh                # installazione + servizio systemd
└── docs/                   # analisi e note tecniche
```

---

## 🌐 API REST (estratto)

| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET` | `/api/rooms` | stato di tutte le stanze (temp, AC, energia, override) |
| `GET` | `/api/status` | connessioni, stagione, presenza |
| `POST` | `/api/rooms/{room}/ac/control` | controllo diretto AC (modo/temp/ventola/swing/nanoe/eco) |
| `GET` | `/api/rooms/{room}/history` | storico letture sensore |
| `GET` | `/api/lights` | luci raggruppate per stanza |
| `POST` | `/api/lights/{id}` | on/off + dimmer di una luce/plafoniera |
| `GET` | `/api/logs` | log delle decisioni di automazione |

---

## 🔐 Privacy & sicurezza

- Tutte le credenziali stanno **solo** in `config/config.yaml`, **gitignorato**.
- Nessun dato lascia la rete locale, salvo le API ufficiali dei produttori
  (Panasonic Comfort Cloud).
- La dashboard è **senza autenticazione**: esponila solo sulla LAN, **mai**
  direttamente su Internet (per l'accesso da fuori usa una VPN).

---

## 📄 Licenza

MIT — vedi [`LICENSE`](LICENSE).

---

<sub>Costruito con cura per una casa che si gestisce da sola. 🏠</sub>
