"use client";

import { useCallback, useMemo } from "react";
import * as THREE from "three";
import { useTelemetryStore } from "@/lib/store";
import { FlowArrows, FlowTube } from "./FlowArrows";
import {
  COLORS,
  CYLINDER_LAYOUT,
  LAYOUT,
  boostToFlow,
} from "./engine3dUtils";

/**
 * Induction path: compressor outlet → throttle body → plenum along the top of the case →
 * a runner down into each cylinder head. This is the cyan path in
 * turbo-flow-diagram.png.
 *
 * Arrow speed and density are driven by boost pressure, so the induction system visibly
 * works harder as the turbo comes up — the same signal that spins the compressor wheel.
 */
export function IntakeManifold({ xray, emphasis }: { xray: boolean; emphasis: number }) {
  const { x: turboX, y: turboY } = LAYOUT.turbo;
  const { x: throttleX, y: throttleY } = LAYOUT.throttleBody;
  const plenumY = LAYOUT.intakePlenumY;

  /** Compressor outlet → throttle body → up the front face → into the plenum. */
  const trunk = useMemo(
    () =>
      new THREE.CatmullRomCurve3([
        new THREE.Vector3(turboX + 0.32, turboY, 0),
        new THREE.Vector3(turboX + 0.55, turboY - 0.22, 0.05),
        new THREE.Vector3(throttleX - 0.18, throttleY, 0.05),
        new THREE.Vector3(throttleX + 0.25, throttleY + 0.05, 0),
        new THREE.Vector3(throttleX + 0.6, 0.1, 0),
        new THREE.Vector3(throttleX + 0.75, plenumY - 0.12, 0),
        new THREE.Vector3(-0.15, plenumY, 0),
        new THREE.Vector3(0.75, plenumY, 0),
      ]),
    [turboX, turboY, throttleX, throttleY, plenumY]
  );

  /** One runner per cylinder: plenum → down and outboard into the head. */
  const runners = useMemo(
    () =>
      CYLINDER_LAYOUT.map((c) =>
        new THREE.CatmullRomCurve3([
          new THREE.Vector3(c.x, plenumY, 0),
          new THREE.Vector3(c.x, plenumY - 0.02, c.bank * 0.34),
          new THREE.Vector3(c.x, plenumY - 0.18, c.bank * 0.82),
          new THREE.Vector3(c.x, 0.28, c.bank * (LAYOUT.cylinderOuter - 0.16)),
        ])
      ),
    [plenumY]
  );

  const sample = useCallback(() => {
    const frame = useTelemetryStore.getState().latest;
    return boostToFlow(frame?.boost_pressure_kpa ?? 100);
  }, []);

  const tubeOpacity = xray ? 0.4 : 0.2;

  return (
    <group>
      <FlowTube curve={trunk} color={COLORS.intake} radius={0.07} opacity={tubeOpacity} />
      <FlowArrows
        curve={trunk}
        color={COLORS.intake}
        count={26}
        sample={sample}
        emphasis={emphasis}
      />

      {runners.map((curve, i) => (
        <group key={i}>
          <FlowTube
            curve={curve}
            color={COLORS.intake}
            radius={0.05}
            opacity={tubeOpacity}
          />
          <FlowArrows
            curve={curve}
            color={COLORS.intake}
            count={7}
            size={0.042}
            sample={sample}
            emphasis={emphasis}
          />
        </group>
      ))}

      {/* throttle body housing */}
      <mesh position={[throttleX, throttleY, 0]}>
        <cylinderGeometry args={[0.15, 0.15, 0.3, 16]} />
        <meshStandardMaterial
          color={COLORS.darkMetal}
          metalness={0.6}
          roughness={0.45}
          transparent={xray}
          opacity={xray ? 0.35 : 1}
        />
      </mesh>
      {/* butterfly plate, visible through the throttle body */}
      <mesh position={[throttleX, throttleY, 0]} rotation={[0.5, 0, 0]}>
        <cylinderGeometry args={[0.13, 0.13, 0.015, 16]} />
        <meshStandardMaterial color={COLORS.brass} metalness={0.85} roughness={0.3} />
      </mesh>

      {/* carburettor / injection housings at bottom centre, as on the boxer render */}
      {[-0.34, 0.34].map((z) => (
        <mesh key={z} position={[throttleX + 0.55, throttleY - 0.05, z]}>
          <boxGeometry args={[0.26, 0.22, 0.2]} />
          <meshStandardMaterial
            color="#2f353f"
            metalness={0.5}
            roughness={0.55}
            transparent={xray}
            opacity={xray ? 0.35 : 1}
          />
        </mesh>
      ))}

      {/* intake plenum log along the top of the case */}
      <mesh position={[0.15, plenumY, 0]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.1, 0.1, 1.9, 16]} />
        <meshStandardMaterial
          color={COLORS.intake}
          metalness={0.4}
          roughness={0.5}
          transparent
          opacity={xray ? 0.4 : 0.75}
        />
      </mesh>
    </group>
  );
}
