/**
 * REST client for the Phase 6 fleet endpoints — GET /fleet/overview, GET /fleet/rankings.
 *
 * Kept as its own tiny file, same reasoning as lib/lifecycle/api.ts: this is read-only
 * and consumed by the fleet roster page, not by anything already using the testbench or
 * lifecycle stores.
 */
import { getAuthToken } from "../auth/store";
import type { FleetOverviewEntry, FleetOverviewResponse } from "./types";

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

function headers(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, { headers: headers() });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** `{ roster, fleet_mission_reliability }` — the squadron-wide joint-success read. */
export function fetchFleetOverview(): Promise<FleetOverviewResponse | null> {
  return getJson<FleetOverviewResponse>("/fleet/overview");
}

/** Same rows as fetchFleetOverview, sorted most-urgent first. */
export function fetchFleetRankings(): Promise<FleetOverviewEntry[] | null> {
  return getJson<FleetOverviewEntry[]>("/fleet/rankings");
}
