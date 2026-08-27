"""Turbocharger: boost pressure with first-order spool lag, plus compressor outlet
temperature.

    P_target  = p_ambient * PR_target,   capped by the wastegate
    PR_target = 1 + (PR_max - 1) * throttle * rpm_norm
    dP_boost/dt = (P_target - P_boost) / tau_turbo

Compressor discharge temperature follows the isentropic relation corrected by
compressor efficiency, which is what raises intake charge temperature under boost:

    T_out = T_amb * (1 + (PR^((gamma-1)/gamma) - 1) / eta_compressor)

`turbo_wear` severity both lengthens tau_turbo (sluggish spool) and caps PR_target lower
(the compressor can no longer make its rated pressure ratio).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.engine_params import PARAMS, EngineParams
from app.physics.environment import atmosphere
from app.physics.fault_models import FaultState


@dataclass
class TurboOutputs:
    boost_pressure_kpa: float      # absolute pressure delivered to the throttle body
    compressor_outlet_temp_k: float
    pressure_ratio: float
    tau_s: float


class TurboModel:
    def __init__(self, params: EngineParams = PARAMS) -> None:
        self.p = params
        self.boost_pressure_kpa: float = atmosphere(0.0).pressure_kpa
        self._initialised = False

    def reset(self, altitude_m: float) -> None:
        self.boost_pressure_kpa = atmosphere(altitude_m).pressure_kpa
        self._initialised = True

    def step(
        self,
        dt: float,
        rpm: float,
        throttle: float,
        altitude_m: float,
        fault_state: FaultState,
        inlet_pressure_kpa: float | None = None,
    ) -> TurboOutputs:
        """`inlet_pressure_kpa` is the pressure at the compressor inlet — ambient minus
        whatever the air filter is costing. The filter sits *upstream* of the compressor,
        so a clog lowers what the turbo has to work with and boost droops accordingly."""
        p = self.p
        atm = atmosphere(altitude_m)
        p_amb_kpa = atm.pressure_kpa
        p_inlet_kpa = p_amb_kpa if inlet_pressure_kpa is None else max(
            5.0, inlet_pressure_kpa
        )

        if not self._initialised:
            self.reset(altitude_m)

        wear = fault_state.turbo_wear

        # Wear reduces achievable pressure ratio and slows the spool.
        pr_max_eff = 1.0 + (p.boost_pressure_ratio_max - 1.0) * (
            1.0 - p.f_turbo_pr_loss * wear
        )
        tau_eff = p.turbo_tau_s * (1.0 + p.f_turbo_tau_growth * wear)

        # Exhaust energy available to drive the turbine scales with speed and load.
        rpm_norm = _clamp(
            (rpm - p.rpm_idle) / max(1.0, p.rpm_redline - p.rpm_idle), 0.0, 1.0
        )
        pr_target = 1.0 + (pr_max_eff - 1.0) * _clamp(throttle, 0.0, 1.0) * rpm_norm

        p_target_kpa = min(p_inlet_kpa * pr_target, p.turbo_wastegate_limit_kpa)

        # First-order lag toward the target.
        self.boost_pressure_kpa += (p_target_kpa - self.boost_pressure_kpa) * (
            dt / max(1e-3, tau_eff)
        )
        self.boost_pressure_kpa = max(p_inlet_kpa * 0.5, self.boost_pressure_kpa)

        pressure_ratio = max(1.0, self.boost_pressure_kpa / max(1e-6, p_inlet_kpa))
        exponent = (p.gamma_air - 1.0) / p.gamma_air
        t_out = atm.temperature_k * (
            1.0
            + (pressure_ratio**exponent - 1.0) / max(0.05, p.compressor_isentropic_eff)
        )

        return TurboOutputs(
            boost_pressure_kpa=self.boost_pressure_kpa,
            compressor_outlet_temp_k=t_out,
            pressure_ratio=pressure_ratio,
            tau_s=tau_eff,
        )


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
