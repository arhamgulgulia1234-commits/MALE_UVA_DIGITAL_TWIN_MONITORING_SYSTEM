"""Remaining Useful Life from Health Indicator trend extrapolation.

Keeps a time-stamped history of each subsystem's HI over the last N minutes of
*simulated* time, fits a trend, and extrapolates forward to the failure threshold
(HI = 40).

Two fits are attempted and the better-conditioned one wins:
  * **linear**       HI(t) = a + b*t          -> t_fail = (HI_fail - a) / b
  * **exponential**  HI(t) = HI_fail + C*exp(-k*t), fitted as a linear regression on
                     log(HI - HI_fail); this captures accelerating degradation, which is
                     the usual shape for wear-driven faults.

Returns `None` when the HI is stable or improving — a healthy engine has no meaningful
RUL and the dashboard shows a dash rather than a fabricated number.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

HI_FAILURE_THRESHOLD = 40.0


@dataclass
class RULEstimate:
    minutes: float | None
    subsystem: str | None
    model: str  # "linear" | "exponential" | "stable"
    slope_per_min: float


class RULPredictor:
    def __init__(
        self,
        window_minutes: float = 5.0,
        min_samples: int = 25,
        hi_failure: float = HI_FAILURE_THRESHOLD,
        max_minutes: float = 600.0,
        min_slope_per_min: float = 0.05,
        smoothing_tau_s: float = 20.0,
        smoothing_tau_fall_s: float = 3.0,
    ) -> None:
        self.window_s = window_minutes * 60.0
        self.min_samples = min_samples
        self.hi_failure = hi_failure
        self.max_minutes = max_minutes
        #: A trend shallower than this is noise, not degradation — reporting an RUL from
        #: it produces the wild swings you get from dividing by a near-zero slope.
        self.min_slope_per_min = min_slope_per_min
        self.smoothing_tau_s = smoothing_tau_s
        #: Smoothing is deliberately asymmetric: a *rising* RUL is damped with
        #: `smoothing_tau_s` to stop the readout flickering, but a *falling* one is let
        #: through on this much shorter constant.
        #:
        #: Symmetric smoothing was actively dangerous here. The estimate is seeded at the
        #: `max_minutes` clamp (600) the first time a trend becomes fittable, and a 20 s
        #: constant cannot come down from there faster than a fault can develop. On a 45 s
        #: bearing-wear ramp the underlying fit said **0.0 minutes** while the frame
        #: reported **409 minutes** — the engine sitting at the failure threshold and the
        #: dashboard showing nearly seven hours of life. The faster the degradation, the
        #: more optimistic the readout became, which is precisely backwards. Lag is
        #: acceptable when the news is getting better; it is not when it is getting worse.
        self.smoothing_tau_fall_s = smoothing_tau_fall_s
        self._history: dict[str, deque[tuple[float, float]]] = {}
        self._smoothed: float | None = None
        self._last_time_s: float | None = None

    def reset(self) -> None:
        self._history.clear()
        self._smoothed = None
        self._last_time_s = None

    def update(self, sim_time_s: float, health_indicators: dict[str, float]) -> RULEstimate:
        for subsystem, hi in health_indicators.items():
            hist = self._history.setdefault(subsystem, deque())
            hist.append((sim_time_s, hi))
            while hist and sim_time_s - hist[0][0] > self.window_s:
                hist.popleft()

        best: RULEstimate | None = None
        for subsystem, hist in self._history.items():
            estimate = self._estimate_for(subsystem, hist)
            if estimate.minutes is None:
                continue
            # `best.minutes` is never None here — the `continue` above filters those out
            # — so compare it directly. It used to read `(best.minutes or math.inf)`,
            # which treats a legitimate RUL of exactly 0.0 as "no estimate" because 0.0
            # is falsy: the instant a subsystem reached the failure threshold and
            # correctly reported 0 minutes, any healthier subsystem's longer estimate
            # replaced it. The readout jumped *up* at the exact moment it should have
            # bottomed out.
            if best is None or estimate.minutes < best.minutes:
                best = estimate

        dt_s = 0.0 if self._last_time_s is None else max(0.0, sim_time_s - self._last_time_s)
        self._last_time_s = sim_time_s

        if best is None:
            self._smoothed = None
            return RULEstimate(None, None, "stable", 0.0)

        # Smooth the reported figure. The underlying least-squares fit is jumpy while a
        # fault is still ramping, and an RUL readout that leaps between 25 and 600 minutes
        # is worse than useless to an operator deciding whether to abort.
        raw = best.minutes if best.minutes is not None else 0.0
        if self._smoothed is None:
            self._smoothed = raw
        else:
            tau = (
                self.smoothing_tau_s
                if raw >= self._smoothed
                else self.smoothing_tau_fall_s
            )
            alpha = 1.0 - math.exp(-dt_s / max(1e-6, tau))
            self._smoothed += (raw - self._smoothed) * alpha

        return RULEstimate(
            minutes=max(0.0, self._smoothed),
            subsystem=best.subsystem,
            model=best.model,
            slope_per_min=best.slope_per_min,
        )

    def _estimate_for(
        self, subsystem: str, hist: "deque[tuple[float, float]]"
    ) -> RULEstimate:
        if len(hist) < self.min_samples:
            return RULEstimate(None, subsystem, "stable", 0.0)

        times = [t for t, _ in hist]
        values = [v for _, v in hist]
        t0 = times[0]
        xs = [(t - t0) / 60.0 for t in times]  # minutes

        slope, intercept = _linreg(xs, values)
        current_hi = values[-1]

        # Improving, flat, or drifting too gently to distinguish from noise.
        if slope >= -self.min_slope_per_min:
            return RULEstimate(None, subsystem, "stable", slope)
        if current_hi <= self.hi_failure:
            return RULEstimate(0.0, subsystem, "linear", slope)

        # Try the exponential fit on log(HI - HI_fail); fall back to linear.
        exp_minutes: float | None = None
        log_pairs = [
            (x, math.log(v - self.hi_failure))
            for x, v in zip(xs, values)
            if v - self.hi_failure > 1e-3
        ]
        if len(log_pairs) >= self.min_samples // 2:
            lx = [p[0] for p in log_pairs]
            ly = [p[1] for p in log_pairs]
            k_slope, k_intercept = _linreg(lx, ly)
            if k_slope < -1e-6:
                # HI(t) = HI_fail + exp(k_intercept + k_slope*t); solve HI(t) = HI_fail
                # is asymptotic, so solve for the point where the exponential term
                # decays to 1% of the failure margin — a finite, well-posed horizon.
                target = math.log(max(1e-3, 0.01 * (values[0] - self.hi_failure)))
                t_fail = (target - k_intercept) / k_slope
                exp_minutes = t_fail - xs[-1]

        lin_minutes = (self.hi_failure - intercept) / slope - xs[-1]

        if exp_minutes is not None and 0.0 <= exp_minutes < lin_minutes:
            minutes, model = exp_minutes, "exponential"
        else:
            minutes, model = lin_minutes, "linear"

        minutes = max(0.0, min(self.max_minutes, minutes))
        return RULEstimate(minutes, subsystem, model, slope)


def _linreg(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Ordinary least squares -> (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 1e-12:
        return 0.0, mean_y
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, mean_y - slope * mean_x
