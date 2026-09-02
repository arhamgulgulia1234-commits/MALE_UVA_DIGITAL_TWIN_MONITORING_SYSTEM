/**
 * REST client for the Phase 4 Test Bench endpoints.
 *
 * Separate from lib/store.ts's fetch helpers on purpose. Those swallow failures — a
 * dropped control POST during a live demo is best handled by the operator pressing the
 * button again, and a toast would be noise. The Test Bench is the opposite: a scenario is
 * a request the operator waited several seconds for, and a 400 carries the *reason* the
 * conditions fell outside modelled validity. Losing that would leave the panel silently
 * empty with no way to tell a rejected request from a slow one.
 */
import { getAuthToken } from "../auth/store";
import { useFleetStore } from "../fleet/store";
import type {
  LiveOperatingPoint,
  OperatingPointResult,
  OptimizerObjective,
  PerformanceMapResponse,
  PerformanceMetric,
  PresetsResponse,
  ScenarioEnvelope,
  ScenarioRequest,
  ScenarioResult,
  ScenarioRunRow,
  ValidityError,
} from "./types";

function resolveApiUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  // Same mixed-content upgrade lib/store.ts does: a plain-http fetch from an https page
  // is blocked outright, and the failure looks exactly like the backend being down.
  if (typeof window !== "undefined" && window.location.protocol === "https:") {
    if (configured.startsWith("http://")) {
      return `https://${configured.slice("http://".length)}`;
    }
  }
  return configured;
}

export const API_URL = resolveApiUrl();

function headers(): Record<string, string> {
  const token = getAuthToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

/** An error the caller is expected to show the operator, with its reasons intact. */
export class TestBenchError extends Error {
  readonly reasons: string[];
  readonly status: number;

  constructor(message: string, reasons: string[], status: number) {
    super(message);
    this.name = "TestBenchError";
    this.reasons = reasons;
    this.status = status;
  }
}

async function toError(res: Response): Promise<TestBenchError> {
  let detail: unknown;
  try {
    detail = (await res.json()) as { detail?: unknown };
  } catch {
    return new TestBenchError(`Request failed (${res.status})`, [], res.status);
  }
  const raw = (detail as { detail?: unknown })?.detail ?? detail;
  if (raw && typeof raw === "object" && "reasons" in raw) {
    const validity = raw as ValidityError;
    return new TestBenchError(validity.error, validity.reasons ?? [], res.status);
  }
  if (typeof raw === "string") {
    return new TestBenchError(raw, [], res.status);
  }
  return new TestBenchError(`Request failed (${res.status})`, [], res.status);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers: headers() });
  } catch {
    throw new TestBenchError(
      "Cannot reach the backend",
      [`No response from ${API_URL}. Is the API running?`],
      0
    );
  }
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

/** The what-if scenario endpoints below (`runScenario`, `fetchEnvelope`, the scenario
 * history) are deliberately NOT scoped to a UAV — a Test Bench run takes its starting
 * wear from `initial_fault_severities` in the request body, never from a live engine,
 * so it is the same computation regardless of which UAV happens to be selected. Only
 * the handful of calls below that actually read or command a *live* engine (the
 * optimizer's `use_current_engine_health`, applying a preset, mission status, and the
 * live map marker) need `uav_id`. */
function withUav(path: string): string {
  const uavId = useFleetStore.getState().selectedUavId;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}uav_id=${encodeURIComponent(uavId)}`;
}

export function runScenario(body: ScenarioRequest): Promise<ScenarioResult> {
  return request<ScenarioResult>("/simulate/scenario", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchEnvelope(): Promise<ScenarioEnvelope> {
  return request<ScenarioEnvelope>("/simulate/scenario/envelope");
}

export function fetchScenarioRuns(limit = 50): Promise<{ runs: ScenarioRunRow[] }> {
  return request<{ runs: ScenarioRunRow[] }>(`/simulate/scenario/runs?limit=${limit}`);
}

export function fetchScenarioRun(id: number): Promise<ScenarioRunRow> {
  return request<ScenarioRunRow>(`/simulate/scenario/runs/${id}`);
}

export function optimizeOperatingPoint(body: {
  altitude_m: number;
  ambient_temperature_c: number | null;
  objective: OptimizerObjective;
  use_current_engine_health: boolean;
}): Promise<OperatingPointResult> {
  return request<OperatingPointResult>(withUav("/optimize/operating-point"), {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function fetchPresets(): Promise<PresetsResponse> {
  return request<PresetsResponse>("/control/presets");
}

export function applyPreset(presetName: string): Promise<{
  ok: boolean;
  preset_name: string;
  setpoint: Record<string, number | null>;
}> {
  return request(withUav("/control/apply-preset"), {
    method: "POST",
    body: JSON.stringify({ preset_name: presetName }),
  });
}

export function fetchMissionStatus(): Promise<{
  recording: boolean;
  mission_id: number | null;
  frames_recorded: number;
}> {
  return request(withUav("/control/mission/status"));
}

// ---- performance maps ---------------------------------------------------------

export function fetchPerformanceMap(params: {
  altitude_m: number;
  metric: PerformanceMetric;
}): Promise<PerformanceMapResponse> {
  const query = new URLSearchParams({
    altitude_m: String(Math.round(params.altitude_m)),
    metric: params.metric,
  });
  return request<PerformanceMapResponse>(`/performance-maps?${query.toString()}`);
}

/**
 * Poll the live engine's operating point for the map marker.
 *
 * The Test Bench opens no WebSocket by design, so this is a plain GET on a timer. It is
 * also the one call on this page that is *expected* to come back saying "nothing here" —
 * on the mock backend, or before the first frame — so the caller treats an unavailable
 * point as a normal state rather than an error to surface.
 */
export function fetchLiveOperatingPoint(): Promise<LiveOperatingPoint> {
  return request<LiveOperatingPoint>(withUav("/performance-maps/live-point"));
}
