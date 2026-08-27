"use client";

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { useTelemetryStore } from "@/lib/store";
import {
  COLORS,
  CYLINDER_LAYOUT,
  LAYOUT,
  busVoltageColor,
  clamp01,
  firingPulse,
  readFaults,
} from "./engine3dUtils";

const SMOKE_COUNT = 26;
const HAZE_COUNT = 34;

/**
 * Visual overlays for conditions that are not a property of any single part: the
 * lubrication glow over the crankcase, misfire smoke, cooling heat haze, and the
 * electrical bus lamp.
 *
 * These are kept out of the part components deliberately. A crankcase glow is a statement
 * about oil pressure, not about the crankcase mesh, and mixing the two would mean the
 * geometry components each had to know about the fault model.
 */
export function FaultOverlays() {
  return (
    <group>
      <CrankcaseGlow />
      <MisfireSmoke />
      <HeatHaze />
      <ElectricalLamp />
    </group>
  );
}

/**
 * Pulsing amber→red translucent shell over the crankcase when oil pressure is low or a
 * bearing/oil fault is active. Sized to envelop the case rather than replace it, so the
 * engine underneath stays readable.
 */
function CrankcaseGlow() {
  const mesh = useRef<THREE.Mesh>(null);
  const material = useRef<THREE.MeshBasicMaterial>(null);
  const color = useMemo(() => new THREE.Color(), []);
  const amber = useMemo(() => new THREE.Color(COLORS.caution), []);
  const red = useMemo(() => new THREE.Color(COLORS.nogo), []);

  useFrame(({ clock }) => {
    const frame = useTelemetryStore.getState().latest;
    const faults = readFaults(frame);
    const severity = faults.bearingOrOilSeverity;

    if (!material.current || !mesh.current) return;

    if (severity < 0.02) {
      material.current.opacity = 0;
      mesh.current.visible = false;
      return;
    }
    mesh.current.visible = true;

    const pulse = Math.sin(clock.getElapsedTime() * 3.4) * 0.5 + 0.5;
    color.copy(amber).lerp(red, clamp01(severity * 1.3));
    material.current.color.copy(color);
    material.current.opacity = (0.12 + severity * 0.3) * (0.55 + pulse * 0.45);
    mesh.current.scale.setScalar(1 + pulse * 0.02 * severity);
  });

  return (
    <mesh ref={mesh} visible={false}>
      <boxGeometry
        args={[
          LAYOUT.crankcase.length + 0.3,
          LAYOUT.crankcase.height + 0.75,
          LAYOUT.crankcase.width + 0.3,
        ]}
      />
      <meshBasicMaterial
        ref={material}
        color={COLORS.caution}
        transparent
        opacity={0}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  );
}

/**
 * Dark smoke puffs from the misfiring cylinder.
 *
 * These are emitted on *skipped* cycles specifically — the cycles where
 * `firingPulse` reports no combustion. A cylinder that fires cleanly produces a bright
 * flash (handled in CylinderBank); one that drops a cycle pushes unburnt charge out
 * instead, and that is what this shows. Tying the two to the same function keeps the
 * flash and the smoke mutually exclusive, which is what makes the stutter legible.
 */
function MisfireSmoke() {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const material = useRef<THREE.MeshBasicMaterial>(null);
  const lastSkipCycle = useRef(-1);

  const particles = useMemo(
    () =>
      Array.from({ length: SMOKE_COUNT }, () => ({
        life: 0,
        position: new THREE.Vector3(),
        velocity: new THREE.Vector3(),
      })),
    []
  );

  const scratch = useMemo(
    () => ({
      matrix: new THREE.Matrix4(),
      quaternion: new THREE.Quaternion(),
      scale: new THREE.Vector3(),
      origin: new THREE.Vector3(),
    }),
    []
  );

  useFrame(({ clock }, delta) => {
    const instanced = mesh.current;
    if (!instanced) return;
    const frame = useTelemetryStore.getState().latest;
    const faults = readFaults(frame);
    const dt = Math.min(delta, 0.1);

    const index = faults.misfireCylinderIndex;
    if (frame && index >= 0 && faults.misfireSeverity > 0.02) {
      const { skipped } = firingPulse(
        clock.getElapsedTime(),
        index,
        frame.rpm,
        faults.misfireSeverity,
        true
      );
      const cyclesPerSecond = Math.max(0.25, frame.rpm / 60 / 2);
      const cycleIndex = Math.floor(clock.getElapsedTime() * cyclesPerSecond);

      // Emit once per skipped cycle, not once per frame within it.
      if (skipped && cycleIndex !== lastSkipCycle.current) {
        lastSkipCycle.current = cycleIndex;
        const layout = CYLINDER_LAYOUT[index];
        if (layout) {
          scratch.origin.set(
            layout.x,
            0.1,
            layout.bank * (LAYOUT.cylinderOuter - 0.1)
          );
          let emitted = 0;
          for (const p of particles) {
            if (p.life > 0 || emitted >= 3) continue;
            p.life = 1;
            p.position.copy(scratch.origin);
            p.velocity.set(
              (Math.random() - 0.5) * 0.35,
              0.35 + Math.random() * 0.3,
              layout.bank * (0.25 + Math.random() * 0.3)
            );
            emitted++;
          }
        }
      }
    }

    let anyAlive = false;
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i]!;
      if (p.life <= 0) {
        scratch.scale.set(0, 0, 0);
      } else {
        p.life -= dt * 0.85;
        p.position.addScaledVector(p.velocity, dt);
        p.velocity.multiplyScalar(1 - dt * 0.9);
        const s = (1 - p.life) * 0.22 + 0.05;
        scratch.scale.set(s, s, s);
        anyAlive = true;
      }
      scratch.matrix.compose(p.position, scratch.quaternion, scratch.scale);
      instanced.setMatrixAt(i, scratch.matrix);
    }
    instanced.instanceMatrix.needsUpdate = true;
    instanced.visible = anyAlive;
    if (material.current) material.current.opacity = 0.5;
  });

  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, SMOKE_COUNT]} frustumCulled={false}>
      <sphereGeometry args={[1, 6, 6]} />
      <meshBasicMaterial
        ref={material}
        color="#1a1d22"
        transparent
        opacity={0.5}
        depthWrite={false}
      />
    </instancedMesh>
  );
}

/** Rising heat shimmer over the cylinder banks; density scales with cooling degradation. */
function HeatHaze() {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const material = useRef<THREE.MeshBasicMaterial>(null);

  const seeds = useMemo(
    () =>
      Array.from({ length: HAZE_COUNT }, () => ({
        cylinder: Math.floor(Math.random() * CYLINDER_LAYOUT.length),
        phase: Math.random(),
        speed: 0.25 + Math.random() * 0.35,
        drift: (Math.random() - 0.5) * 0.22,
      })),
    []
  );

  const scratch = useMemo(
    () => ({
      matrix: new THREE.Matrix4(),
      position: new THREE.Vector3(),
      quaternion: new THREE.Quaternion(),
      scale: new THREE.Vector3(),
    }),
    []
  );

  useFrame(({ clock }) => {
    const instanced = mesh.current;
    if (!instanced) return;
    const frame = useTelemetryStore.getState().latest;
    const faults = readFaults(frame);

    // Baseline shimmer from a hot engine, plus extra when cooling is degraded.
    const cht = frame?.cht_c ?? 150;
    const base = clamp01((cht - 170) / 90) * 0.4;
    const density = clamp01(base + faults.coolingDegradation * 0.9);

    if (density < 0.02) {
      instanced.visible = false;
      return;
    }
    instanced.visible = true;

    const t = clock.getElapsedTime();
    const visible = Math.round(HAZE_COUNT * density);

    for (let i = 0; i < HAZE_COUNT; i++) {
      const seed = seeds[i]!;
      if (i >= visible) {
        scratch.scale.set(0, 0, 0);
        scratch.position.set(0, 0, 0);
      } else {
        const layout = CYLINDER_LAYOUT[seed.cylinder]!;
        const rise = (seed.phase + t * seed.speed) % 1;
        scratch.position.set(
          layout.x + seed.drift,
          0.5 + rise * 1.3,
          layout.bank * (0.9 + seed.drift * 0.5)
        );
        const s = 0.05 + rise * 0.09;
        scratch.scale.set(s, s, s);
      }
      scratch.matrix.compose(scratch.position, scratch.quaternion, scratch.scale);
      instanced.setMatrixAt(i, scratch.matrix);
    }
    instanced.instanceMatrix.needsUpdate = true;
    if (material.current) {
      material.current.opacity = 0.03 + density * 0.13;
    }
  });

  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, HAZE_COUNT]} frustumCulled={false}>
      <sphereGeometry args={[1, 5, 5]} />
      <meshBasicMaterial
        ref={material}
        color="#ff9d5c"
        transparent
        opacity={0.06}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </instancedMesh>
  );
}

/** Small green/amber/red bus-voltage lamp at the crankcase base. */
function ElectricalLamp() {
  const lamp = useRef<THREE.MeshBasicMaterial>(null);
  const light = useRef<THREE.PointLight>(null);
  const color = useMemo(() => new THREE.Color(), []);

  useFrame(({ clock }) => {
    const frame = useTelemetryStore.getState().latest;
    color.set(busVoltageColor(frame));
    const faults = readFaults(frame);

    // Flash when the alternator is actually failing; steady otherwise.
    const urgency = faults.electricalSeverity;
    const pulse =
      urgency > 0.05 ? 0.5 + 0.5 * Math.sin(clock.getElapsedTime() * 6) : 1;

    if (lamp.current) {
      lamp.current.color.copy(color);
      lamp.current.opacity = 0.65 + pulse * 0.35;
    }
    if (light.current) {
      light.current.color.copy(color);
      light.current.intensity = 0.35 + pulse * 0.5;
    }
  });

  return (
    <group position={[-0.15, -0.66, 0.34]}>
      <mesh>
        <sphereGeometry args={[0.055, 12, 12]} />
        <meshBasicMaterial ref={lamp} color={COLORS.go} transparent opacity={1} toneMapped={false} />
      </mesh>
      <pointLight ref={light} distance={0.9} intensity={0.5} />
    </group>
  );
}
