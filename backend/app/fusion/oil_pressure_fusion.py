"""Oil pressure model+sensor fusion — the textbook Kalman predict/update structure.

Unlike CHT and RPM fusion, this channel has a genuine process model: `lubrication_model
.py`'s own pressure equation,

    P_oil = k1 * RPM * mu(T_oil) - k2 * wear_factor

evaluated with `wear_factor = 0` — a deliberately *zero-wear-calibrated* prediction, the
same "healthy reference" idea the digital twin uses at the whole-plant level, applied
here to one equation. That is the PREDICT step. The raw, noisy oil-pressure sensor
reading is the UPDATE step. Two orders of magnitude in the Kalman gain formula separate
"trust the model" from "trust the sensor," and which side of that line the filter sits on
each tick is itself the diagnostic signal this module exists to produce.

**Why Q widens on a known lubrication fault, not on observed disagreement.** A real
degradation-aware system would infer "the model is now unreliable" from independent
evidence (rising vibration, other symptoms) before trusting a sensor over a
freshly-suspect model. This simulation has that independent evidence already, in
`FaultState.bearing_wear` / `FaultState.oil_pump_degradation` — the same ground truth
every other physics module in `app/physics/` already reads to compute its own outputs
(`LubricationModel.step()` reads it to perturb pump gain directly). Reading it here, at
the same physics layer, to decide how much to trust a *different* model of the same
system is architecturally the same move, not a new kind of access. The classifier
downstream never sees `FaultState` — only this fusion's innovation and gain outputs —
so the disambiguation it learns from those is still inferred from symptoms, not handed
the answer.

**The two failure signatures this produces, concretely:**

  * **Sensor fault** (`oil_pressure_sensor_noise`): no lubrication fault is active, so Q
    stays narrow and the filter keeps trusting its own (accurate) zero-wear prediction.
    The raw sensor disagrees with it persistently — a large, sustained
    `oil_pressure_innovation` — while the Kalman gain stays low, because the model has
    given the filter no reason to doubt itself.
  * **Physical fault** (`bearing_wear` / `oil_pump_degradation`): Q widens the moment
    severity crosses the suspect threshold, so the filter progressively hands trust to
    the sensor. The innovation is again large and sustained, but this time the gain
    trends *up* toward 1 alongside it — the model's own prediction confidence degrading
    is the tell, and it is a different shape of evidence than a sensor lying while the
    model stays confident.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.engine_params import PARAMS, EngineParams
from app.fusion.kalman_filter import ScalarKalmanFilter
from app.physics.fault_models import FaultState
from app.physics.lubrication_model import oil_viscosity_pa_s


@dataclass(frozen=True)
class OilPressureFusionOutputs:
    fused_oil_pressure_kpa: float
    model_predicted_kpa: float
    sensor_reading_kpa: float
    innovation_kpa: float
    kalman_gain: float
    #: `kalman_gain` minus its own slow-moving healthy baseline — zero when the gain is
    #: sitting where it normally does, and rising as it trends toward "trust the sensor"
    #: for reasons other than the ordinary settling every filter does after start-up.
    #: This is what makes the gain usable as an EWMA-z classifier feature the same way a
    #: residual is: the raw gain does not average to zero when healthy, so feeding it
    #: through z-scoring machinery built for zero-centred signals would read as
    #: permanently anomalous. See `oil_pressure_gain_baseline_tau_s`.
    kalman_gain_deviation: float
    #: True while Q was widened this tick — exposed so a caller (or the classifier
    #: feature builder) never has to re-derive "was a lubrication fault suspected" from
    #: the raw gain number alone.
    lubrication_fault_suspected: bool


def _zero_wear_predicted_pressure(
    rpm: float, oil_temp_c: float, params: EngineParams
) -> float:
    """The lubrication equation evaluated at zero wear — what a technician's ordinary,
    healthy-engine model expects, given current RPM and oil temperature. Deliberately
    ignorant of the real fault state; that ignorance is the entire point (see module
    docstring)."""
    mu = oil_viscosity_pa_s(oil_temp_c, params)
    predicted = params.oil_pressure_k1 * rpm * mu
    return max(params.oil_pressure_min_kpa, min(params.oil_pressure_max_kpa, predicted))


class OilPressureFusion:
    def __init__(self, params: EngineParams = PARAMS) -> None:
        self.p = params
        self.filter = ScalarKalmanFilter(
            process_variance_q=params.oil_pressure_model_process_variance_healthy_kpa2,
            measurement_variance_r=params.noise_oil_pressure_kpa**2,
            initial_state=params.oil_pressure_min_kpa,
        )
        self._gain_baseline: float | None = None

    def reset(self) -> None:
        self.filter.reset(state=self.p.oil_pressure_min_kpa)
        self._gain_baseline = None

    def step(
        self,
        fused_rpm: float,
        oil_temp_c: float,
        sensor_reading_kpa: float,
        fault_state: FaultState,
        dt_s: float,
    ) -> OilPressureFusionOutputs:
        p = self.p

        suspected = (
            fault_state.bearing_wear > p.oil_pressure_fault_suspect_threshold
            or fault_state.oil_pump_degradation > p.oil_pressure_fault_suspect_threshold
        )
        q = (
            p.oil_pressure_model_process_variance_faulted_kpa2
            if suspected
            else p.oil_pressure_model_process_variance_healthy_kpa2
        )

        predicted = _zero_wear_predicted_pressure(fused_rpm, oil_temp_c, p)
        self.filter.predict(dt_s, control_input=predicted, process_variance=q)
        result = self.filter.update(
            sensor_reading_kpa, measurement_variance=p.noise_oil_pressure_kpa**2
        )

        if self._gain_baseline is None:
            self._gain_baseline = result.gain
        else:
            a = 1.0 - math.exp(
                -max(1e-6, dt_s) / max(1e-6, p.oil_pressure_gain_baseline_tau_s)
            )
            self._gain_baseline += (result.gain - self._gain_baseline) * a

        return OilPressureFusionOutputs(
            fused_oil_pressure_kpa=result.state,
            model_predicted_kpa=predicted,
            sensor_reading_kpa=sensor_reading_kpa,
            innovation_kpa=result.innovation,
            kalman_gain=result.gain,
            kalman_gain_deviation=result.gain - self._gain_baseline,
            lubrication_fault_suspected=suspected,
        )
