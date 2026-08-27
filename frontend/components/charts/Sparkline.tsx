"use client";

import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";

interface SparklineProps {
  data: number[];
  color: string;
  height?: number;
  domain?: [number, number];
}

export function Sparkline({ data, color, height = 36, domain }: SparklineProps) {
  const points = data.map((v, i) => ({ i, v }));
  return (
    <div style={{ height, width: "100%" }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <YAxis hide domain={domain ?? ["dataMin - 1", "dataMax + 1"]} />
          <Line
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.75}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
