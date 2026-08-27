"""Mission reliability: will this engine finish the sortie?

Weibull-style survival over the remaining planned mission duration:

    R(t) = exp( -(t_remaining / RUL) ^ beta ),   beta ~ 2.5

`t_remaining` comes from the mission profile (how much flying is still planned) and
`RUL` from app/ml/rul_predictor.py. The shape parameter beta > 1 gives an increasing
hazard rate: reliability falls away sharply once the remaining mission approaches the
remaining useful life, rather than degrading linearly.

Recommendation thresholds:
    R > 0.85          -> GO
    0.60 <= R <= 0.85 -> CAUTION
    R < 0.60          -> NO-GO
"""
from __future__ import annotations

import math
from dataclasses import dataclass

BETA_DEFAULT = 2.5
GO_THRESHOLD = 0.85
CAUTION_THRESHOLD = 0.60


@dataclass
class ReliabilityEstimate:
    score: float
    recommendation: str  # "GO" | "CAUTION" | "NO-GO"


class MissionReliabilityModel:
    def __init__(self, beta: float = BETA_DEFAULT) -> None:
        self.beta = beta

    def evaluate(
        self,
        rul_minutes: float | None,
        mission_remaining_s: float,
        overall_health: float,
    ) -> ReliabilityEstimate:
        remaining_min = max(0.0, mission_remaining_s / 60.0)

        if rul_minutes is None:
            # No degradation trend detected. Reliability is governed by present health
            # alone, so a healthy engine reads ~1.0 without inventing an RUL.
            score = max(0.0, min(1.0, overall_health / 100.0))
            # Keep a healthy engine comfortably in GO rather than hovering on the line.
            score = min(1.0, score * 1.02)
        elif rul_minutes <= 1e-6:
            score = 0.0
        else:
            ratio = remaining_min / rul_minutes
            score = math.exp(-(ratio**self.beta))
            # A badly degraded engine cannot be called reliable even on a short leg.
            score = min(score, max(0.0, overall_health / 100.0) ** 0.5)

        score = max(0.0, min(1.0, score))

        if score > GO_THRESHOLD:
            recommendation = "GO"
        elif score >= CAUTION_THRESHOLD:
            recommendation = "CAUTION"
        else:
            recommendation = "NO-GO"

        return ReliabilityEstimate(score=score, recommendation=recommendation)
