"use client";

import clsx from "clsx";
import { Sparkline } from "@/components/charts/Sparkline";
import { useTelemetryStore } from "@/lib/store";
import { useTweenedNumber } from "@/hooks/useTweenedNumber";
import { HEALTHY_BANDS } from "@/lib/types";
import { bandStatus, type BandStatus } from "@/lib/format";

const SPARK_WINDOW = 120;

const toneBorder: Record<BandStatus, string> = {
  normal: "border-base-border",
  warn: "border-status-amber/60 shadow-glow-amber",
  critical: "border-status-red/70 shadow-glow-red",
};

const toneText: Record<BandStatus, string> = {
  normal: "text-slate-100",
  warn: "text-status-amber",
  critical: "text-status-red",
};

const toneLine: Record<BandStatus, string> = {
  normal: "#4ab9c6",
  warn: "#d59834",
  critical: "#da6978",
};

/**
 * Bus voltage tile. Shows battery terminal voltage against alternator output, because
 * the two together say something neither says alone: a low battery with healthy
 * alternator output is a pack problem, while both low means the alternator has stopped
 * carrying the load.
 */
export function BatteryAlternatorTile() {
  const buffer = useTelemetryStore((s) => s.buffer);
  const latest = useTelemetryStore((s) => s.latest);

  const battery = latest?.battery_voltage_v ?? null;
  const alternator = latest?.alternator_output_v ?? null;
  const smoothedBattery = useTweenedNumber(battery ?? 0);

  const series = buffer
    .slice(-SPARK_WINDOW)
    .map((f) => f.battery_voltage_v)
    .filter((v): v is number => v != null);

  const status: BandStatus =
    battery == null ? "normal" : bandStatus(battery, HEALTHY_BANDS.battery_voltage_v);

  const charging = battery != null && alternator != null && alternator > battery;

  return (
    <div
      role="status"
      aria-label={`Bus voltage: ${battery != null ? smoothedBattery.toFixed(1) : "no reading"} volts${status !== "normal" ? `, ${status}` : ""}`}
      className={clsx(
        "glass-panel flex flex-col gap-2 border p-3 transition-colors duration-500",
        toneBorder[status]
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-slate-400">
          Bus Voltage
        </span>
        <span className="flex items-center gap-1">
          {status !== "normal" && (
            <span className={clsx("text-[9px] font-bold uppercase tracking-wide", toneText[status])}>
              {status === "critical" ? "Critical" : "Warn"}
            </span>
          )}
          {battery != null && (
            <span
              className={clsx(
                "text-[9px] font-medium uppercase tracking-wide",
                charging ? "text-status-go" : "text-status-amber"
              )}
            >
              {charging ? "chg" : "dis"}
            </span>
          )}
        </span>
      </div>

      <div className="flex items-baseline gap-1">
        <span className={clsx("tabular text-2xl font-semibold leading-none", toneText[status])}>
          {battery != null ? smoothedBattery.toFixed(1) : "—"}
        </span>
        <span className="text-[10px] text-slate-400">V</span>
        {alternator != null && (
          <span className="ml-auto tabular text-[10px] text-slate-400">
            alt {alternator.toFixed(1)}V
          </span>
        )}
      </div>

      <Sparkline
        data={series.length ? series : [battery ?? 0]}
        color={toneLine[status]}
        height={30}
      />
    </div>
  );
}
