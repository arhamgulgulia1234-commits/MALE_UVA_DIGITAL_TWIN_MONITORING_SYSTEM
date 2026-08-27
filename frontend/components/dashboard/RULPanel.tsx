"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { useTelemetryStore } from "@/lib/store";
import { formatRul } from "@/lib/format";

function rulTone(minutes: number | null): { text: string; glow: "cyan" | "amber" | "red" } {
  if (minutes === null) return { text: "text-status-cyan", glow: "cyan" };
  if (minutes > 60) return { text: "text-status-go", glow: "cyan" };
  if (minutes > 15) return { text: "text-status-amber", glow: "amber" };
  return { text: "text-status-red", glow: "red" };
}

export function RULPanel() {
  const latest = useTelemetryStore((s) => s.latest);
  const rul = latest?.rul_minutes ?? null;
  const tone = rulTone(rul);

  return (
    <GlassCard title="Remaining Useful Life" glow={tone.glow} className="h-full">
      <div className="flex h-full flex-col justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span className={`tabular text-4xl font-bold ${tone.text}`}>{formatRul(rul)}</span>
        </div>
        <p className="text-[11px] text-slate-500">
          {rul === null
            ? "No active degradation — RUL estimate suppressed while nominal."
            : "Estimated time to subsystem limit at current fault severity trend."}
        </p>
        {/* TODO(phase-2): replace linear-decay heuristic with app/ml/rul_predictor.py sequence model output. */}
      </div>
    </GlassCard>
  );
}
