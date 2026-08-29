"use client";

/**
 * Builds a what-if scenario: conditions, a throttle profile, pre-existing wear, and
 * faults scheduled to develop mid-run.
 *
 * Bounds shown here come from GET /simulate/scenario/envelope rather than being hardcoded,
 * so the inputs and the backend's validity envelope cannot drift apart. The backend still
 * validates authoritatively — these are hints, not the check.
 */
import { useEffect, useState } from "react";
import clsx from "clsx";
import { GlassCard } from "@/components/ui/GlassCard";
import { FAULT_CATALOG, type FaultType } from "@/lib/types";
import { useTestBenchStore } from "@/lib/testbench/store";
import type { ScheduledFault } from "@/lib/testbench/types";

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
  hint,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
        {label}
        {unit && <span className="ml-1 normal-case tracking-normal text-slate-600">({unit})</span>}
      </span>
      <input
        type="number"
        value={Number.isFinite(value) ? value : ""}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        className="tabular w-full rounded-md border border-base-border bg-base-panel2/60 px-2.5 py-1.5 text-sm text-slate-100 outline-none focus:border-status-cyan/60"
      />
      {hint && <span className="mt-1 block text-[10px] text-slate-600">{hint}</span>}
    </label>
  );
}

function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex gap-1.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={clsx(
            "flex-1 rounded-md border px-2 py-1.5 font-mono text-xs transition-colors",
            value === o.value
              ? "border-status-cyan/60 bg-status-cyan/15 text-status-cyan"
              : "border-base-border text-slate-400 hover:border-status-cyan/30"
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function ScenarioBuilder() {
  const draft = useTestBenchStore((s) => s.draft);
  const envelope = useTestBenchStore((s) => s.envelope);
  const running = useTestBenchStore((s) => s.running);
  const error = useTestBenchStore((s) => s.scenarioError);
  const patchDraft = useTestBenchStore((s) => s.patchDraft);
  const patchThrottle = useTestBenchStore((s) => s.patchThrottle);
  const setFaultSeverity = useTestBenchStore((s) => s.setFaultSeverity);
  const addScheduledFault = useTestBenchStore((s) => s.addScheduledFault);
  const removeScheduledFault = useTestBenchStore((s) => s.removeScheduledFault);
  const resetDraft = useTestBenchStore((s) => s.resetDraft);
  const submitScenario = useTestBenchStore((s) => s.submitScenario);
  const loadEnvelope = useTestBenchStore((s) => s.loadEnvelope);

  useEffect(() => {
    loadEnvelope();
  }, [loadEnvelope]);

  const [newFault, setNewFault] = useState<ScheduledFault>({
    fault_type: "bearing_wear",
    severity: 0.7,
    at_time_min: 10,
    ramp_minutes: 5,
  });

  const standardDay = draft.ambient_temperature_c === null;
  const preExisting = Object.entries(draft.initial_fault_severities).filter(
    ([, v]) => v > 0.001
  );

  return (
    <GlassCard
      title="Scenario Builder"
      subtitle="Conditions, throttle profile and engine condition for one what-if run"
      glow="cyan"
      bodyClassName="space-y-5 p-4"
    >
      {/* ---- conditions ---- */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <NumberField
          label="Altitude"
          unit="m"
          value={draft.altitude_m}
          onChange={(v) => patchDraft({ altitude_m: v })}
          min={envelope?.altitude_m.min ?? 0}
          max={envelope?.altitude_m.max ?? 8000}
          step={100}
          hint={
            envelope
              ? `${envelope.altitude_m.min}–${envelope.altitude_m.max} modelled`
              : undefined
          }
        />
        <div>
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
            Ambient Temp <span className="normal-case tracking-normal text-slate-600">(°C)</span>
          </span>
          <div className="flex gap-1.5">
            <input
              type="number"
              disabled={standardDay}
              value={standardDay ? "" : draft.ambient_temperature_c ?? 0}
              placeholder="ISA"
              step={1}
              onChange={(e) => patchDraft({ ambient_temperature_c: Number(e.target.value) })}
              className="tabular w-full rounded-md border border-base-border bg-base-panel2/60 px-2.5 py-1.5 text-sm text-slate-100 outline-none focus:border-status-cyan/60 disabled:opacity-40"
            />
            <button
              type="button"
              onClick={() =>
                patchDraft({ ambient_temperature_c: standardDay ? 35 : null })
              }
              title={
                standardDay
                  ? "Set a specific outside air temperature"
                  : "Follow the ISA temperature for this altitude"
              }
              className={clsx(
                "shrink-0 rounded-md border px-2 py-1.5 font-mono text-[10px] uppercase transition-colors",
                standardDay
                  ? "border-status-cyan/60 bg-status-cyan/15 text-status-cyan"
                  : "border-base-border text-slate-400 hover:border-status-cyan/30"
              )}
            >
              ISA
            </button>
          </div>
          <span className="mt-1 block text-[10px] text-slate-600">
            {standardDay ? "Standard day for this altitude" : "Hot/cold day override"}
          </span>
        </div>
        <NumberField
          label="Duration"
          unit="min"
          value={draft.duration_minutes}
          onChange={(v) => patchDraft({ duration_minutes: v })}
          min={envelope?.duration_minutes.min ?? 0.5}
          max={envelope?.duration_minutes.max ?? 240}
          step={5}
          hint="Runs far faster than real time"
        />
      </div>

      {/* ---- throttle profile ---- */}
      <div className="space-y-2.5 rounded-lg border border-base-border/70 bg-base-panel2/30 p-3">
        <div className="flex items-center justify-between">
          <span className="panel-title">Throttle Profile</span>
        </div>
        <SegmentedControl
          value={draft.throttle.mode}
          onChange={(mode) => patchThrottle({ mode })}
          options={[
            { value: "constant", label: "Constant" },
            { value: "ramp", label: "Ramp" },
          ]}
        />
        {draft.throttle.mode === "constant" ? (
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">Setting</span>
              <span className="tabular text-sm text-status-cyan">
                {draft.throttle.constant_pct.toFixed(0)}%
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={draft.throttle.constant_pct}
              onChange={(e) => patchThrottle({ constant_pct: Number(e.target.value) })}
              className="w-full accent-status-cyan"
            />
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            <NumberField
              label="From"
              unit="%"
              value={draft.throttle.ramp_from_pct}
              onChange={(v) => patchThrottle({ ramp_from_pct: v })}
              min={0}
              max={100}
            />
            <NumberField
              label="To"
              unit="%"
              value={draft.throttle.ramp_to_pct}
              onChange={(v) => patchThrottle({ ramp_to_pct: v })}
              min={0}
              max={100}
            />
            <NumberField
              label="Over"
              unit="min"
              value={draft.throttle.ramp_over_min}
              onChange={(v) => patchThrottle({ ramp_over_min: v })}
              min={0}
              max={draft.duration_minutes}
              hint="Then held"
            />
          </div>
        )}
      </div>

      {/* ---- pre-existing wear ---- */}
      <div className="space-y-2 rounded-lg border border-base-border/70 bg-base-panel2/30 p-3">
        <div className="flex items-baseline justify-between">
          <span className="panel-title">Pre-existing Wear</span>
          <span className="text-[10px] text-slate-600">Condition at T+0</span>
        </div>
        <div className="grid max-h-52 grid-cols-1 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
          {FAULT_CATALOG.map((fault) => {
            const value = draft.initial_fault_severities[fault.type] ?? 0;
            return (
              <div key={fault.type} className="flex items-center gap-2">
                <span
                  className="w-28 shrink-0 truncate text-[11px] text-slate-400"
                  title={fault.description}
                >
                  {fault.label}
                </span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={Math.round(value * 100)}
                  onChange={(e) =>
                    setFaultSeverity(fault.type, Number(e.target.value) / 100)
                  }
                  className={clsx(
                    "h-1 flex-1",
                    value > 0.001 ? "accent-status-amber" : "accent-status-idle"
                  )}
                />
                <span
                  className={clsx(
                    "tabular w-9 shrink-0 text-right text-[11px]",
                    value > 0.001 ? "text-status-amber" : "text-slate-600"
                  )}
                >
                  {Math.round(value * 100)}%
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ---- scheduled faults ---- */}
      <div className="space-y-2.5 rounded-lg border border-base-border/70 bg-base-panel2/30 p-3">
        <div className="flex items-baseline justify-between">
          <span className="panel-title">Faults During Scenario</span>
          <span className="text-[10px] text-slate-600">Develop mid-run</span>
        </div>

        {draft.scheduled_faults.length > 0 && (
          <ul className="space-y-1">
            {draft.scheduled_faults.map((f, i) => (
              <li
                key={`${f.fault_type}-${f.at_time_min}-${i}`}
                className="flex items-center gap-2 rounded-md border border-status-amber/30 bg-status-amber/5 px-2 py-1.5 text-[11px]"
              >
                <span className="flex-1 text-slate-300">
                  {FAULT_CATALOG.find((c) => c.type === f.fault_type)?.label ?? f.fault_type}
                </span>
                <span className="tabular text-status-amber">
                  {Math.round(f.severity * 100)}% @ T+{f.at_time_min}min
                </span>
                <span className="tabular text-slate-500">ramp {f.ramp_minutes}min</span>
                <button
                  type="button"
                  onClick={() => removeScheduledFault(i)}
                  aria-label="Remove scheduled fault"
                  className="rounded px-1 text-slate-500 hover:text-status-red"
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <label className="col-span-2 block">
            <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
              Fault
            </span>
            <select
              value={newFault.fault_type}
              onChange={(e) => setNewFault({ ...newFault, fault_type: e.target.value })}
              className="w-full rounded-md border border-base-border bg-base-panel2/60 px-2 py-1.5 text-xs text-slate-100 outline-none focus:border-status-cyan/60"
            >
              {FAULT_CATALOG.map((f: { type: FaultType; label: string }) => (
                <option key={f.type} value={f.type}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          <NumberField
            label="Severity"
            unit="%"
            value={Math.round(newFault.severity * 100)}
            onChange={(v) => setNewFault({ ...newFault, severity: v / 100 })}
            min={0}
            max={100}
            step={5}
          />
          <NumberField
            label="At"
            unit="min"
            value={newFault.at_time_min}
            onChange={(v) => setNewFault({ ...newFault, at_time_min: v })}
            min={0}
            max={draft.duration_minutes}
          />
          <NumberField
            label="Ramp"
            unit="min"
            value={newFault.ramp_minutes}
            onChange={(v) => setNewFault({ ...newFault, ramp_minutes: v })}
            min={0}
          />
        </div>
        <button
          type="button"
          onClick={() => addScheduledFault({ ...newFault })}
          className="w-full rounded-md border border-base-border px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-slate-400 transition-colors hover:border-status-amber/50 hover:text-status-amber"
        >
          + Schedule fault
        </button>
      </div>

      {/* ---- label + run ---- */}
      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
            Label <span className="normal-case tracking-normal text-slate-600">(optional)</span>
          </span>
          <input
            type="text"
            maxLength={160}
            value={draft.label}
            placeholder="e.g. Hot-and-high climb with bearing wear"
            onChange={(e) => patchDraft({ label: e.target.value })}
            className="w-full rounded-md border border-base-border bg-base-panel2/60 px-2.5 py-1.5 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-status-cyan/60"
          />
        </label>

        {preExisting.length > 0 && (
          <p className="text-[11px] text-status-amber">
            Engine starts with{" "}
            {preExisting
              .map(
                ([type, v]) =>
                  `${FAULT_CATALOG.find((c) => c.type === type)?.label ?? type} at ${Math.round(
                    v * 100
                  )}%`
              )
              .join(", ")}
            .
          </p>
        )}

        {error && (
          <div
            role="alert"
            className="rounded-lg border border-status-red/40 bg-status-red/10 p-3 text-[11px]"
          >
            <p className="font-medium text-status-red">{error.message}</p>
            {error.reasons.length > 0 && (
              <ul className="mt-1.5 list-disc space-y-1 pl-4 text-slate-300">
                {error.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="flex gap-2">
          <button
            type="button"
            onClick={submitScenario}
            disabled={running}
            className={clsx(
              "flex-1 rounded-md border px-4 py-2.5 font-display text-sm font-semibold uppercase tracking-[0.12em] transition-colors",
              running
                ? "animate-pulseGlow border-status-amber/50 bg-status-amber/10 text-status-amber"
                : "border-status-cyan/50 bg-status-cyan/15 text-status-cyan shadow-glow hover:bg-status-cyan/25"
            )}
          >
            {running ? "Simulating…" : "Run Scenario"}
          </button>
          <button
            type="button"
            onClick={resetDraft}
            disabled={running}
            className="rounded-md border border-base-border px-4 py-2.5 font-mono text-xs uppercase tracking-wider text-slate-400 transition-colors hover:border-slate-500 hover:text-slate-200 disabled:opacity-40"
          >
            Reset
          </button>
        </div>
        {running && (
          <p className="text-center text-[11px] text-slate-500">
            Integrating the full physics stack headless — a long scenario takes a few
            seconds. Live telemetry is unaffected.
          </p>
        )}
      </div>
    </GlassCard>
  );
}
