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
import { fetchFleetRankings } from "./api";
import { DEFAULT_UAV_ID, type FleetOverviewEntry } from "./types";

interface FleetStore {
  selectedUavId: string;
  setSelectedUavId: (uavId: string) => void;

  rankings: FleetOverviewEntry[];
  rankingsLoading: boolean;
  lastLoadedAt: number | null;

  loadRankings: () => Promise<void>;
  startPolling: (intervalMs?: number) => () => void;
}

export const useFleetStore = create<FleetStore>((set, get) => ({
  selectedUavId: DEFAULT_UAV_ID,
  setSelectedUavId: (uavId) => set({ selectedUavId: uavId }),

  rankings: [],
  rankingsLoading: false,
  lastLoadedAt: null,

  loadRankings: async () => {
    set({ rankingsLoading: true });
    const rankings = await fetchFleetRankings();
    set({
      rankings: rankings ?? get().rankings,
      rankingsLoading: false,
      lastLoadedAt: Date.now(),
    });
  },

  /** Poll /fleet/rankings on an interval. Returns a stop function — call it from the
   * mounting component's effect cleanup. Safe to call more than once; each call owns
   * its own timer. */
  startPolling: (intervalMs = 2000) => {
    void get().loadRankings();
    const id = setInterval(() => void get().loadRankings(), intervalMs);
    return () => clearInterval(id);
  },
}));
