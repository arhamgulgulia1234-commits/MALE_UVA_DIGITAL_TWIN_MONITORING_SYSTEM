"use client";

import { useId } from "react";
import { Area, AreaChart, ResponsiveContainer, YAxis } from "recharts";

interface SparklineProps {
  data: number[];
  color: string;
  height?: number;
  domain?: [number, number];
}

// Trailing fade beneath the line reads as "recent history" per real-time-streaming
// chart guidance (current value emphasized, history fading toward transparent).
export function Sparkline({ data, color, height = 36, domain }: SparklineProps) {
  const gradientId = useId();
  const points = data.map((v, i) => ({ i, v }));
  return (
    <div style={{ height, width: "100%" }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={points} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.32} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={domain ?? ["dataMin - 1", "dataMax + 1"]} />
          <Area
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.75}
            fill={`url(#${gradientId})`}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
