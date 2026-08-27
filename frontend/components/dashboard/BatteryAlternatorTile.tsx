"use client";

import clsx from "clsx";
import { Sparkline } from "@/components/charts/Sparkline";
import { useTelemetryStore } from "@/lib/store";
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
  normal: "#3fd0e0",
  warn: "#f5a623",
  critical: "#ef4a5f",
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

  const series = buffer
    .slice(-SPARK_WINDOW)
    .map((f) => f.battery_voltage_v)
    .filter((v): v is number => v != null);

  const status: BandStatus =
    battery == null ? "normal" : bandStatus(battery, HEALTHY_BANDS.battery_voltage_v);

  const charging = battery != null && alternator != null && alternator > battery;

  return (
    <div
      className={clsx(
        "glass-panel flex flex-col gap-1.5 border p-3 transition-shadow duration-500",
        toneBorder[status]
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wider text-slate-500">
          Bus Voltage
        </span>
        {battery != null && (
          <span
            className={clsx(
              "text-[9px] uppercase tracking-wide",
              charging ? "text-status-go" : "text-status-amber"
            )}
          >
            {charging ? "chg" : "dis"}
          </span>
        )}
      </div>

      <div className="flex items-baseline gap-1">
        <span className={clsx("tabular text-xl font-semibold", toneText[status])}>
          {battery != null ? battery.toFixed(1) : "—"}
        </span>
        <span className="text-[10px] text-slate-500">V</span>
        {alternator != null && (
          <span className="ml-auto tabular text-[10px] text-slate-500">
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
