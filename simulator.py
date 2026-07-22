from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import math

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
                exit_mode TEXT NOT NULL DEFAULT 'SMART',
                grace_bars INTEGER NOT NULL DEFAULT 6,
                original_stop REAL,
                best_price REAL,
                management_note TEXT NOT NULL DEFAULT '',
                bars_held INTEGER NOT NULL DEFAULT 0,
                strategy_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        existing = {r[1] for r in conn.execute("PRAGMA table_info(sim_trades)").fetchall()}
        migrations = {
            "exit_mode": "ALTER TABLE sim_trades ADD COLUMN exit_mode TEXT NOT NULL DEFAULT 'SMART'",
            "grace_bars": "ALTER TABLE sim_trades ADD COLUMN grace_bars INTEGER NOT NULL DEFAULT 6",
            "original_stop": "ALTER TABLE sim_trades ADD COLUMN original_stop REAL",
            "best_price": "ALTER TABLE sim_trades ADD COLUMN best_price REAL",
            "management_note": "ALTER TABLE sim_trades ADD COLUMN management_note TEXT NOT NULL DEFAULT ''",
        }
        for name, sql in migrations.items():
            if name not in existing:
                conn.execute(sql)
        conn.execute("UPDATE sim_trades SET original_stop=stop WHERE original_stop IS NULL")
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
    mark = float(out.get("last_mark") or out.get("requested_entry") or 0)
    entry = float(out.get("requested_entry") or 0)
    if out.get("status") == "PENDING" and entry > 0:
        out["distance_to_entry_pct"] = round(abs(mark-entry)/entry*100, 4)
        out["lifecycle_stage"] = "WAITING_ENTRY"
        out["ai_state"] = "接近成交" if out["distance_to_entry_pct"] <= 0.2 else "继续等待挂单"
    elif out.get("status") == "OPEN":
        out["lifecycle_stage"] = "POSITION_MANAGEMENT"
        out["ai_state"] = str(out.get("management_note") or "继续按止盈止损与智能退出规则管理")
    elif out.get("status") == "CLOSED":
        out["lifecycle_stage"] = "REVIEW"
        out["review"] = _trade_review(out)
    else:
        out["lifecycle_stage"] = out.get("status")
    return out


def create_trade(*, contract: str, interval: str, side: str, order_type: str,
                 requested_entry: float, market_price: float, stop: float, target: float,
                 account_balance: float, risk_fraction: float, leverage: float,
                 fee_rate: float, slippage_rate: float, max_holding_bars: int,
                 exit_mode: str = "SMART", grace_bars: int = 6,
                 strategy: dict[str, Any] | None = None, notes: str = "",
                 duplicate_policy: str = "REPLACE") -> dict[str, Any]:
    init_db()
    side = side.upper()
    order_type = order_type.upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("模拟交易方向必须为 LONG 或 SHORT")
    if order_type not in {"MARKET", "LIMIT"}:
        raise ValueError("订单类型必须为 MARKET 或 LIMIT")
    exit_mode = exit_mode.upper()
    if exit_mode not in {"FIXED", "BREAKEVEN", "MIN_LOSS", "TRAILING", "SMART"}:
        raise ValueError("到期处理方式无效")
    max_holding_bars = max(1, min(int(max_holding_bars), 500))
    grace_bars = max(0, min(int(grace_bars), 100))
    if min(requested_entry, market_price, stop, target, account_balance, leverage) <= 0:
        raise ValueError("价格、余额和杠杆必须大于0")
    if side == "LONG" and not (stop < requested_entry < target):
        raise ValueError("做多必须满足：止损 < 入场 < 止盈")
    if side == "SHORT" and not (target < requested_entry < stop):
        raise ValueError("做空必须满足：止盈 < 入场 < 止损")
    duplicate_policy = str(duplicate_policy or "REPLACE").upper()
    if duplicate_policy not in {"REPLACE", "KEEP", "REJECT"}:
        raise ValueError("重复订单处理方式必须为 REPLACE、KEEP 或 REJECT")

    # Protect against double taps and retry submissions on mobile networks.
    with _LOCK, _conn() as conn:
        duplicate = conn.execute(
            """SELECT id FROM sim_trades WHERE contract=? AND interval=? AND side=?
               AND status='PENDING' ORDER BY created_at DESC LIMIT 1""",
            (contract.upper(), interval, side),
        ).fetchone()
        if duplicate:
            if duplicate_policy == "REJECT":
                raise ValueError("该交易对、周期和方向已经存在待成交挂单")
            if duplicate_policy == "REPLACE":
                now_cancel = _now()
                conn.execute(
                    "UPDATE sim_trades SET status='CANCELLED',updated_at=?,closed_at=?,exit_reason='REPLACED' WHERE id=?",
                    (now_cancel, now_cancel, duplicate["id"]),
                )
                conn.commit()

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
            last_mark,last_checked_at,max_holding_bars,exit_mode,grace_bars,original_stop,best_price,management_note,strategy_json,notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_id, now, now, contract.upper(), interval, side, status, order_type,
             requested_entry, fill, stop, target, quantity, notional, leverage,
             fee_rate, slippage_rate, account_balance, risk_fraction, opened_at,
             market_price, now, max_holding_bars, exit_mode, grace_bars, stop, fill or requested_entry, '', json.dumps(strategy or {}, ensure_ascii=False), notes),
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
    original_stop = float(trade.get("original_stop") or stop)
    target = float(trade["target"])
    exit_mode = str(trade.get("exit_mode") or "SMART").upper()
    grace_bars = int(trade.get("grace_bars") or 0)
    requested = float(trade["requested_entry"])
    best_price = float(trade.get("best_price") or trade.get("fill_price") or requested)
    management_note = str(trade.get("management_note") or "")
    # Always initialise the outgoing note. There may be no newly closed candle,
    # so assigning it only inside the loop causes UnboundLocalError.
    note = management_note
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
        risk_unit = max(abs(fill - original_stop), fill * 1e-8)
        if side == "LONG":
            best_price = max(best_price, c.h)
            mfe = max(mfe, (c.h - fill) / fill)
            mae = max(mae, (fill - c.l) / fill)
        else:
            best_price = min(best_price, c.l)
            mfe = max(mfe, (fill - c.l) / fill)
            mae = max(mae, (c.h - fill) / fill)

        # Intelligent trade management starts halfway through the planned holding window.
        mfe_r = (mfe * fill) / risk_unit
        fee_buffer = fill * (2 * float(trade["fee_rate"]) + 2 * slip)
        managed_stop = stop
        note = management_note
        if exit_mode in {"BREAKEVEN", "TRAILING", "SMART"} and bars_held >= max(2, int(trade["max_holding_bars"]) // 2):
            if mfe_r >= 0.50:
                breakeven = fill + fee_buffer if side == "LONG" else fill - fee_buffer
                managed_stop = max(managed_stop, breakeven) if side == "LONG" else min(managed_stop, breakeven)
                note = "浮盈达到0.5R，止损已移动到含费用保本位"
            if exit_mode in {"TRAILING", "SMART"} and mfe_r >= 0.80:
                locked_r = max(0.20, mfe_r * 0.60)
                trailing = fill + locked_r * risk_unit if side == "LONG" else fill - locked_r * risk_unit
                managed_stop = max(managed_stop, trailing) if side == "LONG" else min(managed_stop, trailing)
                note = f"移动止盈生效，当前锁定约{locked_r:.2f}R"
        stop = managed_stop
        hit_stop = c.l <= stop if side == "LONG" else c.h >= stop
        hit_target = c.h >= target if side == "LONG" else c.l <= target

        # With OHLC data the intrabar order is unknowable. Use conservative SL-first.
        if hit_stop:
            exit_price = stop * (1 - slip if side == "LONG" else 1 + slip)
            changes.update(_close_values({**trade, **changes, "fill_price": fill}, exit_price,
                                         (("TRAILING_STOP" if stop != original_stop else "SL") if not hit_target else "SL_FIRST_AMBIGUOUS"), c.t))
            status = "CLOSED"
            break
        if hit_target:
            exit_price = target * (1 - slip if side == "LONG" else 1 + slip)
            changes.update(_close_values({**trade, **changes, "fill_price": fill}, exit_price, "TP", c.t))
            status = "CLOSED"
            break
        max_bars = int(trade["max_holding_bars"])
        current_r = ((c.c - fill) if side == "LONG" else (fill - c.c)) / risk_unit
        adverse_r = (mae * fill) / risk_unit
        if bars_held >= max_bars:
            reason = None
            if exit_mode == "FIXED":
                reason = "TIME"
            elif exit_mode == "BREAKEVEN" and current_r >= -0.10:
                reason = "TIME_NEAR_BREAKEVEN"
            elif exit_mode == "MIN_LOSS" and (current_r >= -0.25 or (adverse_r > 0 and current_r >= -adverse_r * 0.45)):
                reason = "TIME_MIN_LOSS"
            elif exit_mode in {"TRAILING", "SMART"} and current_r > 0 and mfe_r - current_r >= max(0.20, mfe_r * 0.35):
                reason = "TIME_LOCK_PROFIT"
            elif exit_mode == "SMART" and current_r >= -0.15:
                reason = "TIME_SMART_EXIT"
            if bars_held >= max_bars + grace_bars:
                reason = reason or "TIME_GRACE_LIMIT"
            if reason:
                exit_price = c.c * (1 - slip if side == "LONG" else 1 + slip)
                changes.update(_close_values({**trade, **changes, "fill_price": fill, "stop": original_stop}, exit_price, reason, c.t))
                status = "CLOSED"
                break

    last = candles[-1].c
    changes.update({
        "updated_at": _now(), "last_mark": last, "last_checked_at": candles[-1].t,
        "max_favorable_excursion": mfe, "max_adverse_excursion": mae, "bars_held": bars_held,
        "stop": stop, "best_price": best_price, "management_note": note,
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


def clear_trades(scope: str = "ALL") -> dict[str, int]:
    init_db()
    scope = str(scope or "ALL").upper()
    allowed = {"ALL", "CLOSED", "CANCELLED", "ACTIVE"}
    if scope not in allowed:
        raise ValueError("清理范围无效")
    where = ""
    if scope == "CLOSED":
        where = " WHERE status='CLOSED'"
    elif scope == "CANCELLED":
        where = " WHERE status='CANCELLED'"
    elif scope == "ACTIVE":
        where = " WHERE status IN ('OPEN','PENDING')"
    with _LOCK, _conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sim_trades" + where).fetchone()[0]
        conn.execute("DELETE FROM sim_trades" + where)
        conn.commit()
    return {"deleted": int(count)}


def _trade_review(trade: dict[str, Any]) -> dict[str, Any]:
    status = trade.get("status")
    if status != "CLOSED":
        return {}
    r = float(trade.get("r_multiple") or 0)
    mfe = float(trade.get("max_favorable_excursion") or 0)
    mae = float(trade.get("max_adverse_excursion") or 0)
    score = 60 + min(25, max(-25, r * 12)) + min(10, mfe * 200) - min(10, mae * 150)
    score = int(max(0, min(100, round(score))))
    discipline = "遵守计划" if trade.get("exit_reason") in {"TP","SL","TRAILING_STOP","TIME","TIME_NEAR_BREAKEVEN","TIME_MIN_LOSS","TIME_LOCK_PROFIT","TIME_SMART_EXIT","TIME_GRACE_LIMIT","SL_FIRST_AMBIGUOUS"} else "人工干预"
    return {
        "score": score,
        "discipline": discipline,
        "summary": "盈利并控制回撤" if r > 0 else "亏损已受止损或时间规则约束",
        "improvement": "继续积累同类样本，重点比较实际MFE与最终退出R。" if r > 0 else "复查入场质量、止损距离与市场状态是否匹配。",
    }


def stats() -> dict[str, Any]:
    trades = [t for t in list_trades(500) if t["status"] == "CLOSED"]
    wins = [t for t in trades if float(t.get("net_pnl") or 0) > 0]
    losses = [t for t in trades if float(t.get("net_pnl") or 0) <= 0]
    gross_profit = sum(float(t.get("net_pnl") or 0) for t in wins)
    gross_loss = abs(sum(float(t.get("net_pnl") or 0) for t in losses))
    total = len(trades)
    pnls = [float(t.get("net_pnl") or 0) for t in reversed(trades)]
    rs = [float(t.get("r_multiple") or 0) for t in trades]
    equity, curve, peak, max_dd = 0.0, [], 0.0, 0.0
    for i, pnl in enumerate(pnls, 1):
        equity += pnl; peak = max(peak, equity); max_dd = max(max_dd, peak - equity)
        curve.append({"index": i, "equity": round(equity, 4)})
    mean_r = sum(rs)/total if total else 0.0
    variance = sum((x-mean_r)**2 for x in rs)/(total-1) if total > 1 else 0.0
    std = math.sqrt(variance)
    downside = [min(0.0, x) for x in rs]
    downside_dev = math.sqrt(sum(x*x for x in downside)/len(downside)) if downside else 0.0
    sharpe = mean_r/std*math.sqrt(total) if std > 0 else 0.0
    sortino = mean_r/downside_dev*math.sqrt(total) if downside_dev > 0 else 0.0
    avg_win = sum(float(t.get("r_multiple") or 0) for t in wins)/len(wins) if wins else 0.0
    avg_loss = abs(sum(float(t.get("r_multiple") or 0) for t in losses)/len(losses)) if losses else 0.0
    win_prob = len(wins)/total if total else 0.0
    payoff = avg_win/avg_loss if avg_loss > 0 else 0.0
    kelly = max(0.0, min(0.25, win_prob - (1-win_prob)/payoff)) if payoff > 0 else 0.0
    by_side = {}
    for side in ("LONG", "SHORT"):
        bucket=[t for t in trades if t.get("side")==side]; bw=[t for t in bucket if float(t.get("net_pnl") or 0)>0]
        by_side[side]={"trades":len(bucket),"win_rate_pct":round(len(bw)/len(bucket)*100,2) if bucket else 0.0,"net_pnl":round(sum(float(t.get("net_pnl") or 0) for t in bucket),4)}
    return {
        "closed_trades": total, "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins)/total*100,2) if total else 0.0,
        "net_pnl": round(sum(pnls),4), "average_r": round(mean_r,3),
        "profit_factor": round(gross_profit/gross_loss,3) if gross_loss else (None if not gross_profit else 999.0),
        "max_drawdown": round(max_dd,4), "sharpe": round(sharpe,3), "sortino": round(sortino,3),
        "kelly_fraction": round(kelly,4), "by_side": by_side, "equity_curve": curve[-100:],
        "open_trades": len([t for t in list_trades(500) if t["status"] in {"OPEN","PENDING"}]),
        "storage": str(DB_PATH),
    }

init_db()
