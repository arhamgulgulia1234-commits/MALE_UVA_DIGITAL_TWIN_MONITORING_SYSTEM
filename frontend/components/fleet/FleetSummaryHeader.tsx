"use client";

/**
 * Fleet-wide rollup: how many UAVs are GO/CAUTION/NO-GO right now, and how many faults
 * are active across the whole squadron. One glance answers "does anything need
 * attention today" before the operator drills into any one UAV's roster card.
 */
import { GlassCard } from "@/components/ui/GlassCard";
import type { Recommendation } from "@/lib/types";
import type { FleetOverviewEntry } from "@/lib/fleet/types";

const RECOMMENDATION_STYLE: Record<Recommendation, { label: string; tone: string }> = {
  GO: { label: "GO", tone: "text-status-go" },
  CAUTION: { label: "CAUTION", tone: "text-status-amber" },
  "NO-GO": { label: "NO-GO", tone: "text-status-red" },
};

export function FleetSummaryHeader({ roster }: { roster: FleetOverviewEntry[] }) {
  const counts: Record<Recommendation, number> = { GO: 0, CAUTION: 0, "NO-GO": 0 };
  let activeFaults = 0;
  for (const entry of roster) {
    const rec = entry.mission_reliability_recommendation;
    if (rec) counts[rec] += 1;
    activeFaults += entry.active_fault_count;
  }

  return (
    <GlassCard title="Fleet Status" subtitle={`${roster.length} UAVs in the squadron`} glow="none">
      <div className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-4">
        {(Object.keys(RECOMMENDATION_STYLE) as Recommendation[]).map((rec) => (
          <div key={rec} className="flex flex-col items-center gap-1 rounded-lg border border-base-border/70 bg-base-panel/50 py-3">
            <span className={`font-display text-2xl font-bold ${RECOMMENDATION_STYLE[rec].tone}`}>
              {counts[rec]}
            </span>
            <span className="text-[10px] uppercase tracking-[0.08em] text-slate-500">
              {RECOMMENDATION_STYLE[rec].label}
            </span>
          </div>
        ))}
        <div className="flex flex-col items-center gap-1 rounded-lg border border-base-border/70 bg-base-panel/50 py-3">
          <span className="font-display text-2xl font-bold text-slate-200">{activeFaults}</span>
          <span className="text-[10px] uppercase tracking-[0.08em] text-slate-500">
            Active Faults
          </span>
        </div>
      </div>
    </GlassCard>
  );
}
