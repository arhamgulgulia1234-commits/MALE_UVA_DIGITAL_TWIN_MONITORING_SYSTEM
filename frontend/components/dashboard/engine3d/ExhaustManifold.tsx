"use client";

import { useCallback, useMemo } from "react";
import * as THREE from "three";
import { useTelemetryStore } from "@/lib/store";
import { FlowArrows, FlowTube } from "./FlowArrows";
import {
  COLORS,
  CYLINDER_LAYOUT,
  LAYOUT,
  fuelToExhaustFlow,
} from "./engine3dUtils";

/**
 * Exhaust path: each cylinder head → a collector running under the case → past the
 * wastegate → into the turbine. This is the dark teal path in turbo-flow-diagram.png.
 *
 * Flow is driven by fuel burn rather than boost: exhaust mass is what the engine has
 * *already* consumed, so it tracks fuel flow, while the intake side tracks the boost the
 * turbo is producing. Keeping the two on different signals is what makes turbo lag
 * visible — the exhaust drives the turbine, the turbine drives the boost, and boost
 * trails behind.
 */
export function ExhaustManifold({ xray, emphasis }: { xray: boolean; emphasis: number }) {
  const collectorY = LAYOUT.exhaustCollectorY;
  const { x: turboX, y: turboY } = LAYOUT.turbo;

  /** Header from each cylinder head down to the collector. */
  const headers = useMemo(
    () =>
      CYLINDER_LAYOUT.map((c) =>
        new THREE.CatmullRomCurve3([
          new THREE.Vector3(c.x, -0.18, c.bank * (LAYOUT.cylinderOuter - 0.16)),
          new THREE.Vector3(c.x, -0.42, c.bank * 0.8),
          new THREE.Vector3(c.x - 0.1, collectorY, c.bank * 0.38),
          new THREE.Vector3(c.x - 0.2, collectorY, c.bank * 0.16),
        ])
      ),
    [collectorY]
  );

  /** Collector → wastegate junction → turbine inlet. */
  const collector = useMemo(
    () =>
      new THREE.CatmullRomCurve3([
        new THREE.Vector3(0.5, collectorY, 0.16),
        new THREE.Vector3(0.0, collectorY - 0.04, 0.1),
        new THREE.Vector3(-0.6, collectorY - 0.06, 0.04),
        new THREE.Vector3(LAYOUT.wastegate.x + 0.12, LAYOUT.wastegate.y + 0.02, 0),
        new THREE.Vector3(turboX - 0.26, turboY - 0.2, 0),
        new THREE.Vector3(turboX - 0.28, turboY - 0.02, 0),
      ]),
    [collectorY, turboX, turboY]
  );

  const sample = useCallback(() => {
    const frame = useTelemetryStore.getState().latest;
    return fuelToExhaustFlow(frame?.fuel_flow_lph ?? 8);
  }, []);

  const tubeOpacity = xray ? 0.4 : 0.22;

  return (
    <group>
      {headers.map((curve, i) => (
        <group key={i}>
          <FlowTube
            curve={curve}
            color={COLORS.exhaust}
            radius={0.048}
            opacity={tubeOpacity}
          />
          <FlowArrows
            curve={curve}
            color={COLORS.exhaust}
            count={6}
            size={0.04}
            sample={sample}
            emphasis={emphasis}
          />
        </group>
      ))}

      <FlowTube
        curve={collector}
        color={COLORS.exhaust}
        radius={0.075}
        opacity={tubeOpacity}
      />
      <FlowArrows
        curve={collector}
        color={COLORS.exhaust}
        count={24}
        sample={sample}
        emphasis={emphasis}
      />
    </group>
  );
}
