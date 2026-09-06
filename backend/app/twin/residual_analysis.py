"""Statistical monitoring of twin residuals.

For each residual channel we keep an exponentially-weighted moving average of the value
and of its variance:

    mu_t     = a * x_t + (1 - a) * mu_{t-1}
    var_t    = a * (x_t - mu_{t-1})^2 + (1 - a) * var_{t-1}
    z_t      = |mu_t| / max(sqrt(var_t), floor)

A channel is *flagged* when z crosses `z_threshold` — that is, when its residual has
drifted by more than a configurable multiple of its own recent noise. This is the actual
anomaly signal: it adapts to how noisy each channel normally is rather than relying on
fixed thresholds against raw telemetry values. **This is the existing hard fault-alert
threshold** the pre-alert layer below sits in front of — `ResidualMonitor.flagged` is what
ultimately drives `AnomalyDetector`'s gate and, through it, the fault classifier and the
dashboard's FaultAlertFeed. Nothing about `ResidualMonitor` changes in this file.

The variance floor comes from `CHANNEL_SCALES` in app/physics/plant.py so a quiet channel
whose variance has collapsed toward zero cannot produce an infinite z-score.

`feature_vector()` is the single shared definition of the ML feature layout — both
app/ml/train/generate_training_data.py (offline) and app/ml/fault_classifier.py (at
runtime) call it, so training and inference can never drift apart.

---

## Pre-alert: an earlier, softer tier in front of the z-score gate

`ResidualMonitor` answers "has this channel drifted past N sigma of its own noise?" — a
single EWMA, tuned to be a confident, low-false-positive trigger. That confidence is
bought with lag: by design it does not fire on a residual that is merely *starting* to
misbehave while still within the noise band a raw z-score would tolerate.

`PreAlertMonitor` below answers a different, earlier question over the same residual
streams: not "is this now anomalous" but "has this channel's *behaviour* started to
change, even while its current value still looks unremarkable?" Two independent,
statistically-gated tests, each looking at genuinely different shapes of onset:

  * **Variance-ratio test.** Compares the variance of a short recent window against an
    established baseline window immediately before it. A fault whose real signature is
    added noise (e.g. `bearing_wear`'s broadband vibration) can widen a channel's spread
    well before its *mean* has moved enough to trip a z-score gate keyed on the mean.
  * **Trend-slope test.** Fits an ordinary-least-squares slope to the recent window and
    tests it against its own standard error (a t-statistic), so "small but consistent" is
    judged relative to how noisy the channel already is, not against a fixed slope in
    physical units. A fault whose signature is a smooth mean shift (e.g. `bearing_wear`'s
    effect on `oil_pressure_kpa` itself) shows up here as a trend long before the mean has
    moved the several sigma a z-score gate requires.

A channel is **emerging** when exactly one test fires, **building** when both do — a
strictly softer, earlier-stage classification than `ResidualMonitor.flagged`, and neither
test touches `ResidualMonitor`'s own EWMA state or `z_threshold`. Both monitors read the
identical residual dict every tick; they simply ask different questions of it.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Literal, Sequence

from app.physics.plant import CHANNEL_SCALES, CHANNELS


@dataclass
class ChannelStats:
    mean: float = 0.0
    variance: float = 0.0
    z_score: float = 0.0
    flagged: bool = False


@dataclass
class ResidualReport:
    stats: dict[str, ChannelStats]
    warmed_up: bool

    def z(self, channel: str) -> float:
        stat = self.stats.get(channel)
        return stat.z_score if stat else 0.0

    def mean(self, channel: str) -> float:
        stat = self.stats.get(channel)
        return stat.mean if stat else 0.0

    def flagged_channels(self) -> list[str]:
        return [name for name, s in self.stats.items() if s.flagged]


class ResidualMonitor:
    """EWMA statistics over the twin residuals.

    The smoothing constant is expressed as a time constant in *simulated* seconds and
    converted per update via `alpha = 1 - exp(-dt/tau)`. That matters: the operator can
    run the simulation at 20x for a demo, and a filter whose memory was measured in ticks
    would then appear to take twenty times longer in simulated time to notice a fault.
    Anchoring it to simulated time keeps the diagnosis responsive at every time scale.
    """

    def __init__(
        self,
        tau_s: float = 6.0,
        z_threshold: float = 3.0,
        warmup_s: float = 20.0,
        variance_floor_fraction: float = 0.08,
    ) -> None:
        self.tau_s = tau_s
        self.z_threshold = z_threshold
        self.warmup_s = warmup_s
        self.variance_floor_fraction = variance_floor_fraction
        self._stats: dict[str, ChannelStats] = {c: ChannelStats() for c in CHANNELS}
        self._elapsed_s = 0.0

    def reset(self) -> None:
        self._stats = {c: ChannelStats() for c in CHANNELS}
        self._elapsed_s = 0.0

    def _floor(self, channel: str) -> float:
        return CHANNEL_SCALES.get(channel, 1.0) * self.variance_floor_fraction

    def update(self, residuals: dict[str, float], dt_s: float = 0.1) -> ResidualReport:
        self._elapsed_s += dt_s
        a = 1.0 - math.exp(-max(1e-6, dt_s) / max(1e-6, self.tau_s))
        warmed = self._elapsed_s >= self.warmup_s
        for channel in CHANNELS:
            x = residuals.get(channel, 0.0)
            stat = self._stats[channel]
            prev_mean = stat.mean
            stat.mean = a * x + (1.0 - a) * prev_mean
            stat.variance = a * (x - prev_mean) ** 2 + (1.0 - a) * stat.variance
            std = max(stat.variance**0.5, self._floor(channel))
            stat.z_score = abs(stat.mean) / std
            stat.flagged = warmed and stat.z_score >= self.z_threshold
        return ResidualReport(stats=dict(self._stats), warmed_up=warmed)

    # ---- ML feature layout ---------------------------------------------------

    def feature_names(self) -> list[str]:
        names: list[str] = []
        for channel in CHANNELS:
            names.append(f"{channel}__norm_mean")
            names.append(f"{channel}__z")
            names.append(f"{channel}__norm_std")
        return names

    def feature_vector(self) -> list[float]:
        """Normalised residual mean, z-score and dispersion per channel.

        Normalising by the channel scale keeps every feature O(1) so a RandomForest is
        not dominated by the RPM channel's raw magnitude.

        The dispersion term exists specifically for noise-type sensor faults. A sensor
        that has become *noisy* rather than *biased* leaves the residual mean near zero —
        the noise cancels — so mean and z alone cannot see it, and no amount of threshold
        tuning will help. What changes is the spread. Carrying std as its own feature is
        what lets the classifier separate "this reading is wrong" from "this reading is
        unreliable", which are different maintenance actions."""
        values: list[float] = []
        for channel in CHANNELS:
            stat = self._stats[channel]
            scale = CHANNEL_SCALES.get(channel, 1.0)
            values.append(stat.mean / scale)
            values.append(min(stat.z_score, 50.0))
            values.append(min((stat.variance**0.5) / scale, 50.0))
        return values


# ---------------------------------------------------------------------------
# Pre-alert: fluctuation detection ahead of the z-score gate
# ---------------------------------------------------------------------------

PreAlertLevel = Literal["none", "emerging", "building"]

#: Recent window the variance-ratio and trend-slope tests are evaluated over.
DEFAULT_SHORT_WINDOW_S = 45.0
#: Established-behaviour window immediately preceding the recent window. Long enough that
#: a single throttle/altitude transient passing through it does not read as "the baseline
#: was always this noisy" — mission-phase changes are minutes apart, this window is 2.5.
DEFAULT_BASELINE_WINDOW_S = 150.0
#: recent variance / baseline variance must clear this to count as a variance-ratio hit.
DEFAULT_VARIANCE_RATIO_THRESHOLD = 2.25
#: |slope| / standard-error-of-slope must clear this — a t-statistic, so "significant"
#: scales with how noisy the channel already is rather than a fixed physical-unit slope.
DEFAULT_TREND_T_THRESHOLD = 2.5
#: Each window needs at least this many samples before its own test is allowed to fire —
#: guards both against evaluating a variance/regression over a handful of points early in
#: a mission, and against a channel with very few residual updates ever triggering.
DEFAULT_MIN_WINDOW_SAMPLES = 10
#: Of the recent window's step-to-step deltas, at least this fraction must share the
#: fitted slope's sign for the trend to count as "sustained" rather than one big jump
#: dressed up by a regression line — the "not noise-driven" half of the spec.
DEFAULT_TREND_CONSISTENCY_FRACTION = 0.55
#: The trend test additionally requires the recent window to *span* at least this much
#: simulated time, not just accumulate `min_window_samples` — sample count alone is a
#: tick-rate-dependent proxy (10 samples is under a second at ~9 Hz) and a regression over
#: that little data is exactly the "noise-driven" trend the spec warns against.
DEFAULT_MIN_TREND_SPAN_S = 20.0
#: Neither test is trusted until this much simulated time has elapsed since the monitor
#: was created — both plants share an identical cold start, and the startup transient
#: (RPM ramping off idle, manifold filling, etc.) is a genuine, large, *shared* change
#: that briefly perturbs the residual before the two plants settle into lockstep. Without
#: this gate the trend test in particular can trip on that transient in the first few
#: seconds of a mission, which is a false positive, not an early fault signature.
DEFAULT_WARMUP_S = DEFAULT_SHORT_WINDOW_S + DEFAULT_BASELINE_WINDOW_S


@dataclass
class PreAlertResult:
    level: PreAlertLevel
    variance_ratio: float | None = None
    variance_triggered: bool = False
    trend_t_stat: float | None = None
    trend_slope: float | None = None
    trend_triggered: bool = False


def _ols_slope_t_stat(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Ordinary least squares -> (slope, t-statistic of that slope against zero).

    A local, private OLS fit rather than importing `app.ml.rul_predictor._linreg`: the
    two live in different layers (twin residuals vs. HI trend extrapolation) that happen
    to both need a six-line regression, and this one additionally needs the fit's standard
    error, which `_linreg` does not compute. Duplicating six lines beats introducing a
    twin -> ml import for it.
    """
    n = len(xs)
    if n < 3:
        return 0.0, 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 1e-12:
        return 0.0, 0.0
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    if n <= 2:
        return slope, 0.0
    residual_variance = sse / (n - 2)
    se_slope = math.sqrt(residual_variance / sxx) if residual_variance > 0 else 0.0
    if se_slope <= 1e-12:
        # A perfectly-fit line (residual_variance == 0) is the most significant trend
        # possible, not an undefined one — report a large finite t rather than dividing
        # by (near) zero.
        return slope, (0.0 if abs(slope) <= 1e-12 else math.copysign(1e6, slope))
    return slope, slope / se_slope


def pre_alert_check(
    history: Sequence[tuple[float, float]],
    short_window_s: float = DEFAULT_SHORT_WINDOW_S,
    baseline_window_s: float = DEFAULT_BASELINE_WINDOW_S,
    variance_ratio_threshold: float = DEFAULT_VARIANCE_RATIO_THRESHOLD,
    trend_t_threshold: float = DEFAULT_TREND_T_THRESHOLD,
    min_window_samples: int = DEFAULT_MIN_WINDOW_SAMPLES,
    trend_consistency_fraction: float = DEFAULT_TREND_CONSISTENCY_FRACTION,
    min_trend_span_s: float = DEFAULT_MIN_TREND_SPAN_S,
    variance_floor: float = 1e-9,
) -> PreAlertResult:
    """Pure function: given one channel's `(t, residual_value)` history, decide whether it
    is showing an early statistical signature of developing instability.

    `history` must already be time-ordered and pruned to (at most) `baseline_window_s +
    short_window_s` — `PreAlertMonitor` below is what maintains that buffer per channel
    tick over tick; this function only ever reads it.

    Two independent tests, each requiring its own window to actually be populated (so an
    engine three ticks into a mission cannot "detect" anything — there is nothing yet to
    compare against):

      * **Variance-ratio** — var(recent) / var(baseline) >= `variance_ratio_threshold`,
        with `var(recent)` also required to clear an absolute floor so two windows that
        are both essentially flat cannot produce a large but meaningless ratio.
      * **Trend-slope** — an OLS fit over the recent window whose slope is significant
        against its own standard error (a t-statistic, so "significant" adapts to how
        noisy this channel normally is) AND whose sign is consistent across most of the
        recent window's individual steps, not just the fitted line — the "not
        noise-driven" requirement from the spec, and what stops one large single-tick jump
        plus flat noise from reading as a sustained trend.

    `emerging` = exactly one test fires; `building` = both. Neither test's outcome is
    smoothed or latched — like `ResidualMonitor`, this is re-evaluated fresh from the
    current window every call, so a level can also drop back to `none` on its own if the
    channel settles.
    """
    if not history:
        return PreAlertResult(level="none")

    latest_t = history[-1][0]
    recent = [(t, v) for t, v in history if latest_t - t <= short_window_s]
    baseline = [
        (t, v)
        for t, v in history
        if short_window_s < latest_t - t <= short_window_s + baseline_window_s
    ]

    variance_ratio: float | None = None
    variance_triggered = False
    if len(recent) >= min_window_samples and len(baseline) >= min_window_samples:
        recent_vals = [v for _, v in recent]
        baseline_vals = [v for _, v in baseline]
        var_recent = _variance(recent_vals)
        var_baseline = _variance(baseline_vals)
        variance_ratio = var_recent / max(var_baseline, variance_floor)
        variance_triggered = (
            var_recent > variance_floor and variance_ratio >= variance_ratio_threshold
        )

    trend_t_stat: float | None = None
    trend_slope: float | None = None
    trend_triggered = False
    recent_span_s = (recent[-1][0] - recent[0][0]) if len(recent) >= 2 else 0.0
    if len(recent) >= min_window_samples and recent_span_s >= min_trend_span_s:
        t0 = recent[0][0]
        xs = [t - t0 for t, _ in recent]
        ys = [v for _, v in recent]
        slope, t_stat = _ols_slope_t_stat(xs, ys)
        trend_t_stat = t_stat
        trend_slope = slope
        if abs(t_stat) >= trend_t_threshold and abs(slope) > 1e-12:
            same_sign = 0
            deltas = 0
            for i in range(1, len(ys)):
                d = ys[i] - ys[i - 1]
                if abs(d) <= 1e-12:
                    continue
                deltas += 1
                if math.copysign(1.0, d) == math.copysign(1.0, slope):
                    same_sign += 1
            consistency = (same_sign / deltas) if deltas > 0 else 0.0
            trend_triggered = consistency >= trend_consistency_fraction

    n_triggered = int(variance_triggered) + int(trend_triggered)
    level: PreAlertLevel = "none" if n_triggered == 0 else ("building" if n_triggered == 2 else "emerging")

    return PreAlertResult(
        level=level,
        variance_ratio=variance_ratio,
        variance_triggered=variance_triggered,
        trend_t_stat=trend_t_stat,
        trend_slope=trend_slope,
        trend_triggered=trend_triggered,
    )


def _variance(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values) / n


_LEVEL_RANK: dict[PreAlertLevel, int] = {"none": 0, "emerging": 1, "building": 2}


def worst_level(levels: Sequence[PreAlertLevel]) -> PreAlertLevel:
    """The most severe of a set of per-channel levels — used to gate a *subsystem* on
    whichever of its channels is showing the strongest early signature."""
    worst: PreAlertLevel = "none"
    for level in levels:
        if _LEVEL_RANK[level] > _LEVEL_RANK[worst]:
            worst = level
    return worst


@dataclass
class PreAlertReport:
    states: dict[str, PreAlertResult]

    def level(self, channel: str) -> PreAlertLevel:
        result = self.states.get(channel)
        return result.level if result else "none"

    def emerging_or_building(self) -> list[str]:
        return [c for c, r in self.states.items() if r.level != "none"]


class PreAlertMonitor:
    """Maintains the rolling per-channel residual history `pre_alert_check` needs, and
    calls it once per channel per tick.

    Deliberately a second, independent monitor rather than an addition to
    `ResidualMonitor` — it needs raw time-stamped history rather than a running EWMA, has
    its own two window lengths, and its output must never influence
    `ResidualMonitor.flagged`. Fed from the identical residual dict `ResidualMonitor.update`
    already receives, so the two monitors can never disagree about what the residual
    *was* that tick, only about how to interpret it.

    Like `ResidualMonitor`, windows are sized in *simulated* seconds and this class tracks
    its own elapsed-time counter from `dt_s`, so a 45 s short window stays a 45 s short
    window whether the demo runs at 1x or 20x.
    """

    def __init__(
        self,
        short_window_s: float = DEFAULT_SHORT_WINDOW_S,
        baseline_window_s: float = DEFAULT_BASELINE_WINDOW_S,
        variance_ratio_threshold: float = DEFAULT_VARIANCE_RATIO_THRESHOLD,
        trend_t_threshold: float = DEFAULT_TREND_T_THRESHOLD,
        min_window_samples: int = DEFAULT_MIN_WINDOW_SAMPLES,
        trend_consistency_fraction: float = DEFAULT_TREND_CONSISTENCY_FRACTION,
        min_trend_span_s: float = DEFAULT_MIN_TREND_SPAN_S,
        warmup_s: float | None = None,
        variance_floor_fraction: float = 0.02,
    ) -> None:
        self.short_window_s = short_window_s
        self.baseline_window_s = baseline_window_s
        self.variance_ratio_threshold = variance_ratio_threshold
        self.trend_t_threshold = trend_t_threshold
        self.min_window_samples = min_window_samples
        self.trend_consistency_fraction = trend_consistency_fraction
        self.min_trend_span_s = min_trend_span_s
        #: Defaults to a full baseline+recent window — see DEFAULT_WARMUP_S.
        self.warmup_s = short_window_s + baseline_window_s if warmup_s is None else warmup_s
        #: Deliberately a smaller fraction than `ResidualMonitor`'s 0.08 — that floor
        #: exists to cap an EWMA *std* (denominator of a ratio against the mean) from
        #: collapsing to zero; this one caps a *variance* floor for a ratio-of-variances
        #: test, which is already scale-normalised by construction and would otherwise be
        #: made needlessly insensitive by reusing the coarser figure.
        self.variance_floor_fraction = variance_floor_fraction
        self._history: dict[str, deque[tuple[float, float]]] = {
            c: deque() for c in CHANNELS
        }
        self._elapsed_s = 0.0
        #: The last report `update(evaluate=True)` produced, re-served unchanged while
        #: evaluation is being paced (see `update`'s `evaluate` argument).
        self._last_report = PreAlertReport(
            states={c: PreAlertResult(level="none") for c in CHANNELS}
        )

    def reset(self) -> None:
        self._history = {c: deque() for c in CHANNELS}
        self._elapsed_s = 0.0
        self._last_report = PreAlertReport(
            states={c: PreAlertResult(level="none") for c in CHANNELS}
        )

    def _floor(self, channel: str) -> float:
        scale = CHANNEL_SCALES.get(channel, 1.0)
        return (scale * self.variance_floor_fraction) ** 2

    def update(
        self, residuals: dict[str, float], dt_s: float = 0.1, evaluate: bool = True
    ) -> PreAlertReport:
        """Record this tick's residuals and (by default) re-run both tests.

        `evaluate=False` records the sample and prunes the window exactly as usual but
        re-serves the previous report instead of re-running `pre_alert_check`. That call
        is O(window) per channel — at 10 Hz on a 195 s window it re-scans ~1 950 samples
        per channel per tick — and both tests are statistics over tens of seconds, so
        evaluating them ten times a second cannot surface anything a once-a-second
        evaluation misses. The recorded history is identical either way, so an evaluation
        that does run sees exactly the data it would have seen before. Paced by
        `settings.phm_analysis_interval_s`; the caller decides, this class only obeys.
        """
        self._elapsed_s += dt_s
        total_window = self.short_window_s + self.baseline_window_s
        warmed = self._elapsed_s >= self.warmup_s
        states: dict[str, PreAlertResult] = {}
        for channel in CHANNELS:
            x = residuals.get(channel, 0.0)
            hist = self._history[channel]
            hist.append((self._elapsed_s, x))
            while hist and self._elapsed_s - hist[0][0] > total_window:
                hist.popleft()
            if not evaluate:
                continue
            result = pre_alert_check(
                hist,
                short_window_s=self.short_window_s,
                baseline_window_s=self.baseline_window_s,
                variance_ratio_threshold=self.variance_ratio_threshold,
                trend_t_threshold=self.trend_t_threshold,
                min_window_samples=self.min_window_samples,
                trend_consistency_fraction=self.trend_consistency_fraction,
                min_trend_span_s=self.min_trend_span_s,
                variance_floor=self._floor(channel),
            )
            if not warmed and result.level != "none":
                # Diagnostics (ratio/t-stat/slope) stay visible for debugging; only the
                # level itself is held at "none" during warmup, matching how
                # `ResidualReport.flagged` is computed (z is always visible, `flagged`
                # is gated on `warmed_up`).
                result = PreAlertResult(
                    level="none",
                    variance_ratio=result.variance_ratio,
                    variance_triggered=result.variance_triggered,
                    trend_t_stat=result.trend_t_stat,
                    trend_slope=result.trend_slope,
                    trend_triggered=result.trend_triggered,
                )
            states[channel] = result
        if not evaluate:
            return self._last_report
        self._last_report = PreAlertReport(states=states)
        return self._last_report
