"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useTelemetryStore } from "@/lib/store";
import { PartOutline, usePartInteraction } from "./partSelection";
import {
  COLORS,
  LAYOUT,
  boostToTurboVelocity,
  clamp01,
  partMaterial,
  readFaults,
  refreshMaterial,
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

  // Turbo and wastegate are separate registry entries. They are drawn as sibling groups
  // below so a click on the flap selects the wastegate and stops there, rather than also
  // hitting the turbine housing it is bolted to.
  const turboSel = usePartInteraction("turbocharger");
  const gateSel = usePartInteraction("wastegate");

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

  const mat = partMaterial(turboSel.visual, xray, 0.35);
  const gateMat = partMaterial(gateSel.visual, xray, 0.35);
  const { x, y } = LAYOUT.turbo;

  return (
    <>
    <group position={[x, y, 0]} {...turboSel.handlers}>
      {/* shared shaft */}
      <mesh rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.035, 0.035, 0.62, 10]} />
        <meshStandardMaterial
          color="#c2cbd4"
          metalness={0.9}
          roughness={0.22}
          onUpdate={refreshMaterial}
          transparent={mat.transparent}
          opacity={mat.opacity}
        />
      </mesh>

      {/* --- turbine side (exhaust driven, aft) --- */}
      <group position={[-0.26, 0, 0]}>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.3, 0.3, 0.2, 22]} />
          <meshStandardMaterial
            color={COLORS.exhaust}
            metalness={0.55}
            roughness={0.5}
            onUpdate={refreshMaterial}
            transparent={mat.transparent}
            opacity={mat.opacity}
          />
          <PartOutline visual={turboSel.visual} />
        </mesh>
        {/* volute scroll */}
        <mesh rotation={[0, Math.PI / 2, 0]}>
          <torusGeometry args={[0.3, 0.1, 10, 24]} />
          <meshStandardMaterial
            color={COLORS.exhaust}
            metalness={0.5}
            roughness={0.55}
            onUpdate={refreshMaterial}
            transparent={mat.transparent}
            opacity={mat.opacity}
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
              <meshStandardMaterial
                color="#7f8a95"
                metalness={0.85}
                roughness={0.3}
                onUpdate={refreshMaterial}
                transparent={mat.transparent}
                opacity={mat.opacity}
              />
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
            onUpdate={refreshMaterial}
            transparent={mat.transparent}
            opacity={mat.opacity}
          />
          <PartOutline visual={turboSel.visual} />
        </mesh>
        <mesh rotation={[0, Math.PI / 2, 0]}>
          <torusGeometry args={[0.28, 0.095, 10, 24]} />
          <meshStandardMaterial
            color={COLORS.intake}
            metalness={0.5}
            roughness={0.5}
            onUpdate={refreshMaterial}
            transparent={mat.transparent}
            opacity={mat.opacity}
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
              <meshStandardMaterial
                color="#aeb8c2"
                metalness={0.9}
                roughness={0.25}
                onUpdate={refreshMaterial}
                transparent={mat.transparent}
                opacity={mat.opacity}
              />
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
            onUpdate={refreshMaterial}
            transparent={mat.transparent}
            opacity={mat.opacity}
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
          onUpdate={refreshMaterial}
          transparent={mat.transparent}
          opacity={mat.opacity}
        />
      </mesh>
    </group>

    {/* --- wastegate: hinged flap on the turbine housing --- */}
    <group
      position={[LAYOUT.wastegate.x, LAYOUT.wastegate.y, 0]}
      {...gateSel.handlers}
    >
      <mesh>
        <boxGeometry args={[0.16, 0.16, 0.22]} />
        <meshStandardMaterial
          color={COLORS.darkMetal}
          metalness={0.65}
          roughness={0.45}
          onUpdate={refreshMaterial}
          transparent={gateMat.transparent}
          opacity={gateMat.opacity}
        />
        <PartOutline visual={gateSel.visual} />
      </mesh>
      <mesh ref={wastegate} position={[0.08, 0, 0]}>
        <boxGeometry args={[0.19, 0.03, 0.19]} />
        <meshStandardMaterial
          color={COLORS.brass}
          metalness={0.8}
          roughness={0.32}
          emissive={COLORS.brass}
          emissiveIntensity={0.15}
          onUpdate={refreshMaterial}
          transparent={gateMat.transparent}
          opacity={gateMat.opacity}
        />
        <PartOutline visual={gateSel.visual} thickness={2} />
      </mesh>
    </group>
    </>
  );
}

const BLADE_ANGLES = Array.from({ length: 9 }, (_, i) => (i / 9) * Math.PI * 2);
