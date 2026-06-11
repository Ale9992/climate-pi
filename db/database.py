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

-- Indici per le query temporali (history) e per stanza.
CREATE INDEX IF NOT EXISTS idx_readings_room_ts
    ON sensor_readings (room_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_ts
    ON automation_logs (timestamp);
CREATE INDEX IF NOT EXISTS idx_commands_ts
    ON ac_commands (timestamp);
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
        await self._conn.commit()

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

    # -- sensor_readings ----------------------------------------------------
    async def insert_sensor_reading(
        self,
        room_name: str,
        temperature: Optional[float],
        humidity: Optional[float],
        timestamp: Optional[str] = None,
    ) -> None:
        """Salva una lettura sensore."""
        await self._db.execute(
            "INSERT INTO sensor_readings (room_name, temperature, humidity, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (room_name, temperature, humidity, timestamp or _now_iso()),
        )
        await self._db.commit()

    async def get_recent_readings(
        self, room_name: str, hours: int = 24
    ) -> list[dict[str, Any]]:
        """Letture di una stanza nelle ultime N ore, ordinate dal piu' vecchio."""
        since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        cursor = await self._db.execute(
            "SELECT room_name, temperature, humidity, timestamp "
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
            "SELECT room_name, temperature, humidity, timestamp "
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
