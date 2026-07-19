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


function commonBody() {
  return {
    contract: $("contract").value.trim(),
    interval: $("interval").value,
    start: $("start").value,
    end: $("end").value,
    fee_rate: Number($("fee").value),
    slippage_rate: Number($("slippage").value)
  };
}

$("optimizeBtn").onclick = async () => {
  $("optimize").textContent = "正在优化…";
  try {
    const r = await fetch("/api/optimize", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(commonBody())
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "优化失败");
    const b = j.best;
    $("optimize").textContent =
`最佳训练参数：
阈值 ${b.threshold}
风险 ${(b.risk_fraction * 100).toFixed(1)}%
持有 ${b.max_holding_bars} 根
交易 ${b.trades}
净收益 ${b.net_return_pct.toFixed(2)}%
回撤 ${b.max_drawdown_pct.toFixed(2)}%
PF ${b.profit_factor === null ? "—" : Number(b.profit_factor).toFixed(2)}

注意：下一步必须做滚动样本外验证。`;
  } catch (e) {
    $("optimize").textContent = `错误：${e.message}`;
  }
};

$("wfBtn").onclick = async () => {
  $("wf").textContent = "正在滚动验证，耗时可能较长…";
  const body = {
    ...commonBody(),
    train_bars: Number($("trainBars").value),
    test_bars: Number($("testBars").value)
  };
  try {
    const r = await fetch("/api/walk-forward", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "验证失败");
    const x = j.result;
    $("wf").textContent =
`折数：${x.fold_count}
样本外为正折数：${x.positive_test_folds}
正收益折数比例：${x.positive_fold_rate_pct.toFixed(2)}%
样本外平均收益：${x.average_test_return_pct.toFixed(2)}%
样本外平均回撤：${x.average_test_drawdown_pct.toFixed(2)}%
样本外交易总数：${x.total_test_trades}`;
  } catch (e) {
    $("wf").textContent = `错误：${e.message}`;
  }
};

$("mcBtn").onclick = async () => {
  $("mc").textContent = "正在模拟…";
  const body = {
    ...commonBody(),
    threshold: Number($("threshold").value),
    risk_fraction: Number($("risk").value),
    max_holding_bars: 24
  };
  try {
    const r = await fetch("/api/monte-carlo", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "模拟失败");
    const x = j.result;
    $("mc").textContent =
`模拟次数：${x.simulations}
最终收益5%分位：${x.final_return_p05_pct.toFixed(2)}%
最终收益中位数：${x.final_return_p50_pct.toFixed(2)}%
最终收益95%分位：${x.final_return_p95_pct.toFixed(2)}%
最大回撤中位数：${x.max_drawdown_p50_pct.toFixed(2)}%
最大回撤95%分位：${x.max_drawdown_p95_pct.toFixed(2)}%`;
  } catch (e) {
    $("mc").textContent = `错误：${e.message}`;
  }
};


document.querySelectorAll(".tab").forEach((button) => {
  button.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((x) => x.classList.add("hidden"));
    button.classList.add("active");
    $(button.dataset.target).classList.remove("hidden");
  };
});

$("scanBtn").onclick = async () => {
  $("scanResult").textContent = "正在扫描Gate高流动性合约…";
  try {
    const interval = $("scanInterval").value;
    const limit = Number($("scanLimit").value);
    const r = await fetch(`/api/scanner?interval=${interval}&limit=${limit}&min_confidence=55`);
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "扫描失败");
    const x = j.result;
    const rows = (x.rows || []).map((row, i) =>
      `${i+1}. ${row.contract}｜${row.side}｜信心${row.confidence}｜多${row.long_score}/空${row.short_score}｜价格${row.last_price}`
    ).join("\n");
    $("scanResult").textContent =
`周期：${x.interval}
请求：${x.requested}
成功分析：${x.analyzed}

${rows || "没有达到最低评分的信号"}

${x.notice}`;
  } catch (e) {
    $("scanResult").textContent = `错误：${e.message}`;
  }
};

$("consensusBtn").onclick = async () => {
  $("consensusResult").textContent = "正在分析5个周期…";
  try {
    const contract = $("contract").value.trim();
    const r = await fetch(`/api/consensus?contract=${encodeURIComponent(contract)}`);
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "分析失败");
    const x = j.result;
    const details = x.timeframes.map(row =>
      `${row.interval}：${row.side}｜多${row.long_score}/空${row.short_score}｜信心${row.confidence}`
    ).join("\n");
    $("consensusResult").textContent =
`${x.contract}
综合方向：${x.side}
综合多头评分：${x.long_score}
综合空头评分：${x.short_score}

${details}

${x.method}`;
  } catch (e) {
    $("consensusResult").textContent = `错误：${e.message}`;
  }
};

$("predictionBtn").onclick = async () => {
  $("predictionResult").textContent = "正在计算…";
  const body = {
    market_price: Number($("marketPrice").value),
    model_probability: Number($("modelProbability").value),
    fee_rate: Number($("predictionFee").value),
    kelly_cap: Number($("kellyCap").value),
    minimum_edge: 0.05
  };
  try {
    const r = await fetch("/api/prediction-value", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "计算失败");
    const x = j.result;
    $("predictionResult").textContent =
`判断：${x.recommendation}
市场隐含概率：${(x.market_price*100).toFixed(1)}%
模型概率：${(x.model_probability*100).toFixed(1)}%
概率优势：${x.edge_pct_points.toFixed(1)}个百分点
每份期望值：${x.expected_value_per_share.toFixed(4)}
期望收益率：${x.expected_roi_pct.toFixed(2)}%
完整Kelly：${(x.full_kelly_fraction*100).toFixed(2)}%
建议仓位上限：${(x.capped_kelly_fraction*100).toFixed(2)}%

${x.explanation}

注意：本工具不会替你估计真实概率。`;
  } catch (e) {
    $("predictionResult").textContent = `错误：${e.message}`;
  }
};
