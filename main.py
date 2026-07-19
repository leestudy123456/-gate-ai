from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import httpx

app = FastAPI(title="Gate 5分钟交易助手")

HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Gate 5分钟交易助手</title>
<style>
body{margin:0;background:#0b0f14;color:#eef3f8;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:720px;margin:auto;padding:14px}.card{background:#141b24;border:1px solid #263241;border-radius:16px;padding:14px;margin:10px 0}
.row{display:flex;gap:8px;flex-wrap:wrap}input,button{border-radius:10px;border:1px solid #263241;background:#0e141c;color:#eef3f8;padding:10px;font-size:15px}
button{background:#2563eb}.signal{font-size:28px;font-weight:800}.muted{color:#94a3b8;font-size:13px;line-height:1.5}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.metric{border:1px solid #263241;border-radius:12px;padding:10px}
.label{color:#94a3b8;font-size:12px}.value{font-size:20px;font-weight:700;margin-top:4px}
.good{color:#34d399}.bad{color:#fb7185}.warn{color:#fbbf24}
table{width:100%;border-collapse:collapse;font-size:13px}td{padding:8px 0;border-bottom:1px solid #263241}td:last-child{text-align:right}
</style>
</head>
<body><div class="wrap">
<h2>Gate 5分钟交易助手</h2>
<div class="card">
  <div class="row"><input id="contract" value="ESPORTS_USDT"><button id="start">开始盯盘</button></div>
  <p class="muted">读取 Gate 公共5分钟K线，每15秒刷新；不会自动下单。</p>
</div>
<div class="card">
  <div id="signal" class="signal warn">等待数据</div>
  <div id="reason" class="muted">尚未计算</div>
</div>
<div class="grid">
  <div class="metric"><div class="label">最新价</div><div id="price" class="value">--</div></div>
  <div class="metric"><div class="label">信心评分</div><div id="confidence" class="value">--</div></div>
  <div class="metric"><div class="label">EMA 5/10/30</div><div id="emas" class="value" style="font-size:13px">--</div></div>
  <div class="metric"><div class="label">RSI 14</div><div id="rsi" class="value">--</div></div>
</div>
<div class="card"><b>参考点位</b>
<table>
<tr><td>做多触发</td><td id="longEntry">--</td></tr>
<tr><td>做空触发</td><td id="shortEntry">--</td></tr>
<tr><td>多单 SL / TP</td><td id="longPlan">--</td></tr>
<tr><td>空单 SL / TP</td><td id="shortPlan">--</td></tr>
</table>
<p class="muted">触发点应等待5分钟K线收盘确认。评分不是保证胜率。</p>
</div>
</div>
<script>
const $=x=>document.getElementById(x);let timer;
const fmt=x=>Number(x)<0.1?Number(x).toFixed(6):Number(x).toFixed(4);
async function tick(){
 try{
   const c=$("contract").value.trim().toUpperCase();
   const r=await fetch(`/api/signal?contract=${encodeURIComponent(c)}`,{cache:"no-store"});
   const d=await r.json(); if(!r.ok) throw new Error(d.detail||"读取失败");
   $("signal").textContent=d.signal;$("signal").className="signal "+d.class_name;
   $("reason").textContent=d.reason;$("price").textContent=fmt(d.price);
   $("confidence").textContent=d.confidence+"/100";
   $("emas").textContent=[d.ema5,d.ema10,d.ema30].map(fmt).join(" / ");
   $("rsi").textContent=d.rsi.toFixed(1);
   $("longEntry").textContent=fmt(d.long_trigger);
   $("shortEntry").textContent=fmt(d.short_trigger);
   $("longPlan").textContent=`${fmt(d.long_sl)} / ${fmt(d.long_tp)}`;
   $("shortPlan").textContent=`${fmt(d.short_sl)} / ${fmt(d.short_tp)}`;
 }catch(e){$("reason").textContent=e.message}
}
$("start").onclick=()=>{clearInterval(timer);tick();timer=setInterval(tick,15000);$("start").textContent="重新连接"};
</script></body></html>"""

def ema(values, n):
    k = 2 / (n + 1)
    e = values[0]
    for value in values[1:]:
        e = value * k + e * (1 - k)
    return e

def rsi(values, n=14):
    gains = losses = 0.0
    for i in range(len(values)-n, len(values)):
        change = values[i] - values[i-1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - 100 / (1 + rs)

def atr(candles, n=14):
    values = []
    for i in range(1, len(candles)):
        h, l, prev_close = candles[i]["h"], candles[i]["l"], candles[i-1]["c"]
        values.append(max(h-l, abs(h-prev_close), abs(l-prev_close)))
    sample = values[-n:]
    return sum(sample) / len(sample)

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML

@app.get("/api/signal")
async def signal(contract: str = "ESPORTS_USDT"):
    url = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
    params = {"contract": contract.upper(), "interval": "5m", "limit": "120"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            raw = response.json()
    except Exception as exc:
        return JSONResponse(status_code=502, content={"detail": f"Gate行情读取失败：{exc}"})

    candles = sorted(
        [{"t": float(x["t"]), "o": float(x["o"]), "h": float(x["h"]),
          "l": float(x["l"]), "c": float(x["c"]), "v": float(x["v"])} for x in raw],
        key=lambda x: x["t"]
    )
    if len(candles) < 35:
        return JSONResponse(status_code=400, content={"detail": "合约代码错误或K线数量不足"})

    closes = [x["c"] for x in candles]
    volumes = [x["v"] for x in candles]
    last, previous = candles[-1], candles[-2]

    e5 = ema(closes[-45:], 5)
    e10 = ema(closes[-55:], 10)
    e30 = ema(closes[-90:], 30)
    r = rsi(closes)
    a = atr(candles)

    avg_volume = sum(volumes[-21:-1]) / 20
    volume_ratio = last["v"] / (avg_volume or 1)
    high20 = max(x["h"] for x in candles[-21:-1])
    low20 = min(x["l"] for x in candles[-21:-1])

    bull = bear = 0
    reasons = []

    if last["c"] > e5 > e10:
        bull += 18; reasons.append("短均线偏多")
    elif last["c"] < e5 < e10:
        bear += 18; reasons.append("短均线偏空")

    if e10 > e30: bull += 12
    else: bear += 12

    if 55 < r < 75: bull += 12
    if 25 < r < 45: bear += 12
    if r >= 75: bear += 8; reasons.append("RSI偏热")
    if r <= 25: bull += 8; reasons.append("RSI偏冷")

    body = last["c"] - last["o"]
    candle_range = max(last["h"] - last["l"], 1e-12)
    if body > 0 and abs(body) / candle_range > 0.55: bull += 13
    if body < 0 and abs(body) / candle_range > 0.55: bear += 13

    if volume_ratio > 1.5:
        if body > 0: bull += 12
        else: bear += 12
        reasons.append("成交量放大")

    if last["c"] > high20: bull += 18
    if last["c"] < low20: bear += 18
    if last["c"] > previous["c"]: bull += 5
    else: bear += 5

    confidence = min(95, 45 + abs(bull - bear))
    signal_text, class_name = "观望", "warn"
    if confidence >= 70 and bull > bear:
        signal_text, class_name = "偏多：等待确认", "good"
    elif confidence >= 70 and bear > bull:
        signal_text, class_name = "偏空：等待确认", "bad"

    long_trigger = max(high20, last["c"] + 0.25 * a)
    short_trigger = min(low20, last["c"] - 0.25 * a)

    return {
        "signal": signal_text,
        "class_name": class_name,
        "confidence": confidence,
        "reason": f"多头 {bull} 分｜空头 {bear} 分｜量比 {volume_ratio:.2f}。{'；'.join(reasons) or '暂无强信号'}。",
        "price": last["c"],
        "ema5": e5, "ema10": e10, "ema30": e30,
        "rsi": r,
        "long_trigger": long_trigger,
        "short_trigger": short_trigger,
        "long_sl": long_trigger - 1.1 * a,
        "long_tp": long_trigger + 2.0 * a,
        "short_sl": short_trigger + 1.1 * a,
        "short_tp": short_trigger - 2.0 * a,
    }
