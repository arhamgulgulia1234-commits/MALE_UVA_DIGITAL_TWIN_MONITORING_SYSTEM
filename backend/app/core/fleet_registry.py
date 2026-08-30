"""Phase 6: the fleet registry — N independently simulated UAVs.

Every UAV gets its own `SimulationLoop` (its own `EnginePlant`, `DigitalTwin`,
`FaultState`, fusion filters, classifier, the lot) and its own `ReplayEngine`, so one
UAV's mission, fault injection or replay session can never leak into another's. This
module does not change `SimulationLoop` itself — Phase 2 through Phase 5 all still work
exactly as they did for a single engine; a `FleetEntry` is just N of them, ticked in a
loop by `app/sim/simulation_loop.py::run_simulation`.

Continuity with everything built before this phase matters: the very first UAV,
`DEFAULT_UAV_ID` ("UAV-01"), is deliberately backed by the *same* `engine_lifecycle` row
the single-engine Lifecycle feature already writes to (`engine_id="primary"`), and the
`missions` table's new `uav_id` column defaults to "UAV-01" for every pre-existing row
(see app/db/session.py's `_ensure_column` migration). So a user who was already running
missions before this phase sees UAV-01 pick up exactly where "the engine" left off —
same accumulated hours, same wear, same mission history — rather than starting over
under a new key. UAV-02 and UAV-03 are genuinely new engines with their own fresh
`engine_lifecycle` rows, keyed by their own uav_id.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.uav_ids import DEFAULT_UAV_ID, UAV_IDS
from app.db.lifecycle_repository import lifecycle_repository
from app.db.models import DEFAULT_ENGINE_ID
from app.sim.replay_engine import ReplayEngine
from app.sim.simulation_loop import SimulationLoop

logger = logging.getLogger(__name__)

#: Distinct starting wear per UAV, applied once — the very first time each UAV's
#: `engine_lifecycle` row is created — so the fleet looks like a real squadron the
#: moment the backend starts, before any operator has flown a single mission. Chosen to
#: make `/fleet/rankings` demonstrate a real spread on first boot:
#:   UAV-01 — near-pristine (reuses the pre-existing "primary" lifecycle row, which
#:            starts at zero wear on a fresh database, same as it always has).
#:   UAV-02 — moderate, spread across a few subsystems (a normal mid-life engine).
#:   UAV-03 — light on most fronts but deliberately close to a maintenance threshold
#:            on bearing wear, which shows up across several channels at once
#:            (vibration, oil pressure, RPM stability), so it reliably surfaces as
#:            the most urgent UAV without needing a mission run first.
_SEED_WEAR_STATES: dict[str, dict[str, float]] = {
    "UAV-02": {
        "piston_ring_wear": 0.22,
        "turbo_wear": 0.18,
        "air_filter_clog": 0.15,
    },
    "UAV-03": {
        "bearing_wear": 0.62,
        "oil_pump_degradation": 0.20,
    },
}


def lifecycle_engine_id(uav_id: str) -> str:
    """The `engine_lifecycle`/`maintenance_actions` key for a given UAV.

    UAV-01 maps onto the pre-existing `DEFAULT_ENGINE_ID` ("primary") for continuity
    with data recorded before this phase; every other UAV is keyed by its own id."""
    return DEFAULT_ENGINE_ID if uav_id == DEFAULT_UAV_ID else uav_id


@dataclass
class FleetEntry:
    uav_id: str
    sim: SimulationLoop
    replay_engine: ReplayEngine = field(default_factory=ReplayEngine)

    @property
    def status(self) -> str:
        """"live" while a mission is recording, "replay" while this UAV's own replay
        engine owns its socket, "idle" otherwise. Derived rather than tracked
        separately, so it can never drift out of sync with the two flags it reflects."""
        if self.replay_engine.active:
            return "replay"
        if self.sim.active_mission_id is not None:
            return "live"
        return "idle"


class FleetRegistry:
    """Holds one `FleetEntry` per UAV. Iterated once per tick by `run_simulation`."""

    def __init__(self, uav_ids: list[str] | None = None) -> None:
        self.uav_ids: list[str] = list(uav_ids) if uav_ids is not None else list(UAV_IDS)
        self.entries: dict[str, FleetEntry] = {
            uav_id: FleetEntry(uav_id=uav_id, sim=SimulationLoop()) for uav_id in self.uav_ids
        }

    def get(self, uav_id: str) -> FleetEntry:
        entry = self.entries.get(uav_id)
        if entry is None:
            raise KeyError(f"unknown UAV id: {uav_id!r} (known: {self.uav_ids})")
        return entry

    def __iter__(self):
        return iter(self.entries.values())

    @property
    def default(self) -> FleetEntry:
        return self.entries[DEFAULT_UAV_ID]


def _is_fresh_lifecycle(lifecycle: dict) -> bool:
    """True if this `engine_lifecycle` row has never recorded any hours or wear —
    i.e. it was just created by `_get_or_create`, not carried forward from real
    missions. Seeding only fires on a fresh row, so a server restart never re-applies
    the Task-1 starting wear on top of wear the engine has genuinely accumulated
    since."""
    if lifecycle["total_operating_hours"] > 0:
        return False
    return not any(v > 1e-6 for v in lifecycle["current_wear_state"].values())


def seed_fleet_wear(registry: FleetRegistry) -> None:
    """Apply each UAV's pre-seeded starting wear (first boot only) and load whatever
    wear is now persisted — seeded or genuinely accumulated — into that UAV's live
    `FaultState`, exactly as `POST /control/mission/start` already does for one engine.

    Called once at startup, before the simulation loop starts ticking, so
    `/fleet/overview` and `/fleet/rankings` show a realistic, differentiated fleet
    immediately — without the operator needing to fly a mission on each UAV first.
    """
    for uav_id, entry in registry.entries.items():
        engine_id = lifecycle_engine_id(uav_id)
        lifecycle = lifecycle_repository.get_current_lifecycle(engine_id=engine_id)
        seed = _SEED_WEAR_STATES.get(uav_id)
        if seed and _is_fresh_lifecycle(lifecycle):
            lifecycle = lifecycle_repository.set_wear_state(seed, engine_id=engine_id)
            logger.info("Seeded starting wear for %s (engine_id=%s): %s", uav_id, engine_id, seed)
        entry.sim.seed_fault_state_from_wear(lifecycle["current_wear_state"])


def build_default_fleet() -> FleetRegistry:
    registry = FleetRegistry(UAV_IDS)
    seed_fleet_wear(registry)
    return registry
