"""Phase 2 stub.

Will implement the ISA (International Standard Atmosphere) model for ambient pressure,
temperature, and air density as a function of altitude_m, plus airspeed-driven ram-air/
cooling-airflow effects. Provides boundary conditions consumed by turbo_model.py
(inlet density), thermal_model.py (cooling airflow, ambient temp), and engine_model.py
(air density → volumetric efficiency / power correction for the mission's altitude
profile).

Not implemented in Phase 1 — the mock generator ties altitude_m and airspeed_ms directly
to the mission-phase state machine without a density/atmosphere model.
"""
