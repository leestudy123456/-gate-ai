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


def _stats(rows: list[DirectionObservation], side: str | None = None) -> dict:
    chosen = rows if side is None else [x for x in rows if x.side == side]
    correct = sum(x.direction_correct for x in chosen)
    cost_correct = sum(x.cost_adjusted_correct for x in chosen)
    lo, hi = _wilson_interval(cost_correct, len(chosen))
    return {
        "signals": len(chosen),
        "correct": correct,
        "accuracy_pct": correct / len(chosen) * 100 if chosen else 0.0,
        "cost_adjusted_correct": cost_correct,
        "cost_adjusted_accuracy_pct": cost_correct / len(chosen) * 100 if chosen else 0.0,
        "confidence_interval_95_pct": [lo, hi],
    }


def _score_bins(rows: list[DirectionObservation]) -> list[dict]:
    result: list[dict] = []
    for low, high in ((60, 69), (70, 79), (80, 89), (90, 100)):
        bucket = [x for x in rows if low <= x.score <= high]
        if not bucket:
            continue
        stats = _stats(bucket)
        result.append({"range": f"{low}-{high}", **stats})
    return result


def validate_next_bar_direction(
    candles: list[Candle],
    threshold: int = 72,
    sample_size: int = 100,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0002,
    test_fraction: float = 0.35,
) -> dict:
    """Chronological, no-look-ahead next-bar audit with a held-out tail sample.

    Every signal is built using only candles available at that timestamp. The
    latest part of the selected observations is reserved as a strict holdout
    set. Decision logic should use the holdout results, not the full sample.
    """
    if len(candles) < 260:
        raise ValueError("方向验证至少需要260根K线")
    if not 20 <= sample_size <= 1000:
        raise ValueError("样本数应在20到1000之间")
    if not 0.2 <= test_fraction <= 0.5:
        raise ValueError("测试集比例应在20%到50%之间")

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

    test_count = max(20, int(round(len(selected) * test_fraction)))
    test_count = min(test_count, max(1, len(selected) - 20))
    train_rows = selected[:-test_count]
    test_rows = selected[-test_count:]

    consecutive_losses = 0
    max_consecutive_losses = 0
    for row in test_rows:
        if row.cost_adjusted_correct:
            consecutive_losses = 0
        else:
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

    eligible_bars = max(1, len(candles) - 220)
    return {
        "requested_samples": sample_size,
        "used_samples": len(selected),
        "threshold": threshold,
        "definition": "每个信号只使用当时已收盘K线，下一根K线收盘作为结果",
        "split_definition": "按时间顺序切分，最后35%为严格样本外测试集",
        "cost_definition": "方向收益必须超过双边手续费与双边滑点才算成本后正确",
        "round_trip_cost_pct": round_trip_cost * 100,
        "overall": _stats(selected),
        "long": _stats(selected, "LONG"),
        "short": _stats(selected, "SHORT"),
        "train": {
            "samples": len(train_rows),
            "overall": _stats(train_rows),
            "long": _stats(train_rows, "LONG"),
            "short": _stats(train_rows, "SHORT"),
            "score_bins": _score_bins(train_rows),
        },
        "out_of_sample": {
            "samples": len(test_rows),
            "overall": _stats(test_rows),
            "long": _stats(test_rows, "LONG"),
            "short": _stats(test_rows, "SHORT"),
            "score_bins": _score_bins(test_rows),
        },
        "signal_coverage_pct": len(observations) / eligible_bars * 100,
        "max_consecutive_wrong_oos": max_consecutive_losses,
        "observations": [asdict(x) for x in test_rows[-100:]],
        "notice": "决策引擎只使用样本外结果。即使样本外为正，也不保证未来盈利。",
    }
