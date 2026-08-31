"use client";

/**
 * Fleet-wide state — which UAV is currently selected (read by lib/store.ts,
 * lib/lifecycle/store.ts and lib/testbench/store.ts to scope every per-engine request),
 * plus the ranked roster the fleet page renders.
 *
 * `selectedUavId` living here rather than in lib/store.ts is what lets the Live
 * Dashboard, Test Bench and Lifecycle pages all re-point at a newly selected UAV without
 * any of them importing from one another: each of those stores subscribes to this one
 * (see the `useFleetStore.subscribe(...)` calls at the bottom of each), the same way
 * lib/lifecycle/store.ts already sits alongside lib/store.ts and lib/testbench/store.ts
 * as an independent slice rather than one merged store.
 */
import { create } from "zustand";
import { fetchFleetOverview, fetchFleetRankings } from "./api";
import { DEFAULT_UAV_ID, type FleetMissionReliability, type FleetOverviewEntry } from "./types";

interface FleetStore {
  selectedUavId: string;
  setSelectedUavId: (uavId: string) => void;

  rankings: FleetOverviewEntry[];
  rankingsLoading: boolean;
  lastLoadedAt: number | null;

  /** GET /fleet/overview's joint-success read — polled alongside `rankings`, read by
   * FleetFormation3D's header readout. Not derived from `rankings`: it needs each UAV's
   * raw `mission_reliability.score`, which the roster rows don't carry. */
  fleetMissionReliability: FleetMissionReliability | null;

  loadRankings: () => Promise<void>;
  loadFleetMissionReliability: () => Promise<void>;
  startPolling: (intervalMs?: number) => () => void;
}

export const useFleetStore = create<FleetStore>((set, get) => ({
  selectedUavId: DEFAULT_UAV_ID,
  setSelectedUavId: (uavId) => set({ selectedUavId: uavId }),

  rankings: [],
  rankingsLoading: false,
  lastLoadedAt: null,
  fleetMissionReliability: null,

  loadRankings: async () => {
    set({ rankingsLoading: true });
    const rankings = await fetchFleetRankings();
    set({
      rankings: rankings ?? get().rankings,
      rankingsLoading: false,
      lastLoadedAt: Date.now(),
    });
  },

  loadFleetMissionReliability: async () => {
    const overview = await fetchFleetOverview();
    if (overview) set({ fleetMissionReliability: overview.fleet_mission_reliability });
  },

  /** Poll /fleet/rankings (+ /fleet/overview for the joint-success read) on an interval.
   * Returns a stop function — call it from the mounting component's effect cleanup. Safe
   * to call more than once; each call owns its own timer. */
  startPolling: (intervalMs = 2000) => {
    const tick = () => {
      void get().loadRankings();
      void get().loadFleetMissionReliability();
    };
    tick();
    const id = setInterval(tick, intervalMs);
    return () => clearInterval(id);
  },
}));
