from __future__ import annotations

from math import sqrt
from typing import Any

from data_quality import assess_data_quality
from direction_validation import validate_next_bar_direction
from gate_client import Candle, INTERVAL_SECONDS
from strategy import build_signal, snapshot_to_dict


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _consensus_alignment(consensus: dict[str, Any], side: str) -> tuple[float, int, int]:
    rows = consensus.get("timeframes") or []
    if side not in {"LONG", "SHORT"} or not rows:
        return 0.0, 0, len(rows)
    weights = {"5m": 1, "15m": 2, "30m": 2, "1h": 3, "4h": 4, "8h": 4, "1d": 5}
    aligned = 0.0
    total = 0.0
    aligned_count = 0
    for row in rows:
        weight = float(weights.get(str(row.get("interval")), 1))
        total += weight
        if row.get("side") == side:
            aligned += weight
            aligned_count += 1
        elif row.get("side") == "FLAT":
            aligned += 0.35 * weight
    return (aligned / total if total else 0.0), aligned_count, len(rows)


def _grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 75:
        return "B"
    if score >= 65:
        return "C"
    return "D"


def build_decision_engine(
    candles: list[Candle],
    interval: str,
    consensus: dict[str, Any],
    threshold: int = 72,
    sample_size: int = 100,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
    data_warnings: list[str] | None = None,
    account_balance: float = 1000.0,
    max_risk_fraction: float = 0.01,
    kelly_cap: float = 0.05,
    funding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Conservative, auditable decision layer.

    It does not treat a technical score as a probability. Probability is
    estimated from chronological next-bar validation, shrunk toward 50%, then
    discounted for sample size, data quality and multi-timeframe alignment.
    """
    if len(candles) < 300:
        raise ValueError("AI决策引擎至少需要300根K线")
    if interval not in INTERVAL_SECONDS:
        raise ValueError("不支持的周期")

    signal = build_signal(candles[-300:], threshold=threshold)
    quality = assess_data_quality(candles[-300:], interval, data_warnings or [])
    validation = validate_next_bar_direction(
        candles,
        threshold=threshold,
        sample_size=sample_size,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
    )

    if signal.side == "LONG":
        stats = validation["long"]
    elif signal.side == "SHORT":
        stats = validation["short"]
    else:
        stats = validation["overall"]

    samples = int(stats.get("signals", 0))
    empirical = _clamp(float(stats.get("cost_adjusted_accuracy_pct", 0.0)) / 100.0, 0.0, 1.0)
    ci = stats.get("confidence_interval_95_pct", [0.0, 0.0])
    ci_low = _clamp(float(ci[0]) / 100.0, 0.0, 1.0)
    ci_high = _clamp(float(ci[1]) / 100.0, 0.0, 1.0)

    sample_reliability = min(1.0, samples / 120.0)
    quality_factor = _clamp(float(quality.get("score", 0.0)) / 100.0, 0.0, 1.0)
    alignment, aligned_count, timeframe_count = _consensus_alignment(consensus, signal.side)

    # Conservative anchor: lower of point estimate and Wilson lower bound.
    conservative_empirical = min(empirical, ci_low) if samples >= 30 else 0.5
    reliability = sample_reliability * (0.60 + 0.40 * quality_factor) * (0.70 + 0.30 * alignment)
    calibrated_probability = 0.5 + (conservative_empirical - 0.5) * reliability

    # Technical score can only make a small adjustment; it never becomes a probability.
    technical_edge = (signal.confidence - 50.0) / 100.0
    if signal.side in {"LONG", "SHORT"}:
        calibrated_probability += _clamp(technical_edge, -0.08, 0.08) * 0.20
    calibrated_probability = _clamp(calibrated_probability, 0.35, 0.75)

    blockers: list[str] = []
    warnings: list[str] = []
    positives: list[str] = []

    funding = funding or {}
    funding_rate = funding.get("funding_rate")
    funding_adjustment = 0.0
    funding_note = "资金费率数据暂不可用，不阻止其他指标运行"
    if funding_rate is not None:
        rate = float(funding_rate)
        funding_note = f"资金费率{rate * 100:+.4f}%（{funding.get('crowding', '中性')}）"
        # Crowding is a risk adjustment, not a standalone reversal signal.
        if signal.side == "LONG" and rate >= 0.0003:
            funding_adjustment = -0.045
            warnings.append("正资金费率极端，多头拥挤，降低追多概率")
        elif signal.side == "LONG" and rate >= 0.0001:
            funding_adjustment = -0.020
            warnings.append("正资金费率偏高，多头存在拥挤风险")
        elif signal.side == "SHORT" and rate <= -0.0003:
            funding_adjustment = -0.045
            warnings.append("负资金费率极端，空头拥挤，降低追空概率")
        elif signal.side == "SHORT" and rate <= -0.0001:
            funding_adjustment = -0.020
            warnings.append("负资金费率偏低，空头存在轧空风险")
        elif signal.side == "LONG" and rate < 0:
            funding_adjustment = 0.012
            positives.append("价格偏多但资金费率为负，未出现多头拥挤")
        elif signal.side == "SHORT" and rate > 0:
            funding_adjustment = 0.012
            positives.append("价格偏空但资金费率为正，未出现空头拥挤")

    calibrated_probability = _clamp(calibrated_probability + funding_adjustment, 0.35, 0.75)
    positives.append(funding_note)

    rr = float(signal.risk_reward or 0.0)
    expected_r = calibrated_probability * rr - (1.0 - calibrated_probability) if rr > 0 else -1.0
    full_kelly = max(0.0, expected_r / rr) if rr > 0 else 0.0
    quarter_kelly = full_kelly * 0.25
    final_risk_fraction = min(quarter_kelly, kelly_cap, max_risk_fraction)

    if signal.side not in {"LONG", "SHORT"}:
        blockers.append("当前技术评分没有形成明确方向")
    if samples < 30:
        blockers.append(f"同方向历史样本仅{samples}次，低于最低30次")
    if quality_factor < 0.75:
        blockers.append(f"数据质量评分{quality.get('score', 0)}，低于75")
    if ci_low <= 0.50:
        blockers.append(f"95%置信区间下限仅{ci_low * 100:.1f}%，未证明稳定优势")
    if alignment < 0.55:
        blockers.append(f"多周期共振仅{alignment * 100:.0f}%，方向冲突明显")
    if expected_r <= 0:
        blockers.append(f"成本后期望值为{expected_r:.2f}R，不具正期望")
    if signal.risk_level == "高":
        warnings.append("当前波动风险较高，需降低仓位或等待波动回落")
    if signal.volatility_percentile >= 90:
        warnings.append("ATR波动分位位于历史高位")
    if signal.confidence < threshold:
        warnings.append("规则模型评分低于当前阈值")

    if signal.side in {"LONG", "SHORT"}:
        positives.append(f"主周期方向为{signal.side}，规则评分{signal.confidence}/100")
    positives.append(f"成本后历史有效率{empirical * 100:.1f}%，样本{samples}次")
    positives.append(f"多周期对齐度{alignment * 100:.0f}%（{aligned_count}/{timeframe_count}个周期同向）")
    positives.append(f"数据质量{quality.get('grade', '—')}，评分{quality.get('score', 0)}")

    if blockers:
        action = "WAIT"
        action_zh = "观望"
    elif calibrated_probability >= 0.60 and expected_r >= 0.15:
        action = signal.side
        action_zh = "条件做多" if signal.side == "LONG" else "条件做空"
    else:
        action = "WAIT"
        action_zh = "等待更强优势"
        warnings.append("各项门槛虽通过，但校准概率或期望值仍不够高")

    probability_strength = _clamp((calibrated_probability - 0.50) / 0.25, 0.0, 1.0)
    confidence_score = (
        30.0 * probability_strength
        + 20.0 * sample_reliability
        + 20.0 * quality_factor
        + 20.0 * alignment
        + 10.0 * _clamp(max(expected_r, 0.0) / 0.8, 0.0, 1.0)
    )
    if blockers:
        confidence_score = min(confidence_score, 64.0)
    elif action == "WAIT":
        confidence_score = min(confidence_score, 74.0)
    confidence_score = _clamp(confidence_score, 0.0, 100.0)

    # Uncertainty widens when sample/data/consensus reliability is poor.
    base_se = sqrt(max(calibrated_probability * (1 - calibrated_probability), 1e-9) / max(samples, 1))
    uncertainty_multiplier = 1.0 + (1.0 - sample_reliability) + (1.0 - quality_factor) + (1.0 - alignment)
    margin = 1.96 * base_se * min(2.0, uncertainty_multiplier)
    probability_interval = [
        _clamp(calibrated_probability - margin, 0.0, 1.0) * 100.0,
        _clamp(calibrated_probability + margin, 0.0, 1.0) * 100.0,
    ]

    return {
        "version": "7.2.0",
        "action": action,
        "action_zh": action_zh,
        "grade": _grade(confidence_score),
        "decision_score": round(confidence_score, 1),
        "signal": snapshot_to_dict(signal),
        "calibration": {
            "empirical_cost_adjusted_probability": empirical,
            "wilson_interval": [ci_low, ci_high],
            "sample_reliability": sample_reliability,
            "quality_factor": quality_factor,
            "timeframe_alignment": alignment,
            "calibrated_probability": calibrated_probability,
            "probability_interval_pct": probability_interval,
            "samples": samples,
        },
        "economics": {
            "risk_reward": rr,
            "expected_r": expected_r,
            "full_kelly_fraction": full_kelly,
            "quarter_kelly_fraction": quarter_kelly,
            "final_risk_fraction": final_risk_fraction,
            "max_loss": account_balance * final_risk_fraction,
        },
        "quality": quality,
        "consensus": consensus,
        "funding": funding,
        "funding_probability_adjustment": funding_adjustment,
        "positives": positives,
        "warnings": warnings,
        "blockers": blockers,
        "explanation": (
            "只有技术方向、成本后历史验证、置信区间、多周期共振、数据质量和正期望同时通过，"
            "系统才允许输出条件交易；否则统一返回WAIT。"
        ),
        "notice": "校准概率来自历史样本并做保守折扣，不是未来收益保证；系统不连接账户、不自动下单。",
    }
