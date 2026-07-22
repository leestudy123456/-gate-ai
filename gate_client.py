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
CACHE_SECONDS = 20
_response_cache: dict[str, tuple[float, Any]] = {}
_inflight: dict[str, asyncio.Task] = {}


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


async def _request_json(path: str, params: dict[str, str] | None = None, *, cache_seconds: int = CACHE_SECONDS) -> Any:
    params = params or {}
    key = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    now = time.time()
    cached = _response_cache.get(key)
    if cached and now - cached[0] < cache_seconds:
        return cached[1]

    # Reuse the same in-flight request when the user taps refresh repeatedly.
    existing = _inflight.get(key)
    if existing and not existing.done():
        return await asyncio.shield(existing)

    async def run() -> Any:
        timeout = httpx.Timeout(14.0, connect=5.0, read=10.0, write=5.0, pool=5.0)
        url = f"{BASE_URL}{path}"
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    response = await client.get(url, params=params, headers={
                        "Accept": "application/json",
                        "User-Agent": "gate-ai-quant/7.2",
                        "Cache-Control": "no-cache",
                    })
                if response.status_code == 429:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                if response.status_code != 200:
                    raise GateDataError(f"Gate接口返回 {response.status_code}：{response.text[:180]}")
                payload = response.json()
                _response_cache[key] = (time.time(), payload)
                return payload
            except (httpx.HTTPError, ValueError, GateDataError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
        raise GateDataError(f"连接Gate行情接口失败：{last_error}")

    task = asyncio.create_task(run())
    _inflight[key] = task
    try:
        return await asyncio.shield(task)
    finally:
        if _inflight.get(key) is task and task.done():
            _inflight.pop(key, None)


async def _get(params: dict[str, str]) -> list[dict[str, Any]]:
    payload = await _request_json("/futures/usdt/candlesticks", params)
    if not isinstance(payload, list):
        raise GateDataError("Gate返回格式异常")
    return payload


async def fetch_funding_context(contract: str) -> dict[str, Any]:
    """Fetch current funding, recent funding momentum and open-interest proxy.

    This is a public-data call. Missing optional fields degrade gracefully instead
    of blocking the whole analysis page.
    """
    contract = normalize_contract(contract)
    current: dict[str, Any] = {}
    history: list[dict[str, Any]] = []
    try:
        payload = await _request_json(f"/futures/usdt/contracts/{contract}", cache_seconds=15)
        if isinstance(payload, dict):
            current = payload
    except GateDataError:
        current = {}
    try:
        payload = await _request_json(
            "/futures/usdt/funding_rate",
            {"contract": contract, "limit": "30"},
            cache_seconds=60,
        )
        if isinstance(payload, list):
            history = payload
    except GateDataError:
        history = []

    def num(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    rate = num(current.get("funding_rate"))
    next_rate = num(current.get("funding_next_rate"))
    next_time = current.get("funding_next_apply") or current.get("funding_next_time")
    hist_rates = [num(x.get("r")) for x in history if isinstance(x, dict)]
    hist_rates = [x for x in hist_rates if x is not None]
    momentum = (hist_rates[0] - hist_rates[-1]) if len(hist_rates) >= 2 else 0.0
    oi = num(current.get("position_size") or current.get("open_interest"))

    abs_rate = abs(rate or 0.0)
    level = "正常"
    if abs_rate >= 0.0003:
        level = "极端"
    elif abs_rate >= 0.0001:
        level = "偏高"
    crowding = "中性"
    if rate is not None and rate >= 0.0001:
        crowding = "多头拥挤"
    elif rate is not None and rate <= -0.0001:
        crowding = "空头拥挤"

    return {
        "available": rate is not None,
        "funding_rate": rate,
        "funding_rate_pct": rate * 100 if rate is not None else None,
        "funding_next_rate": next_rate,
        "funding_next_time": int(float(next_time)) if next_time not in (None, "") else None,
        "funding_momentum": momentum,
        "funding_level": level,
        "crowding": crowding,
        "open_interest": oi,
        "history_samples": len(hist_rates),
    }


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
    min_bars: int = 220,
) -> tuple[list[Candle], list[str]]:
    if interval not in INTERVAL_SECONDS:
        raise GateDataError("暂不支持该周期")
    if not 50 <= limit <= MAX_PAGE_BARS:
        raise GateDataError(f"limit应在50到{MAX_PAGE_BARS}之间")
    if not 1 <= min_bars <= limit:
        raise GateDataError("min_bars必须在1到limit之间")

    params = {
        "contract": normalize_contract(contract),
        "interval": interval,
        "limit": str(limit),
    }
    payload = await _get(params)
    candles, warnings = _clean(payload, interval, closed_only=True)
    if len(candles) < min_bars:
        raise GateDataError(f"有效已收盘K线不足：仅 {len(candles)} 根，需要至少 {min_bars} 根")
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
