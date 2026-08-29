"""Pydantic models mirroring frontend/lib/types.ts — the TelemetryFrame data contract
shared by the mock generator (Phase 1) and, unchanged, by the physics/ML pipeline
(Phase 2+)."""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

from app.core.engine_params import PARAMS

# Request bounds are sourced from the physics parameters rather than restated as
# literals, so widening the ECU's trim authority or the modelled weather envelope cannot
# leave the API validating against the old numbers.
_P = PARAMS

MissionPhase = Literal["climb", "cruise", "loiter", "descent"]

FaultType = Literal[
    "misfire",
    "spark_degradation",
    "piston_ring_wear",
    "bearing_wear",
    "oil_pump_degradation",
    "cooling_degradation",
    "fuel_injector_clog",
    "turbo_wear",
    "air_filter_clog",
    # --- Phase 3 additions -------------------------------------------------
    "battery_alternator_degradation",
    "injection_timing_drift",
]

#: Phase 3: faults that corrupt what a *sensor reports* rather than the engine itself.
#: Kept as a separate literal because they are injected through a different pipeline
#: (SensorFaultState, applied after the physics and after the twin) — see
#: app/physics/sensor_fault_model.py.
SensorFaultType = Literal[
    "egt_sensor_drift",
    "oil_pressure_sensor_noise",
    "rpm_sensor_stuck",
]

Recommendation = Literal["GO", "CAUTION", "NO-GO"]

#: Phase 5: recovery_reliability's own recommendation labels — deliberately never
#: GO/CAUTION/NO-GO, so the two reliability readouts can never be visually mistaken for
#: each other even when they happen to agree (see app/ml/mission_reliability.py).
RecoveryRecommendation = Literal["RTB-SAFE", "RTB-CAUTION", "RTB-AT-RISK"]

Urgency = Literal["monitor", "schedule_soon", "immediate"]

EfficiencyTrend = Literal["stable", "degrading", "improving"]

#: Whether an anomaly is believed to come from the machine or from the instrumentation.
PredictedSource = Literal["physical_fault", "sensor_fault", "uncertain"]


class CylinderReading(BaseModel):
    id: int
    egt_c: float
    vibration_rms: float


class SubsystemScores(BaseModel):
    cylinder: float
    lubrication: float
    cooling: float
    fuel: float
    turbo: float
    #: Phase 3 addition. Optional so every Phase 1/2 consumer that reads only the
    #: original five keys is unaffected.
    electrical: Optional[float] = None


class HealthState(BaseModel):
    overall_score: float
    subsystem_scores: SubsystemScores


class MissionReliability(BaseModel):
    score: float
    recommendation: Recommendation


class CHTSensorInnovations(BaseModel):
    """Each CHT probe's residual against the fused estimate — not against each other
    directly. A sustained large value on one side while the other stays small is a
    precise, quantitative signal that *that* probe specifically has a problem; see
    app/fusion/cht_fusion.py."""

    primary: float
    secondary: float


class RPMSensorInnovations(BaseModel):
    """Tachometer and vibration-derived RPM, each against the fused estimate. See
    app/fusion/rpm_fusion.py for what a persistent disagreement between them does and
    does not tell you on its own."""

    tachometer: float
    vibration_derived: float


class RecoveryReliability(BaseModel):
    """"Can it get back to base if we abort right now?" — a distinct question from
    `MissionReliability`'s "can it finish the rest of the planned mission?", with its
    own recommendation vocabulary so the two are never confused at a glance. See
    app/ml/mission_reliability.py::compute_recovery_reliability."""

    score: float
    recommendation: RecoveryRecommendation


class ClassifierExplanation(BaseModel):
    """Phase 3: why the classifier said what it said."""

    feature: str
    importance: float
    residual_value: float


class ActiveFault(BaseModel):
    type: str
    severity: float
    started_at: float
    # --- Phase 3 additions (optional, so Phase 1/2 consumers are unaffected) ----
    #: Whether this looks like a real physical fault or an instrumentation problem.
    predicted_source: Optional[PredictedSource] = None
    #: Top contributing residual features behind the classifier's call.
    classifier_explanation: Optional[list[ClassifierExplanation]] = None
    #: True when this entry describes a sensor fault rather than an engine fault.
    is_sensor_fault: bool = False


class MaintenanceAdvisory(BaseModel):
    """Phase 3: an actionable, plain-language recommendation for a ground crew."""

    subsystem: str
    urgency: Urgency
    recommendation: str
    basis: list[str]


class TelemetryFrame(BaseModel):
    timestamp: float
    mission_phase: MissionPhase
    rpm: float
    manifold_pressure_kpa: float
    boost_pressure_kpa: float
    cylinders: list[CylinderReading]
    cht_c: float
    oil_temp_c: float
    oil_pressure_kpa: float
    fuel_flow_lph: float
    altitude_m: float
    airspeed_ms: float
    health: HealthState
    rul_minutes: Optional[float] = None
    mission_reliability: MissionReliability
    active_faults: list[ActiveFault]

    # ---- Phase 3 additions --------------------------------------------------
    # All optional with defaults, so every Phase 1/2 component keeps working
    # against this schema without modification.

    #: Electrical subsystem (app/physics/electrical_model.py)
    battery_voltage_v: Optional[float] = None
    alternator_output_v: Optional[float] = None

    #: Commanded injection timing, crank degrees before top dead centre.
    injection_timing_deg: Optional[float] = None

    #: Cycle-to-cycle IMEP coefficient of variation — rises *before* a misfire fully
    #: manifests, so it carries genuine early-warning value.
    combustion_instability_pct: Optional[float] = None

    #: Ambient air temperature, now independent of the ISA altitude relation.
    ambient_temperature_c: Optional[float] = None

    #: Brake specific fuel consumption and its rolling trend.
    bsfc_g_per_kwh: Optional[float] = None
    efficiency_trend: Optional[EfficiencyTrend] = None

    #: Structured maintenance recommendations (app/ml/maintenance_advisor.py)
    maintenance_advisories: list[MaintenanceAdvisory] = []

    #: True when these frames are being replayed from a stored mission rather than
    #: generated live, so the UI can show a REPLAY badge.
    is_replay: bool = False

    #: Set during replay so the UI can identify the source mission.
    replay_mission_id: Optional[int] = None

    #: Phase 5: "can it get back to base if we abort right now?" — computed every tick
    #: alongside `mission_reliability`, but over the estimated time-to-RTB instead of the
    #: remaining planned mission. Optional so a frame replayed from a mission recorded
    #: before this field existed still validates.
    recovery_reliability: Optional[RecoveryReliability] = None

    # ---- Phase 5: sensor fusion (app/fusion/) --------------------------------
    # These are the values health scoring, RUL and residuals now actually consume —
    # `cht_c`, `rpm` and `oil_pressure_kpa` above already *are* the fused estimates
    # (see SimulationLoop.tick(), which overwrites them before anything downstream
    # reads them). These fields exist so the frontend and the validation script can see
    # the fusion working, not to carry a second, more-authoritative copy of the number.
    fused_cht_c: Optional[float] = None
    cht_sensor_innovations: Optional[CHTSensorInnovations] = None
    fused_rpm: Optional[float] = None
    rpm_sensor_innovations: Optional[RPMSensorInnovations] = None
    fused_oil_pressure_kpa: Optional[float] = None
    oil_pressure_innovation: Optional[float] = None
    #: Trending toward 1 means the fused estimate has shifted to trusting the raw sensor
    #: almost completely — the zero-wear model's own prediction confidence has degraded,
    #: which is itself a health signal (see oil_pressure_fusion.py's module docstring).
    oil_pressure_kalman_gain: Optional[float] = None


# ---- Phase 1/2 request models -----------------------------------------------
#
# Bounds here are load-bearing, not decoration. Without them the control handlers took
# whatever arrived and the *simulation* silently clamped it: `POST /control/throttle
# {"value": 5.0}` returned 200 OK with `throttle: 1.0`, and `{"value": NaN}` also
# returned 200. A caller sending a percentage where a 0-1 fraction was expected got
# full throttle and no indication anything was wrong. A 422 naming the field and the
# bound is the only honest answer to an out-of-range command.


class FaultInjectRequest(BaseModel):
    type: FaultType
    severity: float = Field(default=0.8, ge=0.0, le=1.0)
    ramp_seconds: float = Field(default=15.0, ge=0.0, le=3600.0)


class ClearFaultRequest(BaseModel):
    fault_type: FaultType


class ThrottleRequest(BaseModel):
    #: A 0-1 fraction, not a percentage — the same unit the ControlDeck slider emits.
    value: float = Field(ge=0.0, le=1.0)


class TimeScaleRequest(BaseModel):
    #: Matches the clamp `SimulationLoop.set_time_scale` applies.
    factor: float = Field(ge=0.1, le=50.0)


class PhaseJumpRequest(BaseModel):
    phase: MissionPhase


# ---- Phase 3 request models -------------------------------------------------


class SensorFaultRequest(BaseModel):
    type: SensorFaultType
    severity: float = Field(default=0.8, ge=0.0, le=1.0)
    ramp_seconds: float = Field(default=15.0, ge=0.0, le=3600.0)


class ClearSensorFaultRequest(BaseModel):
    fault_type: SensorFaultType


class MissionStartRequest(BaseModel):
    profile_name: str = Field(default="standard", min_length=1, max_length=64)
    notes: Optional[str] = Field(default=None, max_length=2000)


class ReplayStartRequest(BaseModel):
    mission_id: int = Field(ge=1)
    speed_factor: float = Field(default=1.0, ge=0.1, le=50.0)


class AmbientTemperatureRequest(BaseModel):
    #: Bounded to the same band the scenario validity envelope uses
    #: (`scenario_ambient_min_c` / `scenario_ambient_max_c`). Outside it the thermal and
    #: breathing models are extrapolating, and a live dashboard should not be quietly
    #: showing numbers from outside the calibrated range.
    ambient_temperature_c: Optional[float] = Field(
        default=None, ge=_P.scenario_ambient_min_c, le=_P.scenario_ambient_max_c
    )
    """None restores the ISA-derived temperature for the current altitude."""


class ScenarioRequest(BaseModel):
    scenario: str


# ---- Phase 4 request models (Test Bench + operating-point optimizer) ---------
#
# These describe *requests*, not telemetry. The TelemetryFrame contract above is
# unchanged by Phase 4 — a scenario returns a list of ordinary TelemetryFrames, so the
# Test Bench charts can reuse the live dashboard's components without a second schema.


class ThrottleWaypointRequest(BaseModel):
    """One point on a piecewise-linear throttle schedule."""

    time_min: float = Field(ge=0.0, description="Minutes from the start of the scenario")
    throttle_pct: float = Field(ge=0.0, le=100.0)


class ScheduledFaultRequest(BaseModel):
    """A fault that develops partway through a scenario."""

    fault_type: FaultType
    severity: float = Field(default=0.8, ge=0.0, le=1.0)
    at_time_min: float = Field(ge=0.0)
    ramp_minutes: float = Field(default=1.0, ge=0.0)


class ScenarioParamsRequest(BaseModel):
    """A what-if scenario for POST /simulate/scenario.

    Throttle is a **percentage** everywhere — a bare `throttle_profile: 78` means 78%, the
    same as a waypoint's `throttle_pct: 78`. Using a 0-1 fraction in one place and a
    percentage in the other is the kind of inconsistency that silently produces a 0.78%
    throttle run and a very confusing result.
    """

    altitude_m: float = 2400.0
    ambient_temperature_c: Optional[float] = Field(
        default=None,
        description="Null follows the ISA temperature for this altitude (standard day).",
    )
    duration_minutes: float = 20.0
    throttle_profile: Union[float, list[ThrottleWaypointRequest]] = Field(
        default=78.0,
        description="Constant percentage, or waypoints interpolated linearly between.",
    )
    initial_fault_severities: dict[str, float] = Field(
        default_factory=dict,
        description="Wear the engine already carries at t=0, as fault_type -> 0-1.",
    )
    injected_faults_during_scenario: list[ScheduledFaultRequest] = Field(
        default_factory=list
    )
    label: Optional[str] = Field(default=None, max_length=160)
    save: bool = Field(
        default=True,
        description="Record the parameters and summary in the scenario_runs table.",
    )
    include_frames: bool = Field(
        default=True,
        description="Return the full time-series. Set false for a summary-only run.",
    )


OptimizerObjective = Literal["max_range", "max_power", "max_engine_life", "balanced"]


class OperatingPointRequest(BaseModel):
    """A request for POST /optimize/operating-point."""

    altitude_m: float = Field(
        default=0.0, ge=_P.scenario_altitude_min_m, le=_P.scenario_altitude_max_m
    )
    ambient_temperature_c: Optional[float] = Field(
        default=None, ge=_P.scenario_ambient_min_c, le=_P.scenario_ambient_max_c
    )
    objective: OptimizerObjective = "balanced"
    use_current_engine_health: bool = Field(
        default=False,
        description=(
            "Optimise for the engine on the live simulation right now — its actual "
            "fault severities — instead of a pristine one."
        ),
    )


class ApplyPresetRequest(BaseModel):
    preset_name: str = Field(min_length=1, max_length=64)


class OperatingSetpointRequest(BaseModel):
    """Manual override of the live simulation's operating setpoint.

    Every field is optional and `None` means "leave this one alone", so a caller can trim
    the mixture without disturbing the throttle.
    """

    throttle: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    #: Bounded to the ECU's authority — the same clamps `EngineModel.step` applies
    #: (`afr_trim_min`/`afr_trim_max`, `injection_timing_trim_min_deg`/`_max_deg`).
    #: Accepting a wider value and clamping it silently made the response echo a
    #: setpoint the engine was never going to run.
    afr_trim: Optional[float] = Field(
        default=None, ge=_P.afr_trim_min, le=_P.afr_trim_max
    )
    injection_timing_trim_deg: Optional[float] = Field(
        default=None,
        ge=_P.injection_timing_trim_min_deg,
        le=_P.injection_timing_trim_max_deg,
    )


# ---- Phase 5: engine life-cycle ----------------------------------------------


class MaintenanceActionRequest(BaseModel):
    """Simulate a maintenance action against the engine's persisted wear ledger.

    Applies only to `engine_lifecycle.current_wear_state` — the value the *next*
    mission seeds from — never to whatever mission is live right now. See
    `app/db/lifecycle_repository.py::apply_maintenance_action` for why."""

    fault_type: FaultType
    description: str = Field(min_length=1, max_length=500)
    #: How much severity to clear, 0-1. 1.0 is a full replacement; something less is a
    #: partial fix (an oil change trimming bearing wear without a teardown).
    reset_amount: float = Field(ge=0.0, le=1.0)
