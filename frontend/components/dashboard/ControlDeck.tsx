"use client";

import clsx from "clsx";
import { useRef, useState } from "react";
import { useAuthStore } from "@/lib/auth/store";
import { useTelemetryStore } from "@/lib/store";
import {
  FAULT_CATALOG,
  MISSION_PHASES,
  SENSOR_FAULT_CATALOG,
  type FaultType,
  type MissionPhase,
  type SensorFaultType,
} from "@/lib/types";
import { MissionReplayControls } from "./MissionReplayControls";

const PHASE_LABEL: Record<MissionPhase, string> = {
  climb: "Climb",
  cruise: "Cruise",
  loiter: "Loiter",
  descent: "Descent",
};

const TIME_SCALES = [1, 5, 20];

function severityTone(s: number): string {
  if (s >= 0.7) return "border-status-red/60 bg-status-red/15 text-status-red";
  if (s >= 0.35) return "border-status-amber/60 bg-status-amber/15 text-status-amber";
  return "border-status-cyan/50 bg-status-cyan/10 text-status-cyan";
}

function severityTextTone(s: number): string {
  if (s >= 0.7) return "text-status-red";
  if (s >= 0.35) return "text-status-amber";
  return "text-status-cyan";
}

const DEFAULT_RAMP_SECONDS = 10;

/** One fault type: target-severity slider + ramp time + Inject/Update + live actual
 * readout from `active_faults[]`. Target defaults to 50% until touched; ramp defaults
 * to a fast 10s so a demo doesn't wait around. */
function FaultInjectRow({
  label,
  description,
  targetPct,
  rampSeconds,
  actualSeverity,
  injectDisabled,
  onTargetChange,
  onRampChange,
  onInject,
  onClear,
}: {
  label: string;
  description: string;
  targetPct: number;
  rampSeconds: number;
  actualSeverity: number | undefined;
  injectDisabled: boolean;
  onTargetChange: (pct: number) => void;
  onRampChange: (seconds: number) => void;
  onInject: () => void;
  onClear: () => void;
}) {
  const isActive = actualSeverity !== undefined;
  return (
    <div className="rounded-md border border-base-border/70 bg-base-panel2/30 p-1.5">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span
          className="truncate font-mono text-[11px] font-medium text-slate-300"
          title={description}
        >
          {label}
        </span>
        <span
          className={clsx(
            "tabular shrink-0 text-[10px]",
            isActive ? severityTextTone(actualSeverity) : "text-slate-400"
          )}
        >
          actual {Math.round((actualSeverity ?? 0) * 100)}%
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={targetPct}
          disabled={injectDisabled}
          onChange={(e) => onTargetChange(Number(e.target.value))}
          className="h-1 min-w-0 flex-1 accent-status-amber disabled:opacity-40"
        />
        <span className="tabular w-8 shrink-0 text-right text-[10px] text-slate-400">
          {targetPct}%
        </span>
        <input
          type="number"
          min={0}
          max={600}
          step={1}
          value={rampSeconds}
          title="Ramp time (s)"
          disabled={injectDisabled}
          onChange={(e) => onRampChange(Number(e.target.value))}
          className="tabular w-11 shrink-0 rounded border border-base-border bg-base-panel2/60 px-1 py-0.5 text-[10px] text-slate-200 outline-none focus:border-status-cyan/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-cyan disabled:opacity-40"
        />
        <button
          type="button"
          onClick={onInject}
          disabled={injectDisabled}
          title={injectDisabled ? "Fault injection unavailable (demo mode off, or not signed in as administrator)" : undefined}
          className="shrink-0 rounded border border-status-cyan/50 bg-status-cyan/10 px-1.5 py-0.5 font-mono text-[10px] uppercase text-status-cyan transition-colors hover:bg-status-cyan/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-cyan disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-status-cyan/10"
        >
          {isActive ? "Update" : "Inject"}
        </button>
        {isActive && (
          <button
            type="button"
            onClick={onClear}
            className="shrink-0 rounded border border-status-red/40 px-1.5 py-0.5 font-mono text-[10px] uppercase text-status-red transition-colors hover:bg-status-red/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-cyan"
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

export function ControlDeck() {
  // Collapsed by default: expanded, this sticky-bottom panel now runs tall enough
  // (per-fault severity rows, ramp inputs) to cover most of the viewport over the
  // dashboard it docks against — starting collapsed puts telemetry in view first.
  const [expanded, setExpanded] = useState(false);
  const throttle = useTelemetryStore((s) => s.throttle);
  const timeScale = useTelemetryStore((s) => s.timeScale);
  const setThrottle = useTelemetryStore((s) => s.setThrottle);
  const setTimeScale = useTelemetryStore((s) => s.setTimeScale);
  const jumpPhase = useTelemetryStore((s) => s.jumpPhase);
  const injectFault = useTelemetryStore((s) => s.injectFault);
  const clearFault = useTelemetryStore((s) => s.clearFault);
  const injectSensorFault = useTelemetryStore((s) => s.injectSensorFault);
  const clearSensorFault = useTelemetryStore((s) => s.clearSensorFault);
  const applyScenario = useTelemetryStore((s) => s.applyScenario);
  const latest = useTelemetryStore((s) => s.latest);

  const role = useAuthStore((s) => s.role);
  const demoMode = useAuthStore((s) => s.demoMode);
  //: Mirrors the backend gate exactly — see app/auth/deps.py::require_demo_mode and
  //: require_role(ROLE_ADMINISTRATOR) on POST /control/fault and /control/sensor-fault.
  //: Clearing an active fault stays available to any role; only injection is gated.
  const canInjectFaults = demoMode && role === "administrator";

  const throttleDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [throttleDisplay, setThrottleDisplay] = useState(throttle);

  const [faultDrafts, setFaultDrafts] = useState<
    Record<string, { pct: number; ramp: number }>
  >({});
  function faultDraft(type: string) {
    return faultDrafts[type] ?? { pct: 50, ramp: DEFAULT_RAMP_SECONDS };
  }
  function patchFaultDraft(type: string, patch: Partial<{ pct: number; ramp: number }>) {
    setFaultDrafts((d) => ({ ...d, [type]: { ...faultDraft(type), ...patch } }));
  }

  function onThrottleChange(value: number) {
    setThrottleDisplay(value);
    if (throttleDebounce.current) clearTimeout(throttleDebounce.current);
    throttleDebounce.current = setTimeout(() => setThrottle(value), 80);
  }

  const activeFaults = new Map(
    (latest?.active_faults ?? [])
      .filter((f) => !f.is_sensor_fault)
      .map((f) => [f.type, f.severity])
  );
  const activeSensorFaults = new Map(
    (latest?.active_faults ?? [])
      .filter((f) => f.is_sensor_fault)
      .map((f) => [f.type, f.severity])
  );

  return (
    <div className="sticky bottom-0 z-30 border-t border-base-border bg-base-bg">
      <div className="page-container py-2">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mb-2 flex w-full items-center justify-between text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-cyan"
          aria-expanded={expanded}
        >
          <span className="panel-title">Control Deck</span>
          <span className="text-[11px] text-slate-400">{expanded ? "Hide ▾" : "Show ▴"}</span>
        </button>

        {expanded && (
          <div className="grid grid-cols-1 gap-4 pb-3 xl:grid-cols-[minmax(0,200px)_minmax(0,200px)_minmax(0,240px)_1fr] ">
            {/* Throttle */}
            <div className="glass-panel p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[10px] uppercase tracking-wider text-slate-400">Throttle</span>
                <span className="tabular text-sm text-status-cyan">{Math.round(throttleDisplay * 100)}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={throttleDisplay}
                onChange={(e) => onThrottleChange(Number(e.target.value))}
                className="w-full accent-status-cyan"
              />
            </div>

            {/* Time scale */}
            <div className="glass-panel p-3">
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-400">
                Time Acceleration
              </span>
              <div className="flex gap-2">
                {TIME_SCALES.map((f) => (
                  <button
                    key={f}
                    onClick={() => setTimeScale(f)}
                    className={clsx(
                      "flex-1 rounded-md border px-2 py-1.5 font-mono text-xs font-medium transition-colors",
                      timeScale === f
                        ? "border-status-cyan/60 bg-status-cyan/15 text-status-cyan"
                        : "border-base-border text-slate-400 hover:border-status-cyan/30"
                    )}
                  >
                    {f}x
                  </button>
                ))}
              </div>
            </div>

            {/* Phase jump */}
            <div className="glass-panel p-3">
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-400">
                Mission Phase
              </span>
              <div className="grid grid-cols-2 gap-2">
                {MISSION_PHASES.map((p) => (
                  <button
                    key={p}
                    onClick={() => jumpPhase(p)}
                    className={clsx(
                      "rounded-md border px-2 py-1.5 font-mono text-xs font-medium transition-colors",
                      latest?.mission_phase === p
                        ? "border-status-cyan/60 bg-status-cyan/15 text-status-cyan"
                        : "border-base-border text-slate-400 hover:border-status-cyan/30"
                    )}
                  >
                    {PHASE_LABEL[p]}
                  </button>
                ))}
              </div>
            </div>

            {/* Fault injection grid */}
            <div className="glass-panel p-3">
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-400">
                Fault Injection · engine
              </span>
              {!canInjectFaults && (
                <p className="mb-2 rounded-md border border-base-border/70 bg-base-panel2/40 px-2 py-1.5 text-[10px] text-slate-400">
                  {!demoMode
                    ? "Disabled — DEMO_MODE is off (real-deployment posture)."
                    : "Administrator role required to inject faults."}
                </p>
              )}
              <div className="grid max-h-72 grid-cols-1 gap-1.5 overflow-y-auto pr-1">
                {FAULT_CATALOG.map((meta) => {
                  const severity = activeFaults.get(meta.type);
                  const draft = faultDraft(meta.type);
                  return (
                    <FaultInjectRow
                      key={meta.type}
                      label={meta.label}
                      description={meta.description}
                      targetPct={draft.pct}
                      rampSeconds={draft.ramp}
                      actualSeverity={severity}
                      injectDisabled={!canInjectFaults}
                      onTargetChange={(pct) => patchFaultDraft(meta.type, { pct })}
                      onRampChange={(ramp) => patchFaultDraft(meta.type, { ramp })}
                      onInject={() =>
                        injectFault(meta.type as FaultType, draft.pct / 100, draft.ramp)
                      }
                      onClear={() => clearFault(meta.type as FaultType)}
                    />
                  );
                })}
              </div>
            </div>

            {/* Phase 3: sensor faults. Kept visually separate from engine faults because
                they are a different kind of thing — these corrupt the *reading*, not the
                machine, and the whole point of the demo is that the operator can tell. */}
            <div className="glass-panel p-3">
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-400">
                Sensor Faults · instrumentation only
              </span>
              <div className="grid grid-cols-2 gap-2">
                {SENSOR_FAULT_CATALOG.map((meta, i) => {
                  const severity = activeSensorFaults.get(meta.type);
                  const isActive = severity !== undefined;
                  // Odd-length catalog: let the last button take the full row instead of
                  // leaving a dangling half-empty row underneath the rest.
                  const isLastOfOddRow =
                    i === SENSOR_FAULT_CATALOG.length - 1 && SENSOR_FAULT_CATALOG.length % 2 === 1;
                  const disabled = !isActive && !canInjectFaults;
                  return (
                    <button
                      key={meta.type}
                      disabled={disabled}
                      onClick={() =>
                        isActive
                          ? clearSensorFault(meta.type as SensorFaultType)
                          : injectSensorFault(meta.type as SensorFaultType)
                      }
                      title={disabled ? "Fault injection unavailable (demo mode off, or not signed in as administrator)" : meta.description}
                      className={clsx(
                        "flex flex-col items-start gap-0.5 rounded-md border px-2 py-1.5 text-left transition-colors",
                        isLastOfOddRow && "col-span-2",
                        isActive
                          ? severityTone(severity)
                          : "border-base-border text-slate-400 hover:border-status-cyan/30",
                        disabled && "cursor-not-allowed opacity-30 hover:border-base-border"
                      )}
                    >
                      <span className="font-mono text-[11px] font-medium leading-tight">
                        {meta.label}
                      </span>
                      <span className="text-[10px] opacity-80">
                        {isActive
                          ? `${Math.round(severity * 100)}% · tap to clear`
                          : meta.corruptionKind}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Environment */}
            <div className="glass-panel p-3">
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-400">
                Environment
              </span>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { id: "standard", label: "ISA Std" },
                  { id: "hot_weather", label: "Hot 48°C" },
                  { id: "cold_soak", label: "Cold −25°C" },
                ].map((sc) => (
                  <button
                    key={sc.id}
                    onClick={() => applyScenario(sc.id)}
                    className="rounded-md border border-base-border px-2 py-1.5 font-mono text-[11px] text-slate-400 transition-colors hover:border-status-cyan/30"
                  >
                    {sc.label}
                  </button>
                ))}
              </div>
              {latest?.ambient_temperature_c != null && (
                <p className="mt-2 tabular text-[10px] text-slate-400">
                  ambient {latest.ambient_temperature_c.toFixed(1)}°C
                  {latest.injection_timing_deg != null && (
                    <> · timing {latest.injection_timing_deg.toFixed(1)}° BTDC</>
                  )}
                </p>
              )}
            </div>

            <MissionReplayControls />
          </div>
        )}
      </div>
    </div>
  );
}
