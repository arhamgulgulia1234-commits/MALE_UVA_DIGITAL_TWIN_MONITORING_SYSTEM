"use client";

import clsx from "clsx";
import { useMemo } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { MultiLineChart, type SeriesDef } from "@/components/charts/MultiLineChart";
import { useTelemetryStore } from "@/lib/store";
import type { EfficiencyTrend } from "@/lib/types";

const SERIES: SeriesDef[] = [
  { key: "bsfc", color: "#4ab9c6", label: "BSFC (g/kWh)", yAxisId: "left" },
  { key: "cov", color: "#d59834", label: "COV(IMEP) %", yAxisId: "right" },
];

const TREND_STYLE: Record<EfficiencyTrend, { label: string; className: string }> = {
  stable: { label: "STABLE", className: "text-status-go border-status-go/40 bg-status-go/10" },
  degrading: {
    label: "DEGRADING",
    className: "text-status-amber border-status-amber/40 bg-status-amber/10",
  },
  improving: {
    label: "IMPROVING",
    className: "text-status-cyan border-status-cyan/40 bg-status-cyan/10",
  },
};

/**
 * Fuel efficiency over the mission, alongside combustion stability.
 *
 * These two are paired deliberately: BSFC says the engine is wasting fuel but not why,
 * and COV(IMEP) rising alongside it points at combustion quality specifically rather
 * than, say, a slipping turbo. Both are leading indicators — they move before any single
 * channel crosses a limit.
 */
export function EfficiencyTrendChart() {
  const buffer = useTelemetryStore((s) => s.buffer);
  const latest = useTelemetryStore((s) => s.latest);

  const data = useMemo(() => {
    if (buffer.length === 0) return [];
    const lastT = buffer[buffer.length - 1]!.timestamp;
    return buffer
      .filter((f) => f.bsfc_g_per_kwh != null)
      .map((f) => ({
        t: Math.round((f.timestamp - lastT) * 10) / 10,
        bsfc: f.bsfc_g_per_kwh ?? 0,
        cov: f.combustion_instability_pct ?? 0,
      }));
  }, [buffer]);

  const trend = (latest?.efficiency_trend ?? "stable") as EfficiencyTrend;
  const style = TREND_STYLE[trend];
  const bsfc = latest?.bsfc_g_per_kwh;
  const cov = latest?.combustion_instability_pct;

  return (
    <GlassCard
      title="Efficiency & Combustion Stability"
      subtitle="Brake specific fuel consumption · cycle-to-cycle variability"
      glow={trend === "degrading" ? "amber" : "cyan"}
      headerRight={
        <div className="flex items-center gap-3">
          <div className="text-right">
            <div className="tabular text-sm font-semibold text-slate-100">
              {bsfc != null ? bsfc.toFixed(0) : "—"}
              <span className="ml-1 text-[10px] font-normal text-slate-500">g/kWh</span>
            </div>
            <div className="tabular text-[10px] text-slate-500">
              COV {cov != null ? `${cov.toFixed(1)}%` : "—"}
            </div>
          </div>
          <span
            className={clsx(
              "rounded-full border px-2 py-0.5 font-mono text-[10px] tracking-wider",
              style.className
            )}
          >
            {style.label}
          </span>
        </div>
      }
    >
      {data.length > 3 ? (
        <MultiLineChart data={data} series={SERIES} height={180} />
      ) : (
        <div className="flex h-[180px] items-center justify-center text-xs text-slate-500">
          Waiting for sustained power — BSFC is undefined at idle.
        </div>
      )}
    </GlassCard>
  );
}
