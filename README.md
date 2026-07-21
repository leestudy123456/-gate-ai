# Gate AI Quant Professional 6.0.0 Mobile

手机浏览器可运行的公开行情量化研究工具。支持多周期、技术因子评分、样本外方向验证、回测、滚动验证、Monte Carlo、仓位预算和调整 Kelly。

## 部署
Build: `pip install -r requirements.txt`
Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`

## 风险说明
模型评分不是已校准胜率。调整 Kelly 仍依赖历史样本，不构成投资建议，也不自动下单。
