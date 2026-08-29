"use client";

import { GlassCard } from "@/components/ui/GlassCard";
import { BarSpectrum } from "@/components/charts/BarSpectrum";
import { useTelemetryStore } from "@/lib/store";
import { HEALTHY_BANDS } from "@/lib/types";
import { bandStatus } from "@/lib/format";

// This renders per-cylinder vibration *RMS* as bars, not a frequency spectrum, because
// RMS is the only vibration quantity the TelemetryFrame contract carries.
//
// app/physics/vibration_model.py does now synthesise a real 1 kHz waveform and derive
// spectral features from it (crest factor, high-band energy), and the PHM layer uses
// them as residual channels — but they stay server-side, so there is nothing on the wire
// to plot against a frequency axis. Turning this into a true FFT bin display means first
// widening the frame contract to carry the bins; that is a schema change, not a
// component change.

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
