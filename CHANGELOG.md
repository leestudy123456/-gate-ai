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
