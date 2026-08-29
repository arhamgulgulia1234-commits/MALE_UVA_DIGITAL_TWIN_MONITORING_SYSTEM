"""A small, reusable scalar Kalman filter.

This is the one piece of math every fusion channel in `app/fusion/` shares: a single
hidden state estimated from one or more noisy measurements plus, optionally, a process
model. Three call sites use it very differently —

  * `cht_fusion.py`   fuses two independent sensors of the same state (no process model:
                       CHT changes slowly, so a random walk with a small Q is enough).
  * `rpm_fusion.py`    fuses two *different-modality* estimates of the same state
                       (tachometer, vibration-derived) — also a random walk.
  * `oil_pressure_fusion.py` is the textbook case: `predict()` is driven by the
                       lubrication equation's own output, and `update()` corrects it
                       against the raw sensor.

— and this class is deliberately agnostic to which of those it is being used for. It
knows nothing about CHT, RPM or oil pressure; it only knows "one number, estimated from
noisy evidence."

Standard scalar Kalman equations:

    predict:  x <- x  (or x <- control_input, if a process model provided one)
              p <- p + q
    update:   k <- p / (p + r)
              x <- x + k * (measurement - x)
              p <- (1 - k) * p

`update()` can be called more than once per `predict()` — once per sensor, in sequence —
which is exactly how CHT and RPM fusion combine two measurements of one state: predict
once, then update, update, using each sensor's own measurement variance. Each call folds
that sensor's evidence in before the next one arrives, which is mathematically equivalent
to a single joint update and is the standard way a scalar Kalman filter absorbs more than
one measurement per step.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class KalmanUpdateResult:
    """What one `update()` call produced — the new state, and why.

    `innovation` and `gain` are exposed alongside the state deliberately: Part C's fault
    disambiguation needs them directly (a fused channel's raw per-sensor innovation is
    now a classifier feature in its own right), and a caller building an explainability
    surface needs to say *why* the estimate moved, not just report the new number.
    """

    state: float
    innovation: float
    gain: float


#: The tick cadence every fusion channel's `process_variance_q` default was tuned
#: against — the live loop's own 100 ms wall-clock tick at 1x time_scale, and
#: `scripts/validate_physics.py`'s `TICK_S`. `predict()` scales Q by `dt_s /
#: REFERENCE_DT_S` so a Q tuned at this cadence keeps meaning "this much uncertainty
#: growth per REFERENCE_DT_S of simulated time" at every time-acceleration setting.
REFERENCE_DT_S = 0.1


class ScalarKalmanFilter:
    """One hidden scalar state, estimated from noisy measurements over time.

    `process_variance_q` is how much uncertainty `predict()` adds per step — effectively
    "how fast do we expect the true state to wander between measurements." A slowly
    thermal-inertial quantity like CHT wants a small Q; a state whose own predictive model
    has become unreliable wants Q widened, which is exactly the lever
    `oil_pressure_fusion.py` pulls when a lubrication fault is suspected.

    `measurement_variance_r` is the default noise variance assumed for `update()` when the
    caller does not override it per call — each sensor typically has its own R, passed
    explicitly at the call site instead.
    """

    def __init__(
        self,
        process_variance_q: float,
        measurement_variance_r: float,
        initial_state: float = 0.0,
        initial_variance: float | None = None,
    ) -> None:
        self.q = process_variance_q
        self.r = measurement_variance_r
        self.x = initial_state
        # Deliberately started uncertain (variance = R) rather than at 0: a filter that
        # starts *overconfident* in an arbitrary initial state takes long, biased-looking
        # transient to correct once real measurements arrive. Starting as uncertain as a
        # single raw measurement lets the first update pull the state to roughly where the
        # first real reading says it should be, immediately.
        self.p = initial_variance if initial_variance is not None else measurement_variance_r

    def predict(
        self,
        dt_s: float,
        control_input: float | None = None,
        process_variance: float | None = None,
    ) -> float:
        """Advance the state estimate one step.

        `control_input=None` is a pure random-walk step: the state estimate is left
        alone and only its uncertainty grows — the right model for a quantity with no
        independent physics driving it forward (CHT between sensor readings, or an RPM
        state fused purely from two sensors of itself).

        `control_input` set to a value is a *model-driven* prediction: the caller has an
        independent equation for what the state should be next (the lubrication model's
        pressure equation, given current RPM and oil temperature) and that becomes the
        new prior, before any sensor is consulted.

        `process_variance` overrides the constructor's Q for this call only — this is
        the hook `oil_pressure_fusion.py` uses to widen Q when a lubrication fault is
        suspected active, without needing a second filter instance.

        `dt_s` matters more than it looks: Q is calibrated as "uncertainty growth per
        `REFERENCE_DT_S` of *simulated* time", not per call, because every fusion channel
        is stepped once per tick and a tick's simulated duration is `wall_dt_s *
        time_scale` — anywhere from ~0.02 s to several seconds depending on the demo's
        time-acceleration setting. A fixed per-call Q was verified to break exactly this:
        `scripts/stability_checks.py` caught the fused RPM trajectory at 20x diverging
        214 rpm from the 1x run of the identical mission (tolerance 30 rpm), because
        uncertainty was growing once per *tick* regardless of how much simulated time
        that tick actually covered, while the real RPM dynamics underneath it were not.
        Scaling by `dt_s / REFERENCE_DT_S` is what every other time-constant in this
        codebase already does for the same reason (see `ResidualMonitor`,
        `AnomalyDetector`, `RULPredictor` — all anchored to simulated seconds so a 20x
        demo does not appear to react twenty times slower)."""
        if control_input is not None:
            self.x = control_input
        q = self.q if process_variance is None else process_variance
        self.p += q * (max(0.0, dt_s) / REFERENCE_DT_S)
        return self.x

    def update(
        self, measurement: float, measurement_variance: float | None = None
    ) -> KalmanUpdateResult:
        """Fold in one noisy measurement of the state.

        Call this once per sensor per step to fuse multiple independent measurements —
        each call's posterior becomes the next call's prior, so two sequential updates
        within one step are the standard way a scalar Kalman filter absorbs more than one
        measurement between predictions."""
        r = self.r if measurement_variance is None else measurement_variance
        innovation = measurement - self.x
        denom = self.p + r
        gain = self.p / denom if denom > 1e-12 else 0.0
        self.x = self.x + gain * innovation
        self.p = (1.0 - gain) * self.p
        return KalmanUpdateResult(state=self.x, innovation=innovation, gain=gain)

    def reset(self, state: float = 0.0, variance: float | None = None) -> None:
        self.x = state
        self.p = variance if variance is not None else self.r


def adaptive_measurement_variance(
    base_variance: float, recent_abs_innovation: float, tolerance: float = 3.0
) -> float:
    """Inflate a sensor's measurement variance when its *own* recent average
    |innovation| against the fused estimate has grown well beyond what its nominal noise
    floor would produce on its own.

    A plain Kalman filter has no way to notice this by itself: it combines measurements
    by their *stated* variance, so if one sensor starts drifting, the filter keeps
    trusting its declared noise level and blends the bad reading in proportionally to
    that trust — verified directly while building `cht_fusion.py`, where a secondary
    probe drifting by ~40 degC pulled the fused estimate roughly halfway to it, not
    "close to the true value" as the whole point of fusing two sensors requires.

    This closes that gap without ever looking at ground truth: a sensor whose own recent
    disagreement with the fused estimate has grown large is treated as *currently* less
    trustworthy, which is what a real fault-tolerant fusion system does with a
    disagreeing instrument — down-weight it rather than average it in. `tolerance` is how
    many multiples of the sensor's own expected average deviation
    (E[|N(0, sigma)|] = sigma * sqrt(2/pi)) still count as ordinary noise before
    inflation starts; healthy sensor noise essentially never triggers it, and a sustained
    fault-sized bias inflates the variance quadratically in how far past that line it
    sits — which is what hands the other, still-healthy sensor the gain.
    """
    base_sigma = base_variance**0.5
    expected_abs = base_sigma * 0.7979  # sqrt(2/pi)
    if expected_abs <= 1e-9:
        return base_variance
    ratio = recent_abs_innovation / (tolerance * expected_abs)
    inflation = max(1.0, ratio**2)
    return base_variance * inflation


class InnovationTracker:
    """A slow EWMA of a sensor's |innovation|, used to feed `adaptive_measurement_variance`.

    Deliberately a *slower* time constant than `ResidualMonitor`'s (6 s): reacting to a
    single noisy tick would flicker the effective variance every time ordinary sensor
    noise happened to land a bit large, which would fight the filter it is supposed to be
    protecting. This tracks the sustained disagreement a real fault produces, not the
    noise floor's own natural spread.
    """

    def __init__(self, tau_s: float = 15.0) -> None:
        self.tau_s = tau_s
        self.value = 0.0

    def update(self, abs_innovation: float, dt_s: float) -> float:
        a = 1.0 - math.exp(-max(1e-6, dt_s) / max(1e-6, self.tau_s))
        self.value = a * abs_innovation + (1.0 - a) * self.value
        return self.value
