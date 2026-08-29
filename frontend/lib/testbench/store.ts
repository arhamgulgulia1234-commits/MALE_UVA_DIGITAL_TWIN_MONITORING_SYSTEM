/**
 * Test Bench state — a separate zustand store from lib/store.ts.
 *
 * The live store holds a 600-frame rolling buffer fed by a WebSocket at 10 Hz, and every
 * dashboard panel subscribes to it. Putting a 1800-frame static scenario result in there
 * would make every live component re-render on a Test Bench action and would blur the one
 * distinction this mode exists to keep sharp: nothing in here is live.
 */
import { create } from "zustand";
import {
  TestBenchError,
  applyPreset,
  fetchEnvelope,
  fetchMissionStatus,
  fetchPresets,
  fetchScenarioRun,
  fetchScenarioRuns,
  optimizeOperatingPoint,
  runScenario,
} from "./api";
import {
  type OperatingPointResult,
  type OptimizerObjective,
  type PresetCard,
  type ScenarioEnvelope,
  type ScenarioRequest,
  type ScenarioResult,
  type ScenarioRunRow,
  type ScheduledFault,
  type ThrottleProfileDraft,
  draftToProfile,
} from "./types";

export interface ScenarioDraft {
  altitude_m: number;
  /** null = standard day (ISA temperature for this altitude). */
  ambient_temperature_c: number | null;
  duration_minutes: number;
  throttle: ThrottleProfileDraft;
  /** Pre-existing wear, fault_type -> 0-1. Only non-zero entries are sent. */
  initial_fault_severities: Record<string, number>;
  scheduled_faults: ScheduledFault[];
  label: string;
}

export const DEFAULT_DRAFT: ScenarioDraft = {
  altitude_m: 2400,
  ambient_temperature_c: null,
  duration_minutes: 20,
  throttle: {
    mode: "constant",
    constant_pct: 78,
    ramp_from_pct: 55,
    ramp_to_pct: 95,
    ramp_over_min: 6,
  },
  initial_fault_severities: {},
  scheduled_faults: [],
  label: "",
};

export interface OptimizerDraft {
  altitude_m: number;
  ambient_temperature_c: number | null;
  objective: OptimizerObjective;
  use_current_engine_health: boolean;
}

export const DEFAULT_OPTIMIZER_DRAFT: OptimizerDraft = {
  altitude_m: 2400,
  ambient_temperature_c: null,
  objective: "balanced",
  use_current_engine_health: false,
};

interface TestBenchStore {
  envelope: ScenarioEnvelope | null;

  draft: ScenarioDraft;
  result: ScenarioResult | null;
  running: boolean;
  scenarioError: TestBenchError | null;

  optimizerDraft: OptimizerDraft;
  optimizerResult: OperatingPointResult | null;
  optimizing: boolean;
  optimizerError: TestBenchError | null;

  presets: PresetCard[];
  presetsLoading: boolean;
  presetsError: TestBenchError | null;
  applyingPreset: string | null;
  appliedPreset: string | null;

  missionActive: boolean;

  runs: ScenarioRunRow[];
  runsLoading: boolean;
  selectedRun: ScenarioRunRow | null;

  loadEnvelope: () => Promise<void>;
  patchDraft: (patch: Partial<ScenarioDraft>) => void;
  patchThrottle: (patch: Partial<ThrottleProfileDraft>) => void;
  setFaultSeverity: (faultType: string, severity: number) => void;
  addScheduledFault: (fault: ScheduledFault) => void;
  removeScheduledFault: (index: number) => void;
  resetDraft: () => void;
  submitScenario: () => Promise<void>;
  clearResult: () => void;

  patchOptimizerDraft: (patch: Partial<OptimizerDraft>) => void;
  submitOptimizer: () => Promise<void>;

  loadPresets: () => Promise<void>;
  applyPresetToLive: (name: string) => Promise<void>;
  refreshMissionStatus: () => Promise<void>;

  loadRuns: () => Promise<void>;
  selectRun: (id: number | null) => Promise<void>;
  loadRunIntoDraft: (run: ScenarioRunRow) => void;
}

function asError(err: unknown): TestBenchError {
  if (err instanceof TestBenchError) return err;
  return new TestBenchError("Unexpected error", [String(err)], 0);
}

export function buildScenarioRequest(draft: ScenarioDraft): ScenarioRequest {
  return {
    altitude_m: draft.altitude_m,
    ambient_temperature_c: draft.ambient_temperature_c,
    duration_minutes: draft.duration_minutes,
    throttle_profile: draftToProfile(draft.throttle, draft.duration_minutes),
    // Sliders sit at zero until touched; sending those would be noise in the stored
    // parameters and in the request the backend validates.
    initial_fault_severities: Object.fromEntries(
      Object.entries(draft.initial_fault_severities).filter(([, v]) => v > 0.001)
    ),
    injected_faults_during_scenario: draft.scheduled_faults,
    label: draft.label.trim() || null,
    save: true,
    include_frames: true,
  };
}

export const useTestBenchStore = create<TestBenchStore>((set, get) => ({
  envelope: null,

  draft: { ...DEFAULT_DRAFT },
  result: null,
  running: false,
  scenarioError: null,

  optimizerDraft: { ...DEFAULT_OPTIMIZER_DRAFT },
  optimizerResult: null,
  optimizing: false,
  optimizerError: null,

  presets: [],
  presetsLoading: false,
  presetsError: null,
  applyingPreset: null,
  appliedPreset: null,

  missionActive: false,

  runs: [],
  runsLoading: false,
  selectedRun: null,

  loadEnvelope: async () => {
    try {
      set({ envelope: await fetchEnvelope() });
    } catch {
      // The envelope only tightens input hints; the builder still works without it and
      // the backend validates authoritatively either way.
    }
  },

  patchDraft: (patch) => set((s) => ({ draft: { ...s.draft, ...patch } })),

  patchThrottle: (patch) =>
    set((s) => ({ draft: { ...s.draft, throttle: { ...s.draft.throttle, ...patch } } })),

  setFaultSeverity: (faultType, severity) =>
    set((s) => ({
      draft: {
        ...s.draft,
        initial_fault_severities: {
          ...s.draft.initial_fault_severities,
          [faultType]: severity,
        },
      },
    })),

  addScheduledFault: (fault) =>
    set((s) => ({
      draft: { ...s.draft, scheduled_faults: [...s.draft.scheduled_faults, fault] },
    })),

  removeScheduledFault: (index) =>
    set((s) => ({
      draft: {
        ...s.draft,
        scheduled_faults: s.draft.scheduled_faults.filter((_, i) => i !== index),
      },
    })),

  resetDraft: () => set({ draft: { ...DEFAULT_DRAFT }, scenarioError: null }),

  submitScenario: async () => {
    set({ running: true, scenarioError: null });
    try {
      const result = await runScenario(buildScenarioRequest(get().draft));
      set({ result, running: false });
      await get().loadRuns();
    } catch (err) {
      set({ scenarioError: asError(err), running: false });
    }
  },

  clearResult: () => set({ result: null, scenarioError: null }),

  patchOptimizerDraft: (patch) =>
    set((s) => ({ optimizerDraft: { ...s.optimizerDraft, ...patch } })),

  submitOptimizer: async () => {
    set({ optimizing: true, optimizerError: null });
    try {
      const optimizerResult = await optimizeOperatingPoint(get().optimizerDraft);
      set({ optimizerResult, optimizing: false });
    } catch (err) {
      set({ optimizerError: asError(err), optimizing: false });
    }
  },

  loadPresets: async () => {
    if (get().presetsLoading || get().presets.length > 0) return;
    set({ presetsLoading: true, presetsError: null });
    try {
      const res = await fetchPresets();
      set({ presets: res.presets, presetsLoading: false });
    } catch (err) {
      set({ presetsError: asError(err), presetsLoading: false });
    }
  },

  applyPresetToLive: async (name) => {
    set({ applyingPreset: name, presetsError: null });
    try {
      await applyPreset(name);
      set({ applyingPreset: null, appliedPreset: name });
    } catch (err) {
      set({ presetsError: asError(err), applyingPreset: null });
    }
  },

  refreshMissionStatus: async () => {
    try {
      const status = await fetchMissionStatus();
      set({ missionActive: status.recording });
    } catch {
      set({ missionActive: false });
    }
  },

  loadRuns: async () => {
    set({ runsLoading: true });
    try {
      const res = await fetchScenarioRuns();
      set({ runs: res.runs, runsLoading: false });
    } catch {
      set({ runsLoading: false });
    }
  },

  selectRun: async (id) => {
    if (id === null) {
      set({ selectedRun: null });
      return;
    }
    try {
      set({ selectedRun: await fetchScenarioRun(id) });
    } catch {
      set({ selectedRun: null });
    }
  },

  loadRunIntoDraft: (run) => {
    // Past runs store their parameters, not their time-series, so "reload" means
    // repopulating the builder — re-running is one click away and deterministic.
    const p = run.params as {
      throttle_profile?: number | { time_min: number; throttle_pct: number }[];
      initial_fault_severities?: Record<string, number>;
      injected_faults_during_scenario?: ScheduledFault[];
      label?: string | null;
    };
    const profile = p.throttle_profile;
    const throttle: ThrottleProfileDraft =
      typeof profile === "number"
        ? { ...DEFAULT_DRAFT.throttle, mode: "constant", constant_pct: profile }
        : Array.isArray(profile) && profile.length >= 2
        ? {
            ...DEFAULT_DRAFT.throttle,
            mode: "ramp",
            ramp_from_pct: profile[0]!.throttle_pct,
            ramp_to_pct: profile[1]!.throttle_pct,
            ramp_over_min: profile[1]!.time_min,
          }
        : { ...DEFAULT_DRAFT.throttle };

    set({
      draft: {
        altitude_m: run.altitude_m,
        ambient_temperature_c: run.ambient_temperature_c,
        duration_minutes: run.duration_minutes,
        throttle,
        initial_fault_severities: p.initial_fault_severities ?? {},
        scheduled_faults: p.injected_faults_during_scenario ?? [],
        label: run.label ?? "",
      },
      scenarioError: null,
    });
  },
}));
