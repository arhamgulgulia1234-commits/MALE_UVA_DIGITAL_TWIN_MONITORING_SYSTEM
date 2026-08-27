"""Pydantic models mirroring frontend/lib/types.ts — the TelemetryFrame data contract
shared by the mock generator (Phase 1) and, unchanged, by the physics/ML pipeline
(Phase 2+)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

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


class FaultInjectRequest(BaseModel):
    type: FaultType
    severity: float = 0.8
    ramp_seconds: float = 15.0


class ClearFaultRequest(BaseModel):
    fault_type: FaultType


class ThrottleRequest(BaseModel):
    value: float


class TimeScaleRequest(BaseModel):
    factor: float


class PhaseJumpRequest(BaseModel):
    phase: MissionPhase


# ---- Phase 3 request models -------------------------------------------------


class SensorFaultRequest(BaseModel):
    type: SensorFaultType
    severity: float = 0.8
    ramp_seconds: float = 15.0


class ClearSensorFaultRequest(BaseModel):
    fault_type: SensorFaultType


class MissionStartRequest(BaseModel):
    profile_name: str = "standard"
    notes: Optional[str] = None


class ReplayStartRequest(BaseModel):
    mission_id: int
    speed_factor: float = 1.0


class AmbientTemperatureRequest(BaseModel):
    ambient_temperature_c: Optional[float] = None
    """None restores the ISA-derived temperature for the current altitude."""


class ScenarioRequest(BaseModel):
    scenario: str
