"use client";

import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { GlassCard } from "@/components/ui/GlassCard";
import { getEnginePart, type EnginePart } from "@/lib/enginePartsRegistry";
import { useTelemetryStore } from "@/lib/store";
import { CylinderBank } from "./CylinderBank";
import { Crankshaft } from "./Crankshaft";
import { EngineLabelsOverlay } from "./EngineLabelsOverlay";
import { ExhaustManifold } from "./ExhaustManifold";
import { FaultOverlays } from "./FaultOverlays";
import { IntakeManifold } from "./IntakeManifold";
import { ModuleMap } from "./ModuleMapToggle";
import { PartDetailPanel } from "./PartDetailPanel";
import { PartSelectionProvider, usePartSelection } from "./partSelection";
import { Turbocharger } from "./Turbocharger";
import {
  DIM_OPACITY,
  aggregateVibration,
  refreshMaterial,
  reliabilityTint,
} from "./engine3dUtils";

type ViewMode = "full" | "flow" | "modules";

/** Camera distance the scene rests at with nothing selected — the initial framing. */
const HOME_DISTANCE = Math.hypot(3.9, 2.5, 4.6);

/**
 * Telemetry-reactive 3D cutaway of the turbocharged boxer aero engine.
 *
 * Geometry follows the two reference images: the opposed, air-cooled cylinder layout and
 * central crankcase from `public/reference/boxer-engine-render.png`, wrapped in the
 * turbo / throttle body / intake / exhaust / wastegate plumbing and cyan-vs-teal flow
 * colour coding of `public/reference/turbo-flow-diagram.png`.
 *
 * **Nothing in this scene is driven by React state.** Every child reads the zustand store
 * through `getState()` inside `useFrame` and mutates refs directly. Telemetry arrives at
 * 10 Hz and the scene renders at 60; routing that through component state would mean
 * reconciling this subtree ten times a second to change numbers that only ever affect
 * material and transform properties. The one exception is the label overlay, which is DOM
 * and polls at 4 Hz — see EngineLabelsOverlay.
 */
export function EngineCutaway3D() {
  const [mode, setMode] = useState<ViewMode>("full");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const xray = mode === "flow";

  // Clicking the already-selected part clears it, so the same gesture that opened the
  // inspector closes it.
  const select = useCallback((id: string) => {
    setSelectedId((prev) => (prev === id ? null : id));
  }, []);
  const clear = useCallback(() => setSelectedId(null), []);

  const selection = useMemo(() => ({ selectedId, select }), [selectedId, select]);
  const selectedPart = getEnginePart(selectedId);

  const fromModuleMap = useCallback(
    (id: string) => {
      setMode("full");
      setSelectedId(id);
    },
    []
  );

  return (
    <GlassCard
      title="Engine Cutaway"
      subtitle={
        mode === "modules"
          ? "Module map · physical part → backend module"
          : selectedPart
            ? `Inspecting · ${selectedPart.displayName}`
            : xray
              ? "Flow diagram · intake (cyan) / exhaust (teal)"
              : "Turbocharged opposed-four · live telemetry"
      }
      glow="cyan"
      className="h-full"
      bodyClassName="p-0"
      headerRight={<ViewModeToggle mode={mode} onChange={setMode} />}
    >
      <div className="flex flex-col overflow-hidden rounded-b-xl">
        <div className="relative h-[340px] w-full overflow-hidden">
          <Canvas
            camera={{ position: [3.9, 2.5, 4.6], fov: 42 }}
            dpr={[1, 1.75]}
            gl={{ antialias: true, powerPreference: "high-performance" }}
            onPointerMissed={clear}
          >
            <color attach="background" args={["#0a0e14"]} />
            <fog attach="fog" args={["#0a0e14", 8, 16]} />
            <PartSelectionProvider value={selection}>
              <Scene xray={xray} />
            </PartSelectionProvider>
            <CameraRig part={selectedPart} panelOffset={selectedPart !== null} />
            <OrbitControls
              makeDefault
              enablePan={false}
              minDistance={3.4}
              maxDistance={11}
              autoRotate={!xray && !selectedId}
              autoRotateSpeed={0.45}
              target={[0, 0, 0]}
            />
          </Canvas>

          {selectedPart && mode !== "modules" && (
            <PartDetailPanel part={selectedPart} onClose={clear} />
          )}

          {mode === "modules" && (
            <ModuleMap selectedId={selectedId} onSelect={fromModuleMap} />
          )}

          {selectedPart && mode !== "modules" && (
            <button
              onClick={clear}
              className="absolute bottom-3 right-3 z-20 rounded-lg border border-base-border bg-base-bg px-2.5 py-1 font-mono text-[10px] text-slate-400 transition-colors hover:border-status-cyan/40 hover:text-status-cyan"
            >
              Reset View
            </button>
          )}
          {!selectedPart && mode === "full" && (
            <div className="pointer-events-none absolute bottom-3 right-3 font-mono text-[9px] text-slate-400">
              click a part to inspect
            </div>
          )}
        </div>
        <Legend xray={xray} />
      </div>
    </GlassCard>
  );
}

/**
 * Eases the orbit target and camera distance toward the selected part, then gets out of
 * the way.
 *
 * The rig only runs while a transition is in flight — once it has settled it disables
 * itself, so orbiting and zooming stay entirely the user's after the move. A rig that ran
 * every frame would silently undo every scroll-wheel zoom.
 *
 * The target is nudged sideways by roughly the width of the detail panel so the part
 * being inspected lands right of centre, clear of the panel, instead of directly behind
 * it. That is done in camera space, which is why it is recomputed as the camera moves.
 */
function CameraRig({ part, panelOffset }: { part: EnginePart | null; panelOffset: boolean }) {
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls) as
    | (THREE.EventDispatcher & { target: THREE.Vector3; update: () => void })
    | null;

  const active = useRef(false);
  const scratch = useMemo(
    () => ({
      goal: new THREE.Vector3(),
      offset: new THREE.Vector3(),
      right: new THREE.Vector3(),
    }),
    []
  );

  useEffect(() => {
    active.current = true;
  }, [part]);

  useFrame((_, delta) => {
    if (!active.current || !controls?.target) return;

    const goalDistance = part ? part.focusDistance : HOME_DISTANCE;
    scratch.goal.set(0, 0, 0);
    if (part) {
      scratch.goal.set(part.focus[0], part.focus[1], part.focus[2]);
      if (panelOffset) {
        scratch.right.setFromMatrixColumn(camera.matrixWorld, 0).setY(0).normalize();
        scratch.goal.addScaledVector(scratch.right, -goalDistance * 0.17);
      }
    }

    // ~0.5s ease, frame-rate independent.
    const k = 1 - Math.pow(0.01, Math.min(delta, 0.1) / 0.5);

    scratch.offset.copy(camera.position).sub(controls.target);
    const distance = scratch.offset.length();
    controls.target.lerp(scratch.goal, k);
    if (distance > 1e-4) {
      const next = distance + (goalDistance - distance) * k;
      scratch.offset.multiplyScalar(next / distance);
      camera.position.copy(controls.target).add(scratch.offset);
    }
    controls.update();

    if (
      controls.target.distanceTo(scratch.goal) < 0.01 &&
      Math.abs(distance - goalDistance) < 0.02
    ) {
      active.current = false;
    }
  });

  return null;
}

function Scene({ xray }: { xray: boolean }) {
  const emphasis = xray ? 1 : 0.35;

  return (
    <>
      <ambientLight intensity={xray ? 0.55 : 0.38} />
      <directionalLight position={[5, 6, 4]} intensity={0.85} />
      <directionalLight position={[-4, 2, -3]} intensity={0.3} color="#7fb4d8" />
      <RimLight />

      <JitterRig>
        <Crankshaft xray={xray} />
        <CylinderBank bank={1} xray={xray} />
        <CylinderBank bank={-1} xray={xray} />
        <Turbocharger xray={xray} />
        <IntakeManifold xray={xray} emphasis={emphasis} />
        <ExhaustManifold xray={xray} emphasis={emphasis} />
        <FaultOverlays />
        <IgnitionHarness xray={xray} />
      </JitterRig>

      <EngineLabelsOverlay showCallouts={xray} />

      <gridHelper args={[14, 28, "#1a2230", "#121822"]} position={[0, -1.15, 0]} />
    </>
  );
}

/**
 * Whole-engine shake, scaled by aggregate vibration RMS.
 *
 * Applied to a wrapper group rather than to each part, so the engine shakes as one rigid
 * body — which is what it does. Shaking parts independently would read as things coming
 * loose rather than as an engine running rough.
 */
function JitterRig({ children }: { children: React.ReactNode }) {
  const group = useRef<THREE.Group>(null);

  useFrame(({ clock }) => {
    const frame = useTelemetryStore.getState().latest;
    if (!group.current) return;
    const amount = aggregateVibration(frame?.cylinders ?? []);
    if (amount < 0.01) {
      group.current.position.set(0, 0, 0);
      return;
    }
    const t = clock.getElapsedTime();
    const scale = amount * 0.035;
    group.current.position.set(
      Math.sin(t * 47.3) * scale,
      Math.sin(t * 61.7) * scale,
      Math.sin(t * 53.1) * scale
    );
  });

  return <group ref={group}>{children}</group>;
}

/** Scene rim light tinted by the GO / CAUTION / NO-GO recommendation. */
function RimLight() {
  const light = useRef<THREE.SpotLight>(null);
  const target = useMemo(() => new THREE.Color(), []);
  const current = useMemo(() => new THREE.Color("#37af92"), []);

  useFrame((_, delta) => {
    if (!light.current) return;
    const frame = useTelemetryStore.getState().latest;
    target.set(reliabilityTint(frame));
    // Ease rather than snap — a hard colour cut on every recommendation change is
    // distracting on a screen someone is watching for minutes at a time.
    current.lerp(target, Math.min(1, delta * 1.6));
    light.current.color.copy(current);
  });

  return (
    <spotLight
      ref={light}
      position={[-3.5, 3.5, -4]}
      angle={0.9}
      penumbra={1}
      intensity={1.15}
      distance={22}
    />
  );
}

/**
 * Orange ignition leads looping along each bank, as on the reference render.
 *
 * Not a registry part — you cannot click a lead — but it still ghosts along with
 * everything else when a part is selected, because leaving it at full brightness would
 * leave two vivid orange stripes across an otherwise de-emphasised engine.
 */
function IgnitionHarness({ xray }: { xray: boolean }) {
  const { selectedId } = usePartSelection();
  const geometries = useMemo(() => {
    const make = (bank: 1 | -1) =>
      new THREE.TubeGeometry(
        new THREE.CatmullRomCurve3([
          new THREE.Vector3(-1.15, 0.2, bank * 0.2),
          new THREE.Vector3(-0.75, 0.4, bank * 0.55),
          new THREE.Vector3(-0.58, 0.36, bank * 1.02),
          new THREE.Vector3(-0.1, 0.5, bank * 0.7),
          new THREE.Vector3(0.35, 0.46, bank * 0.62),
          new THREE.Vector3(0.58, 0.36, bank * 1.02),
        ]),
        44,
        0.022,
        7,
        false
      );
    return [make(1), make(-1)];
  }, []);

  return (
    <group>
      {geometries.map((g, i) => (
        <mesh key={i} geometry={g}>
          <meshStandardMaterial
            color="#E8792B"
            roughness={0.65}
            metalness={0.1}
            onUpdate={refreshMaterial}
            transparent={xray || selectedId !== null}
            opacity={selectedId !== null ? DIM_OPACITY : xray ? 0.35 : 1}
          />
        </mesh>
      ))}
    </group>
  );
}

function ViewModeToggle({
  mode,
  onChange,
}: {
  mode: ViewMode;
  onChange: (m: ViewMode) => void;
}) {
  return (
    <div className="flex shrink-0 gap-1 rounded-lg border border-base-border bg-base-bg/60 p-1">
      {(
        [
          ["full", "Full Engine"],
          ["flow", "Flow Diagram"],
          ["modules", "Module Map"],
        ] as const
      ).map(([value, label]) => (
        <button
          key={value}
          onClick={() => onChange(value)}
          className={clsx(
            "rounded-md px-2 py-1 font-mono text-[10px] transition-colors",
            mode === value
              ? "bg-status-cyan/15 text-status-cyan"
              : "text-slate-400 hover:text-slate-300"
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/**
 * Flow Diagram's intake/exhaust colour key.
 *
 * Lives in its own strip below the canvas rather than floating on top of it — the same
 * fix as `ViewModeToggle` moving into the header, for the same reason: a fixed overlay
 * anchored to the canvas competes with the bottom label row for the same screen space no
 * matter how carefully it's positioned. Outside the canvas, it never can.
 */
function Legend({ xray }: { xray: boolean }) {
  if (!xray) return null;
  return (
    <div className="flex shrink-0 items-center gap-4 border-t border-base-border/70 px-4 py-1.5">
      {[
        ["#4FC8E8", "Intake air"],
        ["#3E8574", "Exhaust gas"],
      ].map(([color, label]) => (
        <div key={label} className="flex items-center gap-2">
          <span
            className="h-0.5 w-5 rounded-full"
            style={{ background: color as string }}
          />
          <span className="font-mono text-[9px] text-slate-400">{label}</span>
        </div>
      ))}
    </div>
  );
}
