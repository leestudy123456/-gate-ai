from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backtest import run_backtest
from gate_client import GateDataError, fetch_history, fetch_recent_candles
from strategy import build_signal, snapshot_to_dict

app = FastAPI(title="Gate AI Quant", version="2.0.0")
BASE = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


class BacktestRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="15m")
    start: str
    end: str
    threshold: int = Field(default=72, ge=60, le=90)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)
    risk_fraction: float = Field(default=0.01, gt=0, le=0.05)
    max_holding_bars: int = Field(default=24, ge=1, le=500)


def parse_utc_date(value: str, end_of_day: bool = False) -> int:
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式必须为 YYYY-MM-DD") from exc
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return HTMLResponse((BASE / "templates" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "version": "2.0.0"}


@app.get("/api/analyze")
async def analyze_api(
    contract: str = Query(default="BTC_USDT"),
    interval: str = Query(default="15m"),
) -> dict:
    try:
        candles, warnings = await fetch_recent_candles(contract, interval, 300)
        snapshot = build_signal(candles)
        return {
            "ok": True,
            "contract": contract.upper(),
            "interval": interval,
            "last_closed_at": candles[-1].t,
            "signal": snapshot_to_dict(snapshot),
            "data_warnings": warnings,
        }
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/backtest")
async def backtest_api(req: BacktestRequest) -> dict:
    try:
        start_ts = parse_utc_date(req.start)
        end_ts = parse_utc_date(req.end, end_of_day=True)
        candles, data_warnings = await fetch_history(
            req.contract,
            req.interval,
            start_ts,
            end_ts,
        )
        result = run_backtest(
            candles,
            threshold=req.threshold,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
            risk_fraction=req.risk_fraction,
            max_holding_bars=req.max_holding_bars,
        )
        payload = result.to_dict()
        payload["data_warnings"] = data_warnings
        payload["contract"] = req.contract.upper()
        payload["interval"] = req.interval
        payload["start"] = req.start
        payload["end"] = req.end
        return {"ok": True, "result": payload}
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
