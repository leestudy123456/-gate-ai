from __future__ import annotations


def model_card() -> dict:
    """Human-readable model methodology and limits for transparent use."""
    return {
        "version": "4.0.0",
        "purpose": "研究公开Gate永续合约已收盘K线，生成方向评分、历史验证和条件交易计划。",
        "inputs": [
            "已收盘OHLCV K线",
            "EMA20/50/200",
            "MACD、RSI14、ATR14、ADX14",
            "成交量相对强度、近期支撑阻力",
            "历史同方向下一根K线验证结果",
        ],
        "anti_leakage": [
            "实时分析只使用已收盘K线",
            "方向验证在第t根收盘后生成信号，只用第t+1根检验",
            "回测信号与成交结果按时间顺序推进，不读取未来K线",
            "优化结果必须用滚动样本外验证再次检查",
        ],
        "probability_definition": "历史校准概率是相同方向、相同阈值下的历史下一根K线命中率，不等于未来保证概率。",
        "costs": "成本后有效率会扣除双边手续费和滑点门槛；资金费率和强平机制未纳入。",
        "decision_rules": [
            "数据质量低于70分时观望",
            "同方向样本少于20次时观望",
            "成本后有效率低于50%时观望",
            "95%置信区间下限低于50%时观望",
            "模型评分未达到阈值时观望",
        ],
        "limitations": [
            "历史关系可能随市场状态变化",
            "短周期噪声和交易成本可能消除表面优势",
            "公开K线无法反映订单簿、新闻和链上突发事件",
            "本工具不连接账户，也不自动下单",
        ],
    }
