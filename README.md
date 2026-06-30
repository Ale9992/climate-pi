# 🌡️ Climate Automation

[🇮🇹 Italiano](README.it.md) · **🇬🇧 English**

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-ready-C51A4A?logo=raspberrypi&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

> **Self-hosted smart home for Panasonic air conditioners.** No vendor cloud
> dashboards, no subscriptions — control your ACs by temperature, humidity,
> season and presence, plus IKEA lights, all from one Raspberry Pi.

A **self-hosted home climate automation** system that automatically controls
**Panasonic** air conditioners based on temperature, humidity, season and
**presence**, with an iOS-style web dashboard and **IKEA light** control.

Designed to run 24/7 on a **Raspberry Pi**, with no third-party cloud beyond the
manufacturers' own, and no proprietary apps.

```
IKEA / BME280 sensors ─┐
Panasonic AC state ────┤
Open-Meteo forecast ───┼─► Rule Engine ──► commands the ACs (Cool/Heat/Dry…)
FRITZ!Box presence ────┤      │
                       │      └─► MPC advisor (predict + recommend, advisory)
                       └──────────► Web dashboard (React)  ◄── energy / health
IKEA Dirigera lights ──────────────► on/off + dimmer
Sonoff boiler relay ───────────────► local on/off (no cloud)
```

> ⚠️ Personal project, published for educational purposes. It depends on specific
> hardware (see *Requirements*). Not a commercial product.

---

## ✨ Features

### Smart climate automation
- **Comfort-band model**: keeps the temperature within a band around a target,
  switching the AC on/off with **hysteresis** (no rapid cycling). Thresholds
  calibrated on real consumption history.
- **Season awareness**: the season is decided by the **moving average of the
  outdoor temperature** (read from the ACs), so heating never kicks in during
  summer and vice versa — with a safety override for extreme conditions.
- **Automatic dehumidification**: switches to Dry mode when humidity rises above
  a threshold (low power, better comfort).
- **Forced off + night window**: ACs stay off during a configurable time window
  (e.g. 03:00–08:00), even when it's hot.

### Presence (FRITZ!Box, no app on the phone)
- **Empty home → everything off**: detects whether smartphones are connected to
  WiFi (via TR-064); after a *grace period* with nobody home, it turns the ACs
  off.
- **Per-person presence**: a room can follow **a specific phone** (e.g. the
  bedroom follows only your iPhone).
- **Fail-safe**: if the FRITZ!Box doesn't respond, it assumes "home occupied" —
  a network error never takes comfort away.

### Living with the real world
- **Remote-control aware**: if you turn the AC on/off via the remote or the
  Panasonic app, the system notices and **respects your choice** (it doesn't
  "fight" you).
- **Recovery after a blackout**: on restart it reads the real AC state and
  resumes consistently; the Pi powers back on by itself when power returns.

### IKEA lights
- **On/off + dimmer** control of Dirigera lights, grouped by room.
- **Physical fixtures**: multiple bulbs forming a single light point (a mirror
  light, a hallway run) are controlled together as one control, configurable per
  room.

### Energy & cost
- **Whole-plant consumption** from the Panasonic cloud's monthly aggregation (the
  figure that matches the official app), broken down **per day** for the current
  month, with **estimated cost** from your configured tariff (variable €/kWh + VAT).
- Per-room **AC runtime, consumption and cost** for the day, estimated from the
  periodic state snapshots.

### Boiler (optional, fully local)
- A **Sonoff** dry-contact relay on the boiler is detected and controlled **on the
  local network** (eWeLink LAN protocol, AES-encrypted), with **no cloud**; its
  state is read passively via mDNS. Surfaced in the dashboard as its own room.

### Web dashboard
A responsive **React** interface in *glassmorphism / iOS* style, served by the
same backend process and reachable from the whole local network.

- **Home overview** — a whole-house **comfort gauge**; an extended outdoor
  **weather** card (temperature, feels-like, UV, wind, rain probability, hourly
  trend, all from Open-Meteo); a **Home Engine** card that surfaces the MPC's live
  read (house stability, comfort %, projected consumption, next decision,
  suggestion); **climate energy** (today + month, estimated cost, daily chart);
  **plant health** (Home Engine / Panasonic / Dirigera / sensors / Wi-Fi);
  alerts and a recent-events feed (human-readable, e.g. *"Cooling to 22°"*).
- **Per-room pages** — full thermostat (mode, setpoint with a live real-temperature
  gauge, fan, swing, nanoe™X, Powerful/Quiet), a 24-hour temperature + humidity
  chart, room **environment** (temperature, humidity, comfort, lux), **quick
  actions**, per-room **device & system health**, and a footer with the day's AC
  runtime, consumption and cost.
- **Light-only rooms** (no AC) show their **light controls** instead (toggle +
  dimmer), with multiple bulbs grouped into a single physical fixture where it
  makes sense (e.g. a bathroom mirror light, a hallway run).
- **Light / dark theme** (auto by sunrise/sunset), top-bar quick stats, responsive
  down to mobile.

---

## 🧠 Predictive control (MPC) — **beta, in active development**

A **Model Predictive Control** layer is being built on top of the reactive rule
engine. Instead of acting once a room is already out of comfort, at each step it
solves a finite-horizon optimal-control problem: predict the thermal trajectory
over the next hours and select the input that maintains comfort at minimum energy
cost. It currently runs **open-loop (advisory)** — it predicts and recommends but
does **not** actuate the ACs — a deliberate safety choice for a 24/7 system.

### Thermal model — grey-box lumped-parameter (RC)
Each room is modelled as a single thermal node with two conductive paths, toward
the rest of the conditioned house and toward the outdoors:

$$ C\,\frac{dT}{dt} \;=\; UA_{house}\,(T_{house}-T) \;+\; UA_{ext}\,(T_{out}-T) \;+\; Q_{int} \;+\; Q_{solar} \;+\; Q_{ac} $$

with thermal capacitance $C$ [J/°C], conductances $UA$ [W/°C], internal/solar/HVAC
heat flows $Q$ [W], and time constant $\tau = C/(UA_{house}+UA_{ext})$. $T_{out}$
is an **Open-Meteo** forecast; $T_{house}$ is taken from the other rooms' sensors.
Empirically each room couples mostly to the rest of the house (five interior
surfaces vs. one external wall), so $UA_{house}\approx 2\text{–}3\,UA_{ext}$; this
was confirmed against measured free-response (the asymptote sits near the indoor
house temperature, not the outdoor one).

### Parameter identification — grey-box, self-calibrating
Structural conductances $UA$ are fixed from building geometry; the uncertain
effective gain $Q_{int}$ is identified from **free-response ("natural")
experiments** — the open-loop drift recorded whenever the AC is off (night
setback, empty room) — via an output-error trajectory fit:

$$ \hat{Q}_{int} \;=\; \arg\min_{Q}\ \sum_{k}\big(\,\hat{T}(t_k;Q)-T^{meas}(t_k)\,\big)^2 $$

integrating the model forward at 5-min steps over the AC-off segments. No manual
tuning; the estimate is refined as data accumulates. A coupled **psychrometric
humidity model** and an **occupancy model** (arrival-time estimation) feed the
same optimiser.

### Control formulation
Receding horizon $H = 6$ h, step $\Delta t = 15$ min, discrete candidate inputs
$u \in \{\text{Off, Cool, Dry, Pre-cool}\}$. Each candidate is simulated forward
and selection is **lexicographic multi-objective**: (1) keep $T$ inside the
comfort band, (2) bound humidity, (3) minimise energy cost
$\sum |Q_{ac}|/\mathrm{COP}\cdot\Delta t \times \text{tariff}$. In beta the
optimiser emits the recommended $u^\star$ as advice; closed-loop actuation stays
gated behind the existing safety rules.

### Validation (real, held-out data)
- **State estimate (nowcast)** — predicted vs. measured *current* temperature:
  **MAE 0.02–0.04 °C**.
- **Open-loop $k$-step prediction** on AC-off windows: **MAE ≈ 0.15 °C at $h=1$,
  ≈ 0.34 °C at $h=2$** (0.1 °C-resolution room), **below the persistence baseline**
  $\hat{T}(t{+}h)=T(t)$ at every horizon — i.e. the model carries genuine
  predictive information beyond "it stays the same".
- **Post-calibration +6 h forecast bias = −0.28 °C** (sub-degree, well-sampled
  room); the long-range prediction is consistent with the room's measured
  free-running behaviour (≈ 32 °C without AC on hot days).
- *Known limitations*: closed-loop operation flattens excitation (few large
  drifts to identify from); a first-order RC under-models the fast-air / slow-mass
  two-time-constant response; 1 °C sensor quantisation caps identifiability where
  present.

### Positioning vs. conventional smart-thermostats
| Conventional | This MPC |
|---|---|
| Reactive (feedback once out of band) | Predictive (finite-horizon, 2–6 h) |
| Black-box ML — data-hungry, opaque | Grey-box first-principles — interpretable, data-efficient |
| Cloud / vendor lock-in | Fully on-device (Raspberry Pi), local |
| Comfort *or* energy | Joint comfort + energy, tariff-aware |
| Fixed parameters | Online self-identification from natural drifts |

> ⚠️ **Beta**: advisory-only and under active development; parameters are refined
> as data accumulates and the model does not (yet) actuate the ACs autonomously.

---

## 🧰 Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+ (asyncio), FastAPI + Uvicorn |
| Storage | SQLite (async, `aiosqlite`) |
| Scheduling | APScheduler |
| Integrations | `dirigera` (IKEA), `aio-panasonic-comfort-cloud`, `fritzconnection` |
| Frontend | React 18 + Vite + Tailwind CSS v4, Material Design Icons |
| Deploy | systemd on Raspberry Pi OS / Debian |

---

## 🔌 Hardware requirements

- **IKEA DIRIGERA hub** (VINDSTYRKA environment sensors, lights) — local API
- **Panasonic ACs** compatible with **Comfort Cloud** (e.g. CS-TZ series)
- **FRITZ!Box** (for presence detection via TR-064) — optional
- A host running 24/7: **Raspberry Pi 3** or newer (tested), or any Linux/macOS
  machine for development

> Without all components the system still works in reduced mode (e.g. no
> FRITZ!Box → presence disabled, no IKEA lights → the lights card doesn't appear).

---

## 🚀 Installation

### 1. Configuration

Credentials are **not** in the repository. Copy the template and fill in your data:

```bash
cp config/config.example.yaml config/config.yaml
```

Then edit `config/config.yaml`:
- **Dirigera token** — generated automatically by the mapping tool (step 2)
- **Panasonic Comfort Cloud email/password**
- **FRITZ!Box credentials** — create a dedicated user in *System → Users*
- **Device IDs** (ACs, IKEA sensors) — populated by the mapping tool

`config/config.yaml` is in `.gitignore`: secrets never end up on Git.

### 2. Hardware mapping

The interactive tool discovers your devices and populates the config:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tools/mapping_tool.py
```

(For the first authentication to the Dirigera hub you'll need to press its
physical button.)

### 3. Build dashboard + run

```bash
# build the dashboard (requires Node.js)
cd dashboard && npm install && npm run build && cd ..

# run
.venv/bin/python main.py          # production
DEV=1 .venv/bin/python main.py    # verbose logs
```

Dashboard and API at **http://localhost:8000**.

### 4. Deploy on Raspberry Pi (24/7)

The `setup.sh` script creates the venv, builds the dashboard (if Node is present)
and installs the systemd service:

```bash
./setup.sh
```

Useful commands:
```bash
sudo systemctl status climate-automation     # status
journalctl -u climate-automation -f          # live logs
sudo systemctl restart climate-automation    # restart
```

The service is `enabled`: it restarts itself on every boot, crash or blackout.

> **Headless Pi note**: the React build can exhaust RAM on a Pi 3. It's best to
> build `dashboard/dist/` on another machine and copy it, avoiding `npm` on the Pi.

---

## ⚙️ Main configuration (`config.yaml`)

```yaml
rooms:
  - name: "Bedroom"
    ikea_sensor_id: "<SENSOR_ID>"        # IKEA environment sensor (optional)
    panasonic_device_id: "<DEVICE_ID>"   # air conditioner
    presence_device_ip: "192.168.1.50"   # opt: this room follows this phone
    comfort:
      summer: { target_temp: 25, deadband: 1.5, setpoint: 25 }
      winter: { target_temp: 21.5, deadband: 1.0, setpoint: 21 }

schedule:
  force_off_time: "03:00"   # start of the night window
  night_off_end: "08:00"    # end of window: ACs off 03:00–08:00

presence:
  enabled: true
  fritzbox: { address, user, password }
  away_grace_minutes: 30
  devices: [ { name, ip, mac } ]

lights:
  ceiling_rooms: ["Living room", "Bedroom"]  # bulbs controlled as one fixture
```

The full, commented template is in [`config/config.example.yaml`](config/config.example.yaml).

---

## 🗂️ Project structure

```
climate-automation/
├── main.py                 # entry point: asyncio orchestration + uvicorn
├── core/
│   ├── config.py           # typed config loading
│   ├── rule_engine.py      # the reactive brain: decides and commands the ACs
│   ├── ac_controller.py    # async wrapper over Panasonic Comfort Cloud
│   ├── sensor_poller.py    # IKEA sensor reading (WebSocket + polling)
│   ├── remote_sensor_reader.py # HTTP-pull sensors (BME280/BH1750 nodes)
│   ├── season.py           # season algorithm (outdoor temp moving average)
│   ├── presence.py         # home/person presence via FRITZ!Box
│   ├── occupancy_model.py  # arrival-time / occupancy estimation
│   ├── light_controller.py # IKEA lights (+ grouped fixtures)
│   ├── boiler.py           # Sonoff boiler relay over the LAN (eWeLink)
│   ├── weather.py          # Open-Meteo current + forecast
│   ├── energy_history.py   # Panasonic monthly energy → daily series
│   ├── scheduler.py        # nightly forced off
│   ├── mpc_advisor.py      # MPC arbiter (advisory): predict + recommend
│   ├── mpc_logger.py       # periodic state snapshots for identification
│   ├── thermal_model.py    # grey-box RC room model
│   ├── thermal_calibrator.py # self-identification from natural drifts
│   ├── humidity_model.py   # psychrometric humidity model
│   └── psychro.py          # psychrometrics helpers
├── api/                    # FastAPI: routes + models
├── db/                     # async SQLite (history, logs, commands)
├── dashboard/              # React + Vite + Tailwind frontend
├── tools/mapping_tool.py   # hardware discovery + config generation
├── setup.sh                # install + systemd service
└── docs/                   # analysis and technical notes
```

---

## 🌐 REST API (excerpt)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/rooms` | state of all rooms (temp, AC, energy, override) |
| `GET` | `/api/rooms/{room}/detail` | per-room derived data (comfort, AC runtime/cost today, next action) |
| `GET` | `/api/status` | connections, season, presence |
| `GET` | `/api/overview` | derived home data: comfort score, plant health, Wi-Fi, Home Engine read |
| `POST` | `/api/rooms/{room}/ac/control` | direct AC control (mode/temp/fan/swing/nanoe/eco) |
| `GET` | `/api/rooms/{room}/history` | sensor reading history |
| `GET` | `/api/weather` | outdoor weather + short forecast (Open-Meteo) |
| `GET` | `/api/energy/month` | per-day plant consumption + cost for the month |
| `GET` | `/api/lights` | lights grouped by room |
| `POST` | `/api/lights/{id}` | on/off + dimmer of a light/fixture |
| `GET`·`POST` | `/api/boiler` | boiler state / on-off (Sonoff LAN) |
| `GET` | `/api/logs` | automation decision logs |

---

## 🔐 Privacy & security

- All credentials live **only** in `config/config.yaml`, which is **gitignored**.
- No data leaves the local network, except the manufacturers' official APIs
  (Panasonic Comfort Cloud).
- The dashboard has **no authentication**: expose it on the LAN only, **never**
  directly on the Internet (use a VPN for remote access).

---

## 📄 License

MIT — see [`LICENSE`](LICENSE).

---

<sub>Built with care for a home that runs itself. 🏠</sub>
