"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useTelemetryStore } from "@/lib/store";
import {
  COLORS,
  LAYOUT,
  boostToTurboVelocity,
  clamp01,
  readFaults,
} from "./engine3dUtils";

/**
 * Turbocharger: turbine and compressor wheels on a shared shaft, plus the wastegate —
 * the assembly at the rear of turbo-flow-diagram.png.
 *
 * Two fault behaviours are modelled here, and both are about *dynamics* rather than
 * colour, because that is how a worn turbo actually presents:
 *
 *   - `turbo_wear` lengthens the spool time constant, so wheel speed visibly lags the
 *     boost the engine is asking for instead of tracking it. The lag is the symptom.
 *   - the wastegate, which normally modulates smoothly, starts to judder — a sticking
 *     actuator, which is what wear on the linkage produces.
 */
export function Turbocharger({ xray }: { xray: boolean }) {
  const turbineWheel = useRef<THREE.Group>(null);
  const compressorWheel = useRef<THREE.Group>(null);
  const wastegate = useRef<THREE.Mesh>(null);

  /** Actual wheel speed, lagged behind the commanded speed by the spool constant. */
  const actualSpeed = useRef(0);
  const judder = useRef(0);

  const bladeGeometry = useMemo(() => new THREE.BoxGeometry(0.035, 0.2, 0.09), []);

  useFrame((_, delta) => {
    const frame = useTelemetryStore.getState().latest;
    if (!frame) return;
    const faults = readFaults(frame);
    const dt = Math.min(delta, 0.1);

    const target = boostToTurboVelocity(frame.boost_pressure_kpa ?? 100);

    // Spool lag. A healthy turbo tracks quickly; wear stretches tau, so the wheels
    // visibly trail the boost demand.
    const tau = 0.35 + faults.turboWear * 2.4;
    actualSpeed.current += (target - actualSpeed.current) * clamp01(dt / tau);

    const step = actualSpeed.current * dt;
    if (turbineWheel.current) turbineWheel.current.rotation.x += step;
    if (compressorWheel.current) compressorWheel.current.rotation.x -= step;

    // Wastegate: smooth modulation when healthy, judder when worn.
    if (wastegate.current) {
      const demand = clamp01(((frame.boost_pressure_kpa ?? 100) - 100) / 56);
      let angle = demand * 0.85;
      if (faults.turboWear > 0.05) {
        judder.current += dt * (18 + faults.turboWear * 26);
        const stick = Math.sign(Math.sin(judder.current)) * faults.turboWear * 0.22;
        angle += stick + Math.sin(judder.current * 3.1) * faults.turboWear * 0.09;
      }
      wastegate.current.rotation.z = -0.35 - angle;
    }
  });

  const opacity = xray ? 0.35 : 1;
  const { x, y } = LAYOUT.turbo;

  return (
    <group position={[x, y, 0]}>
      {/* shared shaft */}
      <mesh rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.035, 0.035, 0.62, 10]} />
        <meshStandardMaterial color="#c2cbd4" metalness={0.9} roughness={0.22} />
      </mesh>

      {/* --- turbine side (exhaust driven, aft) --- */}
      <group position={[-0.26, 0, 0]}>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.3, 0.3, 0.2, 22]} />
          <meshStandardMaterial
            color={COLORS.exhaust}
            metalness={0.55}
            roughness={0.5}
            transparent={xray}
            opacity={opacity}
          />
        </mesh>
        {/* volute scroll */}
        <mesh rotation={[0, Math.PI / 2, 0]}>
          <torusGeometry args={[0.3, 0.1, 10, 24]} />
          <meshStandardMaterial
            color={COLORS.exhaust}
            metalness={0.5}
            roughness={0.55}
            transparent={xray}
            opacity={opacity}
          />
        </mesh>
        <group ref={turbineWheel}>
          {BLADE_ANGLES.map((a) => (
            <mesh
              key={a}
              geometry={bladeGeometry}
              position={[0, Math.cos(a) * 0.14, Math.sin(a) * 0.14]}
              rotation={[a, 0.5, 0]}
            >
              <meshStandardMaterial color="#7f8a95" metalness={0.85} roughness={0.3} />
            </mesh>
          ))}
        </group>
      </group>

      {/* --- compressor side (intake, forward) --- */}
      <group position={[0.26, 0, 0]}>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.28, 0.28, 0.2, 22]} />
          <meshStandardMaterial
            color={COLORS.intake}
            metalness={0.55}
            roughness={0.45}
            transparent={xray}
            opacity={opacity}
          />
        </mesh>
        <mesh rotation={[0, Math.PI / 2, 0]}>
          <torusGeometry args={[0.28, 0.095, 10, 24]} />
          <meshStandardMaterial
            color={COLORS.intake}
            metalness={0.5}
            roughness={0.5}
            transparent={xray}
            opacity={opacity}
          />
        </mesh>
        <group ref={compressorWheel}>
          {BLADE_ANGLES.map((a) => (
            <mesh
              key={a}
              geometry={bladeGeometry}
              position={[0, Math.cos(a) * 0.13, Math.sin(a) * 0.13]}
              rotation={[a, -0.5, 0]}
            >
              <meshStandardMaterial color="#aeb8c2" metalness={0.9} roughness={0.25} />
            </mesh>
          ))}
        </group>
        {/* air intake mouth */}
        <mesh position={[0.2, 0, 0]} rotation={[0, 0, -Math.PI / 2]}>
          <cylinderGeometry args={[0.17, 0.12, 0.2, 18, 1, true]} />
          <meshStandardMaterial
            color={COLORS.intake}
            metalness={0.4}
            roughness={0.6}
            side={THREE.DoubleSide}
            transparent={xray}
            opacity={opacity}
          />
        </mesh>
      </group>

      {/* --- wastegate: hinged flap on the turbine housing --- */}
      <group position={[LAYOUT.wastegate.x - x, LAYOUT.wastegate.y - y, 0]}>
        <mesh>
          <boxGeometry args={[0.16, 0.16, 0.22]} />
          <meshStandardMaterial
            color={COLORS.darkMetal}
            metalness={0.65}
            roughness={0.45}
            transparent={xray}
            opacity={opacity}
          />
        </mesh>
        <mesh ref={wastegate} position={[0.08, 0, 0]}>
          <boxGeometry args={[0.19, 0.03, 0.19]} />
          <meshStandardMaterial
            color={COLORS.brass}
            metalness={0.8}
            roughness={0.32}
            emissive={COLORS.brass}
            emissiveIntensity={0.15}
          />
        </mesh>
      </group>

      {/* exhaust gas discharge stack */}
      <mesh position={[-0.62, -0.18, 0]} rotation={[0, 0, Math.PI / 2.6]}>
        <cylinderGeometry args={[0.11, 0.13, 0.42, 14, 1, true]} />
        <meshStandardMaterial
          color={COLORS.exhaust}
          metalness={0.45}
          roughness={0.6}
          side={THREE.DoubleSide}
          transparent={xray}
          opacity={opacity}
        />
      </mesh>
    </group>
  );
}

const BLADE_ANGLES = Array.from({ length: 9 }, (_, i) => (i / 9) * Math.PI * 2);
