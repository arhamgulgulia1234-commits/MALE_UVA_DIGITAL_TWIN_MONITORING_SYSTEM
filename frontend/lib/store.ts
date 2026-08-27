/**
 * Central zustand store. hooks/useTelemetryStream.ts is the only writer of telemetry
 * frames; every dashboard component reads from here instead of touching the WebSocket
 * directly. Also owns the (fire-and-forget) control actions the ControlDeck calls, which
 * POST to the mock backend's /control/* endpoints.
 */
import { create } from "zustand";
import type { ConnectionStatus } from "./websocket";
import type { FaultType, MissionPhase, TelemetryFrame } from "./types";

export const BUFFER_SIZE = 600; // 60s @ 10Hz

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface TelemetryStore {
  status: ConnectionStatus;
  latest: TelemetryFrame | null;
  buffer: TelemetryFrame[];
  throttle: number;
  timeScale: number;

  setStatus: (status: ConnectionStatus) => void;
  pushFrame: (frame: TelemetryFrame) => void;

  injectFault: (type: FaultType, severity?: number, rampSeconds?: number) => Promise<void>;
  clearFault: (type: FaultType) => Promise<void>;
  setThrottle: (value: number) => Promise<void>;
  setTimeScale: (factor: number) => Promise<void>;
  jumpPhase: (phase: MissionPhase) => Promise<void>;
}

async function postJson(path: string, body: unknown): Promise<void> {
  try {
    await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    // control actions are best-effort for the live demo; a dropped POST just means
    // the operator retries the button — no need to surface a toast/error state here.
  }
}

export const useTelemetryStore = create<TelemetryStore>((set, get) => ({
  status: "connecting",
  latest: null,
  buffer: [],
  throttle: 0.8,
  timeScale: 1,

  setStatus: (status) => set({ status }),

  pushFrame: (frame) =>
    set((state) => {
      const buffer = [...state.buffer, frame];
      if (buffer.length > BUFFER_SIZE) buffer.splice(0, buffer.length - BUFFER_SIZE);
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
}));

// convenience selector helpers -------------------------------------------------

export function selectActiveFaultTypes(): FaultType[] {
  return useTelemetryStore.getState().latest?.active_faults.map((f) => f.type) ?? [];
}
