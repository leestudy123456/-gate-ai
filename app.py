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
from data_quality import assess_data_quality
from direction_validation import validate_next_bar_direction
from trade_plan import build_trade_plan
from model_card import model_card

app = FastAPI(title="Gate AI Quant Professional 4.0.0", version="4.0.0")
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



class PositionSizeRequest(BaseModel):
    account_balance: float = Field(gt=0)
    risk_fraction: float = Field(default=0.01, gt=0, le=0.05)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    leverage: float = Field(default=1.0, ge=1.0, le=100.0)



class AdvancedRiskRequest(BaseModel):
    account_balance: float = Field(gt=0)
    win_probability: float = Field(ge=0.01, le=0.99)
    risk_reward: float = Field(gt=0.1, le=10.0)
    max_risk_fraction: float = Field(default=0.01, gt=0, le=0.05)
    kelly_cap: float = Field(default=0.05, gt=0, le=0.25)


class DirectionValidationRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="5m")
    start: str
    end: str
    threshold: int = Field(default=72, ge=60, le=90)
    sample_size: int = Field(default=100, ge=20, le=1000)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)




class TradePlanRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="5m")
    start: str
    end: str
    threshold: int = Field(default=72, ge=60, le=90)
    sample_size: int = Field(default=100, ge=20, le=1000)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)


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
    return {"ok": True, "version": "4.0.0"}


@app.get("/api/model-card")
async def model_card_api() -> dict:
    return {"ok": True, "result": model_card()}


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
            "data_quality": assess_data_quality(candles, interval, warnings),
            "score_notice": "信心值是规则模型评分，不是经过校准的真实胜率。",
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




@app.post("/api/trade-plan")
async def trade_plan_api(req: TradePlanRequest) -> dict:
    try:
        candles, data_warnings = await fetch_history(
            req.contract, req.interval,
            parse_utc_date(req.start),
            parse_utc_date(req.end, end_of_day=True),
        )
        result = build_trade_plan(
            candles, interval=req.interval, threshold=req.threshold,
            sample_size=req.sample_size, fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate, data_warnings=data_warnings,
        )
        result.update({"contract": req.contract.upper(), "interval": req.interval, "start": req.start, "end": req.end})
        return {"ok": True, "result": result}
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


@app.post("/api/direction-validation")
async def direction_validation_api(req: DirectionValidationRequest) -> dict:
    try:
        candles, data_warnings = await fetch_history(
            req.contract,
            req.interval,
            parse_utc_date(req.start),
            parse_utc_date(req.end, end_of_day=True),
        )
        result = validate_next_bar_direction(
            candles,
            threshold=req.threshold,
            sample_size=req.sample_size,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
        )
        result.update({
            "contract": req.contract.upper(),
            "interval": req.interval,
            "start": req.start,
            "end": req.end,
            "data_warnings": data_warnings,
        })
        return {"ok": True, "result": result}
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


@app.get("/api/dashboard")
async def dashboard_api() -> dict:
    recent = recent_signals(100)
    total = len(recent)
    long_count = sum(1 for row in recent if row["side"] == "LONG")
    short_count = sum(1 for row in recent if row["side"] == "SHORT")
    flat_count = sum(1 for row in recent if row["side"] == "FLAT")
    high_confidence = sum(1 for row in recent if int(row["confidence"]) >= 80)
    average_confidence = (
        sum(int(row["confidence"]) for row in recent) / total if total else 0
    )
    return {
        "ok": True,
        "summary": {
            "signals_logged": total,
            "long_signals": long_count,
            "short_signals": short_count,
            "flat_signals": flat_count,
            "high_confidence_signals": high_confidence,
            "average_confidence": round(average_confidence, 2),
        },
        "recent": recent[:10],
        "notice": "统计来自本实例信号日志；Render免费实例重启后日志可能清空。",
    }


@app.post("/api/position-size")
async def position_size_api(req: PositionSizeRequest) -> dict:
    risk_per_unit = abs(req.entry - req.stop)
    if risk_per_unit <= 0:
        raise HTTPException(status_code=400, detail="入场价与止损价不能相同")

    max_loss = req.account_balance * req.risk_fraction
    quantity = max_loss / risk_per_unit
    notional = quantity * req.entry
    estimated_margin = notional / req.leverage

    return {
        "ok": True,
        "result": {
            "account_balance": req.account_balance,
            "risk_fraction": req.risk_fraction,
            "max_loss": max_loss,
            "risk_per_unit": risk_per_unit,
            "quantity": quantity,
            "notional": notional,
            "leverage": req.leverage,
            "estimated_margin": estimated_margin,
        },
        "notice": "未计入强平、资金费率、手续费、滑点和最小下单单位；仅作风险预算参考。",
    }


@app.post("/api/advanced-risk")
async def advanced_risk_api(req: AdvancedRiskRequest) -> dict:
    b = req.risk_reward
    p = req.win_probability
    q = 1.0 - p
    full_kelly = max(0.0, (b * p - q) / b)
    quarter_kelly = full_kelly * 0.25
    capped_kelly = min(quarter_kelly, req.kelly_cap, req.max_risk_fraction)
    max_loss = req.account_balance * capped_kelly
    expected_r = p * b - q

    if expected_r <= 0:
        decision = "无正期望：不建议按Kelly配置仓位"
    elif capped_kelly <= 0.005:
        decision = "优势较弱：仅适合极小风险预算"
    else:
        decision = "存在正期望假设：仓位仍应受样本误差和回撤约束"

    return {
        "ok": True,
        "result": {
            "full_kelly_fraction": full_kelly,
            "quarter_kelly_fraction": quarter_kelly,
            "capped_risk_fraction": capped_kelly,
            "max_loss": max_loss,
            "expected_r": expected_r,
            "decision": decision,
        },
        "notice": "胜率必须来自足够数量的样本外交易；技术评分不能直接当作真实胜率。",
    }


@app.get("/api/professional-overview")
async def professional_overview_api(
    contract: str = Query(default="BTC_USDT"),
) -> dict:
    try:
        consensus = await multi_timeframe_consensus(contract)
        recent = recent_signals(100)
        matching = [x for x in recent if x["contract"] == contract.upper()]
        return {
            "ok": True,
            "contract": contract.upper(),
            "consensus": consensus,
            "signal_history": {
                "count": len(matching),
                "long": sum(1 for x in matching if x["side"] == "LONG"),
                "short": sum(1 for x in matching if x["side"] == "SHORT"),
                "flat": sum(1 for x in matching if x["side"] == "FLAT"),
                "average_confidence": round(
                    sum(x["confidence"] for x in matching) / len(matching), 2
                ) if matching else 0,
            },
            "notice": "历史统计是信号日志，不等于已实现交易胜率。",
        }
    except (GateDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
