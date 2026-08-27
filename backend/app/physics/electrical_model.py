"""Battery and alternator.

Alternator output rises with crankshaft speed and saturates once the machine is spinning
fast enough to hit its regulator setpoint — modelled with a smooth saturating curve rather
than a hard knee:

    V_alt = V_regulated * tanh(RPM / rpm_knee)          (0 below cut-in speed)

Battery voltage chases the alternator through the pack's internal resistance, which
behaves like an RC lag — the capacitance analogy — and sags under electrical load:

    V_batt += (V_alt - I_load*R_internal - V_batt) * dt/tau

`battery_alternator_degradation` attacks both halves: it drops the alternator's regulated
output (worn brushes, failing diode pack) and raises the pack's internal resistance, so
the voltage sags harder the more load is drawn. That combination is what makes the fault
distinguishable from a simple low-RPM condition: at idle both look like low voltage, but
only the fault keeps the voltage low when RPM comes back up.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.engine_params import PARAMS, EngineParams
from app.physics.fault_models import FaultState


@dataclass
class ElectricalOutputs:
    battery_voltage_v: float
    alternator_output_v: float
    load_current_a: float
    charging: bool


class ElectricalModel:
    def __init__(self, params: EngineParams = PARAMS) -> None:
        self.p = params
        self.battery_voltage_v: float = params.battery_nominal_v
        self.alternator_output_v: float = 0.0

    def reset(self) -> None:
        self.battery_voltage_v = self.p.battery_nominal_v
        self.alternator_output_v = 0.0

    def step(
        self,
        dt: float,
        rpm: float,
        fault_state: FaultState,
        electrical_load_a: float | None = None,
    ) -> ElectricalOutputs:
        import math

        p = self.p
        severity = fault_state.battery_alternator_degradation

        load_a = p.electrical_load_a if electrical_load_a is None else electrical_load_a

        # Alternator: nothing below cut-in, then a saturating rise with speed.
        regulated = p.alternator_regulated_v * (1.0 - p.f_alternator_output_loss * severity)
        if rpm <= p.alternator_cutin_rpm:
            v_alt = 0.0
        else:
            span = max(1.0, rpm - p.alternator_cutin_rpm)
            v_alt = regulated * math.tanh(span / p.alternator_knee_rpm)
        self.alternator_output_v = v_alt

        # Battery: chases the alternator through internal resistance, sags under load.
        r_internal = p.battery_internal_ohm * (
            1.0 + p.f_battery_resistance_growth * severity
        )
        if v_alt > p.battery_nominal_v:
            # Charging: terminal voltage pulled up toward the alternator, minus IR drop.
            target = v_alt - load_a * r_internal
        else:
            # Discharging: the pack alone supports the load.
            target = p.battery_nominal_v - load_a * r_internal

        self.battery_voltage_v += (target - self.battery_voltage_v) * min(
            1.0, dt / max(1e-3, p.battery_tau_s)
        )
        self.battery_voltage_v = max(6.0, min(p.alternator_regulated_v + 1.0, self.battery_voltage_v))

        return ElectricalOutputs(
            battery_voltage_v=self.battery_voltage_v,
            alternator_output_v=self.alternator_output_v,
            load_current_a=load_a,
            charging=v_alt > self.battery_voltage_v,
        )
