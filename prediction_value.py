from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class PredictionValueResult:
    market_price: float
    model_probability: float
    edge_pct_points: float
    expected_value_per_share: float
    expected_roi_pct: float
    full_kelly_fraction: float
    capped_kelly_fraction: float
    recommendation: str
    explanation: str

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_prediction_value(
    market_price: float,
    model_probability: float,
    fee_rate: float = 0.0,
    kelly_cap: float = 0.05,
    minimum_edge: float = 0.05,
) -> PredictionValueResult:
    """Analyze a YES share paying 1 if true and 0 otherwise.

    This is a calculator. It does not independently estimate the true probability.
    """
    if not 0.01 <= market_price <= 0.99:
        raise ValueError("市场价格应在0.01到0.99之间")
    if not 0.0 <= model_probability <= 1.0:
        raise ValueError("模型概率应在0到1之间")
    if not 0.0 <= fee_rate <= 0.10:
        raise ValueError("费用率应在0到10%之间")
    if not 0.0 < kelly_cap <= 0.25:
        raise ValueError("Kelly上限应在0到25%之间")

    payout_profit = 1.0 - market_price
    expected_value = model_probability * payout_profit - (1.0 - model_probability) * market_price
    expected_value -= fee_rate * market_price
    roi = expected_value / market_price
    edge = model_probability - market_price

    b = payout_profit / market_price
    q = 1.0 - model_probability
    full_kelly = max(0.0, (b * model_probability - q) / b)
    capped = min(full_kelly, kelly_cap)

    if edge >= minimum_edge and expected_value > 0:
        recommendation = "YES_VALUE"
        explanation = "模型概率明显高于市场隐含概率，存在正期望；仍需核验概率来源。"
    elif edge <= -minimum_edge:
        recommendation = "YES_OVERPRICED"
        explanation = "YES价格高于模型概率，不宜买YES；这不自动等于可以买NO。"
    else:
        recommendation = "NO_EDGE"
        explanation = "估值差不足以覆盖模型误差与交易成本，建议观望。"

    return PredictionValueResult(
        market_price=market_price,
        model_probability=model_probability,
        edge_pct_points=edge * 100,
        expected_value_per_share=expected_value,
        expected_roi_pct=roi * 100,
        full_kelly_fraction=full_kelly,
        capped_kelly_fraction=capped,
        recommendation=recommendation,
        explanation=explanation,
    )
