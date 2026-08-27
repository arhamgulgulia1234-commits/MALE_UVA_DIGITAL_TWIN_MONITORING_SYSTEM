"""Brake specific fuel consumption and its trend.

    BSFC [g/kWh] = fuel_mass_flow [g/h] / brake_power [kW]

BSFC is the cleanest single scalar for "how much fuel is this engine wasting to make a
given amount of work", and it is a genuinely *non-specific* early indicator: almost every
fault in the catalogue degrades it, usually before any single channel crosses a threshold.
A leaking injector, a slipping turbo, a retarded injection event and a fouled plug all
show up here as the same symptom — the engine working harder for the same output.

That non-specificity is the point. This is a "something is costing you fuel" alarm that
runs in parallel with the twin residuals, not a replacement for them: the residual layer
says *which subsystem*, BSFC says *how much it is actually costing the mission*.

Two details worth stating plainly:

  * BSFC is meaningless at low power — dividing by a near-zero denominator explodes. Below
    `min_power_kw` the sample is skipped entirely rather than being clamped, so idle and
    descent do not poison the trend.
  * The trend compares a recent window against an older baseline window *of the same
    length*, and only reports a verdict once both are populated. Comparing an instantaneous
    value against a long average would flag every climb as "degrading" simply because
    climbing costs more fuel per kW than cruising.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

#: Fuel density is needed to turn volumetric flow (L/h) into mass flow (g/h).
DEFAULT_FUEL_DENSITY_KG_PER_L = 0.72


@dataclass
class EfficiencyReport:
    bsfc_g_per_kwh: float | None
    trend: str  # "stable" | "degrading" | "improving"
    baseline_bsfc: float | None
    recent_bsfc: float | None
    change_pct: float


class EfficiencyAnalyser:
    def __init__(
        self,
        fuel_density_kg_per_l: float = DEFAULT_FUEL_DENSITY_KG_PER_L,
        min_power_kw: float = 6.0,
        window: int = 150,
        degrade_threshold_pct: float = 4.0,
        smoothing_tau_s: float = 8.0,
    ) -> None:
        self.fuel_density = fuel_density_kg_per_l
        self.min_power_kw = min_power_kw
        self.window = window
        self.degrade_threshold_pct = degrade_threshold_pct
        self.smoothing_tau_s = smoothing_tau_s
        #: Older baseline window and recent window, same length.
        self._history: deque[float] = deque(maxlen=window * 2)
        self._smoothed: float | None = None

    def reset(self) -> None:
        self._history.clear()
        self._smoothed = None

    def update(
        self, fuel_flow_lph: float, power_kw: float, dt_s: float = 0.1
    ) -> EfficiencyReport:
        import math

        # Below this the denominator is too small for BSFC to mean anything.
        if power_kw < self.min_power_kw or fuel_flow_lph <= 0.0:
            return EfficiencyReport(
                bsfc_g_per_kwh=self._smoothed,
                trend="stable",
                baseline_bsfc=None,
                recent_bsfc=None,
                change_pct=0.0,
            )

        fuel_g_per_h = fuel_flow_lph * self.fuel_density * 1000.0
        bsfc = fuel_g_per_h / power_kw

        if self._smoothed is None:
            self._smoothed = bsfc
        else:
            alpha = 1.0 - math.exp(-max(1e-6, dt_s) / max(1e-6, self.smoothing_tau_s))
            self._smoothed += (bsfc - self._smoothed) * alpha

        self._history.append(bsfc)

        trend = "stable"
        baseline = recent = None
        change_pct = 0.0

        if len(self._history) == self._history.maxlen:
            values = list(self._history)
            baseline_vals = values[: self.window]
            recent_vals = values[self.window :]
            baseline = sum(baseline_vals) / len(baseline_vals)
            recent = sum(recent_vals) / len(recent_vals)
            if baseline > 1e-6:
                change_pct = 100.0 * (recent - baseline) / baseline
                if change_pct > self.degrade_threshold_pct:
                    trend = "degrading"
                elif change_pct < -self.degrade_threshold_pct:
                    trend = "improving"

        return EfficiencyReport(
            bsfc_g_per_kwh=self._smoothed,
            trend=trend,
            baseline_bsfc=baseline,
            recent_bsfc=recent,
            change_pct=change_pct,
        )
