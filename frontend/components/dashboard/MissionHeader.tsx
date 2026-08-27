"use client";

import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { useTelemetryStore } from "@/lib/store";
import type { ConnectionStatus } from "@/lib/websocket";
import { formatClock } from "@/lib/format";
import type { MissionPhase, Recommendation } from "@/lib/types";

const PHASE_LABEL: Record<MissionPhase, string> = {
  climb: "Climb",
  cruise: "Cruise",
  loiter: "Loiter",
  descent: "Descent",
};

const RECOMMENDATION_STYLE: Record<
  Recommendation,
  { bg: string; border: string; text: string; glow: string; pulse: boolean }
> = {
  GO: {
    bg: "bg-status-go/10",
    border: "border-status-go/50",
    text: "text-status-go",
    glow: "shadow-glow-go",
    pulse: false,
  },
  CAUTION: {
    bg: "bg-status-amber/10",
    border: "border-status-amber/50",
    text: "text-status-amber",
    glow: "shadow-glow-amber",
    pulse: true,
  },
  "NO-GO": {
    bg: "bg-status-red/10",
    border: "border-status-red/50",
    text: "text-status-red",
    glow: "shadow-glow-red",
    pulse: true,
  },
};

const CONN_LABEL: Record<ConnectionStatus, { label: string; tone: string }> = {
  connecting: { label: "Connecting", tone: "text-status-amber" },
  open: { label: "Live", tone: "text-status-go" },
  reconnecting: { label: "Reconnecting", tone: "text-status-amber" },
  closed: { label: "Offline", tone: "text-status-red" },
};

export function MissionHeader() {
  const latest = useTelemetryStore((s) => s.latest);
  const status = useTelemetryStore((s) => s.status);
  const [elapsedS, setElapsedS] = useState(0);
  const [startedAt] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setElapsedS((Date.now() - startedAt) / 1000), 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  const recommendation = latest?.mission_reliability.recommendation ?? "GO";
  const isReplay = latest?.is_replay ?? false;
  const style = RECOMMENDATION_STYLE[recommendation];
  const conn = CONN_LABEL[status];

  return (
    <header className="sticky top-0 z-30 border-b border-base-border bg-base-bg/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-center gap-4 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-status-cyan/40 bg-status-cyan/10">
            <span className="font-display text-sm font-bold text-status-cyan">DT</span>
          </div>
          <div>
            <h1 className="font-display text-base font-bold tracking-wide text-slate-100 sm:text-lg">
              SIH26054 · Aero Engine Digital Twin
            </h1>
            <p className="text-[11px] text-slate-500">MALE UAV Piston Engine PHM — Mock Telemetry</p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-4 sm:gap-6">
          <div className="hidden flex-col items-end sm:flex">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Mission Clock</span>
            <span className="tabular text-lg font-medium text-slate-200">{formatClock(elapsedS)}</span>
          </div>

          <div className="hidden flex-col items-end md:flex">
            <span className="text-[10px] uppercase tracking-wider text-slate-500">Phase</span>
            <span className="font-display text-lg font-semibold text-status-cyan">
              {latest ? PHASE_LABEL[latest.mission_phase] : "—"}
            </span>
          </div>

          {isReplay && (
            <span className="animate-pulseGlow rounded-md border border-status-amber/50 bg-status-amber/15 px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.15em] text-status-amber">
              ⏵ Replay{latest?.replay_mission_id ? ` #${latest.replay_mission_id}` : ""}
            </span>
          )}

          <div className="flex items-center gap-1.5 text-[11px]">
            <span className={clsx("h-1.5 w-1.5 rounded-full", conn.tone.replace("text-", "bg-"))} />
            <span className={conn.tone}>{conn.label}</span>
          </div>

          <AnimatePresence mode="wait">
            <motion.div
              key={recommendation}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ duration: 0.25 }}
              className={clsx(
                "rounded-lg border px-4 py-1.5 font-display text-lg font-bold tracking-[0.15em]",
                style.bg,
                style.border,
                style.text,
                style.glow,
                style.pulse && "animate-pulseGlow"
              )}
            >
              {recommendation}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </header>
  );
}
