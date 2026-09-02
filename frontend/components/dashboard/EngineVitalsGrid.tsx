"use client";

import clsx from "clsx";
import { Sparkline } from "@/components/charts/Sparkline";
import { BatteryAlternatorTile } from "./BatteryAlternatorTile";
import { useTelemetryStore } from "@/lib/store";
import { useTweenedNumber } from "@/hooks/useTweenedNumber";
import { HEALTHY_BANDS, type TelemetryFrame } from "@/lib/types";
import { bandStatus, type BandStatus } from "@/lib/format";

interface VitalDef {
  key: string;
  label: string;
  unit: string;
  decimals: number;
  extract: (f: TelemetryFrame) => number;
  band?: { min: number; max: number };
}

const SPARK_WINDOW = 120; // last 12s at 10Hz

const VITALS: VitalDef[] = [
  { key: "rpm", label: "RPM", unit: "rpm", decimals: 0, extract: (f) => f.rpm, band: HEALTHY_BANDS.rpm },
  ...[0, 1, 2, 3].map((i) => ({
    key: `egt_${i}`,
    label: `EGT · Cyl ${i + 1}`,
    unit: "°C",
    decimals: 0,
    extract: (f: TelemetryFrame) => f.cylinders[i]?.egt_c ?? 0,
    band: HEALTHY_BANDS.egt_c,
  })),
  { key: "cht", label: "CHT", unit: "°C", decimals: 0, extract: (f) => f.cht_c, band: HEALTHY_BANDS.cht_c },
  {
    key: "oil_pressure",
    label: "Oil Pressure",
    unit: "kPa",
    decimals: 0,
    extract: (f) => f.oil_pressure_kpa,
    band: HEALTHY_BANDS.oil_pressure_kpa,
  },
  {
    key: "oil_temp",
    label: "Oil Temp",
    unit: "°C",
    decimals: 0,
    extract: (f) => f.oil_temp_c,
    band: HEALTHY_BANDS.oil_temp_c,
  },
  {
    key: "fuel_flow",
    label: "Fuel Flow",
    unit: "L/h",
    decimals: 1,
    extract: (f) => f.fuel_flow_lph,
    band: HEALTHY_BANDS.fuel_flow_lph,
  },
  {
    key: "boost",
    label: "Boost Pressure",
    unit: "kPa",
    decimals: 0,
    extract: (f) => f.boost_pressure_kpa,
    band: HEALTHY_BANDS.boost_pressure_kpa,
  },
];

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

function VitalTile({ def }: { def: VitalDef }) {
  const buffer = useTelemetryStore((s) => s.buffer);
  const latest = useTelemetryStore((s) => s.latest);

  const slice = buffer.slice(-SPARK_WINDOW);
  const series = slice.map(def.extract);
  const current = latest ? def.extract(latest) : 0;
  const smoothed = useTweenedNumber(current);
  const status: BandStatus = def.band ? bandStatus(current, def.band) : "normal";

  return (
    <div
      role="status"
      aria-label={`${def.label}: ${smoothed.toFixed(def.decimals)} ${def.unit}${status !== "normal" ? `, ${status}` : ""}`}
      className={clsx(
        "glass-panel flex flex-col gap-2 border p-3 transition-colors duration-500",
        toneBorder[status]
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-slate-400">
          {def.label}
        </span>
        {status !== "normal" && (
          <span className={clsx("text-[9px] font-bold uppercase tracking-wide", toneText[status])}>
            {status === "critical" ? "Critical" : "Warn"}
          </span>
        )}
      </div>
      <div className="flex items-baseline gap-1">
        <span className={clsx("tabular text-2xl font-semibold leading-none", toneText[status])}>
          {smoothed.toFixed(def.decimals)}
        </span>
        <span className="text-[10px] text-slate-400">{def.unit}</span>
      </div>
      <Sparkline data={series.length ? series : [current]} color={toneLine[status]} height={30} />
    </div>
  );
}

export function EngineVitalsGrid() {
  return (
    <section>
      <h2 className="panel-title mb-2.5 px-1">Engine Vitals</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {VITALS.map((def) => (
          <VitalTile key={def.key} def={def} />
        ))}
        {/* Phase 3: electrical bus. Rendered here rather than added to VITALS because
            it shows two related signals (battery vs alternator), not one. */}
        <BatteryAlternatorTile />
      </div>
    </section>
  );
}
