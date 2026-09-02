"use client";

/**
 * Total operating hours and current wear per fault type — the engine's cumulative
 * ledger, not this mission's telemetry.
 *
 * The overall gauge and the per-fault bars reuse HealthScoreGauge's visual language
 * (RadialGauge, the same go/caution/red thresholds) rather than inventing a second
 * style for "how worn is this engine" right next to "how healthy is this mission" — the
 * two numbers answer different questions (lifetime wear vs. this flight's residuals) but
 * an operator should read them with the same eye. Wear (0-1, higher is worse) is
 * converted to the same 0-100-higher-is-better scale health scores already use before
 * the thresholds are applied, so "amber" means the same thing in both places.
 */
import { useEffect } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { RadialGauge } from "@/components/gauges/RadialGauge";
import { useLifecycleStore } from "@/lib/lifecycle/store";
import { FAULT_CATALOG } from "@/lib/types";
import type { FaultType } from "@/lib/types";

function conditionColor(score: number): string {
  if (score >= 85) return "#37af92";
  if (score >= 60) return "#d59834";
  return "#da6978";
}

function wearToCondition(wear: number): number {
  return Math.max(0, Math.min(100, 100 * (1 - wear)));
}

/**
 * Adaptive precision so a genuinely tiny-but-nonzero value doesn't round to "0.0" and
 * read as broken. A short demo mission (or one flown at a high time-scale, which still
 * bills real simulated seconds — see `sim_time_s` in simulation_loop.py) can easily
 * accumulate under a tenth of an hour; a fixed one-decimal display would show "0.0" for
 * every one of those and look like the counter was not moving at all.
 */
function formatHours(hours: number): string {
  if (hours <= 0) return "0.000";
  if (hours >= 100) return hours.toFixed(0);
  if (hours >= 10) return hours.toFixed(1);
  if (hours >= 1) return hours.toFixed(2);
  return hours.toFixed(3);
}

export function LifecycleOverviewPanel() {
  const summary = useLifecycleStore((s) => s.summary);
  const loading = useLifecycleStore((s) => s.loading);
  const error = useLifecycleStore((s) => s.error);
  const loadSummary = useLifecycleStore((s) => s.loadSummary);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  const wear = summary?.current_wear_state;
  const worstEntry = wear
    ? (Object.entries(wear) as [FaultType, number][]).reduce(
        (worst, entry) => (entry[1] > worst[1] ? entry : worst),
        ["", 0] as [string, number]
      )
    : null;
  const worstWear = worstEntry?.[1] ?? 0;
  const overallCondition = wearToCondition(worstWear);
  const worstLabel =
    worstWear > 1e-3
      ? FAULT_CATALOG.find((f) => f.type === worstEntry?.[0])?.label
      : null;

  // Highest wear first, so the row that actually limits the engine's condition is the
  // one an operator sees without scrolling.
  const rows = wear
    ? FAULT_CATALOG.map((meta) => ({ meta, wear: wear[meta.type] ?? 0 })).sort(
        (a, b) => b.wear - a.wear
      )
    : [];

  return (
    <GlassCard
      title="Engine Life-Cycle"
      subtitle="Cumulative wear across every mission this engine has flown"
      glow="cyan"
      bodyClassName="space-y-5 p-5"
    >
      {error && (
        <p className="text-xs text-status-red">
          {error.message} — is the backend running?
        </p>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[auto_1fr]">
        <div className="flex flex-col items-center gap-3">
          <RadialGauge value={overallCondition} size={168} strokeWidth={12} color={conditionColor(overallCondition)}>
            <span
              className="tabular text-4xl font-bold"
              style={{ color: conditionColor(overallCondition) }}
            >
              {Math.round(overallCondition)}
            </span>
            <span className="mt-1 text-[10px] uppercase tracking-[0.11em] text-slate-500">
              Condition
            </span>
          </RadialGauge>
          {worstLabel && (
            <p className="max-w-[180px] text-center text-[10px] text-slate-500">
              Limited by <span className="text-slate-300">{worstLabel}</span>
            </p>
          )}
          <div className="w-full rounded-lg border border-base-border/70 bg-base-panel2/40 px-3 py-2.5 text-center">
            <span className="tabular block text-2xl font-semibold text-slate-100">
              {loading && !summary ? "—" : formatHours(summary?.total_operating_hours ?? 0)}
            </span>
            <span className="block text-[10px] uppercase tracking-wider text-slate-500">
              Total operating hours
            </span>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {rows.length === 0 && loading && (
            <p className="col-span-2 py-6 text-center text-xs text-slate-500">
              Loading wear state…
            </p>
          )}
          {rows.map(({ meta, wear: w }) => {
            const condition = wearToCondition(w);
            const c = conditionColor(condition);
            return (
              <div key={meta.type} className="flex items-center gap-2 text-xs" title={meta.description}>
                <span className="w-32 shrink-0 truncate text-slate-400">{meta.label}</span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-base-border">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${Math.max(2, w * 100)}%`, background: c }}
                  />
                </div>
                <span className="tabular w-12 shrink-0 text-right text-slate-300">
                  {(w * 100).toFixed(0)}%
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </GlassCard>
  );
}
