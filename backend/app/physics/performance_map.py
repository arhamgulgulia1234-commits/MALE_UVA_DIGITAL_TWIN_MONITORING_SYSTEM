"""Steady-state engine performance maps — the dyno view of the same physics.

A performance map answers a different question from anything else in this codebase. The
live loop and the Test Bench scenario engine both ask "given a throttle, where does the
engine *end up*?" — crankshaft speed is a state, settled by the propeller's omega-squared
load curve, so only one RPM is reachable per throttle setting. A performance map asks the
dynamometer's question instead: "if a brake *held* the crank at this speed, and the
throttle were at this opening, what would the engine make?" That fills the whole
RPM x load plane, including the operating points the fixed-pitch prop can never reach.

So this module deliberately does **not** integrate `EngineModel.step()`. Doing that would
mean fighting the crank ODE at every grid point — pinning `engine.rpm` back after each
sub-step, paying ~250 sub-steps per point for the turbo and manifold lags to settle, and
inheriting the deliberate cycle-to-cycle IMEP scatter that `step()` injects, which would
put visible noise into a chart whose whole job is to show a smooth surface. Instead it
evaluates the same equations at their steady state, where every first-order lag has
settled onto its own target:

    boost     -> its wastegate-capped target pressure ratio   (dP/dt = 0)
    MAP       -> feed_pressure * throttle_open                (dMAP/dt = 0)
    eta_vol    = EngineModel._eta_vol(rpm, MAP, altitude)
    m_air_dot  = eta_vol * (MAP * V_d * N) / (revs_per_cycle * R * T_intake)
    m_fuel_dot = m_air_dot / AFR(throttle)
    P_ind      = m_fuel_dot * LHV * eta_comb * eta_thermal
    IMEP       = P_ind / (V_d * cycles_per_second)
    T_ind      = IMEP * V_d / (4*pi)
    T_fric     = c0 + c1*omega + c2*omega^2
    P_brake    = (T_ind - T_fric) * omega
    BSFC       = fuel_mass_flow / P_brake

Every one of those is called out of `EngineModel` / `TurboModel` itself rather than
restated here — `_eta_vol`, `_afr_targets` and `_afr_eta_comb_penalty` are reached through
a private-but-deliberate seam so the map cannot drift away from the engine it claims to
describe. Retune a coefficient in `engine_params.py` and the map moves with the engine.

**Why no thermal transients.** This is the standard simplification for a performance map,
and in this plant it is not even an approximation of the power numbers: as
`steady_state.py` sets out at length, cylinder-head and oil temperature are strictly
*downstream* of the thermodynamic core. `EngineModel.step()` takes throttle, altitude,
airspeed, ambient temperature and the fault state — and no temperature from the thermal
model. Nothing in the torque, fuel-flow or breathing path reads CHT or oil temperature, and
friction here is a pure function of crank speed, not of oil viscosity. So ignoring CHT/oil
dynamics costs the map nothing on power, BSFC or volumetric efficiency; it only means the
map does not report what the head and oil would settle at, which is what
`relax_to_steady_state()` is for. The lubrication model likewise only sets gallery
pressure, which is not a term in any of the equations above.

**Altitude.** Maps shift with air density, so altitude is a first-class parameter. It
enters three ways at once: through ambient pressure (the compressor has less to work with,
so boost and therefore MAP fall), through the breathing derate inside `_eta_vol`, and
through charge temperature out of the compressor. `ambient_temperature_c` overrides the ISA
temperature for a hot-and-high day, exactly as it does everywhere else in the physics.

The map is generated for a **healthy** engine (all fault severities zero) at the ECU's
scheduled mixture and timing — it is the reference surface an operating point is judged
*against*, so wear and commanded trims deliberately do not enter it.
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from app.core.compute_budget import yield_to_event_loop
from app.core.engine_params import PARAMS, EngineParams
from app.physics.engine_model import EngineModel
from app.physics.environment import atmosphere
from app.physics.fault_models import FaultState

# ---- grid defaults -----------------------------------------------------------

DEFAULT_RPM_STEPS = 30
DEFAULT_LOAD_STEPS = 30
"""~30 x 30 = 900 points. Fine enough that a contour renderer has smooth iso-lines to
follow, coarse enough that a full map generates in a few milliseconds and an altitude
slider can regenerate on every change without a spinner."""

MAX_GRID_STEPS = 120
"""Ceiling on either axis. 120 x 120 is 14 400 evaluations — still fast, but past this the
JSON payload starts to dominate the response and the extra resolution is invisible."""

BSFC_MIN_POWER_KW = 1.0
"""Below this, BSFC is reported as `None` rather than a number.

Same convention and same threshold as `SteadyState.bsfc_g_per_kwh`: the engine is idling,
the denominator is on its way to zero and the ratio diverges. A huge number there would
draw the eye as a catastrophic efficiency result when the honest answer is that BSFC is
simply not defined at no useful output. Downstream it becomes JSON `null`, and the viewer
draws those cells as unshaded rather than as the worst colour on the scale."""

TURBO_SETTLE_DT_S = 0.25
TURBO_SETTLE_MAX_STEPS = 250
TURBO_SETTLE_TOL_KPA = 1e-6
"""Boost is the one state here with no closed form worth writing out — the target is
wastegate-capped and the model applies its own inlet-pressure floor — so it is relaxed by
iterating `TurboModel.step()` to a fixed point instead of restating its algebra. Against a
0.95 s time constant, 0.25 s per iteration converges in ~60; the cap is slack for a wear
case that lengthens tau."""


# ---- metrics -----------------------------------------------------------------


@dataclass(frozen=True)
class MetricSpec:
    """One selectable surface: which field it reads, and how it should be presented."""

    key: str
    label: str
    unit: str
    #: True when *lower is better* — BSFC is the only one, and the viewer inverts its
    #: colour ramp so that "good" is the same end of the scale on every map.
    lower_is_better: bool
    description: str


METRICS: dict[str, MetricSpec] = {
    "power": MetricSpec(
        key="power_kw",
        label="Brake power",
        unit="kW",
        lower_is_better=False,
        description="Shaft power the engine would deliver held at this speed and load.",
    ),
    "bsfc": MetricSpec(
        key="bsfc_g_per_kwh",
        label="BSFC",
        unit="g/kWh",
        lower_is_better=True,
        description=(
            "Fuel burned per unit of shaft work. Undefined below "
            f"{BSFC_MIN_POWER_KW:.0f} kW, where the engine is idling."
        ),
    ),
    "volumetric_efficiency": MetricSpec(
        key="volumetric_efficiency_pct",
        label="Volumetric efficiency",
        unit="%",
        lower_is_better=False,
        description="How well the engine breathes: trapped charge against swept volume.",
    ),
}

DEFAULT_METRIC = "power"


# ---- results -----------------------------------------------------------------


@dataclass(frozen=True)
class MapPoint:
    """Everything the steady-state evaluation knows at one (RPM, throttle) cell."""

    rpm: float
    throttle: float                      # 0-1
    power_kw: float
    torque_nm: float
    bsfc_g_per_kwh: float | None
    volumetric_efficiency_pct: float
    fuel_flow_lph: float
    manifold_pressure_kpa: float
    boost_pressure_kpa: float
    afr: float
    imep_bar: float


@dataclass
class PerformanceMap:
    """A full RPM x load surface, plus the landmarks worth calling out on it."""

    altitude_m: float
    ambient_temperature_c: float | None
    rpm_axis: list[float]
    throttle_axis_pct: list[float]
    #: metric name -> z[load_index][rpm_index]. Row-major with load as the row, so the
    #: array indexes the way the chart is drawn: y (load) outer, x (RPM) inner.
    grids: dict[str, list[list[float | None]]]
    points: list[list[MapPoint]] = field(default_factory=list, repr=False)

    peak_power: MapPoint | None = None
    best_bsfc: MapPoint | None = None

    def grid(self, metric: str) -> list[list[float | None]]:
        return self.grids[_normalise_metric(metric)]

    def extent(self, metric: str) -> tuple[float | None, float | None]:
        """(min, max) of a surface, ignoring undefined cells."""
        values = [v for row in self.grid(metric) for v in row if v is not None]
        if not values:
            return None, None
        return min(values), max(values)


def _point_summary(p: MapPoint | None) -> dict | None:
    if p is None:
        return None
    return {
        "rpm": round(p.rpm, 0),
        "throttle_pct": round(p.throttle * 100.0, 1),
        "power_kw": round(p.power_kw, 2),
        "torque_nm": round(p.torque_nm, 1),
        "bsfc_g_per_kwh": (
            round(p.bsfc_g_per_kwh, 1) if p.bsfc_g_per_kwh is not None else None
        ),
        "volumetric_efficiency_pct": round(p.volumetric_efficiency_pct, 1),
        "fuel_flow_lph": round(p.fuel_flow_lph, 2),
        "manifold_pressure_kpa": round(p.manifold_pressure_kpa, 1),
        "afr": round(p.afr, 2),
    }


# ---- generation --------------------------------------------------------------


def _normalise_metric(metric: str | None) -> str:
    name = (metric or DEFAULT_METRIC).strip().lower()
    if name not in METRICS:
        raise ValueError(
            f"unknown metric {metric!r}; expected one of {', '.join(METRICS)}"
        )
    return name


def _settle_boost(
    engine: EngineModel,
    rpm: float,
    throttle: float,
    altitude_m: float,
    fault_state: FaultState,
    inlet_pressure_kpa: float,
    ambient_temperature_c: float | None,
):
    """Relax the turbocharger to its steady boost at a held speed and throttle.

    Returns the converged `TurboOutputs`. The engine's own turbo instance is used and left
    at that state; each grid point resets it first, so no point inherits its neighbour's
    spool."""
    engine.turbo.reset(altitude_m)

    def one_step():
        return engine.turbo.step(
            TURBO_SETTLE_DT_S,
            rpm,
            throttle,
            altitude_m,
            fault_state,
            inlet_pressure_kpa=inlet_pressure_kpa,
            ambient_temperature_c=ambient_temperature_c,
        )

    outputs = one_step()
    previous = outputs.boost_pressure_kpa
    for _ in range(TURBO_SETTLE_MAX_STEPS - 1):
        outputs = one_step()
        if abs(outputs.boost_pressure_kpa - previous) < TURBO_SETTLE_TOL_KPA:
            break
        previous = outputs.boost_pressure_kpa
    return outputs


def evaluate_operating_point(
    rpm: float,
    throttle: float,
    altitude_m: float,
    ambient_temperature_c: float | None = None,
    engine: EngineModel | None = None,
    fault_state: FaultState | None = None,
    params: EngineParams = PARAMS,
) -> MapPoint:
    """One dyno point: hold the crank at `rpm`, hold the throttle, report the steady state.

    Exposed on its own because it is the unit the map is built from and the natural thing
    to check a single number against by hand.
    """
    p = params
    engine = engine if engine is not None else EngineModel(p)
    fs = fault_state if fault_state is not None else FaultState()
    throttle = _clamp(throttle, 0.0, 1.0)
    rpm = max(0.0, rpm)

    atm = atmosphere(altitude_m, ambient_temperature_c)

    # ---- 1. induction: filter -> compressor -> throttle -> manifold ----------
    # A healthy map has no filter restriction, but the term is kept so the same code path
    # serves a degraded-engine map later without a second implementation.
    rpm_norm = _clamp(rpm / max(1.0, p.rpm_redline), 0.0, 1.2)
    filter_drop_kpa = (
        p.f_air_filter_intake_drop_kpa * fs.air_filter_clog * rpm_norm
    )
    inlet_pressure_kpa = max(10.0, atm.pressure_kpa - filter_drop_kpa)

    turbo = _settle_boost(
        engine, rpm, throttle, altitude_m, fs, inlet_pressure_kpa, ambient_temperature_c
    )
    feed_pressure_kpa = max(p.map_min_kpa, turbo.boost_pressure_kpa)

    # Steady state of the manifold filling ODE is simply its target: dMAP/dt = 0.
    throttle_open = p.throttle_idle_fraction + (1.0 - p.throttle_idle_fraction) * throttle
    map_kpa = max(p.map_min_kpa, feed_pressure_kpa * throttle_open)

    intake_temp_k = turbo.compressor_outlet_temp_k + (
        p.t_intake_base_k - atmosphere(0.0).temperature_k
    )

    # ---- 2. breathing and flows ---------------------------------------------
    eta_vol = engine._eta_vol(rpm, map_kpa, altitude_m, fs, ambient_temperature_c)
    rev_per_s = rpm / 60.0
    cycles_per_s = rev_per_s / p.revs_per_cycle

    m_air_dot = max(
        0.0,
        eta_vol
        * (map_kpa * 1000.0 * p.displacement_m3 * rev_per_s)
        / (p.revs_per_cycle * p.r_air_j_per_kg_k * intake_temp_k),
    )
    m_air_per_cyl = m_air_dot / p.n_cylinders

    afrs = engine._afr_targets(throttle, fs)
    m_fuel_per_cyl = [m_air_per_cyl / max(6.0, afr) for afr in afrs]
    m_fuel_dot = sum(m_fuel_per_cyl)

    # ---- 3. combustion and indicated work ------------------------------------
    # Healthy, at the ECU's scheduled timing: the only efficiency term that varies across
    # the map is the mixture penalty, which is what makes the rich WOT corner cost fuel.
    eta_comb = [
        p.eta_comb_nominal * engine._afr_eta_comb_penalty(afr) for afr in afrs
    ]
    indicated_power_w = sum(
        m_fuel_per_cyl[i] * p.fuel_lhv_j_per_kg * eta_comb[i] * p.eta_thermal_indicated
        for i in range(p.n_cylinders)
    )

    imep_pa = (
        indicated_power_w / (p.displacement_m3 * cycles_per_s)
        if cycles_per_s > 1e-6
        else 0.0
    )
    torque_indicated = imep_pa * p.displacement_m3 / (4.0 * math.pi)

    # ---- 4. friction, and what is left at the shaft --------------------------
    omega = rpm * 2.0 * math.pi / 60.0
    torque_friction = (
        p.friction_c0_nm + p.friction_c1_nm_s * omega + p.friction_c2_nm_s2 * omega**2
    )
    ring_loss = p.f_ring_wear_torque_loss_nm * fs.piston_ring_wear
    torque_brake = torque_indicated - torque_friction - ring_loss

    # Clamped at zero exactly as `EngineModel.step()` clamps it. Below the friction line
    # the engine is being motored by the dyno rather than producing anything, and the map
    # shows that region as a flat zero floor rather than as negative "power".
    power_brake_kw = max(0.0, torque_brake) * omega / 1000.0

    fuel_flow_lph = m_fuel_dot / p.fuel_density_kg_per_l * 3600.0
    bsfc: float | None = None
    if power_brake_kw >= BSFC_MIN_POWER_KW and m_fuel_dot > 1e-12:
        bsfc = (m_fuel_dot * 1000.0 * 3600.0) / power_brake_kw

    return MapPoint(
        rpm=rpm,
        throttle=throttle,
        power_kw=power_brake_kw,
        torque_nm=max(0.0, torque_brake),
        bsfc_g_per_kwh=bsfc,
        volumetric_efficiency_pct=eta_vol * 100.0,
        fuel_flow_lph=fuel_flow_lph,
        manifold_pressure_kpa=map_kpa,
        boost_pressure_kpa=turbo.boost_pressure_kpa,
        afr=sum(afrs) / len(afrs),
        imep_bar=imep_pa / 1e5,
    )


def generate_performance_map(
    altitude_m: float = 0.0,
    metric: str = DEFAULT_METRIC,
    *,
    ambient_temperature_c: float | None = None,
    rpm_steps: int = DEFAULT_RPM_STEPS,
    load_steps: int = DEFAULT_LOAD_STEPS,
    params: EngineParams = PARAMS,
) -> PerformanceMap:
    """Compute the full steady-state map over RPM x throttle at one altitude.

    `metric` is validated here and carried on the result, but **all three surfaces are
    computed regardless** — they come out of the same evaluation, so producing only the
    selected one would save nothing and would force a second full pass the moment the
    operator changed the selector.
    """
    _normalise_metric(metric)
    p = params
    rpm_steps = max(2, min(MAX_GRID_STEPS, int(rpm_steps)))
    load_steps = max(2, min(MAX_GRID_STEPS, int(load_steps)))

    rpm_axis = [
        p.rpm_idle + (p.rpm_redline - p.rpm_idle) * i / (rpm_steps - 1)
        for i in range(rpm_steps)
    ]
    throttle_axis = [j / (load_steps - 1) for j in range(load_steps)]

    engine = EngineModel(p)
    healthy = FaultState()

    points: list[list[MapPoint]] = []
    grids: dict[str, list[list[float | None]]] = {name: [] for name in METRICS}
    peak_power: MapPoint | None = None
    best_bsfc: MapPoint | None = None

    for throttle in throttle_axis:
        row: list[MapPoint] = []
        for rpm in rpm_axis:
            point = evaluate_operating_point(
                rpm,
                throttle,
                altitude_m,
                ambient_temperature_c=ambient_temperature_c,
                engine=engine,
                fault_state=healthy,
                params=p,
            )
            row.append(point)
            if peak_power is None or point.power_kw > peak_power.power_kw:
                peak_power = point
            if point.bsfc_g_per_kwh is not None and (
                best_bsfc is None
                or best_bsfc.bsfc_g_per_kwh is None
                or point.bsfc_g_per_kwh < best_bsfc.bsfc_g_per_kwh
            ):
                best_bsfc = point
        points.append(row)
        # One row is ~30 evaluations; handing the GIL over between rows bounds how long
        # the live telemetry broadcast can be locked out. See app/core/compute_budget.py.
        yield_to_event_loop()
        for name, spec in METRICS.items():
            grids[name].append(
                [_rounded(getattr(pt, spec.key), name) for pt in row]
            )

    return PerformanceMap(
        altitude_m=altitude_m,
        ambient_temperature_c=ambient_temperature_c,
        rpm_axis=[round(r, 1) for r in rpm_axis],
        throttle_axis_pct=[round(t * 100.0, 2) for t in throttle_axis],
        grids=grids,
        points=points,
        peak_power=peak_power,
        best_bsfc=best_bsfc,
    )


def _rounded(value: float | None, metric: str) -> float | None:
    if value is None:
        return None
    return round(value, 1 if metric == "bsfc" else 3)


#: Memo for `cached_performance_map`, newest-last, bounded.
#:
#: A plain dict rather than `functools.lru_cache` because the endpoint needs to *ask*
#: whether a map is already computed before deciding how to serve it: a hit is returned
#: straight off the event loop in microseconds, while a miss goes to a worker thread and
#: through the shared compute slot. `lru_cache` cannot answer that question about a
#: specific key, and always taking the slot would put a cached altitude-slider request in
#: the queue behind a multi-second optimiser search for no reason.
_MAP_CACHE_SIZE = 32
_map_cache: "OrderedDict[tuple, PerformanceMap]" = OrderedDict()
_map_cache_lock = threading.Lock()


def _cache_key(
    altitude_m: float,
    ambient_temperature_c: float | None,
    rpm_steps: int,
    load_steps: int,
) -> tuple:
    return (
        round(float(altitude_m), 3),
        None if ambient_temperature_c is None else round(float(ambient_temperature_c), 3),
        int(rpm_steps),
        int(load_steps),
    )


def is_map_cached(
    altitude_m: float,
    ambient_temperature_c: float | None = None,
    rpm_steps: int = DEFAULT_RPM_STEPS,
    load_steps: int = DEFAULT_LOAD_STEPS,
) -> bool:
    """Whether these conditions are already computed, so a caller can pick how to serve."""
    key = _cache_key(altitude_m, ambient_temperature_c, rpm_steps, load_steps)
    with _map_cache_lock:
        return key in _map_cache


def cached_performance_map(
    altitude_m: float,
    ambient_temperature_c: float | None = None,
    rpm_steps: int = DEFAULT_RPM_STEPS,
    load_steps: int = DEFAULT_LOAD_STEPS,
) -> PerformanceMap:
    """Memoised `generate_performance_map`, keyed on the conditions and the grid size.

    An altitude slider that steps in fixed increments revisits the same handful of
    altitudes constantly, and the map at a given altitude is a pure function of the engine
    parameters — nothing about the live engine enters it, so a cached surface can never go
    stale underneath a caller. The result is treated as read-only by everything that holds
    it; `to_payload()` builds fresh lists for the response.
    """
    key = _cache_key(altitude_m, ambient_temperature_c, rpm_steps, load_steps)
    with _map_cache_lock:
        hit = _map_cache.get(key)
        if hit is not None:
            _map_cache.move_to_end(key)
            return hit

    # Generated outside the lock: two requests for a cold altitude would otherwise
    # serialise, and computing the same map twice is cheaper than holding a lock across
    # 100 ms of pure-Python numerics.
    fresh = generate_performance_map(
        altitude_m,
        ambient_temperature_c=ambient_temperature_c,
        rpm_steps=rpm_steps,
        load_steps=load_steps,
    )
    with _map_cache_lock:
        _map_cache[key] = fresh
        _map_cache.move_to_end(key)
        while len(_map_cache) > _MAP_CACHE_SIZE:
            _map_cache.popitem(last=False)
    return fresh


def to_payload(pmap: PerformanceMap, metric: str) -> dict:
    """JSON shape for the API: the two axes, the selected surface, and its landmarks."""
    name = _normalise_metric(metric)
    spec = METRICS[name]
    lo, hi = pmap.extent(name)
    p = PARAMS
    return {
        "metric": name,
        "metric_label": spec.label,
        "unit": spec.unit,
        "lower_is_better": spec.lower_is_better,
        "description": spec.description,
        "conditions": {
            "altitude_m": round(pmap.altitude_m, 1),
            "ambient_temperature_c": pmap.ambient_temperature_c,
            "ambient_pressure_kpa": round(
                atmosphere(pmap.altitude_m, pmap.ambient_temperature_c).pressure_kpa, 2
            ),
            "density_ratio": round(
                atmosphere(pmap.altitude_m, pmap.ambient_temperature_c).density_kg_per_m3
                / atmosphere(0.0).density_kg_per_m3,
                4,
            ),
        },
        "rpm_axis": list(pmap.rpm_axis),
        "throttle_axis_pct": list(pmap.throttle_axis_pct),
        # z[load_index][rpm_index] — rows are load, columns are RPM.
        "z": [list(row) for row in pmap.grid(name)],
        "z_min": lo,
        "z_max": hi,
        "landmarks": {
            "peak_power": _point_summary(pmap.peak_power),
            "best_bsfc": _point_summary(pmap.best_bsfc),
        },
        "axes": {
            "rpm": {"min": p.rpm_idle, "max": p.rpm_redline, "label": "Crankshaft speed"},
            "throttle_pct": {"min": 0.0, "max": 100.0, "label": "Throttle / load"},
        },
        "assumptions": [
            "Steady state — every lag settled; no thermal transient.",
            "Healthy engine at the ECU's scheduled mixture and timing.",
            "Crank speed held by the dyno, so the whole RPM x load plane is covered, "
            "not only the fixed-pitch propeller's load line.",
        ],
    }


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
