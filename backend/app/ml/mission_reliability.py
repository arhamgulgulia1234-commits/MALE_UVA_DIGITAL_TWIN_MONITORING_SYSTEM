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

`compute_recovery_reliability()` below answers a genuinely different question with the
same formula: not "will this engine finish the *planned* mission" but "if we abort and
turn for base right now, will it get there." The PS treats "mission abort" and "unsafe
recovery conditions" as separate risk categories on purpose — an engine can be too
degraded to trust with the rest of a long loiter while still being perfectly good for the
comparatively short hop back to base, and conflating the two into one score would hide
exactly that case. So the target duration changes (estimated time to fly back, instead of
remaining planned mission time) and the recommendation labels change (RTB-SAFE /
RTB-CAUTION / RTB-AT-RISK, never GO/CAUTION/NO-GO) — but the survival math itself does
not, which is the whole point: the two scores are meant to diverge because of what they
are asked about, never because they were computed by two different models.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ml.rul_predictor import RULEstimate

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

    def compute_recovery_reliability(
        self,
        current_health_indicators: dict[str, float],
        rul_estimate: "RULEstimate | None",
        estimated_rtb_time_minutes: float,
    ) -> ReliabilityEstimate:
        """"Can it get back to base if we abort right now?" — the same survival curve
        as `evaluate()`, asked over the estimated time-to-RTB instead of the remaining
        planned mission.

        Delegates straight to `evaluate()` rather than re-deriving the math, so the two
        scores are guaranteed to share the same shape and thresholds and can only ever
        differ for the reason they are supposed to: a shorter (or longer) target
        duration. `current_health_indicators` is folded into a single overall-health
        scalar exactly the way `AnomalyDetector` already does it — `0.65 * worst +
        0.35 * mean` (see app/ml/anomaly_detector.py) — so both reliability numbers read
        the engine's condition identically and diverge only on duration, never on how
        "healthy" is defined.
        """
        if current_health_indicators:
            worst_hi = min(current_health_indicators.values())
            mean_hi = sum(current_health_indicators.values()) / len(
                current_health_indicators
            )
            overall_health = max(0.0, min(100.0, 0.65 * worst_hi + 0.35 * mean_hi))
        else:
            overall_health = 100.0

        rul_minutes = rul_estimate.minutes if rul_estimate is not None else None

        estimate = self.evaluate(
            rul_minutes=rul_minutes,
            mission_remaining_s=max(0.0, estimated_rtb_time_minutes) * 60.0,
            overall_health=overall_health,
        )

        return ReliabilityEstimate(
            score=estimate.score,
            recommendation=_RECOVERY_LABEL[estimate.recommendation],
        )


#: GO/CAUTION/NO-GO relabelled for the recovery question, so the two recommendations can
#: never be visually mistaken for each other even when they agree on how bad things are.
_RECOVERY_LABEL: dict[str, str] = {
    "GO": "RTB-SAFE",
    "CAUTION": "RTB-CAUTION",
    "NO-GO": "RTB-AT-RISK",
}
