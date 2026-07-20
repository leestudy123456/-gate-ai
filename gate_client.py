from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://api.gateio.ws/api/v4"
INTERVAL_SECONDS = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
MAX_PAGE_BARS = 1000


class GateDataError(RuntimeError):
    """Raised when Gate market data is missing, malformed, or unavailable."""


@dataclass(frozen=True)
class Candle:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float


def normalize_contract(contract: str) -> str:
    value = contract.strip().upper().replace("-", "_").replace("/", "_")
    if not value or "_" not in value:
        raise GateDataError("交易对格式应类似 BTC_USDT")
    return value


def _parse_candle(item: dict[str, Any]) -> Candle:
    try:
        candle = Candle(
            t=int(float(item["t"])),
            o=float(item["o"]),
            h=float(item["h"]),
            l=float(item["l"]),
            c=float(item["c"]),
            v=float(item.get("v", 0.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GateDataError(f"Gate K线字段解析失败：{exc}") from exc

    if min(candle.o, candle.h, candle.l, candle.c) <= 0:
        raise GateDataError("Gate返回了非正价格")
    if candle.h < max(candle.o, candle.c) or candle.l > min(candle.o, candle.c):
        raise GateDataError("Gate返回的K线OHLC关系异常")
    if candle.v < 0:
        raise GateDataError("Gate返回了负成交量")
    return candle


def _validate_sequence(candles: list[Candle], interval: str) -> list[str]:
    warnings: list[str] = []
    seconds = INTERVAL_SECONDS[interval]
    for prev, cur in zip(candles, candles[1:]):
        gap = cur.t - prev.t
        if gap <= 0:
            raise GateDataError("K线时间未严格递增")
        if gap > seconds * 1.5:
            warnings.append(f"检测到历史数据缺口：{prev.t} → {cur.t}")
    return warnings


async def _get(params: dict[str, str]) -> list[dict[str, Any]]:
    timeout = httpx.Timeout(20.0, connect=8.0)
    url = f"{BASE_URL}/futures/usdt/candlesticks"
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": "gate-ai-quant/4.0"},
                )
            if response.status_code == 429:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code != 200:
                raise GateDataError(
                    f"Gate接口返回 {response.status_code}：{response.text[:250]}"
                )
            payload = response.json()
            if not isinstance(payload, list):
                raise GateDataError("Gate返回格式异常")
            return payload
        except (httpx.HTTPError, ValueError, GateDataError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.8 * (attempt + 1))

    raise GateDataError(f"连接Gate行情接口失败：{last_error}")


def _clean(payload: list[dict[str, Any]], interval: str, closed_only: bool) -> tuple[list[Candle], list[str]]:
    candles = sorted((_parse_candle(x) for x in payload), key=lambda x: x.t)
    deduped = {x.t: x for x in candles}
    candles = [deduped[t] for t in sorted(deduped)]

    if closed_only:
        now = int(time.time())
        seconds = INTERVAL_SECONDS[interval]
        candles = [x for x in candles if x.t + seconds <= now + 2]

    warnings = _validate_sequence(candles, interval)
    return candles, warnings


async def fetch_recent_candles(
    contract: str,
    interval: str,
    limit: int = 300,
) -> tuple[list[Candle], list[str]]:
    if interval not in INTERVAL_SECONDS:
        raise GateDataError("暂不支持该周期")
    if not 50 <= limit <= MAX_PAGE_BARS:
        raise GateDataError(f"limit应在50到{MAX_PAGE_BARS}之间")

    params = {
        "contract": normalize_contract(contract),
        "interval": interval,
        "limit": str(limit),
    }
    payload = await _get(params)
    candles, warnings = _clean(payload, interval, closed_only=True)
    if len(candles) < 220:
        raise GateDataError(f"有效已收盘K线不足：仅 {len(candles)} 根")
    return candles, warnings


async def fetch_history(
    contract: str,
    interval: str,
    start_ts: int,
    end_ts: int,
    max_bars: int = 30000,
) -> tuple[list[Candle], list[str]]:
    """Download historical closed candles in non-overlapping chunks.

    The method deduplicates timestamps, sorts ascending, validates OHLC, and
    rejects an excessive request before downloading.
    """
    if interval not in INTERVAL_SECONDS:
        raise GateDataError("暂不支持该周期")
    if start_ts >= end_ts:
        raise GateDataError("开始时间必须早于结束时间")

    seconds = INTERVAL_SECONDS[interval]
    estimated = (end_ts - start_ts) // seconds + 1
    if estimated > max_bars:
        raise GateDataError(
            f"请求约{estimated}根K线，超过当前安全上限{max_bars}根；请缩短日期范围"
        )

    contract = normalize_contract(contract)
    cursor = start_ts
    merged: dict[int, Candle] = {}
    warnings: list[str] = []

    while cursor < end_ts:
        chunk_end = min(end_ts, cursor + seconds * (MAX_PAGE_BARS - 1))
        # Gate rejects requests that contain limit together with from/to.
        # Use bounded from/to windows only; each window is kept below the
        # endpoint's maximum page size, then merged and deduplicated locally.
        params = {
            "contract": contract,
            "interval": interval,
            "from": str(cursor),
            "to": str(chunk_end),
        }
        payload = await _get(params)
        page, page_warnings = _clean(payload, interval, closed_only=True)
        warnings.extend(page_warnings)
        for candle in page:
            if start_ts <= candle.t <= end_ts:
                merged[candle.t] = candle
        cursor = chunk_end + seconds
        await asyncio.sleep(0.06)

    candles = [merged[t] for t in sorted(merged)]
    warnings.extend(_validate_sequence(candles, interval))
    if len(candles) < 260:
        raise GateDataError(f"回测数据不足：仅获取{len(candles)}根已收盘K线")
    return candles, sorted(set(warnings))
