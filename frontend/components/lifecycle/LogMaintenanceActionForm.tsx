"use client";

/**
 * Log a maintenance action against the persisted wear ledger — a bearing replacement, an
 * oil change, anything that reduces `current_wear_state[fault_type]` — and see it take
 * effect immediately in `LifecycleOverviewPanel` above.
 *
 * "Immediately" here means the *ledger* updates and every panel reading from
 * `useLifecycleStore` re-renders off it — not that the live running engine's telemetry
 * changes. Those are deliberately different: a maintenance action lands on the next
 * mission's seed, not on whatever mission is recording right now (see
 * `lifecycle_repository.apply_maintenance_action`), which is also why this form never
 * touches the WebSocket or the live dashboard.
 */
import { useState } from "react";
import clsx from "clsx";
import { GlassCard } from "@/components/ui/GlassCard";
import { useLifecycleStore } from "@/lib/lifecycle/store";
import { FAULT_CATALOG } from "@/lib/types";
import type { FaultType } from "@/lib/types";

const PRESET_DESCRIPTIONS: Partial<Record<FaultType, string>> = {
  bearing_wear: "Replaced main/rod bearing shells",
  piston_ring_wear: "Replaced piston rings",
  oil_pump_degradation: "Replaced oil pump",
  cooling_degradation: "Serviced cooling system / replaced coolant",
  fuel_injector_clog: "Cleaned/replaced fuel injector",
  turbo_wear: "Overhauled turbocharger",
  air_filter_clog: "Replaced air filter",
  spark_degradation: "Replaced spark plugs",
  battery_alternator_degradation: "Replaced battery/alternator",
  injection_timing_drift: "Recalibrated injection timing",
  misfire: "Diagnosed and corrected misfire cause",
};

export function LogMaintenanceActionForm() {
  const summary = useLifecycleStore((s) => s.summary);
  const submitting = useLifecycleStore((s) => s.submitting);
  const submitError = useLifecycleStore((s) => s.submitError);
  const applyMaintenanceAction = useLifecycleStore((s) => s.applyMaintenanceAction);

  const [faultType, setFaultType] = useState<FaultType>("bearing_wear");
  const [description, setDescription] = useState(PRESET_DESCRIPTIONS.bearing_wear ?? "");
  const [resetAmount, setResetAmount] = useState(1.0);
  const [justApplied, setJustApplied] = useState<{ faultType: FaultType; before: number } | null>(
    null
  );

  const currentWear = summary?.current_wear_state?.[faultType] ?? 0;
  const afterWear = Math.max(0, currentWear - resetAmount);

  function onFaultTypeChange(next: FaultType) {
    setFaultType(next);
    // Only replace the description if it still matches the previous fault's preset —
    // an operator who typed their own note should not have it silently overwritten by
    // switching the dropdown.
    setDescription((prev) => {
      const stillDefault = Object.values(PRESET_DESCRIPTIONS).includes(prev) || prev === "";
      return stillDefault ? PRESET_DESCRIPTIONS[next] ?? "" : prev;
    });
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const before = currentWear;
    const ok = await applyMaintenanceAction({
      fault_type: faultType,
      description: description.trim() || "Maintenance action",
      reset_amount: resetAmount,
    });
    if (ok) {
      setJustApplied({ faultType, before });
      window.setTimeout(() => setJustApplied(null), 4000);
    }
  }

  return (
    <GlassCard
      title="Log Maintenance Action"
      subtitle="Reduce persisted wear — takes effect at the next mission's start"
      glow="go"
      bodyClassName="p-4"
    >
      <form onSubmit={onSubmit} className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-400">
            Fault type
          </span>
          <select
            value={faultType}
            onChange={(e) => onFaultTypeChange(e.target.value as FaultType)}
            className="w-full rounded-md border border-base-border bg-base-panel2/60 px-2.5 py-1.5 text-sm text-slate-100 outline-none focus:border-status-go/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-go"
          >
            {FAULT_CATALOG.map((meta) => (
              <option key={meta.type} value={meta.type}>
                {meta.label} — current {((summary?.current_wear_state?.[meta.type] ?? 0) * 100).toFixed(0)}%
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-400">
            Description
          </span>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={500}
            placeholder="What was done"
            className="w-full rounded-md border border-base-border bg-base-panel2/60 px-2.5 py-1.5 text-sm text-slate-100 outline-none focus:border-status-go/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-go"
          />
        </label>

        <div>
          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-[10px] uppercase tracking-wider text-slate-400">
              Wear to clear
            </span>
            <span className="tabular text-sm text-status-go">
              {(resetAmount * 100).toFixed(0)}%
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={resetAmount * 100}
            onChange={(e) => setResetAmount(Number(e.target.value) / 100)}
            className="w-full accent-status-go"
          />
        </div>

        <div className="rounded-lg border border-base-border/70 bg-base-panel2/40 px-3 py-2.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400">Wear before</span>
            <span className="tabular text-slate-300">{(currentWear * 100).toFixed(0)}%</span>
          </div>
          <div className="mt-1 flex items-center justify-between text-xs">
            <span className="text-slate-400">Wear after this action</span>
            <span className="tabular font-semibold text-status-go">
              {(afterWear * 100).toFixed(0)}%
            </span>
          </div>
        </div>

        {submitError && <p className="text-xs text-status-red">{submitError.message}</p>}
        {justApplied && (
          <p className="text-xs text-status-go">
            Applied — {FAULT_CATALOG.find((f) => f.type === justApplied.faultType)?.label} wear
            dropped from {(justApplied.before * 100).toFixed(0)}% to{" "}
            {((summary?.current_wear_state?.[justApplied.faultType] ?? 0) * 100).toFixed(0)}%.
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className={clsx(
            "w-full rounded-md border px-3 py-2 text-sm font-medium transition-colors",
            submitting
              ? "cursor-not-allowed border-base-border text-slate-400"
              : "border-status-go/50 bg-status-go/10 text-status-go hover:bg-status-go/20"
          )}
        >
          {submitting ? "Applying…" : "Log Maintenance Action"}
        </button>
      </form>
    </GlassCard>
  );
}
