"use client";

/**
 * The three named mission presets, each resolved by the operating-point optimizer at
 * standard reference conditions, with an "Apply to Live Engine" button.
 *
 * Applying a preset is the one thing on this page that reaches out of the Test Bench and
 * touches the running engine, so it is gated on a mission actually being recorded. The
 * button is disabled with an explanation rather than hidden — an operator who cannot find
 * the control assumes it is broken, whereas one who can see why it is unavailable knows
 * what to do about it.
 */
import { useEffect } from "react";
import clsx from "clsx";
import { GlassCard } from "@/components/ui/GlassCard";
import { StatusPill } from "@/components/ui/StatusPill";
import { useTestBenchStore } from "@/lib/testbench/store";
import type { PresetCard } from "@/lib/testbench/types";

const MISSION_REQUIRED_HINT =
  "Start a mission from the Live Dashboard's Control Deck before applying a preset — this commands the running engine.";

function Delta({ label, value, suffix = "%" }: { label: string; value: number; suffix?: string }) {
  const positive = value > 0.05;
  const negative = value < -0.05;
  return (
    <div className="flex items-baseline justify-between text-[11px]">
      <span className="text-slate-500">{label}</span>
      <span
        className={clsx(
          "tabular font-medium",
          positive ? "text-status-go" : negative ? "text-status-red" : "text-slate-300"
        )}
      >
        {value > 0 ? "+" : ""}
        {value.toFixed(1)}
        {suffix}
      </span>
    </div>
  );
}

function Card({
  card,
  missionActive,
  applying,
  applied,
  onApply,
}: {
  card: PresetCard;
  missionActive: boolean;
  applying: boolean;
  applied: boolean;
  onApply: () => void;
}) {
  const disabled = !missionActive || applying;
  return (
    <div
      className={clsx(
        "glass-panel flex flex-col gap-3 p-4",
        card.feasible ? "shadow-glow" : "border-status-red/40 shadow-glow-red"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="font-display text-sm font-bold uppercase tracking-[0.14em] text-status-cyan">
            {card.label}
          </h3>
          <p className="mt-0.5 text-[10px] text-slate-500">
            at {card.reference_conditions.altitude_m} m ·{" "}
            {card.reference_conditions.ambient_temperature_c} °C
          </p>
        </div>
        {!card.feasible && <StatusPill tone="nogo">Out of limits</StatusPill>}
      </div>

      <p className="text-[11px] leading-relaxed text-slate-400">{card.description}</p>

      <div className="rounded-md border border-base-border/70 bg-base-panel2/40 px-2.5 py-2">
        <div className="flex items-baseline justify-between">
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Setpoint</span>
          <span className="tabular text-sm font-semibold text-slate-100">
            {card.setpoint.throttle_pct.toFixed(0)}%
          </span>
        </div>
        <div className="mt-1 flex gap-3 text-[10px] text-slate-500">
          <span className="tabular">
            AFR {card.setpoint.afr_trim >= 0 ? "+" : ""}
            {card.setpoint.afr_trim.toFixed(2)}
          </span>
          <span className="tabular">
            Timing {card.setpoint.injection_timing_trim_deg >= 0 ? "+" : ""}
            {card.setpoint.injection_timing_trim_deg.toFixed(1)}°
          </span>
        </div>
      </div>

      <div className="space-y-1">
        <Delta label="Power" value={card.deltas.power_pct} />
        <Delta label="Specific range" value={card.deltas.range_equivalent_pct} />
        <Delta label="Projected life" value={card.deltas.estimated_rul_impact_pct} />
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 border-t border-base-border/70 pt-2 text-[10px]">
        <span className="text-slate-500">
          Power <span className="tabular text-slate-300">{card.predicted.power_kw.toFixed(1)} kW</span>
        </span>
        <span className="text-slate-500">
          BSFC{" "}
          <span className="tabular text-slate-300">
            {card.predicted.bsfc_g_per_kwh?.toFixed(0) ?? "—"} g/kWh
          </span>
        </span>
        <span className="text-slate-500">
          CHT <span className="tabular text-slate-300">{card.predicted.cht_c.toFixed(0)} °C</span>
        </span>
        <span className="text-slate-500">
          EGT <span className="tabular text-slate-300">{card.predicted.egt_max_c.toFixed(0)} °C</span>
        </span>
      </div>

      <p className="text-[10px] italic leading-relaxed text-slate-600">{card.trade_off}</p>

      <button
        type="button"
        onClick={onApply}
        disabled={disabled}
        title={missionActive ? `Command the live engine to ${card.label}` : MISSION_REQUIRED_HINT}
        aria-describedby={missionActive ? undefined : "preset-disabled-hint"}
        className={clsx(
          "mt-auto rounded-md border px-3 py-2 font-mono text-[11px] uppercase tracking-wider transition-colors",
          disabled
            ? "cursor-not-allowed border-base-border bg-base-panel2/40 text-slate-600"
            : applied
            ? "border-status-go/50 bg-status-go/15 text-status-go"
            : "border-status-cyan/50 bg-status-cyan/10 text-status-cyan hover:bg-status-cyan/20"
        )}
      >
        {applying ? "Applying…" : applied ? "✓ Applied to Live Engine" : "Apply to Live Engine"}
      </button>
    </div>
  );
}

export function MissionPresetCards() {
  const presets = useTestBenchStore((s) => s.presets);
  const loading = useTestBenchStore((s) => s.presetsLoading);
  const error = useTestBenchStore((s) => s.presetsError);
  const applying = useTestBenchStore((s) => s.applyingPreset);
  const applied = useTestBenchStore((s) => s.appliedPreset);
  const missionActive = useTestBenchStore((s) => s.missionActive);
  const loadPresets = useTestBenchStore((s) => s.loadPresets);
  const applyPresetToLive = useTestBenchStore((s) => s.applyPresetToLive);
  const refreshMissionStatus = useTestBenchStore((s) => s.refreshMissionStatus);

  useEffect(() => {
    loadPresets();
    refreshMissionStatus();
    // A mission can be started or ended from the Live Dashboard while this page is open,
    // so the button's availability is polled rather than read once on mount.
    const id = setInterval(refreshMissionStatus, 5000);
    return () => clearInterval(id);
  }, [loadPresets, refreshMissionStatus]);

  return (
    <GlassCard
      title="Mission Presets"
      subtitle="Optimizer-resolved setpoints, cached at reference conditions"
      glow="cyan"
      headerRight={
        missionActive ? (
          <StatusPill tone="go" pulse>
            Mission recording
          </StatusPill>
        ) : (
          <StatusPill tone="idle">No live mission</StatusPill>
        )
      }
      bodyClassName="space-y-3 p-4"
    >
      {!missionActive && (
        <p
          id="preset-disabled-hint"
          className="rounded-md border border-base-border/70 bg-base-panel2/40 px-3 py-2 text-[11px] text-slate-500"
        >
          {MISSION_REQUIRED_HINT}
        </p>
      )}

      {error && (
        <div role="alert" className="rounded-lg border border-status-red/40 bg-status-red/10 p-3 text-[11px]">
          <p className="font-medium text-status-red">{error.message}</p>
          {error.reasons.map((r) => (
            <p key={r} className="mt-1 text-slate-300">
              {r}
            </p>
          ))}
        </div>
      )}

      {loading && presets.length === 0 && (
        <p className="py-8 text-center text-xs text-slate-500">
          Resolving presets — each one is a full optimizer search at reference conditions…
        </p>
      )}

      {presets.length > 0 && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {presets.map((card) => (
            <Card
              key={card.name}
              card={card}
              missionActive={missionActive}
              applying={applying === card.name}
              applied={applied === card.name}
              onApply={() => applyPresetToLive(card.name)}
            />
          ))}
        </div>
      )}
    </GlassCard>
  );
}
