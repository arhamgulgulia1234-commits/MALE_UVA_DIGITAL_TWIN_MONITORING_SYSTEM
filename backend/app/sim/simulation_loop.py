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
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.engine_params import PARAMS, EngineParams
from app.core.models import (
    ActiveFault,
    ClassifierExplanation,
    CylinderReading,
    HealthState,
    MaintenanceAdvisory,
    MissionReliability,
    SubsystemScores,
    TelemetryFrame,
)
from app.ml.anomaly_detector import AnomalyDetector, AnomalyReport
from app.ml.efficiency_analysis import EfficiencyAnalyser
from app.ml.fault_classifier import Diagnosis, FaultClassifier
from app.ml.maintenance_advisor import MaintenanceAdvisor
from app.ml.mission_reliability import MissionReliabilityModel
from app.ml.rul_predictor import RULPredictor
from app.physics.environment import atmosphere
from app.physics.fault_models import FaultState
from app.physics.plant import EnginePlant
from app.physics.sensor_fault_model import SensorFaultModel, SensorFaultState
from app.sim.mission_profiles import MissionProfile, apply_scenario
from app.twin.digital_twin import DigitalTwin
from app.twin.residual_analysis import ResidualMonitor

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
        self.anomaly = AnomalyDetector()
        self.rul = RULPredictor()
        self.reliability = MissionReliabilityModel()
        self.classifier = FaultClassifier()
        self.classifier.set_feature_names(self.residuals.feature_names())

        # ---- Phase 3 --------------------------------------------------------
        self.sensor_faults = SensorFaultState()
        self.sensor_model = SensorFaultModel(params)
        self.efficiency = EfficiencyAnalyser(
            fuel_density_kg_per_l=params.fuel_density_kg_per_l
        )
        self.advisor = MaintenanceAdvisor()
        #: None means "use the ISA temperature for the current altitude".
        self.ambient_temperature_c: float | None = None
        self.active_mission_id: int | None = None
        self._true_state_snapshot: dict[str, float] = {}

        self.sim_time_s: float = 0.0
        self.time_scale: float = 1.0
        self.manual_throttle: float | None = None
        self.throttle: float = self.mission.commanded_throttle()

        self._latest: TelemetryFrame | None = None
        self.diagnostics = DiagnosticSnapshot()

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
        real_state = self.plant.finalise_tick()

        # Phase 3: sensor faults corrupt the *reported* values, and they are applied here
        # — after the physics has produced the true state and before the residual is
        # taken. The twin is untouched, so the residual still shows an anomaly even
        # though the engine is fine. That is exactly the situation the disambiguation
        # logic in the classifier exists to resolve.
        self._true_state_snapshot = real_state.channels()
        if self.sensor_faults.any_active():
            self.sensor_model.apply(real_state, self.sensor_faults)

        comparison = self.twin.compare(real_state)
        report = self.residuals.update(comparison.residuals, dt_s=sim_dt_total)
        anomaly: AnomalyReport = self.anomaly.update(report, dt_s=sim_dt_total)
        rul_estimate = self.rul.update(self.sim_time_s, anomaly.health_indicators)
        reliability = self.reliability.evaluate(
            rul_minutes=rul_estimate.minutes,
            mission_remaining_s=self.mission.remaining_seconds(),
            overall_health=anomaly.overall_health,
        )

        residual_z = {c: report.z(c) for c in report.stats}
        diagnosis: Diagnosis = self.classifier.predict(
            self.residuals.feature_vector(), residual_z=residual_z
        )

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
        )
        self._latest = frame
        return frame

    def get_latest(self) -> TelemetryFrame | None:
        return self._latest


async def run_simulation(app) -> None:
    """Background task: tick the simulation, persist frames if a mission is recording,
    and broadcast at the configured rate."""
    from app.api import ws_telemetry
    from app.db.repository import repository
    from app.sim.replay_engine import replay_engine

    sim: SimulationLoop = app.state.sim
    last = time.perf_counter()
    while True:
        await asyncio.sleep(settings.tick_seconds)
        now = time.perf_counter()
        wall_dt = now - last
        last = now
        try:
            frame = sim.tick(wall_dt)
        except Exception:
            logger.exception("Simulation tick failed")
            continue

        payload = frame.model_dump()

        # Persistence happens only inside an explicit mission session. Ad-hoc testing
        # still streams live; it just is not recorded.
        if sim.active_mission_id is not None:
            try:
                repository.save_frame(sim.active_mission_id, payload)
            except Exception:
                logger.exception("Failed to persist telemetry frame")

        # The physics keeps running during a replay (so returning to live is instant),
        # but the replay engine owns the socket while it is active.
        if replay_engine.active:
            continue

        await ws_telemetry.manager.broadcast_json(payload)
