"""
db/database.py — Layer dati SQLite asincrono.

Tre tabelle:
  - sensor_readings : storico letture temperatura/umidita' per stanza
  - automation_logs : decisioni del rule engine (regola scattata + valori)
  - ac_commands     : comandi inviati ai condizionatori + esito

Tutto async tramite aiosqlite. Una sola connessione condivisa, riusata da tutti
i moduli (sensor_poller, rule_engine, ac_controller, scheduler, api). Il WAL
mode permette letture concorrenti (dashboard/API) mentre l'engine scrive.

Uso tipico:
    db = Database("db/climate.db")
    await db.connect()
    await db.insert_sensor_reading("Camera", 25.3, 58.0)
    rows = await db.get_recent_readings("Camera", hours=24)
    await db.close()
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import aiosqlite


# Schema DDL. IF NOT EXISTS rende connect() idempotente.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sensor_readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_name   TEXT    NOT NULL,
    temperature REAL,
    humidity    REAL,
    pressure    REAL,
    lux         REAL,
    timestamp   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_logs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    room_name           TEXT    NOT NULL,
    rule_matched        TEXT,
    action_taken        TEXT,
    temp_at_trigger     REAL,
    humidity_at_trigger REAL,
    timestamp           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS ac_commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       TEXT    NOT NULL,
    command_type    TEXT    NOT NULL,
    parameters_json TEXT,
    success         INTEGER NOT NULL,
    error_message   TEXT,
    timestamp       TEXT    NOT NULL
);

-- mpc_samples: snapshot periodico (ogni ~5 min) di TUTTE le variabili che
-- servono a un modello predittivo/MPC. A differenza di sensor_readings (solo
-- T/umidita') qui ogni riga e' un vettore di stato completo allineato nel tempo:
-- T interna, T esterna, cosa stava facendo l'AC, presenza, energia. E' il
-- dataset di training del "cervello" che impara (modello termico + consumi).
CREATE TABLE IF NOT EXISTS mpc_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    room_name       TEXT    NOT NULL,
    temperature     REAL,   -- T interna IKEA (verita', dove c'e' il sensore)
    humidity        REAL,   -- umidita' IKEA
    ac_inside_temp  REAL,   -- sonda interna AC (fallback dove manca IKEA)
    outside_temp    REAL,   -- T esterna (da Panasonic, passi di 1°C)
    outside_temp_meteo REAL,-- T esterna da Open-Meteo (casa, 0.1°C) + base forecast
    ac_power        TEXT,   -- On/Off
    ac_mode         TEXT,   -- Cool/Heat/Dry/Fan/Auto
    ac_setpoint     REAL,   -- target_temperature impostato
    ac_fan          TEXT,   -- fan_speed
    presence_home   INTEGER,-- 1/0: casa abitata (globale)
    person_home     INTEGER,-- 1/0: persona legata alla stanza in casa
    energy_kwh      REAL,   -- consumo cumulativo giornaliero (kWh)
    energy_cooling  REAL,   -- cooling cumulativo giornaliero (kWh)
    timestamp       TEXT    NOT NULL
);

-- presence_log: storico della presenza PER PERSONA. Una riga ad ogni cambio di
-- stato casa<->fuori di un telefono. Da qui si ricostruiscono intervalli e orari
-- (quanto sta a casa/fuori ciascuno) -> quadro presenza + base per pre-condizionare.
CREATE TABLE IF NOT EXISTS presence_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    person    TEXT    NOT NULL,
    ip        TEXT,
    is_home   INTEGER NOT NULL,   -- 1 = entrato in casa, 0 = uscito
    timestamp TEXT    NOT NULL
);

-- mpc_advisory: consigli del controllo predittivo in modalita' advisory (non
-- comanda). Una riga per consiglio: previsione + tetto + costo per riportarla a
-- target. Serve a confrontare nel tempo previsione vs realta'.
CREATE TABLE IF NOT EXISTS mpc_advisory (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    room_name        TEXT NOT NULL,
    temp_now         REAL,
    temp_pred_end    REAL,   -- T prevista a fine orizzonte (AC spento)
    hours_to_ceiling REAL,   -- tra quante ore supera il tetto (NULL = mai nell'orizzonte)
    message          TEXT,
    timestamp        TEXT NOT NULL
);

-- panasonic_daily: consumo GIORNALIERO dal cloud Panasonic (aggregazione "Month",
-- quella che combacia con l'app). Lo storico ORARIO sotto-conta; questa e' la
-- fonte giusta. Consumo unico d'impianto (replicato sui device): una serie basta.
CREATE TABLE IF NOT EXISTS panasonic_daily (
    device_id   TEXT NOT NULL,
    day         TEXT NOT NULL,   -- 'YYYYMMDD'
    consumption REAL,            -- kWh del giorno
    cost        REAL,            -- costo cloud Panasonic (informativo)
    PRIMARY KEY (device_id, day)
);

-- Indici per le query temporali (history) e per stanza.
CREATE INDEX IF NOT EXISTS idx_advisory_room_ts
    ON mpc_advisory (room_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_presence_person_ts
    ON presence_log (person, timestamp);
CREATE INDEX IF NOT EXISTS idx_readings_room_ts
    ON sensor_readings (room_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_ts
    ON automation_logs (timestamp);
CREATE INDEX IF NOT EXISTS idx_commands_ts
    ON ac_commands (timestamp);
CREATE INDEX IF NOT EXISTS idx_mpc_room_ts
    ON mpc_samples (room_name, timestamp);
"""


def _now_iso() -> str:
    """Timestamp ISO 8601 locale, usato come default per ogni riga."""
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """Wrapper async su una singola connessione SQLite condivisa."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    # -- ciclo di vita ------------------------------------------------------
    async def connect(self) -> None:
        """Apre la connessione, abilita WAL e crea lo schema se assente."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        # Row factory -> dict-like access tramite sqlite3.Row.
        self._conn.row_factory = aiosqlite.Row
        # WAL: letture concorrenti (API/dashboard) durante le scritture engine.
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(_SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Migrazioni leggere: aggiunge colonne nuove a tabelle gia' esistenti
        (CREATE TABLE IF NOT EXISTS non altera una tabella gia' creata)."""
        cur = await self._conn.execute("PRAGMA table_info(sensor_readings)")
        reading_cols = {r[1] for r in await cur.fetchall()}
        if "pressure" not in reading_cols:
            await self._conn.execute(
                "ALTER TABLE sensor_readings ADD COLUMN pressure REAL")
        if "lux" not in reading_cols:
            await self._conn.execute(
                "ALTER TABLE sensor_readings ADD COLUMN lux REAL")

        cur = await self._conn.execute("PRAGMA table_info(mpc_samples)")
        cols = {r[1] for r in await cur.fetchall()}
        if "outside_temp_meteo" not in cols:
            await self._conn.execute(
                "ALTER TABLE mpc_samples ADD COLUMN outside_temp_meteo REAL")

    async def close(self) -> None:
        """Chiude la connessione (idempotente)."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database non connesso: chiama await db.connect() prima.")
        return self._conn

    # -- retention ----------------------------------------------------------
    async def prune_old(self, retention_days: Optional[dict[str, int]] = None
                        ) -> dict[str, int]:
        """Cancella le righe vecchie dalle tabelle ad alto volume / basso valore
        storico, per limitare la crescita del DB e l'usura della SD. Le tabelle
        'dati' per l'MPC (mpc_samples, *_history, presence_log) NON vengono
        toccate: sono il patrimonio di training del modello."""
        defaults = {
            "sensor_readings": 30,   # storico ad alta frequenza, ricreabile
            "automation_logs": 90,
            "ac_commands": 90,
            "mpc_advisory": 30,
        }
        ret = {**defaults, **(retention_days or {})}
        deleted: dict[str, int] = {}
        for table, days in ret.items():
            cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
            cur = await self._db.execute(
                f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
            deleted[table] = cur.rowcount
        await self._db.commit()
        # Checkpoint del WAL e recupero spazio.
        await self._db.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        return deleted

    # -- sensor_readings ----------------------------------------------------
    async def insert_sensor_reading(
        self,
        room_name: str,
        temperature: Optional[float],
        humidity: Optional[float],
        timestamp: Optional[str] = None,
        pressure: Optional[float] = None,
        lux: Optional[float] = None,
    ) -> None:
        """Salva una lettura sensore."""
        await self._db.execute(
            "INSERT INTO sensor_readings "
            "(room_name, temperature, humidity, pressure, lux, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (room_name, temperature, humidity, pressure, lux, timestamp or _now_iso()),
        )
        await self._db.commit()

    # -- presence_log -------------------------------------------------------
    async def insert_presence_event(
        self, person: str, ip: Optional[str], is_home: bool,
        timestamp: Optional[str] = None,
    ) -> None:
        """Registra un cambio di stato presenza di una persona (casa/fuori)."""
        await self._db.execute(
            "INSERT INTO presence_log (person, ip, is_home, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (person, ip, 1 if is_home else 0, timestamp or _now_iso()),
        )
        await self._db.commit()

    # -- mpc_advisory -------------------------------------------------------
    async def insert_mpc_advisory(
        self, room_name: str, temp_now: Optional[float],
        temp_pred_end: Optional[float], hours_to_ceiling: Optional[float],
        message: str, timestamp: Optional[str] = None,
    ) -> None:
        """Registra un consiglio del controllo predittivo (modalita' advisory)."""
        await self._db.execute(
            "INSERT INTO mpc_advisory (room_name, temp_now, temp_pred_end, "
            "hours_to_ceiling, message, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (room_name, temp_now, temp_pred_end, hours_to_ceiling, message,
             timestamp or _now_iso()),
        )
        await self._db.commit()

    async def get_latest_advisories(self, max_age_min: int = 60) -> list[dict[str, Any]]:
        """Ultimo consiglio MPC per ogni stanza (entro max_age_min minuti).

        Serve alla card 'Home Engine' della dashboard: prossima decisione +
        suggerimento, presi dal controllo predittivo gia' in funzione."""
        since = (datetime.now() - timedelta(minutes=max_age_min)).isoformat(timespec="seconds")
        cursor = await self._db.execute(
            "SELECT a.room_name, a.temp_now, a.temp_pred_end, a.hours_to_ceiling, "
            "a.message, a.timestamp FROM mpc_advisory a "
            "JOIN (SELECT room_name, MAX(timestamp) ts FROM mpc_advisory "
            "      WHERE timestamp >= ? GROUP BY room_name) m "
            "ON a.room_name = m.room_name AND a.timestamp = m.ts "
            "ORDER BY a.room_name",
            (since,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_ac_runtime_today(self, room_name: str, sample_minutes: int = 5) -> int:
        """Minuti stimati di AC acceso OGGI per una stanza, dai campioni periodici
        mpc_samples (snapshot ogni ~5 min). Stima = n. campioni con ac_power='On'
        moltiplicato per l'intervallo. Reale (non inventato), risoluzione ~5 min."""
        day = datetime.now().strftime("%Y-%m-%d")
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM mpc_samples WHERE room_name=? AND ac_power='On' "
            "AND substr(timestamp,1,10)=?", (room_name, day))
        row = await cursor.fetchone()
        n = row[0] if row else 0
        return int(n * sample_minutes)

    async def get_presence_events(self, person: str) -> list[tuple]:
        """Transizioni di presenza (timestamp, is_home) di una persona, ordinate."""
        cursor = await self._db.execute(
            "SELECT timestamp, is_home FROM presence_log WHERE person = ? "
            "ORDER BY timestamp", (person,))
        rows = await cursor.fetchall()
        return [(r[0], r[1]) for r in rows]

    async def get_recent_readings(
        self, room_name: str, hours: int = 24
    ) -> list[dict[str, Any]]:
        """Letture di una stanza nelle ultime N ore, ordinate dal piu' vecchio."""
        since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        cursor = await self._db.execute(
            "SELECT room_name, temperature, humidity, pressure, lux, timestamp "
            "FROM sensor_readings "
            "WHERE room_name = ? AND timestamp >= ? "
            "ORDER BY timestamp ASC",
            (room_name, since),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_latest_reading(self, room_name: str) -> Optional[dict[str, Any]]:
        """Ultima lettura nota per una stanza (None se assente)."""
        cursor = await self._db.execute(
            "SELECT room_name, temperature, humidity, pressure, lux, timestamp "
            "FROM sensor_readings WHERE room_name = ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (room_name,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    # -- automation_logs ----------------------------------------------------
    async def insert_automation_log(
        self,
        room_name: str,
        rule_matched: Optional[str],
        action_taken: Optional[str],
        temp_at_trigger: Optional[float],
        humidity_at_trigger: Optional[float],
        timestamp: Optional[str] = None,
    ) -> None:
        """Registra una decisione del rule engine."""
        await self._db.execute(
            "INSERT INTO automation_logs "
            "(room_name, rule_matched, action_taken, temp_at_trigger, "
            " humidity_at_trigger, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (
                room_name,
                rule_matched,
                action_taken,
                temp_at_trigger,
                humidity_at_trigger,
                timestamp or _now_iso(),
            ),
        )
        await self._db.commit()

    async def get_recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Ultimi N log di automazione (piu' recenti per primi)."""
        cursor = await self._db.execute(
            "SELECT id, room_name, rule_matched, action_taken, temp_at_trigger, "
            "humidity_at_trigger, timestamp FROM automation_logs "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # -- ac_commands --------------------------------------------------------
    async def insert_ac_command(
        self,
        device_id: str,
        command_type: str,
        parameters: Optional[dict[str, Any]],
        success: bool,
        error_message: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        """Registra l'invio (riuscito o meno) di un comando a un condizionatore."""
        await self._db.execute(
            "INSERT INTO ac_commands "
            "(device_id, command_type, parameters_json, success, error_message, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                device_id,
                command_type,
                json.dumps(parameters) if parameters is not None else None,
                1 if success else 0,
                error_message,
                timestamp or _now_iso(),
            ),
        )
        await self._db.commit()

    # -- mpc_samples --------------------------------------------------------
    async def insert_mpc_sample(self, sample: dict[str, Any]) -> None:
        """
        Salva uno snapshot completo per il dataset MPC. `sample` e' un dict con
        le chiavi della tabella mpc_samples; le mancanti diventano NULL. I valori
        booleani (presence_home/person_home) vengono normalizzati a 0/1/NULL.
        """
        def _b(v: Any) -> Optional[int]:
            return None if v is None else (1 if v else 0)

        await self._db.execute(
            "INSERT INTO mpc_samples "
            "(room_name, temperature, humidity, ac_inside_temp, outside_temp, "
            " outside_temp_meteo, ac_power, ac_mode, ac_setpoint, ac_fan, "
            " presence_home, person_home, energy_kwh, energy_cooling, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sample.get("room_name"),
                sample.get("temperature"),
                sample.get("humidity"),
                sample.get("ac_inside_temp"),
                sample.get("outside_temp"),
                sample.get("outside_temp_meteo"),
                sample.get("ac_power"),
                sample.get("ac_mode"),
                sample.get("ac_setpoint"),
                sample.get("ac_fan"),
                _b(sample.get("presence_home")),
                _b(sample.get("person_home")),
                sample.get("energy_kwh"),
                sample.get("energy_cooling"),
                sample.get("timestamp") or _now_iso(),
            ),
        )
        await self._db.commit()

    async def get_recent_mpc_samples(
        self, room_name: Optional[str] = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Ultimi N snapshot MPC (piu' recenti per primi); filtrabili per stanza."""
        if room_name:
            cursor = await self._db.execute(
                "SELECT * FROM mpc_samples WHERE room_name = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (room_name, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM mpc_samples ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_recent_commands(self, limit: int = 100) -> list[dict[str, Any]]:
        """Ultimi N comandi AC (piu' recenti per primi)."""
        cursor = await self._db.execute(
            "SELECT id, device_id, command_type, parameters_json, success, "
            "error_message, timestamp FROM ac_commands "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            # Deserializza i parametri per comodita' del chiamante.
            d["parameters"] = json.loads(d["parameters_json"]) if d["parameters_json"] else None
            d["success"] = bool(d["success"])
            result.append(d)
        return result

    # -- storico energia Panasonic (panasonic_history) ----------------------
    async def get_month_energy(self) -> dict[str, Any]:
        """Energia GIORNALIERA del mese corrente dall'aggregazione MENSILE Panasonic
        (panasonic_daily) — combacia con l'app (lo storico orario sotto-conta).
        Consumo unico d'impianto: per ogni giorno MAX tra i device. Ritorna
        {days:[{day,kwh}], today_kwh, month_kwh}."""
        month = datetime.now().strftime("%Y%m")
        today = datetime.now().strftime("%Y%m%d")
        cursor = await self._db.execute(
            "SELECT day, MAX(consumption) AS kwh FROM panasonic_daily "
            "WHERE substr(day,1,6)=? AND consumption IS NOT NULL "
            "GROUP BY day ORDER BY day",
            (month,),
        )
        rows = await cursor.fetchall()
        days = [{"day": r["day"], "kwh": round(float(r["kwh"] or 0), 2)} for r in rows]
        today_kwh = next((d["kwh"] for d in days if d["day"] == today), 0.0)
        month_kwh = round(sum(d["kwh"] for d in days), 2)
        return {"days": days, "today_kwh": today_kwh, "month_kwh": month_kwh}

    async def upsert_panasonic_daily(self, rows: list[tuple]) -> int:
        """Upsert consumo GIORNALIERO Panasonic (PK device_id+day; oggi cambia)."""
        if not rows:
            return 0
        cur = await self._db.executemany(
            "INSERT OR REPLACE INTO panasonic_daily "
            "(device_id, day, consumption, cost) VALUES (?, ?, ?, ?)", rows)
        await self._db.commit()
        return cur.rowcount

    async def upsert_panasonic_history(self, rows: list[tuple]) -> int:
        """Inserisce/aggiorna lo storico orario Panasonic (PK device_id+data_time;
        oggi cambia durante il giorno -> REPLACE per aggiornarlo)."""
        if not rows:
            return 0
        cur = await self._db.executemany(
            "INSERT OR REPLACE INTO panasonic_history "
            "(device_id, data_time, timestamp, setting_temp, inside_temp, "
            " outside_temp, consumption, cost, cool_rate, heat_rate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        await self._db.commit()
        return cur.rowcount
