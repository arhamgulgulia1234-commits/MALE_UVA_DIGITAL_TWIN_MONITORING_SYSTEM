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

---

## Early warning: the same trend fit, a gentler threshold, gated by pre-alert

`update_early_warnings` (bottom of this file) answers an earlier question than the RUL
above: not "minutes until failure (HI=40)" but "minutes until this subsystem crosses a
much gentler warning line (HI=75) it may still be well clear of today." It deliberately
reuses `_estimate_for` — the identical linear/exponential trend fit against the identical
per-subsystem history this class already keeps — parameterised with a different failure
threshold, rather than a second copy of the fitting logic.

It also reuses this class's own fix for the RUL-jump bug (see `smoothing_tau_fall_s`
below): the same asymmetric EMA — slow to rise, fast to fall — is applied per-subsystem to
the warning-threshold estimate, for the identical reason. Without it, a warning ETA seeded
high the instant a trend first becomes fittable would suffer the same "worse degradation
reads as more time remaining" inversion this file already fixed once for the critical RUL.

The gate is external: `update_early_warnings` is only asked about subsystems the caller
(`SimulationLoop`) has already determined are at least `"emerging"` in
`app.twin.residual_analysis.PreAlertMonitor` — a subsystem not in that set is skipped
entirely, `predicted_minutes=None`, rather than fitting a trend to what is still noise.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

HI_FAILURE_THRESHOLD = 40.0
#: Meaningfully above the critical/RUL threshold — early warning fires while a subsystem
#: may still read as broadly healthy on the dashboard's own gauge.
HI_WARNING_THRESHOLD = 75.0

#: Confidence-label spans, in *simulated* seconds of consistent history actually observed
#: — not sample count alone, so a 20x demo does not read as "high confidence" faster than
#: a 1x one just because more ticks arrived per wall-clock second. Chosen so "a prediction
#: based on 3 data points" (barely past `min_samples`, seconds of span) reads low, and "3
#: minutes of consistent trend" reads high, matching the spec's own two examples.
WARNING_CONFIDENCE_LOW_SPAN_S = 60.0
WARNING_CONFIDENCE_HIGH_SPAN_S = 180.0


def _confidence_label(n_samples: int, span_s: float, min_samples: int) -> str:
    if n_samples < min_samples or span_s < WARNING_CONFIDENCE_LOW_SPAN_S:
        return "low"
    if span_s < WARNING_CONFIDENCE_HIGH_SPAN_S:
        return "medium"
    return "high"


@dataclass
class WarningEstimate:
    subsystem: str
    predicted_minutes: float | None
    confidence: str  # "low" | "medium" | "high"
    model: str
    slope_per_min: float


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
        #: Held at `max_minutes` ("practically nominal") whenever no trend is fittable,
        #: rather than `None` — see the gate-to-active handoff note in `update()` below.
        self._smoothed: float = max_minutes
        self._last_time_s: float | None = None
        #: Early-warning smoothing state is per-subsystem (unlike `_smoothed` above, which
        #: tracks only the single worst-subsystem critical RUL) because more than one
        #: subsystem can be gated into early warning at once.
        self._warning_smoothed: dict[str, float] = {}
        self._warning_last_time_s: float | None = None
        #: The last estimate `update(evaluate=True)` produced, re-served unchanged while
        #: evaluation is being paced — see `update`'s `evaluate` argument.
        self._last_estimate = RULEstimate(None, None, "stable", 0.0)

    def reset(self) -> None:
        self._history.clear()
        self._smoothed = self.max_minutes
        self._last_time_s = None
        self._warning_smoothed.clear()
        self._warning_last_time_s = None
        self._last_estimate = RULEstimate(None, None, "stable", 0.0)

    def update(
        self,
        sim_time_s: float,
        health_indicators: dict[str, float],
        evaluate: bool = True,
    ) -> RULEstimate:
        """Record this tick's health indicators and (by default) refit the trend.

        `evaluate=False` records the sample and prunes the window as usual but re-serves
        the previous estimate instead of refitting. `_estimate_for` is a least-squares fit
        over the *whole* window, run once per subsystem — at 10 Hz on a 5 minute window
        that is six fits over ~3 000 samples every 100 ms, to extrapolate a trend measured
        in minutes. The smoothing EMA below is anchored to elapsed time rather than to a
        tick count (`alpha = 1 - exp(-dt/tau)`, with `dt` taken from `sim_time_s`), so a
        held tick does not distort it: the next evaluation simply advances the filter by
        the full interval. Paced by `settings.phm_analysis_interval_s`.
        """
        for subsystem, hi in health_indicators.items():
            hist = self._history.setdefault(subsystem, deque())
            hist.append((sim_time_s, hi))
            while hist and sim_time_s - hist[0][0] > self.window_s:
                hist.popleft()

        if not evaluate:
            return self._last_estimate

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
            # No fittable trend right now — publish nothing (the dashboard shows a dash),
            # but deliberately leave `self._smoothed` untouched rather than resetting it.
            # It is the gate's *hold* value: whenever a trend next becomes fittable — the
            # very first time ever (still sitting at `max_minutes` from __init__), or
            # again after a noisy tick or a cleared fault re-develops — the EMA below
            # resumes from here instead of jumping straight to a freshly-computed raw
            # estimate. That seed-from-raw jump is what used to make the readout snap the
            # instant the minimum-samples gate opened.
            self._last_estimate = RULEstimate(None, None, "stable", 0.0)
            return self._last_estimate

        # Smooth the reported figure. The underlying least-squares fit is jumpy while a
        # fault is still ramping, and an RUL readout that leaps between 25 and 600 minutes
        # is worse than useless to an operator deciding whether to abort.
        raw = best.minutes if best.minutes is not None else 0.0
        tau = (
            self.smoothing_tau_s
            if raw >= self._smoothed
            else self.smoothing_tau_fall_s
        )
        alpha = 1.0 - math.exp(-dt_s / max(1e-6, tau))
        self._smoothed += (raw - self._smoothed) * alpha

        self._last_estimate = RULEstimate(
            minutes=max(0.0, self._smoothed),
            subsystem=best.subsystem,
            model=best.model,
            slope_per_min=best.slope_per_min,
        )
        return self._last_estimate

    def _estimate_for(
        self,
        subsystem: str,
        hist: "deque[tuple[float, float]]",
        hi_failure: float | None = None,
    ) -> RULEstimate:
        """Fit the trend and extrapolate to `hi_failure` (default: the critical RUL
        threshold, `self.hi_failure`). `update_early_warnings` below calls this with
        `HI_WARNING_THRESHOLD` instead — same fit, a different target line — so the two
        predictions can never quietly drift apart into two different trend models."""
        threshold = self.hi_failure if hi_failure is None else hi_failure
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
        if current_hi <= threshold:
            return RULEstimate(0.0, subsystem, "linear", slope)

        # Try the exponential fit on log(HI - threshold); fall back to linear.
        exp_minutes: float | None = None
        log_pairs = [
            (x, math.log(v - threshold))
            for x, v in zip(xs, values)
            if v - threshold > 1e-3
        ]
        if len(log_pairs) >= self.min_samples // 2:
            lx = [p[0] for p in log_pairs]
            ly = [p[1] for p in log_pairs]
            k_slope, k_intercept = _linreg(lx, ly)
            if k_slope < -1e-6:
                # HI(t) = threshold + exp(k_intercept + k_slope*t); solve HI(t) = threshold
                # is asymptotic, so solve for the point where the exponential term
                # decays to 1% of the failure margin — a finite, well-posed horizon.
                target = math.log(max(1e-3, 0.01 * (values[0] - threshold)))
                t_fail = (target - k_intercept) / k_slope
                exp_minutes = t_fail - xs[-1]

        lin_minutes = (threshold - intercept) / slope - xs[-1]

        if exp_minutes is not None and 0.0 <= exp_minutes < lin_minutes:
            minutes, model = exp_minutes, "exponential"
        else:
            minutes, model = lin_minutes, "linear"

        minutes = max(0.0, min(self.max_minutes, minutes))
        return RULEstimate(minutes, subsystem, model, slope)

    # ---- early warning ---------------------------------------------------------

    def update_early_warnings(
        self,
        sim_time_s: float,
        subsystem_pre_alert_levels: dict[str, str],
    ) -> dict[str, WarningEstimate]:
        """One `WarningEstimate` per subsystem currently gated in by pre-alert.

        Must be called after `update()` in the same tick — it reads `self._history`,
        which `update()` is what appends the current tick's HI samples to. Takes the worst
        pre-alert level per subsystem (`"none"` / `"emerging"` / `"building"`) rather than
        raw per-channel results, matching the granularity `MaintenanceAdvisor.
        generate_early_warning` and the `early_warnings` telemetry field both operate at.

        A subsystem at `"none"` is skipped entirely — no entry in the returned dict, and
        its smoothing state is dropped so a later re-trigger starts fresh rather than
        resuming a stale smoothed value from a previous, unrelated episode.
        """
        dt_s = (
            0.0
            if self._warning_last_time_s is None
            else max(0.0, sim_time_s - self._warning_last_time_s)
        )
        self._warning_last_time_s = sim_time_s

        results: dict[str, WarningEstimate] = {}
        for subsystem, hist in self._history.items():
            level = subsystem_pre_alert_levels.get(subsystem, "none")
            if level == "none":
                self._warning_smoothed.pop(subsystem, None)
                continue

            estimate = self._estimate_for(subsystem, hist, hi_failure=HI_WARNING_THRESHOLD)
            n = len(hist)
            span_s = (hist[-1][0] - hist[0][0]) if n >= 2 else 0.0
            confidence = _confidence_label(n, span_s, self.min_samples)
            minutes = self._smooth_warning(subsystem, estimate.minutes, dt_s)

            results[subsystem] = WarningEstimate(
                subsystem=subsystem,
                predicted_minutes=minutes,
                confidence=confidence,
                model=estimate.model,
                slope_per_min=estimate.slope_per_min,
            )
        return results

    def _smooth_warning(
        self, subsystem: str, raw: float | None, dt_s: float
    ) -> float | None:
        """The same asymmetric EMA as the critical-RUL smoothing above, kept per-subsystem.

        `raw is None` means pre-alert has fired but the trend fit itself does not have
        enough samples yet (`_estimate_for` needs `min_samples`, pre-alert's own gate can
        trip on fewer). Rather than holding a stale smoothed number across that gap, drop
        it — `confidence` already tells the caller a number is not available yet, which is
        the honest state, not a held-over estimate from before."""
        if raw is None:
            self._warning_smoothed.pop(subsystem, None)
            return None
        prev = self._warning_smoothed.get(subsystem)
        if prev is None:
            self._warning_smoothed[subsystem] = raw
            return raw
        tau = self.smoothing_tau_s if raw >= prev else self.smoothing_tau_fall_s
        alpha = 1.0 - math.exp(-dt_s / max(1e-6, tau))
        new = prev + (raw - prev) * alpha
        self._warning_smoothed[subsystem] = new
        return max(0.0, new)


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
