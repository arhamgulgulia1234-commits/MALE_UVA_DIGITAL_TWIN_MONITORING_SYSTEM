"""Sensor faults — architecturally distinct from every fault in Phase 2.

Every fault in `fault_models.py` corrupts the **engine**: it perturbs a physical parameter
and lets the consequences propagate through the simulation. A sensor fault corrupts only
what the **instrument reports**. The engine is fine; the number on the dashboard is not.

That distinction is the whole point, and it dictates where this runs in the pipeline:

    physics (true state)
        -> digital twin computes its healthy prediction from the same commands
            -> SENSOR FAULTS APPLIED HERE, to the reported values only
                -> residual = reported - twin        <- still shows an anomaly
                    -> anomaly detector / classifier

Because the corruption lands after the physics and after the twin, a drifting EGT probe
produces a residual that looks superficially like a real combustion problem. What
separates them is *correlation structure*: a real fault moves every physically-linked
channel together (bearing wear drops oil pressure AND raises vibration AND raises oil
temperature), while a sensor fault moves exactly one channel and leaves its physical
neighbours untouched. `app/ml/fault_classifier.py` exploits precisely that.

The true, uncorrupted state remains available for validation and debugging — see
`SensorFaultState.last_corruptions` and the validation script — but is never sent to the
frontend, because exposing it would let the UI "cheat" and read the answer directly
instead of inferring it the way a real ground station would have to.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

SENSOR_FAULT_TYPES: tuple[str, ...] = (
    "egt_sensor_drift",
    "oil_pressure_sensor_noise",
    "rpm_sensor_stuck",
    # --- Phase 5: fused-channel probe faults --------------------------------
    # CHT is the one fused channel with no pre-existing sensor fault to reuse — RPM
    # already had `rpm_sensor_stuck` (the tachometer half of app/fusion/rpm_fusion.py)
    # and oil pressure already had `oil_pressure_sensor_noise` (the sensor half of
    # app/fusion/oil_pressure_fusion.py's predict/update pair). Two probes means two
    # independent fault types, one per instrument, so a fault can be injected on
    # exactly one of them without touching the other.
    "cht_sensor_primary_drift",
    "cht_sensor_secondary_drift",
)

#: Which telemetry channel each sensor fault corrupts. Used by the disambiguation logic
#: to know which channel to check for *missing* correlated movement.
SENSOR_FAULT_CHANNEL: dict[str, str] = {
    "egt_sensor_drift": "egt_mean_c",
    "oil_pressure_sensor_noise": "oil_pressure_kpa",
    "rpm_sensor_stuck": "rpm",
    "cht_sensor_primary_drift": "cht_sensor_primary",
    "cht_sensor_secondary_drift": "cht_sensor_secondary",
}


@dataclass
class _Ramp:
    start_value: float
    target_value: float
    duration_s: float
    elapsed_s: float = 0.0

    def advance(self, dt: float) -> tuple[float, bool]:
        if self.duration_s <= 0.0:
            return self.target_value, True
        self.elapsed_s += dt
        frac = min(1.0, self.elapsed_s / self.duration_s)
        return (
            self.start_value + (self.target_value - self.start_value) * frac,
            frac >= 1.0,
        )


@dataclass
class SensorFaultState:
    """0-1 severity per sensor fault. Deliberately a separate type from FaultState so the
    two can never be confused at a call site — they enter the pipeline at different
    stages and mean different things."""

    egt_sensor_drift: float = 0.0
    oil_pressure_sensor_noise: float = 0.0
    rpm_sensor_stuck: float = 0.0
    # --- Phase 5 -------------------------------------------------------------
    cht_sensor_primary_drift: float = 0.0
    cht_sensor_secondary_drift: float = 0.0

    started_at: dict[str, float] = field(default_factory=dict)
    #: Which cylinder's probe is drifting (EGT is per cylinder).
    target_cylinder: dict[str, int] = field(default_factory=dict)

    _ramps: dict[str, _Ramp] = field(default_factory=dict, repr=False)
    _rng: random.Random = field(default_factory=lambda: random.Random(4242), repr=False)
    #: Frozen reading held by a stuck RPM sensor.
    _stuck_rpm: float | None = field(default=None, repr=False)
    #: Diagnostics only: how much each channel was corrupted this tick.
    last_corruptions: dict[str, float] = field(default_factory=dict, repr=False)

    # ---- query ------------------------------------------------------------

    def severity(self, fault_type: str) -> float:
        return float(getattr(self, fault_type))

    def active(self) -> dict[str, float]:
        return {
            ft: self.severity(ft)
            for ft in SENSOR_FAULT_TYPES
            if self.severity(ft) > 1e-4
        }

    def any_active(self) -> bool:
        return bool(self.active())

    def cylinder_for(self, fault_type: str, n_cylinders: int) -> int:
        if fault_type not in self.target_cylinder:
            self.target_cylinder[fault_type] = self._rng.randrange(n_cylinders)
        return self.target_cylinder[fault_type] % n_cylinders

    # ---- mutation ---------------------------------------------------------

    def inject(
        self,
        fault_type: str,
        target_severity: float,
        ramp_seconds: float,
        *,
        now: float | None = None,
        n_cylinders: int = 4,
    ) -> None:
        if fault_type not in SENSOR_FAULT_TYPES:
            raise ValueError(f"unknown sensor fault type: {fault_type}")
        target = max(0.0, min(1.0, target_severity))
        self._ramps[fault_type] = _Ramp(
            start_value=self.severity(fault_type),
            target_value=target,
            duration_s=max(0.0, ramp_seconds),
        )
        if fault_type == "egt_sensor_drift":
            self.cylinder_for(fault_type, n_cylinders)
        if fault_type == "rpm_sensor_stuck":
            self._stuck_rpm = None  # capture the freeze value on first application
        if fault_type not in self.started_at and now is not None:
            self.started_at[fault_type] = now

    def clear(self, fault_type: str, ramp_seconds: float = 5.0) -> None:
        if fault_type not in SENSOR_FAULT_TYPES:
            raise ValueError(f"unknown sensor fault type: {fault_type}")
        self._ramps[fault_type] = _Ramp(
            start_value=self.severity(fault_type),
            target_value=0.0,
            duration_s=max(0.0, ramp_seconds),
        )

    def step(self, dt: float) -> None:
        finished: list[str] = []
        for fault_type, ramp in self._ramps.items():
            value, done = ramp.advance(dt)
            setattr(self, fault_type, value)
            if done:
                finished.append(fault_type)
        for fault_type in finished:
            del self._ramps[fault_type]
            if self.severity(fault_type) <= 1e-4:
                setattr(self, fault_type, 0.0)
                self.started_at.pop(fault_type, None)
                self.target_cylinder.pop(fault_type, None)
                if fault_type == "rpm_sensor_stuck":
                    self._stuck_rpm = None

    def snapshot(self) -> dict[str, float]:
        return {ft: self.severity(ft) for ft in SENSOR_FAULT_TYPES}


class SensorFaultModel:
    """Applies sensor corruption to a PlantState's reported values, in place.

    Call this *after* the physics has produced true values and after the twin has made its
    prediction — never before, or the corruption would feed back into the dynamics and
    stop being a sensor fault at all."""

    def __init__(self, params=None, seed: int = 6161) -> None:
        from app.core.engine_params import PARAMS

        self.p = params or PARAMS
        self._rng = random.Random(seed)

    def apply(self, state, sensor_faults: SensorFaultState) -> None:
        p = self.p
        corruptions: dict[str, float] = {}

        # --- EGT probe drift: a slowly growing offset on one cylinder's thermocouple ---
        sev = sensor_faults.egt_sensor_drift
        if sev > 1e-4 and state.egt_c:
            idx = sensor_faults.cylinder_for("egt_sensor_drift", len(state.egt_c))
            offset = p.sensor_egt_drift_max_c * sev
            state.egt_c[idx] += offset
            corruptions["egt_c"] = offset

        # --- Oil pressure sensor: excess noise plus occasional dropouts ---------------
        sev = sensor_faults.oil_pressure_sensor_noise
        if sev > 1e-4:
            before = state.oil_pressure_kpa
            noise = self._rng.gauss(0.0, p.sensor_oil_pressure_noise_kpa * sev)
            state.oil_pressure_kpa = max(0.0, state.oil_pressure_kpa + noise)
            # Dropouts: the reading collapses toward zero for a tick.
            if self._rng.random() < p.sensor_oil_pressure_dropout_prob * sev:
                state.oil_pressure_kpa *= 0.25
            corruptions["oil_pressure_kpa"] = state.oil_pressure_kpa - before

        # --- RPM sensor stuck: the reading freezes at whatever it last showed ---------
        sev = sensor_faults.rpm_sensor_stuck
        if sev > p.sensor_rpm_stuck_threshold:
            if sensor_faults._stuck_rpm is None:
                sensor_faults._stuck_rpm = state.rpm
            before = state.rpm
            state.rpm = sensor_faults._stuck_rpm
            corruptions["rpm"] = state.rpm - before
        elif sev <= 1e-4:
            sensor_faults._stuck_rpm = None

        sensor_faults.last_corruptions = corruptions
