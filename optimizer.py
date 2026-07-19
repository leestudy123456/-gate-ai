from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product

from backtest import run_backtest
from gate_client import Candle


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
