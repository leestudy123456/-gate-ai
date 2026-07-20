from __future__ import annotations

import time
from statistics import median

from gate_client import Candle, INTERVAL_SECONDS


def assess_data_quality(candles: list[Candle], interval: str, warnings: list[str] | None = None) -> dict:
    """Transparent quality report for closed-candle analysis.

    The score measures data completeness/freshness/plausibility only. It is not a
    forecast probability and must not be interpreted as expected trading accuracy.
    """
    if not candles:
        return {"score": 0, "grade": "D", "status": "不可用", "issues": ["没有K线数据"], "metrics": {}}

    seconds = INTERVAL_SECONDS[interval]
    issues = list(warnings or [])
    timestamps = [c.t for c in candles]
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    missing_estimate = sum(max(0, round(g / seconds) - 1) for g in gaps if g > seconds * 1.5)
    duplicate_count = len(timestamps) - len(set(timestamps))
    zero_volume_count = sum(1 for c in candles if c.v <= 0)

    returns = [abs(candles[i].c / candles[i - 1].c - 1.0) for i in range(1, len(candles)) if candles[i - 1].c > 0]
    typical_move = median(returns) if returns else 0.0
    outlier_threshold = max(typical_move * 12.0, 0.08)
    outlier_count = sum(1 for r in returns if r > outlier_threshold)

    age_seconds = max(0, int(time.time()) - (candles[-1].t + seconds))
    expected_age = seconds * 2

    score = 100
    score -= min(35, missing_estimate * 5)
    score -= min(20, duplicate_count * 5)
    score -= min(20, zero_volume_count)
    score -= min(20, outlier_count * 5)
    if age_seconds > expected_age:
        score -= min(30, int(age_seconds / seconds) * 5)
        issues.append("最新已收盘K线可能不够新鲜")
    if missing_estimate:
        issues.append(f"估算缺失K线 {missing_estimate} 根")
    if zero_volume_count:
        issues.append(f"零成交量K线 {zero_volume_count} 根")
    if outlier_count:
        issues.append(f"疑似异常跳变 {outlier_count} 处")
    score = max(0, min(100, score))

    if score >= 90:
        grade, status = "A", "优"
    elif score >= 75:
        grade, status = "B", "可用"
    elif score >= 60:
        grade, status = "C", "谨慎"
    else:
        grade, status = "D", "不可用"

    return {
        "score": score,
        "grade": grade,
        "status": status,
        "issues": issues or ["未发现明显数据问题"],
        "metrics": {
            "bars": len(candles),
            "estimated_missing_bars": missing_estimate,
            "duplicate_bars": duplicate_count,
            "zero_volume_bars": zero_volume_count,
            "suspected_outliers": outlier_count,
            "last_closed_age_seconds": age_seconds,
        },
        "notice": "数据质量分只衡量行情数据完整性，不代表信号准确率。",
    }
