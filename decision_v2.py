from __future__ import annotations

from hashlib import sha256
from statistics import mean
from time import time
from typing import Any

from gate_client import Candle

EMA_CONFIG = {
    "5m": [9, 21, 55, 200],
    "15m": [20, 50, 100, 200],
    "30m": [20, 50, 100, 200],
    "1h": [20, 50, 200],
    "4h": [20, 50, 200],
    "1d": [20, 60, 120, 250],
}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    value = values[0]
    for x in values[1:]:
        value = alpha * x + (1.0 - alpha) * value
    return value


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for x in values[1:]:
        out.append(alpha * x + (1.0 - alpha) * out[-1])
    return out


def _vote(name: str, score: float, reasons: list[str], available: bool = True, weight: float = 0.0) -> dict[str, Any]:
    score = _clamp(score)
    side = "LONG" if score >= 56 else "SHORT" if score <= 44 else "FLAT"
    confidence = abs(score - 50.0) * 2.0
    return {
        "model": name,
        "side": side,
        "long_score": round(score, 1),
        "short_score": round(100.0 - score, 1),
        "confidence": round(confidence, 1),
        "reasons": reasons,
        "available": available,
        "weight": round(weight, 3),
    }


def _trend_vote(signal: Any, candles: list[Candle], interval: str) -> dict[str, Any]:
    closes = [float(c.c) for c in candles]
    periods = EMA_CONFIG.get(interval, [20, 50, 200])
    emas = {p: _ema(closes[-max(300, p * 3):], p) for p in periods}
    price = closes[-1]
    ordered_up = all(emas[a] > emas[b] for a, b in zip(periods, periods[1:]))
    ordered_down = all(emas[a] < emas[b] for a, b in zip(periods, periods[1:]))
    score = 50.0
    reasons: list[str] = ["动态EMA周期：" + "/".join(map(str, periods))]
    if price > max(emas.values()) and ordered_up:
        score += 30; reasons.append("价格位于EMA集群上方且多头排列")
    elif price < min(emas.values()) and ordered_down:
        score -= 30; reasons.append("价格位于EMA集群下方且空头排列")
    else:
        score += 10 if price > emas[periods[min(1, len(periods)-1)]] else -10
        reasons.append("EMA尚未完全排列，趋势证据有限")

    fast = periods[0]
    series = _ema_series(closes[-max(80, fast * 5):], fast)
    slope = ((series[-1] / series[-6]) - 1.0) if len(series) >= 6 and series[-6] else 0.0
    score += _clamp(slope * 4500.0, -15, 15)
    reasons.append(f"EMA{fast}近5根斜率 {slope*100:+.3f}%")

    spread = (max(emas.values()) - min(emas.values())) / price if price else 0.0
    if spread < 0.0018:
        reasons.append("EMA集群压缩，突破前噪声风险较高")
        score += -4 if score >= 50 else 4
    elif spread > 0.025:
        reasons.append("EMA发散过大，存在追高/追空风险")
        score += -7 if score >= 50 else 7
    else:
        reasons.append("EMA距离处于正常趋势区间")

    adx_boost = min(10.0, max(0.0, (float(signal.adx) - 18.0) * 0.55))
    score += adx_boost if score >= 50 else -adx_boost
    reasons.append(f"ADX {float(signal.adx):.1f}")
    return _vote("趋势引擎", score, reasons, weight=0.25)


def _momentum_vote(signal: Any, candles: list[Candle]) -> dict[str, Any]:
    closes = [float(c.c) for c in candles]
    score = 50.0
    reasons: list[str] = []
    score += _clamp((float(signal.rsi) - 50.0) * 0.65, -22, 22)
    roc5 = closes[-1] / closes[-6] - 1.0 if len(closes) >= 6 else 0.0
    roc14 = closes[-1] / closes[-15] - 1.0 if len(closes) >= 15 else 0.0
    score += _clamp(roc5 * 1300.0, -13, 13)
    score += _clamp(roc14 * 500.0, -8, 8)
    reasons.extend([f"RSI {float(signal.rsi):.1f}", f"ROC5 {roc5*100:+.2f}%", f"ROC14 {roc14*100:+.2f}%"])
    if float(signal.rsi) >= 75:
        score -= 6; reasons.append("RSI过热，降低追多权重")
    elif float(signal.rsi) <= 25:
        score += 6; reasons.append("RSI过冷，降低追空权重")
    return _vote("动量引擎", score, reasons, weight=0.10)


def _volume_vote(signal: Any, candles: list[Candle]) -> dict[str, Any]:
    vols = [float(c.v) for c in candles]
    closes = [float(c.c) for c in candles]
    recent = mean(vols[-5:]) if len(vols) >= 5 else mean(vols)
    base = mean(vols[-30:-5]) if len(vols) >= 30 and mean(vols[-30:-5]) else recent or 1.0
    rel = recent / base if base else 1.0
    signed = sum((1 if closes[i] >= closes[i-1] else -1) * vols[i] for i in range(max(1, len(closes)-20), len(closes)))
    denom = sum(vols[-20:]) or 1.0
    pressure = signed / denom
    score = 50.0 + _clamp(pressure * 30.0, -22, 22)
    trend_dir = 1 if float(signal.long_score) >= float(signal.short_score) else -1
    if rel >= 1.5:
        score += 8 * trend_dir
    elif rel < 0.7:
        score += -5 * trend_dir
    reasons = [f"相对成交量 {rel:.2f}x", f"近20根方向成交量压力 {pressure:+.2f}", f"规则多空分差 {signal.long_score-signal.short_score:+d}"]
    return _vote("成交量引擎", score, reasons, weight=0.10)


def _volatility_vote(signal: Any) -> dict[str, Any]:
    score = 50.0
    reasons: list[str] = []
    p = float(signal.volatility_percentile)
    if p >= 90:
        score += -12 if signal.side == "LONG" else 12 if signal.side == "SHORT" else 0
        reasons.append("ATR分位极高，降低追随当前方向的权重")
    elif p <= 20:
        reasons.append("波动过低，容易出现假突破")
    else:
        score += 7 if signal.side == "LONG" else -7 if signal.side == "SHORT" else 0
        reasons.append("波动处于正常可交易区间")
    reasons.append(f"ATR% {float(signal.atr_pct):.3f}，分位 {p:.1f}")
    return _vote("波动率引擎", score, reasons, weight=0.10)


def _derivatives_vote(funding: dict[str, Any] | None, signal: Any) -> dict[str, Any]:
    funding = funding or {}
    rate = funding.get("funding_rate")
    oi = funding.get("open_interest")
    momentum = float(funding.get("funding_momentum") or 0.0)
    score = 50.0
    reasons: list[str] = []
    available = rate is not None or oi is not None
    if rate is None:
        reasons.append("资金费率不可用")
    else:
        rate = float(rate)
        score += _clamp(-rate * 100000.0, -20, 20)
        score += _clamp(-momentum * 70000.0, -8, 8)
        reasons.append(f"资金费率 {rate*100:+.4f}%")
        reasons.append(f"资金费率动量 {momentum*100:+.4f}%")
        reasons.append("极端正费率抑制追多，极端负费率抑制追空")
    if oi is None:
        reasons.append("OI不可用，不虚构OI变化")
    else:
        reasons.append(f"当前未平仓量 {float(oi):,.0f}")
        reasons.append("当前接口仅提供OI快照，尚不把缺失的OI变化当成趋势证据")
    return _vote("衍生品引擎", score, reasons, available=available, weight=0.25)


def build_model_votes(signal: Any, candles: list[Candle], funding: dict[str, Any] | None, interval: str) -> list[dict[str, Any]]:
    return [
        _trend_vote(signal, candles, interval),
        _momentum_vote(signal, candles),
        _volume_vote(signal, candles),
        _volatility_vote(signal),
        _derivatives_vote(funding, signal),
        _vote("链上引擎", 50.0, ["未配置可靠链上数据源，本模块保持中性且不参与方向加分"], available=False, weight=0.05),
        _vote("订单流引擎", 50.0, ["未配置逐笔成交/深度历史源，本模块保持中性且不伪造盘口优势"], available=False, weight=0.05),
    ]


def classify_market_regime(signal: Any) -> dict[str, Any]:
    raw = str(signal.regime or "中性")
    if signal.volatility_percentile >= 90 or signal.atr_pct >= 3.0:
        code, label = "HIGH_VOLATILITY", "高波动"
    elif signal.adx >= 25 and "上涨" in raw:
        code, label = "TREND_UP", "趋势上涨"
    elif signal.adx >= 25 and "下跌" in raw:
        code, label = "TREND_DOWN", "趋势下跌"
    elif signal.adx < 18 or "震荡" in raw:
        code, label = "RANGE", "震荡"
    else:
        code, label = "TRANSITION", "过渡状态"
    return {"code": code, "label": label, "adx": round(float(signal.adx), 1), "atr_pct": round(float(signal.atr_pct), 3), "volatility_percentile": round(float(signal.volatility_percentile), 1)}


def _dynamic_weights(regime: dict[str, Any]) -> dict[str, float]:
    code = regime["code"]
    if code in {"TREND_UP", "TREND_DOWN"}:
        return {"趋势引擎": .30, "动量引擎": .10, "成交量引擎": .10, "波动率引擎": .08, "衍生品引擎": .27, "链上引擎": .08, "订单流引擎": .07}
    if code == "RANGE":
        return {"趋势引擎": .16, "动量引擎": .18, "成交量引擎": .15, "波动率引擎": .13, "衍生品引擎": .20, "链上引擎": .08, "订单流引擎": .10}
    if code == "HIGH_VOLATILITY":
        return {"趋势引擎": .18, "动量引擎": .08, "成交量引擎": .12, "波动率引擎": .22, "衍生品引擎": .25, "链上引擎": .08, "订单流引擎": .07}
    return {"趋势引擎": .24, "动量引擎": .12, "成交量引擎": .12, "波动率引擎": .12, "衍生品引擎": .25, "链上引擎": .08, "订单流引擎": .07}


def aggregate_votes(votes: list[dict[str, Any]], regime: dict[str, Any]) -> dict[str, Any]:
    weights = _dynamic_weights(regime)
    active = [v for v in votes if v.get("available", True)]
    active_weight = sum(weights.get(v["model"], 0.0) for v in active) or 1.0
    long_score = sum(v["long_score"] * weights.get(v["model"], 0.0) for v in active) / active_weight
    intended = "LONG" if long_score >= 50 else "SHORT"
    agreement = sum(weights.get(v["model"], 0.0) for v in active if v["side"] == intended) / active_weight
    side = "LONG" if long_score >= 56 else "SHORT" if long_score <= 44 else "FLAT"
    return {
        "side": side,
        "long_score": round(long_score, 1),
        "short_score": round(100 - long_score, 1),
        "agreement": round(agreement, 2),
        "dynamic_weights": {k: round(v, 3) for k, v in weights.items()},
        "active_models": len(active),
        "total_models": len(votes),
    }


def build_risk_engine(signal: Any, quality: dict[str, Any], calibration: dict[str, Any], funding: dict[str, Any] | None, voting: dict[str, Any]) -> dict[str, Any]:
    score = 18.0
    reasons: list[str] = []
    if signal.volatility_percentile >= 90:
        score += 32; reasons.append("ATR波动分位≥90")
    elif signal.volatility_percentile >= 75:
        score += 18; reasons.append("ATR波动分位偏高")
    if float(quality.get("score", 0)) < 75:
        score += 25; reasons.append("数据质量低于75")
    if float(calibration.get("timeframe_alignment", 0)) < 0.55:
        score += 18; reasons.append("多周期方向冲突")
    if int(calibration.get("samples", 0)) < 30:
        score += 20; reasons.append("样本不足30")
    if float(voting.get("agreement", 0)) < 0.45:
        score += 14; reasons.append("多因子模型一致度偏低")
    rate = (funding or {}).get("funding_rate")
    if rate is not None and abs(float(rate)) >= 0.0003:
        score += 15; reasons.append("资金费率处于极端拥挤区")
    score = _clamp(score)
    if score >= 70:
        level, trade_allowed = "HIGH", False
    elif score >= 42:
        level, trade_allowed = "MEDIUM", True
    else:
        level, trade_allowed = "LOW", True
    if not reasons:
        reasons.append("未发现显著异常风险")
    return {"score": round(score, 1), "level": level, "trade_allowed": trade_allowed, "reasons": reasons}


def _quality_grade(voting: dict[str, Any], risk: dict[str, Any], calibration: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    p = float(calibration.get("calibrated_probability", 0.5))
    score = (
        max(0.0, (p - 0.5) / 0.25) * 35
        + float(voting.get("agreement", 0)) * 25
        + float(quality.get("score", 0)) / 100 * 20
        + (100 - float(risk.get("score", 100))) / 100 * 20
    )
    score = _clamp(score)
    grade = "S" if score >= 88 else "A" if score >= 78 else "B" if score >= 66 else "C" if score >= 52 else "D"
    return {"score": round(score, 1), "grade": grade, "tradeable": grade in {"S", "A", "B"} and risk.get("trade_allowed", False)}


def build_v10_layer(signal: Any, candles: list[Candle], quality: dict[str, Any], calibration: dict[str, Any], funding: dict[str, Any] | None, contract: str = "", interval: str = "15m") -> dict[str, Any]:
    regime = classify_market_regime(signal)
    votes = build_model_votes(signal, candles, funding, interval)
    voting = aggregate_votes(votes, regime)
    risk = build_risk_engine(signal, quality, calibration, funding, voting)
    strategy_quality = _quality_grade(voting, risk, calibration, quality)
    explain = []
    weights = voting["dynamic_weights"]
    for vote in votes:
        direction = vote["long_score"] - 50.0
        explain.append({
            "factor": vote["model"],
            "contribution": round(direction * weights.get(vote["model"], 0.0), 2),
            "raw_bias": round(direction, 1),
            "weight": weights.get(vote["model"], 0.0),
            "available": vote.get("available", True),
            "side": vote["side"],
            "confidence": vote["confidence"],
            "reason": "；".join(vote["reasons"]),
        })
    ts = int(time())
    dna_payload = f"{contract}|{interval}|{ts}|{signal.side}|{regime['code']}|{voting['long_score']}|{risk['score']}"
    return {
        "model_version": "11.0.0",
        "models": votes,
        "voting": voting,
        "market_regime": regime,
        "risk_engine": risk,
        "strategy_quality": strategy_quality,
        "data_sources": {
            "price_volume": True,
            "funding": (funding or {}).get("funding_rate") is not None,
            "open_interest_snapshot": (funding or {}).get("open_interest") is not None,
            "onchain": False,
            "order_flow": False,
            "note": "未接入的数据源保持中性，不生成伪信号。",
        },
        "explain_ai": explain,
        "trade_dna": {
            "id": sha256(dna_payload.encode()).hexdigest()[:16],
            "created_at": ts,
            "technical_side": signal.side,
            "voting_side": voting["side"],
            "regime": regime["code"],
            "risk_level": risk["level"],
            "strategy_grade": strategy_quality["grade"],
            "features": {
                "ema_periods": EMA_CONFIG.get(interval, [20, 50, 200]),
                "rsi": round(float(signal.rsi), 2),
                "adx": round(float(signal.adx), 2),
                "atr_pct": round(float(signal.atr_pct), 4),
                "volatility_percentile": round(float(signal.volatility_percentile), 2),
                "rule_long_score": signal.long_score,
                "rule_short_score": signal.short_score,
                "timeframe_alignment": round(float(calibration.get("timeframe_alignment", 0)), 4),
                "data_quality": round(float(quality.get("score", 0)), 1),
                "funding_rate": (funding or {}).get("funding_rate"),
                "open_interest": (funding or {}).get("open_interest"),
            },
        },
    }
