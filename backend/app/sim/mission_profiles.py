"""Nominal per-mission-phase signal targets driving the Phase 1 mock generator.

Mirrors the "Mission phase -> nominal signal targets" table in docs/physics-model.md.
Phase 2's app/physics/engine_model.py + environment.py will replace these hand-tuned
targets with values derived from the actual thermodynamic/atmosphere models, but the
phase list and rough shape (climb hot+high-RPM, cruise steady, loiter low, descent
throttled-down) should carry over.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MissionPhase = Literal["climb", "cruise", "loiter", "descent"]

PHASE_ORDER: list[MissionPhase] = ["climb", "cruise", "loiter", "descent"]

NUM_CYLINDERS = 4
BASE_ALTITUDE_M = 1800.0


@dataclass(frozen=True)
class PhaseProfile:
    duration_s: float
    rpm: float
    manifold_kpa: float
    boost_kpa: float
    egt_c: float
    cht_c: float
    oil_temp_c: float
    oil_pressure_kpa: float
    fuel_flow_lph: float
    altitude_rate_m_s: float
    airspeed_ms: float


PHASE_PROFILES: dict[MissionPhase, PhaseProfile] = {
    "climb": PhaseProfile(
        duration_s=40, rpm=5350, manifold_kpa=95, boost_kpa=145, egt_c=780,
        cht_c=195, oil_temp_c=95, oil_pressure_kpa=420, fuel_flow_lph=28,
        altitude_rate_m_s=6.5, airspeed_ms=38,
    ),
    "cruise": PhaseProfile(
        duration_s=60, rpm=4400, manifold_kpa=75, boost_kpa=110, egt_c=690,
        cht_c=175, oil_temp_c=90, oil_pressure_kpa=400, fuel_flow_lph=16,
        altitude_rate_m_s=0.0, airspeed_ms=45,
    ),
    "loiter": PhaseProfile(
        duration_s=50, rpm=3200, manifold_kpa=55, boost_kpa=85, egt_c=590,
        cht_c=155, oil_temp_c=84, oil_pressure_kpa=370, fuel_flow_lph=9,
        altitude_rate_m_s=-0.5, airspeed_ms=32,
    ),
    "descent": PhaseProfile(
        duration_s=35, rpm=3500, manifold_kpa=45, boost_kpa=70, egt_c=520,
        cht_c=140, oil_temp_c=78, oil_pressure_kpa=360, fuel_flow_lph=7,
        altitude_rate_m_s=-7.0, airspeed_ms=40,
    ),
}
