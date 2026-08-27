"""Phase 2 stub.

Will become the orchestration layer that replaces app/sim/simulation_loop.py as the
tick-loop driver: each tick, it will call into app/physics/* (engine_model, thermal_model,
lubrication_model, vibration_model, turbo_model, environment, fault_models) to advance the
physical state, then hand the resulting signals to residual_analysis.py and app/ml/* to
derive health scores, active faults, RUL, and mission reliability — producing the same
TelemetryFrame contract the mock generator produces today, so app/api/ws_telemetry.py does
not need to change when this lands. Should expose the same start/stop/get_state interface
as app/sim/simulation_loop.SimulationLoop so app/main.py can swap implementations behind a
config flag.

Not implemented in Phase 1 — app/sim/simulation_loop.py is the active driver.
"""
