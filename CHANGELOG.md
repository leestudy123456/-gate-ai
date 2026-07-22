# Gate AI Quant 8.0

- 决策概率改用按时间顺序切分的严格样本外测试集。
- 最后35%历史信号保留为holdout，不参与训练区间统计。
- 置信区间改为基于成本后样本外正确数。
- 页面新增样本外总样本与最大连续错误。
- 保留7.2.2的刷新、扫描缓存与超时稳定性修复。
- 规则信心继续明确标注为技术一致度，不等于真实胜率。

# 7.2.2 扫描与重复操作加速

- 扫描结果缓存45秒，第二次点击通常直接返回。
- 相同扫描请求复用后台任务，避免重复请求堆积。
- 每个币种独立7.5秒超时，整个扫描13秒硬截止。
- 扫描并发从4提高到最多8，同时保留限流保护。
- 前端扫描使用固定请求键，可取消旧扫描。
- 页面显示是否使用短时缓存。
- 保留7.2.1多周期刷新修复和7.2资金费率功能。

# Changelog

## 7.2.1
- Fixed repeated market-overview refresh freezing on mobile.
- Added an 11-second server deadline and 8-second per-timeframe deadline.
- One slow Gate interval now returns as UNAVAILABLE instead of blocking the page.
- Added a 25-second aggregate overview cache.
- Front end now cancels the previous overview request with a stable request key.

# Changelog

## 7.0.0
- 新增 AI 决策引擎 `/api/decision-engine`
- 新增历史概率保守校准与概率区间
- 新增多周期对齐度、数据质量与样本可靠性折扣
- 新增正期望门槛、阻止交易条件和风险提醒
- 新增 Quarter Kelly 与固定风险上限联合预算
- 新增手机端 AI 决策卡
- 版本升级为 PRO 7.0 AI MOBILE


## 7.2.0
- Added Gate futures funding-rate context and crowding adjustment.
- Added 20-second response cache and in-flight request deduplication.
- Added browser request cancellation, 30-second timeout and no-store refresh.
- Switching contracts now cancels stale requests so old responses cannot overwrite the new coin.

## 9.0.0 — Calibrated Paper Trading Journal
- 新增模拟交易页：AI 决策参数可一键带入。
- 支持市价模拟与策略触价入场两种模式。
- 按账户余额、风险比例、止损距离及杠杆上限计算模拟仓位。
- 使用 Gate 已收盘 K 线自动检查入场、止盈、止损与超时平仓。
- 同一根 K 线同时触发止盈和止损时，保守按止损先发生，避免夸大结果。
- 计入双边手续费和滑点，记录净盈亏、R 倍数、MFE、MAE及持有K线数。
- 新增模拟账户统计：胜率、净盈亏、平均R、Profit Factor、未结束订单。
- 使用 SQLite 保存模拟交易日志；Render 免费实例重新部署可能清空本地记录，长期使用需配置持久磁盘和 SIM_DB_PATH。
- 模拟功能不连接 Gate 账户，不会发送真实订单。
