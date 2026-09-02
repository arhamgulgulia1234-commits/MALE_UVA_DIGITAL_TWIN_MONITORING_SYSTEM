"use client";

import { Html } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { useTelemetryStore } from "@/lib/store";
import { ENGINE_PARTS_BY_ID } from "@/lib/enginePartsRegistry";
import { usePartSelection } from "./partSelection";
import { LAYOUT, SENSOR_MOUNTS } from "./engine3dUtils";

/**
 * Callout labels for the Flow Diagram view, plus sensor-fault badges.
 *
 * Flow Diagram labels used to be `<Html>` nodes anchored directly at each part's 3D
 * point, which meant two labels could visually collide whenever the camera angle put
 * their anchors close together on screen. They are now fixed screen-space "slots" — two
 * rows pinned to the top/bottom of the canvas, laid out once from the canvas size — each
 * connected to its part by a thin leader line whose endpoint is reprojected every frame.
 * The slot itself never moves, so two labels can never overlap regardless of camera
 * angle; only the line's far end tracks the orbit.
 *
 * This is all done with one `<Html fullscreen>` wrapper rather than one `<Html>` per
 * label: `fullscreen` centers its content on the *anchor's* projected screen position,
 * and the anchor here is the overlay's own group, which sits at the world origin — the
 * same point `OrbitControls` orbits around. That keeps the fullscreen div aligned with
 * the canvas at every camera angle, so plain CSS pixel coordinates inside it line up
 * with projected 3D points computed the same way (see `projectToPixels`).
 *
 * Unlike everything else in this scene, labels are DOM and therefore do re-render — but
 * only on a view-mode toggle, a hover change, or a sensor fault appearing. Leader-line
 * endpoints are pushed straight into the SVG DOM via refs inside `useFrame`, same as
 * every other per-frame binding in this directory: text/lines that must track the camera
 * every frame do so without going through React state.
 */

interface FlowCallout {
  key: string;
  /** Registry id — clicking the label opens this part's existing PartDetailPanel. */
  partId: string;
  title: string;
}

/**
 * The parts shown in the Flow Diagram. Order matters only in that it drives the
 * alternating top/bottom row assignment below — a stable, camera-independent split, so a
 * label never jumps rows mid-orbit.
 */
const FLOW_CALLOUTS: FlowCallout[] = [
  { key: "turbo", partId: "turbocharger", title: "TURBOCHARGER" },
  { key: "throttle", partId: "throttle-body", title: "THROTTLE BODY" },
  { key: "intake", partId: "intake-manifold", title: "INTAKE MANIFOLD" },
  { key: "cylinders", partId: "cylinder-1", title: "CYLINDERS" },
  { key: "exhaust", partId: "exhaust-manifold", title: "EXHAUST MANIFOLD" },
  { key: "wastegate", partId: "wastegate", title: "WASTE GATE" },
];

const LABEL_WIDTH = 148;
/** Matches the box's `minHeight` below, so a leader line always meets the true edge. */
const LABEL_HEIGHT = 48;
const ROW_MARGIN = 10;
const LINE_GAP = 8;
/** Slots per line before a row wraps into a second stacked line. Six callouts today
 *  never come close to this — it exists so a future part doesn't silently overlap. */
const MAX_PER_LINE = 4;
/** Screen-space radius, in canvas pixels, that counts as "hovering the part itself". */
const HOVER_PROXIMITY_PX = 20;

type Row = "top" | "bottom";
interface Slot {
  x: number;
  y: number;
  row: Row;
}

function layoutRow(keys: string[], row: Row, width: number, height: number): [string, Slot][] {
  return keys.map((key, i) => {
    const line = Math.floor(i / MAX_PER_LINE);
    const lineStart = line * MAX_PER_LINE;
    const countInLine = Math.min(MAX_PER_LINE, keys.length - lineStart);
    const posInLine = i - lineStart;
    const x = (width * (posInLine + 1)) / (countInLine + 1);
    const y =
      row === "top"
        ? ROW_MARGIN + line * (LABEL_HEIGHT + LINE_GAP)
        : height - ROW_MARGIN - LABEL_HEIGHT - line * (LABEL_HEIGHT + LINE_GAP);
    return [key, { x, y, row }];
  });
}

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

  const { select } = usePartSelection();
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);

  // Explicit hover (mouse over the label box itself) and proximity hover (mouse near the
  // part's live projected position) are tracked separately so neither clobbers the
  // other; the label/line render at "hovered" if either says so.
  const [boxHoverKey, setBoxHoverKey] = useState<string | null>(null);
  const [proxHoverKey, setProxHoverKey] = useState<string | null>(null);
  const proxHoverRef = useRef<string | null>(null);
  const activeHoverKey = boxHoverKey ?? proxHoverKey;

  const lineRefs = useRef<Record<string, SVGLineElement | null>>({});
  const dotRefs = useRef<Record<string, SVGCircleElement | null>>({});
  const anchorVec = useMemo(() => new THREE.Vector3(), []);

  // R3F's `pointer` defaults to (0,0) NDC — canvas centre — until the user's mouse has
  // actually moved over the canvas at least once. Without this guard, a part whose
  // anchor happens to project near centre reads as "hovered" from the very first frame,
  // before anyone has touched anything.
  const gl = useThree((s) => s.gl);
  const hasPointerMoved = useRef(false);
  useEffect(() => {
    const dom = gl.domElement;
    const onMove = () => {
      hasPointerMoved.current = true;
    };
    dom.addEventListener("pointermove", onMove);
    return () => dom.removeEventListener("pointermove", onMove);
  }, [gl]);

  const slots = useMemo(() => {
    const topKeys = FLOW_CALLOUTS.filter((_, i) => i % 2 === 0).map((c) => c.key);
    const bottomKeys = FLOW_CALLOUTS.filter((_, i) => i % 2 === 1).map((c) => c.key);
    return new Map<string, Slot>([
      ...layoutRow(topKeys, "top", size.width, size.height),
      ...layoutRow(bottomKeys, "bottom", size.width, size.height),
    ]);
  }, [size.width, size.height]);

  // Reproject every leader line's endpoint each frame, and track whether the pointer is
  // near enough to a part's live screen position to count as hovering it. Both write
  // straight into refs/DOM rather than React state, matching every other per-frame
  // binding in this scene — the exception is `setProxHoverKey`, which only fires on an
  // actual change (same guarded pattern as `setSensorMounts` below).
  useFrame(({ pointer }) => {
    if (!showCallouts) return;
    camera.updateMatrixWorld();

    const pointerPx = (pointer.x * 0.5 + 0.5) * size.width;
    const pointerPy = (1 - (pointer.y * 0.5 + 0.5)) * size.height;

    let nearestKey: string | null = null;
    let nearestDist = HOVER_PROXIMITY_PX;

    for (const c of FLOW_CALLOUTS) {
      const part = ENGINE_PARTS_BY_ID[c.partId];
      const slot = slots.get(c.key);
      if (!part || !slot) continue;

      anchorVec.set(part.focus[0], part.focus[1], part.focus[2]);
      anchorVec.project(camera);
      let px = (anchorVec.x * 0.5 + 0.5) * size.width;
      let py = (1 - (anchorVec.y * 0.5 + 0.5)) * size.height;
      // Clamp on-screen so a part that has scrolled out of frame (fully zoomed into a
      // neighbouring region) still gets a line pointing the right direction instead of
      // one that shoots off into nowhere.
      px = Math.min(Math.max(px, 4), size.width - 4);
      py = Math.min(Math.max(py, 4), size.height - 4);

      const connectorX = slot.x;
      const connectorY = slot.row === "top" ? slot.y + LABEL_HEIGHT : slot.y;

      const line = lineRefs.current[c.key];
      if (line) {
        line.setAttribute("x1", String(connectorX));
        line.setAttribute("y1", String(connectorY));
        line.setAttribute("x2", String(px));
        line.setAttribute("y2", String(py));
      }
      const dot = dotRefs.current[c.key];
      if (dot) {
        dot.setAttribute("cx", String(px));
        dot.setAttribute("cy", String(py));
      }

      const dist = Math.hypot(pointerPx - px, pointerPy - py);
      if (dist < nearestDist) {
        nearestDist = dist;
        nearestKey = c.key;
      }
    }

    const effectiveNearestKey = hasPointerMoved.current ? nearestKey : null;
    if (proxHoverRef.current !== effectiveNearestKey) {
      proxHoverRef.current = effectiveNearestKey;
      setProxHoverKey(effectiveNearestKey);
    }
  });

  // Poll the store at 4 Hz for sensor-fault badges. These are DOM nodes with text in
  // them; re-rendering them at frame rate would be both unreadable and the one
  // genuinely expensive thing in the scene, since each `<Html>` is a positioned overlay
  // element.
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
    }, 250);
    return () => clearInterval(id);
  }, []);

  return (
    <group>
      {showCallouts && (
        <Html fullscreen zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
          <svg
            width={size.width}
            height={size.height}
            style={{ position: "absolute", top: 0, left: 0, overflow: "visible", pointerEvents: "none" }}
          >
            {FLOW_CALLOUTS.map((c) => {
              const hovered = activeHoverKey === c.key;
              return (
                <g key={c.key}>
                  <line
                    ref={(el) => {
                      lineRefs.current[c.key] = el;
                    }}
                    stroke={hovered ? "#4ab9c6" : "rgba(74,185,198,0.5)"}
                    strokeWidth={hovered ? 1.6 : 1}
                    strokeDasharray="3,3"
                  />
                  <circle
                    ref={(el) => {
                      dotRefs.current[c.key] = el;
                    }}
                    r={hovered ? 3 : 2.2}
                    fill={hovered ? "#4ab9c6" : "rgba(74,185,198,0.65)"}
                  />
                </g>
              );
            })}
          </svg>

          {FLOW_CALLOUTS.map((c) => {
            const part = ENGINE_PARTS_BY_ID[c.partId];
            const slot = slots.get(c.key);
            if (!part || !slot) return null;
            const hovered = activeHoverKey === c.key;
            return (
              <div
                key={c.key}
                role="button"
                tabIndex={0}
                aria-label={`Inspect ${part.displayName}`}
                onMouseEnter={() => setBoxHoverKey(c.key)}
                onMouseLeave={() => setBoxHoverKey((k) => (k === c.key ? null : k))}
                onFocus={() => setBoxHoverKey(c.key)}
                onBlur={() => setBoxHoverKey((k) => (k === c.key ? null : k))}
                onClick={() => select(c.partId)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    select(c.partId);
                  }
                }}
                style={{
                  position: "absolute",
                  left: slot.x - LABEL_WIDTH / 2,
                  top: slot.y,
                  width: LABEL_WIDTH,
                  minHeight: LABEL_HEIGHT,
                  boxSizing: "border-box",
                  padding: "6px 8px",
                  border: hovered ? "1px dashed #4ab9c6" : "1px dashed rgba(74,185,198,0.55)",
                  borderRadius: 4,
                  background: "#0f1420",
                  color: "#cfd8e3",
                  fontFamily: "var(--font-mono), monospace",
                  fontSize: 8,
                  lineHeight: 1.4,
                  textAlign: "left",
                  pointerEvents: "auto",
                  cursor: "pointer",
                  outline: hovered ? "2px solid #4ab9c6" : "none",
                  outlineOffset: 2,
                  transition: "border-color 120ms",
                }}
              >
                <div
                  style={{
                    color: "#4ab9c6",
                    fontWeight: 700,
                    letterSpacing: "0.08em",
                    marginBottom: 2,
                    fontSize: 8,
                  }}
                >
                  {c.title}
                </div>
                <div style={{ color: "#8d99a8" }}>{part.short_label}</div>
              </div>
            );
          })}
        </Html>
      )}

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
                border: "1.5px dashed #d59834",
                borderRadius: 4,
                background: "rgba(24,18,6,0.9)",
                color: "#d59834",
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
                  border: "1.5px dashed #d59834",
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
