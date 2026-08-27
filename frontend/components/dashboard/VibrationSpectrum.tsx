"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { BarSpectrum } from "@/components/charts/BarSpectrum";
import { useTelemetryStore } from "@/lib/store";
import { HEALTHY_BANDS } from "@/lib/types";
import { bandStatus } from "@/lib/format";

// TODO(phase-2): this renders per-cylinder vibration RMS as a bar chart, which is what
// app/sim/simulation_loop.py currently produces. Once app/physics/vibration_model.py
// lands, swap this for a real rolling-FFT bin display (frequency bins on the x-axis)
// using the spectral content it will expose instead of a single RMS scalar per cylinder.

export function VibrationSpectrum() {
  const latest = useTelemetryStore((s) => s.latest);
  const cylinders = latest?.cylinders ?? [];

  const data = cylinders.map((c) => ({
    label: `Cyl ${c.id}`,
    value: c.vibration_rms,
    tone: bandStatus(c.vibration_rms, HEALTHY_BANDS.vibration_rms),
  }));

  return (
    <GlassCard title="Vibration Spectrum" subtitle="RMS by cylinder · rolling" glow="cyan">
      {data.length > 0 ? (
        <BarSpectrum data={data} maxValue={0.9} />
      ) : (
        <div className="flex h-[180px] items-center justify-center text-xs text-slate-500">
          Waiting for telemetry…
        </div>
      )}
    </GlassCard>
  );
}
