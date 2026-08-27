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
}

export function RadialGauge({
  value,
  size = 220,
  strokeWidth = 14,
  color,
  trackColor = "#1e2734",
  children,
}: RadialGaugeProps) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;

  const spring = useSpring(0, { stiffness: 60, damping: 16, mass: 0.6 });
  useEffect(() => {
    spring.set(Math.max(0, Math.min(100, value)));
  }, [value, spring]);

  const dashoffset = useTransform(spring, (v) => circumference * (1 - v / 100));

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={trackColor}
          strokeWidth={strokeWidth}
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          style={{ strokeDashoffset: dashoffset, filter: `drop-shadow(0 0 8px ${color}80)` }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">{children}</div>
    </div>
  );
}
