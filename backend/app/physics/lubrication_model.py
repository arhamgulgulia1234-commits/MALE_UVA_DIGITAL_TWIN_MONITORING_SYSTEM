"""Phase 2 stub.

Will implement oil-system hydraulics and bearing lubrication: oil pump flow vs. RPM,
oil-pressure regulation, oil-temperature balance (heat picked up from bearings/pistons vs.
cooler rejection), and a simplified Reynolds hydrodynamic-film-thickness estimate for main/
rod bearings as a function of oil viscosity (temperature-dependent), clearance, and load.
Bearing clearance and oil-pump efficiency are the parameters fault_models.py will perturb
for bearing_wear, oil_pump_degradation, and piston_ring_wear (indirectly, via blow-by
contamination assumptions).

Not implemented in Phase 1 — the mock generator ramps oil_pressure_kpa / oil_temp_c
directly based on active fault severity instead of modeling the oil system.
"""
