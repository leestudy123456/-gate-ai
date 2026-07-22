from __future__ import annotations
from statistics import fmean
from gate_client import Candle
from strategy import ema, rsi_wilder, atr_wilder, macd_values, adx_wilder


def analyze_kline(candles: list[Candle]) -> dict:
    if len(candles) < 220:
        raise ValueError(f"K线分析至少需要220根已收盘K线，当前仅{len(candles)}根")
    closes=[c.c for c in candles]
    e20,e50,e200=ema(closes,20),ema(closes,50),ema(closes,200)
    rsi=rsi_wilder(closes); atr=atr_wilder(candles); macd,signal,hist=macd_values(closes); adx=adx_wilder(candles)
    recent=candles[-20:]
    support=min(c.l for c in recent); resistance=max(c.h for c in recent)
    avg_vol=fmean(c.v for c in candles[-21:-1]) if len(candles)>=21 else 0
    vol_ratio=candles[-1].v/avg_vol if avg_vol>0 else 0
    trend="上涨" if closes[-1]>e20>e50 else "下跌" if closes[-1]<e20<e50 else "震荡"
    momentum="增强" if hist>0 and macd>signal else "减弱" if hist<0 else "中性"
    candle_body=abs(candles[-1].c-candles[-1].o)
    candle_range=max(candles[-1].h-candles[-1].l,1e-12)
    pattern="长阳" if candles[-1].c>candles[-1].o and candle_body/candle_range>.65 else "长阴" if candles[-1].c<candles[-1].o and candle_body/candle_range>.65 else "小实体/犹豫"
    return {"last_price":closes[-1],"trend":trend,"momentum":momentum,"pattern":pattern,
            "ema20":e20,"ema50":e50,"ema200":e200,"rsi14":rsi,"atr14":atr,"atr_pct":atr/closes[-1]*100,
            "macd":macd,"macd_signal":signal,"macd_histogram":hist,"adx14":adx,"support":support,"resistance":resistance,
            "volume_ratio":vol_ratio,"summary":[f"趋势结构：{trend}",f"MACD动能：{momentum}",f"最新K线：{pattern}",f"成交量为近20根均量的{vol_ratio:.2f}倍"]}
