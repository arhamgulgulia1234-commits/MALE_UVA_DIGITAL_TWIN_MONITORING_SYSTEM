"""RPM cross-modality fusion — the tachometer reading and an RPM estimate derived from
the vibration model's dominant firing frequency, combined via `ScalarKalmanFilter`.

This is a genuinely different kind of fusion from CHT's: not two instruments measuring
the same physical quantity the same way, but two *independent physical principles*
converging on the same number. A 4-stroke engine fires each cylinder once every two
crank revolutions, so the dominant vibration harmonic sits at

    f_fire = (RPM / 60) * n_cylinders / revs_per_cycle

which inverts to the RPM this module actually computes from `dominant_frequency_hz`:

    RPM = f_fire * 120 / n_cylinders          (revs_per_cycle = 2, folded into the 120)

`dominant_frequency_hz` comes from `VibrationFeatures` — the *FFT-detected* peak of the
rolling buffer (`vibration_model.compute_features`), not the frequency the synthesiser
used internally to build the signal. Using the synthesis frequency would be reading the
answer off the physics instead of estimating it the way a real vibration sensor and its
signal processing would have to.

Why this cross-check is useful, concretely: a stuck tachometer (`rpm_sensor_stuck`)
freezes the reported RPM while the vibration-derived estimate keeps tracking the real
engine — the two diverge, and the divergence is on the tachometer's side. A real
mechanical problem that changes how the crank actually turns would instead move *both*
estimates together (they are both, after all, downstream of the same crankshaft) while
possibly also showing up as elevated vibration RMS independently — which is exactly the
distinction Part C's disambiguation logic uses this fusion pair for.

**A genuine limitation, stated plainly.** CHT fusion can tell *which* of its two probes
is lying, because a drifting probe's own residual grows relative to a peer that keeps
agreeing with the filter's running state, and the adaptive trust mechanism
(`kalman_filter.adaptive_measurement_variance`) reinforces whichever side the filter
already leans toward. Tachometer vs. vibration-derived RPM does not have that luxury:
with only two sources and a persistent disagreement, the filter will settle toward
whichever one it happened to trust more before the disagreement started (their prior
variances, `noise_rpm` vs. the FFT-resolution-derived floor, are not equal), and that is
not the same thing as identifying which one is *correct*. This is not a bug to fix in
the filter — it is the honest limit of fusing two same-question sources with no third
reference. Resolving it needs the independent evidence the module docstring already
names: cylinder-level vibration RMS, which is not itself part of this fusion's inputs and
is exactly what `app/ml/fault_classifier.py`'s disambiguation logic checks before naming
a suspect.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.engine_params import PARAMS, EngineParams
from app.fusion.kalman_filter import (
    InnovationTracker,
    ScalarKalmanFilter,
    adaptive_measurement_variance,
)
from app.physics.plant import PlantState
from app.physics.sensor_fault_model import SensorFaultState


@dataclass(frozen=True)
class RPMFusionOutputs:
    fused_rpm: float
    tachometer_rpm: float
    vibration_derived_rpm: float
    innovation_tachometer: float
    innovation_vibration_derived: float
    kalman_gain: float


def _vibration_derived_rpm(state: PlantState, params: EngineParams) -> float:
    """Mean implied RPM across cylinders' FFT-detected dominant frequency.

    Averaging over cylinders rather than picking one: they are all mechanically coupled
    to the same crankshaft, so each is an independent noisy read of the identical true
    frequency, and the mean is a cheap, honest way to cut that noise down — the same
    reasoning `EnginePlant` already uses when it reduces per-cylinder EGT to
    `egt_mean_c`.

    Clamped to a physically plausible RPM range for the same reason
    `EngineModel.step()` clamps the real RPM state to `[0, rpm_redline * 1.12]`: FFT
    peak-picking on a noisy spectrum occasionally locks onto a spurious bin instead of
    the true firing harmonic — broadband noise from `bearing_wear` measurably increases
    how often this happens — and an unclamped miss can imply many thousands of rpm from
    a single bad bin. Verified directly: `scripts/stability_checks.py`'s 1x-vs-20x
    trajectory comparison caught a single such tick implying ~7500 rpm against a true
    ~2000 rpm, which is exactly the kind of single-tick outlier a real tachometer cross-
    check would also refuse to act on. Clamping (rather than discarding the reading
    outright) keeps the fusion's sequential-update structure simple — an implausible
    value still updates the filter, but as a bounded, clearly-wrong measurement the
    adaptive trust mechanism can down-weight over subsequent ticks, not as an
    unbounded one that can singularly displace the fused estimate in one update."""
    feats = state.vibration_features
    if not feats:
        return 0.0
    mean_hz = sum(f.dominant_frequency_hz for f in feats) / len(feats)
    rpm = mean_hz * 120.0 / max(1, params.n_cylinders)
    return max(0.0, min(params.rpm_redline * 1.2, rpm))


def _vibration_rpm_measurement_variance(
    params: EngineParams, rpm_rate_of_change: float = 0.0
) -> float:
    """Measurement variance for the vibration-derived RPM, from the FFT's own frequency
    resolution rather than an arbitrary constant.

    `VibrationModel`'s rolling buffer holds `int(vib_sample_rate_hz)` samples (see
    `VibrationModel.__init__`), so its bin width is `sample_rate / buffer_length =
    1.0` Hz regardless of sample rate — a one-second window always resolves to 1 Hz
    bins. Converted to RPM via the same firing-frequency relation this module inverts,
    that bin width sets a hard floor on how precisely a dominant peak can be read; a
    configured floor on top accounts for peak-picking noise a real spectrum estimator
    would also have.

    `rpm_rate_of_change` (rpm/s, always non-negative) widens that floor further during a
    fast transient — a mission-phase change or a throttle step. The FFT's dominant-
    frequency read is a snapshot of the *last second* of vibration, computed once per
    tick; while RPM is genuinely moving fast, that snapshot is smeared across whatever
    range it swept during the window, which is exactly the kind of read a real spectrum
    estimator would also be least confident about. This also happens to be what keeps
    the fused RPM trajectory reproducible across different `time_scale` settings — the
    same real transient is sampled at different tick boundaries at 1x vs. 20x, and
    de-weighting the coarse read during it (using the tachometer's own rate of change,
    which is consistent regardless of tick size once expressed per simulated second)
    is a more honest fix than trying to make two different sample rates agree exactly on
    a snapshot neither can resolve precisely during a transient."""
    buffer_seconds = 1.0  # int(vib_sample_rate_hz) samples at vib_sample_rate_hz Hz
    bin_width_hz = 1.0 / buffer_seconds
    bin_width_rpm = bin_width_hz * 120.0 / max(1, params.n_cylinders)
    sigma = max(params.noise_rpm_vibration_derived_floor, bin_width_rpm * 0.5)
    sigma += params.rpm_vibration_transient_widening_per_rpm_s * rpm_rate_of_change
    return sigma**2


class RPMFusion:
    def __init__(self, params: EngineParams = PARAMS) -> None:
        self.p = params
        self.filter = ScalarKalmanFilter(
            process_variance_q=params.rpm_fusion_process_variance_rpm2,
            measurement_variance_r=params.noise_rpm**2,
            initial_state=params.rpm_idle,
        )
        # Same reasoning as CHT fusion's trackers: a precise-but-wrong tachometer would
        # otherwise dominate a correct-but-coarser vibration-derived estimate purely
        # because its *declared* noise is smaller, regardless of which one is actually
        # right. Verified directly — a stuck tachometer 400 rpm off the true value only
        # pulled the fused estimate ~15 rpm without this.
        self._trust_tachometer = InnovationTracker()
        self._trust_vibration = InnovationTracker()
        #: One-tick-lagged |d(fused rpm)/dt|, same lag convention as the trust
        #: trackers above (this tick's variance uses *last* tick's rate, avoiding the
        #: circularity of a rate that depends on the very update it would inform).
        self._prev_fused_rpm: float | None = None
        self._rpm_rate_of_change: float = 0.0

    def reset(self, rpm: float | None = None) -> None:
        self.filter.reset(state=rpm if rpm is not None else self.p.rpm_idle)
        self._trust_tachometer = InnovationTracker()
        self._trust_vibration = InnovationTracker()
        self._prev_fused_rpm = None
        self._rpm_rate_of_change = 0.0

    def step(
        self,
        state: PlantState,
        sensor_faults: SensorFaultState,
        dt_s: float,
    ) -> RPMFusionOutputs:
        p = self.p

        # `state.rpm` is the tachometer reading: already noised, and already corrupted
        # by `rpm_sensor_stuck` if that fault is active (both applied in
        # `EnginePlant._apply_sensor_noise` / `SensorFaultModel.apply`, upstream of this
        # call). This module does not re-corrupt it — reusing the existing pipeline
        # rather than duplicating its fault logic is the same choice CHT fusion makes
        # for the sensor side of things.
        tachometer_rpm = state.rpm
        vibration_rpm = _vibration_derived_rpm(state, p)

        r_tachometer = adaptive_measurement_variance(
            p.noise_rpm**2, self._trust_tachometer.value
        )
        r_vibration = adaptive_measurement_variance(
            _vibration_rpm_measurement_variance(p, self._rpm_rate_of_change),
            self._trust_vibration.value,
        )

        self.filter.predict(dt_s)
        self.filter.update(tachometer_rpm, measurement_variance=r_tachometer)
        result = self.filter.update(vibration_rpm, measurement_variance=r_vibration)

        fused = result.state
        innovation_tachometer = tachometer_rpm - fused
        innovation_vibration = vibration_rpm - fused
        self._trust_tachometer.update(abs(innovation_tachometer), dt_s)
        self._trust_vibration.update(abs(innovation_vibration), dt_s)
        if self._prev_fused_rpm is not None:
            self._rpm_rate_of_change = abs(fused - self._prev_fused_rpm) / max(1e-6, dt_s)
        self._prev_fused_rpm = fused

        return RPMFusionOutputs(
            fused_rpm=fused,
            tachometer_rpm=tachometer_rpm,
            vibration_derived_rpm=vibration_rpm,
            innovation_tachometer=innovation_tachometer,
            innovation_vibration_derived=innovation_vibration,
            kalman_gain=result.gain,
        )
