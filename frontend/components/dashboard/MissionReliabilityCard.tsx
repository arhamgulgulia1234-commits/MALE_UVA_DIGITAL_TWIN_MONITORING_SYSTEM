"use client";

import clsx from "clsx";
import { motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { StatusPill } from "@/components/ui/StatusPill";
import { useTelemetryStore } from "@/lib/store";
import type { Recommendation } from "@/lib/types";

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

  return (
    <GlassCard title="Mission Reliability" glow="cyan" className="h-full">
      <div className="flex h-full flex-col justify-between gap-4">
        <div className="flex items-center justify-between">
          <span className="tabular text-4xl font-bold text-slate-100">{pct}%</span>
          <StatusPill tone={TONE[rel.recommendation]} pulse={rel.recommendation !== "GO"}>
            {rel.recommendation}
          </StatusPill>
        </div>

        <div className="h-2.5 w-full overflow-hidden rounded-full bg-base-border">
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
        {/* TODO(phase-2): score currently a heuristic of overall_score + worst fault severity; replace with app/ml/mission_reliability.py output. */}
      </div>
    </GlassCard>
  );
}
