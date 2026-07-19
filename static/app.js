const $ = (id) => document.getElementById(id);

function fmt(x, digits=4) {
  return x === null || x === undefined ? "—" : Number(x).toFixed(digits);
}

$("analyzeBtn").onclick = async () => {
  $("analysis").textContent = "正在读取Gate公开行情…";
  const contract = $("contract").value.trim();
  const interval = $("interval").value;
  try {
    const r = await fetch(`/api/analyze?contract=${encodeURIComponent(contract)}&interval=${interval}`);
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "分析失败");
    const s = j.signal;
    $("analysis").textContent =
`交易对：${j.contract}  周期：${j.interval}
方向：${s.side}
做多评分：${s.long_score}
做空评分：${s.short_score}
信心：${s.confidence}
RSI14：${fmt(s.rsi, 2)}
ATR：${fmt(s.atr, 8)}
EMA20 / 50 / 200：${fmt(s.ema20,8)} / ${fmt(s.ema50,8)} / ${fmt(s.ema200,8)}
参考入场：${fmt(s.entry,8)}
止损：${fmt(s.stop,8)}
目标：${fmt(s.target,8)}
原因：${(s.reasons || []).join("；") || "无强方向"}
数据提示：${(j.data_warnings || []).join("；") || "无"}`;
  } catch (e) {
    $("analysis").textContent = `错误：${e.message}`;
  }
};

$("backtestBtn").onclick = async () => {
  $("backtest").textContent = "正在分批下载历史K线并回测，请稍候…";
  const body = {
    contract: $("contract").value.trim(),
    interval: $("interval").value,
    start: $("start").value,
    end: $("end").value,
    threshold: Number($("threshold").value),
    fee_rate: Number($("fee").value),
    slippage_rate: Number($("slippage").value),
    risk_fraction: Number($("risk").value),
    max_holding_bars: 24
  };
  try {
    const r = await fetch("/api/backtest", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "回测失败");
    const x = j.result;
    const pf = x.profit_factor === null ? "—" :
      (x.profit_factor === Infinity ? "∞" : fmt(x.profit_factor, 2));
    $("backtest").textContent =
`交易对：${x.contract}  周期：${x.interval}
区间：${x.start} 至 ${x.end}
K线数：${x.bars}
交易数：${x.trades}
胜 / 负：${x.wins} / ${x.losses}
胜率：${fmt(x.win_rate_pct,2)}%
净收益：${fmt(x.net_return_pct,2)}%
最大回撤：${fmt(x.max_drawdown_pct,2)}%
Profit Factor：${pf}
单笔期望：${fmt(x.expectancy_pct,3)}%
平均盈利：${fmt(x.average_win_pct,3)}%
平均亏损：${fmt(x.average_loss_pct,3)}%
Sharpe-like：${fmt(x.sharpe_like,2)}
双边手续费：${fmt(x.fees_pct_round_trip,3)}%
双边滑点：${fmt(x.slippage_pct_round_trip,3)}%

假设：
- ${(x.assumptions || []).join("\n- ")}

警告：
- ${[...(x.warnings || []), ...(x.data_warnings || [])].join("\n- ") || "无"}`;
  } catch (e) {
    $("backtest").textContent = `错误：${e.message}`;
  }
};
