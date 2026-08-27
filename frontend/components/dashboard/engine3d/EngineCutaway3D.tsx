"use client";

import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import clsx from "clsx";
import { useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { GlassCard } from "@/components/ui/GlassCard";
import { useTelemetryStore } from "@/lib/store";
import { CylinderBank } from "./CylinderBank";
import { Crankshaft } from "./Crankshaft";
import { EngineLabelsOverlay } from "./EngineLabelsOverlay";
import { ExhaustManifold } from "./ExhaustManifold";
import { FaultOverlays } from "./FaultOverlays";
import { IntakeManifold } from "./IntakeManifold";
import { Turbocharger } from "./Turbocharger";
import { aggregateVibration, reliabilityTint } from "./engine3dUtils";

type ViewMode = "full" | "flow";

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
  const xray = mode === "flow";

  return (
    <GlassCard
      title="Engine Cutaway"
      subtitle={
        xray
          ? "Flow diagram · intake (cyan) / exhaust (teal)"
          : "Turbocharged opposed-four · live telemetry"
      }
      glow="cyan"
      className="h-full"
      bodyClassName="p-0"
    >
      <div className="relative h-[340px] w-full overflow-hidden rounded-b-xl">
        <Canvas
          camera={{ position: [3.9, 2.5, 4.6], fov: 42 }}
          dpr={[1, 1.75]}
          gl={{ antialias: true, powerPreference: "high-performance" }}
        >
          <color attach="background" args={["#0a0e14"]} />
          <fog attach="fog" args={["#0a0e14", 8, 16]} />
          <Scene xray={xray} />
          <OrbitControls
            enablePan={false}
            minDistance={3.4}
            maxDistance={11}
            autoRotate={!xray}
            autoRotateSpeed={0.45}
            target={[0, 0, 0]}
          />
        </Canvas>

        <ViewModeToggle mode={mode} onChange={setMode} />
        <Legend xray={xray} />
      </div>
    </GlassCard>
  );
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
  const current = useMemo(() => new THREE.Color("#22d3a8"), []);

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

/** Orange ignition leads looping along each bank, as on the reference render. */
function IgnitionHarness({ xray }: { xray: boolean }) {
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
            transparent={xray}
            opacity={xray ? 0.35 : 1}
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
    <div className="absolute right-3 top-3 flex gap-1 rounded-lg border border-base-border bg-base-bg/85 p-1 backdrop-blur">
      {(
        [
          ["full", "Full Engine"],
          ["flow", "Flow Diagram"],
        ] as const
      ).map(([value, label]) => (
        <button
          key={value}
          onClick={() => onChange(value)}
          className={clsx(
            "rounded-md px-2.5 py-1 font-mono text-[10px] transition-colors",
            mode === value
              ? "bg-status-cyan/15 text-status-cyan"
              : "text-slate-500 hover:text-slate-300"
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function Legend({ xray }: { xray: boolean }) {
  if (!xray) return null;
  return (
    <div className="absolute bottom-3 left-3 flex flex-col gap-1 rounded-lg border border-base-border bg-base-bg/85 px-2.5 py-2 backdrop-blur">
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
