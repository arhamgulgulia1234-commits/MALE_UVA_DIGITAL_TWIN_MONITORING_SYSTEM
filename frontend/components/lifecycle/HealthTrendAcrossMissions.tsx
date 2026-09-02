"use client";

/**
 * End-of-mission health score, one point per past mission, oldest first.
 *
 * Reuses MultiLineChart — the same chart the live dashboard's EfficiencyTrendChart and
 * the Test Bench both already use — with a single series. The x-axis is mission
 * *sequence*, not a timestamp: two missions three weeks apart and two flown back to back
 * both advance the axis by one step, which is what makes this a trend of the engine's
 * flights rather than a calendar with mostly empty space on it.
 */
import { useEffect } from "react";
import { MultiLineChart } from "@/components/charts/MultiLineChart";
import { GlassCard } from "@/components/ui/GlassCard";
import { useLifecycleStore } from "@/lib/lifecycle/store";

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function HealthTrendAcrossMissions() {
  const summary = useLifecycleStore((s) => s.summary);
  const loading = useLifecycleStore((s) => s.loading);
  const loadSummary = useLifecycleStore((s) => s.loadSummary);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  const trend = summary?.mission_health_trend ?? [];
  const data = trend.map((point, i) => ({
    mission: i + 1,
    health: point.health_score,
  }));

  return (
    <GlassCard
      title="Health Trend Across Missions"
      subtitle={
        trend.length > 0
          ? `${trend.length} mission${trend.length === 1 ? "" : "s"} recorded, end-of-mission score`
          : "End-of-mission health score, oldest to newest"
      }
      glow="none"
      bodyClassName="p-4"
    >
      {trend.length === 0 ? (
        <p className="py-10 text-center text-xs text-slate-400">
          {loading ? "Loading mission history…" : "No completed missions with a report yet."}
        </p>
      ) : (
        <>
          <MultiLineChart
            data={data}
            series={[{ key: "health", color: "#4ab9c6", label: "Health score" }]}
            xKey="mission"
            xTickFormatter={(v) => `#${v}`}
            height={220}
          />
          <div className="mt-2 flex justify-between text-[10px] text-slate-400">
            <span>{formatWhen(trend[0]?.started_at ?? null)}</span>
            <span>{formatWhen(trend[trend.length - 1]?.ended_at ?? null)}</span>
          </div>
        </>
      )}
    </GlassCard>
  );
}
