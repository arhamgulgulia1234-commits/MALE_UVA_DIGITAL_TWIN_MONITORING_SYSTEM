"""Phase 3 stub.

Will implement remaining-useful-life (RUL) regression, likely a survival-analysis or
sequence model (e.g. LSTM/temporal CNN over the rolling telemetry window) trained on
run-to-failure synthetic trajectories from the Phase 2 physics engine, conditioned on the
active fault type/severity from fault_classifier.py. Replaces the mock generator's
linear-decay rul_minutes heuristic with a learned, fault-type-aware estimate.

Not implemented in Phase 1 — app/sim/simulation_loop.py computes rul_minutes with a
simple linear decay keyed to the fastest-degrading active fault's severity slope.
"""
