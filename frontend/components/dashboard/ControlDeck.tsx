"use client";

import clsx from "clsx";
import { useRef, useState } from "react";
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

export function ControlDeck() {
  const [expanded, setExpanded] = useState(true);
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

  const throttleDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [throttleDisplay, setThrottleDisplay] = useState(throttle);

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
    <div className="sticky bottom-0 z-30 border-t border-base-border bg-base-bg/95 backdrop-blur">
      <div className="mx-auto max-w-[1800px] px-4 py-2 sm:px-6">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mb-2 flex w-full items-center justify-between text-left"
        >
          <span className="panel-title">Control Deck</span>
          <span className="text-[11px] text-slate-500">{expanded ? "Hide ▾" : "Show ▴"}</span>
        </button>

        {expanded && (
          <div className="grid grid-cols-1 gap-4 pb-3 xl:grid-cols-[minmax(0,200px)_minmax(0,200px)_minmax(0,240px)_1fr] ">
            {/* Throttle */}
            <div className="glass-panel p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[10px] uppercase tracking-wider text-slate-500">Throttle</span>
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
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-500">
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
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-500">
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
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-500">
                Fault Injection · engine
              </span>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {FAULT_CATALOG.map((meta) => {
                  const severity = activeFaults.get(meta.type);
                  const isActive = severity !== undefined;
                  return (
                    <button
                      key={meta.type}
                      onClick={() =>
                        isActive ? clearFault(meta.type as FaultType) : injectFault(meta.type as FaultType)
                      }
                      title={meta.description}
                      className={clsx(
                        "flex flex-col items-start gap-0.5 rounded-md border px-2 py-1.5 text-left transition-colors",
                        isActive
                          ? severityTone(severity)
                          : "border-base-border text-slate-400 hover:border-status-cyan/30"
                      )}
                    >
                      <span className="font-mono text-[11px] font-medium leading-tight">{meta.label}</span>
                      <span className="text-[10px] opacity-80">
                        {isActive ? `${Math.round(severity * 100)}% · tap to clear` : "tap to inject"}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Phase 3: sensor faults. Kept visually separate from engine faults because
                they are a different kind of thing — these corrupt the *reading*, not the
                machine, and the whole point of the demo is that the operator can tell. */}
            <div className="glass-panel p-3">
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-500">
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
                  return (
                    <button
                      key={meta.type}
                      onClick={() =>
                        isActive
                          ? clearSensorFault(meta.type as SensorFaultType)
                          : injectSensorFault(meta.type as SensorFaultType)
                      }
                      title={meta.description}
                      className={clsx(
                        "flex flex-col items-start gap-0.5 rounded-md border px-2 py-1.5 text-left transition-colors",
                        isLastOfOddRow && "col-span-2",
                        isActive
                          ? severityTone(severity)
                          : "border-base-border text-slate-400 hover:border-status-cyan/30"
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
              <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-500">
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
                <p className="mt-2 tabular text-[10px] text-slate-500">
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
