import type { TelemetryFrame } from "@/lib/types";
import { LAYOUT } from "@/components/dashboard/engine3d/engine3dUtils";

/**
 * The part inspector's source of truth: one entry per clickable physical component in
 * the engine cutaway.
 *
 * This file is deliberately the only place that knows how a *physical* part maps onto
 * the *software* that governs it. The 3D scene knows geometry, the store knows numbers,
 * and the health model knows subsystems — none of them knows that the wastegate flap you
 * just clicked is simulated by `turbo_model.py` and scored under the `turbo` subsystem.
 * That crossing is the whole point of the inspector, so it lives in one table rather
 * than being scattered across the components that happen to draw each part.
 *
 * `meshRefKey` is the string the 3D components use to identify themselves to the
 * selection system (see `EngineCutaway3D`), so a registry id and the thing you can
 * actually click can never drift apart.
 */

export type HealthSubsystem =
  | "cylinder"
  | "lubrication"
  | "cooling"
  | "fuel"
  | "turbo"
  | "electrical";

export interface EnginePart {
  /** Stable id; also the value stored in `selectedPartId`. */
  id: string;
  displayName: string;
  /** Two or three plain-language sentences — read by someone who is not an engine person. */
  description: string;
  /** How the part identifies itself to the click/selection system in the 3D scene. */
  meshRefKey: string;
  /**
   * Dot/bracket paths into `TelemetryFrame`, resolved live by `resolveTelemetryField`.
   * These are the numbers that actually move when this part is working or failing.
   */
  relatedTelemetryFields: string[];
  /** The backend module that simulates this part. Shown as a code badge in the panel. */
  backendModule: string;
  /** Modules that also have a hand in it — a cylinder is thermal as well as mechanical. */
  secondaryModules?: string[];
  /** Fault types that, when active, are this part's problem. */
  relatedFaultTypes: string[];
  /** Which health-index subsystem scores this part. */
  healthSubsystem: HealthSubsystem;
  /** Point the camera looks at when the part is selected, in scene units. */
  focus: [number, number, number];
  /** Camera distance from `focus` — large enough that surrounding context stays in frame. */
  focusDistance: number;
}

const CYL_DESCRIPTIONS: Record<number, string> = {
  1: "Front right cylinder. Air and fuel are compressed and burned here, and the heat that escapes with the exhaust is what its EGT probe reads. Its cooling fins glow hotter in the 3D view as that temperature climbs.",
  2: "Front left cylinder. It fires second in the 1-3-4-2 order, opposite its right-bank partner, which is what keeps a boxer engine naturally balanced. A drop in its exhaust temperature relative to the others is the usual first sign of a misfire.",
  3: "Rear right cylinder. Sitting behind the front pair it gets less clean cooling air, so it typically runs slightly warmer under sustained climb. Its vibration signal is measured at the mount flange where the barrel bolts to the crankcase.",
  4: "Rear left cylinder. Fires last in the sequence, completing one full crankshaft revolution's worth of power strokes. Persistent roughness here with normal exhaust temperature usually points at the mount or the piston rings rather than combustion.",
};

/** Every clickable part in the cutaway, in the order the Module Map lists them. */
export const ENGINE_PARTS: EnginePart[] = [
  // --- combustion ---------------------------------------------------------
  ...[0, 1, 2, 3].map<EnginePart>((i) => {
    const layout = [
      { x: LAYOUT.cylinderStations[0], bank: 1 },
      { x: LAYOUT.cylinderStations[0], bank: -1 },
      { x: LAYOUT.cylinderStations[1], bank: 1 },
      { x: LAYOUT.cylinderStations[1], bank: -1 },
    ][i]!;
    return {
      id: `cylinder-${i + 1}`,
      displayName: `Cylinder ${i + 1}`,
      description: CYL_DESCRIPTIONS[i + 1]!,
      meshRefKey: `cylinder-${i + 1}`,
      relatedTelemetryFields: [
        `cylinders[${i}].egt_c`,
        `cylinders[${i}].vibration_rms`,
        "cht_c",
        "combustion_instability_pct",
      ],
      backendModule: "backend/app/physics/engine_model.py",
      secondaryModules: [
        "backend/app/physics/thermal_model.py",
        "backend/app/physics/vibration_model.py",
      ],
      relatedFaultTypes: [
        "misfire",
        "spark_degradation",
        "piston_ring_wear",
        "fuel_injector_clog",
        "injection_timing_drift",
      ],
      healthSubsystem: "cylinder",
      focus: [layout.x, 0.25, layout.bank * (LAYOUT.cylinderInner + 0.55)],
      focusDistance: 5.4,
    };
  }),

  // --- rotating assembly --------------------------------------------------
  {
    id: "crankshaft",
    displayName: "Crankshaft & Prop Hub",
    description:
      "The shaft that turns each cylinder's power stroke into rotation, and the hub stub the propeller bolts to. Its speed in the 3D view is driven directly by live RPM, so it visibly slows and spools with the engine. Because every cylinder feeds into it, roughness in the combustion side shows up here as torsional vibration.",
    meshRefKey: "crankshaft",
    relatedTelemetryFields: ["rpm", "fused_rpm", "airspeed_ms"],
    backendModule: "backend/app/physics/engine_model.py",
    secondaryModules: ["backend/app/fusion/rpm_fusion.py"],
    relatedFaultTypes: ["misfire", "bearing_wear"],
    healthSubsystem: "cylinder",
    focus: [0.5, 0, 0],
    focusDistance: 6.0,
  },
  {
    id: "crankcase-oil",
    displayName: "Crankcase & Oil System",
    description:
      "The central case that carries the crankshaft, plus the sump and pump that keep pressurised oil moving through the bearings. Oil pressure falling while oil temperature climbs is the classic signature of a pump or bearing problem. This is the one subsystem where a slow trend matters more than any single reading.",
    meshRefKey: "crankcase-oil",
    relatedTelemetryFields: [
      "oil_pressure_kpa",
      "oil_temp_c",
      "fused_oil_pressure_kpa",
      "health.subsystem_scores.lubrication",
    ],
    backendModule: "backend/app/physics/lubrication_model.py",
    secondaryModules: ["backend/app/fusion/oil_pressure_fusion.py"],
    relatedFaultTypes: ["bearing_wear", "oil_pump_degradation"],
    healthSubsystem: "lubrication",
    focus: [-0.15, -0.35, 0],
    focusDistance: 5.8,
  },

  // --- induction / charge -------------------------------------------------
  {
    id: "turbocharger",
    displayName: "Turbocharger",
    description:
      "Exhaust gas spins the turbine wheel, which drives the compressor wheel on the same shaft to force extra air into the engine. A worn turbo does not simply produce less boost — it takes noticeably longer to respond, so the wheels in the 3D view lag behind the boost the engine is asking for. That lag is modelled as a lengthened spool time constant.",
    meshRefKey: "turbocharger",
    relatedTelemetryFields: [
      "boost_pressure_kpa",
      "manifold_pressure_kpa",
      "health.subsystem_scores.turbo",
    ],
    backendModule: "backend/app/physics/turbo_model.py",
    relatedFaultTypes: ["turbo_wear"],
    healthSubsystem: "turbo",
    focus: [LAYOUT.turbo.x, LAYOUT.turbo.y, 0],
    focusDistance: 5.4,
  },
  {
    id: "wastegate",
    displayName: "Wastegate",
    description:
      "A hinged flap that dumps exhaust around the turbine instead of through it, which is how boost is held at a target instead of running away. A healthy wastegate moves smoothly; a worn linkage sticks and judders, and you can see that in the 3D flap's motion. It shares the turbo health index because it is part of the same control loop.",
    meshRefKey: "wastegate",
    relatedTelemetryFields: ["boost_pressure_kpa", "manifold_pressure_kpa"],
    backendModule: "backend/app/physics/turbo_model.py",
    relatedFaultTypes: ["turbo_wear"],
    healthSubsystem: "turbo",
    focus: [LAYOUT.wastegate.x, LAYOUT.wastegate.y, 0],
    focusDistance: 5.0,
  },
  {
    id: "intake-manifold",
    displayName: "Intake Manifold",
    description:
      "The plenum and runners that carry compressed air from the compressor outlet to each cylinder head. Arrow speed and density along the cyan path track live boost pressure, so the induction side visibly works harder as the turbo comes up. A clogging air filter starves this path and shows up as manifold pressure falling short of the boost being produced.",
    meshRefKey: "intake-manifold",
    relatedTelemetryFields: [
      "manifold_pressure_kpa",
      "boost_pressure_kpa",
      "ambient_temperature_c",
    ],
    backendModule: "backend/app/physics/engine_model.py",
    secondaryModules: ["backend/app/physics/environment.py"],
    relatedFaultTypes: ["air_filter_clog"],
    healthSubsystem: "turbo",
    focus: [0.15, LAYOUT.intakePlenumY, 0],
    focusDistance: 5.8,
  },
  {
    id: "throttle-body",
    displayName: "Throttle Body",
    description:
      "The butterfly valve between the compressor and the plenum that sets how much of the available charge air actually reaches the engine. It is the pilot's direct lever on power, and every other induction number downstream moves when it does. Its housing sits low and forward on the intake trunk in the cutaway.",
    meshRefKey: "throttle-body",
    relatedTelemetryFields: ["manifold_pressure_kpa", "rpm", "mission_phase"],
    backendModule: "backend/app/physics/engine_model.py",
    secondaryModules: ["backend/app/sim/simulation_loop.py"],
    relatedFaultTypes: ["air_filter_clog"],
    healthSubsystem: "turbo",
    focus: [LAYOUT.throttleBody.x, LAYOUT.throttleBody.y, 0],
    focusDistance: 5.0,
  },

  // --- exhaust ------------------------------------------------------------
  {
    id: "exhaust-manifold",
    displayName: "Exhaust Manifold",
    description:
      "Headers from all four cylinder heads merging into a collector that runs under the case and feeds the turbine. Flow here is driven by fuel burn rather than boost, because exhaust mass is what the engine has already consumed. Watching the two paths on different signals is what makes turbo lag visible in the diagram.",
    meshRefKey: "exhaust-manifold",
    relatedTelemetryFields: [
      "fuel_flow_lph",
      "cylinders[0].egt_c",
      "cylinders[1].egt_c",
      "cylinders[2].egt_c",
      "cylinders[3].egt_c",
    ],
    backendModule: "backend/app/physics/engine_model.py",
    secondaryModules: ["backend/app/physics/thermal_model.py"],
    relatedFaultTypes: ["fuel_injector_clog", "misfire", "cooling_degradation"],
    healthSubsystem: "cylinder",
    focus: [0.0, LAYOUT.exhaustCollectorY, 0],
    focusDistance: 5.8,
  },
];

export const ENGINE_PARTS_BY_ID: Record<string, EnginePart> = Object.fromEntries(
  ENGINE_PARTS.map((p) => [p.id, p])
);

export function getEnginePart(id: string | null): EnginePart | null {
  return id ? ENGINE_PARTS_BY_ID[id] ?? null : null;
}

// ---------------------------------------------------------------------------
// Telemetry field resolution
// ---------------------------------------------------------------------------

/**
 * Display metadata for a telemetry path.
 *
 * Keyed on the path with any array index collapsed to `[]`, so all four cylinders share
 * one entry instead of the table repeating itself four times.
 */
interface FieldMeta {
  label: string;
  unit?: string;
  digits?: number;
}

const FIELD_META: Record<string, FieldMeta> = {
  "cylinders[].egt_c": { label: "EGT", unit: "°C", digits: 0 },
  "cylinders[].vibration_rms": { label: "Vibration", unit: "g rms", digits: 2 },
  cht_c: { label: "CHT", unit: "°C", digits: 0 },
  fused_cht_c: { label: "CHT (fused)", unit: "°C", digits: 0 },
  combustion_instability_pct: { label: "Combustion instability", unit: "%", digits: 1 },
  rpm: { label: "RPM", unit: "rpm", digits: 0 },
  fused_rpm: { label: "RPM (fused)", unit: "rpm", digits: 0 },
  airspeed_ms: { label: "Airspeed", unit: "m/s", digits: 1 },
  oil_pressure_kpa: { label: "Oil pressure", unit: "kPa", digits: 0 },
  fused_oil_pressure_kpa: { label: "Oil pressure (fused)", unit: "kPa", digits: 0 },
  oil_temp_c: { label: "Oil temperature", unit: "°C", digits: 0 },
  boost_pressure_kpa: { label: "Boost pressure", unit: "kPa", digits: 1 },
  manifold_pressure_kpa: { label: "Manifold pressure", unit: "kPa", digits: 1 },
  fuel_flow_lph: { label: "Fuel flow", unit: "L/h", digits: 2 },
  ambient_temperature_c: { label: "Ambient air", unit: "°C", digits: 1 },
  mission_phase: { label: "Mission phase" },
  "health.subsystem_scores.lubrication": { label: "Lubrication index", digits: 1 },
  "health.subsystem_scores.turbo": { label: "Turbo index", digits: 1 },
  "health.subsystem_scores.cylinder": { label: "Cylinder index", digits: 1 },
};

const INDEX_PATTERN = /\[\d+\]/g;

/**
 * Human label for a telemetry path, falling back to the raw path.
 *
 * Indexed paths carry the cylinder number, so a part that lists all four EGTs — the
 * exhaust manifold does — does not render four rows all labelled "EGT".
 */
export function telemetryFieldLabel(path: string): string {
  const base = FIELD_META[path.replace(INDEX_PATTERN, "[]")]?.label ?? path;
  const index = /cylinders\[(\d+)\]/.exec(path);
  return index ? `${base} · cyl ${Number(index[1]) + 1}` : base;
}

/**
 * Read a `TelemetryFrame` through a `a.b[0].c` path.
 *
 * Kept deliberately tiny and dependency-free: this runs on the panel's 4 Hz poll, not on
 * the render loop, so clarity beats cleverness — but it still must never throw on a
 * frame that is missing an optional channel.
 */
export function resolveTelemetryField(
  frame: TelemetryFrame | null,
  path: string
): number | string | null {
  if (!frame) return null;
  let node: unknown = frame;
  for (const rawKey of path.split(".")) {
    const match = /^([A-Za-z_]\w*)(?:\[(\d+)\])?$/.exec(rawKey);
    if (!match) return null;
    if (node == null || typeof node !== "object") return null;
    node = (node as Record<string, unknown>)[match[1]!];
    if (match[2] !== undefined) {
      if (!Array.isArray(node)) return null;
      node = node[Number(match[2])];
    }
  }
  if (typeof node === "number" || typeof node === "string") return node;
  return null;
}

/** Pre-formatted value + unit for the detail panel. */
export function formatTelemetryValue(
  path: string,
  value: number | string | null
): { value: string; unit: string } {
  const meta = FIELD_META[path.replace(INDEX_PATTERN, "[]")];
  if (value == null) return { value: "—", unit: meta?.unit ?? "" };
  if (typeof value === "string") return { value, unit: "" };
  return {
    value: value.toFixed(meta?.digits ?? 1),
    unit: meta?.unit ?? "",
  };
}
