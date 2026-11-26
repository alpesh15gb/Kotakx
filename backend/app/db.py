from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from app.models import (
    AlertRecord,
    KillSwitchState,
    OrderDefaults,
    PnlProtectConfig,
)


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS watchlists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    tokens_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id TEXT PRIMARY KEY,
                    instrument_token TEXT NOT NULL,
                    trading_symbol TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    triggered_at TEXT
                );

                CREATE TABLE IF NOT EXISTS candles (
                    instrument_token TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL DEFAULT 0,
                    synthetic INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (instrument_token, timeframe, time)
                );

                CREATE TABLE IF NOT EXISTS order_idempotency (
                    key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER
                );
                """
            )
            candle_columns = {row["name"] for row in conn.execute("PRAGMA table_info(candles)")}
            if "synthetic" not in candle_columns:
                conn.execute("ALTER TABLE candles ADD COLUMN synthetic INTEGER NOT NULL DEFAULT 0")
                conn.execute("UPDATE candles SET synthetic = 1")
            # Seed default watchlist if empty
            row = conn.execute("SELECT COUNT(*) AS c FROM watchlists").fetchone()
            if row and row["c"] == 0:
                conn.execute(
                    "INSERT INTO watchlists (id, name, tokens_json) VALUES (?, ?, ?)",
                    (
                        "default",
                        "My Scalper Watchlist",
                        json.dumps([
                            "NIFTY 50",
                            "NIFTY BANK",
                            "2885",
                            "1333",
                            "11536",
                            "1594",
                        ]),
                    ),
                )

    def get_json(self, key: str, default: Any = None) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
            if not row:
                return default
            return json.loads(row["value"])

    def set_json(self, key: str, value: Any) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO kv_store (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def begin_order_request(self, key: str, request_hash: str) -> dict[str, Any]:
        now = int(time.time())
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO order_idempotency (key, request_hash, status, created_at) VALUES (?, ?, 'processing', ?)",
                (key, request_hash, now),
            )
            if cur.rowcount == 1:
                return {"status": "claimed"}
            row = conn.execute("SELECT * FROM order_idempotency WHERE key = ?", (key,)).fetchone()
        if not row:
            return {"status": "claimed"}
        if row["request_hash"] != request_hash:
            return {"status": "conflict"}
        if row["status"] == "completed" and row["response_json"]:
            return {"status": "completed", "response": json.loads(row["response_json"])}
        return {"status": "in_progress"}

    def complete_order_request(self, key: str, response: dict[str, Any]) -> None:
        now = int(time.time())
        with self._conn() as conn:
            conn.execute(
                "UPDATE order_idempotency SET status = 'completed', response_json = ?, completed_at = ? WHERE key = ? AND status = 'processing'",
                (json.dumps(response), now, key),
            )

    def abort_order_request(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM order_idempotency WHERE key = ? AND status = 'processing'", (key,))

    def get_order_defaults(self) -> OrderDefaults:
        raw = self.get_json("order_defaults")
        return OrderDefaults(**raw) if raw else OrderDefaults()

    def set_order_defaults(self, defaults: OrderDefaults) -> OrderDefaults:
        self.set_json("order_defaults", defaults.model_dump())
        return defaults

    def get_kill_switch(self) -> KillSwitchState:
        raw = self.get_json("kill_switch")
        return KillSwitchState(**raw) if raw else KillSwitchState()

    def set_kill_switch(self, state: KillSwitchState) -> KillSwitchState:
        self.set_json("kill_switch", state.model_dump())
        return state

    def get_pnl_protect(self) -> PnlProtectConfig:
        raw = self.get_json("pnl_protect")
        return PnlProtectConfig(**raw) if raw else PnlProtectConfig()

    def set_pnl_protect(self, cfg: PnlProtectConfig) -> PnlProtectConfig:
        self.set_json("pnl_protect", cfg.model_dump())
        return cfg

    def list_watchlists(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id, name, tokens_json FROM watchlists").fetchall()
            return [
                {"id": r["id"], "name": r["name"], "tokens": json.loads(r["tokens_json"])}
                for r in rows
            ]

    def upsert_watchlist(self, wl_id: str, name: str, tokens: list[str]) -> dict[str, Any]:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO watchlists (id, name, tokens_json) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name, tokens_json = excluded.tokens_json
                """,
                (wl_id, name, json.dumps(tokens)),
            )
        return {"id": wl_id, "name": name, "tokens": tokens}

    def list_alerts(self, include_deleted: bool = False) -> list[AlertRecord]:
        with self._conn() as conn:
            sql = "SELECT * FROM alerts" if include_deleted else "SELECT * FROM alerts WHERE status != 'DELETED'"
            rows = conn.execute(sql + " ORDER BY created_at DESC").fetchall()
            return [AlertRecord(**dict(r)) for r in rows]

    def save_alert(self, alert: AlertRecord) -> AlertRecord:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO alerts (id, instrument_token, trading_symbol, condition, target_price, note, status, created_at, triggered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    triggered_at = excluded.triggered_at,
                    target_price = excluded.target_price,
                    condition = excluded.condition,
                    note = excluded.note
                """,
                (
                    alert.id,
                    alert.instrument_token,
                    alert.trading_symbol,
                    alert.condition,
                    alert.target_price,
                    alert.note,
                    alert.status,
                    alert.created_at,
                    alert.triggered_at,
                ),
            )
        return alert

    def delete_alert(self, alert_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("UPDATE alerts SET status = 'DELETED' WHERE id = ?", (alert_id,))
            return cur.rowcount > 0

    def upsert_candles(
        self,
        instrument_token: str,
        timeframe: str,
        candles: list[dict[str, Any]],
        synthetic: bool = False,
    ) -> None:
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO candles (instrument_token, timeframe, time, open, high, low, close, volume, synthetic)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_token, timeframe, time) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    synthetic = excluded.synthetic
                """,
                [
                    (
                        instrument_token,
                        timeframe,
                        int(c["time"]),
                        float(c["open"]),
                        float(c["high"]),
                        float(c["low"]),
                        float(c["close"]),
                        int(c.get("volume", 0)),
                        int(synthetic),
                    )
                    for c in candles
                ],
            )

    def get_candles(
        self,
        instrument_token: str,
        timeframe: str,
        limit: int = 300,
        synthetic: bool | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT time, open, high, low, close, volume
            FROM candles
            WHERE instrument_token = ? AND timeframe = ?
        """
        params: list[Any] = [instrument_token, timeframe]
        if synthetic is not None:
            sql += " AND synthetic = ?"
            params.append(int(synthetic))
        sql += " ORDER BY time DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            out = [dict(r) for r in rows]
            out.reverse()
            return out
