"use client";

/**
 * Cumulative fault-event counts by type, plus the maintenance log those counts sit
 * beside.
 *
 * "Count" here means missions, not seconds or severity: `engine_lifecycle.cumulative_
 * fault_event_counts[fault_type]` is incremented once per mission that fault was active
 * in (see lifecycle_repository.record_fault_event), so a fault that has shown up in 6 of
 * this engine's 40 missions reads as 6 — a maintainer's question ("how often does this
 * engine throw bearing wear?") answered directly, rather than as a raw tally of every
 * telemetry sample that happened to see it.
 */
import { useEffect } from "react";
import clsx from "clsx";
import { GlassCard } from "@/components/ui/GlassCard";
import { useLifecycleStore } from "@/lib/lifecycle/store";
import { FAULT_CATALOG } from "@/lib/types";

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function FaultEventHistoryTable() {
  const summary = useLifecycleStore((s) => s.summary);
  const loading = useLifecycleStore((s) => s.loading);
  const loadSummary = useLifecycleStore((s) => s.loadSummary);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  const counts = summary?.cumulative_fault_event_counts;
  const rows = counts
    ? FAULT_CATALOG.map((meta) => ({ meta, count: counts[meta.type] ?? 0 })).sort(
        (a, b) => b.count - a.count
      )
    : [];
  const actions = summary?.maintenance_actions ?? [];

  return (
    <GlassCard
      title="Fault Event History"
      subtitle="Lifetime mission count by fault type, and the maintenance log"
      glow="none"
      bodyClassName="p-0"
    >
      <div className="max-h-[280px] overflow-y-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 bg-base-panel">
            <tr className="border-b border-base-border/70 text-[10px] uppercase tracking-wider text-slate-500">
              <th className="px-3 py-2 text-left font-medium">Fault type</th>
              <th className="px-3 py-2 text-right font-medium">Missions active in</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={2} className="px-3 py-8 text-center text-slate-500">
                  {loading ? "Loading…" : "No data yet."}
                </td>
              </tr>
            )}
            {rows.map(({ meta, count }) => (
              <tr
                key={meta.type}
                className="border-b border-base-border/40 last:border-0"
                title={meta.description}
              >
                <td className="px-3 py-1.5 text-slate-300">{meta.label}</td>
                <td
                  className={clsx(
                    "tabular px-3 py-1.5 text-right font-medium",
                    count > 0 ? "text-status-amber" : "text-slate-600"
                  )}
                >
                  {count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="border-t border-base-border/70 px-3 py-2">
        <span className="text-[10px] uppercase tracking-wider text-slate-500">
          Maintenance log
        </span>
      </div>
      <div className="max-h-[200px] overflow-y-auto px-3 pb-3">
        {actions.length === 0 ? (
          <p className="py-4 text-center text-[11px] text-slate-500">
            No maintenance actions recorded.
          </p>
        ) : (
          <ul className="space-y-2">
            {actions.map((a) => {
              const label = FAULT_CATALOG.find((f) => f.type === a.fault_type)?.label ?? a.fault_type;
              return (
                <li key={a.id} className="rounded-md border border-base-border/60 bg-base-panel2/30 px-2.5 py-2">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium text-slate-200">{label}</span>
                    <span className="tabular text-status-go">
                      −{(a.wear_reset_amount * 100).toFixed(0)}%
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11px] text-slate-500">{a.description}</p>
                  <span className="mt-0.5 block text-[10px] text-slate-600">
                    {formatWhen(a.performed_at)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </GlassCard>
  );
}
