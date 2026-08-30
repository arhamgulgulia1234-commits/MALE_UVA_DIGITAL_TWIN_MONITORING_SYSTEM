"use client";

import { RadialGauge } from "@/components/gauges/RadialGauge";
import { GlassCard } from "@/components/ui/GlassCard";
import { useTelemetryStore } from "@/lib/store";
import { useTweenedNumber } from "@/hooks/useTweenedNumber";
import type { SubsystemScores } from "@/lib/types";

const SUBSYSTEM_LABEL: Partial<Record<keyof SubsystemScores, string>> = {
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

function SubsystemRow({ label, value }: { label: string; value: number }) {
  const smoothed = useTweenedNumber(value);
  const c = scoreColor(smoothed);
  return (
    <div className="flex items-center gap-2.5 text-xs">
      <span className="w-20 shrink-0 tracking-wide text-slate-400">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-base-border">
        <div
          className="h-full rounded-full transition-[width] duration-300 ease-out"
          style={{ width: `${Math.max(0, Math.min(100, smoothed))}%`, background: c }}
        />
      </div>
      <span className="tabular w-8 shrink-0 text-right font-medium text-slate-300">
        {Math.round(smoothed)}
      </span>
    </div>
  );
}

export function HealthScoreGauge() {
  const latest = useTelemetryStore((s) => s.latest);
  const overall = latest?.health.overall_score ?? 100;
  const smoothedOverall = useTweenedNumber(overall);
  const color = scoreColor(smoothedOverall);
  const subsystems = latest?.health.subsystem_scores;

  return (
    <GlassCard
      title="Engine Health Score"
      glow="cyan"
      className="h-full ring-1 ring-white/[0.03]"
      bodyClassName="flex flex-col items-center gap-6 p-5"
    >
      <RadialGauge value={overall} size={200} strokeWidth={14} color={color} showTicks needleMarker>
        <span className="tabular text-6xl font-bold leading-none" style={{ color }}>
          {Math.round(smoothedOverall)}
        </span>
        <span className="mt-2 text-[10px] font-medium uppercase tracking-[0.22em] text-slate-500">
          Overall
        </span>
      </RadialGauge>

      <div className="grid w-full grid-cols-1 gap-2 sm:grid-cols-5 lg:grid-cols-1">
        {(Object.keys(SUBSYSTEM_LABEL) as (keyof SubsystemScores)[]).map((key) => (
          <SubsystemRow key={key} label={SUBSYSTEM_LABEL[key]!} value={subsystems?.[key] ?? 100} />
        ))}
      </div>
    </GlassCard>
  );
}
