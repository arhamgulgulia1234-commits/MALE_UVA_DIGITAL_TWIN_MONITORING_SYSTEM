"use client";

/**
 * 3D fleet formation — same pattern as dashboard/engine3d/EngineCutaway3D: nothing here
 * is driven by React state. Each UAV's flight loop and status colour are mutated
 * directly onto refs inside `useFrame`, reading the fleet store via `getState()` rather
 * than a subscribing selector, so a store update (every ~2s, from polling) never
 * reconciles this subtree. Geometries are created once at module scope and shared across
 * all three UAV instances; only materials are per-instance (each needs its own
 * independently animated colour).
 */
import { Canvas, type ThreeEvent, useFrame } from "@react-three/fiber";
import { useCallback, useMemo, useRef } from "react";
import { useRouter } from "next/navigation";
import * as THREE from "three";
import { mergeBufferGeometries } from "three-stdlib";
import { GlassCard } from "@/components/ui/GlassCard";
import { COLORS } from "@/components/dashboard/engine3d/engine3dUtils";
import { useFleetStore } from "@/lib/fleet/store";
import type { FleetMissionReliability, FleetOverviewEntry } from "@/lib/fleet/types";

const UAV_ORDER = ["UAV-01", "UAV-02", "UAV-03"] as const;

/** Loose formation offset + animation phase per UAV — purely cosmetic, not a flight path. */
const FORMATION: { base: [number, number, number]; phase: number }[] = [
  { base: [0, 0, 0.4], phase: 0 },
  { base: [-1.7, -0.12, -0.7], phase: 2.1 },
  { base: [1.7, 0.12, -0.7], phase: 4.2 },
];

/**
 * MALE UAV silhouette (see public/reference/male-uav-reference.png): slender tapered
 * fuselage, high-aspect straight wings with slight dihedral mounted mid-fuselage, twin
 * tail booms with small vertical fins, and a ventral sensor turret under the nose — all
 * baked into ONE low-poly primitive-built BufferGeometry (local +X = nose/forward, +Y =
 * up, +Z = right). The pusher prop is deliberately excluded: it spins independently, so
 * it stays a second, separate geometry with its own per-instance rotation.
 *
 * Every part is authored (translated/rotated) in local UAV space before merging, so the
 * merged result can be mounted as a single `<mesh>` with a single material — one draw
 * call per UAV airframe instead of five.
 */
function buildAirframeGeometry(): THREE.BufferGeometry {
  const parts: THREE.BufferGeometry[] = [];

  // Fuselage — tapered cylinder, nose (fatter) at +X, tail (thinner) at -X.
  const fuselage = new THREE.CylinderGeometry(0.045, 0.022, 0.62, 8);
  fuselage.rotateZ(-Math.PI / 2);
  parts.push(fuselage);

  // Ventral sensor turret — small cone bump, apex down, tucked under the nose.
  const turret = new THREE.ConeGeometry(0.03, 0.05, 6);
  turret.rotateX(Math.PI);
  turret.translate(0.18, -0.06, 0);
  parts.push(turret);

  // Wings — thin boxes, root-anchored at local origin so rotateX pivots at the root,
  // giving a true dihedral (tips lift) rather than the whole span tilting as one plane.
  const wingChord = 0.14;
  const wingThickness = 0.014;
  const wingSpan = 0.46;
  const dihedral = 0.12; // ~7 degrees
  for (const side of [1, -1] as const) {
    const wing = new THREE.BoxGeometry(wingChord, wingThickness, wingSpan);
    wing.translate(0, 0, (side * wingSpan) / 2); // root at z=0, tip at z=side*wingSpan
    wing.rotateX(-side * dihedral);
    wing.translate(0, 0, side * 0.03); // mount root just outside the fuselage skin
    parts.push(wing);
  }

  // Twin tail booms — running aft from just behind the wing root, past the fuselage
  // tail, to where the fins mount.
  const boomLength = 0.62;
  const boomRadius = 0.014;
  const boomCenterX = -0.29;
  const boomTailX = boomCenterX - boomLength / 2;
  for (const side of [1, -1] as const) {
    const boom = new THREE.CylinderGeometry(boomRadius, boomRadius, boomLength, 6);
    boom.rotateZ(-Math.PI / 2);
    boom.translate(boomCenterX, -0.01, side * 0.22);
    parts.push(boom);

    // Small vertical fin at the boom's aft tip.
    const fin = new THREE.BoxGeometry(0.06, 0.16, 0.012);
    fin.translate(boomTailX, 0.03, side * 0.22);
    parts.push(fin);
  }

  const merged = mergeBufferGeometries(parts);
  for (const part of parts) part.dispose();
  return merged ?? new THREE.BoxGeometry(0.6, 0.1, 0.1);
}

/** Pusher prop disk — kept separate from the airframe so it can spin on its own axis;
 * the geometry itself is still built once and shared across all three UAV instances. */
function buildPropGeometry(): THREE.BufferGeometry {
  const prop = new THREE.CylinderGeometry(0.15, 0.15, 0.015, 10);
  prop.rotateZ(-Math.PI / 2); // spin axis -> local +X, same convention as the fuselage
  return prop;
}

/**
 * Invisible, generously-sized click/hover target covering the whole airframe+prop
 * assembly. The visible geometry above is deliberately slender (a real MALE UAV's actual
 * silhouette), which leaves too little on-screen area at this panel's size (~30px per
 * UAV) to reliably hit while it's also continuously moving through its flight loop.
 * `visible={false}` on the mesh that uses this hides it from rendering without disabling
 * raycasting — the standard r3f pattern for a forgiving hit target on thin geometry.
 */
function buildHitTargetGeometry(): THREE.BufferGeometry {
  const box = new THREE.BoxGeometry(1.3, 0.35, 1.2);
  box.translate(-0.15, 0.02, 0);
  return box;
}

function recommendationColor(
  rec: FleetOverviewEntry["mission_reliability_recommendation"]
): string {
  if (rec === "NO-GO") return COLORS.nogo;
  if (rec === "CAUTION") return COLORS.caution;
  return COLORS.go;
}

function UavMesh({
  uavId,
  index,
  airframeGeometry,
  propGeometry,
  hitGeometry,
  onSelect,
}: {
  uavId: string;
  index: number;
  airframeGeometry: THREE.BufferGeometry;
  propGeometry: THREE.BufferGeometry;
  hitGeometry: THREE.BufferGeometry;
  onSelect: (uavId: string) => void;
}) {
  const group = useRef<THREE.Group>(null);
  const prop = useRef<THREE.Mesh>(null);
  const airframeMat = useRef<THREE.MeshStandardMaterial>(null);
  const current = useMemo(() => new THREE.Color(COLORS.go), []);
  const target = useMemo(() => new THREE.Color(), []);
  // `index` is always 0-2, matching FORMATION's fixed length — bounded by UAV_ORDER.map.
  const { base, phase } = FORMATION[index]!;

  useFrame(({ clock }, delta) => {
    const t = clock.getElapsedTime() + phase;
    if (group.current) {
      // Gentle figure-eight/orbit, offset per UAV — cosmetic ambient motion only.
      group.current.position.set(
        base[0] + Math.sin(t * 0.32) * 0.55,
        base[1] + Math.sin(t * 0.5) * 0.15,
        base[2] + Math.sin(t * 0.64) * 0.45
      );
      group.current.rotation.y = Math.cos(t * 0.32) * 0.35;
      group.current.rotation.z = Math.sin(t * 0.32) * 0.12;
    }
    // Pusher prop — spins on its own local axis, independent of the flight loop above.
    if (prop.current) {
      prop.current.rotation.x += delta * 14;
    }

    const entry = useFleetStore
      .getState()
      .rankings.find((r) => r.uav_id === uavId);
    target.set(recommendationColor(entry?.mission_reliability_recommendation ?? null));
    current.lerp(target, Math.min(1, delta * 1.5));
    airframeMat.current?.emissive.copy(current);
  });

  const handleClick = useCallback(
    (e: ThreeEvent<MouseEvent>) => {
      e.stopPropagation();
      onSelect(uavId);
    },
    [onSelect, uavId]
  );
  const handleOver = useCallback((e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    document.body.style.cursor = "pointer";
  }, []);
  const handleOut = useCallback(() => {
    document.body.style.cursor = "";
  }, []);

  return (
    <group ref={group}>
      <mesh geometry={airframeGeometry}>
        <meshStandardMaterial
          ref={airframeMat}
          color={COLORS.casting}
          emissiveIntensity={0.9}
          roughness={0.55}
          metalness={0.3}
        />
      </mesh>
      <mesh ref={prop} geometry={propGeometry} position={[-0.36, 0, 0]}>
        <meshStandardMaterial color={COLORS.darkMetal} roughness={0.5} side={THREE.DoubleSide} />
      </mesh>
      <mesh
        geometry={hitGeometry}
        visible={false}
        onClick={handleClick}
        onPointerOver={handleOver}
        onPointerOut={handleOut}
      />
    </group>
  );
}

function Scene({ onSelect }: { onSelect: (uavId: string) => void }) {
  // Built once (useMemo, empty deps) and reused across all three UAV instances below —
  // only position/rotation (per-frame) and material colour (per-instance) differ.
  const { airframe, prop, hit } = useMemo(
    () => ({
      airframe: buildAirframeGeometry(),
      prop: buildPropGeometry(),
      hit: buildHitTargetGeometry(),
    }),
    []
  );

  return (
    <>
      <ambientLight intensity={0.5} />
      <directionalLight position={[4, 5, 3]} intensity={0.75} />
      <directionalLight position={[-3, 2, -2]} intensity={0.25} color="#7fb4d8" />
      {UAV_ORDER.map((uavId, index) => (
        <UavMesh
          key={uavId}
          uavId={uavId}
          index={index}
          airframeGeometry={airframe}
          propGeometry={prop}
          hitGeometry={hit}
          onSelect={onSelect}
        />
      ))}
      <gridHelper args={[10, 20, "#1a2230", "#121822"]} position={[0, -0.9, 0]} />
    </>
  );
}

function ReadoutStat({
  label,
  value,
  tone = "text-slate-200",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.15em] text-slate-500">{label}</div>
      <div className={`font-display text-xl font-bold ${tone}`}>{value}</div>
    </div>
  );
}

export function FleetFormation3D({
  roster,
  fleetMissionReliability,
}: {
  roster: FleetOverviewEntry[];
  fleetMissionReliability: FleetMissionReliability | null;
}) {
  const router = useRouter();
  const setSelectedUavId = useFleetStore((s) => s.setSelectedUavId);

  const onSelect = useCallback(
    (uavId: string) => {
      setSelectedUavId(uavId);
      router.push("/");
    },
    [router, setSelectedUavId]
  );

  const worstHealth =
    roster.length > 0
      ? Math.min(...roster.map((r) => r.overall_health ?? 100))
      : null;
  const allSucceedPct =
    fleetMissionReliability?.all_succeed_probability != null
      ? Math.round(fleetMissionReliability.all_succeed_probability * 100)
      : null;
  const weakestPct =
    fleetMissionReliability?.weakest_score != null
      ? Math.round(fleetMissionReliability.weakest_score * 100)
      : null;

  return (
    <GlassCard
      title="Fleet Formation"
      subtitle="3 UAVs · live GO / CAUTION / NO-GO beacon status"
      glow="cyan"
      bodyClassName="p-0"
    >
      <div className="flex flex-wrap items-center gap-6 border-b border-base-border/70 px-4 py-3">
        <ReadoutStat
          label="All-Succeed Probability"
          value={allSucceedPct != null ? `${allSucceedPct}%` : "—"}
          tone="text-status-cyan"
        />
        <ReadoutStat
          label="Weakest UAV"
          value={
            fleetMissionReliability?.weakest_uav_id
              ? `${fleetMissionReliability.weakest_uav_id}${weakestPct != null ? ` (${weakestPct}%)` : ""}`
              : "—"
          }
          tone="text-status-amber"
        />
        <ReadoutStat
          label="Fleet Health (worst)"
          value={worstHealth != null ? `${Math.round(worstHealth)}` : "—"}
        />
      </div>
      <div className="relative h-[260px] w-full overflow-hidden">
        <Canvas
          camera={{ position: [0, 2.1, 5.4], fov: 42 }}
          dpr={[1, 1.75]}
          gl={{ antialias: true, powerPreference: "high-performance" }}
        >
          <color attach="background" args={["#0a0e14"]} />
          <fog attach="fog" args={["#0a0e14", 6, 13]} />
          <Scene onSelect={onSelect} />
        </Canvas>
        <div className="pointer-events-none absolute bottom-2 right-3 font-mono text-[9px] text-slate-600">
          click a UAV to open its dashboard
        </div>
      </div>
    </GlassCard>
  );
}
