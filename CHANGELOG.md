# 11.3 Smart Exit Manager

- Added configurable maximum holding bars (1–500) with mobile presets.
- Added interval-aware holding-time display and regime-based AI recommendation.
- Added SMART, BREAKEVEN, MIN_LOSS, TRAILING and FIXED exit modes.
- Added configurable grace bars after the planned holding window.
- Added fee/slippage-aware breakeven stop and R-based trailing protection.
- Added automatic SQLite schema migration for existing deployments.
- Added management status and richer exit reasons to the simulation journal.

# 11.2 Simulation Fix

- 修复“开始模拟交易”固定报有效K线仅59根的问题。
- 修复手动平仓调用少于API最小limit的问题。
- 新增按用途配置 min_bars。
- 模拟持仓刷新返回可见错误信息。

## 9.2.0 — Trade Decision Center

- Scanner results redesigned as decision cards.
- Added direct paper-trading button to every scan result.
- Automatically prefills contract, side, entry, stop and target.
- Added model-confidence stars, RR, recommendation label and favorites.
- Added explicit AI analysis and candle-data actions for mobile.

## 9.1.0 — Scanner Interaction Hotfix

- 修复扫描结果点击无反应。
- 每个扫描结果新增明确的“AI分析”和“选择币种”按钮。
- 点击AI分析会自动切换币种、同步周期、返回总览并启动实时分析。
- 增加事件委托，避免列表重绘后点击事件丢失。
- 修复顶部5个导航标签在手机端的布局。
- 增加前端缓存版本号，避免手机继续加载旧JavaScript。

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

## 10.0.0

- 新增五子模型 Decision Engine 2.0 与加权投票。
- 新增 Risk Engine，高风险时统一返回 WAIT。
- 新增 Market Regime 结构化市场状态。
- 新增 Explain AI 指标贡献面板。
- 新增 Trade DNA 决策快照标识与特征结构。
- 决策页增加模型投票、市场状态、风险等级和可展开解释。
- 前端静态资源缓存版本升级至 10.0.0。

## 11.0 Multi-Factor Strategy Lab

- 按周期动态切换 EMA 集群，并加入斜率、压缩和距离风险。
- 新增趋势、动量、成交量、波动率和衍生品多因子引擎。
- 资金费率升级为绝对值、动量和拥挤分析；显示 OI 快照可用性。
- 链上与订单流模块增加数据源门控，未接入时保持中性。
- 市场状态动态调整模型权重。
- 新增策略质量 S/A/B/C/D。
- 新增 AI Strategy Lab 绩效归因与决策回放 API/UI。
- 版本升级为 11.0.0。


## 11.1 Stable

- 自动创建 data 目录并初始化两套 SQLite 数据库。
- 信号数据库迁移到 data/gate_ai_quant.db，并支持 SIGNAL_DB_PATH 环境变量。
- 保留 SIM_DB_PATH 环境变量，方便 Render 持久磁盘部署。
- 更新版本标识和静态资源缓存版本。
- 增加 data/.gitkeep，GitHub 上传后不再丢失空目录。

## 12.0 Professional

- Separate AI decision and K-line analysis endpoints.
- Add fast strategy endpoint to avoid research-page timeout.
- Replace basic position sizing with direction-aware professional risk sizing.
- Include fees, slippage, leverage/margin cap, worst-case loss and safety warnings.
- Separate scanner AI and chart actions.
- Preserve V11.3 smart exit and custom holding-bar manager.
- Improve timeout error messages and bump mobile asset cache version.
