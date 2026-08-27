"use client";

/**
 * Procedural stylized engine cutaway (no external 3D asset — see public/models/ stub).
 * Cylinder banks flash through the firing order at a rate derived from RPM and glow
 * hotter/redder as each cylinder's EGT rises, giving an at-a-glance "is it running
 * smoothly" cue that complements the numeric gauges.
 *
 * TODO(phase-2): once a real geometry/asset pipeline exists, this can be swapped for an
 * imported GLTF model without changing the store wiring below.
 */
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { GlassCard } from "@/components/ui/GlassCard";
import { useTelemetryStore } from "@/lib/store";

const FIRING_ORDER = [0, 2, 3, 1]; // 1-3-4-2, 0-indexed

function tempToColor(egt: number): THREE.Color {
  const t = THREE.MathUtils.clamp((egt - 400) / 500, 0, 1);
  const cool = new THREE.Color("#3fd0e0");
  const warm = new THREE.Color("#f5a623");
  const hot = new THREE.Color("#ef4a5f");
  if (t < 0.6) return cool.clone().lerp(warm, t / 0.6);
  return warm.clone().lerp(hot, (t - 0.6) / 0.4);
}

interface CylinderJugProps {
  index: number;
  x: number;
  egt: number;
  rpm: number;
}

function CylinderJug({ index, x, egt, rpm }: CylinderJugProps) {
  const matRef = useRef<THREE.MeshStandardMaterial>(null);
  const baseColor = useMemo(() => tempToColor(egt), [egt]);

  useFrame(({ clock }) => {
    if (!matRef.current) return;
    const firingHz = Math.max(0.4, rpm / 60 / 2);
    const cyclePos = (clock.getElapsedTime() * firingHz) % 1;
    const myOrderPos = FIRING_ORDER.indexOf(index) / FIRING_ORDER.length;
    const raw = Math.abs(cyclePos - myOrderPos);
    const dist = Math.min(raw, 1 - raw);
    const flash = Math.max(0, 1 - dist * FIRING_ORDER.length * 2.2);
    matRef.current.color.copy(baseColor);
    matRef.current.emissive.copy(baseColor);
    matRef.current.emissiveIntensity = 0.35 + flash * 1.7;
  });

  return (
    <group position={[x, 0, 0]}>
      <mesh position={[0, 0.75, 0]}>
        <cylinderGeometry args={[0.32, 0.36, 1.1, 24]} />
        <meshStandardMaterial ref={matRef} roughness={0.4} metalness={0.5} />
      </mesh>
      <mesh position={[0, 1.4, 0]}>
        <cylinderGeometry args={[0.38, 0.38, 0.18, 24]} />
        <meshStandardMaterial color="#2a3648" roughness={0.5} metalness={0.6} />
      </mesh>
    </group>
  );
}

function Crankshaft({ rpm }: { rpm: number }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame((_, delta) => {
    if (!ref.current) return;
    ref.current.rotation.z += delta * (rpm / 60) * Math.PI * 2 * 0.15;
  });
  return (
    <mesh ref={ref} position={[0, -0.35, 0]}>
      <cylinderGeometry args={[0.12, 0.12, 3.6, 12]} />
      <meshStandardMaterial color="#5b6b82" metalness={0.8} roughness={0.3} />
    </mesh>
  );
}

function Scene() {
  const latest = useTelemetryStore((s) => s.latest);
  const cylinders = latest?.cylinders ?? [
    { id: 1, egt_c: 500, vibration_rms: 0 },
    { id: 2, egt_c: 500, vibration_rms: 0 },
    { id: 3, egt_c: 500, vibration_rms: 0 },
    { id: 4, egt_c: 500, vibration_rms: 0 },
  ];
  const rpm = latest?.rpm ?? 0;
  const n = cylinders.length;
  const spacing = 1.05;
  const startX = -((n - 1) * spacing) / 2;

  return (
    <>
      <ambientLight intensity={0.35} />
      <directionalLight position={[4, 6, 4]} intensity={0.9} />
      <pointLight position={[0, 3, 3]} intensity={0.4} color="#3fd0e0" />

      <mesh position={[0, 0, 0]}>
        <boxGeometry args={[n * spacing + 0.4, 0.7, 1.3]} />
        <meshStandardMaterial color="#131a28" roughness={0.6} metalness={0.4} />
      </mesh>

      {cylinders.map((c, i) => (
        <CylinderJug key={c.id} index={i} x={startX + i * spacing} egt={c.egt_c} rpm={rpm} />
      ))}

      <Crankshaft rpm={rpm} />

      <gridHelper args={[8, 16, "#1e2734", "#131a28"]} position={[0, -0.9, 0]} />
    </>
  );
}

export function EngineCutaway3D() {
  return (
    <GlassCard
      title="Engine Cutaway"
      subtitle="Procedural · firing order animated"
      glow="cyan"
      className="h-full"
      bodyClassName="p-0"
    >
      <div className="h-[340px] w-full overflow-hidden rounded-b-xl">
        <Canvas camera={{ position: [3.2, 2.4, 4.2], fov: 42 }} dpr={[1, 1.5]}>
          <color attach="background" args={["#0a0e14"]} />
          <Scene />
          <OrbitControls enablePan={false} minDistance={3} maxDistance={8} autoRotate autoRotateSpeed={0.6} />
        </Canvas>
      </div>
    </GlassCard>
  );
}
