"""Phase 2 stub.

Will compute residuals between physics-model predicted signals (the "healthy twin"
expectation) and the actual/perturbed simulated signals once faults are injected via
app/physics/fault_models.py. These residuals are the feature input to
app/ml/anomaly_detector.py and app/ml/fault_classifier.py in Phase 3 — the digital-twin
pattern is specifically: run a healthy-baseline physics model in parallel with the
"true" (possibly faulted) physics model, and flag/classify/quantify divergence between
them, rather than inferring health from raw signals alone.

Not implemented in Phase 1 — app/sim/simulation_loop.py derives health scores directly
from active fault severity rather than from residuals against a baseline model.
"""
