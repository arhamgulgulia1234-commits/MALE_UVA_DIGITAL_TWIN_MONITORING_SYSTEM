"use client";

import clsx from "clsx";
import { useTelemetryStore } from "@/lib/store";
import {
  type EnginePart,
  formatTelemetryValue,
  resolveTelemetryField,
  telemetryFieldLabel,
} from "@/lib/enginePartsRegistry";
import type { SubsystemScores } from "@/lib/types";

/**
 * The inspector panel for the selected part.
 *
 * It is a DOM panel docked to the left of the canvas rather than a drei `<Html>` anchored
 * in 3D. Two reasons: at this card's size an in-scene panel would either be unreadably
 * small or would swallow the engine behind it, and — more importantly — a DOM sibling of
 * the `<Canvas>` can subscribe to the telemetry store directly. Re-rendering it on every
 * frame the store publishes costs the 3D scene nothing, because the scene is a different
 * React subtree that never reads the store through a hook.
 *
 * The camera rig compensates for the panel by framing the selected part right of centre,
 * so the panel sits over empty scene rather than over the thing being inspected.
 */

function scoreColor(score: number): string {
  if (score >= 85) return "#37af92";
  if (score >= 60) return "#d59834";
  return "#da6978";
}

const SUBSYSTEM_LABEL: Record<string, string> = {
  cylinder: "Cylinder",
  lubrication: "Lubrication",
  cooling: "Cooling",
  fuel: "Fuel",
  turbo: "Turbo",
  electrical: "Electrical",
};

export function PartDetailPanel({
  part,
  onClose,
}: {
  part: EnginePart;
  onClose: () => void;
}) {
  // Subscribed, not polled: these numbers must move while you watch them.
  const latest = useTelemetryStore((s) => s.latest);

  const subsystemScore =
    latest?.health.subsystem_scores[part.healthSubsystem as keyof SubsystemScores] ?? 100;
  const subsystemColor = scoreColor(subsystemScore);

  const activeFaults = (latest?.active_faults ?? []).filter(
    (f) => !f.is_sensor_fault && part.relatedFaultTypes.includes(f.type)
  );

  return (
    <div className="absolute left-0 top-0 z-20 flex h-full w-[200px] flex-col border-r border-base-border bg-base-bg">
      <div className="flex items-start justify-between gap-2 border-b border-base-border px-2.5 py-1.5">
        <div>
          <div className="text-[11px] font-semibold text-slate-100">{part.displayName}</div>
          <div className="font-mono text-[8px] uppercase tracking-[0.09em] text-slate-400">
            Part Inspector
          </div>
        </div>
        <button
          onClick={onClose}
          className="-mr-1 -mt-0.5 rounded px-1.5 py-0.5 font-mono text-[13px] leading-none text-slate-400 transition-colors hover:text-slate-200"
          aria-label="Clear selection"
        >
          ×
        </button>
      </div>

      <div className="flex-1 space-y-2.5 overflow-y-auto px-2.5 py-2">
        {/* --- subsystem health, first: it is the headline judgement ---- */}
        <section>
          <div className="mb-1 flex items-baseline justify-between">
            <span className="font-mono text-[8px] uppercase tracking-[0.09em] text-slate-400">
              {SUBSYSTEM_LABEL[part.healthSubsystem] ?? part.healthSubsystem} index
            </span>
            <span
              className="tabular font-mono text-[11px] font-semibold"
              style={{ color: subsystemColor }}
            >
              {Math.round(subsystemScore)}
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-base-border">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${Math.max(0, Math.min(100, subsystemScore))}%`,
                background: subsystemColor,
              }}
            />
          </div>
        </section>

        {/* --- faults attributable to this part ------------------------- */}
        {activeFaults.length > 0 && (
          <section className="space-y-1">
            {activeFaults.map((f) => (
              <div
                key={f.type}
                className="flex items-center gap-1.5 rounded border border-status-red/40 bg-status-red/10 px-1.5 py-1"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-status-red" />
                <span className="flex-1 truncate font-mono text-[9px] text-status-red">
                  {f.type}
                </span>
                <span className="tabular font-mono text-[9px] text-status-red">
                  {Math.round(f.severity * 100)}%
                </span>
              </div>
            ))}
          </section>
        )}

        {/* --- live telemetry ------------------------------------------- */}
        <section>
          <div className="mb-1 font-mono text-[8px] uppercase tracking-[0.09em] text-slate-400">
            Live telemetry
          </div>
          <div className="space-y-0.5">
            {part.relatedTelemetryFields.map((path) => {
              const raw = resolveTelemetryField(latest, path);
              const { value, unit } = formatTelemetryValue(path, raw);
              return (
                <div key={path} className="flex items-baseline gap-1.5 text-[9px]">
                  <span className="flex-1 truncate text-slate-400">
                    {telemetryFieldLabel(path)}
                  </span>
                  <span
                    className={clsx(
                      "tabular font-mono text-[10px]",
                      raw == null ? "text-slate-400" : "text-slate-200"
                    )}
                  >
                    {value}
                  </span>
                  {unit && (
                    <span className="w-7 shrink-0 font-mono text-[8px] text-slate-400">
                      {unit}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        {activeFaults.length === 0 && (
          <div className="flex items-center gap-1.5 text-[9px] text-slate-400">
            <span className="h-1.5 w-1.5 rounded-full bg-status-go" />
            No active fault on this part
          </div>
        )}

        <p className="text-[9px] leading-[1.5] text-slate-400">{part.description}</p>
      </div>

      {/*
        The software-module badge.

        This is the line that makes the panel an architecture diagram rather than an
        anatomy lesson: it names the exact file that simulates the part you just clicked.
      */}
      <div className="border-t border-base-border px-2.5 py-1.5">
        <div className="mb-1 font-mono text-[8px] uppercase tracking-[0.09em] text-slate-400">
          Governed by
        </div>
        <code className="block break-all rounded border border-status-cyan/35 bg-status-cyan/10 px-1.5 py-1 font-mono text-[9px] leading-[1.4] text-status-cyan">
          {part.backendModule}
        </code>
        {part.secondaryModules?.map((m) => (
          <code
            key={m}
            className="mt-1 block break-all rounded border border-base-border bg-base-panel2/60 px-1.5 py-1 font-mono text-[9px] leading-[1.4] text-slate-400"
          >
            {m}
          </code>
        ))}
      </div>
    </div>
  );
}
