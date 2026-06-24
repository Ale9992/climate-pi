# 🌡️ Climate Automation

**🇮🇹 Italiano** · [🇬🇧 English](README.md)

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

## 🧠 Controllo predittivo (MPC) — **beta, in sviluppo attivo**

Sopra al rule engine reattivo, il progetto sta costruendo un cervello a **Model
Predictive Control**. Non è un termostato che **reagisce** quando la stanza è già
troppo calda: **guarda ore in avanti** e decide cosa fare *adesso* per mantenere
il comfort spendendo meno energia possibile. Oggi gira in **modalità advisory**
(prevede e consiglia, **non** comanda ancora gli AC) — scelta voluta: un sistema
24/7 che protegge il comfort va dimostrato prima di affidargli il controllo.

### Cosa fa
Ogni 15 minuti, per ogni stanza sensorizzata, simula le 6 ore successive e un
**arbitro** valuta le azioni candidate — `Off / Cool / Dry / Pre-raffrescamento`
— scegliendo per priorità **temperatura → umidità → costo** (tariffa elettrica
reale). Sa rispondere a: *"se non fai niente, tra quante ore questa stanza supera
la soglia di comfort, e quanto costerebbe riportarla a posto?"*

### Come funziona dentro
- **Modello termico grey-box RC** (fisica, *non* una rete neurale black-box): a
  2 conduttanze — stanza ↔ resto-casa e stanza ↔ esterno — con guadagni interni e
  previsione **Open-Meteo**.
- **Auto-calibrante**: impara i parametri di ogni stanza dagli *esperimenti
  naturali* — la deriva libera quando l'AC è spento di notte o la stanza è vuota
  — con un fit di traiettoria, senza taratura manuale.
- Modello **umidità** accoppiato (psicrometrico) e modello di **occupazione** che
  impara gli orari tipici di rientro (base per pre-raffrescare prima che torni a
  casa).
- Gira **interamente sul Raspberry Pi**, in locale, senza ML in cloud.

### Prove concrete che funziona (misurate su dati reali)
- Legge il presente alla perfezione: temperatura *attuale* prevista-vs-reale
  **MAE 0.02–0.04 °C**.
- Il modello termico prevede la **deriva libera (AC spento)** a **~0.15 °C a +1h,
  ~0.34 °C a +2h** (stanza col sensore a 0.1 °C) — **battendo la baseline ingenua
  "resta uguale"** a ogni orizzonte.
- Dopo l'auto-calibrazione il **bias della previsione a +6h** sulla stanza meglio
  campionata è **−0.28 °C** (sotto il grado), e la previsione a lungo termine
  combacia con la realtà vissuta (una stanza che senza AC, nei giorni caldi,
  arriva davvero a ~32 °C).

### In cosa è diverso dai termostati smart comuni
| Sistemi tipici | Questo MPC |
|---|---|
| **Reattivo** (agisce quando è già caldo) | **Predittivo** (agisce prima, orizzonte 2–6h) |
| ML black-box, affamato di dati, opaco | **Fisica grey-box**, interpretabile, parsimonioso |
| Dipendente dal cloud / vendor lock-in | **100% locale su Raspberry Pi** |
| Ottimizza comfort *oppure* energia | **Comfort *e* energia insieme**, tariffa reale |
| Parametri fissi | **Auto-calibrante** dalle derive naturali |

> ⚠️ **Beta**: l'MPC è solo advisory e in sviluppo attivo. I parametri si
> affinano man mano che si accumulano dati; non controlla (ancora) gli AC in
> autonomia.

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
