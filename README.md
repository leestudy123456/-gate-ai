# Gate AI Quant Professional 3.0

FastAPI + Gate 公开合约行情的移动端量化研究应用。提供实时多因子分析、市场扫描、多周期共振、无前视回测、参数优化、Walk-Forward、Monte Carlo、仓位预算和预测市场价值计算。

不读取账户、不保存 API Key、不自动下单。技术评分不是确定概率，也不构成投资建议。

## Render
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Python: 3.12.11

健康检查：`/api/health`，应返回版本 `3.0.0`。
