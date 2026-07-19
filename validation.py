from __future__ import annotations

from dataclasses import asdict, dataclass
import random
from statistics import fmean

from backtest import run_backtest
from gate_client import Candle
from optimizer import optimize_parameters


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
