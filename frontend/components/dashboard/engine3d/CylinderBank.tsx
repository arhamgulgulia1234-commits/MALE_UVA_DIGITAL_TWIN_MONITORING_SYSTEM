"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useTelemetryStore } from "@/lib/store";
import {
  CYLINDER_LAYOUT,
  COLORS,
  LAYOUT,
  clamp01,
  egtToColor,
  egtToEmissiveIntensity,
  firingPulse,
  readFaults,
  vibrationOutliers,
} from "./engine3dUtils";

interface CylinderBankProps {
  /** +1 = right bank, -1 = left bank. */
  bank: 1 | -1;
  xray: boolean;
}

/**
 * One row of air-cooled finned cylinders, as in boxer-engine-render.png: a barrel with
 * stacked cooling fins, a head casting on the outboard end, and the ignition lead looping
 * back toward the crankcase.
 *
 * Every per-frame update writes straight into a material or a mesh ref. Nothing here
 * touches React state — at 10 Hz telemetry and 60 fps rendering, driving this through
 * re-renders would mean reconciling the whole subtree six times per telemetry frame for
 * changes that are purely visual.
 */
export function CylinderBank({ bank, xray }: CylinderBankProps) {
  const cylinders = useMemo(
    () => CYLINDER_LAYOUT.map((c, i) => ({ ...c, index: i })).filter((c) => c.bank === bank),
    [bank]
  );

  return (
    <group>
      {cylinders.map((c) => (
        <Cylinder key={c.id} index={c.index} x={c.x} bank={bank} xray={xray} />
      ))}
    </group>
  );
}

function Cylinder({
  index,
  x,
  bank,
  xray,
}: {
  index: number;
  x: number;
  bank: 1 | -1;
  xray: boolean;
}) {
  const barrelMat = useRef<THREE.MeshStandardMaterial>(null);
  const headMat = useRef<THREE.MeshStandardMaterial>(null);
  const flashMat = useRef<THREE.MeshBasicMaterial>(null);
  const flashMesh = useRef<THREE.Mesh>(null);
  const mountMat = useRef<THREE.MeshStandardMaterial>(null);
  const finMats = useRef<(THREE.MeshStandardMaterial | null)[]>([]);

  // Scratch colours — reused every frame so the hot path never allocates.
  const scratch = useMemo(() => new THREE.Color(), []);
  const mountScratch = useMemo(() => new THREE.Color(), []);

  const length = LAYOUT.cylinderOuter - LAYOUT.cylinderInner;
  const finGeometry = useMemo(
    () => new THREE.CylinderGeometry(LAYOUT.finRadius, LAYOUT.finRadius, 0.035, 20),
    []
  );
  const finOffsets = useMemo(() => {
    const out: number[] = [];
    for (let i = 0; i < LAYOUT.finCount; i++) {
      out.push(0.12 + (i / (LAYOUT.finCount - 1)) * (length - 0.28));
    }
    return out;
  }, [length]);

  useFrame(({ clock }) => {
    const frame = useTelemetryStore.getState().latest;
    if (!frame) return;

    const reading = frame.cylinders[index];
    if (!reading) return;

    const faults = readFaults(frame);
    const outliers = vibrationOutliers(frame.cylinders);
    const outlier = outliers[index] ?? 0;

    // --- fin / barrel temperature -------------------------------------------
    egtToColor(reading.egt_c, scratch, faults.coolingDegradation);
    const intensity = egtToEmissiveIntensity(reading.egt_c, faults.coolingDegradation);

    if (barrelMat.current) {
      barrelMat.current.emissive.copy(scratch);
      barrelMat.current.emissiveIntensity = intensity * 0.55;
    }
    if (headMat.current) {
      headMat.current.emissive.copy(scratch);
      headMat.current.emissiveIntensity = intensity * 0.35;
    }
    for (const mat of finMats.current) {
      if (!mat) continue;
      mat.emissive.copy(scratch);
      mat.emissiveIntensity = intensity;
    }

    // --- firing flash --------------------------------------------------------
    const { flash } = firingPulse(
      clock.getElapsedTime(),
      index,
      frame.rpm,
      faults.misfireSeverity,
      index === faults.misfireCylinderIndex
    );
    if (flashMat.current && flashMesh.current) {
      flashMat.current.opacity = flash * (xray ? 0.35 : 0.6);
      const s = 0.55 + flash * 0.5;
      flashMesh.current.scale.setScalar(s);
    }

    // --- vibration outlier: the mount pulses red -----------------------------
    if (mountMat.current) {
      const pulse = outlier > 0.05 ? (Math.sin(clock.getElapsedTime() * 9) * 0.5 + 0.5) : 0;
      const amount = clamp01(outlier) * (0.35 + pulse * 0.65);
      mountScratch.set(COLORS.darkMetal).lerp(TMP_RED, amount);
      mountMat.current.color.copy(mountScratch);
      mountMat.current.emissive.copy(TMP_RED);
      mountMat.current.emissiveIntensity = amount * 1.4;
    }
  });

  const opacity = xray ? 0.3 : 1;

  return (
    <group position={[x, 0.05, bank * LAYOUT.cylinderInner]} rotation={[bank * Math.PI / 2, 0, 0]}>
      {/* mount flange at the crankcase joint */}
      <mesh position={[0, 0.05, 0]}>
        <cylinderGeometry args={[0.31, 0.31, 0.1, 20]} />
        <meshStandardMaterial
          ref={mountMat}
          color={COLORS.darkMetal}
          metalness={0.6}
          roughness={0.5}
          transparent={xray}
          opacity={opacity}
        />
      </mesh>

      {/* barrel */}
      <mesh position={[0, length / 2, 0]}>
        <cylinderGeometry args={[LAYOUT.cylinderRadius, LAYOUT.cylinderRadius, length, 24]} />
        <meshStandardMaterial
          ref={barrelMat}
          color={COLORS.casting}
          metalness={0.45}
          roughness={0.55}
          transparent={xray}
          opacity={opacity}
        />
      </mesh>

      {/* cooling fins */}
      {finOffsets.map((offset, i) => (
        <mesh key={i} geometry={finGeometry} position={[0, offset, 0]}>
          <meshStandardMaterial
            ref={(m) => {
              finMats.current[i] = m;
            }}
            color={COLORS.casting}
            metalness={0.35}
            roughness={0.62}
            transparent={xray}
            opacity={opacity}
          />
        </mesh>
      ))}

      {/* head casting */}
      <mesh position={[0, length + 0.06, 0]}>
        <boxGeometry args={[0.62, 0.24, 0.5]} />
        <meshStandardMaterial
          ref={headMat}
          color={COLORS.casting}
          metalness={0.5}
          roughness={0.5}
          transparent={xray}
          opacity={opacity}
        />
      </mesh>

      {/* combustion flash — sits inside the head, brightens on each firing event */}
      <mesh ref={flashMesh} position={[0, length - 0.05, 0]}>
        <sphereGeometry args={[0.19, 12, 12]} />
        <meshBasicMaterial
          ref={flashMat}
          color="#ffb066"
          transparent
          opacity={0}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* spark plug boss + ignition lead anchor */}
      <mesh position={[0.22, length - 0.02, 0.16]} rotation={[0, 0, Math.PI / 5]}>
        <cylinderGeometry args={[0.045, 0.045, 0.16, 10]} />
        <meshStandardMaterial color={COLORS.brass} metalness={0.8} roughness={0.35} />
      </mesh>
    </group>
  );
}

const TMP_RED = new THREE.Color(COLORS.nogo);
