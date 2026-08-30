import type { Recommendation, RecoveryRecommendation } from "../types";

/** The fixed fleet roster. Mirrors backend/app/core/uav_ids.py::UAV_IDS. */
export const UAV_IDS = ["UAV-01", "UAV-02", "UAV-03"] as const;
export type UavId = (typeof UAV_IDS)[number];
export const DEFAULT_UAV_ID: UavId = "UAV-01";

/** One row of GET /fleet/overview or /fleet/rankings. */
export interface FleetOverviewEntry {
  uav_id: string;
  status: "live" | "idle" | "replay";
  overall_health: number | null;
  worst_subsystem: string | null;
  worst_subsystem_score: number | null;
  rul_minutes: number | null;
  mission_reliability_recommendation: Recommendation | null;
  recovery_reliability_recommendation: RecoveryRecommendation | null;
  active_fault_count: number;
  total_operating_hours: number;
}
