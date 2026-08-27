import * as THREE from "three";
import type { TelemetryFrame } from "@/lib/types";

/**
 * Shared geometry layout, colour ramps and telemetry→visual mapping for the engine
 * cutaway.
 *
 * Everything here is pure and allocation-free on the hot path. The mapping functions are
 * called from `useFrame` at 60fps, so they either return primitives or write into a
 * caller-supplied THREE.Color rather than allocating a new one each frame.
 */

// ---------------------------------------------------------------------------
// Layout — axes and positions
// ---------------------------------------------------------------------------
//
//   +X = forward (propeller end)      +Y = up      +Z = right bank
//
// A horizontally-opposed (boxer) four: crankshaft runs fore-aft down the centre, two
// cylinders per side pointing outward along ±Z, matching boxer-engine-render.png.
// The turbo, throttle body and manifolds wrap around the rear and top of that core mass
// the way turbo-flow-diagram.png lays them out.

export const LAYOUT = {
  crankcase: { length: 2.4, height: 0.58, width: 0.72 },
  /** Cylinder axial stations along X (front pair, rear pair). */
  cylinderStations: [0.58, -0.58],
  /** Where a cylinder barrel starts and ends, measured outward from centreline. */
  cylinderInner: 0.36,
  cylinderOuter: 1.26,
  cylinderRadius: 0.26,
  finRadius: 0.38,
  finCount: 9,
  propHubX: 1.52,
  turbo: { x: -1.95, y: -0.18 },
  wastegate: { x: -1.72, y: -0.72 },
  throttleBody: { x: -1.15, y: -0.62 },
  intakePlenumY: 0.72,
  exhaustCollectorY: -0.62,
} as const;

/**
 * Physical position of each cylinder, in the order the telemetry array uses.
 *
 * Standard opposed-four numbering: odd cylinders on the right bank, even on the left,
 * front pair first. `bank` is the sign of Z.
 */
export const CYLINDER_LAYOUT: { id: number; x: number; bank: 1 | -1 }[] = [
  { id: 1, x: LAYOUT.cylinderStations[0], bank: 1 },
  { id: 2, x: LAYOUT.cylinderStations[0], bank: -1 },
  { id: 3, x: LAYOUT.cylinderStations[1], bank: 1 },
  { id: 4, x: LAYOUT.cylinderStations[1], bank: -1 },
];

/** Firing order as 0-based indices into the telemetry cylinder array (1-3-4-2). */
export const FIRING_ORDER = [0, 2, 3, 1];

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------

export const COLORS = {
  /** Intake air — cyan, matching the blue flow path in turbo-flow-diagram.png. */
  intake: "#4FC8E8",
  /** Exhaust gas — dark teal, matching the diagram's exhaust path. */
  exhaust: "#3E8574",
  casting: "#8d97a3",
  crankcase: "#6d7683",
  darkMetal: "#3f4753",
  brass: "#b08d4f",
  /** Ignition harness — the orange leads in boxer-engine-render.png. */
  harness: "#E8792B",
  go: "#22d3a8",
  caution: "#f5a623",
  nogo: "#ef4a5f",
  cyan: "#3fd0e0",
} as const;

/**
 * EGT colour ramp: cool grey → orange → red-white, in the temperature range this engine
 * actually operates over (see docs/physics-model.md — cruise sits near 730 °C).
 */
const EGT_STOPS: { t: number; c: THREE.Color }[] = [
  { t: 380, c: new THREE.Color("#5c6672") },
  { t: 650, c: new THREE.Color("#a8571f") },
  { t: 760, c: new THREE.Color("#e2721c") },
  { t: 860, c: new THREE.Color("#ff9a5a") },
  { t: 980, c: new THREE.Color("#ffd8c2") },
];

/**
 * Write the colour for a given EGT into `out`.
 *
 * `coolingPenalty` (0–1) shifts the whole ramp hotter without the temperature itself
 * changing — that is how `cooling_degradation` makes the fins redden faster than EGT
 * alone would justify, which is the visual tell that heat is not being carried away
 * rather than that more heat is being produced.
 */
export function egtToColor(
  egtC: number,
  out: THREE.Color,
  coolingPenalty = 0
): THREE.Color {
  const t = egtC + coolingPenalty * 140;
  if (t <= EGT_STOPS[0]!.t) return out.copy(EGT_STOPS[0]!.c);
  for (let i = 0; i < EGT_STOPS.length - 1; i++) {
    const a = EGT_STOPS[i]!;
    const b = EGT_STOPS[i + 1]!;
    if (t <= b.t) {
      const k = (t - a.t) / (b.t - a.t);
      return out.copy(a.c).lerp(b.c, k);
    }
  }
  return out.copy(EGT_STOPS[EGT_STOPS.length - 1]!.c);
}

/** How brightly a fin should glow, 0–1, from EGT plus any cooling penalty. */
export function egtToEmissiveIntensity(egtC: number, coolingPenalty = 0): number {
  const norm = (egtC - 420) / 480;
  return clamp01(norm) * 1.5 + coolingPenalty * 0.7;
}

// ---------------------------------------------------------------------------
// Telemetry → visual mappings
// ---------------------------------------------------------------------------

export function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x;
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Crankshaft angular velocity in rad/s, scaled down so the eye can read it. */
export function rpmToAngularVelocity(rpm: number): number {
  // A real 2600 RPM crank is a blur. Scale to something legible while staying
  // proportional, so "faster engine = faster shaft" still reads correctly.
  return (rpm / 60) * Math.PI * 2 * 0.09;
}

/**
 * Turbo wheel speed from boost. Above ambient the compressor is doing work; below it the
 * wheels are just windmilling.
 */
export function boostToTurboVelocity(boostKpa: number): number {
  const above = Math.max(0, boostKpa - 95);
  return 0.8 + above * 0.22;
}

/** Intake flow-arrow speed and density from boost pressure. */
export function boostToFlow(boostKpa: number): { speed: number; density: number } {
  const above = Math.max(0, boostKpa - 90);
  return {
    speed: 0.08 + clamp01(above / 70) * 0.5,
    density: 0.25 + clamp01(above / 70) * 0.75,
  };
}

/** Exhaust flow scales with fuel burn — more fuel in, more gas out. */
export function fuelToExhaustFlow(fuelLph: number): { speed: number; density: number } {
  const k = clamp01((fuelLph - 4) / 16);
  return { speed: 0.07 + k * 0.42, density: 0.25 + k * 0.75 };
}

/**
 * Per-cylinder vibration outlier score, 0–1.
 *
 * A cylinder is only interesting if it is vibrating *more than its neighbours* — the
 * whole engine shaking harder at high RPM is normal. Comparing each cylinder against the
 * bank median is what isolates a single bad cylinder from a generally rough engine.
 */
export function vibrationOutliers(cylinders: { vibration_rms: number }[]): number[] {
  if (cylinders.length === 0) return [];
  const values = cylinders.map((c) => c.vibration_rms);
  const sorted = [...values].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)] ?? 0;
  if (median <= 1e-6) return values.map(() => 0);
  return values.map((v) => clamp01((v / median - 1.25) / 1.6));
}

/** Aggregate vibration above the healthy floor, 0–1 — drives whole-engine jitter. */
export function aggregateVibration(cylinders: { vibration_rms: number }[]): number {
  if (cylinders.length === 0) return 0;
  const mean =
    cylinders.reduce((sum, c) => sum + c.vibration_rms, 0) / cylinders.length;
  return clamp01((mean - 0.13) / 0.32);
}

export interface FaultView {
  misfireCylinderIndex: number;
  misfireSeverity: number;
  turboWear: number;
  coolingDegradation: number;
  bearingOrOilSeverity: number;
  electricalSeverity: number;
  sensorFaultChannels: string[];
}

/** Which sensor a sensor-fault type is attached to, for badge placement. */
export const SENSOR_MOUNTS: Record<string, string> = {
  egt_sensor_drift: "egt",
  oil_pressure_sensor_noise: "oil",
  rpm_sensor_stuck: "rpm",
};

const EMPTY_FAULT_VIEW: FaultView = {
  misfireCylinderIndex: -1,
  misfireSeverity: 0,
  turboWear: 0,
  coolingDegradation: 0,
  bearingOrOilSeverity: 0,
  electricalSeverity: 0,
  sensorFaultChannels: [],
};

/**
 * Collapse the frame's fault list into the handful of numbers the 3D scene cares about.
 *
 * Sensor faults are kept strictly separate from physical ones. A drifting probe must
 * never colour the physical part — the whole point of the disambiguation work is that an
 * instrument problem looks different from engine damage, and letting a bad sensor redden
 * a cylinder would undo that on the most visible surface in the product.
 */
export function readFaults(frame: TelemetryFrame | null): FaultView {
  if (!frame) return EMPTY_FAULT_VIEW;

  const view: FaultView = {
    misfireCylinderIndex: -1,
    misfireSeverity: 0,
    turboWear: 0,
    coolingDegradation: 0,
    bearingOrOilSeverity: 0,
    electricalSeverity: 0,
    sensorFaultChannels: [],
  };

  for (const fault of frame.active_faults ?? []) {
    if (fault.is_sensor_fault) {
      const mount = SENSOR_MOUNTS[fault.type];
      if (mount) view.sensorFaultChannels.push(mount);
      continue;
    }
    switch (fault.type) {
      case "misfire":
        view.misfireSeverity = fault.severity;
        break;
      case "turbo_wear":
        view.turboWear = fault.severity;
        break;
      case "cooling_degradation":
        view.coolingDegradation = fault.severity;
        break;
      case "bearing_wear":
      case "oil_pump_degradation":
      case "piston_ring_wear":
        view.bearingOrOilSeverity = Math.max(
          view.bearingOrOilSeverity,
          fault.severity
        );
        break;
      case "battery_alternator_degradation":
        view.electricalSeverity = fault.severity;
        break;
      default:
        break;
    }
  }

  // The backend does not say *which* cylinder is misfiring (it is ground truth the
  // dashboard is not given), so infer it the way an engineer would: the cylinder whose
  // EGT has collapsed relative to the others. A misfiring cylinder stops making heat.
  if (view.misfireSeverity > 0.02 && frame.cylinders.length > 0) {
    let coldest = 0;
    for (let i = 1; i < frame.cylinders.length; i++) {
      if (frame.cylinders[i]!.egt_c < frame.cylinders[coldest]!.egt_c) coldest = i;
    }
    view.misfireCylinderIndex = coldest;
  }

  // Low oil pressure counts even without a named fault — the symptom matters, not the
  // label, and the label may not have been classified yet.
  const oil = frame.oil_pressure_kpa;
  if (oil < 250) {
    view.bearingOrOilSeverity = Math.max(
      view.bearingOrOilSeverity,
      clamp01((250 - oil) / 160)
    );
  }

  return view;
}

/** Scene rim-light tint from the GO / CAUTION / NO-GO recommendation. */
export function reliabilityTint(frame: TelemetryFrame | null): string {
  switch (frame?.mission_reliability?.recommendation) {
    case "NO-GO":
      return COLORS.nogo;
    case "CAUTION":
      return COLORS.caution;
    default:
      return COLORS.go;
  }
}

/** Electrical indicator lamp colour from bus voltage. */
export function busVoltageColor(frame: TelemetryFrame | null): string {
  const v = frame?.battery_voltage_v;
  if (v == null) return COLORS.go;
  if (v < 11.4) return COLORS.nogo;
  if (v < 12.4) return COLORS.caution;
  return COLORS.go;
}

/**
 * Firing flash intensity for one cylinder, 0–1.
 *
 * Cylinders fire in sequence at a rate set by RPM. A misfire does not simply dim the
 * flash — it *skips* cycles stochastically, which is why this returns 0 outright on a
 * dropped cycle rather than a reduced value. The visual stutter is the signature.
 */
export function firingPulse(
  clock: number,
  cylinderIndex: number,
  rpm: number,
  misfireSeverity: number,
  isMisfiringCylinder: boolean
): { flash: number; skipped: boolean } {
  const cyclesPerSecond = Math.max(0.25, rpm / 60 / 2);
  const position = (clock * cyclesPerSecond) % 1;
  const slot = FIRING_ORDER.indexOf(cylinderIndex) / FIRING_ORDER.length;
  const raw = Math.abs(position - slot);
  const distance = Math.min(raw, 1 - raw);
  const flash = Math.max(0, 1 - distance * FIRING_ORDER.length * 2.2);

  if (isMisfiringCylinder && misfireSeverity > 0.02) {
    // Deterministic pseudo-random per cycle, so a skipped cycle stays skipped for its
    // whole duration instead of flickering within one combustion event.
    const cycleIndex = Math.floor(clock * cyclesPerSecond);
    const hash = Math.sin(cycleIndex * 12.9898 + cylinderIndex * 78.233) * 43758.5453;
    const roll = hash - Math.floor(hash);
    if (roll < misfireSeverity * 0.65) return { flash: 0, skipped: true };
  }
  return { flash, skipped: false };
}
