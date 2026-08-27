"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useTelemetryStore } from "@/lib/store";
import { COLORS, LAYOUT, rpmToAngularVelocity } from "./engine3dUtils";

/**
 * Central crankcase, crankshaft and propeller hub stub.
 *
 * The shaft and hub rotate at a speed proportional to live RPM. Rotation is integrated
 * from the frame delta rather than set from absolute elapsed time — otherwise every RPM
 * change would jump the shaft to a new angle instead of smoothly changing its rate.
 */
export function Crankshaft({ xray }: { xray: boolean }) {
  const shaft = useRef<THREE.Group>(null);
  const hub = useRef<THREE.Group>(null);

  const throwGeometry = useMemo(
    () => new THREE.BoxGeometry(0.1, 0.26, 0.16),
    []
  );

  useFrame((_, delta) => {
    const frame = useTelemetryStore.getState().latest;
    const omega = rpmToAngularVelocity(frame?.rpm ?? 0);
    const step = omega * Math.min(delta, 0.1);
    if (shaft.current) shaft.current.rotation.x += step;
    if (hub.current) hub.current.rotation.x += step;
  });

  const opacity = xray ? 0.3 : 1;

  return (
    <group>
      {/* crankcase — the central mass the banks bolt onto */}
      <mesh>
        <boxGeometry
          args={[LAYOUT.crankcase.length, LAYOUT.crankcase.height, LAYOUT.crankcase.width]}
        />
        <meshStandardMaterial
          color={COLORS.crankcase}
          metalness={0.45}
          roughness={0.6}
          transparent={xray}
          opacity={opacity}
        />
      </mesh>

      {/* cast ribbing along the case top, as on the reference render */}
      {[-0.85, -0.3, 0.25, 0.8].map((x) => (
        <mesh key={x} position={[x, LAYOUT.crankcase.height / 2, 0]}>
          <boxGeometry args={[0.08, 0.07, LAYOUT.crankcase.width * 0.92]} />
          <meshStandardMaterial
            color={COLORS.casting}
            metalness={0.4}
            roughness={0.6}
            transparent={xray}
            opacity={opacity}
          />
        </mesh>
      ))}

      {/* accessory / sump housing under the case */}
      <mesh position={[-0.15, -0.45, 0]}>
        <boxGeometry args={[1.1, 0.34, 0.6]} />
        <meshStandardMaterial
          color={COLORS.darkMetal}
          metalness={0.55}
          roughness={0.5}
          transparent={xray}
          opacity={opacity}
        />
      </mesh>

      {/* crankshaft — always visible, it is the thing that moves */}
      <group ref={shaft}>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.075, 0.075, LAYOUT.crankcase.length + 0.5, 14]} />
          <meshStandardMaterial color="#b9c2cc" metalness={0.9} roughness={0.22} />
        </mesh>
        {/* crank throws, offset so the rotation is legible */}
        {LAYOUT.cylinderStations.map((x, i) => (
          <mesh
            key={x}
            geometry={throwGeometry}
            position={[x, 0.11, 0]}
            rotation={[i * Math.PI, 0, 0]}
          >
            <meshStandardMaterial color="#98a3af" metalness={0.85} roughness={0.3} />
          </mesh>
        ))}
      </group>

      {/* propeller hub stub */}
      <group ref={hub} position={[LAYOUT.propHubX, 0, 0]}>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.2, 0.26, 0.26, 20]} />
          <meshStandardMaterial color={COLORS.darkMetal} metalness={0.7} roughness={0.35} />
        </mesh>
        <mesh position={[0.2, 0, 0]} rotation={[0, 0, -Math.PI / 2]}>
          <coneGeometry args={[0.19, 0.34, 20]} />
          <meshStandardMaterial color="#c9d2db" metalness={0.75} roughness={0.28} />
        </mesh>
        {/* three stub blade roots — enough to read the rotation */}
        {[0, (Math.PI * 2) / 3, (Math.PI * 4) / 3].map((a) => (
          <mesh
            key={a}
            position={[0, Math.cos(a) * 0.3, Math.sin(a) * 0.3]}
            rotation={[a, 0, 0]}
          >
            <boxGeometry args={[0.07, 0.34, 0.1]} />
            <meshStandardMaterial color="#8d97a3" metalness={0.6} roughness={0.45} />
          </mesh>
        ))}
      </group>

      {/* gearcase at the front of the case */}
      <mesh position={[LAYOUT.crankcase.length / 2 + 0.12, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.3, 0.34, 0.24, 20]} />
        <meshStandardMaterial
          color={COLORS.crankcase}
          metalness={0.45}
          roughness={0.6}
          transparent={xray}
          opacity={opacity}
        />
      </mesh>
    </group>
  );
}
