"use client";

/**
 * Sensor fusion — the raw readings behind three fused channels (CHT, RPM, oil
 * pressure), the fused best estimate, and which specific instrument disagrees with it.
 *
 * The point of exposing the *raw* readings here, not just the fused number, is that a
 * fused value on its own hides exactly the thing this panel exists to show: two sensors
 * agreeing, or one of them quietly lying while fusion compensates for it. `TelemetryFrame`
 * does not carry each raw reading directly — only the fused value and each source's
 * innovation against it (`raw - fused`) — so the raw readings are recovered here as
 * `fused + innovation`, which is exact by construction, not an approximation.
 *
 * "Suspect" flagging is a plain magnitude comparison against a fixed threshold per
 * channel, computed client-side from the same innovations the backend's own classifier
 * uses (see backend/app/ml/fault_classifier.py `_classify_cht` / `_classify_rpm` /
 * `_classify_oil_pressure`) — this panel does not call the classifier, it just applies
 * the same "one side moved, the other didn't" read a viewer can verify with their own
 * eyes against the numbers on screen.
 */
import clsx from "clsx";
import { GlassCard } from "@/components/ui/GlassCard";
import { useTelemetryStore } from "@/lib/store";

/** Same order-of-magnitude gate the backend's disambiguation logic uses (CHANNEL_MOVED_Z
 * is a z-score there; these are the equivalent raw-unit thresholds for a quick client-
 * side read without needing the classifier's own EWMA state). */
const CHT_SUSPECT_C = 3.0;
const RPM_TACH_SUSPECT_RPM = 15.0;
const RPM_VIB_SUSPECT_RPM = 40.0;
const OIL_SUSPECT_KPA = 12.0;
/** Gain above this reads as "trusting the sensor" rather than "trusting the model". */
const OIL_GAIN_SENSOR_TRUST = 0.75;

type Verdict = "ok" | "suspect" | "unknown";

function verdictFor(absInnovation: number, threshold: number): Verdict {
  return absInnovation >= threshold ? "suspect" : "ok";
}

const VERDICT_STYLE: Record<Verdict, { text: string; dot: string; label: string }> = {
  ok: { text: "text-status-go", dot: "bg-status-go", label: "tracking" },
  suspect: { text: "text-status-red", dot: "bg-status-red", label: "suspect" },
  unknown: { text: "text-slate-400", dot: "bg-status-idle", label: "—" },
};

function SourceRow({
  label,
  value,
  unit,
  innovation,
  verdict,
  decimals = 1,
}: {
  label: string;
  value: number;
  unit: string;
  innovation: number;
  verdict: Verdict;
  decimals?: number;
}) {
  const style = VERDICT_STYLE[verdict];
  return (
    <div
      className={clsx(
        "flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs",
        verdict === "suspect"
          ? "border-status-red/40 bg-status-red/10"
          : "border-base-border/70 bg-base-panel2/30"
      )}
    >
      <span aria-hidden="true" className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", style.dot)} />
      <span className="w-28 shrink-0 text-slate-400">{label}</span>
      <span className="tabular flex-1 text-right font-medium text-slate-100">
        {value.toFixed(decimals)} {unit}
      </span>
      <span className={clsx("tabular w-20 shrink-0 text-right text-[10px]", style.text)}>
        {innovation >= 0 ? "+" : ""}
        {innovation.toFixed(decimals)} {style.label !== "—" && `· ${style.label}`}
      </span>
    </div>
  );
}

function FusedRow({ value, unit, decimals = 1 }: { value: number; unit: string; decimals?: number }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-status-cyan/30 bg-status-cyan/5 px-2.5 py-2">
      <span className="text-[10px] uppercase tracking-wider text-status-cyan">
        Fused estimate
      </span>
      <span className="tabular text-lg font-semibold text-status-cyan">
        {value.toFixed(decimals)} <span className="text-xs font-normal">{unit}</span>
      </span>
    </div>
  );
}

export function SensorFusionPanel() {
  const latest = useTelemetryStore((s) => s.latest);

  if (!latest || latest.fused_cht_c == null) {
    return (
      <GlassCard
        title="Sensor Fusion"
        subtitle="Kalman-fused CHT, RPM and oil pressure"
        glow="cyan"
        bodyClassName="p-4"
      >
        <p className="py-6 text-center text-xs text-slate-400">Waiting for telemetry…</p>
      </GlassCard>
    );
  }

  const chtInnovations = latest.cht_sensor_innovations;
  const rpmInnovations = latest.rpm_sensor_innovations;

  const chtPrimary = chtInnovations ? latest.fused_cht_c + chtInnovations.primary : null;
  const chtSecondary = chtInnovations ? latest.fused_cht_c + chtInnovations.secondary : null;
  const chtPrimaryVerdict = chtInnovations
    ? verdictFor(Math.abs(chtInnovations.primary), CHT_SUSPECT_C)
    : "unknown";
  const chtSecondaryVerdict = chtInnovations
    ? verdictFor(Math.abs(chtInnovations.secondary), CHT_SUSPECT_C)
    : "unknown";

  const tachRpm =
    rpmInnovations && latest.fused_rpm != null
      ? latest.fused_rpm + rpmInnovations.tachometer
      : null;
  const vibRpm =
    rpmInnovations && latest.fused_rpm != null
      ? latest.fused_rpm + rpmInnovations.vibration_derived
      : null;
  const tachVerdict = rpmInnovations
    ? verdictFor(Math.abs(rpmInnovations.tachometer), RPM_TACH_SUSPECT_RPM)
    : "unknown";
  const vibVerdict = rpmInnovations
    ? verdictFor(Math.abs(rpmInnovations.vibration_derived), RPM_VIB_SUSPECT_RPM)
    : "unknown";

  const oilInnovation = latest.oil_pressure_innovation ?? 0;
  const oilSensorReading =
    latest.fused_oil_pressure_kpa != null ? latest.fused_oil_pressure_kpa + oilInnovation : null;
  const oilVerdict = verdictFor(Math.abs(oilInnovation), OIL_SUSPECT_KPA);
  const gain = latest.oil_pressure_kalman_gain ?? 0;
  const trustingSensor = gain >= OIL_GAIN_SENSOR_TRUST;

  return (
    <GlassCard
      title="Sensor Fusion"
      subtitle="Kalman-fused CHT, RPM and oil pressure — raw sources vs. the fused best estimate"
      glow="cyan"
      bodyClassName="grid grid-cols-1 gap-4 p-4 lg:grid-cols-3"
    >
      {/* ---- CHT ---- */}
      <div className="space-y-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Cylinder Head Temp
        </h3>
        {chtPrimary !== null && chtInnovations && (
          <SourceRow
            label="Probe #1 (primary)"
            value={chtPrimary}
            unit="°C"
            innovation={chtInnovations.primary}
            verdict={chtPrimaryVerdict}
          />
        )}
        {chtSecondary !== null && chtInnovations && (
          <SourceRow
            label="Probe #2 (secondary)"
            value={chtSecondary}
            unit="°C"
            innovation={chtInnovations.secondary}
            verdict={chtSecondaryVerdict}
          />
        )}
        <FusedRow value={latest.fused_cht_c} unit="°C" />
      </div>

      {/* ---- RPM ---- */}
      <div className="space-y-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Crankshaft Speed
        </h3>
        {tachRpm !== null && rpmInnovations && (
          <SourceRow
            label="Tachometer"
            value={tachRpm}
            unit="rpm"
            innovation={rpmInnovations.tachometer}
            verdict={tachVerdict}
            decimals={0}
          />
        )}
        {vibRpm !== null && rpmInnovations && (
          <SourceRow
            label="Vibration-derived"
            value={vibRpm}
            unit="rpm"
            innovation={rpmInnovations.vibration_derived}
            verdict={vibVerdict}
            decimals={0}
          />
        )}
        {latest.fused_rpm != null && <FusedRow value={latest.fused_rpm} unit="rpm" decimals={0} />}
        {tachVerdict === "ok" && vibVerdict === "suspect" && (
          <p className="text-[10px] leading-relaxed text-slate-400">
            Vibration-derived RPM disagrees with the tachometer — check whether cylinder
            vibration RMS is independently elevated before blaming the sensor.
          </p>
        )}
      </div>

      {/* ---- Oil pressure ---- */}
      <div className="space-y-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Oil Pressure
        </h3>
        {oilSensorReading !== null && (
          <SourceRow
            label="Sensor"
            value={oilSensorReading}
            unit="kPa"
            innovation={oilInnovation}
            verdict={oilVerdict}
          />
        )}
        {latest.fused_oil_pressure_kpa != null && (
          <FusedRow value={latest.fused_oil_pressure_kpa} unit="kPa" />
        )}
        <div className="rounded-md border border-base-border/70 bg-base-panel2/30 px-2.5 py-2">
          <div className="mb-1 flex items-center justify-between text-[10px] text-slate-400">
            <span>MODEL PREDICTION</span>
            <span>SENSOR</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-base-border">
            <div
              className={clsx(
                "h-full rounded-full transition-all duration-500",
                trustingSensor ? "bg-status-amber" : "bg-status-cyan"
              )}
              style={{ width: `${Math.max(2, gain * 100)}%` }}
            />
          </div>
          <p className="mt-1.5 text-[10px] leading-relaxed text-slate-400">
            Kalman gain {gain.toFixed(2)}
            {trustingSensor
              ? " — trusting the sensor: the zero-wear model's own prediction confidence has degraded."
              : " — trusting the model: no lubrication fault suspected, so the healthy-model prediction leads."}
          </p>
        </div>
      </div>
    </GlassCard>
  );
}
