from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backtest import run_backtest
from gate_client import GateDataError, INTERVAL_SECONDS, fetch_history, fetch_recent_candles, fetch_funding_context
from strategy import build_signal, snapshot_to_dict
from optimizer import optimize_parameters
from validation import walk_forward_validate, monte_carlo_trade_paths
from market_scanner import scan_market, multi_timeframe_consensus
from prediction_value import analyze_prediction_value
from signal_store import initialize as initialize_signal_store, save_signal, recent_signals
from data_quality import assess_data_quality
from direction_validation import validate_next_bar_direction
from trade_plan import build_trade_plan
from model_card import model_card
from decision_engine import build_decision_engine
from simulator import create_trade, evaluate_trade, init_db as initialize_simulation_store, list_trades, manual_close, stats as simulation_stats
from strategy_lab import performance as strategy_lab_performance, replay as strategy_lab_replay
from position_manager import calculate_position
from kline_analysis import analyze_kline

app = FastAPI(title="Gate AI Quant V12.1 Stability Edition Mobile", version="12.1.0")
BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
initialize_signal_store()
initialize_simulation_store()
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
    side: str = Field(default="LONG")
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    leverage: float = Field(default=1.0, ge=1.0, le=100.0)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)
    max_margin_fraction: float = Field(default=0.95, gt=0.05, le=1.0)



class AdvancedRiskRequest(BaseModel):
    account_balance: float = Field(gt=0)
    win_probability: float = Field(ge=0.01, le=0.99)
    confidence_lower: float | None = Field(default=None, ge=0.0, le=0.99)
    data_quality_score: float = Field(default=100.0, ge=0.0, le=100.0)
    risk_reward: float = Field(gt=0.1, le=10.0)
    max_risk_fraction: float = Field(default=0.01, gt=0, le=0.05)
    kelly_cap: float = Field(default=0.05, gt=0, le=0.25)
    kelly_fraction: float = Field(default=0.25, gt=0, le=0.5)


class DirectionValidationRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="5m")
    start: str
    end: str
    threshold: int = Field(default=72, ge=60, le=90)
    sample_size: int = Field(default=100, ge=20, le=1000)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)




class DecisionEngineRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="15m")
    start: str
    end: str
    threshold: int = Field(default=72, ge=60, le=90)
    sample_size: int = Field(default=100, ge=30, le=1000)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)
    account_balance: float = Field(default=1000.0, gt=0)
    max_risk_fraction: float = Field(default=0.01, gt=0, le=0.05)
    kelly_cap: float = Field(default=0.05, gt=0, le=0.25)


class TradePlanRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="15m")
    start: str
    end: str
    threshold: int = Field(default=72, ge=60, le=90)
    sample_size: int = Field(default=100, ge=20, le=1000)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)
    account_balance: float = Field(default=1000.0, gt=0)
    max_risk_fraction: float = Field(default=0.01, gt=0, le=0.05)
    kelly_cap: float = Field(default=0.05, gt=0, le=0.25)


class SimulationCreateRequest(BaseModel):
    contract: str = Field(default="BTC_USDT")
    interval: str = Field(default="15m")
    side: str
    order_type: str = Field(default="MARKET")
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float = Field(gt=0)
    account_balance: float = Field(default=1000.0, gt=0)
    risk_fraction: float = Field(default=0.01, gt=0, le=0.05)
    leverage: float = Field(default=1.0, ge=1.0, le=100.0)
    fee_rate: float = Field(default=0.0005, ge=0, le=0.005)
    slippage_rate: float = Field(default=0.0002, ge=0, le=0.005)
    max_holding_bars: int = Field(default=30, ge=1, le=500)
    exit_mode: str = Field(default="SMART")
    grace_bars: int = Field(default=6, ge=0, le=100)
    strategy: dict = Field(default_factory=dict)
    notes: str = Field(default="", max_length=500)


class SimulationCloseRequest(BaseModel):
    trade_id: str


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
    return {"ok": True, "version": "12.1.0", "edition": "Stability Edition: simulation refresh fix, isolated trade errors, professional position manager"}


@app.get("/api/model-card")
async def model_card_api() -> dict:
    return {"ok": True, "result": model_card()}


@app.get("/api/analyze")
async def analyze_api(
    contract: str = Query(default="BTC_USDT"),
    interval: str = Query(default="15m"),
) -> dict:
    try:
        candles, warnings = await asyncio.wait_for(fetch_recent_candles(contract, interval, 300), timeout=18)
        funding = await asyncio.wait_for(fetch_funding_context(contract), timeout=12)
        snapshot = build_signal(candles)
        generated_at = int(datetime.now(timezone.utc).timestamp())
        interval_seconds = INTERVAL_SECONDS[interval]
        expires_at = ((generated_at // interval_seconds) + 1) * interval_seconds
        payload = {
            "ok": True,
            "contract": contract.upper(),
            "interval": interval,
            "generated_at": generated_at,
            "expires_at": expires_at,
            "last_closed_at": candles[-1].t,
            "signal": snapshot_to_dict(snapshot),
            "data_warnings": warnings,
            "data_quality": assess_data_quality(candles, interval, warnings),
            "funding": funding,
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
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc




@app.get("/api/kline-analysis")
async def kline_analysis_api(contract: str = Query(default="BTC_USDT"), interval: str = Query(default="15m")) -> dict:
    try:
        candles, warnings = await asyncio.wait_for(fetch_recent_candles(contract, interval, 300, min_bars=220), timeout=18)
        return {"ok": True, "contract": contract.upper(), "interval": interval, "result": analyze_kline(candles), "warnings": warnings}
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/api/strategy/quick")
async def quick_strategy_api(contract: str = Query(default="BTC_USDT"), interval: str = Query(default="15m"), account_balance: float = Query(default=1000, gt=0), risk_fraction: float = Query(default=.01, gt=0, le=.05)) -> dict:
    try:
        candles, warnings = await asyncio.wait_for(fetch_recent_candles(contract, interval, 300, min_bars=220), timeout=18)
        snapshot = build_signal(candles); signal=snapshot_to_dict(snapshot)
        side=signal.get("side")
        position=None
        if side in {"LONG","SHORT"} and signal.get("entry") and signal.get("stop"):
            position=calculate_position(account_balance=account_balance,risk_fraction=risk_fraction,side=side,entry=float(signal["entry"]),stop=float(signal["stop"]),leverage=3,fee_rate=.0005,slippage_rate=.0002)
        action_zh={"LONG":"做多","SHORT":"做空","FLAT":"观望"}.get(side,"观望")
        return {"ok":True,"result":{"contract":contract.upper(),"interval":interval,"side":side,"action_zh":action_zh,"entry":signal.get("entry"),"stop":signal.get("stop"),"target":signal.get("target"),"risk_reward":signal.get("risk_reward"),"confidence":signal.get("confidence"),"regime":signal.get("regime"),"position":position,"rationale":signal.get("factor_details") or signal.get("reasons") or [],"exit_rules":["达到止损或止盈退出","浮盈达到0.5R后保护成本","达到最长持有K线后进入智能退出观察窗口"],"warnings":warnings},"notice":"快速策略使用最近已收盘K线，不执行耗时历史验证；历史胜率请到历史研究单独验证。"}
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/decision-engine")
async def decision_engine_api(req: DecisionEngineRequest) -> dict:
    try:
        candles, data_warnings = await fetch_history(
            req.contract, req.interval,
            parse_utc_date(req.start),
            parse_utc_date(req.end, end_of_day=True),
        )
        consensus, funding = await asyncio.gather(
            multi_timeframe_consensus(req.contract),
            fetch_funding_context(req.contract),
        )
        result = build_decision_engine(
            candles=candles,
            interval=req.interval,
            consensus=consensus,
            threshold=req.threshold,
            sample_size=req.sample_size,
            fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate,
            data_warnings=data_warnings,
            account_balance=req.account_balance,
            max_risk_fraction=req.max_risk_fraction,
            kelly_cap=req.kelly_cap,
            funding=funding,
        )
        result.update({
            "contract": req.contract.upper(), "interval": req.interval,
            "start": req.start, "end": req.end,
        })
        return {"ok": True, "result": result}
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
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
            account_balance=req.account_balance,
            max_risk_fraction=req.max_risk_fraction,
            kelly_cap=req.kelly_cap,
        )
        result.update({"contract": req.contract.upper(), "interval": req.interval, "start": req.start, "end": req.end})
        return {"ok": True, "result": result}
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/simulation/create")
async def simulation_create_api(req: SimulationCreateRequest) -> dict:
    try:
        candles, warnings = await asyncio.wait_for(fetch_recent_candles(req.contract, req.interval, 300, min_bars=220), timeout=15)
        market_price = float(candles[-1].c)
        trade = create_trade(
            contract=req.contract, interval=req.interval, side=req.side,
            order_type=req.order_type, requested_entry=req.entry,
            market_price=market_price, stop=req.stop, target=req.target,
            account_balance=req.account_balance, risk_fraction=req.risk_fraction,
            leverage=req.leverage, fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate, max_holding_bars=req.max_holding_bars,
            exit_mode=req.exit_mode, grace_bars=req.grace_bars,
            strategy=req.strategy, notes=req.notes,
        )
        return {"ok": True, "result": trade, "market_price": market_price, "data_warnings": warnings,
                "notice": "这是本地模拟订单，不会连接Gate账户或发送真实订单。"}
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/simulation/trades")
async def simulation_trades_api(status: str = Query(default="ALL"), refresh: bool = Query(default=True)) -> dict:
    try:
        trades = list_trades(100, status)
        refresh_errors: list[str] = []
        if refresh:
            active = [t for t in trades if t.get("status") in {"OPEN", "PENDING"}]
            async def update_one(t: dict) -> dict:
                try:
                    candles, _ = await asyncio.wait_for(
                        fetch_recent_candles(t["contract"], t["interval"], 300, min_bars=220),
                        timeout=12,
                    )
                    return evaluate_trade(t["id"], candles)
                except Exception as exc:
                    # One broken record must not stop the remaining active trades.
                    refresh_errors.append(
                        f"{t['contract']} {t['interval']}（{t.get('side', '-')} / {t.get('status', '-')}）："
                        f"{type(exc).__name__}: {exc}"
                    )
                    return t
            if active:
                await asyncio.gather(*(update_one(t) for t in active[:20]))
            trades = list_trades(100, status)
        return {"ok": True, "result": trades, "stats": simulation_stats(),
                "refresh_errors": refresh_errors,
                "storage_notice": "默认数据库位于应用本地目录；Render免费实例重新部署可能清空记录，正式长期使用需挂载持久磁盘。"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc




@app.get("/api/strategy-lab/performance")
async def strategy_lab_performance_api(limit: int = Query(default=500, ge=20, le=500)) -> dict:
    return {"ok": True, "result": strategy_lab_performance(limit)}


@app.get("/api/strategy-lab/replay/{trade_id}")
async def strategy_lab_replay_api(trade_id: str) -> dict:
    try:
        return {"ok": True, "result": strategy_lab_replay(trade_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/simulation/close")
async def simulation_close_api(req: SimulationCloseRequest) -> dict:
    try:
        trade = next((t for t in list_trades(500) if t["id"] == req.trade_id), None)
        if not trade:
            raise ValueError("找不到该模拟交易")
        candles, _ = await asyncio.wait_for(fetch_recent_candles(trade["contract"], trade["interval"], 60, min_bars=1), timeout=12)
        result = manual_close(req.trade_id, float(candles[-1].c))
        return {"ok": True, "result": result, "stats": simulation_stats()}
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
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
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
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
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
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
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
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
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
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
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
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
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/consensus")
async def consensus_api(contract: str = Query(default="BTC_USDT")) -> dict:
    try:
        return {"ok": True, "result": await multi_timeframe_consensus(contract)}
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
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
    try:
        result = calculate_position(account_balance=req.account_balance, risk_fraction=req.risk_fraction, side=req.side,
            entry=req.entry, stop=req.stop, leverage=req.leverage, fee_rate=req.fee_rate,
            slippage_rate=req.slippage_rate, max_margin_fraction=req.max_margin_fraction)
        return {"ok": True, "result": result, "notice": "已计入双边手续费、滑点、方向校验和保证金上限；交易所最小下单单位仍需下单前核对。"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/advanced-risk")
async def advanced_risk_api(req: AdvancedRiskRequest) -> dict:
    # Conservative probability: never use a point estimate more optimistic
    # than the lower confidence bound, then discount for data quality.
    base_p = min(req.win_probability, req.confidence_lower) if req.confidence_lower is not None else req.win_probability
    quality_factor = 0.70 + 0.30 * (req.data_quality_score / 100.0)
    adjusted_p = 0.5 + (base_p - 0.5) * quality_factor
    adjusted_p = max(0.01, min(0.99, adjusted_p))

    b = req.risk_reward
    q = 1.0 - adjusted_p
    full_kelly = max(0.0, (b * adjusted_p - q) / b)
    fractional_kelly = full_kelly * req.kelly_fraction
    capped_kelly = min(fractional_kelly, req.kelly_cap, req.max_risk_fraction)
    max_loss = req.account_balance * capped_kelly
    expected_r = adjusted_p * b - q

    if expected_r <= 0 or full_kelly <= 0:
        decision = "无正期望：建议观望，不配置Kelly风险"
    elif capped_kelly <= 0.005:
        decision = "优势较弱：仅适合极小风险预算"
    else:
        decision = "存在正期望假设：采用折扣Kelly并受固定风险上限约束"

    return {
        "ok": True,
        "result": {
            "input_probability": req.win_probability,
            "conservative_probability": base_p,
            "adjusted_probability": adjusted_p,
            "data_quality_score": req.data_quality_score,
            "full_kelly_fraction": full_kelly,
            "kelly_fraction": req.kelly_fraction,
            "fractional_kelly_fraction": fractional_kelly,
            "capped_risk_fraction": capped_kelly,
            "max_loss": max_loss,
            "expected_r": expected_r,
            "decision": decision,
        },
        "notice": "采用样本外概率/置信区间下限、数据质量折扣、分数Kelly和固定风险上限；技术评分不能直接当作胜率。",
    }


_overview_cache: dict[str, tuple[float, dict]] = {}
_OVERVIEW_CACHE_SECONDS = 25

@app.get("/api/professional-overview")
async def professional_overview_api(
    contract: str = Query(default="BTC_USDT"),
) -> dict:
    try:
        normalized = contract.strip().upper().replace("-", "_").replace("/", "_")
        cached = _overview_cache.get(normalized)
        if cached and time.time() - cached[0] < _OVERVIEW_CACHE_SECONDS:
            payload = dict(cached[1])
            payload["cached"] = True
            return payload

        consensus = await asyncio.wait_for(
            multi_timeframe_consensus(normalized), timeout=11.0
        )
        recent = recent_signals(100)
        matching = [x for x in recent if x["contract"] == contract.upper()]
        payload = {
            "ok": True,
            "contract": normalized,
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
            "cached": False,
        }
        _overview_cache[normalized] = (time.time(), payload)
        return payload
    except (GateDataError, ValueError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
