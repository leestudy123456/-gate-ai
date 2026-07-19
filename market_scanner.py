from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from gate_client import BASE_URL, GateDataError, fetch_recent_candles, normalize_contract
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


_cache: dict[str, tuple[float, Any]] = {}
CACHE_SECONDS = 55


async def fetch_futures_tickers() -> list[dict[str, Any]]:
    key = "futures_tickers"
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    url = f"{BASE_URL}/futures/usdt/tickers"
    timeout = httpx.Timeout(20.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={"Accept": "application/json", "User-Agent": "gate-ai-quant-v5"},
        )
    if response.status_code != 200:
        raise GateDataError(f"Gate合约行情列表返回{response.status_code}")
    payload = response.json()
    if not isinstance(payload, list):
        raise GateDataError("Gate合约行情列表格式异常")

    _cache[key] = (now, payload)
    return payload


def _liquidity_value(item: dict[str, Any]) -> float:
    # Gate versions may expose different 24h volume field names.
    candidates = (
        "volume_24h_quote",
        "volume_24h_settle",
        "volume_24h_usd",
        "volume_24h",
    )
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
    tickers: list[dict[str, Any]],
    limit: int = 12,
    min_liquidity: float = 0,
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
    contract: str,
    interval: str,
    liquidity: float,
    semaphore: asyncio.Semaphore,
) -> ScanRow | None:
    async with semaphore:
        try:
            candles, warnings = await fetch_recent_candles(contract, interval, 300)
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
        except (GateDataError, ValueError):
            return None


async def scan_market(
    interval: str = "15m",
    limit: int = 12,
    min_confidence: int = 55,
) -> dict:
    if not 3 <= limit <= 30:
        raise ValueError("扫描数量应在3到30之间")
    if interval not in {"5m", "15m", "30m", "1h", "4h"}:
        raise ValueError("暂不支持该周期")

    tickers = await fetch_futures_tickers()
    candidates = top_liquid_contracts(tickers, limit=limit)
    semaphore = asyncio.Semaphore(4)
    rows = await asyncio.gather(
        *[
            _scan_one(contract, interval, liquidity, semaphore)
            for contract, liquidity in candidates
        ]
    )
    valid = [row for row in rows if row is not None and row.confidence >= min_confidence]
    valid.sort(
        key=lambda row: (
            row.side != "FLAT",
            row.confidence,
            row.liquidity_24h,
        ),
        reverse=True,
    )
    return {
        "interval": interval,
        "requested": limit,
        "analyzed": len([row for row in rows if row is not None]),
        "rows": [row.to_dict() for row in valid],
        "notice": "排行榜仅表示规则评分，不代表未来胜率。",
    }


async def multi_timeframe_consensus(contract: str) -> dict:
    contract = normalize_contract(contract)
    intervals = ("5m", "15m", "30m", "1h", "4h")
    semaphore = asyncio.Semaphore(3)

    async def one(interval: str) -> dict:
        async with semaphore:
            candles, warnings = await fetch_recent_candles(contract, interval, 300)
            signal = build_signal(candles)
            return {
                "interval": interval,
                "side": signal.side,
                "long_score": signal.long_score,
                "short_score": signal.short_score,
                "confidence": signal.confidence,
                "warnings": warnings,
            }

    results = await asyncio.gather(*(one(interval) for interval in intervals))
    weights = {"5m": 1, "15m": 2, "30m": 2, "1h": 3, "4h": 4}
    long_total = sum(x["long_score"] * weights[x["interval"]] for x in results)
    short_total = sum(x["short_score"] * weights[x["interval"]] for x in results)
    weight_total = sum(weights.values())
    long_avg = round(long_total / weight_total)
    short_avg = round(short_total / weight_total)

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
        "method": "1h与4h权重高于短周期，5m主要用于入场时机。",
    }
