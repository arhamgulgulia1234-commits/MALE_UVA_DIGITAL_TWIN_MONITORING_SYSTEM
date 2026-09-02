"use client";

import { motion, useSpring, useTransform } from "framer-motion";
import { useEffect } from "react";

interface RadialGaugeProps {
  value: number; // 0-100
  size?: number;
  strokeWidth?: number;
  color: string;
  trackColor?: string;
  children?: React.ReactNode;
  /** Opt-in HUD instrument marks — off by default so existing callers are unaffected. */
  showTicks?: boolean;
  /** Opt-in glowing needle-tip marker that tracks the same spring as the arc fill. */
  needleMarker?: boolean;
}

const TICK_VALUES = [0, 25, 50, 75, 100];

export function RadialGauge({
  value,
  size = 220,
  strokeWidth = 14,
  color,
  trackColor = "#1e2734",
  children,
  showTicks = false,
  needleMarker = false,
}: RadialGaugeProps) {
  const center = size / 2;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;

  const spring = useSpring(0, { stiffness: 60, damping: 16, mass: 0.6 });
  useEffect(() => {
    spring.set(Math.max(0, Math.min(100, value)));
  }, [value, spring]);

  const dashoffset = useTransform(spring, (v) => circumference * (1 - v / 100));

  // The <svg> is CSS-rotated -90deg so 0% renders at 12 o'clock; the needle marker is a
  // sibling HTML element outside that rotation, so its angle bakes the same -90deg offset
  // in directly to land in the same screen position as the rotated arc.
  const markerAngleRad = useTransform(spring, (v) => (((v / 100) * 360 - 90) * Math.PI) / 180);
  const markerX = useTransform(markerAngleRad, (a) => center + radius * Math.cos(a));
  const markerY = useTransform(markerAngleRad, (a) => center + radius * Math.sin(a));

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke={trackColor}
          strokeWidth={strokeWidth}
        />
        {showTicks &&
          TICK_VALUES.map((t) => {
            const rad = ((t / 100) * 360 * Math.PI) / 180;
            const inner = radius - strokeWidth / 2 - 3;
            const outer = radius + strokeWidth / 2 + 3;
            return (
              <line
                key={t}
                x1={center + inner * Math.cos(rad)}
                y1={center + inner * Math.sin(rad)}
                x2={center + outer * Math.cos(rad)}
                y2={center + outer * Math.sin(rad)}
                stroke="#3a4657"
                strokeWidth={1.5}
              />
            );
          })}
        <motion.circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          style={{ strokeDashoffset: dashoffset }}
        />
      </svg>
      {needleMarker && (
        <motion.div
          className="pointer-events-none absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
          style={{ left: markerX, top: markerY, background: color }}
        />
      )}
      <div className="absolute inset-0 flex flex-col items-center justify-center">{children}</div>
    </div>
  );
}
