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
import { EarlyWarningBanner } from "@/components/dashboard/EarlyWarningBanner";
import { EfficiencyTrendChart } from "@/components/dashboard/EfficiencyTrendChart";
import { MaintenanceAdvisoryPanel } from "@/components/dashboard/MaintenanceAdvisoryPanel";
import { SensorFusionPanel } from "@/components/dashboard/SensorFusionPanel";
import { MissionReportView } from "@/components/dashboard/MissionReportView";
import { ControlDeck } from "@/components/dashboard/ControlDeck";
import { PerformanceMapViewer } from "@/components/dashboard/PerformanceMapViewer";
// Phase 4: the only change to the live dashboard — a link across to the Test Bench.
import { ModeNav } from "@/components/nav/ModeNav";
// Phase 5: engine life-cycle — cumulative wear and hours across every mission this
// engine has flown, not just the one currently on screen. Lives on this page rather
// than a route of its own: it reads the same live-updating ledger every other panel
// here answers from (the running SimulationLoop and its database), so splitting it off
// behind a separate nav tab bought nothing but an extra click and a colder cache.
import { LifecycleOverviewPanel } from "@/components/lifecycle/LifecycleOverviewPanel";
import { HealthTrendAcrossMissions } from "@/components/lifecycle/HealthTrendAcrossMissions";
import { FaultEventHistoryTable } from "@/components/lifecycle/FaultEventHistoryTable";
import { LogMaintenanceActionForm } from "@/components/lifecycle/LogMaintenanceActionForm";
import { EMBLEM_PATH } from "@/lib/branding";

const EngineCutaway3D = dynamic(
  () =>
    import("@/components/dashboard/engine3d/EngineCutaway3D").then(
      (m) => m.EngineCutaway3D
    ),
  {
    ssr: false,
    loading: () => (
      <div className="glass-panel relative flex h-[340px] w-full items-center justify-center overflow-hidden text-xs text-slate-500">
        <img
          src={EMBLEM_PATH}
          alt=""
          aria-hidden="true"
          className="pointer-events-none absolute h-40 w-40 opacity-[0.06]"
        />
        <span className="relative">Loading engine model…</span>
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
        <ModeNav className="w-fit" />

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

        {/* Early warning: a NEW, earlier tier sitting above the fault feed it precedes —
            softer amber "watch" styling, distinct from FaultAlertFeed's red/amber
            confirmed-alert treatment, so the two tiers can never be visually confused.
            Renders nothing (returns null) once there is no active early warning. */}
        <EarlyWarningBanner />

        {/* Phase 3: advisories sit beside the raw fault feed — one says what happened,
            the other says what to do about it. */}
        <section className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <FaultAlertFeed />
          <MaintenanceAdvisoryPanel />
        </section>

        {/* Phase 5: the one new panel this feature adds — see docs/architecture.md's
            Sensor Fusion section for why CHT, RPM and oil pressure are each fused the
            way they are. */}
        <SensorFusionPanel />

        {/* Phase 4: the steady-state dyno map, with the live engine and (once the
            Test Bench optimizer has been run at least once) its recommended operating
            point both plotted on top of it. */}
        <PerformanceMapViewer />

        {/* Phase 5: engine life-cycle — cumulative wear and hours across every mission
            this engine has ever flown, distinct from everything above it on this page,
            which is scoped to the mission on screen right now. */}
        <LifecycleOverviewPanel />
        <section className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <HealthTrendAcrossMissions />
          <FaultEventHistoryTable />
        </section>
        <div className="xl:max-w-xl">
          <LogMaintenanceActionForm />
        </div>
      </main>

      <ControlDeck />
      <MissionReportView />
    </div>
  );
}
