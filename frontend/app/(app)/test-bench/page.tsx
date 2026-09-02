"use client";

/**
 * Test Bench — a mode, not a panel.
 *
 * Deliberately its own route rather than a tab inside the dashboard. The live page mounts
 * `useTelemetryStream`, and this one does not: no WebSocket is opened here at all, so
 * there is no path by which a scenario chart could be fed a live frame, or a live gauge a
 * simulated one. That separation is the whole safety argument, and the persistent banner
 * below makes it visible rather than merely true — during a demo, a chart of engine
 * temperatures looks the same whether the numbers came from a running engine or from a
 * hypothetical one, and getting that wrong in front of an evaluator would be worse than
 * not having the feature.
 */
import { useFleetStore } from "@/lib/fleet/store";
import { UAV_IDS } from "@/lib/fleet/types";
import { MissionPresetCards } from "@/components/testbench/MissionPresetCards";
import { OptimizerPanel } from "@/components/testbench/OptimizerPanel";
import { ScenarioBuilder } from "@/components/testbench/ScenarioBuilder";
import { ScenarioHistoryList } from "@/components/testbench/ScenarioHistoryList";
import { ScenarioResultsView } from "@/components/testbench/ScenarioResultsView";

export default function TestBenchPage() {
  const selectedUavId = useFleetStore((s) => s.selectedUavId);
  const setSelectedUavId = useFleetStore((s) => s.setSelectedUavId);

  return (
    <>
      {/* Distinct header treatment from the live dashboard's cyan: amber hazard striping,
          so the two modes are never confused at a glance. */}
      <header className="border-b border-status-amber/40 bg-base-bg">
        <div
          className="h-1 w-full"
          style={{
            backgroundImage:
              "repeating-linear-gradient(45deg, rgba(213,152,52,0.65) 0 10px, transparent 10px 20px)",
          }}
        />
        <div className="page-container flex flex-wrap items-center gap-4 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-status-amber/40 bg-status-amber/10">
              <span className="font-display text-sm font-bold text-status-amber">TB</span>
            </div>
            <div>
              <h1 className="font-display text-base font-bold tracking-wide text-slate-100 sm:text-lg">
                Test Bench
              </h1>
              <p className="text-[11px] text-slate-400">
                What-if scenarios &amp; operating-point optimisation
              </p>
            </div>
          </div>

          {/* Phase 6: same persistent UAV selector as MissionHeader on the Live
              Dashboard — the optimizer's "use current engine health", "apply preset"
              and the live map marker below all act on whichever UAV this picks. */}
          <label className="ml-auto flex items-center gap-1.5 text-[11px] lg:ml-0">
            <span className="uppercase tracking-wider text-slate-400">UAV</span>
            <select
              value={selectedUavId}
              onChange={(e) => setSelectedUavId(e.target.value)}
              className="rounded-md border border-base-border bg-base-panel px-2 py-1 font-display text-xs font-semibold text-slate-200 outline-none focus:border-status-amber/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-amber"
            >
              {UAV_IDS.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>

          <div className="w-full rounded-md border border-status-amber/50 bg-status-amber/10 px-3 py-1.5 text-center lg:w-auto">
            <span className="font-display text-xs font-bold uppercase tracking-[0.12em] text-status-amber">
              ⚠ Simulation — Not Live Data
            </span>
          </div>
        </div>
      </header>

      <main className="page-container flex-1 space-y-6 py-6">
        <MissionPresetCards />

        <OptimizerPanel />

        <section className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,420px)_1fr]">
          <div className="space-y-6">
            <ScenarioBuilder />
            <ScenarioHistoryList />
          </div>
          <ScenarioResultsView />
        </section>
      </main>

      <footer className="border-t border-status-amber/30 bg-base-bg py-3 text-center">
        <p className="text-[10px] uppercase tracking-[0.11em] text-status-amber/90">
          Every number on this page is simulated · nothing here is recorded as a flight
        </p>
      </footer>
    </>
  );
}
