"""Headless what-if scenario simulation — the Test Bench half of Phase 4.

This drives the *same* physics stack the live dashboard runs on (`EnginePlant`,
`DigitalTwin`, `ResidualMonitor`, `AnomalyDetector`, `RULPredictor`,
`MissionReliabilityModel`, `FaultClassifier`, `EfficiencyAnalyser`,
`MaintenanceAdvisor`) with three differences from `app/sim/simulation_loop.py`:

  * **No wall clock.** The live loop advances `100 ms x time_scale` per real 100 ms tick.
    Here the whole scenario is integrated as fast as the CPU allows, so a two-hour
    what-if answer arrives in seconds instead of two hours.
  * **No WebSocket.** Frames are collected and returned; nothing is broadcast, nothing is
    persisted to `telemetry_frames`, and no live client can tell this ran at all.
  * **Commanded, not flown.** Altitude and ambient temperature are held at the operator's
    chosen values instead of following a mission-phase profile, and throttle comes from a
    scenario profile (constant, or linearly interpolated waypoints).

Because the live loop is untouched, a scenario can be run while a real mission is
recording — the two share nothing but immutable parameter objects.

Integration and sampling
------------------------
The binding stability constraint is the manifold filling time constant (120 ms), so the
integration sub-step must stay well under it; `DT_MAX_S` is 50 ms. Short scenarios use the
same 20 ms sub-step as the live loop, and longer ones widen it toward `DT_MAX_S` under a
fixed sub-step budget so a four-hour request does not turn into a four-minute request.

The PHM chain (twin residuals, anomaly scores, RUL, reliability, advisories) and the
returned time-series both run on the *sample* cadence, which is one per simulated second
for scenarios up to 30 minutes and stretches beyond that to cap the response at
`MAX_SAMPLES` frames. Every filter in the PHM layer expresses its memory in simulated
seconds and converts via `alpha = 1 - exp(-dt/tau)`, so a coarser sample cadence changes
resolution but not behaviour — the same property that lets the live demo run at 20x.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Literal, Sequence

from app.core.compute_budget import yield_to_event_loop
from app.core.engine_params import PARAMS, EngineParams
from app.core.models import (
    ActiveFault,
    CylinderReading,
    HealthState,
    MaintenanceAdvisory,
    MissionReliability,
    RecoveryReliability,
    SubsystemScores,
    TelemetryFrame,
)
from app.ml.anomaly_detector import AnomalyDetector
from app.ml.efficiency_analysis import EfficiencyAnalyser
from app.ml.fault_classifier import FaultClassifier
from app.ml.maintenance_advisor import MaintenanceAdvisor
from app.ml.mission_reliability import MissionReliabilityModel
from app.ml.rul_predictor import RULPredictor
from app.physics.environment import atmosphere, isa_deviation_k
from app.physics.fault_models import FAULT_TYPES, FaultState
from app.physics.plant import EnginePlant
from app.physics.steady_state import relax_to_steady_state
from app.twin.digital_twin import DigitalTwin
from app.twin.residual_analysis import ResidualMonitor

logger = logging.getLogger(__name__)

# ---- integration and sampling policy ----------------------------------------

DT_MIN_S = 0.02
"""Same sub-step as the live loop."""

DT_MAX_S = 0.05
"""Ceiling. The manifold time constant is 120 ms; going past ~60 ms makes the explicit
filling integration overshoot, so this is a stability limit, not a taste limit."""

MAX_SUBSTEPS = 60_000
"""Sub-step budget before the step is widened. At roughly 20 000 sub-steps/s this holds
the physics portion of a run to about three seconds."""

MAX_SAMPLES = 1800
"""Cap on returned frames (and on PHM evaluations). One per simulated second up to 30
minutes; beyond that the cadence stretches so a four-hour scenario still returns 1800
frames rather than 14 400."""

MAX_CLASSIFIER_EVALUATIONS = 120
"""How many times the RandomForest is asked for an opinion over a whole run.

One 300-tree forest prediction costs roughly what fifty simulated seconds of physics
cost, so at the sample cadence a long scenario would spend more time classifying than
simulating. The classifier's verdict — which subsystem, and physical-versus-sensor —
moves on a timescale of tens of seconds, so it is evaluated on a stride and the last
verdict is carried between evaluations. The anomaly detector, RUL predictor, reliability
model and advisor — everything the PASS/CAUTION/FAIL summary is actually built from —
still run on every single sample."""

VIBRATION_WINDOW_MAX_S = 1.0
"""The vibration model's rolling buffer is one second long, so synthesising more than a
second per sample would be thrown away."""


# ---- airspeed schedule -------------------------------------------------------

AIRSPEED_IDLE_MS = 24.0
AIRSPEED_SPAN_MS = 26.0


def scenario_airspeed_ms(throttle: float) -> float:
    """Representative steady-flight airspeed for a given throttle setting.

    A scenario specifies power and weather, not a flight path, but the cooling model needs
    an airspeed (forced convection scales as v^0.55) and the propeller model needs one to
    know how hard the airstream is driving the disc. Rather than invent a separate
    aerodynamic model, this maps throttle onto the airspeed band the mission profiles in
    `app/sim/mission_profiles.py` actually fly: about 24 m/s at idle rising to roughly
    50 m/s at full power, which puts the nominal 0.78 cruise setting at ~44 m/s.

    It is a schedule, not a simulation, and the scenario result says so.
    """
    return AIRSPEED_IDLE_MS + AIRSPEED_SPAN_MS * max(0.0, min(1.0, throttle))


# ---- parameters --------------------------------------------------------------


@dataclass(frozen=True)
class ThrottleWaypoint:
    """One point on a piecewise-linear throttle schedule."""

    time_min: float
    throttle_pct: float


@dataclass(frozen=True)
class ScheduledFault:
    """A fault that appears partway through the scenario rather than at t=0."""

    fault_type: str
    severity: float
    at_time_min: float
    ramp_minutes: float = 1.0


@dataclass(frozen=True)
class ScenarioParams:
    """Everything that defines a what-if run.

    Throttle is expressed in **percent** throughout (0-100), matching the
    `throttle_pct` field on a waypoint, so a scalar and a waypoint list never mean
    different things by the same number.
    """

    altitude_m: float = 2400.0
    ambient_temperature_c: float | None = None
    """None follows the ISA temperature for `altitude_m` — a standard day."""
    duration_minutes: float = 20.0
    throttle_profile: float | Sequence[ThrottleWaypoint] = 78.0
    initial_fault_severities: dict[str, float] = field(default_factory=dict)
    """Wear the engine already carries going into the scenario, applied at t=0 with no
    ramp — "what happens if I fly this mission on the engine I have", as distinct from
    "what happens if this fault develops in flight"."""
    injected_faults_during_scenario: Sequence[ScheduledFault] = ()
    label: str | None = None
    """Optional operator name for the run, carried into the history list."""


# ---- validation --------------------------------------------------------------


def validate_scenario_params(
    params: ScenarioParams, engine: EngineParams = PARAMS
) -> list[str]:
    """Return a list of human-readable reasons the request falls outside modelled
    validity. Empty means the request is inside the envelope the physics was built for.

    The point of refusing rather than clamping: an answer produced by extrapolating the
    ISA column, the breathing curve or the cooling correlation past their range looks
    exactly like a real answer, and a mission planner has no way to tell. Better to say
    which bound was crossed.
    """
    errors: list[str] = []
    p = engine

    if not math.isfinite(params.altitude_m):
        errors.append("altitude_m must be a finite number")
    elif not (p.scenario_altitude_min_m <= params.altitude_m <= p.scenario_altitude_max_m):
        errors.append(
            f"altitude_m={params.altitude_m:.0f} is outside the modelled range "
            f"{p.scenario_altitude_min_m:.0f}-{p.scenario_altitude_max_m:.0f} m. "
            "Above the tropopause the ISA column becomes isothermal and the turbocharger "
            "map in turbo_model.py is no longer being used near its calibrated region."
        )

    if params.ambient_temperature_c is not None:
        t = params.ambient_temperature_c
        if not math.isfinite(t):
            errors.append("ambient_temperature_c must be a finite number or null")
        elif not (p.scenario_ambient_min_c <= t <= p.scenario_ambient_max_c):
            errors.append(
                f"ambient_temperature_c={t:.1f} is outside the modelled range "
                f"{p.scenario_ambient_min_c:.0f} to {p.scenario_ambient_max_c:.0f} degC."
            )
        else:
            # An absolute temperature inside the band can still be an absurd *deviation*
            # from ISA at that altitude — +40 degC at 10 km is not weather.
            deviation = isa_deviation_k(params.altitude_m, t)
            if not (
                p.scenario_isa_deviation_min_k
                <= deviation
                <= p.scenario_isa_deviation_max_k
            ):
                isa_c = atmosphere(params.altitude_m).temperature_c
                errors.append(
                    f"ambient_temperature_c={t:.1f} is ISA{deviation:+.0f} K at "
                    f"{params.altitude_m:.0f} m (ISA there is {isa_c:.1f} degC). The "
                    f"cooling and density corrections are only modelled for ISA"
                    f"{p.scenario_isa_deviation_min_k:+.0f} K to ISA"
                    f"{p.scenario_isa_deviation_max_k:+.0f} K."
                )

    d = params.duration_minutes
    if not math.isfinite(d) or not (
        p.scenario_duration_min_minutes <= d <= p.scenario_duration_max_minutes
    ):
        errors.append(
            f"duration_minutes must be between {p.scenario_duration_min_minutes} and "
            f"{p.scenario_duration_max_minutes}"
        )

    errors.extend(_validate_throttle_profile(params.throttle_profile, d))

    for fault_type, severity in (params.initial_fault_severities or {}).items():
        if fault_type not in FAULT_TYPES:
            errors.append(
                f"unknown fault type '{fault_type}' in initial_fault_severities; "
                f"known types: {', '.join(FAULT_TYPES)}"
            )
        elif not (0.0 <= severity <= 1.0):
            errors.append(
                f"initial severity for '{fault_type}' must be between 0 and 1, got {severity}"
            )

    for i, sched in enumerate(params.injected_faults_during_scenario or ()):
        where = f"injected_faults_during_scenario[{i}]"
        if sched.fault_type not in FAULT_TYPES:
            errors.append(
                f"{where}: unknown fault type '{sched.fault_type}'; "
                f"known types: {', '.join(FAULT_TYPES)}"
            )
        if not (0.0 <= sched.severity <= 1.0):
            errors.append(f"{where}: severity must be between 0 and 1")
        if not (0.0 <= sched.at_time_min <= d):
            errors.append(
                f"{where}: at_time_min={sched.at_time_min} must fall inside the "
                f"{d:.1f} minute scenario"
            )
        if sched.ramp_minutes < 0.0:
            errors.append(f"{where}: ramp_minutes cannot be negative")

    return errors


def _validate_throttle_profile(
    profile: float | Sequence[ThrottleWaypoint], duration_minutes: float
) -> list[str]:
    errors: list[str] = []
    if isinstance(profile, (int, float)):
        if not (0.0 <= float(profile) <= 100.0):
            errors.append("throttle_profile must be a percentage between 0 and 100")
        return errors

    waypoints = list(profile)
    if not waypoints:
        errors.append("throttle_profile waypoint list cannot be empty")
        return errors
    for i, wp in enumerate(waypoints):
        if not (0.0 <= wp.throttle_pct <= 100.0):
            errors.append(
                f"throttle_profile[{i}]: throttle_pct must be between 0 and 100"
            )
        if not (0.0 <= wp.time_min <= duration_minutes):
            errors.append(
                f"throttle_profile[{i}]: time_min={wp.time_min} must fall inside the "
                f"{duration_minutes:.1f} minute scenario"
            )
    return errors


# ---- throttle schedule -------------------------------------------------------


class ThrottleSchedule:
    """Resolves a scenario's throttle profile to a fraction at any simulated time.

    Outside the waypoint span the first/last value is held rather than extrapolated —
    extrapolating a ramp past its last waypoint is how you end up commanding 140%.
    """

    def __init__(self, profile: float | Sequence[ThrottleWaypoint]) -> None:
        if isinstance(profile, (int, float)):
            self._points: list[tuple[float, float]] = [(0.0, float(profile) / 100.0)]
        else:
            points = sorted(
                ((wp.time_min * 60.0, wp.throttle_pct / 100.0) for wp in profile),
                key=lambda p: p[0],
            )
            self._points = points or [(0.0, 0.0)]

    def at(self, t_s: float) -> float:
        points = self._points
        if len(points) == 1 or t_s <= points[0][0]:
            return points[0][1]
        if t_s >= points[-1][0]:
            return points[-1][1]
        for (t0, v0), (t1, v1) in zip(points, points[1:]):
            if t0 <= t_s <= t1:
                span = t1 - t0
                if span <= 1e-9:
                    return v1
                return v0 + (v1 - v0) * (t_s - t0) / span
        return points[-1][1]

    def describe(self) -> list[dict[str, float]]:
        return [
            {"time_min": t / 60.0, "throttle_pct": v * 100.0} for t, v in self._points
        ]


# ---- results -----------------------------------------------------------------

Verdict = Literal["PASS", "CAUTION", "FAIL"]


@dataclass
class LimitExcursion:
    """One operating band the scenario left, and for how long.

    `band="limit"` is a red-line breach and fails the scenario outright. `band="caution"`
    is the edge of the normal envelope — worth telling a planner about, but not on its own
    a reason to call the mission off.
    """

    parameter: str
    band: Literal["limit", "caution"]
    limit: float
    unit: str
    peak_value: float
    first_at_min: float
    duration_min: float
    direction: Literal["above", "below"]


@dataclass
class ReliabilityPoint:
    time_min: float
    score: float
    recommendation: str


@dataclass
class ScenarioSummary:
    verdict: Verdict
    stayed_within_safe_health: bool
    stayed_within_operating_limits: bool
    health_safe_threshold: float

    min_health_score: float
    min_health_at_min: float
    final_health_score: float
    final_rul_minutes: float | None
    min_rul_minutes: float | None

    worst_subsystem: str
    worst_subsystem_score: float
    final_subsystem_scores: dict[str, float]

    final_recommendation: str
    worst_recommendation: str
    mission_reliability_trajectory: list[ReliabilityPoint]

    # ---- Phase 5: recovery reliability ---------------------------------------
    # Deliberately does not feed `verdict` — a scenario can end RTB-AT-RISK-worst and
    # still PASS, or NO-GO-worst and still show RTB-SAFE throughout, because the two
    # answer different questions (see app/ml/mission_reliability.py).
    final_recovery_recommendation: str
    worst_recovery_recommendation: str
    recovery_reliability_trajectory: list[ReliabilityPoint]

    limit_excursions: list[LimitExcursion]
    caution_excursions: list[LimitExcursion]

    peak_cht_c: float
    peak_egt_c: float
    min_oil_pressure_kpa: float
    peak_oil_temp_c: float
    mean_power_kw: float
    mean_bsfc_g_per_kwh: float | None
    total_fuel_litres: float
    fuel_burn_lph_mean: float

    final_advisories: list[dict]
    headline: str


@dataclass
class ScenarioResult:
    params: dict
    frames: list[TelemetryFrame]
    summary: ScenarioSummary
    integration_dt_s: float
    sample_interval_s: float
    simulated_seconds: float
    compute_seconds: float
    notes: list[str]


# ---- the runner --------------------------------------------------------------


class _ScenarioRun:
    """One scenario's mutable state. Mirrors `SimulationLoop`'s composition of the PHM
    chain, but commanded by a scenario rather than by a mission profile and a wall clock.
    """

    def __init__(self, params: ScenarioParams, engine_params: EngineParams) -> None:
        self.params = params
        self.p = engine_params

        self.plant = EnginePlant(engine_params, seed=7)
        self.twin = DigitalTwin(engine_params, seed=101)
        self.faults = FaultState()

        self.residuals = ResidualMonitor()
        self.anomaly = AnomalyDetector()
        self.rul = RULPredictor()
        self.reliability = MissionReliabilityModel()
        self.classifier = FaultClassifier()
        self.classifier.set_feature_names(self.residuals.feature_names())
        self.efficiency = EfficiencyAnalyser(
            fuel_density_kg_per_l=engine_params.fuel_density_kg_per_l
        )
        self.advisor = MaintenanceAdvisor()

        self.throttle = ThrottleSchedule(params.throttle_profile)

        # Pre-existing wear is applied instantly (zero ramp) so the engine starts the
        # scenario already carrying it, rather than developing it in the first minute.
        for fault_type, severity in (params.initial_fault_severities or {}).items():
            if severity > 1e-4:
                self.faults.inject(
                    fault_type,
                    severity,
                    ramp_seconds=0.0,
                    now=0.0,
                    n_cylinders=engine_params.n_cylinders,
                )
        self.faults.step(0.0)

        # Warm start. `EnginePlant.reset()` leaves a cold crank: oil pressure at its 15 kPa
        # floor, head at 145 degC, oil at 88 degC. Integrating forward from there means the
        # first several minutes of every scenario are a start-up transient, and an oil
        # gauge still on its way up gets recorded as a lubrication excursion the engine
        # never actually had. A test bench answers "hold this condition and see what
        # happens", so it starts from the condition already held — with whatever
        # pre-existing wear the operator specified. The twin gets the same treatment with
        # a healthy fault state, so the residual starts at zero instead of decaying out of
        # a start-up mismatch.
        throttle_0 = self.throttle.at(0.0)
        airspeed_0 = scenario_airspeed_ms(throttle_0)
        self.initial_state = relax_to_steady_state(
            self.plant,
            throttle_0,
            params.altitude_m,
            airspeed_0,
            self.faults,
            ambient_temperature_c=params.ambient_temperature_c,
            params=engine_params,
        )
        relax_to_steady_state(
            self.twin.plant,
            throttle_0,
            params.altitude_m,
            airspeed_0,
            self.twin.healthy,
            ambient_temperature_c=params.ambient_temperature_c,
            params=engine_params,
        )

        self._pending = sorted(
            (params.injected_faults_during_scenario or ()),
            key=lambda f: f.at_time_min,
        )
        self._next_pending = 0

    def release_due_faults(self, t_s: float) -> None:
        while (
            self._next_pending < len(self._pending)
            and self._pending[self._next_pending].at_time_min * 60.0 <= t_s
        ):
            sched = self._pending[self._next_pending]
            self.faults.inject(
                sched.fault_type,
                sched.severity,
                ramp_seconds=max(0.0, sched.ramp_minutes * 60.0),
                now=t_s,
                n_cylinders=self.p.n_cylinders,
            )
            self._next_pending += 1


def _choose_timestep(simulated_seconds: float) -> float:
    """Widen the sub-step for long scenarios, but never past the stability ceiling."""
    if simulated_seconds / DT_MIN_S <= MAX_SUBSTEPS:
        return DT_MIN_S
    return min(DT_MAX_S, simulated_seconds / MAX_SUBSTEPS)


def _choose_sample_interval(simulated_seconds: float) -> float:
    """One sample per simulated second, stretched so the response stays bounded."""
    return max(1.0, simulated_seconds / MAX_SAMPLES)


def run_scenario(
    params: ScenarioParams, engine_params: EngineParams = PARAMS
) -> ScenarioResult:
    """Fly a what-if scenario headless and return the full time-series plus a summary.

    Raises `ValueError` if the request falls outside modelled validity — the API layer
    turns that into a 400 with the same explanation.
    """
    errors = validate_scenario_params(params, engine_params)
    if errors:
        raise ValueError("; ".join(errors))

    started = time.perf_counter()
    run = _ScenarioRun(params, engine_params)
    p = engine_params

    total_s = params.duration_minutes * 60.0
    dt = _choose_timestep(total_s)
    sample_interval_s = _choose_sample_interval(total_s)
    substeps_per_sample = max(1, int(round(sample_interval_s / dt)))
    # Re-derive the effective step so an integer number of sub-steps lands exactly on
    # each sample boundary; otherwise sample times drift against simulated time.
    dt = sample_interval_s / substeps_per_sample
    n_samples = max(1, int(round(total_s / sample_interval_s)))

    altitude = params.altitude_m
    ambient = params.ambient_temperature_c

    frames: list[TelemetryFrame] = []
    reliability_traj: list[ReliabilityPoint] = []
    recovery_traj: list[ReliabilityPoint] = []
    excursion_state: dict[tuple[str, str, str], dict] = {}

    peak_cht = -1e9
    peak_egt = -1e9
    peak_oil_temp = -1e9
    min_oil_pressure = 1e9
    min_health = 1e9
    min_health_at_min = 0.0
    min_rul: float | None = None
    power_sum = 0.0
    bsfc_values: list[float] = []
    fuel_litres = 0.0
    fuel_lph_sum = 0.0
    worst_reco = "GO"
    reco_rank = {"GO": 0, "CAUTION": 1, "NO-GO": 2}
    worst_recovery_reco = "RTB-SAFE"
    recovery_reco_rank = {"RTB-SAFE": 0, "RTB-CAUTION": 1, "RTB-AT-RISK": 2}

    sim_time_s = 0.0
    advisories: list = []
    frame_dicts_last: dict = {}
    classifier_stride = max(1, math.ceil(n_samples / MAX_CLASSIFIER_EVALUATIONS))
    diagnosis = None

    for sample_index in range(n_samples):
        # One sample is a few milliseconds of tight Python. Releasing the GIL between them
        # keeps a long scenario from locking the live telemetry broadcast out of the
        # interpreter for hundreds of milliseconds at a stretch.
        yield_to_event_loop()
        misfire_seen = False
        throttle = 0.0
        for substep_index in range(substeps_per_sample):
            if substep_index and substep_index % 200 == 0:
                yield_to_event_loop()
            run.faults.step(dt)
            sim_time_s += dt
            run.release_due_faults(sim_time_s)

            throttle = run.throttle.at(sim_time_s)
            airspeed = scenario_airspeed_ms(throttle)

            eng = run.plant.substep(
                dt,
                throttle,
                altitude,
                airspeed,
                run.faults,
                ambient_temperature_c=ambient,
                generate_vibration=False,
            )
            run.twin.substep(
                dt,
                throttle,
                altitude,
                airspeed,
                ambient_temperature_c=ambient,
                generate_vibration=False,
            )
            misfire_seen = misfire_seen or any(eng.misfire_events)

        # Vibration, batched into one window per sample rather than per sub-step. Same
        # model, same sample rate, same rolling buffer — just called less often.
        vib_window_s = min(VIBRATION_WINDOW_MAX_S, sample_interval_s)
        real_vib = run.plant.vibration.generate_window(
            vib_window_s,
            rpm=run.plant.state.rpm,
            fault_state=run.faults,
            misfire_active=misfire_seen,
            with_features=False,
        )
        run.plant.state.vibration_rms = real_vib.rms_per_cylinder
        twin_vib = run.twin.plant.vibration.generate_window(
            vib_window_s,
            rpm=run.twin.plant.state.rpm,
            fault_state=run.twin.healthy,
            misfire_active=False,
            with_features=False,
        )
        run.twin.plant.state.vibration_rms = twin_vib.rms_per_cylinder

        # ---- PHM chain, identical to the live loop --------------------------
        real_state = run.plant.finalise_tick()
        comparison = run.twin.compare(real_state)
        report = run.residuals.update(comparison.residuals, dt_s=sample_interval_s)
        anomaly = run.anomaly.update(report, dt_s=sample_interval_s)
        rul_estimate = run.rul.update(sim_time_s, anomaly.health_indicators)
        reliability = run.reliability.evaluate(
            rul_minutes=rul_estimate.minutes,
            mission_remaining_s=max(0.0, total_s - sim_time_s),
            overall_health=anomaly.overall_health,
        )
        # Phase 5: a scenario has no phase machinery (no climb/cruise/loiter/descent —
        # just a throttle profile held or ramped over the run), so there is no outbound
        # transit to mirror the way `MissionProfile.estimated_rtb_seconds()` does. The
        # simplest estimate that is still monotonic and still answers "how far out is
        # this what-if, right now": elapsed scenario time so far, same heuristic as the
        # mission profile's loiter case ("RTB mirrors time already spent").
        recovery_reliability = run.reliability.compute_recovery_reliability(
            current_health_indicators=anomaly.health_indicators,
            rul_estimate=rul_estimate,
            estimated_rtb_time_minutes=sim_time_s / 60.0,
        )
        if diagnosis is None or sample_index % classifier_stride == 0:
            residual_z = {c: report.z(c) for c in report.stats}
            diagnosis = run.classifier.predict(
                run.residuals.feature_vector(), residual_z=residual_z
            )
        efficiency = run.efficiency.update(
            fuel_flow_lph=real_state.fuel_flow_lph,
            power_kw=real_state.power_brake_kw,
            dt_s=sample_interval_s,
        )
        advisories = run.advisor.evaluate(
            health_indicators=anomaly.health_indicators,
            rul_minutes=rul_estimate.minutes,
            rul_subsystem=rul_estimate.subsystem,
            active_faults=run.faults.active(),
            efficiency_trend=efficiency.trend,
            combustion_instability_pct=real_state.combustion_instability_pct,
            predicted_source=diagnosis.predicted_source,
            battery_voltage_v=real_state.battery_voltage_v,
        )

        t_min = sim_time_s / 60.0

        # ---- running extrema and limit tracking ------------------------------
        egt_max_now = max(real_state.egt_c) if real_state.egt_c else 0.0
        peak_cht = max(peak_cht, real_state.cht_c)
        peak_egt = max(peak_egt, egt_max_now)
        peak_oil_temp = max(peak_oil_temp, real_state.oil_temp_c)
        min_oil_pressure = min(min_oil_pressure, real_state.oil_pressure_kpa)
        if anomaly.overall_health < min_health:
            min_health = anomaly.overall_health
            min_health_at_min = t_min
        if rul_estimate.minutes is not None:
            min_rul = (
                rul_estimate.minutes
                if min_rul is None
                else min(min_rul, rul_estimate.minutes)
            )
        power_sum += real_state.power_brake_kw
        if efficiency.bsfc_g_per_kwh is not None:
            bsfc_values.append(efficiency.bsfc_g_per_kwh)
        fuel_litres += real_state.fuel_flow_lph * (sample_interval_s / 3600.0)
        fuel_lph_sum += real_state.fuel_flow_lph
        if reco_rank[reliability.recommendation] > reco_rank[worst_reco]:
            worst_reco = reliability.recommendation
        if (
            recovery_reco_rank[recovery_reliability.recommendation]
            > recovery_reco_rank[worst_recovery_reco]
        ):
            worst_recovery_reco = recovery_reliability.recommendation

        for parameter, value, hard, caution, unit, direction in (
            ("cht_c", real_state.cht_c, p.cht_limit_c, p.cht_caution_c, "degC", "above"),
            ("egt_c", egt_max_now, p.egt_limit_c, p.egt_caution_c, "degC", "above"),
            (
                "oil_temp_c", real_state.oil_temp_c,
                p.oil_temp_limit_c, p.oil_temp_caution_c, "degC", "above",
            ),
            (
                "oil_pressure_kpa", real_state.oil_pressure_kpa,
                p.oil_pressure_min_operating_kpa, p.oil_pressure_caution_kpa,
                "kPa", "below",
            ),
        ):
            _track_excursion(
                excursion_state, parameter, "limit", value, hard,
                unit, direction, t_min, sample_interval_s,
            )
            _track_excursion(
                excursion_state, parameter, "caution", value, caution,
                unit, direction, t_min, sample_interval_s,
            )

        # Cold oil has no red line — it is a "this power setting never warms the engine
        # through" finding, which matters at high altitude and low power.
        _track_excursion(
            excursion_state, "oil_temp_c", "caution", real_state.oil_temp_c,
            p.oil_temp_min_operating_c, "degC", "below", t_min, sample_interval_s,
        )

        reliability_traj.append(
            ReliabilityPoint(
                time_min=round(t_min, 3),
                score=round(reliability.score, 4),
                recommendation=reliability.recommendation,
            )
        )
        recovery_traj.append(
            ReliabilityPoint(
                time_min=round(t_min, 3),
                score=round(recovery_reliability.score, 4),
                recommendation=recovery_reliability.recommendation,
            )
        )

        frame = _build_frame(
            sim_time_s=sim_time_s,
            throttle=throttle,
            altitude_m=altitude,
            airspeed_ms=scenario_airspeed_ms(throttle),
            ambient_temperature_c=ambient,
            real_state=real_state,
            anomaly=anomaly,
            rul_minutes=rul_estimate.minutes,
            reliability=reliability,
            recovery_reliability=recovery_reliability,
            faults=run.faults,
            predicted_source=diagnosis.predicted_source,
            efficiency=efficiency,
            advisories=advisories,
        )
        frames.append(frame)
        frame_dicts_last = {"health": anomaly.health_indicators}

    subsystem_scores = frame_dicts_last.get("health", {})
    worst_subsystem = (
        min(subsystem_scores, key=lambda k: subsystem_scores[k])
        if subsystem_scores
        else "cylinder"
    )
    excursions = _finalise_excursions(excursion_state, "limit")
    cautions = _finalise_excursions(excursion_state, "caution")
    final_health = frames[-1].health.overall_score if frames else 100.0
    final_rul = frames[-1].rul_minutes if frames else None
    final_reco = frames[-1].mission_reliability.recommendation if frames else "GO"
    final_recovery_reco = (
        frames[-1].recovery_reliability.recommendation
        if frames and frames[-1].recovery_reliability is not None
        else "RTB-SAFE"
    )

    stayed_healthy = min_health >= p.scenario_health_safe_threshold
    stayed_in_limits = not excursions
    verdict = _verdict(stayed_healthy, stayed_in_limits, bool(cautions), worst_reco)

    summary = ScenarioSummary(
        verdict=verdict,
        stayed_within_safe_health=stayed_healthy,
        stayed_within_operating_limits=stayed_in_limits,
        health_safe_threshold=p.scenario_health_safe_threshold,
        min_health_score=round(min_health, 1),
        min_health_at_min=round(min_health_at_min, 2),
        final_health_score=round(final_health, 1),
        final_rul_minutes=final_rul,
        min_rul_minutes=round(min_rul, 1) if min_rul is not None else None,
        worst_subsystem=worst_subsystem,
        worst_subsystem_score=round(subsystem_scores.get(worst_subsystem, 100.0), 1),
        final_subsystem_scores={k: round(v, 1) for k, v in subsystem_scores.items()},
        final_recommendation=final_reco,
        worst_recommendation=worst_reco,
        mission_reliability_trajectory=_downsample(reliability_traj, 240),
        final_recovery_recommendation=final_recovery_reco,
        worst_recovery_recommendation=worst_recovery_reco,
        recovery_reliability_trajectory=_downsample(recovery_traj, 240),
        limit_excursions=excursions,
        caution_excursions=cautions,
        peak_cht_c=round(peak_cht, 1),
        peak_egt_c=round(peak_egt, 1),
        min_oil_pressure_kpa=round(min_oil_pressure, 1),
        peak_oil_temp_c=round(peak_oil_temp, 1),
        mean_power_kw=round(power_sum / max(1, n_samples), 2),
        mean_bsfc_g_per_kwh=(
            round(sum(bsfc_values) / len(bsfc_values), 1) if bsfc_values else None
        ),
        total_fuel_litres=round(fuel_litres, 2),
        fuel_burn_lph_mean=round(fuel_lph_sum / max(1, n_samples), 2),
        final_advisories=[a.to_dict() for a in advisories],
        headline=_headline(
            verdict, min_health, worst_subsystem, worst_reco, excursions, cautions
        ),
    )

    compute_s = time.perf_counter() - started
    notes = [
        f"Integrated at {dt * 1000:.0f} ms sub-steps, sampled every "
        f"{sample_interval_s:.1f} simulated seconds ({len(frames)} frames).",
        f"{total_s / max(compute_s, 1e-9):.0f}x faster than real time.",
        "Airspeed follows a throttle schedule, not a flight-path simulation "
        f"({scenario_airspeed_ms(0.0):.0f}-{scenario_airspeed_ms(1.0):.0f} m/s).",
        "Simulated result — not live telemetry, and not recorded as a mission.",
    ]

    logger.info(
        "Scenario complete: %.1f simulated minutes in %.2f s (%s)",
        params.duration_minutes,
        compute_s,
        verdict,
    )

    return ScenarioResult(
        params=_params_to_dict(params),
        frames=frames,
        summary=summary,
        integration_dt_s=round(dt, 5),
        sample_interval_s=round(sample_interval_s, 3),
        simulated_seconds=round(total_s, 1),
        compute_seconds=round(compute_s, 3),
        notes=notes,
    )


# ---- helpers -----------------------------------------------------------------


def _build_frame(
    *,
    sim_time_s: float,
    throttle: float,
    altitude_m: float,
    airspeed_ms: float,
    ambient_temperature_c: float | None,
    real_state,
    anomaly,
    rul_minutes: float | None,
    reliability,
    recovery_reliability,
    faults: FaultState,
    predicted_source: str,
    efficiency,
    advisories,
) -> TelemetryFrame:
    """Assemble a TelemetryFrame in exactly the live schema.

    `timestamp` carries *seconds since the start of the scenario*, not a wall-clock epoch.
    These frames never touch the WebSocket or the missions table, and the Test Bench
    charts them on a relative axis; stamping them with a fake epoch would only invite
    someone to mistake them for a recording.
    """
    cylinders = [
        CylinderReading(
            id=i + 1,
            egt_c=round(real_state.egt_c[i], 1),
            vibration_rms=round(max(0.0, real_state.vibration_rms[i]), 4),
        )
        for i in range(len(real_state.egt_c))
    ]
    active = [
        ActiveFault(
            type=ft,
            severity=round(sev, 4),
            started_at=faults.started_at.get(ft, 0.0),
            predicted_source=predicted_source,  # type: ignore[arg-type]
            classifier_explanation=None,
            is_sensor_fault=False,
        )
        for ft, sev in faults.active().items()
    ]
    return TelemetryFrame(
        timestamp=round(sim_time_s, 3),
        mission_phase="cruise",
        rpm=round(real_state.rpm, 1),
        manifold_pressure_kpa=round(real_state.manifold_pressure_kpa, 2),
        boost_pressure_kpa=round(real_state.boost_pressure_kpa, 2),
        cylinders=cylinders,
        cht_c=round(real_state.cht_c, 1),
        oil_temp_c=round(real_state.oil_temp_c, 1),
        oil_pressure_kpa=round(real_state.oil_pressure_kpa, 1),
        fuel_flow_lph=round(real_state.fuel_flow_lph, 2),
        altitude_m=round(altitude_m, 1),
        airspeed_ms=round(airspeed_ms, 1),
        health=HealthState(
            overall_score=round(anomaly.overall_health, 1),
            subsystem_scores=SubsystemScores(
                **{k: round(v, 1) for k, v in anomaly.health_indicators.items()}
            ),
        ),
        rul_minutes=round(rul_minutes, 1) if rul_minutes is not None else None,
        mission_reliability=MissionReliability(
            score=round(reliability.score, 3),
            recommendation=reliability.recommendation,  # type: ignore[arg-type]
        ),
        recovery_reliability=RecoveryReliability(
            score=round(recovery_reliability.score, 3),
            recommendation=recovery_reliability.recommendation,  # type: ignore[arg-type]
        ),
        active_faults=active,
        battery_voltage_v=round(real_state.battery_voltage_v, 2),
        alternator_output_v=round(real_state.alternator_output_v, 2),
        injection_timing_deg=round(real_state.injection_timing_deg, 2),
        combustion_instability_pct=round(real_state.combustion_instability_pct, 2),
        ambient_temperature_c=round(
            ambient_temperature_c
            if ambient_temperature_c is not None
            else atmosphere(altitude_m).temperature_c,
            1,
        ),
        bsfc_g_per_kwh=(
            round(efficiency.bsfc_g_per_kwh, 1)
            if efficiency.bsfc_g_per_kwh is not None
            else None
        ),
        efficiency_trend=efficiency.trend,  # type: ignore[arg-type]
        maintenance_advisories=[MaintenanceAdvisory(**a.to_dict()) for a in advisories],
        is_replay=False,
    )


def _track_excursion(
    state: dict[tuple[str, str, str], dict],
    parameter: str,
    band: str,
    value: float,
    limit: float,
    unit: str,
    direction: str,
    t_min: float,
    sample_interval_s: float,
) -> None:
    """Accumulate one parameter's time outside one band.

    Excursions are recorded per parameter rather than per contiguous event: what a mission
    planner needs is "CHT was over its limit for four minutes, peaking at 238" — the fact
    that it happened across two separate climbs does not change the decision.
    """
    if not (value > limit if direction == "above" else value < limit):
        return
    # Direction is part of the key because one parameter can have bands on both sides:
    # oil that is too hot and oil that never warmed up are different findings.
    key = (parameter, band, direction)
    entry = state.get(key)
    if entry is None:
        state[key] = {
            "parameter": parameter,
            "band": band,
            "limit": limit,
            "unit": unit,
            "direction": direction,
            "peak_value": value,
            "first_at_min": t_min,
            "duration_min": sample_interval_s / 60.0,
        }
        return
    entry["duration_min"] += sample_interval_s / 60.0
    entry["peak_value"] = (
        max(entry["peak_value"], value)
        if direction == "above"
        else min(entry["peak_value"], value)
    )


def _finalise_excursions(
    state: dict[tuple[str, str, str], dict], band: str
) -> list[LimitExcursion]:
    """Excursions in one band, worst (longest) first."""
    entries = [e for e in state.values() if e["band"] == band]
    entries.sort(key=lambda e: -e["duration_min"])
    return [
        LimitExcursion(
            parameter=e["parameter"],
            band=e["band"],  # type: ignore[arg-type]
            limit=round(e["limit"], 1),
            unit=e["unit"],
            peak_value=round(e["peak_value"], 1),
            first_at_min=round(e["first_at_min"], 2),
            duration_min=round(e["duration_min"], 2),
            direction=e["direction"],  # type: ignore[arg-type]
        )
        for e in entries
    ]


def _downsample(points: list[ReliabilityPoint], target: int) -> list[ReliabilityPoint]:
    """Thin the reliability trajectory, always keeping the first and last point."""
    if len(points) <= target:
        return points
    stride = math.ceil(len(points) / target)
    thinned = points[::stride]
    if thinned[-1] is not points[-1]:
        thinned.append(points[-1])
    return thinned


def _verdict(
    stayed_healthy: bool,
    stayed_in_limits: bool,
    entered_caution: bool,
    worst_recommendation: str,
) -> Verdict:
    """PASS / CAUTION / FAIL, taken from the *worst* point of the run.

    A scenario that dipped into NO-GO halfway through and recovered is not a pass — the
    whole reason to run a what-if is to find that dip before flying it.
    """
    if worst_recommendation == "NO-GO" or not stayed_in_limits:
        return "FAIL"
    if worst_recommendation == "CAUTION" or entered_caution or not stayed_healthy:
        return "CAUTION"
    return "PASS"


def _headline(
    verdict: Verdict,
    min_health: float,
    worst_subsystem: str,
    worst_recommendation: str,
    excursions: list[LimitExcursion],
    cautions: list[LimitExcursion],
) -> str:
    worst = (excursions or cautions or [None])[0]
    if worst is None:
        breach = "every parameter stayed inside its normal operating band"
    else:
        band = "red-line" if worst.band == "limit" else "caution"
        breach = (
            f"{worst.parameter} spent {worst.duration_min:.1f} min {worst.direction} its "
            f"{worst.limit:g} {worst.unit} {band} (peak {worst.peak_value:g}, first at "
            f"T+{worst.first_at_min:.1f} min)"
        )
    return (
        f"{verdict}: health bottomed out at {min_health:.0f}/100 "
        f"({worst_subsystem} worst), mission reliability reached "
        f"{worst_recommendation}, and {breach}."
    )


def _params_to_dict(params: ScenarioParams) -> dict:
    profile = params.throttle_profile
    if isinstance(profile, (int, float)):
        profile_out: object = float(profile)
    else:
        profile_out = [
            {"time_min": wp.time_min, "throttle_pct": wp.throttle_pct} for wp in profile
        ]
    return {
        "altitude_m": params.altitude_m,
        "ambient_temperature_c": params.ambient_temperature_c,
        "duration_minutes": params.duration_minutes,
        "throttle_profile": profile_out,
        "initial_fault_severities": dict(params.initial_fault_severities or {}),
        "injected_faults_during_scenario": [
            {
                "fault_type": f.fault_type,
                "severity": f.severity,
                "at_time_min": f.at_time_min,
                "ramp_minutes": f.ramp_minutes,
            }
            for f in (params.injected_faults_during_scenario or ())
        ],
        "label": params.label,
    }
