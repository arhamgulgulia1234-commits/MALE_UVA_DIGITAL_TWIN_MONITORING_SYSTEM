"use client";

/**
 * Fleet — squadron-wide health across all three UAVs.
 *
 * Deliberately its own route, unlike Lifecycle and the performance map, which were
 * folded into the Live Dashboard: every other tab is scoped to one UAV at a time (via
 * MissionHeader's selector), and this is the one view that is not — it exists
 * specifically to look across all of them at once, so folding it into a single-UAV
 * page would defeat the point.
 *
 * No WebSocket here, same reasoning as the Test Bench: this page polls GET
 * /fleet/rankings on a 2s timer (see useFleetStore.startPolling) rather than opening a
 * socket, which keeps it simple and is plenty fresh for a roster view.
 */
import { useEffect } from "react";
import dynamic from "next/dynamic";
import { ModeNav } from "@/components/nav/ModeNav";
import { FleetSummaryHeader } from "@/components/fleet/FleetSummaryHeader";
import { FleetRosterGrid } from "@/components/fleet/FleetRosterGrid";
import { FleetTrendMiniCharts } from "@/components/fleet/FleetTrendMiniCharts";
import { useFleetStore } from "@/lib/fleet/store";

const FleetFormation3D = dynamic(
  () => import("@/components/fleet/FleetFormation3D").then((m) => m.FleetFormation3D),
  {
    ssr: false,
    loading: () => (
      <div className="glass-panel flex h-[340px] w-full items-center justify-center text-xs text-slate-500">
        Loading fleet formation…
      </div>
    ),
  }
);

export default function FleetPage() {
  const rankings = useFleetStore((s) => s.rankings);
  const rankingsLoading = useFleetStore((s) => s.rankingsLoading);
  const fleetMissionReliability = useFleetStore((s) => s.fleetMissionReliability);
  const startPolling = useFleetStore((s) => s.startPolling);

  useEffect(() => {
    const stop = startPolling(2000);
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b border-base-border bg-base-bg">
        <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-4 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-status-cyan/40 bg-status-cyan/10">
              <span className="font-display text-sm font-bold text-status-cyan">FL</span>
            </div>
            <div>
              <h1 className="font-display text-base font-bold tracking-wide text-slate-100 sm:text-lg">
                Fleet Overview
              </h1>
              <p className="text-[11px] text-slate-500">3 UAVs — ranked most urgent first</p>
            </div>
          </div>
          <ModeNav className="ml-auto" />
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1800px] flex-1 space-y-6 px-4 py-6 sm:px-6">
        {rankings.length === 0 ? (
          <p className="py-16 text-center text-sm text-slate-500">
            {rankingsLoading ? "Loading fleet…" : "No fleet data yet."}
          </p>
        ) : (
          <>
            <FleetSummaryHeader roster={rankings} />
            <FleetFormation3D roster={rankings} fleetMissionReliability={fleetMissionReliability} />
            <FleetRosterGrid roster={rankings} />
            <FleetTrendMiniCharts />
          </>
        )}
      </main>
    </div>
  );
}
