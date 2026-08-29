"use client";

/**
 * Operating-point optimizer: pick an objective and conditions, get a recommended
 * throttle / mixture / timing setpoint and what it costs against the book cruise setting.
 *
 * The comparison is the point of this panel, so the recommended and baseline setpoints are
 * shown side by side with the deltas as the headline. Every delta uses the backend's sign
 * convention — **positive is better for the thing it names** — so "range +6.1%" and "life
 * −79.5%" read the way an operator expects without needing to know that one is derived
 * from an inverted BSFC and the other from a stress rate.
 */
import { useEffect } from "react";
import clsx from "clsx";
import { GlassCard } from "@/components/ui/GlassCard";
import { StatusPill } from "@/components/ui/StatusPill";
import { useTestBenchStore } from "@/lib/testbench/store";
import {
  OBJECTIVE_LABELS,
  type OptimizerObjective,
  type SafetyCheck,
  type SetpointEvaluation,
} from "@/lib/testbench/types";

const OBJECTIVES: OptimizerObjective[] = [
  "max_range",
  "max_power",
  "max_engine_life",
  "balanced",
];

const OBJECTIVE_HINT: Record<OptimizerObjective, string> = {
  max_range: "Hold cruise power for the least fuel",
  max_power: "Most power the limits allow",
  max_engine_life: "Lowest thermal and mechanical stress rate",
  balanced: "Meet the power requirement, then trade fuel against wear",
};

function DeltaTile({
  label,
  value,
  suffix = "%",
  sub,
}: {
  label: string;
  value: number;
  suffix?: string;
  sub?: string;
}) {
  const positive = value > 0.05;
  const negative = value < -0.05;
  return (
    <div className="rounded-lg border border-base-border/70 bg-base-panel2/40 px-3 py-2.5">
      <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
        {label}
      </span>
      <span
        className={clsx(
          "tabular text-2xl font-semibold leading-none",
          positive ? "text-status-go" : negative ? "text-status-red" : "text-slate-300"
        )}
      >
        {value > 0 ? "+" : ""}
        {value.toFixed(1)}
        <span className="ml-0.5 text-sm font-normal text-slate-500">{suffix}</span>
      </span>
      {sub && <span className="mt-1 block text-[10px] text-slate-600">{sub}</span>}
    </div>
  );
}

function SetpointColumn({
  title,
  accent,
  evaluation,
  note,
}: {
  title: string;
  accent: string;
  evaluation: SetpointEvaluation;
  note?: string;
}) {
  const p = evaluation.predicted;
  const rows: [string, string][] = [
    ["Throttle", `${evaluation.setpoint.throttle_pct.toFixed(0)} %`],
    ["AFR trim", `${evaluation.setpoint.afr_trim >= 0 ? "+" : ""}${evaluation.setpoint.afr_trim.toFixed(2)}`],
    [
      "Timing trim",
      `${evaluation.setpoint.injection_timing_trim_deg >= 0 ? "+" : ""}${evaluation.setpoint.injection_timing_trim_deg.toFixed(1)}°`,
    ],
    ["Power", `${p.power_kw.toFixed(1)} kW`],
    ["BSFC", p.bsfc_g_per_kwh !== null ? `${p.bsfc_g_per_kwh.toFixed(0)} g/kWh` : "—"],
    ["Fuel flow", `${p.fuel_flow_lph.toFixed(1)} L/h`],
    ["CHT", `${p.cht_c.toFixed(0)} °C`],
    ["EGT (max)", `${p.egt_max_c.toFixed(0)} °C`],
    ["Oil temp", `${p.oil_temp_c.toFixed(0)} °C`],
    ["Oil press.", `${p.oil_pressure_kpa.toFixed(0)} kPa`],
    ["RPM", p.rpm.toFixed(0)],
    ["Life @ this point", `${evaluation.estimated_life_hours.toFixed(0)} h`],
  ];
  return (
    <div className="flex-1 rounded-lg border border-base-border/70 bg-base-panel2/30 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className={clsx("font-display text-xs font-semibold uppercase tracking-[0.14em]", accent)}>
          {title}
        </span>
        {!evaluation.feasible && (
          <StatusPill tone="nogo">Out of limits</StatusPill>
        )}
      </div>
      <dl className="space-y-0.5">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between text-[11px]">
            <dt className="text-slate-500">{k}</dt>
            <dd className="tabular text-slate-200">{v}</dd>
          </div>
        ))}
      </dl>
      {note && <p className="mt-2 text-[10px] leading-relaxed text-slate-600">{note}</p>}
    </div>
  );
}

function SafetyRow({ check }: { check: SafetyCheck }) {
  return (
    <li
      className={clsx(
        "flex items-baseline gap-2 rounded-md border px-2.5 py-1.5 text-[11px]",
        check.ok
          ? "border-base-border/70 bg-base-panel2/30"
          : "border-status-red/40 bg-status-red/10"
      )}
    >
      <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", check.ok ? "bg-status-go" : "bg-status-red")} />
      <span className="text-slate-300">{check.label}</span>
      <span className="tabular ml-auto text-slate-200">
        {check.value.toFixed(0)} {check.unit}
      </span>
      <span className="tabular w-24 text-right text-slate-500">
        {check.direction === "max" ? "max" : "min"} {check.limit.toFixed(0)} ·{" "}
        <span className={check.ok ? "text-status-go" : "text-status-red"}>
          {check.margin >= 0 ? "+" : ""}
          {check.margin.toFixed(0)}
        </span>
      </span>
    </li>
  );
}

export function OptimizerPanel() {
  const draft = useTestBenchStore((s) => s.optimizerDraft);
  const result = useTestBenchStore((s) => s.optimizerResult);
  const optimizing = useTestBenchStore((s) => s.optimizing);
  const error = useTestBenchStore((s) => s.optimizerError);
  const patch = useTestBenchStore((s) => s.patchOptimizerDraft);
  const submit = useTestBenchStore((s) => s.submitOptimizer);
  const envelope = useTestBenchStore((s) => s.envelope);
  const loadEnvelope = useTestBenchStore((s) => s.loadEnvelope);

  useEffect(() => {
    loadEnvelope();
  }, [loadEnvelope]);

  const standardDay = draft.ambient_temperature_c === null;

  return (
    <GlassCard
      title="Operating-Point Optimizer"
      subtitle="Recommended setpoint versus the book cruise setting"
      glow="cyan"
      bodyClassName="space-y-4 p-4"
    >
      {/* ---- inputs ---- */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <label className="block sm:col-span-2">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
            Objective
          </span>
          <select
            value={draft.objective}
            onChange={(e) => patch({ objective: e.target.value as OptimizerObjective })}
            className="w-full rounded-md border border-base-border bg-base-panel2/60 px-2.5 py-1.5 text-sm text-slate-100 outline-none focus:border-status-cyan/60"
          >
            {OBJECTIVES.map((o) => (
              <option key={o} value={o}>
                {OBJECTIVE_LABELS[o]}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-[10px] text-slate-600">
            {OBJECTIVE_HINT[draft.objective]}
          </span>
        </label>

        <label className="block">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
            Altitude <span className="normal-case tracking-normal text-slate-600">(m)</span>
          </span>
          <input
            type="number"
            value={draft.altitude_m}
            min={envelope?.altitude_m.min ?? 0}
            max={envelope?.altitude_m.max ?? 8000}
            step={100}
            onChange={(e) => patch({ altitude_m: Number(e.target.value) })}
            className="tabular w-full rounded-md border border-base-border bg-base-panel2/60 px-2.5 py-1.5 text-sm text-slate-100 outline-none focus:border-status-cyan/60"
          />
        </label>

        <div>
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
            Ambient <span className="normal-case tracking-normal text-slate-600">(°C)</span>
          </span>
          <div className="flex gap-1.5">
            <input
              type="number"
              disabled={standardDay}
              placeholder="ISA"
              value={standardDay ? "" : draft.ambient_temperature_c ?? 0}
              onChange={(e) => patch({ ambient_temperature_c: Number(e.target.value) })}
              className="tabular w-full rounded-md border border-base-border bg-base-panel2/60 px-2.5 py-1.5 text-sm text-slate-100 outline-none focus:border-status-cyan/60 disabled:opacity-40"
            />
            <button
              type="button"
              onClick={() => patch({ ambient_temperature_c: standardDay ? 30 : null })}
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
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex cursor-pointer items-center gap-2 text-[11px] text-slate-300">
          <input
            type="checkbox"
            checked={draft.use_current_engine_health}
            onChange={(e) => patch({ use_current_engine_health: e.target.checked })}
            className="h-3.5 w-3.5 accent-status-cyan"
          />
          Use current engine health
          <span className="text-slate-600">
            (optimise for the live engine&rsquo;s actual wear, not a pristine one)
          </span>
        </label>

        <button
          type="button"
          onClick={submit}
          disabled={optimizing}
          className={clsx(
            "ml-auto rounded-md border px-5 py-2 font-display text-sm font-semibold uppercase tracking-[0.12em] transition-colors",
            optimizing
              ? "animate-pulseGlow border-status-amber/50 bg-status-amber/10 text-status-amber"
              : "border-status-cyan/50 bg-status-cyan/15 text-status-cyan shadow-glow hover:bg-status-cyan/25"
          )}
        >
          {optimizing ? "Searching…" : "Calculate"}
        </button>
      </div>

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

      {result && (
        <div className="space-y-4 border-t border-base-border/70 pt-4">
          {/* ---- headline deltas ---- */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <DeltaTile label="Power" value={result.comparison.power_pct} />
            <DeltaTile
              label="Specific Range"
              value={result.comparison.range_equivalent_pct}
              sub="at constant power"
            />
            <DeltaTile
              label="Projected Life"
              value={result.comparison.estimated_rul_impact_pct}
              sub={`${result.comparison.life_hours_baseline.toFixed(0)} → ${result.comparison.life_hours_recommended.toFixed(0)} h`}
            />
            <DeltaTile
              label="Fuel Flow"
              value={-result.comparison.fuel_flow_delta_lph}
              suffix=" L/h"
              sub="saved per hour"
            />
          </div>

          <p className="rounded-lg border border-base-border/70 bg-base-panel2/30 p-3 text-[11px] leading-relaxed text-slate-300">
            {result.rationale}
          </p>

          {!result.feasible && (
            <div className="rounded-lg border border-status-red/50 bg-status-red/10 p-3">
              <p className="font-display text-xs font-bold uppercase tracking-[0.14em] text-status-red">
                No safe setpoint exists here
              </p>
              <p className="mt-1 text-[11px] text-slate-300">
                Every candidate breaches a limit. The setpoint below is the closest the
                search got — it is not a recommendation.
              </p>
            </div>
          )}

          {result.safety_notes.length > 0 && (
            <ul className="space-y-1.5">
              {result.safety_notes.map((n) => (
                <li
                  key={n}
                  className="rounded-md border border-status-amber/30 bg-status-amber/5 px-2.5 py-1.5 text-[11px] leading-relaxed text-status-amber"
                >
                  {n}
                </li>
              ))}
            </ul>
          )}

          {/* ---- before / after ---- */}
          <div className="flex flex-col gap-3 sm:flex-row">
            <SetpointColumn
              title="Baseline (book)"
              accent="text-slate-400"
              evaluation={result.baseline}
              note="The nominal cruise setting, evaluated at these same conditions and on this same engine."
            />
            <SetpointColumn
              title={`Recommended · ${result.objective_label}`}
              accent={result.feasible ? "text-status-cyan" : "text-status-red"}
              evaluation={result.recommended}
              note={`Steady state · ${result.evaluations} evaluations in ${result.compute_seconds.toFixed(1)} s · ${result.health_source}`}
            />
          </div>

          {/* ---- hard bounds ---- */}
          <div>
            <span className="panel-title">Hard Safety Bounds At The Recommended Point</span>
            <ul className="mt-2 space-y-1">
              {result.recommended.safety.map((c) => (
                <SafetyRow key={c.parameter} check={c} />
              ))}
            </ul>
            {result.health.severity_index > 0 && (
              <p className="mt-2 text-[10px] leading-relaxed text-slate-600">
                Limits shown are derated for {(result.health.severity_index * 100).toFixed(0)}%
                accumulated wear ({Object.entries(result.health.faults)
                  .map(([k, v]) => `${k.replace(/_/g, " ")} ${(v * 100).toFixed(0)}%`)
                  .join(", ")}). Continuous power rating derated to{" "}
                {result.constraints.power_ceiling_kw?.toFixed(1)} kW from{" "}
                {result.constraints.rated_power_kw?.toFixed(1)} kW.
              </p>
            )}
          </div>

          <ul className="space-y-0.5 text-[10px] text-slate-600">
            {result.notes.map((n) => (
              <li key={n}>· {n}</li>
            ))}
          </ul>
        </div>
      )}
    </GlassCard>
  );
}
