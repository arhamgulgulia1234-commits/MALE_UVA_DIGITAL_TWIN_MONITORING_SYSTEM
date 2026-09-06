"""The Phase 2 simulation loop: real physics -> digital twin -> PHM -> TelemetryFrame.

Each 100 ms wall-clock tick:

  1. Work out how much *simulated* time to advance (100 ms x the time-acceleration
     factor) and split it into fixed 20 ms integration sub-steps.
  2. For every sub-step: advance the mission profile, ramp any in-flight fault, then step
     the real EnginePlant and the healthy DigitalTwin with identical commands.
  3. Once per tick: compute spectral features, take residuals against the twin, update the
     residual monitor, anomaly detector, RUL predictor and mission-reliability model.
  4. Assemble a TelemetryFrame in the exact Phase 1 schema and broadcast it.

The frame schema is unchanged from Phase 1 — the frontend cannot tell the difference
except that the numbers are now physically derived.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.engine_params import PARAMS, EngineParams
from app.core.models import (
    ActiveFault,
    CHTSensorInnovations,
    ClassifierExplanation,
    CylinderReading,
    EarlyWarning as EarlyWarningModel,
    HealthState,
    MaintenanceAdvisory,
    MissionReliability,
    RecoveryReliability,
    RPMSensorInnovations,
    SubsystemScores,
    TelemetryFrame,
)
from app.fusion.cht_fusion import CHTFusion
from app.fusion.fusion_monitor import FusionMonitor
from app.fusion.oil_pressure_fusion import OilPressureFusion
from app.fusion.rpm_fusion import RPMFusion
from app.ml.anomaly_detector import AnomalyDetector, AnomalyReport, SUBSYSTEM_CHANNELS
from app.ml.efficiency_analysis import EfficiencyAnalyser
from app.ml.fault_classifier import Diagnosis, FaultClassifier
from app.ml.maintenance_advisor import EarlyWarning, MaintenanceAdvisor
from app.ml.mission_reliability import MissionReliabilityModel
from app.ml.rul_predictor import RULPredictor
from app.physics.environment import atmosphere
from app.physics.fault_models import CYLINDER_LOCALISED_FAULTS, FAULT_TYPES, FaultState
from app.physics.plant import EnginePlant
from app.physics.sensor_fault_model import SensorFaultModel, SensorFaultState
from app.sim.mission_profiles import MissionProfile, apply_scenario
from app.twin.digital_twin import DigitalTwin
from app.twin.residual_analysis import PreAlertMonitor, ResidualMonitor, worst_level

logger = logging.getLogger(__name__)

INTERNAL_DT_S = 0.02  # fixed 20 ms integration sub-step


@dataclass
class DiagnosticSnapshot:
    """Everything the PHM layer inferred this tick.

    The TelemetryFrame schema is frozen, so the classifier's opinion is exposed through
    GET /twin/diagnosis instead of being forced into the WebSocket contract."""

    residual_z: dict[str, float] = field(default_factory=dict)
    residual_mean: dict[str, float] = field(default_factory=dict)
    flagged_channels: list[str] = field(default_factory=list)
    anomaly_scores: dict[str, float] = field(default_factory=dict)
    predicted_fault: str = "healthy"
    prediction_confidence: float = 0.0
    classifier_available: bool = False
    rul_subsystem: str | None = None
    rul_model: str = "stable"
    twin_channels: dict[str, float] = field(default_factory=dict)
    real_channels: dict[str, float] = field(default_factory=dict)
    # ---- Phase 3 ---------------------------------------------------------
    predicted_source: str = "uncertain"
    source_rationale: str = ""
    classifier_explanation: list[dict] = field(default_factory=list)
    #: The uncorrupted physical state, before sensor faults were applied. Diagnostic
    #: only — it is what lets the validation script prove a sensor fault left the engine
    #: untouched, and it is deliberately never put on the telemetry stream.
    true_state: dict[str, float] = field(default_factory=dict)
    sensor_fault_truth: dict[str, float] = field(default_factory=dict)
    bsfc_g_per_kwh: float | None = None
    efficiency_trend: str = "stable"
    # ---- Phase 5: sensor fusion --------------------------------------------
    #: Raw fusion outputs, keyed for the TelemetryFrame fields and /twin/diagnosis.
    fusion_values: dict[str, float] = field(default_factory=dict)
    #: EWMA z-scores over the fusion signals — the classifier's per-fused-channel
    #: disambiguation input (see fault_classifier.classify_source).
    fusion_z: dict[str, float] = field(default_factory=dict)
    #: Which specific probe/instrument the disambiguation logic named, if any — e.g.
    #: "cht_sensor_secondary", "rpm_tachometer". None when no fused channel is
    #: implicated, or when the culprit cannot be narrowed past a general subsystem.
    suspect_sensor: str | None = None
    # ---- Early warning: pre-alert tier ------------------------------------
    #: Per-channel pre-alert level ("none"/"emerging"/"building"), diagnostic-only —
    #: mirrors residual_z/residual_mean above. Never sent to the frontend; the
    #: per-subsystem worst-of-channels view below and `TelemetryFrame.early_warnings` are
    #: what the dashboard and the validation harness actually consume.
    pre_alert_levels: dict[str, str] = field(default_factory=dict)
    #: Per-subsystem worst-of-its-channels pre-alert level — what gates
    #: `RULPredictor.update_early_warnings` and `MaintenanceAdvisor.generate_early_warning`.
    subsystem_pre_alert_levels: dict[str, str] = field(default_factory=dict)


class SimulationLoop:
    """Owns all mutable simulation state.

    Driven by a single asyncio task and mutated from HTTP control handlers running on the
    same event loop, so no locking is required."""

    def __init__(self, params: EngineParams = PARAMS) -> None:
        self.p = params

        self.plant = EnginePlant(params, seed=7)
        self.twin = DigitalTwin(params, seed=101)
        self.mission = MissionProfile()
        self.faults = FaultState()

        self.residuals = ResidualMonitor()
        #: Early warning: reads the identical residual dict `self.residuals.update()`
        #: receives every tick (see `tick()` below), so the two monitors can never
        #: disagree about what the residual *was* that tick — only about how early to
        #: react to it. See app/twin/residual_analysis.py's module docstring.
        self.pre_alert = PreAlertMonitor()
        self.fusion_stats = FusionMonitor()
        self.anomaly = AnomalyDetector()
        self.rul = RULPredictor()
        self.reliability = MissionReliabilityModel()
        self.classifier = FaultClassifier()
        self.classifier.set_feature_names(
            self.residuals.feature_names() + self.fusion_stats.feature_names()
        )

        # ---- Phase 3 --------------------------------------------------------
        self.sensor_faults = SensorFaultState()
        self.sensor_model = SensorFaultModel(params)
        # Phase 5: sensor fusion. Each replaces a single noisy reading with a Kalman
        # best-estimate from independent evidence — see app/fusion/ for why each channel
        # is fused the way it is. Their outputs are written back onto `real_state`
        # every tick (below, in `tick()`), which is what makes the fused values — not
        # the raw sensor readings — what residuals, health scoring and RUL actually
        # consume from here on.
        self.cht_fusion = CHTFusion(params)
        self.rpm_fusion = RPMFusion(params)
        self.oil_pressure_fusion = OilPressureFusion(params)
        self.efficiency = EfficiencyAnalyser(
            fuel_density_kg_per_l=params.fuel_density_kg_per_l
        )
        self.advisor = MaintenanceAdvisor()
        #: None means "use the ISA temperature for the current altitude".
        self.ambient_temperature_c: float | None = None
        self.active_mission_id: int | None = None
        self._true_state_snapshot: dict[str, float] = {}
        #: Phase 5: the wear state this mission was seeded from, set by
        #: `seed_fault_state_from_wear()` at mission start and read back at mission end
        #: to decide which faults were "active during the mission" — see
        #: app/api/control.py's mission-end handler.
        self.mission_seed_wear_state: dict[str, float] = {}
        #: Phase 5: `sim_time_s` at the moment the current mission started, so mission
        #: end can bill `engine_lifecycle.total_operating_hours` for *simulated* engine
        #: seconds elapsed — see the note on `sim_time_s` below for why that is not the
        #: same number as the mission report's `duration_s`.
        self.mission_start_sim_time_s: float = 0.0

        #: Simulated seconds since this process started, advanced by `dt` every
        #: sub-step — i.e. by wall-clock time *times `time_scale`*, not by wall-clock
        #: time alone. This is the number an operating-hours meter on the actual engine
        #: would read. `TelemetryFrame.timestamp` (below, in `tick()`) is deliberately a
        #: different clock: it is `time.time()`, because the dashboard and the replay
        #: engine need to place frames on a real timeline. A mission report's
        #: `duration_s` comes from *that* clock, so at any time_scale other than 1x it
        #: answers "how long did the operator wait" — the wrong question for a wear
        #: ledger, which needs "how long did the crank turn."
        self.sim_time_s: float = 0.0
        self.time_scale: float = 1.0
        self.manual_throttle: float | None = None
        self.throttle: float = self.mission.commanded_throttle()

        self._latest: TelemetryFrame | None = None
        self.diagnostics = DiagnosticSnapshot()

        #: Simulated-time bookkeeping for `_phm_analysis_due()`. Every tick still runs
        #: the physics, the twin, the residual/anomaly EWMAs and assembles a full
        #: TelemetryFrame; these hold the slow-window analytics' last answers between
        #: their (less frequent) re-evaluations.
        self._phm_next_eval_s: float = 0.0
        self._cached_warning_estimates: dict = {}
        self._cached_diagnosis: Diagnosis | None = None

        self.plant.reset(self.mission.altitude_m)
        self.twin.reset(self.mission.altitude_m)

    # ---- control surface -----------------------------------------------------

    def set_throttle(self, value: float) -> None:
        """Pin the throttle manually, overriding the mission profile's command."""
        self.manual_throttle = max(0.0, min(1.0, value))
        # Reflect it immediately so a control response does not report the stale value
        # from the previous tick.
        self.throttle = self.manual_throttle

    def release_throttle(self) -> None:
        self.manual_throttle = None

    # ---- Phase 4: mixture and timing trims -----------------------------------
    #
    # Phase 2 fixed AFR and injection timing to internal schedules, so the only
    # externally settable command was throttle. The Test Bench's operating-point
    # optimizer recommends all three, so the manual-override mechanism is extended to
    # carry them.
    #
    # Two things make this a small change rather than a change to the tick loop. The
    # trims live on the EngineModel as commands, not integrator state, so setting them
    # once persists — there is nothing to reapply every sub-step. And they are set on the
    # digital twin as well as on the real engine, because a commanded operating point is
    # an operating *condition*, not a fault: exactly the argument that already applies to
    # ambient temperature. A twin still flying the book mixture while the engine runs
    # trimmed would show the difference as a residual, and the PHM layer would report an
    # operator's deliberate lean as a developing fuel-system fault.

    def set_operating_setpoint(
        self,
        throttle: float | None = None,
        afr_trim: float | None = None,
        injection_timing_trim_deg: float | None = None,
    ) -> dict:
        """Command any subset of the operating point. `None` leaves that lever alone."""
        if throttle is not None:
            self.set_throttle(throttle)
        if afr_trim is not None or injection_timing_trim_deg is not None:
            for engine in (self.plant.engine, self.twin.plant.engine):
                engine.set_trims(
                    afr_trim=afr_trim,
                    injection_timing_trim_deg=injection_timing_trim_deg,
                )
        return self.operating_setpoint()

    def reset_trims(self) -> dict:
        """Return mixture and timing to their scheduled values."""
        return self.set_operating_setpoint(
            afr_trim=0.0, injection_timing_trim_deg=0.0
        )

    def operating_setpoint(self) -> dict:
        afr_trim, timing_trim = self.plant.engine.trims()
        return {
            "throttle": round(self.throttle, 4),
            "manual_throttle": self.manual_throttle,
            "afr_trim": round(afr_trim, 3),
            "injection_timing_trim_deg": round(timing_trim, 3),
        }

    def current_health_state(self) -> dict[str, float]:
        """The live engine's fault severities, for optimising against the engine we
        actually have rather than a pristine one."""
        return dict(self.faults.active())

    # ---- Phase 5: engine life-cycle ------------------------------------------

    def seed_fault_state_from_wear(self, wear_state: dict[str, float]) -> dict[str, float]:
        """Replace the live `FaultState` with one seeded from persisted engine wear.

        Called once, from `POST /control/mission/start`, with
        `engine_lifecycle.current_wear_state`. A fresh `FaultState` is built rather than
        mutating the live one in place, so there is nothing left over from whatever the
        operator had injected live a moment before this mission began.

        Severities are set directly through the dataclass constructor rather than via
        `inject()`. `inject()` only *registers a ramp* — the attribute itself is not
        written until the next `FaultState.step(dt)` call, which is exactly right for a
        live operator command that should ease in over `ramp_seconds`, but wrong here:
        for one sub-step (up to 100 ms of wall-clock, at 1x time scale) the freshly
        seeded engine would read back as healthy even though the persisted wear says
        otherwise, and the confirmation this method returns to the caller would be
        flatly wrong before the first tick ever ran. Constructing the severities
        directly makes the seed exact and immediate, matching what the caller is told.

        The digital twin is deliberately not touched: `DigitalTwin` always flies
        `FaultState.healthy()`, and that reference has to stay exactly zero regardless of
        this engine's accumulated wear, or the residuals it exists to produce would stop
        meaning anything.
        """
        now = time.time()
        severities = {
            ft: max(0.0, min(1.0, float(wear_state.get(ft, 0.0)))) for ft in FAULT_TYPES
        }
        fresh = FaultState(**severities)
        for fault_type in CYLINDER_LOCALISED_FAULTS:
            if severities[fault_type] > 1e-4:
                fresh.cylinder_for(fault_type, self.p.n_cylinders)
                fresh.started_at[fault_type] = now
        self.faults = fresh
        self.mission_seed_wear_state = dict(severities)
        return dict(self.faults.snapshot())

    def set_time_scale(self, factor: float) -> None:
        self.time_scale = max(0.1, min(50.0, factor))

    def jump_phase(self, phase: str) -> None:
        self.mission.jump_to(phase)  # type: ignore[arg-type]

    def inject_fault(self, fault_type: str, severity: float, ramp_seconds: float) -> None:
        self.faults.inject(
            fault_type,
            severity,
            ramp_seconds,
            now=time.time(),
            n_cylinders=self.p.n_cylinders,
        )

    def clear_fault(self, fault_type: str, ramp_seconds: float = 8.0) -> None:
        self.faults.clear(fault_type, ramp_seconds)

    @property
    def active_faults(self) -> dict[str, float]:
        return self.faults.active()

    # ---- Phase 3 control surface --------------------------------------------

    def inject_sensor_fault(
        self, fault_type: str, severity: float, ramp_seconds: float
    ) -> None:
        self.sensor_faults.inject(
            fault_type,
            severity,
            ramp_seconds,
            now=time.time(),
            n_cylinders=self.p.n_cylinders,
        )

    def clear_sensor_fault(self, fault_type: str, ramp_seconds: float = 5.0) -> None:
        self.sensor_faults.clear(fault_type, ramp_seconds)

    def set_ambient_temperature(self, celsius: float | None) -> None:
        """None restores the ISA temperature for the current altitude."""
        self.ambient_temperature_c = celsius

    def apply_scenario(self, scenario: str) -> dict:
        """Apply a named environmental scenario from the mission profile table."""
        applied = apply_scenario(self, scenario)
        return applied

    # ---- PHM pacing ----------------------------------------------------------

    def _phm_analysis_due(self) -> bool:
        """True when the slow-window PHM analytics should be re-evaluated this tick.

        Three of them — the pre-alert variance/trend tests, the RUL trend fit and the
        random-forest classifier — recompute from scratch over windows measured in tens
        to hundreds of *simulated* seconds. Running them at the full 10 Hz tick rate was
        ~90% of this loop's CPU in steady state (once those windows had filled), for
        answers that by construction cannot change meaningfully within one 100 ms tick.
        Everything on the live telemetry path — physics, twin, residuals, anomaly scores,
        fusion, the frame itself — is untouched by this and still runs every tick; these
        three simply publish a value that is refreshed every
        `settings.phm_analysis_interval_s` simulated seconds and held in between.

        Gating on simulated time rather than on a tick count keeps the number of
        refreshes *per analysis window* the same at every `time_scale`: at 10x a single
        tick already advances 1 s of simulated time, so every tick evaluates and the
        behaviour is identical to the unpaced version. An interval of 0 disables the
        pacing entirely.
        """
        if settings.phm_analysis_interval_s <= 0.0:
            return True
        if self.sim_time_s < self._phm_next_eval_s:
            return False
        self._phm_next_eval_s = self.sim_time_s + settings.phm_analysis_interval_s
        return True

    # ---- main tick -----------------------------------------------------------

    def tick(self, wall_dt_s: float) -> TelemetryFrame:
        sim_dt_total = max(1e-4, wall_dt_s) * self.time_scale
        n_substeps = max(1, int(round(sim_dt_total / INTERNAL_DT_S)))
        dt = sim_dt_total / n_substeps

        for _ in range(n_substeps):
            self.faults.step(dt)
            self.sensor_faults.step(dt)
            self.mission.step(dt)
            self.sim_time_s += dt

            throttle = (
                self.manual_throttle
                if self.manual_throttle is not None
                else self.mission.commanded_throttle()
            )
            self.throttle = throttle
            altitude = self.mission.altitude_m
            airspeed = self.mission.airspeed_ms
            ambient = self.ambient_temperature_c

            self.plant.substep(
                dt, throttle, altitude, airspeed, self.faults, ambient_temperature_c=ambient
            )
            self.twin.substep(
                dt, throttle, altitude, airspeed, ambient_temperature_c=ambient
            )

        # ---- once-per-tick PHM chain ----------------------------------------
        # Phase 5: the true, pre-noise CHT and oil temperature — captured here, before
        # `finalise_tick()` applies the legacy single-sensor Gaussian noise, because
        # `cht_fusion.py` and `oil_pressure_fusion.py` need to draw their *own*
        # independent noise from the real physical value, not from an already-noised
        # one. `plant.state` at this exact point (last substep already ran, nothing
        # once-per-tick has touched it yet) is the one place that value exists.
        true_cht_c = self.plant.state.cht_c
        true_oil_temp_c = self.plant.state.oil_temp_c

        real_state = self.plant.finalise_tick()

        # Phase 3: sensor faults corrupt the *reported* values, and they are applied here
        # — after the physics has produced the true state and before the residual is
        # taken. The twin is untouched, so the residual still shows an anomaly even
        # though the engine is fine. That is exactly the situation the disambiguation
        # logic in the classifier exists to resolve.
        self._true_state_snapshot = real_state.channels()
        if self.sensor_faults.any_active():
            self.sensor_model.apply(real_state, self.sensor_faults)

        # Phase 5: fuse. RPM first (it uses the tachometer + vibration readings
        # `finalise_tick()`/`sensor_model.apply()` just finished producing), then oil
        # pressure (it needs *this tick's* fused RPM for its model-predicted term).
        # Every fused value is written back onto `real_state` immediately — the residual
        # monitor, anomaly detector, RUL predictor and the frame assembled below all read
        # `real_state.cht_c` / `.rpm` / `.oil_pressure_kpa` exactly as before, so from
        # this line on they are consuming the fused best-estimate instead of one noisy
        # sensor, with no changes needed anywhere downstream.
        cht_fusion_out = self.cht_fusion.step(true_cht_c, self.sensor_faults, sim_dt_total)
        rpm_fusion_out = self.rpm_fusion.step(real_state, self.sensor_faults, sim_dt_total)
        oil_fusion_out = self.oil_pressure_fusion.step(
            fused_rpm=rpm_fusion_out.fused_rpm,
            oil_temp_c=true_oil_temp_c,
            sensor_reading_kpa=real_state.oil_pressure_kpa,
            fault_state=self.faults,
            dt_s=sim_dt_total,
        )
        real_state.cht_c = cht_fusion_out.fused_cht_c
        real_state.rpm = rpm_fusion_out.fused_rpm
        real_state.oil_pressure_kpa = oil_fusion_out.fused_oil_pressure_kpa

        fusion_z = self.fusion_stats.update(
            {
                "cht_innovation_primary": cht_fusion_out.innovation_primary_c,
                "cht_innovation_secondary": cht_fusion_out.innovation_secondary_c,
                "rpm_innovation_tachometer": rpm_fusion_out.innovation_tachometer,
                "rpm_innovation_vibration_derived": rpm_fusion_out.innovation_vibration_derived,
                "oil_pressure_innovation": oil_fusion_out.innovation_kpa,
                "oil_pressure_gain_deviation": oil_fusion_out.kalman_gain_deviation,
            },
            dt_s=sim_dt_total,
        )

        comparison = self.twin.compare(real_state)
        report = self.residuals.update(comparison.residuals, dt_s=sim_dt_total)
        # Early warning: the identical residual dict `self.residuals.update()` just
        # consumed, handed to the earlier, softer pre-alert tier — see
        # app/twin/residual_analysis.py. Deliberately computed alongside, not instead of,
        # the existing z-score gate below; neither monitor's state feeds the other.
        # `phm_due` paces only the three slow-window analyses below — see
        # `_phm_analysis_due()`. Their inputs are still recorded every tick.
        phm_due = self._phm_analysis_due()
        pre_alert_report = self.pre_alert.update(
            comparison.residuals, dt_s=sim_dt_total, evaluate=phm_due
        )
        anomaly: AnomalyReport = self.anomaly.update(report, dt_s=sim_dt_total)
        rul_estimate = self.rul.update(
            self.sim_time_s, anomaly.health_indicators, evaluate=phm_due
        )
        reliability = self.reliability.evaluate(
            rul_minutes=rul_estimate.minutes,
            mission_remaining_s=self.mission.remaining_seconds(),
            overall_health=anomaly.overall_health,
        )
        # Phase 5: same PHM inputs, a different question — "can it get back to base
        # right now" instead of "can it finish what's planned." See
        # app/ml/mission_reliability.py::compute_recovery_reliability.
        recovery_reliability = self.reliability.compute_recovery_reliability(
            current_health_indicators=anomaly.health_indicators,
            rul_estimate=rul_estimate,
            estimated_rtb_time_minutes=self.mission.estimated_rtb_seconds() / 60.0,
        )

        # Early warning: gate each subsystem on the worst pre-alert level among the
        # channels that already feed its health score (SUBSYSTEM_CHANNELS — the identical
        # mapping AnomalyDetector uses), then ask for a trend-projected ETA to the gentler
        # warning threshold only for subsystems that gate passes.
        subsystem_pre_alert_levels = {
            subsystem: worst_level(
                [pre_alert_report.level(channel) for channel, *_ in channels]
            )
            for subsystem, channels in SUBSYSTEM_CHANNELS.items()
        }
        if phm_due:
            # Another whole-window least-squares fit per gated subsystem, and it reads
            # the pre-alert levels held above — so it is refreshed on exactly the ticks
            # those are, never against a stale gate.
            self._cached_warning_estimates = self.rul.update_early_warnings(
                self.sim_time_s, subsystem_pre_alert_levels
            )
        warning_estimates = self._cached_warning_estimates

        residual_z = {c: report.z(c) for c in report.stats}
        if phm_due or self._cached_diagnosis is None:
            # A random-forest `predict_proba` — hundreds of tree traversals — over
            # features that are themselves EWMAs with multi-second time constants.
            self._cached_diagnosis = self.classifier.predict(
                self.residuals.feature_vector() + self.fusion_stats.feature_vector(),
                residual_z=residual_z,
                fusion_z=fusion_z,
            )
        diagnosis: Diagnosis = self._cached_diagnosis

        efficiency = self.efficiency.update(
            fuel_flow_lph=real_state.fuel_flow_lph,
            power_kw=real_state.power_brake_kw,
            dt_s=sim_dt_total,
        )

        advisories = self.advisor.evaluate(
            health_indicators=anomaly.health_indicators,
            rul_minutes=rul_estimate.minutes,
            rul_subsystem=rul_estimate.subsystem,
            active_faults=self.faults.active(),
            efficiency_trend=efficiency.trend,
            combustion_instability_pct=real_state.combustion_instability_pct,
            predicted_source=diagnosis.predicted_source,
            battery_voltage_v=real_state.battery_voltage_v,
        )

        # Early warning: one concrete instruction per subsystem currently gated in.
        # `evaluate()` above is untouched by this — an early warning and a regular
        # advisory for the same subsystem can both be present on the same frame; this is
        # an earlier, additive tier, not a replacement. "building" (both pre-alert tests
        # firing) is listed ahead of "emerging", then by ascending predicted_minutes with
        # an as-yet-unfittable trend (None) sorted last.
        early_warnings: list[EarlyWarning] = [
            self.advisor.generate_early_warning(
                subsystem, level, warning_estimates[subsystem].predicted_minutes,
                warning_estimates[subsystem].confidence,
            )
            for subsystem, level in subsystem_pre_alert_levels.items()
            if level != "none"
        ]
        early_warnings.sort(
            key=lambda w: (
                0 if w.pre_alert_state == "building" else 1,
                w.predicted_minutes if w.predicted_minutes is not None else math.inf,
            )
        )

        self.diagnostics = DiagnosticSnapshot(
            residual_z=residual_z,
            residual_mean={c: report.mean(c) for c in report.stats},
            flagged_channels=anomaly.flagged_channels,
            anomaly_scores=anomaly.anomaly_scores,
            predicted_fault=diagnosis.predicted_fault,
            prediction_confidence=diagnosis.confidence,
            classifier_available=diagnosis.model_available,
            rul_subsystem=rul_estimate.subsystem,
            rul_model=rul_estimate.model,
            twin_channels=comparison.twin.channels(),
            real_channels=comparison.real.channels(),
            predicted_source=diagnosis.predicted_source,
            source_rationale=diagnosis.source_rationale,
            classifier_explanation=diagnosis.explanation_dicts(),
            # Ground truth for validation only — never sent to the frontend, which must
            # infer sensor-vs-physical the same way a real ground station would.
            true_state=self._true_state_snapshot,
            sensor_fault_truth=self.sensor_faults.snapshot(),
            bsfc_g_per_kwh=efficiency.bsfc_g_per_kwh,
            efficiency_trend=efficiency.trend,
            fusion_values={
                # Ground truth for validation only — the exact pre-noise physical CHT,
                # captured before either probe's independent noise was drawn from it.
                # Never sent to the frontend, same rule as `true_state` above.
                "true_cht_c": true_cht_c,
                "fused_cht_c": cht_fusion_out.fused_cht_c,
                "cht_primary_reading_c": cht_fusion_out.primary_reading_c,
                "cht_secondary_reading_c": cht_fusion_out.secondary_reading_c,
                "cht_innovation_primary_c": cht_fusion_out.innovation_primary_c,
                "cht_innovation_secondary_c": cht_fusion_out.innovation_secondary_c,
                "fused_rpm": rpm_fusion_out.fused_rpm,
                "rpm_tachometer": rpm_fusion_out.tachometer_rpm,
                "rpm_vibration_derived": rpm_fusion_out.vibration_derived_rpm,
                "rpm_innovation_tachometer": rpm_fusion_out.innovation_tachometer,
                "rpm_innovation_vibration_derived": rpm_fusion_out.innovation_vibration_derived,
                "fused_oil_pressure_kpa": oil_fusion_out.fused_oil_pressure_kpa,
                "oil_pressure_model_predicted_kpa": oil_fusion_out.model_predicted_kpa,
                "oil_pressure_sensor_reading_kpa": oil_fusion_out.sensor_reading_kpa,
                "oil_pressure_innovation_kpa": oil_fusion_out.innovation_kpa,
                "oil_pressure_kalman_gain": oil_fusion_out.kalman_gain,
            },
            fusion_z=fusion_z,
            suspect_sensor=diagnosis.suspect_sensor,
            pre_alert_levels={c: r.level for c, r in pre_alert_report.states.items()},
            subsystem_pre_alert_levels=subsystem_pre_alert_levels,
        )

        # ---- assemble the frame (schema identical to Phase 1) ----------------
        cylinders = [
            CylinderReading(
                id=i + 1,
                egt_c=round(real_state.egt_c[i], 1),
                vibration_rms=round(max(0.0, real_state.vibration_rms[i]), 4),
            )
            for i in range(len(real_state.egt_c))
        ]

        # Ground-truth fault state. The ControlDeck drives its per-fault "tap to clear"
        # toggle from this list, so it must reflect what is actually injected rather than
        # what the classifier believes — a misclassification must not make the control
        # surface lie about what is running.
        active = [
            ActiveFault(
                type=ft,
                severity=round(sev, 4),
                started_at=self.faults.started_at.get(ft, time.time()),
                predicted_source=diagnosis.predicted_source,
                classifier_explanation=[
                    ClassifierExplanation(**e) for e in diagnosis.explanation_dicts()
                ]
                or None,
                is_sensor_fault=False,
            )
            for ft, sev in self.faults.active().items()
        ]
        # Sensor faults appear in the same list, flagged, so the alert feed shows them
        # without needing a second channel.
        active += [
            ActiveFault(
                type=ft,
                severity=round(sev, 4),
                started_at=self.sensor_faults.started_at.get(ft, time.time()),
                predicted_source="sensor_fault",
                classifier_explanation=None,
                is_sensor_fault=True,
            )
            for ft, sev in self.sensor_faults.active().items()
        ]

        frame = TelemetryFrame(
            timestamp=time.time(),
            mission_phase=self.mission.phase,
            rpm=round(real_state.rpm, 1),
            manifold_pressure_kpa=round(real_state.manifold_pressure_kpa, 2),
            boost_pressure_kpa=round(real_state.boost_pressure_kpa, 2),
            cylinders=cylinders,
            cht_c=round(real_state.cht_c, 1),
            oil_temp_c=round(real_state.oil_temp_c, 1),
            oil_pressure_kpa=round(real_state.oil_pressure_kpa, 1),
            fuel_flow_lph=round(real_state.fuel_flow_lph, 2),
            altitude_m=round(self.mission.altitude_m, 1),
            airspeed_ms=round(self.mission.airspeed_ms, 1),
            health=HealthState(
                overall_score=round(anomaly.overall_health, 1),
                subsystem_scores=SubsystemScores(
                    **{k: round(v, 1) for k, v in anomaly.health_indicators.items()}
                ),
            ),
            rul_minutes=(
                round(rul_estimate.minutes, 1) if rul_estimate.minutes is not None else None
            ),
            mission_reliability=MissionReliability(
                score=round(reliability.score, 3),
                recommendation=reliability.recommendation,  # type: ignore[arg-type]
            ),
            recovery_reliability=RecoveryReliability(
                score=round(recovery_reliability.score, 3),
                recommendation=recovery_reliability.recommendation,  # type: ignore[arg-type]
            ),
            active_faults=active,
            # ---- Phase 3 fields ------------------------------------------
            battery_voltage_v=round(real_state.battery_voltage_v, 2),
            alternator_output_v=round(real_state.alternator_output_v, 2),
            injection_timing_deg=round(real_state.injection_timing_deg, 2),
            combustion_instability_pct=round(real_state.combustion_instability_pct, 2),
            ambient_temperature_c=round(
                self.ambient_temperature_c
                if self.ambient_temperature_c is not None
                else atmosphere(self.mission.altitude_m).temperature_c,
                1,
            ),
            bsfc_g_per_kwh=(
                round(efficiency.bsfc_g_per_kwh, 1)
                if efficiency.bsfc_g_per_kwh is not None
                else None
            ),
            efficiency_trend=efficiency.trend,  # type: ignore[arg-type]
            maintenance_advisories=[
                MaintenanceAdvisory(**a.to_dict()) for a in advisories
            ],
            is_replay=False,
            # ---- Phase 5: sensor fusion ------------------------------------
            fused_cht_c=round(cht_fusion_out.fused_cht_c, 1),
            cht_sensor_innovations=CHTSensorInnovations(
                primary=round(cht_fusion_out.innovation_primary_c, 2),
                secondary=round(cht_fusion_out.innovation_secondary_c, 2),
            ),
            fused_rpm=round(rpm_fusion_out.fused_rpm, 1),
            rpm_sensor_innovations=RPMSensorInnovations(
                tachometer=round(rpm_fusion_out.innovation_tachometer, 1),
                vibration_derived=round(rpm_fusion_out.innovation_vibration_derived, 1),
            ),
            fused_oil_pressure_kpa=round(oil_fusion_out.fused_oil_pressure_kpa, 1),
            oil_pressure_innovation=round(oil_fusion_out.innovation_kpa, 2),
            oil_pressure_kalman_gain=round(oil_fusion_out.kalman_gain, 4),
            early_warnings=[
                EarlyWarningModel(
                    **{
                        **w.to_dict(),
                        "predicted_minutes": (
                            round(w.predicted_minutes, 1)
                            if w.predicted_minutes is not None
                            else None
                        ),
                    }
                )
                for w in early_warnings
            ],
        )
        self._latest = frame
        return frame

    def get_latest(self) -> TelemetryFrame | None:
        return self._latest


async def run_simulation(app) -> None:
    """Background task: tick every registered UAV's simulation, persist frames for
    whichever ones are recording a mission, and broadcast each to its own subscribers.

    Phase 6 generalises this from one implicit engine to the fleet: `app.state.fleet`
    holds one independent `SimulationLoop` (+ `ReplayEngine`) per UAV, and this loop
    ticks all of them every 100 ms rather than a single `app.state.sim`. Nothing about
    how any *one* `SimulationLoop` ticks changes — the physics, PHM chain and fusion
    layer are exactly as they were for a single engine; this function is just N of them,
    each independently live/idle/replaying, persisted and broadcast on its own."""
    from app.api import ws_telemetry
    from app.core.fleet_registry import FleetRegistry
    from app.db.repository import repository

    fleet: FleetRegistry = app.state.fleet
    last = time.perf_counter()
    #: Absolute deadline for the next tick, so the period is `tick_seconds` rather than
    #: `tick_seconds + however long the tick itself took`. Sleeping a flat interval made
    #: the achieved rate sag below the configured one in proportion to the work per tick
    #: — measured at 7.5-7.9 Hz against a configured 10 Hz. The physics is unaffected
    #: either way (it integrates the *measured* `wall_dt`, not an assumed one), but the
    #: dashboard's frame rate is not, and "10 Hz" should mean 10 Hz.
    next_tick = last + settings.tick_seconds
    while True:
        await asyncio.sleep(max(0.0, next_tick - time.perf_counter()))
        now = time.perf_counter()
        # Skip missed deadlines outright rather than bursting to catch up: a backlog of
        # ticks would be replayed as fast as the loop could run them, which is the one
        # thing a real-time twin must never do.
        next_tick = max(now, next_tick + settings.tick_seconds)
        wall_dt = now - last
        last = now

        for entry in fleet:
            try:
                frame = entry.sim.tick(wall_dt)
            except Exception:
                logger.exception("Simulation tick failed for %s", entry.uav_id)
                continue

            payload = frame.model_dump()

            # Persistence happens only inside an explicit mission session. Ad-hoc
            # testing still streams live; it just is not recorded.
            if entry.sim.active_mission_id is not None:
                try:
                    repository.save_frame(entry.sim.active_mission_id, payload)
                except Exception:
                    logger.exception(
                        "Failed to persist telemetry frame for %s", entry.uav_id
                    )

            # The physics keeps running during a replay (so returning to live is
            # instant), but that UAV's own replay engine owns its socket while active.
            if entry.replay_engine.active:
                continue

            await ws_telemetry.manager.broadcast_to_uav(entry.uav_id, payload)
