"""Read-only inspection of the digital-twin / PHM layer.

The TelemetryFrame contract is frozen (the frontend depends on it exactly as-is), so the
residuals, the twin's healthy reference values, the per-subsystem anomaly scores and the
fault classifier's prediction are exposed here instead of being forced into the WebSocket
schema. Useful for debugging the physics, for the validation script, and as a hook for a
future "diagnostics" panel in the UI.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/twin", tags=["diagnostics"])


@router.get("/diagnosis")
async def diagnosis(request: Request) -> dict:
    sim = request.app.state.sim
    diagnostics = getattr(sim, "diagnostics", None)
    if diagnostics is None:
        return {
            "physics_enabled": False,
            "detail": "Running the Phase 1 mock generator — no twin diagnostics available.",
        }

    return {
        "physics_enabled": True,
        "sim_time_s": round(sim.sim_time_s, 2),
        "throttle": round(sim.throttle, 3),
        "time_scale": sim.time_scale,
        "mission_phase": sim.mission.phase,
        "mission_remaining_s": round(sim.mission.remaining_seconds(), 1),
        "predicted_fault": diagnostics.predicted_fault,
        "prediction_confidence": round(diagnostics.prediction_confidence, 3),
        "classifier_available": diagnostics.classifier_available,
        # ---- Phase 3: explainability and source attribution ----------------
        "predicted_source": diagnostics.predicted_source,
        "source_rationale": diagnostics.source_rationale,
        "classifier_explanation": diagnostics.classifier_explanation,
        # ---- Phase 5: sensor fusion — which specific probe, and the raw signals
        # that named it. `fusion_values` includes `true_cht_c`, ground truth only
        # available here for the same reason `true_state_uncorrupted` is: never on the
        # telemetry stream, so the dashboard has to infer sensor-vs-physical honestly.
        "suspect_sensor": diagnostics.suspect_sensor,
        "fusion_z": {k: round(v, 3) for k, v in diagnostics.fusion_z.items()},
        "fusion_values": {k: round(v, 3) for k, v in diagnostics.fusion_values.items()},
        "bsfc_g_per_kwh": diagnostics.bsfc_g_per_kwh,
        "efficiency_trend": diagnostics.efficiency_trend,
        "ground_truth_faults": {
            k: round(v, 4) for k, v in sim.active_faults.items()
        },
        # Ground-truth sensor faults and the *uncorrupted* physical state. Exposed here
        # for validation and debugging only — never on the telemetry stream, because the
        # dashboard must infer sensor-vs-physical the way a real ground station would
        # rather than being handed the answer.
        "ground_truth_sensor_faults": {
            k: round(v, 4) for k, v in diagnostics.sensor_fault_truth.items() if v > 1e-4
        },
        "true_state_uncorrupted": {
            k: round(v, 3) for k, v in diagnostics.true_state.items()
        },
        "anomaly_scores": {
            k: round(v, 4) for k, v in diagnostics.anomaly_scores.items()
        },
        "flagged_channels": diagnostics.flagged_channels,
        "rul_subsystem": diagnostics.rul_subsystem,
        "rul_model": diagnostics.rul_model,
        "residual_z": {k: round(v, 3) for k, v in diagnostics.residual_z.items()},
        "residual_mean": {k: round(v, 4) for k, v in diagnostics.residual_mean.items()},
        "real": {k: round(v, 3) for k, v in diagnostics.real_channels.items()},
        "twin": {k: round(v, 3) for k, v in diagnostics.twin_channels.items()},
    }
