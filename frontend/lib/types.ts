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
  | "air_filter_clog"
  // --- Phase 3 additions ---
  | "battery_alternator_degradation"
  | "injection_timing_drift";

/**
 * Phase 3: faults that corrupt what a *sensor reports* rather than the engine itself.
 * Injected through a separate backend pipeline and applied after the physics and after
 * the digital twin, so residuals still show an anomaly while the engine stays healthy.
 */
export type SensorFaultType =
  | "egt_sensor_drift"
  | "oil_pressure_sensor_noise"
  | "rpm_sensor_stuck";

export type Recommendation = "GO" | "CAUTION" | "NO-GO";

export type Urgency = "monitor" | "schedule_soon" | "immediate";

export type EfficiencyTrend = "stable" | "degrading" | "improving";

export type PredictedSource = "physical_fault" | "sensor_fault" | "uncertain";

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
  /** Phase 3 addition — optional so existing consumers are unaffected. */
  electrical?: number | null;
}

export interface HealthState {
  overall_score: number;
  subsystem_scores: SubsystemScores;
}

export interface MissionReliability {
  score: number;
  recommendation: Recommendation;
}

export interface ClassifierExplanation {
  feature: string;
  importance: number;
  residual_value: number;
}

export interface ActiveFault {
  /** Widened in Phase 3 to also carry SensorFaultType values. */
  type: FaultType | SensorFaultType | string;
  severity: number;
  started_at: number;
  // --- Phase 3 additions ---
  predicted_source?: PredictedSource | null;
  classifier_explanation?: ClassifierExplanation[] | null;
  is_sensor_fault?: boolean;
}

export interface MaintenanceAdvisory {
  subsystem: string;
  urgency: Urgency;
  recommendation: string;
  basis: string[];
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

  // ---- Phase 3 additions -------------------------------------------------
  // All optional, so every Phase 1/2 component keeps compiling and rendering
  // against this type without modification.

  /** Electrical subsystem. */
  battery_voltage_v?: number | null;
  alternator_output_v?: number | null;

  /** Commanded injection timing, crank degrees before TDC. */
  injection_timing_deg?: number | null;

  /** Cycle-to-cycle IMEP coefficient of variation — a misfire leading indicator. */
  combustion_instability_pct?: number | null;

  /** Ambient air temperature, independent of the ISA altitude relation. */
  ambient_temperature_c?: number | null;

  /** Brake specific fuel consumption and its rolling trend. */
  bsfc_g_per_kwh?: number | null;
  efficiency_trend?: EfficiencyTrend | null;

  maintenance_advisories?: MaintenanceAdvisory[];

  /** True when frames are replayed from a stored mission rather than generated live. */
  is_replay?: boolean;
  replay_mission_id?: number | null;
}

/** A recorded mission, as listed by GET /control/missions. */
export interface MissionSummary {
  id: number;
  started_at: string | null;
  ended_at: string | null;
  mission_profile_name: string;
  notes: string | null;
  has_report: boolean;
  frame_count: number;
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
  // --- Phase 3 additions ---
  {
    type: "battery_alternator_degradation",
    label: "Alternator Degradation",
    description: "Alternator output falls, battery internal resistance rises",
    subsystem: "multiple",
  },
  {
    type: "injection_timing_drift",
    label: "Injection Timing Drift",
    description: "Timing wanders off nominal — burns late, EGT rises",
    subsystem: "cylinder",
  },
];

export interface SensorFaultMeta {
  type: SensorFaultType;
  label: string;
  description: string;
  channel: string;
}

/**
 * Sensor faults corrupt the *reading*, not the engine. Injecting one is the sharpest
 * demonstration of why the digital twin matters: the dashboard shows an anomaly, but the
 * engine underneath is genuinely healthy, and the system has to work that out from the
 * correlation structure of the residuals alone.
 */
export const SENSOR_FAULT_CATALOG: SensorFaultMeta[] = [
  {
    type: "egt_sensor_drift",
    label: "EGT Probe Drift",
    description: "Thermocouple reads progressively high — combustion is fine",
    channel: "EGT",
  },
  {
    type: "oil_pressure_sensor_noise",
    label: "Oil Press. Sensor Noise",
    description: "Excess noise and dropouts on an otherwise good transducer",
    channel: "Oil pressure",
  },
  {
    type: "rpm_sensor_stuck",
    label: "RPM Sensor Stuck",
    description: "Tachometer reading freezes while the engine keeps changing speed",
    channel: "RPM",
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
  // --- Phase 3 additions ---
  battery_voltage_v: { min: 12.0, max: 14.6 },
  alternator_output_v: { min: 12.5, max: 14.6 },
  combustion_instability_pct: { min: 0, max: 6 },
  bsfc_g_per_kwh: { min: 200, max: 400 },
} as const;
