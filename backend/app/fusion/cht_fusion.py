"""CHT dual-sensor fusion — two independent probes on the same true cylinder head
temperature, combined into one best estimate via `ScalarKalmanFilter`.

Before this, `PlantState.cht_c` was a single reported value: the true CHT plus one
Gaussian noise draw (`EnginePlant._apply_sensor_noise`), with no sensor fault able to
target it at all. That is fine for a single instrument, but it means a "CHT sensor fault"
literally could not be modelled or detected — there was only one number, and nothing to
cross-check it against.

Two independent probes change that. Each is its own instrument: its own noise
characteristic (`noise_cht_c_primary` / `noise_cht_c_secondary`), and its own fault
severity via a dedicated field on the shared `SensorFaultState`
(`cht_sensor_primary_drift` / `cht_sensor_secondary_drift`) — reusing that one object
rather than instantiating two is a deliberate simplification: independence here only
requires independent *fields* with independent ramps, which `SensorFaultState` already
gives any two of its attributes, and duplicating the object would only add a second
control-surface target for no functional gain.

The Kalman filter treats this as a pure random walk (`control_input=None` on every
`predict()`) — CHT's thermal mass means it cannot move far tick to tick, so a small
process variance is the entire "process model" this channel needs — and folds both
sensors in sequentially each tick, which is the standard way a scalar filter absorbs two
measurements between predictions.

`innovation_primary` / `innovation_secondary` are deliberately *not* the Kalman filter's
own per-update innovation (that is `raw - predicted_state_before_that_update`, which
differs between the two calls because the first update already moved the state before
the second one runs). They are each sensor's residual against the *final* fused
estimate for this tick — the question Part C's fault disambiguation actually needs
answered: "how far is this instrument from the answer we ended up trusting," not "how far
was it from an intermediate prediction no one downstream ever sees."

**Robustness to one sensor drifting.** A plain Kalman filter blends both sensors by their
*declared* variance, which does not shrink just because one of them has started lying —
verified directly while building this: without the adaptive step below, a secondary probe
drifting by ~40 degC pulled the fused estimate roughly halfway to it, not "close to true"
as the whole reason to fuse two sensors requires. `adaptive_measurement_variance`
(`kalman_filter.py`) closes that gap using only each sensor's own recent disagreement
with the fused estimate — no ground truth, no knowledge that a fault exists — which is
what lets a healthy sensor's evidence dominate once its faulty partner's own residual has
been large for a sustained period.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from app.core.engine_params import PARAMS, EngineParams
from app.fusion.kalman_filter import (
    InnovationTracker,
    ScalarKalmanFilter,
    adaptive_measurement_variance,
)
from app.physics.sensor_fault_model import SensorFaultState


@dataclass(frozen=True)
class CHTFusionOutputs:
    fused_cht_c: float
    primary_reading_c: float
    secondary_reading_c: float
    innovation_primary_c: float
    innovation_secondary_c: float
    #: Kalman gain from the *last* update this tick (secondary's). Exposed for the same
    #: explainability reason oil-pressure fusion exposes its gain, even though CHT's gain
    #: is far less diagnostic on its own — it is cheap to carry and keeps the three
    #: fusion outputs shaped consistently.
    kalman_gain: float


class CHTFusion:
    def __init__(self, params: EngineParams = PARAMS, seed: int = 5301) -> None:
        self.p = params
        self._rng = random.Random(seed)
        self.filter = ScalarKalmanFilter(
            process_variance_q=params.cht_fusion_process_variance_c2,
            measurement_variance_r=params.noise_cht_c_primary**2,
            initial_state=params.cht_init_c,
        )
        # Trust trackers use *last* tick's innovation to set *this* tick's effective
        # variance — using this tick's own innovation would be circular (the variance
        # would depend on the very update it is meant to weight).
        self._trust_primary = InnovationTracker()
        self._trust_secondary = InnovationTracker()

    def reset(self) -> None:
        self.filter.reset(state=self.p.cht_init_c)
        self._trust_primary = InnovationTracker()
        self._trust_secondary = InnovationTracker()

    def step(
        self, true_cht_c: float, sensor_faults: SensorFaultState, dt_s: float
    ) -> CHTFusionOutputs:
        p = self.p

        # ---- two independent noisy readings of the same true CHT ----------------
        primary = true_cht_c + self._rng.gauss(0.0, p.noise_cht_c_primary)
        secondary = true_cht_c + self._rng.gauss(0.0, p.noise_cht_c_secondary)

        # A drifting probe grows an offset on *its own* reading only — the same shape
        # `EngineModel`'s EGT drift uses, scaled to CHT's operating band.
        if sensor_faults.cht_sensor_primary_drift > 1e-4:
            primary += p.sensor_cht_drift_max_c * sensor_faults.cht_sensor_primary_drift
        if sensor_faults.cht_sensor_secondary_drift > 1e-4:
            secondary += (
                p.sensor_cht_drift_max_c * sensor_faults.cht_sensor_secondary_drift
            )

        r_primary = adaptive_measurement_variance(
            p.noise_cht_c_primary**2, self._trust_primary.value
        )
        r_secondary = adaptive_measurement_variance(
            p.noise_cht_c_secondary**2, self._trust_secondary.value
        )

        # ---- fuse: predict (random walk), then update from each sensor in turn ---
        self.filter.predict(dt_s)
        self.filter.update(primary, measurement_variance=r_primary)
        result = self.filter.update(secondary, measurement_variance=r_secondary)

        fused = result.state
        innovation_primary = primary - fused
        innovation_secondary = secondary - fused
        self._trust_primary.update(abs(innovation_primary), dt_s)
        self._trust_secondary.update(abs(innovation_secondary), dt_s)

        return CHTFusionOutputs(
            fused_cht_c=fused,
            primary_reading_c=primary,
            secondary_reading_c=secondary,
            innovation_primary_c=innovation_primary,
            innovation_secondary_c=innovation_secondary,
            kalman_gain=result.gain,
        )
