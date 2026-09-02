/**
 * Central zustand store. hooks/useTelemetryStream.ts is the only writer of telemetry
 * frames; every dashboard component reads from here instead of touching the WebSocket
 * directly. Also owns the (fire-and-forget) control actions the ControlDeck calls, which
 * POST to the mock backend's /control/* endpoints.
 *
 * Phase 6: every one of those endpoints is now uav_id-aware on the backend, so every
 * request this store makes carries `?uav_id=<selected>` (see `withUav` below) — read
 * from `useFleetStore`, not duplicated here, so a UAV switch anywhere in the app is
 * picked up by every request the very next time one fires.
 */
import { create } from "zustand";
import { getAuthToken } from "./auth/store";
import { useFleetStore } from "./fleet/store";
import { toastError } from "./toast/store";
import type { ConnectionStatus } from "./websocket";
import type {
  FaultType,
  MissionPhase,
  MissionSummary,
  SensorFaultType,
  TelemetryFrame,
} from "./types";

export const BUFFER_SIZE = 600; // 60s @ 10Hz

/**
 * Backend base URL. Upgraded to https when the page is secure, for the same
 * mixed-content reason the WebSocket is — a plain-http fetch from an https page is
 * blocked outright.
 */
function resolveApiUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  if (typeof window !== "undefined" && window.location.protocol === "https:") {
    if (configured.startsWith("http://")) {
      return `https://${configured.slice("http://".length)}`;
    }
  }
  return configured;
}

const API_URL = resolveApiUrl();

interface TelemetryStore {
  status: ConnectionStatus;
  latest: TelemetryFrame | null;
  buffer: TelemetryFrame[];
  throttle: number;
  timeScale: number;

  // ---- Phase 3 state ----
  missions: MissionSummary[];
  activeMissionId: number | null;
  isRecording: boolean;
  replayMissionId: number | null;
  missionReport: Record<string, unknown> | null;

  setStatus: (status: ConnectionStatus) => void;
  pushFrame: (frame: TelemetryFrame) => void;

  injectFault: (type: FaultType, severity?: number, rampSeconds?: number) => Promise<void>;
  clearFault: (type: FaultType) => Promise<void>;
  setThrottle: (value: number) => Promise<void>;
  setTimeScale: (factor: number) => Promise<void>;
  jumpPhase: (phase: MissionPhase) => Promise<void>;

  // ---- Phase 3 actions ----
  injectSensorFault: (
    type: SensorFaultType,
    severity?: number,
    rampSeconds?: number
  ) => Promise<void>;
  clearSensorFault: (type: SensorFaultType) => Promise<void>;
  applyScenario: (scenario: string) => Promise<void>;
  startMission: (profileName?: string) => Promise<void>;
  endMission: () => Promise<void>;
  refreshMissions: () => Promise<void>;
  startReplay: (missionId: number, speedFactor: number) => Promise<void>;
  stopReplay: () => Promise<void>;
  loadMissionReport: (missionId: number) => Promise<void>;
  clearMissionReport: () => void;

  // ---- Phase 6: fleet UAV switching ----
  /** Clears everything scoped to "whichever engine was previously selected" so a
   * switch never shows a frame of buffer mixing two UAVs' telemetry. Called from the
   * useFleetStore subscription below, not by any component directly. */
  resetForUavSwitch: () => void;
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Appends the currently selected UAV's id as a query param on every request this
 * store makes — every endpoint it calls is per-engine on the backend. */
function withUav(path: string): string {
  const uavId = useFleetStore.getState().selectedUavId;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}uav_id=${encodeURIComponent(uavId)}`;
}

async function postJson<T = unknown>(
  path: string,
  body: unknown
): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${withUav(path)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      toastError(`${path} failed (${res.status}) — the operator can retry.`)();
      return null;
    }
    return (await res.json()) as T;
  } catch {
    toastError(`No response from ${path} — is the backend running?`)();
    return null;
  }
}

async function getJson<T = unknown>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${withUav(path)}`, { headers: authHeaders() });
    if (!res.ok) {
      toastError(`${path} failed (${res.status})`)();
      return null;
    }
    return (await res.json()) as T;
  } catch {
    toastError(`No response from ${path} — is the backend running?`)();
    return null;
  }
}

export const useTelemetryStore = create<TelemetryStore>((set, get) => ({
  status: "connecting",
  latest: null,
  buffer: [],
  throttle: 0.8,
  timeScale: 1,

  missions: [],
  activeMissionId: null,
  isRecording: false,
  replayMissionId: null,
  missionReport: null,

  setStatus: (status) => set({ status }),

  pushFrame: (frame) =>
    set((state) => {
      const buffer = [...state.buffer, frame];
      if (buffer.length > BUFFER_SIZE) buffer.splice(0, buffer.length - BUFFER_SIZE);
      // Switching between live and replay makes the previous buffer meaningless —
      // they are different timelines, and splicing them produces a chart that jumps
      // backwards in time.
      const wasReplay = state.latest?.is_replay ?? false;
      const isReplay = frame.is_replay ?? false;
      if (wasReplay !== isReplay) {
        return {
          latest: frame,
          buffer: [frame],
          replayMissionId: isReplay ? frame.replay_mission_id ?? null : null,
        };
      }
      return { latest: frame, buffer };
    }),

  injectFault: async (type, severity = 0.85, rampSeconds = 18) => {
    await postJson("/control/fault", { type, severity, ramp_seconds: rampSeconds });
  },

  clearFault: async (type) => {
    await postJson("/control/clear-fault", { fault_type: type });
  },

  setThrottle: async (value) => {
    set({ throttle: value });
    await postJson("/control/throttle", { value });
  },

  setTimeScale: async (factor) => {
    set({ timeScale: factor });
    await postJson("/control/time-scale", { factor });
  },

  jumpPhase: async (phase) => {
    await postJson("/control/phase", { phase });
  },

  // ---- Phase 3 actions ---------------------------------------------------

  injectSensorFault: async (type, severity = 0.85, rampSeconds = 15) => {
    await postJson("/control/sensor-fault", {
      type,
      severity,
      ramp_seconds: rampSeconds,
    });
  },

  clearSensorFault: async (type) => {
    await postJson("/control/clear-sensor-fault", { fault_type: type });
  },

  applyScenario: async (scenario) => {
    await postJson("/control/scenario", { scenario });
  },

  startMission: async (profileName = "standard") => {
    const res = await postJson<{ mission_id: number }>("/control/mission/start", {
      profile_name: profileName,
    });
    if (res?.mission_id != null) {
      set({ activeMissionId: res.mission_id, isRecording: true });
    }
  },

  endMission: async () => {
    const res = await postJson<{ report: Record<string, unknown> }>(
      "/control/mission/end",
      {}
    );
    set({
      activeMissionId: null,
      isRecording: false,
      missionReport: res?.report ?? null,
    });
    await get().refreshMissions();
  },

  refreshMissions: async () => {
    const res = await getJson<{ missions: MissionSummary[] }>("/control/missions");
    if (res?.missions) set({ missions: res.missions });
  },

  startReplay: async (missionId, speedFactor) => {
    await postJson("/control/replay/start", {
      mission_id: missionId,
      speed_factor: speedFactor,
    });
    set({ replayMissionId: missionId });
  },

  stopReplay: async () => {
    await postJson("/control/replay/stop", {});
    set({ replayMissionId: null });
  },

  loadMissionReport: async (missionId) => {
    const res = await getJson<{ report: Record<string, unknown> }>(
      `/control/missions/${missionId}/report`
    );
    set({ missionReport: res?.report ?? null });
  },

  clearMissionReport: () => set({ missionReport: null }),

  resetForUavSwitch: () =>
    set({
      latest: null,
      buffer: [],
      activeMissionId: null,
      isRecording: false,
      replayMissionId: null,
      missionReport: null,
    }),
}));

// ---- Phase 6: react to a fleet UAV switch, wherever it was triggered from ---------
//
// A cross-store subscription rather than a React effect in some component: the switch
// can be triggered from the fleet page, the MissionHeader selector, or (later) anywhere
// else, and every one of those should produce the exact same reset-and-resync behaviour
// without each caller needing to remember to do it.
useFleetStore.subscribe((state, prevState) => {
  if (state.selectedUavId === prevState.selectedUavId) return;
  const store = useTelemetryStore.getState();
  store.resetForUavSwitch();
  void store.refreshMissions();
  void getJson<{ recording: boolean; mission_id: number | null }>(
    "/control/mission/status"
  ).then((status) => {
    if (!status) return;
    useTelemetryStore.setState({
      activeMissionId: status.mission_id,
      isRecording: status.recording,
    });
  });
});

// convenience selector helpers -------------------------------------------------

export function selectActiveFaultTypes(): string[] {
  return useTelemetryStore.getState().latest?.active_faults.map((f) => f.type) ?? [];
}
