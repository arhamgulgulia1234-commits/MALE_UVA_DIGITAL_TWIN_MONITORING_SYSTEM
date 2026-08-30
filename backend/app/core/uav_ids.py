"""Phase 6: the fleet's UAV identifiers.

Split out from `fleet_registry.py` so low-level modules (the DB repository, the
lifecycle repository callers) can depend on the *identifiers* without pulling in
`fleet_registry`'s heavier imports (SimulationLoop, ThermalModel, etc.), which would
risk a circular import — `fleet_registry` itself needs the repository.
"""
from __future__ import annotations

DEFAULT_UAV_ID = "UAV-01"

UAV_IDS: list[str] = ["UAV-01", "UAV-02", "UAV-03"]
