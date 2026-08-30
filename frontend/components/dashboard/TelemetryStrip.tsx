"use client";

import { useMemo, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { MultiLineChart, type SeriesDef } from "@/components/charts/MultiLineChart";
import { useTelemetryStore } from "@/lib/store";

const ALL_SERIES: SeriesDef[] = [
  { key: "egt_avg", color: "#ef4a5f", label: "Avg EGT (°C)", yAxisId: "left" },
  { key: "cht", color: "#f5a623", label: "CHT (°C)", yAxisId: "left" },
  { key: "oil_temp", color: "#c084fc", label: "Oil Temp (°C)", yAxisId: "left" },
  { key: "rpm", color: "#3fd0e0", label: "RPM", yAxisId: "right" },
];

export function TelemetryStrip() {
  const buffer = useTelemetryStore((s) => s.buffer);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const data = useMemo(() => {
    if (buffer.length === 0) return [];
    const lastT = buffer[buffer.length - 1]!.timestamp;
    return buffer.map((f) => {
      const egtAvg = f.cylinders.reduce((sum, c) => sum + c.egt_c, 0) / f.cylinders.length;
      return {
        t: Math.round((f.timestamp - lastT) * 10) / 10,
        egt_avg: Math.round(egtAvg * 10) / 10,
        cht: f.cht_c,
        oil_temp: f.oil_temp_c,
        rpm: f.rpm,
      };
    });
  }, [buffer]);

  const visibleSeries = ALL_SERIES.filter((s) => !hidden.has(s.key));

  function toggle(key: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <GlassCard
      title="Telemetry Strip"
      subtitle="Last 60s · full-resolution"
      glow="cyan"
      headerRight={
        <div className="flex flex-wrap gap-2">
          {ALL_SERIES.map((s) => {
            const isHidden = hidden.has(s.key);
            return (
              <button
                key={s.key}
                onClick={() => toggle(s.key)}
                aria-pressed={!isHidden}
                className="flex items-center gap-1.5 rounded-full border border-base-border px-2.5 py-1 text-[10px] font-medium tracking-wide transition-all duration-200 hover:border-slate-500/60"
                style={{ opacity: isHidden ? 0.35 : 1 }}
              >
                <span
                  className="h-1.5 w-1.5 rounded-full transition-shadow duration-200"
                  style={{
                    background: s.color,
                    boxShadow: isHidden ? "none" : `0 0 6px ${s.color}`,
                  }}
                />
                <span className="text-slate-400">{s.label}</span>
              </button>
            );
          })}
        </div>
      }
    >
      <MultiLineChart data={data} series={visibleSeries} height={240} />
    </GlassCard>
  );
}
