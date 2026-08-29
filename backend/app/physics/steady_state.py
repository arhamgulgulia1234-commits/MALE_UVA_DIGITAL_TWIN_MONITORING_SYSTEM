"""Relax an `EnginePlant` to the steady state of a commanded operating point.

Two Phase 4 features need the same thing: "if this engine were held at this throttle, at
this altitude, on this day, where would every signal settle?"

  * `app/sim/scenario_engine.py` uses it to start a what-if run from a stabilised engine
    rather than from a cold crank — otherwise the first minute of every scenario is a
    start-up transient, and an oil-pressure gauge still on its way up reads as a
    lubrication excursion.
  * `app/ml/operating_point_optimizer.py` uses it as the objective function. A search over
    throttle, mixture trim and timing trim needs hundreds of evaluations, and each one has
    to answer the steady-state question.

Why not just integrate forward
------------------------------
Time-marching the plant to thermal equilibrium at the live loop's 20 ms sub-step takes
about 2 000 simulated seconds — 100 000 sub-steps, several seconds of CPU, for *one*
operating point. That is fine once and hopeless as an objective function.

The way out is that this plant's fast and slow dynamics are genuinely decoupled.
`EngineModel.step()` takes throttle, altitude, airspeed, ambient temperature and the fault
state — and no temperature from the thermal model. Cylinder head and oil temperature are
therefore *downstream* of the thermodynamic core, never upstream of it, so once the
engine has settled its heat output is a constant and the two thermal ODEs can be relaxed
on their own at a step sized for their own time constants (about 250 s for the head, 440 s
for the oil) instead of the manifold's 120 ms.

That is the entire trick: the same model objects, the same equations, stepped at the rate
each one actually needs. Nothing here re-implements any physics — every number still comes
out of `EngineModel`, `ThermalModel`, `LubricationModel`, `ElectricalModel` and
`VibrationModel`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.compute_budget import yield_to_event_loop
from app.core.engine_params import PARAMS, EngineParams
from app.physics.fault_models import FaultState
from app.physics.plant import EnginePlant

# ---- relaxation schedule -----------------------------------------------------

FAST_DT_S = 0.02
"""Same sub-step the live loop uses, so the fast states settle exactly as they would in
flight."""

FAST_STEPS_MIN = 200
FAST_STEPS_MAX = 900
"""Between 4 and 18 simulated seconds, with an early exit once crankshaft speed stops
moving.

The turbocharger settles in about 5 s (tau = 0.95 s), but crankshaft speed is slower and
its settling time depends on where on the propeller's omega-squared load curve the engine
lands, so a fixed step count is either wasteful at one end of the throttle range or
truncated at the other. Truncation matters here: the optimizer compares setpoints against
each other, and a residual RPM drift of a few tens biases power by a percent — the same
order as the differences it is trying to resolve."""

FAST_CONVERGENCE_WINDOW = 25
FAST_CONVERGENCE_RPM = 0.05
"""Settled when crankshaft speed moves less than this over half a second."""

FAST_AVERAGE_STEPS = 100
"""Engine outputs are averaged over the last two seconds of the fast phase. Combustion
carries deliberate cycle-to-cycle IMEP scatter, and a steady-state answer taken from a
single sub-step would inherit that noise straight into the optimizer's objective."""

COARSE_DT_S = 10.0
"""Thermal relaxation step. Against a ~250 s head time constant this is dt/tau = 0.04 —
two orders of magnitude inside the explicit-Euler stability limit."""

MAX_COARSE_STEPS = 600
"""Ceiling of 6 000 simulated seconds. Reached only if the thermal balance has no
equilibrium inside the model's clamps (for example a head pinned at its 400 degC bound)."""

CONVERGENCE_TOL_K = 0.005
"""Per-step change in both head and oil temperature below which the state is settled."""

VIBRATION_WINDOW_S = 0.25


@dataclass
class SteadyState:
    """Where every signal settles at one commanded operating point."""

    throttle: float
    altitude_m: float
    airspeed_ms: float
    ambient_temperature_c: float | None

    rpm: float
    manifold_pressure_kpa: float
    boost_pressure_kpa: float
    egt_c: list[float] = field(default_factory=list)
    egt_mean_c: float = 0.0
    egt_max_c: float = 0.0
    cht_c: float = 0.0
    oil_temp_c: float = 0.0
    oil_pressure_kpa: float = 0.0
    fuel_flow_lph: float = 0.0
    afr_mean: float = 0.0
    power_brake_kw: float = 0.0
    torque_brake_nm: float = 0.0
    friction_power_w: float = 0.0
    eta_vol: float = 0.0
    eta_comb_mean: float = 0.0
    vibration_rms_mean: float = 0.0
    battery_voltage_v: float = 0.0
    alternator_output_v: float = 0.0
    injection_timing_deg: float = 0.0
    afr_trim: float = 0.0
    injection_timing_trim_deg: float = 0.0

    converged: bool = True
    thermal_steps: int = 0
    fast_steps: int = 0

    @property
    def bsfc_g_per_kwh(self) -> float | None:
        """Brake specific fuel consumption, or None where the denominator is meaningless.

        Below a few kW the engine is idling and BSFC diverges; reporting a huge number
        there would let a low-power point look like a catastrophic efficiency result
        rather than simply an undefined one.
        """
        if self.power_brake_kw < 1.0 or self.fuel_flow_lph <= 0.0:
            return None
        fuel_g_per_h = self.fuel_flow_lph * PARAMS.fuel_density_kg_per_l * 1000.0
        return fuel_g_per_h / self.power_brake_kw

    def to_dict(self) -> dict:
        bsfc = self.bsfc_g_per_kwh
        return {
            "throttle_pct": round(self.throttle * 100.0, 1),
            "afr_trim": round(self.afr_trim, 3),
            "injection_timing_trim_deg": round(self.injection_timing_trim_deg, 2),
            "rpm": round(self.rpm, 0),
            "power_kw": round(self.power_brake_kw, 2),
            "torque_nm": round(self.torque_brake_nm, 1),
            "bsfc_g_per_kwh": round(bsfc, 1) if bsfc is not None else None,
            "fuel_flow_lph": round(self.fuel_flow_lph, 2),
            "cht_c": round(self.cht_c, 1),
            "egt_max_c": round(self.egt_max_c, 1),
            "egt_mean_c": round(self.egt_mean_c, 1),
            "oil_temp_c": round(self.oil_temp_c, 1),
            "oil_pressure_kpa": round(self.oil_pressure_kpa, 1),
            "manifold_pressure_kpa": round(self.manifold_pressure_kpa, 1),
            "boost_pressure_kpa": round(self.boost_pressure_kpa, 1),
            "afr_mean": round(self.afr_mean, 2),
            "injection_timing_deg": round(self.injection_timing_deg, 2),
            "vibration_rms_g": round(self.vibration_rms_mean, 4),
            "battery_voltage_v": round(self.battery_voltage_v, 2),
        }


def relax_to_steady_state(
    plant: EnginePlant,
    throttle: float,
    altitude_m: float,
    airspeed_ms: float,
    fault_state: FaultState,
    ambient_temperature_c: float | None = None,
    params: EngineParams = PARAMS,
    reset: bool = True,
) -> SteadyState:
    """Settle `plant` at a commanded operating point and report where everything landed.

    The plant is left *at* that state, so a caller can carry straight on integrating it —
    which is exactly how `scenario_engine` warm-starts a run.
    """
    throttle = max(0.0, min(1.0, throttle))
    if reset:
        plant.reset(altitude_m)

    # ---- phase 1: the fast states -------------------------------------------
    # The engine alone: turbo, manifold and crankshaft speed. Nothing downstream is
    # stepped here, because nothing downstream feeds back into the engine — head and oil
    # temperature are consequences of its heat output, not inputs to it — so the thermal,
    # lubrication and electrical models are left for phase 2, where they can be relaxed at
    # a step sized for their own time constants instead of the manifold's.
    sums = {"heat_to_head_w": 0.0, "friction_power_w": 0.0, "rpm": 0.0,
            "power_kw": 0.0, "torque_nm": 0.0, "fuel_lph": 0.0, "afr": 0.0,
            "eta_vol": 0.0, "eta_comb": 0.0, "map_kpa": 0.0, "boost_kpa": 0.0}
    n_avg = 0
    egt_c: list[float] = []
    injection_timing_deg = params.injection_timing_nominal_deg

    # Two passes: run until crankshaft speed settles, then average over a fixed window at
    # the settled condition. Averaging is a separate pass so the window is always the same
    # length regardless of how long settling took.
    rpm_marker = plant.engine.rpm
    fast_steps = FAST_STEPS_MIN
    for step in range(1, FAST_STEPS_MAX + 1):
        plant.engine.step(
            FAST_DT_S,
            throttle,
            altitude_m,
            fault_state,
            airspeed_ms=airspeed_ms,
            ambient_temperature_c=ambient_temperature_c,
        )
        if step % FAST_CONVERGENCE_WINDOW == 0:
            yield_to_event_loop()
            settled = abs(plant.engine.rpm - rpm_marker) < FAST_CONVERGENCE_RPM
            rpm_marker = plant.engine.rpm
            if settled and step >= FAST_STEPS_MIN:
                fast_steps = step
                break
        fast_steps = step

    for _ in range(FAST_AVERAGE_STEPS):
        eng = plant.engine.step(
            FAST_DT_S,
            throttle,
            altitude_m,
            fault_state,
            airspeed_ms=airspeed_ms,
            ambient_temperature_c=ambient_temperature_c,
        )
        sums["heat_to_head_w"] += eng.heat_to_head_w
        sums["friction_power_w"] += eng.friction_power_w
        sums["rpm"] += eng.rpm
        sums["power_kw"] += eng.power_brake_kw
        sums["torque_nm"] += eng.torque_brake_nm
        sums["fuel_lph"] += eng.fuel_flow_lph
        sums["afr"] += eng.afr_mean
        sums["eta_vol"] += eng.eta_vol
        sums["eta_comb"] += eng.eta_comb_mean
        sums["map_kpa"] += eng.manifold_pressure_kpa
        sums["boost_kpa"] += eng.boost_pressure_kpa
        n_avg += 1
        egt_c = list(eng.egt_c)
        injection_timing_deg = eng.injection_timing_deg

    avg = {k: v / max(1, n_avg) for k, v in sums.items()}

    # ---- phase 2: the thermal states ----------------------------------------
    # The engine is settled, so its heat output is now a constant boundary condition and
    # the head/oil ODEs can be relaxed at a step sized for their own time constants.
    converged = False
    thermal_steps = 0
    for thermal_steps in range(1, MAX_COARSE_STEPS + 1):
        if thermal_steps % 100 == 0:
            yield_to_event_loop()
        prev_cht = plant.thermal.cht_c
        prev_oil = plant.thermal.oil_temp_c
        plant.thermal.step(
            COARSE_DT_S,
            heat_to_head_w=avg["heat_to_head_w"],
            friction_power_w=avg["friction_power_w"],
            altitude_m=altitude_m,
            airspeed_ms=airspeed_ms,
            fault_state=fault_state,
            cooling_flap_command=throttle,
            ambient_temperature_c=ambient_temperature_c,
        )
        plant.lubrication.step(
            COARSE_DT_S,
            rpm=avg["rpm"],
            oil_temp_c=plant.thermal.oil_temp_c,
            fault_state=fault_state,
        )
        plant.electrical.step(COARSE_DT_S, avg["rpm"], fault_state)
        if (
            abs(plant.thermal.cht_c - prev_cht) < CONVERGENCE_TOL_K
            and abs(plant.thermal.oil_temp_c - prev_oil) < CONVERGENCE_TOL_K
        ):
            converged = True
            break

    # ---- phase 3: re-settle EGT at the converged condition -------------------
    # The exhaust probes have a 1.6 s time constant and phase 1 already settled them, but
    # a short re-run keeps the engine's internal state consistent with the temperatures
    # the thermal relaxation arrived at, and leaves the plant ready to keep integrating.
    for _ in range(FAST_AVERAGE_STEPS):
        eng = plant.engine.step(
            FAST_DT_S,
            throttle,
            altitude_m,
            fault_state,
            airspeed_ms=airspeed_ms,
            ambient_temperature_c=ambient_temperature_c,
        )
        egt_c = list(eng.egt_c)
        injection_timing_deg = eng.injection_timing_deg

    vib = plant.vibration.generate_window(
        VIBRATION_WINDOW_S,
        rpm=avg["rpm"],
        fault_state=fault_state,
        misfire_active=False,
        with_features=False,
    )
    vib_rms_mean = sum(vib.rms_per_cylinder) / max(1, len(vib.rms_per_cylinder))

    state = SteadyState(
        throttle=throttle,
        altitude_m=altitude_m,
        airspeed_ms=airspeed_ms,
        ambient_temperature_c=ambient_temperature_c,
        rpm=avg["rpm"],
        manifold_pressure_kpa=avg["map_kpa"],
        boost_pressure_kpa=avg["boost_kpa"],
        egt_c=egt_c,
        egt_mean_c=sum(egt_c) / len(egt_c) if egt_c else 0.0,
        egt_max_c=max(egt_c) if egt_c else 0.0,
        cht_c=plant.thermal.cht_c,
        oil_temp_c=plant.thermal.oil_temp_c,
        oil_pressure_kpa=plant.lubrication.oil_pressure_kpa,
        fuel_flow_lph=avg["fuel_lph"],
        afr_mean=avg["afr"],
        power_brake_kw=avg["power_kw"],
        torque_brake_nm=avg["torque_nm"],
        friction_power_w=avg["friction_power_w"],
        eta_vol=avg["eta_vol"],
        eta_comb_mean=avg["eta_comb"],
        vibration_rms_mean=vib_rms_mean,
        battery_voltage_v=plant.electrical.battery_voltage_v,
        alternator_output_v=plant.electrical.alternator_output_v,
        injection_timing_deg=injection_timing_deg,
        afr_trim=plant.engine.afr_trim,
        injection_timing_trim_deg=plant.engine.injection_timing_trim_deg,
        converged=converged,
        thermal_steps=thermal_steps,
        fast_steps=fast_steps,
    )

    # Publish into the plant's own state object so a caller that goes on to use the plant
    # normally (the scenario engine does) sees a consistent picture from sub-step one.
    plant.state.rpm = state.rpm
    plant.state.manifold_pressure_kpa = state.manifold_pressure_kpa
    plant.state.boost_pressure_kpa = state.boost_pressure_kpa
    plant.state.egt_c = list(state.egt_c)
    plant.state.cht_c = state.cht_c
    plant.state.oil_temp_c = state.oil_temp_c
    plant.state.oil_pressure_kpa = state.oil_pressure_kpa
    plant.state.fuel_flow_lph = state.fuel_flow_lph
    plant.state.afr_mean = state.afr_mean
    plant.state.torque_brake_nm = state.torque_brake_nm
    plant.state.power_brake_kw = state.power_brake_kw
    plant.state.eta_vol = state.eta_vol
    plant.state.eta_comb_mean = state.eta_comb_mean
    plant.state.vibration_rms = list(vib.rms_per_cylinder)
    plant.state.battery_voltage_v = state.battery_voltage_v
    plant.state.alternator_output_v = state.alternator_output_v
    plant.state.injection_timing_deg = state.injection_timing_deg

    return state
