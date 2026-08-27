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
        "ground_truth_faults": {
            k: round(v, 4) for k, v in sim.active_faults.items()
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
