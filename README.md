# Gate AI Quant V2

一个只读取 Gate 公开 USDT 永续合约行情的分析与回测工具。

## 已包含

- 5m / 15m / 30m / 1h / 4h
- 只使用已收盘K线
- EMA、MACD、RSI、ATR、成交量和结构评分
- 实时信号
- 历史K线分批下载
- 无前视回测
- 下一根K线开盘入场
- 手续费与滑点
- 止损/止盈同柱冲突时按止损先发生
- 胜率、净收益、最大回撤、Profit Factor、期望和 Sharpe-like

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests
uvicorn app:app --reload
```

打开 `http://127.0.0.1:8000`

## 重要限制

- 回测结果不等于未来收益。
- 尚未计入资金费率、强平、盘口深度、部分成交、税费。
- 参数优化必须使用训练区间，最后用未参与优化的样本外区间验证。
- 不应只根据胜率决策；应同时查看交易数、最大回撤、Profit Factor和期望。
- 本项目不连接账户、不自动下单。
