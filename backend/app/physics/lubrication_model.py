"""Oil pressure from pump speed, oil viscosity, and bearing wear.

    P_oil = k1 * RPM * mu(T_oil) - k2 * wear_factor

Viscosity follows a Vogel-type temperature curve, which decreases monotonically with
temperature (hot oil is thin, so the same pump makes less pressure):

    mu(T) = A * exp(B / (T + C))        [Pa*s, T in degC]

`bearing_wear` raises `wear_factor` (increased running clearance leaks pressure away),
and `oil_pump_degradation` attacks the pump gain k1 directly. The two therefore produce
different pressure-vs-RPM signatures: pump degradation scales with speed, bearing wear is
an approximately constant offset — which is what lets the fault classifier tell them
apart from oil pressure alone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.engine_params import PARAMS, EngineParams
from app.physics.fault_models import FaultState


@dataclass
class LubricationOutputs:
    oil_pressure_kpa: float
    viscosity_pa_s: float
    wear_factor: float
    pump_gain: float


def oil_viscosity_pa_s(oil_temp_c: float, params: EngineParams = PARAMS) -> float:
    """Vogel equation: mu(T) = A * exp(B / (T + C)), monotonically falling with T."""
    denom = oil_temp_c + params.vogel_c_c
    if denom <= 1.0:
        denom = 1.0
    return params.vogel_a_pa_s * math.exp(params.vogel_b_c / denom)


class LubricationModel:
    def __init__(self, params: EngineParams = PARAMS) -> None:
        self.p = params
        self.oil_pressure_kpa: float = params.oil_pressure_min_kpa

    def reset(self) -> None:
        self.oil_pressure_kpa = self.p.oil_pressure_min_kpa

    def step(
        self,
        dt: float,
        rpm: float,
        oil_temp_c: float,
        fault_state: FaultState,
    ) -> LubricationOutputs:
        p = self.p

        mu = oil_viscosity_pa_s(oil_temp_c, p)
        # Blow-by past worn rings dilutes the oil with combustion products, thinning it.
        mu *= 1.0 - p.f_ring_wear_oil_visc_loss * fault_state.piston_ring_wear

        # A failing pump loses gain; worn bearings leak pressure away.
        pump_gain = p.oil_pressure_k1 * (
            1.0 - p.f_oil_pump_k1_loss * fault_state.oil_pump_degradation
        )
        wear_factor = p.f_bearing_wear_factor * fault_state.bearing_wear

        pressure = pump_gain * rpm * mu - p.oil_pressure_k2_kpa * wear_factor
        pressure = max(p.oil_pressure_min_kpa, min(p.oil_pressure_max_kpa, pressure))

        # Small hydraulic lag so the trace is smooth rather than instantaneous.
        self.oil_pressure_kpa += (pressure - self.oil_pressure_kpa) * min(1.0, dt / 0.25)

        return LubricationOutputs(
            oil_pressure_kpa=self.oil_pressure_kpa,
            viscosity_pa_s=mu,
            wear_factor=wear_factor,
            pump_gain=pump_gain,
        )
