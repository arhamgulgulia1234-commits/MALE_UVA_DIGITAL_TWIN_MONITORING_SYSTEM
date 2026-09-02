"use client";

import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { useTelemetryStore } from "@/lib/store";
import type { Urgency } from "@/lib/types";

const URGENCY_STYLE: Record<
  Urgency,
  { label: string; border: string; dot: string; text: string }
> = {
  immediate: {
    label: "Immediate",
    border: "border-status-red/50 bg-status-red/10",
    dot: "bg-status-red",
    text: "text-status-red",
  },
  schedule_soon: {
    label: "Schedule Soon",
    border: "border-status-amber/50 bg-status-amber/10",
    dot: "bg-status-amber",
    text: "text-status-amber",
  },
  monitor: {
    label: "Monitor",
    border: "border-base-border bg-base-panel2/60",
    dot: "bg-status-cyan",
    text: "text-status-cyan",
  },
};

/**
 * What the ground crew should actually do. A health score of 34 tells a technician
 * nothing actionable; this panel turns the numbers into a task with a stated basis, so
 * the reasoning can be checked rather than taken on trust.
 */
export function MaintenanceAdvisoryPanel() {
  const latest = useTelemetryStore((s) => s.latest);
  const advisories = latest?.maintenance_advisories ?? [];

  const worst = advisories[0]?.urgency;
  const glow =
    worst === "immediate" ? "red" : worst === "schedule_soon" ? "amber" : "cyan";

  return (
    <GlassCard
      title="Maintenance Advisories"
      subtitle="Rule-based · most urgent first"
      glow={glow}
      bodyClassName="p-0"
    >
      <div className="max-h-[320px] overflow-y-auto p-3">
        {advisories.length === 0 && (
          <p className="px-2 py-8 text-center text-xs text-slate-400">
            No maintenance action required — all subsystems nominal.
          </p>
        )}

        <ul className="flex flex-col gap-2">
          <AnimatePresence initial={false}>
            {advisories.map((advisory) => {
              const style = URGENCY_STYLE[advisory.urgency];
              return (
                <motion.li
                  key={`${advisory.subsystem}-${advisory.urgency}`}
                  layout
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.25, ease: "easeOut" }}
                  className={clsx(
                    "flex flex-col gap-1.5 rounded-lg border px-3 py-2.5",
                    style.border
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className={clsx("h-2 w-2 shrink-0 rounded-full", style.dot)} />
                    <span className="font-mono text-[11px] font-medium uppercase tracking-wide text-slate-200">
                      {advisory.subsystem}
                    </span>
                    <span
                      className={clsx(
                        "ml-auto font-mono text-[10px] uppercase tracking-wider",
                        style.text
                      )}
                    >
                      {style.label}
                    </span>
                  </div>

                  <p className="text-[11px] leading-relaxed text-slate-300">
                    {advisory.recommendation}
                  </p>

                  {advisory.basis.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {advisory.basis.map((b) => (
                        <span
                          key={b}
                          className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[9px] text-slate-400"
                        >
                          {b}
                        </span>
                      ))}
                    </div>
                  )}
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      </div>
    </GlassCard>
  );
}
