from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Any

from gate_client import GateDataError, _request_json, fetch_recent_candles, normalize_contract
from strategy import build_signal


@dataclass
class ScanRow:
    contract: str
    interval: str
    side: str
    long_score: int
    short_score: int
    confidence: int
    liquidity_24h: float
    last_price: float
    entry: float | None
    stop: float | None
    target: float | None
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


_ticker_cache: dict[str, tuple[float, Any]] = {}
_scan_cache: dict[str, tuple[float, dict]] = {}
_scan_inflight: dict[str, asyncio.Task] = {}
TICKER_CACHE_SECONDS = 55
SCAN_CACHE_SECONDS = 45
STALE_SCAN_SECONDS = 180


async def fetch_futures_tickers() -> list[dict[str, Any]]:
    key = "futures_tickers"
    now = time.time()
    cached = _ticker_cache.get(key)
    if cached and now - cached[0] < TICKER_CACHE_SECONDS:
        return cached[1]

    payload = await _request_json("/futures/usdt/tickers", cache_seconds=TICKER_CACHE_SECONDS)
    if not isinstance(payload, list):
        raise GateDataError("Gate合约行情列表格式异常")
    _ticker_cache[key] = (time.time(), payload)
    return payload


def _liquidity_value(item: dict[str, Any]) -> float:
    candidates = ("volume_24h_quote", "volume_24h_settle", "volume_24h_usd", "volume_24h")
    for field in candidates:
        try:
            value = float(item.get(field, 0) or 0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    try:
        return float(item.get("volume_24h_base", 0) or 0) * float(item.get("last", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def top_liquid_contracts(
    tickers: list[dict[str, Any]], limit: int = 12, min_liquidity: float = 0
) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for item in tickers:
        contract = str(item.get("contract", "")).upper()
        if not contract.endswith("_USDT"):
            continue
        liquidity = _liquidity_value(item)
        if liquidity >= min_liquidity:
            rows.append((contract, liquidity))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows[:limit]


async def _scan_one(
    contract: str, interval: str, liquidity: float, semaphore: asyncio.Semaphore
) -> ScanRow | None:
    async with semaphore:
        try:
            candles, warnings = await asyncio.wait_for(
                fetch_recent_candles(contract, interval, 300), timeout=7.5
            )
            snapshot = build_signal(candles)
            return ScanRow(
                contract=contract,
                interval=interval,
                side=snapshot.side,
                long_score=snapshot.long_score,
                short_score=snapshot.short_score,
                confidence=snapshot.confidence,
                liquidity_24h=liquidity,
                last_price=candles[-1].c,
                entry=snapshot.entry,
                stop=snapshot.stop,
                target=snapshot.target,
                reasons=snapshot.reasons,
                warnings=warnings,
            )
        except Exception:
            return None


async def _compute_scan(interval: str, limit: int, min_confidence: int) -> dict:
    tickers = await asyncio.wait_for(fetch_futures_tickers(), timeout=7.0)
    candidates = top_liquid_contracts(tickers, limit=limit)
    # More parallelism materially improves mobile scan latency while the
    # per-request timeout and shared Gate request de-duplication prevent piling up.
    semaphore = asyncio.Semaphore(min(8, max(4, limit)))
    tasks = [_scan_one(c, interval, liq, semaphore) for c, liq in candidates]
    try:
        rows = await asyncio.wait_for(asyncio.gather(*tasks), timeout=12.0)
    except asyncio.TimeoutError:
        rows = []

    valid = [row for row in rows if row is not None and row.confidence >= min_confidence]
    valid.sort(
        key=lambda row: (row.side != "FLAT", row.confidence, row.liquidity_24h),
        reverse=True,
    )
    analyzed = len([row for row in rows if row is not None])
    if analyzed == 0:
        raise GateDataError("Gate扫描响应较慢，请稍后重试")
    return {
        "interval": interval,
        "requested": limit,
        "analyzed": analyzed,
        "rows": [row.to_dict() for row in valid],
        "cached": False,
        "age_seconds": 0,
        "notice": "排行榜仅表示规则评分，不代表未来胜率。",
    }


async def scan_market(
    interval: str = "15m", limit: int = 12, min_confidence: int = 55
) -> dict:
    if not 3 <= limit <= 30:
        raise ValueError("扫描数量应在3到30之间")
    if interval not in {"5m", "15m", "30m", "1h", "4h"}:
        raise ValueError("暂不支持该周期")

    key = f"{interval}:{limit}:{min_confidence}"
    now = time.time()
    cached = _scan_cache.get(key)
    if cached and now - cached[0] < SCAN_CACHE_SECONDS:
        result = dict(cached[1])
        result["cached"] = True
        result["age_seconds"] = round(now - cached[0], 1)
        return result

    existing = _scan_inflight.get(key)
    if existing and not existing.done():
        try:
            return await asyncio.wait_for(asyncio.shield(existing), timeout=13.0)
        except asyncio.TimeoutError:
            if cached and now - cached[0] < STALE_SCAN_SECONDS:
                result = dict(cached[1])
                result["cached"] = True
                result["stale"] = True
                result["age_seconds"] = round(now - cached[0], 1)
                return result
            raise GateDataError("扫描仍在后台进行，请稍后再试")

    async def run() -> dict:
        result = await _compute_scan(interval, limit, min_confidence)
        _scan_cache[key] = (time.time(), result)
        return result

    task = asyncio.create_task(run())
    _scan_inflight[key] = task
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=13.5)
    except asyncio.TimeoutError:
        if cached and now - cached[0] < STALE_SCAN_SECONDS:
            result = dict(cached[1])
            result["cached"] = True
            result["stale"] = True
            result["age_seconds"] = round(now - cached[0], 1)
            return result
        raise GateDataError("扫描超过13秒，Gate接口暂时较慢")
    finally:
        if _scan_inflight.get(key) is task and task.done():
            _scan_inflight.pop(key, None)


async def multi_timeframe_consensus(contract: str) -> dict:
    contract = normalize_contract(contract)
    intervals = ("5m", "15m", "30m", "1h", "4h")

    async def one(interval: str) -> dict:
        try:
            candles, warnings = await asyncio.wait_for(
                fetch_recent_candles(contract, interval, 300), timeout=8.0
            )
            signal = build_signal(candles)
            return {
                "interval": interval,
                "side": signal.side,
                "long_score": signal.long_score,
                "short_score": signal.short_score,
                "confidence": signal.confidence,
                "warnings": warnings,
                "ok": True,
            }
        except Exception as exc:
            return {
                "interval": interval,
                "side": "UNAVAILABLE",
                "long_score": 0,
                "short_score": 0,
                "confidence": 0,
                "warnings": [f"{interval}读取失败：{exc}"],
                "ok": False,
            }

    results = await asyncio.gather(*(one(interval) for interval in intervals))
    valid = [x for x in results if x["ok"]]
    if len(valid) < 2:
        raise GateDataError("Gate多周期行情暂时响应缓慢，请稍后重试")

    weights = {"5m": 1, "15m": 2, "30m": 2, "1h": 3, "4h": 4}
    used_weight = sum(weights[x["interval"]] for x in valid)
    long_avg = round(sum(x["long_score"] * weights[x["interval"]] for x in valid) / used_weight)
    short_avg = round(sum(x["short_score"] * weights[x["interval"]] for x in valid) / used_weight)

    side = "FLAT"
    if long_avg >= 70 and long_avg - short_avg >= 12:
        side = "LONG"
    elif short_avg >= 70 and short_avg - long_avg >= 12:
        side = "SHORT"

    return {
        "contract": contract,
        "side": side,
        "long_score": long_avg,
        "short_score": short_avg,
        "timeframes": results,
        "partial": len(valid) != len(results),
        "successful_timeframes": len(valid),
        "method": "1h与4h权重高于短周期；单个周期超时不会阻塞整页。",
    }
