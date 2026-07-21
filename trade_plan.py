from __future__ import annotations

from gate_client import Candle
from strategy import build_signal, snapshot_to_dict
from direction_validation import validate_next_bar_direction
from data_quality import assess_data_quality
from gate_client import INTERVAL_SECONDS
import time


def build_trade_plan(
    candles: list[Candle],
    interval: str,
    threshold: int = 72,
    sample_size: int = 100,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
    data_warnings: list[str] | None = None,
    account_balance: float = 1000.0,
    max_risk_fraction: float = 0.01,
    kelly_cap: float = 0.05,
) -> dict:
    if len(candles) < 300:
        raise ValueError("生成交易计划至少需要300根K线")

    signal = build_signal(candles[-300:], threshold=threshold)
    validation = validate_next_bar_direction(
        candles,
        threshold=threshold,
        sample_size=sample_size,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
    )
    quality = assess_data_quality(candles[-300:], interval, data_warnings or [])

    side_stats = validation["long"] if signal.side == "LONG" else validation["short"] if signal.side == "SHORT" else validation["overall"]
    accuracy = float(side_stats.get("accuracy_pct", 0.0))
    cost_accuracy = float(side_stats.get("cost_adjusted_accuracy_pct", 0.0))
    ci = side_stats.get("confidence_interval_95_pct", [0.0, 0.0])
    side_samples = int(side_stats.get("signals", 0))


    # Adjusted Kelly: use the weaker of cost-adjusted hit rate and the 95% CI
    # lower bound, then discount the edge toward 50% according to data quality.
    raw_p = max(0.0, min(1.0, cost_accuracy / 100.0))
    lower_p = max(0.0, min(1.0, float(ci[0]) / 100.0))
    conservative_p = min(raw_p, lower_p) if side_samples >= 20 else 0.5
    quality_factor = 0.70 + 0.30 * (float(quality.get("score", 0)) / 100.0)
    adjusted_p = 0.5 + (conservative_p - 0.5) * quality_factor
    rr = float(signal.risk_reward or 0.0)
    full_kelly = max(0.0, (rr * adjusted_p - (1.0 - adjusted_p)) / rr) if rr > 0 else 0.0
    quarter_kelly = full_kelly * 0.25
    final_risk_fraction = min(quarter_kelly, kelly_cap, max_risk_fraction)
    risk_budget = account_balance * final_risk_fraction
    generated_at = int(time.time())
    seconds = INTERVAL_SECONDS[interval]
    expires_at = ((generated_at // seconds) + 1) * seconds

    action = "WAIT"
    action_zh = "观望"
    rationale: list[str] = []
    if quality.get("score", 0) < 70:
        rationale.append("行情数据质量不足，暂不生成入场建议")
    elif signal.side not in {"LONG", "SHORT"}:
        rationale.append("当前多空评分未形成足够明显的方向优势")
    elif side_samples < 20:
        rationale.append("同方向历史样本不足20次，概率可信度较低")
    elif signal.confidence < threshold:
        rationale.append("当前模型评分未达到设定阈值")
    elif cost_accuracy < 50:
        rationale.append("历史上扣除手续费与滑点后的有效率不足50%")
    elif float(ci[0]) < 50:
        rationale.append("95%置信区间下限仍低于50%，优势不够稳定")
    else:
        action = signal.side
        action_zh = "条件做多" if signal.side == "LONG" else "条件做空"
        rationale.append("当前方向、评分、数据质量与历史验证同时达到最低条件")

    entry = signal.entry
    stop = signal.stop
    target = signal.target
    if action == "WAIT":
        entry_note = "等待下一次已收盘K线重新确认，不追价"
    else:
        entry_note = f"仅在价格接近参考入场位 {entry:.4f} 且方向条件仍成立时考虑" if entry else "等待明确入场位"

    exit_rules = []
    if stop is not None:
        exit_rules.append(f"止损离场：价格触及 {stop:.4f}")
    if target is not None:
        exit_rules.append(f"止盈离场：价格触及 {target:.4f}")
    exit_rules.append("时间离场：下一根K线结束后重新评估；信号反转则提前退出")
    exit_rules.append("失效离场：数据质量下降、方向评分跌破阈值或多周期方向明显冲突")

    return {
        "action": action,
        "action_zh": action_zh,
        "side": signal.side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_reward": signal.risk_reward,
        "model_score": signal.confidence,
        "historical_hit_rate_pct": accuracy,
        "cost_adjusted_hit_rate_pct": cost_accuracy,
        "historical_samples": side_samples,
        "confidence_interval_95_pct": ci,
        "data_quality": quality,
        "generated_at": generated_at,
        "expires_at": expires_at,
        "risk_budget": {
            "account_balance": account_balance,
            "input_cost_adjusted_probability": raw_p,
            "conservative_probability": conservative_p,
            "adjusted_probability": adjusted_p,
            "full_kelly_fraction": full_kelly,
            "quarter_kelly_fraction": quarter_kelly,
            "final_risk_fraction": final_risk_fraction,
            "max_loss": risk_budget,
            "max_risk_fraction": max_risk_fraction,
            "kelly_cap": kelly_cap,
        },
        "entry_instruction": entry_note,
        "exit_rules": exit_rules,
        "rationale": rationale + signal.factor_details[:5],
        "signal": snapshot_to_dict(signal),
        "probability_label": "同方向历史下一根K线命中率",
        "notice": "这是基于公开历史数据的研究计划，不是保证收益或个性化投资建议。历史命中率会随市场状态变化。",
    }
