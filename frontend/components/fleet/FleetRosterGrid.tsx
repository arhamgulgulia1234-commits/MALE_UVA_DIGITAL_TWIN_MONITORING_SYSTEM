"use client";

/**
 * One card per UAV, ranked most-urgent first (the same order GET /fleet/rankings
 * already returns — this component does not re-sort). The most urgent card gets a
 * colored ring and a badge, not just its position, so it still reads as "this one
 * needs attention" after the grid re-flows on a narrow screen or the ranking shifts.
 *
 * Clicking a card selects that UAV in the shared fleet store (the same `selectedUavId`
 * MissionHeader's dropdown writes to) and navigates to the Live Dashboard — which
 * already carries the Lifecycle panels — or to the Test Bench via the secondary link.
 * Neither destination page needed any change: they already read `selectedUavId` from
 * the store this sets.
 */
import { useRouter } from "next/navigation";
import { RadialGauge } from "@/components/gauges/RadialGauge";
import { GlassCard } from "@/components/ui/GlassCard";
import { useFleetStore } from "@/lib/fleet/store";
import type { FleetOverviewEntry } from "@/lib/fleet/types";

function scoreColor(score: number): string {
  if (score >= 85) return "#37af92";
  if (score >= 60) return "#d59834";
  return "#da6978";
}

const STATUS_LABEL: Record<FleetOverviewEntry["status"], string> = {
  live: "Recording",
  replay: "Replaying",
  idle: "Idle",
};

const STATUS_TONE: Record<FleetOverviewEntry["status"], string> = {
  live: "text-status-go",
  replay: "text-status-amber",
  idle: "text-slate-500",
};

export function FleetRosterGrid({ roster }: { roster: FleetOverviewEntry[] }) {
  const router = useRouter();
  const setSelectedUavId = useFleetStore((s) => s.setSelectedUavId);

  const goTo = (uavId: string, path: string) => {
    setSelectedUavId(uavId);
    router.push(path);
  };

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {roster.map((entry, index) => {
        const isMostUrgent = index === 0 && roster.length > 1;
        const health = entry.overall_health ?? 100;
        const color = scoreColor(health);
        return (
          <GlassCard
            key={entry.uav_id}
            glow="none"
            className={
              isMostUrgent
                ? "border-status-red/70 ring-2 ring-status-red/50"
                : undefined
            }
            bodyClassName="flex flex-col gap-3 p-4"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h3 className="font-display text-sm font-bold text-slate-100">
                  {entry.uav_id}
                </h3>
                {isMostUrgent && (
                  <span className="rounded-full border border-status-red/60 bg-status-red/15 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-status-red">
                    Most Urgent
                  </span>
                )}
              </div>
              <span className={`text-[11px] font-medium ${STATUS_TONE[entry.status]}`}>
                {STATUS_LABEL[entry.status]}
              </span>
            </div>

            <div className="flex items-center gap-4">
              <RadialGauge value={health} size={72} strokeWidth={7} color={color}>
                <span className="tabular text-lg font-bold" style={{ color }}>
                  {Math.round(health)}
                </span>
              </RadialGauge>
              <div className="flex flex-1 flex-col gap-1 text-[11px] text-slate-400">
                <div className="flex justify-between">
                  <span>Worst subsystem</span>
                  <span className="text-slate-200">
                    {entry.worst_subsystem ?? "—"}
                    {entry.worst_subsystem_score != null
                      ? ` (${Math.round(entry.worst_subsystem_score)})`
                      : ""}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>RUL</span>
                  <span className="text-slate-200">
                    {entry.rul_minutes != null ? `${Math.round(entry.rul_minutes)} min` : "—"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Active faults</span>
                  <span className="text-slate-200">{entry.active_fault_count}</span>
                </div>
                <div className="flex justify-between">
                  <span>Total hours</span>
                  <span className="text-slate-200">{entry.total_operating_hours.toFixed(1)}</span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2 text-xs font-bold">
              <span
                className={
                  entry.mission_reliability_recommendation === "NO-GO"
                    ? "rounded border border-status-red/50 bg-status-red/10 px-2 py-1 text-status-red"
                    : entry.mission_reliability_recommendation === "CAUTION"
                    ? "rounded border border-status-amber/50 bg-status-amber/10 px-2 py-1 text-status-amber"
                    : "rounded border border-status-go/50 bg-status-go/10 px-2 py-1 text-status-go"
                }
              >
                {entry.mission_reliability_recommendation ?? "—"}
              </span>
              <span
                className={
                  entry.recovery_reliability_recommendation === "RTB-AT-RISK"
                    ? "rounded-full border border-status-red/50 bg-status-red/10 px-2 py-1 text-status-red"
                    : entry.recovery_reliability_recommendation === "RTB-CAUTION"
                    ? "rounded-full border border-status-amber/50 bg-status-amber/10 px-2 py-1 text-status-amber"
                    : "rounded-full border border-status-go/50 bg-status-go/10 px-2 py-1 text-status-go"
                }
              >
                {entry.recovery_reliability_recommendation ?? "—"}
              </span>
            </div>

            <div className="mt-auto flex items-center gap-2 border-t border-base-border/60 pt-3 text-[11px]">
              <button
                onClick={() => goTo(entry.uav_id, "/")}
                className="flex-1 rounded-md border border-status-cyan/40 bg-status-cyan/10 py-1.5 font-semibold text-status-cyan transition-colors hover:bg-status-cyan/20"
              >
                Live Dashboard
              </button>
              <button
                onClick={() => goTo(entry.uav_id, "/test-bench")}
                className="flex-1 rounded-md border border-status-amber/40 bg-status-amber/10 py-1.5 font-semibold text-status-amber transition-colors hover:bg-status-amber/20"
              >
                Test Bench
              </button>
            </div>
          </GlassCard>
        );
      })}
    </div>
  );
}
