"""Phase 2 stub.

Will implement fault injection as *parameter perturbation* rather than direct signal
scripting: each fault type in docs/physics-model.md's fault table maps to a perturbation
of one or more physical parameters (e.g. bearing_wear -> increased bearing clearance in
lubrication_model.py; misfire -> zeroed combustion efficiency on a cylinder in
engine_model.py for randomly-dropped cycles; turbo_wear -> reduced compressor efficiency
in turbo_model.py). This is the key architectural change from Phase 1: faults propagate
through the physics models instead of directly overriding output signals, so their
visible effects emerge from the simulation rather than being hand-scripted ramps.

Not implemented in Phase 1 — app/sim/simulation_loop.py directly ramps output signals
and health scores per active fault instead of perturbing model parameters.
"""
