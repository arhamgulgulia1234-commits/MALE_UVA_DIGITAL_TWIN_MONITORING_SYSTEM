"""Per-subsystem anomaly scores and health indicators.

Two questions have to be answered separately, and conflating them is the classic mistake:

  * **Is this real?**  — answered by the z-score from app/twin/residual_analysis.py.
    A sustained residual against a low-noise channel produces a very large z, so z is
    excellent for *detection* but useless for ranking severity: once a fault is obvious,
    every affected channel saturates and every subsystem looks equally dead.
  * **How bad is it?** — answered by the residual *magnitude* relative to a per-channel
    full-scale deviation (the size of residual that means "this subsystem is gone").

So each channel contributes `gate(z) * magnitude`, where the gate rejects statistical
noise and the magnitude carries the severity. That is what lets a misfire read as a
cylinder fault with a mild cooling side-effect, rather than pinning every subsystem at
zero at once.

Health Indicator: HI = 100 * (1 - anomaly_score), which the RUL predictor extrapolates
and the frontend renders as the subsystem score.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.twin.residual_analysis import ResidualReport

SUBSYSTEMS: tuple[str, ...] = ("cylinder", "lubrication", "cooling", "fuel", "turbo")

#: subsystem -> list of (channel, weight, full_scale_residual, direction).
#:
#: `full_scale_residual` is the residual magnitude treated as complete degradation of
#: that channel. `direction` encodes which *sign* of residual actually indicts this
#: subsystem: +1 = only a rise is a symptom, -1 = only a fall, 0 = either.
#:
#: Direction matters more than it looks. Any fault that costs the engine power also cools
#: the cylinder head, so an undirected CHT channel would report "cooling degradation"
#: every time the air filter clogged — exactly backwards. Only a head running *hotter*
#: than the twin indicts the cooling system.
SUBSYSTEM_CHANNELS: dict[str, list[tuple[str, float, float, int]]] = {
    "cylinder": [
        ("egt_spread_c", 1.00, 90.0, 0),
        ("crest_factor_max", 0.65, 3.0, +1),
        ("vibration_rms_max", 0.55, 0.35, +1),
        ("torque_brake_nm", 0.45, 60.0, -1),
    ],
    "lubrication": [
        ("oil_pressure_kpa", 1.00, 150.0, -1),
        ("oil_temp_c", 0.60, 35.0, +1),
        ("vibration_rms_mean", 0.55, 0.25, +1),
    ],
    "cooling": [
        ("cht_c", 1.00, 90.0, +1),
        ("oil_temp_c", 0.40, 45.0, +1),
    ],
    "fuel": [
        ("afr_mean", 1.00, 1.2, +1),
        ("fuel_flow_lph", 0.70, 5.0, 0),
        ("egt_spread_c", 0.55, 130.0, 0),
        ("egt_mean_c", 0.40, 90.0, 0),
    ],
    "turbo": [
        ("boost_pressure_kpa", 1.00, 32.0, -1),
        ("manifold_pressure_kpa", 0.75, 30.0, -1),
        ("vib_band_high", 0.35, 0.30, +1),
    ],
}

HI_MAX = 100.0


@dataclass
class AnomalyReport:
    anomaly_scores: dict[str, float]      # 0-1 per subsystem
    health_indicators: dict[str, float]   # 0-100 per subsystem
    overall_health: float                 # 0-100
    flagged_channels: list[str]

    def worst_subsystem(self) -> str:
        return min(self.health_indicators, key=lambda k: self.health_indicators[k])


class AnomalyDetector:
    def __init__(self, gate_z: float = 2.5, gate_width: float = 1.5,
                 smoothing_tau_s: float = 4.0) -> None:
        #: z below `gate_z` is treated as noise; the gate ramps to 1 over `gate_width`
        #: so a fault fades in smoothly instead of latching on.
        self.gate_z = gate_z
        self.gate_width = gate_width
        #: Smoothing memory in *simulated* seconds, so the gauge responds the
        #: same way whether the demo is running at 1x or 20x.
        self.smoothing_tau_s = smoothing_tau_s
        self._smoothed: dict[str, float] = {s: 0.0 for s in SUBSYSTEMS}

    def reset(self) -> None:
        self._smoothed = {s: 0.0 for s in SUBSYSTEMS}

    def _gate(self, z: float) -> float:
        if z <= self.gate_z:
            return 0.0
        return min(1.0, (z - self.gate_z) / max(1e-6, self.gate_width))

    def update(self, report: ResidualReport, dt_s: float = 0.1) -> AnomalyReport:
        smoothing = 1.0 - math.exp(-max(1e-6, dt_s) / max(1e-6, self.smoothing_tau_s))
        scores: dict[str, float] = {}

        for subsystem, channels in SUBSYSTEM_CHANNELS.items():
            contributions: list[float] = []
            for channel, weight, full_scale, direction in channels:
                residual = report.mean(channel)
                # Wrong-signed deviations are not symptoms of *this* subsystem.
                if direction > 0:
                    signed = max(0.0, residual)
                elif direction < 0:
                    signed = max(0.0, -residual)
                else:
                    signed = abs(residual)
                gate = self._gate(report.z(channel))
                magnitude = min(1.0, signed / full_scale)
                contributions.append(weight * gate * magnitude)

            if contributions:
                worst = max(contributions)
                mean = sum(contributions) / len(contributions)
                raw = min(1.0, 0.75 * worst + 0.25 * mean)
            else:
                raw = 0.0

            # Smooth so the gauge moves deliberately rather than jittering.
            self._smoothed[subsystem] += (raw - self._smoothed[subsystem]) * smoothing
            scores[subsystem] = max(0.0, min(1.0, self._smoothed[subsystem]))

        his = {s: max(0.0, HI_MAX * (1.0 - scores[s])) for s in SUBSYSTEMS}

        # Overall is pulled down by the worst subsystem, not an average that hides it.
        worst_hi = min(his.values())
        mean_hi = sum(his.values()) / len(his)
        overall = max(0.0, min(HI_MAX, 0.65 * worst_hi + 0.35 * mean_hi))

        return AnomalyReport(
            anomaly_scores=scores,
            health_indicators=his,
            overall_health=overall,
            flagged_channels=report.flagged_channels(),
        )
