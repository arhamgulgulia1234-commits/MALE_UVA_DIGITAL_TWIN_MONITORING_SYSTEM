/**
 * Shared telemetry data contract. Mirrors backend/app/core/models.py exactly — this is
 * the shape everything speaks, whether the source is the Phase 1 mock generator or the
 * Phase 2 physics engine.
 */

export type MissionPhase = "climb" | "cruise" | "loiter" | "descent";

export type FaultType =
  | "misfire"
  | "spark_degradation"
  | "piston_ring_wear"
  | "bearing_wear"
  | "oil_pump_degradation"
  | "cooling_degradation"
  | "fuel_injector_clog"
  | "turbo_wear"
  | "air_filter_clog";

export type Recommendation = "GO" | "CAUTION" | "NO-GO";

export interface CylinderReading {
  id: number;
  egt_c: number;
  vibration_rms: number;
}

export interface SubsystemScores {
  cylinder: number;
  lubrication: number;
  cooling: number;
  fuel: number;
  turbo: number;
}

export interface HealthState {
  overall_score: number;
  subsystem_scores: SubsystemScores;
}

export interface MissionReliability {
  score: number;
  recommendation: Recommendation;
}

export interface ActiveFault {
  type: FaultType;
  severity: number;
  started_at: number;
}

export interface TelemetryFrame {
  timestamp: number;
  mission_phase: MissionPhase;
  rpm: number;
  manifold_pressure_kpa: number;
  boost_pressure_kpa: number;
  cylinders: CylinderReading[];
  cht_c: number;
  oil_temp_c: number;
  oil_pressure_kpa: number;
  fuel_flow_lph: number;
  altitude_m: number;
  airspeed_ms: number;
  health: HealthState;
  rul_minutes: number | null;
  mission_reliability: MissionReliability;
  active_faults: ActiveFault[];
}

/** Metadata for the fault-injection UI — mirrors docs/physics-model.md's fault table. */
export interface FaultMeta {
  type: FaultType;
  label: string;
  description: string;
  subsystem: keyof SubsystemScores | "multiple";
}

export const FAULT_CATALOG: FaultMeta[] = [
  {
    type: "misfire",
    label: "Misfire",
    description: "Intermittent cylinder combustion failure",
    subsystem: "cylinder",
  },
  {
    type: "spark_degradation",
    label: "Spark Degradation",
    description: "Weak/fouled spark, incomplete combustion",
    subsystem: "cylinder",
  },
  {
    type: "piston_ring_wear",
    label: "Piston Ring Wear",
    description: "Blow-by past worn rings",
    subsystem: "multiple",
  },
  {
    type: "bearing_wear",
    label: "Bearing Wear",
    description: "Main/rod bearing degradation",
    subsystem: "lubrication",
  },
  {
    type: "oil_pump_degradation",
    label: "Oil Pump Degradation",
    description: "Reduced lubrication flow/pressure",
    subsystem: "lubrication",
  },
  {
    type: "cooling_degradation",
    label: "Cooling Degradation",
    description: "Reduced cooling airflow / coolant flow",
    subsystem: "cooling",
  },
  {
    type: "fuel_injector_clog",
    label: "Fuel Injector Clog",
    description: "Partial injector blockage on a cylinder",
    subsystem: "fuel",
  },
  {
    type: "turbo_wear",
    label: "Turbo Wear",
    description: "Compressor/turbine wheel wear, seal leakage",
    subsystem: "turbo",
  },
  {
    type: "air_filter_clog",
    label: "Air Filter Clog",
    description: "Progressive intake restriction",
    subsystem: "multiple",
  },
];

export const MISSION_PHASES: MissionPhase[] = ["climb", "cruise", "loiter", "descent"];

/** Healthy operating bands used to decide tile/border coloring in the vitals grid. */
export const HEALTHY_BANDS = {
  rpm: { min: 1800, max: 5700 },
  egt_c: { min: 400, max: 850 },
  cht_c: { min: 100, max: 230 },
  oil_pressure_kpa: { min: 250, max: 450 },
  oil_temp_c: { min: 60, max: 115 },
  fuel_flow_lph: { min: 3, max: 34 },
  boost_pressure_kpa: { min: 40, max: 160 },
  manifold_pressure_kpa: { min: 30, max: 105 },
  vibration_rms: { min: 0, max: 0.45 },
} as const;
