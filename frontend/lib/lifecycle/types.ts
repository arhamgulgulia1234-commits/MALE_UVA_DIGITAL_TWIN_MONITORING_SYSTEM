/**
 * Phase 5 engine life-cycle contract. Mirrors backend/app/api/lifecycle.py and
 * backend/app/db/lifecycle_repository.py.
 *
 * Deliberately separate from lib/testbench/types.ts, for the same reason that file is
 * separate from lib/types.ts: this is neither live telemetry nor a hypothetical
 * what-if — it is the one view that looks *across* missions rather than into one, and
 * giving it its own contract means none of the other three ever have to widen to
 * accommodate it.
 */
import type { FaultType } from "@/lib/types";

/** fault_type -> value, always carrying every key in FAULT_TYPES (0 where inactive). */
export type WearState = Record<FaultType, number>;
export type FaultEventCounts = Record<FaultType, number>;

export interface MissionHealthPoint {
  mission_id: number;
  started_at: string | null;
  ended_at: string | null;
  profile_name: string;
  health_score: number;
  worst_subsystem: string | null;
}

export interface MaintenanceActionRow {
  id: number;
  fault_type: FaultType;
  description: string;
  performed_at: string | null;
  wear_reset_amount: number;
}

/** The bare ledger row — what `lifecycle_repository._to_dict()` returns. */
export interface EngineLifecycle {
  engine_id: string;
  total_operating_hours: number;
  current_wear_state: WearState;
  cumulative_fault_event_counts: FaultEventCounts;
  updated_at: string | null;
}

/** `GET /lifecycle/summary` — the ledger plus the cross-mission views built on top of it. */
export interface LifecycleSummary extends EngineLifecycle {
  mission_health_trend: MissionHealthPoint[];
  maintenance_actions: MaintenanceActionRow[];
  fault_types: FaultType[];
}

export interface MaintenanceActionRequest {
  fault_type: FaultType;
  description: string;
  reset_amount: number;
}

export interface MaintenanceActionResponse {
  ok: boolean;
  //: The bare ledger, not the full summary — matches what the endpoint actually
  //: returns. The caller re-fetches `/lifecycle/summary` for the trend and the action
  //: log rather than expecting this response to carry them.
  lifecycle: EngineLifecycle;
}
