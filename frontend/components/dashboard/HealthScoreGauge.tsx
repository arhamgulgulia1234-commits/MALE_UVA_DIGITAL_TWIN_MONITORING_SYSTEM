"use client";

import { RadialGauge } from "@/components/gauges/RadialGauge";
import { GlassCard } from "@/components/ui/GlassCard";
import { useTelemetryStore } from "@/lib/store";
import type { SubsystemScores } from "@/lib/types";

const SUBSYSTEM_LABEL: Record<keyof SubsystemScores, string> = {
  cylinder: "Cylinder",
  lubrication: "Lubrication",
  cooling: "Cooling",
  fuel: "Fuel",
  turbo: "Turbo",
};

function scoreColor(score: number): string {
  if (score >= 85) return "#22d3a8";
  if (score >= 60) return "#f5a623";
  return "#ef4a5f";
}

export function HealthScoreGauge() {
  const latest = useTelemetryStore((s) => s.latest);
  const overall = latest?.health.overall_score ?? 100;
  const color = scoreColor(overall);
  const subsystems = latest?.health.subsystem_scores;

  return (
    <GlassCard title="Engine Health Score" glow="cyan" className="h-full" bodyClassName="flex flex-col items-center gap-5 p-5">
      <RadialGauge value={overall} size={200} strokeWidth={14} color={color}>
        <span className="tabular text-5xl font-bold" style={{ color }}>
          {Math.round(overall)}
        </span>
        <span className="mt-1 text-[10px] uppercase tracking-[0.2em] text-slate-500">Overall</span>
      </RadialGauge>

      <div className="grid w-full grid-cols-1 gap-1.5 sm:grid-cols-5 lg:grid-cols-1">
        {(Object.keys(SUBSYSTEM_LABEL) as (keyof SubsystemScores)[]).map((key) => {
          const v = subsystems?.[key] ?? 100;
          const c = scoreColor(v);
          return (
            <div key={key} className="flex items-center gap-2 text-xs">
              <span className="w-20 shrink-0 text-slate-400">{SUBSYSTEM_LABEL[key]}</span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-base-border">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{ width: `${Math.max(0, Math.min(100, v))}%`, background: c }}
                />
              </div>
              <span className="tabular w-8 shrink-0 text-right text-slate-300">{Math.round(v)}</span>
            </div>
          );
        })}
      </div>
    </GlassCard>
  );
}
