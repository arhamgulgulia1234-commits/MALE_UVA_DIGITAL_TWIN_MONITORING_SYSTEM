"use client";

import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { GlassCard } from "@/components/ui/GlassCard";
import { useTelemetryStore } from "@/lib/store";
import { FAULT_CATALOG, type FaultType } from "@/lib/types";
import { formatTimeHHMMSS } from "@/lib/format";

interface FeedEntry {
  id: string;
  type: FaultType;
  event: "detected" | "cleared";
  severity: number;
  timestamp: number;
}

function severityTone(s: number): "caution" | "nogo" | "cyan" {
  if (s >= 0.7) return "nogo";
  if (s >= 0.35) return "caution";
  return "cyan";
}

export function FaultAlertFeed() {
  const latest = useTelemetryStore((s) => s.latest);
  const [feed, setFeed] = useState<FeedEntry[]>([]);
  const prevTypesRef = useRef<Set<FaultType>>(new Set());

  useEffect(() => {
    if (!latest) return;
    const currentTypes = new Set(latest.active_faults.map((f) => f.type));
    const prevTypes = prevTypesRef.current;
    const newEntries: FeedEntry[] = [];

    for (const f of latest.active_faults) {
      if (!prevTypes.has(f.type)) {
        newEntries.push({
          id: `${f.type}-${f.started_at}-detected`,
          type: f.type,
          event: "detected",
          severity: f.severity,
          timestamp: latest.timestamp,
        });
      }
    }
    for (const t of Array.from(prevTypes)) {
      if (!currentTypes.has(t)) {
        newEntries.push({
          id: `${t}-${latest.timestamp}-cleared`,
          type: t,
          event: "cleared",
          severity: 0,
          timestamp: latest.timestamp,
        });
      }
    }
    if (newEntries.length) {
      setFeed((prev) => [...newEntries, ...prev].slice(0, 30));
    }
    prevTypesRef.current = currentTypes;
  }, [latest]);

  const activeSeverityByType = new Map(
    latest?.active_faults.map((f) => [f.type, f.severity]) ?? []
  );

  return (
    <GlassCard title="Fault Alert Feed" subtitle="Newest first · live severity" glow="cyan" bodyClassName="p-0">
      <div className="max-h-[320px] overflow-y-auto p-3">
        {feed.length === 0 && (
          <p className="px-2 py-8 text-center text-xs text-slate-500">
            No faults recorded this session — nominal.
          </p>
        )}
        <ul className="flex flex-col gap-2">
          <AnimatePresence initial={false}>
            {feed.map((entry) => {
              const meta = FAULT_CATALOG.find((f) => f.type === entry.type);
              const liveSeverity =
                entry.event === "detected" ? activeSeverityByType.get(entry.type) : undefined;
              const displaySeverity = liveSeverity ?? entry.severity;
              const tone = entry.event === "cleared" ? "cyan" : severityTone(displaySeverity);

              return (
                <motion.li
                  key={entry.id}
                  layout
                  initial={{ opacity: 0, y: -14, height: 0 }}
                  animate={{ opacity: 1, y: 0, height: "auto" }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.28, ease: "easeOut" }}
                  className={clsx(
                    "flex items-center gap-3 rounded-lg border px-3 py-2 text-xs",
                    tone === "nogo" && "border-status-red/40 bg-status-red/10",
                    tone === "caution" && "border-status-amber/40 bg-status-amber/10",
                    tone === "cyan" && "border-base-border bg-base-panel2/60"
                  )}
                >
                  <span
                    className={clsx("h-2 w-2 shrink-0 rounded-full", {
                      "bg-status-red": tone === "nogo",
                      "bg-status-amber": tone === "caution",
                      "bg-status-cyan": tone === "cyan",
                    })}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="truncate font-medium text-slate-200">{meta?.label ?? entry.type}</span>
                      <span className="text-[10px] uppercase tracking-wide text-slate-500">
                        {entry.event === "cleared" ? "cleared" : "detected"}
                      </span>
                    </div>
                    <p className="truncate text-[11px] text-slate-500">{meta?.description}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    {entry.event === "detected" && (
                      <span className="tabular text-sm font-semibold text-slate-100">
                        {Math.round(displaySeverity * 100)}%
                      </span>
                    )}
                    <p className="tabular text-[10px] text-slate-500">{formatTimeHHMMSS(entry.timestamp)}</p>
                  </div>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      </div>
    </GlassCard>
  );
}
