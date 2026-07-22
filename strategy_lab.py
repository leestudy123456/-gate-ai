from __future__ import annotations

from collections import defaultdict
from typing import Any

from simulator import get_trade, list_trades


def _safe_float(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bucket_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r.get("status") == "CLOSED"]
    wins = [r for r in closed if _safe_float(r.get("net_pnl")) > 0]
    losses = [r for r in closed if _safe_float(r.get("net_pnl")) <= 0]
    gp = sum(_safe_float(r.get("net_pnl")) for r in wins)
    gl = abs(sum(_safe_float(r.get("net_pnl")) for r in losses))
    return {
        "trades": len(closed),
        "wins": len(wins),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "net_pnl": round(sum(_safe_float(r.get("net_pnl")) for r in closed), 4),
        "average_r": round(sum(_safe_float(r.get("r_multiple")) for r in closed) / len(closed), 3) if closed else 0.0,
        "profit_factor": round(gp / gl, 3) if gl else (999.0 if gp else None),
        "average_mfe_pct": round(sum(_safe_float(r.get("max_favorable_excursion")) for r in closed) / len(closed) * 100, 3) if closed else 0.0,
        "average_mae_pct": round(sum(_safe_float(r.get("max_adverse_excursion")) for r in closed) / len(closed) * 100, 3) if closed else 0.0,
    }


def performance(limit: int = 500) -> dict[str, Any]:
    trades = list_trades(limit)
    closed = sorted([t for t in trades if t.get("status") == "CLOSED"], key=lambda x: int(x.get("closed_at") or x.get("created_at") or 0))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    curve = []
    win_streak = loss_streak = max_win_streak = max_loss_streak = 0
    by_grade: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_side: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for t in closed:
        pnl = _safe_float(t.get("net_pnl"))
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        curve.append({"time": t.get("closed_at"), "equity": round(equity, 4), "trade_id": t.get("id")})
        if pnl > 0:
            win_streak += 1; loss_streak = 0; max_win_streak = max(max_win_streak, win_streak)
        else:
            loss_streak += 1; win_streak = 0; max_loss_streak = max(max_loss_streak, loss_streak)
        strategy = t.get("strategy") or {}
        grade = strategy.get("strategy_quality", {}).get("grade") or strategy.get("grade") or "未知"
        regime = strategy.get("market_regime", {}).get("label") or strategy.get("market_regime", {}).get("code") or "未知"
        by_grade[str(grade)].append(t)
        by_regime[str(regime)].append(t)
        by_side[str(t.get("side") or "未知")].append(t)

    return {
        "overall": _bucket_stats(trades),
        "max_drawdown_usdt": round(max_dd, 4),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "equity_curve": curve[-200:],
        "by_grade": {k: _bucket_stats(v) for k, v in sorted(by_grade.items())},
        "by_regime": {k: _bucket_stats(v) for k, v in sorted(by_regime.items())},
        "by_side": {k: _bucket_stats(v) for k, v in sorted(by_side.items())},
        "methodology": "仅统计本地模拟交易；费用和滑点已计入。样本不足时不应据此推断未来胜率。",
    }


def replay(trade_id: str) -> dict[str, Any]:
    trade = get_trade(trade_id)
    strategy = trade.get("strategy") or {}
    return {
        "trade": trade,
        "decision": {
            "action": strategy.get("action"),
            "action_zh": strategy.get("action_zh"),
            "decision_score": strategy.get("decision_score"),
            "grade": strategy.get("grade"),
            "strategy_quality": strategy.get("strategy_quality"),
            "calibration": strategy.get("calibration"),
            "market_regime": strategy.get("market_regime"),
            "risk_engine": strategy.get("risk_engine"),
            "voting": strategy.get("voting"),
            "models": strategy.get("models"),
            "explain_ai": strategy.get("explain_ai"),
            "trade_dna": strategy.get("trade_dna"),
            "data_sources": strategy.get("data_sources"),
        },
        "timeline": [
            {"event": "CREATED", "time": trade.get("created_at"), "price": trade.get("requested_entry")},
            {"event": "OPENED", "time": trade.get("opened_at"), "price": trade.get("fill_price")},
            {"event": trade.get("exit_reason") or "OPEN", "time": trade.get("closed_at") or trade.get("last_checked_at"), "price": trade.get("exit_price") or trade.get("last_mark")},
        ],
        "integrity_note": "回放显示创建模拟交易时保存的决策快照，不使用事后指标重写当时结论。",
    }
