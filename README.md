# Gate 多周期交易助手

读取 Gate USDT 永续合约公开K线，提供技术信号和参考点位。

支持周期：

- 5分钟
- 15分钟
- 30分钟
- 1小时
- 4小时

Render 部署：

- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

本工具不会自动下单；信号与评分仅供参考，不保证盈利。
