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
from optimizer import optimize_parameters
from validation import walk_forward_validate, monte_carlo_trade_paths
from market_scanner import scan_market, multi_timeframe_consensus
from prediction_value import analyze_prediction_value
from signal_store import save_signal, recent_signals

app = FastAPI(title="Gate AI Quant", version="5.0.0")
BASE = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


class OptimizeRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="15m")
    start: str
    end: str
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)


class WalkForwardRequest(OptimizeRequest):
    train_bars: int = Field(default=1200, ge=500, le=10000)
    test_bars: int = Field(default=400, ge=200, le=5000)


class PredictionRequest(BaseModel):
    market_price: float = Field(ge=0.01, le=0.99)
    model_probability: float = Field(ge=0.0, le=1.0)
    fee_rate: float = Field(default=0.0, ge=0.0, le=0.10)
    kelly_cap: float = Field(default=0.05, gt=0.0, le=0.25)
    minimum_edge: float = Field(default=0.05, ge=0.0, le=0.30)


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
    return {"ok": True, "version": "5.0.0"}


@app.get("/api/analyze")
async def analyze_api(
    contract: str = Query(default="BTC_USDT"),
    interval: str = Query(default="15m"),
) -> dict:
    try:
        candles, warnings = await fetch_recent_candles(contract, interval, 300)
        snapshot = build_signal(candles)
        payload = {
            "ok": True,
            "contract": contract.upper(),
            "interval": interval,
            "last_closed_at": candles[-1].t,
            "signal": snapshot_to_dict(snapshot),
            "data_warnings": warnings,
        }
        save_signal(
            contract.upper(),
            interval,
            snapshot.side,
            snapshot.confidence,
            payload,
        )
        return payload
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


@app.post("/api/optimize")
async def optimize_api(req: OptimizeRequest) -> dict:
    try:
        candles, data_warnings = await fetch_history(
            req.contract,
            req.interval,
            parse_utc_date(req.start),
            parse_utc_date(req.end, end_of_day=True),
        )
        rows = optimize_parameters(
            candles,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
        )
        return {
            "ok": True,
            "best": rows[0].to_dict() if rows else None,
            "top10": [row.to_dict() for row in rows[:10]],
            "data_warnings": data_warnings,
            "notice": "优化区间属于训练样本，必须再做样本外验证。",
        }
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/walk-forward")
async def walk_forward_api(req: WalkForwardRequest) -> dict:
    try:
        candles, data_warnings = await fetch_history(
            req.contract,
            req.interval,
            parse_utc_date(req.start),
            parse_utc_date(req.end, end_of_day=True),
        )
        result = walk_forward_validate(
            candles,
            train_bars=req.train_bars,
            test_bars=req.test_bars,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
        )
        result["data_warnings"] = data_warnings
        return {"ok": True, "result": result}
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/monte-carlo")
async def monte_carlo_api(req: BacktestRequest) -> dict:
    try:
        candles, _ = await fetch_history(
            req.contract,
            req.interval,
            parse_utc_date(req.start),
            parse_utc_date(req.end, end_of_day=True),
        )
        result = run_backtest(
            candles,
            threshold=req.threshold,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
            risk_fraction=req.risk_fraction,
            max_holding_bars=req.max_holding_bars,
        )
        returns = [
            trade["net_return_pct"] for trade in result.trades_detail
        ]
        simulation = monte_carlo_trade_paths(returns)
        return {"ok": True, "result": simulation}
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/scanner")
async def scanner_api(
    interval: str = Query(default="15m"),
    limit: int = Query(default=12, ge=3, le=30),
    min_confidence: int = Query(default=55, ge=0, le=100),
) -> dict:
    try:
        return {
            "ok": True,
            "result": await scan_market(
                interval=interval,
                limit=limit,
                min_confidence=min_confidence,
            ),
        }
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/consensus")
async def consensus_api(contract: str = Query(default="BTC_USDT")) -> dict:
    try:
        return {"ok": True, "result": await multi_timeframe_consensus(contract)}
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/prediction-value")
async def prediction_value_api(req: PredictionRequest) -> dict:
    try:
        result = analyze_prediction_value(
            market_price=req.market_price,
            model_probability=req.model_probability,
            fee_rate=req.fee_rate,
            kelly_cap=req.kelly_cap,
            minimum_edge=req.minimum_edge,
        )
        return {
            "ok": True,
            "result": result.to_dict(),
            "notice": "本模块只计算价值差，不会自动生成真实概率。",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/signals")
async def signals_api(limit: int = Query(default=30, ge=1, le=200)) -> dict:
    return {"ok": True, "rows": recent_signals(limit)}
