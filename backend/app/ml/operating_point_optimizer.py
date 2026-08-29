"""Operating-point optimisation: what throttle, mixture and timing should this engine run?

Given an altitude, an outside air temperature, an objective and — optionally — the wear the
engine is already carrying, this searches for the setpoint that best serves that objective
*within the engine's operating limits*, and reports what it costs against the book cruise
setting.

Search variables
----------------
``throttle`` (fraction), ``afr_trim`` (AFR units offset from the scheduled mixture,
positive = leaner) and ``injection_timing_trim`` (crank degrees offset from nominal,
positive = advance). All three are real levers on this engine model, and each one buys
something at somebody else's expense:

* Leaning burns less fuel per unit air and improves BSFC, but moves EGT toward its peak
  and — because a lean charge dumps a larger share of its residual heat into the head
  rather than out of the pipe — raises CHT sharply. Enriching does the reverse: it is a
  charge-cooling lever that costs fuel. This is why real aero engines run rich of peak at
  high power, and the model reproduces it.
* Nominal injection timing is MBT, so any trim costs combustion efficiency. What it buys
  is a CHT-versus-EGT trade: advance keeps heat in the cylinder, retard sends it out of
  the valve.
* Throttle moves everything at once.

Objectives
----------
``max_range``       minimise BSFC while *holding* cruise power. Range is not "fly
                    slower" — it is the same work for less fuel — so power is pinned to
                    within `cruise_power_hold_fraction` of what the book setting makes
                    here, above and below, and only mixture and timing are really free.
                    Without the upper bound the objective degenerates: BSFC improves with
                    load, because friction becomes a smaller share of indicated work, so
                    "minimise BSFC" would answer "open the throttle".
``max_power``       maximise brake power, subject to every safety limit and to the
                    health-derated continuous power rating.
``max_engine_life`` minimise the combined thermal + mechanical stress rate, subject to
                    still producing `life_min_power_fraction` of cruise power.
``balanced``        a weighted combination of all three, each normalised against the
                    baseline so the weights mean what they say.

Hard safety bounds
------------------
CHT, EGT, oil temperature and oil pressure limits come from `engine_params.py` and are
**hard**. The optimizer never returns a setpoint the engine model itself predicts would
breach one: the returned point is always re-evaluated and its per-limit margins reported.
Where the engine's condition makes *every* setpoint unsafe, the result says so
(`feasible=False`) with the failing checks attached, rather than quietly returning the
least-bad point as if it were a recommendation. "This engine should not be dispatched" is
a legitimate answer and the only honest one.

Accounting for existing wear
----------------------------
`current_health_state` is a fault-severity map — the same vocabulary
`app/physics/fault_models.py` uses. It enters in two ways:

1. **Through the physics.** The candidate setpoints are evaluated on an engine that
   actually has that wear, so a worn bearing's lost oil pressure and a clogged filter's
   lost breathing are in every number.
2. **Through the limits.** Worn hardware gets a derated envelope: lower CHT/EGT/oil-temp
   ceilings, a higher oil-pressure floor, and — the important one — a reduced continuous
   power rating. That last is what makes `max_power` on a badly degraded engine recommend
   *less* power than the book setting rather than blindly winding the throttle open.

A caveat worth stating: the stress-rate model is a physically-motivated ordering, not a
calibrated life model. The Arrhenius-style thermal terms and the reference point are tuned
so nominal cruise reads 1.0; turning the resulting hours figure into a real overhaul
interval needs run-to-failure data this project does not have. See
docs/deployment-roadmap.md.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Literal

from app.core.compute_budget import yield_to_event_loop
from app.core.engine_params import PARAMS, EngineParams
from app.physics.environment import isa_deviation_k
from app.physics.fault_models import FAULT_TYPES, FaultState
from app.physics.plant import EnginePlant
from app.physics.steady_state import SteadyState, relax_to_steady_state

logger = logging.getLogger(__name__)

Objective = Literal["max_range", "max_power", "max_engine_life", "balanced"]

OBJECTIVES: tuple[str, ...] = (
    "max_range",
    "max_power",
    "max_engine_life",
    "balanced",
)

OBJECTIVE_LABELS: dict[str, str] = {
    "max_range": "Max Range",
    "max_power": "Max Power",
    "max_engine_life": "Max Engine Life",
    "balanced": "Balanced",
}

# ---- search resolution -------------------------------------------------------

GRID_THROTTLE = 6
GRID_AFR = 4
GRID_TIMING = 3
"""Coarse grid resolution per axis. Its job is to find the right basin, not the exact
optimum — the local refinement below does that. The bounded region is small and the
surface is smooth, so a grid this coarse is enough to avoid the local minima a pure
gradient method would fall into around the mixture's EGT peak.

Zero is forced onto both trim axes and the book cruise throttle onto the throttle axis, so
the baseline setpoint is always *in* the search space. Without that, an optimizer can
return something worse than the setting it is being compared against simply because the
grid stepped over it."""

REFINE_MAX_EVALUATIONS = 70
"""Budget for each Nelder-Mead refinement. Derivative-free on purpose: the objective is a
time-marched simulation with cycle-to-cycle combustion scatter in it, so a finite-
difference gradient would mostly measure noise."""

CONSTRAINT_PENALTY = 1.0e4
"""Scale on normalised constraint violation, added to the objective during refinement so
the simplex is pushed back inside the feasible region rather than wandering out of it."""


# ---- results -----------------------------------------------------------------


@dataclass
class SafetyCheck:
    """One hard limit, evaluated at a candidate setpoint."""

    parameter: str
    label: str
    value: float
    limit: float
    unit: str
    direction: Literal["max", "min"]
    ok: bool
    margin: float
    """Signed distance to the limit in the parameter's own units. Positive is margin in
    hand; negative is the size of the breach."""

    def to_dict(self) -> dict:
        return {
            "parameter": self.parameter,
            "label": self.label,
            "value": round(self.value, 1),
            "limit": round(self.limit, 1),
            "unit": self.unit,
            "direction": self.direction,
            "ok": self.ok,
            "margin": round(self.margin, 1),
        }


@dataclass
class StressBreakdown:
    thermal_head: float
    thermal_oil: float
    vibration: float
    friction: float
    lubrication: float
    total: float

    def to_dict(self) -> dict:
        return {
            "thermal_head": round(self.thermal_head, 3),
            "thermal_oil": round(self.thermal_oil, 3),
            "vibration": round(self.vibration, 3),
            "friction": round(self.friction, 3),
            "lubrication": round(self.lubrication, 3),
            "total": round(self.total, 3),
        }


@dataclass
class SetpointEvaluation:
    """A setpoint, where the engine settles at it, and whether that is allowed."""

    throttle_pct: float
    afr_trim: float
    injection_timing_trim_deg: float

    power_kw: float
    bsfc_g_per_kwh: float | None
    fuel_flow_lph: float
    cht_c: float
    egt_max_c: float
    oil_temp_c: float
    oil_pressure_kpa: float
    rpm: float
    afr_mean: float
    injection_timing_deg: float
    vibration_rms_g: float

    stress_rate: float
    stress_breakdown: StressBreakdown
    estimated_life_hours: float

    safety: list[SafetyCheck]
    feasible: bool

    def to_dict(self) -> dict:
        return {
            "setpoint": {
                "throttle_pct": round(self.throttle_pct, 1),
                "afr_trim": round(self.afr_trim, 2),
                "injection_timing_trim_deg": round(self.injection_timing_trim_deg, 2),
            },
            "predicted": {
                "power_kw": round(self.power_kw, 2),
                "bsfc_g_per_kwh": (
                    round(self.bsfc_g_per_kwh, 1)
                    if self.bsfc_g_per_kwh is not None
                    else None
                ),
                "fuel_flow_lph": round(self.fuel_flow_lph, 2),
                "cht_c": round(self.cht_c, 1),
                "egt_max_c": round(self.egt_max_c, 1),
                "oil_temp_c": round(self.oil_temp_c, 1),
                "oil_pressure_kpa": round(self.oil_pressure_kpa, 1),
                "rpm": round(self.rpm, 0),
                "afr_mean": round(self.afr_mean, 2),
                "injection_timing_deg": round(self.injection_timing_deg, 2),
                "vibration_rms_g": round(self.vibration_rms_g, 4),
            },
            "stress_rate": round(self.stress_rate, 3),
            "stress_breakdown": self.stress_breakdown.to_dict(),
            "estimated_life_hours": round(self.estimated_life_hours, 0),
            "safety": [c.to_dict() for c in self.safety],
            "feasible": self.feasible,
        }


@dataclass
class OperatingPointComparison:
    """Recommended versus baseline, as percentage deltas. This is the headline the Test
    Bench renders, so every field is a plain number with a fixed sign convention:
    **positive is better for the thing it names.**"""

    power_pct: float
    """Change in brake power. Positive = more power."""
    bsfc_pct: float
    """Change in specific fuel consumption. Positive = burning *less* fuel per kWh."""
    range_equivalent_pct: float
    """Change in specific range at constant power, which is the inverse of BSFC. Positive
    = further on the same tank."""
    stress_rate_pct: float
    """Change in combined stress rate. Positive = wearing out more slowly."""
    estimated_rul_impact_pct: float
    """Change in projected hours to overhaul if this setpoint were sustained. Positive =
    more hours."""

    life_hours_baseline: float
    life_hours_recommended: float
    cht_delta_c: float
    egt_delta_c: float
    oil_pressure_delta_kpa: float
    fuel_flow_delta_lph: float

    def to_dict(self) -> dict:
        return {
            "power_pct": round(self.power_pct, 1),
            "bsfc_pct": round(self.bsfc_pct, 1),
            "range_equivalent_pct": round(self.range_equivalent_pct, 1),
            "stress_rate_pct": round(self.stress_rate_pct, 1),
            "estimated_rul_impact_pct": round(self.estimated_rul_impact_pct, 1),
            "life_hours_baseline": round(self.life_hours_baseline, 0),
            "life_hours_recommended": round(self.life_hours_recommended, 0),
            "cht_delta_c": round(self.cht_delta_c, 1),
            "egt_delta_c": round(self.egt_delta_c, 1),
            "oil_pressure_delta_kpa": round(self.oil_pressure_delta_kpa, 1),
            "fuel_flow_delta_lph": round(self.fuel_flow_delta_lph, 2),
        }


@dataclass
class OperatingPointResult:
    objective: str
    objective_label: str
    altitude_m: float
    ambient_temperature_c: float | None
    airspeed_ms: float

    recommended: SetpointEvaluation
    baseline: SetpointEvaluation
    comparison: OperatingPointComparison

    feasible: bool
    health_severity_index: float
    health_state: dict[str, float]
    effective_limits: dict[str, float]
    constraints: dict[str, float]
    safety_notes: list[str]
    rationale: str
    evaluations: int
    compute_seconds: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "objective": self.objective,
            "objective_label": self.objective_label,
            "conditions": {
                "altitude_m": round(self.altitude_m, 0),
                "ambient_temperature_c": (
                    round(self.ambient_temperature_c, 1)
                    if self.ambient_temperature_c is not None
                    else None
                ),
                "airspeed_ms": round(self.airspeed_ms, 1),
            },
            "feasible": self.feasible,
            "recommended": self.recommended.to_dict(),
            "baseline": self.baseline.to_dict(),
            "comparison": self.comparison.to_dict(),
            "health": {
                "severity_index": round(self.health_severity_index, 3),
                "faults": {k: round(v, 3) for k, v in self.health_state.items()},
                "effective_limits": {
                    k: round(v, 1) for k, v in self.effective_limits.items()
                },
            },
            "constraints": {k: round(v, 3) for k, v in self.constraints.items()},
            "safety_notes": self.safety_notes,
            "rationale": self.rationale,
            "evaluations": self.evaluations,
            "compute_seconds": round(self.compute_seconds, 3),
            "notes": self.notes,
        }


# ---- health -------------------------------------------------------------------


def health_severity_index(health_state: dict[str, float] | None) -> float:
    """Collapse a fault-severity map into one 0-1 "how worn is this engine" scalar.

    Compounding rather than max: two faults at 0.5 leave less margin than one at 0.5, and
    the derated envelope should reflect that. `1 - prod(1 - s)` is the standard
    independent-degradation combination and reduces to `s` for a single fault.
    """
    if not health_state:
        return 0.0
    survival = 1.0
    for severity in health_state.values():
        survival *= 1.0 - max(0.0, min(1.0, float(severity)))
    return max(0.0, min(1.0, 1.0 - survival))


def _fault_state_from(health_state: dict[str, float] | None) -> FaultState:
    faults = FaultState()
    for fault_type, severity in (health_state or {}).items():
        if fault_type not in FAULT_TYPES:
            raise ValueError(
                f"unknown fault type '{fault_type}' in current_health_state; "
                f"known types: {', '.join(FAULT_TYPES)}"
            )
        faults.inject(
            fault_type,
            float(severity),
            ramp_seconds=0.0,
            now=0.0,
            n_cylinders=PARAMS.n_cylinders,
        )
    faults.step(0.0)
    return faults


def effective_limits(
    severity_index: float, p: EngineParams = PARAMS
) -> dict[str, float]:
    """The operating envelope, pulled in to account for wear already on the engine."""
    return {
        "cht_limit_c": p.cht_limit_c - p.health_cht_derate_k * severity_index,
        "egt_limit_c": p.egt_limit_c - p.health_egt_derate_k * severity_index,
        "oil_temp_limit_c": p.oil_temp_limit_c
        - p.health_oil_temp_derate_k * severity_index,
        "oil_pressure_min_kpa": p.oil_pressure_min_operating_kpa
        + p.health_oil_pressure_margin_kpa * severity_index,
    }


# ---- stress -------------------------------------------------------------------


def stress_rate(state: SteadyState, p: EngineParams = PARAMS) -> StressBreakdown:
    """Dimensionless life-consumption rate, normalised to 1.0 at nominal cruise.

    Five contributions, each expressed relative to what nominal cruise does:

    * **Head and oil temperature** use an Arrhenius-style doubling law. Oxidation and
      creep-driven damage go roughly exponential with temperature, which is why a
      twenty-degree CHT reduction is worth far more than a twenty-degree one sounds.
    * **Vibration** enters squared: fatigue damage scales with stress *amplitude* to a
      power well above one, and squaring is the conservative first-order stand-in.
    * **Friction power** is linear — it is a direct measure of rubbing work being done in
      the bearings and on the bores.
    * **Lubrication** is the inverse-square of oil pressure: the hydrodynamic film
      thickness that keeps metal apart falls with supply pressure, and once it is gone the
      wear rate is not gentle about it.
    """
    thermal_head = 2.0 ** (
        (state.cht_c - p.cht_stress_reference_c) / max(1e-6, p.cht_stress_doubling_k)
    )
    thermal_oil = 2.0 ** (
        (state.oil_temp_c - p.oil_temp_stress_reference_c)
        / max(1e-6, p.oil_temp_stress_doubling_k)
    )
    vibration = (
        state.vibration_rms_mean / max(1e-9, p.vibration_stress_reference_g)
    ) ** 2
    friction = state.friction_power_w / max(1e-9, p.friction_stress_reference_w)
    lubrication = (
        p.oil_pressure_stress_reference_kpa / max(1.0, state.oil_pressure_kpa)
    ) ** 2

    total = (
        p.stress_weight_cht * thermal_head
        + p.stress_weight_oil_temp * thermal_oil
        + p.stress_weight_vibration * vibration
        + p.stress_weight_friction * friction
        + p.stress_weight_lubrication * lubrication
    )
    return StressBreakdown(
        thermal_head=thermal_head,
        thermal_oil=thermal_oil,
        vibration=vibration,
        friction=friction,
        lubrication=lubrication,
        total=max(1e-6, total),
    )


# ---- airspeed ------------------------------------------------------------------


def optimizer_airspeed_ms(throttle: float) -> float:
    """Same throttle-to-airspeed schedule the Test Bench scenarios use, so an optimizer
    recommendation and a scenario run at the same setpoint agree with each other."""
    from app.sim.scenario_engine import scenario_airspeed_ms

    return scenario_airspeed_ms(throttle)


# ---- the optimizer -------------------------------------------------------------


class _Evaluator:
    """Steady-state evaluation of a candidate setpoint, on one reusable plant.

    The plant is reset for every evaluation, so results are deterministic and independent
    of the order the search visits points in — which matters, because a search that gives
    different answers depending on its own history is not reproducible.
    """

    def __init__(
        self,
        altitude_m: float,
        ambient_temperature_c: float | None,
        faults: FaultState,
        limits: dict[str, float],
        p: EngineParams,
    ) -> None:
        self.altitude_m = altitude_m
        self.ambient_temperature_c = ambient_temperature_c
        self.faults = faults
        self.limits = limits
        self.p = p
        #: `sensor_noise=False`: an optimizer should reason about the engine, not about
        #: what the instruments happened to report this millisecond.
        self.plant = EnginePlant(p, seed=7, sensor_noise=False)
        self.count = 0
        self._cache: dict[tuple[int, int, int], SteadyState] = {}

    def steady(
        self, throttle: float, afr_trim: float, timing_trim: float
    ) -> SteadyState:
        key = (round(throttle, 4), round(afr_trim, 3), round(timing_trim, 3))
        cached = self._cache.get(key)  # type: ignore[arg-type]
        if cached is not None:
            return cached
        # Each evaluation is ~20 ms of uninterrupted Python; hand the GIL over first so the
        # live telemetry broadcast is never locked out for longer than one of them.
        yield_to_event_loop()
        self.plant.engine.set_trims(
            afr_trim=afr_trim, injection_timing_trim_deg=timing_trim
        )
        state = relax_to_steady_state(
            self.plant,
            throttle,
            self.altitude_m,
            optimizer_airspeed_ms(throttle),
            self.faults,
            ambient_temperature_c=self.ambient_temperature_c,
            params=self.p,
        )
        self.count += 1
        self._cache[key] = state  # type: ignore[index]
        return state

    def safety_checks(self, state: SteadyState) -> list[SafetyCheck]:
        limits = self.limits
        return [
            SafetyCheck(
                "cht_c", "Cylinder head temp", state.cht_c, limits["cht_limit_c"],
                "degC", "max", state.cht_c <= limits["cht_limit_c"],
                limits["cht_limit_c"] - state.cht_c,
            ),
            SafetyCheck(
                "egt_max_c", "Exhaust gas temp", state.egt_max_c, limits["egt_limit_c"],
                "degC", "max", state.egt_max_c <= limits["egt_limit_c"],
                limits["egt_limit_c"] - state.egt_max_c,
            ),
            SafetyCheck(
                "oil_temp_c", "Oil temperature", state.oil_temp_c,
                limits["oil_temp_limit_c"], "degC", "max",
                state.oil_temp_c <= limits["oil_temp_limit_c"],
                limits["oil_temp_limit_c"] - state.oil_temp_c,
            ),
            SafetyCheck(
                "oil_pressure_kpa", "Oil pressure", state.oil_pressure_kpa,
                limits["oil_pressure_min_kpa"], "kPa", "min",
                state.oil_pressure_kpa >= limits["oil_pressure_min_kpa"],
                state.oil_pressure_kpa - limits["oil_pressure_min_kpa"],
            ),
        ]

    def violation(self, state: SteadyState) -> float:
        """Total normalised constraint violation. Zero means every limit is respected.

        Normalising each breach by its own limit keeps a 5 kPa oil-pressure shortfall from
        being drowned out by a 5 degC temperature overshoot.
        """
        total = 0.0
        for check in self.safety_checks(state):
            if not check.ok:
                total += abs(check.margin) / max(1.0, abs(check.limit))
        return total

    def evaluate(
        self, throttle: float, afr_trim: float, timing_trim: float
    ) -> SetpointEvaluation:
        state = self.steady(throttle, afr_trim, timing_trim)
        checks = self.safety_checks(state)
        breakdown = stress_rate(state, self.p)
        return SetpointEvaluation(
            throttle_pct=throttle * 100.0,
            afr_trim=afr_trim,
            injection_timing_trim_deg=timing_trim,
            power_kw=state.power_brake_kw,
            bsfc_g_per_kwh=state.bsfc_g_per_kwh,
            fuel_flow_lph=state.fuel_flow_lph,
            cht_c=state.cht_c,
            egt_max_c=state.egt_max_c,
            oil_temp_c=state.oil_temp_c,
            oil_pressure_kpa=state.oil_pressure_kpa,
            rpm=state.rpm,
            afr_mean=state.afr_mean,
            injection_timing_deg=state.injection_timing_deg,
            vibration_rms_g=state.vibration_rms_mean,
            stress_rate=breakdown.total,
            stress_breakdown=breakdown,
            estimated_life_hours=self.p.tbo_hours_nominal / breakdown.total,
            safety=checks,
            feasible=all(c.ok for c in checks),
        )


def _objective_value(
    objective: str,
    state: SteadyState,
    p: EngineParams,
    baseline: SteadyState,
    baseline_stress: float,
) -> float:
    """The quantity being minimised, before any constraint penalty."""
    if objective == "max_power":
        return -state.power_brake_kw

    if objective == "max_engine_life":
        return stress_rate(state, p).total

    bsfc = state.bsfc_g_per_kwh
    if bsfc is None:
        # Below the power floor BSFC is undefined; make the point unattractive rather
        # than letting `None` propagate into the search.
        return 1.0e6

    if objective == "max_range":
        return bsfc

    # balanced — every term normalised against the baseline, all "lower is better".
    #
    # The power term is a *shortfall*, floored at 1.0, not a ratio. A cruise setting has a
    # power requirement, not a power appetite: falling short of the book setting is a real
    # cost, but exceeding it buys nothing the mission asked for. Left as an open-ended
    # ratio, "balanced" simply reads more power as more good and collapses into
    # "max_power" — full throttle, rich and retarded — which is not a balanced answer to
    # anything.
    baseline_bsfc = baseline.bsfc_g_per_kwh or bsfc
    power_term = max(
        1.0, baseline.power_brake_kw / max(1e-6, state.power_brake_kw)
    )
    return (
        p.balanced_weight_range * (bsfc / max(1e-6, baseline_bsfc))
        + p.balanced_weight_power * power_term
        + p.balanced_weight_life * (stress_rate(state, p).total / max(1e-6, baseline_stress))
    )


def optimize_operating_point(
    altitude_m: float,
    ambient_temperature_c: float | None,
    objective: str = "balanced",
    current_health_state: dict[str, float] | None = None,
    params: EngineParams = PARAMS,
) -> OperatingPointResult:
    """Find the best setpoint for `objective` at these conditions and engine condition.

    Raises `ValueError` for an unknown objective, an unknown fault type, or conditions
    outside the modelled envelope.
    """
    started = time.perf_counter()
    p = params

    if objective not in OBJECTIVES:
        raise ValueError(
            f"unknown objective '{objective}'; expected one of {', '.join(OBJECTIVES)}"
        )
    _validate_conditions(altitude_m, ambient_temperature_c, p)

    health_state = {
        k: max(0.0, min(1.0, float(v)))
        for k, v in (current_health_state or {}).items()
        if float(v) > p.health_severity_floor
    }
    severity = health_severity_index(health_state)
    faults = _fault_state_from(health_state)
    limits = effective_limits(severity, p)

    ev = _Evaluator(altitude_m, ambient_temperature_c, faults, limits, p)

    # ---- baseline: the book cruise setting, on the engine we actually have -----
    baseline_state = ev.steady(p.nominal_cruise_throttle, 0.0, 0.0)
    baseline_stress = stress_rate(baseline_state, p).total
    baseline_eval = ev.evaluate(p.nominal_cruise_throttle, 0.0, 0.0)

    # ---- continuous power rating, derated for condition -----------------------
    # The rating is a property of the *hardware*, so it is measured on a healthy engine at
    # full throttle in these conditions and then derated. Measuring it on the worn engine
    # instead would let wear quietly redefine what "rated power" means.
    rating_ev = _Evaluator(
        altitude_m, ambient_temperature_c, FaultState.healthy(), limits, p
    )
    rated_power_kw = rating_ev.steady(p.opt_throttle_max, 0.0, 0.0).power_brake_kw
    power_ceiling_kw = rated_power_kw * max(
        0.0, 1.0 - p.health_power_derate_per_severity * severity
    )

    # ---- power band for the objectives that hold a cruise ----------------------
    # `max_range` gets an upper bound as well as a lower one, and that matters. Brake
    # specific fuel consumption falls as load rises — friction becomes a smaller share of
    # indicated work — so a floor alone lets "minimise BSFC" answer "run full throttle",
    # which is the opposite of a range setting. Range means the *same* work for less fuel,
    # so the search is pinned to the baseline's power and only mixture and timing are
    # genuinely free. The other objectives are explicitly allowed to trade power away, so
    # they keep a floor only.
    power_hold_ceiling_kw: float | None = None
    if objective == "max_range":
        power_floor_kw = baseline_state.power_brake_kw * p.cruise_power_hold_fraction
        power_hold_ceiling_kw = baseline_state.power_brake_kw / max(
            1e-6, p.cruise_power_hold_fraction
        )
    elif objective == "max_engine_life":
        power_floor_kw = baseline_state.power_brake_kw * p.life_min_power_fraction
    elif objective == "balanced":
        power_floor_kw = baseline_state.power_brake_kw * p.balanced_min_power_fraction
    else:
        power_floor_kw = 0.0

    if power_hold_ceiling_kw is not None:
        power_ceiling_kw = min(power_ceiling_kw, power_hold_ceiling_kw)

    safety_notes: list[str] = []
    if power_floor_kw > power_ceiling_kw:
        safety_notes.append(
            f"Holding {power_floor_kw:.1f} kW would exceed this engine's derated "
            f"continuous rating of {power_ceiling_kw:.1f} kW at "
            f"{severity * 100:.0f}% accumulated wear. The power floor has been lowered to "
            "the rating — plan a lower cruise power, or a shorter leg."
        )
        power_floor_kw = power_ceiling_kw

    constraints = {
        "power_floor_kw": power_floor_kw,
        "power_ceiling_kw": power_ceiling_kw,
        "rated_power_kw": rated_power_kw,
        "derated_power_ceiling_kw": rated_power_kw
        * max(0.0, 1.0 - p.health_power_derate_per_severity * severity),
        "baseline_power_kw": baseline_state.power_brake_kw,
    }

    def penalised(x: tuple[float, float, float]) -> float:
        throttle, afr_trim, timing_trim = _clip_to_bounds(x, p)
        state = ev.steady(throttle, afr_trim, timing_trim)
        value = _objective_value(objective, state, p, baseline_state, baseline_stress)
        violation = ev.violation(state)
        if state.power_brake_kw < power_floor_kw:
            violation += (power_floor_kw - state.power_brake_kw) / max(
                1e-6, power_floor_kw
            )
        if state.power_brake_kw > power_ceiling_kw:
            violation += (state.power_brake_kw - power_ceiling_kw) / max(
                1e-6, power_ceiling_kw
            )
        return value + CONSTRAINT_PENALTY * violation

    def is_feasible(x: tuple[float, float, float]) -> bool:
        throttle, afr_trim, timing_trim = _clip_to_bounds(x, p)
        state = ev.steady(throttle, afr_trim, timing_trim)
        return (
            ev.violation(state) <= 0.0
            and power_floor_kw - 1e-6 <= state.power_brake_kw <= power_ceiling_kw + 1e-6
        )

    def raw_objective(x: tuple[float, float, float]) -> float:
        return _objective_value(
            objective, ev.steady(*_clip_to_bounds(x, p)), p, baseline_state,
            baseline_stress,
        )

    # ---- stage 1: bounded coarse grid ----------------------------------------
    baseline_x = (p.nominal_cruise_throttle, 0.0, 0.0)
    throttles = _axis(p.opt_throttle_min, p.opt_throttle_max, GRID_THROTTLE,
                      include=p.nominal_cruise_throttle)
    afr_trims = _axis(p.afr_trim_min, p.afr_trim_max, GRID_AFR, include=0.0)
    timing_trims = _axis(p.injection_timing_trim_min_deg,
                         p.injection_timing_trim_max_deg, GRID_TIMING, include=0.0)

    best_x = baseline_x
    best_score = penalised(baseline_x)
    best_feasible_x: tuple[float, float, float] | None = (
        baseline_x if is_feasible(baseline_x) else None
    )
    best_feasible_score = (
        raw_objective(baseline_x) if best_feasible_x is not None else math.inf
    )

    for throttle in throttles:
        for afr_trim in afr_trims:
            for timing_trim in timing_trims:
                x = (throttle, afr_trim, timing_trim)
                score = penalised(x)
                if score < best_score:
                    best_score, best_x = score, x
                if is_feasible(x):
                    raw = raw_objective(x)
                    if raw < best_feasible_score:
                        best_feasible_score, best_feasible_x = raw, x

    # ---- stage 2: bounded local refinement, from every promising start -------
    # Multi-start rather than a single descent. The power floor puts a cliff in the
    # penalised surface exactly where several objectives want to sit, and a simplex that
    # starts on the wrong side of it will crawl along the cliff instead of crossing it.
    starts: list[tuple[float, float, float]] = [best_x]
    if best_feasible_x is not None and best_feasible_x not in starts:
        starts.append(best_feasible_x)
    if baseline_x not in starts:
        starts.append(baseline_x)

    candidates = list(starts)
    for start in starts:
        candidates.append(_refine(penalised, start, p))

    # ---- stage 3: the returned point must be one we verified ------------------
    # A setpoint is never returned as a recommendation unless the model says it is safe,
    # so feasible candidates are ranked on the true objective and infeasible ones are only
    # considered when nothing feasible exists at all.
    feasible_candidates = [x for x in candidates if is_feasible(x)]
    if feasible_candidates:
        chosen = min(feasible_candidates, key=raw_objective)
        feasible = True
    else:
        chosen = min(candidates, key=penalised)
        feasible = False

    recommended = ev.evaluate(*_clip_to_bounds(chosen, p))

    # The book setting is a legitimate answer. Saying so is more useful than presenting a
    # 0.2% shuffle as a recommendation.
    if feasible and _same_setpoint(chosen, baseline_x):
        safety_notes.append(
            "The book cruise setting is already optimal for this objective at these "
            "conditions — no change recommended."
        )

    if not feasible:
        failing = [c for c in recommended.safety if not c.ok]
        for check in failing:
            over = "above" if check.direction == "max" else "below"
            safety_notes.append(
                f"No setpoint in the search space keeps {check.label.lower()} inside its "
                f"{check.limit:.0f} {check.unit} limit — the closest point still sits "
                f"{abs(check.margin):.0f} {check.unit} {over} it."
            )
        if recommended.power_kw < power_floor_kw - 1e-6:
            safety_notes.append(
                f"This engine cannot hold the {power_floor_kw:.1f} kW the objective "
                f"requires at these conditions; the best available is "
                f"{recommended.power_kw:.1f} kW."
            )
        safety_notes.append(
            "This is the least-unsafe point found, NOT a recommendation. The engine "
            "should not be dispatched at these conditions in this condition."
        )

    if not baseline_eval.feasible:
        breached = ", ".join(
            f"{c.label.lower()} {c.value:.0f} {c.unit} against a {c.limit:.0f} "
            f"{c.unit} limit"
            for c in baseline_eval.safety
            if not c.ok
        )
        safety_notes.insert(
            0,
            "The book cruise setting is itself outside limits at these conditions "
            f"({breached}), so every percentage below is measured against a setting that "
            "could not legally be held here.",
        )

    comparison = _compare(baseline_eval, recommended)
    result = OperatingPointResult(
        objective=objective,
        objective_label=OBJECTIVE_LABELS[objective],
        altitude_m=altitude_m,
        ambient_temperature_c=ambient_temperature_c,
        airspeed_ms=optimizer_airspeed_ms(recommended.throttle_pct / 100.0),
        recommended=recommended,
        baseline=baseline_eval,
        comparison=comparison,
        feasible=feasible,
        health_severity_index=severity,
        health_state=health_state,
        effective_limits=limits,
        constraints=constraints,
        safety_notes=safety_notes,
        rationale=_rationale(objective, baseline_eval, recommended, comparison, severity),
        evaluations=ev.count + rating_ev.count,
        compute_seconds=time.perf_counter() - started,
        notes=[
            "Steady-state prediction: where the engine settles if this setpoint is held, "
            "not what it does during the transition to it.",
            "Life-hours figures come from a physically-motivated stress index normalised "
            "to nominal cruise, not from run-to-failure data.",
        ],
    )
    logger.info(
        "Optimised %s at %.0f m / %s: throttle %.0f%%, AFR trim %+.2f, timing %+.1f deg "
        "(%d evaluations, %.2f s, feasible=%s)",
        objective,
        altitude_m,
        f"{ambient_temperature_c:.0f} degC" if ambient_temperature_c is not None else "ISA",
        recommended.throttle_pct,
        recommended.afr_trim,
        recommended.injection_timing_trim_deg,
        result.evaluations,
        result.compute_seconds,
        feasible,
    )
    return result


# ---- helpers -------------------------------------------------------------------


def _validate_conditions(
    altitude_m: float, ambient_temperature_c: float | None, p: EngineParams
) -> None:
    if not (p.scenario_altitude_min_m <= altitude_m <= p.scenario_altitude_max_m):
        raise ValueError(
            f"altitude_m={altitude_m:.0f} is outside the modelled range "
            f"{p.scenario_altitude_min_m:.0f}-{p.scenario_altitude_max_m:.0f} m"
        )
    if ambient_temperature_c is None:
        return
    if not (p.scenario_ambient_min_c <= ambient_temperature_c <= p.scenario_ambient_max_c):
        raise ValueError(
            f"ambient_temperature_c={ambient_temperature_c:.1f} is outside the modelled "
            f"range {p.scenario_ambient_min_c:.0f} to {p.scenario_ambient_max_c:.0f} degC"
        )
    deviation = isa_deviation_k(altitude_m, ambient_temperature_c)
    if not (
        p.scenario_isa_deviation_min_k <= deviation <= p.scenario_isa_deviation_max_k
    ):
        raise ValueError(
            f"ambient_temperature_c={ambient_temperature_c:.1f} is ISA{deviation:+.0f} K "
            f"at {altitude_m:.0f} m; the cooling and density corrections are only "
            f"modelled for ISA{p.scenario_isa_deviation_min_k:+.0f} K to "
            f"ISA{p.scenario_isa_deviation_max_k:+.0f} K"
        )


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n <= 1:
        return [lo]
    step = (hi - lo) / (n - 1)
    return [lo + step * i for i in range(n)]


def _axis(lo: float, hi: float, n: int, include: float) -> list[float]:
    """An evenly spaced axis that is guaranteed to contain `include`."""
    values = _linspace(lo, hi, n)
    if not any(abs(v - include) < 1e-9 for v in values):
        values.append(include)
    return sorted(values)


def _same_setpoint(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> bool:
    return (
        abs(a[0] - b[0]) < 5e-3 and abs(a[1] - b[1]) < 5e-2 and abs(a[2] - b[2]) < 5e-2
    )


def _clip_to_bounds(
    x: "tuple[float, float, float] | list[float]", p: EngineParams
) -> tuple[float, float, float]:
    """Every evaluation goes through this, so no search step can escape the ECU's
    authority even transiently."""
    return (
        max(p.opt_throttle_min, min(p.opt_throttle_max, float(x[0]))),
        max(p.afr_trim_min, min(p.afr_trim_max, float(x[1]))),
        max(
            p.injection_timing_trim_min_deg,
            min(p.injection_timing_trim_max_deg, float(x[2])),
        ),
    )


def _refine(
    fun, start: tuple[float, float, float], p: EngineParams
) -> tuple[float, float, float]:
    """Bounded Nelder-Mead around the grid's best point.

    Derivative-free by necessity: the objective is a time-marched simulation carrying
    deliberate cycle-to-cycle combustion scatter, so a finite-difference gradient would
    largely measure that noise. If SciPy is unavailable the grid result stands on its own —
    the search degrades in precision, not in safety, because the feasibility check that
    guards the returned setpoint is independent of how the point was found.
    """
    try:
        from scipy.optimize import minimize
    except ImportError:  # pragma: no cover - SciPy ships with scikit-learn
        logger.warning("SciPy not available — returning the coarse-grid optimum.")
        return start

    bounds = [
        (p.opt_throttle_min, p.opt_throttle_max),
        (p.afr_trim_min, p.afr_trim_max),
        (p.injection_timing_trim_min_deg, p.injection_timing_trim_max_deg),
    ]
    try:
        outcome = minimize(
            lambda x: fun(tuple(x)),
            x0=list(start),
            method="Nelder-Mead",
            bounds=bounds,
            options={
                "maxfev": REFINE_MAX_EVALUATIONS,
                "xatol": 1e-3,
                "fatol": 1e-4,
                "adaptive": True,
            },
        )
    except Exception:
        logger.exception("Local refinement failed — falling back to the grid optimum.")
        return start
    return _clip_to_bounds(outcome.x, p)


def _pct_change(new: float, old: float) -> float:
    if abs(old) < 1e-9:
        return 0.0
    return 100.0 * (new - old) / old


def _compare(
    baseline: SetpointEvaluation, recommended: SetpointEvaluation
) -> OperatingPointComparison:
    base_bsfc = baseline.bsfc_g_per_kwh
    rec_bsfc = recommended.bsfc_g_per_kwh
    if base_bsfc and rec_bsfc:
        # Sign convention: positive means "better", so a *fall* in BSFC is a positive
        # number. Specific range at constant power is the inverse of BSFC.
        bsfc_pct = -_pct_change(rec_bsfc, base_bsfc)
        range_pct = _pct_change(base_bsfc / rec_bsfc, 1.0)
    else:
        bsfc_pct = range_pct = 0.0

    return OperatingPointComparison(
        power_pct=_pct_change(recommended.power_kw, baseline.power_kw),
        bsfc_pct=bsfc_pct,
        range_equivalent_pct=range_pct,
        stress_rate_pct=-_pct_change(recommended.stress_rate, baseline.stress_rate),
        estimated_rul_impact_pct=_pct_change(
            recommended.estimated_life_hours, baseline.estimated_life_hours
        ),
        life_hours_baseline=baseline.estimated_life_hours,
        life_hours_recommended=recommended.estimated_life_hours,
        cht_delta_c=recommended.cht_c - baseline.cht_c,
        egt_delta_c=recommended.egt_max_c - baseline.egt_max_c,
        oil_pressure_delta_kpa=recommended.oil_pressure_kpa - baseline.oil_pressure_kpa,
        fuel_flow_delta_lph=recommended.fuel_flow_lph - baseline.fuel_flow_lph,
    )


def _rationale(
    objective: str,
    baseline: SetpointEvaluation,
    recommended: SetpointEvaluation,
    comparison: OperatingPointComparison,
    severity: float,
) -> str:
    """One plain-language sentence a planner can act on or argue with."""
    throttle_move = recommended.throttle_pct - baseline.throttle_pct
    mixture = (
        "leaner"
        if recommended.afr_trim > 0.05
        else "richer"
        if recommended.afr_trim < -0.05
        else "at the scheduled mixture"
    )
    timing = (
        "advanced"
        if recommended.injection_timing_trim_deg > 0.05
        else "retarded"
        if recommended.injection_timing_trim_deg < -0.05
        else "at nominal timing"
    )
    wear = (
        f" Accounting for {severity * 100:.0f}% accumulated wear, which pulls the "
        "operating limits in."
        if severity > 0.0
        else ""
    )
    binding = min(recommended.safety, key=lambda c: c.margin / max(1.0, abs(c.limit)))
    return (
        f"{OBJECTIVE_LABELS[objective]}: run {recommended.throttle_pct:.0f}% throttle "
        f"({throttle_move:+.0f} pts vs book), {mixture}, {timing}. "
        f"Power {comparison.power_pct:+.1f}%, specific range "
        f"{comparison.range_equivalent_pct:+.1f}%, projected life "
        f"{comparison.estimated_rul_impact_pct:+.1f}%. "
        f"Closest limit is {binding.label.lower()} at {binding.value:.0f} "
        f"{binding.unit} against {binding.limit:.0f}.{wear}"
    )
