"use client";

import { useEffect, useRef, useState } from "react";
import gsap from "gsap";

/**
 * Damps a rapidly-updating telemetry number (frames arrive at 10Hz) into a smooth
 * instrument-needle motion instead of a jittery digit snap. Retargets the running tween
 * on every update (overwrite: true) rather than queuing, so it always eases toward the
 * latest value the way a physical gauge would settle. Skips the tween entirely under
 * prefers-reduced-motion and jumps straight to the target.
 */
export function useTweenedNumber(target: number, duration = 0.35): number {
  const [display, setDisplay] = useState(target);
  const proxy = useRef({ value: target });

  useEffect(() => {
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduced || !Number.isFinite(target)) {
      proxy.current.value = target;
      setDisplay(target);
      return;
    }

    const tween = gsap.to(proxy.current, {
      value: target,
      duration,
      ease: "power2.out",
      overwrite: true,
      onUpdate: () => setDisplay(proxy.current.value),
    });

    return () => {
      tween.kill();
    };
  }, [target, duration]);

  return display;
}
