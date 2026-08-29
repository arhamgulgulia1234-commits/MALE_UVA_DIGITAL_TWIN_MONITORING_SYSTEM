/**
 * Phase 4 Test Bench contract. Mirrors backend/app/api/scenario.py and
 * backend/app/api/optimizer.py.
 *
 * Deliberately a separate file from lib/types.ts. That file is the *live telemetry*
 * contract shared with the WebSocket, and the Test Bench must not be able to widen it by
 * accident — a scenario is hypothetical, and nothing here should ever end up flowing
 * through the live path. The one thing the two share is `TelemetryFrame`: a scenario
 * returns ordinary frames, which is what lets the Test Bench render them with the same
 * chart components the dashboard uses.
 */
import type { MaintenanceAdvisory, Recommendation, TelemetryFrame } from "@/lib/types";

export type Verdict = "PASS" | "CAUTION" | "FAIL";

export type OptimizerObjective =
  | "max_range"
  | "max_power"
  | "max_engine_life"
  | "balanced";

export type PresetName = "max_endurance" | "balanced" | "max_power";

// ---- scenario requests -------------------------------------------------------

export interface ThrottleWaypoint {
  time_min: number;
  /** Percent, 0-100 — the same unit a constant `throttle_profile` uses. */
  throttle_pct: number;
}

export interface ScheduledFault {
  fault_type: string;
  severity: number;
  at_time_min: number;
  ramp_minutes: number;
}

export interface ScenarioRequest {
  altitude_m: number;
  /** null = follow the ISA temperature for this altitude (standard day). */
  ambient_temperature_c: number | null;
  duration_minutes: number;
  throttle_profile: number | ThrottleWaypoint[];
  initial_fault_severities: Record<string, number>;
  injected_faults_during_scenario: ScheduledFault[];
  label?: string | null;
  save?: boolean;
  include_frames?: boolean;
}

// ---- scenario results --------------------------------------------------------

export interface LimitExcursion {
  parameter: string;
  band: "limit" | "caution";
  limit: number;
  unit: string;
  peak_value: number;
  first_at_min: number;
  duration_min: number;
  direction: "above" | "below";
}

export interface ReliabilityPoint {
  time_min: number;
  score: number;
  recommendation: Recommendation;
}

export interface ScenarioSummary {
  verdict: Verdict;
  headline: string;
  stayed_within_safe_health: boolean;
  stayed_within_operating_limits: boolean;
  health_safe_threshold: number;
  min_health_score: number;
  min_health_at_min: number;
  final_health_score: number;
  final_rul_minutes: number | null;
  min_rul_minutes: number | null;
  worst_subsystem: string;
  worst_subsystem_score: number;
  final_subsystem_scores: Record<string, number>;
  final_recommendation: Recommendation;
  worst_recommendation: Recommendation;
  mission_reliability_trajectory: ReliabilityPoint[];
  limit_excursions: LimitExcursion[];
  caution_excursions: LimitExcursion[];
  peak_cht_c: number;
  peak_egt_c: number;
  min_oil_pressure_kpa: number;
  peak_oil_temp_c: number;
  mean_power_kw: number;
  mean_bsfc_g_per_kwh: number | null;
  total_fuel_litres: number;
  fuel_burn_lph_mean: number;
  final_advisories: MaintenanceAdvisory[];
}

export interface ScenarioResult {
  params: Record<string, unknown>;
  frames: TelemetryFrame[];
  frame_count: number;
  summary: ScenarioSummary;
  integration_dt_s: number;
  sample_interval_s: number;
  simulated_seconds: number;
  compute_seconds: number;
  notes: string[];
  scenario_run_id: number | null;
  is_simulation: true;
}

export interface ScenarioRunRow {
  id: number;
  created_at: string | null;
  label: string | null;
  altitude_m: number;
  ambient_temperature_c: number | null;
  duration_minutes: number;
  verdict: Verdict;
  min_health_score: number | null;
  final_rul_minutes: number | null;
  worst_subsystem: string | null;
  compute_seconds: number | null;
  headline: string | null;
  params: Record<string, unknown>;
  summary?: ScenarioSummary;
}

export interface ScenarioEnvelope {
  altitude_m: { min: number; max: number };
  ambient_temperature_c: { min: number; max: number };
  isa_deviation_k: { min: number; max: number };
  duration_minutes: { min: number; max: number };
  throttle_pct: { min: number; max: number };
  fault_types: string[];
  operating_limits: Record<string, number>;
  health_safe_threshold: number;
}

// ---- optimizer ---------------------------------------------------------------

export interface SafetyCheck {
  parameter: string;
  label: string;
  value: number;
  limit: number;
  unit: string;
  direction: "max" | "min";
  ok: boolean;
  margin: number;
}

export interface SetpointPredicted {
  power_kw: number;
  bsfc_g_per_kwh: number | null;
  fuel_flow_lph: number;
  cht_c: number;
  egt_max_c: number;
  oil_temp_c: number;
  oil_pressure_kpa: number;
  rpm: number;
  afr_mean: number;
  injection_timing_deg: number;
  vibration_rms_g: number;
}

export interface Setpoint {
  throttle_pct: number;
  afr_trim: number;
  injection_timing_trim_deg: number;
}

export interface SetpointEvaluation {
  setpoint: Setpoint;
  predicted: SetpointPredicted;
  stress_rate: number;
  stress_breakdown: Record<string, number>;
  estimated_life_hours: number;
  safety: SafetyCheck[];
  feasible: boolean;
}

/** Sign convention: **positive is better** for whatever the field names. */
export interface OperatingPointComparison {
  power_pct: number;
  bsfc_pct: number;
  range_equivalent_pct: number;
  stress_rate_pct: number;
  estimated_rul_impact_pct: number;
  life_hours_baseline: number;
  life_hours_recommended: number;
  cht_delta_c: number;
  egt_delta_c: number;
  oil_pressure_delta_kpa: number;
  fuel_flow_delta_lph: number;
}

export interface OperatingPointResult {
  objective: OptimizerObjective;
  objective_label: string;
  conditions: {
    altitude_m: number;
    ambient_temperature_c: number | null;
    airspeed_ms: number;
  };
  feasible: boolean;
  recommended: SetpointEvaluation;
  baseline: SetpointEvaluation;
  comparison: OperatingPointComparison;
  health: {
    severity_index: number;
    faults: Record<string, number>;
    effective_limits: Record<string, number>;
  };
  constraints: Record<string, number>;
  safety_notes: string[];
  rationale: string;
  evaluations: number;
  compute_seconds: number;
  notes: string[];
  health_source: string;
  used_current_engine_health: boolean;
}

// ---- presets -----------------------------------------------------------------

export interface PresetCard {
  name: PresetName;
  label: string;
  objective: OptimizerObjective;
  description: string;
  trade_off: string;
  reference_conditions: { altitude_m: number; ambient_temperature_c: number };
  setpoint: Setpoint & { throttle: number };
  predicted: SetpointPredicted;
  deltas: OperatingPointComparison;
  safety: SafetyCheck[];
  feasible: boolean;
  safety_notes: string[];
  rationale: string;
}

export interface PresetsResponse {
  presets: PresetCard[];
  reference: {
    altitude_m: number;
    ambient_temperature_c: number;
    nominal_cruise_throttle_pct: number;
    presets: PresetName[];
  };
}

/** A 400 from either Phase 4 endpoint carries reasons, not just a message. */
export interface ValidityError {
  error: string;
  reasons: string[];
  envelope?: ScenarioEnvelope;
  objectives?: string[];
}

// ---- throttle-profile builder ------------------------------------------------

export type ThrottleMode = "constant" | "ramp";

export interface ThrottleProfileDraft {
  mode: ThrottleMode;
  constant_pct: number;
  ramp_from_pct: number;
  ramp_to_pct: number;
  ramp_over_min: number;
}

export function draftToProfile(
  draft: ThrottleProfileDraft,
  durationMinutes: number
): number | ThrottleWaypoint[] {
  if (draft.mode === "constant") return draft.constant_pct;
  // Ramp from X to Y over Z minutes, then hold Y for the rest of the scenario. The
  // trailing hold is what makes the run answer "and then what?", which is usually the
  // interesting half — a climb that survives is not the same as a climb followed by an
  // hour of cruise on a hot day.
  const rampEnd = Math.min(draft.ramp_over_min, durationMinutes);
  const points: ThrottleWaypoint[] = [
    { time_min: 0, throttle_pct: draft.ramp_from_pct },
    { time_min: rampEnd, throttle_pct: draft.ramp_to_pct },
  ];
  if (rampEnd < durationMinutes) {
    points.push({ time_min: durationMinutes, throttle_pct: draft.ramp_to_pct });
  }
  return points;
}

export const OBJECTIVE_LABELS: Record<OptimizerObjective, string> = {
  max_range: "Max Range",
  max_power: "Max Power",
  max_engine_life: "Max Engine Life",
  balanced: "Balanced",
};
