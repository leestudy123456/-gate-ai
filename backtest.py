from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from statistics import fmean, pstdev

from gate_client import Candle
from strategy import build_signal


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
