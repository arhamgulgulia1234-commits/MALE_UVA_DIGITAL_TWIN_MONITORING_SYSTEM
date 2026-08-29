"use client";

/**
 * Past what-if runs from the scenario_runs table.
 *
 * Only parameters and summaries are stored, never the time-series, so "reload" repopulates
 * the builder rather than re-rendering a saved chart. That is not a limitation worked
 * around — the scenario engine is deterministic given its parameters, so re-running is
 * exact, and storing 1800 frames per what-if would swamp the flight recordings that
 * actually cannot be regenerated.
 */
import { useEffect } from "react";
import clsx from "clsx";
import { GlassCard } from "@/components/ui/GlassCard";
import { useTestBenchStore } from "@/lib/testbench/store";
import type { ScenarioRunRow, Verdict } from "@/lib/testbench/types";

const VERDICT_CLASS: Record<Verdict, string> = {
  PASS: "border-status-go/40 bg-status-go/10 text-status-go",
  CAUTION: "border-status-amber/40 bg-status-amber/10 text-status-amber",
  FAIL: "border-status-red/40 bg-status-red/10 text-status-red",
};

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  // The backend stamps these in UTC without an offset suffix, so say so rather than
  // letting the browser silently read them as local time.
  const date = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function conditions(run: ScenarioRunRow): string {
  const temp =
    run.ambient_temperature_c === null
      ? "ISA"
      : `${run.ambient_temperature_c.toFixed(0)}°C`;
  return `${run.altitude_m.toFixed(0)} m · ${temp} · ${run.duration_minutes.toFixed(0)} min`;
}

export function ScenarioHistoryList() {
  const runs = useTestBenchStore((s) => s.runs);
  const loading = useTestBenchStore((s) => s.runsLoading);
  const selected = useTestBenchStore((s) => s.selectedRun);
  const loadRuns = useTestBenchStore((s) => s.loadRuns);
  const selectRun = useTestBenchStore((s) => s.selectRun);
  const loadRunIntoDraft = useTestBenchStore((s) => s.loadRunIntoDraft);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  return (
    <GlassCard
      title="Scenario History"
      subtitle={`${runs.length} past what-if run${runs.length === 1 ? "" : "s"}`}
      glow="none"
      headerRight={
        <button
          type="button"
          onClick={loadRuns}
          className="rounded-md border border-base-border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-400 transition-colors hover:border-status-cyan/40 hover:text-status-cyan"
        >
          Refresh
        </button>
      }
      bodyClassName="p-3"
    >
      {loading && runs.length === 0 && (
        <p className="py-6 text-center text-xs text-slate-500">Loading…</p>
      )}

      {!loading && runs.length === 0 && (
        <p className="py-6 text-center text-xs text-slate-500">
          No scenarios run yet. Every run you execute is logged here — separately from the
          missions table, because these are hypothetical, not flown.
        </p>
      )}

      <ul className="max-h-[420px] space-y-1.5 overflow-y-auto pr-1">
        {runs.map((run) => {
          const isOpen = selected?.id === run.id;
          return (
            <li
              key={run.id}
              className="rounded-lg border border-base-border/70 bg-base-panel2/30 p-2.5"
            >
              <div className="flex items-start gap-2">
                <span
                  className={clsx(
                    "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wider",
                    VERDICT_CLASS[run.verdict]
                  )}
                >
                  {run.verdict}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[11px] font-medium text-slate-200">
                    {run.label || `Scenario #${run.id}`}
                  </p>
                  <p className="tabular text-[10px] text-slate-500">{conditions(run)}</p>
                </div>
                <span className="tabular shrink-0 text-[10px] text-slate-600">
                  {formatWhen(run.created_at)}
                </span>
              </div>

              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-500">
                <span>
                  Min health{" "}
                  <span className="tabular text-slate-300">
                    {run.min_health_score?.toFixed(0) ?? "—"}
                  </span>
                </span>
                <span className="capitalize">
                  Worst <span className="text-slate-300">{run.worst_subsystem ?? "—"}</span>
                </span>
                <span className="ml-auto flex gap-2">
                  <button
                    type="button"
                    onClick={() => selectRun(isOpen ? null : run.id)}
                    className="text-slate-400 underline-offset-2 hover:text-status-cyan hover:underline"
                  >
                    {isOpen ? "Hide" : "Details"}
                  </button>
                  <button
                    type="button"
                    onClick={() => loadRunIntoDraft(run)}
                    title="Load these parameters back into the builder"
                    className="text-slate-400 underline-offset-2 hover:text-status-cyan hover:underline"
                  >
                    Reload
                  </button>
                </span>
              </div>

              {isOpen && selected?.summary && (
                <div className="mt-2 space-y-1.5 border-t border-base-border/70 pt-2">
                  <p className="text-[11px] leading-relaxed text-slate-300">
                    {selected.summary.headline}
                  </p>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] text-slate-500">
                    <span>
                      Peak CHT{" "}
                      <span className="tabular text-slate-300">
                        {selected.summary.peak_cht_c} °C
                      </span>
                    </span>
                    <span>
                      Peak EGT{" "}
                      <span className="tabular text-slate-300">
                        {selected.summary.peak_egt_c} °C
                      </span>
                    </span>
                    <span>
                      Min oil press.{" "}
                      <span className="tabular text-slate-300">
                        {selected.summary.min_oil_pressure_kpa} kPa
                      </span>
                    </span>
                    <span>
                      Fuel{" "}
                      <span className="tabular text-slate-300">
                        {selected.summary.total_fuel_litres} L
                      </span>
                    </span>
                  </div>
                  {selected.summary.limit_excursions.length > 0 && (
                    <ul className="space-y-0.5">
                      {selected.summary.limit_excursions.map((e) => (
                        <li
                          key={`${e.parameter}-${e.direction}`}
                          className="text-[10px] text-status-red"
                        >
                          {e.parameter} {e.direction} {e.limit}
                          {e.unit} for {e.duration_min.toFixed(1)} min (peak {e.peak_value})
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </GlassCard>
  );
}
