"use client";

import clsx from "clsx";
import { motion, AnimatePresence } from "framer-motion";
import { useTelemetryStore } from "@/lib/store";

/**
 * Post-mission debrief modal.
 *
 * Functional and readable rather than polished — a dedicated visual pass follows this
 * phase. The `debrief` paragraph is the part meant to be read aloud; everything else is
 * for the maintenance log.
 */
export function MissionReportView() {
  const report = useTelemetryStore((s) => s.missionReport);
  const clearMissionReport = useTelemetryStore((s) => s.clearMissionReport);

  const r = report as Record<string, any> | null;

  return (
    <AnimatePresence>
      {r && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          onClick={clearMissionReport}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97 }}
            transition={{ duration: 0.2 }}
            onClick={(e) => e.stopPropagation()}
            className="glass-panel shadow-glow max-h-[85vh] w-full max-w-3xl overflow-y-auto"
          >
            <div className="flex items-center justify-between border-b border-base-border px-5 py-3">
              <div>
                <h2 className="panel-title">Mission Report</h2>
                <p className="mt-0.5 text-[11px] text-slate-400">
                  Mission #{r.mission_id} · {r.profile_name} · {r.duration}
                </p>
              </div>
              <button
                onClick={clearMissionReport}
                className="rounded-md border border-base-border px-2.5 py-1 font-mono text-[11px] text-slate-400 hover:border-status-cyan/40"
              >
                close
              </button>
            </div>

            <div className="space-y-5 p-5">
              {r.error && (
                <p className="rounded-lg border border-status-amber/40 bg-status-amber/10 px-3 py-2 text-xs text-status-amber">
                  {r.error}
                </p>
              )}

              {r.debrief && (
                <section>
                  <h3 className="panel-title mb-2">Debrief</h3>
                  <p className="text-[13px] leading-relaxed text-slate-300">
                    {r.debrief}
                  </p>
                </section>
              )}

              {r.health && (
                <section>
                  <h3 className="panel-title mb-2">Health</h3>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <Stat label="Start" value={fmt(r.health.start)} />
                    <Stat label="End" value={fmt(r.health.end)} />
                    <Stat label="Minimum" value={fmt(r.health.minimum)} />
                    <Stat label="Trend" value={r.health.trend ?? "—"} />
                  </div>
                  {r.health.final_subsystem_scores && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {Object.entries(
                        r.health.final_subsystem_scores as Record<string, number>
                      )
                        .filter(([, v]) => v != null)
                        .map(([k, v]) => (
                          <span
                            key={k}
                            className={clsx(
                              "rounded border px-2 py-0.5 font-mono text-[10px]",
                              v < 40
                                ? "border-status-red/40 text-status-red"
                                : v < 70
                                  ? "border-status-amber/40 text-status-amber"
                                  : "border-base-border text-slate-400"
                            )}
                          >
                            {k} {Math.round(v)}
                          </span>
                        ))}
                    </div>
                  )}
                </section>
              )}

              <section>
                <h3 className="panel-title mb-2">Mission</h3>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Stat label="Frames" value={String(r.frame_count ?? "—")} />
                  <Stat
                    label="Final RUL"
                    value={
                      r.final_rul_minutes != null
                        ? `${Math.round(r.final_rul_minutes)} min`
                        : "—"
                    }
                  />
                  <Stat label="Ended" value={r.final_recommendation ?? "—"} />
                  <Stat
                    label="Not-GO"
                    value={
                      r.fraction_of_mission_not_go != null
                        ? `${Math.round(r.fraction_of_mission_not_go * 100)}%`
                        : "—"
                    }
                  />
                </div>
                {r.phases_flown?.length > 0 && (
                  <p className="mt-2 font-mono text-[11px] text-slate-400">
                    phases: {r.phases_flown.join(" → ")}
                  </p>
                )}
              </section>

              {r.efficiency && (
                <section>
                  <h3 className="panel-title mb-2">Efficiency</h3>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                    <Stat
                      label="Avg BSFC"
                      value={
                        r.efficiency.average_bsfc_g_per_kwh
                          ? `${r.efficiency.average_bsfc_g_per_kwh} g/kWh`
                          : "—"
                      }
                    />
                    <Stat
                      label="Fuel used"
                      value={
                        r.efficiency.estimated_fuel_used_l
                          ? `${r.efficiency.estimated_fuel_used_l} L`
                          : "—"
                      }
                    />
                    <Stat label="Trend" value={r.efficiency.final_trend ?? "—"} />
                  </div>
                </section>
              )}

              <section>
                <h3 className="panel-title mb-2">
                  Fault Events ({r.fault_events?.length ?? 0})
                </h3>
                {!r.fault_events?.length ? (
                  <p className="text-xs text-slate-400">None recorded.</p>
                ) : (
                  <ul className="flex flex-col gap-1.5">
                    {r.fault_events.map((e: any, i: number) => (
                      <li
                        key={i}
                        className="flex items-center gap-3 rounded-lg border border-base-border bg-base-panel2/60 px-3 py-2 text-[11px]"
                      >
                        <span className="font-mono text-slate-200">{e.fault_type}</span>
                        {e.is_sensor_fault && (
                          <span className="rounded border border-status-cyan/40 px-1.5 py-0.5 font-mono text-[9px] text-status-cyan">
                            sensor
                          </span>
                        )}
                        <span className="ml-auto tabular text-slate-400">
                          T+{e.t_plus}
                        </span>
                        <span
                          className={clsx(
                            "tabular",
                            e.resolved ? "text-status-go" : "text-status-amber"
                          )}
                        >
                          {e.resolved ? "cleared" : "open"}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {r.final_advisories?.length > 0 && (
                <section>
                  <h3 className="panel-title mb-2">Outstanding Actions</h3>
                  <ul className="flex flex-col gap-1.5">
                    {r.final_advisories.map((a: any, i: number) => (
                      <li
                        key={i}
                        className="rounded-lg border border-base-border bg-base-panel2/60 px-3 py-2"
                      >
                        <div className="mb-1 flex items-center gap-2">
                          <span className="font-mono text-[10px] uppercase text-slate-300">
                            {a.subsystem}
                          </span>
                          <span className="font-mono text-[10px] uppercase text-status-amber">
                            {a.urgency}
                          </span>
                        </div>
                        <p className="text-[11px] leading-relaxed text-slate-400">
                          {a.recommendation}
                        </p>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function fmt(v: number | null | undefined): string {
  return v == null ? "—" : String(Math.round(v));
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-base-border bg-base-panel2/50 px-3 py-2">
      <div className="text-[9px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className="tabular mt-0.5 text-sm font-semibold text-slate-100">{value}</div>
    </div>
  );
}
