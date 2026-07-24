from __future__ import annotations

import asyncio
import json
import math
import random
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import product
from math import isfinite, sqrt
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Iterable

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ===== gate_client.py =====
import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://api.gateio.ws/api/v4"
INTERVAL_SECONDS = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
}
MAX_PAGE_BARS = 1000


class GateDataError(RuntimeError):
    """Raised when Gate market data is missing, malformed, or unavailable."""


@dataclass(frozen=True)
class Candle:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float


def normalize_contract(contract: str) -> str:
    value = contract.strip().upper().replace("-", "_").replace("/", "_")
    if not value or "_" not in value:
        raise GateDataError("交易对格式应类似 BTC_USDT")
    return value


def _parse_candle(item: dict[str, Any]) -> Candle:
    try:
        candle = Candle(
            t=int(float(item["t"])),
            o=float(item["o"]),
            h=float(item["h"]),
            l=float(item["l"]),
            c=float(item["c"]),
            v=float(item.get("v", 0.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GateDataError(f"Gate K线字段解析失败：{exc}") from exc

    if min(candle.o, candle.h, candle.l, candle.c) <= 0:
        raise GateDataError("Gate返回了非正价格")
    if candle.h < max(candle.o, candle.c) or candle.l > min(candle.o, candle.c):
        raise GateDataError("Gate返回的K线OHLC关系异常")
    if candle.v < 0:
        raise GateDataError("Gate返回了负成交量")
    return candle


def _validate_sequence(candles: list[Candle], interval: str) -> list[str]:
    warnings: list[str] = []
    seconds = INTERVAL_SECONDS[interval]
    for prev, cur in zip(candles, candles[1:]):
        gap = cur.t - prev.t
        if gap <= 0:
            raise GateDataError("K线时间未严格递增")
        if gap > seconds * 1.5:
            warnings.append(f"检测到历史数据缺口：{prev.t} → {cur.t}")
    return warnings


async def _get(params: dict[str, str]) -> list[dict[str, Any]]:
    timeout = httpx.Timeout(8.0, connect=4.0)
    url = f"{BASE_URL}/futures/usdt/candlesticks"
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": "gate-ai-quant/2.0"},
                )
            if response.status_code == 429:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code != 200:
                raise GateDataError(
                    f"Gate接口返回 {response.status_code}：{response.text[:250]}"
                )
            payload = response.json()
            if not isinstance(payload, list):
                raise GateDataError("Gate返回格式异常")
            return payload
        except (httpx.HTTPError, ValueError, GateDataError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.8 * (attempt + 1))

    raise GateDataError(f"连接Gate行情接口失败：{last_error}")


def _clean(payload: list[dict[str, Any]], interval: str, closed_only: bool) -> tuple[list[Candle], list[str]]:
    candles = sorted((_parse_candle(x) for x in payload), key=lambda x: x.t)
    deduped = {x.t: x for x in candles}
    candles = [deduped[t] for t in sorted(deduped)]

    if closed_only:
        now = int(time.time())
        seconds = INTERVAL_SECONDS[interval]
        candles = [x for x in candles if x.t + seconds <= now + 2]

    warnings = _validate_sequence(candles, interval)
    return candles, warnings


async def fetch_recent_candles(
    contract: str,
    interval: str,
    limit: int = 300,
) -> tuple[list[Candle], list[str]]:
    if interval not in INTERVAL_SECONDS:
        raise GateDataError("暂不支持该周期")
    if not 50 <= limit <= MAX_PAGE_BARS:
        raise GateDataError(f"limit应在50到{MAX_PAGE_BARS}之间")

    params = {
        "contract": normalize_contract(contract),
        "interval": interval,
        "limit": str(limit),
    }
    payload = await _get(params)
    candles, warnings = _clean(payload, interval, closed_only=True)
    if len(candles) < 220:
        raise GateDataError(f"有效已收盘K线不足：仅 {len(candles)} 根")
    return candles, warnings


async def fetch_history(
    contract: str,
    interval: str,
    start_ts: int,
    end_ts: int,
    max_bars: int = 30000,
) -> tuple[list[Candle], list[str]]:
    """Download historical closed candles in non-overlapping chunks.

    The method deduplicates timestamps, sorts ascending, validates OHLC, and
    rejects an excessive request before downloading.
    """
    if interval not in INTERVAL_SECONDS:
        raise GateDataError("暂不支持该周期")
    if start_ts >= end_ts:
        raise GateDataError("开始时间必须早于结束时间")

    seconds = INTERVAL_SECONDS[interval]
    estimated = (end_ts - start_ts) // seconds + 1
    if estimated > max_bars:
        raise GateDataError(
            f"请求约{estimated}根K线，超过当前安全上限{max_bars}根；请缩短日期范围"
        )

    contract = normalize_contract(contract)
    cursor = start_ts
    merged: dict[int, Candle] = {}
    warnings: list[str] = []

    while cursor < end_ts:
        chunk_end = min(end_ts, cursor + seconds * (MAX_PAGE_BARS - 1))
        params = {
            "contract": contract,
            "interval": interval,
            "from": str(cursor),
            "to": str(chunk_end),
            "limit": str(MAX_PAGE_BARS),
        }
        payload = await _get(params)
        page, page_warnings = _clean(payload, interval, closed_only=True)
        warnings.extend(page_warnings)
        for candle in page:
            if start_ts <= candle.t <= end_ts:
                merged[candle.t] = candle
        cursor = chunk_end + seconds
        await asyncio.sleep(0.06)

    candles = [merged[t] for t in sorted(merged)]
    warnings.extend(_validate_sequence(candles, interval))
    if len(candles) < 260:
        raise GateDataError(f"回测数据不足：仅获取{len(candles)}根已收盘K线")
    return candles, sorted(set(warnings))


# ===== strategy.py =====
from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean



@dataclass(frozen=True)
class SignalSnapshot:
    side: str
    long_score: int
    short_score: int
    confidence: int
    entry: float | None
    stop: float | None
    target: float | None
    atr: float
    rsi: float
    ema20: float
    ema50: float
    ema200: float
    reasons: list[str]


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("EMA样本不足")
    alpha = 2.0 / (period + 1.0)
    value = fmean(values[:period])
    for x in values[period:]:
        value = alpha * x + (1.0 - alpha) * value
    return value


def ema_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        raise ValueError("EMA样本不足")
    value = fmean(values[:period])
    out = [value]
    alpha = 2.0 / (period + 1.0)
    for x in values[period:]:
        value = alpha * x + (1.0 - alpha) * value
        out.append(value)
    return out


def rsi_wilder(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        raise ValueError("RSI样本不足")
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = fmean(gains[:period])
    avg_loss = fmean(losses[:period])
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr_wilder(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        raise ValueError("ATR样本不足")
    tr: list[float] = []
    for prev, cur in zip(candles, candles[1:]):
        tr.append(max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c)))
    value = fmean(tr[:period])
    for x in tr[period:]:
        value = (value * (period - 1) + x) / period
    return value


def macd_values(values: list[float]) -> tuple[float, float, float]:
    fast = ema_series(values, 12)
    slow = ema_series(values, 26)
    fast_aligned = fast[-len(slow):]
    line = [a - b for a, b in zip(fast_aligned, slow)]
    signal = ema_series(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]


def _clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def build_signal(candles: list[Candle], threshold: int = 72) -> SignalSnapshot:
    """Build a signal using only candles supplied up to the decision time."""
    if len(candles) < 220:
        raise ValueError("至少需要220根历史K线")

    closes = [x.c for x in candles]
    volumes = [x.v for x in candles]
    price = closes[-1]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    rsi = rsi_wilder(closes)
    atr = atr_wilder(candles)
    macd, macd_sig, hist = macd_values(closes)
    avg_vol = fmean(volumes[-21:-1]) or 1.0
    vol_ratio = volumes[-1] / avg_vol

    long_score = 50.0
    short_score = 50.0
    reasons: list[str] = []

    if price > e20 > e50 > e200:
        long_score += 24
        short_score -= 18
        reasons.append("多头均线排列")
    elif price < e20 < e50 < e200:
        short_score += 24
        long_score -= 18
        reasons.append("空头均线排列")
    else:
        if price > e50:
            long_score += 6
        else:
            short_score += 6

    if macd > macd_sig and hist > 0:
        long_score += 13
        short_score -= 6
        reasons.append("MACD偏多")
    elif macd < macd_sig and hist < 0:
        short_score += 13
        long_score -= 6
        reasons.append("MACD偏空")

    if 52 <= rsi <= 68:
        long_score += 10
    elif 32 <= rsi <= 48:
        short_score += 10
    elif rsi > 72:
        long_score -= 14
    elif rsi < 28:
        short_score -= 14

    if vol_ratio >= 1.30:
        if candles[-1].c > candles[-1].o:
            long_score += 8
        elif candles[-1].c < candles[-1].o:
            short_score += 8
    elif vol_ratio < 0.60:
        long_score -= 6
        short_score -= 6

    support = min(x.l for x in candles[-21:-1])
    resistance = max(x.h for x in candles[-21:-1])
    if price > resistance:
        long_score += 10
        reasons.append("收盘突破近期阻力")
    elif price < support:
        short_score += 10
        reasons.append("收盘跌破近期支撑")

    if atr / price > 0.04:
        long_score -= 5
        short_score -= 5

    long_i, short_i = _clamp(long_score), _clamp(short_score)
    edge = abs(long_i - short_i)
    confidence = _clamp(0.70 * max(long_i, short_i) + 0.30 * edge)

    side = "FLAT"
    entry = stop = target = None
    recent_low = min(x.l for x in candles[-11:-1])
    recent_high = max(x.h for x in candles[-11:-1])

    if long_i >= threshold and long_i - short_i >= 15:
        side = "LONG"
        entry = price
        stop = min(recent_low, price - atr)
        risk = entry - stop
        if risk > 0:
            target = entry + 1.8 * risk
        else:
            side = "FLAT"
    elif short_i >= threshold and short_i - long_i >= 15:
        side = "SHORT"
        entry = price
        stop = max(recent_high, price + atr)
        risk = stop - entry
        if risk > 0:
            target = entry - 1.8 * risk
            if target <= 0:
                side = "FLAT"
        else:
            side = "FLAT"

    values = [e20, e50, e200, rsi, atr, macd, macd_sig]
    if not all(isfinite(x) for x in values):
        raise ValueError("指标出现非有限值")

    return SignalSnapshot(
        side=side,
        long_score=long_i,
        short_score=short_i,
        confidence=confidence,
        entry=entry,
        stop=stop,
        target=target,
        atr=atr,
        rsi=rsi,
        ema20=e20,
        ema50=e50,
        ema200=e200,
        reasons=reasons,
    )


def snapshot_to_dict(snapshot: SignalSnapshot) -> dict:
    return asdict(snapshot)


# ===== backtest.py =====
from dataclasses import asdict, dataclass
from math import sqrt
from statistics import fmean, pstdev



@dataclass
class Trade:
    side: str
    signal_time: int
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    stop: float
    target: float
    gross_return_pct: float
    net_return_pct: float
    outcome: str
    exit_reason: str


@dataclass
class BacktestResult:
    bars: int
    trades: int
    wins: int
    losses: int
    win_rate_pct: float
    net_return_pct: float
    max_drawdown_pct: float
    profit_factor: float | None
    expectancy_pct: float
    sharpe_like: float | None
    average_win_pct: float
    average_loss_pct: float
    fees_pct_round_trip: float
    slippage_pct_round_trip: float
    assumptions: list[str]
    warnings: list[str]
    trades_detail: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def _entry_price(open_price: float, side: str, slippage_rate: float) -> float:
    return open_price * (1 + slippage_rate if side == "LONG" else 1 - slippage_rate)


def _exit_price(raw_price: float, side: str, slippage_rate: float) -> float:
    return raw_price * (1 - slippage_rate if side == "LONG" else 1 + slippage_rate)


def run_backtest(
    candles: list[Candle],
    threshold: int = 72,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
    risk_fraction: float = 0.01,
    max_holding_bars: int = 24,
) -> BacktestResult:
    """No-lookahead single-position backtest.

    Signal is computed after candle i closes. Entry occurs at candle i+1 open.
    If stop and target are both touched in one candle, stop is assumed first.
    This is deliberately conservative because lower-timeframe path is unknown.
    """
    if len(candles) < 300:
        raise ValueError("回测至少需要300根K线")
    if not (0 < risk_fraction <= 0.05):
        raise ValueError("risk_fraction应在0到5%之间")
    if fee_rate < 0 or slippage_rate < 0:
        raise ValueError("费用与滑点不能为负")

    trades: list[Trade] = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    i = 219

    while i < len(candles) - 2:
        snapshot = build_signal(candles[: i + 1], threshold=threshold)
        if snapshot.side == "FLAT" or snapshot.stop is None or snapshot.target is None:
            i += 1
            continue

        next_bar = candles[i + 1]
        side = snapshot.side
        entry = _entry_price(next_bar.o, side, slippage_rate)

        # Preserve the signal's absolute stop distance but anchor it to actual next-open entry.
        signal_risk = abs((snapshot.entry or next_bar.o) - snapshot.stop)
        if signal_risk <= 0:
            i += 1
            continue
        stop = entry - signal_risk if side == "LONG" else entry + signal_risk
        target = entry + 1.8 * signal_risk if side == "LONG" else entry - 1.8 * signal_risk
        if target <= 0:
            i += 1
            continue

        exit_raw = candles[min(i + 1 + max_holding_bars, len(candles) - 1)].c
        exit_time = candles[min(i + 1 + max_holding_bars, len(candles) - 1)].t
        reason = "TIME"
        outcome = "TIME"
        exit_index = min(i + 1 + max_holding_bars, len(candles) - 1)

        for j in range(i + 1, min(i + 2 + max_holding_bars, len(candles))):
            bar = candles[j]
            if side == "LONG":
                hit_stop = bar.l <= stop
                hit_target = bar.h >= target
            else:
                hit_stop = bar.h >= stop
                hit_target = bar.l <= target

            if hit_stop and hit_target:
                exit_raw = stop
                exit_time = bar.t
                exit_index = j
                reason = "STOP_FIRST_AMBIGUOUS"
                outcome = "LOSS"
                break
            if hit_stop:
                exit_raw = stop
                exit_time = bar.t
                exit_index = j
                reason = "STOP"
                outcome = "LOSS"
                break
            if hit_target:
                exit_raw = target
                exit_time = bar.t
                exit_index = j
                reason = "TARGET"
                outcome = "WIN"
                break

        exit_price = _exit_price(exit_raw, side, slippage_rate)
        gross = ((exit_price - entry) / entry) if side == "LONG" else ((entry - exit_price) / entry)
        net = gross - 2 * fee_rate
        if outcome == "TIME":
            outcome = "WIN" if net > 0 else "LOSS"

        # Fixed-fraction risk sizing: a full stop approximates risk_fraction of equity.
        stop_pct = signal_risk / entry
        position_fraction = min(1.0, risk_fraction / stop_pct) if stop_pct > 0 else 0.0
        equity *= max(0.0, 1.0 + net * position_fraction)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

        trades.append(
            Trade(
                side=side,
                signal_time=candles[i].t,
                entry_time=next_bar.t,
                exit_time=exit_time,
                entry=entry,
                exit=exit_price,
                stop=stop,
                target=target,
                gross_return_pct=gross * 100,
                net_return_pct=net * 100,
                outcome=outcome,
                exit_reason=reason,
            )
        )
        i = max(i + 1, exit_index + 1)

    returns = [t.net_return_pct / 100 for t in trades]
    wins = [x for x in returns if x > 0]
    losses = [x for x in returns if x <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (None if not wins else float("inf"))
    sharpe_like = None
    if len(returns) >= 2 and pstdev(returns) > 0:
        sharpe_like = fmean(returns) / pstdev(returns) * sqrt(len(returns))

    warnings: list[str] = []
    if len(trades) < 30:
        warnings.append("交易样本少于30笔，统计结论不稳定")
    if len(candles) < 1000:
        warnings.append("历史K线少于1000根，建议扩大回测区间")
    if trades and max_dd > 0.20:
        warnings.append("最大回撤超过20%，策略风险较高")

    return BacktestResult(
        bars=len(candles),
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=(len(wins) / len(trades) * 100) if trades else 0.0,
        net_return_pct=(equity - 1.0) * 100,
        max_drawdown_pct=max_dd * 100,
        profit_factor=profit_factor,
        expectancy_pct=(fmean(returns) * 100) if returns else 0.0,
        sharpe_like=sharpe_like,
        average_win_pct=(fmean(wins) * 100) if wins else 0.0,
        average_loss_pct=(fmean(losses) * 100) if losses else 0.0,
        fees_pct_round_trip=fee_rate * 2 * 100,
        slippage_pct_round_trip=slippage_rate * 2 * 100,
        assumptions=[
            "信号仅使用当时已经收盘的K线",
            "信号产生后的下一根K线开盘价入场",
            "同一根K线同时触发止损和止盈时，保守地按止损先发生",
            "每次只持有一个仓位，不允许重叠交易",
            "已计入双边手续费和双边滑点",
            "结果不包含资金费率、强平、盘口冲击和税费",
        ],
        warnings=warnings,
        trades_detail=[asdict(t) for t in trades[-100:]],
    )


# ===== optimizer.py =====
from dataclasses import asdict, dataclass
from itertools import product



@dataclass
class OptimizationRow:
    threshold: int
    risk_fraction: float
    max_holding_bars: int
    trades: int
    win_rate_pct: float
    net_return_pct: float
    max_drawdown_pct: float
    profit_factor: float | None
    expectancy_pct: float
    robust_score: float

    def to_dict(self) -> dict:
        return asdict(self)


def score_result(
    trades: int,
    net_return_pct: float,
    max_drawdown_pct: float,
    profit_factor: float | None,
    expectancy_pct: float,
) -> float:
    if trades < 20:
        return -9999.0
    pf = 0.0 if profit_factor is None else min(float(profit_factor), 4.0)
    sample_bonus = min(trades, 200) / 200 * 15
    return (
        net_return_pct * 0.30
        - max_drawdown_pct * 0.60
        + pf * 12
        + expectancy_pct * 10
        + sample_bonus
    )


def optimize_parameters(
    candles: list[Candle],
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
) -> list[OptimizationRow]:
    rows: list[OptimizationRow] = []
    for threshold, risk_fraction, holding in product(
        (68, 72, 76, 80),
        (0.005, 0.01, 0.015),
        (12, 24, 48),
    ):
        result = run_backtest(
            candles,
            threshold=threshold,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            risk_fraction=risk_fraction,
            max_holding_bars=holding,
        )
        rows.append(
            OptimizationRow(
                threshold=threshold,
                risk_fraction=risk_fraction,
                max_holding_bars=holding,
                trades=result.trades,
                win_rate_pct=result.win_rate_pct,
                net_return_pct=result.net_return_pct,
                max_drawdown_pct=result.max_drawdown_pct,
                profit_factor=result.profit_factor,
                expectancy_pct=result.expectancy_pct,
                robust_score=score_result(
                    result.trades,
                    result.net_return_pct,
                    result.max_drawdown_pct,
                    result.profit_factor,
                    result.expectancy_pct,
                ),
            )
        )
    return sorted(rows, key=lambda row: row.robust_score, reverse=True)


# ===== validation.py =====
from dataclasses import asdict, dataclass
import random
from statistics import fmean



@dataclass
class WalkForwardFold:
    fold: int
    train_bars: int
    test_bars: int
    threshold: int
    risk_fraction: float
    max_holding_bars: int
    train_return_pct: float
    test_return_pct: float
    test_drawdown_pct: float
    test_profit_factor: float | None
    test_trades: int

    def to_dict(self) -> dict:
        return asdict(self)


def walk_forward_validate(
    candles: list[Candle],
    train_bars: int = 1200,
    test_bars: int = 400,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
) -> dict:
    if train_bars < 500 or test_bars < 200:
        raise ValueError("训练区间至少500根，测试区间至少200根")
    if len(candles) < train_bars + test_bars:
        raise ValueError("历史数据不足以进行滚动验证")

    folds: list[WalkForwardFold] = []
    cursor = 0
    fold_no = 1

    while cursor + train_bars + test_bars <= len(candles):
        train = candles[cursor: cursor + train_bars]
        test = candles[cursor + train_bars: cursor + train_bars + test_bars]

        best = optimize_parameters(
            train,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
        )[0]

        train_result = run_backtest(
            train,
            threshold=best.threshold,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            risk_fraction=best.risk_fraction,
            max_holding_bars=best.max_holding_bars,
        )
        test_result = run_backtest(
            test,
            threshold=best.threshold,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            risk_fraction=best.risk_fraction,
            max_holding_bars=best.max_holding_bars,
        )

        folds.append(
            WalkForwardFold(
                fold=fold_no,
                train_bars=len(train),
                test_bars=len(test),
                threshold=best.threshold,
                risk_fraction=best.risk_fraction,
                max_holding_bars=best.max_holding_bars,
                train_return_pct=train_result.net_return_pct,
                test_return_pct=test_result.net_return_pct,
                test_drawdown_pct=test_result.max_drawdown_pct,
                test_profit_factor=test_result.profit_factor,
                test_trades=test_result.trades,
            )
        )
        fold_no += 1
        cursor += test_bars

    positive = sum(1 for fold in folds if fold.test_return_pct > 0)
    return {
        "folds": [fold.to_dict() for fold in folds],
        "fold_count": len(folds),
        "positive_test_folds": positive,
        "positive_fold_rate_pct": positive / len(folds) * 100 if folds else 0,
        "average_test_return_pct": fmean(
            [fold.test_return_pct for fold in folds]
        ) if folds else 0,
        "average_test_drawdown_pct": fmean(
            [fold.test_drawdown_pct for fold in folds]
        ) if folds else 0,
        "total_test_trades": sum(fold.test_trades for fold in folds),
    }


def monte_carlo_trade_paths(
    trade_returns_pct: list[float],
    simulations: int = 1000,
    seed: int = 42,
) -> dict:
    if len(trade_returns_pct) < 20:
        raise ValueError("至少需要20笔交易才能运行蒙特卡洛")
    if not 100 <= simulations <= 10000:
        raise ValueError("模拟次数应在100到10000之间")

    rng = random.Random(seed)
    final_returns: list[float] = []
    max_drawdowns: list[float] = []

    for _ in range(simulations):
        path = trade_returns_pct[:]
        rng.shuffle(path)
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        for trade_pct in path:
            equity *= max(0.0, 1 + trade_pct / 100)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
        final_returns.append((equity - 1) * 100)
        max_drawdowns.append(max_dd * 100)

    def percentile(values: list[float], p: float) -> float:
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
        return ordered[index]

    return {
        "simulations": simulations,
        "final_return_p05_pct": percentile(final_returns, 0.05),
        "final_return_p50_pct": percentile(final_returns, 0.50),
        "final_return_p95_pct": percentile(final_returns, 0.95),
        "max_drawdown_p50_pct": percentile(max_drawdowns, 0.50),
        "max_drawdown_p95_pct": percentile(max_drawdowns, 0.95),
    }


# ===== market_scanner.py =====
import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx



@dataclass
class ScanRow:
    contract: str
    interval: str
    side: str
    long_score: int
    short_score: int
    confidence: int
    liquidity_24h: float
    last_price: float
    entry: float | None
    stop: float | None
    target: float | None
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


_cache: dict[str, tuple[float, Any]] = {}
CACHE_SECONDS = 20
SCAN_CACHE_SECONDS = 30
STALE_SCAN_SECONDS = 300
_scan_result_cache: dict[str, tuple[float, dict]] = {}


async def fetch_futures_tickers() -> list[dict[str, Any]]:
    key = "futures_tickers"
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    url = f"{BASE_URL}/futures/usdt/tickers"
    timeout = httpx.Timeout(20.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={"Accept": "application/json", "User-Agent": "gate-ai-quant-v13"},
        )
    if response.status_code != 200:
        raise GateDataError(f"Gate合约行情列表返回{response.status_code}")
    payload = response.json()
    if not isinstance(payload, list):
        raise GateDataError("Gate合约行情列表格式异常")

    _cache[key] = (now, payload)
    return payload


def _liquidity_value(item: dict[str, Any]) -> float:
    # Gate versions may expose different 24h volume field names.
    candidates = (
        "volume_24h_quote",
        "volume_24h_settle",
        "volume_24h_usd",
        "volume_24h",
    )
    for field in candidates:
        try:
            value = float(item.get(field, 0) or 0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    try:
        return float(item.get("volume_24h_base", 0) or 0) * float(item.get("last", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def top_liquid_contracts(
    tickers: list[dict[str, Any]],
    limit: int = 12,
    min_liquidity: float = 0,
) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for item in tickers:
        contract = str(item.get("contract", "")).upper()
        if not contract.endswith("_USDT"):
            continue
        liquidity = _liquidity_value(item)
        if liquidity >= min_liquidity:
            rows.append((contract, liquidity))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows[:limit]


async def _scan_one(
    contract: str,
    interval: str,
    liquidity: float,
    semaphore: asyncio.Semaphore,
) -> ScanRow | None:
    async with semaphore:
        try:
            candles, warnings = await fetch_recent_candles(contract, interval, 240)
            snapshot = build_signal(candles)
            return ScanRow(
                contract=contract,
                interval=interval,
                side=snapshot.side,
                long_score=snapshot.long_score,
                short_score=snapshot.short_score,
                confidence=snapshot.confidence,
                liquidity_24h=liquidity,
                last_price=candles[-1].c,
                entry=snapshot.entry,
                stop=snapshot.stop,
                target=snapshot.target,
                reasons=snapshot.reasons,
                warnings=warnings,
            )
        except (GateDataError, ValueError):
            return None


async def scan_market(
    interval: str = "15m",
    limit: int = 12,
    min_confidence: int = 55,
) -> dict:
    if not 3 <= limit <= 30:
        raise ValueError("扫描数量应在3到30之间")
    if interval not in {"5m", "15m", "30m", "1h", "4h"}:
        raise ValueError("暂不支持该周期")

    cache_key = f"{interval}:{limit}:{min_confidence}"
    now = time.time()
    cached = _scan_result_cache.get(cache_key)
    if cached and now - cached[0] < SCAN_CACHE_SECONDS:
        result = dict(cached[1])
        result["cache"] = "fresh"
        return result

    try:
        tickers = await fetch_futures_tickers()
        candidates = top_liquid_contracts(tickers, limit=limit)
        semaphore = asyncio.Semaphore(8)
        tasks = [
            _scan_one(contract, interval, liquidity, semaphore)
            for contract, liquidity in candidates
        ]
        rows = await asyncio.wait_for(asyncio.gather(*tasks), timeout=12.0)
        valid = [row for row in rows if row is not None and row.confidence >= min_confidence]
        valid.sort(
            key=lambda row: (row.side != "FLAT", row.confidence, row.liquidity_24h),
            reverse=True,
        )
        result = {
            "interval": interval,
            "requested": limit,
            "analyzed": len([row for row in rows if row is not None]),
            "rows": [row.to_dict() for row in valid],
            "generated_at": int(now),
            "cache": "miss",
            "notice": "排行榜仅表示规则评分，不代表未来胜率。",
        }
        _scan_result_cache[cache_key] = (now, result)
        return result
    except (asyncio.TimeoutError, httpx.HTTPError, GateDataError):
        if cached and now - cached[0] < STALE_SCAN_SECONDS:
            result = dict(cached[1])
            result["cache"] = "stale"
            result["notice"] = "Gate响应较慢，当前显示最近一次成功扫描结果。"
            return result
        raise GateDataError("Gate行情响应超时；服务已取消本次扫描，请稍后重试")


async def multi_timeframe_consensus(contract: str) -> dict:
    contract = normalize_contract(contract)
    intervals = ("5m", "15m", "30m", "1h", "4h")
    semaphore = asyncio.Semaphore(3)

    async def one(interval: str) -> dict:
        async with semaphore:
            candles, warnings = await fetch_recent_candles(contract, interval, 300)
            signal = build_signal(candles)
            return {
                "interval": interval,
                "side": signal.side,
                "long_score": signal.long_score,
                "short_score": signal.short_score,
                "confidence": signal.confidence,
                "warnings": warnings,
            }

    results = await asyncio.gather(*(one(interval) for interval in intervals))
    weights = {"5m": 1, "15m": 2, "30m": 2, "1h": 3, "4h": 4}
    long_total = sum(x["long_score"] * weights[x["interval"]] for x in results)
    short_total = sum(x["short_score"] * weights[x["interval"]] for x in results)
    weight_total = sum(weights.values())
    long_avg = round(long_total / weight_total)
    short_avg = round(short_total / weight_total)

    side = "FLAT"
    if long_avg >= 70 and long_avg - short_avg >= 12:
        side = "LONG"
    elif short_avg >= 70 and short_avg - long_avg >= 12:
        side = "SHORT"

    return {
        "contract": contract,
        "side": side,
        "long_score": long_avg,
        "short_score": short_avg,
        "timeframes": results,
        "method": "1h与4h权重高于短周期，5m主要用于入场时机。",
    }


# ===== prediction_value.py =====
from dataclasses import asdict, dataclass


@dataclass
class PredictionValueResult:
    market_price: float
    model_probability: float
    edge_pct_points: float
    expected_value_per_share: float
    expected_roi_pct: float
    full_kelly_fraction: float
    capped_kelly_fraction: float
    recommendation: str
    explanation: str

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_prediction_value(
    market_price: float,
    model_probability: float,
    fee_rate: float = 0.0,
    kelly_cap: float = 0.05,
    minimum_edge: float = 0.05,
) -> PredictionValueResult:
    """Analyze a YES share paying 1 if true and 0 otherwise.

    This is a calculator. It does not independently estimate the true probability.
    """
    if not 0.01 <= market_price <= 0.99:
        raise ValueError("市场价格应在0.01到0.99之间")
    if not 0.0 <= model_probability <= 1.0:
        raise ValueError("模型概率应在0到1之间")
    if not 0.0 <= fee_rate <= 0.10:
        raise ValueError("费用率应在0到10%之间")
    if not 0.0 < kelly_cap <= 0.25:
        raise ValueError("Kelly上限应在0到25%之间")

    payout_profit = 1.0 - market_price
    expected_value = model_probability * payout_profit - (1.0 - model_probability) * market_price
    expected_value -= fee_rate * market_price
    roi = expected_value / market_price
    edge = model_probability - market_price

    b = payout_profit / market_price
    q = 1.0 - model_probability
    full_kelly = max(0.0, (b * model_probability - q) / b)
    capped = min(full_kelly, kelly_cap)

    if edge >= minimum_edge and expected_value > 0:
        recommendation = "YES_VALUE"
        explanation = "模型概率明显高于市场隐含概率，存在正期望；仍需核验概率来源。"
    elif edge <= -minimum_edge:
        recommendation = "YES_OVERPRICED"
        explanation = "YES价格高于模型概率，不宜买YES；这不自动等于可以买NO。"
    else:
        recommendation = "NO_EDGE"
        explanation = "估值差不足以覆盖模型误差与交易成本，建议观望。"

    return PredictionValueResult(
        market_price=market_price,
        model_probability=model_probability,
        edge_pct_points=edge * 100,
        expected_value_per_share=expected_value,
        expected_roi_pct=roi * 100,
        full_kelly_fraction=full_kelly,
        capped_kelly_fraction=capped,
        recommendation=recommendation,
        explanation=explanation,
    )


# ===== signal_store.py =====
import json
import sqlite3
import time
from pathlib import Path


DB_PATH = Path("/tmp/gate_ai_quant.db")


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

INDEX_HTML = '<!doctype html>\n<html lang="zh-CN">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width,initial-scale=1">\n  <title>Gate AI Quant</title>\n  <style>\n:root { font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#16181d; background:#f4f6f8; }\n* { box-sizing:border-box; }\nbody { margin:0; }\nmain { max-width:820px; margin:auto; padding:20px; }\nheader { padding:10px 2px 18px; }\nh1 { margin:0 0 8px; }\nheader p,.note { color:#626b78; }\n.card { background:white; border-radius:18px; padding:18px; margin:14px 0; box-shadow:0 5px 24px rgba(0,0,0,.06); }\n.grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }\nlabel { font-size:14px; font-weight:600; }\ninput,select { width:100%; margin-top:6px; padding:12px; border:1px solid #d8dde5; border-radius:10px; font-size:16px; background:white; }\nbutton { width:100%; margin-top:14px; border:0; border-radius:12px; padding:13px; font-size:16px; font-weight:700; background:#171a20; color:white; }\npre { white-space:pre-wrap; word-break:break-word; background:#f7f8fa; border-radius:12px; padding:14px; line-height:1.55; min-height:80px; }\n.warning { border-left:5px solid #d89b18; }\n@media(max-width:600px){ .grid{grid-template-columns:1fr;} main{padding:14px;} }\n\n.tabs { display:flex; gap:8px; margin:8px 0 14px; }\n.tabs .tab { margin:0; background:#e8ebf0; color:#252932; }\n.tabs .tab.active { background:#171a20; color:white; }\n.hidden { display:none; }\n.badge { display:inline-block; padding:2px 8px; border-radius:999px; background:#eceff3; }\n\n</style>\n</head>\n<body>\n<main>\n  <header>\n    <h1>Gate AI Quant</h1>\n    <p>公开行情分析与无前视回测。不会连接账户，也不会自动下单。</p>\n  </header>\n\n  \n  <section class="card" style="padding:14px 18px">\n    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px">\n      <div><strong>服务状态</strong><div id="serviceStatus" class="note">正在检查…</div></div>\n      <button id="healthBtn" style="width:auto;margin:0;padding:10px 16px">重新检查</button>\n    </div>\n  </section>\n\n  <nav class="tabs">\n    <button class="tab active" data-target="researchPanel">研究</button>\n    <button class="tab" data-target="scannerPanel">排行榜</button>\n    <button class="tab" data-target="predictionPanel">预测价值</button>\n  </nav>\n\n  <section id="scannerPanel" class="panel hidden">\n    <section class="card">\n      <h2>Gate高流动性合约扫描</h2>\n      <div class="grid">\n        <label>扫描周期<select id="scanInterval">\n          <option>5m</option><option selected>15m</option><option>30m</option><option>1h</option><option>4h</option>\n        </select></label>\n        <label>扫描数量<input id="scanLimit" type="number" value="12" min="3" max="30"></label>\n      </div>\n      <button id="scanBtn">开始扫描</button>\n      <label style="display:block;margin-top:12px"><input id="autoScan" type="checkbox" style="width:auto;margin-right:8px">每30秒自动刷新（页面打开时）</label>\n      <pre id="scanResult">等待扫描</pre>\n    </section>\n\n    <section class="card">\n      <h2>多周期共振</h2>\n      <button id="consensusBtn">分析当前交易对的5个周期</button>\n      <pre id="consensusResult">等待分析</pre>\n    </section>\n  </section>\n\n  <section id="predictionPanel" class="panel hidden">\n    <section class="card">\n      <h2>预测市场价值计算器</h2>\n      <p class="note">Gate官方API文档中未确认公开Prediction接口，因此本版采用手动输入市场价格和你独立估计的真实概率。</p>\n      <div class="grid">\n        <label>YES市场价格<input id="marketPrice" type="number" step="0.01" value="0.60"></label>\n        <label>你的模型概率<input id="modelProbability" type="number" step="0.01" value="0.68"></label>\n        <label>费用率<input id="predictionFee" type="number" step="0.001" value="0"></label>\n        <label>最大Kelly仓位<input id="kellyCap" type="number" step="0.01" value="0.05"></label>\n      </div>\n      <button id="predictionBtn">计算价值和仓位上限</button>\n      <pre id="predictionResult">等待计算</pre>\n    </section>\n  </section>\n\n  <div id="researchPanel" class="panel">\n\n  <section class="card">\n    <h2>实时信号</h2>\n    <div class="grid">\n      <label>交易对<input id="contract" value="BTC_USDT"></label>\n      <label>周期<select id="interval">\n        <option>5m</option><option selected>15m</option><option>30m</option>\n        <option>1h</option><option>4h</option>\n      </select></label>\n    </div>\n    <button id="analyzeBtn">分析已收盘K线</button>\n    <pre id="analysis">等待分析</pre>\n  </section>\n\n  <section class="card">\n    <h2>历史回测</h2>\n    <div class="grid">\n      <label>开始日期<input id="start" type="date" value="2026-01-01"></label>\n      <label>结束日期<input id="end" type="date" value="2026-03-31"></label>\n      <label>信号阈值<input id="threshold" type="number" value="72" min="60" max="90"></label>\n      <label>单边手续费<input id="fee" type="number" step="0.0001" value="0.0005"></label>\n      <label>单边滑点<input id="slippage" type="number" step="0.0001" value="0.0002"></label>\n      <label>每笔风险比例<input id="risk" type="number" step="0.005" value="0.01"></label>\n    </div>\n    <button id="backtestBtn">开始回测</button>\n    <p class="note">5分钟三个月约2.6万根K线，接近本版本单次安全上限。建议先用15分钟或缩短日期。</p>\n    <button id="exportBtn" type="button">导出当前回测报告</button>\n<canvas id="equityChart" width="900" height="320" style="width:100%;max-width:900px;background:#fff;border:1px solid #ddd;border-radius:10px"></canvas>\n<pre id="backtest">等待回测</pre>\n  </section>\n\n\n  <section class="card">\n    <h2>参数优化</h2>\n    <button id="optimizeBtn">运行36组参数</button>\n    <pre id="optimize">等待优化</pre>\n  </section>\n\n  <section class="card">\n    <h2>滚动样本外验证</h2>\n    <div class="grid">\n      <label>训练K线<input id="trainBars" type="number" value="1200"></label>\n      <label>测试K线<input id="testBars" type="number" value="400"></label>\n    </div>\n    <button id="wfBtn">开始Walk-Forward</button>\n    <pre id="wf">等待验证</pre>\n  </section>\n\n  <section class="card">\n    <h2>蒙特卡洛风险</h2>\n    <button id="mcBtn">运行1000次模拟</button>\n    <pre id="mc">等待模拟</pre>\n  </section>\n\n  <section class="card warning">\n    <h2>结果解释</h2>\n    <p>回测不是盈利承诺。先查看交易笔数、最大回撤、Profit Factor和样本外表现，不要只看胜率。</p>\n  </section>\n  </div>\n</main>\n<script>\nconst $ = (id) => document.getElementById(id);\n\nfunction fmt(x, digits=4) {\n  return x === null || x === undefined ? "—" : Number(x).toFixed(digits);\n}\n\n$("analyzeBtn").onclick = async () => {\n  $("analysis").textContent = "正在读取Gate公开行情…";\n  const contract = $("contract").value.trim();\n  const interval = $("interval").value;\n  try {\n    const r = await fetch(`/api/analyze?contract=${encodeURIComponent(contract)}&interval=${interval}`);\n    const j = await r.json();\n    if (!r.ok) throw new Error(j.detail || "分析失败");\n    const s = j.signal;\n    $("analysis").textContent =\n`交易对：${j.contract}  周期：${j.interval}\n方向：${s.side}\n做多评分：${s.long_score}\n做空评分：${s.short_score}\n信心：${s.confidence}\nRSI14：${fmt(s.rsi, 2)}\nATR：${fmt(s.atr, 8)}\nEMA20 / 50 / 200：${fmt(s.ema20,8)} / ${fmt(s.ema50,8)} / ${fmt(s.ema200,8)}\n参考入场：${fmt(s.entry,8)}\n止损：${fmt(s.stop,8)}\n目标：${fmt(s.target,8)}\n原因：${(s.reasons || []).join("；") || "无强方向"}\n数据提示：${(j.data_warnings || []).join("；") || "无"}`;\n  } catch (e) {\n    $("analysis").textContent = `错误：${e.message}`;\n  }\n};\n\n$("backtestBtn").onclick = async () => {\n  $("backtest").textContent = "正在分批下载历史K线并回测，请稍候…";\n  const body = {\n    contract: $("contract").value.trim(),\n    interval: $("interval").value,\n    start: $("start").value,\n    end: $("end").value,\n    threshold: Number($("threshold").value),\n    fee_rate: Number($("fee").value),\n    slippage_rate: Number($("slippage").value),\n    risk_fraction: Number($("risk").value),\n    max_holding_bars: 24\n  };\n  try {\n    const r = await fetch("/api/backtest", {\n      method: "POST",\n      headers: {"Content-Type": "application/json"},\n      body: JSON.stringify(body)\n    });\n    const j = await r.json();\n    if (!r.ok) throw new Error(j.detail || "回测失败");\n    const x = j.result;\n    lastBacktestPayload = j;\n    drawEquityChart(x.trades_detail || []);\n    const pf = x.profit_factor === null ? "—" :\n      (x.profit_factor === Infinity ? "∞" : fmt(x.profit_factor, 2));\n    $("backtest").textContent =\n`交易对：${x.contract}  周期：${x.interval}\n区间：${x.start} 至 ${x.end}\nK线数：${x.bars}\n交易数：${x.trades}\n胜 / 负：${x.wins} / ${x.losses}\n胜率：${fmt(x.win_rate_pct,2)}%\n净收益：${fmt(x.net_return_pct,2)}%\n最大回撤：${fmt(x.max_drawdown_pct,2)}%\nProfit Factor：${pf}\n单笔期望：${fmt(x.expectancy_pct,3)}%\n平均盈利：${fmt(x.average_win_pct,3)}%\n平均亏损：${fmt(x.average_loss_pct,3)}%\nSharpe-like：${fmt(x.sharpe_like,2)}\n双边手续费：${fmt(x.fees_pct_round_trip,3)}%\n双边滑点：${fmt(x.slippage_pct_round_trip,3)}%\n\n假设：\n- ${(x.assumptions || []).join("\n- ")}\n\n警告：\n- ${[...(x.warnings || []), ...(x.data_warnings || [])].join("\n- ") || "无"}`;\n  } catch (e) {\n    $("backtest").textContent = `错误：${e.message}`;\n  }\n};\n\n\nfunction commonBody() {\n  return {\n    contract: $("contract").value.trim(),\n    interval: $("interval").value,\n    start: $("start").value,\n    end: $("end").value,\n    fee_rate: Number($("fee").value),\n    slippage_rate: Number($("slippage").value)\n  };\n}\n\n$("optimizeBtn").onclick = async () => {\n  $("optimize").textContent = "正在优化…";\n  try {\n    const r = await fetch("/api/optimize", {\n      method: "POST",\n      headers: {"Content-Type": "application/json"},\n      body: JSON.stringify(commonBody())\n    });\n    const j = await r.json();\n    if (!r.ok) throw new Error(j.detail || "优化失败");\n    const b = j.best;\n    $("optimize").textContent =\n`最佳训练参数：\n阈值 ${b.threshold}\n风险 ${(b.risk_fraction * 100).toFixed(1)}%\n持有 ${b.max_holding_bars} 根\n交易 ${b.trades}\n净收益 ${b.net_return_pct.toFixed(2)}%\n回撤 ${b.max_drawdown_pct.toFixed(2)}%\nPF ${b.profit_factor === null ? "—" : Number(b.profit_factor).toFixed(2)}\n\n注意：下一步必须做滚动样本外验证。`;\n  } catch (e) {\n    $("optimize").textContent = `错误：${e.message}`;\n  }\n};\n\n$("wfBtn").onclick = async () => {\n  $("wf").textContent = "正在滚动验证，耗时可能较长…";\n  const body = {\n    ...commonBody(),\n    train_bars: Number($("trainBars").value),\n    test_bars: Number($("testBars").value)\n  };\n  try {\n    const r = await fetch("/api/walk-forward", {\n      method: "POST",\n      headers: {"Content-Type": "application/json"},\n      body: JSON.stringify(body)\n    });\n    const j = await r.json();\n    if (!r.ok) throw new Error(j.detail || "验证失败");\n    const x = j.result;\n    $("wf").textContent =\n`折数：${x.fold_count}\n样本外为正折数：${x.positive_test_folds}\n正收益折数比例：${x.positive_fold_rate_pct.toFixed(2)}%\n样本外平均收益：${x.average_test_return_pct.toFixed(2)}%\n样本外平均回撤：${x.average_test_drawdown_pct.toFixed(2)}%\n样本外交易总数：${x.total_test_trades}`;\n  } catch (e) {\n    $("wf").textContent = `错误：${e.message}`;\n  }\n};\n\n$("mcBtn").onclick = async () => {\n  $("mc").textContent = "正在模拟…";\n  const body = {\n    ...commonBody(),\n    threshold: Number($("threshold").value),\n    risk_fraction: Number($("risk").value),\n    max_holding_bars: 24\n  };\n  try {\n    const r = await fetch("/api/monte-carlo", {\n      method: "POST",\n      headers: {"Content-Type": "application/json"},\n      body: JSON.stringify(body)\n    });\n    const j = await r.json();\n    if (!r.ok) throw new Error(j.detail || "模拟失败");\n    const x = j.result;\n    $("mc").textContent =\n`模拟次数：${x.simulations}\n最终收益5%分位：${x.final_return_p05_pct.toFixed(2)}%\n最终收益中位数：${x.final_return_p50_pct.toFixed(2)}%\n最终收益95%分位：${x.final_return_p95_pct.toFixed(2)}%\n最大回撤中位数：${x.max_drawdown_p50_pct.toFixed(2)}%\n最大回撤95%分位：${x.max_drawdown_p95_pct.toFixed(2)}%`;\n  } catch (e) {\n    $("mc").textContent = `错误：${e.message}`;\n  }\n};\n\n\ndocument.querySelectorAll(".tab").forEach((button) => {\n  button.onclick = () => {\n    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));\n    document.querySelectorAll(".panel").forEach((x) => x.classList.add("hidden"));\n    button.classList.add("active");\n    $(button.dataset.target).classList.remove("hidden");\n  };\n});\n\nlet scanTimer = null;\n\nasync function fetchJsonWithTimeout(url, options={}, timeoutMs=45000) {\n  const controller = new AbortController();\n  const timer = setTimeout(() => controller.abort(), timeoutMs);\n  try {\n    const response = await fetch(url, {...options, signal: controller.signal});\n    const payload = await response.json();\n    if (!response.ok) throw new Error(payload.detail || "请求失败");\n    return payload;\n  } catch (error) {\n    if (error.name === "AbortError") throw new Error("等待服务超过45秒，请点击重新检查后再试");\n    throw error;\n  } finally { clearTimeout(timer); }\n}\n\nasync function runScan() {\n  $("scanBtn").disabled = true;\n  $("scanBtn").textContent = "扫描中…";\n  $("scanResult").textContent = "正在并发扫描；Render休眠后的首次请求可能需要20–45秒…";\n  try {\n    const interval = $("scanInterval").value;\n    const limit = Number($("scanLimit").value);\n    const j = await fetchJsonWithTimeout(`/api/scanner?interval=${interval}&limit=${limit}&min_confidence=55`);\n    const x = j.result;\n    const rows = (x.rows || []).map((row, i) =>\n      `${i+1}. ${row.contract}｜${row.side}｜信心${row.confidence}｜多${row.long_score}/空${row.short_score}｜价格${row.last_price}`\n    ).join("\n");\n    const cacheText = x.cache === "fresh" ? "缓存即时返回" : (x.cache === "stale" ? "最近成功结果" : "实时扫描");\n    $("scanResult").textContent =\n`周期：${x.interval}｜${cacheText}\n请求：${x.requested}\n成功分析：${x.analyzed}\n\n${rows || "没有达到最低评分的信号"}\n\n${x.notice}`;\n  } catch (e) {\n    $("scanResult").textContent = `错误：${e.message}`;\n  } finally {\n    $("scanBtn").disabled = false;\n    $("scanBtn").textContent = "开始扫描";\n  }\n}\n\n$("scanBtn").onclick = runScan;\n$("autoScan").onchange = () => {\n  if (scanTimer) clearInterval(scanTimer);\n  scanTimer = null;\n  if ($("autoScan").checked) {\n    runScan();\n    scanTimer = setInterval(runScan, 30000);\n  }\n};\n\nasync function checkHealth() {\n  $("serviceStatus").textContent = "正在检查…";\n  try {\n    const j = await fetchJsonWithTimeout("/api/health", {}, 45000);\n    $("serviceStatus").textContent = `正常｜V${j.version}｜${j.scan_cache_entries}个扫描缓存`;\n  } catch (e) {\n    $("serviceStatus").textContent = `检查失败：${e.message}`;\n  }\n}\n$("healthBtn").onclick = checkHealth;\ncheckHealth();\n\n$("consensusBtn").onclick = async () => {\n  $("consensusResult").textContent = "正在分析5个周期…";\n  try {\n    const contract = $("contract").value.trim();\n    const r = await fetch(`/api/consensus?contract=${encodeURIComponent(contract)}`);\n    const j = await r.json();\n    if (!r.ok) throw new Error(j.detail || "分析失败");\n    const x = j.result;\n    const details = x.timeframes.map(row =>\n      `${row.interval}：${row.side}｜多${row.long_score}/空${row.short_score}｜信心${row.confidence}`\n    ).join("\n");\n    $("consensusResult").textContent =\n`${x.contract}\n综合方向：${x.side}\n综合多头评分：${x.long_score}\n综合空头评分：${x.short_score}\n\n${details}\n\n${x.method}`;\n  } catch (e) {\n    $("consensusResult").textContent = `错误：${e.message}`;\n  }\n};\n\n$("predictionBtn").onclick = async () => {\n  $("predictionResult").textContent = "正在计算…";\n  const body = {\n    market_price: Number($("marketPrice").value),\n    model_probability: Number($("modelProbability").value),\n    fee_rate: Number($("predictionFee").value),\n    kelly_cap: Number($("kellyCap").value),\n    minimum_edge: 0.05\n  };\n  try {\n    const r = await fetch("/api/prediction-value", {\n      method: "POST",\n      headers: {"Content-Type": "application/json"},\n      body: JSON.stringify(body)\n    });\n    const j = await r.json();\n    if (!r.ok) throw new Error(j.detail || "计算失败");\n    const x = j.result;\n    $("predictionResult").textContent =\n`判断：${x.recommendation}\n市场隐含概率：${(x.market_price*100).toFixed(1)}%\n模型概率：${(x.model_probability*100).toFixed(1)}%\n概率优势：${x.edge_pct_points.toFixed(1)}个百分点\n每份期望值：${x.expected_value_per_share.toFixed(4)}\n期望收益率：${x.expected_roi_pct.toFixed(2)}%\n完整Kelly：${(x.full_kelly_fraction*100).toFixed(2)}%\n建议仓位上限：${(x.capped_kelly_fraction*100).toFixed(2)}%\n\n${x.explanation}\n\n注意：本工具不会替你估计真实概率。`;\n  } catch (e) {\n    $("predictionResult").textContent = `错误：${e.message}`;\n  }\n};\n\n\nlet lastBacktestPayload = null;\n\nfunction drawEquityChart(trades) {\n  const canvas = document.getElementById("equityChart");\n  if (!canvas || !canvas.getContext) return;\n  const ctx = canvas.getContext("2d");\n  ctx.clearRect(0, 0, canvas.width, canvas.height);\n  ctx.fillStyle = "#ffffff";\n  ctx.fillRect(0, 0, canvas.width, canvas.height);\n  ctx.strokeStyle = "#d9dce2";\n  ctx.strokeRect(0, 0, canvas.width, canvas.height);\n\n  const values = [1];\n  let equity = 1;\n  (trades || []).forEach(t => {\n    equity *= Math.max(0, 1 + Number(t.net_return_pct || 0) / 100);\n    values.push(equity);\n  });\n  if (values.length < 2) return;\n\n  const minV = Math.min(...values);\n  const maxV = Math.max(...values);\n  const range = Math.max(maxV - minV, 0.000001);\n  ctx.beginPath();\n  values.forEach((v, i) => {\n    const x = 28 + (canvas.width - 56) * i / (values.length - 1);\n    const y = canvas.height - 28 - (canvas.height - 56) * (v - minV) / range;\n    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);\n  });\n  ctx.strokeStyle = "#20242b";\n  ctx.lineWidth = 2;\n  ctx.stroke();\n  ctx.fillStyle = "#20242b";\n  ctx.font = "16px sans-serif";\n  ctx.fillText(`资金曲线：${values[values.length-1].toFixed(3)}x`, 32, 24);\n}\n\ndocument.getElementById("exportBtn").onclick = () => {\n  if (!lastBacktestPayload) {\n    alert("请先运行一次回测");\n    return;\n  }\n  const html = `<!doctype html><meta charset="utf-8"><title>Gate AI Quant 回测报告</title>\n  <style>body{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;padding:20px;white-space:pre-wrap}h1{margin-bottom:8px}</style>\n  <h1>Gate AI Quant V13 Professional 回测报告</h1>\n  <p>生成时间：${new Date().toLocaleString()}</p>\n  <pre>${JSON.stringify(lastBacktestPayload, null, 2)}</pre>`;\n  const blob = new Blob([html], {type:"text/html;charset=utf-8"});\n  const url = URL.createObjectURL(blob);\n  const a = document.createElement("a");\n  a.href = url;\n  a.download = "gate-ai-backtest-report.html";\n  a.click();\n  URL.revokeObjectURL(url);\n};\n\n</script>\n</body>\n</html>\n'


app = FastAPI(title="Gate AI Quant", version="13.0.0")


class OptimizeRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="15m")
    start: str
    end: str
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)


class WalkForwardRequest(OptimizeRequest):
    train_bars: int = Field(default=1200, ge=500, le=10000)
    test_bars: int = Field(default=400, ge=200, le=5000)


class PredictionRequest(BaseModel):
    market_price: float = Field(ge=0.01, le=0.99)
    model_probability: float = Field(ge=0.0, le=1.0)
    fee_rate: float = Field(default=0.0, ge=0.0, le=0.10)
    kelly_cap: float = Field(default=0.05, gt=0.0, le=0.25)
    minimum_edge: float = Field(default=0.05, ge=0.0, le=0.30)


class BacktestRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="15m")
    start: str
    end: str
    threshold: int = Field(default=72, ge=60, le=90)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)
    risk_fraction: float = Field(default=0.01, gt=0, le=0.05)
    max_holding_bars: int = Field(default=24, ge=1, le=500)


def parse_utc_date(value: str, end_of_day: bool = False) -> int:
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式必须为 YYYY-MM-DD") from exc
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "version": "13.0.0", "scan_cache_entries": len(_scan_result_cache), "server_time": int(time.time())}


@app.get("/api/analyze")
async def analyze_api(
    contract: str = Query(default="BTC_USDT"),
    interval: str = Query(default="15m"),
) -> dict:
    try:
        candles, warnings = await fetch_recent_candles(contract, interval, 300)
        snapshot = build_signal(candles)
        payload = {
            "ok": True,
            "contract": contract.upper(),
            "interval": interval,
            "last_closed_at": candles[-1].t,
            "signal": snapshot_to_dict(snapshot),
            "data_warnings": warnings,
        }
        save_signal(
            contract.upper(),
            interval,
            snapshot.side,
            snapshot.confidence,
            payload,
        )
        return payload
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/backtest")
async def backtest_api(req: BacktestRequest) -> dict:
    try:
        start_ts = parse_utc_date(req.start)
        end_ts = parse_utc_date(req.end, end_of_day=True)
        candles, data_warnings = await fetch_history(
            req.contract,
            req.interval,
            start_ts,
            end_ts,
        )
        result = run_backtest(
            candles,
            threshold=req.threshold,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
            risk_fraction=req.risk_fraction,
            max_holding_bars=req.max_holding_bars,
        )
        payload = result.to_dict()
        payload["data_warnings"] = data_warnings
        payload["contract"] = req.contract.upper()
        payload["interval"] = req.interval
        payload["start"] = req.start
        payload["end"] = req.end
        return {"ok": True, "result": payload}
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/optimize")
async def optimize_api(req: OptimizeRequest) -> dict:
    try:
        candles, data_warnings = await fetch_history(
            req.contract,
            req.interval,
            parse_utc_date(req.start),
            parse_utc_date(req.end, end_of_day=True),
        )
        rows = optimize_parameters(
            candles,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
        )
        return {
            "ok": True,
            "best": rows[0].to_dict() if rows else None,
            "top10": [row.to_dict() for row in rows[:10]],
            "data_warnings": data_warnings,
            "notice": "优化区间属于训练样本，必须再做样本外验证。",
        }
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/walk-forward")
async def walk_forward_api(req: WalkForwardRequest) -> dict:
    try:
        candles, data_warnings = await fetch_history(
            req.contract,
            req.interval,
            parse_utc_date(req.start),
            parse_utc_date(req.end, end_of_day=True),
        )
        result = walk_forward_validate(
            candles,
            train_bars=req.train_bars,
            test_bars=req.test_bars,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
        )
        result["data_warnings"] = data_warnings
        return {"ok": True, "result": result}
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/monte-carlo")
async def monte_carlo_api(req: BacktestRequest) -> dict:
    try:
        candles, _ = await fetch_history(
            req.contract,
            req.interval,
            parse_utc_date(req.start),
            parse_utc_date(req.end, end_of_day=True),
        )
        result = run_backtest(
            candles,
            threshold=req.threshold,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
            risk_fraction=req.risk_fraction,
            max_holding_bars=req.max_holding_bars,
        )
        returns = [
            trade["net_return_pct"] for trade in result.trades_detail
        ]
        simulation = monte_carlo_trade_paths(returns)
        return {"ok": True, "result": simulation}
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/scanner")
async def scanner_api(
    interval: str = Query(default="15m"),
    limit: int = Query(default=12, ge=3, le=30),
    min_confidence: int = Query(default=55, ge=0, le=100),
) -> dict:
    try:
        return {
            "ok": True,
            "result": await scan_market(
                interval=interval,
                limit=limit,
                min_confidence=min_confidence,
            ),
        }
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/consensus")
async def consensus_api(contract: str = Query(default="BTC_USDT")) -> dict:
    try:
        return {"ok": True, "result": await multi_timeframe_consensus(contract)}
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/prediction-value")
async def prediction_value_api(req: PredictionRequest) -> dict:
    try:
        result = analyze_prediction_value(
            market_price=req.market_price,
            model_probability=req.model_probability,
            fee_rate=req.fee_rate,
            kelly_cap=req.kelly_cap,
            minimum_edge=req.minimum_edge,
        )
        return {
            "ok": True,
            "result": result.to_dict(),
            "notice": "本模块只计算价值差，不会自动生成真实概率。",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/signals")
async def signals_api(limit: int = Query(default=30, ge=1, le=200)) -> dict:
    return {"ok": True, "rows": recent_signals(limit)}