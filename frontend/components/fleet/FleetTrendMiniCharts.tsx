"use client";

/**
 * One small health-trend chart per UAV, fed by the same `/lifecycle/summary`
 * `mission_health_trend` series the Lifecycle view's HealthTrendAcrossMissions
 * already plots — reused here for all three UAVs at once rather than only the
 * currently selected one, via `fetchLifecycleSummary(uavId)`'s explicit override.
 */
import { useEffect, useState } from "react";
import { MultiLineChart } from "@/components/charts/MultiLineChart";
import { GlassCard } from "@/components/ui/GlassCard";
import { fetchLifecycleSummary } from "@/lib/lifecycle/api";
import { UAV_IDS } from "@/lib/fleet/types";

interface TrendState {
  loading: boolean;
  points: { mission: number; health: number }[];
}

export function FleetTrendMiniCharts() {
  const [trends, setTrends] = useState<Record<string, TrendState>>({});

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const results = await Promise.all(
        UAV_IDS.map(async (uavId) => {
          try {
            const summary = await fetchLifecycleSummary(uavId);
            const points = (summary.mission_health_trend ?? []).map((p, i) => ({
              mission: i + 1,
              health: p.health_score,
            }));
            return [uavId, { loading: false, points }] as const;
          } catch {
            return [uavId, { loading: false, points: [] }] as const;
          }
        })
      );
      if (!cancelled) setTrends(Object.fromEntries(results));
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {UAV_IDS.map((uavId) => {
        const trend = trends[uavId];
        return (
          <GlassCard key={uavId} title={`${uavId} — Health Trend`} glow="none" bodyClassName="p-3">
            {!trend || trend.points.length === 0 ? (
              <p className="py-6 text-center text-[11px] text-slate-500">
                {trend ? "No completed missions yet" : "Loading…"}
              </p>
            ) : (
              <MultiLineChart
                data={trend.points}
                series={[{ key: "health", color: "#3fd0e0", label: "Health score" }]}
                xKey="mission"
                xTickFormatter={(v) => `#${v}`}
                height={110}
              />
            )}
          </GlassCard>
        );
      })}
    </div>
  );
}
