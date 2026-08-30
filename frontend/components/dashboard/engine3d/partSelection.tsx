"use client";

import { Outlines } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import { createContext, useContext, useEffect, useMemo } from "react";
import { COLORS, type PartVisual, visualFor } from "./engine3dUtils";

/**
 * Selection plumbing for the part inspector.
 *
 * Selection is ordinary React state and lives at the top of the scene, but it is
 * delivered through context rather than props so that adding a clickable part to a
 * component costs one hook call instead of threading two more props through every mesh.
 *
 * Two things are deliberately kept *out* of React here:
 *
 *  - **Hover.** The cursor is set by mutating `document.body.style` directly. Routing
 *    hover through state would reconcile the scene on every mouse move across a part,
 *    which is exactly the per-frame re-render the rest of this directory exists to avoid.
 *  - **Appearance.** Selection only changes `transparent`/`opacity` props and adds an
 *    outline child. Every telemetry binding still writes colour and rotation from inside
 *    `useFrame` against a ref, untouched by any of this.
 */

interface PartSelectionValue {
  selectedId: string | null;
  select: (id: string) => void;
}

const PartSelectionContext = createContext<PartSelectionValue>({
  selectedId: null,
  select: () => {},
});

export const PartSelectionProvider = PartSelectionContext.Provider;

export function usePartSelection(): PartSelectionValue {
  return useContext(PartSelectionContext);
}

export interface PartInteraction {
  visual: PartVisual;
  /** Spread onto the group that wraps the part's meshes — r3f events bubble up to it. */
  handlers: {
    onClick: (e: ThreeEvent<MouseEvent>) => void;
    onPointerOver: (e: ThreeEvent<PointerEvent>) => void;
    onPointerOut: () => void;
  };
}

export function usePartInteraction(partId: string): PartInteraction {
  const { selectedId, select } = usePartSelection();

  const handlers = useMemo(
    () => ({
      onClick: (e: ThreeEvent<MouseEvent>) => {
        // Stop here so a click on the wastegate does not also register on the turbo
        // housing behind it, and so it never reaches the canvas-level deselect.
        e.stopPropagation();
        select(partId);
      },
      onPointerOver: (e: ThreeEvent<PointerEvent>) => {
        e.stopPropagation();
        document.body.style.cursor = "pointer";
      },
      onPointerOut: () => {
        document.body.style.cursor = "";
      },
    }),
    [partId, select]
  );

  // If a part unmounts while hovered (view-mode switch, page navigation) the cursor
  // would otherwise stay stuck as a pointer over unrelated UI.
  useEffect(() => () => {
    document.body.style.cursor = "";
  }, []);

  return { visual: visualFor(selectedId, partId), handlers };
}

/**
 * Cyan rim on the selected part.
 *
 * An outline rather than an emissive boost, because emissive is telemetry-owned on most
 * of this engine — the cylinders write it from EGT every frame, so a selection glow
 * poked into the same channel would either be overwritten or would corrupt the
 * temperature reading the colour is supposed to convey. `<Outlines>` adds a separate
 * back-faced child mesh and never touches the parent material.
 */
export function PartOutline({
  visual,
  thickness = 2.5,
}: {
  visual: PartVisual;
  /** Rim width in pixels — drei's default (non-screenspace) mode offsets in clip space. */
  thickness?: number;
}) {
  if (visual !== "selected") return null;
  return (
    <Outlines
      thickness={thickness}
      color={COLORS.cyan}
      transparent
      opacity={1}
      toneMapped={false}
      renderOrder={2}
    />
  );
}
