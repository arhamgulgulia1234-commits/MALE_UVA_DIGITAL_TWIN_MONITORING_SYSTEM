"use client";

import dynamic from "next/dynamic";
import { useTelemetryStream } from "@/hooks/useTelemetryStream";
import { MissionHeader } from "@/components/dashboard/MissionHeader";
import { HealthScoreGauge } from "@/components/dashboard/HealthScoreGauge";
import { MissionReliabilityCard } from "@/components/dashboard/MissionReliabilityCard";
import { RULPanel } from "@/components/dashboard/RULPanel";
import { EngineVitalsGrid } from "@/components/dashboard/EngineVitalsGrid";
import { TelemetryStrip } from "@/components/dashboard/TelemetryStrip";
import { VibrationSpectrum } from "@/components/dashboard/VibrationSpectrum";
import { FaultAlertFeed } from "@/components/dashboard/FaultAlertFeed";
import { EfficiencyTrendChart } from "@/components/dashboard/EfficiencyTrendChart";
import { MaintenanceAdvisoryPanel } from "@/components/dashboard/MaintenanceAdvisoryPanel";
import { MissionReportView } from "@/components/dashboard/MissionReportView";
import { ControlDeck } from "@/components/dashboard/ControlDeck";

const EngineCutaway3D = dynamic(
  () =>
    import("@/components/dashboard/engine3d/EngineCutaway3D").then(
      (m) => m.EngineCutaway3D
    ),
  {
    ssr: false,
    loading: () => (
      <div className="glass-panel flex h-[340px] w-full items-center justify-center text-xs text-slate-500">
        Loading engine model…
      </div>
    ),
  }
);

export default function Home() {
  useTelemetryStream();

  return (
    <div className="flex min-h-screen flex-col">
      <MissionHeader />

      <main className="mx-auto w-full max-w-[1800px] flex-1 space-y-6 px-4 py-6 sm:px-6">
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1.4fr_0.9fr]">
          <HealthScoreGauge />
          <EngineCutaway3D />
          <div className="grid grid-cols-1 gap-4">
            <MissionReliabilityCard />
            <RULPanel />
          </div>
        </section>

        <EngineVitalsGrid />
        <TelemetryStrip />

        {/* Phase 3: efficiency + combustion stability, beside the vibration spectrum */}
        <section className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <EfficiencyTrendChart />
          <VibrationSpectrum />
        </section>

        {/* Phase 3: advisories sit beside the raw fault feed — one says what happened,
            the other says what to do about it. */}
        <section className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <FaultAlertFeed />
          <MaintenanceAdvisoryPanel />
        </section>
      </main>

      <ControlDeck />
      <MissionReportView />
    </div>
  );
}
