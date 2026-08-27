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
    CylinderReading,
    HealthState,
    MissionReliability,
    SubsystemScores,
    TelemetryFrame,
)
from app.ml.anomaly_detector import AnomalyDetector, AnomalyReport
from app.ml.fault_classifier import Diagnosis, FaultClassifier
from app.ml.mission_reliability import MissionReliabilityModel
from app.ml.rul_predictor import RULPredictor
from app.physics.fault_models import FaultState
from app.physics.plant import EnginePlant
from app.sim.mission_profiles import MissionProfile
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

    # ---- main tick -----------------------------------------------------------

    def tick(self, wall_dt_s: float) -> TelemetryFrame:
        sim_dt_total = max(1e-4, wall_dt_s) * self.time_scale
        n_substeps = max(1, int(round(sim_dt_total / INTERNAL_DT_S)))
        dt = sim_dt_total / n_substeps

        for _ in range(n_substeps):
            self.faults.step(dt)
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

            self.plant.substep(dt, throttle, altitude, airspeed, self.faults)
            self.twin.substep(dt, throttle, altitude, airspeed)

        # ---- once-per-tick PHM chain ----------------------------------------
        real_state = self.plant.finalise_tick()
        comparison = self.twin.compare(real_state)
        report = self.residuals.update(comparison.residuals, dt_s=sim_dt_total)
        anomaly: AnomalyReport = self.anomaly.update(report, dt_s=sim_dt_total)
        rul_estimate = self.rul.update(self.sim_time_s, anomaly.health_indicators)
        reliability = self.reliability.evaluate(
            rul_minutes=rul_estimate.minutes,
            mission_remaining_s=self.mission.remaining_seconds(),
            overall_health=anomaly.overall_health,
        )

        diagnosis: Diagnosis = self.classifier.predict(self.residuals.feature_vector())

        self.diagnostics = DiagnosticSnapshot(
            residual_z={c: report.z(c) for c in report.stats},
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

        active = [
            ActiveFault(
                type=ft,  # type: ignore[arg-type]
                severity=round(sev, 4),
                started_at=self.faults.started_at.get(ft, time.time()),
            )
            for ft, sev in self.faults.active().items()
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
        )
        self._latest = frame
        return frame

    def get_latest(self) -> TelemetryFrame | None:
        return self._latest


async def run_simulation(app) -> None:
    """Background task: tick the simulation and broadcast frames at the configured rate."""
    from app.api import ws_telemetry

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
        await ws_telemetry.manager.broadcast_json(frame.model_dump())
