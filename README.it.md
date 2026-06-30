# 🌡️ Climate Automation

**🇮🇹 Italiano** · [🇬🇧 English](README.md)

Sistema di **domotica climatica self-hosted** che governa automaticamente i
condizionatori **Panasonic** in base a temperatura, umidità, stagione e
**presenza** delle persone in casa — con dashboard web in stile iOS e controllo
delle **luci IKEA**.

Pensato per girare 24/7 su un **Raspberry Pi**, senza cloud di terze parti oltre
a quelli dei produttori, senza app proprietarie.

```
Sensori IKEA / BME280 ─┐
Stato AC Panasonic ────┤
Previsione Open-Meteo ─┼─► Rule Engine ──► comanda gli AC (Cool/Heat/Dry…)
Presenza FRITZ!Box ────┤      │
                       │      └─► MPC advisor (predice + consiglia, advisory)
                       └──────────► Dashboard web (React)  ◄── energia / salute
Luci IKEA Dirigera ────────────────► on/off + dimmer
Relè caldaia Sonoff ───────────────► on/off locale (no cloud)
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
- **Punti luce fisici**: più lampadine che formano un unico punto luce (una
  specchiera, una fila in corridoio) vengono comandate insieme come un solo
  controllo, configurabile per stanza.

### Energia & costo
- **Consumo d'impianto** dall'aggregazione mensile del cloud Panasonic (il dato
  che combacia con l'app ufficiale), suddiviso **per giorno** nel mese corrente,
  con **costo stimato** dalla tariffa configurata (€/kWh variabile + IVA).
- Per stanza: **tempo AC, consumo e costo** del giorno, stimati dagli snapshot
  periodici di stato.

### Caldaia (opzionale, tutto locale)
- Un relè **Sonoff** a contatto pulito sulla caldaia viene rilevato e comandato
  **sulla rete locale** (protocollo eWeLink LAN, cifrato AES), **senza cloud**; lo
  stato è letto passivamente via mDNS. In dashboard compare come una sua stanza.

### Dashboard web
Interfaccia **React** responsive in stile *glassmorphism / iOS*, servita dallo
stesso backend e raggiungibile da tutta la rete locale.

- **Home** — gauge **comfort** di tutta la casa; card **meteo** estesa (temperatura,
  percepita, UV, vento, probabilità pioggia, andamento orario da Open-Meteo); card
  **Home Engine** che mostra la lettura live dell'MPC (casa stabile, comfort %,
  consumo previsto, prossima decisione, suggerimento); **energia clima** (oggi +
  mese, costo, grafico giornaliero); **stato impianti** (Home Engine / Panasonic /
  Dirigera / sensori / Wi-Fi); alert ed eventi recenti in linguaggio leggibile.
- **Pagine stanza** — termostato completo (modalità, setpoint con gauge della
  temperatura reale, ventola, swing, nanoe™X, Powerful/Quiet), grafico 24h
  temperatura + umidità, **ambiente** (temperatura, umidità, comfort, lux),
  **azioni rapide**, **stato dispositivi/impianti** per stanza, e in fondo tempo
  AC, consumo e costo del giorno.
- **Stanze senza AC** mostrano i **controlli luci** (toggle + dimmer), con i punti
  luce multipli raggruppati in un unico controllo.
- **Tema chiaro / scuro** (auto su alba/tramonto), responsive fino al mobile.

---

## 🧠 Controllo predittivo (MPC) — **beta, in sviluppo attivo**

Sopra al rule engine reattivo si sta costruendo uno strato a **Model Predictive
Control**. Invece di agire quando una stanza è già fuori comfort, a ogni passo
risolve un problema di controllo ottimo a orizzonte finito: prevede la traiettoria
termica delle ore successive e seleziona l'ingresso che mantiene il comfort al
minimo costo energetico. Oggi opera **ad anello aperto (advisory)** — prevede e
consiglia ma **non** comanda gli AC — scelta di sicurezza per un sistema 24/7.

### Modello termico — grey-box a parametri concentrati (RC)
Ogni stanza è un singolo nodo termico con due percorsi conduttivi, verso il resto
della casa climatizzata e verso l'esterno:

$$ C\,\frac{dT}{dt} \;=\; UA_{house}\,(T_{house}-T) \;+\; UA_{ext}\,(T_{out}-T) \;+\; Q_{int} \;+\; Q_{solar} \;+\; Q_{ac} $$

con capacità termica $C$ [J/°C], conduttanze $UA$ [W/°C], flussi termici interni/
solari/HVAC $Q$ [W], e costante di tempo $\tau = C/(UA_{house}+UA_{ext})$. $T_{out}$
è una previsione **Open-Meteo**; $T_{house}$ dai sensori delle altre stanze. Dai
dati ogni stanza si accoppia soprattutto al resto della casa (cinque superfici
interne contro una parete esterna), quindi $UA_{house}\approx 2\text{–}3\,UA_{ext}$;
confermato sulla risposta libera misurata (l'asintoto è vicino alla temperatura
interna di casa, non a quella esterna).

### Identificazione dei parametri — grey-box, auto-calibrante
Le conduttanze strutturali $UA$ sono fissate dalla geometria; il guadagno efficace
incerto $Q_{int}$ è identificato dagli **esperimenti naturali (risposta libera)** —
la deriva ad anello aperto registrata quando l'AC è spento (fascia notturna, stanza
vuota) — con un fit di traiettoria (output-error):

$$ \hat{Q}_{int} \;=\; \arg\min_{Q}\ \sum_{k}\big(\,\hat{T}(t_k;Q)-T^{meas}(t_k)\,\big)^2 $$

integrando il modello in avanti a passi di 5 min sui tratti ad AC spento. Nessuna
taratura manuale; la stima si affina con l'accumulo di dati. Un **modello umidità
psicrometrico** accoppiato e un **modello di occupazione** (stima orari di rientro)
alimentano lo stesso ottimizzatore.

### Formulazione del controllo
Orizzonte mobile $H = 6$ h, passo $\Delta t = 15$ min, ingressi candidati discreti
$u \in \{\text{Off, Cool, Dry, Pre-raffr.}\}$. Ogni candidato è simulato in avanti e
la scelta è **multi-obiettivo lessicografica**: (1) tenere $T$ nella banda di
comfort, (2) limitare l'umidità, (3) minimizzare il costo energetico
$\sum |Q_{ac}|/\mathrm{COP}\cdot\Delta t \times \text{tariffa}$. In beta emette
l'azione consigliata $u^\star$; l'attuazione ad anello chiuso resta dietro le regole
di sicurezza esistenti.

### Validazione (dati reali, hold-out)
- **Stima di stato (nowcast)** — temperatura *attuale* prevista-vs-misurata:
  **MAE 0.02–0.04 °C**.
- **Predizione ad anello aperto a $k$ passi** su finestre ad AC spento:
  **MAE ≈ 0.15 °C a $h=1$, ≈ 0.34 °C a $h=2$** (stanza con sensore a 0.1 °C),
  **sotto la baseline di persistenza** $\hat{T}(t{+}h)=T(t)$ a ogni orizzonte —
  cioè il modello porta informazione predittiva reale oltre il "resta uguale".
- **Bias della previsione a +6 h post-calibrazione = −0.28 °C** (sotto il grado,
  stanza ben campionata); la previsione a lungo termine è coerente con la risposta
  libera misurata (≈ 32 °C senza AC nei giorni caldi).
- *Limiti noti*: l'anello chiuso appiattisce l'eccitazione (poche derive ampie da
  cui identificare); un RC del primo ordine sotto-modella la risposta a due costanti
  di tempo (aria veloce / massa lenta); la quantizzazione a 1 °C limita
  l'identificabilità dove presente.

### Posizionamento rispetto ai termostati smart comuni
| Convenzionali | Questo MPC |
|---|---|
| Reattivo (feedback a comfort già violato) | Predittivo (orizzonte finito, 2–6 h) |
| ML black-box — affamato di dati, opaco | Grey-box di principi primi — interpretabile, parsimonioso |
| Cloud / vendor lock-in | Interamente on-device (Raspberry Pi), locale |
| Comfort *oppure* energia | Comfort + energia congiunti, tariffa reale |
| Parametri fissi | Auto-identificazione online dalle derive naturali |

> ⚠️ **Beta**: solo advisory e in sviluppo attivo; i parametri si affinano con
> l'accumulo di dati e il modello non comanda (ancora) gli AC in autonomia.

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
│   ├── rule_engine.py      # cervello reattivo: decide e comanda gli AC
│   ├── ac_controller.py    # wrapper async Panasonic Comfort Cloud
│   ├── sensor_poller.py    # lettura sensori IKEA (WebSocket + polling)
│   ├── remote_sensor_reader.py # sensori HTTP-pull (nodi BME280/BH1750)
│   ├── season.py           # algoritmo stagionale (media mobile T esterna)
│   ├── presence.py         # presenza casa/persona via FRITZ!Box
│   ├── occupancy_model.py  # stima orari di rientro / occupazione
│   ├── light_controller.py # luci IKEA (+ gruppi)
│   ├── boiler.py           # relè caldaia Sonoff in LAN (eWeLink)
│   ├── weather.py          # Open-Meteo: corrente + previsione
│   ├── energy_history.py   # energia mensile Panasonic → serie giornaliera
│   ├── scheduler.py        # spegnimento forzato notturno
│   ├── mpc_advisor.py      # arbitro MPC (advisory): predice + consiglia
│   ├── mpc_logger.py       # snapshot di stato periodici per l'identificazione
│   ├── thermal_model.py    # modello RC grey-box della stanza
│   ├── thermal_calibrator.py # auto-identificazione dai drift naturali
│   ├── humidity_model.py   # modello psicrometrico dell'umidità
│   └── psychro.py          # helper psicrometria
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
| `GET` | `/api/rooms/{room}/detail` | dati derivati per stanza (comfort, tempo/costo AC oggi, prossima azione) |
| `GET` | `/api/status` | connessioni, stagione, presenza |
| `GET` | `/api/overview` | dati derivati home: comfort, salute impianti, Wi-Fi, lettura Home Engine |
| `POST` | `/api/rooms/{room}/ac/control` | controllo diretto AC (modo/temp/ventola/swing/nanoe/eco) |
| `GET` | `/api/rooms/{room}/history` | storico letture sensore |
| `GET` | `/api/weather` | meteo esterno + previsione breve (Open-Meteo) |
| `GET` | `/api/energy/month` | consumo d'impianto giornaliero + costo del mese |
| `GET` | `/api/lights` | luci raggruppate per stanza |
| `POST` | `/api/lights/{id}` | on/off + dimmer di una luce/punto luce |
| `GET`·`POST` | `/api/boiler` | stato / accensione caldaia (Sonoff LAN) |
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
