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
]

Recommendation = Literal["GO", "CAUTION", "NO-GO"]


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


class HealthState(BaseModel):
    overall_score: float
    subsystem_scores: SubsystemScores


class MissionReliability(BaseModel):
    score: float
    recommendation: Recommendation


class ActiveFault(BaseModel):
    type: FaultType
    severity: float
    started_at: float


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
