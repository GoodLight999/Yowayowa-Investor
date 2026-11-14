"""Quantitative rule engine for C2 (vol_target 10% and 15%).

Standard S-02 logic:
- sigma_star = target_vol / sqrt(252)
- w_rule = min(1.0, sigma_star / sigma21_daily)
- Universe equal allocation: w_asset = w_rule / 4 for [SPY, IWM, JNJ, PG]
- Cash: 1.0 - w_rule
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .c2_features import ANNUALIZATION_FACTOR, C2_UNIVERSE


@dataclass(frozen=True)
class RuleOutput:
    target_vol: float
    sigma_star: float
    sigma21_daily: float
    w_rule: float
    weights: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_vol": self.target_vol,
            "sigma_star": round(self.sigma_star, 6),
            "sigma21_daily": round(self.sigma21_daily, 6),
            "w_rule": round(self.w_rule, 6),
            "weights": {k: round(v, 6) for k, v in self.weights.items()},
        }


def compute_rule_weights(target_vol: float, sigma21_daily: float) -> RuleOutput:
    """Compute base quantitative weights for given target volatility."""
    if sigma21_daily < 0:
        raise ValueError(f"sigma21_daily must be non-negative: {sigma21_daily}")

    sigma_star = target_vol / ANNUALIZATION_FACTOR
    w_rule = 1.0 if sigma21_daily == 0.0 else min(1.0, sigma_star / sigma21_daily)

    n_assets = len(C2_UNIVERSE)
    per_asset = w_rule / n_assets
    weights = {sym: per_asset for sym in C2_UNIVERSE}
    weights["USD"] = max(0.0, 1.0 - w_rule)

    return RuleOutput(
        target_vol=target_vol,
        sigma_star=sigma_star,
        sigma21_daily=sigma21_daily,
        w_rule=w_rule,
        weights=weights,
    )
