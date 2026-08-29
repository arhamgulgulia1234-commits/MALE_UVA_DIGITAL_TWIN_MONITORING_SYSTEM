"use client";

/**
 * Renders a completed scenario: PASS/CAUTION/FAIL banner, health gauge, time-series and
 * the limit excursions that drove the verdict.
 *
 * Everything here is *static*. It reuses the dashboard's chart and gauge primitives —
 * MultiLineChart, RadialGauge, GlassCard, StatusPill — but none of the live panels
 * themselves, because those subscribe to the WebSocket store and would go on updating
 * from live telemetry while displaying what is supposed to be a frozen result.
 *
 * The verdict banner reports the *worst* point of the run, not the last one. A scenario
 * that dipped into NO-GO halfway through and recovered by the end is exactly the mission
 * a planner needs warned about, and an end-state summary would hide it completely.
 */
import { useMemo } from "react";
import clsx from "clsx";
import { MultiLineChart, type SeriesDef } from "@/components/charts/MultiLineChart";
import { RadialGauge } from "@/components/gauges/RadialGauge";
import { GlassCard } from "@/components/ui/GlassCard";
import { MetricValue } from "@/components/ui/MetricValue";
import { StatusPill } from "@/components/ui/StatusPill";
import { formatRul } from "@/lib/format";
import { useTestBenchStore } from "@/lib/testbench/store";
import type { LimitExcursion, ScenarioResult, Verdict } from "@/lib/testbench/types";
import type { RecoveryRecommendation } from "@/lib/types";

const VERDICT_STYLE: Record<
  Verdict,
  { border: string; bg: string; text: string; glow: string; tone: "go" | "caution" | "nogo" }
> = {
  PASS: {
    border: "border-status-go/50",
    bg: "bg-status-go/10",
    text: "text-status-go",
    glow: "shadow-glow-go",
    tone: "go",
  },
  CAUTION: {
    border: "border-status-amber/50",
    bg: "bg-status-amber/10",
    text: "text-status-amber",
    glow: "shadow-glow-amber",
    tone: "caution",
  },
  FAIL: {
    border: "border-status-red/50",
    bg: "bg-status-red/10",
    text: "text-status-red",
    glow: "shadow-glow-red",
    tone: "nogo",
  },
};

/** Phase 5: recovery_reliability's own pill tone, kept separate from VERDICT_STYLE so
 * the two readouts never share a lookup that could make them drift in step. */
const RECOVERY_TONE: Record<RecoveryRecommendation, "go" | "caution" | "nogo"> = {
  "RTB-SAFE": "go",
  "RTB-CAUTION": "caution",
  "RTB-AT-RISK": "nogo",
};

const PARAMETER_LABEL: Record<string, string> = {
  cht_c: "Cylinder head temp",
  egt_c: "Exhaust gas temp",
  oil_temp_c: "Oil temperature",
  oil_pressure_kpa: "Oil pressure",
};

const TEMP_SERIES: SeriesDef[] = [
  { key: "egt", color: "#ef4a5f", label: "Max EGT (°C)", yAxisId: "left" },
  { key: "cht", color: "#f5a623", label: "CHT (°C)", yAxisId: "left" },
  { key: "oil_temp", color: "#c084fc", label: "Oil Temp (°C)", yAxisId: "left" },
  { key: "rpm", color: "#3fd0e0", label: "RPM", yAxisId: "right" },
];

const HEALTH_SERIES: SeriesDef[] = [
  { key: "health", color: "#22d3a8", label: "Overall Health", yAxisId: "left" },
  { key: "reliability", color: "#3fd0e0", label: "Mission Reliability ×100", yAxisId: "left" },
  // Phase 5: recovery_reliability, in amber so it reads as its own thing next to
  // mission reliability's cyan rather than a shade of the same series.
  { key: "recovery", color: "#f5a623", label: "Recovery Reliability ×100", yAxisId: "left" },
  { key: "oil_p", color: "#c084fc", label: "Oil Press. (kPa)", yAxisId: "right" },
];

/** Scenario charts are plotted in minutes from T+0, not seconds. */
const minuteTick = (v: number) => `${v}m`;

function scoreColor(score: number): string {
  if (score >= 85) return "#22d3a8";
  if (score >= 60) return "#f5a623";
  return "#ef4a5f";
}

function Stat({
  label,
  value,
  unit,
  tone = "normal",
  sub,
}: {
  label: string;
  value: string | number;
  unit?: string;
  tone?: "normal" | "amber" | "red" | "cyan";
  sub?: string;
}) {
  return (
    <div className="rounded-lg border border-base-border/70 bg-base-panel2/30 px-3 py-2.5">
      <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">
        {label}
      </span>
      <MetricValue value={value} unit={unit} size="sm" tone={tone} />
      {sub && <span className="mt-1 block text-[10px] text-slate-600">{sub}</span>}
    </div>
  );
}

function ExcursionRow({ excursion }: { excursion: LimitExcursion }) {
  const hard = excursion.band === "limit";
  return (
    <li
      className={clsx(
        "flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-md border px-2.5 py-1.5 text-[11px]",
        hard
          ? "border-status-red/40 bg-status-red/10"
          : "border-status-amber/30 bg-status-amber/5"
      )}
    >
      <span className={clsx("font-medium", hard ? "text-status-red" : "text-status-amber")}>
        {PARAMETER_LABEL[excursion.parameter] ?? excursion.parameter}
      </span>
      <span className="text-slate-400">
        {excursion.direction} {excursion.limit}
        {excursion.unit} {hard ? "red-line" : "caution"}
      </span>
      <span className="tabular ml-auto text-slate-300">
        peak {excursion.peak_value}
        {excursion.unit}
      </span>
      <span className="tabular text-slate-500">
        {excursion.duration_min.toFixed(1)} min from T+{excursion.first_at_min.toFixed(1)}
      </span>
    </li>
  );
}

export function ScenarioResultsView() {
  const result = useTestBenchStore((s) => s.result);
  const running = useTestBenchStore((s) => s.running);

  const charts = useMemo(() => buildCharts(result), [result]);

  if (!result) {
    return (
      <GlassCard
        title="Scenario Results"
        subtitle="Run a scenario to see how the engine behaves"
        glow="none"
        bodyClassName="flex min-h-[320px] items-center justify-center p-6"
      >
        <p className="max-w-sm text-center text-xs leading-relaxed text-slate-500">
          {running
            ? "Simulating…"
            : "Set the conditions and throttle profile on the left, then run. The whole scenario is integrated headless through the same physics, digital twin and PHM stack the live dashboard uses — just without a wall clock."}
        </p>
      </GlassCard>
    );
  }

  const { summary } = result;
  const style = VERDICT_STYLE[summary.verdict];
  const healthColor = scoreColor(summary.min_health_score);

  return (
    <div className="space-y-4">
      {/* ---- verdict banner ---- */}
      <div
        className={clsx(
          "glass-panel flex flex-wrap items-center gap-4 border p-4",
          style.border,
          style.bg,
          style.glow
        )}
      >
        <div
          className={clsx(
            "font-display text-4xl font-bold tracking-[0.12em]",
            style.text,
            summary.verdict !== "PASS" && "animate-pulseGlow"
          )}
        >
          {summary.verdict}
        </div>
        <div className="min-w-[240px] flex-1">
          <p className="text-xs leading-relaxed text-slate-300">{summary.headline}</p>
          <p className="mt-1 text-[10px] text-slate-500">
            Verdict is taken from the worst point of the run, not the end state ·{" "}
            {result.frame_count} frames · {result.compute_seconds.toFixed(1)} s to compute ·{" "}
            {(result.simulated_seconds / 60).toFixed(0)} simulated minutes
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <StatusPill tone={style.tone}>
            Worst reliability: {summary.worst_recommendation}
          </StatusPill>
          <span className="text-[10px] text-slate-500">
            Ends {summary.final_recommendation}
          </span>
        </div>
        {/* Phase 5: recovery reliability gets its own pill, deliberately not folded into
            the verdict above — it answers "could it have gotten home," not "did it
            finish the plan," and the two can legitimately disagree throughout the run. */}
        <div className="flex flex-col items-end gap-1">
          <StatusPill tone={RECOVERY_TONE[summary.worst_recovery_recommendation]}>
            Worst recovery: {summary.worst_recovery_recommendation}
          </StatusPill>
          <span className="text-[10px] text-slate-500">
            Ends {summary.final_recovery_recommendation}
          </span>
        </div>
      </div>

      {/* ---- headline numbers ---- */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[220px_1fr]">
        <GlassCard
          title="Minimum Health"
          glow="none"
          bodyClassName="flex flex-col items-center gap-3 p-4"
        >
          <RadialGauge value={summary.min_health_score} size={150} strokeWidth={11} color={healthColor}>
            <span className="tabular text-3xl font-bold" style={{ color: healthColor }}>
              {Math.round(summary.min_health_score)}
            </span>
            <span className="mt-0.5 text-[9px] uppercase tracking-[0.18em] text-slate-500">
              Worst
            </span>
          </RadialGauge>
          <div className="w-full space-y-1 text-[11px]">
            <div className="flex justify-between">
              <span className="text-slate-500">Reached at</span>
              <span className="tabular text-slate-300">
                T+{summary.min_health_at_min.toFixed(1)} min
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Ends at</span>
              <span className="tabular text-slate-300">
                {Math.round(summary.final_health_score)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Worst subsystem</span>
              <span className="text-slate-300 capitalize">{summary.worst_subsystem}</span>
            </div>
          </div>
        </GlassCard>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
          <Stat
            label="Final RUL"
            value={formatRul(summary.final_rul_minutes)}
            sub={
              summary.min_rul_minutes !== null
                ? `min ${formatRul(summary.min_rul_minutes)}`
                : "no degradation trend"
            }
            tone={
              summary.final_rul_minutes !== null && summary.final_rul_minutes < 60
                ? "red"
                : "normal"
            }
          />
          <Stat
            label="Peak CHT"
            value={summary.peak_cht_c}
            unit="°C"
            tone={summary.peak_cht_c > 210 ? "amber" : "normal"}
          />
          <Stat
            label="Peak EGT"
            value={summary.peak_egt_c}
            unit="°C"
            tone={summary.peak_egt_c > 800 ? "amber" : "normal"}
          />
          <Stat
            label="Min Oil Press."
            value={summary.min_oil_pressure_kpa}
            unit="kPa"
            tone={summary.min_oil_pressure_kpa < 250 ? "amber" : "normal"}
          />
          <Stat label="Peak Oil Temp" value={summary.peak_oil_temp_c} unit="°C" />
          <Stat label="Mean Power" value={summary.mean_power_kw} unit="kW" />
          <Stat
            label="Mean BSFC"
            value={summary.mean_bsfc_g_per_kwh ?? "—"}
            unit={summary.mean_bsfc_g_per_kwh ? "g/kWh" : undefined}
          />
          <Stat
            label="Fuel Burned"
            value={summary.total_fuel_litres}
            unit="L"
            sub={`${summary.fuel_burn_lph_mean} L/h mean`}
          />
        </div>
      </div>

      {/* ---- excursions ---- */}
      {(summary.limit_excursions.length > 0 || summary.caution_excursions.length > 0) && (
        <GlassCard
          title="Operating Limits"
          subtitle="Time spent outside the normal envelope"
          glow={summary.limit_excursions.length > 0 ? "red" : "amber"}
          bodyClassName="space-y-1.5 p-4"
        >
          {summary.limit_excursions.map((e) => (
            <ExcursionRow key={`${e.parameter}-${e.band}-${e.direction}`} excursion={e} />
          ))}
          {summary.caution_excursions.map((e) => (
            <ExcursionRow key={`${e.parameter}-${e.band}-${e.direction}`} excursion={e} />
          ))}
        </GlassCard>
      )}

      {/* ---- time series ---- */}
      <GlassCard
        title="Temperatures & Speed"
        subtitle="Full scenario · static, not streaming"
        glow="cyan"
      >
        <MultiLineChart
          data={charts.temps}
          series={TEMP_SERIES}
          height={240}
          xKey="t"
          xTickFormatter={minuteTick}
        />
      </GlassCard>

      <GlassCard
        title="Health, Reliability & Lubrication"
        subtitle="Reliability is scaled ×100 to share the health axis"
        glow="cyan"
      >
        <MultiLineChart
          data={charts.health}
          series={HEALTH_SERIES}
          height={220}
          xKey="t"
          xTickFormatter={minuteTick}
        />
      </GlassCard>

      {/* ---- advisories ---- */}
      {summary.final_advisories.length > 0 && (
        <GlassCard
          title="Advisories At Scenario End"
          subtitle="What a ground crew would be told after this flight"
          glow="amber"
          bodyClassName="space-y-2 p-4"
        >
          {summary.final_advisories.map((a) => (
            <div
              key={`${a.subsystem}-${a.urgency}`}
              className="rounded-md border border-base-border/70 bg-base-panel2/30 p-2.5"
            >
              <div className="mb-1 flex items-center gap-2">
                <StatusPill
                  tone={
                    a.urgency === "immediate"
                      ? "nogo"
                      : a.urgency === "schedule_soon"
                      ? "caution"
                      : "cyan"
                  }
                >
                  {a.urgency.replace("_", " ")}
                </StatusPill>
                <span className="text-[11px] capitalize text-slate-400">{a.subsystem}</span>
              </div>
              <p className="text-[11px] leading-relaxed text-slate-300">{a.recommendation}</p>
              <p className="mt-1 text-[10px] text-slate-600">{a.basis.join(" · ")}</p>
            </div>
          ))}
        </GlassCard>
      )}

      <ul className="space-y-0.5 px-1 text-[10px] text-slate-600">
        {result.notes.map((n) => (
          <li key={n}>· {n}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Turns the returned frames into chart rows.
 *
 * The x-axis is minutes from the start of the scenario. A scenario frame's `timestamp` is
 * already seconds-since-start rather than a wall-clock epoch, precisely so this does not
 * need a reference point — and so nothing can mistake these frames for a recording.
 */
function buildCharts(result: ScenarioResult | null): {
  temps: Record<string, number>[];
  health: Record<string, number>[];
} {
  if (!result || result.frames.length === 0) return { temps: [], health: [] };

  // Recharts renders every point; ~600 is past the width of any screen this runs on, and
  // a 1800-frame series makes panning noticeably heavy for no visible detail.
  const MAX_POINTS = 600;
  const stride = Math.max(1, Math.ceil(result.frames.length / MAX_POINTS));
  const frames = result.frames.filter((_, i) => i % stride === 0);

  const temps = frames.map((f) => ({
    t: Math.round((f.timestamp / 60) * 100) / 100,
    egt: Math.round(Math.max(...f.cylinders.map((c) => c.egt_c)) * 10) / 10,
    cht: f.cht_c,
    oil_temp: f.oil_temp_c,
    rpm: f.rpm,
  }));

  const health = frames.map((f) => ({
    t: Math.round((f.timestamp / 60) * 100) / 100,
    health: f.health.overall_score,
    reliability: Math.round(f.mission_reliability.score * 1000) / 10,
    // Optional on TelemetryFrame for backward compatibility with frames from before
    // this field existed (see lib/types.ts); a scenario always populates it, but a
    // saved run replayed from an older schema version should not crash the chart.
    recovery: Math.round((f.recovery_reliability?.score ?? 0) * 1000) / 10,
    oil_p: f.oil_pressure_kpa,
  }));

  return { temps, health };
}
