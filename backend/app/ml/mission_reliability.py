"""Mission reliability: will this engine finish the sortie?

Weibull-style survival over the remaining planned mission duration:

    R(t) = exp( -(t_remaining / RUL) ^ beta ),   beta ~ 2.0

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

#: Was 2.5. Still an increasing-hazard Weibull shape, but a materially gentler one — the
#: rate limiter below is the real fix for the score's tick-to-tick nonlinearity, but there
#: is no reason to also fit the steepest curve that still makes the point.
BETA_DEFAULT = 2.0
GO_THRESHOLD = 0.85
CAUTION_THRESHOLD = 0.60

#: Same asymmetry as RULPredictor's smoothing, and for the same reason: a reliability
#: score getting worse must reach the operator quickly, one getting better can afford to
#: lag. Expressed as an absolute score-point cap per call (this runs once per sim tick),
#: not a time constant — the point is a *hard* bound on the per-tick step, so that however
#: steep exp(-(ratio**beta)) is right where ratio crosses 1, the displayed score can never
#: cross it in a single frame.
MAX_SCORE_RISE_PER_TICK = 0.01
MAX_SCORE_FALL_PER_TICK = 0.03


@dataclass
class ReliabilityEstimate:
    score: float
    recommendation: str  # "GO" | "CAUTION" | "NO-GO"


def _recommend(score: float, labels: dict[str, str]) -> str:
    if score > GO_THRESHOLD:
        key = "GO"
    elif score >= CAUTION_THRESHOLD:
        key = "CAUTION"
    else:
        key = "NO-GO"
    return labels[key]


_IDENTITY_LABEL: dict[str, str] = {"GO": "GO", "CAUTION": "CAUTION", "NO-GO": "NO-GO"}


class MissionReliabilityModel:
    def __init__(self, beta: float = BETA_DEFAULT) -> None:
        self.beta = beta
        #: Rate-limiter state, kept separate per question ("finish the mission" vs.
        #: "get back to base") — they can be asked about very different durations on the
        #: same tick and must not smooth into each other.
        self._mission_score: float | None = None
        self._recovery_score: float | None = None

    def _raw_score(
        self,
        rul_minutes: float | None,
        mission_remaining_s: float,
        overall_health: float,
    ) -> float:
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

        return max(0.0, min(1.0, score))

    @staticmethod
    def _rate_limit(prev: float | None, raw: float) -> float:
        """Clamp the step from `prev` to `raw` to a fixed max per call — the same
        per-tick rate limiting RULPredictor already applies to its own output, moved
        onto the score itself so a steep beta (or a jump in its RUL input) can no longer
        reach the display as a single-frame cliff."""
        if prev is None:
            return raw
        limit = MAX_SCORE_FALL_PER_TICK if raw < prev else MAX_SCORE_RISE_PER_TICK
        delta = max(-limit, min(limit, raw - prev))
        return max(0.0, min(1.0, prev + delta))

    def evaluate(
        self,
        rul_minutes: float | None,
        mission_remaining_s: float,
        overall_health: float,
    ) -> ReliabilityEstimate:
        raw = self._raw_score(rul_minutes, mission_remaining_s, overall_health)
        score = self._rate_limit(self._mission_score, raw)
        self._mission_score = score
        return ReliabilityEstimate(score=score, recommendation=_recommend(score, _IDENTITY_LABEL))

    def compute_recovery_reliability(
        self,
        current_health_indicators: dict[str, float],
        rul_estimate: "RULEstimate | None",
        estimated_rtb_time_minutes: float,
    ) -> ReliabilityEstimate:
        """"Can it get back to base if we abort right now?" — the same survival curve
        as `evaluate()`, asked over the estimated time-to-RTB instead of the remaining
        planned mission.

        Shares `_raw_score` with `evaluate()` rather than re-deriving the math, so the
        two scores are guaranteed the same shape and thresholds and can only ever differ
        for the reason they are supposed to: a shorter (or longer) target duration. Rate
        limiting runs through its own `_recovery_score` state, not `evaluate()`'s — the
        two questions can be asked over very different durations on the same tick and
        must not smooth into each other. `current_health_indicators` is folded into a
        single overall-health scalar exactly the way `AnomalyDetector` already does it —
        `0.65 * worst + 0.35 * mean` (see app/ml/anomaly_detector.py) — so both
        reliability numbers read the engine's condition identically and diverge only on
        duration, never on how "healthy" is defined.
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

        raw = self._raw_score(
            rul_minutes=rul_minutes,
            mission_remaining_s=max(0.0, estimated_rtb_time_minutes) * 60.0,
            overall_health=overall_health,
        )
        score = self._rate_limit(self._recovery_score, raw)
        self._recovery_score = score

        return ReliabilityEstimate(score=score, recommendation=_recommend(score, _RECOVERY_LABEL))


#: GO/CAUTION/NO-GO relabelled for the recovery question, so the two recommendations can
#: never be visually mistaken for each other even when they agree on how bad things are.
_RECOVERY_LABEL: dict[str, str] = {
    "GO": "RTB-SAFE",
    "CAUTION": "RTB-CAUTION",
    "NO-GO": "RTB-AT-RISK",
}
