"use client";

import { Html } from "@react-three/drei";
import { useEffect, useState } from "react";
import { useTelemetryStore } from "@/lib/store";
import { LAYOUT, SENSOR_MOUNTS } from "./engine3dUtils";

/**
 * Callout labels in the dashed-box style of turbo-flow-diagram.png, plus sensor-fault
 * badges.
 *
 * Unlike everything else in this scene, labels are DOM (drei `<Html>`) and therefore do
 * re-render — but only when the label set actually changes, which is on a view-mode
 * toggle or when a sensor fault appears. The live numeric values inside them are
 * throttled to 4 Hz rather than updated per frame: they are text, and no one can read
 * text that changes 60 times a second.
 */

interface Callout {
  key: string;
  position: [number, number, number];
  title: string;
  body: string;
}

const CALLOUTS: Callout[] = [
  {
    key: "turbo",
    position: [LAYOUT.turbo.x - 0.15, LAYOUT.turbo.y + 0.62, 0],
    title: "TURBOCHARGER",
    body: "Exhaust-driven turbine on a shared shaft with the intake compressor.",
  },
  {
    key: "throttle",
    position: [LAYOUT.throttleBody.x - 0.1, LAYOUT.throttleBody.y - 0.5, 0],
    title: "THROTTLE BODY",
    body: "Regulates airflow into the intake plenum.",
  },
  {
    key: "intake",
    position: [0.35, LAYOUT.intakePlenumY + 0.5, 0],
    title: "INTAKE MANIFOLD",
    body: "Pressurised air from the compressor is distributed to the cylinders.",
  },
  {
    key: "cylinders",
    position: [0.6, 0.2, LAYOUT.cylinderOuter + 0.55],
    title: "CYLINDERS",
    body: "Air-cooled opposed four. Fin colour tracks that cylinder's EGT.",
  },
  {
    key: "exhaust",
    position: [0.55, LAYOUT.exhaustCollectorY - 0.42, -0.5],
    title: "EXHAUST MANIFOLD",
    body: "Collects from all four cylinders and drives the turbine.",
  },
  {
    key: "wastegate",
    position: [LAYOUT.wastegate.x - 0.35, LAYOUT.wastegate.y - 0.42, 0],
    title: "WASTE GATE",
    body: "Bypasses exhaust around the turbine to regulate boost.",
  },
];

/** Where each sensor-fault badge attaches, in world space. */
const SENSOR_BADGE_POSITION: Record<string, [number, number, number]> = {
  egt: [LAYOUT.cylinderStations[0], 0.55, LAYOUT.cylinderOuter + 0.1],
  oil: [-0.15, -0.72, -0.42],
  rpm: [LAYOUT.propHubX - 0.35, 0.42, 0],
  // Phase 5: two independent mount points for the two independent CHT probes — placed
  // on opposite cylinder stations, the way a real dual-probe installation would be.
  cht_primary: [LAYOUT.cylinderStations[0], 0.3, LAYOUT.cylinderOuter + 0.1],
  cht_secondary: [LAYOUT.cylinderStations[1], 0.3, LAYOUT.cylinderOuter + 0.1],
};

const SENSOR_BADGE_LABEL: Record<string, string> = {
  egt: "EGT probe",
  oil: "Oil pressure",
  rpm: "RPM pickup",
  cht_primary: "CHT probe #1",
  cht_secondary: "CHT probe #2",
};

/** Phase 5: the fused-channel innovation that justifies each badge, so it names what is
 * wrong with the probe rather than just that something is. `null` entries (EGT, and any
 * channel with no live innovation yet) fall back to the generic "suspect reading". */
function fusionBadgeDetail(mount: string, frame: ReturnType<typeof useTelemetryStore.getState>["latest"]): string | null {
  if (!frame) return null;
  switch (mount) {
    case "cht_primary":
      return frame.cht_sensor_innovations
        ? `disagrees with fused estimate by ${frame.cht_sensor_innovations.primary >= 0 ? "+" : ""}${frame.cht_sensor_innovations.primary.toFixed(1)}°C`
        : null;
    case "cht_secondary":
      return frame.cht_sensor_innovations
        ? `disagrees with fused estimate by ${frame.cht_sensor_innovations.secondary >= 0 ? "+" : ""}${frame.cht_sensor_innovations.secondary.toFixed(1)}°C`
        : null;
    case "rpm":
      return frame.rpm_sensor_innovations
        ? `tachometer off by ${frame.rpm_sensor_innovations.tachometer >= 0 ? "+" : ""}${frame.rpm_sensor_innovations.tachometer.toFixed(0)} rpm`
        : null;
    case "oil":
      return frame.oil_pressure_innovation != null
        ? `off model prediction by ${frame.oil_pressure_innovation >= 0 ? "+" : ""}${frame.oil_pressure_innovation.toFixed(1)} kPa`
        : null;
    default:
      return null;
  }
}

export function EngineLabelsOverlay({ showCallouts }: { showCallouts: boolean }) {
  const [sensorMounts, setSensorMounts] = useState<string[]>([]);
  const [sensorDetail, setSensorDetail] = useState<Record<string, string | null>>({});
  const [vitals, setVitals] = useState({ boost: 0, rpm: 0, egt: 0 });

  // Poll the store at 4 Hz. These are DOM nodes with text in them; re-rendering them at
  // frame rate would be both unreadable and the one genuinely expensive thing in the
  // scene, since each `<Html>` is a positioned overlay element.
  useEffect(() => {
    const id = setInterval(() => {
      const frame = useTelemetryStore.getState().latest;
      if (!frame) return;

      const mounts: string[] = [];
      for (const fault of frame.active_faults ?? []) {
        if (!fault.is_sensor_fault) continue;
        const mount = SENSOR_MOUNTS[fault.type];
        if (mount && !mounts.includes(mount)) mounts.push(mount);
      }
      setSensorMounts((prev) =>
        prev.length === mounts.length && prev.every((m, i) => m === mounts[i])
          ? prev
          : mounts
      );
      setSensorDetail(
        Object.fromEntries(mounts.map((m) => [m, fusionBadgeDetail(m, frame)]))
      );

      const egtMean =
        frame.cylinders.reduce((s, c) => s + c.egt_c, 0) /
        Math.max(1, frame.cylinders.length);
      setVitals({
        boost: frame.boost_pressure_kpa,
        rpm: frame.rpm,
        egt: egtMean,
      });
    }, 250);
    return () => clearInterval(id);
  }, []);

  return (
    <group>
      {showCallouts &&
        CALLOUTS.map((c) => (
          <Html
            key={c.key}
            position={c.position}
            center
            distanceFactor={9}
            zIndexRange={[20, 0]}
            style={{ pointerEvents: "none" }}
          >
            <div
              style={{
                width: 168,
                padding: "6px 8px",
                border: "1px dashed rgba(63,208,224,0.55)",
                borderRadius: 4,
                background: "rgba(10,14,20,0.82)",
                color: "#cfd8e3",
                fontFamily: "var(--font-mono), monospace",
                fontSize: 8,
                lineHeight: 1.45,
                textAlign: "left",
                backdropFilter: "blur(2px)",
              }}
            >
              <div
                style={{
                  color: "#3fd0e0",
                  fontWeight: 700,
                  letterSpacing: "0.08em",
                  marginBottom: 2,
                  fontSize: 8,
                }}
              >
                {c.title}
              </div>
              <div style={{ color: "#8d99a8" }}>{c.body}</div>
              {c.key === "turbo" && (
                <div style={{ color: "#4FC8E8", marginTop: 3 }}>
                  boost {vitals.boost.toFixed(0)} kPa
                </div>
              )}
              {c.key === "cylinders" && (
                <div style={{ color: "#f5a623", marginTop: 3 }}>
                  mean EGT {vitals.egt.toFixed(0)} °C
                </div>
              )}
            </div>
          </Html>
        ))}

      {/*
        Sensor-fault badges.

        These deliberately do NOT colour the physical part. A drifting probe means the
        reading is wrong, not that the cylinder is damaged — and telling those two apart
        is the point of the whole disambiguation layer, so the most visible surface in the
        product must not conflate them. Hence a dashed amber "?" badge floating at the
        sensor's mount point, visually unlike the solid red of a real fault.
      */}
      {sensorMounts.map((mount) => {
        const position = SENSOR_BADGE_POSITION[mount];
        if (!position) return null;
        return (
          <Html
            key={mount}
            position={position}
            center
            distanceFactor={8}
            zIndexRange={[30, 0]}
            style={{ pointerEvents: "none" }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "4px 7px",
                border: "1.5px dashed #f5a623",
                borderRadius: 4,
                background: "rgba(24,18,6,0.9)",
                color: "#f5a623",
                fontFamily: "var(--font-mono), monospace",
                fontSize: 8,
                whiteSpace: "nowrap",
              }}
            >
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 12,
                  height: 12,
                  borderRadius: "50%",
                  border: "1.5px dashed #f5a623",
                  fontWeight: 700,
                  fontSize: 8,
                }}
              >
                ?
              </span>
              <span>
                <strong>{SENSOR_BADGE_LABEL[mount] ?? mount}</strong>
                <span style={{ opacity: 0.75 }}>
                  {" — "}
                  {sensorDetail[mount] ?? "suspect reading"}
                </span>
              </span>
            </div>
          </Html>
        );
      })}
    </group>
  );
}
