"""Two first-order thermal ODEs, integrated with the same sub-stepper as the engine.

Cylinder head temperature:
    m_head * cp_head * dT_cht/dt = Q_combustion - Q_cooling
    Q_cooling = h_eff * (T_cht - T_ambient)

`h_eff` is the head-to-air conductance. It scales with airspeed (forced convection,
h ~ v^0.55) and with air density, and is reduced directly by `cooling_degradation` —
a blocked duct or failing cooling fan.

Oil temperature:
    m_oil * cp_oil * dT_oil/dt = Q_friction_pickup + Q_from_head - Q_oil_cooler
    Q_friction_pickup = friction_power * oil_friction_pickup_fraction
                        + blow-by heat from piston_ring_wear

Both temperatures are carried as state in Celsius.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.engine_params import PARAMS, EngineParams
from app.physics.environment import atmosphere, density_ratio, isa_deviation_k
from app.physics.fault_models import FaultState


@dataclass
class ThermalOutputs:
    cht_c: float
    oil_temp_c: float
    cooling_conductance_w_per_k: float
    heat_rejected_head_w: float
    heat_into_oil_w: float


class ThermalModel:
    def __init__(self, params: EngineParams = PARAMS) -> None:
        self.p = params
        self.cht_c: float = params.cht_init_c
        self.oil_temp_c: float = params.oil_temp_init_c

    def reset(self) -> None:
        self.cht_c = self.p.cht_init_c
        self.oil_temp_c = self.p.oil_temp_init_c

    def step(
        self,
        dt: float,
        heat_to_head_w: float,
        friction_power_w: float,
        altitude_m: float,
        airspeed_ms: float,
        fault_state: FaultState,
        cooling_flap_command: float = 1.0,
        ambient_temperature_c: float | None = None,
    ) -> ThermalOutputs:
        p = self.p
        atm = atmosphere(altitude_m, ambient_temperature_c)
        t_ambient_c = atm.temperature_c

        # ---- cylinder head ----------------------------------------------------
        # Forced-convection cooling: more airspeed and denser air reject more heat.
        speed_factor = (
            max(0.15, airspeed_ms) / p.cooling_airspeed_ref_ms
        ) ** p.cooling_airspeed_exponent
        density_factor = density_ratio(altitude_m, ambient_temperature_c) ** 0.5
        # A hot day hurts cooling twice over: the driving temperature difference
        # (T_cht - T_ambient) shrinks on its own, and the cooling air itself is thinner
        # and carries less heat per unit volume. The second effect is this term.
        hot_day_k = isa_deviation_k(altitude_m, ambient_temperature_c)
        hot_day_factor = max(
            0.45, 1.0 - p.cooling_hot_weather_loss_per_k * max(0.0, hot_day_k)
        )
        # Cooling flaps are scheduled off power demand: they close as the engine is
        # throttled back, which is what stops CHT collapsing during a descent.
        flap_factor = p.cooling_flap_min_fraction + (
            1.0 - p.cooling_flap_min_fraction
        ) * max(0.0, min(1.0, cooling_flap_command))
        conductance = (
            p.cooling_effectiveness_w_per_k
            * speed_factor
            * density_factor
            * flap_factor
            * hot_day_factor
            * (1.0 - p.f_cooling_effectiveness_loss * fault_state.cooling_degradation)
        )
        conductance = max(4.0, conductance)

        q_cooling_w = conductance * (self.cht_c - t_ambient_c)
        dcht_dt = (heat_to_head_w - q_cooling_w) / p.head_thermal_mass_j_per_k
        self.cht_c += dcht_dt * dt

        # ---- oil --------------------------------------------------------------
        q_friction_w = friction_power_w * p.oil_friction_pickup_fraction
        # Blow-by past worn rings puts hot combustion gas into the crankcase.
        q_blowby_w = p.f_ring_wear_oil_heat_w * fault_state.piston_ring_wear
        # Conduction from the (hotter) head into the oil galleries.
        q_from_head_w = p.oil_head_coupling_w_per_k * (self.cht_c - self.oil_temp_c)
        q_cooler_w = p.oil_cooler_w_per_k * speed_factor * (self.oil_temp_c - t_ambient_c)

        heat_into_oil_w = q_friction_w + q_blowby_w + q_from_head_w
        doil_dt = (heat_into_oil_w - q_cooler_w) / p.oil_thermal_mass_j_per_k
        self.oil_temp_c += doil_dt * dt

        # Physical bounds — nothing cools below ambient, nothing survives past these.
        self.cht_c = max(t_ambient_c, min(400.0, self.cht_c))
        self.oil_temp_c = max(t_ambient_c, min(220.0, self.oil_temp_c))

        return ThermalOutputs(
            cht_c=self.cht_c,
            oil_temp_c=self.oil_temp_c,
            cooling_conductance_w_per_k=conductance,
            heat_rejected_head_w=q_cooling_w,
            heat_into_oil_w=heat_into_oil_w,
        )
