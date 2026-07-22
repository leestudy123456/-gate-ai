from __future__ import annotations


def calculate_position(*, account_balance: float, risk_fraction: float, side: str, entry: float, stop: float,
                       leverage: float, fee_rate: float, slippage_rate: float,
                       max_margin_fraction: float = 0.95, max_notional_multiple: float = 1.0) -> dict:
    side = side.upper().strip()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("交易方向必须是 LONG 或 SHORT")
    if side == "LONG" and stop >= entry:
        raise ValueError("做多时止损价必须低于入场价")
    if side == "SHORT" and stop <= entry:
        raise ValueError("做空时止损价必须高于入场价")
    if min(account_balance, entry, stop, leverage) <= 0:
        raise ValueError("账户、价格和杠杆必须大于0")

    max_loss = account_balance * risk_fraction
    price_risk = abs(entry - stop)
    round_trip_cost_per_unit = entry * 2.0 * (fee_rate + slippage_rate)
    effective_risk_per_unit = price_risk + round_trip_cost_per_unit
    risk_quantity = max_loss / effective_risk_per_unit

    max_margin = account_balance * max_margin_fraction
    leverage_notional_cap = max_margin * leverage * max_notional_multiple
    leverage_quantity = leverage_notional_cap / entry
    quantity = min(risk_quantity, leverage_quantity)
    notional = quantity * entry
    estimated_margin = notional / leverage
    estimated_cost = quantity * round_trip_cost_per_unit
    estimated_price_loss = quantity * price_risk
    worst_case_loss = estimated_price_loss + estimated_cost
    stop_distance_pct = price_risk / entry * 100.0
    margin_usage_pct = estimated_margin / account_balance * 100.0

    limiting_factor = "风险预算" if risk_quantity <= leverage_quantity else "保证金/杠杆上限"
    suggested_leverage = max(1.0, min(10.0, notional / max(account_balance * 0.35, 1e-12)))
    warnings: list[str] = []
    if stop_distance_pct < 0.25:
        warnings.append("止损距离过窄，容易被正常波动和交易成本触发。")
    if stop_distance_pct > 8:
        warnings.append("止损距离较宽，建议重新检查周期、入场和止损位置。")
    if leverage > 20:
        warnings.append("杠杆高于20倍，强平与滑点风险显著增加。")
    if margin_usage_pct > 70:
        warnings.append("预计保证金占用超过账户70%，组合风险偏高。")

    safety_score = 100
    safety_score -= max(0, leverage - 5) * 2
    safety_score -= 20 if margin_usage_pct > 70 else 10 if margin_usage_pct > 50 else 0
    safety_score -= 15 if stop_distance_pct < 0.25 else 0
    safety_score = max(0, min(100, round(safety_score)))
    safety_level = "低" if safety_score < 50 else "中" if safety_score < 75 else "较高"

    return {
        "side": side,
        "account_balance": account_balance,
        "risk_fraction": risk_fraction,
        "max_loss": max_loss,
        "entry": entry,
        "stop": stop,
        "stop_distance": price_risk,
        "stop_distance_pct": stop_distance_pct,
        "cost_per_unit": round_trip_cost_per_unit,
        "effective_risk_per_unit": effective_risk_per_unit,
        "risk_quantity": risk_quantity,
        "leverage_quantity_cap": leverage_quantity,
        "quantity": quantity,
        "notional": notional,
        "estimated_margin": estimated_margin,
        "margin_usage_pct": margin_usage_pct,
        "estimated_round_trip_cost": estimated_cost,
        "worst_case_loss": worst_case_loss,
        "leverage": leverage,
        "suggested_leverage": round(suggested_leverage, 1),
        "limiting_factor": limiting_factor,
        "safety_score": safety_score,
        "safety_level": safety_level,
        "warnings": warnings,
    }
