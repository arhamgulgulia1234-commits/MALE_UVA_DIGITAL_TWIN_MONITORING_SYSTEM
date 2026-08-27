"use client";

import { useFrame } from "@react-three/fiber";
import { useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";

interface FlowArrowsProps {
  /** Path the arrows travel along. */
  curve: THREE.Curve<THREE.Vector3>;
  color: string;
  /** Maximum arrows on this path; density scales how many are actually shown. */
  count?: number;
  size?: number;
  /**
   * Read live speed (path-lengths per second) and density (0–1) from telemetry.
   * Called once per frame — must be cheap and must not allocate.
   */
  sample: () => { speed: number; density: number };
  /** Emphasised in Flow Diagram mode, muted in Full Engine mode. */
  emphasis: number;
}

/**
 * Animated particle stream along a tube path — the moving arrows from
 * turbo-flow-diagram.png, made live.
 *
 * A single InstancedMesh carries every arrow on the path. The alternative, one mesh per
 * arrow, would mean dozens of draw calls per manifold; instancing keeps each flow path to
 * one. All per-instance transforms are written into the instance matrix buffer directly
 * inside `useFrame`, so no React state is involved and nothing allocates per frame.
 */
export function FlowArrows({
  curve,
  color,
  count = 22,
  size = 0.05,
  sample,
  emphasis,
}: FlowArrowsProps) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const material = useRef<THREE.MeshBasicMaterial>(null);
  const progress = useRef(0);

  // Scratch objects, allocated once.
  const scratch = useMemo(
    () => ({
      matrix: new THREE.Matrix4(),
      position: new THREE.Vector3(),
      tangent: new THREE.Vector3(),
      quaternion: new THREE.Quaternion(),
      scale: new THREE.Vector3(),
      up: new THREE.Vector3(0, 1, 0),
      hidden: new THREE.Vector3(0, 0, 0),
    }),
    []
  );

  const geometry = useMemo(() => {
    // Cone pointing along +Y by default; we rotate it onto the path tangent.
    const g = new THREE.ConeGeometry(size * 0.62, size * 1.9, 6);
    return g;
  }, [size]);

  useLayoutEffect(() => {
    if (mesh.current) mesh.current.frustumCulled = false;
  }, []);

  useFrame((_, delta) => {
    const instanced = mesh.current;
    if (!instanced) return;

    const { speed, density } = sample();
    progress.current = (progress.current + speed * Math.min(delta, 0.1)) % 1;

    const visible = Math.max(1, Math.round(count * density));

    for (let i = 0; i < count; i++) {
      if (i >= visible) {
        // Collapse unused instances to zero scale rather than rebuilding the mesh —
        // changing instance count would mean reallocating the buffer every frame.
        scratch.matrix.compose(
          scratch.position.set(0, 0, 0),
          scratch.quaternion.identity(),
          scratch.hidden
        );
        instanced.setMatrixAt(i, scratch.matrix);
        continue;
      }

      const t = (progress.current + i / visible) % 1;
      curve.getPointAt(t, scratch.position);
      curve.getTangentAt(t, scratch.tangent).normalize();
      scratch.quaternion.setFromUnitVectors(scratch.up, scratch.tangent);

      // Fade in and out at the ends so arrows do not pop into existence mid-air.
      const edge = Math.min(t, 1 - t);
      const fade = Math.min(1, edge / 0.06);
      const s = size * (0.7 + emphasis * 0.6) * fade;
      scratch.scale.set(s / size, s / size, s / size);

      scratch.matrix.compose(scratch.position, scratch.quaternion, scratch.scale);
      instanced.setMatrixAt(i, scratch.matrix);
    }

    instanced.instanceMatrix.needsUpdate = true;
    if (material.current) {
      material.current.opacity = 0.35 + emphasis * 0.55;
    }
  });

  return (
    <instancedMesh ref={mesh} args={[geometry, undefined, count]}>
      <meshBasicMaterial
        ref={material}
        color={color}
        transparent
        opacity={0.6}
        depthWrite={false}
        toneMapped={false}
      />
    </instancedMesh>
  );
}

/** Translucent tube showing where a flow path runs, under the arrows. */
export function FlowTube({
  curve,
  color,
  radius = 0.055,
  opacity = 0.22,
}: {
  curve: THREE.Curve<THREE.Vector3>;
  color: string;
  radius?: number;
  opacity?: number;
}) {
  const geometry = useMemo(
    () => new THREE.TubeGeometry(curve, 48, radius, 10, false),
    [curve, radius]
  );
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial
        color={color}
        transparent
        opacity={opacity}
        metalness={0.3}
        roughness={0.6}
        depthWrite={false}
      />
    </mesh>
  );
}
