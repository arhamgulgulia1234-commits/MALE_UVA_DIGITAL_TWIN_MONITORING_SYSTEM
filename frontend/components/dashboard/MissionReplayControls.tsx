"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { useTelemetryStore } from "@/lib/store";

const REPLAY_SPEEDS = [1, 5, 20];

/**
 * Mission recording and replay. Recording is an explicit session — telemetry always
 * streams live, but it is only persisted between start and end, so ad-hoc fiddling does
 * not fill the database with junk missions.
 */
export function MissionReplayControls() {
  const missions = useTelemetryStore((s) => s.missions);
  const isRecording = useTelemetryStore((s) => s.isRecording);
  const activeMissionId = useTelemetryStore((s) => s.activeMissionId);
  const latest = useTelemetryStore((s) => s.latest);

  const startMission = useTelemetryStore((s) => s.startMission);
  const endMission = useTelemetryStore((s) => s.endMission);
  const refreshMissions = useTelemetryStore((s) => s.refreshMissions);
  const startReplay = useTelemetryStore((s) => s.startReplay);
  const stopReplay = useTelemetryStore((s) => s.stopReplay);
  const loadMissionReport = useTelemetryStore((s) => s.loadMissionReport);

  const [selected, setSelected] = useState<number | "">("");
  const [speed, setSpeed] = useState(5);

  const isReplaying = latest?.is_replay ?? false;

  useEffect(() => {
    refreshMissions();
  }, [refreshMissions]);

  return (
    <div className="glass-panel p-3">
      <span className="mb-2 block text-[10px] uppercase tracking-wider text-slate-400">
        Mission Record / Replay
      </span>

      {/* Recording */}
      <div className="mb-2 flex gap-2">
        <button
          onClick={() => (isRecording ? endMission() : startMission())}
          className={clsx(
            "flex-1 rounded-md border px-2 py-1.5 font-mono text-xs font-medium transition-colors",
            isRecording
              ? "border-status-red/60 bg-status-red/15 text-status-red"
              : "border-base-border text-slate-400 hover:border-status-cyan/30"
          )}
        >
          {isRecording ? `■ Stop rec (#${activeMissionId})` : "● Record mission"}
        </button>
      </div>

      {/* Replay */}
      <div className="flex flex-col gap-2">
        <select
          value={selected}
          onChange={(e) =>
            setSelected(e.target.value === "" ? "" : Number(e.target.value))
          }
          className="w-full rounded-md border border-base-border bg-base-panel2 px-2 py-1.5 font-mono text-[11px] text-slate-300 outline-none focus:border-status-cyan/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-status-cyan"
        >
          <option value="">Select recorded mission…</option>
          {missions.map((m) => (
            <option key={m.id} value={m.id}>
              #{m.id} · {m.mission_profile_name} · {m.frame_count} frames
            </option>
          ))}
        </select>

        <div className="flex gap-2">
          {REPLAY_SPEEDS.map((f) => (
            <button
              key={f}
              onClick={() => setSpeed(f)}
              className={clsx(
                "flex-1 rounded-md border px-2 py-1 font-mono text-[11px] transition-colors",
                speed === f
                  ? "border-status-cyan/60 bg-status-cyan/15 text-status-cyan"
                  : "border-base-border text-slate-400 hover:border-status-cyan/30"
              )}
            >
              {f}x
            </button>
          ))}
        </div>

        <div className="flex gap-2">
          <button
            disabled={selected === ""}
            onClick={() => selected !== "" && startReplay(selected, speed)}
            className={clsx(
              "flex-1 rounded-md border px-2 py-1.5 font-mono text-[11px] font-medium transition-colors",
              selected === ""
                ? "cursor-not-allowed border-base-border text-slate-400"
                : "border-status-cyan/50 bg-status-cyan/10 text-status-cyan hover:bg-status-cyan/20"
            )}
          >
            ▶ Replay
          </button>
          <button
            onClick={() => stopReplay()}
            disabled={!isReplaying}
            className={clsx(
              "flex-1 rounded-md border px-2 py-1.5 font-mono text-[11px] font-medium transition-colors",
              isReplaying
                ? "border-status-amber/50 bg-status-amber/10 text-status-amber"
                : "cursor-not-allowed border-base-border text-slate-400"
            )}
          >
            ■ Live
          </button>
        </div>

        <button
          disabled={selected === ""}
          onClick={() => selected !== "" && loadMissionReport(selected)}
          className={clsx(
            "rounded-md border px-2 py-1.5 font-mono text-[11px] transition-colors",
            selected === ""
              ? "cursor-not-allowed border-base-border text-slate-400"
              : "border-base-border text-slate-400 hover:border-status-cyan/30"
          )}
        >
          ⨳ View mission report
        </button>
      </div>
    </div>
  );
}
