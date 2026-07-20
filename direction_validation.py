from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt

from gate_client import Candle
from strategy import build_signal


@dataclass(frozen=True)
class DirectionObservation:
    signal_time: int
    outcome_time: int
    side: str
    score: int
    current_close: float
    next_close: float
    move_pct: float
    direction_correct: bool
    cost_adjusted_correct: bool


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return max(0.0, centre - margin) * 100, min(1.0, centre + margin) * 100


def validate_next_bar_direction(
    candles: list[Candle],
    threshold: int = 72,
    sample_size: int = 100,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
) -> dict:
    """Out-of-sample-like chronological next-bar direction audit.

    At each timestamp the model sees only candles up to that closed bar. The
    following bar close is then used as the outcome. No future bar is included
    in signal construction. The most recent `sample_size` actionable signals
    are reported.
    """
    if len(candles) < 260:
        raise ValueError("方向验证至少需要260根K线")
    if not 20 <= sample_size <= 1000:
        raise ValueError("样本数应在20到1000之间")

    observations: list[DirectionObservation] = []
    round_trip_cost = 2 * (fee_rate + slippage_rate)

    for i in range(219, len(candles) - 1):
        signal = build_signal(candles[: i + 1], threshold=threshold)
        if signal.side not in {"LONG", "SHORT"}:
            continue
        current = candles[i]
        nxt = candles[i + 1]
        raw_move = (nxt.c / current.c - 1.0) if current.c else 0.0
        signed_move = raw_move if signal.side == "LONG" else -raw_move
        observations.append(
            DirectionObservation(
                signal_time=current.t,
                outcome_time=nxt.t,
                side=signal.side,
                score=signal.confidence,
                current_close=current.c,
                next_close=nxt.c,
                move_pct=raw_move * 100,
                direction_correct=signed_move > 0,
                cost_adjusted_correct=signed_move > round_trip_cost,
            )
        )

    selected = observations[-sample_size:]
    if len(selected) < 20:
        raise ValueError(f"有效方向信号不足：仅{len(selected)}次；请扩大日期范围或降低阈值")

    def stats(side: str | None = None) -> dict:
        rows = selected if side is None else [x for x in selected if x.side == side]
        correct = sum(x.direction_correct for x in rows)
        cost_correct = sum(x.cost_adjusted_correct for x in rows)
        lo, hi = _wilson_interval(correct, len(rows))
        return {
            "signals": len(rows),
            "correct": correct,
            "accuracy_pct": correct / len(rows) * 100 if rows else 0.0,
            "cost_adjusted_correct": cost_correct,
            "cost_adjusted_accuracy_pct": cost_correct / len(rows) * 100 if rows else 0.0,
            "confidence_interval_95_pct": [lo, hi],
        }

    total = stats()
    long_stats = stats("LONG")
    short_stats = stats("SHORT")
    consecutive_losses = 0
    max_consecutive_losses = 0
    for row in selected:
        if row.direction_correct:
            consecutive_losses = 0
        else:
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

    eligible_bars = max(1, len(candles) - 220)
    return {
        "requested_samples": sample_size,
        "used_samples": len(selected),
        "threshold": threshold,
        "definition": "信号在当前已收盘K线后生成，用下一根K线收盘方向验证",
        "cost_definition": "手续费后有效要求方向收益超过双边手续费与双边滑点",
        "round_trip_cost_pct": round_trip_cost * 100,
        "overall": total,
        "long": long_stats,
        "short": short_stats,
        "signal_coverage_pct": len(observations) / eligible_bars * 100,
        "max_consecutive_wrong": max_consecutive_losses,
        "observations": [asdict(x) for x in selected[-100:]],
        "notice": "这是历史下一根K线方向命中率，不保证未来结果；样本重叠且市场状态会变化。",
    }
