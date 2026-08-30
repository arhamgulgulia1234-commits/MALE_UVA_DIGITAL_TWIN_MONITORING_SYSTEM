"use client";

/**
 * Life-cycle page state — a third small zustand store, alongside lib/store.ts (live
 * telemetry) and lib/testbench/store.ts (what-if scenarios).
 *
 * Living in one store rather than four independent `useState`/`useEffect` pairs is what
 * makes "log a maintenance action and see the wear state drop immediately after" (the
 * point of `LogMaintenanceActionForm`) just work: the form calls `applyMaintenanceAction`,
 * that refreshes `summary`, and `LifecycleOverviewPanel` re-renders off the same field
 * without the two components needing any direct relationship.
 */
import { create } from "zustand";
import { useFleetStore } from "../fleet/store";
import {
  LifecycleError,
  fetchLifecycleSummary,
  postMaintenanceAction,
} from "./api";
import type { LifecycleSummary, MaintenanceActionRequest } from "./types";

interface LifecycleStore {
  summary: LifecycleSummary | null;
  loading: boolean;
  error: LifecycleError | null;

  submitting: boolean;
  submitError: LifecycleError | null;
  lastActionAt: number | null;

  loadSummary: () => Promise<void>;
  applyMaintenanceAction: (req: MaintenanceActionRequest) => Promise<boolean>;
}

export const useLifecycleStore = create<LifecycleStore>((set, get) => ({
  summary: null,
  loading: false,
  error: null,

  submitting: false,
  submitError: null,
  lastActionAt: null,

  loadSummary: async () => {
    set({ loading: true, error: null });
    try {
      const summary = await fetchLifecycleSummary();
      set({ summary, loading: false });
    } catch (e) {
      set({
        loading: false,
        error: e instanceof LifecycleError ? e : new LifecycleError("Failed to load", 0),
      });
    }
  },

  applyMaintenanceAction: async (req) => {
    set({ submitting: true, submitError: null });
    try {
      await postMaintenanceAction(req);
      // Re-fetch the whole summary rather than splicing the response's bare ledger into
      // `summary` in place — the action also changed `maintenance_actions`, which the
      // POST response does not carry (see MaintenanceActionResponse), and a full
      // summary refresh is one cheap GET rather than two sources of truth to reconcile.
      await get().loadSummary();
      set({ submitting: false, lastActionAt: Date.now() });
      return true;
    } catch (e) {
      set({
        submitting: false,
        submitError:
          e instanceof LifecycleError ? e : new LifecycleError("Failed to apply", 0),
      });
      return false;
    }
  },
}));

// Phase 6: re-fetch this UAV's ledger whenever the fleet selector points somewhere
// else, so the Lifecycle view never keeps showing a previous UAV's summary under a
// newly selected one's header.
useFleetStore.subscribe((state, prevState) => {
  if (state.selectedUavId === prevState.selectedUavId) return;
  void useLifecycleStore.getState().loadSummary();
});
