from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from statistics import fmean

from gate_client import Candle


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
