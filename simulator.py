from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gate_client import Candle

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SIM_DB_PATH", str(BASE / "data" / "sim_trades.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_LOCK = threading.RLock()


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db() -> None:
    with _LOCK, _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sim_trades (
                id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                contract TEXT NOT NULL,
                interval TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                order_type TEXT NOT NULL,
                requested_entry REAL NOT NULL,
                fill_price REAL,
                stop REAL NOT NULL,
                target REAL NOT NULL,
                quantity REAL NOT NULL,
                notional REAL NOT NULL,
                leverage REAL NOT NULL,
                fee_rate REAL NOT NULL,
                slippage_rate REAL NOT NULL,
                account_balance REAL NOT NULL,
                risk_fraction REAL NOT NULL,
                opened_at INTEGER,
                closed_at INTEGER,
                exit_price REAL,
                exit_reason TEXT,
                gross_pnl REAL,
                net_pnl REAL,
                r_multiple REAL,
                max_favorable_excursion REAL NOT NULL DEFAULT 0,
                max_adverse_excursion REAL NOT NULL DEFAULT 0,
                last_mark REAL,
                last_checked_at INTEGER,
                max_holding_bars INTEGER NOT NULL DEFAULT 24,
                bars_held INTEGER NOT NULL DEFAULT 0,
                strategy_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    try:
        out["strategy"] = json.loads(out.pop("strategy_json") or "{}")
    except json.JSONDecodeError:
        out["strategy"] = {}
        out.pop("strategy_json", None)
    for k in ("created_at", "updated_at", "opened_at", "closed_at", "last_checked_at"):
        if out.get(k):
            out[k + "_iso"] = datetime.fromtimestamp(out[k], tz=timezone.utc).isoformat()
    return out


def create_trade(*, contract: str, interval: str, side: str, order_type: str,
                 requested_entry: float, market_price: float, stop: float, target: float,
                 account_balance: float, risk_fraction: float, leverage: float,
                 fee_rate: float, slippage_rate: float, max_holding_bars: int,
                 strategy: dict[str, Any] | None = None, notes: str = "") -> dict[str, Any]:
    init_db()
    side = side.upper()
    order_type = order_type.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("模拟交易方向必须为 LONG 或 SHORT")
    if order_type not in {"MARKET", "LIMIT"}:
        raise ValueError("订单类型必须为 MARKET 或 LIMIT")
    if min(requested_entry, market_price, stop, target, account_balance, leverage) <= 0:
        raise ValueError("价格、余额和杠杆必须大于0")
    if side == "LONG" and not (stop < requested_entry < target):
        raise ValueError("做多必须满足：止损 < 入场 < 止盈")
    if side == "SHORT" and not (target < requested_entry < stop):
        raise ValueError("做空必须满足：止盈 < 入场 < 止损")

    risk_budget = account_balance * risk_fraction
    stop_distance = abs(requested_entry - stop)
    quantity_by_risk = risk_budget / stop_distance
    max_quantity_by_margin = account_balance * leverage / requested_entry
    quantity = max(0.0, min(quantity_by_risk, max_quantity_by_margin))
    if quantity <= 0:
        raise ValueError("无法计算有效模拟仓位")
    notional = quantity * requested_entry

    trade_id = uuid.uuid4().hex[:12]
    now = _now()
    status = "OPEN" if order_type == "MARKET" else "PENDING"
    # Market simulation includes adverse slippage at entry.
    fill = None
    opened_at = None
    if status == "OPEN":
        fill = market_price * (1 + slippage_rate if side == "LONG" else 1 - slippage_rate)
        if side == "LONG" and not (stop < fill < target):
            raise ValueError("当前市价已经超出策略止损/止盈区间，请重新运行决策或改用触价入场")
        if side == "SHORT" and not (target < fill < stop):
            raise ValueError("当前市价已经超出策略止损/止盈区间，请重新运行决策或改用触价入场")
        opened_at = now

    with _LOCK, _conn() as conn:
        conn.execute(
            """INSERT INTO sim_trades (
            id,created_at,updated_at,contract,interval,side,status,order_type,
            requested_entry,fill_price,stop,target,quantity,notional,leverage,
            fee_rate,slippage_rate,account_balance,risk_fraction,opened_at,
            last_mark,last_checked_at,max_holding_bars,strategy_json,notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_id, now, now, contract.upper(), interval, side, status, order_type,
             requested_entry, fill, stop, target, quantity, notional, leverage,
             fee_rate, slippage_rate, account_balance, risk_fraction, opened_at,
             market_price, now, max_holding_bars, json.dumps(strategy or {}, ensure_ascii=False), notes),
        )
        conn.commit()
    return get_trade(trade_id)


def get_trade(trade_id: str) -> dict[str, Any]:
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM sim_trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise ValueError("找不到该模拟交易")
    return _row(row)


def list_trades(limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM sim_trades"
    params: list[Any] = []
    if status and status.upper() != "ALL":
        sql += " WHERE status=?"
        params.append(status.upper())
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row(r) for r in rows]


def _close_values(trade: dict[str, Any], exit_price: float, reason: str, closed_at: int) -> dict[str, Any]:
    fill = float(trade["fill_price"])
    qty = float(trade["quantity"])
    side_mult = 1.0 if trade["side"] == "LONG" else -1.0
    gross = (exit_price - fill) * qty * side_mult
    fees = (fill * qty + exit_price * qty) * float(trade["fee_rate"])
    net = gross - fees
    initial_risk = abs(fill - float(trade["stop"])) * qty
    r_multiple = net / initial_risk if initial_risk > 0 else 0.0
    return {
        "status": "CLOSED", "closed_at": closed_at, "exit_price": exit_price,
        "exit_reason": reason, "gross_pnl": gross, "net_pnl": net,
        "r_multiple": r_multiple,
    }


def evaluate_trade(trade_id: str, candles: list[Candle]) -> dict[str, Any]:
    trade = get_trade(trade_id)
    if trade["status"] in {"CLOSED", "CANCELLED"} or not candles:
        return trade

    side = trade["side"]
    status = trade["status"]
    fill = trade.get("fill_price")
    opened_at = trade.get("opened_at")
    stop = float(trade["stop"])
    target = float(trade["target"])
    requested = float(trade["requested_entry"])
    slip = float(trade["slippage_rate"])
    mfe = float(trade.get("max_favorable_excursion") or 0)
    mae = float(trade.get("max_adverse_excursion") or 0)
    bars_held = int(trade.get("bars_held") or 0)
    changes: dict[str, Any] = {}

    relevant = [c for c in candles if c.t > int(trade.get("last_checked_at") or trade["created_at"])]
    for c in relevant:
        if status == "PENDING":
            touched = c.l <= requested <= c.h
            if not touched:
                continue
            fill = requested * (1 + slip if side == "LONG" else 1 - slip)
            opened_at = c.t
            status = "OPEN"
            changes.update({"status": status, "fill_price": fill, "opened_at": opened_at})

        if status != "OPEN" or c.t < int(opened_at or 0):
            continue
        bars_held += 1
        assert fill is not None
        if side == "LONG":
            mfe = max(mfe, (c.h - fill) / fill)
            mae = max(mae, (fill - c.l) / fill)
            hit_stop, hit_target = c.l <= stop, c.h >= target
        else:
            mfe = max(mfe, (fill - c.l) / fill)
            mae = max(mae, (c.h - fill) / fill)
            hit_stop, hit_target = c.h >= stop, c.l <= target

        # With OHLC data the intrabar order is unknowable. Use conservative SL-first.
        if hit_stop:
            exit_price = stop * (1 - slip if side == "LONG" else 1 + slip)
            changes.update(_close_values({**trade, **changes, "fill_price": fill}, exit_price,
                                         "SL" if not hit_target else "SL_FIRST_AMBIGUOUS", c.t))
            status = "CLOSED"
            break
        if hit_target:
            exit_price = target * (1 - slip if side == "LONG" else 1 + slip)
            changes.update(_close_values({**trade, **changes, "fill_price": fill}, exit_price, "TP", c.t))
            status = "CLOSED"
            break
        if bars_held >= int(trade["max_holding_bars"]):
            exit_price = c.c * (1 - slip if side == "LONG" else 1 + slip)
            changes.update(_close_values({**trade, **changes, "fill_price": fill}, exit_price, "TIME", c.t))
            status = "CLOSED"
            break

    last = candles[-1].c
    changes.update({
        "updated_at": _now(), "last_mark": last, "last_checked_at": candles[-1].t,
        "max_favorable_excursion": mfe, "max_adverse_excursion": mae, "bars_held": bars_held,
    })
    fields = list(changes)
    with _LOCK, _conn() as conn:
        conn.execute(f"UPDATE sim_trades SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                     [changes[k] for k in fields] + [trade_id])
        conn.commit()
    return get_trade(trade_id)


def manual_close(trade_id: str, market_price: float) -> dict[str, Any]:
    trade = get_trade(trade_id)
    if trade["status"] == "PENDING":
        with _LOCK, _conn() as conn:
            conn.execute("UPDATE sim_trades SET status='CANCELLED',updated_at=?,closed_at=?,exit_reason='CANCELLED' WHERE id=?",
                         (_now(), _now(), trade_id))
            conn.commit()
        return get_trade(trade_id)
    if trade["status"] != "OPEN":
        return trade
    slip = float(trade["slippage_rate"])
    exit_price = market_price * (1 - slip if trade["side"] == "LONG" else 1 + slip)
    changes = _close_values(trade, exit_price, "MANUAL", _now())
    changes["updated_at"] = _now()
    with _LOCK, _conn() as conn:
        fields = list(changes)
        conn.execute(f"UPDATE sim_trades SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                     [changes[k] for k in fields] + [trade_id])
        conn.commit()
    return get_trade(trade_id)


def stats() -> dict[str, Any]:
    trades = [t for t in list_trades(500) if t["status"] == "CLOSED"]
    wins = [t for t in trades if float(t.get("net_pnl") or 0) > 0]
    losses = [t for t in trades if float(t.get("net_pnl") or 0) <= 0]
    gross_profit = sum(float(t.get("net_pnl") or 0) for t in wins)
    gross_loss = abs(sum(float(t.get("net_pnl") or 0) for t in losses))
    total = len(trades)
    return {
        "closed_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / total * 100, 2) if total else 0.0,
        "net_pnl": round(sum(float(t.get("net_pnl") or 0) for t in trades), 4),
        "average_r": round(sum(float(t.get("r_multiple") or 0) for t in trades) / total, 3) if total else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else (None if not gross_profit else 999.0),
        "open_trades": len([t for t in list_trades(500) if t["status"] in {"OPEN", "PENDING"}]),
        "storage": str(DB_PATH),
    }

init_db()
