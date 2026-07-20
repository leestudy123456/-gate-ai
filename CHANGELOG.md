# Changelog

## 4.0.0

- 将3.x累积功能整理为单一完整可部署版本
- 统一版本号、静态资源缓存标记和Render配置
- 保留并验证历史K线分段下载，避免Gate `limit/from/to` 参数冲突
- 保留数据质量、方向验证、交易计划、回测、优化、Walk-forward和Monte Carlo
- 新增模型说明接口 `/api/model-card`
- 明确模型评分、历史命中率和未来概率之间的区别
- 更新手机端一次性部署说明
