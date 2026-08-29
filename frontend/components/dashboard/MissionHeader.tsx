"use client";

import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { useTelemetryStore } from "@/lib/store";
import type { ConnectionStatus } from "@/lib/websocket";
import { formatClock } from "@/lib/format";
import type { MissionPhase, Recommendation, RecoveryRecommendation } from "@/lib/types";

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

/**
 * Phase 5: recovery_reliability's badge, styled to never be mistaken for the mission-
 * reliability one next to it even though they can share a color — a pill shape (the
 * mission badge is a rectangle), its own icon, and the label text itself always reads
 * "RTB-something" rather than a bare GO/CAUTION/NO-GO. RTB-AT-RISK additionally gets a
 * ring the other two states don't, so the one state that must never be missed is
 * structurally distinct, not just red — a viewer who cannot see color still gets it.
 */
const RECOVERY_STYLE: Record<
  RecoveryRecommendation,
  { bg: string; border: string; text: string; glow: string; pulse: boolean; icon: string; ring: string }
> = {
  "RTB-SAFE": {
    bg: "bg-status-go/10",
    border: "border-status-go/50",
    text: "text-status-go",
    glow: "shadow-glow-go",
    pulse: false,
    icon: "⌂",
    ring: "",
  },
  "RTB-CAUTION": {
    bg: "bg-status-amber/10",
    border: "border-status-amber/50",
    text: "text-status-amber",
    glow: "shadow-glow-amber",
    pulse: true,
    icon: "⌂",
    ring: "",
  },
  "RTB-AT-RISK": {
    bg: "bg-status-red/15",
    border: "border-status-red/70",
    text: "text-status-red",
    glow: "shadow-glow-red",
    pulse: true,
    icon: "⚠",
    ring: "ring-2 ring-status-red/60 ring-offset-2 ring-offset-base-bg",
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
  const recoveryRecommendation = latest?.recovery_reliability?.recommendation ?? "RTB-SAFE";
  const isReplay = latest?.is_replay ?? false;
  const style = RECOMMENDATION_STYLE[recommendation];
  const recoveryStyle = RECOVERY_STYLE[recoveryRecommendation];
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
              title="Mission reliability — can it finish the rest of the planned mission?"
            >
              {recommendation}
            </motion.div>
          </AnimatePresence>

          {/* Phase 5: recovery reliability. A deliberately different question from the
              badge above ("can it get back to base if we abort right now?"), so the two
              can legitimately disagree — mission_reliability might read NO-GO for
              continuing while this still reads RTB-SAFE, which is precisely the
              life-saving distinction this second readout exists to show. */}
          <AnimatePresence mode="wait">
            <motion.div
              key={recoveryRecommendation}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ duration: 0.25 }}
              className={clsx(
                "flex items-center gap-1.5 rounded-full border px-4 py-1.5 font-display text-lg font-bold tracking-[0.1em]",
                recoveryStyle.bg,
                recoveryStyle.border,
                recoveryStyle.text,
                recoveryStyle.glow,
                recoveryStyle.ring,
                recoveryStyle.pulse && "animate-pulseGlow"
              )}
              title="Recovery reliability — can it safely get back to base if we abort right now?"
            >
              <span aria-hidden="true">{recoveryStyle.icon}</span>
              {recoveryRecommendation}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </header>
  );
}
