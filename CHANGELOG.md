# Changelog

## 3.1.0
- Added transparent data-quality scoring for completeness, freshness, zero-volume bars and suspected outliers.
- Added data-quality grade and issues to live analysis UI.
- Clarified that signal confidence is a rule-model score, not calibrated win probability.
- Added 1d interval support to match the mobile selector.
- Updated cache-busting and service version to 3.1.0.

## 3.1.1
- 修复 Gate 历史K线接口 `limit` 与 `from/to` 参数冲突。
- 新增下一根K线方向概率验证：支持最近50/100/300/500个有效信号。
- 分开显示总体、做多、做空命中率，95% Wilson置信区间和交易成本后有效率。
- 明确区分方向命中率、策略回测与 Monte Carlo 风险模拟。

## 3.2.0
- 新增策略与交易计划：方向、参考入场、止损、目标、离场规则。
- 将模型评分与历史同方向命中率明确分开。
- 显示样本量、成本后有效率和95%置信区间。
- 加入数据质量和最低统计条件，不满足时输出观望。
- 新增专业术语点击解释弹窗。
