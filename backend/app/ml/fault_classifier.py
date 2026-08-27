"""Phase 3 stub.

Will implement a supervised multi-class classifier over the fault types in
docs/physics-model.md's fault table, trained on labeled synthetic runs from the Phase 2
physics engine with app/physics/fault_models.py perturbations applied. Consumes the same
residual features as anomaly_detector.py and produces (fault_type, confidence, severity
estimate) tuples that will populate TelemetryFrame.active_faults in place of the ground-
truth fault state the mock/physics simulator currently reports directly.

Not implemented in Phase 1 — active_faults is populated directly from ControlDeck-
triggered fault injection state, not inferred from signals.
"""
