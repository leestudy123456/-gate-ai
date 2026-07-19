from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "gate_ai_quant.db"


def initialize() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                contract TEXT NOT NULL,
                interval TEXT NOT NULL,
                side TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )


def save_signal(contract: str, interval: str, side: str, confidence: int, payload: dict) -> None:
    initialize()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO signal_log
            (created_at, contract, interval, side, confidence, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                contract,
                interval,
                side,
                confidence,
                json.dumps(payload, ensure_ascii=False),
            ),
        )


def recent_signals(limit: int = 50) -> list[dict]:
    initialize()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT created_at, contract, interval, side, confidence, payload_json
            FROM signal_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        {
            "created_at": row[0],
            "contract": row[1],
            "interval": row[2],
            "side": row[3],
            "confidence": row[4],
            "payload": json.loads(row[5]),
        }
        for row in rows
    ]
