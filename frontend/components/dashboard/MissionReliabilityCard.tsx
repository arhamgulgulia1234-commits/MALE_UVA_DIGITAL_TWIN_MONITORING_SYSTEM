"use client";

import clsx from "clsx";
import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { StatusPill } from "@/components/ui/StatusPill";
import { useTelemetryStore } from "@/lib/store";
import { useTweenedNumber } from "@/hooks/useTweenedNumber";
import type { Recommendation } from "@/lib/types";

// `mission_reliability` on the frame is the Weibull survival estimate from
// app/ml/mission_reliability.py — R(t) = exp(-(t_remaining / RUL)^beta) — not a frontend
// heuristic. This component only renders it.

const TONE: Record<Recommendation, "go" | "caution" | "nogo"> = {
  GO: "go",
  CAUTION: "caution",
  "NO-GO": "nogo",
};

const BAR_COLOR: Record<Recommendation, string> = {
  GO: "#22d3a8",
  CAUTION: "#f5a623",
  "NO-GO": "#ef4a5f",
};

export function MissionReliabilityCard() {
  const latest = useTelemetryStore((s) => s.latest);
  const rel = latest?.mission_reliability ?? { score: 1, recommendation: "GO" as Recommendation };
  const pct = Math.round(rel.score * 100);
  const smoothedPct = useTweenedNumber(pct);

  return (
    <GlassCard title="Mission Reliability" glow="cyan" className="h-full">
      <div className="flex h-full flex-col justify-between gap-4">
        <div className="flex items-center justify-between">
          <span className="tabular text-4xl font-bold leading-none text-slate-100">
            {Math.round(smoothedPct)}%
          </span>
          <StatusPill tone={TONE[rel.recommendation]} pulse={rel.recommendation !== "GO"}>
            {rel.recommendation}
          </StatusPill>
        </div>

        <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-base-border">
          {[25, 50, 75].map((mark) => (
            <span
              key={mark}
              className="absolute inset-y-0 z-10 w-px bg-base-bg/60"
              style={{ left: `${mark}%` }}
            />
          ))}
          <motion.div
            className="h-full rounded-full"
            style={{ background: BAR_COLOR[rel.recommendation] }}
            animate={{ width: `${pct}%` }}
            transition={{ type: "spring", stiffness: 60, damping: 16 }}
          />
        </div>

        <p
          className={clsx(
            "text-[11px] leading-relaxed text-slate-500",
            rel.recommendation === "NO-GO" && "text-status-red/80",
            rel.recommendation === "CAUTION" && "text-status-amber/80"
          )}
        >
          {rel.recommendation === "GO" &&
            "Engine parameters nominal — mission continuation probability is high."}
          {rel.recommendation === "CAUTION" &&
            "Degraded margin detected — monitor closely, consider shortening mission."}
          {rel.recommendation === "NO-GO" &&
            "Reliability below safe threshold — recommend immediate abort / RTB."}
        </p>
      </div>
    </GlassCard>
  );
}
