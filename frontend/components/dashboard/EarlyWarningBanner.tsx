"use client";

import clsx from "clsx";
import { AnimatePresence, motion } from "framer-motion";
import { GlassCard } from "@/components/ui/GlassCard";
import { useTelemetryStore } from "@/lib/store";
import type { EarlyWarning } from "@/lib/types";

/**
 * A NEW, earlier tier — deliberately styled to never be mistaken for FaultAlertFeed or
 * MaintenanceAdvisoryPanel below it. Both of those use a coloured status dot plus
 * red/amber/cyan severity tone, because they report *confirmed* conditions. Nothing here
 * is confirmed yet — it is a statistical fluctuation that has started to look
 * non-random — so this panel never reaches for red or a dot-plus-severity-tone layout at
 * all. Instead: a single soft amber accent regardless of "emerging" vs "building" (the
 * latter gets a filled badge instead of an outlined one, not a deeper alarm colour), a
 * left accent bar rather than a bordered alert box, and the recommended action set in the
 * largest, most prominent type on the card — because unlike a fault alert, the entire
 * point of this tier is that there is still time to act on it.
 */

const SUBSYSTEM_LABEL: Record<string, string> = {
  cylinder: "Cylinder",
  lubrication: "Lubrication",
  cooling: "Cooling",
  fuel: "Fuel",
  turbo: "Turbo",
  electrical: "Electrical",
};

function EtaReadout({ warning }: { warning: EarlyWarning }) {
  if (warning.confidence === "low" || warning.predicted_minutes == null) {
    return (
      <span className="font-mono text-[10px] uppercase tracking-wider text-amber-300/70">
        confidence: low
      </span>
    );
  }
  return (
    <span className="tabular text-sm font-semibold text-amber-200">
      ~{Math.round(warning.predicted_minutes)} min until action needed
    </span>
  );
}

function WarningCard({ warning }: { warning: EarlyWarning }) {
  const building = warning.pre_alert_state === "building";

  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className="relative flex flex-col gap-1.5 overflow-hidden rounded-lg border border-amber-500/25 bg-amber-500/[0.06] py-2.5 pl-3.5 pr-3"
    >
      <span
        aria-hidden="true"
        className={clsx(
          "absolute inset-y-0 left-0 w-1",
          building ? "bg-amber-400" : "bg-amber-400/50"
        )}
      />

      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] font-medium uppercase tracking-wide text-slate-200">
          {SUBSYSTEM_LABEL[warning.subsystem] ?? warning.subsystem}
        </span>
        <span
          className={clsx(
            "rounded-full px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider",
            building
              ? "bg-amber-400/90 text-amber-950"
              : "border border-amber-400/50 text-amber-300"
          )}
        >
          {warning.pre_alert_state}
        </span>
        <span className="ml-auto">
          <EtaReadout warning={warning} />
        </span>
      </div>

      <p className="text-[13px] font-medium leading-snug text-slate-100">
        {warning.recommended_action}
      </p>

      {warning.basis.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {warning.basis.map((b) => (
            <span
              key={b}
              className="rounded border border-amber-500/20 px-1.5 py-0.5 font-mono text-[9px] text-amber-200/60"
            >
              {b}
            </span>
          ))}
        </div>
      )}
    </motion.li>
  );
}

export function EarlyWarningBanner() {
  const latest = useTelemetryStore((s) => s.latest);
  const warnings = latest?.early_warnings ?? [];

  if (warnings.length === 0) return null;

  return (
    <GlassCard
      title="Early Warning"
      subtitle="Developing fluctuation · earlier and softer than a confirmed fault"
      glow="amber"
      bodyClassName="p-0"
      className="border-amber-500/20"
    >
      <div className="max-h-[280px] overflow-y-auto p-3">
        <ul className="flex flex-col gap-2">
          <AnimatePresence initial={false}>
            {warnings.map((warning) => (
              <WarningCard key={warning.subsystem} warning={warning} />
            ))}
          </AnimatePresence>
        </ul>
      </div>
    </GlassCard>
  );
}
