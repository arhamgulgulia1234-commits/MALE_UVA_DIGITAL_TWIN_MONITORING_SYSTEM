/**
 * REST client for the Phase 5 engine life-cycle endpoints.
 *
 * Same `TestBenchError`-style contract as lib/testbench/api.ts (a rejected maintenance
 * action carries a reason worth showing, not just a toast), but kept as its own file
 * rather than imported from there — this page is not part of the Test Bench and should
 * not need to pull in its store or its scenario/optimizer types to make one GET.
 */
import type {
  LifecycleSummary,
  MaintenanceActionRequest,
  MaintenanceActionResponse,
} from "./types";

function resolveApiUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  if (typeof window !== "undefined" && window.location.protocol === "https:") {
    if (configured.startsWith("http://")) {
      return `https://${configured.slice("http://".length)}`;
    }
  }
  return configured;
}

export const API_URL = resolveApiUrl();

const AUTH_TOKEN = process.env.NEXT_PUBLIC_TELEMETRY_TOKEN ?? "";

function headers(): Record<string, string> {
  return {
    "Content-Type": "application/json",
    ...(AUTH_TOKEN ? { Authorization: `Bearer ${AUTH_TOKEN}` } : {}),
  };
}

export class LifecycleError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "LifecycleError";
    this.status = status;
  }
}

async function toError(res: Response): Promise<LifecycleError> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    const detail = body?.detail;
    if (typeof detail === "string") return new LifecycleError(detail, res.status);
  } catch {
    // fall through to the generic message below
  }
  return new LifecycleError(`Request failed (${res.status})`, res.status);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers: headers() });
  } catch {
    throw new LifecycleError(`Cannot reach the backend at ${API_URL}`, 0);
  }
  if (!res.ok) throw await toError(res);
  return (await res.json()) as T;
}

export function fetchLifecycleSummary(): Promise<LifecycleSummary> {
  return request<LifecycleSummary>("/lifecycle/summary");
}

export function postMaintenanceAction(
  body: MaintenanceActionRequest
): Promise<MaintenanceActionResponse> {
  return request<MaintenanceActionResponse>("/lifecycle/maintenance-action", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
